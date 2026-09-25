"""
db_init_ops.py — Initialise the OpenClaw ops.db SQLite database.

Creates the database file and all internal operations tables if they do not
already exist. Safe to re-run at any time — idempotent.

Database: ops.db — Internal operations

Email pipeline tables (written by 01-05 scripts):
    purchase_orders, email_po_matches, po_lines, asana_write_log

Legacy / utility tables:
    scraper_runs, asana_task_states, bom_requirements,
    supplier_deliveries, golden_index

Asana mirror tables (written by nightly sync scripts via db_writer.py):
    asana_field_registry, stock_inventory, build_assembly_packs,
    parts_control, suppliers, procurement_tasks, qc_issues

Audit table (written by db_writer.py):
    db_write_log

Usage:
    python db_init_ops.py
    python db_init_ops.py --db-path C:\\custom\\path\\ops.db
"""

import argparse
import logging
import os
import sqlite3
import sys

from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[3] / "lib"))
from path_config import p as _p

DEFAULT_DB_PATH = os.environ.get("OPENCLAW_OPS_DB_PATH") or _p("db.ops")

SCHEMA = """
CREATE TABLE IF NOT EXISTS scraper_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_name TEXT NOT NULL,
    source      TEXT NOT NULL,
    pulled_at   TIMESTAMP NOT NULL,
    row_count   INTEGER,
    status      TEXT NOT NULL CHECK(status IN ('success', 'partial', 'failed')),
    notes       TEXT,
    file_path   TEXT
);

CREATE TABLE IF NOT EXISTS asana_task_states (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    asana_task_id   TEXT NOT NULL,
    project_name    TEXT,
    task_name       TEXT,
    status          TEXT,
    due_date        DATE,
    recorded_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    source_run_id   INTEGER REFERENCES scraper_runs(id)
);

CREATE TABLE IF NOT EXISTS purchase_orders (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    po_number           TEXT UNIQUE,
    supplier_name       TEXT NOT NULL,
    supplier_email      TEXT,
    part_numbers        TEXT,
    order_date          DATE,
    expected_delivery   DATE,
    confirmed_delivery  DATE,
    status              TEXT NOT NULL DEFAULT 'pending_review'
                            CHECK(status IN ('pending_review','acknowledged','overdue','complete','cancelled')),
    asana_task_gid      TEXT,
    source_email_id     TEXT,
    notes               TEXT,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS bom_requirements (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    build_ref           TEXT NOT NULL,
    asana_project_gid   TEXT,
    sesame_part_no      TEXT NOT NULL,
    part_description    TEXT,
    required_qty        INTEGER NOT NULL,
    stock_qty_at_import INTEGER,
    status              TEXT NOT NULL DEFAULT 'unchecked'
                            CHECK(status IN ('unchecked','in_stock','needs_order','ordered','received')),
    po_id               INTEGER REFERENCES purchase_orders(id),
    asana_task_gid      TEXT,
    source_bom_file     TEXT,
    imported_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS supplier_deliveries (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    po_id               INTEGER REFERENCES purchase_orders(id),
    supplier_name       TEXT NOT NULL,
    delivery_date       DATE NOT NULL,
    expected_date       DATE,
    days_variance       INTEGER,
    items_received      TEXT,
    shortages           TEXT,
    quality_flag        TEXT NOT NULL DEFAULT 'ok'
                            CHECK(quality_flag IN ('ok','query','rejected')),
    quality_notes       TEXT,
    received_by         TEXT,
    goods_in_asana_gid  TEXT,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS email_po_matches (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    email_message_id        TEXT,
    email_subject           TEXT,
    received_at             TIMESTAMP,
    supplier_name           TEXT,
    attachment_filename     TEXT,
    extracted_po_number     TEXT,
    extracted_order_ref     TEXT,
    asana_task_gid_matched  TEXT,
    asana_task_name         TEXT,
    match_confidence        TEXT NOT NULL DEFAULT 'no_match'
                                CHECK(match_confidence IN ('exact','partial','no_match')),
    comment_written         INTEGER NOT NULL DEFAULT 0 CHECK(comment_written IN (0,1)),
    comment_gid             TEXT,
    verified_correct        INTEGER CHECK(verified_correct IN (0,1)),
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS po_lines (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    po_id           INTEGER NOT NULL REFERENCES purchase_orders(id),
    sesame_part_no  TEXT,
    description     TEXT,
    ordered_qty     INTEGER,
    allocated_qty   INTEGER,      -- qty earmarked for bom_req_id project demand; remainder goes to stock
    unit_price      REAL,
    line_status     TEXT NOT NULL DEFAULT 'ordered'
                        CHECK(line_status IN ('ordered','confirmed','dispatched',
                                              'received','short','cancelled')),
    asana_task_gid  TEXT,
    bom_req_id      INTEGER REFERENCES bom_requirements(id),
    confirmed_at    TIMESTAMP,
    received_at     TIMESTAMP,
    notes           TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS golden_index (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    sesame_part_no      TEXT UNIQUE NOT NULL,
    kan_number          TEXT,
    asana_task_gid      TEXT,
    asana_task_url      TEXT,
    stock_project       TEXT,
    description         TEXT,
    stock_qty           INTEGER,
    trigger_order_qty   INTEGER,
    stock_location      TEXT,
    drawing_url         TEXT,
    match_confidence    INTEGER,
    last_synced         TIMESTAMP,
    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS asana_write_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    task_gid        TEXT NOT NULL,
    task_name       TEXT,
    source          TEXT,
    email_id        TEXT,
    action_type     TEXT,
    field_name      TEXT,
    field_gid       TEXT,
    value_applied   TEXT,
    result          TEXT NOT NULL DEFAULT 'pending'
                        CHECK(result IN ('success','failed','skipped','pending')),
    error_detail    TEXT,
    executed_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- =========================================================================
-- ASANA MIRROR TABLES — written exclusively by nightly sync scripts
-- via db_writer.py. No agent or pipeline writes to these directly.
-- =========================================================================

CREATE TABLE IF NOT EXISTS asana_field_registry (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    field_name    TEXT UNIQUE NOT NULL,
    field_gid     TEXT NOT NULL,
    field_type    TEXT,
    applies_to    TEXT,
    enum_options  TEXT,
    notes         TEXT
);

INSERT OR IGNORE INTO asana_field_registry (field_name, field_gid, field_type, applies_to, notes) VALUES
    ('price_ex_vat',        '854330340727040',  'number',    'procurement_tasks',               'Price Ex VAT on order/quote tasks.'),
    ('delivery_date',       '1208706270294053', 'date',      'procurement_tasks',               'Expected/confirmed delivery date.'),
    ('order_status',        '1143955334207040', 'enum',      'procurement_tasks',               'ORDER Status field. Delivery Date CONFIRMED = 1203944084697376 ("On Order - Delivery Date CONFIRMED"). 1143955334207043 is "On Order - Awaiting CONFIRMATION", NOT confirmed. Options get renamed/added/disabled: resolve by label from the live field before writing (execute_requests.py does).'),
    ('sesame_part_no',      '1213228090549470', 'text',      'stock_inventory, parts_control',  'Full part number e.g. 1234.1008.h.234. Never strip when writing to Asana.'),
    ('kan_number',          '1206590288351824', 'text',      'stock_inventory, bom_pipeline',   'KAN reference number e.g. KAN-330. Set from golden_index mapping. Central to stock identity — must not be guessed or inferred from description.'),
    ('stock_qty',           '1126782469949969', 'number',    'stock_inventory (KANBAN)',         'Current physical stock quantity.'),
    ('trigger_order_qty',   '1198179728033183', 'number',    'stock_inventory (KANBAN)',         'Reorder threshold quantity.'),
    ('min_stock_qty',       '1205084645800516', 'number',    'stock_inventory (Consumables)',    'Minimum stock threshold — Consumables project only.'),
    ('order_qty',           '1201447044882111', 'number',    'parts_control',                   'Quantity on order for this part.'),
    ('vendor',              '854127216887083',  'text',      'parts_control, stock_inventory',  'Vendor name text field.'),
    ('stock_action',        '1199111659373956', 'enum',      'parts_control, stock_inventory',  'Stock action enum: IN STOCK / ORDER FOR STOCK etc.'),
    ('build_packs_reference','1211660742155293','reference', 'parts_control',                   'Links a Parts Control task to its Build Assembly Pack task GID. ONLY ever set to a build/assembly pack task GID. Never set to a KANBAN stock task GID — stock task linkage is a separate concept requiring its own field.');

CREATE TABLE IF NOT EXISTS stock_inventory (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    asana_task_gid      TEXT UNIQUE NOT NULL,
    sesame_pn           TEXT UNIQUE NOT NULL,
    kan_number          TEXT,
    description         TEXT,
    stock_qty           REAL DEFAULT 0,
    trigger_order_qty   REAL,
    min_stock_qty       REAL,
    stock_location      TEXT,
    vendor_part_number  TEXT,
    stock_action        TEXT,
    supplier_type       TEXT,
    drawing_url         TEXT,
    stock_project       TEXT,
    is_subtask          INTEGER NOT NULL DEFAULT 0,
    last_synced_at      TEXT,
    created_at          TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_stock_sesame_pn ON stock_inventory(sesame_pn);
CREATE INDEX IF NOT EXISTS idx_stock_kan       ON stock_inventory(kan_number);
CREATE INDEX IF NOT EXISTS idx_stock_vendor_pn ON stock_inventory(vendor_part_number);

CREATE TABLE IF NOT EXISTS build_assembly_packs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    asana_task_gid      TEXT UNIQUE NOT NULL,
    pack_reference      TEXT NOT NULL,
    pack_name           TEXT NOT NULL,
    revision            TEXT,
    status              TEXT,
    approval_status     TEXT,
    parts_readiness     TEXT,
    stage_of_production TEXT,
    ai_processed_status TEXT,
    url_to_drawing      TEXT,
    source_project_gid  TEXT,
    project_name        TEXT,
    last_synced_at      TEXT,
    created_at          TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_bap_reference ON build_assembly_packs(pack_reference);

CREATE TABLE IF NOT EXISTS parts_control (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    asana_task_gid          TEXT UNIQUE NOT NULL,
    sesame_pn               TEXT,
    part_name               TEXT NOT NULL,
    order_qty               REAL,
    stock_action            TEXT,
    vendor_name             TEXT,
    build_assembly_pack_gid TEXT,
    procurement_task_gid    TEXT,
    is_subtask              INTEGER NOT NULL DEFAULT 0,
    is_completed            INTEGER NOT NULL DEFAULT 0,
    project_name            TEXT,
    last_synced_at          TEXT,
    created_at              TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_pc_sesame_pn  ON parts_control(sesame_pn);
CREATE INDEX IF NOT EXISTS idx_pc_vendor     ON parts_control(vendor_name);
CREATE INDEX IF NOT EXISTS idx_pc_bap        ON parts_control(build_assembly_pack_gid);
CREATE INDEX IF NOT EXISTS idx_pc_proc_task  ON parts_control(procurement_task_gid);

CREATE TABLE IF NOT EXISTS suppliers (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    asana_task_gid          TEXT UNIQUE,
    vendor_name             TEXT UNIQUE NOT NULL COLLATE NOCASE,
    primary_domain          TEXT UNIQUE,
    contact_name            TEXT,
    contact_email           TEXT,
    contact_phone           TEXT,
    payment_type            TEXT,
    risk_profile            TEXT,
    typical_lead_times_days INTEGER,
    supplier_type           TEXT,
    asana_tag_gid           TEXT,
    last_synced_at          TEXT,
    created_at              TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_sup_domain ON suppliers(primary_domain);
CREATE INDEX IF NOT EXISTS idx_sup_name   ON suppliers(vendor_name);

CREATE TABLE IF NOT EXISTS procurement_tasks (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    asana_task_gid      TEXT UNIQUE NOT NULL,
    task_name           TEXT NOT NULL,
    document_type       TEXT,
    po_number           TEXT,
    sqr_number          TEXT,
    price_ex_vat        REAL,
    order_date          TEXT,
    due_date            TEXT,
    order_status        TEXT,
    vendor_name         TEXT,
    vendor_gid          TEXT,
    source_project      TEXT,
    is_subtask          INTEGER NOT NULL DEFAULT 0,
    last_synced_at      TEXT,
    created_at          TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_pt_po_number  ON procurement_tasks(po_number);
CREATE INDEX IF NOT EXISTS idx_pt_sqr_number ON procurement_tasks(sqr_number);
CREATE INDEX IF NOT EXISTS idx_pt_vendor     ON procurement_tasks(vendor_gid);

CREATE TABLE IF NOT EXISTS qc_issues (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    asana_task_gid          TEXT UNIQUE NOT NULL,
    ncr_id                  TEXT,
    issue_name              TEXT NOT NULL,
    sesame_part_no          TEXT,
    build_pack_reference    TEXT,
    linked_to_buildpack     TEXT,
    parts_control_gid       TEXT,
    po_task_gid             TEXT,
    associated_po_number    TEXT,
    vendor                  TEXT,
    supplier_email          TEXT,
    associated_sqr_number   TEXT,
    qc_status               TEXT,
    order_status            TEXT,
    build_impact            TEXT,
    impact                  TEXT,
    reason_for_issue        TEXT,
    qc_issue_reason         TEXT,
    needs_from_purch        TEXT,
    project_name            TEXT,
    date_raised             TEXT,
    needed_by               TEXT,
    days_since_raised       TEXT,
    order_qty               REAL,
    order_value             REAL,
    completed               INTEGER DEFAULT 0,
    asana_completed_at      TEXT,
    asana_created_at        TEXT,
    asana_modified_at       TEXT,
    last_synced_at          TEXT,
    created_at              TEXT DEFAULT (datetime('now'))
);
-- Source: Asana QC project 737971594126804 ("1.0 - QC issues / Parts Needed / Causing delays").
-- NOTE: previously this table was populated from the WRONG project (902269769783650 = the
-- purchasing PO log). Repointed 2026-09-23; see skills/sql_db/scripts/sync_qc_issues.py.
CREATE INDEX IF NOT EXISTS idx_qc_ncr   ON qc_issues(ncr_id);
CREATE INDEX IF NOT EXISTS idx_qc_part  ON qc_issues(sesame_part_no);
CREATE INDEX IF NOT EXISTS idx_qc_pack  ON qc_issues(build_pack_reference);
CREATE INDEX IF NOT EXISTS idx_qc_po    ON qc_issues(associated_po_number);
CREATE INDEX IF NOT EXISTS idx_qc_sqr   ON qc_issues(associated_sqr_number);
CREATE INDEX IF NOT EXISTS idx_qc_status ON qc_issues(qc_status);
CREATE INDEX IF NOT EXISTS idx_qc_vendor ON qc_issues(vendor);
CREATE INDEX IF NOT EXISTS idx_qc_completed ON qc_issues(completed);

-- =========================================================================
-- AUDIT TABLE — written by db_writer.py, append-only
-- =========================================================================

CREATE TABLE IF NOT EXISTS db_write_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name    TEXT NOT NULL,
    row_gid       TEXT,
    action        TEXT NOT NULL,
    source        TEXT NOT NULL,
    fields_changed TEXT,
    written_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)


def init_db(db_path: str) -> None:
    db_dir = os.path.dirname(db_path)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)
        log.info("Created directory: %s", db_dir)

    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
        log.info("ops.db initialised at: %s", db_path)

        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = [row[0] for row in cursor.fetchall()]
        log.info("Tables: %s", ", ".join(tables))
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialise the OpenClaw ops.db SQLite database.")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH, help="Path to the SQLite database file.")
    args = parser.parse_args()

    try:
        init_db(args.db_path)
    except Exception as exc:
        log.error("Failed to initialise ops.db: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
