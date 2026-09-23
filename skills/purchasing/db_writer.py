"""
db_writer.py — Sole write gateway for ops.db.

All scripts that need to write to ops.db import and call functions from this
module. No script calls sqlite3 INSERT/UPDATE/DELETE directly.

Pattern for every write function:
  1. Validate input
  2. Check for conflicts (stale-sync guard on last_synced_at)
  3. Apply the write
  4. Log to db_write_log
  5. Return dict: {written, skipped, errors}
"""

import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Path resolution: env var wins, else PATH_CONFIG.yaml (db.ops).
# Fail fast if the DB file does not exist — sqlite3.connect would silently
# create a new empty database at a wrong path and cause split-brain.
_LIB_DIR = Path(__file__).resolve().parents[2] / "lib"
if str(_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_LIB_DIR))
from path_config import p as _p

DEFAULT_DB_PATH = os.environ.get("OPENCLAW_OPS_DB_PATH") or _p("db.ops")
if not Path(DEFAULT_DB_PATH).is_file():
    raise FileNotFoundError(
        f"ops.db not found at {DEFAULT_DB_PATH!r} "
        "(resolved from OPENCLAW_OPS_DB_PATH env var or PATH_CONFIG.yaml db.ops). "
        "Refusing to continue: connecting would create an empty database and "
        "split operational data across two files."
    )

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _log_write(conn: sqlite3.Connection, table_name: str, row_gid: str | None,
               action: str, source: str, fields_changed: list[str] | None) -> None:
    conn.execute(
        """INSERT INTO db_write_log (table_name, row_gid, action, source, fields_changed)
           VALUES (?, ?, ?, ?, ?)""",
        (table_name, row_gid, action, source,
         json.dumps(fields_changed) if fields_changed else None),
    )


def _is_stale(existing_synced_at: str | None, incoming_synced_at: str | None) -> bool:
    """Return True if the existing DB record is NEWER than the incoming row — skip the update."""
    if not existing_synced_at or not incoming_synced_at:
        return False
    try:
        existing_dt = datetime.fromisoformat(existing_synced_at.replace("Z", "+00:00"))
        incoming_dt = datetime.fromisoformat(incoming_synced_at.replace("Z", "+00:00"))
        return existing_dt > incoming_dt
    except (ValueError, AttributeError):
        return existing_synced_at > incoming_synced_at


# Columns that never count as a change on their own — they move every run.
_VOLATILE_COLS = {"last_synced_at", "created_at", "updated_at",
                  "association_checked_at", "last_state_hash", "first_seen_at"}

# Field changes worth emitting as a supply event, and how loud each one is.
# Anything not listed is stored but does not raise an event.
_EVENT_FIELDS = {
    "parts_control": {
        "stock_action":         "notable",
        "order_status":         "notable",
        "vendor_name":          "notable",
        "arrived_qty":          "critical",
        "procurement_task_gid": "notable",
        "due_on":               "routine",
        "is_completed":         "routine",
        "rev_number":           "critical",
    },
    "procurement_tasks": {
        "order_status":         "notable",
        "due_date":             "critical",
        "po_number":            "notable",
        "vendor_name":          "routine",
        "price_ex_vat":         "routine",
        "is_completed":         "routine",
    },
    "build_assembly_packs": {
        "parts_readiness":      "critical",
        "stage_of_production":  "notable",
        "steel_profile":        "critical",
        "needs_from_purch":     "critical",
        "due_on":               "critical",
        "unplanned_hours":      "notable",
    },
    "stock_inventory": {
        "stock_qty":            "routine",
        "stock_action":         "routine",
    },
}

_ENTITY_TYPE = {
    "parts_control":        "part",
    "procurement_tasks":    "order",
    "build_assembly_packs": "pack",
    "bom_requirements":     "requirement",
}


