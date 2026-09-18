#!/usr/bin/env python3
"""build_orders_by_pack.py — SAS OS dashboard: "Orders by Pack" pane.

READ-ONLY. Seeds by BUILD PACK (incomplete only) and shows what is impacting
each build, so Purchasing can see, per pack:

  * the current build due date (the clock we measure against)
  * the build pack STEEL PROFILE custom-field status
  * the build pack readiness rollup (Ready for production / Kitted, parts
    missing / Waiting delivery / Not started)
  * the parts that feed the pack (linked via parts_control.build_assembly_pack_gid)
  * their orders: order due-in (procurement_tasks.due_date = DELIVERY date,
    Trap #1), order status, and confirmation state
  * the parts still OUTSTANDING for the pack (no order link, stock_action not
    landed)
  * the derived next action: draft confirmation e-mail / chase / at-risk / clear
  * risk band + next action + owner reused (NOT recomputed) from production_impact

SEEDING RULE: only incomplete packs (build_assembly_packs.is_completed = 0).
Completed packs are never shown.

PART STATE vs ORDER STATE (Giles, 2026-09-11) — the load-bearing distinction:
  * `stock_action` (parts_control) describes THE STATE OF THE PART and is
    AUTHORITATIVE for whether it is available. KITTED / ITEM On Premises /
    IN STOCK / INHOUSE / PLEASE PICK / SUBCON TASKS => the part is present.
  * `order_status` (procurement_tasks) describes THE ORDER and only supplies
    order-side detail (quote/confirm/chase/incoming).
  Completed tasks often leave order_status un-updated (still 'Quote / Order'),
  so order_status alone would show delivered stock as "to be ordered". Parts
  are therefore classified stock_action-FIRST; the order is only consulted for
  parts that are past the ordering stage. QC ISSUE is a part-level blocker and
  is held outstanding even though the part is physically present.

GROUPING (Giles, 2026-09-11): packs are grouped into per-PROJECT sections,
where the project = the FIRST 4 DIGITS of the pack reference (e.g.
'1575.1001.A.Stairs.' -> project '1575'). The project_code column is only
populated on ~1/3 of packs, so the ref is the reliable source. Within each
project the packs are ordered by BUILD DUE DATE, soonest first -- the most
urgent build is at the top of the group, a pushed-back date falls to the
bottom. Projects themselves are ordered by their earliest build due date.

PRESENTATION: every pack card renders COLLAPSED. The summary line carries the
nudge verdict (see below) so the page is scannable without expanding; an
"Expand all / Collapse all" button flips every card at once.

NUDGE VERDICT (the summary line): "⚠ NUDGE +Nd" when an ORDER feeding the pack
is due to land AFTER the build date, so the build would need to move by N days.
This is computed from the ORDER due dates ONLY -- never the part's own date.
Packs with work outstanding but no order-driven date slip show "chase — no date
slip"; clean packs show "✓ on track".

NUDGE ACTION: the "Nudge build" link does NOT write to Asana. SAP is the only
Asana writer (hard rule). The link targets a local REQUESTS-draft generator
(`--nudge <pack_ref>`), which drops a draft into queues/REQUESTS for SAP to
drain. Nothing is posted without Giles approving the drain.

Governance: reads ops.db read-only. Writes NOTHING to ops.db or Asana.
Any nudge draft is a queues/REQUESTS file (the sanctioned write path).

Output: data/os_status/orders-by-pack.html (+ dated copy).

Usage:
  python build_orders_by_pack.py [--out DIR]
  python build_orders_by_pack.py --nudge 1575.1008.Q1.DoubleLiftingJig --order 70926
"""
from __future__ import annotations

import argparse
import datetime
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

WS = Path(r"C:/Users/GilesHugo/SES_Workspace")
OPS_DB = r"C:/Users/GilesHugo/SES_Workspace/db/ops.db"
# Asana deep-link template (Giles, 2026-09-11): the working URL carries the
# workspace/org segment, e.g.
#     https://app.asana.com/1/357939803556123/task/1218240884022851
# A bare https://app.asana.com/0/<task_gid> does NOT resolve, which is why the
# order/part links 404'd. ASANA_BASE ends with the org id, and callers append
# /task/<gid>.
ASANA_ORG = "357939803556123"
ASANA_BASE = f"https://app.asana.com/1/{ASANA_ORG}/task"

sys.path.insert(0, str(WS / "skills" / "os_portal"))
try:
    from build_os_status import esc  # reuse the shared escaper
except Exception:
    def esc(x):
        return (str(x).replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))


def asana_task_link(gid, label=None):
    """Canonical Asana deep link: /1/<org>/task/<gid>.

    NOTE: deliberately NOT imported from build_os_status -- that helper emits
    the legacy '/0/<project_gid>/<gid>' form, which does not resolve. The
    working template (Giles, 2026-09-11) is
        https://app.asana.com/1/357939803556123/task/<gid>
    """
    return f'<a href="{ASANA_BASE}/{gid}" target="_blank">{esc(label or gid)}</a>'


# ── normalisation (Trap #2: pack column holds NAMES, not GIDs) ───────────────
def _norm_pack(ref: str | None) -> str:
    """Normalise a pack reference/name for name-based matching.

    '1037.1016.BS.2.' -> '1037.1016.bs.2'.  Trap #2: build_assembly_pack_gid
    holds the referenced task's NAME; store honestly, match by name, never
    guess a GID.
    """
    return (ref or "").strip().lower().rstrip(".")


def _norm_pn(pn: str | None) -> str:
    return (pn or "").strip().lower().rstrip(".")


# ── status vocabularies (confirmed against live ops.db values) ───────────────
# order_status: landed / satisfied -> do NOT chase.
_LANDED_ORDER = re.compile(
    r"ALL PICKED|LINE ITEM DELIVERED|ARRIVED - MOVES TO INVOICES|INHOUSE|CANCELLED|TO BE RETURNED|HOLD"
)
# landed but NOT usable yet -- needs QC before it can go to the build.
_QC_HOLD = re.compile(r"ARRIVED - NEED TO QC", re.I)
# ordered, but the DEPENDENT parts are NOT order tasks (laser-cut pattern):
# the order lives on an ancestor task, not on the part's own parent.
_ORDERED_VIA_ANCESTOR = re.compile(r"dependents NOT ORDER", re.I)
_AWAIT_CONFIRM = re.compile(r"Awaiting CONFIRMATION", re.I)
_CONFIRMED = re.compile(r"Delivery Date CONFIRMED|AWAITING DISPATCH", re.I)
_TO_ORDER = re.compile(r"Quote / Order|To be ordered|Requires attention|TO BE ASSESSED|Requires Price", re.I)

# ── THE PART-vs-ORDER DISTINCTION (Giles, 2026-09-11) ───────────────────────
# `stock_action` describes the STATE OF THE PART and is AUTHORITATIVE for
# whether the part is available. `order_status` describes the ORDER and only
# supplies order-side detail. A part can be KITTED (physically on site, ready)
# while its order task still reads "Quote / Order" because the order was never
# closed out after delivery. stock_action must win; if we let order_status win
# the part shows as "to be ordered" when it is sitting on the shelf.
#
# On site / satisfied -> the part is present, not outstanding:
#   KITTED, ITEM On Premises, IN STOCK, INHOUSE  (received)
#   PLEASE PICK, SUBCON TASKS                    (picked / at subcon, also past ordering)
_ON_SITE_STOCK = re.compile(
    r"KITTED|ITEM On\s*Premises|IN STOCK|INHOUSE|PLEASE PICK|SUBCON TASKS", re.I)
