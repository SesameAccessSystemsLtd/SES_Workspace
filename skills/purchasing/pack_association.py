"""
pack_association.py — Flag parts with no assembly-pack association.

Purpose
-------
A part in `parts_control` with no linked `build_assembly_packs` row means the
drawing pipeline has not been run for it. That step is manual and easy to
forget, so this module FLAGS those parts as priority. It never creates packs,
never edits Asana, and never runs the drawing pipeline itself.

Link resolution — read this before changing it
----------------------------------------------
`parts_control.build_assembly_pack_gid` is populated inconsistently:

  * 103 rows hold a real `build_assembly_packs.asana_task_gid`   (correct)
  * 732 rows hold a `build_assembly_packs.pack_reference` string (mis-keyed)
  * 1269 rows are NULL/empty                                     (genuinely unlinked)

The mis-keyed rows are a known, deliberately-unfixed issue. They ARE associated
with a pack — the value is just recorded under the wrong key — so resolving via
BOTH columns is what keeps them out of the flag set. Matching only on
asana_task_gid would raise 732 false "drawing pipeline required" alerts.

Statuses written to parts_control.association_status:
    linked          — resolved via asana_task_gid
    linked_by_ref   — resolved via pack_reference (mis-keyed but valid)
    unassociated    — no pack found; drawing pipeline likely required
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
from pathlib import Path

# Resolved relative to this file, not hardcoded — this must survive the move to
# a VPS, where C:\Users\GilesHugo does not exist.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))

try:
    from path_config import p as _p
    DEFAULT_DB_PATH = os.environ.get("OPENCLAW_OPS_DB_PATH") or _p("db.ops")
except Exception:  # pragma: no cover - PATH_CONFIG unreadable
    # Last resort: locate ops.db relative to this file so the module still works
    # off-Windows (CI, VPS) without PATH_CONFIG.
    _fallback = Path(__file__).resolve().parents[2] / "db" / "ops.db"
    DEFAULT_DB_PATH = os.environ.get("OPENCLAW_OPS_DB_PATH") or (
        str(_fallback) if _fallback.exists() else None
    )

STATUS_LINKED = "linked"
STATUS_LINKED_BY_REF = "linked_by_ref"
STATUS_UNASSOCIATED = "unassociated"

# Parts already finished need no drawing pipeline run.
FLAGGABLE_ONLY_IF_OPEN = True


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Add the association_status column if absent. Idempotent and additive."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(parts_control)")}
    if "association_status" not in cols:
        conn.execute("ALTER TABLE parts_control ADD COLUMN association_status TEXT")
    if "association_checked_at" not in cols:
        conn.execute("ALTER TABLE parts_control ADD COLUMN association_checked_at TEXT")
    conn.commit()


def classify(conn: sqlite3.Connection) -> dict[str, int]:
    """Recompute association_status for every part. Returns counts by status."""
    ensure_schema(conn)

    # Resolve via BOTH keys — see module docstring.
    conn.execute(
        """
        UPDATE parts_control
           SET association_status = CASE
                 WHEN build_assembly_pack_gid IS NULL
                   OR TRIM(build_assembly_pack_gid) = ''
                   THEN ?
                 WHEN EXISTS (SELECT 1 FROM build_assembly_packs b
                               WHERE b.asana_task_gid = parts_control.build_assembly_pack_gid)
                   THEN ?
                 WHEN EXISTS (SELECT 1 FROM build_assembly_packs b
                               WHERE b.pack_reference = parts_control.build_assembly_pack_gid)
                   THEN ?
                 ELSE ?
               END,
               association_checked_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
        """,
        (STATUS_UNASSOCIATED, STATUS_LINKED, STATUS_LINKED_BY_REF, STATUS_UNASSOCIATED),
    )
    conn.commit()

    return {
        row[0]: row[1]
        for row in conn.execute(
            "SELECT association_status, COUNT(*) FROM parts_control GROUP BY 1"
        )
    }


def get_flagged_parts(conn: sqlite3.Connection, open_only: bool = FLAGGABLE_ONLY_IF_OPEN):
    """Return parts needing a drawing-pipeline run, newest first."""
    sql = """
        SELECT asana_task_gid, sesame_pn, part_name, vendor_name,
               order_qty, stock_action, project_name, last_synced_at
          FROM parts_control
         WHERE association_status = ?
    """
    if open_only:
        sql += " AND (is_completed IS NULL OR is_completed = 0)"
    sql += " ORDER BY last_synced_at DESC"
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(sql, (STATUS_UNASSOCIATED,))]


# ── Reporting for the daily brief ──────────────────────────────────────────
# parts_control.project_name is the Asana BOARD name ("Parts Control") for every
# row, so it cannot separate build projects. The build/job number is instead the
# leading 4-digit prefix of the part number ("1588.1011.Y.007" -> 1588), which
# matches the build_assembly_packs.pack_reference convention. Covers 98.3% of
# open unmapped parts; the remainder are generic catalogue items with no project.
_PROJECT_RE = re.compile(r'(?<!\d)(1[0-9]{3})\s*\.')

# stock_action is free text. These buckets are an INFERENCE from the observed
# values and drive the "not on order / awaiting confirmation" figures — review
# them before trusting those numbers. Anything unlisted falls to "other".
ORDER_STATUS_BUCKETS = {
    "on_order": ["on order"],
    "awaiting_confirmation": ["requires price update", "confirmation"],
    "not_ordered": ["need to order", "project order", "order for stock"],
    "on_hold": ["hold order"],
}


def _project_of(row) -> str | None:
    for field in ("sesame_pn", "part_name"):
        value = row[field] if field in row.keys() else None
        if value:
            m = _PROJECT_RE.search(str(value))
            if m:
                return m.group(1)
    return None


def _order_bucket(stock_action: str | None) -> str:
    text = (stock_action or "").strip().lower()
    if not text:
        return "unknown"
    for bucket, needles in ORDER_STATUS_BUCKETS.items():
        if any(n in text for n in needles):
            return bucket
    return "other"


def unmapped_summary(conn: sqlite3.Connection) -> dict:
    """Summarise open unmapped parts for the daily brief.

    Returns {total, by_project: [(project, n)], by_order_status: {bucket: n}}.
    """
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT sesame_pn, part_name, stock_action
             FROM parts_control
            WHERE association_status = ?
              AND (is_completed IS NULL OR is_completed = 0)""",
        (STATUS_UNASSOCIATED,),
    ).fetchall()

    by_project: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for r in rows:
        key = _project_of(r) or "unidentified"
        by_project[key] = by_project.get(key, 0) + 1
        b = _order_bucket(r["stock_action"])
        by_status[b] = by_status.get(b, 0) + 1

    ordered = sorted(by_project.items(), key=lambda kv: (-kv[1], kv[0]))
    return {"total": len(rows), "by_project": ordered, "by_order_status": by_status}


