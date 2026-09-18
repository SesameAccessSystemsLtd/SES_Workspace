#!/usr/bin/env python3
"""
Steel Profile — outstanding requirements (READ-ONLY PILOT)
==========================================================
Answers: which Build Schedule parents are OUTSTANDING for steel profile, and
which are READY to flip to "All Cut" because their "Weld" subtask is completed.

READ-ONLY: makes GET calls only. No Asana writes. Safe to run repeatedly.

Source of truth = LIVE Asana project 3.1 Build Schedule (852956628490855).
Do NOT trust the local ops.db build_assembly_packs mirror for steel_profile —
it is known-stale vs live (verified 2026-09-07).

Output:
  - stdout summary table
  - CSV at SES_Workspace/personal/steel_profile_outstanding_<date>.csv  (diff vs Giles's manual run)

Usage:
  .venv/Scripts/python.exe steel_profile_outstanding.py
"""
import os, csv, sys, time
from datetime import datetime, timezone
import requests

PROJECT   = "852956628490855"      # 3.1 Build Schedule
FIELD_STEEL = "1142754335683355"    # STEEL PROFILE
OPT_NOT_ASSESSED = "1142754335683357"
OPT_READY        = "1176676181015819"
OPT_ALL_CUT      = "1142754335683356"
OUTSTANDING = {"Not Assessed", "All in ready to be cut "}   # note trailing space on ready

def _token():
    home = os.path.expanduser("~")
    for ep in [os.path.join(home, ".hermes", ".env"), os.path.join(home, ".openclaw", ".env")]:
        try:
            for line in open(ep):
                if line.startswith("ASANA_PERSONAL_ACCESS_TOKEN"):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
        except FileNotFoundError:
            continue
    return ""

H = {"Authorization": f"Bearer {_token()}"}

def paged(path, params):
    out, off = [], None
    while True:
        p = dict(params); 
        if off: p["offset"] = off
        r = requests.get(f"https://app.asana.com/api/1.0/{path}", headers=H, params=p)
        r.raise_for_status()
        j = r.json()
        out += j.get("data", [])
        off = (j.get("next_page") or {}).get("offset")
        if not off: break
        time.sleep(0.15)
    return out

def steel_val(task):
    for cf in task.get("custom_fields", []):
        if cf.get("gid") == FIELD_STEEL:
            return cf.get("display_value")
    return None

def main():
    print(f"Pulling ALL tasks from Build Schedule {PROJECT} (live)...")
    tasks = paged(f"projects/{PROJECT}/tasks",
                  {"opt_fields": "name,completed,gid,custom_fields.name,custom_fields.gid,custom_fields.display_value",
                   "limit": 100})
    print(f"  {len(tasks)} tasks total.")

    outstanding = [t for t in tasks if steel_val(t) in OUTSTANDING]
    print(f"  {len(outstanding)} OUTSTANDING (Not Assessed / All in ready to be cut).\n")

    rows = []
    for t in outstanding:
        gid = t["gid"]
        subs = paged(f"tasks/{gid}/subtasks", {"opt_fields": "name,completed,gid", "limit": 100})
        weld = next((s for s in subs if str(s.get("name", "")).strip().lower().startswith("weld")), None)
        weld_done = bool(weld and weld.get("completed"))
        rows.append({
            "gid": gid,
            "name": (t.get("name") or "").strip(),
            "steel_profile": steel_val(t),
            "parent_completed": bool(t.get("completed")),
            "subtask_count": len(subs),
            "weld_subtask_gid": weld["gid"] if weld else "",
            "weld_completed": weld_done,
            "ready_to_flip_all_cut": weld_done,   # the trigger
            "url": f"https://app.asana.com/0/{PROJECT}/{gid}",
        })
        time.sleep(0.15)

    # summary
    ready = [r for r in rows if r["ready_to_flip_all_cut"]]
    print("="*90)
    print(f"  OUTSTANDING: {len(rows)}   |   READY TO FLIP 'All Cut' (Weld done): {len(ready)}")
    print("="*90)
    for r in sorted(rows, key=lambda x: (not x["ready_to_flip_all_cut"], x["steel_profile"], x["name"])):
        flag = ">> FLIP All Cut" if r["ready_to_flip_all_cut"] else ""
        na = "no Weld subtask" if r["subtask_count"] == 0 else ""
        print(f"  {r['steel_profile'][:22]:24} {'WELD✓' if r['weld_completed'] else '     '} "
              f"{r['subtask_count']:>2}sub {r['name'][:44]:46} {flag}{na}")

    # CSV for diff against Giles's manual run
    outp = os.path.join(os.path.expanduser("~"), "SES_Workspace", "personal",
                        f"steel_profile_outstanding_{datetime.now().strftime('%Y%m%d_%H%M')}.csv")
    os.makedirs(os.path.dirname(outp), exist_ok=True)
    with open(outp, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    print(f"\nCSV written: {outp}")
    print(f"(diff this against your manual run: {len(rows)} outstanding, {len(ready)} ready-to-flip)")

if __name__ == "__main__":
    main()
