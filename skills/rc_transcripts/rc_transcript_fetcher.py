"""
rc_transcript_fetcher.py
========================
Reads the RC Notes shared mailbox (rc-notes@sesameaccess.onmicrosoft.com) via
Microsoft Graph API and extracts structured data from RingCentral AI summary
emails (from: service-uk@ringcentral.com, subject: "Notes from your call...").

Auth: reuses the existing OpenClaw Graph API app (same credentials as email_reader).
No new Azure AD registration required.

Output:
  - workspace/data/rc_transcripts_raw.json   — append-only structured records
  - workspace/data/rc_transcripts/           — one .txt file per call (human-readable)
  - workspace/data/rc_processed_email_ids.json — dedup state

Usage:
  python rc_transcript_fetcher.py            # fetch new emails, parse, save
  python rc_transcript_fetcher.py --lookback 48  # override lookback hours
  python rc_transcript_fetcher.py --test     # dry-run: print first result, don't save
"""

import os
import re
import sys
import json
import argparse
import requests
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dotenv import load_dotenv
from bs4 import BeautifulSoup

# ── Paths ─────────────────────────────────────────────────────────────────────

ROOT        = Path(r"C:\Users\GilesHugo\.hermes")   # .env lives here post-decommission
ENV_PATH    = ROOT / ".env"
DATA_DIR    = Path(r"C:\Users\GilesHugo\SES_Workspace\data")
TXT_DIR     = DATA_DIR / "rc_transcripts"
RAW_JSON    = DATA_DIR / "rc_transcripts_raw.json"
STATE_FILE  = DATA_DIR / "rc_processed_email_ids.json"

load_dotenv(ENV_PATH, override=True)

# ── Auth (same app as email_reader / purchasing fetcher) ──────────────────────

TENANT_ID     = "3bfd9e7d-219f-48db-bd9e-e63b3b990a1a"
CLIENT_ID     = os.environ.get("Purchaisng_secret_ID")
CLIENT_SECRET = os.environ.get("PURCHASING_API_KEY") or os.environ.get("purchasing_Email_Api_Key")
SCOPES        = ["https://graph.microsoft.com/.default"]

RC_MAILBOX    = "rc-notes@sesameaccess.onmicrosoft.com"
RC_SENDER     = "service-uk@ringcentral.com"
RC_SUBJECT_KW = "notes from your call"   # case-insensitive match

# ── Token ─────────────────────────────────────────────────────────────────────

def get_access_token() -> str:
    url = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
    resp = requests.post(url, data={
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "scope":         " ".join(SCOPES),
        "grant_type":    "client_credentials",
    }, timeout=30)
    resp.raise_for_status()
    return resp.json()["access_token"]

# ── Graph fetch ───────────────────────────────────────────────────────────────

def fetch_rc_emails(token: str, since: datetime, max_results: int = 100) -> list:
    """Fetch RC notes emails from the shared mailbox, newest first."""
    headers = {"Authorization": f"Bearer {token}"}
    since_iso = since.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Graph filter: sender + date. Subject keyword is checked post-fetch
    # (Graph $filter on subject is unreliable for partial matches).
    params = {
        "$filter": (
            f"from/emailAddress/address eq '{RC_SENDER}' "
            f"and receivedDateTime ge {since_iso}"
        ),
        "$orderby": "receivedDateTime desc",
        "$top": max_results,
        "$select": "id,subject,receivedDateTime,from,bodyPreview,body,hasAttachments",
    }

    url = f"https://graph.microsoft.com/v1.0/users/{RC_MAILBOX}/messages"
    resp = requests.get(url, headers=headers, params=params, timeout=30)

    if resp.status_code == 403:
        print("ERROR 403: The Graph app does not have Mail.Read permission for this mailbox.")
        print("Ask your O365 admin to grant the app access to rc-notes@sesameaccess.onmicrosoft.com")
        sys.exit(1)
    if resp.status_code == 404:
        print(f"ERROR 404: Mailbox {RC_MAILBOX} not found. Check the address is correct.")
        sys.exit(1)

    resp.raise_for_status()
    all_messages = resp.json().get("value", [])

    # Post-filter: confirm subject contains the keyword
    return [
        m for m in all_messages
        if RC_SUBJECT_KW in m.get("subject", "").lower()
    ]

# ── HTML parser ───────────────────────────────────────────────────────────────

