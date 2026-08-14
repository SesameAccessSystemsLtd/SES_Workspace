r"""
db_write.py — Write interface for the OpenClaw SQLite databases.

Supports insert, upsert (insert or replace on conflict), and update operations.
Every write is appended to the relevant audit log.
Implements exponential backoff retry on SQLITE_BUSY (max 3 retries).

Usage:
    # Insert into ops.db (default)
    python db_write.py --db ops --table scraper_runs --operation insert \
        --data '{"dataset_name": "hubspot_contacts", "source": "hubspot", "pulled_at": "2026-03-17T09:00:00", "status": "success"}'

    # Upsert into crm.db
    python db_write.py --db crm --table contacts --operation upsert --conflict-key hubspot_id \
        --data '{"hubspot_id": "hs_123", "email": "jane@example.com", "lifecycle_stage": "lead"}'

    # Update rows matching --where condition
    python db_write.py --db crm --table campaigns --operation update \
        --where '{"id": 5}' --data '{"status": "complete"}'

    # Custom db path (overrides --db)
    python db_write.py --db-path C:\\custom\\crm.db --table contacts --operation insert --data '{...}'

--db flag accepts: ops, crm (default: ops)
  ops  → C:\Users\GilesHugo\SES_Workspace\db\ops.db  (env: OPENCLAW_OPS_DB_PATH)
        audit log → ops_audit.log
  crm  → C:\Users\GilesHugo\SES_Workspace\db\crm.db  (env: OPENCLAW_CRM_DB_PATH)
        audit log → crm_audit.log

Exit code 0 on success, 1 on error.
"""

import argparse
import json
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone

from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[3] / "lib"))
from path_config import p as _p

DB_DEFAULTS = {
    "ops": os.environ.get("OPENCLAW_OPS_DB_PATH") or _p("db.ops"),
    "crm": os.environ.get("OPENCLAW_CRM_DB_PATH") or _p("db.crm"),
}
AUDIT_LOG_DEFAULTS = {
    "ops": os.environ.get(
        "OPENCLAW_OPS_AUDIT_LOG",
        r"C:\Users\GilesHugo\SES_Workspace\db\ops_audit.log",
    ),
    "crm": os.environ.get(
        "OPENCLAW_CRM_AUDIT_LOG",
        r"C:\Users\GilesHugo\SES_Workspace\db\crm_audit.log",
    ),
}

OPS_TABLES = {
    "scraper_runs",
    "asana_task_states",
    "purchase_orders",
    "bom_requirements",
    "supplier_deliveries",
    "email_po_matches",
}
CRM_TABLES = {
    "contacts",
    "leads",
    "companies",
    "campaigns",
    "campaign_results",
    "lead_snippets",
    "planning_applications",
}
ALLOWED_TABLES = OPS_TABLES | CRM_TABLES

MAX_RETRIES = 3
RETRY_BASE_DELAY = 0.2  # seconds

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)


def audit(log_path: str, operation: str, table: str, data: dict, success: bool, notes: str = "") -> None:
    ts = datetime.now(timezone.utc).isoformat()
    status = "OK" if success else "FAIL"
    entry = f"[{ts}] {status} | op={operation} | table={table} | data={json.dumps(data)} | notes={notes}\n"
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(entry)
    except Exception as exc:
        log.warning("Could not write to audit log: %s", exc)


def execute_with_retry(conn: sqlite3.Connection, sql: str, params: tuple) -> sqlite3.Cursor:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return conn.execute(sql, params)
        except sqlite3.OperationalError as exc:
            if "database is locked" in str(exc) and attempt < MAX_RETRIES:
                delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                log.warning("Database locked (attempt %d/%d). Retrying in %.1fs...", attempt, MAX_RETRIES, delay)
                time.sleep(delay)
            else:
                raise


def do_insert(conn: sqlite3.Connection, table: str, data: dict) -> int:
    cols = ", ".join(data.keys())
    placeholders = ", ".join("?" * len(data))
    sql = f"INSERT INTO {table} ({cols}) VALUES ({placeholders})"
    cursor = execute_with_retry(conn, sql, tuple(data.values()))
    return cursor.lastrowid


