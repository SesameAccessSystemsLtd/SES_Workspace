#!/usr/bin/env python3
"""project_cycle.py — one row per project, position across the 7-stage cycle.

The OS portal answers "what is wrong right now" per pane. This answers the other
question: for every live project, HOW FAR ROUND THE CYCLE IS IT, and what is the
single next action that moves it on.

    S1 BOM received          parts exist for the project
    S2 BOM -> Stage 4        parts carry vendor/stock routing
    S3 committed to Asana    procurement (order) tasks exist for those parts
    S4 steel profile in      pack(s) carry a steel_profile marker
    S5 parts -> packs        every live part row has build_assembly_pack_gid
    S6 orders -> parts       every live part row has procurement_task_gid
    S7 cycle complete        S1-S6 green and no incomplete packs left

Read-only. Never writes ops.db, never calls Asana. Live reconcile stays the job of
centre_of_truth.py in the render layer -- this is the DB-truth skeleton it hangs on.

JOIN KEY (corrected 2026-09-17, runbook prompt 2b)
  Attribution is by `project_code`, NOT by the leading 4 digits of sesame_pn.
  Verified on 1588: prefix matching returned 538 rows vs 219 by project_code, and
  inflated "live parts without a pack" from 6 to 282 by dragging in foreign rows
  that merely share a 1588.x part number. The two sets are not nested -- prefix
  matching both over- and under-counts.
  There is NO prefix fallback. A prefix fallback was tried and removed the same
  day: it put 534 rows back on the wrong projects (323 onto 1588 alone) and held
  1588's S5 blocker at 284 against a true 6. Rows with no project_code go to the
  explicit UNATTRIB bucket, which is always displayed and never merged into a real
  project. Guessing attribution is worse than admitting we cannot attribute.

GRAIN
  parts_control is one row per part-per-operation (Anodising / Laser Cut / Marine
  Lacquer are separate rows for one part number, mirroring Asana's process tasks).
  `parts` = distinct part numbers. `rows` = process task rows. Stage gating counts
  ROWS, because a process row is the actionable Asana task.

Usage:
    python project_cycle.py --probe        # schema + row counts, verify assumptions
    python project_cycle.py --reconcile    # project_code vs prefix divergence
    python project_cycle.py --verify       # attribution balances against the DB
    python project_cycle.py --json
    python project_cycle.py --project 1597
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys

OPS_DB = r"C:/Users/GilesHugo/SES_Workspace/db/ops.db"
ASANA_ORG = "357939803556123"

TABLES = ["build_assembly_packs", "parts_control", "procurement_tasks",
          "production_impact", "suppliers"]

STAGES = [
    ("S1", "BOM received"),
    ("S2", "BOM -> Stage 4"),
    ("S3", "Committed to Asana"),
    ("S4", "Steel profile in"),
    ("S5", "Parts -> packs"),
    ("S6", "Orders -> parts"),
    ("S7", "Cycle complete"),
]

PROJ_RE = re.compile(r"^(\d{4})")
CONFIRMED_RE = re.compile(r"Delivery Date CONFIRMED|AWAITING DISPATCH", re.I)


def _cols(conn, table) -> set[str]:
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def _prefix(value) -> str | None:
    m = PROJ_RE.match(str(value or "").strip())
    return m.group(1) if m else None


UNATTRIBUTED = "UNATTRIB"


def _attribute(project_code) -> str:
    """project_code, or the explicit UNATTRIBUTED bucket. THERE IS NO FALLBACK.

    2026-09-17: a pn-prefix fallback was tried and removed the same day. It
    reintroduced the very defect the project_code join was added to fix -- 534 rows
    prefix-attributed, 323 of them onto 1588 alone, holding its S5 blocker at 284
    against a true 6. Prefix matching is wrong for three independent reasons:
      * rows with project_code but a NULL sesame_pn (182 on 1561) match nothing;
      * standards designations (ISO 4762, DIN 6799) prefix-match to nonsense;
      * a part number's leading digits are not a project key (7003.E.085 belongs
        to project 1458).
    A row we cannot attribute is shown in its own bucket. It is never guessed at
    and never silently merged into a real project's counts.
    """
    pc = str(project_code or "").strip()
    return pc if pc else UNATTRIBUTED


def probe(conn) -> dict:
    report = {}
    for t in TABLES:
        cols = _cols(conn, t)
        if not cols:
            report[t] = {"exists": False}
            continue
        try:
            n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except sqlite3.Error as e:
            n = f"ERROR: {e}"
        report[t] = {"exists": True, "rows": n, "columns": sorted(cols),
                     "has_project_code": "project_code" in cols}
    return report


def reconcile(conn) -> dict:
    """Quantify the join-key problem across ALL projects, not just 1588 --
    CLI Hermes's stated caveat on the 2026-09-17 finding."""
    pc = _cols(conn, "parts_control")
    if "project_code" not in pc:
        return {"error": "parts_control has no project_code column"}
    by_code, by_prefix, orphan, disagree = {}, {}, 0, 0
    for code, pn in conn.execute("SELECT project_code, sesame_pn FROM parts_control"):
        c = str(code or "").strip()
        p = _prefix(pn)
        if c:
            by_code[c] = by_code.get(c, 0) + 1
            if p and p != (_prefix(c) or c):
                disagree += 1
        else:
            orphan += 1
        if p:
            by_prefix[p] = by_prefix.get(p, 0) + 1
    projects = sorted(set(by_code) | set(by_prefix))
    rows = [{"project": k, "by_project_code": by_code.get(k, 0),
             "by_pn_prefix": by_prefix.get(k, 0),
             "delta": by_prefix.get(k, 0) - by_code.get(k, 0)}
            for k in projects]
    rows.sort(key=lambda r: -abs(r["delta"]))
    return {"parts_control_rows_without_project_code": orphan,
            "rows_where_project_code_disagrees_with_pn_prefix": disagree,
            "projects_with_any_divergence": sum(1 for r in rows if r["delta"]),
            "worst": rows[:25]}


