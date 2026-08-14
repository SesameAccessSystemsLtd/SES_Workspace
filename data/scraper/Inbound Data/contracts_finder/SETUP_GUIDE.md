# Setup Guide — SesameAccess × Contracts Finder Pipeline

## The complete workflow (no Marketing Hub required)

```
Contracts Finder API
        ↓ scrape + keyword filter
contracts_finder_scraper.py
        ↓ lead dicts
apollo_enrichment.py (if no email in notice)
        ↓ enriched lead dicts
hubspot_pipeline.py
        ↓ creates Contact + Company + Deal in HubSpot
        ↓ sets  council_outreach = TO_EMAIL
HubSpot CRM (contact record exists, queued for send)
        ↓
email_sender.py (SMTP — external to HubSpot)
        ↓ reads queue: contacts where council_outreach = TO_EMAIL
        ↓ sends personalised email via SMTP
        ↓ updates council_outreach = EMAILED in HubSpot
        ↓
HubSpot Workflows (already created — you must ENABLE them)
  ├── "Council outreach - qualified responders"
  │     → reply/engagement detected → adds to "Hubspot inport Council test 1.05.25"
  └── "Council outreach - remove not interested from follow-up"
        → council_outreach = NOT_Interested → removes from all lists
```

---

## API Keys in your .env file

The pipeline reads from the parent OpenClaw `.env` file. Ensure these are set:

```env
# HubSpot — both keys defined; pipeline prefers PAT (broadest scope)
HUBSPOT_PAT_API_KEY=pat-na1-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
HUBSPOT_API_KEY_CRM_UPDATER=your-crm-updater-key

# HubSpot portal ID (visible in URL: app.hubspot.com/contacts/XXXXXX/)
HUBSPOT_PORTAL_ID=XXXXXX

# Elastic Email — outbound email sender (already added to your .env)
EMAIL_SERVICE_Elasticemail_API_KEY=your-elastic-email-api-key
ELASTIC_EMAIL_FROM=outreach@sesameaccess.com   # must be a verified sender in Elastic Email
ELASTIC_EMAIL_FROM_NAME=SesameAccess
ELASTIC_EMAIL_REPLY_TO=outreach@sesameaccess.com   # optional — defaults to FROM

# Optional — Apollo.io contact enrichment
APOLLO_API_KEY=your_apollo_api_key
```

**Important:** The `ELASTIC_EMAIL_FROM` address must be verified in your Elastic Email account under Settings → Sending Domains / Verified Senders before emails will deliver.

---

## HubSpot setup already done (by you)

✅ `council_outreach` property created on Contact object
   - `TO_EMAIL` — queued for external send
   - `EMAILED` — email has been sent
   - `NOT_Interested` — exclude from all follow-up

✅ Two workflows created (currently **DISABLED** — you need to turn them on):
   - **"Council outreach - qualified responders"** → detects reply/engagement → adds to engaged list
   - **"Council outreach - remove not interested from follow-up"** → removes NOT_Interested contacts

✅ Static list **"Hubspot inport Council test 1.05.25"** — this is where engaged/replied contacts land

---

## Step 1 — Enable the HubSpot workflows

In HubSpot → Automation → Workflows, find and **enable** both workflows:
1. `Council outreach - qualified responders`
2. `Council outreach - remove not interested from follow-up`

---

## Step 2 — Install and place files

Copy the `contracts_finder/` folder to:
```
C:\Users\GilesHugo\.openclaw\agents\scraper\workspace\Inbound Data\contracts_finder\
```

Install the only Python dependency:
```bash
pip install requests
```

---

## Step 3 — Test Elastic Email connection

Preview the email content without calling the API:
```bash
python email_sender.py --test-email giles@sesameaccess.com --dry-run
```

Send a real test to confirm delivery, formatting, and sender address:
```bash
python email_sender.py --test-email giles@sesameaccess.com
```

Check your inbox to confirm the email arrives, looks correct, and the sender domain shows properly. If it lands in spam, check your Elastic Email domain SPF/DKIM settings.

---

## Step 4 — First run (dry run)

```bash
python orchestrator.py --mode=full --dry-run
```

This scrapes everything and enriches contacts but does NOT push to HubSpot or send any emails. Review `contracts_finder_leads.csv` to verify the keyword filtering is working correctly (no passenger lift maintenance contracts should appear).

---

## Step 5 — First live run

```bash
python orchestrator.py --mode=full
```

