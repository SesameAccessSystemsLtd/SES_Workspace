"""
rename_placeholder.py — Replace [PROJECT NAME] placeholders in Asana task names.

Recursively scans all tasks and subtasks in a project (any depth).
Case-insensitive match on [PROJECT NAME] / [Project Name] / [project name].

Usage:
  # Dry run (default — safe, prints what would change):
  python rename_placeholder.py --project-gid <GID> --replacement "1581 Admiralty House"

  # Commit changes to Asana:
  python rename_placeholder.py --project-gid <GID> --replacement "1581 Admiralty House" --commit

  # Output JSON (for agent/script consumption):
  python rename_placeholder.py --project-gid <GID> --replacement "1581 Admiralty House" --json

Auth: reads ASANA_PERSONAL_ACCESS_TOKEN from environment, or falls back to ASANAconfig.json.

Changelog (2026-07-07):
  - Fixed: subtask fetches now paginate. Previously `/tasks/{gid}/subtasks` was
    called with no `limit` and `next_page` was never followed, so any branch
    with more direct subtasks than a single page (Asana default 50) silently
    lost everything past the first page.
  - Fixed: a single 404/inaccessible task hit mid-recursion (e.g. deleted or
    not-yet-visible while the scan is running) no longer aborts the entire
    scan. It's now skipped, logged to stderr, and reported under "skipped" in
    JSON output so you can see what was missed and why.
  - Not changed: placeholder matching is still bracket-only ("[Project Name]"
    style) — differently formatted placeholders (no brackets) are out of
    scope for now. Also not changed: when/how this script gets invoked — no
    built-in wait-for-project-to-finish-populating logic was added; that's
    being handled operationally (running this later, after Asana's project
    templating has finished creating all subtasks) rather than in code.
"""

import sys
import os
import json
import argparse
import re
import time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE = "https://app.asana.com/api/1.0"
PLACEHOLDER = re.compile(r"\[project name\]", re.IGNORECASE)
PAGE_LIMIT = 100


def get_token():
    token = os.environ.get("ASANA_PERSONAL_ACCESS_TOKEN") or os.environ.get("ASANA_PAT")
    if not token:
        for path in [
            os.path.join(os.path.dirname(__file__), "ASANAconfig.json"),
            r"C:\Users\GilesHugo\OneDrive - SESAME ACCESS AZURE\OneDrive Documents\LAB. SESAME\Asana Scripts\ASANAconfig.json",
        ]:
            try:
                with open(path) as f:
                    cfg = json.load(f)
                token = cfg.get("ASANA_PERSONAL_ACCESS_TOKEN") or cfg.get("ASANA_PAT")
                if token:
                    break
            except (FileNotFoundError, json.JSONDecodeError):
                continue
    if not token:
        print("ERROR: ASANA_PERSONAL_ACCESS_TOKEN not set", file=sys.stderr)
        sys.exit(1)
    return token


