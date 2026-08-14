"""
execute_requests.py — Process PENDING REQUESTS/ files and execute Asana writes.

Reads all PENDING .md files in the root REQUESTS/ directory (not subfolders),
parses the companion .json, calls Asana API directly, marks files COMPLETE/FAILED.

Run: venv/Scripts/python.exe execute_requests.py
"""

import os
import json
import re
import glob
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

load_dotenv(r"C:\Users\GilesHugo\.hermes\.env", override=True)

import sys as _sys
_sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "lib")))
from path_config import p as _p

REQUESTS_DIR = _p("queues.requests")
REQUESTS_COMPLETE = _p("queues.requests_complete")
REQUESTS_FAILED   = _p("queues.requests_failed")


def _archive(md_path, dest_dir):
    """Move a processed request (and its companion .json) out of the live queue,
    so REQUESTS/ only ever holds genuinely pending work."""
    os.makedirs(dest_dir, exist_ok=True)
    for src in (md_path, md_path[:-3] + ".json"):
        if os.path.exists(src):
            try:
                os.replace(src, os.path.join(dest_dir, os.path.basename(src)))
            except OSError:
                pass

ASANA_TOKEN = os.getenv("ASANA_PERSONAL_ACCESS_TOKEN")
ASANA_HEADERS = {
    "Authorization": f"Bearer {ASANA_TOKEN}",
    "Content-Type": "application/json",
    "Accept": "application/json",
}

# Known custom field GIDs — fallback for old-format files without inline GIDs
PRICE_EX_VAT_GID    = os.getenv("ASANA_PRICE_EX_VAT_FIELD_GID",      "854330340727040")
ORDER_STATUS_GID    = os.getenv("ASANA_ORDER_STATUS_FIELD_GID",       "1143955334207040")
ORDER_CONFIRMED_GID = os.getenv("ASANA_ORDER_STATUS_CONFIRMED_GID",   "1143955334207043")

WORKSPACE_GID       = "357939803556123"
GILES_USER_GID      = "369446862164512"
GILES_PROFILE_URL   = "https://app.asana.com/1/357939803556123/profile/369446862164512"
GILES_MENTION_TAG   = f'<a data-asana-gid="{GILES_USER_GID}" data-asana-type="user">@Giles</a>'


def parse_actions(action_text):
    """Return list of structured ops from action text (handles old & new format)."""
    ops = []

    # Due date
    m = re.search(r"Update delivery date to (\d{4}-\d{2}-\d{2})", action_text)
    if m:
        ops.append({"type": "due_date", "value": m.group(1)})

    # Price Ex VAT — new format with inline GID
    m = re.search(r"Update custom field 'Price Ex VAT' \(field GID: (\d+)\) to ([\d.]+)", action_text)
    if m:
        ops.append({"type": "custom_field_number", "gid": m.group(1), "value": float(m.group(2))})
    else:
        # Old format without GID
        m = re.search(r"Update 'Price Ex VAT' to ([\d.]+)", action_text)
        if m:
            ops.append({"type": "custom_field_number", "gid": PRICE_EX_VAT_GID, "value": float(m.group(1))})

    # Note added to description
    m = re.search(r"Note added to description: ([^;]+)", action_text)
    if m:
        ops.append({"type": "append_notes", "text": m.group(1).strip()})

    # ORDER Status — new format with inline option GID
    m = re.search(
        r"Update ORDER Status custom field \(field GID: (\d+)\) to .+\(option GID: (\d+)\)",
        action_text,
    )
    if m:
        ops.append({"type": "custom_field_enum", "gid": m.group(1), "enum_gid": m.group(2)})
    else:
        # Old format: "Propose changing ORDER Status to 'On Order - Delivery Date Confirmed'"
        if "On Order - Delivery Date Confirmed" in action_text:
            ops.append({"type": "custom_field_enum", "gid": ORDER_STATUS_GID, "enum_gid": ORDER_CONFIRMED_GID})

    return ops


def asana_update_task(task_gid, due_date=None, custom_fields=None):
    body = {"data": {}}
    if due_date:
        body["data"]["due_on"] = due_date
    if custom_fields:
        body["data"]["custom_fields"] = custom_fields
    if not body["data"]:
        return True, "no task fields to update"
    r = requests.put(
        f"https://app.asana.com/api/1.0/tasks/{task_gid}",
        headers=ASANA_HEADERS,
        json=body,
        timeout=15,
    )
    if r.status_code in (200, 201):
        return True, "task fields updated"
    return False, f"HTTP {r.status_code}: {r.text[:300]}"


