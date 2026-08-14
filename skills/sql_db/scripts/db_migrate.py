"""
db_migrate.py — Migrate existing openclaw.db to the latest schema.

Safe to re-run — checks whether migration is already applied before proceeding.

Migrations applied:
  v2 (2026-04-01): Add companies + lead_snippets tables.
                   Widen leads.source CHECK constraint to include ihbc/hespr/scraper/hubspot.
                   Add county, specialisations, hespr_url, source_dataset, company_id to leads.

Usage:
    python db_migrate.py
    python db_migrate.py --db-path C:\\custom\\path\\openclaw.db
"""

import argparse
import logging
import os
import sqlite3
import sys

DEFAULT_DB_PATH = os.environ.get(
    "OPENCLAW_DB_PATH",
    r"C:\Users\GilesHugo\SES_Workspace\db\openclaw.db",
)

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


def migrate_v2(conn: sqlite3.Connection) -> None:
    """
    v2: Add companies + lead_snippets tables.
        Widen leads.source CHECK and add new columns to leads.
    """
    log.info("Applying migration v2...")

    # --- 1. Create companies table ---
    if not table_exists(conn, "companies"):
        conn.execute("""
            CREATE TABLE companies (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                company_name        TEXT NOT NULL,
                company_slug        TEXT UNIQUE NOT NULL,
                website             TEXT,
                sector              TEXT,
                region              TEXT,
                county              TEXT,
                hespr_url           TEXT,
                specialisations     TEXT,
                context_summary     TEXT,
                hubspot_company_id  TEXT,
                hubspot_sync_status TEXT NOT NULL DEFAULT 'pending'
                                        CHECK(hubspot_sync_status IN ('pending','synced','error','skipped')),
                source_datasets     TEXT,
                notes               TEXT,
                created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        log.info("  Created table: companies")
    else:
        log.info("  Table companies already exists — skipped.")

    # --- 2. Create lead_snippets table ---
    if not table_exists(conn, "lead_snippets"):
        conn.execute("""
            CREATE TABLE lead_snippets (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_type     TEXT NOT NULL CHECK(entity_type IN ('company','contact','project','general')),
                entity_id       INTEGER,
                entity_table    TEXT,
                entity_name     TEXT NOT NULL,
                snippet_text    TEXT NOT NULL,
                snippet_source  TEXT NOT NULL DEFAULT 'user'
                                    CHECK(snippet_source IN ('user','scraper','hubspot','asana','planning')),
                added_by        TEXT NOT NULL DEFAULT 'main',
                added_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                tags            TEXT
            )
        """)
        log.info("  Created table: lead_snippets")
    else:
        log.info("  Table lead_snippets already exists — skipped.")

    # --- 3. Widen leads table ---
    # SQLite cannot ALTER a CHECK constraint. Strategy:
    # rename → recreate with updated schema → copy data → drop old.
    # Only do this if the new columns are missing (i.e. migration not yet applied).
    if table_exists(conn, "leads") and not column_exists(conn, "leads", "county"):
        log.info("  Rebuilding leads table with updated schema...")
        conn.execute("ALTER TABLE leads RENAME TO leads_v1_backup")
        conn.execute("""
            CREATE TABLE leads (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                company_name        TEXT NOT NULL,
                contact_name        TEXT,
                job_title           TEXT,
                email               TEXT,
                phone               TEXT,
                company_size        TEXT,
                region              TEXT,
                county              TEXT,
                industry            TEXT,
                specialisations     TEXT,
                hespr_url           TEXT,
                pain_points         TEXT,
                source              TEXT NOT NULL DEFAULT 'scraper'
                                        CHECK(source IN ('sample','apollo','linkedin','ihbc','hespr','scraper','hubspot')),
                source_dataset      TEXT,
                generated_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                apollo_id           TEXT,
                verified_email      TEXT,
                linkedin_url        TEXT,
                enrichment_status   TEXT NOT NULL DEFAULT 'skipped'
                                        CHECK(enrichment_status IN ('enriched','not_found','skipped','error')),
                hubspot_contact_id  TEXT,
                hubspot_sync_status TEXT NOT NULL DEFAULT 'pending'
                                        CHECK(hubspot_sync_status IN ('pending','synced','error','skipped')),
                hubspot_synced_at   TIMESTAMP,
                campaign_tag        TEXT,
                source_run_id       INTEGER REFERENCES scraper_runs(id),
                company_id          INTEGER REFERENCES companies(id),
                upserted_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Copy columns that exist in both old and new schema
        conn.execute("""
            INSERT INTO leads (
                id, company_name, contact_name, job_title, email, phone,
                company_size, region, industry, pain_points, source,
                generated_at, apollo_id, verified_email, linkedin_url,
                enrichment_status, hubspot_contact_id, hubspot_sync_status,
                hubspot_synced_at, campaign_tag, source_run_id, upserted_at
            )
            SELECT
                id, company_name, contact_name, job_title, email, phone,
                company_size, region, industry, pain_points,
                CASE
                    WHEN source IN ('sample','apollo','linkedin') THEN source
                    ELSE 'scraper'
                END,
                generated_at, apollo_id, verified_email, linkedin_url,
                enrichment_status, hubspot_contact_id, hubspot_sync_status,
                hubspot_synced_at, campaign_tag, source_run_id, upserted_at
            FROM leads_v1_backup
        """)
        row_count = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        log.info("  Migrated %d rows from leads_v1_backup → leads", row_count)
        conn.execute("DROP TABLE leads_v1_backup")
        log.info("  Dropped leads_v1_backup")
    elif not table_exists(conn, "leads"):
        log.info("  leads table does not exist — will be created by db_init.py")
    else:
        log.info("  leads table already has v2 columns — skipped rebuild.")

    conn.commit()
    log.info("Migration v2 complete.")


def run_migrations(db_path: str) -> None:
    if not os.path.exists(db_path):
        log.error("Database not found at %s — run db_init.py first.", db_path)
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = OFF")  # Required during table rebuild
    try:
        migrate_v2(conn)
        log.info("All migrations applied successfully.")

        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = [row[0] for row in cursor.fetchall()]
        log.info("Tables in database: %s", ", ".join(tables))
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate the OpenClaw SQLite database.")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    args = parser.parse_args()

    try:
        run_migrations(args.db_path)
    except Exception as exc:
        log.error("Migration failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