def _norm(value) -> str:
    """Compare loosely enough that 4 and 4.0 and ' 4 ' are the same value."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _changed_columns(existing, row: dict) -> list[str]:
    keys = existing.keys() if hasattr(existing, "keys") else []
    out = []
    for col, new in row.items():
        if col in _VOLATILE_COLS:
            continue
        if col not in keys:          # new column not yet migrated — treat as change
            out.append(col)
            continue
        if _norm(existing[col]) != _norm(new):
            out.append(col)
    return out


def _emit_supply_events(conn: sqlite3.Connection, table: str, gid: str,
                        existing, row: dict, changed: list[str], source: str) -> None:
    """Write one part_supply_events row per meaningful field change.

    Silently does nothing if the table has not been migrated yet, so this is safe
    to deploy ahead of migrate_impact_engine.py.
    """
    watch = _EVENT_FIELDS.get(table)
    if not watch:
        return
    interesting = [c for c in changed if c in watch]
    if not interesting:
        return
    observed = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    project_code = row.get("project_code")
    keys = existing.keys() if existing is not None and hasattr(existing, "keys") else []
    try:
        for col in interesting:
            old = existing[col] if (existing is not None and col in keys) else None
            # Keep "0" distinct from empty. `_norm(x) or None` collapsed a real
            # zero to NULL, which silently destroyed the signal on quantity
            # fields — Arrived Stock Qty is a staging field that is written,
            # consumed and cleared, so 0 -> NULL and NULL -> 0 are exactly the
            # transitions worth seeing.
            old_v = None if old is None else _norm(old)
            new_raw = row.get(col)
            new_v = None if new_raw is None else _norm(new_raw)
            if old_v == new_v:
                continue
            conn.execute(
                """INSERT INTO part_supply_events
                   (entity_type, entity_gid, project_code, field, old_value,
                    new_value, observed_at, source, significance)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (_ENTITY_TYPE.get(table, "part"), gid, project_code, col,
                 old_v, new_v, observed, source, watch[col]),
            )
    except sqlite3.OperationalError:
        pass  # table not migrated yet


def _upsert_rows(conn: sqlite3.Connection, table: str, rows: list[dict],
                 key_col: str, source: str, db_path: str) -> dict:
    written = skipped = errors = 0
    for row in rows:
        gid = row.get(key_col)
        if not gid:
            log.warning("%s: row missing %s — skipped", table, key_col)
            errors += 1
            continue
        try:
            existing = conn.execute(
                f"SELECT * FROM {table} WHERE {key_col} = ?", (gid,)
            ).fetchone()
            if existing and _is_stale(existing["last_synced_at"], row.get("last_synced_at")):
                _log_write(conn, table, gid, "skip", source, None)
                skipped += 1
                continue

            # Change detection. Previously every column of every row was logged as
            # "changed" on all nine daily sync runs, which is why db_write_log grew
            # to 3.8M rows and could never answer "what changed since yesterday".
            changed = _changed_columns(existing, row) if existing else list(row.keys())
            if existing and not changed:
                # Refresh the sync timestamp only. No event, no write-log noise.
                conn.execute(
                    f"UPDATE {table} SET last_synced_at = ? WHERE {key_col} = ?",
                    (row.get("last_synced_at"), gid),
                )
                skipped += 1
                continue

            cols = list(row.keys())
            placeholders = ", ".join("?" * len(cols))
            updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c != key_col)
            sql = (
                f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
                f"ON CONFLICT({key_col}) DO UPDATE SET {updates}"
            )
            conn.execute(sql, list(row.values()))
            action = "update" if existing else "insert"
            _log_write(conn, table, gid, action, source, changed)
            _emit_supply_events(conn, table, gid, existing, row, changed, source)
            written += 1
        except Exception as exc:
            log.error("%s gid=%s: %s", table, gid, exc)
            errors += 1
    conn.commit()
    return {"written": written, "skipped": skipped, "errors": errors}


# ---------------------------------------------------------------------------
# Public API — Asana mirror upserts (called by sync scripts)
# ---------------------------------------------------------------------------

def upsert_stock_inventory(rows: list[dict], source: str = "sync",
                            db_path: str = DEFAULT_DB_PATH) -> dict:
    with _connect(db_path) as conn:
        return _upsert_rows(conn, "stock_inventory", rows, "asana_task_gid", source, db_path)


def upsert_build_assembly_packs(rows: list[dict], source: str = "sync",
                                 db_path: str = DEFAULT_DB_PATH) -> dict:
    with _connect(db_path) as conn:
        return _upsert_rows(conn, "build_assembly_packs", rows, "asana_task_gid", source, db_path)


def upsert_parts_control(rows: list[dict], source: str = "sync",
                          db_path: str = DEFAULT_DB_PATH) -> dict:
    with _connect(db_path) as conn:
        return _upsert_rows(conn, "parts_control", rows, "asana_task_gid", source, db_path)


