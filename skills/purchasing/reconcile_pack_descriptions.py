"""
reconcile_pack_descriptions.py — Write the canonical "Assembly Pack" description
line onto every part linked to an assembly pack, so no report ever sees a
"false link" (a part linked to a pack whose description does not say so).

Read this before changing it
----------------------------
* READ-ONLY against ops.db. The candidate SELECT opens `file:...?mode=ro` and
  never writes. The ONLY DB write is the audit row via db_writer.log_asana_write
  (the sanctioned gateway into asana_write_log).
* Description-only. NEVER touch task name, custom fields, orders, or write a QTY
  onto an order/PO task (guardrail Rule 7b). We therefore do NOT replicate the
  legacy multi-pack `*` name-prefix — the guardrail "never touch the name" wins
  over legacy behaviour. Multi-pack is only ever a joined ` | ` description line.
* Append-only. Read live notes first; never overwrite. Idempotent: the substring
  check ("Assembly Pack {pack_reference}") means a re-run never double-appends.
* Pack-name resolution: prefer parts_control.build_pack_ref_name; when it is NULL/
  empty, join build_assembly_packs on build_assembly_pack_gid to recover
  pack_reference. impact_engine.resolve_pack_gids must run BEFORE this script so
  name-linked parts (build_pack_ref_name set, GID NULL) have their GID filled and
  become candidates.
* The line format + idempotency check mirror legacy_bom_pack_allocation's
  `_note_line` / `_already_has_pack_note` (see plan section 0). One deliberate,
  documented divergence: a missing/zero qty omits "off" cleanly —
  "in Assembly Pack X" instead of legacy's " off in Assembly Pack X".

Usage
-----
  python reconcile_pack_descriptions.py --dry-run      # read-only, print would-be writes
  python reconcile_pack_descriptions.py               # LIVE — append missing lines
  python reconcile_pack_descriptions.py --watchdog    # silent on success, alert on failure
  python reconcile_pack_descriptions.py --limit 10    # cap the batch (first real run)
  python reconcile_pack_descriptions.py --all         # ignore the 31-day window
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path

# ── path / dependency resolution (survives the move off C:\\Users\\GilesHugo) ──
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

import requests
from asana_batch_updater import get_asana_token
import db_writer

SOURCE = "reconcile_pack_descriptions"
ASANA_BASE = "https://app.asana.com/api/1.0"
SEPARATOR = "\n---\n"
WINDOW_DAYS = 31

# Console safety on Windows cp1252 terminals.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# ── the canonical line + idempotency (mirrors legacy_bom_pack_ingest.py) ─────
def _qty_text(qty) -> str:
    """Normalise parts_control.order_qty (REAL/NULL) to the string legacy saw.

    Legacy `_note_line` did `(qty or "").strip()` on a string from the Excel parse.
    Here order_qty is REAL (e.g. 3.0) or NULL, so normalise first:
      3.0 -> '3'    2.5 -> '2.5'    None / 0 -> '' (falsy == missing).
    """
    if qty is None:
        return ""
    if isinstance(qty, float) and qty.is_integer():
        qty = int(qty)
    s = str(qty).strip()
    if not s:
        return ""
    try:
        if float(s) == 0:          # a zero quantity carries no pack meaning
            return ""
    except ValueError:
        pass
    return s


def _note_line(qty, pack_name: str) -> str:
    """Description line, e.g. '3 off in Assembly Pack 1591.7003.B.WheelStop-MT_MOD1.'.

    Mirrors legacy `_note_line` EXCEPT that a missing/zero qty special-cases to omit
    'off' cleanly (the plan prefers this):
        qty present  -> '{qty} off in Assembly Pack {pack_name}'
        qty missing  -> 'in Assembly Pack {pack_name}'
    The idempotency substring 'Assembly Pack {pack_name}' is unaffected either way.
    """
    q = _qty_text(qty)
    if q:
        return f"{q} off in Assembly Pack {pack_name}"
    return f"in Assembly Pack {pack_name}"


def _already_has_pack_note(notes: str | None, pack_reference: str) -> bool:
    return f"Assembly Pack {pack_reference}" in (notes or "")


# ── ops.db (READ-ONLY) ──────────────────────────────────────────────────────
def _connect_readonly(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


CANDIDATE_SQL = """
SELECT p.asana_task_gid AS task_gid,
       p.sesame_pn      AS sesame_pn,
       p.order_qty      AS order_qty,
       p.project_code   AS project_code,
       COALESCE(NULLIF(p.build_pack_ref_name, ''), b.pack_reference) AS pack_reference
  FROM parts_control p
  LEFT JOIN build_assembly_packs b ON b.asana_task_gid = p.build_assembly_pack_gid
 WHERE p.build_assembly_pack_gid IS NOT NULL
   AND p.build_assembly_pack_gid <> ''
   AND COALESCE(NULLIF(p.build_pack_ref_name, ''), b.pack_reference) IS NOT NULL
   AND COALESCE(NULLIF(p.build_pack_ref_name, ''), b.pack_reference) <> ''
