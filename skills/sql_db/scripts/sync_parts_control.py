"""
sync_parts_control.py — Sync Asana Parts Control project → ops.db:parts_control

Source:  Asana project 1211660707080749 (Parts Control)
Target:  ops.db table: parts_control
Writer:  db_writer.upsert_parts_control()

Run nightly. Safe to re-run — idempotent upsert. Never deletes rows.

Usage:
    python sync_parts_control.py
    python sync_parts_control.py --db-path C:\\custom\\ops.db
    python sync_parts_control.py --dry-run   # print rows without writing
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

PROJECT_GID = "1211660707080749"  # Parts Control

# Custom field GIDs — sourced from asana_field_registry / bom_pipeline/CLAUDE.md
GID_SESAME_PN    = "1213228090549470"
GID_ORDER_QTY    = "1201447044882111"
GID_STOCK_ACTION = "1199111659373956"
GID_VENDOR       = "854127216887083"
GID_BUILD_PACKS  = "1211660742155293"  # reference field → returns the pack NAME, not a GID
GID_ORDER_STATUS = "1143955334207040"
GID_ARRIVED_QTY  = "1216071767931108"
GID_UNIT_PRICE   = "1182371951145356"
GID_REV_NUMBER   = "1214229889503630"
GID_PO_TASK      = "1213595302673057"  # reference field → the order task
GID_STOCK_LINK   = "1214966724754842"

OPT_FIELDS = (
    "gid,name,completed,completed_at,due_on,parent.gid,parent.name,"
    "memberships.project.gid,memberships.section.name,"
    "custom_fields.gid,custom_fields.type,custom_fields.resource_subtype,"
    "custom_fields.display_value,custom_fields.number_value,custom_fields.text_value,"
    "custom_fields.enum_value.gid,custom_fields.enum_value.name"
)

# Job number appears as a 4-digit prefix in the part name, the Sesame Part No or
# the parent order task name, e.g. "21564-1595 - Thameside Machining - 1595 - ...".
PROJECT_CODE_RE = re.compile(r"\b(1[0-9]{3})\b")

# Derived supply route — drives the pre-lasercut impact forecast. The vendor
# strings are the ones actually present in Parts Control today.
LASERCUT_HINTS = ("laser cut", "lasercut", "laser-cut", "exact", "lasershape", "nbss")
SUBCON_HINTS   = ("plating", "anodis", "galvan", "powder", "engraving", "blacken",
                  "chem black", "machining", "hardening", "welded by sesame")
INHOUSE_HINTS  = ("sesame", "inhouse", "in house")


def _project_code(*candidates: str | None) -> str | None:
    for text in candidates:
        if not text:
            continue
        m = PROJECT_CODE_RE.search(text)
        if m:
            return m.group(1)
    return None


def _supply_route(vendor: str | None, stock_action: str | None, part_name: str | None,
                  order_status: str | None = None) -> str:
    """Classify how this part is supplied. Deterministic, and only from fields we
    already hold — no guessing at material or process.

    Explicit INHOUSE markers win over vendor hints: a part named
    "... - Bush Machining INHOUSE" with ORDER Status "INHOUSE - Add to build" is
    made here, even though the vendor field says Machining.
    """
    action = (stock_action or "").strip().lower()
    status = (order_status or "").strip().lower()
    blob = f"{vendor or ''} {part_name or ''}".lower()

    if "inhouse" in action or "inhouse" in status or "in house" in status:
        return "inhouse"
    if "inhouse" in (part_name or "").lower() or "in house" in (part_name or "").lower():
        return "inhouse"
    if "subcon" in action:
        return "subcon"
    if action.startswith("kitted") or "on  premises" in action or "in stock" in action:
        return "stock"
    if any(h in blob for h in LASERCUT_HINTS):
        return "lasercut"
    if any(h in blob for h in SUBCON_HINTS):
        return "subcon"
    if any(h in blob for h in INHOUSE_HINTS):
        return "inhouse"
    return "buy"

# lib/ is on sys.path via the db_writer import above
from path_config import p as _p
DEFAULT_DB_PATH = os.environ.get("OPENCLAW_OPS_DB_PATH") or _p("db.ops")


def task_to_row(task: dict, sync_ts: str) -> dict:
    build_pack_ref = get_custom_field(task, GID_BUILD_PACKS)
    parent         = task.get("parent") or {}
    vendor         = get_custom_field(task, GID_VENDOR)
    stock_action   = get_custom_field(task, GID_STOCK_ACTION)
    sesame_pn      = get_custom_field(task, GID_SESAME_PN)
    part_name      = task.get("name", "").strip()

    row = {
        "asana_task_gid":          task["gid"],
        "sesame_pn":               sesame_pn,
        "part_name":               part_name,
        "order_qty":               get_custom_field(task, GID_ORDER_QTY),
        "stock_action":            stock_action,
        "vendor_name":             vendor,
        # The reference field returns the pack NAME. Keep it truthful here; the
        # resolver in impact_engine.py fills build_assembly_pack_gid with the real
        # GID by matching build_assembly_packs.pack_name.
        "build_pack_ref_name":     build_pack_ref,
        # THE ORDER LINK. Asana already asserts it via task parentage on 2,426 of
        # 2,434 parts. It was previously hardcoded to None and thrown away, which
        # is why nothing downstream could answer "which order covers this part".
        "procurement_task_gid":    parent.get("gid"),
        "po_task_gid":             get_custom_field(task, GID_PO_TASK),
        "stock_link_gid":          get_custom_field(task, GID_STOCK_LINK),
        "order_status":            get_custom_field(task, GID_ORDER_STATUS),
        "arrived_qty":             get_custom_field(task, GID_ARRIVED_QTY),
        "unit_price":              get_custom_field(task, GID_UNIT_PRICE),
        "rev_number":              get_custom_field(task, GID_REV_NUMBER),
        "due_on":                  task.get("due_on"),
        "completed_at":            task.get("completed_at"),
        "project_code":            _project_code(sesame_pn, part_name, parent.get("name")),
        "supply_route":            _supply_route(vendor, stock_action, part_name,
                                                 get_custom_field(task, GID_ORDER_STATUS)),
        "is_subtask":              1 if parent else 0,
        "is_completed":            1 if task.get("completed") else 0,
        "project_name":            "Parts Control",
        "last_synced_at":          sync_ts,
    }
    # Only overwrite an existing real GID if we actually resolved one.
    if not build_pack_ref:
        row.pop("build_pack_ref_name")
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync Parts Control → ops.db:parts_control")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    parser.add_argument("--dry-run", action="store_true", help="Print rows without writing")
    args = parser.parse_args()

    log = setup_logging("sync_parts_control")
    sync_ts = now_utc()

    log.info("Fetching tasks from Parts Control project %s", PROJECT_GID)
    tasks = paginate_tasks(PROJECT_GID, OPT_FIELDS, all_completion_states=True)
    log.info("Fetched %d tasks (%d completed)",
             len(tasks), sum(1 for t in tasks if t.get("completed")))

    rows = [task_to_row(t, sync_ts) for t in tasks if t.get("name")]

    if args.dry_run:
        import json
        print(json.dumps(rows[:5], indent=2, default=str))
        log.info("Dry run — %d rows prepared, not written", len(rows))
        return

    result = db_writer.upsert_parts_control(rows, source="sync_parts_control",
                                             db_path=args.db_path)
    log.info("Done — written: %d  skipped: %d  errors: %d",
             result["written"], result["skipped"], result["errors"])


if __name__ == "__main__":
    main()