def verify(conn) -> dict:
    """Self-check: recompute every project's counts with INDEPENDENT SQL and compare
    to what collect() produced. Any disagreement is a bug in collect(), full stop.

    This exists because the 2026-09-17 fallback defect was not caught by testing --
    the test fixture was built from the same assumptions as the code, so it agreed
    with itself while disagreeing with the database by 47x. A fixture cannot check a
    join key. Only the real table can.
    """
    computed = {r["project"]: r for r in collect(conn)}
    pc = _cols(conn, "parts_control")
    done_col = next((c for c in ("is_completed", "completed") if c in pc), None)
    if "project_code" not in pc:
        return {"error": "parts_control has no project_code column"}

    live = f"NOT COALESCE({done_col},0)" if done_col else "1"
    sql = f"""
        SELECT COALESCE(NULLIF(TRIM(COALESCE(project_code,'')),''), '{UNATTRIBUTED}') AS p,
               COUNT(*),
               COUNT(DISTINCT NULLIF(TRIM(COALESCE(sesame_pn,'')),'')),
               SUM(CASE WHEN {live} AND COALESCE(TRIM(build_assembly_pack_gid),'')=''
                        THEN 1 ELSE 0 END),
               SUM(CASE WHEN {live} AND COALESCE(TRIM(procurement_task_gid),'')=''
                        THEN 1 ELSE 0 END)
        FROM parts_control GROUP BY p"""

    mismatches, checked = [], 0
    for p, rows, parts, no_pack, no_order in conn.execute(sql):
        checked += 1
        got = computed.get(p)
        if not got:
            mismatches.append({"project": p, "error": "missing from collect() output"})
            continue
        for field, sql_val, got_val in (("rows", rows, got["rows"]),
                                        ("parts", parts, got["parts"]),
                                        ("rows_no_pack", no_pack or 0, got["rows_no_pack"]),
                                        ("rows_no_order", no_order or 0, got["rows_no_order"])):
            if sql_val != got_val:
                mismatches.append({"project": p, "field": field,
                                   "sql": sql_val, "collect": got_val})
    extra = sorted(set(computed) - {r[0] for r in conn.execute(sql)})
    total_sql = conn.execute("SELECT COUNT(*) FROM parts_control").fetchone()[0]
    total_got = sum(r["rows"] for r in computed.values())
    return {"projects_checked": checked,
            "parts_control_rows_in_db": total_sql,
            "parts_control_rows_attributed": total_got,
            "rows_balance": total_sql == total_got,
            "in_collect_but_not_in_sql": extra,
            "mismatches": mismatches[:50],
            "PASS": not mismatches and total_sql == total_got and not extra}


