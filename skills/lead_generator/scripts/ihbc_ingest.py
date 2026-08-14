"""
ihbc_ingest.py — Load IHBC/HESPR scraped contacts into the OpenClaw database.

Reads:  agents/scraper/workspace/Inbound Data/ihbc_accredited/ihbc_hespr_contacts.csv
Writes: companies table (upsert on company_slug)
        leads table (upsert on email OR hespr_url when no email)
        scraper_runs table (audit entry)

Usage:
    python ihbc_ingest.py
    python ihbc_ingest.py --csv-path "C:\\path\\to\\ihbc_hespr_contacts.csv" --dry-run
"""

import argparse
import csv
import json
import logging
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone

DEFAULT_CSV_PATH = (
    r"C:\Users\GilesHugo\SES_Workspace\data\scraper"
    r"\Inbound Data\ihbc_accredited\ihbc_hespr_contacts.csv"
)
DEFAULT_DB_PATH = os.environ.get(
    "OPENCLAW_CRM_DB_PATH",
    r"C:\Users\GilesHugo\SES_Workspace\db\crm.db",
)
SOURCE_DATASET = "ihbc_hespr"

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def slugify(text: str) -> str:
    """Normalise company name for deduplication."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s]+", "_", text)
    return text[:120]


def extract_adviser_name(context: str, job_title_field: str) -> str:
    """
    Try to extract the HESPR Designated Service Adviser's personal name.
    The context or job_title field often contains:
        HESPR 'Designated Service Adviser'Name:Firstname Lastname
    """
    for field in (context, job_title_field):
        m = re.search(
            r"(?:Designated Service Adviser['\u2019]?\s*)?Name:\s*([A-Z][a-zA-Z\s\-'\.]+?)(?:Job title:|Tel:|Email:|$)",
            field,
        )
        if m:
            return m.group(1).strip()
    return ""


def extract_region_county(context: str) -> tuple[str, str]:
    """
    HESPR context fields often start with:  'East Midlands  Lincolnshire  [description...]'
    or 'London, but we operate...'
    Try to pull region and county from the first two tokens.
    """
    # Strip any leading whitespace / newlines
    text = context.strip()
    # Take first 100 chars to avoid parsing whole context
    head = text[:100]
    # Known IHBC branch names
    branches = [
        "East Midlands", "West Midlands", "Yorkshire", "North West",
        "North East", "South East", "South West", "London", "East of England",
        "East", "Scotland", "Wales", "Northern Ireland", "Ireland",
    ]
    for branch in branches:
        if head.startswith(branch):
            remainder = head[len(branch):].strip().lstrip(",").strip()
            # County is likely the next word or two before description starts
            county_m = re.match(r"^([A-Z][a-zA-Z\s]+?)(?:\s{2,}|\s+[A-Z][a-z])", remainder)
            county = county_m.group(1).strip() if county_m else ""
            return branch, county
    return "", ""


def parse_specialisations(context: str) -> str:
    """Extract HESPR service categories marked YES in the context."""
    categories = [
        "Historic Building conservation",
        "Conservation planning",
        "Architectural design and new build in historic areas",
        "Historical research",
        "Historic area assessments",
        "Project Management",
        "Archaeology",
    ]
    found = []
    for cat in categories:
        if re.search(re.escape(cat) + r"\s*YES", context, re.IGNORECASE):
            found.append(cat)
    return ", ".join(found) if found else ""


def clean_company_name(raw_name: str) -> str:
    """The name field from HESPR is generally the company name, strip noise."""
    # Remove trailing job title artefacts e.g. "Acme Ltd\nDirector"
    name = raw_name.split("\n")[0].strip()
    # Remove anything after a newline or tab
    name = re.split(r"[\t\r]", name)[0].strip()
    return name


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def log_scraper_run(conn: sqlite3.Connection, row_count: int, status: str, notes: str) -> int:
    now = datetime.now(timezone.utc).isoformat()
    cursor = conn.execute(
        """
        INSERT INTO scraper_runs (dataset_name, source, pulled_at, row_count, status, notes, file_path)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (SOURCE_DATASET, "ihbc_hespr_csv", now, row_count, status, notes, DEFAULT_CSV_PATH),
    )
    conn.commit()
    return cursor.lastrowid


