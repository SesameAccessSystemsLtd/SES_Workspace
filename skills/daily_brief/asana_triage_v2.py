"""
asana_triage_v2.py  –  Sesame Access / Giles Hugo
===================================================
Three-layer purchasing intelligence system.

ARCHITECTURE
------------
Layer 1  |  Asana Native Rules      Instant triggers inside Asana (no latency)
         |                          → set urgency on task creation, escalate
         |                            overdue alerts, webhook to external endpoint
         |
Layer 2  |  THIS SCRIPT (cron)      Run by openclaw each morning (e.g. 07:00)
         |                          → detect blocked sub-contract tasks,
         |                            identify unordered items, reschedule stale
         |                            tasks, write back to Asana, emit digest
         |
Layer 3  |  On-demand (Claude/      User or openclaw invokes directly for ad-hoc
         |  openclaw)               decisions, edge cases, complex multi-task
         |                          triage that needs judgement

USAGE
-----
  # Dry run (read-only, safe default for cron):
  python asana_triage_v2.py --dry-run

  # Apply all changes:
  python asana_triage_v2.py --apply

  # Emit markdown digest only (for email/Slack/openclaw):
  python asana_triage_v2.py --dry-run --digest

  # Cron example (openclaw / Task Scheduler):
  0 7 * * 1-5  cd /path/to/scripts && python asana_triage_v2.py --apply >> logs/triage.log 2>&1

SETUP
-----
  set ASANA_PAT=your_token_from_asana_my_apps
  pip install requests
"""

import os
import sys
import csv
import json
import logging
import argparse
from datetime import date, timedelta
from collections import defaultdict

# ── CONFIG ────────────────────────────────────────────────────────────────────
PAT           = os.getenv("ASANA_PAT", "YOUR_PAT_HERE")
WORKSPACE_GID = "357939803556123"

# ── CUSTOM FIELD NAMES (local to your workspace) ──────────────────────────────
URGENCY_FIELD      = "Urgency GH"
ORDER_STATUS_FIELD = "ORDER Status"   # GID auto-discovered at runtime
SIGNIFICANCE_FIELD = "Significance GH"

# ── PROJECT CATEGORIES ────────────────────────────────────────────────────────
# Used for BLOCKED detection and tier classification.
# Edit these if projects are renamed or added.

PURCHASING_PROJECTS = {
    "1.1- Picking / Order control ",
    "1.2 - Goods in / Order Requests",
    "1.3 Project Orders",
    "1.4 Purchasing Dept",
    " 1 -  Supplier List / Engagement ",
    "0 - Order Numbers",
    "QUOTE CONTROL ",
    "Stock Control / Kanban - Tracking ",
    "Motor Pumps POWER PACK STOCK",
}

PRODUCTION_TRACKING_PROJECTS = {
    "Production overdue tasks - Auto control ",
}

MANUFACTURING_PROJECT_PREFIX = (
    "1",  # e.g. 1670 -, 1582 -, 1575 -, 1458 -
)

SERVICE_PROJECTS = {
    "4.1 Service Rack",
    "Institutional Ticketing System / Edge cases",
    "0.2 Sample Control Project",
}

VARIATION_PROJECTS = {
    "0.4 Variations and Additions",
    "3.3 Delay \"Destroyer\" - CAN I LEARN FROM IT?",
}

ISO_HEALTH_PROJECTS = {
    "ISO45001 - Health and Employee standards",
    "First Aid Kit Management",
    "HIDDEN PROJECT FOR AUTO RETASK Training  H&S",
    "9 - Training & Procedures",
    "Label / Asset Control & Register",
    "Tool Control",
}

DEV_IMPROVEMENT_PROJECTS = {
    "Improvements / Change Management Control",
    "ASANA AI KNOWLEDGE AND PROMPT CONTROLS",
    "Giles Priority Organising ",
    "Q&A - Suggestion Box",
    "LEARNINGS",
    "KHWPC Knowledge Hub Web Page Content ",
    "capture feedback on Automations - Giles",
    "Beta testing BEC / BBU",
}

