"""
01_populate_assembly_packs.py
Fetches assembly pack tasks from Asana for a given job prefix and upserts
them into ops.db build_assembly_packs table.

For each task in Build Schedule project (GID: 852956628490855) matching --job-prefix:
  1. Fetches its parent task (the drawing task in Drawing Control)
  2. Extracts pack_reference from the parent drawing task name
  3. Upserts into build_assembly_packs

--task-gid mode (targeted recovery):
  Accepts a single Asana task GID (or the GID extracted from an Asana URL).
  Works regardless of which project the task belongs to — Drawing Control,
  Build Schedule, or both (multi-homed). Use this when a drawing task was
  added to Asana AFTER the pipeline's last full run.

  Project disambiguation:
  - Build Schedule (852956628490855): task is the assembly pack task directly
  - Drawing Control (1142754335688166): task is the drawing task; its GID is
    used as the pack source (Stage 2 fetches attachments from this GID)
  - Multi-homed in both: treated as Build Schedule (preferred)
  - Neither: logged as warning but still upserted using the provided GID

Usage:
  python 01_populate_assembly_packs.py --job-prefix 1575 [--dry-run]
  python 01_populate_assembly_packs.py --job-prefix 1670 --task-gid 1215575033042714 [--dry-run]

Output:
  data/{job_prefix}_missing_packs.csv  — parts_control rows whose pack prefix has
                                         no matching entry in build_assembly_packs
                                         (written after every run, even if empty)
"""

import argparse
import csv
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(r"C:\Users\GilesHugo\.hermes\.env", override=True)

ASANA_TOKEN            = os.getenv("ASANA_PERSONAL_ACCESS_TOKEN")
HEADERS                = {"Authorization": f"Bearer {ASANA_TOKEN}", "Accept": "application/json"}
BASE                   = "https://app.asana.com/api/1.0"
DRAWING_PROJECT_GID    = "1142754335688166"
BUILD_SCHEDULE_GID     = "852956628490855"
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))
from path_config import p as _p
DB_PATH                = _p("db.ops")
SKILL_DIR              = Path(__file__).parent.parent
DATA_DIR               = SKILL_DIR / "data"


def asana_get(path, params=None):
    r = requests.get(f"{BASE}{path}", headers=HEADERS, params=params or {})
    r.raise_for_status()
    return r.json().get("data", [])


# ---------------------------------------------------------------------------
# Project-type disambiguation
# ---------------------------------------------------------------------------

PROJECT_LABELS = {
    BUILD_SCHEDULE_GID:  "Build Schedule",
    DRAWING_PROJECT_GID: "Drawing Control",
}


def fetch_task_with_memberships(task_gid: str) -> dict:
    """Fetch a single task including its project memberships."""
    r = requests.get(
        f"{BASE}/tasks/{task_gid}",
        headers=HEADERS,
        params={"opt_fields": "name,gid,completed,memberships.project.gid,memberships.project.name"},
    )
    r.raise_for_status()
    return r.json().get("data", {})


def classify_task_project(task: dict) -> str:
    """
    Return which known project(s) this task belongs to.
    Priority: build_schedule > drawing_control > other.
    Returns one of: "build_schedule", "drawing_control", "both", "other"
    """
    memberships = task.get("memberships", [])
    project_gids = {m["project"]["gid"] for m in memberships}

    in_bs  = BUILD_SCHEDULE_GID  in project_gids
    in_dc  = DRAWING_PROJECT_GID in project_gids

    if in_bs and in_dc:
        return "both"
    if in_bs:
        return "build_schedule"
    if in_dc:
        return "drawing_control"
    return "other"


# ---------------------------------------------------------------------------
# Build Schedule scan helpers
# ---------------------------------------------------------------------------

def get_build_schedule_tasks(job_prefix: str) -> list[dict]:
    """Return all Build Schedule tasks matching the job prefix."""
    params = {
        "project": BUILD_SCHEDULE_GID,
        "opt_fields": "name,gid,completed",
        "limit": 100,
    }
    tasks, offset = [], None
    while True:
        if offset:
            params["offset"] = offset
        r = requests.get(f"{BASE}/tasks", headers=HEADERS, params=params)
        r.raise_for_status()
        body = r.json()
        tasks.extend(body.get("data", []))
        nxt = body.get("next_page")
        if not nxt:
            break
        offset = nxt.get("offset")

    matched = [t for t in tasks if t.get("name", "").startswith(job_prefix)]
    standard, skipped = [], []
    for t in matched:
        parts = t["name"].rstrip(".").split(".")
        # Require at least 3 components and second component must be numeric (pack number)
        if len(parts) >= 3 and parts[1].isdigit():
            standard.append(t)
        else:
            skipped.append(t)
    if skipped:
        print(f"  Skipping {len(skipped)} non-standard task(s):")
        for t in skipped:
            print(f"    SKIP: {t['name']} [{t['gid']}]")
    return standard


