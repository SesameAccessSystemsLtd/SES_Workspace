"""
parts_control_propagator.py — Propagate On Order status to Parts Control dependents.

Scans the Order Numbers project for tasks with ORDER Status = "On Order - Awaiting CONFIRMATION".
For each parent order, collects tasks to update via TWO methods:
  1. Direct dependents + subtasks (preserved from original)
  2. Tasks referenced in the order's description (Asana task URLs) that are
     also confirmed dependents — not just mentioned in passing

Only updates tasks that have a Stock ACTION field and are NOT already at
PICKING COMPLETE or "On order (dependents...)".

Edge case logging: tasks in description but NOT actual dependents are reported
so Giles can review whether they should be linked.

Source: Order Numbers project (854650811917168) — replaces legacy Goods In scan.
Depth: direct subtasks + direct dependents only (1 level).

Audit log: actual updates written to propagator_audit_log table in ops.db via db_writer.

Usage:
    python parts_control_propagator.py --scan    # dry run, prints summary, no changes
    python parts_control_propagator.py --commit  # scan + apply in one pass
"""

import os
import re
import sys
import argparse
import requests
from datetime import datetime, timezone
from pathlib import Path

# Summary output contains emoji; without this the cron's cp1252 stdout raises
# UnicodeEncodeError in print_summary() — which runs BEFORE the commit and would
# abort the run before any updates were applied.
sys.stdout.reconfigure(encoding="utf-8")

# Co-located batch updater — handles Batch API + 429 retry
sys.path.insert(0, str(Path(__file__).parent))
from asana_batch_updater import push_updates, get_asana_token

# db_writer — sole write gateway for ops.db
_WORKSPACE = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_WORKSPACE / "skills" / "purchasing"))
from db_writer import log_propagator_run

BASE = "https://app.asana.com/api/1.0"

# === NEW: Scan Order Numbers instead of Goods In ===
ORDER_NUMBERS_PROJECT = "854650811917168"

# Parts Control project — for matching description-referenced GIDs
PARTS_CONTROL_PROJECT = "1211660707080749"

# Custom field GIDs
ORDER_STATUS_FIELD = "1143955334207040"
STOCK_ACTION_FIELD = "1199111659373956"

# Enum GIDs
ON_ORDER_AWAITING  = "1143955334207043"  # ORDER Status: On Order - Awaiting CONFIRMATION
ON_ORDER_DEPS_ENUM = "1200721961626388"  # Stock ACTION: On order (dependents NOT ORDER TASKS)
PICKING_COMPLETE   = "1208001339654376"  # Stock ACTION: PICKING COMPLETE

# Stock ACTION values we should NOT touch (already terminal or in-stock states)
PROTECTED_STOCK_ACTIONS = {
    "1208001339654376",  # PICKING COMPLETE / ITEM On Premises
    "1199111659373957",  # IN STOCK
    "1205886444471822",  # OBSOLETE
}

# Stock ACTION values that indicate the task is already in the ordering pipeline
# and should be updated to "On order (dependents NOT ORDER TASKS)"
UPDATEABLE_STOCK_ACTIONS = {
    "1199111659373959",  # ORDER FOR STOCK
    "1199111659373958",  # Urgent ORDER - Adds To Project order
    "1214069309221719",  # PROJECT ORDER / Lasercut parts
    "1214097752304880",  # SUBCON TASKS
    "1215532235640694",  # KITTED
    "1216446720921957",  # INHOUSE
    "1213735528777178",  # HOLD ORDER
    "1205843231438999",  # PLEASE PICK
    "1199111659373960",  # Check Stock Levels
    "1208418409602940",  # Requires Price update / confirmation
    "1215650969470979",  # QC ISSUE
    None,                 # Not set — treat as updateable (default state)
}

TASK_FIELDS = (
    "gid,name,notes,projects.gid,"
    "custom_fields.gid,"
    "custom_fields.enum_value.gid,"
    "custom_fields.enum_value.name"
)

