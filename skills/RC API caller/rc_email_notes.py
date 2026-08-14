"""
rc_email_notes.py
=================
Polls the rc-notes@sesameaccess.onmicrosoft.com shared mailbox for RingCentral
AI summary emails (forwarded by each user), extracts the content, and saves
structured .txt files to OUTPUT_FOLDER.

Run as scheduled task or on demand.
Config read from ~/.hermes/.env
"""

import os, re, json, logging, requests
from datetime import datetime, timezone
from pathlib import Path
from html import unescape
from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[3] / ".hermes" / ".env", override=True)  # .env wins over stale gateway env

TENANT_ID     = os.getenv("PURCHASING_TENANT_ID")
CLIENT_ID     = os.getenv("PURCHAISNG_SECRET_ID")
CLIENT_SECRET = os.getenv("PURCHASING_API_KEY") or os.getenv("PURCHASING_EMAIL_API_KEY")
MAILBOX       = "rc-notes@sesameaccess.onmicrosoft.com"
OUTPUT_FOLDER = Path(os.getenv("RC_EMAIL_NOTES_FOLDER",
    str(Path(__file__).parent / "call_notes_email")))
STATE_FILE    = Path(os.getenv("RC_EMAIL_STATE_FILE",
    str(Path(__file__).parent / "processed_emails.json")))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(Path(__file__).parent / "rc_email_notes.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


def get_token() -> str:
    resp = requests.post(
        f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token",
        data={"grant_type": "client_credentials", "client_id": CLIENT_ID,
              "client_secret": CLIENT_SECRET, "scope": "https://graph.microsoft.com/.default"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"processed_ids": []}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def strip_html(html: str) -> str:
    """Remove HTML tags and decode entities."""
    text = re.sub(r'<br\s*/?>', '\n', html, flags=re.IGNORECASE)
    text = re.sub(r'<p[^>]*>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</p>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    text = unescape(text)
    text = re.sub(r'\r\n', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def extract_rc_section(plain_text: str) -> str:
    """
    Isolate the RingCentral original email content from forwarded message.
    Looks for the RC email body starting after 'Notes' header or 'Dear ...'
    and ending before 'Powered by RingCentral'.
    """
    # Find where the RC original content starts (after forwarding header)
    start_markers = [
        r"Notes\s+Dear\s+",
        r"Dear\s+\w+[\w\s]+,\s+Here",
    ]
    start = -1
    for marker in start_markers:
        m = re.search(marker, plain_text, re.IGNORECASE)
        if m:
            start = m.start()
            break
    if start == -1:
        # Fall back: look for "Here's the notes from your call"
        m = re.search(r"Here.s the notes from your call", plain_text, re.IGNORECASE)
        start = m.start() if m else 0

    end_markers = ["Powered by RingCentral", "Copyright 2026 RingCentral", "Terms of Use"]
    end = len(plain_text)
    for marker in end_markers:
        idx = plain_text.find(marker, start)
        if idx != -1 and idx < end:
            end = idx

    return plain_text[start:end].strip()


def parse_rc_email(subject: str, plain_text: str) -> dict:
    """Parse RC email into structured fields."""
    rc_text = extract_rc_section(plain_text)

    # Extract call date/time/parties from subject
    # Subject: "Notes from your call with [Name] [Phone] on DD/MM/YYYY HH:MM"
    # (may be prefixed with "FW: " or "RE: ")
    clean_subject = re.sub(r'^(FW:|RE:|FWD:)\s*', '', subject, flags=re.IGNORECASE).strip()
    match = re.search(
        r"Notes from your call with (.+?) on (\d{2}/\d{2}/\d{4}) (\d{2}:\d{2})",
        clean_subject, re.IGNORECASE
    )
    caller_name = match.group(1).strip() if match else "Unknown"
    call_date   = match.group(2) if match else ""
    call_time   = match.group(3) if match else ""

    # Extract summary — match time pattern HH:MM then the sentence colon after it
    summary = ""
    m = re.search(
        r"Here.s the notes from your call.+?\d{1,2}:\d{2}[^:]*:\s*(.+?)(?:Recap|Tasks|$)",
        rc_text, re.DOTALL | re.IGNORECASE
    )
    if m:
        summary = m.group(1).strip()

    # Extract Recap
    recap = ""
    m = re.search(r"Recap\s+(.+?)(?:Tasks|View transcript|↗|$)", rc_text, re.DOTALL | re.IGNORECASE)
    if m:
        recap = m.group(1).strip()

    # Extract Tasks
    tasks = ""
    m = re.search(r"Tasks\s+(.+?)(?:View transcript|↗|Powered by|$)", rc_text, re.DOTALL | re.IGNORECASE)
    if m:
        raw_tasks = m.group(1).strip()
        tasks = raw_tasks if raw_tasks.lower() not in ("none", "") else "None"

    return {
        "caller_name": caller_name,
        "call_date":   call_date,
        "call_time":   call_time,
        "summary":     summary,
        "recap":       recap,
        "tasks":       tasks,
    }


def format_output(parsed: dict, subject: str, forwarded_by: str) -> str:
    lines = [
        "=" * 60,
        "  RINGCENTRAL AI CALL NOTES",
        f"  {parsed['call_date']}  {parsed['call_time']}",
        "=" * 60,
        f"With:         {parsed['caller_name']}",
        f"Forwarded by: {forwarded_by}",
        "",
    ]
    if parsed["summary"]:
        lines += ["--- SUMMARY " + "-" * 48, parsed["summary"], ""]
    if parsed["recap"]:
        lines += ["--- RECAP " + "-" * 50, parsed["recap"], ""]
    if parsed["tasks"]:
        lines += ["--- TASKS " + "-" * 50, parsed["tasks"], ""]
    lines += ["(Full transcript: RC web portal only — no RingSense API)", ""]
    return "\n".join(lines)


def safe_filename(parsed: dict) -> str:
    date_str = parsed["call_date"].replace("/", "-") if parsed["call_date"] else "unknown-date"
    time_str = parsed["call_time"].replace(":", "-") if parsed["call_time"] else "unknown-time"
    name = re.sub(r'[^\w\s-]', '', parsed["caller_name"]).strip()[:50]
    name = re.sub(r'\s+', '_', name)
    return f"{date_str}_{time_str}_{name}.txt"


def fetch_messages(token: str, processed_ids: set) -> list:
    hdrs = {"Authorization": f"Bearer {token}"}
    msgs = []
    url = f"https://graph.microsoft.com/v1.0/users/{MAILBOX}/messages"
    params = {
        "$top": 50,
        "$orderby": "receivedDateTime desc",
        "$select": "id,subject,from,receivedDateTime,body,isRead",
    }
    while url:
        r = requests.get(url, headers=hdrs, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        for m in data.get("value", []):
            subj = m.get("subject", "").lower()
            if "notes from your call" in subj and m["id"] not in processed_ids:
                msgs.append(m)
        url = data.get("@odata.nextLink")
        params = None
    return msgs


def mark_read(token: str, msg_id: str) -> None:
    requests.patch(
        f"https://graph.microsoft.com/v1.0/users/{MAILBOX}/messages/{msg_id}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"isRead": True},
        timeout=30,
    )


def main():
    log.info("Starting RC email notes sync...")
    state = load_state()
    processed = set(state["processed_ids"])

    token = get_token()
    messages = fetch_messages(token, processed)
    log.info(f"Found {len(messages)} unprocessed RC notes emails")

    OUTPUT_FOLDER.mkdir(parents=True, exist_ok=True)
    new_count = 0

    for msg in messages:
        msg_id   = msg["id"]
        subject  = msg.get("subject", "")
        forwarded_by = msg.get("from", {}).get("emailAddress", {}).get("address", "unknown")
        body_html = msg.get("body", {}).get("content", "")

        plain = strip_html(body_html)
        parsed = parse_rc_email(subject, plain)

        if not parsed["summary"] and not parsed["recap"]:
            log.info(f"  Skipping (no extractable content): {subject[:60]}")
            processed.add(msg_id)
            continue

        content  = format_output(parsed, subject, forwarded_by)
        filename = safe_filename(parsed)
        out_path = OUTPUT_FOLDER / filename

        # Avoid overwriting if filename collision (same call, multiple forwards)
        if out_path.exists():
            stem = out_path.stem
            out_path = OUTPUT_FOLDER / f"{stem}_{msg_id[:8]}.txt"

        out_path.write_text(content, encoding="utf-8")
        mark_read(token, msg_id)
        processed.add(msg_id)
        new_count += 1
        log.info(f"  Saved: {filename}")

    state["processed_ids"] = list(processed)[-5000:]
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    save_state(state)
    log.info(f"Done. {new_count} new note file(s) saved to {OUTPUT_FOLDER}")


if __name__ == "__main__":
    main()