def update_parts_pack_allocation(task_gid: str, pack_gid: str,
                                 source: str = "legacy_bom_pack_allocation",
                                 checked_at: str | None = None,
                                 db_path: str = DEFAULT_DB_PATH) -> dict:
    """Allocate a build-pack GID to one parts_control task (targeted UPDATE).

    Sets build_assembly_pack_gid, association_status='linked', association_checked_at.
    A plain UPDATE (not upsert) so it never touches NOT NULL columns like part_name and
    never creates a phantom row. Returns {written, skipped, errors}; written=0 if the
    task_gid is not present (skipped, not an error).
    """
    if not task_gid or not pack_gid:
        raise ValueError("update_parts_pack_allocation: task_gid and pack_gid required")
    checked_at = checked_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with _connect(db_path) as conn:
        cur = conn.execute(
            """UPDATE parts_control
                  SET build_assembly_pack_gid = ?,
                      association_status = 'linked',
                      association_checked_at = ?
                WHERE asana_task_gid = ?""",
            (pack_gid, checked_at, task_gid),
        )
        if cur.rowcount:
            _log_write(conn, "parts_control", task_gid, "update", source,
                       ["build_assembly_pack_gid", "association_status", "association_checked_at"])
            conn.commit()
            return {"written": 1, "skipped": 0, "errors": 0}
        conn.commit()
        return {"written": 0, "skipped": 1, "errors": 0}


def update_parts_control_stock_action(task_gid: str, new_stock_action: str,
                                      source: str = "parts_control_propagator",
                                      db_path: str = DEFAULT_DB_PATH) -> dict:
    """Targeted UPDATE of parts_control.stock_action for one task (CHG-1A).

    Sets stock_action only — never touches NOT NULL columns like part_name, and
    never creates a phantom row. Audits via db_write_log (`_log_write`).
    Returns {written, skipped, errors}; written=0 (skipped) if the task_gid is not
    present in parts_control — that is NOT an error (the mirror may lag the flip).

    NOTE (CHG-1B): the register sketched an `updated_source` column, but
    parts_control has NO such column (verified against live schema). The `source`
    argument is captured in db_write_log.source instead, which is the correct
    audit location and avoids an unsanctioned schema migration.
    """
    if not task_gid:
        raise ValueError("update_parts_control_stock_action: task_gid required")
    if new_stock_action is None:
        raise ValueError("update_parts_control_stock_action: new_stock_action required")
    with _connect(db_path) as conn:
        cur = conn.execute(
            "UPDATE parts_control SET stock_action = ? WHERE asana_task_gid = ?",
            (new_stock_action, task_gid),
        )
        if cur.rowcount:
            _log_write(conn, "parts_control", task_gid, "update", source, ["stock_action"])
            conn.commit()
            return {"written": 1, "skipped": 0, "errors": 0}
        conn.commit()
        return {"written": 0, "skipped": 1, "errors": 0}


def upsert_suppliers(rows: list[dict], source: str = "sync",
                     db_path: str = DEFAULT_DB_PATH) -> dict:
    with _connect(db_path) as conn:
        return _upsert_rows(conn, "suppliers", rows, "asana_task_gid", source, db_path)


def upsert_procurement_tasks(rows: list[dict], source: str = "sync",
                              db_path: str = DEFAULT_DB_PATH) -> dict:
    with _connect(db_path) as conn:
        return _upsert_rows(conn, "procurement_tasks", rows, "asana_task_gid", source, db_path)


def upsert_qc_issues(rows: list[dict], source: str = "sync",
                     db_path: str = DEFAULT_DB_PATH) -> dict:
    with _connect(db_path) as conn:
        return _upsert_rows(conn, "qc_issues", rows, "asana_task_gid", source, db_path)


def upsert_golden_index(rows: list[dict], source: str = "seed",
                         db_path: str = DEFAULT_DB_PATH) -> dict:
    with _connect(db_path) as conn:
        return _upsert_rows(conn, "golden_index", rows, "sesame_part_no", source, db_path)


# ---------------------------------------------------------------------------
# Public API — Email pipeline writes (called by 02_llm_parser.py)
# ---------------------------------------------------------------------------

def write_email_po_match(row: dict, source: str = "email_pipeline",
                          db_path: str = DEFAULT_DB_PATH) -> dict:
    required = {"email_message_id"}
    if not required.issubset(row):
        raise ValueError(f"write_email_po_match: missing required fields {required - row.keys()}")
    with _connect(db_path) as conn:
        conn.execute(
            """INSERT INTO email_po_matches
               (email_message_id, email_subject, received_at, supplier_name,
                attachment_filename, extracted_po_number, extracted_order_ref,
                asana_task_gid_matched, asana_task_name, match_confidence,
                comment_written, comment_gid)
               VALUES (:email_message_id, :email_subject, :received_at, :supplier_name,
                       :attachment_filename, :extracted_po_number, :extracted_order_ref,
                       :asana_task_gid_matched, :asana_task_name,
                       :match_confidence, :comment_written, :comment_gid)
               ON CONFLICT(email_message_id) DO NOTHING""",
            row,
        )
        _log_write(conn, "email_po_matches", row.get("email_message_id"),
                   "insert", source, list(row.keys()))
        conn.commit()
    return {"written": 1, "skipped": 0, "errors": 0}