def is_part_unassociated(conn: sqlite3.Connection, asana_task_gid: str) -> bool:
    """True if a single part has no resolvable pack. Used by the email pipeline."""
    row = conn.execute(
        "SELECT association_status FROM parts_control WHERE asana_task_gid = ?",
        (asana_task_gid,),
    ).fetchone()
    if row is None:
        return False
    status = row[0] if not isinstance(row, sqlite3.Row) else row["association_status"]
    return status == STATUS_UNASSOCIATED


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db-path", default=DEFAULT_DB_PATH)
    ap.add_argument("--dry-run", action="store_true",
                    help="Report counts without writing association_status")
    ap.add_argument("--list", action="store_true",
                    help="List open parts needing a drawing-pipeline run")
    args = ap.parse_args()

    if not args.db_path:
        print("ERROR: --db-path required (PATH_CONFIG unavailable)")
        sys.exit(1)
    if not Path(args.db_path).exists():
        print(f"ERROR: database not found at {args.db_path}")
        sys.exit(1)

    conn = sqlite3.connect(args.db_path, timeout=30)
    try:
        if args.dry_run:
            ensure_schema(conn)
            counts = dict(conn.execute("""
                SELECT CASE
                    WHEN build_assembly_pack_gid IS NULL OR TRIM(build_assembly_pack_gid)=''
                        THEN 'unassociated'
                    WHEN EXISTS (SELECT 1 FROM build_assembly_packs b
                                  WHERE b.asana_task_gid = parts_control.build_assembly_pack_gid)
                        THEN 'linked'
                    WHEN EXISTS (SELECT 1 FROM build_assembly_packs b
                                  WHERE b.pack_reference = parts_control.build_assembly_pack_gid)
                        THEN 'linked_by_ref'
                    ELSE 'unassociated' END, COUNT(*)
                FROM parts_control GROUP BY 1"""))
            print("DRY RUN — no changes written")
        else:
            counts = classify(conn)

        total = sum(counts.values())
        print(f"parts_control: {total} rows")
        for status in (STATUS_LINKED, STATUS_LINKED_BY_REF, STATUS_UNASSOCIATED):
            print(f"  {status:16} {counts.get(status, 0)}")

        if args.dry_run:
            # association_status is not written yet, so derive the count from the
            # same expression rather than reading a column that is still NULL.
            n_open = conn.execute("""
                SELECT COUNT(*) FROM parts_control
                 WHERE (is_completed IS NULL OR is_completed = 0)
                   AND (build_assembly_pack_gid IS NULL OR TRIM(build_assembly_pack_gid)=''
                        OR NOT (EXISTS (SELECT 1 FROM build_assembly_packs b
                                         WHERE b.asana_task_gid = parts_control.build_assembly_pack_gid)
                             OR EXISTS (SELECT 1 FROM build_assembly_packs b
                                         WHERE b.pack_reference = parts_control.build_assembly_pack_gid)))
            """).fetchone()[0]
            flagged = []
        else:
            flagged = get_flagged_parts(conn)
            n_open = len(flagged)
        print(f"\nOPEN parts needing drawing pipeline: {n_open}")

        if args.list:
            for row in flagged[:50]:
                pn = (row["sesame_pn"] or "-")[:18]
                nm = (row["part_name"] or "-")[:40].replace("\t", " ")
                print(f"  {pn:18} | {nm:40} | {row['project_name'] or '-'}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
