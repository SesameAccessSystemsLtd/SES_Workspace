"""
migrate_impact_engine.py — Additive schema migration for the Parts Impact +
Improvement Engine (2026-09-09).

Idempotent. Adds columns and tables only. Never drops, never rewrites data.
Safe to re-run; each step checks for existence first.

What it adds
------------
1. parts_control        — the fields needed to answer "where is this part and what
                          is it holding up", including the order link that is
                          currently thrown away by sync_parts_control.py.
2. build_assembly_packs — the production-readiness fields that already exist in
                          Asana (PARTS - Readiness, STAGE of production,
                          STEEL PROFILE, NEEDS - from Purch) and are not mirrored.
3. procurement_tasks    — delivery-days, impact, scope (live/legacy/improvement).
4. part_supply_events   — the change stream. One row per real state change.
5. production_impact    — computed readiness + risk per build pack per run.
6. improvement_cases    — the improvement engine: edge case → assessment → action.
7. improvement_observations — raw signals attached to a case.
8. metric_snapshots     — the validation spine. Proves an amendment worked.
9. po_number_ledger     — fast PO number allocation with an authoritative counter.

Usage:
    python migrate_impact_engine.py            # apply
    python migrate_impact_engine.py --dry-run  # show what would change
"""

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))
from path_config import p as _p

DB_PATH = _p("db.ops")

# ---------------------------------------------------------------------------
# 1-3 · Additive columns on existing mirror tables
# ---------------------------------------------------------------------------

NEW_COLUMNS = {
    "parts_control": [
        ("order_status",        "TEXT"),      # ORDER Status enum  1143955334207040
        ("arrived_qty",         "REAL"),      # Arrived Stock Qty  1216071767931108
        ("unit_price",          "REAL"),      # Unit Price £       1182371951145356
        ("rev_number",          "TEXT"),      # Rev Number         1214229889503630
        ("po_task_gid",         "TEXT"),      # PO TASK reference  1213595302673057
        ("stock_link_gid",      "TEXT"),      # Stock Link ref     1214966724754842
        # build_assembly_pack_gid actually holds the referenced pack NAME on
        # 1,470 of 1,519 populated rows (only 44 hold a real GID, written by
        # update_parts_pack_allocation). Store the name honestly here and let the
        # resolver populate the real GID by name lookup.
        ("build_pack_ref_name", "TEXT"),
        ("due_on",              "TEXT"),
        ("completed_at",        "TEXT"),
        ("project_code",        "TEXT"),      # parsed job number, e.g. 1597
        ("supply_route",        "TEXT"),      # derived: lasercut|subcon|buy|inhouse|stock
        ("first_seen_at",       "TEXT"),
        ("last_state_hash",     "TEXT"),      # change detection — see part_supply_events
    ],
    "build_assembly_packs": [
        ("steel_profile",       "TEXT"),      # STEEL PROFILE      1142754335683355
        ("needs_from_purch",    "TEXT"),      # NEEDS - from Purch 1207196303096574
        ("due_on",              "TEXT"),
        ("estimated_hours",     "REAL"),      # Estimated Hours    1155021669124711
        ("unplanned_hours",     "REAL"),      # "Actual Cost"      1155021669124674
        ("requires_debrief",    "TEXT"),      # Requires De-Brief  1201006767756706
        ("project_code",        "TEXT"),
        ("is_completed",        "INTEGER NOT NULL DEFAULT 0"),
        ("last_state_hash",     "TEXT"),
    ],
    "procurement_tasks": [
        ("days_for_delivery",   "REAL"),      # Days for delivery  1208706270294055
        ("impact",              "TEXT"),      # IMPACT enum        1148052247637687
        ("po_task_gid",         "TEXT"),
        ("is_completed",        "INTEGER NOT NULL DEFAULT 0"),
        ("project_code",        "TEXT"),
        ("scope",               "TEXT"),      # live|legacy|improvement|unknown
        ("last_state_hash",     "TEXT"),
    ],
}

# ---------------------------------------------------------------------------
# 4-9 · New tables
# ---------------------------------------------------------------------------

