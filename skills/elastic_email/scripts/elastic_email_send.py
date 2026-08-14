"""
elastic_email_send.py — Send emails via Elastic Email v4 API.

Supports:
  - Single recipient send
  - Bulk send from CSV (email, contact_name, company_name columns)
  - {{merge_tag}} substitution in HTML body
  - Dry-run preview mode
  - Campaign stats retrieval
  - JSONL send log at workspace/db/email_send_log.jsonl

Usage:
    # Single email
    python elastic_email_send.py --to "jane@example.com" --to-name "Jane Smith" \
        --subject "Subject here" --html-file email.html --campaign-tag "tag"

    # Bulk from CSV
    python elastic_email_send.py --bulk-csv leads.csv \
        --subject "Subject" --html-file email.html --campaign-tag "tag" --delay 2

    # Dry run
    python elastic_email_send.py --bulk-csv leads.csv --subject "X" --html-file e.html --dry-run

    # Stats
    python elastic_email_send.py --stats --campaign-tag "ihbc_outreach_2026_04"
"""

import argparse
import csv
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

API_KEY = (
    os.environ.get("EMAIL_SERVICE_Elasticemail_API_KEY")
    or os.environ.get("ELASTIC_EMAIL_API_KEY", "")
)
FROM_EMAIL = os.environ.get("ELASTIC_EMAIL_FROM", "alisonl@sesameaccess.com")
FROM_NAME = os.environ.get("ELASTIC_EMAIL_FROM_NAME", "Sesame Access")
BASE_URL = "https://api.elasticemail.com/v4"