def write_purchase_order(row: dict, source: str = "email_pipeline",
                          db_path: str = DEFAULT_DB_PATH) -> dict:
    required = {"supplier_name"}
    if not required.issubset(row):
        raise ValueError(f"write_purchase_order: missing required fields {required - row.keys()}")
    with _connect(db_path) as conn:
        cursor = conn.execute(
            """INSERT INTO purchase_orders
               (po_number, supplier_name, supplier_email, part_numbers,
                order_date, expected_delivery, status, asana_task_gid, source_email_id, notes)
               VALUES (:po_number, :supplier_name, :supplier_email, :part_numbers,
                       :order_date, :expected_delivery, :status, :asana_task_gid,
                       :source_email_id, :notes)
               ON CONFLICT(po_number) DO NOTHING""",
            row,
        )
        new_id = cursor.lastrowid
        _log_write(conn, "purchase_orders", row.get("po_number"),
                   "insert", source, list(row.keys()))
        conn.commit()
    return {"written": 1, "skipped": 0, "errors": 0, "id": new_id}


def write_bom_requirement(row: dict, source: str = "bom_pipeline",
                           db_path: str = DEFAULT_DB_PATH) -> dict:
    required = {"build_ref", "sesame_part_no", "required_qty"}
    if not required.issubset(row):
        raise ValueError(f"write_bom_requirement: missing required fields {required - row.keys()}")
    with _connect(db_path) as conn:
        # Idempotent: skip if this Asana task GID already recorded
        if row.get("asana_task_gid"):
            existing = conn.execute(
                "SELECT id FROM bom_requirements WHERE asana_task_gid = ?",
                (row["asana_task_gid"],)
            ).fetchone()
            if existing:
                _log_write(conn, "bom_requirements", row["asana_task_gid"],
                           "skip", source, None)
                conn.commit()
                return {"written": 0, "skipped": 1, "errors": 0}
        conn.execute(
            """INSERT INTO bom_requirements
               (build_ref, asana_project_gid, sesame_part_no, part_description,
                required_qty, stock_qty_at_import, status, po_id, asana_task_gid, source_bom_file)
               VALUES (:build_ref, :asana_project_gid, :sesame_part_no, :part_description,
                       :required_qty, :stock_qty_at_import, :status, :po_id, :asana_task_gid,
                       :source_bom_file)""",
            {**{"po_id": None}, **row},
        )
        _log_write(conn, "bom_requirements", row.get("asana_task_gid"),
                   "insert", source, list(row.keys()))
        conn.commit()
    return {"written": 1, "skipped": 0, "errors": 0}


def write_po_line(row: dict, source: str = "email_pipeline",
                  db_path: str = DEFAULT_DB_PATH) -> dict:
    required = {"po_id"}
    if not required.issubset(row):
        raise ValueError(f"write_po_line: missing required fields {required - row.keys()}")
    with _connect(db_path) as conn:
        conn.execute(
            """INSERT INTO po_lines
               (po_id, sesame_part_no, description, ordered_qty, allocated_qty, unit_price,
                line_status, asana_task_gid, bom_req_id, notes)
               VALUES (:po_id, :sesame_part_no, :description, :ordered_qty, :allocated_qty,
                       :unit_price, :line_status, :asana_task_gid, :bom_req_id, :notes)""",
            row,
        )
        _log_write(conn, "po_lines", str(row.get("po_id")), "insert", source, list(row.keys()))
        conn.commit()
    return {"written": 1, "skipped": 0, "errors": 0}


# ---------------------------------------------------------------------------
# Public API — Asana write log (called by 04_asana_executer.py)
# ---------------------------------------------------------------------------