def extract_pack_reference(task_name: str) -> str:
    """
    Extract the pack reference from a task name.
    e.g. "1575.6000.A.Stairs_Rev.1.DRAWING" -> "1575.6000.A"
         "1670.1055.A.Hand Held"              -> "1670.1055.A"
    Strategy: take the first 3 dot-separated components.
    """
    name = task_name.rstrip(".")
    parts = name.split(".")
    if len(parts) >= 3:
        return ".".join(parts[:3])
    return name


# ---------------------------------------------------------------------------
# DB upsert
# ---------------------------------------------------------------------------

def upsert_pack(conn: sqlite3.Connection, pack: dict, dry_run: bool) -> str:
    """Upsert one row into build_assembly_packs. Returns 'inserted'/'updated'/'skipped'."""
    cur = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()

    cur.execute("SELECT id, pack_reference FROM build_assembly_packs WHERE asana_task_gid = ?",
                (pack["asana_task_gid"],))
    existing_by_gid = cur.fetchone()

    if existing_by_gid:
        if existing_by_gid[1] == pack["pack_reference"]:
            return "skipped"
        if not dry_run:
            cur.execute("""
                UPDATE build_assembly_packs
                SET pack_reference=?, pack_name=?, last_synced_at=?
                WHERE asana_task_gid=?
            """, (pack["pack_reference"], pack["pack_name"], now, pack["asana_task_gid"]))
            conn.commit()
        return "updated"

    if not dry_run:
        cur.execute("""
            INSERT INTO build_assembly_packs
                (asana_task_gid, pack_reference, pack_name, last_synced_at, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (pack["asana_task_gid"], pack["pack_reference"], pack["pack_name"], now, now))
        conn.commit()
    return "inserted"


# ---------------------------------------------------------------------------
# Missing packs report
# ---------------------------------------------------------------------------

def write_missing_packs_report(conn: sqlite3.Connection, job_prefix: str) -> int:
    """
    Compare parts_control pack prefixes against build_assembly_packs.
    Write data/{job}_missing_packs.csv with any parts whose pack has no
    registered assembly pack task. Returns count of missing pack prefixes.
    """
    cur = conn.cursor()

    # Get all known pack_references for this job
    cur.execute(
        "SELECT pack_reference FROM build_assembly_packs WHERE pack_reference LIKE ?",
        (f"{job_prefix}.%",)
    )
    known_packs = {r[0] for r in cur.fetchall()}

    # Get all parts_control rows for this job
    cur.execute(
        "SELECT sesame_pn FROM parts_control WHERE sesame_pn LIKE ?",
        (f"{job_prefix}.%",)
    )
    pc_pns = [r[0] for r in cur.fetchall()]

    # Derive pack prefix (first 3 dot components) for each part
    missing_rows = []
    seen_missing_prefixes = set()
    for pn in pc_pns:
        parts = pn.split(".")
        if len(parts) < 3:
            continue  # NBSS short code — no pack encoding
        pack_prefix = ".".join(parts[:3])
        if pack_prefix not in known_packs and pack_prefix not in seen_missing_prefixes:
            seen_missing_prefixes.add(pack_prefix)
            missing_rows.append({
                "pack_prefix": pack_prefix,
                "example_sesame_pn": pn,
                "reason": "no_assembly_pack_task_found",
            })

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DATA_DIR / f"{job_prefix}_missing_packs.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["pack_prefix", "example_sesame_pn", "reason"])
        w.writeheader()
        w.writerows(missing_rows)

    if missing_rows:
        print(f"\n  WARNING: {len(missing_rows)} pack prefix(es) in parts_control have no "
              f"registered assembly pack task:")
        for row in missing_rows:
            print(f"    {row['pack_prefix']}  (e.g. {row['example_sesame_pn']})")
        print(f"  → To recover, run: python scripts/01_populate_assembly_packs.py "
              f"--job-prefix {job_prefix} --task-gid <GID>")
        print(f"  → Missing packs log: {out_path}")
    else:
        print(f"\n  All parts_control pack prefixes have registered assembly pack tasks. ✓")

    return len(missing_rows)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Populate build_assembly_packs from Asana")
    parser.add_argument("--job-prefix", required=True, help="e.g. 1670")
    parser.add_argument("--dry-run", action="store_true", help="Print actions without writing")
    parser.add_argument(
        "--task-gid",
        help=(
            "Register a single Asana task GID directly (targeted recovery for late-added "
            "drawings). Accepts tasks from Drawing Control, Build Schedule, or both. "
            "The GID is the long number at the end of any Asana task URL."
        ),
    )
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    stats = {"inserted": 0, "updated": 0, "skipped": 0}

    # ------------------------------------------------------------------
    # --task-gid path: targeted single-pack registration
    # ------------------------------------------------------------------
    if args.task_gid:
        print(f"\n--task-gid mode: fetching task {args.task_gid} ...")
        try:
            task = fetch_task_with_memberships(args.task_gid)
        except requests.HTTPError as e:
            print(f"  ERROR: could not fetch task {args.task_gid}: {e}")
            conn.close()
            sys.exit(1)

        task_name = task.get("name", "")
        task_gid  = task.get("gid", args.task_gid)
        project_type = classify_task_project(task)

        # Report which projects this task belongs to
        memberships = task.get("memberships", [])
        project_names = [m["project"]["name"] for m in memberships]
        print(f"  Task name     : {task_name}")
        print(f"  Task GID      : {task_gid}")
        print(f"  Found in      : {', '.join(project_names) if project_names else 'no projects'}")
        print(f"  Project type  : {project_type}")

        if project_type == "build_schedule":
            print(f"  → Task is in Build Schedule — Stage 1 normal scan WOULD find this.")
            print(f"    Re-running without --task-gid is sufficient. Proceeding with upsert anyway.")
        elif project_type == "drawing_control":
            print(f"  → Task is in Drawing Control ONLY — Stage 1 normal scan would miss this.")
            print(f"    Registering directly so Stage 2 can fetch its attachment.")
        elif project_type == "both":
            print(f"  → Task is multi-homed (Drawing Control + Build Schedule).")
            print(f"    Stage 1 normal scan would already find this via Build Schedule.")
            print(f"    Proceeding with upsert to ensure DB is current.")
        else:
            print(f"  → Task is not in Drawing Control or Build Schedule.")
            print(f"    Will upsert using provided GID but Stage 2 may find no PDF attachment.")

        # Validate name matches job prefix and has 3+ components
        name_parts = task_name.rstrip(".").split(".")
        if not task_name.startswith(args.job_prefix):
            print(f"\n  WARNING: task name '{task_name}' does not start with job prefix "
                  f"'{args.job_prefix}'. Proceeding but verify this is the correct task.")
        if len(name_parts) < 3 or not name_parts[1].isdigit():
            print(f"\n  WARNING: task name '{task_name}' does not follow expected "
                  f"'{args.job_prefix}.NNNN.X' format. Pack reference extraction may be wrong.")

        pack_ref  = extract_pack_reference(task_name)
        pack_name = task_name.rstrip(".")
        print(f"\n  Pack reference: {pack_ref}")
        print(f"  Pack name     : {pack_name}")

        row = {
            "asana_task_gid": task_gid,
            "pack_reference": pack_ref,
            "pack_name":      pack_name,
        }
        result = upsert_pack(conn, row, args.dry_run)
        label = "DRY-RUN: would be " if args.dry_run else ""
        print(f"\n  → {label}{result.upper()}")
        stats[result] += 1

    # ------------------------------------------------------------------
    # Normal path: scan Build Schedule for all job tasks
    # ------------------------------------------------------------------
    else:
        print(f"Fetching Build Schedule tasks for job prefix '{args.job_prefix}'...")
        build_tasks = get_build_schedule_tasks(args.job_prefix)
        print(f"  Found {len(build_tasks)} Build Schedule tasks")

        for bt in build_tasks:
            print(f"\n  {bt['name']} [{bt['gid']}]")
            pack_ref = extract_pack_reference(bt["name"])
            print(f"    -> Pack reference: {pack_ref}")

            row = {
                "asana_task_gid": bt["gid"],
                "pack_reference": pack_ref,
                "pack_name":      bt["name"].rstrip("."),
            }
            result = upsert_pack(conn, row, args.dry_run)
            print(f"    -> {result.upper()}" + (" (dry-run)" if args.dry_run else ""))
            stats[result] += 1
            time.sleep(0.2)

    # ------------------------------------------------------------------
    # Missing packs report (runs after both paths)
    # ------------------------------------------------------------------
    if not args.dry_run:
        write_missing_packs_report(conn, args.job_prefix)
    else:
        print(f"\n[dry-run] Skipping missing packs report (no DB writes made).")

    conn.close()

    print(f"\n{'DRY RUN — ' if args.dry_run else ''}Summary:")
    print(f"  Inserted: {stats['inserted']}")
    print(f"  Updated:  {stats['updated']}")
    print(f"  Skipped:  {stats['skipped']}")


if __name__ == "__main__":
    main()
