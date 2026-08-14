"""
01_email_fetcher.py -- Purchasing Email Fetcher (multi-folder)

Scans multiple mailbox folders for supplier emails.
Uses processed_email_ids.json to skip anything already run through the pipeline.
Fully idempotent -- safe to run as many times as needed.

Folders scanned:
  - Inbox (Focused)
  - 1.3 Confirmations
  - INVOICES TO PROCESS
  - Archive

Internal emails (sesameaccess.com / sesame-access.com) are skipped.
PDF attachments are extracted and appended to email text.
"""

import os
import json
import sys
import traceback
from datetime import datetime, timezone

import requests
from pathlib import Path
from dotenv import load_dotenv
import msal

try:
    import sys
    sys.path.insert(0, r'C:\Users\GilesHugo\SES_Workspace\skills\pdf_extractor')
    from pdf_extractor import PDFExtractorSkill
    pdf_extractor_skill = PDFExtractorSkill()
except ImportError:
    print("Warning: pdf_extractor not found -- PDF attachments will not be processed.")
    pdf_extractor_skill = None

load_dotenv(Path(__file__).parents[3] / ".hermes" / ".env", override=True)  # .env wins over stale gateway env

TENANT_ID     = os.getenv("Purchasing_tenant_ID")
CLIENT_ID     = os.getenv("Purchaisng_secret_ID")
CLIENT_SECRET = os.getenv("PURCHASING_API_KEY") or os.getenv("purchasing_Email_Api_Key")
MAILBOX       = "purchasing@sesameaccess.com"

INTERNAL_DOMAINS = {"sesameaccess.com", "sesame-access.com"}

# Folders to scan. "inbox-focused" is handled specially.
EXTRA_FOLDERS = ["1.3 Confirmations", "INVOICES TO PROCESS", "Archive"]

# Lookback for named folders (hours). Inbox uses unread filter instead.
FOLDER_LOOKBACK_HOURS = 24

OUTPUT_PATH      = r"C:\Users\GilesHugo\SES_Workspace\data\raw_purchasing_emails.json"
PROCESSED_IDS_PATH = r"C:\Users\GilesHugo\SES_Workspace\data\processed_email_ids.json"
# Cron notifications truncate stderr well before a traceback is visible, which
# hid the 2026-07-10 outage for 11 days. Every fatal error is mirrored here.
ERROR_LOG_PATH   = r"C:\Users\GilesHugo\SES_Workspace\data\email_fetcher_last_error.json"

# Exit codes let the pipeline distinguish "credentials expired" (needs a human)
# from "transient upstream error" (retry next hour).
EXIT_AUTH_FAILURE = 2
EXIT_FETCH_FAILURE = 3


