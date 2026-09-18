"""
impact_engine.py — Live parts → production impact, computed ahead of lasercut delivery.

The problem this solves
-----------------------
Production due dates are usually not set until the lasercut order lands, because
that is when packs get built and impact gets assessed. By then the assessment is
too late to act on. This engine forecasts a pack's production date from its own
supply chain — the lasercut ETA, or the longest-lead unresolved part — so impact
is visible while there is still time to expedite.

What it writes
--------------
production_impact — one row per live build pack per run, with:
  * parts ready / on order / unordered / unknown
  * lasercut state and ETA
  * longest-lead part, vendor and ETA
  * a forecast production date and where that date came from
  * a risk band, the reason for it, and the next action with an owner

It also repairs build_assembly_pack_gid, which the Asana reference field fills
with the pack NAME rather than a GID.

Read-only against Asana. Writes only to ops.db via db_writer.

Usage:
    python impact_engine.py                 # compute and store
    python impact_engine.py --dry-run       # print, store nothing
    python impact_engine.py --project 1597  # one job
    python impact_engine.py --report        # print the current at-risk list
"""

import argparse
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
from path_config import p as _p

try:  # console safety on Windows cp1252 terminals
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DB_PATH = _p("db.ops")

# ---------------------------------------------------------------------------
# Tuning — every number here is a stated assumption, not a hidden constant.
# ---------------------------------------------------------------------------

# Working days between the last part landing and the pack being buildable.
# Corrected 2026-09-10 (Giles): lasercut and steel profile turn round almost
# immediately, and a pack is scheduled as soon as PARTS - Readiness reads
# "Ready for Production" — the constraint at that point is labour, not parts.
# So this is a labour queue allowance, not a materials lead time.
LABOUR_QUEUE_DAYS = 3

# Default supplier lead time when procurement_tasks.days_for_delivery is unset.
DEFAULT_LEAD_DAYS = 14

# Lead-time assumption by supply route, used only when there is no order yet.
# lasercut/steel are near-immediate in practice; the long poles are bought-in
# and subcontracted finishing.
ROUTE_LEAD_DAYS = {
    "lasercut":  2,
    "steel":     2,
    "subcon":   12,
    "buy":      14,
    "inhouse":   5,
    "stock":     0,
}

# PARTS - Readiness values that mean the pack can be scheduled now.
READY_STATES = ("ready for production", "ready - but second ops needed",
                "rfp - non critical parts missing")

# Stock ACTION values that mean the part is physically available.
READY_ACTIONS = {
    "kitted", "item on  premises", "item on premises", "in stock",
    "all picked / no further action",
}
# Stock ACTION values that mean the part is committed to a supplier.
ON_ORDER_ACTIONS = {
    "on order (dependents not order tasks)", "subcon tasks",
    "project order / lasercut parts",
}
# Stock ACTION values that mean nothing has happened yet.
UNORDERED_ACTIONS = {
    "need to order", "please pick", "order for stock",
    "requires price update / confirmation", "hold order", "qc issue",
}