def asana_create_task(project_gid, name, description=""):
    # Try adding to project first
    body = {"data": {"name": name, "projects": [project_gid], "notes": description}}
    r = requests.post(
        "https://app.asana.com/api/1.0/tasks",
        headers=ASANA_HEADERS,
        json=body,
        timeout=15,
    )
    if r.status_code in (200, 201):
        gid = r.json().get("data", {}).get("gid", "")
        return True, f"task created: {gid}"
    # 400 "not a valid object" = project_gid is a user task list (My Tasks), not a project.
    # Fall back to workspace + assignee — task lands in Giles's My Tasks automatically.
    if r.status_code == 400:
        body2 = {
            "data": {
                "name": name,
                "workspace": WORKSPACE_GID,
                "assignee": GILES_USER_GID,
                "notes": description,
            }
        }
        r2 = requests.post(
            "https://app.asana.com/api/1.0/tasks",
            headers=ASANA_HEADERS,
            json=body2,
            timeout=15,
        )
        if r2.status_code in (200, 201):
            gid = r2.json().get("data", {}).get("gid", "")
            return True, f"task created (My Tasks): {gid}"
        return False, f"HTTP {r2.status_code}: {r2.text[:300]}"
    return False, f"HTTP {r.status_code}: {r.text[:300]}"


def asana_add_comment(task_gid, text):
    text_stripped = text.strip()

    # If already in Asana html_text format, post directly — no re-processing.
    if re.match(r"(?is)^<body>.*</body>$", text_stripped):
        payload = {"data": {"html_text": text_stripped}}
    elif re.search(r"@giles\b", text_stripped, flags=re.IGNORECASE) or GILES_PROFILE_URL in text_stripped:
        # Plain text with @Giles mention — convert to html_text with mention tag.
        html_body = text_stripped.replace(GILES_PROFILE_URL, GILES_MENTION_TAG)
        if GILES_PROFILE_URL not in text_stripped:
            html_body = re.sub(r"(?<![>])@giles\b", GILES_MENTION_TAG, html_body, flags=re.IGNORECASE)
        html_body = html_body.replace("\n", "<br>")
        payload = {"data": {"html_text": f"<body>{html_body}</body>"}}
    else:
        payload = {"data": {"text": text_stripped}}
    r = requests.post(
        f"https://app.asana.com/api/1.0/tasks/{task_gid}/stories",
        headers=ASANA_HEADERS,
        json=payload,
        timeout=15,
    )
    if r.status_code in (200, 201):
        return True, "comment posted"
    return False, f"HTTP {r.status_code}: {r.text[:300]}"


def asana_add_follower(task_gid, user_gid):
    r = requests.post(
        f"https://app.asana.com/api/1.0/tasks/{task_gid}/addFollowers",
        headers=ASANA_HEADERS,
        json={"data": {"followers": [user_gid]}},
        timeout=15,
    )
    if r.status_code in (200, 201):
        return True, "follower added"
    return False, f"HTTP {r.status_code}: {r.text[:200]}"


def asana_get_task_notes(task_gid):
    r = requests.get(
        f"https://app.asana.com/api/1.0/tasks/{task_gid}",
        headers=ASANA_HEADERS,
        params={"opt_fields": "notes"},
        timeout=15,
    )
    if r.status_code == 200:
        return r.json().get("data", {}).get("notes", "")
    return None


def asana_append_to_task_notes(task_gid, note_line):
    current = asana_get_task_notes(task_gid)
    if current is None:
        return False, "could not fetch task notes"
    separator = "\n" if current else ""
    updated = current + separator + note_line
    r = requests.put(
        f"https://app.asana.com/api/1.0/tasks/{task_gid}",
        headers=ASANA_HEADERS,
        json={"data": {"notes": updated}},
        timeout=15,
    )
    if r.status_code in (200, 201):
        return True, "note appended to description"
    return False, f"HTTP {r.status_code}: {r.text[:300]}"


