#!/usr/bin/env python
"""
Daily Picking & Booking-in Morning Review (Richard).

Reads LIVE Asana Parts Control, finds Richard's open tasks, and splits them into:
  1) Booking-in / arrived  — physically received stock (IN STOCK) OR an order that has genuinely
                             LANDED (ARRIVED status), where the PC task is still open.
  2) Awaiting picking      — Stock ACTION = PLEASE PICK (assigned to Richard).

IMPORTANT scope rules (Giles, 2026-09-03):
  * Everything is scoped to tasks CURRENTLY ASSIGNED TO RICHARD. INHOUSE items are assigned to
    Richard only to CONFIRM MATERIAL IS IN; once he unassigns them they drop out of this report
    automatically (the assignee filter handles it) — even if their parent/order task is still
    "on order".
  * INHOUSE stock_action is EXCLUDED from the booking-in category — in-house manufacture is not a
    delivery to book in. (It is tracked by assignment, per above.)

Output is a TABLE per section with the PART NAME as the hyperlink (no separate link line).
Read-only on Asana. Never writes. On error, exits non-zero with a concise diagnostic.
"""
import os
import re
import sys
from datetime import date

try:
    import requests
except ImportError:
    print("FATAL: requests not available", file=sys.stderr)
    sys.exit(2)

TOKEN = os.environ.get("ASANA_PERSONAL_ACCESS_TOKEN", "")
if not TOKEN:
    print("FATAL: ASANA_PERSONAL_ACCESS_TOKEN not set", file=sys.stderr)
    sys.exit(2)

PARTS_CONTROL = "1211660707080749"
RICHARD_GID = "357614200440807"
BASE = "https://app.asana.com/api/1.0"
H = {"Authorization": f"Bearer {TOKEN}"}

# Stock actions that mean the part is physically received and on hand.
BOOK_IN_STOCK = {"IN STOCK"}

# Stock actions excluded from booking-in entirely (in-house manufacture = assignment-tracked).
EXCLUDE_FROM_BOOKIN = {"INHOUSE"}

# Order statuses meaning the delivery has LANDED (physically arrived).
ARRIVED_ORDER_STATUS = {"ARRIVED - MOVES TO INVOICES", "ARRIVED - NEED TO QC", "ITEM ARRIVED"}

# Emoji / mojibake artefacts to strip from display values (e.g. the mis-decoded 'ðŸ’¢' = 💢).
_ARTIFACT_RE = re.compile(
    r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u2190-\u21FF\u2B00-\u2BFF]"
    r"|\u00f0\u0178.{0,2}"   # cp1252-mis-decoded UTF-8 emoji lead ("ðŸ" + up to 2 trailing chars)
)


def clean(s):
    """Strip emoji / mojibake artefacts and collapse whitespace."""
    if not s:
        return ""
    s = _ARTIFACT_RE.sub("", s)
    s = s.replace("\u200b", "")
    return " ".join(s.split()).strip()


def paginate(url):
    out, off = [], None
    while True:
        p = {"limit": 100, "opt_fields": "name,completed,assignee.name,custom_fields,memberships.section.name"}
        if off:
            p["offset"] = off
        r = requests.get(url, headers=H, params=p, timeout=40)
        r.encoding = "utf-8"
        j = r.json()
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


def link(gid):
    return f"https://app.asana.com/0/{PARTS_CONTROL}/{gid}"


def extract_qty(name):
    """Part task names lead with the qty, e.g. '4 off 1597. 1016.3.001 - ...' or '2\toff\t...'."""
    m = re.match(r"^\s*(\d+)\s*(?:off|Off|OFF)?\b", name or "")
    return m.group(1) if m else ""


def _normalise_status(s):
    """Drop leading emoji then match against known arrived statuses."""
    return clean(s).upper()


def main():
    try:
        tasks = paginate(f"{BASE}/projects/{PARTS_CONTROL}/tasks")
    except RuntimeError as e:
        print(f"SCAN ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    # Scope: OPEN tasks CURRENTLY ASSIGNED TO RICHARD only.
    # (Once Richard unassigns an in-house confirmation item it disappears here.)
    richard_open = [
        t for t in tasks
        if not t.get("completed")
        and (t.get("assignee") or {}).get("gid") == RICHARD_GID
    ]

    booking_in, awaiting_pick = [], []

    for t in richard_open:
        sa = clean(cfv(t, "Stock ACTION"))
        ost = clean(cfv(t, "ORDER Status"))
        name = clean(t.get("name", ""))
        gid = t["gid"]

        if sa == "PLEASE PICK":
            awaiting_pick.append((name, gid, sa, ost))
            continue

        # Booking-in: physically received stock, OR a genuinely LANDED order — but NEVER when the
        # stock action is INHOUSE (in-house manufacture is assignment-tracked, not a delivery).
        arrived_order = _normalise_status(ost) in ARRIVED_ORDER_STATUS
        if sa in EXCLUDE_FROM_BOOKIN:
            continue
        if sa in BOOK_IN_STOCK or arrived_order:
            booking_in.append((name, gid, sa, ost))

    today = date.today().isoformat()
    print(f"SAS Purchasing \u2014 Daily Picking & Booking-in Review \u00b7 {today}")
    print(f"Richard open Parts Control tasks: {len(richard_open)}\n")

    def table(rows):
        if not rows:
            return "   None.\n"
        dash = "\u2014"
        out = ["| Part | Qty | State | Order Status |", "|---|---|---|---|"]
        for name, gid, sa, ost in rows:
            out.append(f"| [{name}]({link(gid)}) | {extract_qty(name) or dash} | {sa or dash} | {ost or dash} |")
        return "\n".join(out) + "\n"

    print("## 1. Need booking-in / arrived but not closed out")
    print(table(booking_in))

    print("## 2. Awaiting picking (assigned to Richard)")
    print(table(awaiting_pick))

    print("Source: live Asana Parts Control (read-only).")


if __name__ == "__main__":
    main()
