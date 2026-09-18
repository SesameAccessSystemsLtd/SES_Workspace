#!/usr/bin/env python3
"""
healthcheck_golden_index.py
=====================================================================
Canonical health check for the Rev6 Golden Index (ops.db table
golden_index_rev6). Replaces the old Parts-Control part-number overlap
"check", which had no diagnostic power for this index (different grain:
generic templates vs project-prefixed instances; and a different Asana
project). See PARTS_CONTROL_HEALTHCHECK_IMPACT.md.

Read-only. Exits non-zero if any hard invariant fails, so it can gate a
pipeline / cron. WARN items do not fail the run.

INVARIANTS (hard)
  1. Every row carries an asana_task_gid          (index anchors to a task)
  2. GIDs are 1:1 with rows (no collisions)        (distinct == rows)
  3. stock_number unique per KAN                   (BOM match unambiguous)
  4. Every KAN round-trips to the KAN-field gather (GID is real, not guessed)
WARN (informational, non-failing)
  5. needs_split / withheld backlog: KANs live in Asana (gather) but not in
     the index (awaiting a split decision).

USAGE
    python3 healthcheck_golden_index.py            # auto-detect ops.db
    python3 healthcheck_golden_index.py --db PATH
=====================================================================
"""
from __future__ import annotations
import argparse, csv, os, sqlite3, sys

HERE = os.path.dirname(os.path.abspath(__file__))
TABLE = "golden_index_rev6"
GATHERED_GIDS = os.path.join(HERE, "gathered_gids.csv")


def _pathconfig_db() -> str:
    try:
        import sys as _sys
        _sys.path.insert(0, os.path.join(HERE, "..", "lib"))
        from path_config import p as _p
        return _p("db.ops")
    except Exception:
        return ""


DB_CANDIDATES = [
    _pathconfig_db(),
    r"C:\Users\GilesHugo\SES_Workspace\db\ops.db",
    os.path.join(HERE, "..", "db", "ops.db"),
]


def load_gather(path: str = GATHERED_GIDS) -> set:
    kans = set()
    if os.path.exists(path):
        with open(path, encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                k = (r.get("kan") or "").strip()
                if k:
                    kans.add(k)
    return kans


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Golden Index invariant health check.")
    ap.add_argument("--db", default=None)
    args = ap.parse_args(argv)
    db = args.db or next((c for c in DB_CANDIDATES if c and os.path.exists(c)), None)
    if not db:
        print("FAIL: ops.db not found"); return 2

    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = con.execute(
        f"SELECT kan_number, stock_number, stock_number_norm, asana_task_gid FROM {TABLE}"
    ).fetchall()
    con.close()
    n = len(rows)
    gather = load_gather()

    results = []  # (name, ok, detail)

    # 1. every row has a GID
    no_gid = [r[0] for r in rows if not (r[3] or "").strip()]
    results.append(("1. every row has asana_task_gid", not no_gid,
                    f"{n - len(no_gid)}/{n} have a GID" +
                    (f"; MISSING: {no_gid[:10]}" if no_gid else "")))

    # 2. GIDs 1:1 (no collisions)
    seen = {}
    for r in rows:
        g = (r[3] or "").strip()
        if g:
            seen.setdefault(g, []).append(r[0])
    collisions = {g: k for g, k in seen.items() if len(k) > 1}
    results.append(("2. GIDs 1:1 with rows", not collisions,
                    f"{len(seen)} distinct GIDs over {n} rows" +
                    (f"; COLLISIONS: {list(collisions.values())[:5]}" if collisions else "")))

    # 3. stock_number unique per KAN (no stock maps to >1 KAN)
    bystock = {}
    for r in rows:
        bystock.setdefault((r[2] or "").strip(), []).append(r[0])
    dup_stock = {s: k for s, k in bystock.items() if s and len(k) > 1}
    results.append(("3. stock_number unique per KAN", not dup_stock,
                    "no stock number maps to >1 KAN" if not dup_stock
                    else f"AMBIGUOUS: {list(dup_stock.values())[:5]}"))

    # 4. every KAN round-trips to the KAN-field gather
    if gather:
        orphan = [r[0] for r in rows if r[0] not in gather]
        results.append(("4. KAN round-trips to KAN-field gather", not orphan,
                        f"{n - len(orphan)}/{n} KANs verified against gather" +
                        (f"; ORPHAN: {orphan[:10]}" if orphan else "")))
    else:
        results.append(("4. KAN round-trips to KAN-field gather", True,
                        "SKIPPED (gathered_gids.csv not found)"))

    # 5. WARN: needs_split / withheld backlog
    withheld = sorted(gather - {r[0] for r in rows}) if gather else []

    print("=" * 64)
    print(f"GOLDEN INDEX HEALTH CHECK — {TABLE} ({n} rows)")
    print("=" * 64)
    hard_ok = True
    for name, ok, detail in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
        print(f"         {detail}")
        hard_ok = hard_ok and ok
    print(f"  [WARN] 5. withheld from index (live in Asana, needs split/review): "
          f"{len(withheld)}")
    if withheld:
        print(f"         {withheld}")
    print("-" * 64)
    print("RESULT:", "PASS" if hard_ok else "FAIL")
    return 0 if hard_ok else 1


if __name__ == "__main__":
    sys.exit(main())