def make_session():
    """Persistent session with automatic retry on connection errors."""
    session = requests.Session()
    retry = Retry(
        total=5,
        backoff_factor=1.0,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "PUT"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.headers.update({
        "Authorization": f"Bearer {get_token()}",
        "Content-Type": "application/json",
    })
    return session


SESSION = None


def session():
    global SESSION
    if SESSION is None:
        SESSION = make_session()
    return SESSION


def api_get(path, params=None):
    r = session().get(f"{BASE}{path}", params=params or {})
    r.raise_for_status()
    return r.json().get("data", r.json())


def api_get_paginated(path, params=None):
    """GET a collection endpoint, following Asana's next_page cursor until exhausted.

    Used for both the top-level project task listing and each subtasks call —
    previously only the former paginated, so subtask branches beyond the
    first page were silently dropped.
    """
    params = dict(params or {})
    params.setdefault("limit", PAGE_LIMIT)
    results = []
    offset = None
    while True:
        page_params = dict(params)
        if offset:
            page_params["offset"] = offset
        r = session().get(f"{BASE}{path}", params=page_params)
        r.raise_for_status()
        body = r.json()
        results.extend(body.get("data", []))
        next_page = body.get("next_page")
        if not next_page:
            break
        offset = next_page.get("offset")
        if not offset:
            break
    return results


def api_put(path, payload):
    r = session().put(f"{BASE}{path}", json=payload)
    r.raise_for_status()
    return r.json().get("data", {})


def get_subtasks_recursive(task_gid, depth=0, skipped=None):
    """Return a flat list of all descendant tasks at any depth.

    If a task's subtasks can't be fetched (404 because it was deleted, access
    revoked, etc.) mid-recursion, that branch is skipped and recorded in
    `skipped` instead of raising and killing the whole scan.
    """
    if skipped is None:
        skipped = []
    try:
        subs = api_get_paginated(f"/tasks/{task_gid}/subtasks", {"opt_fields": "name,gid"})
    except requests.HTTPError as e:
        skipped.append({"gid": task_gid, "depth": depth, "error": str(e)})
        print(f"WARNING: could not fetch subtasks of {task_gid}: {e}", file=sys.stderr)
        return []

    results = []
    for s in subs:
        results.append({"gid": s["gid"], "name": s["name"], "depth": depth})
        results.extend(get_subtasks_recursive(s["gid"], depth + 1, skipped))
    return results


def scan_project(project_gid, skipped=None):
    """Return flat list of every task and subtask in the project."""
    if skipped is None:
        skipped = []
    top_tasks = api_get_paginated("/tasks", {"project": project_gid, "opt_fields": "name,gid"})

    all_tasks = []
    for t in top_tasks:
        all_tasks.append({"gid": t["gid"], "name": t["name"], "depth": 0})
        all_tasks.extend(get_subtasks_recursive(t["gid"], depth=1, skipped=skipped))
    return all_tasks


def rename_task(task_gid, new_name):
    """PUT the new task name to Asana."""
    return api_put(f"/tasks/{task_gid}", {"data": {"name": new_name}})


def main():
    parser = argparse.ArgumentParser(description="Replace [PROJECT NAME] placeholders in Asana task names")
    parser.add_argument("--project-gid", required=True, help="Asana project GID to scan")
    parser.add_argument("--replacement", required=True, help="Text to replace [PROJECT NAME] with, e.g. '1581 Admiralty House'")
    parser.add_argument("--commit", action="store_true", help="Actually rename tasks (default is dry-run)")
    parser.add_argument("--json", dest="output_json", action="store_true", help="Output results as JSON")
    args = parser.parse_args()

    if not args.output_json:
        print(f"Scanning project {args.project_gid} (recursive, all depths)...")

    skipped = []
    try:
        all_tasks = scan_project(args.project_gid, skipped=skipped)
    except requests.HTTPError as e:
        print(json.dumps({"error": str(e), "status": e.response.status_code}), file=sys.stderr)
        sys.exit(1)

    matched = [t for t in all_tasks if PLACEHOLDER.search(t["name"])]

    if not args.output_json:
        print(f"Total tasks scanned: {len(all_tasks)}")
        print(f"Tasks with [PROJECT NAME]: {len(matched)}")
        if skipped:
            print(f"Tasks skipped due to fetch errors: {len(skipped)}")
        print()

    results = []
    errors = []

    for t in matched:
        new_name = PLACEHOLDER.sub(args.replacement, t["name"])
        entry = {
            "gid": t["gid"],
            "depth": t["depth"],
            "old_name": t["name"],
            "new_name": new_name,
            "status": "dry_run",
        }

        if args.commit:
            try:
                rename_task(t["gid"], new_name)
                entry["status"] = "renamed"
                time.sleep(0.05)  # mild rate-limit courtesy
            except requests.HTTPError as e:
                entry["status"] = "error"
                entry["error"] = str(e)
                errors.append(entry)

        results.append(entry)

        if not args.output_json:
            status_label = f"[{entry['status'].upper()}]"
            print(f"  {status_label} depth={t['depth']} GID={t['gid']}")
            print(f"    OLD: {t['name']}")
            print(f"    NEW: {new_name}")
            print()

    if args.output_json:
        print(json.dumps({
            "project_gid": args.project_gid,
            "replacement": args.replacement,
            "committed": args.commit,
            "total_scanned": len(all_tasks),
            "total_matched": len(matched),
            "results": results,
            "errors": errors,
            "skipped": skipped,
        }, indent=2))
    else:
        if args.commit:
            renamed = sum(1 for r in results if r["status"] == "renamed")
            failed = len(errors)
            print(f"Done. Renamed: {renamed}  Errors: {failed}")
            if errors:
                print("Errors:")
                for e in errors:
                    print(f"  GID {e['gid']}: {e['error']}")
            if skipped:
                print("Skipped (fetch errors, not renamed):")
                for s in skipped:
                    print(f"  GID {s['gid']} depth={s['depth']}: {s['error']}")
        else:
            print(f"DRY RUN complete. {len(matched)} tasks would be renamed. Re-run with --commit to apply.")


if __name__ == "__main__":
    main()