# Physically present but BLOCKED -- cannot go to the build yet:
_ON_SITE_BLOCKED = re.compile(r"QC ISSUE", re.I)
# Past the ordering stage: the part's state is now the ORDER's responsibility.
# (_ORDERED_VIA_ANCESTOR covers lasecut; PROJECT ORDER is the same idea.)
# Still to be bought for this pack:
_TO_ORDER_STOCK = re.compile(r"NEED TO ORDER|ORDER FOR STOCK|HOLD ORDER", re.I)


def _fmt_date(d) -> str:
    s = (d or "").strip()
    return s[:10] if s else "—"


def _days_to(due, today: datetime.date):
    """Signed days from today to `due` (negative = overdue). None if no date."""
    s = (due or "").strip()[:10]
    if not s:
        return None
    try:
        d = datetime.date.fromisoformat(s)
    except ValueError:
        return None
    return (d - today).days


# ── data load ───────────────────────────────────────────────────────────────
def load_incomplete_packs(conn) -> list[dict]:
    """Seed: ONLY incomplete packs. Ordered by due_on (nulls last), then ref."""
    sql = """
        SELECT asana_task_gid, pack_reference, pack_name, due_on, steel_profile,
               parts_readiness, stage_of_production, needs_from_purch,
               project_code, is_completed
        FROM build_assembly_packs
        WHERE COALESCE(is_completed, 0) = 0
        ORDER BY (due_on IS NULL OR due_on = ''), due_on, pack_reference
    """
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(sql)]


def load_parts_by_pack(conn) -> dict[str, list[dict]]:
    """{pack Asana task GID: [part rows]} from parts_control.

    Parts link to a pack by the pack's Asana task GID held in
    build_assembly_pack_gid. (Trap #2 has been fixed: the column now holds
    NUMERIC GIDs -- 1,520/1,520 verified -- not names. The legacy name form is
    still handled by _norm_pack so pre-fix rows degrade gracefully.)

    Join key therefore = the GID string, matched to build_assembly_packs.
    asana_task_gid. Rows with no pack link cannot appear against a build
    (accepted: they belong to the separate unassociated improvement case).
    """
    sql = """
        SELECT asana_task_gid, sesame_pn, part_name, stock_action, vendor_name,
               order_status, procurement_task_gid, build_assembly_pack_gid,
               build_pack_ref_name, due_on, arrived_qty, is_completed
        FROM parts_control
        WHERE build_assembly_pack_gid IS NOT NULL
          AND build_assembly_pack_gid != ''
          AND (supply_route IS NOT NULL)          -- Trap #7: drop stale ghosts
    """
    conn.row_factory = sqlite3.Row
    out: dict[str, list[dict]] = defaultdict(list)
    for r in conn.execute(sql):
        out[_norm_pack(r["build_assembly_pack_gid"])].append(dict(r))
    return out


def load_orders_by_part(conn) -> dict[str, dict]:
    """{procurement_task_gid: order row} for order due-in + confirmation state.

    procurement_tasks.due_date IS the DELIVERY date (Trap #1, canary-guarded).
    """
    sql = """
        SELECT asana_task_gid, task_name, po_number, order_status, due_date,
               order_date, vendor_name, days_for_delivery, is_completed
        FROM procurement_tasks
    """
    conn.row_factory = sqlite3.Row
    return {r["asana_task_gid"]: dict(r) for r in conn.execute(sql)}


def load_impact(conn) -> dict[str, dict]:
    """Latest production_impact row per pack (reuse, do NOT recompute)."""
    sql = """
        SELECT pack_reference, risk_band, risk_score, risk_reason, next_action,
               action_owner, production_due_on, due_date_source,
               parts_total, parts_ready, parts_on_order, parts_unordered,
               longest_lead_eta, longest_lead_part, blocking_part_count,
               run_at
        FROM production_impact
        ORDER BY run_at
    """
    conn.row_factory = sqlite3.Row
    latest: dict[str, dict] = {}
    for r in conn.execute(sql):
        latest[_norm_pack(r["pack_reference"])] = dict(r)
    return latest


def load_supplier_email(conn) -> dict[str, str]:
    sql = "SELECT vendor_name, contact_email FROM suppliers"
    conn.row_factory = sqlite3.Row
    out = {}
    for r in conn.execute(sql):
        k = (r["vendor_name"] or "").strip().lower()
        if k:
            out[k] = (r["contact_email"] or "").strip()
    return out


# ── per-pack derivation ─────────────────────────────────────────────────────
LANDED = "landed"
OUTSTANDING = "outstanding"


def effective_due(part_due, order: dict | None, hier: dict | None = None):
    """Resolve the ORDER due-in, preferring the ORDER TASK's LIVE date.

    Giles (2026-09-11): a part's own due date is often NOT maintained, while the
    ORDER task's due date IS kept current on confirmation. Live check of 1,112
    linked parts: 408 had an order date but no part date, and 134 had both set
    to DIFFERENT values -- i.e. in 542/1112 (49%) the order task holds the truth.

    ALSO: the ops.db mirror of the order task's date is itself stale -- sampled
    40 order tasks, 21 (52%) had a DB due_date differing from live Asana, usually
    the delivery slipping LATER. So the precedence is:

        1. the ORDER task's LIVE due date (from the hierarchy resolve)
        2. the DB mirror of the order task's due date
        3. the part's own due_on

    Returns (iso_date, source) where source is 'order-live' | 'order-db' |
    'part' | ''.
    """
    hd = ((hier or {}).get("order_due") or "").strip()
    if hd:
        return hd[:10], "order-live"
    od = ((order or {}).get("due_date") or "").strip()
    if od:
        return od[:10], "order-db"
    pd = (part_due or "").strip()
    if pd:
        return pd[:10], "part"
    return "", ""


def classify_part(p: dict, order: dict | None) -> dict:
    """Return the part with a resolved bucket + next action.

    PRECEDENCE (Giles, 2026-09-11):
      0. the part task is COMPLETE in Asana  -> LANDED (nothing further to do)
      1. `stock_action` describes the PART and is checked next
      2. `order_status` describes the ORDER and is only consulted when neither
         of the above settles the part's state

    Rule 0 (Giles, confirmed 2026-09-11): "any completed part task is landed."
    A ticked part task means the line is done -- even if `stock_action` was
    never refreshed off 'On order / PROJECT ORDER / Lasercut'. Without this,
    362 completed parts (mostly laser-cut) kept showing as outstanding on
    packs that were in fact finished. QC ISSUE on a COMPLETED line is also
    treated as landed: the task being closed means any QC was resolved.

    Rule 1 fixes the earlier bug where a delivered, KITTED part whose order
    task was never closed out (order_status still 'Quote / Order') showed as
    "Chase quote/order" -- stock_action is the truth about the part.

    Order of tests:
      0. part task complete                          -> LANDED
      1. stock_action says on site / picked / subcon -> LANDED
      2. stock_action says QC ISSUE                  -> outstanding (QC)
      3. stock_action says still to be bought        -> outstanding (order it)
      4. otherwise: stock_action past ordering (or blank) -> defer to the
         ORDER: lasercut/ancestor, raise, confirm, chase, incoming
    """
    stock = (p.get("stock_action") or "").strip()
    ostatus = (order or {}).get("order_status") or p.get("order_status") or ""
    ostatus = ostatus.strip()

    has_order = bool(p.get("procurement_task_gid"))
    is_complete = p.get("is_completed") == 1

    # ── 0. the part task is ticked -> the line is DONE ──
    if is_complete:
        bucket, action = LANDED, "—"
    # ── 1. the PART is present -> landed, whatever the order says ──
    elif _ON_SITE_STOCK.search(stock):
        bucket, action = LANDED, "—"
    # ── 2. present but QC-blocked -> cannot go to the build yet ──
    elif _ON_SITE_BLOCKED.search(stock) or _QC_HOLD.search(ostatus):
        bucket, action = OUTSTANDING, "QC outstanding"
    # ── 3. stock_action says the part still has to be bought ──
    elif _TO_ORDER_STOCK.search(stock):
        bucket, action = OUTSTANDING, "Raise order"
    # ── 4. past the ordering stage -> the ORDER carries the state now ──
    elif _ORDERED_VIA_ANCESTOR.search(ostatus) or _ORDERED_VIA_ANCESTOR.search(stock):
        # ordered, but the order sits on an ANCESTOR task (laser-cut pattern).
        # The part's own parent is a vendor group, so the true order is above.
        bucket, action = OUTSTANDING, "Confirm order (via ancestor)"
    elif not has_order:
        bucket, action = OUTSTANDING, "Raise order"
    elif _AWAIT_CONFIRM.search(ostatus):
        bucket, action = OUTSTANDING, "Draft confirmation e-mail"
    elif _TO_ORDER.search(ostatus):
        bucket, action = OUTSTANDING, "Chase quote/order"
    elif _CONFIRMED.search(ostatus):
        bucket, action = OUTSTANDING, "Incoming — monitor ETA"
    elif _LANDED_ORDER.search(ostatus):
        # order reads as satisfied but the part is NOT marked on site -- the
        # order landed (or was cancelled) yet nothing shows the part available.
        # Do not silently call it clear; flag it for a human look.
        bucket, action = OUTSTANDING, "Review — order satisfied, part not on site"
    else:
        bucket, action = OUTSTANDING, "Review"

    return {**p, "_order": order, "_bucket": bucket, "_action": action,
            "_ostatus": ostatus, "_stock": stock}