def asana_multi_home_task(task_gid, project_gid, section_gid=None):
    """Add task to an additional project (multi-home). Optionally place in a section."""
    body = {"data": {"project": project_gid}}
    if section_gid:
        body["data"]["section"] = section_gid
    r = requests.post(
        f"https://app.asana.com/api/1.0/tasks/{task_gid}/addProject",
        headers=ASANA_HEADERS,
        json=body,
        timeout=15,
    )
    if r.status_code in (200, 201):
        return True, f"task multi-homed to project {project_gid}"
    return False, f"HTTP {r.status_code}: {r.text[:300]}"


def asana_update_task_title(task_gid, new_title):
    """Rename a task."""
    r = requests.put(
        f"https://app.asana.com/api/1.0/tasks/{task_gid}",
        headers=ASANA_HEADERS,
        json={"data": {"name": new_title}},
        timeout=15,
    )
    if r.status_code in (200, 201):
        return True, f"title updated to: {new_title}"
    return False, f"HTTP {r.status_code}: {r.text[:300]}"


def asana_reset_custom_field(task_gid, field_gid, value=0):
    """Set a number custom field to a specific value (default 0 to reset arrived qty)."""
    r = requests.put(
        f"https://app.asana.com/api/1.0/tasks/{task_gid}",
        headers=ASANA_HEADERS,
        json={"data": {"custom_fields": {field_gid: value}}},
        timeout=15,
    )
    if r.status_code in (200, 201):
        return True, f"field {field_gid} reset to {value}"
    return False, f"HTTP {r.status_code}: {r.text[:300]}"


def asana_create_task_in_project(project_gid, section_gid, task_name,
                                  description="", tag_gids=None, custom_fields=None):
    """Create task in project, place in section, apply tags and custom fields."""
    body = {
        "data": {
            "name": task_name,
            "projects": [project_gid],
            "notes": description,
        }
    }
    if tag_gids:
        body["data"]["tags"] = tag_gids
    if custom_fields:
        body["data"]["custom_fields"] = custom_fields

    r = requests.post(
        "https://app.asana.com/api/1.0/tasks",
        headers=ASANA_HEADERS,
        json=body,
        timeout=15,
    )
    if r.status_code not in (200, 201):
        return False, f"HTTP {r.status_code}: {r.text[:300]}", None

    new_gid = r.json().get("data", {}).get("gid", "")

    if section_gid and new_gid:
        rs = requests.post(
            f"https://app.asana.com/api/1.0/sections/{section_gid}/addTask",
            headers=ASANA_HEADERS,
            json={"data": {"task": new_gid}},
            timeout=15,
        )
        if rs.status_code not in (200, 201):
            return True, f"task created {new_gid} but section move failed: HTTP {rs.status_code}", new_gid

    return True, f"task created: {new_gid}", new_gid


def asana_append_to_description(task_gid, block):
    """Append a structured block to task description. Same as asana_append_to_task_notes
    but expects a multi-line block and preserves blank-line separation."""
    current = asana_get_task_notes(task_gid)
    if current is None:
        return False, "could not fetch task notes"
    separator = "\n\n" if current.strip() else ""
    updated = current + separator + block.strip()
    r = requests.put(
        f"https://app.asana.com/api/1.0/tasks/{task_gid}",
        headers=ASANA_HEADERS,
        json={"data": {"notes": updated}},
        timeout=15,
    )
    if r.status_code in (200, 201):
        return True, "description block appended"
    return False, f"HTTP {r.status_code}: {r.text[:300]}"


def stamp_md(md_path, status, note=""):
    with open(md_path, "r", encoding="utf-8") as f:
        content = f.read()
    replacement = f"Status: {status}"
    if note:
        replacement += f"\nCompleted: {datetime.now(timezone.utc).isoformat()}\nNote: {note}"
    content = re.sub(r"^Status: .*$", replacement, content, flags=re.MULTILINE)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(content)


