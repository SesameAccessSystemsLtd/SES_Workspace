#!/usr/bin/env python3
"""
build_supplier_contacts.py — Stage P3/P4: per-contact supplier table.

Giles 2026-08-27: multiple contacts exist at each company, and outbound replies must
go back to WHO we received the quote from (the actual sender of the returned quote,
often a different person/email than the generic inbox, and often in a SEPARATE email
chain). A single `suppliers.contact_email` column can't model that.

This creates + seeds `supplier_contacts`:
    supplier_contacts (
        id INTEGER PK,
        supplier_gid TEXT,          -- FK-ish to suppliers.asana_task_gid
        supplier_name TEXT,
        contact_name TEXT,
        email TEXT,
        phone TEXT,
        is_primary INTEGER,          -- 1 = the primary contact from the Supplier project
        source TEXT,                 -- 'project' | 'quote_sender'
        created_at TEXT, last_synced_at TEXT
    )
Seeds one primary contact row per supplier from the Supplier project mapping. A
quote_sender row is ADDED (dedup by email) when a returned quote's sender is captured —
that is the per-contact address outbound replies route back to.

Read-only except the CREATE TABLE + seed. Safe to re-run (idempotent upsert by email).
Usage:
    python build_supplier_contacts.py [--db PATH]
"""
import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

WS = Path(r"C:/Users/GilesHugo/SES_Workspace")
sys.path.insert(0, str(WS / "lib"))
try:
    from path_config import p as _p
    DEFAULT_DB_PATH = _p("db.ops")
except Exception:
    DEFAULT_DB_PATH = str(WS / "db" / "ops.db")


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_schema(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS supplier_contacts (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            supplier_gid   TEXT,
            supplier_name  TEXT,
            contact_name   TEXT,
            email          TEXT,
            phone          TEXT,
            is_primary     INTEGER DEFAULT 0,
            source         TEXT DEFAULT 'project',
            created_at     TEXT,
            last_synced_at TEXT,
            UNIQUE(supplier_gid, email)
        )
    """)
    conn.commit()


def seed_from_suppliers(conn) -> dict:
    ts = now()
    rows = conn.execute("""
        SELECT asana_task_gid, vendor_name, contact_name, contact_email, contact_phone
        FROM suppliers
    """).fetchall()
    seen = ins = 0
    for gid, name, cname, email, phone in rows:
        if not (email or phone):
            continue
        # One primary contact row seeded from the Supplier project.
        conn.execute("""
            INSERT INTO supplier_contacts
                (supplier_gid, supplier_name, contact_name, email, phone, is_primary,
                 source, created_at, last_synced_at)
            VALUES (?, ?, ?, ?, ?, 1, 'project', ?, ?)
            ON CONFLICT(supplier_gid, email) DO UPDATE SET
                supplier_name=excluded.supplier_name,
                contact_name=excluded.contact_name,
                phone=excluded.phone,
                is_primary=1,
                last_synced_at=excluded.last_synced_at
        """, (gid, name, cname, email, phone, ts, ts))
        seen += 1
    conn.commit()
    return {"suppliers_with_contact": seen}


def add_quote_sender(gid: str, sender_email: str, sender_name: str = "") -> dict:
    """Add/capture a quote_sender contact for a supplier (per-contact reply routing).
    Idempotent by (supplier_gid, email).""" 
    conn = sqlite3.connect(DEFAULT_DB_PATH)
    ensure_schema(conn)
    ts = now()
    # Look up supplier name from suppliers if gid matches.
    name = conn.execute("SELECT vendor_name FROM suppliers WHERE asana_task_gid=?",
                        (gid,)).fetchone()
    sup_name = name[0] if name else gid
    conn.execute("""
        INSERT INTO supplier_contacts
            (supplier_gid, supplier_name, contact_name, email, phone, is_primary,
             source, created_at, last_synced_at)
        VALUES (?, ?, ?, ?, NULL, 0, 'quote_sender', ?, ?)
        ON CONFLICT(supplier_gid, email) DO UPDATE SET
            contact_name=excluded.contact_name, is_primary=0, last_synced_at=excluded.last_synced_at
    """, (gid, sup_name, sender_name, sender_email, ts, ts))
    conn.commit()
    conn.close()
    return {"ok": True, "gid": gid, "quote_sender": sender_email}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db-path", default=DEFAULT_DB_PATH)
    args = ap.parse_args()
    conn = sqlite3.connect(args.db_path)
    ensure_schema(conn)
    res = seed_from_suppliers(conn)
    total = conn.execute("SELECT COUNT(*) FROM supplier_contacts").fetchone()[0]
    print(f"Seeded {res['suppliers_with_contact']} primary supplier contacts. Total supplier_contacts rows: {total}")
    conn.close()


if __name__ == "__main__":
    main()
