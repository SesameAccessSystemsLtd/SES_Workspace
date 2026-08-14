"""
legacy_bom_handover_watcher.py — Detect "Handover Legacy BOM" tasks and notify.

INTERIM SCOPE (Asana-only, no Dropbox): the autonomous heartbeat runs this. It
detects new handover tasks, records them, and pings Giles. It does NOT resolve
the Dropbox link or run the allocation — that happens in a Claude/Cowork session
(which holds the Dropbox connector) on Giles's go. See the design doc.

What it does each run:
  1. Search 1.4 Purchasing Dept for INCOMPLETE tasks assigned to Giles whose title
     starts "Handover Legacy BOM", skipping GIDs already in legacy_bom_handover_log.
  2. For each new one: parse the project token (title) and the Dropbox URL (notes),
     write a legacy_bom_handover_log row (status 'prepared').
  3. Notify Giles (Telegram) + optionally comment on the task, with the raw Dropbox
     URL and the next-step command. Runs NO ingest and touches NO Dropbox.

Idempotent: a task is notified once (deduped on task_gid).

Usage:
  python legacy_bom_handover_watcher.py --dry-run   # detect + print, no writes/notify
  python legacy_bom_handover_watcher.py             # detect + log + notify (+ comment)
  python legacy_bom_handover_watcher.py --no-comment # notify via Telegram only
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

_WORKSPACE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_WORKSPACE / "lib"))
sys.path.insert(0, str(_WORKSPACE / "skills" / "purchasing"))
sys.path.insert(0, str(_WORKSPACE / "skills" / "asana" / "scripts"))

try:
    from path_config import p as _p
    DEFAULT_DB_PATH = os.environ.get("OPENCLAW_OPS_DB_PATH") or _p("db.ops")
except Exception:
    _fallback = _WORKSPACE / "db" / "ops.db"
    DEFAULT_DB_PATH = os.environ.get("OPENCLAW_OPS_DB_PATH") or (
        str(_fallback) if _fallback.exists() else None
    )

BASE = "https://app.asana.com/api/1.0"
PURCHASING_DEPT_GID = "850066254549317"     # 1.4 Purchasing Dept
GILES_GID = "1142108324926610"
TITLE_PREFIX = "handover legacy bom"
DROPBOX_RE = re.compile(r'https?://(?:www\.)?dropbox\.com/\S+')


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── schema ───────────────────────────────────────────────────────────────────
def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS legacy_bom_handover_log (
            task_gid       TEXT PRIMARY KEY,
            project_token  TEXT,
            dropbox_url    TEXT,
            resolved_folder TEXT,
            status         TEXT NOT NULL DEFAULT 'prepared',
            prepared_at    TEXT,
            processed_at   TEXT,
            note           TEXT
        )"""
    )
    conn.commit()


# ── Asana token + fetch ──────────────────────────────────────────────────────
def get_token() -> str | None:
    import json
    token = os.environ.get("ASANA_PERSONAL_ACCESS_TOKEN") or os.environ.get("ASANA_PAT")
    if token:
        return token
    for path in [
        _WORKSPACE / "skills" / "asana" / "scripts" / "ASANAconfig.json",
        Path(r"C:\Users\GilesHugo\OneDrive - SESAME ACCESS AZURE\OneDrive Documents"
             r"\LAB. SESAME\Asana Scripts\ASANAconfig.json"),
    ]:
        try:
            cfg = json.loads(Path(path).read_text())
            token = cfg.get("ASANA_PERSONAL_ACCESS_TOKEN") or cfg.get("ASANA_PAT")
            if token:
                return token
        except (FileNotFoundError, ValueError, OSError):
            continue
    return None


def fetch_inbound_tasks(token: str) -> list[dict]:
    """Return inbound BOM work from 1.4 Purchasing Dept that needs Giles's action.

    Watches (assignee IGNORED — Karol often forgets to assign to Giles):
      * incomplete 'Handover Legacy BOM - {project}' tasks
      * incomplete 'Karol Generate Entire BOM - {project}' control tasks that have
        at least one OPEN (unticked) sub-BOM  -> the sub-BOMs need processing
    Also flags incomplete 'Issue PLC BOM' and 'BOM: {N}.' tasks for completeness.
    Paginates the Purchasing Dept project task list.
    """
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    fields = "name,completed,assignee.gid,notes,permalink_url"
    out, offset = [], None
    while True:
        params = {"opt_fields": fields, "limit": 100,
                  "completed_since": "now"}   # incomplete only
        if offset:
            params["offset"] = offset
        r = requests.get(f"{BASE}/projects/{PURCHASING_DEPT_GID}/tasks",
                         headers=headers, params=params, timeout=30)
        r.raise_for_status()
        body = r.json()
        for t in body.get("data", []):
            if t.get("completed"):
                continue
            name = (t.get("name") or "").strip()
            low = name.lower()
            if low.startswith("handover legacy bom"):
                out.append(t)
            elif low.startswith("karol generate entire bom"):
                # only surface if it has open (unticked) sub-BOMs needing processing
                subs = fetch_subtasks(token, t["gid"])
                open_subs = [s for s in subs if not s.get("completed")]
                t["_open_karol_subs"] = [s.get("name") or "" for s in open_subs]
                if open_subs:
                    out.append(t)
            elif low.startswith("issue plc bom") or re.match(r"^bom\s*:", low):
                out.append(t)
        offset = (body.get("next_page") or {}).get("offset")
        if not offset:
            break
    return out


