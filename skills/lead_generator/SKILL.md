---
name: lead_generator
description: >
  Generate, enrich, export, and CRM-sync structured B2B lead lists for outbound sales and marketing
  campaigns. Supports synthetic sample data (Phase 1), Apollo.io contact enrichment (Phase 2),
  LinkedIn Sales Navigator sourcing (Phase 3), and direct HubSpot CRM sync (Phase 4).
  Use this skill when scraper_agent is asked to build a lead dataset for a given industry, region,
  job role, or company size profile.
---

# Lead Generator Skill

**Owned and executed by:** scraper_agent (SAD)
**Consumed by:** sesame_marketing (SAM) via DATAMAP.md
**Output dataset key:** `leads` (in DATAMAP.md)

---

## Why This Exists

SAM identifies target audiences but cannot source raw contact data itself — that is a data acquisition
task, not a creative one. scraper_agent runs the lead generator skill to produce a structured lead
list, writes it to the shared OneDrive export path and the `leads` SQLite table, then updates
DATAMAP.md. SAM reads the result and builds HubSpot campaigns against it.

---

## Auth Requirements

| Variable               | Required from Phase | Notes                                      |
|------------------------|---------------------|--------------------------------------------|
| `HUBSPOT_API_KEY`      | Phase 4             | Already in `~/.hermes/.env` — no new entry |
| `APOLLO_API_KEY`       | Phase 2             | Add to `~/.hermes/.env`                    |
| `LINKEDIN_CLIENT_ID`   | Phase 3             | Add to `~/.hermes/.env`                    |
| `LINKEDIN_CLIENT_SECRET` | Phase 3           | Add to `~/.hermes/.env`                    |
| `LINKEDIN_ACCESS_TOKEN` | Phase 3            | Add to `~/.hermes/.env`                    |

---

## Access Policy

| Agent             | Scripts                   | Access        |
|-------------------|---------------------------|---------------|
| scraper_agent     | all scripts               | READ + WRITE  |
| sesame_marketing  | output files (CSV/JSON)   | READ ONLY     |
| main              | leads table via sql_db    | READ ONLY     |

---

## Python Interpreter

```
C:\Users\GilesHugo\SES_Workspace\.venv\Scripts\python.exe
```

All scripts use standard library only for Phase 1 (`json`, `csv`, `datetime`, `os`, `argparse`,
`logging`, `uuid`). `requests` (already in venv) is used from Phase 2 onward.

---

## Scripts

### 1. Generate Leads

Produces a list of lead records. In Phase 1, synthetic sample data is returned. From Phase 2,
use `--mode apollo` to call Apollo.io. From Phase 3, use `--mode linkedin`.

```
"C:\Users\GilesHugo\SES_Workspace\.venv\Scripts\python.exe" \
  "C:\Users\GilesHugo\SES_Workspace\skills\lead_generator\scripts\lead_generator.py" \
  --industry "construction" \
  --count 50 \
  --job-roles "Director,Project Manager,Site Manager" \
  --company-size "11-200" \
  --region "UK" \
  --mode sample
```

Returns: JSON list of lead dicts to stdout. Also writes raw JSON to the output path.

Output fields: `company_name`, `contact_name`, `job_title`, `email`, `phone`, `company_size`,
`region`, `industry`, `pain_points`, `source`, `generated_at`

---

### 2. Enrich Leads (Phase 2+)

Calls Apollo.io to verify and enrich each lead. In Phase 1, returns leads unchanged with
`enrichment_status="skipped"`.

```
"C:\Users\GilesHugo\SES_Workspace\.venv\Scripts\python.exe" \
  "C:\Users\GilesHugo\SES_Workspace\skills\lead_generator\scripts\lead_enricher.py" \
  --input "C:\...\leads_raw.json" \
  --output "C:\...\leads_enriched.json" \
  --enrich-limit 50
```

Adds fields: `apollo_id`, `verified_email`, `linkedin_url`, `enrichment_status`
(`"enriched"` | `"not_found"` | `"skipped"` | `"error"`)

---

### 3. Export Leads

Writes leads to a timestamped file at the OneDrive export path. Prints the output file path to stdout.

```
"C:\Users\GilesHugo\SES_Workspace\.venv\Scripts\python.exe" \
  "C:\Users\GilesHugo\SES_Workspace\skills\lead_generator\scripts\lead_exporter.py" \
  --input "C:\...\leads_enriched.json" \
  --format csv
```

Formats: `csv` (default) or `json`. Returns the canonical output file path to stdout.

---

### 4. Sync to HubSpot CRM (Phase 4)

Upserts leads to HubSpot as contacts. Reads `HUBSPOT_API_KEY` from env (already present).
Use `--dry-run` to preview without making API calls.

```
"C:\Users\GilesHugo\SES_Workspace\.venv\Scripts\python.exe" \
  "C:\Users\GilesHugo\SES_Workspace\skills\lead_generator\scripts\lead_hubspot_sync.py" \
  --input "C:\...\leads_enriched.json" \
  --lifecycle-stage lead

# Dry run (safe preview):
"C:\Users\GilesHugo\SES_Workspace\.venv\Scripts\python.exe" \
  "C:\Users\GilesHugo\SES_Workspace\skills\lead_generator\scripts\lead_hubspot_sync.py" \
  --input "C:\...\leads_enriched.json" \
  --dry-run
```

Returns JSON summary: `{ "total", "created", "updated", "skipped", "errors", "synced_at" }`

---

## Typical SAD Workflow (triggered via REQUESTS/)

```
1. SAD reads REQUESTS/ file for a leads dataset request
2. Checks DATAMAP.md — if leads entry is FRESH and recent, skip generation
3. Runs lead_generator.py with parameters from the request file
4. Runs lead_enricher.py (Phase 2+; Phase 1 is a no-op pass-through)
5. Runs lead_exporter.py → captures output file path
6. Upserts all records to leads table via sql_db/scripts/db_write.py
7. Logs a scraper_run record via db_write.py
8. Updates DATAMAP.md leads entry: file path, last_updated, Status: FRESH
9. Updates REQUESTS/ file: Status PENDING → COMPLETE with summary note
```

## Typical SAM Consumption

```
1. SAM reads DATAMAP.md at session start → finds leads entry FRESH
2. Reads leads_latest.csv from the OneDrive path
3. Optionally cross-references: SELECT l.email, c.lifecycle_stage FROM leads l
   LEFT JOIN contacts c ON l.email = c.email WHERE l.industry = '...'
4. Phase 4: calls lead_hubspot_sync.py → leads become HubSpot contacts
5. Builds email sequences targeting those contacts in HubSpot
```

---

## Output Export Path

```
C:\Users\GilesHugo\OneDrive - SESAME ACCESS AZURE\OneDrive Documents\LAB. SESAME\updates\leads_latest.csv
C:\Users\GilesHugo\OneDrive - SESAME ACCESS AZURE\OneDrive Documents\LAB. SESAME\updates\leads_latest.json
```

Timestamped archives are also written alongside these canonical files.

---

## Dependencies

No new pip installs required for Phase 1 or Phase 4.
- Phase 1: stdlib only (`json`, `csv`, `datetime`, `os`, `argparse`, `logging`, `uuid`, `hashlib`)
- Phase 2+: `requests` (already in venv)
- Phase 3: `requests` + LinkedIn OAuth token management
