#!/usr/bin/env python3
"""steel_weld_flip.py — flip Build Schedule STEEL PROFILE to "All Cut" when the
pack's "Weld" subtask is COMPLETED.

WHAT IT DOES
  For every INCOMPLETE Build Schedule pack whose STEEL PROFILE is still
  outstanding, check its subtasks. If the "Weld" subtask is completed, the pack
  is physically ready to cut, so the steel status should become "All Cut".

  Outstanding = the same set the dashboard treats as not-yet-done:
    'Not Assessed', 'All in ready to be cut'   (and any blank)
  Anything already 'All Cut' / 'Not needed' is skipped -- nothing to do.

SAFETY (this is a WRITE)
  * DRY-RUN BY DEFAULT. Prints what it would change. Writes nothing.
  * `--commit` is required to perform writes, and is deliberately explicit.
  * Every write is logged to data/asana_write_log.jsonl (timestamp, task gid,
    from-value, outcome) so the change is auditable.
  * Only ever sets STEEL PROFILE to 'All Cut'. It never deletes, never creates,
    and never touches any other field.
  * Per CLAUDE.md, `--commit` should not be run without Giles's explicit
    go-ahead. The dashboard button runs this in DRY-RUN mode and shows the
    result; committing stays a deliberate, separate act.

RELATION TO THE EXISTING SCRIPT
  personal/steel_profile_flip_allcut.py does the same job but only reads the
  build schedule via its own pull and does not handle blanks. This version is
  the dashboard-facing one and adds the blank case. The two should be
  consolidated when the Stage 6 work lands.

Usage:
  .venv/Scripts/python.exe steel_weld_flip.py                 # dry-run
  .venv/Scripts/python.exe steel_weld_flip.py --commit        # live write
  .venv/Scripts/python.exe steel_weld_flip.py --json          # machine output
"""
import os, re, sys, json, time, argparse
from datetime import datetime, timezone
import requests

WS = os.path.expanduser("~/SES_Workspace")
PROJECT = "852956628490855"          # 3.1 Build Schedule
FIELD_STEEL = "1142754335683355"
OPT_ALL_CUT = "1142754335683356"

# Outstanding = not yet decided, so a completed Weld means "go".
OUTSTANDING = {"not assessed", "all in ready to be cut", ""}
DO_NOTHING = {"all cut", "not needed"}

LOG = os.path.join(WS, "data", "asana_write_log.jsonl")


def _token():
    for ep in (os.path.expanduser("~/.hermes/.env"), os.path.expanduser("~/.openclaw/.env")):
        try:
            for line in open(ep):
                if line.startswith("ASANA_PERSONAL_ACCESS_TOKEN"):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
        except FileNotFoundError:
            continue
    return os.environ.get("ASANA_PERSONAL_ACCESS_TOKEN", "")


def _headers():
    return {"Authorization": f"Bearer {_token()}", "Content-Type": "application/json"}


def paged(path, params, headers):
    out, off = [], None
    while True:
        p = dict(params)
        if off:
            p["offset"] = off
        r = requests.get(f"https://app.asana.com/api/1.0/{path}", headers=headers,
                         params=p, timeout=45)
        r.raise_for_status()
        j = r.json()
        out += j.get("data", [])
        off = (j.get("next_page") or {}).get("offset")
        if not off:
            break
        time.sleep(0.1)
    return out


def steel_status(task):
    for cf in task.get("custom_fields", []):
        if cf.get("gid") == FIELD_STEEL:
            return cf.get("display_value")
    return None


def is_pack_ref(name):
    """True when a task name parses as a build pack reference.

    Packs look like '1588.1004.B.MainStairFrame.' -- a 4-digit project prefix
    then dot-separated segments. This deliberately rejects:
      * 'DRAWING' variants           (drawing control, not a build pack)
      * 'QC CONTROL - 1552 - ...'    (whole-lift QC task)
      * '4 off 1597.7110.J.026 - ...' (part/order row)
    """
    n = (name or "").strip()
    if not n or "DRAWING" in n.upper():
        return False
    if not re.match(r"^\d{4}\.", n):
        return False
    parts = [p for p in n.rstrip(".").split(".") if p]
    return len(parts) >= 3


