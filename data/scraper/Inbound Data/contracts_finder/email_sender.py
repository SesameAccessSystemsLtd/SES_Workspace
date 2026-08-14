"""
email_sender.py — SesameAccess Outbound Email Sender (Elastic Email)
=====================================================================
Reads the HubSpot send queue (contacts where council_outreach = TO_EMAIL)
and sends personalised outreach emails via the Elastic Email HTTP API.
Updates council_outreach = EMAILED in HubSpot after each successful send.

WHY ELASTIC EMAIL (not HubSpot Marketing Email):
  SesameAccess does not have HubSpot Marketing Hub. Emails are sent externally
  and HubSpot is used purely for contact management, status tracking, and
  response handling via its two existing workflows.

FULL PIPELINE:
  1. contracts_finder_scraper.py   → finds new relevant contracts
  2. hubspot_pipeline.py           → pushes Contact + Company + Deal to HubSpot
                                     sets council_outreach = TO_EMAIL
  3. *** THIS SCRIPT ***           → reads TO_EMAIL queue from HubSpot
                                     sends via Elastic Email HTTP API
                                     sets council_outreach = EMAILED in HubSpot
  4. HubSpot workflow              → "Council outreach - qualified responders"
                                     reply/engagement → "Hubspot inport Council test 1.05.25"
  5. HubSpot workflow              → "Council outreach - remove not interested from follow-up"
                                     NOT_Interested → removed from all lists

USAGE:
  python email_sender.py                            # Send all queued
  python email_sender.py --dry-run                  # Preview only, no sends
  python email_sender.py --limit 10                 # Send at most 10 this run
  python email_sender.py --test-email you@domain.com  # Send one test to yourself
"""

import argparse
import base64
import io
import json
import logging
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

# Ensure the console can display Unicode (arrows, tick marks, etc.) on Windows.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import requests

from config import (
    ELASTIC_EMAIL_API_KEY, ELASTIC_EMAIL_API_URL,
    ELASTIC_EMAIL_FROM, ELASTIC_EMAIL_FROM_NAME, ELASTIC_EMAIL_REPLY_TO,
    OUTREACH_COOLDOWN_DAYS, SEEN_CONTACTS_FILE, LOG_DIR,
    SESAME_WEBSITE,
    COUNCIL_OUTREACH_PROP, COUNCIL_OUTREACH_EMAILED, COUNCIL_OUTREACH_NOT_INT,
)
from hubspot_pipeline import (
    get_send_queue, mark_as_emailed,
    load_seen_contacts, save_seen_contacts, is_in_cooldown,
    find_contact_by_email,
)

# ─────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────
Path(LOG_DIR).mkdir(exist_ok=True)
log_path = Path(LOG_DIR) / f"email_sender_{date.today().isoformat()}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_path, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger("cf_email_sender")


# ─────────────────────────────────────────────
# Logo (embedded inline so it works without external hosting)
# ─────────────────────────────────────────────
_LOGO_PATH = Path(r"C:\Users\GilesHugo\OneDrive - SESAME ACCESS AZURE\Desktop\Sesame Branding Images\Logo%20Stamp%20Blue.jpg.png")

def _load_logo_b64() -> str:
    """Return base64-encoded logo, or empty string if file not found."""
    try:
        return base64.b64encode(_LOGO_PATH.read_bytes()).decode("ascii")
    except Exception as e:
        log.warning(f"Could not load logo ({_LOGO_PATH}): {e}")
        return ""

_LOGO_B64 = _load_logo_b64()
_LOGO_TAG = (
    f'<img src="data:image/png;base64,{_LOGO_B64}" '
    'alt="Sesame Access" width="120" style="display:block;margin-bottom:20px;">'
    if _LOGO_B64 else ""
)


# ─────────────────────────────────────────────
# Email templates
# ─────────────────────────────────────────────
EMAIL_SUBJECT = "Insight for Heritage Projects — Sesame Access"