def log_propagator_run(run_at: str, updates: list[dict],
                       db_path: str = DEFAULT_DB_PATH) -> dict:
    """Log each cascaded On Order update from parts_control_propagator. One row per task."""
    if not updates:
        return {"written": 0, "skipped": 0, "errors": 0}
    with _connect(db_path) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS propagator_audit_log (
               id                   INTEGER PRIMARY KEY AUTOINCREMENT,
               run_at               TEXT NOT NULL,
               task_gid             TEXT NOT NULL,
               task_name            TEXT,
               parent_gid           TEXT,
               parent_name          TEXT,
               relationship         TEXT,
               previous_stock_action TEXT,
               new_stock_action     TEXT
            )"""
        )
        written = errors = 0
        for row in updates:
            try:
                conn.execute(
                    """INSERT INTO propagator_audit_log
                       (run_at, task_gid, task_name, parent_gid, parent_name,
                        relationship, previous_stock_action, new_stock_action)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        run_at,
                        row.get("task_gid"),
                        row.get("task_name"),
                        row.get("parent_gid"),
                        row.get("parent_name"),
                        row.get("relationship"),
                        row.get("current_stock_action"),
                        row.get("new_stock_action"),
                    ),
                )
                written += 1
            except Exception as exc:
                log.error("propagator_audit_log insert failed: %s", exc)
                errors += 1
        _log_write(conn, "propagator_audit_log", None, "insert_batch",
                   "parts_control_propagator", ["run_at", "task_gid"])
        conn.commit()
    return {"written": written, "skipped": 0, "errors": errors}


def log_asana_write(row: dict, source: str = "asana_executer",
                    db_path: str = DEFAULT_DB_PATH) -> dict:
    required = {"task_gid", "action_type"}
    if not required.issubset(row):
        raise ValueError(f"log_asana_write: missing required fields {required - row.keys()}")
    with _connect(db_path) as conn:
        conn.execute(
            """INSERT INTO asana_write_log
               (task_gid, task_name, source, email_id, action_type,
                field_name, field_gid, value_applied, result, error_detail)
               VALUES (:task_gid, :task_name, :source, :email_id, :action_type,
                       :field_name, :field_gid, :value_applied, :result, :error_detail)""",
            {**row, "source": source},
        )
        conn.commit()
    return {"written": 1, "skipped": 0, "errors": 0}


# ---------------------------------------------------------------------------
# Public API — Ordering pipeline (called by ordering skill scripts)
# ---------------------------------------------------------------------------

import re as _re


def normalise_vendor_name(raw: str | None) -> str | None:
    """Canonical vendor normalisation: lower, strip punctuation, collapse whitespace.

    'R.S. Components Ltd.' → 'rs components ltd'
    Use this everywhere vendor names are compared across scripts.
    """
    if not raw:
        return None
    name = raw.lower()
    name = _re.sub(r"[^a-z0-9 ]", "", name)
    name = _re.sub(r"\s+", " ", name).strip()
    return name or None


def write_ordering_queue_item(row: dict, db_path: str = DEFAULT_DB_PATH) -> dict:
    """Upsert one item into ordering_queue by asana_task_gid.

    Caller is responsible for setting vendor_name_normalised before passing row.
    """
    required = {"asana_task_gid"}
    if not required.issubset(row):
        raise ValueError(f"write_ordering_queue_item: missing asana_task_gid")
    row = dict(row)
    if "vendor_name" in row and "vendor_name_normalised" not in row:
        row["vendor_name_normalised"] = normalise_vendor_name(row["vendor_name"])
    with _connect(db_path) as conn:
        cols = list(row.keys())
        placeholders = ", ".join("?" * len(cols))
        updates = ", ".join(
            f"{c} = excluded.{c}" for c in cols if c != "asana_task_gid"
        )
        conn.execute(
            f"INSERT INTO ordering_queue ({', '.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT(asana_task_gid) DO UPDATE SET {updates}",
            list(row.values()),
        )
        _log_write(conn, "ordering_queue", row["asana_task_gid"],
                   "upsert", "ordering_pipeline", cols)
        conn.commit()
    return {"written": 1, "skipped": 0, "errors": 0}


def update_ordering_queue_status(
    task_gid: str,
    status: str,
    db_path: str = DEFAULT_DB_PATH,
    **kwargs,
) -> dict:
    """Update status + any additional columns on an ordering_queue row.

    kwargs: any column=value pairs to set alongside status
    (e.g. ordered_at=..., arrived_qty=..., order_task_gid=...)
    """
    valid_statuses = {"grouped", "ordered", "arrived", "error", "cancelled"}
    if status not in valid_statuses:
        raise ValueError(f"update_ordering_queue_status: invalid status '{status}'")
    updates = {"status": status, **kwargs}
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    with _connect(db_path) as conn:
        conn.execute(
            f"UPDATE ordering_queue SET {set_clause} WHERE asana_task_gid = ?",
            [*updates.values(), task_gid],
        )
        _log_write(conn, "ordering_queue", task_gid, "update",
                   "ordering_pipeline", list(updates.keys()))
        conn.commit()
    return {"written": 1, "skipped": 0, "errors": 0}


