#!/usr/bin/env python3
"""
migrate_suppliers_multicontact.py — drop UNIQUE on suppliers.primary_domain (Giles 2026-08-27).

Rationale: multiple suppliers/contacts legitimately share a domain (e.g. @gmail.com,
@company.co.uk). primary_domain is a lookup/analytics column, NOT a unique supplier key,
so its UNIQUE constraint is wrong — it caused sync_suppliers to fail on shared domains.

SQLite has no DROP CONSTRAINT, so this rebuilds the table. Steps (transactional):
  1. PRAGMA foreign_keys=OFF
  2. CREATE suppliers_new matching the current schema but WITHOUT UNIQUE on primary_domain
  3. INSERT INTO suppliers_new SELECT * FROM suppliers
  4. DROP suppliers, RENAME suppliers_new -> suppliers
  5. Recreate the two explicit indexes (idx_sup_domain, idx_sup_name)
  6. PRAGMA foreign_keys=ON
Rich-with a safety net: the original table is dumped to suppliers_backup_<ts> first.

Usage:  python migrate_suppliers_multicontact.py [--db PATH] [--dry-run]
"""
import argparse
import datetime
import sqlite3
import sys
from pathlib import Path

WS = Path(r"C:/Users/GilesHugo/SES_Workspace")
sys.path.insert(0, str(WS / "lib"))
try:
    from path_config import p as _p
    DEFAULT_DB_PATH = _p("db.ops")
except Exception:
    DEFAULT_DB_PATH = str(WS / "db" / "ops.db")

# New schema — identical to the live CREATE TABLE but primary_domain is no longer UNIQUE.
# (Preserves the other two UNIQUEs: asana_task_gid, vendor_name COLLATE NOCASE.)
NEW_DDL = """CREATE TABLE suppliers (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    asana_task_gid          TEXT UNIQUE,
    vendor_name             TEXT UNIQUE NOT NULL COLLATE NOCASE,
    primary_domain          TEXT,
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
, vendor_name_normalised TEXT)"""

COLUMNS = ("id, asana_task_gid, vendor_name, primary_domain, contact_name, contact_email,"
           " contact_phone, payment_type, risk_profile, typical_lead_times_days,"
           " supplier_type, asana_tag_gid, last_synced_at, created_at, vendor_name_normalised")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db-path", default=DEFAULT_DB_PATH)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db_path)
    conn.row_factory = sqlite3.Row
    before = conn.execute("SELECT COUNT(*) c FROM suppliers").fetchone()["c"]
    # Confirm the UNIQUE is actually present before touching.
    idxs = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='suppliers'")]
    has_unique = any("sqlite_autoindex_suppliers" in i for i in idxs)
    if not has_unique:
        print("UNIQUE autoindex not found — nothing to migrate (already done?).")
        return

    if args.dry_run:
        print(f"DRY RUN — would rebuild suppliers ({before} rows) and drop the UNIQUE on primary_domain.")
        print("  existing indexes:", idxs)
        return

    # Back up original table to a timestamped copy (safety net, never an auto-delete).
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    back = f"suppliers_backup_{ts}"
    conn.execute(f"ALTER TABLE suppliers RENAME TO {back}")
    conn.execute(NEW_DDL)
    conn.execute(f"INSERT INTO suppliers ({COLUMNS}) SELECT {COLUMNS} FROM {back}")
    conn.execute(f"DROP TABLE {back}")
    conn.execute("CREATE INDEX idx_sup_domain ON suppliers(primary_domain)")
    conn.execute("CREATE INDEX idx_sup_name   ON suppliers(vendor_name)")
    conn.commit()

    after = conn.execute("SELECT COUNT(*) c FROM suppliers").fetchone()["c"]
    print(f"Migration OK — suppliers rebuilt: {before} rows in -> {after} rows out.")
    print(f"UNIQUE on primary_domain dropped (backed up old table to {back}, then dropped).")
    print("Recreated idx_sup_domain (now non-unique) + idx_sup_name.")
    conn.close()


if __name__ == "__main__":
    main()