def readiness_rollup(parts: list[dict]) -> tuple[str, str]:
    """(label, css_class) — Ready for production / Kitted parts missing /
    Waiting delivery / Not started. Derived from the classified parts."""
    if not parts:
        return "No linked parts", "muted"
    outstanding = [p for p in parts if p["_bucket"] == OUTSTANDING]
    if not outstanding:
        return "Ready for production", "good"
    waiting = [p for p in outstanding
               if p["_order"] and (_CONFIRMED.search(p["_ostatus"]) or p["_order"].get("due_date"))]
    to_order = [p for p in outstanding if not p["_order"]]
    if waiting and not to_order:
        return "Waiting delivery", "warn"
    if to_order:
        return "Kitted, parts missing", "bad"
    return "In progress", "warn"


def pack_next_action(parts: list[dict]) -> tuple[str, str]:
    """(action, severity) for the pack as a whole."""
    acts = [p["_action"] for p in parts if p["_action"] != "—"]
    if not acts:
        return "Clear", "good"
    for want, sev in (("Draft confirmation e-mail", "bad"),
                      ("Raise order", "bad"),
                      ("Confirm order (via ancestor)", "bad"),
                      ("QC outstanding", "warn"),
                      ("Chase quote/order", "warn"),
                      ("Review", "warn"),
                      ("Incoming — monitor ETA", "warn")):
        if want in acts:
            return want, sev
    return "Clear", "good"


def impact_rollup(impact: dict) -> tuple[str, str, str]:
    """Fallback rollup FROM the impact row when no parts could be joined.

    Returns (readiness_label, action_label, severity). This is the honest state
    for a pack the impact engine scored but whose part rows did not join --
    never report 'Clear' just because the join was empty.
    """
    total = impact.get("parts_total") or 0
    ready = impact.get("parts_ready") or 0
    unordered = impact.get("parts_unordered") or 0
    on_order = impact.get("parts_on_order") or 0
    band = (impact.get("risk_band") or "").strip().lower()

    if total == 0:
        return ("No parts on pack record", impact.get("next_action") or "Assess", "warn")
    if ready >= total:
        return (f"Ready for production ({ready}/{total})",
                impact.get("next_action") or "Clear", "good")
    if unordered:
        return (f"Kitted, parts missing ({ready}/{total})",
                impact.get("next_action") or f"Raise {unordered} order(s)", "bad")
    if on_order:
        return (f"Waiting delivery ({ready}/{total})",
                impact.get("next_action") or f"{on_order} on order", "warn")
    sev = {"blocked": "bad", "at_risk": "bad", "watch": "warn"}.get(band, "warn")
    return (f"Parts outstanding ({ready}/{total})",
            impact.get("next_action") or "Review", sev)


# ── Asana task hierarchy (for lasercut + click-through) ──────────────────────
_LASER_VENDOR = re.compile(r"laser\s*cut|lasercut|lasershape|nbss", re.I)
# an ORDER task name starts with a 5-digit (or 4) PO prefix followed by '-'
_ORDER_NAME = re.compile(r"^\d{4,6}-\d")


def _asana_get(path, params):
    """Live Asana GET bypassing the epoch cache (hierarchy is small + volatile).

    Returns None on ANY failure -- the pane degrades to DB-only rather than
    crashing (centre-of-truth doctrine).
    """
    try:
        import requests
        from build_os_status import API_BASE, headers
        r = requests.get(f"{API_BASE}{path}", headers=headers(), params=params, timeout=20)
        r.raise_for_status()
        return r.json().get("data")
    except Exception:
        return None


def resolve_subtask_count(pack_gid: str) -> int:
    """Whether a pack has ANY subtasks (1 = yes, 0 = none, -1 = unknown).

    Giles (2026-09-11): build-schedule tasks with NO subtasks are not real
    build packs -- they are holiday requests, QC copies, drawing control
    headings, 'Working from home' -- and must NOT appear on the dashboard.

    We only need the boolean "does it have any subtasks", so we fetch a SINGLE
    subtask page (limit 1) and report presence, not a true count. (Reporting a
    count from a limit-1 page would under-report -- e.g. a pack with 11
    subtasks would be recorded as 1.)

    A failure returns -1 (unknown) so the caller KEEPS the pack rather than
    hiding it because of a network blip.
    """
    if not pack_gid:
        return -1
    d = _asana_get(f"/tasks/{pack_gid}/subtasks", {"opt_fields": "gid", "limit": 1})
    if d is None:
        return -1
    return 1 if len(d) > 0 else 0


def build_subtask_index(pack_gids: list[str]) -> dict:
    """{pack gid: 1|0} — has-subtasks flag, resolved LIVE + concurrently.

    Cached to disk so repeat runs are instant. A pack absent from the result is
    treated as unknown (-1) and kept visible -- never hide a pack because a
    network call failed.
    """
    import json as _json
    from concurrent.futures import ThreadPoolExecutor

    cache_path = WS / "data" / "os_status" / ".subtask_cache.json"
    cache = {}
    try:
        if cache_path.exists():
            cache = _json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        cache = {}

    todo = [g for g in pack_gids if g and g not in cache]
    if todo:
        print(f"[orders-by-pack] checking subtasks for {len(todo)} pack tasks "
              f"({len(pack_gids) - len(todo)} cached) ...")
        with ThreadPoolExecutor(max_workers=12) as ex:
            for gid, n in zip(todo, ex.map(resolve_subtask_count, todo)):
                if n >= 0:                    # only cache a real answer
                    cache[gid] = n
        try:
            cache_path.write_text(_json.dumps(cache), encoding="utf-8")
        except Exception:
            pass

    return {g: cache.get(g, -1) for g in pack_gids}


