#!/usr/bin/env python
"""
Richard Daily Report — rich, multi-source morning report for Richard.

Sections (see skill 'richard-daily-report' for the architecture + data-source director):
  1. Unticked sub/dependent tasks on ARRIVED goods   (Invoices -> Goods Received)
  2. Left to picking                                 (PARTS CONTROL, PLEASE PICK, assigned Richard)
  3. Orders OVERDUE / due today-tomorrow             (0 - Order Numbers, due_on)
  4. Build packs needing update (outstanding parts)   (3.1 Build Schedule, PARTS - Readiness)
  5. Service parts to pick (assigned Richard)         (4.1 Service Rack)

Output rules (Giles 2026-09-03):
  * Clean readable structure — NOT a mangled copy-table blob.
  * FULL, un-abbreviated Asana hyperlinks on every item (never truncated).
  * Part/ref is the hyperlink text; one line per item.
  * Emoji / cp1252 mojibake stripped from all display values.
  * Read-only on Asana. Never writes. Never fails silently.
"""
import os
import re
import sys
from datetime import date, datetime, timedelta

try:
    import requests
except ImportError:
    print("FATAL: requests not available", file=sys.stderr)
    sys.exit(2)

TOKEN = os.environ.get("ASANA_PERSONAL_ACCESS_TOKEN", "")
if not TOKEN:
    print("FATAL: ASANA_PERSONAL_ACCESS_TOKEN not set", file=sys.stderr)
    sys.exit(2)

BASE = "https://app.asana.com/api/1.0"
H = {"Authorization": f"Bearer {TOKEN}"}

# Project GIDs (see skill richard-daily-report)
INVOICES = "902269769783650"
INVOICES_GOODS_RECEIVED = "902269769783651"
PARTS_CONTROL = "1211660707080749"
ORDER_NUMBERS = "854650811917168"
BUILD_SCHEDULE = "852956628490855"
SERVICE_RACK = "1116850796015692"
RICHARD_GID = "357614200440807"

# Build Schedule "PARTS - Readiness" field + the two target option GIDs
READINESS_FIELD = "1142754335683361"
READINESS_OPTIONS = {
    "1142754335683365": "Kitted / Parts missing",
    "1198895237627057": "RFP - Non Critical Parts Missing",
}

# Terminal order statuses = already arrived/finished (exclude from overdue/due list)
TERMINAL_ORDER_SUBSTR = ("ARRIVED", "CANCELLED", "ALL PICKED", "TO BE RETURNED")

# --- emoji / mojibake stripping ------------------------------------------------
_ARTIFACT_RE = re.compile(
    r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u2190-\u21FF\u2B00-\u2BFF]"
    r"|\u00f0\u0178.{0,2}"
)


def clean(s):
    if not s:
        return ""
    s = _ARTIFACT_RE.sub("", str(s))
    s = s.replace("\u200b", "")
    return " ".join(s.split()).strip()


def link(project_gid, task_gid):
    return f"https://app.asana.com/0/{project_gid}/{task_gid}"


def get(url, **params):
    r = requests.get(url, headers=H, params=params, timeout=45)
    r.encoding = "utf-8"
    return r.json()


def paginate(url, fields, limit=100):
    out, off = [], None
    while True:
        p = {"limit": limit, "opt_fields": fields}
        if off:
            p["offset"] = off
        j = get(url, **p)
        if "data" not in j:
            raise RuntimeError(f"Asana error on {url}: {j.get('errors')}")
        out += j["data"]
        np = j.get("next_page")
        if np and np.get("offset"):
            off = np["offset"]
        else:
            break
    return out


def cfv(task, name):
    for c in task.get("custom_fields", []):
        if c.get("name") == name:
            return c.get("display_value")
    return None


def cf_enum_gid(task, field_gid):
    for c in task.get("custom_fields", []):
        if c.get("gid") == field_gid:
            ev = c.get("enum_value")
            return ev.get("gid") if ev else None
    return None


def extract_qty(name):
    m = re.match(r"^\s*(\d+)\s*(?:off|Off|OFF)?\b", name or "")
    return m.group(1) if m else ""


# --- Section builders -----------------------------------------------------------

def section1_arrived_unticked():
    """Arrived orders in Invoices->Goods Received with unticked dependents/subtasks."""
    from concurrent.futures import ThreadPoolExecutor
    tasks = paginate(f"{BASE}/sections/{INVOICES_GOODS_RECEIVED}/tasks",
                     "name,completed")
    open_tasks = [t for t in tasks if not t.get("completed")]

    def probe(t):
        gid = t["gid"]
        deps = get(f"{BASE}/tasks/{gid}/dependents", opt_fields="name,completed", limit=100).get("data", [])
        unticked = [d for d in deps if not d.get("completed")]
        if unticked:
            return (clean(t.get("name", "")), gid, len(deps), len(unticked))
        return None

    rows = []
    # Parallelise the per-order dependents calls (the pipeline's slowest part).
    with ThreadPoolExecutor(max_workers=8) as pool:
        for res in pool.map(probe, open_tasks):
            if res:
                rows.append(res)
    return rows


