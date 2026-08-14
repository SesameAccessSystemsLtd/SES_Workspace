"""
05_sheet_approver.py — Email Approval Sheet Processor

Reads rows from the Google Sheet Pending tab where Giles has set
the DECISION column. Creates REQUESTS/ files for SAP to act on,
then archives the rows to the Processed tab.

Decision values:
  A = Approve   — use proposed action as-is
  M = Amend     — use Amendment Notes column content instead
  R = Reject    — no Asana action, just archive

Requires: C:/Users/GilesHugo/.hermes/Google_Service_Account.json
See: workspace/skills/purchasing/GOOGLE_SHEETS_SETUP.md
"""

import os
import sys
from datetime import datetime, timezone
import uuid
import json

from dotenv import load_dotenv

load_dotenv(r"C:\Users\GilesHugo\.hermes\.env", override=True)

SERVICE_ACCOUNT_PATH = r"C:\Users\GilesHugo\.hermes\Google_Service_Account.json"
SHEET_ID = "1fvoSKnvH0vdM_aZbo2_seY6AU3SZaXABaxRUKSxWVlo"
PENDING_TAB = "Pending"
PROCESSED_TAB = "Processed"
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[2] / "lib"))
from path_config import p as _p

REQUESTS_DIR = _p("queues.requests")

HEADERS = [
    "Email ID", "Received", "Subject", "Sender Domain", "Document Type",
    "PO Number", "Matched Task", "Asana Link", "Proposed Action", "Comments",
    "DECISION", "Amendment Notes",
]
COL = {h: i for i, h in enumerate(HEADERS)}

PROCESSED_HEADERS = HEADERS + ["Outcome", "Request ID", "Request Status"]


def get_spreadsheet():
    try:
        import gspread
    except ImportError:
        print("ERROR: gspread not installed.")
        sys.exit(1)

    if not os.path.exists(SERVICE_ACCOUNT_PATH):
        print(f"ERROR: Google credentials not found at {SERVICE_ACCOUNT_PATH}")
        print("See workspace/skills/purchasing/GOOGLE_SHEETS_SETUP.md")
        sys.exit(1)

    gc = gspread.service_account(filename=SERVICE_ACCOUNT_PATH)
    return gc.open_by_key(SHEET_ID)


def get_or_create_tab(spreadsheet, tab_name, headers):
    try:
        return spreadsheet.worksheet(tab_name)
    except Exception:
        ws = spreadsheet.add_worksheet(title=tab_name, rows=1000, cols=len(headers))
        ws.append_row(headers, value_input_option="RAW")
        return ws


def _extract_task_gid(asana_url):
    """Extract task GID from Asana permalink URL."""
    if not asana_url:
        return None
    asana_url = asana_url.split("?")[0]  # strip query params e.g. ?focus=true
    parts = asana_url.rstrip("/").split("/")
    if len(parts) >= 2:
        candidate = parts[-1]
        if candidate.isdigit():
            return candidate
    return None


