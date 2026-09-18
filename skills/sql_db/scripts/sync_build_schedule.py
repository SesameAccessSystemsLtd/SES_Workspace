"""
sync_build_schedule.py — Sync Drawing Control + Build Schedule → ops.db:build_assembly_packs

Sources:
  Drawing Control  GID: 1142754335688166
  Build Schedule   GID: 852956628490855
Target:  ops.db table: build_assembly_packs
Writer:  db_writer.upsert_build_assembly_packs()

Both projects contribute to the same table — source_project_gid distinguishes them.
Safe to re-run — idempotent upsert. Never deletes rows.

Usage:
    python sync_build_schedule.py
    python sync_build_schedule.py --dry-run
"""

import argparse
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "purchasing"))

from sync_utils import paginate_tasks, get_custom_field, now_utc, setup_logging
import db_writer

DRAWING_CONTROL_GID  = "1142754335688166"
BUILD_SCHEDULE_GID   = "852956628490855"

OPT_FIELDS = (
    "gid,name,notes,completed,completed_at,due_on,"
    "custom_fields.gid,custom_fields.type,custom_fields.resource_subtype,"
    "custom_fields.display_value,custom_fields.number_value,custom_fields.text_value,"
    "custom_fields.enum_value.gid,custom_fields.enum_value.name"
)

# Production-readiness fields. These exist on the Build Schedule project and were
# previously hardcoded to None in task_to_row, which is why all 2,618 mirrored
# pack rows carried a null parts_readiness and stage_of_production.
GID_PARTS_READINESS = "1142754335683361"   # PARTS - Readiness
GID_STAGE           = "1146377793927906"   # STAGE of production
GID_STEEL_PROFILE   = "1142754335683355"   # STEEL PROFILE
GID_NEEDS_PURCH     = "1207196303096574"   # NEEDS - from Purch
GID_EST_HOURS       = "1155021669124711"   # Estimated Hours
GID_ACTUAL_COST     = "1155021669124674"   # "Actual Cost" = unplanned engineer hours
GID_REQUIRES_DEBRIEF= "1201006767756706"   # Requires De-Brief
GID_PROJECT_NAME    = "1202882645384174"   # PROJECT  NAME

PROJECT_CODE_RE = re.compile(r"\b(1[0-9]{3})\b")


def _project_code(*candidates: str | None) -> str | None:
    for text in candidates:
        if not text:
            continue
        m = PROJECT_CODE_RE.search(text)
        if m:
            return m.group(1)
    return None

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[3] / "lib"))
from path_config import p as _p

DEFAULT_DB_PATH = os.environ.get("OPENCLAW_OPS_DB_PATH") or _p("db.ops")


def task_to_row(task: dict, project_gid: str, project_name: str, sync_ts: str) -> dict:
    name = task.get("name", "").strip()
    return {
        "asana_task_gid":      task["gid"],
        "pack_reference":      name,
        "pack_name":           name,
        "revision":            None,
        "status":              None,
        "approval_status":     None,
        "parts_readiness":     get_custom_field(task, GID_PARTS_READINESS),
        "stage_of_production": get_custom_field(task, GID_STAGE),
        "steel_profile":       get_custom_field(task, GID_STEEL_PROFILE),
        "needs_from_purch":    get_custom_field(task, GID_NEEDS_PURCH),
        "estimated_hours":     get_custom_field(task, GID_EST_HOURS),
        "unplanned_hours":     get_custom_field(task, GID_ACTUAL_COST),
        "requires_debrief":    get_custom_field(task, GID_REQUIRES_DEBRIEF),
        "due_on":              task.get("due_on"),
        "project_code":        _project_code(name, get_custom_field(task, GID_PROJECT_NAME)),
        "is_completed":        1 if task.get("completed") else 0,
        "ai_processed_status": None,
        "url_to_drawing":      None,
        "source_project_gid":  project_gid,
        "project_name":        project_name,
        "last_synced_at":      sync_ts,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync build schedule projects → ops.db:build_assembly_packs")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    log = setup_logging("sync_build_schedule")
    sync_ts = now_utc()
    all_rows = []

    for project_gid, project_name in [
        (DRAWING_CONTROL_GID, "Drawing Control"),
        (BUILD_SCHEDULE_GID,  "Build Schedule"),
    ]:
        log.info("Fetching from %s (%s)", project_name, project_gid)
        tasks = paginate_tasks(project_gid, OPT_FIELDS)
        log.info("  %d tasks", len(tasks))
        all_rows.extend(
            task_to_row(t, project_gid, project_name, sync_ts)
            for t in tasks if t.get("name")
        )

    log.info("Total rows prepared: %d", len(all_rows))

    if args.dry_run:
        import json
        print(json.dumps(all_rows[:5], indent=2, default=str))
        return

    result = db_writer.upsert_build_assembly_packs(all_rows, source="sync_build_schedule",
                                                    db_path=args.db_path)
    log.info("Done — written: %d  skipped: %d  errors: %d",
             result["written"], result["skipped"], result["errors"])


if __name__ == "__main__":
    main()
