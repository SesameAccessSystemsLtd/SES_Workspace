---
name: sql_db
description: Read and write structured data to the local SQLite database at workspace/db/openclaw.db. Use this skill when you need to store scraped data, query campaign performance, cross-reference contacts with Asana tasks, or retrieve historical trends. Supports SELECT queries, INSERT, UPDATE, and UPSERT operations. No server required — zero-config local SQLite. All writes are audit-logged automatically.
---

# SQL Database Skill

Local SQLite database for structured, queryable, persistent data storage across all agents.

## Why This Exists

File-based storage (CSVs, OneDrive exports) is great for human readability and Excel compatibility. It is not great for answering questions like:
- "Which HubSpot contacts have been in 'lead' stage for more than 60 days?"
- "What was the average email open rate across all campaigns last quarter?"
- "Which planning applications are in areas where we have active contacts?"

This skill provides a SQLite database that complements (not replaces) the file-based approach. The scraper writes to both OneDrive files and the database. Agents query the database for trend analysis and cross-referencing.

## Configuration

**Database path:** `C:\Users\GilesHugo\.openclaw\workspace\db\openclaw.db`

Override with environment variable:
```
OPENCLAW_DB_PATH=C:\path\to\custom.db
```

**Audit log path:** `C:\Users\GilesHugo\.openclaw\workspace\db\audit.log`

No pip installs required. All scripts use Python standard library only (`sqlite3`, `json`, `argparse`, `logging`).

## Initialise the Database

Run once (safe to re-run — idempotent):

```
python C:\Users\GilesHugo\.openclaw\workspace\skills\sql_db\scripts\db_init.py
```

This creates `openclaw.db` and all tables if they do not already exist.

---

## Agent Access Policy

Access is enforced by convention (SQLite has no per-user permissions). Each agent must respect the following:

| Agent | Tables | Access |
|---|---|---|
| scraper_agent | scraper_runs, contacts, planning_applications, asana_task_states, purchase_orders, leads | READ + WRITE |
| sesame_marketing | campaigns, campaign_results | READ + WRITE |
| sesame_marketing | contacts, scraper_runs, leads | READ ONLY |
| asana_agent | asana_task_states | READ + WRITE |
| asana_agent | campaigns, contacts, purchase_orders, bom_requirements | READ ONLY |
| ops_agent | purchase_orders, bom_requirements, supplier_deliveries, email_po_matches | READ + WRITE |
| ops_agent | asana_task_states, contacts | READ ONLY |
| main (Commander Claw) | all tables | READ ONLY |

---

## Actions

### 1. Query Data (READ)

Use `db_query.py` for all SELECT operations. Returns JSON to stdout.

```
python scripts/db_query.py "SELECT * FROM contacts WHERE lifecycle_stage = 'lead' LIMIT 10"
```

```
python scripts/db_query.py "SELECT campaign_name, AVG(metric_value) as avg_open_rate FROM campaign_results WHERE metric_name = 'open_rate' GROUP BY campaign_name ORDER BY avg_open_rate DESC"
```

```
python scripts/db_query.py "SELECT c.email, c.lifecycle_stage, a.task_name FROM contacts c JOIN asana_task_states a ON c.company = a.project_name WHERE a.status = 'blocked'"
```

Output format:
```json
{
  "rows": [...],
  "count": 42,
  "query": "SELECT ..."
}
```

Exit code 0 on success, 1 on error. Errors go to stderr.

---

### 2. Write Data (INSERT / UPDATE / UPSERT)

Use `db_write.py` for all write operations. Every write is appended to `audit.log`.

**Insert a new record:**
```
python scripts/db_write.py --table scraper_runs --operation insert --data "{\"dataset_name\": \"hubspot_contacts\", \"source\": \"hubspot\", \"pulled_at\": \"2026-03-17T09:14:00\", \"row_count\": 3412, \"status\": \"success\", \"file_path\": \"C:\\\\...\\\\hubspot_contacts_latest.csv\"}"
```

**Upsert (insert or update on conflict):**
```
python scripts/db_write.py --table contacts --operation upsert --conflict-key hubspot_id --data "{\"hubspot_id\": \"hs_123\", \"email\": \"jane@example.com\", \"lifecycle_stage\": \"customer\"}"
```

**Update existing record:**
```
python scripts/db_write.py --table campaigns --operation update --where "{\"id\": 5}" --data "{\"status\": \"complete\"}"
```

Exit code 0 on success, 1 on error.

---

### 3. Initialise / Reset Schema

```
python scripts/db_init.py
```

