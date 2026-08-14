"""
04_sheet_writer.py — Email Approval Dashboard Writer

Reads pending_approval_queue.json and writes new items to the
Google Sheet approval dashboard for Giles to review.

Skips items already in the sheet (matched by email_id in column A).

Requires: C:/Users/GilesHugo/.hermes/Google_Service_Account.json
See: workspace/skills/purchasing/GOOGLE_SHEETS_SETUP.md
"""

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(str(Path(__file__).parents[3] / ".hermes" / ".env"))

QUEUE_PATH = r"C:\Users\GilesHugo\SES_Workspace\data\pending_approval_queue.json"
SERVICE_ACCOUNT_PATH = r"C:\Users\GilesHugo\.hermes\Google_Service_Account.json"
SHEET_ID = "1fvoSKnvH0vdM_aZbo2_seY6AU3SZaXABaxRUKSxWVlo"
PENDING_TAB = "Pending"

HEADERS = [
    "Email ID", "Received", "Subject", "Sender Domain", "Document Type",
    "PO Number", "Matched Task", "Asana Link", "Proposed Action", "Comments",
    "DECISION", "Amendment Notes",
]


def get_spreadsheet():
    try:
        import gspread
    except ImportError:
        print("ERROR: gspread not installed.")
        print("Fix: C:\\Users\\GilesHugo\\SES_Workspace\\.venv\\Scripts\\pip.exe install gspread")
        sys.exit(1)

    if not os.path.exists(SERVICE_ACCOUNT_PATH):
        print(f"ERROR: Google credentials not found at {SERVICE_ACCOUNT_PATH}")
        print("See workspace/skills/purchasing/GOOGLE_SHEETS_SETUP.md for setup instructions.")
        sys.exit(1)

    gc = gspread.service_account(filename=SERVICE_ACCOUNT_PATH)
    return gc.open_by_key(SHEET_ID)


def get_or_create_tab(spreadsheet, tab_name):
    import gspread.exceptions
    try:
        return spreadsheet.worksheet(tab_name)
    except Exception:
        ws = spreadsheet.add_worksheet(title=tab_name, rows=1000, cols=len(HEADERS))
        ws.append_row(HEADERS, value_input_option="RAW")
        return ws


def get_existing_email_ids(ws):
    try:
        col_a = ws.col_values(1)
        return set(col_a[1:])  # skip header row
    except Exception:
        return set()


def main():
    if not os.path.exists(QUEUE_PATH):
        print("No pending approval queue found — skipping sheet write.")
        return

    with open(QUEUE_PATH, "r", encoding="utf-8") as f:
        queue = json.load(f)

    pending = [item for item in queue if item.get("status") == "pending_review"]
    if not pending:
        print("No pending_review items in queue — nothing to write.")
        return

    spreadsheet = get_spreadsheet()
    ws = get_or_create_tab(spreadsheet, PENDING_TAB)
    existing_ids = get_existing_email_ids(ws)

    added = 0
    skipped = 0

    for item in pending:
        email_id = item.get("email_id", "")
        if email_id in existing_ids:
            skipped += 1
            continue

        insights = item.get("insights", {})

        row = [
            email_id,
            item.get("received_at", ""),
            item.get("subject", ""),
            item.get("sender_domain", ""),
            insights.get("document_type", ""),
            insights.get("po_number") or "",
            item.get("asana_task_name") or "NOT MATCHED",
            item.get("asana_task_url") or "",
            item.get("proposed_action", ""),
            item.get("comment_to_push", ""),  # J - Comments
            "",   # K - DECISION — blank for Giles to fill
            "",   # L - Amendment Notes
        ]

        ws.append_row(row, value_input_option="USER_ENTERED")
        existing_ids.add(email_id)
        added += 1

    print(f"Sheet updated: {added} new item(s) added, {skipped} already present.")


if __name__ == "__main__":
    main()