def _record_fatal(stage: str, exc: BaseException, hint: str = "") -> None:
    """Persist a fatal error so a truncated cron message can never hide it."""
    payload = {
        "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stage": stage,
        "error_type": type(exc).__name__,
        "error": str(exc),
        "hint": hint,
        "traceback": traceback.format_exc(),
    }
    try:
        os.makedirs(os.path.dirname(ERROR_LOG_PATH), exist_ok=True)
        with open(ERROR_LOG_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
    except Exception as write_err:  # never mask the original failure
        print(f"  (could not write error log: {write_err})")
    # Printed first so it survives aggressive stderr truncation.
    print(f"FATAL [{stage}]: {type(exc).__name__}: {exc}")
    if hint:
        print(f"HINT: {hint}")
    print(f"Full traceback written to {ERROR_LOG_PATH}")


def _clear_error_log() -> None:
    """Remove a stale error file once a run succeeds."""
    try:
        if os.path.exists(ERROR_LOG_PATH):
            os.remove(ERROR_LOG_PATH)
    except OSError:
        pass

# Built lazily. Constructing this at module scope performed Azure tenant
# discovery at IMPORT time, so any network/credential problem raised a raw
# traceback before main() could run its checks or write an error log — and a
# missing TENANT_ID produced a baffling ".../None/v2.0/..." URL rather than
# naming the absent variable.
_app = None


def _get_app():
    global _app
    if _app is None:
        _app = msal.ConfidentialClientApplication(
            CLIENT_ID,
            authority=f"https://login.microsoftonline.com/{TENANT_ID}",
            client_credential=CLIENT_SECRET,
        )
    return _app


def get_access_token():
    result = _get_app().acquire_token_for_client(
        scopes=["https://graph.microsoft.com/.default"]
    )
    if "access_token" in result:
        return result["access_token"]
    raise Exception(f"Token error: {result.get('error_description', 'No description')}")


def load_processed_ids():
    if not os.path.exists(PROCESSED_IDS_PATH):
        return set(), set()
    with open(PROCESSED_IDS_PATH, 'r') as f:
        ids = set(json.load(f))
    # Also load internet message IDs for cross-folder dedup
    internet_ids_path = PROCESSED_IDS_PATH.replace('.json', '_internet_ids.json')
    internet_ids = set()
    if os.path.exists(internet_ids_path):
        with open(internet_ids_path, 'r') as f:
            internet_ids = set(json.load(f))
    return ids, internet_ids


def save_processed_ids(ids: set, internet_ids: set = None):
    os.makedirs(os.path.dirname(PROCESSED_IDS_PATH), exist_ok=True)
    with open(PROCESSED_IDS_PATH, 'w') as f:
        json.dump(list(ids), f, indent=2)
    if internet_ids is not None:
        internet_ids_path = PROCESSED_IDS_PATH.replace('.json', '_internet_ids.json')
        with open(internet_ids_path, 'w') as f:
            json.dump(list(internet_ids), f, indent=2)


def get_folder_id(token, folder_name):
    """Resolve a folder display name to its Graph API folder ID."""
    headers = {"Authorization": f"Bearer {token}"}
    url = f"https://graph.microsoft.com/v1.0/users/{MAILBOX}/mailFolders"
    params = {"$top": 100}
    resp = requests.get(url, headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    folders = resp.json().get("value", [])
    for folder in folders:
        if folder.get("displayName", "").lower() == folder_name.lower():
            return folder["id"]
    # Try child folders one level deep
    for folder in folders:
        child_url = f"https://graph.microsoft.com/v1.0/users/{MAILBOX}/mailFolders/{folder['id']}/childFolders"
        try:
            cr = requests.get(child_url, headers=headers, params={"$top": 100}, timeout=30)
            cr.raise_for_status()
            for child in cr.json().get("value", []):
                if child.get("displayName", "").lower() == folder_name.lower():
                    return child["id"]
        except Exception:
            continue
    print(f"  Warning: folder '{folder_name}' not found -- skipping.")
    return None


def fetch_from_url(url, headers):
    """Fetch all pages from a Graph messages URL."""
    messages = []
    while url:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        messages.extend(data.get("value", []))
        url = data.get("@odata.nextLink")
    return messages


def fetch_inbox_focused(token):
    """Fetch unread inbox emails."""
    headers = {"Authorization": f"Bearer {token}"}
    url = (
        f"https://graph.microsoft.com/v1.0/users/{MAILBOX}/mailFolders/inbox/messages"
        f"?$filter=isRead eq false"
        f"&$expand=attachments&$top=50"
    )
    msgs = fetch_from_url(url, headers)
    print(f"  Inbox (unread): {len(msgs)} email(s)")
    return msgs


def fetch_from_folder(token, folder_name, folder_id):
    """Fetch recent emails from a named folder (last FOLDER_LOOKBACK_HOURS hours)."""
    from datetime import datetime, timedelta, timezone
    since = (datetime.now(timezone.utc) - timedelta(hours=FOLDER_LOOKBACK_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    headers = {"Authorization": f"Bearer {token}"}
    url = (
        f"https://graph.microsoft.com/v1.0/users/{MAILBOX}"
        f"/mailFolders/{folder_id}/messages"
        f"?$filter=receivedDateTime ge {since}"
        f"&$expand=attachments&$top=50"
    )
    msgs = fetch_from_url(url, headers)
    print(f"  Folder '{folder_name}': {len(msgs)} email(s) in last {FOLDER_LOOKBACK_HOURS}h")
    return msgs


def extract_email_data(email, token):
    """Parse a Graph message object into our pipeline format."""
    email_id   = email["id"]
    subject    = email.get("subject", "No Subject")
    sender     = email["sender"]["emailAddress"]["address"]
    received   = email["receivedDateTime"]
    body       = email.get("body", {}).get("content", "")
    internet_message_id = email.get("internetMessageId", email_id)  # stable across folders

    email_data = {
        "id":                   email_id,
        "internetMessageId":    internet_message_id,
        "subject":              subject,
        "sender":               sender,
        "date":                 received,
        "body_content":         body,
        "full_text":            body,
        "pdf_text":             "",
    }

    pdf_texts = []
    for attachment in email.get("attachments", []):
        if attachment.get("name", "").lower().endswith(".pdf") and pdf_extractor_skill:
            print(f"    Downloading PDF: {attachment['name']}")
            try:
                att_url = (
                    f"https://graph.microsoft.com/v1.0/users/{MAILBOX}"
                    f"/messages/{email_id}/attachments/{attachment['id']}/$value"
                )
                r = requests.get(att_url, headers={"Authorization": f"Bearer {token}"}, timeout=60)
                r.raise_for_status()
                result = pdf_extractor_skill.extract_from_bytes(r.content)
                pdf_texts.append(result.get("raw_text", ""))
            except Exception as e:
                print(f"    PDF error: {e}")

    if pdf_texts:
        email_data["pdf_text"] = "\n".join(pdf_texts)
        email_data["full_text"] += "\n\n--- PDF Attachments ---\n\n" + email_data["pdf_text"]

    return email_data


def mark_as_read(token, email_id):
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    url = f"https://graph.microsoft.com/v1.0/users/{MAILBOX}/messages/{email_id}"
    try:
        requests.patch(url, headers=headers, json={"isRead": True}, timeout=30)
    except Exception as e:
        print(f"  Warning: could not mark {email_id[:20]}... as read: {e}")


def main():
    # Unguarded before: an expired Azure client secret killed the run with a
    # bare traceback that the cron notification truncated away.
    missing = [n for n, v in (("Purchasing_tenant_ID", TENANT_ID),
                              ("Purchaisng_secret_ID", CLIENT_ID),
                              ("purchasing_Email_Api_Key", CLIENT_SECRET)) if not v]
    if missing:
        err = RuntimeError(f"Missing credential env var(s): {', '.join(missing)}")
        _record_fatal("config", err,
                      hint=r"Expected in C:\Users\GilesHugo\.hermes\.env. "
                           r"Note the names are case-mangled and 'Purchaisng' is misspelled "
                           r"in both .env and this script — they must match exactly on Linux/VPS.")
        sys.exit(EXIT_AUTH_FAILURE)

    try:
        token = get_access_token()
    except Exception as exc:
        _record_fatal("authentication", exc,
                      hint="Azure AD app credentials rejected. Most likely the client "
                           "secret (purchasing_Email_Api_Key) has expired — Azure secrets "
                           "expire on a fixed schedule. Regenerate in Azure Portal > App "
                           "registrations > Certificates & secrets, then update .env.")
        sys.exit(EXIT_AUTH_FAILURE)
    print("Access token obtained.")

    processed_ids, processed_internet_ids = load_processed_ids()
    print(f"Loaded {len(processed_ids)} previously processed email ID(s) + {len(processed_internet_ids)} internet IDs.")

    # Collect all messages from all sources
    all_messages = []

    # 1. Focused inbox (unread)
    try:
        inbox_msgs = fetch_inbox_focused(token)
        all_messages.extend(("inbox", m) for m in inbox_msgs)
    except Exception as e:
        print(f"  Inbox fetch error: {e}")

    # 2. Named folders
    # get_folder_id() calls raise_for_status() internally; before this guard it
    # was the one unwrapped call in main(), so any 401/403/429 while resolving a
    # folder aborted the entire run before a single state file was written.
    folder_failures = []
    for folder_name in EXTRA_FOLDERS:
        try:
            folder_id = get_folder_id(token, folder_name)
        except Exception as e:
            print(f"  Folder '{folder_name}' resolution error: {e}")
            folder_failures.append(folder_name)
            continue
        if not folder_id:
            continue
        try:
            folder_msgs = fetch_from_folder(token, folder_name, folder_id)
            all_messages.extend((folder_name, m) for m in folder_msgs)
        except Exception as e:
            print(f"  Folder '{folder_name}' fetch error: {e}")
            folder_failures.append(folder_name)

    if folder_failures and not all_messages:
        # Nothing reachable anywhere — treat as a real outage rather than
        # writing an empty result set that looks like "no new mail".
        err = RuntimeError(
            f"No mail retrieved and {len(folder_failures)} folder(s) failed: "
            f"{', '.join(folder_failures)}"
        )
        _record_fatal("fetch", err,
                      hint="Inbox returned nothing AND folder access failed. Check the "
                           "app registration still has Mail.Read on the purchasing mailbox.")
        sys.exit(EXIT_FETCH_FAILURE)

    print(f"\nTotal emails fetched across all sources: {len(all_messages)}")

    fetched_emails_data = []
    new_processed_ids = set(processed_ids)
    new_internet_ids = set(processed_internet_ids)

    for source, email in all_messages:
        email_id = email["id"]
        internet_id = email.get("internetMessageId", email_id)

        # Skip already processed (check both Exchange ID and internetMessageId)
        if email_id in processed_ids or internet_id in processed_internet_ids:
            continue

        sender = email.get("sender", {}).get("emailAddress", {}).get("address", "")
        sender_domain = sender.split("@")[-1].lower() if "@" in sender else ""

        # Skip internal emails
        if sender_domain in INTERNAL_DOMAINS:
            if source == "inbox":
                mark_as_read(token, email_id)
            new_processed_ids.add(email_id)
            continue

        # Only print subject, not the email ID (noise in cron output)
        safe_subj = email.get('subject', '')[:60].encode('ascii', 'ignore').decode()
        print(f"  [{source}] {safe_subj}")
        email_data = extract_email_data(email, token)
        fetched_emails_data.append(email_data)

        # Mark inbox emails as read; leave folder emails untouched
        if source == "inbox":
            mark_as_read(token, email_id)

        new_processed_ids.add(email_id)
        new_internet_ids.add(internet_id)

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(fetched_emails_data, f, indent=4)

    save_processed_ids(new_processed_ids, internet_ids=new_internet_ids)

    _clear_error_log()
    print(f"\nDone: {len(fetched_emails_data)} email(s) to process. "
          f"Saved to raw_purchasing_emails.json")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:
        # Last-resort net: nothing escapes without leaving a readable record.
        _record_fatal("unhandled", exc)
        sys.exit(1)