def fetch_subtasks(token: str, task_gid: str) -> list[dict]:
    try:
        r = requests.get(f"{BASE}/tasks/{task_gid}/subtasks",
                         headers={"Authorization": f"Bearer {token}",
                                  "Accept": "application/json"},
                         params={"opt_fields": "name,gid,completed", "limit": 100},
                         timeout=30)
        r.raise_for_status()
        return r.json().get("data", [])
    except requests.RequestException:
        return []


def post_comment(token: str, task_gid: str, text: str) -> bool:
    try:
        r = requests.post(f"{BASE}/tasks/{task_gid}/stories",
                          headers={"Authorization": f"Bearer {token}"},
                          json={"data": {"text": text}}, timeout=30)
        return r.status_code in (200, 201)
    except requests.RequestException:
        return False


# ── parsing ──────────────────────────────────────────────────────────────────
def parse_project_token(name: str) -> str:
    # "Handover Legacy BOM - 1581 Admiralty House" -> "1581 Admiralty House"
    m = re.split(r'handover legacy bom\s*[-–]?\s*', name, flags=re.I, maxsplit=1)
    return (m[1].strip() if len(m) > 1 and m[1].strip() else name.strip())


def extract_dropbox_url(notes: str) -> str | None:
    m = DROPBOX_RE.search(notes or "")
    return m.group(0) if m else None


# ── notification text ────────────────────────────────────────────────────────
def build_notification(task: dict, project_token: str, dropbox_url: str | None) -> str:
    url_line = dropbox_url or "⚠ no Dropbox link found in task body"
    low = (task.get("name") or "").lower()
    if low.startswith("karol generate entire bom"):
        subs = task.get("_open_karol_subs") or []
        sub_txt = ("\n  · " + "\n  · ".join(subs)) if subs else ""
        return (
            f"🗂 Karol BOM release needs processing\n"
            f"Project: {project_token}\n"
            f"Task: {task.get('permalink_url', task['gid'])}\n"
            f"Open sub-BOMs:{sub_txt}\n"
            f"Dropbox (per sub-task): {url_line}\n\n"
            f"Next: run the BOM push (Karol Generate) via /run_bom_push or the "
            f"BOM pipeline to process the open sub-BOM(s)."
        )
    return (
        f"📦 New BOM handover ready\n"
        f"Project: {project_token}\n"
        f"Task: {task.get('permalink_url', task['gid'])}\n"
        f"Dropbox: {url_line}\n\n"
        f"Next: in a Cowork session, resolve the Dropbox link → local folder, then run\n"
        f"  legacy_bom_pack_ingest.py --parts-folder <folder> --scan\n"
        f"review, then --commit --stage-file <scan.json>."
    )


# ── main loop ────────────────────────────────────────────────────────────────
def run(conn: sqlite3.Connection, tasks: list[dict], *, dry_run: bool,
        token: str | None, do_comment: bool) -> dict:
    ensure_schema(conn)
    known = {r[0] for r in conn.execute("SELECT task_gid FROM legacy_bom_handover_log")}
    new_items = [t for t in tasks if t["gid"] not in known]
    results = []
    for t in new_items:
        pt = parse_project_token(t.get("name", ""))
        url = extract_dropbox_url(t.get("notes", ""))
        msg = build_notification(t, pt, url)
        results.append({"gid": t["gid"], "project_token": pt, "dropbox_url": url,
                        "name": t.get("name"), "message": msg})
        if dry_run:
            continue
        conn.execute(
            """INSERT OR IGNORE INTO legacy_bom_handover_log
               (task_gid, project_token, dropbox_url, status, prepared_at, note)
               VALUES (?, ?, ?, 'prepared', ?, ?)""",
            (t["gid"], pt, url, _now(),
             None if url else "no dropbox url in task body"),
        )
        conn.commit()
        # notify
        try:
            from notify_telegram import send_purchasing_topic
            send_purchasing_topic(msg)   # BOM releases post to the Purchasing topic
        except Exception as e:            # notifications must never crash detection
            print(f"  [WARN] telegram notify failed: {e}")
        if do_comment and token:
            post_comment(token, t["gid"],
                         "Detected by Legacy BOM handover watcher — queued for pack "
                         "allocation. " + (f"Dropbox: {url}" if url else
                                           "No Dropbox link found in this task."))
    return {"detected": len(tasks), "new": len(new_items), "items": results}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db-path", default=DEFAULT_DB_PATH)
    ap.add_argument("--dry-run", action="store_true",
                    help="Detect + print only; write nothing, notify nothing")
    ap.add_argument("--no-comment", action="store_true",
                    help="Notify via Telegram only; do not comment on the Asana task")
    args = ap.parse_args()

    if not args.db_path or not Path(args.db_path).is_file():
        raise SystemExit(f"ERROR: ops.db not found at {args.db_path!r}")

    token = get_token()
    if not token:
        raise SystemExit("ERROR: ASANA_PERSONAL_ACCESS_TOKEN not found.")
    tasks = fetch_inbound_tasks(token)

    conn = sqlite3.connect(args.db_path, timeout=30)
    try:
        res = run(conn, tasks, dry_run=args.dry_run, token=token,
                  do_comment=not args.no_comment)
    finally:
        conn.close()

    mode = "DRY RUN" if args.dry_run else "LIVE"
    print(f"[{mode}] handover tasks matched: {res['detected']} | new: {res['new']}")
    for it in res["items"]:
        print(f"  • {it['name']}  (gid {it['gid']})")
        print(f"      project_token: {it['project_token']}")
        print(f"      dropbox_url:   {it['dropbox_url'] or '— none —'}")


if __name__ == "__main__":
    main()
