"""
ops_db_migrate.py — Schema migrations for ops.db.

Safe to re-run — every migration checks before applying.

Migrations:
  v1 (2026-06-26): Add ordering_queue + ordering_edge_cases tables.
                   Add vendor_name_normalised column to procurement_tasks,
                   parts_control, and suppliers. Backfill from vendor_name.

Usage:
    python ops_db_migrate.py
    python ops_db_migrate.py --db-path C:\\custom\\path\\ops.db
"""

import argparse
import logging
import re
import sqlite3
import sys
import os

from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[3] / "lib"))
from path_config import p as _p

DEFAULT_DB_PATH = os.environ.get("OPENCLAW_OPS_DB_PATH") or _p("db.ops")

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)


def column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cursor = conn.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    )
    return cursor.fetchone() is not None


def _normalise(raw: str | None) -> str | None:
    if not raw:
        return None
    name = raw.lower()
    name = re.sub(r"[^a-z0-9 ]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name or None


def migrate_v1(conn: sqlite3.Connection) -> None:
    """
    v1: Create ordering_queue + ordering_edge_cases.
        Add vendor_name_normalised to procurement_tasks, parts_control, suppliers.
    """
    log.info("Applying migration v1...")

    # --- 1. ordering_queue ---
    if not table_exists(conn, "ordering_queue"):
        conn.execute("""
            CREATE TABLE ordering_queue (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                asana_task_gid          TEXT NOT NULL UNIQUE,
                task_name               TEXT,
                sesame_pn               TEXT,
                item_type               TEXT CHECK(item_type IN ('stock','consumable','project','need')),
                source_project_gid      TEXT,
                vendor_name             TEXT,
                vendor_name_normalised  TEXT,
                order_qty               REAL,
                stock_qty_at_detection  REAL,
                stock_action_value      TEXT,
                parts_control_task_gid  TEXT,
                order_task_gid          TEXT,
                order_task_type         TEXT CHECK(order_task_type IN ('needs','project_orders',NULL)),
                vendor_name_at_order    TEXT,
                vendor_is_fallback      INTEGER DEFAULT 0,
                status                  TEXT DEFAULT 'grouped'
                                        CHECK(status IN ('grouped','ordered','arrived','error','cancelled')),
                grouped_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                ordered_at              TIMESTAMP,
                arrived_qty             REAL,
                arrived_at              TIMESTAMP,
                arrived_reset_pending   INTEGER DEFAULT 0,
                notes                   TEXT
            )
        """)
        log.info("  Created table: ordering_queue")
    else:
        # Add arrived_reset_pending if missing (upgrade from earlier draft)
        if not column_exists(conn, "ordering_queue", "arrived_reset_pending"):
            conn.execute("ALTER TABLE ordering_queue ADD COLUMN arrived_reset_pending INTEGER DEFAULT 0")
            log.info("  Added arrived_reset_pending to ordering_queue")
        if not column_exists(conn, "ordering_queue", "vendor_name_normalised"):
            conn.execute("ALTER TABLE ordering_queue ADD COLUMN vendor_name_normalised TEXT")
            log.info("  Added vendor_name_normalised to ordering_queue")
        log.info("  ordering_queue already exists — column check done.")

    # --- 2. ordering_edge_cases ---
    if not table_exists(conn, "ordering_edge_cases"):
        conn.execute("""
            CREATE TABLE ordering_edge_cases (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                run_timestamp       TEXT NOT NULL,
                script              TEXT NOT NULL,
                stage               TEXT,
                asana_task_gid      TEXT,
                task_name           TEXT,
                vendor_name         TEXT,
                issue_type          TEXT NOT NULL,
                description         TEXT,
                raw_data            TEXT,
                asana_edge_case_gid TEXT,
                created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                resolved            INTEGER DEFAULT 0
            )
        """)
        log.info("  Created table: ordering_edge_cases")
    else:
        log.info("  ordering_edge_cases already exists — skipped.")

    # --- 3. vendor_name_normalised columns ---
    for table in ("procurement_tasks", "parts_control", "suppliers"):
        if not table_exists(conn, table):
            log.info("  %s: table not found — skipping column add.", table)
            continue
        if not column_exists(conn, table, "vendor_name_normalised"):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN vendor_name_normalised TEXT")
            log.info("  Added vendor_name_normalised to %s", table)
        else:
            log.info("  %s.vendor_name_normalised already exists — skipped.", table)

    conn.commit()

    # --- 4. Backfill vendor_name_normalised ---
    for table in ("procurement_tasks", "parts_control", "suppliers"):
        if not table_exists(conn, table):
            continue
        rows = conn.execute(
            f"SELECT rowid, vendor_name FROM {table} WHERE vendor_name_normalised IS NULL AND vendor_name IS NOT NULL"
        ).fetchall()
        if not rows:
            log.info("  %s: no rows to backfill.", table)
            continue
        updates = [((_normalise(r[1])), r[0]) for r in rows]
        conn.executemany(
            f"UPDATE {table} SET vendor_name_normalised = ? WHERE rowid = ?",
            updates,
        )
        conn.commit()
        log.info("  Backfilled %d vendor_name_normalised rows in %s", len(updates), table)

    log.info("Migration v1 complete.")


def run_migrations(db_path: str) -> None:
    if not os.path.exists(db_path):
        log.error("Database not found at %s", db_path)
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        migrate_v1(conn)
        log.info("All migrations applied.")

        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = [row[0] for row in cursor.fetchall()]
        log.info("Tables in ops.db: %s", ", ".join(tables))
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate ops.db schema.")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    args = parser.parse_args()
    try:
        run_migrations(args.db_path)
    except Exception as exc:
        log.error("Migration failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
