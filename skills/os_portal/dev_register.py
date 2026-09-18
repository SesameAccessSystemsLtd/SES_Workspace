#!/usr/bin/env python3
"""dev_register.py — the open-development-work register.

WHY: development forks across Telegram/Hermes, Claude, Codex and ChatGPT sessions.
Each leaves files behind; nothing marks a thread open or closed, so work is started
and never signed off. This is the ledger that fixes that.

MODEL (deliberately unlike queues/REQUESTS — see
docs/dev-work-register-decision-2026-09-17.md):
  * One file per thread:  queues/DEV_OPEN/<id>.json
  * Files are NEVER moved and state is NEVER overwritten. Every change appends to
    `events[]`; current state is derived from the last event. The original record
    survives, so "what was this meant to do" is always answerable.
  * Asana (project "Giles Ai Dev work") is the system of record for triage —
    priority, area, sign-off. This file is the durable local buffer and audit
    trail, so an agent can log with Asana unreachable and sync later.

STATES:  open -> awaiting_signoff -> closed | cancelled
Only a human closes. Agents may set open / awaiting_signoff only.

USAGE
  # an agent logs a new thread before writing code
  python dev_register.py log --objective "..." --where "path[,path]" \
      --channel hermes:purchasing --area "VPS / Webhooks" --effort M

  python dev_register.py event <id> --status awaiting_signoff --note "ready for review"
  python dev_register.py list [--status open] [--stale-hours 48]
  python dev_register.py sync              # push unsynced threads to Asana
  python dev_register.py check             # nightly: stale + awaiting-signoff -> Telegram
  python dev_register.py probe             # verify env, paths, Asana reachability
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

_WS = Path(__file__).resolve().parents[2]              # SES_Workspace
sys.path.insert(0, str(_WS / "lib"))
sys.path.insert(0, str(_WS / "skills" / "purchasing"))

REGISTER_DIR = _WS / "queues" / "DEV_OPEN"
ENV_PATH = Path(r"C:/Users/GilesHugo/.hermes/.env")

# ── Asana config — read live from the workspace on 2026-09-17. If Asana's schema
#    changes, re-read it rather than editing these by hand; `probe` will flag drift.
ASANA_ORG = "357939803556123"
DEV_PROJECT_GID = "1147424298774210"          # "Giles Ai Dev work"
CF_PRIORITY = "1216356798129221"
CF_STATUS = "1216356799734180"
CF_EFFORT = "1216357254285781"
CF_AREA = "1216356954621557"

PRIORITY_OPTS = {"Critical": "1216356798129222", "High": "1216356798129223",
                 "Medium": "1216356798129224", "Low": "1216356798129225"}
STATUS_OPTS = {"Not Started": "1216356799734181", "In Progress": "1216356799734182",
               "Blocked": "1216356799734183", "Done": "1216356799734184",
               "Cancelled": "1216356799734185"}
EFFORT_OPTS = {"S": "1216357254285782", "M": "1216357254285783",
               "L": "1216357254285784", "XL": "1216357254285785"}
AREA_OPTS = {"Paths / Migration": "1216356954621558", "Data Health": "1216356954621559",
             "Ordering / Inventory": "1216356954621560", "VPS / Webhooks": "1216356954621561",
             "Documentation": "1216356954621562", "Review / Feedback": "1216356954621563",
             "Edge Cases": "1216356954621564"}
AREA_SECTIONS = {"Paths / Migration": "1216357405921584", "Data Health": "1216357256092340",
                 "Ordering / Inventory": "1216357256037977", "VPS / Webhooks": "1216357087488035",
                 "Documentation": "1216357405910021", "Review / Feedback": "1216357153233507",
                 "Edge Cases": "1216357153626617"}

# register state -> Asana Status option.
# NOTE 2026-09-17: the Asana Status field has no "Awaiting sign-off" option
# (Not Started / In Progress / Blocked / Done / Cancelled). Until one is added,
# awaiting_signoff maps to In Progress and the sign-off signal is carried by the
# nightly Telegram report instead. Adding an option is a one-click change in
# Asana; if you make it, add its gid to STATUS_OPTS and remap here.
STATE_TO_ASANA = {"open": "In Progress", "awaiting_signoff": "In Progress",
                  "closed": "Done", "cancelled": "Cancelled", "blocked": "Blocked"}
VALID_STATES = {"open", "awaiting_signoff", "blocked", "closed", "cancelled"}
AGENT_STATES = {"open", "awaiting_signoff", "blocked"}      # agents may not close


def load_env() -> None:
    """Merge ~/.hermes/.env (Doppler-synced) without clobbering the real env.
    Same contract as bom_dispatch_server.load_env()."""
    if not ENV_PATH.exists():
        return
    try:
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            os.environ.setdefault(k.strip(), v)
    except OSError as e:
        print(f"[warn] could not read {ENV_PATH}: {e}", file=sys.stderr)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(text: str, n: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s[:n].rstrip("-") or "thread")


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, rec: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rec, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)                       # atomic; never a half-written record


def _all() -> list[dict]:
    if not REGISTER_DIR.exists():
        return []
    out = []
    for f in sorted(REGISTER_DIR.glob("*.json")):
        try:
            out.append(_read(f))
        except (OSError, json.JSONDecodeError) as e:
            print(f"[warn] unreadable register file {f.name}: {e}", file=sys.stderr)
    return out


def state_of(rec: dict) -> str:
    evs = rec.get("events") or []
    return evs[-1].get("status", "open") if evs else "open"


def last_touch(rec: dict) -> datetime:
    evs = rec.get("events") or []
    ts = evs[-1].get("at") if evs else rec.get("created_at")
    try:
        return datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)


def path_for(rec_id: str) -> Path:
    hits = list(REGISTER_DIR.glob(f"*{rec_id}*.json"))
    if not hits:
        raise SystemExit(f"no register entry matching {rec_id!r}")
    if len(hits) > 1:
        raise SystemExit("ambiguous id, matches: " + ", ".join(h.name for h in hits))
    return hits[0]


# ── commands ────────────────────────────────────────────────────────────────
def cmd_log(a) -> None:
    where = [w.strip() for w in (a.where or "").split(",") if w.strip()]
    if not a.objective.strip():
        raise SystemExit("--objective is required and must say what DONE looks like")
    if not where:
        raise SystemExit("--where is required: the paths/repo this work touches")
    if a.area and a.area not in AREA_OPTS:
        raise SystemExit(f"--area must be one of: {', '.join(AREA_OPTS)}")

    # de-dupe: an identical objective already open means RESUME, not a second thread
    for rec in _all():
        if rec["objective"].strip().lower() == a.objective.strip().lower() \
                and state_of(rec) in ("open", "blocked", "awaiting_signoff"):
            print(json.dumps({"ok": True, "resumed": rec["id"],
                              "note": "identical objective already open — resume it"}, indent=2))
            return

    rid = uuid.uuid4().hex[:8]
    rec = {
        "id": rid,
        "objective": a.objective.strip(),
        "where": where,
        "channel": a.channel or os.environ.get("HERMES_CHANNEL", "unknown"),
        "area": a.area or "",
        "effort": a.effort or "",
        "priority": a.priority or "Medium",
        "created_at": _now(),
        "asana_gid": None,
        "events": [{"at": _now(), "status": "open", "by": a.channel or "agent",
                    "note": "thread opened"}],
    }
    p = REGISTER_DIR / f"{datetime.now(timezone.utc):%Y-%m-%dT%H-%M}-{_slug(a.objective)}-{rid}.json"
    _write(p, rec)
    print(json.dumps({"ok": True, "id": rid, "file": str(p)}, indent=2))


def cmd_event(a) -> None:
    if a.status not in VALID_STATES:
        raise SystemExit(f"--status must be one of: {', '.join(sorted(VALID_STATES))}")
    if a.status in ("closed", "cancelled") and not a.human:
        raise SystemExit("closing is a human action — pass --human to confirm you are Giles")
    p = path_for(a.id)
    rec = _read(p)
    rec["events"].append({"at": _now(), "status": a.status,
                          "by": "giles" if a.human else (a.by or "agent"),
                          "note": a.note or ""})
    _write(p, rec)
    print(json.dumps({"ok": True, "id": rec["id"], "state": a.status}, indent=2))


def cmd_amend(a) -> None:
    """Reword an objective (or retag) WITHOUT losing what it originally said.
    The old value is preserved in the event log, so 'what was this meant to do
    when it was opened' stays answerable. Added 2026-09-17: a thread's finding
    often sharpens after investigation, and syncing a stale objective to Asana
    just puts the staleness somewhere more visible."""
    p = path_for(a.id)
    rec = _read(p)
    changed = {}
    for field, val in (("objective", a.objective), ("area", a.area),
                       ("priority", a.priority), ("effort", a.effort)):
        if val is None:
            continue
        if field == "area" and val not in AREA_OPTS:
            raise SystemExit(f"--area must be one of: {', '.join(AREA_OPTS)}")
        changed[field] = {"from": rec.get(field), "to": val}
        rec[field] = val
    if not changed:
        raise SystemExit("nothing to amend — pass --objective/--area/--priority/--effort")
    rec["events"].append({"at": _now(), "status": state_of(rec),
                          "by": "giles" if a.human else (a.by or "agent"),
                          "note": a.note or "amended", "amended": changed})
    _write(p, rec)
    if rec.get("asana_gid"):
        print(f"note: already synced to Asana task {rec['asana_gid']} — the local record "
              f"is amended but the Asana task name is NOT. Update it there by hand.",
              file=sys.stderr)
    print(json.dumps({"ok": True, "id": rec["id"], "amended": changed}, indent=2))


def cmd_list(a) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=a.stale_hours)
    rows = []
    for rec in _all():
        st = state_of(rec)
        if a.status and st != a.status:
            continue
        if not a.all and st in ("closed", "cancelled"):
            continue
        t = last_touch(rec)
        rows.append({"id": rec["id"], "state": st, "stale": t < cutoff,
                     "age_h": round((datetime.now(timezone.utc) - t).total_seconds() / 3600, 1),
                     "channel": rec.get("channel", ""), "area": rec.get("area", ""),
                     "asana": rec.get("asana_gid"), "objective": rec["objective"],
                     "where": rec.get("where", [])})
    rows.sort(key=lambda r: (-r["age_h"],))
    if a.json:
        print(json.dumps(rows, indent=2))
        return
    if not rows:
        print("register is empty — nothing open.")
        return
    print(f"{'id':10} {'state':17} {'age':>7}  {'channel':22} objective")
    print("-" * 110)
    for r in rows:
        flag = " !" if r["stale"] else "  "
        print(f"{r['id']:10} {r['state']:17} {r['age_h']:>6}h{flag}{r['channel'][:22]:22} "
              f"{r['objective'][:50]}")
    print(f"\n{len(rows)} threads.  ! = untouched for >{a.stale_hours}h.  "
          f"{sum(1 for r in rows if not r['asana'])} not yet in Asana.")


def _asana(method, path, payload=None):
    import requests
    tok = os.environ.get("ASANA_PERSONAL_ACCESS_TOKEN")
    if not tok:
        raise SystemExit("ASANA_PERSONAL_ACCESS_TOKEN not set (check ~/.hermes/.env)")
    r = requests.request(method, f"https://app.asana.com/api/1.0{path}",
                         headers={"Authorization": f"Bearer {tok}",
                                  "Content-Type": "application/json"},
                         json=payload, timeout=30)
    if r.status_code >= 300:
        return None, f"HTTP {r.status_code}: {r.text[:200]}"
    return r.json().get("data"), ""


def cmd_sync(a) -> None:
    """Push threads that have no Asana task yet. Idempotent: a thread with an
    asana_gid is never re-created. Never closes an Asana task — sign-off is
    a human act in Asana, mirrored back by `pull`."""
    load_env()
    made, failed = [], []
    for rec in _all():
        if rec.get("asana_gid") or state_of(rec) in ("closed", "cancelled"):
            continue
        area = rec.get("area") or ""
        cf = {CF_STATUS: STATUS_OPTS.get(STATE_TO_ASANA.get(state_of(rec), "In Progress"),
                                         STATUS_OPTS["In Progress"]),
              CF_PRIORITY: PRIORITY_OPTS.get(rec.get("priority") or "Medium",
                                             PRIORITY_OPTS["Medium"])}
        if rec.get("effort") in EFFORT_OPTS:
            cf[CF_EFFORT] = EFFORT_OPTS[rec["effort"]]
        if area in AREA_OPTS:
            cf[CF_AREA] = AREA_OPTS[area]
        notes = (f"{rec['objective']}\n\n"
                 f"Opened by: {rec.get('channel')}\n"
                 f"Touching: {', '.join(rec.get('where', []))}\n"
                 f"Register id: {rec['id']}\n"
                 f"(logged automatically by dev_register.py — close here to sign off)")
        if a.dry_run:
            print(f"[dry-run] would create: {rec['objective'][:70]}")
            continue
        data, err = _asana("POST", "/tasks", {"data": {
            "projects": [DEV_PROJECT_GID], "name": rec["objective"][:250],
            "notes": notes, "custom_fields": cf}})
        if err:
            failed.append((rec["id"], err))
            continue
        gid = data["gid"]
        if area in AREA_SECTIONS:
            _asana("POST", f"/sections/{AREA_SECTIONS[area]}/addTask", {"data": {"task": gid}})
        rec["asana_gid"] = gid
        rec["events"].append({"at": _now(), "status": state_of(rec), "by": "dev_register.sync",
                              "note": f"created Asana task {gid}"})
        _write(path_for(rec["id"]), rec)
        made.append((rec["id"], gid))
    for rid, gid in made:
        print(f"synced {rid} -> https://app.asana.com/1/{ASANA_ORG}/task/{gid}")
    for rid, err in failed:
        print(f"FAILED {rid}: {err}", file=sys.stderr)
    print(f"\n{len(made)} created, {len(failed)} failed.")


def cmd_check(a) -> None:
    """Nightly. Anything awaiting sign-off, or open and untouched beyond the
    threshold, goes to Giles in ONE message. Sends nothing when there is nothing
    to say — a nightly 'all clear' trains you to ignore it."""
    load_env()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=a.stale_hours)
    signoff, stale = [], []
    for rec in _all():
        st = state_of(rec)
        if st == "awaiting_signoff":
            signoff.append(rec)
        elif st in ("open", "blocked") and last_touch(rec) < cutoff:
            stale.append(rec)
    if not signoff and not stale:
        print("nothing to report.")
        return

    def line(r):
        url = (f' · <a href="https://app.asana.com/1/{ASANA_ORG}/task/{r["asana_gid"]}">asana</a>'
               if r.get("asana_gid") else " · <i>not in asana</i>")
        return f"• <b>{r['objective'][:70]}</b> — {r.get('channel', '?')}{url}"

    parts = ["🧾 <b>Dev register — nightly</b>"]
    if signoff:
        parts += ["", f"<b>Awaiting your sign-off ({len(signoff)})</b>"] + [line(r) for r in signoff]
    if stale:
        parts += ["", f"<b>Open, untouched &gt;{a.stale_hours}h ({len(stale)})</b>"] + [line(r) for r in stale]
    parts += ["", "<code>dev_register.py list</code> to review · close in Asana to sign off"]
    msg = "\n".join(parts)

    if a.dry_run:
        print(msg)
        return
    try:
        from notify_telegram import send_telegram
        ok = send_telegram(msg)
    except Exception as e:                      # noqa: BLE001 — never fail the cron
        print(f"[warn] telegram send failed: {e}", file=sys.stderr)
        ok = False
    print(f"reported {len(signoff)} for sign-off, {len(stale)} stale; telegram={'ok' if ok else 'FAILED'}")


def cmd_probe(a) -> None:
    load_env()
    out = {
        "register_dir": str(REGISTER_DIR),
        "register_dir_exists": REGISTER_DIR.exists(),
        "threads": len(_all()),
        "env_file": str(ENV_PATH), "env_file_exists": ENV_PATH.exists(),
        "asana_token_present": bool(os.environ.get("ASANA_PERSONAL_ACCESS_TOKEN")),
    }
    if out["asana_token_present"]:
        data, err = _asana("GET", f"/projects/{DEV_PROJECT_GID}?opt_fields=name,archived")
        out["asana_project"] = data or err
        cfs, err2 = _asana(
            "GET", f"/projects/{DEV_PROJECT_GID}?opt_fields=custom_field_settings.custom_field.gid,"
                   "custom_field_settings.custom_field.name")
        live = {c["custom_field"]["gid"] for c in (cfs or {}).get("custom_field_settings", [])} \
            if cfs else set()
        expected = {CF_PRIORITY, CF_STATUS, CF_EFFORT, CF_AREA}
        out["custom_field_drift"] = sorted(expected - live) or "none"
    print(json.dumps(out, indent=2, default=str))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("log", help="open a new dev thread")
    p.add_argument("--objective", required=True, help="one sentence: what DONE looks like")
    p.add_argument("--where", required=True, help="comma-separated paths / repo this touches")
    p.add_argument("--channel", help="e.g. hermes:purchasing, claude-cli, chatgpt")
    p.add_argument("--area", help=" | ".join(AREA_OPTS))
    p.add_argument("--effort", choices=list(EFFORT_OPTS))
    p.add_argument("--priority", choices=list(PRIORITY_OPTS))
    p.set_defaults(func=cmd_log)

    p = sub.add_parser("event", help="append a state change")
    p.add_argument("id")
    p.add_argument("--status", required=True, help=" | ".join(sorted(VALID_STATES)))
    p.add_argument("--note")
    p.add_argument("--by")
    p.add_argument("--human", action="store_true", help="required to close or cancel")
    p.set_defaults(func=cmd_event)

    p = sub.add_parser("amend", help="reword objective / retag, preserving the original")
    p.add_argument("id")
    p.add_argument("--objective")
    p.add_argument("--area", help=" | ".join(AREA_OPTS))
    p.add_argument("--priority", choices=list(PRIORITY_OPTS))
    p.add_argument("--effort", choices=list(EFFORT_OPTS))
    p.add_argument("--note")
    p.add_argument("--by")
    p.add_argument("--human", action="store_true")
    p.set_defaults(func=cmd_amend)

    p = sub.add_parser("list")
    p.add_argument("--status")
    p.add_argument("--all", action="store_true", help="include closed/cancelled")
    p.add_argument("--stale-hours", type=int, default=48)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("sync", help="create Asana tasks for unsynced threads")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("check", help="nightly stale + sign-off report")
    p.add_argument("--stale-hours", type=int, default=48)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("probe", help="verify env, paths and Asana schema")
    p.set_defaults(func=cmd_probe)

    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