NEW_TABLES = {

    # -- 4 · the change stream -------------------------------------------------
    # One row per REAL change. This is what db_write_log was never able to be:
    # it logs every column of every row on every one of nine daily syncs.
    "part_supply_events": """
        CREATE TABLE part_supply_events (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_type     TEXT NOT NULL
                                CHECK(entity_type IN ('part','order','pack','requirement')),
            entity_gid      TEXT NOT NULL,
            project_code    TEXT,
            field           TEXT NOT NULL,
            old_value       TEXT,
            new_value       TEXT,
            observed_at     TEXT NOT NULL,
            source          TEXT NOT NULL,
            significance    TEXT                       -- routine|notable|critical
        )
    """,

    # -- 5 · computed impact per pack -----------------------------------------
    # Rebuilt each run. History retained so trend and validation are possible.
    "production_impact": """
        CREATE TABLE production_impact (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at                  TEXT NOT NULL,
            project_code            TEXT,
            pack_gid                TEXT,
            pack_reference          TEXT,
            pack_name               TEXT,
            production_due_on       TEXT,
            due_date_source         TEXT,   -- asana|forecast_lasercut|forecast_longlead|none
            parts_total             INTEGER NOT NULL DEFAULT 0,
            parts_ready             INTEGER NOT NULL DEFAULT 0,
            parts_on_order          INTEGER NOT NULL DEFAULT 0,
            parts_unordered         INTEGER NOT NULL DEFAULT 0,
            parts_unknown           INTEGER NOT NULL DEFAULT 0,
            lasercut_state          TEXT,   -- not_needed|not_assessed|ready_to_cut|all_cut|on_order|delivered
            lasercut_eta            TEXT,
            longest_lead_eta        TEXT,
            longest_lead_part       TEXT,
            longest_lead_vendor     TEXT,
            blocking_part_count     INTEGER NOT NULL DEFAULT 0,
            risk_score              REAL,
            risk_band               TEXT,   -- clear|watch|at_risk|blocked
            risk_reason             TEXT,
            next_action             TEXT,
            action_owner            TEXT,
            expedite_flag           INTEGER NOT NULL DEFAULT 0,
            created_at              TEXT DEFAULT (datetime('now'))
        )
    """,

    # -- 6 · the improvement engine -------------------------------------------
    # An edge case becomes a case; a case gets assessed; an assessment produces a
    # staged action; the action is validated against a metric. Nothing is closed
    # on assertion — only on a measured change.
    "improvement_cases": """
        CREATE TABLE improvement_cases (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            signature           TEXT UNIQUE NOT NULL,   -- stable dedupe key
            category            TEXT NOT NULL,          -- taxonomy slug
            title               TEXT NOT NULL,
            description         TEXT,
            first_seen_at       TEXT NOT NULL,
            last_seen_at        TEXT NOT NULL,
            occurrences         INTEGER NOT NULL DEFAULT 1,
            sources             TEXT,                   -- JSON list of harvesters that saw it
            example_gids        TEXT,                   -- JSON list, capped
            friction_score      REAL,                   -- occurrences x weight x recency
            state               TEXT NOT NULL DEFAULT 'new'
                                    CHECK(state IN ('new','assessing','staged',
                                                    'validating','proven','rejected','dormant')),
            assessment          TEXT,                   -- why it happens
            proposed_action     TEXT,                   -- what would remove it
            action_type         TEXT,                   -- eliminate|simplify|automate|delegate|retain
            owner               TEXT,
            metric_key          TEXT,                   -- FK-ish → metric_snapshots.metric_key
            baseline_value      REAL,
            baseline_at         TEXT,
            target_value        REAL,
            staged_at           TEXT,
            validated_at        TEXT,
            validation_note     TEXT,
            asana_task_gid      TEXT,
            created_at          TEXT DEFAULT (datetime('now')),
            updated_at          TEXT DEFAULT (datetime('now'))
        )
    """,

    "improvement_observations": """
        CREATE TABLE improvement_observations (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id         INTEGER NOT NULL REFERENCES improvement_cases(id),
            observed_at     TEXT NOT NULL,
            source          TEXT NOT NULL,
            entity_gid      TEXT,
            project_code    TEXT,
            detail          TEXT,
            raw             TEXT
        )
    """,

    # -- 7 · validation spine --------------------------------------------------
    # Every metric that a proposed amendment claims to move gets snapshotted on a
    # schedule. Before/after is then a query, not an opinion.
    "metric_snapshots": """
        CREATE TABLE metric_snapshots (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            metric_key      TEXT NOT NULL,
            captured_at     TEXT NOT NULL,
            value           REAL,
            denominator     REAL,
            unit            TEXT,
            window_days     INTEGER,
            detail          TEXT,
            source          TEXT NOT NULL
        )
    """,

    # -- 8 · fast PO number allocation ----------------------------------------
    # An authoritative counter. Never inferred from procurement_tasks.po_number,
    # which holds 933 distinct 5-digit prefixes across 1,529 rows because supplier
    # refs and invoice numbers have been entered in the same field.
    "po_number_ledger": """
        CREATE TABLE po_number_ledger (
            po_number       INTEGER PRIMARY KEY,
            suffix          TEXT,
            full_reference  TEXT,
            state           TEXT NOT NULL DEFAULT 'reserved'
                                CHECK(state IN ('reserved','confirmed','voided')),
            reserved_at     TEXT NOT NULL,
            confirmed_at    TEXT,
            voided_at       TEXT,
            void_reason     TEXT,
            requested_by    TEXT,
            vendor_name     TEXT,
            project_code    TEXT,
            asana_task_gid  TEXT,
            note            TEXT
        )
    """,
}