def process_file(md_path):
    with open(md_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Read companion JSON
    json_path = md_path[:-3] + ".json"
    payload = {}
    if os.path.exists(json_path):
        with open(json_path, "r", encoding="utf-8") as f:
            payload = json.load(f)

    # Handle create_task requests — no Task GID exists (they have Project GID instead)
    action_type_m = re.search(r"^Action:\s*(.+)$", content, re.MULTILINE)
    action_type = action_type_m.group(1).strip().lower() if action_type_m else ""

    if action_type == "create_task":
        proj_m    = re.search(r"^Project GID:\s*(.+)$", content, re.MULTILINE)
        name_m    = re.search(r"^Task name:\s*(.+)$", content, re.MULTILINE)
        desc_m    = re.search(r"Description:\n(.*?)(?=\n## Context|\Z)", content, re.DOTALL)
        project_gid = proj_m.group(1).strip() if proj_m else ""
        task_name   = name_m.group(1).strip() if name_m else "Untitled Task"
        description = desc_m.group(1).strip() if desc_m else ""
        if not project_gid:
            stamp_md(md_path, "FAILED", "create_task: Project GID missing")
            return False, "create_task: Project GID missing"
        ok, msg = asana_create_task(project_gid, task_name, description)
        if ok:
            stamp_md(md_path, "COMPLETE", msg)
            return True, msg
        stamp_md(md_path, "FAILED", msg)
        return False, msg

    elif action_type == "multi_home_task":
        task_gid_m = re.search(r"^Task GID:\s*(.+)$", content, re.MULTILINE)
        proj_m     = re.search(r"^Project GID:\s*(.+)$", content, re.MULTILINE)
        sect_m     = re.search(r"^Section GID:\s*(.+)$", content, re.MULTILINE)
        task_gid   = (payload.get("task_gid") or (task_gid_m.group(1).strip() if task_gid_m else ""))
        project_gid = proj_m.group(1).strip() if proj_m else payload.get("project_gid", "")
        section_gid = sect_m.group(1).strip() if sect_m else payload.get("section_gid")
        if not task_gid or not project_gid:
            stamp_md(md_path, "FAILED", "multi_home_task: Task GID or Project GID missing")
            return False, "multi_home_task: Task GID or Project GID missing"
        ok, msg = asana_multi_home_task(task_gid, project_gid, section_gid)
        stamp_md(md_path, "COMPLETE" if ok else "FAILED", msg)
        return ok, msg

    elif action_type == "update_task_title":
        task_gid_m = re.search(r"^Task GID:\s*(.+)$", content, re.MULTILINE)
        title_m    = re.search(r"^New Title:\s*(.+)$", content, re.MULTILINE)
        task_gid   = (payload.get("task_gid") or (task_gid_m.group(1).strip() if task_gid_m else ""))
        new_title  = payload.get("new_title") or (title_m.group(1).strip() if title_m else "")
        if not task_gid or not new_title:
            stamp_md(md_path, "FAILED", "update_task_title: Task GID or New Title missing")
            return False, "update_task_title: Task GID or New Title missing"
        ok, msg = asana_update_task_title(task_gid, new_title)
        stamp_md(md_path, "COMPLETE" if ok else "FAILED", msg)
        return ok, msg

    elif action_type == "reset_custom_field":
        task_gid_m = re.search(r"^Task GID:\s*(.+)$", content, re.MULTILINE)
        field_m    = re.search(r"^Field GID:\s*(.+)$", content, re.MULTILINE)
        value_m    = re.search(r"^Value:\s*(.+)$", content, re.MULTILINE)
        task_gid   = (payload.get("task_gid") or (task_gid_m.group(1).strip() if task_gid_m else ""))
        field_gid  = payload.get("field_gid") or (field_m.group(1).strip() if field_m else "")
        try:
            value = float(value_m.group(1).strip()) if value_m else 0
        except (ValueError, AttributeError):
            value = 0
        if not task_gid or not field_gid:
            stamp_md(md_path, "FAILED", "reset_custom_field: Task GID or Field GID missing")
            return False, "reset_custom_field: Task GID or Field GID missing"
        ok, msg = asana_reset_custom_field(task_gid, field_gid, value)
        stamp_md(md_path, "COMPLETE" if ok else "FAILED", msg)
        return ok, msg

    elif action_type == "create_task_in_project":
        proj_m    = re.search(r"^Project GID:\s*(.+)$", content, re.MULTILINE)
        sect_m    = re.search(r"^Section GID:\s*(.+)$", content, re.MULTILINE)
        name_m    = re.search(r"^Task name:\s*(.+)$", content, re.MULTILINE)
        tags_m    = re.search(r"^Tag GIDs:\s*(.+)$", content, re.MULTILINE)
        cf_m      = re.search(r"^Custom Fields JSON:\s*(.+)$", content, re.MULTILINE)
        desc_m    = re.search(r"Description:\n(.*?)(?=\n## Context|\Z)", content, re.DOTALL)
        project_gid  = proj_m.group(1).strip() if proj_m else payload.get("project_gid", "")
        section_gid  = sect_m.group(1).strip() if sect_m else payload.get("section_gid")
        task_name    = name_m.group(1).strip() if name_m else payload.get("task_name", "Untitled")
        description  = desc_m.group(1).strip() if desc_m else payload.get("description", "")
        tag_gids_raw = tags_m.group(1).strip() if tags_m else payload.get("tag_gids", "")
        tag_gids = [t.strip() for t in tag_gids_raw.split(",") if t.strip()] if tag_gids_raw else []
        custom_fields = {}
        if cf_m:
            try:
                custom_fields = json.loads(cf_m.group(1).strip())
            except json.JSONDecodeError:
                pass
        if not project_gid:
            stamp_md(md_path, "FAILED", "create_task_in_project: Project GID missing")
            return False, "create_task_in_project: Project GID missing"
        ok, msg, new_gid = asana_create_task_in_project(
            project_gid, section_gid, task_name, description, tag_gids or None, custom_fields or None
        )
        stamp_md(md_path, "COMPLETE" if ok else "FAILED", msg)
        return ok, msg

    elif action_type == "append_to_description":
        task_gid_m = re.search(r"^Task GID:\s*(.+)$", content, re.MULTILINE)
        block_m    = re.search(r"Description:\n(.*?)(?=\n## Context|\Z)", content, re.DOTALL)
        task_gid   = (payload.get("task_gid") or (task_gid_m.group(1).strip() if task_gid_m else ""))
        block      = payload.get("description_block") or (block_m.group(1).strip() if block_m else "")
        if not task_gid or not block:
            stamp_md(md_path, "FAILED", "append_to_description: Task GID or block missing")
            return False, "append_to_description: Task GID or block missing"
        ok, msg = asana_append_to_description(task_gid, block)
        stamp_md(md_path, "COMPLETE" if ok else "FAILED", msg)
        return ok, msg

    elif action_type == "bom_pipeline_push":
        # Dispatched from the BOM tracker. Run bom_push to Stage 4 — NO commit,
        # NO Asana writes. bom_push posts the Stage 4 review to the topic itself.
        import subprocess
        here = os.path.dirname(__file__)
        venv_py  = os.path.join(here, "..", "..", ".venv", "Scripts", "python.exe")
        bom_push = os.path.join(here, "..", "bom_pipeline", "bom_push.py")
        # Persist the payload to a stable path (the REQUESTS .json gets archived).
        disp_dir = os.path.join(here, "..", "bom_pipeline", "dispatch")
        os.makedirs(disp_dir, exist_ok=True)
        stable = os.path.join(disp_dir, os.path.basename(md_path)[:-3] + ".json")
        with open(stable, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        try:
            r = subprocess.run([venv_py, bom_push, "--payload", stable],
                               capture_output=True, text=True, timeout=1500)
        except subprocess.TimeoutExpired:
            stamp_md(md_path, "FAILED", "bom_pipeline_push: timed out")
            return False, "bom_pipeline_push: timed out"
        if r.returncode == 0:
            stamp_md(md_path, "COMPLETE", "bom push ran to Stage 4")
            return True, "bom push -> Stage 4"
        tail = ((r.stdout or "")[-250:] + (r.stderr or "")[-250:]).replace("\n", " ")
        stamp_md(md_path, "FAILED", f"bom push rc={r.returncode}: {tail}")
        return False, f"bom push rc={r.returncode}"

    elif action_type == "run_drawing_pipeline":
        # Dispatched from the tracker 'Stage drawing' button. Runs to stage 3
        # (pack mapping in ops.db) — the Asana write (stage 4) stays SAP/manual.
        import subprocess
        job = str(payload.get("job") or "")
        if not job:
            jm = re.search(r"^Job:\s*(.+)$", content, re.MULTILINE)
            job = jm.group(1).strip() if jm else ""
        if not job:
            stamp_md(md_path, "FAILED", "run_drawing_pipeline: no job prefix")
            return False, "run_drawing_pipeline: no job prefix"
        here = os.path.dirname(__file__)
        venv_py = os.path.join(here, "..", "..", ".venv", "Scripts", "python.exe")
        runner  = os.path.join(here, "..", "drawing_pipeline", "scripts", "run_drawing_pipeline.py")
        try:
            r = subprocess.run([venv_py, runner, "--job-prefix", job, "--stop-after", "3"],
                               capture_output=True, text=True, timeout=1500)
        except subprocess.TimeoutExpired:
            stamp_md(md_path, "FAILED", "run_drawing_pipeline: timed out")
            return False, "run_drawing_pipeline: timed out"
        try:
            from notify_telegram import send_purchasing_topic
            send_purchasing_topic(
                f"🧭 Drawing pipeline for {job} ran to stage 3 (pack mapping, no Asana "
                f"writes). rc={r.returncode}. Review before the SAP write step.")
        except Exception:
            pass
        stamp_md(md_path, "COMPLETE" if r.returncode == 0 else "FAILED",
                 f"drawing pipeline rc={r.returncode}")
        return (r.returncode == 0), f"drawing pipeline rc={r.returncode}"

    # Task GID — prefer JSON, fall back to .md
    task_gid = (payload.get("task_gid") or "").strip()
    if not task_gid:
        m = re.search(r"^Task GID: (.+)$", content, re.MULTILINE)
        task_gid = m.group(1).strip() if m else ""

    if not task_gid or task_gid == "NOT_MATCHED":
        subject = payload.get("subject", "")
        stamp_md(md_path, "FAILED", f"Task GID not matched — manual resolution required. Subject: {subject}")
        return False, f"NOT_MATCHED — {subject}"

    # Action text
    action_text = payload.get("action", "")
    if not action_text:
        m = re.search(r"^Details:\n(.+?)(?=\n\n|\nComment To Push:|\n## Context)", content, re.MULTILINE | re.DOTALL)
        action_text = m.group(1).strip() if m else ""

    # Comment to push
    comment_text = payload.get("comment_to_push", "")
    if not comment_text:
        m = re.search(r"^Comment To Push:\n(.+?)(?=\n\n|\n## Context)", content, re.MULTILINE | re.DOTALL)
        comment_text = m.group(1).strip() if m else ""

    ops = parse_actions(action_text)
    results = []
    errors = []

    # Build task update call
    due_date = None
    custom_fields = {}
    for op in ops:
        if op["type"] == "due_date":
            due_date = op["value"]
        elif op["type"] == "custom_field_number":
            custom_fields[op["gid"]] = op["value"]
        elif op["type"] == "custom_field_enum":
            custom_fields[op["gid"]] = op["enum_gid"]

    if due_date or custom_fields:
        ok, msg = asana_update_task(task_gid, due_date=due_date, custom_fields=custom_fields or None)
        if ok:
            results.append(msg)
        else:
            errors.append(msg)

    # Append to task description (notes) — runs as separate GET+PUT
    for op in ops:
        if op["type"] == "append_notes":
            ok, msg = asana_append_to_task_notes(task_gid, op["text"])
            if ok:
                results.append(msg)
            else:
                errors.append(msg)

    # Post comment
    if comment_text:
        ok, msg = asana_add_comment(task_gid, comment_text)
        if ok:
            results.append(msg)
            asana_add_follower(task_gid, GILES_USER_GID)
        else:
            errors.append(msg)

    if not ops and not comment_text:
        results.append("no actionable items in request")

    if errors:
        stamp_md(md_path, "FAILED", "; ".join(errors))
        return False, "; ".join(errors)

    note = "; ".join(results)
    stamp_md(md_path, "COMPLETE", note)
    return True, note


def main():
    if not ASANA_TOKEN:
        print("ERROR: ASANA_PERSONAL_ACCESS_TOKEN not set in .env")
        return

    # Root dir only — ignore archive subfolders
    files = sorted(glob.glob(os.path.join(REQUESTS_DIR, "*.md")))

    completed = 0
    failed = 0

    for md_path in files:
        try:
            with open(md_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            print(f"  ERROR reading {os.path.basename(md_path)}: {e}")
            continue

        if "Status: PENDING" not in content:
            continue
        if "To: asana_agent" not in content:
            continue

        ok, msg = process_file(md_path)
        fname = os.path.basename(md_path)
        if ok:
            completed += 1
            print(f"  COMPLETE  {fname}: {msg}")
        else:
            failed += 1
            print(f"  FAILED    {fname}: {msg}")
        # Move out of the live queue so REQUESTS/ shows only pending work.
        _archive(md_path, REQUESTS_COMPLETE if ok else REQUESTS_FAILED)

    if completed or failed:
        print(f"\nTotal: {completed} completed, {failed} failed")
    else:
        print("No PENDING files found.")


if __name__ == "__main__":
    main()
