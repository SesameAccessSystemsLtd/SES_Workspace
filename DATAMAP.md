# DATAMAP.md — Shared Data Registry

**Writers:** scraper_agent/SAD (marketing data) · ops_agent/SAO (pipeline data)
**Read by:** sesame_marketing, main agent, any agent needing data
**Rule:** Only act on FRESH entries. STALE/ERROR/PENDING = do not use without refresh.

---

## ⚠️ Write Rules — STRICTLY ENFORCED

### Who writes what
| Data type | Authorised writer |
|---|---|
| Marketing / scraping datasets | scraper_agent (SAD) |
| Purchasing pipeline data files (`workspace/data/`) | ops_agent (SAO) |
| No other agent may add or modify entries without Giles approval |

### Status definitions
| Status | Meaning | Action |
|---|---|---|
| `FRESH` | Updated within 30 days, usable | OK to act |
| `STALE` | Not updated in >30 days | Do NOT act — request refresh from SAD via REQUESTS/ |
| `ERROR` | Last ingest/scrape failed | Do NOT use — add error note in Notes column |
| `PENDING_DEVELOPMENT` | Scraper not yet built | No data exists |
| `PENDING_CSV_INGESTION` | Waiting for user CSV drop | No data exists yet |

### Freshness rule — 30-day threshold
- **>30 days since Last Updated → Status must be set to STALE**
- When writing/refreshing a dataset: set Status = `FRESH`, update Last Updated to current ISO timestamp
- When ingest fails: set Status = `ERROR`, note the error in Notes column
- sesame_marketing must check FRESH before any outreach run — STALE = stop and file REQUESTS/ ticket to SAD

### Workspace cleanliness rules
- Every file in `workspace/data/` must have a DATAMAP entry before any agent reads it
- `workspace/data/` is NOT a scratch pad — temporary work goes in `dev_temp/`
- When a dataset is retired: move its entry to Dropped Sources, delete the file
- No orphan data files — if it's not in DATAMAP, it is not authorised for use
- Agents must update their DATAMAP entry **in the same operation** as writing a data file

---

## Active Datasets — Scraper (SAD)

| Dataset Key | Type | Source / File Path | Status | Last Updated | Row Count | Notes |
|---|---|---|---|---|---|---|
| ihbc_hespr | CSV + DB | `agents/scraper/workspace/Inbound Data/ihbc_accredited/ihbc_hespr_contacts.csv` | FRESH | 2026-06-23T10:03:00Z | 43 contacts | Loaded to `crm.db`: 42 companies + 42 leads. HubSpot crossref pending. Refresh SAD before next outreach. |
| ihbc_hespr_new_leads | CSV | `agents/scraper/workspace/Inbound Data/ihbc_accredited/new_leads_for_outreach_20260401.csv` | STALE | 2026-04-01 | ~21 rows | Not yet in HubSpot — needs SESAL sign-off before outreach. Verify still current before use. |
| ihbc_hespr_review | CSV | `agents/scraper/workspace/Inbound Data/ihbc_accredited/already_in_hubspot_20260401.csv` | STALE | 2026-04-01 | ~22 rows | Already in HubSpot — review_action column needs SESAL check before acting. |

---

## Active Datasets — Ops Pipeline (SAO)

| Dataset Key | Type | File Path | Status | Last Updated | Notes |
|---|---|---|---|---|---|
| purchasing_raw_emails | JSON | `workspace/data/raw_purchasing_emails.json` | FRESH | 2026-06-09T16:00:29Z | Raw email cache from `purchasing@sesameaccess.com`. Written by pipeline stage 01 (email fetcher). Do not edit manually. |
| purchasing_processed_ids | JSON | `workspace/data/processed_email_ids.json` | FRESH | 2026-06-09T16:00:29Z | Dedup cache of processed email IDs. Prevents double-processing. Written by pipeline stage 01. Do not clear without Giles approval. |
| purchasing_parsed_insights | JSON | `workspace/data/parsed_insights.json` | FRESH | 2026-06-09T16:01:04Z | Claude-parsed email insights. Written by pipeline stage 02/03. Input to approval dashboard. |
| purchasing_approval_queue | JSON | `workspace/data/pending_approval_queue.json` | FRESH | 2026-06-09T16:01:00Z | Pending approval actions for Giles (Google Sheet gate). Written by stage 04_sheet_writer. Read by stage 05_sheet_approver. |

---

## Pending Development (no data yet)

| Dataset Key | Source | Status | Notes |
|---|---|---|---|
| building_conservation_directory | https://www.buildingconservation.com/ | PENDING_DEVELOPMENT | Scraper not yet built |
| contracts_finder_leads | User-provided CSV fortnightly | PENDING_CSV_INGESTION | User drops CSV to `Inbound Data/contracts_finder/Gov Contracts Notices/` — SAD processes on receipt |
| historic_houses | https://www.historichouses.org/ | PENDING_DEVELOPMENT | Scraper not yet built |
| nia_member_directory | https://www.niauk.org/membership/member-directory/ | PENDING_DEVELOPMENT | Scraper not yet built |

---

## Dropped Sources

| Dataset Key | Reason |
|---|---|
| historic_england_nhle | Low ROI — statutory records only, no emails |
| ihbc_gridiron | Data intentionally obscured |
| care_register | Limited contact info, requires manual LinkedIn follow-up |
| english_heritage_supplier | High bot protection + mandatory login |
| national_trust_supplier | High bot protection + mandatory login |

---

_Last updated: 2026-06-23T10:03:00Z — Commander Claw (updated ihbc_hespr status)_
_SAD must update this file at: `C:\Users\GilesHugo\.openclaw\workspace\DATAMAP.md`_
_SAO must update the Ops Pipeline section when any `workspace/data/` file changes_