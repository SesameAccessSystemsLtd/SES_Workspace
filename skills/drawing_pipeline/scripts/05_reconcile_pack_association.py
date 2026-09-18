"""
05_reconcile_pack_association.py
Fills build_assembly_pack_gid for parts the drawing route never reached.

Where stages 1-4 flow drawing -> DB (a part only gets a pack if it was legible
on a PDF BOM), this stage flows DB -> pack: the part number itself encodes the
pack (job.pack.rev.item), so the association can be derived without any PDF.

Designed to run nightly. Append-only by construction:
  * never overwrites an existing build_assembly_pack_gid
  * never writes to Asana (SAP does that via 03_write_to_asana.py)
  * skips ambiguous cases rather than guessing

MOD/revision rule (per Giles, 2026-07-24)
----------------------------------------
A MOD drawing is additive. It never re-homes a part that already has a pack.
So when a pack code resolves to a base pack AND a MOD/rev sibling
(e.g. 1588.1004.B -> MainStairFrame. + MainStairFrame_MOD1.), an unassociated
part that is on no drawing BOM is assigned the BASE pack: had it been new on
the MOD, it would have appeared on the MOD drawing and stage 3 would have
caught it. Parts genuinely new on a MOD are picked up by stage 3, not here.

Usage:
  python 05_reconcile_pack_association.py                  # report only, no writes
  python 05_reconcile_pack_association.py --commit         # write to ops.db
  python 05_reconcile_pack_association.py --job-prefix 1588

Output:
  data/reconcile_proposed.csv   — every link this stage would make / made
  data/reconcile_ambiguous.csv  — pack code hits 2+ unrelated packs; needs a human
"""

from __future__ import annotations

import argparse
import csv
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))
from path_config import p as _p

DB_PATH   = _p("db.ops")
SKILL_DIR = Path(__file__).parent.parent
DATA_DIR  = SKILL_DIR / "data"

# Trailing revision markers that make a pack a MOD/rev sibling of another.
MOD_SUFFIX = re.compile(r"(_MOD\d*|_rev\d*|_\d+)$", re.I)

# A full-format PN embedded in a free-text task name, tolerating the stray space
# some imports leave after the job prefix ("1575. 1008.D.MotorPumpUnit").
PN_IN_NAME = re.compile(r"\b(\d{4})\.\s?((?:[A-Za-z0-9\-]+\.){2,}[A-Za-z0-9\-_]+)")


def strip_mod(pack_reference: str) -> str:
    """Pack reference with any MOD/rev suffix removed, for family grouping."""
    return MOD_SUFFIX.sub("", (pack_reference or "").rstrip(".")).rstrip(".")


def is_mod(pack_reference: str) -> bool:
    return bool(MOD_SUFFIX.search((pack_reference or "").rstrip(".")))


def pack_code(sesame_pn: str) -> str | None:
    """First three dot-components of a PN: job.pack.rev. None for short NBSS PNs."""
    parts = (sesame_pn or "").split(".")
    return ".".join(parts[:3]) if len(parts) >= 4 else None


def recover_pn(part_name: str) -> str | None:
    """Pull a full-format PN out of a task name when sesame_pn is empty."""
    m = PN_IN_NAME.search(part_name or "")
    if not m:
        return None
    return f"{m.group(1)}.{m.group(2)}"


def load_packs(cur) -> dict[str, list[tuple[str, str]]]:
    """pack code -> [(pack_reference, asana_task_gid)] for Build Schedule packs."""
    cur.execute("""
        SELECT pack_reference, asana_task_gid
        FROM build_assembly_packs
        WHERE project_name = 'Build Schedule'
          AND pack_reference IS NOT NULL
    """)
    by_code: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for ref, gid in cur.fetchall():
        m = re.match(r"^(\d{4}\.[^.]+\.[^.]+)", ref)
        if m:
            by_code[m.group(1)].append((ref, gid))
    return by_code


def resolve(sesame_pn: str, by_code) -> list[tuple[str, str]]:
    """Candidate packs for a PN, exact code first then revision-stripped."""
    code = pack_code(sesame_pn)
    if not code:
        return []
    if code in by_code:
        return by_code[code]
    # 1575.1005.B33 -> 1575.1005.B
    base = re.sub(r"^(\d{4}\.[^.]+\.[A-Za-z]+)\d*$", r"\1", code)
    return by_code.get(base, [])


def choose(candidates: list[tuple[str, str]]) -> tuple[tuple[str, str] | None, str]:
    """Apply the MOD rule. Returns (chosen, reason)."""
    if not candidates:
        return None, "no_pack_for_code"
    if len(candidates) == 1:
        return candidates[0], "single_pack"

    # Same drawing at different revisions -> take the base (see module docstring).
    if len({strip_mod(ref) for ref, _ in candidates}) == 1:
        bases = [c for c in candidates if not is_mod(c[0])]
        if len(bases) == 1:
            return bases[0], "mod_family_base"
        return None, "mod_family_no_single_base"

    # Genuinely different packs sharing a code — a human must decide.
    return None, "ambiguous_distinct_packs"