def update_arrived_reset_pending(
    task_gid: str,
    value: int,
    db_path: str = DEFAULT_DB_PATH,
) -> dict:
    """Set arrived_reset_pending flag on an ordering_queue row.

    value=1: REQUESTS/ file has been filed to reset Arrived field — skip on next run.
    value=0: SAP has confirmed reset — safe to process again.
    """
    if value not in (0, 1):
        raise ValueError("update_arrived_reset_pending: value must be 0 or 1")
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE ordering_queue SET arrived_reset_pending = ? WHERE asana_task_gid = ?",
            (value, task_gid),
        )
        _log_write(conn, "ordering_queue", task_gid, "update",
                   "arrived_stock_processor", ["arrived_reset_pending"])
        conn.commit()
    return {"written": 1, "skipped": 0, "errors": 0}


def write_ordering_edge_case(row: dict, db_path: str = DEFAULT_DB_PATH) -> dict:
    """Insert one edge case record into ordering_edge_cases. Immutable — never updated."""
    required = {"run_timestamp", "script", "issue_type"}
    if not required.issubset(row):
        raise ValueError(f"write_ordering_edge_case: missing {required - row.keys()}")
    # Optional columns default to NULL. Without this, any caller that omits one
    # (every caller omitted asana_edge_case_gid) raised
    # sqlite3.ProgrammingError mid-loop and aborted the whole run — which is why
    # ordering_edge_cases stayed empty and the nightly routing audit exited 1.
    row = {
        "stage": None,
        "asana_task_gid": None,
        "task_name": None,
        "vendor_name": None,
        "description": None,
        "raw_data": None,
        "asana_edge_case_gid": None,
        **row,
    }
    with _connect(db_path) as conn:
        conn.execute(
            """INSERT INTO ordering_edge_cases
               (run_timestamp, script, stage, asana_task_gid, task_name,
                vendor_name, issue_type, description, raw_data, asana_edge_case_gid)
               VALUES (:run_timestamp, :script, :stage, :asana_task_gid, :task_name,
                       :vendor_name, :issue_type, :description, :raw_data,
                       :asana_edge_case_gid)""",
            row,
        )
        _log_write(conn, "ordering_edge_cases", row.get("asana_task_gid"),
                   "insert", row["script"], list(row.keys()))
        conn.commit()
    return {"written": 1, "skipped": 0, "errors": 0}


# ---------------------------------------------------------------------------
# Public API — Email gateway documents + line items (added 2026-09-18)
#
# One row per inbound email / call (email_documents) and one row per extracted
# line item (email_line_items). This is the local, queryable memory of what each
# supplier told us: their quote / order / invoice numbers, prices, carriage,
# delivery dates, who actually sent it, which task it was matched to and what
# Giles decided. matcher.py reads it so an invoice quoting a supplier's order
# number can be matched to the confirmation that carried the same number, and
# it is the raw material for line-level cost mapping and price-over-time.
# ---------------------------------------------------------------------------

