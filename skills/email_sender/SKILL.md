---
name: email_sender
description: Send a notification email to Giles from any agent. Use this when you need to alert, report, or summarise something that Giles should see in his inbox. Simple single-message send — not for bulk campaigns (use elastic_email for those).
---

# Email Sender Skill

Internal notification email skill. Agents use this to reach Giles directly in his inbox.

## This is NOT the same as elastic_email

| Skill | Purpose |
|---|---|
| `email_sender` | Agent → Giles (internal notifications, reports, alerts) |
| `elastic_email` | Sesame Access → external leads (marketing campaigns) |

---

## Configuration

All set via `.openclaw/.env`:

```
AGENT_NOTIFY_EMAIL=giles@youremail.com     ← set this to your inbox
EMAIL_SERVICE_Elasticemail_API_KEY=...     ← already set
ELASTIC_EMAIL_FROM=alisonl@sesameaccess.com  ← already set
ELASTIC_EMAIL_FROM_NAME=Sesame Access        ← already set
```

---

## Usage

```
python workspace/skills/email_sender/scripts/notify.py \
  --subject "Daily Review Ready" \
  --body "Here is your morning summary..."
```

**With a structured HTML body:**
```
python workspace/skills/email_sender/scripts/notify.py \
  --subject "Production Blocker Alert" \
  --body "<p>Task <strong>X</strong> is blocked.</p>" \
  --html
```

**Tagging the sending agent (recommended):**
```
python workspace/skills/email_sender/scripts/notify.py \
  --subject "Navigator: Weekly Review" \
  --body "..." \
  --from-agent "Navigator"
```

This prefixes the subject: `[Navigator] Weekly Review`

---

## Agent Access

| Agent | Access |
|---|---|
| main (Commander Claw) | YES |
| personal_planner (Navigator) | YES |
| ops_agent (SAO) | YES |
| asana_agent (SAP) | YES |
| sesame_marketing (SAM) | Use elastic_email instead |
| scraper (SAD) | Use REQUESTS/ to ask main to notify |

---

## When to use it

- End of a scheduled heartbeat — send a summary
- Something urgent needs Giles's attention right now
- A report has been generated and is ready for review
- A cron job failed and Giles needs to know
- Navigator completing a weekly review

## When NOT to use it

- Bulk outreach to leads → use `elastic_email`
- Routine info Giles can see via Telegram → use Telegram
- Asking for approval on a small thing → just ask in the session

---

## Send log

Every send is appended to:
```
C:\Users\GilesHugo\.openclaw\workspace\db\email_send_log.jsonl
```
Same log as elastic_email — all outbound email in one place.