def clean(text: str) -> str:
    """Collapse whitespace."""
    return re.sub(r"\s+", " ", text or "").strip()


def parse_rc_email_body(html: str) -> dict:
    """
    Parse a RingCentral AI Notes email body into structured fields.
    RC emails vary slightly by version; this handles common layouts.
    Returns a dict with keys: summary, action_items, key_topics, raw_text.
    """
    soup = BeautifulSoup(html, "html.parser")
    raw_text = soup.get_text(separator="\n")

    result = {
        "summary": None,
        "action_items": [],
        "key_topics": [],
        "highlights": [],
        "raw_text": clean(raw_text),
    }

    # ── Strategy 1: look for labelled sections by heading text ────────────────
    section_map = {
        "summary":      ["summary", "call summary", "overview"],
        "action_items": ["action items", "next steps", "follow-up", "follow up", "to-do"],
        "key_topics":   ["key topics", "topics discussed", "topics covered", "key points"],
        "highlights":   ["highlights", "key highlights", "notable moments"],
    }

    # Gather all block-level elements with text
    blocks = soup.find_all(
        ["h1", "h2", "h3", "h4", "h5", "p", "td", "div", "li", "span"]
    )

    current_section = None
    for el in blocks:
        txt = clean(el.get_text())
        if not txt or len(txt) < 2:
            continue

        txt_lower = txt.lower().rstrip(":")

        # Check if this element is a section heading
        matched_section = None
        for section, keywords in section_map.items():
            if txt_lower in keywords or any(kw in txt_lower for kw in keywords):
                matched_section = section
                break

        if matched_section:
            current_section = matched_section
            continue

        # Accumulate content under current section
        if current_section == "summary" and result["summary"] is None:
            # Take the first substantial paragraph after the Summary heading
            if len(txt) > 20:
                result["summary"] = txt
        elif current_section in ("action_items", "key_topics", "highlights"):
            if len(txt) > 5:
                result[current_section].append(txt)

    # ── Strategy 2: regex fallback on raw text (if structured parse failed) ──
    if not result["summary"]:
        m = re.search(
            r"(?:summary|overview)[:\s]*(.+?)(?=\n[A-Z][^a-z\n]{3,}|\Z)",
            raw_text, re.IGNORECASE | re.DOTALL
        )
        if m:
            result["summary"] = clean(m.group(1))[:1000]

    if not result["action_items"]:
        m = re.search(
            r"(?:action items?|next steps?|follow.up)[:\s]*(.+?)(?=\n[A-Z][^a-z\n]{3,}|\Z)",
            raw_text, re.IGNORECASE | re.DOTALL
        )
        if m:
            items = [clean(l) for l in m.group(1).split("\n") if clean(l) and len(clean(l)) > 5]
            result["action_items"] = items[:10]

    return result


def extract_call_metadata(subject: str, received: str) -> dict:
    """Pull contact name and datetime from subject line and received timestamp."""
    # Subject: "Notes from your call with John Smith" or "Notes from your call — Sesame Access"
    contact = None
    for pat in [
        r"notes from your call with (.+)",
        r"notes from your call[—\-–:]\s*(.+)",
        r"notes from your call\s+(.+)",
    ]:
        m = re.search(pat, subject, re.IGNORECASE)
        if m:
            contact = clean(m.group(1))
            break

    dt = None
    try:
        dt = datetime.fromisoformat(received.replace("Z", "+00:00"))
    except Exception:
        pass

    return {
        "contact_name": contact,
        "received_datetime": received,
        "received_date": dt.strftime("%Y-%m-%d") if dt else None,
        "received_time": dt.strftime("%H:%M") if dt else None,
        "day_of_week": dt.strftime("%A") if dt else None,
    }

# ── Output ────────────────────────────────────────────────────────────────────

