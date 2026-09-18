#!/usr/bin/env python
"""
Daily Picking & Booking-in Morning Review (Richard) — 2026-09-01.

Reads LIVE Asana Parts Control, finds Richard's open tasks, and splits them into:
  1) Booking-in candidates  — arrived/in-house (INHOUSE/IN STOCK/On Premises), OR
                              order status ARRIVED / Delivery CONFIRMED, but PC task still open.
  2) Awaiting-picking       — Stock ACTION PLEASE PICK (and the picking signal) assigned to Richard.

Emits a clear priority summary with direct hyperlinks. READ-ONLY on Asana.
Writes no data. On error, exits non-zero and prints a concise diagnostic (no silent failure).
"""
import os
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

# Terminal stock states = already handled (exclude from booking-in / available-for-pick)
TERMINAL_STOCK = {
    "KITTED", "ITEM On  Premises", "ITEM ON PREMISES", "IN STOCK",
    "PICKING COMPLETE", "OBSOLETE", "INHOUSE",
}
ARRIVED_EXCL_STATE = ("KITTED", "OBSOLETE", "PICKING COMPLETE")

# Stock actions indicating the part is ON HAND and awaiting pick / issue
PICK_SIGNALS = {"PLEASE PICK"}

# Order statuses indicating delivery has LANDED (physically arrived) and the PC side has not
# been closed / booked in. NOTE: "Delivery Date CONFIRMED" means a future date is set (still in
# transit) — that alone is NOT "arrived" and must not trigger booking-in.
ARRIVED_ORDER_STATUS = {"✅ARRIVED - MOVES TO INVOICES!!", "ARRIVED - MOVES TO INVOICES",
                        "ARRIVED - NEED TO QC", "ITEM ARRIVED"}


def paginate(url):
    out, off = [], None
    while True:
        p = {"limit": 100, "opt_fields": "name,completed,assignee.name,custom_fields,memberships.section.name"}
        if off:
            p["offset"] = off
        j = requests.get(url, headers=H, params=p, timeout=40).json()
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


def main():
    try:
        tasks = paginate(f"{BASE}/projects/{PARTS_CONTROL}/tasks")
    except RuntimeError as e:
        print(f"SCAN ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    richard_open = [
        t for t in tasks
        if not t.get("completed")
        and (t.get("assignee") or {}).get("gid") == RICHARD_GID
    ]

    booking_in, awaiting_pick = [], []

    for t in richard_open:
        sa = (cfv(t, "Stock ACTION") or "").strip()
        ost = (cfv(t, "ORDER Status") or "").strip()
        name = t.get("name", "")
        gid = t["gid"]

        # Choose a SINGLE destination per task to keep the summary clean.
        # Awaiting-pick first: PLEASE PICK is unambiguously a pick action.
        if sa == "PLEASE PICK":
            awaiting_pick.append((name, gid, sa, ost))
        # Booking-in candidate: physically arrived (IN STOCK / INHOUSE) or the delivery has
        # LANDED (ARRIVED order status), but the PC task is still OPEN (not ticked / not closed
        # through KITTED / PICKING COMPLETE). Richard books these in so they are usable by
        # production and the loop closes.
        elif sa in {"IN STOCK", "INHOUSE"} or ost in ARRIVED_ORDER_STATUS:
            booking_in.append((name, gid, sa, ost))

    print(f"SAS Purchasing — Daily Picking & Booking-in Review · {date.today().isoformat()}")
    print(f"Richard open Parts Control tasks: {len(richard_open)}\n")

    print("## 🔴 1. Need booking-in / arrived but not closed out")
    if not booking_in:
        print("   None.")
    for name, gid, sa, ost in booking_in:
        print(f"   • {name[:80]}")
        print(f"     {sa or '—'} / {ost or '—'} · {link(gid)}")

    print("\n## 🟠 2. Awaiting picking (assigned to Richard)")
    if not awaiting_pick:
        print("   None.")
    for name, gid, sa, ost in awaiting_pick:
        print(f"   • {name[:80]}")
        print(f"     {sa or '—'} / {ost or '—'} · {link(gid)}")

    print("\nSource: live Asana Parts Control (read-only). ")


if __name__ == "__main__":
    main()
