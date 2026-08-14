#!/usr/bin/env python3
"""
asana_delete_with_safeguard.py — Safe Asana Task Deletion Tool

THIS IS THE ONLY PERMITTED WAY TO DELETE ASANA TASKS.

Without --confirmed: exports a CSV of tasks to be deleted and STOPS.
With --confirmed:    deletes the tasks and logs each deletion.

NEVER call the Asana DELETE endpoint directly — always use this script.
See workspace/DELETE_SAFETY.md for the full protocol.

Usage:
    # Step 1 — export CSV only (no deletion):
    python asana_delete_with_safeguard.py --gids 123456 789012 ...

    # Step 2 — delete after Giles has reviewed the CSV and confirmed:
    python asana_delete_with_safeguard.py --gids 123456 789012 ... --confirmed

    # Supply GIDs from a file (one per line):
    python asana_delete_with_safeguard.py --gid-file pending_gids.txt
"""

import sys
import os
import csv
import json
import argparse
import logging
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")

SCRIPT_DIR    = Path(__file__).parent.resolve()
WORKSPACE_DIR = SCRIPT_DIR.parents[2]         # scripts -> asana -> skills -> SES_Workspace

load_dotenv(Path.home() / ".hermes" / ".env")

ASANA_PAT = os.environ.get("ASANA_PERSONAL_ACCESS_TOKEN", "")
DB_DIR    = WORKSPACE_DIR / "db"
LOG_PATH  = DB_DIR / "delete_log.jsonl"

WORKSPACE_GID = "357939803556123"

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)


def _headers() -> dict:
    if not ASANA_PAT:
        log.error("ASANA_PERSONAL_ACCESS_TOKEN not set in .env")
        sys.exit(1)
    return {"Authorization": f"Bearer {ASANA_PAT}", "Accept": "application/json"}


def fetch_task_details(gids: list[str]) -> list[dict]:
    """Fetch full details for each GID. Returns list of task dicts."""
    hdrs  = _headers()
    tasks = []
    for gid in gids:
        try:
            r = requests.get(
                f"https://app.asana.com/api/1.0/tasks/{gid}",
                headers=hdrs,
                params={"opt_fields": "name,gid,projects.name,projects.gid,completed,due_on,notes,subtasks.gid,subtasks.name"},
                timeout=15,
            )
            r.raise_for_status()
            tasks.append(r.json().get("data", {}))
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                log.warning("Task %s not found (already deleted?)", gid)
            else:
                log.error("Error fetching task %s: %s", gid, e)
    return tasks


def build_task_url(task: dict) -> str:
    projects = task.get("projects", [])
    gid      = task.get("gid", "")
    pgid     = projects[0].get("gid", WORKSPACE_GID) if projects else WORKSPACE_GID
    return f"https://app.asana.com/0/{pgid}/{gid}"


def export_csv(tasks: list[dict], timestamp: str) -> Path:
    """Write pending-delete CSV. Returns the CSV path."""
    DB_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = DB_DIR / f"pending_deletes_{timestamp}.csv"

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "GID", "Task Name", "Projects", "Due On",
            "Completed", "Subtask Count", "Notes (first 200 chars)", "Asana URL"
        ])
        for t in tasks:
            projects    = ", ".join(p.get("name", "") for p in t.get("projects", []))
            subtasks    = t.get("subtasks", [])
            sub_count   = len(subtasks)
            sub_names   = "; ".join(s.get("name","") for s in subtasks[:5])
            sub_display = f"{sub_count} subtasks" + (f": {sub_names}" if sub_names else "")
            writer.writerow([
                t.get("gid", ""),
                t.get("name", ""),
                projects,
                t.get("due_on") or "",
                t.get("completed", False),
                sub_display,
                (t.get("notes") or "")[:200].replace("\n", " "),
                build_task_url(t),
            ])

    return csv_path


