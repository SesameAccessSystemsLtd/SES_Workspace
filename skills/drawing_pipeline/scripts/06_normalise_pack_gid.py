"""
06_normalise_pack_gid.py — one-off migration: pack NAME strings -> pack GIDs.

WHY
---
`parts_control.build_assembly_pack_gid` accumulated two incompatible value
formats because its three writers disagreed:

  02_map_parts_to_packs.py        -> pack GID     (numeric)
  05_reconcile_pack_association.py-> pack GID     (numeric)
  execute_requests.apply_pack_assign (SAP) -> pack NAME  (reference string)

Measured 2026-09-04: 1239 rows held a NAME, 44 held a GID.

The damage is silent and severe. 03_write_to_asana.py (drawing pipeline stage 4)
INNER JOINs `pc.build_assembly_pack_gid = bap.asana_task_gid`, so every NAME row
is dropped from its result set:

    job 1037 : stage 4 saw   0 of 102 linked parts
    job 1575 : stage 4 saw   0 of 373 linked parts
    job 1588 : stage 4 saw  44 of 168 linked parts

GID is canonical, not NAME. A pack name is mutable — renaming a pack (e.g.
1037.9992.A -> 1037.9992.B) silently orphans every row keyed on the old string.
A GID survives renames. The column is named `_gid` and the primary consumer
joins on a GID.

WHAT THIS DOES
--------------
For every row whose value is NOT all-numeric, resolve
`build_assembly_packs.pack_reference == <value>` and replace it with that
pack's `asana_task_gid`.

  * Ambiguous (a name matching 2+ packs) -> SKIPPED, reported, never guessed.
  * Orphan (a name matching no pack)     -> SKIPPED, reported.
  * Rows already numeric                 -> untouched.

Also repairs `association_status` where it contradicts the data: a row with a
populated pack must not read 'unassociated'. Provenance values ('linked' vs
'linked_by_ref') are PRESERVED — they record how the match was made, not what
format the column holds.

Report-only by default. --commit writes, and always drops a rollback ledger
first (same idiom as 05_reconcile_pack_association.py).

Usage:
  python 06_normalise_pack_gid.py                  # report only
  python 06_normalise_pack_gid.py --job-prefix 1037
  python 06_normalise_pack_gid.py --commit
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))
from path_config import p as _p

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def is_gid(v: str) -> bool:
    """A GID is all digits. Pack names start with a digit too ('1037.9992...'),
    so a leading-digit test is NOT sufficient — that mistake under-reported the
    problem by 28x on first measurement."""
    return bool(v) and v.isdigit()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job-prefix", default=None,
                    help="Limit to one job, e.g. 1037. Default: whole table.")
    ap.add_argument("--commit", action="store_true",
                    help="Write. Without this, report only.")
    args = ap.parse_args()

    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(_p("db.ops"))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # pack_reference -> [gid, ...]; a duplicated reference is ambiguous.
    ref_to_gids: dict[str, list[str]] = defaultdict(list)
    for r in cur.execute("SELECT asana_task_gid, pack_reference "
                         "FROM build_assembly_packs"):
        if r["pack_reference"]:
            ref_to_gids[r["pack_reference"].strip()].append(r["asana_task_gid"])

    sql = ("SELECT id, asana_task_gid, sesame_pn, build_assembly_pack_gid, "
           "       association_status "
           "FROM parts_control "
           "WHERE build_assembly_pack_gid IS NOT NULL "
           "  AND build_assembly_pack_gid <> ''")
    params: tuple = ()
    if args.job_prefix:
        sql += " AND sesame_pn LIKE ?"
        params = (f"{args.job_prefix}.%",)
    rows = list(cur.execute(sql, params))

    migrate, ambiguous, orphan, already = [], [], [], 0
    for r in rows:
        val = (r["build_assembly_pack_gid"] or "").strip()
        if is_gid(val):
            already += 1
            continue
        gids = ref_to_gids.get(val, [])
        rec = {
            "id": r["id"],
            "asana_task_gid": r["asana_task_gid"],
            "sesame_pn": r["sesame_pn"],
            "old_value": val,
            "new_gid": "",
            "association_status": r["association_status"] or "",
        }
        if len(gids) == 1:
            rec["new_gid"] = gids[0]
            migrate.append(rec)
        elif len(gids) > 1:
            rec["new_gid"] = "|".join(gids)
            ambiguous.append(rec)
        else:
            orphan.append(rec)

    # Status contradiction: a populated pack that still reads 'unassociated'.
    # (05_reconcile_pack_association.py writes the pack but never the status.)
    status_sql = ("SELECT id, asana_task_gid, sesame_pn, build_assembly_pack_gid, "
                  "       association_status FROM parts_control "
                  "WHERE build_assembly_pack_gid IS NOT NULL "
                  "  AND build_assembly_pack_gid <> '' "
                  "  AND (association_status IS NULL "
                  "       OR association_status = 'unassociated')")
    if args.job_prefix:
        status_sql += " AND sesame_pn LIKE ?"
    status_fix = list(cur.execute(status_sql, params))

    print(f"scope            : {args.job_prefix or 'ALL JOBS'}")
    print(f"populated rows   : {len(rows)}")
    print(f"  already GID    : {already}")
    print(f"  name -> GID    : {len(migrate)}")
    print(f"  ambiguous      : {len(ambiguous)}   (skipped, never guessed)")
    print(f"  orphan         : {len(orphan)}   (skipped, no matching pack)")
    print(f"status to repair : {len(status_fix)}")

    if ambiguous:
        print("\nAMBIGUOUS — pack_reference maps to more than one pack:")
        for r in ambiguous[:20]:
            print(f"   {r['sesame_pn']:24s} {r['old_value']!r} -> {r['new_gid']}")
    if orphan:
        print("\nORPHAN — no pack carries this reference:")
        for r in orphan[:20]:
            print(f"   {r['sesame_pn']:24s} {r['old_value']!r}")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    stamp = now[:19].replace(":", "")

    def dump(name: str, recs: list[dict]) -> Path | None:
        if not recs:
            return None
        pth = DATA_DIR / f"normalise_{name}_{stamp}.csv"
        with open(pth, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(recs[0].keys()))
            w.writeheader()
            w.writerows(recs)
        return pth

    for nm, recs in (("proposed", migrate), ("ambiguous", ambiguous),
                     ("orphan", orphan)):
        pth = dump(nm, recs)
        if pth:
            print(f"  {nm:10s} : {pth}")

    if not args.commit:
        print(f"\nREPORT ONLY — no writes ({now})")
        print("Re-run with --commit to apply.")
        conn.close()
        return 0

    # Rollback ledger BEFORE any write.
    ledger_dir = DATA_DIR / "normalise_ledger"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    ledger = ledger_dir / f"{stamp}.csv"
    with open(ledger, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "asana_task_gid", "sesame_pn",
                    "old_build_assembly_pack_gid", "new_build_assembly_pack_gid",
                    "old_association_status", "new_association_status"])
        for r in migrate:
            w.writerow([r["id"], r["asana_task_gid"], r["sesame_pn"],
                        r["old_value"], r["new_gid"],
                        r["association_status"], r["association_status"]])
        for r in status_fix:
            w.writerow([r["id"], r["asana_task_gid"], r["sesame_pn"],
                        r["build_assembly_pack_gid"], r["build_assembly_pack_gid"],
                        r["association_status"] or "", "linked"])
    print(f"\n  rollback ledger : {ledger}")

    for r in migrate:
        cur.execute("UPDATE parts_control SET build_assembly_pack_gid = ? "
                    "WHERE id = ?", (r["new_gid"], r["id"]))
    # Only repair the contradiction. Never downgrade an existing provenance
    # value such as 'linked_by_ref'.
    for r in status_fix:
        cur.execute("UPDATE parts_control SET association_status = 'linked', "
                    "association_checked_at = ? WHERE id = ?", (now, r["id"]))
    conn.commit()
    conn.close()

    print(f"COMMITTED ({now})")
    print(f"  pack gid normalised : {len(migrate)}")
    print(f"  status repaired     : {len(status_fix)}")
    print("\nRollback:")
    print(f"  restore old_build_assembly_pack_gid / old_association_status "
          f"from {ledger}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