EMAIL_BODY_PLAIN = """\
Hello {first_name}!

Great to see disability access taken seriously within your Council.

At Sesame Access, we specialise in elegant, hidden wheelchair lifts used \
worldwide — fully compliant, highly reliable, and never refused planning on \
any Grade-listed building.

You can see a Sesame Access lift in action on our website: {website}

We'd love to connect with the right people across your Council, including \
Access or Inclusive Design Officers, Planning Case Officers, Access Teams, \
Building Control Officers, your Disabled Facilities Grant team, and central \
procurement.

Could you share the best contact details so we can support any upcoming \
accessibility needs?

Looking forward to helping you deliver the perfect solution.

Alison Lyons
Commercial Director
Sesame Access Systems
{website}

---
You're receiving this because your contact details were listed on a public \
procurement notice via Contracts Finder. To opt out, simply reply with \
"not interested" and we'll remove you from our list immediately.
"""

EMAIL_BODY_HTML = """\
<!DOCTYPE html>
<html>
<body style="font-family: Arial, sans-serif; font-size: 14px; color: #333333; \
max-width: 600px; margin: 0 auto; padding: 20px; line-height: 1.6;">

  {logo}

  <p>Hello <strong>{first_name}</strong>!</p>

  <p>Great to see disability access taken seriously within your Council.</p>

  <p>At Sesame Access, we specialise in elegant, <strong>hidden wheelchair \
lifts</strong> used worldwide — fully compliant, highly reliable, and never \
refused planning on any <strong>Grade&#8209;listed</strong> building.</p>

  <p>You can see a <strong><a href="{website}" style="color: #0066cc;">Sesame \
Access lift in action</a></strong> on our website.</p>

  <p>We'd love to connect with the right people across your Council, including \
Access or Inclusive Design Officers, Planning Case Officers, Access Teams, \
Building Control Officers, your Disabled Facilities Grant team, and central \
procurement.</p>

  <p>Could you share the best contact details so we can support any upcoming \
accessibility needs?</p>

  <p>Looking forward to helping you deliver the perfect solution.</p>

  <br>
  <p>
    Alison Lyons<br>
    <strong>Commercial Director</strong><br>
    Sesame Access Systems<br>
    <a href="{website}" style="color: #0066cc;">{website}</a>
  </p>

  <hr style="border: none; border-top: 1px solid #eeeeee; margin: 24px 0;">
  <p style="font-size: 11px; color: #999999;">
    You're receiving this because your contact details were listed on a public
    procurement notice via Contracts Finder. To opt out, simply reply with
    "not interested" and we'll remove you from our list immediately.
  </p>

</body>
</html>
"""


def build_email_content(contact: dict) -> tuple[str, str, str]:
    """
    Build personalised subject, plain text, and HTML for a HubSpot contact dict.
    Returns (subject, plain_body, html_body).
    """
    props = contact.get("properties", {})
    firstname    = (props.get("firstname") or "").strip() or "there"
    council_name = (props.get("cf_buyer_org") or props.get("company") or "your council").strip()

    subs = {
        "first_name":   firstname,
        "council_name": council_name,
        "website":      SESAME_WEBSITE,
        "logo":         _LOGO_TAG,
    }
    return (
        EMAIL_SUBJECT.format(**subs),
        EMAIL_BODY_PLAIN.format(**subs),
        EMAIL_BODY_HTML.format(**subs),
    )


