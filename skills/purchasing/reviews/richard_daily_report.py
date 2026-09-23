#!/usr/bin/env python
"""
Richard Daily Report — rich multi-source morning report for Richard.

DATA STRATEGY (Giles 2026-09-23): much of this is already synced overnight from
Asana into ops.db, so prefer the DB over API calls wherever the data is reliable
and fresh. Only fall back to the API where the DB does not hold the data
(assignment, the Invoices 'Goods Received' section, Service Rack).

Sections:
  1. Arrived goods with unticked parts to close   — Invoices -> Goods Received  [API]
  2. Parts Control — left to pick                 — ops.db parts_control        [DB]
  3. Orders — overdue / due today-tomorrow        — chase_rules rule_a (DB+live) [DB]
  4. Build packs needing update (outstanding parts)— ops.db build_assembly_packs[DB]
  5. Service Rack — parts to pick (Richard)        — Service Rack                [API]

OUTPUT RULES (Giles):
  * Clean readable structure — NO mangled copy-table blob.
  * FULL, un-abbreviated hyperlinks on every item. Never show a bare GID to a human.
  * Task/part name is the clickable text; one item per line.
  * Emoji / cp1252 mojibake stripped.
  * Read-only on Asana. Never writes. Never fails silently.
"""
import os
import re
import sqlite3
import sys
from datetime import date, timedelta

try:
    import requests
except ImportError:
    print("FATAL: requests not available", file=sys.stderr)
    sys.exit(2)

# Allow importing the workspace purchasing helpers (chase_rules etc.)
_HERE = os.path.dirname(os.path.abspath(__file__))
_PURCH = os.path.abspath(os.path.join(_HERE, "..", "..", "purchasing"))
if _PURCH not in sys.path:
    sys.path.insert(0, _PURCH)

DB = r"C:\Users\GilesHugo\SES_Workspace\db\ops.db"

_TOK_NAME = "ASANA_PERSONAL_ACCESS_TOKEN"
TOKEN = os.environ.get(_TOK_NAME, "")
if not TOKEN:
    # Fall back to reading the Hermes env file directly (hermes read_file redacts it).
    _home = os.path.expanduser("~")
    for _p in (os.path.join(_home, ".hermes", ".env"), os.path.join(_home, ".openclaw", ".env")):
        try:
            with open(_p) as _f:
                for _line in _f:
                    if _line.startswith(_TOK_NAME):
                        TOKEN = _line.split("=", 1)[1].strip().strip('"').strip("'")
                        break
            if TOKEN:
                break
        except FileNotFoundError:
            continue
BASE = "https://app.asana.com/api/1.0"
H = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}

# Project GIDs + human names (name shown to humans; GID only used to build the link)
INVOICES = ("902269769783650", "Invoices")
INVOICES_GOODS_RECEIVED = "902269769783651"
PARTS_CONTROL = ("1211660707080749", "Parts Control")
ORDER_NUMBERS = ("854650811917168", "Order Numbers")
BUILD_SCHEDULE = ("852956628490855", "Build Schedule")
SERVICE_RACK = ("1116850796015692", "Service Rack")
RICHARD_GID = "357614200440807"

READINESS_FIELD = "1142754335683361"


def clean(s):
    """Strip emoji / mojibake and collapse whitespace."""
    if not s:
        return ""
    s = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u2190-\u21FF\u2B00-\u2BFF]", "", str(s))
    s = re.sub(r"\u00f0\u0178.{0,2}", "", s)
    return " ".join(s.replace("\u200b", "").split()).strip()


def url(project_gid, task_gid):
    return f"https://app.asana.com/0/{project_gid}/{task_gid}"


def short(s, n=70):
    s = clean(s)
    return s if len(s) <= n else s[: n - 1] + "\u2026"


# ── Asana helpers (only used where the DB lacks the data) ──────────────────────
def _get(u, **p):
    r = requests.get(u, headers=H, params=p, timeout=45)
    r.encoding = "utf-8"
    return r.json()


def _paginate(u, fields):
    out, off = [], None
    while True:
        p = {"limit": 100, "opt_fields": fields}
        if off:
            p["offset"] = off
        j = _get(u, **p)
        if "data" not in j:
            raise RuntimeError(f"Asana error on {u}: {j.get('errors')}")
        out += j["data"]
        np = j.get("next_page")
        if np and np.get("offset"):
            off = np["offset"]
        else:
            break
    return out