# Regex to extract Asana task GIDs from description text
# Matches: app.asana.com/1/357939803556123/task/GID or /0/0/GID
TASK_URL_RE = re.compile(r'app\.asana\.com/[01]/(?:\d+/)?task/(\d+)')


def _headers(token):
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def _has_field(task, field_gid):
    """True if field_gid exists on the task (regardless of value)."""
    return any(cf.get("gid") == field_gid for cf in task.get("custom_fields", []))


def _get_enum(task, field_gid):
    """Return (enum_gid, enum_name) for a custom field on a task, or (None, None)."""
    for cf in task.get("custom_fields", []):
        if cf.get("gid") == field_gid:
            ev = cf.get("enum_value")
            return (ev.get("gid"), ev.get("name")) if ev else (None, None)
    return None, None


def _get_all_pages(url, params, token):
    """Fetch all pages from a paginated Asana list endpoint."""
    results = []
    while url:
        r = requests.get(url, headers=_headers(token), params=params)
        r.raise_for_status()
        body = r.json()
        results.extend(body.get("data", []))
        nxt = body.get("next_page")
        url = nxt.get("uri") if nxt else None
        params = {}  # cursor is embedded in the next_page URI
    return results


def find_on_order_parents(token):
    """Return all incomplete Order Numbers tasks with ORDER Status = On Order - Awaiting CONFIRMATION."""
    print(f"Scanning Order Numbers ({ORDER_NUMBERS_PROJECT}) for 'On Order - Awaiting CONFIRMATION' tasks...")
    all_tasks = _get_all_pages(
        f"{BASE}/projects/{ORDER_NUMBERS_PROJECT}/tasks",
        {"completed_since": "now", "opt_fields": TASK_FIELDS, "limit": 100},
        token,
    )
    parents = [t for t in all_tasks if _get_enum(t, ORDER_STATUS_FIELD)[0] == ON_ORDER_AWAITING]
    print(f"  Found {len(parents)} parent task(s) with 'On Order - Awaiting CONFIRMATION':")
    for p in parents:
        pu = _task_url(p["gid"], p["name"])
        print(f"    • {pu}")
    return parents


def collect_children(parent_gid, token):
    """Return [(relationship, task)] for all direct subtasks and dependents.

    Deduped by task gid: a part is frequently BOTH a subtask and a dependent of the
    same order (marking the main task as a dependent makes it look addressed in
    Asana). Without deduping it gets updated and audit-logged twice per run. Where a
    task holds both roles the relationship is recorded as "subtask+dependent".
    """
    subtasks   = _get_all_pages(f"{BASE}/tasks/{parent_gid}/subtasks",   {"opt_fields": TASK_FIELDS, "limit": 100}, token)
    dependents = _get_all_pages(f"{BASE}/tasks/{parent_gid}/dependents", {"opt_fields": TASK_FIELDS, "limit": 100}, token)
    merged = {}
    for rel, t in [("subtask", t) for t in subtasks] + [("dependent", t) for t in dependents]:
        gid = t["gid"]
        if gid in merged:
            prev_rel, prev_task = merged[gid]
            if rel not in prev_rel:
                merged[gid] = (f"{prev_rel}+{rel}", prev_task)
        else:
            merged[gid] = (rel, t)
    return list(merged.values())


def get_parts_control_gids(token):
    """Return a set of all incomplete Parts Control task GIDs."""
    tasks = _get_all_pages(
        f"{BASE}/projects/{PARTS_CONTROL_PROJECT}/tasks",
        {"completed_since": "now", "opt_fields": "gid", "limit": 100},
        token,
    )
    return {t["gid"] for t in tasks}


def parse_description_task_gids(notes, pc_gids):
    """Extract Asana task GIDs from notes, returning only those in Parts Control."""
    if not notes:
        return set()
    all_gids = set(TASK_URL_RE.findall(notes))
    return all_gids & pc_gids  # intersection: only Parts Control tasks