def collect(conn, only_project: str | None = None) -> list[dict]:
    pc = _cols(conn, "parts_control")
    bp = _cols(conn, "build_assembly_packs")
    pt = _cols(conn, "procurement_tasks")

    missing = [f"{t}.{c}" for c, have, t in (
        ("sesame_pn", pc, "parts_control"),
        ("build_assembly_pack_gid", pc, "parts_control"),
        ("procurement_task_gid", pc, "parts_control"),
        ("pack_reference", bp, "build_assembly_packs")) if c not in have]
    if missing:
        raise SystemExit("schema mismatch, refusing to guess: missing "
                         + ", ".join(missing) + "\nRun --probe and send me the output.")

    has_stock, has_vendor = "stock_action" in pc, "vendor_name" in pc
    has_steel, has_status = "steel_profile" in bp, "order_status" in pt
    part_done = next((c for c in ("is_completed", "completed") if c in pc), None)
    pack_done = next((c for c in ("is_completed", "completed") if c in bp), None)
    part_code = "project_code" if "project_code" in pc else None
    pack_code = "project_code" if "project_code" in bp else None

    proj: dict[str, dict] = {}

    def slot(code):
        return proj.setdefault(code, {
            "project": code, "parts": 0, "rows": 0, "rows_live": 0, "rows_done": 0,
            "rows_no_pack": 0, "rows_no_order": 0, "rows_routed": 0,
            "parts_no_pack": 0, "parts_no_order": 0, "rows_no_pn": 0,
            "packs": 0, "packs_open": 0, "packs_steel": 0,
            "orders": set(), "orders_confirmed": set(), "_pn": {},
        })

    # ---- part rows ---------------------------------------------------------
    sel = ["sesame_pn", "build_assembly_pack_gid", "procurement_task_gid"]
    for c in (has_stock and "stock_action", has_vendor and "vendor_name",
              part_done, part_code):
        if c and c not in sel:
            sel.append(c)
    for row in conn.execute(f"SELECT {', '.join(sel)} FROM parts_control"):
        r = dict(zip(sel, row))
        code = _attribute(r.get(part_code) if part_code else None)
        if only_project and code != only_project:
            continue
        s = slot(code)
        s["rows"] += 1
        if not str(r["sesame_pn"] or "").strip():
            s["rows_no_pn"] += 1
        done = bool(r.get(part_done)) if part_done else False
        pn = str(r["sesame_pn"] or "").strip()
        agg = s["_pn"].setdefault(pn, {"live": 0, "pack": False, "order": False})
        if done:
            s["rows_done"] += 1
        else:
            s["rows_live"] += 1
            agg["live"] += 1
            if r["build_assembly_pack_gid"]:
                agg["pack"] = True
            else:
                s["rows_no_pack"] += 1
            if r["procurement_task_gid"]:
                agg["order"] = True
            else:
                s["rows_no_order"] += 1
        if (r.get("stock_action") or "").strip() or (r.get("vendor_name") or "").strip():
            s["rows_routed"] += 1

    # ---- packs -------------------------------------------------------------
    sel = ["pack_reference"]
    for c in (has_steel and "steel_profile", pack_done, pack_code):
        if c and c not in sel:
            sel.append(c)
    for row in conn.execute(f"SELECT {', '.join(sel)} FROM build_assembly_packs"):
        r = dict(zip(sel, row))
        code = _attribute(r.get(pack_code) if pack_code else None)
        if only_project and code != only_project:
            continue
        s = slot(code)
        s["packs"] += 1
        if not pack_done or not r.get(pack_done):
            s["packs_open"] += 1
        if has_steel and str(r.get("steel_profile") or "").strip():
            s["packs_steel"] += 1

    # ---- orders ------------------------------------------------------------
    order_status = {}
    if has_status:
        for gid, st in conn.execute("SELECT asana_task_gid, order_status FROM procurement_tasks"):
            order_status[str(gid)] = st or ""

    sel = ["sesame_pn", "procurement_task_gid"] + ([part_code] if part_code else [])
    for row in conn.execute(
            f"SELECT {', '.join(sel)} FROM parts_control "
            "WHERE procurement_task_gid IS NOT NULL AND procurement_task_gid != ''"):
        r = dict(zip(sel, row))
        code = _attribute(r.get(part_code) if part_code else None)
        if only_project and code != only_project:
            continue
        s = slot(code)
        gid = str(r["procurement_task_gid"])
        s["orders"].add(gid)
        if CONFIRMED_RE.search(order_status.get(gid, "")):
            s["orders_confirmed"].add(gid)

    return [_finalise(s) for s in proj.values()]


