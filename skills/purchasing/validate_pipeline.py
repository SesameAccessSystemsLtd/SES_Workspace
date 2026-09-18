"""
validate_pipeline.py — Read-only health check for the purchasing pipeline.

Every check is a SELECT. Nothing is written, nothing is sent, no Asana call is
made. Safe to run at any time, including mid-sync.

Each check prints PASS, WARN or FAIL with the number behind it, so a failure
says what is wrong rather than that something is.

Usage:
    python validate_pipeline.py            # all checks
    python validate_pipeline.py --verbose  # include the passing detail
"""

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
from path_config import p as _p

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DB_PATH = _p("db.ops")

RESULTS = {"PASS": 0, "WARN": 0, "FAIL": 0}


def report(state: str, name: str, detail: str) -> None:
    RESULTS[state] += 1
    mark = {"PASS": "  PASS", "WARN": "  WARN", "FAIL": "  FAIL"}[state]
    print(f"{mark}  {name:<44} {detail}")


def section(title: str) -> None:
    print(f"\n{title}")
    print("-" * 78)


def one(conn, sql, args=()):
    row = conn.execute(sql, args).fetchone()
    return row[0] if row else None


def main() -> None:
    ap = argparse.ArgumentParser(description="Read-only pipeline validation")
    ap.add_argument("--db-path", default=DB_PATH)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    conn = sqlite3.connect(f"file:{args.db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    print(f"Purchasing pipeline validation — {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC")
    print(f"Database: {args.db_path}")

    # ---------------------------------------------------------------- 1
    section("1 · THE SIX FIXES — did they land and are they holding")

    n = one(conn, "SELECT COUNT(*) FROM parts_control "
                  "WHERE procurement_task_gid IS NOT NULL AND procurement_task_gid <> ''")
    total = one(conn, "SELECT COUNT(*) FROM parts_control")
    report("PASS" if n > 2000 else "FAIL", "part -> order link populated",
           f"{n} of {total}  (was 0)")

    # due_date must be a delivery date, i.e. it should sit AFTER the order date
    ahead = one(conn, """SELECT COUNT(*) FROM procurement_tasks
                          WHERE scope='live' AND due_date IS NOT NULL AND order_date IS NOT NULL
                            AND date(due_date) >= date(substr(order_date,1,10))""")
    behind = one(conn, """SELECT COUNT(*) FROM procurement_tasks
                           WHERE scope='live' AND due_date IS NOT NULL AND order_date IS NOT NULL
                             AND date(due_date) < date(substr(order_date,1,10))""")
    report("PASS" if ahead > behind else "FAIL", "due_date is a DELIVERY date",
           f"{ahead} on/after order date, {behind} before it")

    edge = one(conn, "SELECT COUNT(*) FROM ordering_edge_cases")
    report("PASS" if edge > 0 else "WARN", "edge-case channel receiving",
           f"{edge} row(s)  (was 0 since creation)")

    named = one(conn, "SELECT COUNT(*) FROM parts_control "
                      "WHERE build_assembly_pack_gid IS NOT NULL AND build_assembly_pack_gid <> '' "
                      "AND build_assembly_pack_gid NOT GLOB '[0-9]*'")
    report("PASS" if named == 0 else "FAIL", "pack column holds GIDs, not names",
           f"{named} name-valued row(s) remaining")

    ready = one(conn, "SELECT COUNT(*) FROM build_assembly_packs WHERE parts_readiness IS NOT NULL")
    report("PASS" if ready > 100 else "FAIL", "pack readiness mirrored",
           f"{ready} pack(s)  (was 0 of 2,618)")

    # change detection: a healthy day writes far fewer rows than the table holds
    today_writes = one(conn, "SELECT COUNT(*) FROM db_write_log WHERE written_at >= date('now')")
    report("PASS" if today_writes < 5000 else "WARN", "change detection holding",
           f"{today_writes} write-log rows today  (was ~26,000/day)")

    # ---------------------------------------------------------------- 2
    section("2 · THE CHANGE STREAM — is it carrying real signal")

    ev = one(conn, "SELECT COUNT(*) FROM part_supply_events WHERE observed_at >= date('now','-2 day')")
    report("PASS" if ev > 0 else "WARN", "events in the last 48h", f"{ev}")

    bogus = one(conn, "SELECT COUNT(*) FROM part_supply_events "
                      "WHERE old_value IS NULL AND new_value IS NULL")
    report("PASS" if bogus == 0 else "FAIL", "no null->null artefact events", f"{bogus}")

    if args.verbose:
        for r in conn.execute("""SELECT field, significance, COUNT(*) n FROM part_supply_events
                                  WHERE observed_at >= date('now','-2 day')
                                  GROUP BY 1,2 ORDER BY n DESC LIMIT 8"""):
            print(f"        {r['field']:<22} {r['significance']:<9} {r['n']}")

    # ---------------------------------------------------------------- 3
    section("3 · LIVE ORDER PICTURE — does it agree with the portal")

    live = one(conn, "SELECT COUNT(*) FROM procurement_tasks WHERE scope='live'")
    report("PASS" if 20 <= live <= 120 else "WARN", "live orders in a believable range",
           f"{live}  (portal showed ~40-50)")

    overdue = one(conn, "SELECT COUNT(*) FROM procurement_tasks "
                        "WHERE scope='live' AND due_date < date('now')")
    report("PASS" if overdue < live else "WARN", "overdue is a subset of live",
           f"{overdue} of {live}")

    noscope = one(conn, "SELECT COUNT(*) FROM procurement_tasks WHERE scope IS NULL")
    report("WARN" if noscope else "PASS", "orders with no scope classification",
           f"{noscope}  (historic rows, excluded from live views)")

    # ---------------------------------------------------------------- 4
    section("4 · STALE DATA — rows that no longer exist in Asana")

    stale_parts = one(conn, "SELECT COUNT(*) FROM parts_control WHERE supply_route IS NULL")
    report("WARN" if stale_parts else "PASS", "parts_control rows never re-synced",
           f"{stale_parts}  (no longer in the Asana project; inflate live counts)")

    logsize = one(conn, "SELECT COUNT(*) FROM db_write_log")
    report("WARN" if logsize > 1_000_000 else "PASS", "db_write_log size",
           f"{logsize:,} rows  (pre-09-09 rows record nothing real)")

    # ---------------------------------------------------------------- 5
    section("5 · IMPACT ENGINE — is it producing usable output")

    run_at = one(conn, "SELECT MAX(run_at) FROM production_impact")
    fresh = run_at and run_at >= datetime.now(timezone.utc).strftime("%Y-%m-%d")
    report("PASS" if fresh else "WARN", "impact computed today", f"last run {run_at}")

    if run_at:
        w = "WHERE run_at = (SELECT MAX(run_at) FROM production_impact)"
        bands = dict(conn.execute(f"SELECT risk_band, COUNT(*) FROM production_impact {w} GROUP BY 1").fetchall())
        report("PASS" if bands else "FAIL", "packs scored",
               "  ".join(f"{k}={v}" for k, v in sorted(bands.items())))

        conflicts = one(conn, f"SELECT COUNT(*) FROM production_impact {w} AND readiness_conflict IS NOT NULL")
        report("PASS", "readiness contradictions surfaced", f"{conflicts}")

        sched = one(conn, f"SELECT COUNT(*) FROM production_impact {w} AND schedulable=1")
        report("PASS", "packs schedulable now", f"{sched}")

        orphan = one(conn, f"SELECT COUNT(*) FROM production_impact {w} AND parts_total = 0")
        report("PASS" if orphan == 0 else "WARN", "no pack scored with zero parts", f"{orphan}")

    # ---------------------------------------------------------------- 6
    section("6 · IMPROVEMENT ENGINE — is the loop turning")

    cases = one(conn, "SELECT COUNT(*) FROM improvement_cases")
    report("PASS" if cases > 0 else "WARN", "open friction cases", f"{cases}")

    snaps = one(conn, "SELECT COUNT(DISTINCT metric_key) FROM metric_snapshots")
    report("PASS" if snaps >= 10 else "WARN", "metrics being snapshotted", f"{snaps} distinct")

    fails = one(conn, "SELECT COUNT(*) FROM improvement_cases "
                      "WHERE category='pipeline_failure' AND state IN ('new','assessing','staged')")
    report("PASS" if fails == 0 else "FAIL", "no scheduled job in an error state", f"{fails}")

    if args.verbose and cases:
        for r in conn.execute("""SELECT friction_score, occurrences, category, title
                                   FROM improvement_cases
                                  WHERE state NOT IN ('proven','rejected','dormant')
                                  ORDER BY friction_score DESC LIMIT 6"""):
            print(f"        {r['friction_score']:>7.1f}  {r['occurrences']:>4}  "
                  f"{r['category'][:24]:<24} {r['title'][:36]}")

    # ---------------------------------------------------------------- 7
    section("7 · PO LEDGER — state of play")

    led = one(conn, "SELECT COUNT(*) FROM po_number_ledger")
    if led == 0:
        report("WARN", "ledger seeded", "NOT SEEDED — nothing can reserve until it is")
    else:
        last = one(conn, "SELECT MAX(po_number) FROM po_number_ledger")
        manual = one(conn, "SELECT COUNT(*) FROM po_number_ledger WHERE origin='manual'")
        report("PASS", "ledger seeded", f"last issued {last}, {led} row(s), {manual} adopted")

    # ---------------------------------------------------------------- summary
    print()
    print("=" * 78)
    print(f"  PASS {RESULTS['PASS']}   WARN {RESULTS['WARN']}   FAIL {RESULTS['FAIL']}")
    print("=" * 78)
    print("\nWARN = known and expected, or awaiting a decision from you.")
    print("FAIL = something regressed and needs looking at.")
    conn.close()
    sys.exit(1 if RESULTS["FAIL"] else 0)


if __name__ == "__main__":
    main()