def resolve_pack_completed(pack_gid: str):
    """LIVE completion flag for a build pack task: True / False / None (unknown).

    The DB seed (`build_assembly_packs.is_completed`) is a NIGHTLY mirror snapshot,
    so a pack completed since the last 01:00 sync still reads as incomplete and
    leaks onto the page (Giles, 2026-09-11: a completed task was still showing).
    We therefore confirm completion LIVE. None means the lookup failed -> keep the
    pack (never hide a pack because a network call failed).
    """
    if not pack_gid:
        return None
    try:
        d = _asana_get(f"/tasks/{pack_gid}", {"opt_fields": "completed"})
        if isinstance(d, dict) and "completed" in d:
            return bool(d.get("completed"))
    except Exception:
        pass
    return None


def build_completed_index(pack_gids: list[str]) -> dict:
    """{pack gid: True/False} — live completion, resolved concurrently + disk-cached.

    A pack absent from the result is UNKNOWN and is kept visible. Cache is short-lived
    in spirit: the daily flow rebuilds the page, and a completed pack leaving the page
    a few hours late is acceptable; a pack shown wrongly as 'open' is what we fix.
    """
    import json as _json
    from concurrent.futures import ThreadPoolExecutor

    cache_path = WS / "data" / "os_status" / ".pack_completed_cache.json"
    cache = {}
    try:
        if cache_path.exists():
            cache = _json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        cache = {}

    todo = [g for g in pack_gids if g and g not in cache]
    if todo:
        print(f"[orders-by-pack] live completeness check for {len(todo)} pack task(s) "
              f"({len(pack_gids) - len(todo)} cached) ...")
        with ThreadPoolExecutor(max_workers=12) as ex:
            for gid, done in zip(todo, ex.map(resolve_pack_completed, todo)):
                if done is not None:          # only cache a real answer
                    cache[gid] = done
        try:
            cache_path.write_text(_json.dumps(cache), encoding="utf-8")
        except Exception:
            pass

    return {g: cache[g] for g in pack_gids if g in cache}


def resolve_part_hierarchy(part_gid: str) -> dict:
    """Resolve a part task's parent + grandparent + the ORDER's live due date.

    Returns {parent_gid, parent_name, grand_gid, grand_name, order_gid,
    order_name, order_due, is_lasercut}.

    THE LASERCUT RULE (verified live 2026-09-11): for a laser-cut part the
    ORDER is the GRANDPARENT, not the parent --
        part  "6 off 1575.6000.A.070.01 - Rubber buffer plate Laser Cut"
          parent      "Laser Cut - 1575 Jenners"      (vendor group, NOT an order)
            grandparent "20978-1575 - Lasershape ..." (THE ORDER, PO 20978)
    For a normal part the parent IS the order --
        part  "1 off 1569.6000.A.37 MayTec assembly"
          parent  "20705-1569 - MayTec - ..."          (THE ORDER, PO 20705)

    Discriminator: is the parent's name an ORDER (starts with a PO pattern)?
    If not, walk up one more level and take the grandparent as the order.

    ORDER DUE DATE (Giles, 2026-09-11): the order task's due date is the one to
    trust -- the DB mirror is stale for ~52% of orders (sampled 40: 21 differed,
    usually the delivery slipping later). So fetch the ORDER task's `due_on`
    LIVE and carry it here.
    """
    out = {"parent_gid": None, "parent_name": None, "grand_gid": None,
           "grand_name": None, "order_gid": None, "order_name": None,
           "order_due": None, "is_lasercut": False}
    if not part_gid:
        return out
    d = _asana_get(f"/tasks/{part_gid}", {
        "opt_fields": "name,parent.gid,parent.name,parent.parent.gid,parent.parent.name"})
    if not d:
        return out
    p = d.get("parent") or {}
    pp = p.get("parent") or {}
    out["parent_gid"], out["parent_name"] = p.get("gid"), p.get("name")
    out["grand_gid"], out["grand_name"] = pp.get("gid"), pp.get("name")

    pname = (p.get("name") or "")
    gname = (pp.get("name") or "")
    # if the parent looks like a vendor group (no PO prefix), the order is above it
    if p.get("gid") and not _ORDER_NAME.match(pname):
        out["is_lasercut"] = bool(_LASER_VENDOR.search(pname) or _LASER_VENDOR.search(d.get("name") or ""))
        out["order_gid"], out["order_name"] = pp.get("gid"), gname
    else:
        out["order_gid"], out["order_name"] = p.get("gid"), pname

    # live due date of the ORDER task (the date to trust)
    if out["order_gid"]:
        od = _asana_get(f"/tasks/{out['order_gid']}", {"opt_fields": "due_on,completed"})
        if od:
            out["order_due"] = (od.get("due_on") or "").strip() or None
    return out


def build_hierarchy_index(db_part_rows: list[dict], limit: int = 0) -> dict:
    """{part task gid: hierarchy dict} for the parts we are about to render.

    Bounded live Asana fan-out (one GET per distinct part task), resolved
    CONCURRENTLY (thread pool) and CACHED TO DISK so repeat runs are instant.

    Only parts that HAVE a procurement link are resolved -- unlinked parts need
    no order lookup. On any failure the part degrades to {} (pane shows the
    parent task, unchanged behaviour).
    """
    import json as _json
    from concurrent.futures import ThreadPoolExecutor

    cache_path = WS / "data" / "os_status" / ".hier_cache.json"
    cache = {}
    try:
        if cache_path.exists():
            cache = _json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        cache = {}

    # distinct part tasks with an order link
    gids = []
    seen = set()
    for x in db_part_rows:
        gid = x.get("asana_task_gid")
        if gid and gid not in seen and x.get("procurement_task_gid"):
            seen.add(gid)
            gids.append(gid)
    if limit:
        gids = gids[:limit]

    todo = [g for g in gids if g not in cache or not cache[g].get("_v3")]
    if todo:
        print(f"[orders-by-pack] resolving {len(todo)} part hierarchies "
              f"({len(gids) - len(todo)} cached) ...")
        with ThreadPoolExecutor(max_workers=12) as ex:
            for gid, res in zip(todo, ex.map(resolve_part_hierarchy, todo)):
                res["_v3"] = True
                cache[gid] = res
        try:
            cache_path.write_text(_json.dumps(cache), encoding="utf-8")
        except Exception:
            pass

    return {g: cache.get(g, {}) for g in gids}


def part_link(x: dict, label: str) -> str:
    """Click-through to the part's own Asana task (amend without copy-paste)."""
    gid = x.get("asana_task_gid")
    if not gid:
        return esc(label)
    return f'<a href="{ASANA_BASE}/{gid}" target="_blank" title="open the part task in Asana">{esc(label)}</a>'


# ── HTML ────────────────────────────────────────────────────────────────────
SEV_PILL = {"good": "background:#052e16;color:#4ade80",
            "warn": "background:#3b2f05;color:#fbbf24",
            "bad": "background:#3b1616;color:#f87171",
            "muted": "background:#1e293b;color:#94a3b8"}


def pill(text, sev="muted"):
    return f'<span class="pill" style="{SEV_PILL.get(sev, SEV_PILL["muted"])}">{esc(text)}</span>'


def nudge_link(pack_ref, order_po, advised_date):
    """Link to the REQUESTS-draft generator. NOT an Asana write."""
    q = f"nudge={esc(pack_ref)}"
    if order_po:
        q += f"&order={esc(order_po)}"
    if advised_date:
        q += f"&date={esc(advised_date)}"
    return (f'<a class="nudge" href="orders-by-pack-nudge.html?{q}" '
            f'title="Files a REQUESTS draft — SAP posts it after review">'
            f'✉ Nudge build</a>')


