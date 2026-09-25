r"""
audit_order_status_confirmed.py — READ-ONLY audit for the 2026-09-25 ORDER Status defect.

Until 2026-09-25 execute_requests.py sent option 1143955334207043 ('⏰On Order - Awaiting
CONFIRMATION') whenever an approved request asked for 'On Order - Delivery Date Confirmed'.
This script finds every approved request that asked for Confirmed, reads each task LIVE from
Asana (GET only — task + story history) and proposes, per task, what ORDER Status should be:

  * the executor's stale write never overrides a HUMAN status change made after the email
    arrived -> restore the value it overwrote (e.g. Richard's ✅ARRIVED on the RS order)
  * otherwise the approved intent stands -> 🚚On Order - Delivery Date CONFIRMED
  * a human who set ORDER Status after the executor ran is left alone (review list)

It also replays every historical request through the FIXED executor's label resolution
(live field read, nothing written) as proof of the fix.

Writes nothing to Asana, ops.db or the queues. Output:
  reports/order_status_confirmed_audit_<date>.md  (+ .json with the proposed batch)
Run:  ..\..\.venv\Scripts\python.exe audit_order_status_confirmed.py
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import execute_requests as ex  # noqa: E402  — same parser + live resolver the executor runs
from matcher import ARRIVED_STATUSES, CLOSED_STATUSES  # noqa: E402  — Giles's arrived/closed sets
from path_config import p as _p  # noqa: E402

API = "https://app.asana.com/api/1.0"
WS = Path(__file__).resolve().parents[2]
REPORTS = WS / "reports"
ORDER_NUMBERS = "854650811917168"          # '0 - Order Numbers'
FIELD = ex.ORDER_STATUS_GID
AWAITING = "1143955334207043"               # checked against the live field at run time
CONFIRMED_KEY = ex._norm_label("On Order - Delivery Date Confirmed")
PAST_CONFIRMED = {ex._norm_label(s) for s in CLOSED_STATUSES | ARRIVED_STATUSES | {"TO BE RETURNED "}}
EXECUTOR_WINDOW = timedelta(seconds=300)    # the PUT lands a few seconds before the file is stamped


def get(path, **params):
    time.sleep(0.35)
    r = requests.get(API + path, headers={"Authorization": ex.ASANA_HEADERS["Authorization"]},
                     params=params, timeout=30)
    return r.status_code, (r.json() if r.content else {})


def ts(value):
    if not value:
        return None
    v = value.strip().replace("Z", "+00:00").replace(" ", "T")
    try:
        d = datetime.fromisoformat(v)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def header(text, key):
    m = re.search(rf"^{key}:\s*(.*)$", text, re.MULTILINE)
    return m.group(1).strip() if m else ""


# ------------------------------------------------------------------ 1. approved requests
def scan_requests():
    out = []
    for folder, outcome in ((ex.REQUESTS_COMPLETE, "COMPLETE"), (ex.REQUESTS_FAILED, "FAILED")):
        for md in sorted(Path(folder).glob("*.md")):
            text = md.read_text(encoding="utf-8", errors="replace")
            payload = {}
            js = md.with_suffix(".json")
            if js.exists():
                try:
                    payload = json.loads(js.read_text(encoding="utf-8"))
                except (ValueError, UnicodeDecodeError):
                    payload = {}
            action = payload.get("action") or ""
            if not action:
                m = re.search(r"^Details:\n(.+?)(?=\n\n|\nComment To Push:|\n## Context)", text, re.MULTILINE | re.DOTALL)
                action = m.group(1).strip() if m else ""
            ops = ex.parse_actions(action)
            labels = [o for o in ops if o["type"] == "enum_label"]
            if not labels or ex._norm_label(labels[0]["label"]) != CONFIRMED_KEY:
                continue
            task_gid = (payload.get("task_gid") or header(text, "Task GID")).strip()
            rec = re.search(r"received (\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2})", text) or \
                re.search(r"Email received: (\S+)", text)
            out.append({
                "file": md.name, "outcome": outcome, "task_gid": task_gid,
                "task_name": payload.get("task_name") or header(text, "Task Name"),
                "created": header(text, "Created"), "completed": header(text, "Completed"),
                "note": header(text, "Note"), "decision": payload.get("decision") or "",
                "email_received": rec.group(1) if rec else "",
                "format": "inline option GID " + ",".join(labels[0]["claimed_enum_gids"])
                          if labels[0]["claimed_enum_gids"] else "label only",
                "ops": ops,
            })
    return out


# ------------------------------------------------------------------ 2. live task + history
def live_task(gid):
    code, body = get(f"/tasks/{gid}", opt_fields=(
        "name,completed,completed_at,due_on,permalink_url,parent.name,"
        "memberships.project.gid,memberships.project.name,memberships.section.name,"
        "custom_fields.gid,custom_fields.enum_value.gid,custom_fields.enum_value.name"))
    if code != 200:
        return None
    t = body["data"]
    cf = next((c for c in t.get("custom_fields") or [] if c.get("gid") == FIELD), None)
    t["status"] = (cf or {}).get("enum_value") or {}
    t["has_field"] = cf is not None
    return t


def status_history(gid):
    """ORDER Status change stories, oldest first: [{at, by_gid, by, old, new, new_gid}]."""
    rows, offset = [], None
    while True:
        params = {"limit": 100, "opt_fields": "created_at,created_by.gid,created_by.name,resource_subtype,"
                  "custom_field.gid,old_enum_value.gid,old_enum_value.name,new_enum_value.gid,new_enum_value.name"}
        if offset:
            params["offset"] = offset
        code, body = get(f"/tasks/{gid}/stories", **params)
        if code != 200:
            break
        for s in body.get("data") or []:
            if s.get("resource_subtype") == "enum_custom_field_changed" and (s.get("custom_field") or {}).get("gid") == FIELD:
                rows.append({"at": s["created_at"], "by_gid": (s.get("created_by") or {}).get("gid"),
                             "by": (s.get("created_by") or {}).get("name") or "Asana rule",
                             "old": (s.get("old_enum_value") or {}).get("name"),
                             "old_gid": (s.get("old_enum_value") or {}).get("gid"),
                             "new": (s.get("new_enum_value") or {}).get("name"),
                             "new_gid": (s.get("new_enum_value") or {}).get("gid")})
        offset = (body.get("next_page") or {}).get("offset")
        if not offset:
            break
    return rows


def mirror_rows():
    try:
        conn = sqlite3.connect(f"file:{_p('db.ops')}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        rows = {r["asana_task_gid"]: dict(r) for r in conn.execute(
            "SELECT asana_task_gid, order_status, due_date, is_completed, source_project FROM procurement_tasks")}
        conn.close()
        return rows
    except sqlite3.Error:
        return {}


# ------------------------------------------------------------------ 3. classify
def classify(task, history, reqs, confirmed):
    """-> (group, (target_gid, target_label) or None, reason). `confirmed` = (gid, label)."""
    if task is None:
        return "E", None, "task not readable (deleted / no access)"
    if not task["has_field"]:
        return "D", None, "ORDER Status field not on this task any more"
    cur_gid, cur = task["status"].get("gid"), task["status"].get("name") or "(blank)"
    if cur_gid != AWAITING:
        if cur_gid == confirmed[0]:
            return "E", None, "already Delivery Date CONFIRMED"
        return "E", None, f"moved on since - now '{cur}'"

    done = [d for d in (ts(r["completed"]) for r in reqs) if d]
    exec_at = max(done) if done else None
    # The executor's own writes: set Awaiting, within minutes of a request being stamped.
    by_executor = lambda h: h["new_gid"] == AWAITING and any(abs(ts(h["at"]) - d) <= EXECUTOR_WINDOW for d in done)
    later = [h for h in history if exec_at and ts(h["at"]) > exec_at and not by_executor(h)]
    if later:
        h = later[-1]
        return "D", None, (f"{h['by']} set ORDER Status to '{h['new']}' on {h['at'][:10]}, after the executor ran "
                           "- human decision, left alone")

    received = min((d for d in (ts(r["email_received"]) for r in reqs) if d), default=None)
    exec_writes = [h for h in history if by_executor(h)]
    between = [h for h in history if received and exec_at and received < ts(h["at"]) < exec_at and not by_executor(h)]
    last = exec_writes[-1] if exec_writes else None
    if last and last["old_gid"] and (between or ex._norm_label(last["old"]) in PAST_CONFIRMED):
        why = (f"{between[-1]['by']} moved it to '{between[-1]['new']}' on {between[-1]['at'][:10]} after the email arrived; "
               if between else f"'{last['old']}' was already past confirmation; ")
        return "B", (last["old_gid"], last["old"]), why + f"the stale approval overwrote it on {last['at'][:10]}"
    if task.get("completed"):
        return "C", confirmed, "completed while still Awaiting - the approved intent was CONFIRMED"
    if not any((m.get("project") or {}).get("gid") == ORDER_NUMBERS for m in task.get("memberships") or []):
        return "D", confirmed, "open but not in 0 - Order Numbers - confirm it is a live order first"
    return "A", confirmed, "approved confirmation never landed"


# ------------------------------------------------------------------ 4. report
def md_table(rows, cols):
    head = "| " + " | ".join(c for c, _ in cols) + " |\n|" + "---|" * len(cols) + "\n"
    return head + "".join("| " + " | ".join(str(f(r)).replace("|", "/") for _, f in cols) + " |\n" for r in rows)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    today = date.today().isoformat()
    name, options = ex.fetch_enum_field(FIELD)
    by_gid = {o["gid"]: o["name"] for o in options}
    confirmed_gid, confirmed_label = ex.resolve_enum_option(FIELD, "On Order - Delivery Date Confirmed", ex.ORDER_STATUS_NAME)
    if ex._norm_label(by_gid.get(AWAITING)) != ex._norm_label("On Order - Awaiting CONFIRMATION"):
        sys.exit(f"option {AWAITING} is no longer 'Awaiting CONFIRMATION' - stop and re-check")

    reqs = scan_requests()
    print(f"{len(reqs)} approved requests asked for Delivery Date Confirmed")

    # Proof: what the fixed executor now does with each historical request (live field, no write).
    for r in reqs:
        try:
            resolved = ex.resolve_enum_ops(r["ops"])
            r["fixed_executor"] = "would write " + next(o["enum_gid"] for o in resolved if o["type"] == "custom_field_enum")
        except ex.EnumResolutionError as exc:
            r["fixed_executor"] = f"would FAIL, nothing written: {exc}"
        del r["ops"]

    tasks = {}
    for r in reqs:
        tasks.setdefault(r["task_gid"], []).append(r)
    mirror = mirror_rows()
    rows = []
    for i, (gid, rq) in enumerate(sorted(tasks.items()), 1):
        print(f"  [{i}/{len(tasks)}] {gid}")
        task = live_task(gid) if gid.isdigit() else None
        hist = status_history(gid) if task else []
        group, target, reason = classify(task, hist, rq, (confirmed_gid, confirmed_label))
        if target and not any(o["gid"] == target[0] and o.get("enabled") is not False for o in options):
            group, reason, target = "D", reason + f" - but option {target[0]} is not an enabled live option", None
        mrow = mirror.get(gid) or {}
        due = (mrow.get("due_date") or "")[:10]
        rows.append({
            "task_gid": gid, "group": group, "reason": reason,
            "name": (task or {}).get("name") or rq[-1]["task_name"],
            "url": (task or {}).get("permalink_url") or f"https://app.asana.com/1/357939803556123/task/{gid}",
            "completed": bool((task or {}).get("completed")),
            "in_order_numbers": any((m.get("project") or {}).get("gid") == ORDER_NUMBERS
                                    for m in (task or {}).get("memberships") or []),
            "projects": [(m.get("project") or {}).get("name") for m in (task or {}).get("memberships") or []],
            "live_status": ((task or {}).get("status") or {}).get("name"),
            "live_status_gid": ((task or {}).get("status") or {}).get("gid"),
            "target_label": target[1] if target else None,
            "target_gid": target[0] if target else None,
            "due_on": (task or {}).get("due_on"),
            "mirror_status": mrow.get("order_status"),
            "chased_by_rule_a": bool(mrow and "Awaiting CONFIRMATION" in (mrow.get("order_status") or "")
                                     and due and due < today),
            "requests": [{k: r[k] for k in ("file", "outcome", "completed", "email_received", "format",
                                            "decision", "fixed_executor")} for r in rq],
            "status_history": hist,
        })

    batch = [r for r in rows if r["group"] in ("A", "B", "C") and r["target_gid"]]
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    REPORTS.mkdir(exist_ok=True)
    out_json = REPORTS / f"order_status_confirmed_audit_{today}.json"
    out_md = REPORTS / f"order_status_confirmed_audit_{today}.md"
    out_json.write_text(json.dumps({
        "generated_at": stamp, "field_gid": FIELD, "field_name": name,
        "awaiting": {"gid": AWAITING, "label": by_gid[AWAITING]},
        "confirmed": {"gid": confirmed_gid, "label": confirmed_label},
        "requests": reqs, "tasks": rows,
        "proposed_batch": [{k: r[k] for k in ("task_gid", "name", "group", "live_status", "live_status_gid",
                                              "target_label", "target_gid", "reason")} for r in batch],
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    groups = {
        "A": "A - open order, set to Delivery Date CONFIRMED (approved intent never landed)",
        "B": "B - restore the status the stale approval overwrote",
        "C": "C - completed task still Awaiting: set CONFIRMED? (Giles's call)",
        "D": "D - review only, no write proposed",
        "E": "E - no action",
    }
    cols = [("Task", lambda r: f"[{r['name'][:60]}]({r['url']})"), ("GID", lambda r: r["task_gid"]),
            ("Done", lambda r: "yes" if r["completed"] else "no"),
            ("Order Nos", lambda r: "yes" if r["in_order_numbers"] else "no"),
            ("Live now", lambda r: r["live_status"] or "-"), ("Proposed", lambda r: r["target_label"] or "-"),
            ("Rule A chase", lambda r: "YES" if r["chased_by_rule_a"] else "no"),
            ("Executed", lambda r: ", ".join((q["completed"] or q["file"])[:10] for q in r["requests"])),
            ("Why", lambda r: r["reason"])]
    lines = [f"# ORDER Status 'Confirmed' defect — read-only audit ({stamp})", "",
             "Generated by `skills/purchasing/audit_order_status_confirmed.py`. **Nothing was written to Asana.**", "",
             f"- Approved requests that asked for Delivery Date Confirmed: **{len(reqs)}** "
             f"({sum(r['format'] == 'label only' for r in reqs)} label-only, "
             f"{sum(r['format'] != 'label only' for r in reqs)} with inline option GID {AWAITING})",
             f"- Distinct tasks: **{len(rows)}** · proposed writes: **{len(batch)}** "
             f"(A {sum(r['group'] == 'A' for r in batch)} · B {sum(r['group'] == 'B' for r in batch)} · "
             f"C {sum(r['group'] == 'C' for r in batch)})",
             f"- Live options: Awaiting = `{AWAITING}` '{by_gid[AWAITING]}' · Confirmed = `{confirmed_gid}` '{confirmed_label}'", ""]
    for g, title in groups.items():
        sel = [r for r in rows if r["group"] == g]
        lines += [f"## {title} — {len(sel)}", ""]
        lines += [md_table(sel, cols) if sel else "_none_", ""]
    fixed = {}
    for r in reqs:
        key = r["fixed_executor"].split(":")[0] if r["fixed_executor"].startswith("would FAIL") else r["fixed_executor"]
        fixed[key] = fixed.get(key, 0) + 1
    lines += ["## Proof — historical requests replayed through the fixed executor (live field, no write)", ""]
    lines += [f"- {k}: **{v}**" for k, v in sorted(fixed.items())] + [""]
    out_md.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {out_md} ({out_md.stat().st_size} bytes)\nwrote {out_json} ({out_json.stat().st_size} bytes)")
    for g in groups:
        print(f"  group {g}: {sum(r['group'] == g for r in rows)}")


if __name__ == "__main__":
    main()