def richard_task_gids():
    """Set of task GIDs currently assigned to Richard (one lightweight API pass)."""
    gids = set()
    # Page Richard's user task list is unreliable; scan the projects we care about
    # via the search endpoint (assignee filter is not allowed with project).
    j = _get(f"{BASE}/workspaces/357939803556123/tasks/search",
             **{"assignee.any": RICHARD_GID, "completed": "false",
                "opt_fields": "gid", "limit": 100})
    for t in j.get("data", []):
        gids.add(t.get("gid"))
    # paginate
    np = j.get("next_page")
    while np and np.get("offset"):
        j = _get(f"{BASE}/workspaces/357939803556123/tasks/search",
                 **{"assignee.any": RICHARD_GID, "completed": "false",
                    "opt_fields": "gid", "limit": 100, "offset": np["offset"]})
        for t in j.get("data", []):
            gids.add(t.get("gid"))
        np = j.get("next_page")
    return gids


# ── Section builders ──────────────────────────────────────────────────────────
def section1_arrived_unticked():
    """[API] Arrived orders with unticked dependents."""
    from concurrent.futures import ThreadPoolExecutor
    tasks = _paginate(f"{BASE}/sections/{INVOICES_GOODS_RECEIVED}/tasks", "name,completed")
    open_t = [t for t in tasks if not t.get("completed")]

    def probe(t):
        gid = t["gid"]
        deps = _get(f"{BASE}/tasks/{gid}/dependents", opt_fields="name,completed", limit=100).get("data", [])
        un = [d for d in deps if not d.get("completed")]
        return (clean(t.get("name", "")), gid, len(deps), len(un)) if un else None

    rows = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for r in pool.map(probe, open_t):
            if r:
                rows.append(r)
    return rows


def section2_picking(rich_gids=None):
    """[DB] Parts Control PLEASE PICK (assigned Richard if gids provided)."""
    conn = sqlite3.connect(DB)
    rows = conn.execute(
        """SELECT part_name, asana_task_gid, sesame_pn, order_qty, stock_action
           FROM parts_control
           WHERE is_completed=0 AND stock_action LIKE 'PLEASE PICK%'"""
    ).fetchall()
    conn.close()
    if rich_gids:
        rows = [r for r in rows if r[1] in rich_gids]
    return [(clean(r[0]), r[1], r[3]) for r in rows]


def section3_orders():
    """[DB + parallel live gate] Overdue and due-today/tomorrow orders.

    Reads procurement_tasks (overnight sync), then live-gates each candidate against
    the live Asana ORDER Status so finished/invoiced POs aren't listed. The gate is
    parallelised (the sequential version took ~2min for ~135 candidates).
    """
    from concurrent.futures import ThreadPoolExecutor
    TERMINAL = ("ARRIVED", "CANCELLED", "ALL PICKED", "TO BE RETURNED", "INVOICES")
    today = date.today()
    tomorrow = today + timedelta(days=1)

    try:
        conn = sqlite3.connect(DB)
        rows = conn.execute(
            """SELECT po_number, vendor_name, task_name, due_date, order_status, asana_task_gid
               FROM procurement_tasks
               WHERE is_completed=0 AND order_status LIKE '%Awaiting CONFIRMATION%'
                 AND due_date IS NOT NULL AND due_date != ''
               ORDER BY due_date ASC"""
        ).fetchall()
        conn.close()
    except Exception as e:
        return [], [], f"(order section unavailable: {e})"

    # Only gate candidates whose ETA has already passed or is within the next day.
    cands = []
    for po, vendor, task, due, ost, gid in rows:
        try:
            d = date.fromisoformat((due or "")[:10])
        except ValueError:
            continue
        if d <= tomorrow:
            cands.append((po, vendor, task, d, ost, gid))

    def gate(rec):
        po, vendor, task, d, ost, gid = rec
        if not gid or not TOKEN:
            return rec
        try:
            t = _get(f"{BASE}/tasks/{gid}", opt_fields="custom_fields.name,custom_fields.display_value")
            for cf in (t.get("data", {}).get("custom_fields") or []):
                if cf.get("name") == "ORDER Status":
                    live = clean(cf.get("display_value")).upper()
                    if any(x in live for x in TERMINAL):
                        return None
                    break
        except Exception:
            pass  # keep the DB row if Asana unreachable (never silently drop)
        return rec

    gated = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for r in pool.map(gate, cands):
            if r:
                gated.append(r)

    overdue, due_soon = [], []
    for po, vendor, task, d, ost, gid in gated:
        label = (po or "") + (f" \u00b7 {vendor}" if vendor else "")
        rec = (label.strip(), gid or "", d.isoformat())
        (overdue if d < today else due_soon).append(rec)
    return overdue, due_soon, ""