# ─────────────────────────────────────────────
# Elastic Email HTTP API
# ─────────────────────────────────────────────
def send_via_elastic_email(
    to_email: str,
    to_name: str,
    subject: str,
    plain_body: str,
    html_body: str,
) -> tuple[bool, str]:
    """
    Send one transactional email via the Elastic Email v4 API.
    Returns (success: bool, message_id_or_error: str).

    API docs: https://elasticemail.com/developers/api-documentation/rest-api
    """
    payload = {
        "Recipients": {
            "To": [f"{to_name} <{to_email}>" if to_name else to_email],
        },
        "Content": {
            "From":     f"{ELASTIC_EMAIL_FROM_NAME} <{ELASTIC_EMAIL_FROM}>",
            "ReplyTo":  ELASTIC_EMAIL_REPLY_TO or ELASTIC_EMAIL_FROM,
            "Subject":  subject,
            "Body": [
                {
                    "ContentType": "HTML",
                    "Content":     html_body,
                    "Charset":     "utf-8",
                },
                {
                    "ContentType": "PlainText",
                    "Content":     plain_body,
                    "Charset":     "utf-8",
                },
            ],
        },
    }

    headers = {
        "X-ElasticEmail-ApiKey": ELASTIC_EMAIL_API_KEY,
        "Content-Type":          "application/json",
        "Accept":                "application/json",
    }

    try:
        resp = requests.post(
            ELASTIC_EMAIL_API_URL,
            headers=headers,
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        data       = resp.json()
        message_id = data.get("TransactionID", data.get("MessageID", "sent"))
        log.info(f"  ✅ Sent → {to_email} (id={message_id})")
        return True, message_id

    except requests.HTTPError as e:
        error_body = ""
        try:
            error_body = e.response.json()
        except Exception:
            error_body = e.response.text[:200]
        log.error(f"  ❌ Elastic Email HTTP {e.response.status_code} for {to_email}: {error_body}")
        return False, str(error_body)

    except requests.RequestException as e:
        log.error(f"  ❌ Request error sending to {to_email}: {e}")
        return False, str(e)


# ─────────────────────────────────────────────
# Main send loop
# ─────────────────────────────────────────────
def run_send_queue(
    dry_run: bool = False,
    limit: int = 0,
    test_email: Optional[str] = None,
    send_delay_secs: float = 2.0,
) -> dict:
    """
    Read HubSpot contacts where council_outreach = TO_EMAIL and send
    outreach emails via Elastic Email. Updates HubSpot to EMAILED on success.

    Args:
        dry_run:         Log what would be sent without calling Elastic Email
        limit:           Max emails to send this run (0 = all queued)
        test_email:      Send a single test email to this address and exit
        send_delay_secs: Pause between sends (default 2s — avoid rate limits)

    Returns:
        Summary stats dict
    """
    log.info("=" * 60)
    log.info(f"SesameAccess × Elastic Email sender — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    log.info(f"Mode: {'DRY RUN' if dry_run else 'LIVE'} | Limit: {limit or 'none'}")
    log.info("=" * 60)

    if not ELASTIC_EMAIL_API_KEY:
        log.error("EMAIL_SERVICE_Elasticemail_API_KEY is not set in environment")
        return {"error": "no_api_key"}

    if not ELASTIC_EMAIL_FROM:
        log.error("ELASTIC_EMAIL_FROM (sender address) is not set in environment")
        return {"error": "no_from_address"}

    # ── Test mode ─────────────────────────────────────────────────────────
    if test_email:
        dummy = {
            "id": "test",
            "properties": {
                "firstname":         "Test",
                "cf_buyer_org":      "Example Council",
                "company":           "Example Council",
                "cf_contract_title": "Disability access platform lift installation",
            }
        }
        subject, plain, html = build_email_content(dummy)
        log.info(f"Test email → {test_email} (from {ELASTIC_EMAIL_FROM})")
        log.info(f"Subject: {subject}")
        if dry_run:
            log.info("DRY RUN — email not sent")
            return {"test": True, "dry_run": True}
        success, msg_id = send_via_elastic_email(test_email, "Test Contact", subject, plain, html)
        return {"test": True, "sent": success, "message_id": msg_id}

    # ── Fetch HubSpot queue ──────────────────────────────────────────────
    fetch_limit = max(limit, 100) if limit > 0 else 100
    queue = get_send_queue(limit=fetch_limit)
    if not queue:
        log.info("Send queue is empty — no contacts with TO_EMAIL status in HubSpot")
        return {"sent": 0, "queued": 0}

    log.info(f"Contacts in queue: {len(queue)}")

    seen  = load_seen_contacts()
    stats = {
        "queued":            len(queue),
        "sent":              0,
        "skipped_cooldown":  0,
        "skipped_no_email":  0,
        "skipped_hs_check":  0,
        "failed":            0,
        "dry_run_previewed": 0,
    }

    for contact in queue:
        # Enforce per-run send limit
        if limit > 0 and (stats["sent"] + stats["dry_run_previewed"]) >= limit:
            log.info(f"Send limit ({limit}) reached — stopping")
            break

        props      = contact.get("properties", {})
        contact_id = contact["id"]
        email      = (props.get("email") or "").strip()
        firstname  = (props.get("firstname") or "").strip()
        lastname   = (props.get("lastname") or "").strip()
        full_name  = f"{firstname} {lastname}".strip()

        if not email:
            stats["skipped_no_email"] += 1
            log.info(f"  SKIP: contact {contact_id} has no email address")
            continue

        # Belt-and-braces cooldown check against local cache
        if is_in_cooldown(email, seen):
            stats["skipped_cooldown"] += 1
            log.info(f"  COOLDOWN: {email} — skip (contacted within {OUTREACH_COOLDOWN_DAYS} days)")
            continue

        # Live HubSpot status check — guard against double-sends if another process
        # has already sent or the contact opted out since the queue was fetched
        try:
            hs_contact = find_contact_by_email(email)
            if hs_contact:
                live_status = (hs_contact.get("properties", {}).get(COUNCIL_OUTREACH_PROP) or "").strip()
                if live_status in (COUNCIL_OUTREACH_EMAILED, COUNCIL_OUTREACH_NOT_INT):
                    stats["skipped_hs_check"] += 1
                    log.info(f"  SKIP (HubSpot): {email} already '{live_status}' — not re-sending")
                    continue
        except Exception as hs_err:
            log.warning(f"  HubSpot pre-check failed for {email}: {hs_err} — proceeding anyway")

        subject, plain_body, html_body = build_email_content(contact)

        if dry_run:
            log.info(f"  DRY RUN → {email} ({props.get('cf_buyer_org', '')}) | {subject[:50]}")
            stats["dry_run_previewed"] += 1
            continue

        # Send via Elastic Email
        success, msg_id = send_via_elastic_email(email, full_name, subject, plain_body, html_body)

        if success:
            # Update HubSpot: council_outreach → EMAILED + cf_last_outreach date
            mark_as_emailed(contact_id, email, seen)
            stats["sent"] += 1
        else:
            stats["failed"] += 1

        time.sleep(send_delay_secs)

    # Persist cooldown log
    save_seen_contacts(seen)

    log.info("\n" + "=" * 60)
    log.info("SEND SUMMARY")
    for k, v in stats.items():
        log.info(f"  {k}: {v}")
    log.info("=" * 60)

    return stats


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Send queued SesameAccess outreach emails via Elastic Email"
    )
    parser.add_argument("--dry-run",    action="store_true",
                        help="Preview what would be sent — no API calls to Elastic Email")
    parser.add_argument("--limit",      type=int, default=0,
                        help="Max emails to send this run (0 = all queued)")
    parser.add_argument("--test-email", default=None,
                        help="Send a single test email to this address and exit")
    parser.add_argument("--delay",      type=float, default=2.0,
                        help="Seconds between sends (default: 2)")
    args = parser.parse_args()

    result = run_send_queue(
        dry_run=args.dry_run,
        limit=args.limit,
        test_email=args.test_email,
        send_delay_secs=args.delay,
    )

    if result.get("error"):
        print(f"\n❌ Error: {result['error']}")
        raise SystemExit(1)

    sent = result.get("sent", 0)
    prev = result.get("dry_run_previewed", 0)
    if args.dry_run:
        print(f"\n✅ Dry run complete — {prev} email(s) would be sent")
    else:
        print(f"\n✅ Done — {sent} sent, {result.get('failed', 0)} failed")
