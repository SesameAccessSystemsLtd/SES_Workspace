---
name: elastic_email
description: Send individual or bulk marketing emails via Elastic Email v4 API. Use this skill when sesame_marketing needs to send outreach emails, nurture sequences, or campaign blasts to leads. Logs all sends to the campaigns and campaign_results tables.
---

# Elastic Email Skill

Sends emails through the Elastic Email platform using the v4 REST API.

## Why This Exists

HubSpot can send emails, but Elastic Email gives us:
- Direct programmatic control over deliverability
- Per-send cost visibility
- Campaign tagging that maps to our openclaw `campaigns` table
- Bulk sending without HubSpot sequence limits

## Configuration

**API key:** Set `ELASTIC_EMAIL_API_KEY` in `.openclaw/.env`

**From address:** Must be a verified sender in your Elastic Email account.
Default: set `ELASTIC_EMAIL_FROM` env var (e.g. `marketing@sesameaccess.com`).

```
ELASTIC_EMAIL_API_KEY=your_key_here
ELASTIC_EMAIL_FROM=marketing@sesameaccess.com
ELASTIC_EMAIL_FROM_NAME=Sesame Access
```

**Base URL:** `https://api.elasticemail.com/v4`

**Docs:** https://elasticemail.com/developers/api-documentation/rest-api

---

## Scripts

### `elastic_email_send.py`

**Send a single email:**
```
python scripts/elastic_email_send.py \
  --to "jane@example.com" \
  --to-name "Jane Smith" \
  --subject "Heritage Project: How Sesame Solves Hidden Hinge Access" \
  --html-file "path/to/email.html" \
  --campaign-tag "ihbc_outreach_2026_04"
```

**Send bulk from CSV:**
```
python scripts/elastic_email_send.py \
  --bulk-csv "path/to/new_leads_for_outreach.csv" \
  --subject "Heritage Project: How Sesame Solves Hidden Hinge Access" \
  --html-file "path/to/email.html" \
  --campaign-tag "ihbc_outreach_2026_04" \
  --delay 2
```

CSV must have columns: `email`, optionally `contact_name`, `company_name`.

**Preview (no send):**
Add `--dry-run` to either command to log recipients and render the email without sending.

**Get stats for a campaign:**
```
python scripts/elastic_email_send.py --stats --campaign-tag "ihbc_outreach_2026_04"
```

---

## Merge Tags

The HTML body supports simple `{{field}}` merge tags:
- `{{first_name}}` — first word of contact_name, or "there" as fallback
- `{{company_name}}` — company name from lead record
- `{{region}}` — region / branch from HESPR data

Example:
```html
<p>Hi {{first_name}},</p>
<p>We work with conservation practices across {{region}}...</p>
```

---

## Agent Access Policy

| Agent | Access |
|---|---|
| sesame_marketing | FULL (send, preview, stats) |
| main | READ stats only |
| scraper_agent | None |
| ops_agent | None |

---

## Before Sending — Mandatory Checklist

sesame_marketing MUST complete all steps before executing a bulk send:

1. **Campaign exists in DB** — `campaigns` table entry with status='active'
2. **List reviewed** — `new_leads_for_outreach.csv` reviewed for data quality
3. **Email approved** — HTML content signed off by SESAL (or explicitly authorised in task brief)
4. **Unsubscribe link** — HTML body must contain `{{unsubscribe_link}}` or a plain unsubscribe URL
5. **Test send done** — `--dry-run` run completed with no errors

---

## Logging

Every send attempt is appended to:
```
C:\Users\GilesHugo\.openclaw\workspace\db\email_send_log.jsonl
```

Format:
```json
{"timestamp": "2026-04-01T09:00:00Z", "campaign_tag": "...", "to": "...", "status": "sent|failed", "message_id": "...", "error": null}
```

---

## Rate Limits

Elastic Email free tier: 100 emails/day.
Paid plans: configurable.
Script enforces `--delay N` seconds between bulk sends (default: 1 second).
