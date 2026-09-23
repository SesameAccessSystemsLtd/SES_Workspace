"""
sync_qc_issues.py — Sync Asana QC project → ops.db:qc_issues

Source:  Asana project 737971594126804 ("1.0 - QC issues / Parts Needed / Causing delays")
Target:  ops.db table: qc_issues
Writer:  db_writer.upsert_qc_issues()

⚠️ STAGE 0 CORRECTION (2026-09-23)
    This script previously pointed at PROJECT_GID 902269769783650, which is the
    **purchasing PO log** ("POnumber-ddmmyy - Supplier - item") — NOT the QC project.
    Every nightly run therefore mirrored PO-log rows into a QC table. Repointed to
    the correct QC project below, and the stub task_to_row() replaced with a real
    field mapping.

SCOPE — WHAT IS MIRRORED
    A row is mirrored only if it is an OPEN task that carries a QC STATUS.
      * OPEN ONLY    — the project holds ~1,900 tasks, ~1,890 completed over 8 years.
                       Chase/automation workflows consume live issues only. Completed
                       rows are excluded CLIENT-SIDE on the per-task `completed` flag
                       because Asana IGNORES `completed=false` on
                       /projects/{gid}/tasks (verified 2026-09-17).
      * QC STATUS    — the project also holds standing process/reference notes that
                       document HOW QC is run with certain suppliers (e.g. "QC Serapid
                       system", "QC Lasercut deliveries - Process and supplier notes",
                       "QC Machining deliveries", "QC Maytec Materials"). These are NOT
                       issues and never carry a QC STATUS. Anything without a QC STATUS
                       is omitted. (Giles directive, 2026-09-23.)

    ⚠️ Both filters are the single place to change scope — do NOT bulk-import
    completed or note-type tasks by default. `--include-completed` is the explicit
    opt-in for completed history.

Records are permanent — a task that disappears from Asana is NOT deleted.
Its last_synced_at stops updating, which flags staleness. (Deletion is a separate,
deliberate decision; see the design review §9.)

Usage:
    python sync_qc_issues.py
    python sync_qc_issues.py --dry-run
    python sync_qc_issues.py --include-completed   # explicit opt-in, for reporting
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "purchasing"))

from sync_utils import paginate_tasks, get_custom_field, now_utc, setup_logging
import db_writer

# --------------------------------------------------------------------------
# Source project — CORRECTED 2026-09-23 (Stage 0)
# --------------------------------------------------------------------------
PROJECT_GID = "737971594126804"  # "1.0 - QC issues / Parts Needed / Causing delays"
PROJECT_URL = "https://app.asana.com/1/357939803556123/project/737971594126804"

OPT_FIELDS = (
    "gid,name,completed,completed_at,created_at,modified_at,"
    "custom_fields.gid,custom_fields.name,custom_fields.type,custom_fields.resource_subtype,"
    "custom_fields.display_value,custom_fields.number_value,custom_fields.text_value,"
    "custom_fields.date_value,custom_fields.enum_value.gid,custom_fields.enum_value.name,"
    "custom_fields.multi_enum_values.gid,custom_fields.multi_enum_values.name"
)

# 200 pages @ 100/page = 20,000 tasks. The project holds ~1,900 tasks, so this
# guarantees a full sweep. (Previously capped at 50 pages = 5,000, which silently
# truncated on the larger PO-log project.)
MAX_PAGES = 200

# --------------------------------------------------------------------------
# Field GIDs — verified live 2026-09-23 against the correct QC project.
# See asana_field_registry and reports/QC_PROJECT_CLAUDE_HANDOFF.md §2.3.
# --------------------------------------------------------------------------
F_NCR              = "1218774119815824"  # QC Issue ID - NCR          (text, AUTO-GENERATED)
F_PART_NO          = "1213228090549470"  # Sesame Part No             (text)
F_BUILD_PACK       = "1211660742155293"  # Build Packs reference      (reference)
F_LINKED_PACK      = "1200825119140954"  # Linked to Buildpack        (enum)
F_PO_TASK          = "1213595302673057"  # PO TASK                    (reference)
F_VENDOR           = "854127216887083"   # Vendor                     (text)
F_EMAIL            = "1109691357791838"  # Email                      (text)
F_SQR              = "1211774738554014"  # SQR                        (text)
F_QC_STATUS        = "1171735794722533"  # QC STATUS                  (enum)
F_ORDER_STATUS     = "1143955334207040"  # ORDER Status               (enum)
F_NEEDED_BY        = "1205496988611293"  # Needed by  (EDD)           (date)
F_DATE_RAISED      = "1205496988611295"  # Date Raised                (date)
F_DAYS_SINCE       = "1205496988611298"  # Days since raised          (number)
F_BUILD_IMPACT     = "1211173614771815"  # BUILD IMPACT               (enum)
F_PROJECT_NAME     = "1202882645384174"  # PROJECT  NAME              (enum)
F_REASON_ISSUE     = "1183656755187876"  # REASON FOR ISSUE           (enum)
F_IMPACT_LEGACY    = "1148052247637687"  # IMPACT (legacy)            (enum)
F_QC_REASON        = "1210660559208298"  # QC ISSUE REASON            (multi_enum)
F_NEEDS_PURCH      = "1207196303096574"  # NEEDS - from Purch         (multi_enum)
F_ORDER_QTY        = "1201447044882111"  # ORDER QTY                  (number)
F_ORDER_VALUE      = "1204913986258520"  # ORDER Value                (number)

# lib/ is on sys.path via the db_writer import above
from path_config import p as _p
DEFAULT_DB_PATH = os.environ.get("OPENCLAW_OPS_DB_PATH") or _p("db.ops")


def _num(task: dict, gid: str):
    """Return a numeric custom-field value (or None)."""
    for cf in task.get("custom_fields", []) or []:
        if cf.get("gid") == gid:
            return cf.get("number_value")
    return None


def _date(task: dict, gid: str):
    """Return a date custom-field value as YYYY-MM-DD (or None)."""
    for cf in task.get("custom_fields", []) or []:
        if cf.get("gid") == gid:
            dv = cf.get("date_value") or {}
            return dv.get("date") or cf.get("display_value")
    return None


def task_to_row(task: dict, sync_ts: str) -> dict:
    """Map an Asana QC task to an ops.db qc_issues row.

    Uses get_custom_field() for enum/multi_enum/text/number/reference (it falls back
    to display_value, which is correct for reference and date custom fields), and the
    _date/_num helpers where the raw typed value is wanted.
    """
    return {
        # identity
        "asana_task_gid":         task["gid"],
        "ncr_id":                 get_custom_field(task, F_NCR),
        "issue_name":             (task.get("name") or "").strip(),

        # part mapping (the chain that the chase depends on)
        "sesame_part_no":         get_custom_field(task, F_PART_NO),
        "build_pack_reference":   get_custom_field(task, F_BUILD_PACK),
        "linked_to_buildpack":    get_custom_field(task, F_LINKED_PACK),
        "parts_control_gid":      None,  # resolved later by the pack-link step

        # order + supplier mapping
        "po_task_gid":            get_custom_field(task, F_PO_TASK),
        "associated_po_number":   None,  # derived from po_task_gid / name when resolvable
        "vendor":                 get_custom_field(task, F_VENDOR),
        "supplier_email":         get_custom_field(task, F_EMAIL),
        "associated_sqr_number":  get_custom_field(task, F_SQR),

        # status
        "qc_status":              get_custom_field(task, F_QC_STATUS),
        "order_status":           get_custom_field(task, F_ORDER_STATUS),
        "build_impact":           get_custom_field(task, F_BUILD_IMPACT),
        "impact":                 get_custom_field(task, F_IMPACT_LEGACY),
        "reason_for_issue":       get_custom_field(task, F_REASON_ISSUE),
        "qc_issue_reason":        get_custom_field(task, F_QC_REASON),
        "needs_from_purch":       get_custom_field(task, F_NEEDS_PURCH),
        "project_name":           get_custom_field(task, F_PROJECT_NAME),

        # dates + money
        "date_raised":            _date(task, F_DATE_RAISED),
        "needed_by":              _date(task, F_NEEDED_BY),
        "days_since_raised":      get_custom_field(task, F_DAYS_SINCE),
        "order_qty":              _num(task, F_ORDER_QTY),
        "order_value":            _num(task, F_ORDER_VALUE),

        # lifecycle
        "completed":              1 if task.get("completed") else 0,
        "asana_completed_at":     task.get("completed_at"),
        "asana_created_at":       task.get("created_at"),
        "asana_modified_at":      task.get("modified_at"),
        "last_synced_at":         sync_ts,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync QC project → ops.db:qc_issues")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--include-completed", action="store_true",
                        help="Also mirror completed tasks (explicit opt-in; default is open-only)")
    args = parser.parse_args()

    log = setup_logging("sync_qc_issues")
    sync_ts = now_utc()

    log.info("Fetching tasks from QC project %s (%s)", PROJECT_GID, PROJECT_URL)
    tasks = paginate_tasks(PROJECT_GID, OPT_FIELDS, max_pages=MAX_PAGES)
    log.info("Fetched %d tasks (all completion states)", len(tasks))

    # Client-side completion filter — see module docstring SCOPE note.
    if args.include_completed:
        selected = tasks
        log.info("--include-completed set: mirroring all %d tasks", len(tasks))
    else:
        selected = [t for t in tasks if not t.get("completed")]
        log.info("Open tasks: %d  (skipped completed: %d)",
                 len(selected), len(tasks) - len(selected))

    # QC STATUS filter (Giles, 2026-09-23): the project also carries standing
    # process/reference notes ("QC Serapid system", "QC Lasercut deliveries -
    # Process and supplier notes", "QC Machining deliveries", "QC Maytec
    # Materials"). Those exist to document HOW QC is run with certain suppliers
    # and are NOT issues — they never carry a QC STATUS. Omit anything without one.
    #   has QC STATUS    -> a real issue, mirror it
    #   no  QC STATUS    -> a process/reference note, omit
    with_status = [t for t in selected if get_custom_field(t, F_QC_STATUS)]
    omitted = len(selected) - len(with_status)
    if omitted:
        log.info("Omitted %d task(s) with no QC STATUS (process/reference notes)", omitted)

    rows = [task_to_row(t, sync_ts) for t in with_status if t.get("name")]

    if args.dry_run:
        import json
        print(json.dumps(rows[:3], indent=2, default=str))
        log.info("Dry run — %d rows prepared, not written", len(rows))
        return

    result = db_writer.upsert_qc_issues(rows, source="sync_qc_issues", db_path=args.db_path)
    log.info("Done — written: %d  skipped: %d  errors: %d",
             result["written"], result["skipped"], result["errors"])


if __name__ == "__main__":
    main()