EMAIL_GATEWAY_DDL = [
    """CREATE TABLE IF NOT EXISTS email_documents (
        email_message_id      TEXT PRIMARY KEY,      -- Graph message id (stable)
        internet_message_id   TEXT,                  -- RFC message id (stable across folders)
        conversation_id       TEXT,
        received_at           TEXT,
        folder                TEXT,
        sender_email          TEXT,
        sender_name           TEXT,
        sender_domain         TEXT,
        subject               TEXT,
        document_type         TEXT,                  -- quote / order_confirmation / invoice / dispatch_note / update / call_notes / other
        triage                TEXT,                  -- supplier_doc / supplier_call / non_supplier_call / noise
        summary               TEXT,
        action_required       INTEGER,
        action_items          TEXT,                  -- JSON list
        has_issues            INTEGER,
        issue_summary         TEXT,
        -- references
        po_number             TEXT,                  -- Sesame PO as stated by the supplier
        sqr_number            TEXT,
        job_numbers           TEXT,                  -- JSON list
        supplier_quote_ref    TEXT,                  -- THEIR quote number
        supplier_order_ref    TEXT,                  -- THEIR order / acknowledgement number
        supplier_invoice_ref  TEXT,                  -- THEIR invoice number
        -- money / dates
        total_price_ex_vat    REAL,
        carriage_ex_vat       REAL,
        delivery_date         TEXT,
        lead_time             TEXT,
        -- who we think it is vs who the task says
        resolved_supplier_gid TEXT,                  -- from data/suppliers.json
        resolved_supplier     TEXT,
        supplier_how          TEXT,
        task_vendor_name      TEXT,                  -- Vendor field on the matched task at match time
        -- match
        proposed_task_gid     TEXT,
        proposed_task_name    TEXT,
        match_confidence      TEXT,
        matched_by            TEXT,
        alternatives          TEXT,                  -- JSON list of {gid,name,status}
        -- decision (filled by 05)
        decision              TEXT,                  -- A / M / R
        outcome               TEXT,                  -- APPROVED / AMENDED / REJECTED
        final_task_gid        TEXT,
        feedback              TEXT,
        learned               TEXT,
        decided_at            TEXT,
        -- call notes
        call_contact_label    TEXT,
        call_phone            TEXT,
        call_tasks            TEXT,                  -- JSON list
        attachment_names      TEXT,                  -- JSON list
        archive_path          TEXT,
        parser_version        TEXT,
        last_synced_at        TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS ix_email_documents_task ON email_documents(proposed_task_gid)",
    "CREATE INDEX IF NOT EXISTS ix_email_documents_final ON email_documents(final_task_gid)",
    "CREATE INDEX IF NOT EXISTS ix_email_documents_sup_order ON email_documents(supplier_order_ref)",
    "CREATE INDEX IF NOT EXISTS ix_email_documents_sup_quote ON email_documents(supplier_quote_ref)",
    "CREATE INDEX IF NOT EXISTS ix_email_documents_sender ON email_documents(sender_email)",
    "CREATE INDEX IF NOT EXISTS ix_email_documents_received ON email_documents(received_at)",
    """CREATE TABLE IF NOT EXISTS email_line_items (
        line_key              TEXT PRIMARY KEY,      -- <email_message_id>#<line_no>
        email_message_id      TEXT NOT NULL,
        line_no               INTEGER NOT NULL,
        received_at           TEXT,
        document_type         TEXT,
        supplier_gid          TEXT,
        supplier              TEXT,
        task_gid              TEXT,                  -- order task the email was matched to (final if decided, else proposed)
        po_number             TEXT,
        part_ref              TEXT,                  -- supplier's part number / code as written
        description           TEXT,
        qty                   REAL,
        unit_price            REAL,
        line_total            REAL,
        notes                 TEXT,
        sesame_part_no        TEXT,                  -- filled later by line→part mapping
        parts_control_gid     TEXT,                  -- filled later by line→part mapping
        mapping_confidence    TEXT,
        last_synced_at        TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS ix_email_line_items_email ON email_line_items(email_message_id)",
    "CREATE INDEX IF NOT EXISTS ix_email_line_items_task ON email_line_items(task_gid)",
    "CREATE INDEX IF NOT EXISTS ix_email_line_items_part ON email_line_items(part_ref)",
]


def ensure_email_gateway_schema(db_path: str = DEFAULT_DB_PATH) -> None:
    """Additive, idempotent. Safe to call at the top of every gateway run."""
    with _connect(db_path) as conn:
        for ddl in EMAIL_GATEWAY_DDL:
            conn.execute(ddl)
        conn.commit()


def upsert_email_documents(rows: list[dict], source: str = "email_pipeline",
                           db_path: str = DEFAULT_DB_PATH) -> dict:
    """One row per email. Re-running a stage over the same email updates it (change-detected)."""
    ensure_email_gateway_schema(db_path)
    with _connect(db_path) as conn:
        return _upsert_rows(conn, "email_documents", rows, "email_message_id", source, db_path)


def replace_email_line_items(email_message_id: str, lines: list[dict],
                             source: str = "email_pipeline", db_path: str = DEFAULT_DB_PATH) -> dict:
    """Line items are replaced as a set per email (a re-parse should not leave stale lines)."""
    ensure_email_gateway_schema(db_path)
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM email_line_items WHERE email_message_id = ?", (email_message_id,))
        rows = []
        for i, ln in enumerate(lines, start=1):
            r = dict(ln)
            r["line_key"] = f"{email_message_id}#{i}"
            r["email_message_id"] = email_message_id
            r["line_no"] = i
            rows.append(r)
        res = _upsert_rows(conn, "email_line_items", rows, "line_key", source, db_path) if rows else \
            {"written": 0, "skipped": 0, "errors": 0}
        conn.commit()
        return res


def update_email_document_decision(email_message_id: str, decision: str, outcome: str,
                                   final_task_gid: str | None, feedback: str = "", learned: str = "",
                                   source: str = "sheet_approver", db_path: str = DEFAULT_DB_PATH) -> dict:
    """Record Giles' decision against the email and re-point its line items at the final task."""
    ensure_email_gateway_schema(db_path)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with _connect(db_path) as conn:
        cur = conn.execute(
            """UPDATE email_documents SET decision = ?, outcome = ?, final_task_gid = ?, feedback = ?,
                      learned = ?, decided_at = ?, last_synced_at = ?
               WHERE email_message_id = ?""",
            (decision, outcome, final_task_gid or None, feedback or None, learned or None, now, now, email_message_id))
        if final_task_gid:
            conn.execute("UPDATE email_line_items SET task_gid = ?, last_synced_at = ? WHERE email_message_id = ?",
                         (final_task_gid, now, email_message_id))
        _log_write(conn, "email_documents", email_message_id, "update", source,
                   ["decision", "outcome", "final_task_gid"])
        conn.commit()
        return {"written": cur.rowcount, "skipped": 0, "errors": 0}


# ---------------------------------------------------------------------------
# Public API — Invoice gate documents (added 2026-09-18)
# One row per invoice PDF the gate has seen, whatever its source (email attachment
# or a file dropped into the Dropbox 'to be signed off' folder). Keyed on the file's
# SHA-256 so the same PDF arriving twice is recognised. This is also the duplicate
# guard: (supplier, invoice number) must never be filed twice.
# ---------------------------------------------------------------------------

INVOICE_DDL = [
    """CREATE TABLE IF NOT EXISTS invoice_documents (
        file_sha256           TEXT PRIMARY KEY,
        source                TEXT,                  -- email | dropbox
        source_ref            TEXT,                  -- email_message_id or original dropbox path
        original_name         TEXT,
        seen_at               TEXT,
        sender_domain         TEXT,
        supplier              TEXT,
        po_number             TEXT,
        task_gid              TEXT,
        task_name             TEXT,
        invoice_number        TEXT,
        invoice_date          TEXT,
        doc_gross             REAL,                  -- largest total printed on the document
        expected_gross        REAL,                  -- (Price Ex VAT + Carriage) x 1.2 from Asana
        verdict               TEXT,                  -- AUTO | CHECK
        conditions            TEXT,                  -- JSON {name: [pass, reason]}
        flags                 TEXT,                  -- JSON list
        suggested_task_gid    TEXT,                  -- when no PO in text but a confirmation ref points somewhere
        filed_folder          TEXT,                  -- 'AI SIGNED OFF' | '2. invoice queries' | '3. INVOICES - Signed Off'
        filed_name            TEXT,
        filed_at              TEXT,
        dropbox_url           TEXT,
        sheet_decision        TEXT,                  -- A / M / R
        request_id            TEXT,                  -- REQUESTS/ id of the Asana comment
        human_signed_off_at   TEXT,                  -- when the nightly mover saw Signed OFF = 'Signed Off '
        moved_to_signed_off_at TEXT,
        gate_version          TEXT,
        last_synced_at        TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS ix_invoice_documents_task ON invoice_documents(task_gid)",
    "CREATE INDEX IF NOT EXISTS ix_invoice_documents_inv ON invoice_documents(supplier, invoice_number)",
    "CREATE INDEX IF NOT EXISTS ix_invoice_documents_state ON invoice_documents(filed_folder, moved_to_signed_off_at)",
]


def ensure_invoice_schema(db_path: str = DEFAULT_DB_PATH) -> None:
    with _connect(db_path) as conn:
        for ddl in INVOICE_DDL:
            conn.execute(ddl)
        conn.commit()


def upsert_invoice_documents(rows: list[dict], source: str = "invoice_gate",
                             db_path: str = DEFAULT_DB_PATH) -> dict:
    ensure_invoice_schema(db_path)
    with _connect(db_path) as conn:
        return _upsert_rows(conn, "invoice_documents", rows, "file_sha256", source, db_path)


def update_invoice_document(file_sha256: str, fields: dict, source: str = "invoice_gate",
                            db_path: str = DEFAULT_DB_PATH) -> dict:
    """Partial update by primary key (decision, request id, mover timestamps)."""
    ensure_invoice_schema(db_path)
    allowed = {"sheet_decision", "request_id", "human_signed_off_at", "moved_to_signed_off_at",
               "filed_folder", "filed_name", "filed_at", "dropbox_url", "task_gid", "task_name"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return {"written": 0, "skipped": 1, "errors": 0}
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sets = ", ".join(f"{k} = ?" for k in fields) + ", last_synced_at = ?"
    with _connect(db_path) as conn:
        cur = conn.execute(f"UPDATE invoice_documents SET {sets} WHERE file_sha256 = ?",
                           list(fields.values()) + [now, file_sha256])
        _log_write(conn, "invoice_documents", file_sha256, "update", source, list(fields.keys()))
        conn.commit()
        return {"written": cur.rowcount, "skipped": 0, "errors": 0}
