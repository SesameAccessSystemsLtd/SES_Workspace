# Skill: rc_transcripts

**Location:** `workspace/skills/rc_transcripts/`
**Entry point:** `rc_transcript_fetcher.py`
**Auth:** Microsoft Graph API — reuses existing OpenClaw app credentials (same as `email_reader` / purchasing fetcher). No new Azure AD registration needed.

---

## What it does

Reads the shared mailbox `rc-notes@sesameaccess.onmicrosoft.com` via Graph API and extracts structured data from RingCentral AI summary emails (sender: `service-uk@ringcentral.com`, subject: "Notes from your call...").

Outputs:
- `workspace/data/rc_transcripts/YYYY-MM-DD_HH-MM_ContactName.txt` — one human-readable file per call
- `workspace/data/rc_transcripts_raw.json` — append-only structured JSON log (all records)
- `workspace/data/rc_processed_email_ids.json` — dedup state (safe to re-run)

Parsed fields per call:
- `contact_name` — extracted from subject line
- `received_datetime`, `received_date`, `received_time`, `day_of_week`
- `parsed.summary` — AI-generated call summary
- `parsed.action_items` — next steps / follow-ups
- `parsed.key_topics` — topics discussed
- `parsed.highlights` — notable moments
- `parsed.raw_text` — full plain-text fallback (always present)

---

## Commands

```bash
# Standard run — fetch last 24 hours
& "C:\Users\GilesHugo\.openclaw\venv\Scripts\python.exe" "C:\Users\GilesHugo\.openclaw\workspace\skills\rc_transcripts\rc_transcript_fetcher.py"

# First run — backfill 30 days
& "C:\Users\GilesHugo\.openclaw\venv\Scripts\python.exe" "C:\Users\GilesHugo\.openclaw\workspace\skills\rc_transcripts\rc_transcript_fetcher.py" --lookback 720

# Dry-run test (prints first result, saves nothing)
& "C:\Users\GilesHugo\.openclaw\venv\Scripts\python.exe" "C:\Users\GilesHugo\.openclaw\workspace\skills\rc_transcripts\rc_transcript_fetcher.py" --test
```

---

## Prerequisites

1. **Mailbox must be reachable by the Graph app.** The Azure AD app (`Purchaisng_secret_ID`) needs `Mail.Read` application permission and must be granted access to `rc-notes@sesameaccess.onmicrosoft.com`. If you get a 403 error, ask your O365 admin to add the mailbox to the app's allowed resources (or confirm tenant-wide Mail.Read is already granted).

2. **Emails must be routed to the shared mailbox.** Set up a mail rule in Exchange/O365 admin to forward/copy RC notes emails (from: `service-uk@ringcentral.com`) to `rc-notes@sesameaccess.onmicrosoft.com`.

3. **No new .env keys needed** — uses `Purchaisng_secret_ID` and `purchasing_Email_Api_Key` already in `.env`.

---

## Current state (2026-08-11)

- **`rc_email_notes.py`** (under `skills/RC API caller/`) is the live fetcher — pulls RC AI call notes from the `rc-notes@sesameaccess.onmicrosoft.com` shared mailbox into `skills/RC API caller/call_notes_email/*.txt`. Fixed 2026-08-11 to read `PURCHASING_API_KEY` (was expired `PURCHASING_EMAIL_API_KEY`). Cron `fcdffdd011f5` hourly.
- **`rc_transcript_fetcher.py`** (this skill) is the STRUCTURED extractor (summary/action_items/key_topics). It is NOT yet wired to a cron and has never produced `data/rc_transcripts/` output in SES_Workspace. Needs re-pointing from `.openclaw` paths and a cron job.
- No `rc_call_notes` table exists in ops.db yet (planned; SKILL.md stale on that).

## Dry-run mapping (proved workable, 2026-08-11)

Thameside call notes → concrete Asana tasks, via three routes:
1. **Explicit part number** in the recap (best). E.g. "1594 1010 L 084" → `parts_control.sesame_pn=1594.1010.L.084` → "Pivot block Machining", gid `1216623739800393`, pack `1594.1010.L.ScissorLift_Single_LowProfile 1600.` (= job 1594 St Mary).
2. **Project/place name** from the summary. "St. Mary's"→1594, "Fifth Avenue Tesla"→1555 (QE2/Tesla handrail), "Russell Square"→1582. Requires a place-name → job-number index (not currently in DB as a lookup table).
3. **Exact-vendor match**: Thameside = supplier id 145 → `procurement_tasks` vendor_name variants ("Thameside", "Thameside Eng", "thameside eng") are INCONSISTENT (86 rows, ~13 spellings) — needs normalisation before reliable matching.
Note: most Thameside calls reference projects by name, not PO. Only a minority carry a parseable part number.

## Two mailboxes (future)

When the sales mailbox is ready (`rc-notes-sales@sesameaccess.onmicrosoft.com` or similar), pass `--mailbox` arg or call `RCTranscriptFetcher(mailbox=...)` directly. The parser and auth are mailbox-agnostic.

---

## Planned integrations (Hermes / long-term)

- **CRM lookup:** match `contact_name` against HubSpot contacts (`hubspot-crm` skill)
- **Asana mapping:** match contact/topic to open tasks in `ops.db` → submit write request via `workspace/REQUESTS/` to SAP
- **Phone number index:** cross-reference caller number from email metadata against global contacts index
- **ops.db table:** `rc_call_notes` — structured log queryable by all agents

---

## Specialist agents

`scraper` (SAD) — primary runner for scheduled fetch
`ops_agent` (SAO) — reads `rc_transcripts_raw.json` for CRM/Asana mapping
`main` (SESAL) — read-only access to outputs