Safe to re-run. Uses `CREATE TABLE IF NOT EXISTS`. Never drops data.

---

## Schema Reference

### `scraper_runs`
Audit trail of every data pull. Written by scraper after each successful or failed pull.

| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | auto |
| dataset_name | TEXT | e.g. hubspot_contacts |
| source | TEXT | hubspot, dropbox, csv, ga, etc. |
| pulled_at | TIMESTAMP | ISO 8601 |
| row_count | INTEGER | nullable |
| status | TEXT | success, partial, failed |
| notes | TEXT | anomalies, schema changes |
| file_path | TEXT | canonical OneDrive output path |

---

### `contacts`
Normalised HubSpot contacts. Scraper upserts on `hubspot_id`.

| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | auto |
| hubspot_id | TEXT UNIQUE | HubSpot record ID |
| email | TEXT | |
| first_name | TEXT | |
| last_name | TEXT | |
| company | TEXT | |
| lifecycle_stage | TEXT | subscriber, lead, MQL, customer, etc. |
| last_activity_date | DATE | |
| deal_value_gbp | REAL | |
| source_run_id | INTEGER | FK → scraper_runs.id |
| upserted_at | TIMESTAMP | auto-set on write |

---

### `campaigns`
Marketing campaigns tracked by sesame_marketing.

| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | auto |
| campaign_name | TEXT | |
| channel | TEXT | email, linkedin, blog, ppc |
| status | TEXT | draft, active, paused, complete |
| start_date | DATE | |
| end_date | DATE | |
| target_segment | TEXT | description of audience |
| created_at | TIMESTAMP | auto |
| created_by | TEXT | default: sesame_marketing |

---

### `campaign_results`
Performance metrics per campaign. sesame_marketing writes after each reporting period.

| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | auto |
| campaign_id | INTEGER | FK → campaigns.id |
| recorded_at | TIMESTAMP | auto |
| metric_name | TEXT | open_rate, ctr, conversions, impressions |
| metric_value | REAL | |
| notes | TEXT | |

---

### `asana_task_states`
Point-in-time snapshots of Asana task states. Enables trend analysis over time.

| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | auto |
| asana_task_id | TEXT | |
| project_name | TEXT | |
| task_name | TEXT | |
| status | TEXT | incomplete, complete, blocked |
| due_date | DATE | |
| recorded_at | TIMESTAMP | auto |
| source_run_id | INTEGER | FK → scraper_runs.id |

---

### `planning_applications`
Council planning data. Scraper upserts on `application_ref`.

| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | auto |
| application_ref | TEXT UNIQUE | council reference number |
| location | TEXT | |
| description | TEXT | |
| status | TEXT | |
| decision_date | DATE | |
| relevance_score | INTEGER | 1–5, scored by scraper |
| source_run_id | INTEGER | FK → scraper_runs.id |
| upserted_at | TIMESTAMP | auto |

---

### `leads`
Outbound lead records generated by the `lead_generator` skill. Written by scraper_agent. Read by sesame_marketing for campaign targeting. Upsert conflict key: `email`.

| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | auto |
| company_name | TEXT | required |
| contact_name | TEXT | |
| job_title | TEXT | |
| email | TEXT | upsert dedup key |
| phone | TEXT | |
| company_size | TEXT | e.g. 11-200 |
| region | TEXT | e.g. UK, US |
| industry | TEXT | e.g. construction |
| pain_points | TEXT | identified pain points |
| source | TEXT | sample, apollo, linkedin |
| generated_at | TIMESTAMP | ISO 8601 |
| apollo_id | TEXT | Apollo.io person ID (Phase 2+) |
| verified_email | TEXT | Apollo-verified email (Phase 2+) |
| linkedin_url | TEXT | LinkedIn profile URL (Phase 2+) |
| enrichment_status | TEXT | enriched, not_found, skipped, error |
| hubspot_contact_id | TEXT | HubSpot contact ID after sync (Phase 4+) |
| hubspot_sync_status | TEXT | pending, synced, error, skipped |
| hubspot_synced_at | TIMESTAMP | |
| campaign_tag | TEXT | optional tag linking to campaigns table |
| source_run_id | INTEGER | FK → scraper_runs.id |
| upserted_at | TIMESTAMP | auto |

---

## Concurrency Note

SQLite uses file-level write locks. If two agents attempt simultaneous writes, one will receive `SQLITE_BUSY`. `db_write.py` handles this automatically with exponential backoff (max 3 retries). If all retries fail, the script exits with code 1 and logs the error. This is rare given the system's sequential agent patterns.