def latest_sync_date(cur) -> str:
    """Date of the most recent nightly mirror sync, used to exclude dead rows.

    parts_control is upsert-only (sync_parts_control.py never deletes), so rows
    for Asana tasks that no longer exist linger forever. Anything the newest
    sync did not touch is a tombstone and must not be written to.
    """
    cur.execute("SELECT MAX(substr(last_synced_at, 1, 10)) FROM parts_control")
    return cur.fetchone()[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--job-prefix", default=None,
                    help="Limit to one job, e.g. 1588. Default: all jobs.")
    ap.add_argument("--commit", action="store_true",
                    help="Write to ops.db. Without this, report only.")
    ap.add_argument("--include-stale", action="store_true",
                    help="Also consider rows the newest sync did not touch (unsafe).")
    args = ap.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    by_code = load_packs(cur)
    live_date = latest_sync_date(cur)
    print(f"Build Schedule pack codes loaded: {len(by_code)}")
    print(f"Newest mirror sync: {live_date}"
          + ("  (stale rows INCLUDED)" if args.include_stale else "  (stale rows excluded)"))

    where = ["(build_assembly_pack_gid IS NULL OR build_assembly_pack_gid = '')"]
    params: list = []
    if not args.include_stale:
        where.append("last_synced_at LIKE ?")
        params.append(f"{live_date}%")
    if args.job_prefix:
        where.append("(sesame_pn LIKE ? OR sesame_pn IS NULL OR sesame_pn = '')")
        params.append(f"{args.job_prefix}.%")

    cur.execute(f"""
        SELECT id, asana_task_gid, sesame_pn, part_name
        FROM parts_control
        WHERE {' AND '.join(where)}
    """, params)
    rows = cur.fetchall()
    print(f"Unassociated rows in scope: {len(rows)}")

    proposed, ambiguous = [], []
    stats = defaultdict(int)
    now = datetime.now(timezone.utc).isoformat()

    for r in rows:
        pn = (r["sesame_pn"] or "").strip()
        pn_source = "sesame_pn"
        if not pn:
            pn = recover_pn(r["part_name"]) or ""
            pn_source = "recovered_from_part_name"
        if not pn:
            stats["no_pn"] += 1
            continue

        if args.job_prefix and not pn.startswith(f"{args.job_prefix}."):
            continue

        cands = resolve(pn, by_code)
        chosen, reason = choose(cands)
        stats[reason] += 1

        if chosen is None:
            if reason != "no_pack_for_code":
                ambiguous.append({
                    "asana_task_gid": r["asana_task_gid"],
                    "sesame_pn": pn,
                    "pn_source": pn_source,
                    "reason": reason,
                    "candidate_packs": " | ".join(ref for ref, _ in cands),
                })
            continue

        proposed.append({
            "asana_task_gid": r["asana_task_gid"],
            "sesame_pn": pn,
            "pn_source": pn_source,
            "pack_reference": chosen[0],
            "pack_gid": chosen[1],
            "reason": reason,
            "part_name": (r["part_name"] or "")[:80],
        })

        if args.commit:
            # last_synced_at is the mirror sync's field — do not touch it here,
            # or the liveness check above starts reading this stage's own writes.
            #
            # association_status MUST move with the pack link. Writing the pack
            # but leaving the status at 'unassociated' left 170 rows contradicting
            # themselves and made every status-filtered dashboard under-report
            # linked parts (found 2026-09-04). chosen[1] is the numeric pack GID —
            # GID is the canonical format for this column, never the pack name.
            cur.execute(
                "UPDATE parts_control SET build_assembly_pack_gid = ?, "
                "association_status = 'linked', association_checked_at = ? "
                "WHERE id = ?",
                (chosen[1], now, r["id"]),
            )

    if args.commit:
        conn.commit()
    conn.close()

    with open(DATA_DIR / "reconcile_proposed.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["asana_task_gid", "sesame_pn", "pn_source",
                                          "pack_reference", "pack_gid", "reason", "part_name"])
        w.writeheader()
        w.writerows(proposed)
    with open(DATA_DIR / "reconcile_ambiguous.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["asana_task_gid", "sesame_pn", "pn_source",
                                          "reason", "candidate_packs"])
        w.writeheader()
        w.writerows(ambiguous)

    # The two files above are overwritten every run. A committed batch also gets an
    # immutable ledger, so any night's writes can be identified and rolled back:
    #   UPDATE parts_control SET build_assembly_pack_gid = NULL
    #   WHERE asana_task_gid IN (<gids from the ledger>);
    if args.commit and proposed:
        ledger = DATA_DIR / "reconcile_ledger" / f"{now[:19].replace(':', '')}.csv"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        with open(ledger, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["asana_task_gid", "sesame_pn", "pn_source",
                                              "pack_reference", "pack_gid", "reason", "part_name"])
            w.writeheader()
            w.writerows(proposed)
        print(f"  ledger         : {ledger}")

    print(f"\n{'COMMITTED' if args.commit else 'REPORT ONLY — no DB writes'}  ({now})")
    print(f"  links proposed : {len(proposed)}")
    print(f"  ambiguous      : {len(ambiguous)}")
    for k, v in sorted(stats.items(), key=lambda x: -x[1]):
        print(f"    {k:<32} {v}")
    print(f"  {DATA_DIR / 'reconcile_proposed.csv'}")
    print(f"  {DATA_DIR / 'reconcile_ambiguous.csv'}")


if __name__ == "__main__":
    main()