# ── RESCHEDULE RULES (applied in --apply mode) ────────────────────────────────
# Stale tasks with no urgency AND no project → propose closing
CLOSE_THRESHOLD_DAYS   = 365

# Tasks overdue < 90 days in purchasing projects → push to next Monday
RESCHEDULE_PURCHASING_DAYS = 90

# Comment added when a date is pushed
RESCHEDULE_COMMENT = (
    "📅 Date rescheduled by Sesame triage script (asana_triage_v2.py). "
    "Reason: {reason}. Original due: {original}."
)

# ── URGENCY VALUES ─────────────────────────────────────────────────────────────
URGENCY_ORDER = [
    "MUST DO - Urgent Important ",
    "Control Tasks",
    "PARTS ISSUE - In Progress",          # new — add in Asana
    "Need to do - Important not Urgent ",
    "REVIEW",
    "Admin & Updating ",
    "Want to Do  - Quick & Satisfying ",
    "WAITING - Blocked/Dependency",        # new — add in Asana
    "Could do - Not so important",
    "Later",
    "UNCLASSIFIED - Needs Review",         # new — add in Asana
    None,
]

STALENESS_BANDS = [
    (7,    "🔴 <1wk",  "Review now — almost certainly live"),
    (30,   "🟠 <1mo",  "Check status, likely still relevant"),
    (90,   "🟡 <3mo",  "Decide: reschedule or close"),
    (365,  "⚪ <1yr",  "Probably stale — close unless justified"),
    (9999, "💀 1yr+",  "Almost certainly dead — close it"),
]

# ── LOGGING ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M",
)
log = logging.getLogger("triage")


# ── ASANA API ─────────────────────────────────────────────────────────────────
try:
    import requests
except ImportError:
    raise SystemExit("pip install requests")

BASE = "https://app.asana.com/api/1.0"


def _headers():
    return {"Authorization": f"Bearer {PAT}", "Content-Type": "application/json"}


def api_get(path, params=None):
    r = requests.get(f"{BASE}{path}", headers=_headers(), params=params or {})
    r.raise_for_status()
    return r.json()


def api_put(path, data):
    r = requests.put(f"{BASE}{path}", headers=_headers(), json={"data": data})
    r.raise_for_status()
    return r.json()


def api_post(path, data):
    r = requests.post(f"{BASE}{path}", headers=_headers(), json={"data": data})
    r.raise_for_status()
    return r.json()


def get_all_my_incomplete_tasks():
    opt = ",".join([
        "gid", "name", "due_on", "created_at",
        "memberships.project.name", "memberships.project.gid",
        "memberships.section.name",
        "custom_fields.gid", "custom_fields.name",
        "custom_fields.display_value", "custom_fields.enum_value.gid",
    ])
    params = {
        "workspace":       WORKSPACE_GID,
        "assignee":        "me",
        "completed_since": "now",
        "limit":           100,
        "opt_fields":      opt,
    }
    tasks = []
    while True:
        data = api_get("/tasks", params)
        tasks.extend(data["data"])
        log.info(f"Fetched {len(tasks)} tasks...")
        nxt = data.get("next_page")
        if not nxt:
            break
        params = {**params, "offset": nxt["offset"]}
        del params["workspace"], params["assignee"], params["completed_since"]
    return tasks


# ── ENRICHMENT ────────────────────────────────────────────────────────────────

def get_cf(task, field_name):
    for cf in task.get("custom_fields", []):
        if cf.get("name") == field_name:
            return cf.get("display_value"), cf.get("gid"), (
                cf.get("enum_value", {}) or {}
            ).get("gid")
    return None, None, None


def get_projects(task):
    seen, out = set(), []
    for m in task.get("memberships", []):
        p = m.get("project", {})
        n = p.get("name")
        if n and n not in seen:
            out.append(n)
            seen.add(n)
    return out