def upsert_company(conn: sqlite3.Connection, row: dict) -> int:
    """Insert or update a company. Returns the company id."""
    slug = slugify(row["company_name"])
    now = datetime.now(timezone.utc).isoformat()

    existing = conn.execute(
        "SELECT id FROM companies WHERE company_slug = ?", (slug,)
    ).fetchone()

    if existing:
        conn.execute(
            """
            UPDATE companies SET
                company_name    = ?,
                hespr_url       = COALESCE(NULLIF(hespr_url,''), ?),
                region          = COALESCE(NULLIF(region,''), ?),
                county          = COALESCE(NULLIF(county,''), ?),
                specialisations = COALESCE(NULLIF(specialisations,''), ?),
                context_summary = COALESCE(NULLIF(context_summary,''), ?),
                source_datasets = ?,
                updated_at      = ?
            WHERE company_slug = ?
            """,
            (
                row["company_name"],
                row.get("hespr_url", ""),
                row.get("region", ""),
                row.get("county", ""),
                row.get("specialisations", ""),
                row.get("context_summary", ""),
                json.dumps([SOURCE_DATASET]),
                now,
                slug,
            ),
        )
        return existing["id"]
    else:
        cursor = conn.execute(
            """
            INSERT INTO companies
                (company_name, company_slug, hespr_url, region, county,
                 specialisations, context_summary, source_datasets, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["company_name"],
                slug,
                row.get("hespr_url", ""),
                row.get("region", ""),
                row.get("county", ""),
                row.get("specialisations", ""),
                row.get("context_summary", ""),
                json.dumps([SOURCE_DATASET]),
                now,
                now,
            ),
        )
        return cursor.lastrowid


def upsert_lead(conn: sqlite3.Connection, row: dict, company_id: int, run_id: int) -> None:
    """Insert or update a lead. Dedup key: email if present, else hespr_url."""
    now = datetime.now(timezone.utc).isoformat()
    email = row.get("email", "").strip() or None
    hespr_url = row.get("hespr_url", "").strip() or None

    # Try to find existing by email or hespr_url
    existing = None
    if email:
        existing = conn.execute(
            "SELECT id FROM leads WHERE email = ?", (email,)
        ).fetchone()
    if not existing and hespr_url:
        existing = conn.execute(
            "SELECT id FROM leads WHERE hespr_url = ?", (hespr_url,)
        ).fetchone()

    fields = {
        "company_name":     row.get("company_name", ""),
        "contact_name":     row.get("contact_name", ""),
        "job_title":        row.get("job_title_clean", ""),
        "email":            email,
        "phone":            row.get("tel", "").strip() or None,
        "region":           row.get("region", ""),
        "county":           row.get("county", ""),
        "specialisations":  row.get("specialisations", ""),
        "hespr_url":        hespr_url,
        "source":           "ihbc",
        "source_dataset":   SOURCE_DATASET,
        "generated_at":     now,
        "source_run_id":    run_id,
        "company_id":       company_id,
        "upserted_at":      now,
    }

    if existing:
        conn.execute(
            """
            UPDATE leads SET
                company_name    = :company_name,
                contact_name    = COALESCE(NULLIF(contact_name,''), :contact_name),
                job_title       = COALESCE(NULLIF(job_title,''), :job_title),
                phone           = COALESCE(NULLIF(phone,''), :phone),
                region          = COALESCE(NULLIF(region,''), :region),
                county          = COALESCE(NULLIF(county,''), :county),
                specialisations = COALESCE(NULLIF(specialisations,''), :specialisations),
                hespr_url       = COALESCE(NULLIF(hespr_url,''), :hespr_url),
                source_dataset  = :source_dataset,
                source_run_id   = :source_run_id,
                company_id      = :company_id,
                upserted_at     = :upserted_at
            WHERE id = :_id
            """,
            {**fields, "_id": existing["id"]},
        )
    else:
        conn.execute(
            """
            INSERT INTO leads
                (company_name, contact_name, job_title, email, phone,
                 region, county, specialisations, hespr_url,
                 source, source_dataset, generated_at,
                 source_run_id, company_id, upserted_at)
            VALUES
                (:company_name, :contact_name, :job_title, :email, :phone,
                 :region, :county, :specialisations, :hespr_url,
                 :source, :source_dataset, :generated_at,
                 :source_run_id, :company_id, :upserted_at)
            """,
            fields,
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(csv_path: str, db_path: str, dry_run: bool = False) -> None:
    if not os.path.exists(csv_path):
        log.error("CSV not found: %s", csv_path)
        sys.exit(1)
    if not os.path.exists(db_path):
        log.error("Database not found: %s — run db_init.py then db_migrate.py first.", db_path)
        sys.exit(1)

    rows_processed = 0
    rows_skipped = 0
    companies_upserted = 0
    leads_upserted = 0

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        raw_rows = list(reader)

    log.info("Loaded %d rows from %s", len(raw_rows), csv_path)

    conn = get_connection(db_path)
    run_id = None

    try:
        for raw in raw_rows:
            url = (raw.get("url") or "").strip()
            raw_name = (raw.get("name") or "").strip()
            if not raw_name or not url:
                rows_skipped += 1
                continue

            company_name = clean_company_name(raw_name)
            if not company_name:
                rows_skipped += 1
                continue

            context = (raw.get("context") or "").strip()
            job_title_field = (raw.get("job_title") or "").strip()
            email = (raw.get("email") or "").strip()
            tel = (raw.get("tel") or "").strip()
            has_details = str(raw.get("has_contact_details", "")).strip().lower() == "true"

            region, county = extract_region_county(context)
            specialisations = parse_specialisations(context)
            contact_name = extract_adviser_name(context, job_title_field)

            # Clean job title (extract first segment before concatenated contact info)
            job_title_clean = re.split(r"Tel:|Email:|Which of", job_title_field)[0].strip()

            # Context summary: first 500 chars of context
            context_summary = context[:500].strip()

            company_row = {
                "company_name":     company_name,
                "hespr_url":        url,
                "region":           region,
                "county":           county,
                "specialisations":  specialisations,
                "context_summary":  context_summary,
            }
            lead_row = {
                "company_name":     company_name,
                "contact_name":     contact_name,
                "job_title_clean":  job_title_clean,
                "email":            email,
                "tel":              tel,
                "region":           region,
                "county":           county,
                "specialisations":  specialisations,
                "hespr_url":        url,
                "has_details":      has_details,
            }

            if dry_run:
                log.info("[DRY-RUN] Would upsert company=%r contact=%r email=%r",
                         company_name, contact_name, email)
                rows_processed += 1
                continue

            if run_id is None:
                run_id = log_scraper_run(conn, 0, "partial", "In progress")

            company_id = upsert_company(conn, company_row)
            companies_upserted += 1

            upsert_lead(conn, lead_row, company_id, run_id)
            leads_upserted += 1
            rows_processed += 1

        if not dry_run and run_id is not None:
            conn.commit()
            # Update the scraper_run with final counts
            conn.execute(
                "UPDATE scraper_runs SET row_count=?, status=?, notes=? WHERE id=?",
                (rows_processed, "success",
                 f"companies_upserted={companies_upserted} leads_upserted={leads_upserted} skipped={rows_skipped}",
                 run_id),
            )
            conn.commit()

    except Exception as exc:
        conn.rollback()
        if run_id is not None:
            try:
                conn.execute(
                    "UPDATE scraper_runs SET status='failed', notes=? WHERE id=?",
                    (str(exc), run_id),
                )
                conn.commit()
            except Exception:
                pass
        log.error("Ingest failed: %s", exc)
        raise
    finally:
        conn.close()

    log.info(
        "Done. processed=%d companies=%d leads=%d skipped=%d dry_run=%s",
        rows_processed, companies_upserted, leads_upserted, rows_skipped, dry_run,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest IHBC/HESPR contacts into OpenClaw DB.")
    parser.add_argument("--csv-path", default=DEFAULT_CSV_PATH)
    parser.add_argument("--db-path",  default=DEFAULT_DB_PATH)
    parser.add_argument("--dry-run",  action="store_true", help="Parse only, no DB writes.")
    args = parser.parse_args()
    run(args.csv_path, args.db_path, args.dry_run)


if __name__ == "__main__":
    main()