def delete_task(gid: str, task_name: str) -> bool:
    """Delete a single task. Returns True on success."""
    try:
        r = requests.delete(
            f"https://app.asana.com/api/1.0/tasks/{gid}",
            headers=_headers(),
            timeout=15,
        )
        r.raise_for_status()
        return True
    except requests.exceptions.HTTPError as e:
        log.error("Failed to delete %s (%s): %s", gid, task_name, e)
        return False


def log_deletion(gid: str, task_name: str, success: bool) -> None:
    DB_DIR.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "gid":       gid,
        "name":      task_name,
        "status":    "deleted" if success else "failed",
    }
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def print_task_table(tasks: list[dict]) -> None:
    print(f"\n{'GID':<20} {'Task Name':<55} {'Projects':<30}")
    print("-" * 110)
    for t in tasks:
        gid   = t.get("gid", "")
        name  = (t.get("name") or "")[:54]
        projs = ", ".join(p.get("name", "") for p in t.get("projects", []))[:29]
        subs  = len(t.get("subtasks", []))
        sub_s = f" [{subs} subtasks]" if subs else ""
        print(f"{gid:<20} {name:<55} {projs:<30}{sub_s}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Safe Asana task deletion tool.")
    parser.add_argument("--gids",      nargs="+", help="Task GIDs to delete")
    parser.add_argument("--gid-file",  help="File with one GID per line")
    parser.add_argument("--confirmed", action="store_true",
                        help="Actually delete. Omit to export CSV only.")
    args = parser.parse_args()

    # Collect GIDs
    gids: list[str] = []
    if args.gids:
        gids.extend(args.gids)
    if args.gid_file:
        fp = Path(args.gid_file)
        if not fp.exists():
            log.error("GID file not found: %s", fp)
            sys.exit(1)
        gids.extend(line.strip() for line in fp.read_text().splitlines() if line.strip())

    if not gids:
        log.error("No task GIDs supplied. Use --gids or --gid-file.")
        sys.exit(1)

    log.info("Fetching details for %d task(s) …", len(gids))
    tasks = fetch_task_details(gids)

    if not tasks:
        log.error("No valid tasks found for supplied GIDs.")
        sys.exit(1)

    # Count total including subtasks
    total_subtasks = sum(len(t.get("subtasks", [])) for t in tasks)

    print("\n" + "=" * 70)
    print("  ASANA TASK DELETION SAFEGUARD")
    print("=" * 70)
    print(f"  Tasks selected for deletion: {len(tasks)}")
    print(f"  Subtasks that will also be deleted: {total_subtasks}")
    print(f"  TOTAL items affected: {len(tasks) + total_subtasks}")
    print("=" * 70)
    print_task_table(tasks)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    csv_path  = export_csv(tasks, timestamp)
    print(f"\nCSV exported to:\n  {csv_path}\n")

    if not args.confirmed:
        print("=" * 70)
        print("  STOPPED — NO TASKS DELETED")
        print()
        print("  Review the CSV above, then re-run with --confirmed to delete.")
        print("  Giles must explicitly approve before running with --confirmed.")
        print("  See workspace/DELETE_SAFETY.md for the full protocol.")
        print("=" * 70 + "\n")
        sys.exit(0)

    # Confirmed — proceed with deletion
    print("=" * 70)
    print(f"  CONFIRMED — DELETING {len(tasks)} TASK(S)")
    print("=" * 70 + "\n")

    deleted = 0
    failed  = 0
    for t in tasks:
        gid  = t.get("gid", "")
        name = t.get("name", "")
        ok   = delete_task(gid, name)
        log_deletion(gid, name, ok)
        if ok:
            print(f"  ✓ Deleted: {name} ({gid})")
            deleted += 1
        else:
            print(f"  ✗ Failed:  {name} ({gid})")
            failed += 1

    print(f"\n  Done. Deleted: {deleted}  Failed: {failed}")
    print(f"  Deletion log: {LOG_PATH}\n")

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
