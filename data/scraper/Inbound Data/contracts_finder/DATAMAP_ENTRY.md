# DATAMAP Entry — Contracts Finder → SesameAccess Pipeline

_Add this block to your main DATAMAP.md in the SAD agent workspace._

---

## Dataset: contracts_finder_leads

| Field             | Value |
|-------------------|-------|
| **Source**        | UK Government Contracts Finder API (`https://www.contractsfinder.service.gov.uk/api/rest/2/`) |
| **Output File**   | `contracts_finder/contracts_finder_leads.csv` |
| **Output CRM**    | HubSpot — Contacts + Companies + Lists + Sequences |
| **Trigger**       | Cron (daily, 06:00), OpenClaw REQUESTS folder, or Cowork manual run |
| **Status**        | ✅ ACTIVE |
| **Last Updated**  | See `contracts_finder/logs/pipeline_run_history.json` |

### Data Points Captured

| Column | Description |
|--------|-------------|
| `contract_ref` | Contracts Finder OCDS reference ID (deduplication key) |
| `contract_title` | Title of the tender/contract notice |
| `description` | First 500 chars of notice description |
| `published_date` | Date notice was published |
| `deadline` | Tender submission deadline |
| `contract_value` | Estimated value in GBP |
| `buyer_org` | Name of the council / housing body |
| `buyer_address` | Buyer address |
| `contact_name` | Name of the contact (from notice or Apollo) |
| `contact_email` | Email address (from notice or Apollo) |
| `contact_phone` | Phone number |
| `contact_title` | Job title (Apollo-enriched contacts only) |
| `found_via` | `"notice"` or `"apollo"` |
| `matched_keyword` | Which INCLUDE keyword triggered this result |
| `notice_url` | Direct link to the contract notice |
| `scrape_date` | Date this lead was captured |
| `hubspot_contact_id` | HubSpot contact ID once pushed |

### Keyword Filter Logic

**INCLUDE** (contract must match ≥1):
disability lift, platform lift, DFG, disabled facilities grant, wheelchair lift, step-free access, through-floor lift, porch lift, mobility lift, home lift, accessibility lift, step lift, stairlift, vertical platform lift, level access

**EXCLUDE** (any of these disqualifies, even if include keyword also present):
passenger lift, goods lift, escalator, lift shaft, traction lift, car lift, scissor lift, dumbwaiter

### Deduplication Layers

1. `seen_contracts.json` — Contract ref IDs already scraped (never re-processed)
2. `seen_contacts_log.json` — Emails contacted within cooldown window (default: 90 days)
3. HubSpot `cf_last_outreach` property — Source of truth in CRM

### HubSpot Integration

- **Lists created**: "Contracts Finder — Prospects" / "Contracts Finder — Interested"
- **Custom properties**: `cf_contract_ref`, `cf_buyer_org`, `cf_status`, `cf_last_outreach`, etc.
- **Sequence**: Enrol via `HUBSPOT_SEQUENCE_ID` env var (Sales Hub required)
- **Engagement sweep**: Daily check promotes opened/clicked contacts to "Interested" list

### Environment Variables Required

```
HUBSPOT_API_KEY=your_hubspot_private_app_token
HUBSPOT_PORTAL_ID=your_portal_id
HUBSPOT_SEQUENCE_ID=your_sequence_id   # optional — enrolment only
APOLLO_API_KEY=your_apollo_api_key     # optional — contact enrichment
```

### Triggering via OpenClaw Inter-Agent Requests

SAD can trigger this pipeline by writing a JSON file to:
`contracts_finder/REQUESTS/contracts_finder_YYYYMMDD_HHMMSS.json`

See `contracts_finder/REQUESTS_SCHEMA.json` for the full schema.

The orchestrator writes a `RESPONSE_*.json` back to the same folder when done.

### Update Frequency Recommendation

| Mode | Frequency | When to use |
|------|-----------|-------------|
| `incremental` | Daily (06:00 cron) | Normal ongoing operation |
| `full` | Weekly or on reset | Initial population, or after keyword changes |
| `sweep` | After every email send | Promote engaged contacts to Interested list |