def nudge_summary(parts: list[dict], dleft, today: datetime.date) -> tuple[bool, int, str]:
    """Does this pack need a nudge, by how many days, and why?

    Returns (needs_nudge, days, reason). `days` = how far the pack's build date
    would need to move to accommodate the worst impacting order (0 if none).

    The critical measure: an order that LANDS AFTER the build date means the pack
    cannot build on time -> the build date needs to move by (order_due - build_due).
    """
    if dleft is None:
        return False, 0, ""
    worst = 0
    reasons = []
    for x in parts:
        if x["_bucket"] == LANDED:
            continue
        o = x.get("_order")
        # ORDER-BASED ONLY (Giles, 2026-09-11): the recommended move must be
        # driven by the ORDER due date, never the part's own date. Skip any
        # outstanding part whose order has no date -- it cannot tell us how far
        # the build needs to move.
        h = x.get("_hier") or {}
        eta_iso = (h.get("order_due") or "").strip()
        if not eta_iso and o:
            eta_iso = (o.get("due_date") or "").strip()
        if not eta_iso:
            continue
        eta_iso = eta_iso[:10]
        odays = _days_to(eta_iso, today)
        # order lands after the build date -> the build must slip
        if odays is not None and odays > dleft:
            over = odays - dleft
            if over > worst:
                worst = over
            reasons.append(f"{x.get('sesame_pn') or 'item'} +{over}d")
    if worst:
        return True, worst, "lands after build: " + ", ".join(reasons[:3])
    return False, 0, ""


def pack_block(p: dict, parts: list[dict], impact: dict, email_map: dict,
               today: datetime.date, compact: bool = False) -> str:
    pref = p.get("pack_reference") or p.get("pack_name") or "(no ref)"
    gid = p.get("asana_task_gid")
    due = _fmt_date(p.get("due_on"))
    dleft = _days_to(p.get("due_on"), today)
    steel = (p.get("steel_profile") or "Not Assessed").strip()

    if compact:
        return (f'<div class="card compact">'
                f'<span class="muted">{esc(pref)}</span> '
                f'<span class="meta">due {due} · steel {esc(steel)} · no linked parts</span>'
                f'</div>')

    rlabel, rsev = readiness_rollup(parts)
    action, asev = pack_next_action(parts)

    # If parts did not join but the impact engine scored this pack, do NOT
    # report a false "Clear / no linked parts" -- use the impact row's numbers.
    if not parts and impact:
        rlabel, action, asev = impact_rollup(impact)
        rsev = asev

    # deadline pill
    if dleft is None:
        due_pill = pill("no build date", "muted")
    elif dleft < 0:
        due_pill = pill(f"BUILD OVERDUE {-dleft}d", "bad")
    elif dleft <= 7:
        due_pill = pill(f"build in {dleft}d", "warn")
    else:
        due_pill = pill(f"build in {dleft}d", "good")

    imp_bits = ""
    if impact:
        imp_bits = (f" · risk <b>{esc(impact.get('risk_band') or '—')}</b>"
                    f" · owner {esc(impact.get('action_owner') or '—')}")

    # ── nudge summary (the top line): does it need nudging, by how many days ──
    needs, ndays, nreason = nudge_summary(parts, dleft, today)
    if needs:
        nudge_badge = (f'<span class="pill" style="{SEV_PILL["bad"]}" '
                       f'title="{esc(nreason)}">⚠ NUDGE +{ndays}d</span>')
    elif action != "Clear":
        nudge_badge = f'<span class="pill" style="{SEV_PILL["warn"]}">chase — no date slip</span>'
    else:
        nudge_badge = f'<span class="pill" style="{SEV_PILL["good"]}">✓ on track</span>'

    # expiring/near builds sort the urgency on the summary line
    head = (
        f'<summary id="pack-{esc(str(gid) or _norm_pack(pref))}">'
        f'<span class="sref">{asana_task_link(gid, pref) if gid else esc(pref)}</span>'
        f' <span class="ssep">·</span> build due <b>{due}</b> {due_pill}'
        f' {nudge_badge}'
        f' <span class="spill">{pill(rlabel, rsev)} {pill(action, asev)}</span>'
        f'<span class="smeta">steel {esc(steel)} · {len(parts)} part(s)'
        f'{imp_bits}</span>'
        f'</summary>')

    body = (f'<p class="meta">Build due <b>{due}</b> · steel profile <b>{esc(steel)}</b>'
            f' · parts linked <b>{len(parts)}</b>'
            f' · project {esc(p.get("project_code") or "—")}{imp_bits}</p>')

    if needs:
        body += (f'<div class="nudgetip">⚠ <b>Recommend moving this build by {ndays} day(s).</b> '
                 f'{esc(nreason)}</div>')

    if impact and impact.get("next_action"):
        body += (f'<p class="meta">Impact engine: {esc(impact["next_action"])} '
                 f'<span class="muted">({esc(impact.get("risk_reason") or "")})</span></p>')

    if not parts:
        if impact:
            t = impact.get("parts_total") or 0
            rd = impact.get("parts_ready") or 0
            uo = impact.get("parts_unordered") or 0
            oo = impact.get("parts_on_order") or 0
            return (f'<details class="card">' + head + body +
                    f'<p class="muted">Part rows did not join to this pack, but the impact '
                    f'engine scores it: <b>{rd}/{t}</b> ready · <b>{uo}</b> unordered · '
                    f'<b>{oo}</b> on order. Pack association needs checking.</p></details>')
        return (f'<details class="card">' + head + body +
                f'<p class="muted">No parts linked to this pack yet.</p></details>')
    # ── orders impacting the build ──
    # ONLY outstanding parts belong here. A landed part (task complete, or
    # stock_action on-site) has already arrived and cannot be "impacting" the
    # build -- showing its order would resurrect completed lines.
    ordered = [x for x in parts if x["_order"] and x["_bucket"] == OUTSTANDING]
    rows = []
    for x in sorted(ordered, key=lambda y: (effective_due(y.get("due_on"), y["_order"], y.get("_hier"))[0] or "9999")):
        o = x["_order"]
        ostatus = x["_ostatus"] or "—"
        # due-in: prefer the ORDER task's LIVE date, then the DB mirror, then part
        eta_iso, eta_src = effective_due(x.get("due_on"), o, x.get("_hier"))
        eta = eta_iso or "—"
        odays = _days_to(eta_iso, today)
        late_vs_build = ""
        if dleft is not None and odays is not None and odays > dleft:
            late_vs_build = ' ' + pill(f"lands after build by {odays - dleft}d", "bad")
        # flag the provenance: live order date is the trusted one
        if eta_src == "order-live":
            eta_flag = (' <span class="pill" style="%s" title="live from the order task in Asana">'
                        'order</span>' % SEV_PILL["good"])
        elif eta_src == "order-db":
            eta_flag = (' <span class="pill" style="%s" title="order task date from the ops.db '
                        'mirror — live check unavailable">order ·db</span>' % SEV_PILL["muted"])
        elif eta_src == "part":
            eta_flag = (' <span class="pill" style="%s" title="the order task has no date — '
                        'falling back to the part date">part date</span>' % SEV_PILL["muted"])
        else:
            eta_flag = ""
        vendor = (o.get("vendor_name") or x.get("vendor_name") or "—")
        email = email_map.get(vendor.strip().lower(), "")
        # a chase needs an address -- say so plainly rather than showing a blank
        needs_chase = x["_action"] in ("Draft confirmation e-mail", "Chase quote/order")
        if email:
            email_cell = esc(email)
        elif needs_chase:
            email_cell = '<span style="color:#fbbf24">no supplier email on file</span>'
        else:
            email_cell = "—"
        sev = ("good" if x["_bucket"] == LANDED else
               "bad" if re.search(r"CONFIRMATION|Raise order", x["_action"]) else "warn")
        # click-through: part ID and description link to the part's Asana task
        pn_cell = part_link(x, x.get("sesame_pn") or "—")
        desc_cell = part_link(x, (x.get("part_name") or "")[:40])
        # order cell: for lasercut the ORDER is the grandparent, not the parent.
        # ALWAYS hyperlink to the order task so PO numbers are click-through.
        h = x.get("_hier") or {}
        if h.get("is_lasercut") and h.get("order_gid"):
            po_txt = o.get("po_number") or (h.get("order_name") or "")[:18]
            order_cell = (f'<a href="{ASANA_BASE}/{h["order_gid"]}" target="_blank" '
                          f'title="laser-cut: order is the parent of the parent">'
                          f'{esc(po_txt)}</a> <span class="pill" '
                          f'style="{SEV_PILL["warn"]}">lasercut↑↑</span>')
        elif o.get("asana_task_gid"):
            po_txt = o.get("po_number") or (o.get("task_name") or "")[:24]
            order_cell = (f'<a href="{ASANA_BASE}/{o["asana_task_gid"]}" target="_blank" '
                          f'title="open the order task in Asana">{esc(po_txt)}</a>')
        else:
            order_cell = esc(o.get("po_number") or "—")
        rows.append(
            f"<tr><td>{pn_cell}</td>"
            f"<td>{desc_cell}</td>"
            f"<td>{esc(vendor[:30])}</td>"
            f"<td>{order_cell}</td>"
            f"<td>{pill(ostatus[:38], sev)}</td>"
            f"<td>{esc(eta)}{eta_flag}{late_vs_build}</td>"
            f"<td>{email_cell}</td>"
            f"<td>{pill(x['_action'], sev)}</td>"
            f"<td>{nudge_link(pref, o.get('po_number'), eta_iso)}</td></tr>")

    orders_html = ""
    if rows:
        orders_html = ("<table><tr><th>Part</th><th>Description</th><th>Vendor</th>"
                       "<th>PO/SQR</th><th>Order status</th><th>Order due-in</th>"
                       "<th>Email</th><th>Action</th><th></th></tr>"
                       + "".join(rows) + "</table>")

    # ── parts still outstanding for this pack ──
    outs = [x for x in parts if x["_bucket"] == OUTSTANDING and not x["_order"]]
    out_rows = []
    for x in sorted(outs, key=lambda y: (y.get("sesame_pn") or "")):
        out_rows.append(
            f"<tr><td>{part_link(x, x.get('sesame_pn') or '—')}</td>"
            f"<td>{part_link(x, (x.get('part_name') or '')[:45])}</td>"
            f"<td>{esc((x.get('vendor_name') or '—')[:30])}</td>"
            f"<td>{pill(x['_stock'] or 'not set', 'bad')}</td>"
            f"<td>{pill(x['_action'], 'bad')}</td>"
            f"<td>{nudge_link(pref, '', '')}</td></tr>")
    out_html = ""
    if out_rows:
        out_html = (f"<h4>Still outstanding for this pack — {len(outs)} "
                    f"(not on site and no order raised)</h4>"
                    "<table><tr><th>Part</th><th>Description</th><th>Vendor</th>"
                    "<th>Stock action</th><th>Action</th><th></th></tr>"
                    + "".join(out_rows) + "</table>")

    # nothing outstanding -> say so plainly rather than showing a blank body
    if not orders_html and not out_html:
        orders_html = ('<p class="meta">✅ Nothing outstanding — every linked '
                       'part on this pack is landed or already complete.</p>')

    # ALL packs render COLLAPSED by default (Giles, 2026-09-11): the page loads
    # tidy, the summary line carries the nudge verdict, and you expand only the
    # ones you care about. The "Expand all / Collapse all" button flips them.
    return f'<details class="card">{head}{body}{orders_html}{out_html}</details>'


