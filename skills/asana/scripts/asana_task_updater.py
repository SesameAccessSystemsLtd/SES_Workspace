"""
asana_task_updater.py — SAP write tool for task updates and comments.

Subcommands:
  add_comment  --task-gid GID --text "..."        Post a comment (story) to a task
  set_due_date --task-gid GID --date YYYY-MM-DD   Set task due date
  update_field --task-gid GID --field-gid GID --value VALUE  Set a custom field (text/enum GID)

All writes are logged to ops.db via db_writer.py.

Exit codes: 0 = success, 1 = error
"""

import argparse
import os
import re
import sys

import requests
from dotenv import load_dotenv

load_dotenv(r"C:\Users\GilesHugo\.hermes\.env", override=True)

ASANA_TOKEN = os.getenv("ASANA_PERSONAL_ACCESS_TOKEN")
BASE_URL = "https://app.asana.com/api/1.0"
GILES_GID = "1142108324926610"

DB_WRITER = r"C:\Users\GilesHugo\SES_Workspace\skills\purchasing\db_writer.py"
sys.path.insert(0, r"C:\Users\GilesHugo\SES_Workspace\skills\purchasing")
import db_writer


def _headers():
    if not ASANA_TOKEN:
        print("ERROR: ASANA_PERSONAL_ACCESS_TOKEN not set in .env", file=sys.stderr)
        sys.exit(1)
    return {"Authorization": f"Bearer {ASANA_TOKEN}"}


def _log(task_gid, action_type, task_name="", value_applied="", result="ok", error_detail="", email_id=""):
    try:
        db_writer.log_asana_write({
            "task_gid": task_gid,
            "task_name": task_name,
            "email_id": email_id,
            "action_type": action_type,
            "field_name": "",
            "field_gid": "",
            "value_applied": str(value_applied),
            "result": result,
            "error_detail": str(error_detail),
        }, source="asana_task_updater")
    except Exception as e:
        print(f"WARNING: failed to log write to ops.db: {e}", file=sys.stderr)


def add_comment(task_gid: str, text: str, tag_giles: bool = True, email_id: str = ""):
    """Post a comment/story to an Asana task. Optionally @mention Giles."""
    if tag_giles:
        comment_body = text.strip()
        comment_body = re.sub(r"(?is)^<body>(.*)</body>$", r"\1", comment_body)
        html_text = (
            f'<body>{comment_body} '
            f'<a data-asana-type="user" data-asana-gid="{GILES_GID}">@Giles</a>'
            f'</body>'
        )
        payload = {"data": {"html_text": html_text}}
    else:
        payload = {"data": {"text": text}}

    r = requests.post(
        f"{BASE_URL}/tasks/{task_gid}/stories",
        headers=_headers(),
        json=payload,
    )
    if r.status_code in (200, 201):
        story_gid = r.json().get("data", {}).get("gid", "")
        print(f"OK: comment posted to task {task_gid} (story {story_gid})")
        _log(task_gid, "add_comment", value_applied=text[:120], result="ok", email_id=email_id)
        return True
    else:
        print(f"ERROR: {r.status_code} {r.text}", file=sys.stderr)
        _log(task_gid, "add_comment", value_applied=text[:120], result="error", error_detail=r.text[:200], email_id=email_id)
        return False


def set_due_date(task_gid: str, date_str: str, email_id: str = ""):
    """Set a task's due date. date_str must be YYYY-MM-DD."""
    r = requests.put(
        f"{BASE_URL}/tasks/{task_gid}",
        headers=_headers(),
        json={"data": {"due_on": date_str}},
    )
    if r.status_code == 200:
        print(f"OK: due date set to {date_str} on task {task_gid}")
        _log(task_gid, "set_due_date", value_applied=date_str, result="ok", email_id=email_id)
        return True
    else:
        print(f"ERROR: {r.status_code} {r.text}", file=sys.stderr)
        _log(task_gid, "set_due_date", value_applied=date_str, result="error", error_detail=r.text[:200], email_id=email_id)
        return False


def update_field(task_gid: str, field_gid: str, value, email_id: str = ""):
    """Set a custom field on a task. value can be a string (text) or GID (enum)."""
    # Detect enum vs text: if value looks like a long numeric GID, treat as enum
    if str(value).isdigit() and len(str(value)) > 10:
        field_payload = {"enum_value": str(value)}
    else:
        field_payload = {"text_value": str(value)}

    r = requests.put(
        f"{BASE_URL}/tasks/{task_gid}",
        headers=_headers(),
        json={"data": {"custom_fields": {field_gid: field_payload}}},
    )
    if r.status_code == 200:
        print(f"OK: field {field_gid} set to '{value}' on task {task_gid}")
        _log(task_gid, "update_custom_field", field_gid=field_gid, value_applied=str(value), result="ok", email_id=email_id)
        return True
    else:
        print(f"ERROR: {r.status_code} {r.text}", file=sys.stderr)
        _log(task_gid, "update_custom_field", field_gid=field_gid, value_applied=str(value), result="error", error_detail=r.text[:200], email_id=email_id)
        return False


def main():
    parser = argparse.ArgumentParser(description="Asana task write tool for SAP")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_comment = sub.add_parser("add_comment", help="Post a comment to a task")
    p_comment.add_argument("--task-gid", required=True)
    p_comment.add_argument("--text", required=True)
    p_comment.add_argument("--no-tag", action="store_true", help="Omit @Giles mention")
    p_comment.add_argument("--email-id", default="")

    p_date = sub.add_parser("set_due_date", help="Set task due date")
    p_date.add_argument("--task-gid", required=True)
    p_date.add_argument("--date", required=True, help="YYYY-MM-DD")
    p_date.add_argument("--email-id", default="")

    p_field = sub.add_parser("update_field", help="Set a custom field value")
    p_field.add_argument("--task-gid", required=True)
    p_field.add_argument("--field-gid", required=True)
    p_field.add_argument("--value", required=True)
    p_field.add_argument("--email-id", default="")

    args = parser.parse_args()

    if args.cmd == "add_comment":
        ok = add_comment(args.task_gid, args.text, tag_giles=not args.no_tag, email_id=args.email_id)
    elif args.cmd == "set_due_date":
        ok = set_due_date(args.task_gid, args.date, email_id=args.email_id)
    elif args.cmd == "update_field":
        ok = update_field(args.task_gid, args.field_gid, args.value, email_id=args.email_id)
    else:
        ok = False

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