NEW_INDEXES = [
    ("idx_pse_entity",       "part_supply_events",   "(entity_gid, observed_at)"),
    ("idx_pse_project",      "part_supply_events",   "(project_code, observed_at)"),
    ("idx_pse_field",        "part_supply_events",   "(field, observed_at)"),
    ("idx_pi_run",           "production_impact",    "(run_at)"),
    ("idx_pi_project",       "production_impact",    "(project_code, run_at)"),
    ("idx_pi_band",          "production_impact",    "(risk_band, run_at)"),
    ("idx_ic_state",         "improvement_cases",    "(state, friction_score)"),
    ("idx_ic_category",      "improvement_cases",    "(category, last_seen_at)"),
    ("idx_io_case",          "improvement_observations", "(case_id, observed_at)"),
    ("idx_ms_key",           "metric_snapshots",     "(metric_key, captured_at)"),
    ("idx_pnl_state",        "po_number_ledger",     "(state, reserved_at)"),
    ("idx_pc_po_task",       "parts_control",        "(po_task_gid)"),
    ("idx_pc_proc_task",     "parts_control",        "(procurement_task_gid)"),
    ("idx_pc_project",       "parts_control",        "(project_code, is_completed)"),
    ("idx_bap_project",      "build_assembly_packs", "(project_code, is_completed)"),
    ("idx_pt_project",       "procurement_tasks",    "(project_code, scope)"),
]


def existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def index_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (name,)
    ).fetchone() is not None


def main() -> None:
    ap = argparse.ArgumentParser(description="Additive migration for the impact + improvement engine")
    ap.add_argument("--db-path", default=DB_PATH)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db_path)
    planned: list[str] = []

    for table, cols in NEW_COLUMNS.items():
        if not table_exists(conn, table):
            print(f"!! {table} does not exist — skipping its columns")
            continue
        have = existing_columns(conn, table)
        for col, decl in cols:
            if col not in have:
                planned.append(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")

    for table, ddl in NEW_TABLES.items():
        if not table_exists(conn, table):
            planned.append(ddl.strip())

    for name, table, cols in NEW_INDEXES:
        if not index_exists(conn, name):
            planned.append(f"CREATE INDEX {name} ON {table} {cols}")

    if not planned:
        print("Nothing to do — schema already current.")
        conn.close()
        return

    for stmt in planned:
        first = stmt.strip().splitlines()[0]
        print(("WOULD RUN: " if args.dry_run else "RUN: ") + first)

    if args.dry_run:
        print(f"\n{len(planned)} statement(s) pending. Re-run without --dry-run to apply.")
        conn.close()
        return

    for stmt in planned:
        conn.execute(stmt)
    conn.commit()
    conn.close()
    print(f"\nApplied {len(planned)} statement(s).")


if __name__ == "__main__":
    main()