def classify_tier(projects):
    """Assign a workflow tier based on project membership."""
    pset = set(projects)
    if pset & PRODUCTION_TRACKING_PROJECTS and pset & PURCHASING_PROJECTS:
        return "BLOCKED"          # sub-contract waiting on production
    if pset & PURCHASING_PROJECTS:
        return "PURCHASING"
    if any(p.lstrip().startswith("1") and len(p.split("-")[0].strip()) == 4
           for p in projects):
        return "MANUFACTURING"
    if pset & SERVICE_PROJECTS:
        return "SERVICE"
    if pset & VARIATION_PROJECTS:
        return "VARIATION"
    if pset & ISO_HEALTH_PROJECTS:
        return "ISO/H&S"
    if pset & DEV_IMPROVEMENT_PROJECTS:
        return "DEV/IMPROVE"
    return "UNGROUPED"


def staleness(due_str):
    today = date.today()
    due   = date.fromisoformat(due_str)
    days  = (today - due).days
    if days <= 0:
        return 0, "✅ live", ""
    for thresh, label, action in STALENESS_BANDS:
        if days <= thresh:
            return days, label, action
    return days, "💀 1yr+", "Close it"


def enrich(tasks):
    today = date.today().isoformat()
    out = []
    for t in tasks:
        due = t.get("due_on")
        if not due or due >= today:
            continue
        days_over, band, action = staleness(due)
        urgency_val, urgency_cf_gid, urgency_enum_gid = get_cf(t, URGENCY_FIELD)
        order_val,   order_cf_gid,   _                = get_cf(t, ORDER_STATUS_FIELD)
        projects = get_projects(t)
        tier = classify_tier(projects)
        out.append({
            "gid":             t["gid"],
            "name":            t["name"],
            "due_on":          due,
            "days_over":       days_over,
            "band":            band,
            "action":          action,
            "urgency":         urgency_val,
            "urgency_cf_gid":  urgency_cf_gid,
            "urgency_enum_gid":urgency_enum_gid,
            "order_status":    order_val,
            "order_cf_gid":    order_cf_gid,
            "projects":        projects,
            "tier":            tier,
            "url":             f"https://app.asana.com/0/0/{t['gid']}/f",
        })
    return out


# ── DECISION ENGINE ───────────────────────────────────────────────────────────

def next_monday():
    today = date.today()
    days_ahead = 7 - today.weekday()  # Mon=0
    if days_ahead == 7:
        days_ahead = 7
    return (today + timedelta(days=days_ahead)).isoformat()


def decide(task):
    """
    Returns (action, new_due, reason) where action is one of:
      'CLOSE'      – mark complete (stale, no value)
      'RESCHEDULE' – push due date forward
      'FLAG'       – surface to user, no auto-change
      'SKIP'       – leave alone
    """
    days  = task["days_over"]
    tier  = task["tier"]
    urg   = (task["urgency"] or "").strip()
    order = task["order_status"]

    # BLOCKED sub-contracts: push 1 week silently
    if tier == "BLOCKED":
        return "RESCHEDULE", (date.today() + timedelta(days=7)).isoformat(), \
               "Blocked — sub-contract waiting on production milestone"

    # Control Tasks: flag for human decision — never auto-close
    if urg == "Control Tasks":
        return "FLAG", None, "Control task — needs human sign-off before close"

    # Purchasing items ready to order (no order status, not blocked)
    if tier == "PURCHASING" and not order and days < RESCHEDULE_PURCHASING_DAYS:
        return "RESCHEDULE", next_monday(), "Unordered purchasing item — pushed to next Monday"

    # Stale no-project no-urgency tasks over a year old → close
    if days >= CLOSE_THRESHOLD_DAYS and not task["projects"] and not urg:
        return "CLOSE", None, "Orphaned task, 1yr+ overdue, no urgency — closing"

    # Stale dev/improvement tasks > 90 days → push 30 days
    if tier == "DEV/IMPROVE" and days > 90:
        new_due = (date.today() + timedelta(days=30)).isoformat()
        return "RESCHEDULE", new_due, "Dev/improvement task — deprioritised, pushed 30 days"

    return "SKIP", None, ""