def _finalise(s: dict) -> dict:
    s["orders"] = len(s["orders"])
    s["orders_confirmed"] = len(s["orders_confirmed"])
    pn = s.pop("_pn")
    s["parts"] = len(pn)
    s["parts_live"] = sum(1 for v in pn.values() if v["live"])
    s["parts_no_pack"] = sum(1 for v in pn.values() if v["live"] and not v["pack"])
    s["parts_no_order"] = sum(1 for v in pn.values() if v["live"] and not v["order"])

    if s["project"] == UNATTRIBUTED:
        s["stages"] = {k: "nodata" for k, _ in STAGES}
        s["evidence"] = {k: "unattributed rows — no project_code" for k, _ in STAGES}
        s["next"] = {"stage": "-", "action":
                     f"{s['rows']} part rows and {s['packs']} packs carry no project_code "
                     f"— attribute them or they are invisible to every project view"}
        s["reached"], s["unassessable"], s["report_ready"] = 0, len(STAGES), False
        return s

    st, why, nxt = {}, {}, None

    # THREE states. "nodata" is the state a stage is in when there is nothing to
    # judge it by. Rendering those as done hides work; rendering them as open
    # invents work. (Defect A, 2026-09-17 gate.)
    def mark(key, ok, reason, action, assessable=True):
        nonlocal nxt
        st[key] = ("done" if ok else "open") if assessable else "nodata"
        why[key] = reason if assessable else "no data to assess this stage"
        if assessable and not ok and nxt is None:
            nxt = {"stage": key, "action": action}

    has_parts, has_packs = s["rows"] > 0, s["packs"] > 0

    mark("S1", has_parts,
         f"{s['parts']} parts / {s['rows']} process rows",
         "No BOM imported for this project — chase Karol / check the Handover task")
    mark("S2", has_parts and s["rows_routed"] == s["rows"],
         f"{s['rows_routed']}/{s['rows']} rows routed",
         f"{s['rows'] - s['rows_routed']} rows carry no vendor/stock routing — re-run BOM push to Stage 4",
         assessable=has_parts)
    mark("S3", s["orders"] > 0,
         f"{s['orders']} order tasks ({s['orders_confirmed']} confirmed)",
         "Stage 4 not committed to Asana — approve the commit",
         assessable=has_parts)
    # S4 reads the steel_profile TEXT column, NOT stage_of_production (NULL for
    # ~94% of packs). Green = marker present, not that the steel BOM imported clean.
    mark("S4", s["packs_steel"] > 0,
         f"{s['packs_steel']}/{s['packs']} packs carry steel profile",
         "Steel Profile BOM not imported — run the steel follow-up",
         assessable=has_packs)
    mark("S5", s["rows_no_pack"] == 0,
         f"{s['rows_no_pack']} live rows without a pack ({s['parts_no_pack']} parts)",
         f"{s['rows_no_pack']} rows unmapped — run the drawing pipeline (pack assembly ingress)",
         assessable=has_parts)
    mark("S6", s["rows_no_order"] == 0,
         f"{s['rows_no_order']} live rows without an order ({s['parts_no_order']} parts)",
         f"{s['rows_no_order']} rows unordered — raise orders / reconcile",
         assessable=has_parts)
    mark("S7", all(st[k] == "done" for k in ("S1", "S2", "S3", "S4", "S5", "S6"))
         and s["packs_open"] == 0,
         f"{s['packs_open']} packs still open",
         f"{s['packs_open']} packs still open — close them out",
         assessable=has_parts and has_packs)

    # A closed cycle is not "nothing to do" -- it is the one row where the report is
    # worth pushing. Without this, every fully-green project rendered "—".
    s["report_ready"] = st.get("S7") == "done"
    if s["report_ready"] and nxt is None:
        nxt = {"stage": "S7", "action": "▶ Push the full project report to Asana"}

    s["stages"], s["evidence"], s["next"] = st, why, nxt
    s["reached"] = sum(1 for k, _ in STAGES if st.get(k) == "done")
    s["unassessable"] = sum(1 for k, _ in STAGES if st.get(k) == "nodata")
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=OPS_DB)
    ap.add_argument("--project")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--reconcile", action="store_true")
    ap.add_argument("--verify", action="store_true",
                    help="recompute every count with independent SQL and compare")
    args = ap.parse_args()

    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    try:
        if args.probe:
            print(json.dumps(probe(conn), indent=2))
            return
        if args.reconcile:
            print(json.dumps(reconcile(conn), indent=2))
            return
        if args.verify:
            res = verify(conn)
            print(json.dumps(res, indent=2))
            sys.exit(0 if res.get("PASS") else 1)
            return
        rows = collect(conn, args.project)
    finally:
        conn.close()

    rows.sort(key=lambda r: (r["project"] != UNATTRIBUTED, r["reached"], r["project"]))

    if args.json:
        print(json.dumps(rows, indent=2))
        return

    glyph = {"done": "#", "open": ".", "nodata": "?"}
    print(f"{'proj':9} {'cycle':10} {'parts':>6} {'rows':>6} {'noPN':>5} {'noPack':>7} "
          f"{'noOrd':>6} {'packs':>6} {'orders':>7}  next action")
    print("-" * 124)
    for r in rows:
        bar = "".join(glyph[r["stages"][k]] for k, _ in STAGES)
        nxt = r["next"]["action"] if r["next"] else "—"
        print(f"{r['project']:9} {bar:10} {r['parts']:>6} {r['rows']:>6} "
              f"{r['rows_no_pn']:>5} {r['rows_no_pack']:>7} {r['rows_no_order']:>6} "
              f"{r['packs']:>6} {r['orders']:>7}  {nxt[:50]}")
    ready = sum(1 for r in rows if r.get("report_ready"))
    print(f"\n{len(rows)} rows (incl. the UNATTRIB bucket).  # = done, . = open, "
          f"? = no data to assess.  {ready} ready for a report push.")
    print("parts = distinct part numbers · rows = process task rows (gating counts rows)")
    print("noPN = rows with a project_code but no part number — real work, no part id.")
    print("UNATTRIB = rows/packs with no project_code. Never guessed at, never merged.")
    print("Stages: " + " ".join(f"{k}={n}" for k, n in STAGES))


if __name__ == "__main__":
    main()