def format_txt(record: dict) -> str:
    lines = [
        "=" * 60,
        "  RINGCENTRAL CALL NOTES",
        f"  {record.get('day_of_week', '')} {record.get('received_date', '')}  {record.get('received_time', '')}",
        "=" * 60,
        f"Contact : {record.get('contact_name', 'Unknown')}",
        f"Subject : {record.get('subject', '')}",
        f"Email ID: {record.get('email_id', '')}",
        "",
    ]

    parsed = record.get("parsed", {})

    if parsed.get("summary"):
        lines += ["─── SUMMARY " + "─" * 48, parsed["summary"], ""]

    if parsed.get("action_items"):
        lines.append("─── ACTION ITEMS " + "─" * 43)
        for item in parsed["action_items"]:
            lines.append(f"• {item}")
        lines.append("")

    if parsed.get("key_topics"):
        lines.append("─── KEY TOPICS " + "─" * 45)
        for topic in parsed["key_topics"]:
            lines.append(f"• {topic}")
        lines.append("")

    if parsed.get("highlights"):
        lines.append("─── HIGHLIGHTS " + "─" * 45)
        for h in parsed["highlights"]:
            lines.append(f"• {h}")
        lines.append("")

    if parsed.get("raw_text") and not parsed.get("summary"):
        lines += ["─── RAW TEXT (parsing failed — review manually) " + "─" * 13,
                  parsed["raw_text"][:2000], ""]

    return "\n".join(lines)


def safe_filename(record: dict) -> str:
    date = record.get("received_date", "0000-00-00")
    time = (record.get("received_time") or "00-00").replace(":", "-")
    contact = record.get("contact_name") or "Unknown"
    safe = re.sub(r"[^\w\s\-]", "", contact).strip()[:50]
    return f"{date}_{time}_{safe}.txt"

# ── State ─────────────────────────────────────────────────────────────────────

def load_processed_ids() -> set:
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return set(json.load(f))
    return set()


def save_processed_ids(ids: set):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(sorted(ids), f, indent=2)


def append_raw_json(records: list):
    existing = []
    if RAW_JSON.exists():
        with open(RAW_JSON) as f:
            existing = json.load(f)
    existing.extend(records)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(RAW_JSON, "w") as f:
        json.dump(existing, f, indent=2, ensure_ascii=False)

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Fetch RC call notes from shared mailbox")
    parser.add_argument("--lookback", type=int, default=24,
                        help="Hours to look back (default: 24). First run: use 720 for 30 days.")
    parser.add_argument("--test", action="store_true",
                        help="Dry-run: print first parsed result, skip saving.")
    args = parser.parse_args()

    if not CLIENT_ID or not CLIENT_SECRET:
        print("FATAL: Purchaisng_secret_ID or purchasing_Email_Api_Key not set in .env")
        sys.exit(1)

    since = datetime.now(timezone.utc) - timedelta(hours=args.lookback)
    processed_ids = load_processed_ids()

    print(f"Authenticating with Microsoft Graph...")
    token = get_access_token()
    print(f"Token acquired. Fetching RC notes emails since {since.strftime('%Y-%m-%d %H:%M')} UTC...")

    emails = fetch_rc_emails(token, since)
    print(f"Found {len(emails)} RC notes email(s).")

    new_records = []
    for msg in emails:
        email_id = msg["id"]
        if email_id in processed_ids:
            print(f"  [skip] Already processed: {msg.get('subject', '')[:60]}")
            continue

        subject   = msg.get("subject", "")
        received  = msg.get("receivedDateTime", "")
        body_html = msg.get("body", {}).get("content", "")

        meta   = extract_call_metadata(subject, received)
        parsed = parse_rc_email_body(body_html)

        record = {
            "email_id":          email_id,
            "subject":           subject,
            "mailbox":           RC_MAILBOX,
            **meta,
            "parsed":            parsed,
            "fetched_at":        datetime.now(timezone.utc).isoformat(),
        }

        if args.test:
            print("\n── TEST OUTPUT ──────────────────────────────────────")
            print(format_txt(record))
            print(json.dumps({k: v for k, v in record.items() if k != "parsed"}, indent=2))
            print(f"\nParsed sections: {list(k for k, v in parsed.items() if v and k != 'raw_text')}")
            return

        # Save .txt
        TXT_DIR.mkdir(parents=True, exist_ok=True)
        fname = safe_filename(record)
        (TXT_DIR / fname).write_text(format_txt(record), encoding="utf-8")
        print(f"  [saved] {fname}")

        new_records.append(record)
        processed_ids.add(email_id)

    if new_records:
        append_raw_json(new_records)
        save_processed_ids(processed_ids)
        print(f"\nDone. {len(new_records)} new record(s) saved.")
        print(f"  TXT files : {TXT_DIR}")
        print(f"  JSON log  : {RAW_JSON}")
    else:
        print("No new records to save.")


if __name__ == "__main__":
    main()