def section2_picking():
    """Parts Control PLEASE PICK assigned to Richard."""
    rows = []
    tasks = paginate(f"{BASE}/projects/{PARTS_CONTROL}/tasks",
                     "name,completed,assignee.name,assignee.gid,custom_fields")
    for t in tasks:
        if t.get("completed"):
            continue
        if (t.get("assignee") or {}).get("gid") != RICHARD_GID:
            continue
        if clean(cfv(t, "Stock ACTION")) == "PLEASE PICK":
            rows.append((clean(t.get("name", "")), t["gid"]))
    return rows


def section3_orders():
    """Open order tasks overdue or due today/tomorrow."""
    today = date.today()
    tomorrow = today + timedelta(days=1)
    overdue, due_soon = [], []
    tasks = paginate(f"{BASE}/projects/{ORDER_NUMBERS}/tasks",
                     "name,completed,due_on,custom_fields")
    for t in tasks:
        if t.get("completed") or not t.get("due_on"):
            continue
        ost = clean(cfv(t, "ORDER Status")).upper()
        if any(x in ost for x in TERMINAL_ORDER_SUBSTR):
            continue
        due = t["due_on"]
        rec = (clean(t.get("name", "")), t["gid"], due, clean(cfv(t, "ORDER Status")))
        if due < today.isoformat():
            overdue.append(rec)
        elif due in (today.isoformat(), tomorrow.isoformat()):
            due_soon.append(rec)
    overdue.sort(key=lambda x: x[2])
    due_soon.sort(key=lambda x: x[2])
    return overdue, due_soon


def section4_build_packs():
    """Open Build Schedule packs whose PARTS - Readiness is a target option."""
    rows = []
    tasks = paginate(f"{BASE}/projects/{BUILD_SCHEDULE}/tasks",
                     "name,completed,assignee.name,custom_fields")
    for t in tasks:
        if t.get("completed"):
            continue
        g = cf_enum_gid(t, READINESS_FIELD)
        if g in READINESS_OPTIONS:
            rows.append((clean(t.get("name", "")), t["gid"], READINESS_OPTIONS[g]))
    return rows


def section5_service():
    """Service Rack tasks assigned to Richard (open)."""
    rows = []
    tasks = paginate(f"{BASE}/projects/{SERVICE_RACK}/tasks",
                     "name,completed,assignee.gid,due_on")
    for t in tasks:
        if t.get("completed"):
            continue
        if (t.get("assignee") or {}).get("gid") == RICHARD_GID:
            rows.append((clean(t.get("name", "")), t["gid"], t.get("due_on")))
    return rows


# --- Rendering ------------------------------------------------------------------

def render():
    today = date.today().isoformat()
    out = []
    out.append(f"RICHARD \u2014 DAILY PURCHASING REPORT \u00b7 {today}")
    out.append("")

    # 1
    s1 = section1_arrived_unticked()
    out.append(f"1) ARRIVED \u2014 unticked parts still to close  ({len(s1)})")
    if s1:
        for name, gid, ndeps, nunt in s1:
            out.append(f"   \u2022 {name}  \u2014 {nunt} of {ndeps} part(s) outstanding")
            out.append(f"     {link(INVOICES, gid)}")
    else:
        out.append("   none")
    out.append("")

    # 2
    s2 = section2_picking()
    out.append(f"2) PARTS CONTROL \u2014 left to pick  ({len(s2)})")
    if s2:
        for name, gid in s2:
            out.append(f"   \u2022 {name}")
            out.append(f"     {link(PARTS_CONTROL, gid)}")
    else:
        out.append("   none")
    out.append("")

    # 3
    overdue, due_soon = section3_orders()
    dash = "\u2014"
    out.append(f"3) ORDERS \u2014 overdue ({len(overdue)}) / due today-tomorrow ({len(due_soon)})")
    if overdue:
        out.append("   OVERDUE:")
        for name, gid, due, ost in overdue:
            out.append(f"   \u2022 {name}  \u2014 due {due} \u00b7 {ost or dash}")
            out.append(f"     {link(ORDER_NUMBERS, gid)}")
    else:
        out.append("   OVERDUE: none")
    if due_soon:
        out.append("   DUE TODAY/TOMORROW:")
        for name, gid, due, ost in due_soon:
            out.append(f"   \u2022 {name}  \u2014 due {due} \u00b7 {ost or dash}")
            out.append(f"     {link(ORDER_NUMBERS, gid)}")
    else:
        out.append("   DUE TODAY/TOMORROW: none")
    out.append("")

    # 4
    s4 = section4_build_packs()
    out.append(f"4) BUILD PACKS to update \u2014 outstanding parts  ({len(s4)})")
    if s4:
        for name, gid, readiness in s4:
            out.append(f"   \u2022 {name}  \u2014 {readiness}")
            out.append(f"     {link(BUILD_SCHEDULE, gid)}")
    else:
        out.append("   none")
    out.append("")

    # 5
    s5 = section5_service()
    out.append(f"5) SERVICE RACK \u2014 parts to pick for servicing  ({len(s5)})")
    if s5:
        for name, gid, due in s5:
            out.append(f"   \u2022 {name}  \u2014 due {due or dash}")
            out.append(f"     {link(SERVICE_RACK, gid)}")
    else:
        out.append("   none")
    out.append("")
    out.append("Source: live Asana (Invoices / Parts Control / Order Numbers / Build Schedule / Service Rack). Read-only.")
    return "\n".join(out)


def main():
    try:
        print(render())
    except Exception as e:
        print(f"REPORT ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