"""

WINDOW_SQL = " AND p.created_at >= date('now', '-31 days')"


def select_candidates(conn: sqlite3.Connection, all_window: bool) -> list[sqlite3.Row]:
    sql = CANDIDATE_SQL
    if not all_window:
        sql += WINDOW_SQL
    return conn.execute(sql).fetchall()


# ── Asana (append-only) ─────────────────────────────────────────────────────
def _asana_get(token: str, task_gid: str):
    r = requests.get(
        f"{ASANA_BASE}/tasks/{task_gid}",
        headers={"Authorization": f"Bearer {token}"},
        params={"opt_fields": "name,notes"},
        timeout=30,
    )
    if r.status_code == 404:
        return None, "not_found"
    if r.status_code != 200:
        return None, f"http_{r.status_code}"
    return r.json().get("data", {}), None


def _asana_put(token: str, task_gid: str, payload: dict) -> tuple[bool, str]:
    r = requests.put(
        f"{ASANA_BASE}/tasks/{task_gid}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"data": payload},
        timeout=30,
    )
    if r.status_code in (200, 201):
        return True, ""
    return False, r.text[:200]


# ── main ────────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db-path", default=DEFAULT_DB_PATH)
    ap.add_argument("--dry-run", action="store_true",
                    help="Read-only: print would-be writes, no PUT")
    ap.add_argument("--watchdog", action="store_true",
                    help="Silent on success; print only failures and exit 1 if any")
    ap.add_argument("--limit", type=int, default=None,
                    help="Cap the number of tasks processed (first real run)")
    ap.add_argument("--all", action="store_true",
                    help="Ignore the 31-day window (wider backfill)")
    ap.add_argument("--pause", type=float, default=0.4,
                    help="Rate-limit pause per task, seconds (default 0.4)")
    args = ap.parse_args()

    if not args.db_path or not Path(args.db_path).is_file():
        raise SystemExit(f"ERROR: ops.db not found at {args.db_path!r}")

    token = get_asana_token()
    if not token:
        raise SystemExit("ERROR: ASANA_PERSONAL_ACCESS_TOKEN not found — cannot proceed.")

    # 1) READ-ONLY candidate selection.
    conn = _connect_readonly(args.db_path)
    try:
        rows = select_candidates(conn, args.all)
    finally:
        conn.close()

    # Group by task_gid (defensive: parts_control is keyed by gid, so 1 pack/task
    # today, but handle 2+ pack refs per task to match legacy multi-pack).
    by_task: dict[str, dict] = {}
    for r in rows:
        gid = r["task_gid"]
        entry = by_task.setdefault(gid, {
            "task_gid": gid,
            "sesame_pn": (r["sesame_pn"] or "").strip(),
            "project_code": r["project_code"],
            "packs": [],
        })
        entry["packs"].append({"qty": r["order_qty"], "pack_reference": r["pack_reference"]})

    candidates = len(by_task)
    task_ids = list(by_task.keys())
    if args.limit is not None:
        task_ids = task_ids[: args.limit]

    would_write = already_have = wrote = failed = 0
    not_found: list[str] = []
    failures: list[str] = []

    for gid in task_ids:
        task = by_task[gid]
        task_data, err = _asana_get(token, gid)
        if task_data is None:
            failed += 1
            if err == "not_found":
                not_found.append(gid)
            failures.append(f"  GET {gid} ({task['sesame_pn']}): {err}")
            time.sleep(args.pause)
            continue

        current_notes = task_data.get("notes") or ""
        current_name = task_data.get("name") or ""

        # Which pack refs are missing from the notes?
        missing = [p for p in task["packs"]
                   if not _already_has_pack_note(current_notes, p["pack_reference"])]
        if not missing:
            already_have += 1
            time.sleep(args.pause)
            continue

        would_write += 1
        note_lines = [_note_line(p["qty"], p["pack_reference"]) for p in missing]
        combined = " | ".join(note_lines)
        separator = SEPARATOR if current_notes.strip() else ""
        new_notes = current_notes + separator + combined

        if args.dry_run:
            for line in note_lines:
                print(f"  [would-write] {gid}  {task['sesame_pn']:26}  {line}")
            time.sleep(args.pause)
            continue

        success, msg = _asana_put(token, gid, {"notes": new_notes})
        if not success:
            failed += 1
            failures.append(f"  PUT {gid} ({task['sesame_pn']}): {msg}")
            time.sleep(args.pause)
            continue

        wrote += 1
        try:
            db_writer.log_asana_write({
                "task_gid": gid,
                "task_name": current_name,
                "email_id": None,
                "action_type": "update_description",
                "field_name": "notes",
                "field_gid": None,
                "value_applied": combined,
                "result": "success",
                "error_detail": None,
            }, source=SOURCE, db_path=args.db_path)
        except Exception as e:
            failures.append(f"  log_asana_write {gid} ({task['sesame_pn']}): {e}")
        time.sleep(args.pause)

    skipped = already_have + len(not_found)

    if args.watchdog:
        if failed or failures:
            for f in failures:
                print(f, file=sys.stderr)
            if not_found:
                print(f"  phantom GIDs (404, never guessed): {', '.join(not_found)}",
                      file=sys.stderr)
            return 1
        return 0

    # Normal (non-watchdog) reporting.
    mode = "DRY RUN (no writes)" if args.dry_run else "LIVE"
    print(f"\nPack-description reconcile — {mode}")
    print(f"Candidates: {candidates}"
          + (f"  (limited to {args.limit})" if args.limit is not None else ""))
    print(f"  would-write: {would_write}")
    print(f"  already-have: {already_have}")
    print(f"  failed/not-found: {failed}")
    print(f"Summary: checked={len(task_ids)} wrote={wrote} "
          f"skipped={skipped} failed={failed}")
    if not_found:
        print(f"  phantom GIDs (404, never guessed): {', '.join(not_found)}")
    if args.dry_run and would_write:
        print("\nRe-run (after a real run writes them) shows 0 would-write — idempotent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