def render(body: str, today: str, n_real: int, n_noise: int, n_incomplete: int) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Orders by Pack — {today}</title>
<style>
:root {{ --bg:#0f172a; --ink:#e2e8f0; --muted:#94a3b8; --panel:#1e293b;
        --line:#334155; --acc:#38bdf8; --good:#4ade80; --bad:#f87171; --warn:#fbbf24; }}
body {{ margin:0; font-family:Segoe UI,system-ui,sans-serif; background:var(--bg); color:var(--ink); }}
.wrap {{ max-width:1500px; margin:0 auto; padding:24px; }}
h1 {{ font-size:22px; }} h2 {{ font-size:18px; border-bottom:1px solid var(--line);
      padding-bottom:6px; color:var(--acc); margin-top:34px; }}
h3 {{ font-size:15px; margin:0 0 4px; }}
h4 {{ color:var(--warn); margin:14px 0 4px; font-size:13px; }}
table {{ border-collapse:collapse; width:100%; margin:6px 0 12px; font-size:12.5px; }}
th,td {{ border:1px solid var(--line); padding:4px 7px; text-align:left; vertical-align:top; }}
th {{ background:#111c31; color:var(--muted); font-weight:600; }}
a {{ color:var(--acc); text-decoration:none; }} a:hover {{ text-decoration:underline; }}
.muted {{ color:var(--muted); font-style:italic; }}
.meta {{ color:var(--muted); font-size:12px; margin:2px 0; }}
.pill {{ display:inline-block; padding:1px 8px; border-radius:10px; margin:1px 2px; font-size:11px; }}
.card {{ background:var(--panel); border:1px solid var(--line); border-radius:8px;
        padding:12px 14px; margin:10px 0; }}
.card.compact {{ padding:4px 10px; margin:3px 0; font-size:12px; }}
/* collapsible pack rows */
details.card {{ padding:0; overflow:hidden; }}
details.card > summary {{ cursor:pointer; padding:10px 14px; list-style:none;
        display:flex; flex-wrap:wrap; align-items:center; gap:6px; font-size:13px; }}
details.card > summary::-webkit-details-marker {{ display:none; }}
details.card > summary::before {{ content:"▸"; color:var(--acc); font-weight:700;
        margin-right:2px; }}
details.card[open] > summary::before {{ content:"▾"; }}
details.card[open] > summary {{ border-bottom:1px solid var(--line); }}
details.card > summary:hover {{ background:#16243a; }}
details.card > *:not(summary) {{ margin-left:14px; margin-right:14px; }}
.sref {{ font-weight:600; }}
.ssep {{ color:var(--muted); }}
.spill {{ margin-left:4px; }}
.smeta {{ color:var(--muted); font-size:11.5px; margin-left:auto; }}
.nudgetip {{ background:#3b1616; border-left:3px solid var(--bad); padding:6px 10px;
        border-radius:0 6px 6px 0; font-size:12.5px; margin:8px 0; }}
.nudge {{ display:inline-block; padding:1px 8px; border-radius:10px; font-size:11px;
         border:1px solid var(--line); }}
.togglebtn {{ position:fixed; top:14px; right:150px; z-index:999; cursor:pointer;
        background:#1e293b; color:var(--ink); border:1px solid var(--line);
        border-radius:20px; padding:6px 14px; font-size:12.5px; font-family:inherit; }}
.togglebtn:hover {{ background:#16243a; }}
.back {{ position:fixed; top:14px; right:16px; z-index:999; background:#0f172a; color:#fff;
        border:1px solid var(--line); border-radius:20px; padding:6px 14px; text-decoration:none; }}
.note {{ background:#111c31; border-left:3px solid var(--acc); padding:8px 12px;
        font-size:12.5px; color:var(--muted); margin:10px 0; }}
</style></head>
<body><div class="wrap">
<a class="back" href="nav.html">&larr; SAS OS portal</a>
<h1>📦 Orders by Pack — build impact</h1>
<button id="toggleAll" class="togglebtn" type="button">⊞ Expand all</button>
<p class="meta">Read-only. Seeded by <b>incomplete</b> build pack ({n_real} live builds +
{n_noise} unlinked tasks = {n_incomplete} incomplete) · grouped by project, then
ordered by build due date · Nothing writes to Asana or the DB. Generated {today} UTC.</p>
<div class="note"><b>How to read this.</b> Each card is one live build pack. The build due
date is the clock; parts link to the pack by its Asana task GID; each order shows its
<b>order due-in</b> (the delivery date), its <b>order status</b>, and whether it is
<b>confirmed</b>. The action per line tells you whether to <b>draft a confirmation
e-mail</b>, <b>chase</b>, <b>raise an order</b>, or whether it is <b>clear</b>. Parts at
the bottom of each card are still to be bought for that build. <b>Nudge build</b> files a
local REQUESTS draft for SAP — it does not post to Asana.</div>
{body}
</div>
<script>
(function() {{
  var btn = document.getElementById('toggleAll');
  function cards() {{ return document.querySelectorAll('details.card'); }}
  function allOpen() {{
    var c = cards();
    for (var i = 0; i < c.length; i++) {{ if (!c[i].open) return false; }}
    return c.length > 0;
  }}
  function setAll(open) {{
    var c = cards();
    for (var i = 0; i < c.length; i++) {{ c[i].open = open; }}
    btn.textContent = open ? '⊟ Collapse all' : '⊞ Expand all';
  }}
  btn.addEventListener('click', function() {{ setAll(!allOpen()); }});
  // keep the label honest if the user opens/closes a few by hand
  document.addEventListener('toggle', function() {{
    btn.textContent = allOpen() ? '⊟ Collapse all' : '⊞ Expand all';
  }}, true);
  setAll(false);  // everything collapsed on load
}})();
</script>
</body></html>"""


def project_of(p: dict) -> str:
    """The PROJECT a pack belongs to = the first 4 digits of its pack reference.

    Giles (2026-09-11): "the first four numbers of the bill pack identifies what
    project they are to do with". The `project_code` column is only populated on
    ~1/3 of packs (342/502 are NULL), so we derive the project from the ref
    itself -- which is always `<4-digit project>.<sequence>...`.

    Falls back to project_code when the ref is not in that shape, else '—'.
    """
    ref = (p.get("pack_reference") or p.get("pack_name") or "").strip()
    m = re.match(r"^(\d{4})", ref)
    if m:
        return m.group(1)
    return (p.get("project_code") or "—").strip() or "—"


def _pack_ready_tier(parts: list[dict], impact: dict) -> int:
    """0 = NOT ready for production (floats to the top of a project group);
    1 = READY for production (nothing outstanding) but not yet completed.

    Mirrors the readiness label shown on the card: 'Ready for production' is the
    ONLY ready state. Packs with no joined parts fall back to the impact engine's
    rollup, so the sort agrees with what the user sees. Giles (2026-09-11): within
    each project, list not-ready packs first (date order), then ready-but-incomplete
    packs (date order).
    """
    if parts:
        label, _sev = readiness_rollup(parts)
        return 1 if label == "Ready for production" else 0
    if impact:
        label, _a, _s = impact_rollup(impact)
        return 1 if str(label).startswith("Ready for production") else 0
    return 0                      # unknown / nothing joined -> treat as not-ready


def pack_sort_key(p: dict, parts: list[dict], has_impact: bool, impact: dict | None = None):
    """Ordering INSIDE a project group (Giles, 2026-09-11):

      1. NOT-ready-for-production packs first, by build due date (soonest first);
      2. then READY-for-production-but-not-completed packs, by build due date.

    The pack with the earliest build due date is the most urgent and appears first
    within its tier; a pushed-back date falls further down. Ties broken by ref so
    the page is stable between runs.

    (Project grouping itself is handled by the caller, which buckets packs by
    project_of() -- this key orders within each bucket.)
    """
    tier = _pack_ready_tier(parts, impact or {})
    due = (p.get("due_on") or "")[:10]
    return (tier, due or "9999-99-99", p.get("pack_reference") or "")


def write_nudge_draft(pack_ref: str, order_po: str, advised_date: str,
                      reason: str = "", out_dir: Path | None = None) -> Path:
    """File a REQUESTS draft asking SAP to post a nudge comment on the build
    assembly task + subtasks. This is the sanctioned write path (SAP is the only
    Asana writer). Nothing is posted until the draft is drained and approved.
    """
    ts = datetime.datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", pack_ref)[:60]
    req_dir = out_dir if out_dir is not None else (WS / "queues" / "REQUESTS")
    req_dir.mkdir(parents=True, exist_ok=True)
    out = req_dir / f"{ts}-nudge-build-{safe}.md"

    body = f"""---
From: hermes
To: sap
Type: asana_write
Status: PENDING
Priority: normal
Created: {datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}
---

## Request
Action: comment (build assembly task + its subtasks)

## Summary
Post a delay notice on the build assembly task for pack **{pack_ref}** and its
subtasks, so the build owner sees that a feeding order is delayed and the pack
date is at risk.

Comment body to post:
> ⚠️ Order impact — pack `{pack_ref}`.
> Order **{order_po or '(no PO yet)'}** is due in on **{advised_date or 'TBC'}**.
> This impacts this build — we advise moving the build date to **{advised_date or 'TBC'}**.
> {reason or 'Raised from the Orders-by-Pack build-impact pane.'}

Target task: build pack `{pack_ref}` (resolve the assembly task GID by pack
reference), then post the same comment to its subtasks.

## Context
Giles raised this from the read-only "Orders by Pack" dashboard pane. The pane
cannot write to Asana itself (SAP is the only writer), so the nudge is filed
here for SAP to drain.

## Guardrail assessment
- Write type is a COMMENT on a build assembly task — additive, no status change.
- Does NOT move the build date automatically; it only advises. Any date change
  stays a human decision.
- Not a forbidden GID. Not a delete. Safe to drain after Giles confirms.
"""
    out.write_text(body, encoding="utf-8")
    return out


def main():
    ap = argparse.ArgumentParser(description="Orders by Pack (read-only)")
    ap.add_argument("--out", default=str(WS / "data" / "os_status"))
    ap.add_argument("--limit", type=int, default=0, help="cap packs rendered (0 = all)")
    ap.add_argument("--nudge", metavar="PACK_REF",
                    help="file a REQUESTS nudge draft for this pack instead of building")
    ap.add_argument("--order", default="", help="order/PO number for the nudge")
    ap.add_argument("--date", default="", help="advised new build date for the nudge")
    ap.add_argument("--reason", default="", help="reason text for the nudge")
    ap.add_argument("--nudge-dry-run", action="store_true",
                    help="print the nudge draft to stdout instead of filing it")
    ap.add_argument("--nudge-to", default="",
                    help="override the output dir for the nudge draft (e.g. a test dir)")
    args = ap.parse_args()

    if args.nudge:
        if args.nudge_dry_run:
            import tempfile
            with tempfile.TemporaryDirectory() as td:
                p = write_nudge_draft(args.nudge, args.order, args.date, args.reason,
                                      out_dir=Path(td))
                print(p.read_text(encoding="utf-8"))
            return
        p = write_nudge_draft(args.nudge, args.order, args.date, args.reason,
                              out_dir=Path(args.nudge_to) if args.nudge_to else None)
        print(f"OK filed REQUESTS draft: {p}")
        print("Nothing has been written to Asana. SAP drains it after review.")
        return

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(f"file:{OPS_DB}?mode=ro", uri=True)
    today_d = datetime.date.today()
    today = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M")

    packs = load_incomplete_packs(conn)
    parts_by_pack = load_parts_by_pack(conn)
    orders = load_orders_by_part(conn)
    impact = load_impact(conn)
    email_map = load_supplier_email(conn)
    conn.close()

    # ── live hierarchy resolve (lasercut order = grandparent) ──
    all_parts = [x for lst in parts_by_pack.values() for x in lst]
    try:
        hier = build_hierarchy_index(all_parts)
        print(f"[orders-by-pack] resolved task hierarchy for {len(hier)} linked part tasks")
    except Exception as e:
        hier = {}
        print(f"[orders-by-pack] hierarchy resolve skipped ({e}) -- lasercut orders "
              f"will show the parent task, not the grandparent")

    # ── assemble, ordered: real scheduled builds first, noise last ──
    rows = []
    for p in packs:
        gid_key = _norm_pack(p.get("asana_task_gid"))
        raw = parts_by_pack.get(gid_key, [])
        classified = []
        for x in raw:
            c = classify_part(x, orders.get(x.get("procurement_task_gid")))
            c["_hier"] = hier.get(x.get("asana_task_gid")) or {}
            classified.append(c)
        imp = impact.get(_norm_pack(p.get("pack_reference") or p.get("pack_name")))
        rows.append((pack_sort_key(p, classified, bool(imp), imp), p, classified, imp))
    rows.sort(key=lambda t: t[0])

    real, noise = [], []
    for _k, p, classified, imp in rows:
        # has parts, or an impact row = tracked build; otherwise noise
        (real if (classified or imp) else noise).append((p, classified, imp))

    # ── DROP non-packs (Giles, 2026-09-11) ──
    # A build-schedule task with NO subtasks is not a pack at all (holiday
    # requests, QC copies, drawing-control headings, 'Working from home'). Ask
    # Asana live for the subtask count and remove those from the dashboard
    # entirely. Unknown counts (-1, lookup failed) are KEPT -- we never hide a
    # pack because a network call failed.
    all_pack_gids = [p.get("asana_task_gid") for p in packs if p.get("asana_task_gid")]
    try:
        subs = build_subtask_index(all_pack_gids)
    except Exception as e:
        subs = {}
        print(f"[orders-by-pack] subtask check skipped ({e}) -- showing all packs")

    def _has_subtasks(p) -> bool:
        n = subs.get(p.get("asana_task_gid"), -1)
        return n != 0                     # 0 => drop; -1/None/positive => keep

    dropped = [p for p in packs if not _has_subtasks(p)]
    real = [(p, c, i) for p, c, i in real if _has_subtasks(p)]
    noise = [(p, c, i) for p, c, i in noise if _has_subtasks(p)]
    print(f"[orders-by-pack] dropped {len(dropped)} task(s) with no subtasks")

    # ── DROP packs COMPLETED live in Asana (Giles, 2026-09-11) ──────────────
    # The seed already filters the DB's is_completed flag, but that is a nightly
    # mirror snapshot: a pack completed since the last 01:00 sync still reads as
    # incomplete and leaks onto the page (reported: task 1214059103848019 visible
    # though completed). Confirm completion LIVE and drop only those the API says
    # are done; unknown (lookup failed / not cached) is KEPT -- never hide a pack
    # because a network call failed.
    all_pack_gids = [p.get("asana_task_gid") for p in packs if p.get("asana_task_gid")]
    try:
        completed_idx = build_completed_index(all_pack_gids)
    except Exception as e:
        completed_idx = {}
        print(f"[orders-by-pack] live completeness check skipped ({e}) -- using DB flag only")

    def _is_completed_live(p) -> bool:
        return completed_idx.get(p.get("asana_task_gid")) is True

    n_done = sum(1 for p in packs if _is_completed_live(p))
    real = [(p, c, i) for p, c, i in real if not _is_completed_live(p)]
    noise = [(p, c, i) for p, c, i in noise if not _is_completed_live(p)]
    dropped_packs = [p for p in packs if not _has_subtasks(p) or _is_completed_live(p)]
    print(f"[orders-by-pack] dropped {n_done} pack(s) completed LIVE in Asana "
          f"(of {len(packs)} incomplete-seeded)")

    # ── GROUP BY PROJECT (Giles, 2026-09-11): sections like Build Schedule ──
    # Project = first 4 digits of the pack ref. Inside each project the packs
    # are ordered by build due date (most urgent first, pushed-back last).
    by_project: dict[str, list] = defaultdict(list)
    for p, c, i in real:
        by_project[project_of(p)].append((p, c, i))

    # projects ordered by their EARLIEST build due date (so the project under
    # most pressure floats to the top); no-date groups last.
    def _proj_key(kv):
        proj, lst = kv
        dues = sorted((p.get("due_on") or "9999")[:10] or "9999" for p, _c, _i in lst)
        return (dues[0] if dues else "9999-99-99", proj)

    proj_sections = []
    for proj, lst in sorted(by_project.items(), key=_proj_key):
        lst.sort(key=lambda t: pack_sort_key(t[0], t[1], bool(t[2]), t[2]))
        n_nudge = sum(1 for p, c, _i in lst
                      if nudge_summary(c, _days_to(p.get("due_on"), today_d), today_d)[0])
        blocks = "".join(pack_block(p, c, i, email_map, today_d) for p, c, i in lst)
        badge = (f'<span class="pill" style="{SEV_PILL["bad"]}">⚠ {n_nudge} to nudge</span>'
                 if n_nudge else
                 f'<span class="pill" style="{SEV_PILL["good"]}">no nudge needed</span>')
        proj_sections.append(
            f'<h2 id="proj-{esc(proj)}">📁 Project {esc(proj)} — {len(lst)} build(s) {badge}</h2>'
            + blocks)

    noise_blocks = [pack_block(p, c, i, email_map, today_d, compact=True)
                    for p, c, i in noise]

    with_parts = sum(1 for _p, c, _i in real if c)
    imp_only = len(real) - with_parts
    body = (f'<h2 id="builds">Live build packs — {len(real)} across '
            f'{len(by_project)} project(s)</h2>'
            f'<div class="note">Seeded by build pack, <b>grouped by project</b> '
            f'(first 4 digits of the pack ref). Within each project packs are ordered '
            f'<b>not-ready-for-production first</b> (build due date, soonest first), then '
            f'<b>ready-for-production but not yet completed</b> (build due date). '
            f'Completed packs are excluded (confirmed live against Asana). Every card loads '
            f'<b>collapsed</b>; the summary line carries the nudge verdict. '
            f'<b>{with_parts}</b> packs have their parts joined (full order-impact view); '
            f'<b>{imp_only}</b> are scored by the impact engine but their part rows did not '
            f'join — shown from the engine\'s own numbers, flagged "association needs checking".'
            f'</div>'
            + "".join(proj_sections))
    if noise_blocks:
        body += (f'<h2 id="noise">Build-schedule tasks with no linked parts or '
                 f'impact row — {len(noise_blocks)}</h2>'
                 '<div class="note">These are real tasks in the Build Schedule project but '
                 'have <b>no parts linked</b> and <b>no production-impact row</b> — '
                 'workshop duties, QC copies, stock jobs. Shown for completeness, '
                 'excluded from build-impact tracking.</div>'
                 + "".join(noise_blocks))

    html = render(body, today, len(real), len(noise_blocks), len(packs))
    out_html = out_dir / "orders-by-pack.html"
    out_html.write_text(html, encoding="utf-8")
    dated = out_dir / f"orders-by-pack-{datetime.datetime.utcnow().strftime('%Y%m%d_%H%M')}.html"
    dated.write_text(html, encoding="utf-8")
    print(f"OK wrote {out_html} ({len(html)} bytes) + dated copy")
    print(f"[orders-by-pack] incomplete_packs={len(packs)} live_builds={len(real)} "
          f"projects={len(by_project)} unlinked_tasks={len(noise_blocks)}")


if __name__ == "__main__":
    main()
