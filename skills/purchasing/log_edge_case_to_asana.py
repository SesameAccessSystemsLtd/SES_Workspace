"""
log_edge_case_to_asana.py — Write an ordering edge case to Asana edge case project.

Creates a REQUESTS/ file (action: create_task_in_project) so SAP creates the task.
Also updates ordering_edge_cases.asana_edge_case_gid once SAP confirms — but since
SAP runs async we only write the REQUESTS/ file here and mark source row as pending.

Usage (module):
    from log_edge_case_to_asana import log_edge_case

Usage (CLI — flush all unlogged edge cases):
    python log_edge_case_to_asana.py
"""

import os
import sys
import json
import sqlite3
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "lib")))
from path_config import p as _p

REQUESTS_DIR = _p("queues.requests")
EDGE_CASE_PROJECT_GID = "1214604066893979"

# Severity display map
SEVERITY = {
    "vendor_fallback":           "INFO",
    "title_parse_fail":          "WARNING",
    "arrived_exceeds_order":     "WARNING",
    "stock_record_not_found":    "WARNING",
    "title_update_fail":         "ERROR",
    "sap_write_fail":            "ERROR",
    "no_task_created":           "ERROR",
}


def _requests_filename(script: str, issue_type: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    slug = issue_type.replace("_", "-")
    return f"{ts}-ordering-edge-case-{slug}.md"


def log_edge_case(
    run_timestamp: str,
    script: str,
    issue_type: str,
    task_name: str = "",
    asana_task_gid: str = "",
    vendor_name: str = "",
    description: str = "",
    raw_data: dict | None = None,
    stage: str = "",
    db_path: str = _p("db.ops"),
) -> dict:
    """Write edge case to ops.db and create a REQUESTS/ file for SAP.

    Returns {"written": 1/0, "request_file": path or None, "errors": 0/1}
    """
    from db_writer import write_ordering_edge_case

    row = {
        "run_timestamp": run_timestamp,
        "script": script,
        "stage": stage,
        "asana_task_gid": asana_task_gid,
        "task_name": task_name,
        "vendor_name": vendor_name,
        "issue_type": issue_type,
        "description": description,
        "raw_data": json.dumps(raw_data) if raw_data else None,
        "asana_edge_case_gid": None,
    }

    try:
        write_ordering_edge_case(row, db_path=db_path)
    except Exception as exc:
        return {"written": 0, "request_file": None, "errors": 1,
                "error_detail": str(exc)}

    severity_label = SEVERITY.get(issue_type, "❓ UNKNOWN")
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    task_title = f"[EDGE CASE] {issue_type} — {task_name or 'unknown'} — {date_str}"

    source_url = ""
    if asana_task_gid:
        source_url = f"https://app.asana.com/0/0/{asana_task_gid}/f"

    desc_block = "\n".join(filter(None, [
        f"Severity: {severity_label}",
        f"Script: {script}",
        f"Stage: {stage}" if stage else "",
        f"Issue: {issue_type}",
        f"Task: {task_name}" if task_name else "",
        f"Vendor: {vendor_name}" if vendor_name else "",
        f"Source task: {source_url}" if source_url else "",
        f"Detail: {description}" if description else "",
        f"Run: {run_timestamp}",
    ]))

    md_content = f"""---
From: ops_agent
To: asana_agent
Type: asana_write
Status: PENDING
Priority: normal
Created: {datetime.now(timezone.utc).isoformat()}
---

## Request
Action: create_task_in_project
Project GID: {EDGE_CASE_PROJECT_GID}
Task name: {task_title}

Description:
{desc_block}

## Context
Automated edge case from ordering pipeline script: {script}
"""

    fname = _requests_filename(script, issue_type)
    fpath = os.path.join(REQUESTS_DIR, fname)
    try:
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(md_content)
    except Exception as exc:
        return {"written": 1, "request_file": None, "errors": 1,
                "error_detail": f"REQUESTS/ file write failed: {exc}"}

    return {"written": 1, "request_file": fpath, "errors": 0}


def flush_unlogged(db_path: str = _p("db.ops")) -> None:
    """Find edge cases in ops.db with no Asana task GID and create REQUESTS/ files."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM ordering_edge_cases WHERE asana_edge_case_gid IS NULL ORDER BY id"
    ).fetchall()
    conn.close()

    if not rows:
        print("No unlogged edge cases.")
        return

    print(f"Flushing {len(rows)} unlogged edge cases...")
    for row in rows:
        result = log_edge_case(
            run_timestamp=row["run_timestamp"],
            script=row["script"],
            issue_type=row["issue_type"],
            task_name=row["task_name"] or "",
            asana_task_gid=row["asana_task_gid"] or "",
            vendor_name=row["vendor_name"] or "",
            description=row["description"] or "",
            raw_data=json.loads(row["raw_data"]) if row["raw_data"] else None,
            stage=row["stage"] or "",
            db_path=db_path,
        )
        status = "OK" if result["errors"] == 0 else f"FAIL: {result.get('error_detail')}"
        print(f"  [{row['id']}] {row['issue_type']} — {row['task_name']}: {status}")


if __name__ == "__main__":
    flush_unlogged()