def already_has_request(email_id):
    """Return True if a REQUESTS/ JSON already exists for this email_id (prevents duplicates on re-run)."""
    if not os.path.isdir(REQUESTS_DIR):
        return False
    for fname in os.listdir(REQUESTS_DIR):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(REQUESTS_DIR, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("email_id") == email_id:
                return True
        except Exception:
            continue
    return False


def create_request_file(email_id, subject, task_gid, task_name, asana_url, action_text, comment_text=""):
    """Write a REQUESTS/ file for SAP to process."""
    os.makedirs(REQUESTS_DIR, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M")
    unique_id = str(uuid.uuid4())[:8]
    filename = f"{ts}-ops_agent-email_approval-{unique_id}.md"
    filepath = os.path.join(REQUESTS_DIR, filename)

    comment_section = ""
    if comment_text:
        comment_section = f"Comment To Push:\n{comment_text}\n\n"

    content = f"""---
From: ops_agent
To: asana_agent
Type: asana_write
Status: PENDING
Priority: normal
Created: {datetime.now(timezone.utc).isoformat()}
---

## Request
Action: update_task
Task GID: {task_gid or 'NOT_MATCHED'}
Task Name: {task_name}
Task URL: {asana_url}

Details:
{action_text}

{comment_section}## Context
Purchasing email approved by Giles via Email Approval Dashboard (Google Sheet).
Original email subject: {subject}
Email ID: {email_id}

If Task GID is NOT_MATCHED, SAP must locate the correct task manually using
the subject line and proposed action context before applying any updates.
"""

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    return filepath


def create_json_request_file(md_filepath, payload):
    json_path = md_filepath[:-3] + ".json"
    with open(json_path, "w", encoding="utf-8") as jf:
        json.dump(payload, jf, ensure_ascii=False, indent=2, default=str)
    return json_path


def main():
    spreadsheet = get_spreadsheet()
    pending_ws = get_or_create_tab(spreadsheet, PENDING_TAB, HEADERS)
    processed_ws = get_or_create_tab(spreadsheet, PROCESSED_TAB, PROCESSED_HEADERS)

    all_rows = pending_ws.get_all_values()
    if len(all_rows) <= 1:
        print("Pending tab is empty — nothing to process.")
        return

    # Build column map from actual sheet header row — handles old/new column layouts
    actual_header = all_rows[0]
    col = {h: i for i, h in enumerate(actual_header)}
    required = ["Email ID", "Subject", "Matched Task", "Asana Link",
                "Proposed Action", "DECISION", "Amendment Notes"]
    missing = [h for h in required if h not in col]
    if missing:
        print(f"ERROR: Sheet missing required columns: {missing}")
        print(f"Actual headers: {actual_header}")
        return

    data_rows = all_rows[1:]  # skip header

    to_archive = []   # list of (1-based row index, row list, outcome string)
    requests_created = 0
    rejected = 0
    skipped = 0

    for row_num, row in enumerate(data_rows, start=2):
        row = list(row) + [""] * (len(actual_header) - len(row))

        email_id    = row[col["Email ID"]].strip()
        subject     = row[col["Subject"]].strip()
        task_name   = row[col["Matched Task"]].strip()
        asana_url   = row[col["Asana Link"]].strip()
        proposed    = row[col["Proposed Action"]].strip()
        decision    = row[col["DECISION"]].strip().upper()
        amendment   = row[col["Amendment Notes"]].strip()
        comment_to_push = row[col["Comments"]].strip() if "Comments" in col else ""
        processed_at = row[col["Processed At"]].strip() if "Processed At" in col else ""

        if not decision:
            continue  # not yet decided
        if processed_at:
            continue  # already processed (shouldn't be in Pending but guard anyway)
        if decision not in ("A", "M", "R"):
            print(f"  Row {row_num}: unknown DECISION '{decision}' — skipping.")
            skipped += 1
            continue

        task_gid = _extract_task_gid(asana_url)
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        if decision == "R":
            rejected += 1
            outcome = "REJECTED"
            request_id = str(uuid.uuid4())
            to_archive.append((row_num, row, outcome, now_str, request_id))

        elif decision == "A":
            if already_has_request(email_id):
                print(f"  Row {row_num}: APPROVED and a prior REQUESTS/ file exists for this email — creating a new request anyway. Subject: {subject}")
            if not task_gid:
                print(f"  Row {row_num}: APPROVED but task not matched — SAP will need to locate manually. Subject: {subject}")
            request_id = str(uuid.uuid4())
            filepath = create_request_file(
                email_id, subject, task_gid, task_name, asana_url, proposed, comment_to_push
            )
            # write companion JSON with structured fields
            payload = {
                "request_id": request_id,
                "source": "email_approval_sheet",
                "row_index": row_num,
                "email_id": email_id,
                "subject": subject,
                "task_gid": task_gid,
                "task_name": task_name,
                "asana_url": asana_url,
                "action": proposed,
                "comment_to_push": comment_to_push,
                "decision": decision,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            create_json_request_file(filepath, payload)
            requests_created += 1
            outcome = "APPROVED"
            print(f"  APPROVED row {row_num}: REQUESTS file -> {os.path.basename(filepath)}")
            to_archive.append((row_num, row, outcome, now_str, request_id))

        elif decision == "M":
            if not amendment and not task_gid:
                # No override text and no usable link — nothing to act on
                print(f"  Row {row_num}: AMEND decision but Amendment Notes is empty and no Asana link — skipping. Subject: {subject}")
                skipped += 1
                continue
            if not amendment and task_gid:
                # Giles corrected the Asana Link in the sheet — approve with original action against new link
                print(f"  Row {row_num}: AMEND with corrected Asana link (no amendment text) — using proposed action.")
                action = proposed
            elif not task_gid:
                print(f"  Row {row_num}: AMENDED but task not matched — SAP will need to locate manually.")
                action = f"AMENDMENT (task not auto-matched — SAP must locate manually):\n{amendment}"
            else:
                action = f"AMENDMENT (Giles overrode proposed action):\nOriginal: {proposed}\nAmendment: {amendment}"
            if already_has_request(email_id):
                print(f"  Row {row_num}: AMENDED and a prior REQUESTS/ file exists for this email — creating a new request anyway. Subject: {subject}")
            request_id = str(uuid.uuid4())
            filepath = create_request_file(
                email_id, subject, task_gid, task_name, asana_url, action, comment_to_push
            )
            payload = {
                "request_id": request_id,
                "source": "email_approval_sheet",
                "row_index": row_num,
                "email_id": email_id,
                "subject": subject,
                "task_gid": task_gid,
                "task_name": task_name,
                "asana_url": asana_url,
                "action": action,
                "comment_to_push": comment_to_push,
                "decision": decision,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            create_json_request_file(filepath, payload)
            requests_created += 1
            outcome = "AMENDED"
            print(f"  AMENDED row {row_num}: REQUESTS file -> {os.path.basename(filepath)}")
            to_archive.append((row_num, row, outcome, now_str, request_id))

    if not to_archive:
        print("No decided rows found. Nothing to process.")
        return

    # Batch-write to Processed tab (single API call)
    processed_rows = []
    for row_num, row, outcome, now_str, request_id in to_archive:
        archived_row = list(row)
        if "Processed At" in col:
            archived_row[col["Processed At"]] = now_str
        processed_rows.append(archived_row + [outcome, request_id, "PENDING"])

    if processed_rows:
        processed_ws.append_rows(processed_rows, value_input_option="RAW")

    # Delete processed rows from Pending — reverse order to avoid index shift
    for row_num in sorted([r[0] for r in to_archive], reverse=True):
        pending_ws.delete_rows(row_num)

    print(
        f"\nDone: {requests_created} REQUESTS file(s) created, "
        f"{rejected} rejected, {skipped} skipped, "
        f"{len(to_archive)} rows deleted from Pending and archived to Processed tab."
    )


if __name__ == "__main__":
    main()