ORDER_ARRIVED_HINTS  = ("arrived",)
ORDER_ONORDER_HINTS  = ("on order", "awaiting dispatch", "partial delivery")
ORDER_PRE_HINTS      = ("quote", "to be ordered", "to be assessed", "picking",
                        "awaiting position", "hold")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today() -> datetime:
    return datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def _parse_date(value) -> datetime | None:
    if not value:
        return None
    text = str(value)[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _add_working_days(start: datetime, days: int) -> datetime:
    d = start
    added = 0
    while added < days:
        d += timedelta(days=1)
        if d.weekday() < 5:
            added += 1
    return d


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Pack GID resolution
# ---------------------------------------------------------------------------

def resolve_pack_gids(conn: sqlite3.Connection) -> int:
    """Fill parts_control.build_assembly_pack_gid from build_pack_ref_name.

    The Asana reference field returns the referenced task's name, so 1,470 of the
    1,519 populated rows held a pack name in a column called ..._gid. Matching on
    build_assembly_packs.pack_name recovers the real GID deterministically.

    Ambiguity guard: a pack name that resolves to more than one pack task is
    SKIPPED, never guessed - same discipline as
    drawing_pipeline/scripts/06_normalise_pack_gid.py, which found 12 such names.
    """
    try:
        rows = conn.execute("""
            SELECT p.asana_task_gid AS part_gid, b.asana_task_gid AS pack_gid
              FROM parts_control p
              JOIN build_assembly_packs b ON b.pack_name = p.build_pack_ref_name
             WHERE p.build_pack_ref_name IS NOT NULL
               AND p.build_pack_ref_name <> ''
               AND (p.build_assembly_pack_gid IS NULL
                    OR p.build_assembly_pack_gid = ''
                    OR p.build_assembly_pack_gid = p.build_pack_ref_name)
               AND p.build_pack_ref_name NOT IN (
                     SELECT pack_name FROM build_assembly_packs
                      GROUP BY pack_name
                     HAVING COUNT(DISTINCT asana_task_gid) > 1)
        """).fetchall()
    except sqlite3.OperationalError:
        return 0
    for r in rows:
        conn.execute(
            """UPDATE parts_control
                  SET build_assembly_pack_gid = ?,
                      association_status = 'linked_by_name',
                      association_checked_at = ?
                WHERE asana_task_gid = ?""",
            (r["pack_gid"], _now(), r["part_gid"]),
        )
    conn.commit()
    return len(rows)


# ---------------------------------------------------------------------------
# Part classification
# ---------------------------------------------------------------------------

def classify_part(part: sqlite3.Row) -> str:
    """ready | on_order | unordered | unknown — from what the record actually says."""
    if part["is_completed"]:
        return "ready"
    if part["arrived_qty"] and float(part["arrived_qty"] or 0) > 0:
        return "ready"

    status = (part["order_status"] or "").lower()
    if any(h in status for h in ORDER_ARRIVED_HINTS):
        return "ready"

    action = (part["stock_action"] or "").strip().lower()
    if action in READY_ACTIONS:
        return "ready"
    if action in ON_ORDER_ACTIONS:
        # An order-stage label is only trustworthy if an order task backs it.
        return "on_order" if part["procurement_task_gid"] else "unknown"
    if action in UNORDERED_ACTIONS:
        return "unordered"

    if any(h in status for h in ORDER_ONORDER_HINTS):
        return "on_order"
    if any(h in status for h in ORDER_PRE_HINTS):
        return "unordered"
    return "unknown"


def part_eta(part: sqlite3.Row, order: sqlite3.Row | None) -> datetime | None:
    """Best available arrival date for one part."""
    if order is not None:
        due = _parse_date(order["due_date"])
        if due:
            return due
        lead = order["days_for_delivery"] if "days_for_delivery" in order.keys() else None
        ordered = _parse_date(order["order_date"])
        if ordered and lead:
            return _add_working_days(ordered, int(float(lead)))
    due = _parse_date(part["due_on"])
    if due:
        return due
    # No order yet — assume the route's typical lead time from today.
    route = (part["supply_route"] or "buy")
    return _add_working_days(_today(), ROUTE_LEAD_DAYS.get(route, DEFAULT_LEAD_DAYS))


# ---------------------------------------------------------------------------
# Impact computation
# ---------------------------------------------------------------------------

def _unassigned_rows(conn, orders: dict, project: str | None, run_at: str) -> list[dict]:
    """One row per job for live parts that belong to no build pack.

    These are real supply risk — they just cannot yet be attributed to a pack, so
    they are surfaced as their own line rather than smeared across every pack.
    """
    jobs = conn.execute("""
        SELECT project_code, COUNT(*) AS n
          FROM parts_control
         WHERE COALESCE(is_completed,0) = 0
           AND (build_assembly_pack_gid IS NULL OR build_assembly_pack_gid = '')
           AND project_code IS NOT NULL
           AND (? IS NULL OR project_code = ?)
         GROUP BY project_code
    """, (project, project)).fetchall()

    out = []
    for job in jobs:
        parts = conn.execute("""
            SELECT asana_task_gid, part_name, sesame_pn, stock_action, order_status,
                   vendor_name, arrived_qty, due_on, supply_route, is_completed,
                   procurement_task_gid, project_code
              FROM parts_control
             WHERE COALESCE(is_completed,0) = 0
               AND (build_assembly_pack_gid IS NULL OR build_assembly_pack_gid = '')
               AND project_code = ?
        """, (job["project_code"],)).fetchall()
        counts = {"ready": 0, "on_order": 0, "unordered": 0, "unknown": 0}
        blockers = []
        for part in parts:
            state = classify_part(part)
            counts[state] += 1
            if state == "ready":
                continue
            eta = part_eta(part, orders.get(part["procurement_task_gid"]))
            if eta:
                blockers.append((eta, part))
        blockers.sort(key=lambda x: x[0], reverse=True)
        longest = blockers[0] if blockers else None
        outstanding = counts["on_order"] + counts["unordered"] + counts["unknown"]
        if not outstanding:
            continue
        risk = round(min(100.0, 20 + 40 * (counts["unordered"] + counts["unknown"]) / len(parts)), 1)
        out.append({
            "run_at": run_at,
            "project_code": job["project_code"],
            "pack_gid": None,
            "pack_reference": f"{job['project_code']}·UNASSIGNED",
            "pack_name": f"Parts not attributed to any build pack ({job['project_code']})",
            "production_due_on": None,
            "due_date_source": "none",
            "parts_total": len(parts),
            "parts_ready": counts["ready"],
            "parts_on_order": counts["on_order"],
            "parts_unordered": counts["unordered"],
            "parts_unknown": counts["unknown"],
            "parts_readiness": None,
            "readiness_conflict": None,
            "schedulable": 0,
            "parts_ready_eta": longest[0].strftime("%Y-%m-%d") if longest else None,
            "lasercut_state": None,
            "lasercut_eta": None,
            "longest_lead_eta": longest[0].strftime("%Y-%m-%d") if longest else None,
            "longest_lead_part": longest[1]["part_name"][:120] if longest else None,
            "longest_lead_vendor": longest[1]["vendor_name"] if longest else None,
            "blocking_part_count": outstanding,
            "risk_score": risk,
            "risk_band": "at_risk" if risk >= 40 else "watch",
            "risk_reason": (f"{outstanding} live part(s) on this job cannot be attributed "
                            f"to a build pack, so their impact is invisible to production"),
            "next_action": "Associate these parts with a build pack",
            "action_owner": "Purchasing + Design",
            "expedite_flag": 0,
        })
    return out


def compute(conn: sqlite3.Connection, project: str | None = None) -> list[dict]:
    packs = conn.execute("""
        SELECT asana_task_gid, pack_reference, pack_name, project_code, due_on,
               parts_readiness, stage_of_production, steel_profile,
               needs_from_purch, is_completed, project_name
          FROM build_assembly_packs
         WHERE COALESCE(is_completed, 0) = 0
           AND project_code IS NOT NULL
           AND (? IS NULL OR project_code = ?)
    """, (project, project)).fetchall()

    orders = {
        r["asana_task_gid"]: r
        for r in conn.execute("""
            SELECT asana_task_gid, po_number, vendor_name, due_date, order_date,
                   order_status, days_for_delivery
              FROM procurement_tasks
        """).fetchall()
    }

    run_at = _now()
    today = _today()
    out: list[dict] = []

    for pack in packs:
        # Only parts actually linked to this pack. Parts with no pack association
        # are NOT spread across every pack in the job — that would multiply the
        # same shortage into a dozen false blockers. They are reported once per
        # job as a synthetic "unassigned" row below.
        # All parts linked to the pack, completed included — a completed part is a
        # ready part, and excluding them would report every live pack as 0 ready.
        parts = conn.execute("""
            SELECT asana_task_gid, part_name, sesame_pn, stock_action, order_status,
                   vendor_name, arrived_qty, due_on, supply_route, is_completed,
                   procurement_task_gid, project_code
              FROM parts_control
             WHERE build_assembly_pack_gid = ?
        """, (pack["asana_task_gid"],)).fetchall()

        if not parts:
            continue

        counts = {"ready": 0, "on_order": 0, "unordered": 0, "unknown": 0}
        blockers: list[tuple[datetime, sqlite3.Row]] = []
        lasercut_eta: datetime | None = None
        lasercut_open = False

        for part in parts:
            state = classify_part(part)
            counts[state] += 1
            if state == "ready":
                continue
            order = orders.get(part["procurement_task_gid"])
            eta = part_eta(part, order)
            if eta:
                blockers.append((eta, part))
            if (part["supply_route"] or "") == "lasercut":
                lasercut_open = True
                if eta and (lasercut_eta is None or eta > lasercut_eta):
                    lasercut_eta = eta

        blockers.sort(key=lambda x: x[0], reverse=True)
        longest = blockers[0] if blockers else None

        # ---- lasercut state -------------------------------------------------
        # STEEL PROFILE only exists on the Build Schedule project. A Drawing
        # Control pack with no value has not "failed to assess" — the field is not
        # on its form, so it is unknown and carries no penalty.
        steel = (pack["steel_profile"] or "").strip().lower()
        has_steel_field = pack["project_name"] == "Build Schedule"
        if steel.startswith("not needed"):
            lasercut_state = "not_needed"
        elif steel.startswith("all cut"):
            lasercut_state = "all_cut"
        elif "ready to be cut" in steel:
            lasercut_state = "ready_to_cut"
        elif steel.startswith("not assessed") or (not steel and has_steel_field):
            lasercut_state = "not_assessed"
        elif not steel:
            lasercut_state = "on_order" if lasercut_open else "unknown"
        elif lasercut_open:
            lasercut_state = "on_order"
        else:
            lasercut_state = "delivered"

        # ---- readiness ------------------------------------------------------
        readiness = (pack["parts_readiness"] or "").strip()
        schedulable = 1 if readiness.lower() in READY_STATES else 0

        # ---- when will the parts actually be there --------------------------
        # No blockers means every part is already accounted for, so today.
        parts_ready_eta = longest[0] if longest else _today()

        # ---- production date, forecast if Asana has not set one -------------
        due = _parse_date(pack["due_on"])
        if due:
            production_due, source = due, "asana"
        elif schedulable:
            # Parts are there. The date is a labour decision, not a supply one.
            production_due = _add_working_days(_today(), LABOUR_QUEUE_DAYS)
            source = "forecast_labour"
        elif lasercut_eta:
            production_due = _add_working_days(lasercut_eta, LABOUR_QUEUE_DAYS)
            source = "forecast_lasercut"
        elif longest:
            production_due = _add_working_days(longest[0], LABOUR_QUEUE_DAYS)
            source = "forecast_longlead"
        else:
            production_due, source = None, "none"

        # ---- risk -----------------------------------------------------------
        total = len(parts)
        outstanding = counts["on_order"] + counts["unordered"] + counts["unknown"]
        risk = 0.0
        reasons: list[str] = []

        if counts["unordered"]:
            risk += 40 * (counts["unordered"] / total)
            reasons.append(f"{counts['unordered']} part(s) not yet ordered")
        if counts["unknown"]:
            risk += 30 * (counts["unknown"] / total)
            reasons.append(f"{counts['unknown']} part(s) in an unresolved state")
        if lasercut_state == "not_assessed":
            risk += 25
            reasons.append("steel profile not assessed — lasercut scope unknown")
        if production_due and longest and longest[0] > production_due:
            slip = (longest[0] - production_due).days
            risk += min(40, slip)
            reasons.append(f"longest-lead part lands {slip}d after the production date")
        if production_due and production_due < today and outstanding:
            risk += 30
            reasons.append("production date already passed with parts outstanding")
        if (pack["needs_from_purch"] or "").strip():
            risk += 15
            reasons.append(f"production has raised a need: {pack['needs_from_purch']}")

        # ---- readiness contradictions ---------------------------------------
        # Two states that cannot both be true. These are data faults, not supply
        # risk, and they mislead whoever reads the board — so they are named.
        conflict = None
        if schedulable and lasercut_state == "not_assessed":
            conflict = "declared ready for production, steel profile never assessed"
            risk += 20
            reasons.append(conflict)
        elif schedulable and (counts["unordered"] or counts["unknown"]):
            conflict = (f"declared ready for production with {counts['unordered']} unordered "
                        f"and {counts['unknown']} unresolved part(s)")
            risk += 25
            reasons.append(conflict)
        elif readiness.lower().startswith("kitted") and not outstanding:
            conflict = "marked parts missing, but no part is outstanding"
            reasons.append(conflict)

        risk = round(min(risk, 100.0), 1)
        band = ("blocked" if risk >= 70 else
                "at_risk" if risk >= 40 else
                "watch"   if risk >= 15 else "clear")

        # ---- next action ----------------------------------------------------
        if conflict:
            action, owner = (f"Resolve contradiction: {conflict}", "Production + Purchasing")
        elif schedulable and not pack["due_on"]:
            action, owner = ("Schedule labour — parts are ready and no date is set",
                             "Production")
        elif counts["unordered"]:
            action, owner = (f"Raise orders for {counts['unordered']} outstanding part(s)",
                             "Purchasing")
        elif lasercut_state == "not_assessed":
            action, owner = ("Assess steel profile / lasercut scope for this pack",
                             "Design + Purchasing")
        elif counts["unknown"]:
            action, owner = (f"Resolve {counts['unknown']} part(s) with no order behind the label",
                             "Purchasing")
        elif longest and production_due and longest[0] > production_due:
            action, owner = (f"Expedite {longest[1]['part_name'][:60]} "
                             f"({longest[1]['vendor_name'] or 'vendor unset'})",
                             "Purchasing")
        elif band == "clear":
            action, owner = ("None — pack is on track", None)
        else:
            action, owner = ("Monitor", "Purchasing")

        expedite = 1 if (band in ("at_risk", "blocked") and counts["unordered"] == 0) else 0

        out.append({
            "run_at": run_at,
            "project_code": pack["project_code"],
            "pack_gid": pack["asana_task_gid"],
            "pack_reference": pack["pack_reference"],
            "pack_name": pack["pack_name"],
            "production_due_on": production_due.strftime("%Y-%m-%d") if production_due else None,
            "due_date_source": source,
            "parts_total": total,
            "parts_ready": counts["ready"],
            "parts_on_order": counts["on_order"],
            "parts_unordered": counts["unordered"],
            "parts_unknown": counts["unknown"],
            "parts_readiness": readiness or None,
            "readiness_conflict": conflict,
            "schedulable": schedulable,
            "parts_ready_eta": parts_ready_eta.strftime("%Y-%m-%d") if parts_ready_eta else None,
            "lasercut_state": lasercut_state,
            "lasercut_eta": lasercut_eta.strftime("%Y-%m-%d") if lasercut_eta else None,
            "longest_lead_eta": longest[0].strftime("%Y-%m-%d") if longest else None,
            "longest_lead_part": longest[1]["part_name"][:120] if longest else None,
            "longest_lead_vendor": longest[1]["vendor_name"] if longest else None,
            "blocking_part_count": outstanding,
            "risk_score": risk,
            "risk_band": band,
            "risk_reason": "; ".join(reasons) or "no outstanding parts",
            "next_action": action,
            "action_owner": owner,
            "expedite_flag": expedite,
        })

    out.extend(_unassigned_rows(conn, orders, project, run_at))
    return out


def store(conn: sqlite3.Connection, rows: list[dict]) -> int:
    cols = list(rows[0].keys()) if rows else []
    if not cols:
        return 0
    sql = (f"INSERT INTO production_impact ({', '.join(cols)}) "
           f"VALUES ({', '.join('?' * len(cols))})")
    for r in rows:
        conn.execute(sql, [r[c] for c in cols])
    conn.commit()
    return len(rows)


def report(rows: list[dict]) -> None:
    order = {"blocked": 0, "at_risk": 1, "watch": 2, "clear": 3}
    rows = sorted(rows, key=lambda r: (order.get(r["risk_band"], 9), -r["risk_score"]))
    print(f"{'BAND':9} {'RISK':>5}  {'JOB':5} {'DUE':11} {'SRC':18} {'PARTS r/o/u/?':14} PACK")
    print("-" * 118)
    for r in rows:
        if r["risk_band"] == "clear":
            continue
        parts = f"{r['parts_ready']}/{r['parts_on_order']}/{r['parts_unordered']}/{r['parts_unknown']}"
        print(f"{r['risk_band']:9} {r['risk_score']:5.1f}  {r['project_code'] or '----':5} "
              f"{r['production_due_on'] or '-':11} {r['due_date_source']:18} {parts:14} "
              f"{(r['pack_name'] or '')[:44]}")
        print(f"{'':9} → {r['next_action']}  [{r['action_owner'] or '-'}]")
        print(f"{'':9}   {r['risk_reason']}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Compute live parts → production impact")
    ap.add_argument("--db-path", default=DB_PATH)
    ap.add_argument("--project", default=None, help="Restrict to one job number, e.g. 1597")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", action="store_true", help="Print the at-risk list")
    args = ap.parse_args()

    conn = _connect(args.db_path)
    fixed = resolve_pack_gids(conn)
    if fixed:
        print(f"Resolved {fixed} pack reference(s) from name → GID")

    rows = compute(conn, args.project)
    print(f"Computed impact for {len(rows)} live pack(s)")

    if args.report or args.dry_run:
        report(rows)
    if not args.dry_run:
        print(f"Stored {store(conn, rows)} row(s) in production_impact")
    conn.close()


if __name__ == "__main__":
    main()
