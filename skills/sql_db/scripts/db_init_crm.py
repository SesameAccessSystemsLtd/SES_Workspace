"""
db_init_crm.py — Initialise the OpenClaw crm.db SQLite database.

Creates the database file and all CRM / marketing intelligence tables if they
do not already exist. Safe to re-run at any time — idempotent.

Database: crm.db — External CRM / marketing intelligence
Tables: contacts, leads, companies, campaigns, campaign_results,
        lead_snippets, planning_applications

Usage:
    python db_init_crm.py
    python db_init_crm.py --db-path C:\\custom\\path\\crm.db
"""

import argparse
import logging
import os
import sqlite3
import sys

DEFAULT_DB_PATH = os.environ.get(
    "OPENCLAW_CRM_DB_PATH",
    r"C:\Users\GilesHugo\SES_Workspace\db\crm.db",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    hubspot_id          TEXT UNIQUE,
    email               TEXT,
    first_name          TEXT,
    last_name           TEXT,
    company             TEXT,
    lifecycle_stage     TEXT,
    last_activity_date  DATE,
    deal_value_gbp      REAL,
    source_run_id       INTEGER,
    upserted_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS companies (
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
);

CREATE TABLE IF NOT EXISTS leads (
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
    source_run_id       INTEGER,
    company_id          INTEGER REFERENCES companies(id),
    upserted_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS campaigns (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_name   TEXT NOT NULL,
    channel         TEXT NOT NULL,
    status          TEXT NOT NULL CHECK(status IN ('draft', 'active', 'paused', 'complete')),
    start_date      DATE,
    end_date        DATE,
    target_segment  TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_by      TEXT DEFAULT 'sesame_marketing'
);

CREATE TABLE IF NOT EXISTS campaign_results (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER REFERENCES campaigns(id),
    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    metric_name TEXT NOT NULL,
    metric_value REAL NOT NULL,
    notes       TEXT
);

CREATE TABLE IF NOT EXISTS lead_snippets (
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
);

CREATE TABLE IF NOT EXISTS planning_applications (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    application_ref TEXT UNIQUE,
    location        TEXT,
    description     TEXT,
    status          TEXT,
    decision_date   DATE,
    relevance_score INTEGER CHECK(relevance_score BETWEEN 1 AND 5),
    source_run_id   INTEGER,
    upserted_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
        log.info("crm.db initialised at: %s", db_path)

        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = [row[0] for row in cursor.fetchall()]
        log.info("Tables: %s", ", ".join(tables))
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialise the OpenClaw crm.db SQLite database.")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH, help="Path to the SQLite database file.")
    args = parser.parse_args()

    try:
        init_db(args.db_path)
    except Exception as exc:
        log.error("Failed to initialise crm.db: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