# ── WRITE-BACK ────────────────────────────────────────────────────────────────

def update_due(gid, new_due, dry_run=True):
    if dry_run:
        log.info(f"  [DRY-RUN] Would set due_on={new_due} on task {gid}")
        return
    api_put(f"/tasks/{gid}", {"due_on": new_due})
    log.info(f"  Updated due_on={new_due} on {gid}")


def close_task(gid, dry_run=True):
    if dry_run:
        log.info(f"  [DRY-RUN] Would mark complete: {gid}")
        return
    api_put(f"/tasks/{gid}", {"completed": True})
    log.info(f"  Closed task {gid}")


def add_comment(gid, text, dry_run=True):
    if dry_run:
        log.info(f"  [DRY-RUN] Would comment on {gid}: {text[:60]}...")
        return
    api_post(f"/tasks/{gid}/stories", {"text": text})


def apply_decision(task, action, new_due, reason, dry_run=True):
    if action == "RESCHEDULE":
        comment = RESCHEDULE_COMMENT.format(reason=reason, original=task["due_on"])
        update_due(task["gid"], new_due, dry_run)
        add_comment(task["gid"], comment, dry_run)
    elif action == "CLOSE":
        comment = f"🗑️ Closed by triage script. Reason: {reason}"
        add_comment(task["gid"], comment, dry_run)
        close_task(task["gid"], dry_run)


# ── REPORTS ───────────────────────────────────────────────────────────────────

def print_summary(tasks, decisions):
    today = date.today().isoformat()
    print(f"\n{'='*72}")
    print(f"  SESAME ACCESS — DAILY TRIAGE  {today}")
    print(f"  Total overdue: {len(tasks)}")
    print(f"{'='*72}")

    # Tier breakdown
    by_tier = defaultdict(int)
    for t in tasks:
        by_tier[t["tier"]] += 1
    print("\n  TIER BREAKDOWN")
    tier_order = ["BLOCKED","PURCHASING","MANUFACTURING","SERVICE",
                  "VARIATION","ISO/H&S","DEV/IMPROVE","UNGROUPED"]
    for tier in tier_order:
        if by_tier[tier]:
            print(f"  {tier:25s} {by_tier[tier]:>4}")

    # Decision breakdown
    by_dec = defaultdict(int)
    for _, (act, _, _) in decisions.items():
        by_dec[act] += 1
    print(f"\n  PROPOSED ACTIONS")
    for a, c in sorted(by_dec.items()):
        print(f"  {a:15s} {c:>4}")

    # Critical flags
    flags = [(gid, r) for gid, (a, _, r) in decisions.items() if a == "FLAG"]
    if flags:
        print(f"\n  ⚠️  CONTROL TASKS REQUIRING HUMAN SIGN-OFF ({len(flags)})")
        flagged = {t["gid"]: t for t in tasks}
        for gid, reason in flags:
            t = flagged.get(gid, {})
            print(f"  → {t.get('name','?')[:60]}")
            print(f"     Due: {t.get('due_on')} | {reason}")
            print(f"     {t.get('url','')}")

    print()