This will:
1. Scrape ~180 days of Contracts Finder notices
2. Filter for disability lift / DFG / platform lift contracts
3. Enrich missing contacts via Apollo
4. Push contacts to HubSpot with `council_outreach = TO_EMAIL`
5. Send outreach emails via SMTP
6. Update each contact to `council_outreach = EMAILED`

---

## Step 6 — Schedule daily incremental runs

### Option A: Cowork (recommended)
Ask Claude in Cowork to schedule:
> "Run the contracts finder pipeline daily at 6am"

### Option B: Windows Task Scheduler / cron
```
0 6 * * * cd "C:\Users\GilesHugo\.openclaw\agents\scraper\workspace\Inbound Data\contracts_finder" && python orchestrator.py --mode=incremental
```

### Option C: Via OpenClaw SAD agent
See `SAD_HEARTBEAT_SNIPPET.md` — add the snippet to SAD's HEARTBEAT.md.

---

## Step 7 — Update DATAMAP.md

Copy `DATAMAP_ENTRY.md` into your SAD agent's main DATAMAP.md.

---

## CLI Reference

```bash
# Normal daily run
python orchestrator.py --mode=incremental

# Full re-scan (180 days)
python orchestrator.py --mode=full

# Dry run — no HubSpot push, no emails
python orchestrator.py --mode=incremental --dry-run

# Scrape only — no Apollo, no HubSpot
python orchestrator.py --mode=incremental --dry-run --no-apollo

# Just send queued emails (without re-scraping)
python email_sender.py

# Preview who would be emailed
python email_sender.py --dry-run

# Send max 5 emails
python email_sender.py --limit 5

# Check pipeline run history
python orchestrator.py --mode=status

# Test scraper standalone → CSV only
python contracts_finder_scraper.py --since=2026-01-01

# Test Apollo for a specific council
python apollo_enrichment.py
```

---

## How deduplication works

Three layers protect against emailing the same person twice:

| Layer | File/System | What it checks |
|-------|-------------|----------------|
| 1 | `seen_contracts.json` | Contract refs already scraped (never re-processed) |
| 2 | `seen_contacts_log.json` | Emails queued/sent + date (local fast cache) |
| 3 | HubSpot `council_outreach` | If already `EMAILED` or `NOT_Interested`, skip re-queue |

The cooldown window is **90 days** (configurable in `config.py` as `OUTREACH_COOLDOWN_DAYS`).

---

## HubSpot contact fields pushed

Every contact is created/updated with:

| HubSpot Field | Value |
|---------------|-------|
| `firstname` / `lastname` | From contract notice or Apollo |
| `email` | From contract notice or Apollo |
| `phone` | From contract notice or Apollo |
| `jobtitle` | From Apollo enrichment |
| `company` | Council/buyer name |
| `council_outreach` | `TO_EMAIL` → email_sender sets `EMAILED` |
| `cf_contract_ref` | Contracts Finder OCDS reference |
| `cf_buyer_org` | Council name |
| `cf_contract_title` | Contract title (also used as Deal name) |
| `cf_published` | Notice published date |
| `cf_deadline` | Tender deadline |
| `cf_source` | Direct link to notice |
| `cf_last_outreach` | Date last emailed (set by email_sender) |
| `cf_outreach_count` | Running count of emails sent to this contact |

A **Deal** is also created for each new contact, named `"[Council] — [Contract Title]"`, and associated with the contact. This lets you track potential business in the HubSpot pipeline view.

---

## Email template customisation

The outreach email template lives in `email_sender.py` as:
- `EMAIL_SUBJECT` — subject line template
- `EMAIL_BODY_PLAIN` — plain text version
- `EMAIL_BODY_HTML` — HTML version

Edit these directly to change tone, content, or add a CTA link. The template variables available are:
- `{first_name}` — contact's first name
- `{council_name}` — council/buyer organisation
- `{contract_context}` — short description of the contract that triggered the lead
- `{sender_name}` — from `SMTP_FROM_NAME` in your .env
- `{website}` — `https://www.sesameaccess.com`

---

## Keyword tuning

All keywords are in `config.py`:
- `INCLUDE_KEYWORDS` — contract must match at least one
- `EXCLUDE_KEYWORDS` — any match here disqualifies the contract

The most important exclusion rule: **"passenger lift"** is excluded even if the notice also mentions "disability" — this directly solves the problem found in manual scraping where maintenance contracts dominated.

---

## Council knowledge base

`council_knowledge_base.json` is your growing intelligence file. Update it as relationships develop:
- Fill in the three Telford DFG officer names and emails Clare Turner introduced
- Record procurement routes each council uses
- Note approved supplier list status

This context is referenced in HubSpot notes and Apollo search tuning.