LOG_PATH = os.environ.get(
    "OPENCLAW_EMAIL_LOG",
    r"C:\Users\GilesHugo\SES_Workspace\db\email_send_log.jsonl",
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def _headers() -> dict:
    if not API_KEY:
        raise ValueError(
            "ELASTIC_EMAIL_API_KEY not set. Add it to ~/.hermes/.env and reload."
        )
    return {
        "X-ElasticEmail-ApiKey": API_KEY,
        "Content-Type": "application/json",
    }


def send_email(
    to_email: str,
    to_name: str,
    subject: str,
    html_body: str,
    plain_body: str | None = None,
    campaign_tag: str | None = None,
) -> dict:
    """
    Send a single transactional email via Elastic Email v4.
    Returns the API response dict.
    """
    payload = {
        "Recipients": {
            "To": [f"{to_name} <{to_email}>" if to_name else to_email],
        },
        "Content": {
            "Body": [
                {"ContentType": "HTML", "Charset": "utf-8", "Content": html_body},
            ],
            "From": f"{FROM_NAME} <{FROM_EMAIL}>",
            "Subject": subject,
        },
    }
    if plain_body:
        payload["Content"]["Body"].append(
            {"ContentType": "PlainText", "Charset": "utf-8", "Content": plain_body}
        )
    if campaign_tag:
        payload["Options"] = {"ChannelName": campaign_tag}

    resp = requests.post(
        f"{BASE_URL}/emails/transactional",
        headers=_headers(),
        json=payload,
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


def get_campaign_stats(campaign_tag: str) -> dict:
    """Fetch delivery statistics for a channel/campaign tag."""
    resp = requests.get(
        f"{BASE_URL}/statistics/channel",
        headers=_headers(),
        params={"channelName": campaign_tag},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Merge tag substitution
# ---------------------------------------------------------------------------

def apply_merge_tags(template: str, row: dict) -> str:
    """Replace {{field}} tokens with values from row dict."""
    contact_name = row.get("contact_name", "") or row.get("to_name", "")
    first_name = contact_name.split()[0] if contact_name.strip() else "there"

    defaults = {
        "first_name":   first_name,
        "company_name": row.get("company_name", "your firm"),
        "region":       row.get("region", "your region"),
    }
    merged = defaults
    merged.update({k: v for k, v in row.items() if v})

    def replacer(m: re.Match) -> str:
        key = m.group(1).strip()
        return str(merged.get(key, m.group(0)))

    return re.sub(r"\{\{(\w+)\}\}", replacer, template)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def append_log(entry: dict) -> None:
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def log_send(
    to: str,
    campaign_tag: str,
    status: str,
    message_id: str | None = None,
    error: str | None = None,
) -> None:
    append_log({
        "timestamp":    datetime.now(timezone.utc).isoformat(),
        "campaign_tag": campaign_tag or "",
        "to":           to,
        "status":       status,
        "message_id":   message_id,
        "error":        error,
    })


# ---------------------------------------------------------------------------
# Core send logic
# ---------------------------------------------------------------------------

def _load_html(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _check_unsubscribe(html: str) -> None:
    if "unsubscribe" not in html.lower():
        log.warning(
            "HTML body does not contain an unsubscribe link. "
            "Add {{unsubscribe_link}} or a plain URL before sending externally."
        )


def send_single(args: argparse.Namespace, html_template: str) -> None:
    if not args.to:
        log.error("--to is required for single send.")
        sys.exit(1)

    row = {"to_name": args.to_name or "", "company_name": ""}
    html_body = apply_merge_tags(html_template, row)

    if args.dry_run:
        log.info("[DRY-RUN] Would send to %s | subject: %s | tag: %s",
                 args.to, args.subject, args.campaign_tag)
        return

    _check_unsubscribe(html_body)
    try:
        result = send_email(
            args.to, args.to_name or "", args.subject, html_body,
            campaign_tag=args.campaign_tag,
        )
        msg_id = result.get("MessageID", "")
        log.info("Sent to %s | message_id=%s", args.to, msg_id)
        log_send(args.to, args.campaign_tag or "", "sent", msg_id)
    except requests.exceptions.HTTPError as e:
        log.error("Failed to send to %s: %s", args.to, e)
        log_send(args.to, args.campaign_tag or "", "failed", error=str(e))
        sys.exit(1)


def send_bulk(args: argparse.Namespace, html_template: str) -> None:
    if not os.path.exists(args.bulk_csv):
        log.error("CSV not found: %s", args.bulk_csv)
        sys.exit(1)

    with open(args.bulk_csv, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    log.info("Loaded %d recipients from %s", len(rows), args.bulk_csv)
    if not rows:
        log.warning("CSV is empty — nothing to send.")
        return

    _check_unsubscribe(html_template)

    sent = 0
    failed = 0
    skipped = 0

    for i, row in enumerate(rows):
        email = (row.get("email") or "").strip()
        if not email:
            log.warning("Row %d: no email address — skipped.", i)
            skipped += 1
            continue

        to_name = (row.get("contact_name") or row.get("to_name") or "").strip()
        html_body = apply_merge_tags(html_template, {**row, "to_name": to_name})

        if args.dry_run:
            first_name = to_name.split()[0] if to_name else "there"
            log.info(
                "[DRY-RUN] %d/%d → %s (%s) | company=%s",
                i + 1, len(rows), email, first_name, row.get("company_name", ""),
            )
            sent += 1
            continue

        try:
            result = send_email(
                email, to_name, args.subject, html_body,
                campaign_tag=args.campaign_tag,
            )
            msg_id = result.get("MessageID", "")
            log.info("  [%d/%d] Sent → %s | id=%s", i + 1, len(rows), email, msg_id)
            log_send(email, args.campaign_tag or "", "sent", msg_id)
            sent += 1
        except requests.exceptions.HTTPError as e:
            log.error("  [%d/%d] Failed → %s: %s", i + 1, len(rows), email, e)
            log_send(email, args.campaign_tag or "", "failed", error=str(e))
            failed += 1

        if i < len(rows) - 1:
            time.sleep(args.delay)

    log.info("Bulk send complete. sent=%d failed=%d skipped=%d dry_run=%s",
             sent, failed, skipped, args.dry_run)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Send emails via Elastic Email v4.")
    parser.add_argument("--to",           help="Single recipient email address.")
    parser.add_argument("--to-name",      default="", help="Single recipient display name.")
    parser.add_argument("--subject",      help="Email subject line.")
    parser.add_argument("--html-file",    help="Path to HTML body template file.")
    parser.add_argument("--bulk-csv",     help="CSV with columns: email, contact_name, company_name.")
    parser.add_argument("--campaign-tag", default="", help="Campaign tag for Elastic Email channel.")
    parser.add_argument("--delay",        type=float, default=1.0,
                        help="Seconds between bulk sends (default: 1).")
    parser.add_argument("--dry-run",      action="store_true",
                        help="Preview mode — no emails sent, no log written.")
    parser.add_argument("--stats",        action="store_true",
                        help="Fetch stats for --campaign-tag. No email sent.")

    args = parser.parse_args()

    # Stats mode
    if args.stats:
        if not args.campaign_tag:
            log.error("--campaign-tag required with --stats.")
            sys.exit(1)
        stats = get_campaign_stats(args.campaign_tag)
        print(json.dumps(stats, indent=2))
        return

    # Require subject + html for all send modes
    if not args.subject:
        log.error("--subject is required.")
        sys.exit(1)
    if not args.html_file:
        log.error("--html-file is required.")
        sys.exit(1)
    if not os.path.exists(args.html_file):
        log.error("HTML file not found: %s", args.html_file)
        sys.exit(1)

    html_template = _load_html(args.html_file)

    if args.bulk_csv:
        send_bulk(args, html_template)
    elif args.to:
        send_single(args, html_template)
    else:
        log.error("Provide either --to (single) or --bulk-csv (bulk).")
        sys.exit(1)


if __name__ == "__main__":
    main()