def build_update_list(token):
    """Scan all On Order parents and classify each child as update / skip / mismatch.

    Children are collected from:
      1. Direct dependents and subtasks (existing logic)
      2. Parts Control tasks referenced in the parent's description
         (only if they are ALSO confirmed dependents — not just mentioned)
    """
    parents = find_on_order_parents(token)

    # Pre-fetch all Parts Control GIDs for description matching
    print("  Fetching Parts Control task GIDs for description matching...")
    pc_gids = get_parts_control_gids(token)
    print(f"  Parts Control tasks: {len(pc_gids)}")

    updates = []
    skipped = []
    mismatches = []  # tasks in description but NOT actual dependents

    for parent in parents:
        pgid  = parent["gid"]
        pname = parent["name"]
        pu = _task_url(pgid, pname)

        # Method 1: Direct dependents + subtasks
        children = collect_children(pgid, token)
        dependent_gids = {
            t["gid"] for rel, t in children
        }

        # Method 2: Tasks referenced in description (if they're ALSO dependents)
        notes = parent.get("notes", "")
        desc_gids = parse_description_task_gids(notes, pc_gids)
        desc_also_deps = desc_gids & dependent_gids  # safe: only confirmed dependencies
        desc_not_deps = desc_gids - dependent_gids   # edge case: mentioned but not linked

        if desc_not_deps:
            mismatches.append({
                "parent_gid": pgid,
                "parent_name": pname,
                "desc_only_gids": list(desc_not_deps),
            })

        # Union: dependents/subtasks + description-referenced (and confirmed dependent)
        all_child_gids = dependent_gids.union(desc_also_deps)

        # If description-matching found new ones via dependents, fetch full task data
        extended_children = list(children)  # existing list
        new_from_desc = desc_also_deps - dependent_gids
        if new_from_desc:
            for child_gid in new_from_desc:
                r = requests.get(
                    f"{BASE}/tasks/{child_gid}",
                    headers=_headers(token),
                    params={"opt_fields": TASK_FIELDS},
                )
                if r.status_code == 200:
                    extended_children.append(("dependent (via description)", r.json()["data"]))

        print(f"  {pu}: {len(children)} direct child(ren), "
              f"{len(desc_gids)} PC task(s) in description "
              f"({len(desc_also_deps)} also dependent, {len(desc_not_deps)} not dependent)")

        for rel, task in extended_children:
            tgid = task["gid"]
            tname = task["name"]

            if not _has_field(task, STOCK_ACTION_FIELD):
                skipped.append({
                    "task_gid": tgid,
                    "task_name": tname,
                    "parent_name": pname,
                    "relationship": rel,
                    "reason": "Stock ACTION field not on this task (different project)",
                    "current_stock_action": None,
                })
                continue

            # Asana custom fields are project-scoped: a task with no project membership
            # (typically an annotation/marker subtask like "MOVED TO LASERCUT NOW") accepts
            # the Batch API write with 200 OK but never persists the value. Left unguarded
            # these re-write every 30-min cycle forever — one such task logged 661 phantom
            # updates. Skip them so we neither burn API calls nor pollute the audit log.
            if not task.get("projects"):
                skipped.append({
                    "task_gid": tgid,
                    "task_name": tname,
                    "parent_name": pname,
                    "relationship": rel,
                    "reason": "Task belongs to no project — custom fields cannot persist",
                    "current_stock_action": None,
                })
                continue

            sa_gid, sa_name = _get_enum(task, STOCK_ACTION_FIELD)

            if sa_gid in PROTECTED_STOCK_ACTIONS:
                skipped.append({
                    "task_gid": tgid,
                    "task_name": tname,
                    "parent_name": pname,
                    "relationship": rel,
                    "reason": f"Protected state — not updated ({sa_name})",
                    "current_stock_action": sa_name,
                })

            elif sa_gid == ON_ORDER_DEPS_ENUM:
                skipped.append({
                    "task_gid": tgid,
                    "task_name": tname,
                    "parent_name": pname,
                    "relationship": rel,
                    "reason": "Already set — no change needed",
                    "current_stock_action": sa_name,
                })

            elif sa_gid in UPDATEABLE_STOCK_ACTIONS or sa_gid is None:
                updates.append({
                    "task_gid": tgid,
                    "task_name": tname,
                    "parent_gid": pgid,
                    "parent_name": pname,
                    "relationship": rel,
                    "current_stock_action": sa_name,
                    "custom_field_gid": STOCK_ACTION_FIELD,
                    "new_enum_gid": ON_ORDER_DEPS_ENUM,
                    "new_stock_action": "On order (dependents NOT ORDER TASKS)",
                })

            else:
                # Unknown enum value — skip and log
                skipped.append({
                    "task_gid": tgid,
                    "task_name": tname,
                    "parent_name": pname,
                    "relationship": rel,
                    "reason": f"Unexpected Stock ACTION value: '{sa_name}' (gid={sa_gid})",
                    "current_stock_action": sa_name,
                })

    return updates, skipped, mismatches