def build_digest(tasks, decisions):
    """Markdown digest suitable for email / Slack / openclaw summary."""
    today = date.today().isoformat()
    flagged = {t["gid"]: t for t in tasks}
    lines = [
        f"# Sesame Access — Triage Digest {today}",
        f"**Total overdue:** {len(tasks)}",
        "",
    ]

    # Critical purchasing — what is unordered
    unordered = [t for t in tasks
                 if t["tier"] == "PURCHASING" and not t["order_status"]]
    if unordered:
        lines += [f"## 🛒 Unordered Purchasing Items ({len(unordered)})"]
        for t in sorted(unordered, key=lambda x: x["days_over"], reverse=True):
            lines.append(f"- [{t['name']}]({t['url']}) — {t['days_over']}d overdue")
        lines.append("")

    # Blocked sub-contracts
    blocked = [t for t in tasks if t["tier"] == "BLOCKED"]
    if blocked:
        lines += [f"## ⏸️  Blocked Sub-Contracts ({len(blocked)}) — waiting on production"]
        for t in blocked:
            lines.append(f"- [{t['name']}]({t['url']}) | Projects: {', '.join(t['projects'][:2])}")
        lines.append("")

    # Control tasks flagged
    ctrl_flags = [gid for gid, (a, _, _) in decisions.items() if a == "FLAG"]
    if ctrl_flags:
        lines += [f"## 🎛️  Control Tasks Needing Sign-off ({len(ctrl_flags)})"]
        for gid in ctrl_flags:
            t = flagged.get(gid, {})
            lines.append(f"- [{t.get('name','?')}]({t.get('url','')}) — Due: {t.get('due_on')}")
        lines.append("")

    # Proposed closes
    closes = [gid for gid, (a, _, _) in decisions.items() if a == "CLOSE"]
    if closes:
        lines += [f"## 🗑️  Proposed Closes ({len(closes)}) — stale orphaned tasks"]
        for gid in closes[:10]:  # cap at 10 in digest
            t = flagged.get(gid, {})
            lines.append(f"- {t.get('name','?')} (due: {t.get('due_on')}, {t.get('days_over')}d)")
        if len(closes) > 10:
            lines.append(f"  _(and {len(closes)-10} more...)_")
        lines.append("")

    return "\n".join(lines)


def export_csv(tasks, decisions, filename="triage_output.csv"):
    fields = ["tier","urgency","projects","name","due_on","days_over",
              "band","order_status","decision","new_due","reason","url","gid"]
    dec_map = decisions
    with open(filename, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for t in tasks:
            act, new_due, reason = dec_map.get(t["gid"], ("SKIP", None, ""))
            row = {k: t.get(k, "") for k in fields}
            row["projects"]  = " | ".join(t["projects"])
            row["decision"]  = act
            row["new_due"]   = new_due or ""
            row["reason"]    = reason
            w.writerow(row)
    log.info(f"CSV → {filename}")


# ── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Sesame Access Asana Triage v2")
    parser.add_argument("--apply",   action="store_true",
                        help="Write changes back to Asana (default is dry-run)")
    parser.add_argument("--digest",  action="store_true",
                        help="Print markdown digest to stdout (for openclaw/email)")
    parser.add_argument("--csv",     default="triage_output.csv",
                        help="CSV output filename (default: triage_output.csv)")
    parser.add_argument("--no-csv",  action="store_true",
                        help="Skip CSV export")
    args = parser.parse_args()

    dry_run = not args.apply

    if PAT == "YOUR_PAT_HERE":
        raise SystemExit("⛔  Set ASANA_PAT environment variable first.")

    log.info(f"Mode: {'APPLY' if args.apply else 'DRY-RUN'}")
    log.info("Fetching tasks...")
    raw = get_all_my_incomplete_tasks()
    log.info(f"Total incomplete: {len(raw)}")

    tasks = enrich(raw)
    log.info(f"Overdue: {len(tasks)}")

    # Build decision map
    decisions = {}
    for t in tasks:
        act, new_due, reason = decide(t)
        decisions[t["gid"]] = (act, new_due, reason)
        if act in ("RESCHEDULE", "CLOSE") and not dry_run:
            apply_decision(t, act, new_due, reason, dry_run=False)

    print_summary(tasks, decisions)

    if args.digest:
        print(build_digest(tasks, decisions))

    if not args.no_csv:
        export_csv(tasks, decisions, args.csv)

    # Exit code for cron monitoring (non-zero if critical flags exist)
    flags = sum(1 for a, _, _ in decisions.values() if a == "FLAG")
    if flags:
        log.warning(f"{flags} control tasks need human review — exit 1")
        sys.exit(1)  # cron/openclaw can detect and alert
    sys.exit(0)


if __name__ == "__main__":
    main()