def do_upsert(conn: sqlite3.Connection, table: str, data: dict, conflict_key: str) -> int:
    cols = ", ".join(data.keys())
    placeholders = ", ".join("?" * len(data))
    update_cols = ", ".join(f"{k} = excluded.{k}" for k in data if k != conflict_key)
    sql = (
        f"INSERT INTO {table} ({cols}) VALUES ({placeholders}) "
        f"ON CONFLICT({conflict_key}) DO UPDATE SET {update_cols}"
    )
    cursor = execute_with_retry(conn, sql, tuple(data.values()))
    return cursor.lastrowid


def do_update(conn: sqlite3.Connection, table: str, data: dict, where: dict) -> int:
    set_clause = ", ".join(f"{k} = ?" for k in data)
    where_clause = " AND ".join(f"{k} = ?" for k in where)
    sql = f"UPDATE {table} SET {set_clause} WHERE {where_clause}"
    params = tuple(data.values()) + tuple(where.values())
    cursor = execute_with_retry(conn, sql, params)
    return cursor.rowcount


def main() -> None:
    parser = argparse.ArgumentParser(description="Write data to the OpenClaw SQLite database.")
    parser.add_argument("--table", required=True, help=f"Target table. One of: {', '.join(sorted(ALLOWED_TABLES))}")
    parser.add_argument("--operation", required=True, choices=["insert", "upsert", "update"], help="Write operation.")
    parser.add_argument("--data", required=True, help="JSON object of column→value pairs to write.")
    parser.add_argument("--conflict-key", help="Column name for upsert conflict resolution (required for upsert).")
    parser.add_argument("--where", help="JSON object of conditions for update operations.")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH, help="Path to the SQLite database file.")
    parser.add_argument("--audit-log", default=DEFAULT_AUDIT_LOG, help="Path to the audit log file.")
    args = parser.parse_args()

    # Validate table
    if args.table not in ALLOWED_TABLES:
        print(f"ERROR: Unknown table '{args.table}'. Allowed: {', '.join(sorted(ALLOWED_TABLES))}", file=sys.stderr)
        sys.exit(1)

    # Parse JSON data
    try:
        data = json.loads(args.data)
    except json.JSONDecodeError as exc:
        print(f"ERROR: Invalid JSON in --data: {exc}", file=sys.stderr)
        sys.exit(1)

    where = {}
    if args.where:
        try:
            where = json.loads(args.where)
        except json.JSONDecodeError as exc:
            print(f"ERROR: Invalid JSON in --where: {exc}", file=sys.stderr)
            sys.exit(1)

    # Validate operation-specific args
    if args.operation == "upsert" and not args.conflict_key:
        print("ERROR: --conflict-key is required for upsert operations.", file=sys.stderr)
        sys.exit(1)
    if args.operation == "update" and not where:
        print("ERROR: --where is required for update operations.", file=sys.stderr)
        sys.exit(1)

    if not os.path.exists(args.db_path):
        print(f"ERROR: Database not found at {args.db_path}. Run db_init.py first.", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(args.db_path)
    try:
        if args.operation == "insert":
            rowid = do_insert(conn, args.table, data)
            conn.commit()
            print(json.dumps({"status": "ok", "operation": "insert", "table": args.table, "rowid": rowid}))
            audit(args.audit_log, "insert", args.table, data, True)

        elif args.operation == "upsert":
            rowid = do_upsert(conn, args.table, data, args.conflict_key)
            conn.commit()
            print(json.dumps({"status": "ok", "operation": "upsert", "table": args.table, "rowid": rowid}))
            audit(args.audit_log, "upsert", args.table, data, True)

        elif args.operation == "update":
            affected = do_update(conn, args.table, data, where)
            conn.commit()
            print(json.dumps({"status": "ok", "operation": "update", "table": args.table, "rows_affected": affected}))
            audit(args.audit_log, "update", args.table, {**data, "_where": where}, True)

    except sqlite3.Error as exc:
        conn.rollback()
        audit(args.audit_log, args.operation, args.table, data, False, str(exc))
        print(f"SQL ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        conn.rollback()
        audit(args.audit_log, args.operation, args.table, data, False, str(exc))
        print(f"UNEXPECTED ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