ASA_BASE = "https://app.asana.com/0/0/"


def _task_url(gid, name):
    return f"[{name}]({ASA_BASE}{gid}/f)"


def print_summary(updates, skipped, mismatches):
    by_reason = {}
    for s in skipped:
        by_reason[s["reason"]] = by_reason.get(s["reason"], 0) + 1

    print(f"\nSummary:")
    print(f"  To update:                       {len(updates)}")
    for reason, count in sorted(by_reason.items(), key=lambda x: -x[1]):
        print(f"  Skipped ({reason[:40]}): {count}")

    if mismatches:
        print(f"\n⚠️  Tasks in description but NOT set as dependents ({len(mismatches)} parent(s)):")
        print("  These tasks are mentioned in the order description but are NOT linked as dependents.")
        print("  Review and add dependency links if these items are part of the order.")
        for m in mismatches:
            pu = _task_url(m["parent_gid"], m["parent_name"])
            print(f"\n  Order: {pu}")
            for gid in m["desc_only_gids"]:
                # Use direct task URL format: /0/0/{gid}/f
                tu = f"https://app.asana.com/0/0/{gid}/f"
                print(f"    • https://app.asana.com/0/0/{gid}/f")

    if updates:
        print(f"\nChildren being updated:")
        seen_parents = {}
        for u in updates:
            pu = seen_parents.get(u["parent_gid"])
            if not pu:
                pu = _task_url(u["parent_gid"], u["parent_name"])
                seen_parents[u["parent_gid"]] = pu
            tu = _task_url(u["task_gid"], u["task_name"])
            print(f"    • {tu} ({u['relationship']}, under {pu})")


def main():
    parser = argparse.ArgumentParser(
        description="Propagate On Order status to Parts Control dependents (scans Order Numbers project)."
    )
    parser.add_argument("--scan",   action="store_true", help="Dry run — print summary, no changes, no DB write")
    parser.add_argument("--commit", action="store_true", help="Apply updates via Batch API and log to ops.db")
    args = parser.parse_args()

    if not args.scan and not args.commit:
        parser.print_help()
        sys.exit(1)

    token = get_asana_token()
    if not token:
        sys.exit("ERROR: ASANA_PERSONAL_ACCESS_TOKEN not found in environment or ASANAconfig.json")

    updates, skipped, mismatches = build_update_list(token)
    print_summary(updates, skipped, mismatches)

    if mismatches:
        print("\n[EDGE CASE] Description-referenced PC tasks not set as dependents:")
        print("  These tasks are mentioned in the order description but are NOT")
        print("  linked as dependents. Giles should review and add dependency links")
        print("  if these items are part of the order.")

    if not updates:
        print("\nNothing to update.")
        sys.exit(0)

    if args.scan:
        print("\nDry run — no changes applied.")
        sys.exit(0)

    # --commit: apply and log to ops.db
    print(f"\nApplying {len(updates)} update(s) via Batch API...")
    ok, fail = push_updates(updates, token, dry_run=False)
    print(f"Done. Applied: {ok}  Failed: {fail}")

    run_at = datetime.now(timezone.utc).isoformat()
    result = log_propagator_run(run_at, updates)
    if result["errors"]:
        print(f"WARNING: {result['errors']} audit log row(s) failed to write to ops.db")

    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
