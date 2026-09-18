#!/usr/bin/env python3
"""
Steel Profile — flip ready parents to "All Cut" (WRITE)
=======================================================
Sets STEEL PROFILE = "All Cut" (1142754335683356) on Build Schedule parents whose
"Weld" subtask is COMPLETED and whose current STEEL PROFILE is still outstanding
("Not Assessed" or "All in ready to be cut").

Approved by Giles 2026-09-07 (explicit: flip the 6 ready-to-flip tasks).
Logs each write to SES_Workspace/data/asana_write_log.jsonl.

Usage:
  .venv/Scripts/python.exe steel_profile_flip_allcut.py --commit      # live write
  .venv/Scripts/python.exe steel_profile_flip_allcut.py               # dry-run (default)
"""
import os, sys, time, json
from datetime import datetime, timezone
import requests

PROJECT     = "852956628490855"
FIELD_STEEL = "1142754335683355"
OPT_ALL_CUT = "1142754335683356"
OUTSTANDING = {"Not Assessed", "All in ready to be cut "}

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

H = {"Authorization": f"Bearer {_token()}", "Content-Type": "application/json"}

def paged(path, params):
    out, off = [], None
    while True:
        p = dict(params)
        if off: p["offset"] = off
        r = requests.get(f"https://app.asana.com/api/1.0/{path}", headers=H, params=p)
        r.raise_for_status(); j = r.json()
        out += j.get("data", []); off = (j.get("next_page") or {}).get("offset")
        if not off: break
        time.sleep(0.1)
    return out

def sv(t):
    for cf in t.get("custom_fields", []):
        if cf.get("gid") == FIELD_STEEL: return cf.get("display_value")
    return None

def main():
    commit = "--commit" in sys.argv
    tasks = paged(f"projects/{PROJECT}/tasks",
                  {"opt_fields": "name,gid,custom_fields.gid,custom_fields.display_value", "limit": 100})
    targets = []
    for t in tasks:
        if sv(t) not in OUTSTANDING: continue
        subs = paged(f"tasks/{t['gid']}/subtasks", {"opt_fields": "name,completed", "limit": 100})
        weld = next((s for s in subs if str(s.get("name", "")).strip().lower().startswith("weld")), None)
        if weld and weld.get("completed"):
            targets.append((t["gid"], (t.get("name") or "").strip(), sv(t)))

    print(f"{'COMMIT' if commit else 'DRY-RUN'}: {len(targets)} tasks ready to flip -> 'All Cut'")
    logp = os.path.join(os.path.expanduser("~"), "SES_Workspace", "data", "asana_write_log.jsonl")
    ok = 0
    for gid, name, cur in targets:
        if not commit:
            print(f"   [would set] {gid}  {cur!r} -> 'All Cut'  {name[:44]}")
            continue
        r = requests.put(f"https://app.asana.com/api/1.0/tasks/{gid}", headers=H,
                         json={"data": {"custom_fields": {FIELD_STEEL: OPT_ALL_CUT}}})
        status = "success" if r.status_code == 200 else f"FAIL {r.status_code}"
        if r.status_code == 200: ok += 1
        print(f"   [{status}] {gid}  {name[:44]}")
        with open(logp, "a", encoding="utf-8") as f:
            f.write(json.dumps({"timestamp": datetime.now(timezone.utc).isoformat(),
                                "action": "update_task", "task_gid": gid, "outcome": status,
                                "note": "STEEL PROFILE -> All Cut (weld complete)",
                                "from": cur}) + "\n")
        time.sleep(0.35)
    if commit:
        print(f"\n{ok}/{len(targets)} flipped to 'All Cut'. Logged to {logp}")

if __name__ == "__main__":
    main()