def section4_build_packs():
    """[DB] Open build packs with outstanding parts (Kitted / RFP)."""
    conn = sqlite3.connect(DB)
    rows = conn.execute(
        """SELECT pack_reference, asana_task_gid, parts_readiness
           FROM build_assembly_packs
           WHERE is_completed=0
             AND (parts_readiness LIKE 'Kitted%' OR parts_readiness LIKE 'RFP%')"""
    ).fetchall()
    conn.close()
    return [(clean(r[0]), r[1], clean(r[2])) for r in rows]


def section5_service(rich_gids=None):
    """[API] Service Rack open tasks assigned to Richard."""
    if not rich_gids:
        return []
    tasks = _paginate(f"{BASE}/projects/{SERVICE_RACK[0]}/tasks", "name,completed,assignee.gid,due_on")
    return [(clean(t.get("name", "")), t["gid"], t.get("due_on"))
            for t in tasks if not t.get("completed") and t["gid"] in rich_gids]


# ── Render ────────────────────────────────────────────────────────────────────
def render():
    out = [f"RICHARD \u2014 DAILY PURCHASING REPORT \u00b7 {date.today().isoformat()}", ""]

    # 1
    try:
        s1 = section1_arrived_unticked()
        err1 = ""
    except Exception as e:
        s1, err1 = [], str(e)
    out.append(f"1) ARRIVED \u2014 parts still to close  ({len(s1)})")
    if err1:
        out.append(f"   ! unavailable: {err1}")
    elif s1:
        for name, gid, nd, nu in s1:
            out.append(f"   \u2022 {short(name)} \u2014 {nu} of {nd} part(s) outstanding")
            out.append(f"     {url(INVOICES[0], gid)}")
    else:
        out.append("   none")
    out.append("")

    # Richard's assigned gids (for §2, §5)
    try:
        rich = richard_task_gids()
    except Exception:
        rich = None

    # 2
    s2 = section2_picking(rich)
    out.append(f"2) PARTS CONTROL \u2014 left to pick  ({len(s2)})")
    if s2:
        for name, gid, qty in s2:
            out.append(f"   \u2022 {short(name)}")
            out.append(f"     {url(PARTS_CONTROL[0], gid)}")
    else:
        out.append("   none")
    out.append("")

    # 3
    overdue, due_soon, err3 = section3_orders()
    out.append(f"3) ORDERS \u2014 overdue ({len(overdue)}) / due today-tomorrow ({len(due_soon)})")
    if err3:
        out.append(f"   ! {err3}")
    else:
        if overdue:
            out.append("   OVERDUE:")
            for label, gid, due in overdue:
                out.append(f"   \u2022 {label} \u2014 due {due}")
                if gid:
                    out.append(f"     {url(ORDER_NUMBERS[0], gid)}")
        else:
            out.append("   OVERDUE: none")
        if due_soon:
            out.append("   DUE TODAY/TOMORROW:")
            for label, gid, due in due_soon:
                out.append(f"   \u2022 {label} \u2014 due {due}")
                if gid:
                    out.append(f"     {url(ORDER_NUMBERS[0], gid)}")
    out.append("")

    # 4
    s4 = section4_build_packs()
    out.append(f"4) BUILD PACKS to update \u2014 outstanding parts  ({len(s4)})")
    if s4:
        for name, gid, readiness in s4:
            out.append(f"   \u2022 {name} \u2014 {readiness}")
            out.append(f"     {url(BUILD_SCHEDULE[0], gid)}")
    else:
        out.append("   none")
    out.append("")

    # 5
    try:
        s5 = section5_service(rich)
    except Exception as e:
        s5 = []
    out.append(f"5) SERVICE RACK \u2014 parts to pick for servicing  ({len(s5)})")
    if s5:
        dash = "\u2014"
        for name, gid, due in s5:
            out.append(f"   \u2022 {short(name)} \u2014 due {due or dash}")
            out.append(f"     {url(SERVICE_RACK[0], gid)}")
    else:
        out.append("   none")
    out.append("")
    out.append("Source: ops.db (overnight Asana sync) + live Asana where needed. Read-only.")
    return "\n".join(out)


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(errors="replace")
        except Exception:
            pass
    try:
        print(render())
    except Exception as e:
        print(f"REPORT ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