def scan():
    """Return (targets, summary). targets = ready-to-flip packs.

    Only real build PACKS are considered. Build Schedule also holds rows that
    are not packs -- whole-lift 'QC CONTROL - <job>' tasks, and part/order rows
    like '4 off 1597.7110.J.026 - Tube machined to 48'. Those never carry a
    Weld subtask and must not be reported as anomalies, so a row is only a
    candidate when its name parses as a pack reference (>=3 dot-segments with a
    4-digit project prefix).
    """
    H = _headers()
    tasks = paged(f"projects/{PROJECT}/tasks",
                  {"opt_fields": "name,gid,completed,custom_fields.gid,custom_fields.display_value",
                   "limit": 100}, H)
    targets, checked = [], 0
    skipped = {"done": 0, "not_a_pack": 0, "no_weld": 0, "weld_open": 0}
    for t in tasks:
        name = (t.get("name") or "").strip()
        if not is_pack_ref(name):
            skipped["not_a_pack"] += 1
            continue
        if t.get("completed"):
            skipped["done"] += 1
            continue
        status = (steel_status(t) or "").strip()
        if status.lower() in DO_NOTHING:
            continue
        if status.lower() not in OUTSTANDING:
            continue
        checked += 1
        subs = paged(f"tasks/{t['gid']}/subtasks",
                     {"opt_fields": "name,completed,gid", "limit": 100}, H)
        weld = next((s for s in subs
                     if str(s.get("name", "")).strip().lower().startswith("weld")), None)
        if not weld:
            skipped["no_weld"] += 1
            continue
        if weld.get("completed"):
            targets.append({"gid": t["gid"], "name": name,
                            "from": status, "weld_gid": weld["gid"]})
        else:
            skipped["weld_open"] += 1
    return targets, {"checked": checked, "skipped": skipped}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true", help="perform the writes")
    ap.add_argument("--json", action="store_true", help="emit JSON only")
    args = ap.parse_args()

    targets, summary = scan()
    mode = "COMMIT" if args.commit else "DRY-RUN"

    if args.json:
        print(json.dumps({"mode": mode, "targets": targets, "summary": summary}, indent=2))
        if not args.commit:
            return

    ok = 0
    for t in targets:
        if not args.commit:
            continue
        r = requests.put(f"https://app.asana.com/api/1.0/tasks/{t['gid']}",
                         headers=_headers(),
                         json={"data": {"custom_fields": {FIELD_STEEL: OPT_ALL_CUT}}},
                         timeout=45)
        outcome = "success" if r.status_code == 200 else f"FAIL {r.status_code}"
        if r.status_code == 200:
            ok += 1
        t["outcome"] = outcome
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "action": "update_task", "task_gid": t["gid"], "outcome": outcome,
                "note": "STEEL PROFILE -> All Cut (weld subtask complete)",
                "from": t["from"], "actor": "steel_weld_flip.py",
            }) + "\n")
        time.sleep(0.35)

    if not args.json:
        print(f"{mode}: {len(targets)} pack(s) ready to flip to 'All Cut'")
        for t in targets:
            extra = f"  [{t.get('outcome')}]" if "outcome" in t else ""
            print(f"   {t['gid']}  {t['from']!r:26} -> 'All Cut'  {t['name'][:46]}{extra}")
        s = summary["skipped"]
        print(f"\n   checked outstanding: {summary['checked']}")
        print(f"   no Weld subtask    : {s['no_weld']}")
        print(f"   Weld still open    : {s['weld_open']}")
        print(f"   pack already done  : {s['done']}")
        if args.commit:
            print(f"\n   {ok}/{len(targets)} flipped. Logged to {LOG}")
        else:
            print("\n   DRY-RUN — nothing written. Re-run with --commit to apply.")


if __name__ == "__main__":
    main()
