#!/usr/bin/env python3
"""
daily_brief.py — Navigator Daily Brief Generator

Fetches ALL incomplete Asana tasks assigned to Giles, triages them using Claude,
generates an HTML email brief, and sends it via the email_sender skill.

Run:
    "C:\\Users\\GilesHugo\\SES_Workspace\\.venv\\Scripts\\python.exe" "C:\\Users\\GilesHugo\\SES_Workspace\\skills\\daily_brief\\daily_brief.py"

Dry run (saves HTML preview, no email):
    ... daily_brief.py --dry-run
"""

import sys
import os
import json
import sqlite3
import requests
import logging
import argparse
from datetime import datetime, date, timezone, timedelta
from pathlib import Path

from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")

# ── Path resolution ───────────────────────────────────────────────────────────
# This file: SES_Workspace/skills/daily_brief/daily_brief.py
SCRIPT_DIR    = Path(__file__).parent.resolve()
SES_WORKSPACE = SCRIPT_DIR.parents[1]   # skills/daily_brief -> SES_Workspace

# Load env BEFORE importing llm — llm reads API keys at import time.
load_dotenv(SES_WORKSPACE.parents[0] / ".hermes" / ".env")

sys.path.insert(0, str(SES_WORKSPACE / "lib"))
from llm import complete as _llm_complete  # noqa: E402
from path_config import p as _p  # noqa: E402

# Import email sender directly (avoids subprocess + cmd-line length issues)
sys.path.insert(0, str(SES_WORKSPACE / "skills" / "email_sender" / "scripts"))
from notify import send_notification  # noqa: E402

# ── Config ────────────────────────────────────────────────────────────────────
ASANA_PAT       = os.environ.get("ASANA_PERSONAL_ACCESS_TOKEN", "")
# Live ops.db + live REQUESTS queue via PATH_CONFIG (was hardcoded to the
# SES_Workspace copies: a frozen DB fork and a queue nothing drained —
# fixed 2026-07-08).
DB_PATH         = Path(_p("db.ops"))
RULES_PATH      = SCRIPT_DIR / "triage_rules.md"

WORKSPACE_GID           = "357939803556123"
GILES_USER_GID          = "1142108324926610"
GH_SIGNIFICANCE_GID     = "1207160402234668"
PERSONAL_TASK_PROJECT_GID = "369446862164514"
REQUESTS_DIR            = Path(_p("queues.requests"))
DEV_LOG_PROJECT_GID     = "1210260491988082"
DEV_LOG_ACTIVE_SECTIONS = ["In Progress", "Blocked", "This Week", "Backlog"]

TODAY   = date.today().isoformat()
NOW_ISO = datetime.now(timezone.utc).isoformat()

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)


# ── SQLite ────────────────────────────────────────────────────────────────────
def init_db(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS giles_tasks (
            gid              TEXT PRIMARY KEY,
            name             TEXT NOT NULL,
            projects         TEXT,
            due_on           TEXT,
            notes            TEXT,
            asana_url        TEXT,
            gh_significance  TEXT,
            synced_at        TEXT NOT NULL,
            triage_category  TEXT,
            triage_reason    TEXT,
            triage_action    TEXT,
            triaged_at       TEXT
        )
    """)
    conn.commit()


# ── Asana fetch ───────────────────────────────────────────────────────────────
def _asana_get(params: dict) -> list[dict]:
    """Paginated GET against /tasks with given params."""
    headers = {"Authorization": f"Bearer {ASANA_PAT}", "Accept": "application/json"}
    url     = "https://app.asana.com/api/1.0/tasks"
    tasks   = []
    while url:
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        tasks.extend(data.get("data", []))
        nxt = data.get("next_page")
        if nxt and nxt.get("uri"):
            url    = nxt["uri"]
            params = {}
        else:
            url = None
    return tasks


# Significance values in descending priority order
# Business priority: active orders → quotes/servicing → dev → admin
GH_SIG_PRIORITY = [
    "CRITICAL / Reactive / URGENT",  # GID 1207160402234670
    "PROJECT Orders",                 # GID 1207160402234669
    "For Servicing",                  # GID 1207160402234674
    "Dev work",                       # GID 1211783997433540
    "Admin Orders / Facilities",      # GID 1207160402234671
]
GH_SIG_RANK = {v: i for i, v in enumerate(GH_SIG_PRIORITY)}

# Staleness thresholds — tasks overdue AND untouched beyond these are skipped
STALE_DUE_DAYS      = 90   # overdue by more than this many days
STALE_MODIFIED_DAYS = 60   # AND not modified in this many days → skip


def _extract_gh_sig(task: dict) -> str:
    for cf in task.get("custom_fields", []):
        if cf.get("gid") == GH_SIGNIFICANCE_GID:
            ev = cf.get("enum_value")
            raw = ev.get("name", "") if ev else (cf.get("display_value") or "")
            return raw.strip()
    return ""


def fetch_giles_tasks() -> tuple[list[dict], list[dict]]:
    """
    Returns (triage_tasks, all_tasks).

    triage_tasks: up to 60 tasks filtered by GH Significance, staleness-pruned, priority-ranked.
    all_tasks: every incomplete task assigned to Giles — used for Eisenhower audit.

    My Tasks (user_task_list) API returns 0 via /user_task_lists/{gid}/tasks — Asana quirk.
    All tasks assigned to Giles are correctly captured by the assignee+workspace query.
    """
    if not ASANA_PAT:
        raise RuntimeError("ASANA_PERSONAL_ACCESS_TOKEN not set in .env")

    base_params = {
        "assignee":        GILES_USER_GID,
        "workspace":       WORKSPACE_GID,
        "completed_since": "now",
        "opt_fields":      "name,gid,projects.gid,projects.name,due_on,modified_at,notes,custom_fields,memberships.section.name",
        "limit":           100,
    }

    # Significance is the primary gate — overdue without significance = legacy noise, ignored
    all_tasks = _asana_get(base_params)
    log.info("Fetched %d total tasks, filtering by significance", len(all_tasks))

    today    = date.today()
    tomorrow = (today + timedelta(days=1)).isoformat()
    stale_due_cutoff      = (today - timedelta(days=STALE_DUE_DAYS)).isoformat()
    stale_modified_cutoff = (datetime.now(timezone.utc) - timedelta(days=STALE_MODIFIED_DAYS)).isoformat()

    buckets: dict[int, list] = {i: [] for i in range(len(GH_SIG_PRIORITY))}
    skipped_stale = 0
    for t in all_tasks:
        sig = _extract_gh_sig(t)
        if sig not in GH_SIG_RANK:
            continue

        # Skip tasks that are deeply overdue AND untouched — likely abandoned legacy noise
        due      = t.get("due_on") or ""
        modified = t.get("modified_at") or ""
        if due and due < stale_due_cutoff and modified and modified < stale_modified_cutoff:
            skipped_stale += 1
            continue

        t["_gh_sig"] = sig
        # Within each bucket: overdue/due-today first, then future due, then no date
        t["_sort_key"] = (0 if due and due < tomorrow else (1 if due else 2), due)
        buckets[GH_SIG_RANK[sig]].append(t)

    if skipped_stale:
        log.info("Skipped %d stale tasks (overdue >%dd AND unmodified >%dd)",
                 skipped_stale, STALE_DUE_DAYS, STALE_MODIFIED_DAYS)

    seen  = set()
    focal = []
    for rank in sorted(buckets):
        for t in sorted(buckets[rank], key=lambda x: x["_sort_key"]):
            if t["gid"] not in seen:
                seen.add(t["gid"])
                focal.append(t)
            if len(focal) >= 60:
                break
        if len(focal) >= 60:
            break

    log.info("Returning %d significance-filtered tasks for triage (of %d total)", len(focal), len(all_tasks))
    return focal, all_tasks


def _task_url(task: dict) -> str:
    gid      = task.get("gid", "")
    projects = task.get("projects", [])
    pgid     = projects[0].get("gid", WORKSPACE_GID) if projects else WORKSPACE_GID
    return f"https://app.asana.com/0/{pgid}/{gid}"


def sync_tasks(conn: sqlite3.Connection, raw: list[dict]) -> None:
    """Upsert raw Asana tasks into giles_tasks table."""
    for t in raw:
        gid    = t.get("gid", "")
        name   = t.get("name", "")
        projs  = json.dumps([p.get("name", "") for p in t.get("projects", [])])
        due    = t.get("due_on") or ""
        notes  = (t.get("notes") or "")[:350]
        url    = _task_url(t)
        gh_sig = ""
        for cf in t.get("custom_fields", []):
            if cf.get("gid") == GH_SIGNIFICANCE_GID:
                ev     = cf.get("enum_value")
                gh_sig = ev.get("name", "") if ev else (cf.get("display_value") or "")
                break

        conn.execute("""
            INSERT INTO giles_tasks
                (gid, name, projects, due_on, notes, asana_url, gh_significance, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(gid) DO UPDATE SET
                name            = excluded.name,
                projects        = excluded.projects,
                due_on          = excluded.due_on,
                notes           = excluded.notes,
                asana_url       = excluded.asana_url,
                gh_significance = excluded.gh_significance,
                synced_at       = excluded.synced_at
        """, (gid, name, projs, due, notes, url, gh_sig, NOW_ISO))
    conn.commit()


# ── Project task fetch ───────────────────────────────────────────────────────
EISENHOWER_TASK_CAP = 120  # max tasks sent to Gemini for Eisenhower audit


def fetch_project_tasks(project_gid: str) -> list[dict]:
    """Return incomplete tasks from a project via the correct /projects/{gid}/tasks endpoint.

    Caps at EISENHOWER_TASK_CAP to prevent Gemini token overflow on large personal projects.
    """
    if not ASANA_PAT:
        return []
    headers = {"Authorization": f"Bearer {ASANA_PAT}", "Accept": "application/json"}
    params  = {
        "completed_since": "now",
        "opt_fields":      "name,gid,due_on,notes,memberships.section.name",
        "limit":           100,
    }
    url   = f"https://app.asana.com/api/1.0/projects/{project_gid}/tasks"
    tasks = []
    while url and len(tasks) < EISENHOWER_TASK_CAP:
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        tasks.extend(data.get("data", []))
        nxt = data.get("next_page")
        if nxt and nxt.get("uri"):
            url    = nxt["uri"]
            params = {}
        else:
            url = None
    if len(tasks) >= EISENHOWER_TASK_CAP:
        log.info("Eisenhower fetch capped at %d tasks for project %s", EISENHOWER_TASK_CAP, project_gid)
    log.info("Fetched %d tasks from project %s", len(tasks), project_gid)
    return tasks[:EISENHOWER_TASK_CAP]


# ── Dev log fetch ────────────────────────────────────────────────────────────
def fetch_dev_log_tasks() -> dict:
    """
    Fetch incomplete tasks from the AI Dev Log project (1210260491988082).
    Returns dict: {section_name: [task_dict, ...], "_summary": {...}}
    """
    if not ASANA_PAT:
        return {}

    headers = {"Authorization": f"Bearer {ASANA_PAT}", "Accept": "application/json"}
    params  = {
        "project":       DEV_LOG_PROJECT_GID,
        "completed_since": "now",
        "opt_fields":    "name,gid,memberships.section.name,memberships.section.gid,due_on,notes",
        "limit":         100,
    }
    url   = "https://app.asana.com/api/1.0/tasks"
    tasks = []

    while url:
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            tasks.extend(data.get("data", []))
            nxt = data.get("next_page")
            if nxt and nxt.get("uri"):
                url    = nxt["uri"]
                params = {}
            else:
                url = None
        except Exception as e:
            log.warning("Dev log fetch error: %s", e)
            break

    log.info("Fetched %d dev log tasks", len(tasks))

    # Group by section
    by_section: dict[str, list] = {}
    for t in tasks:
        memberships = t.get("memberships", [])
        section_name = (
            memberships[0].get("section", {}).get("name", "Unsectioned")
            if memberships else "Unsectioned"
        )
        by_section.setdefault(section_name, []).append({
            "gid":   t.get("gid", ""),
            "name":  t.get("name", ""),
            "due":   t.get("due_on") or "",
            "notes": (t.get("notes") or "")[:150],
            "url":   f"https://app.asana.com/0/{DEV_LOG_PROJECT_GID}/{t.get('gid','')}",
        })

    # Summary counts for non-active sections
    summary = {
        "total":           len(tasks),
        "active_sections": {},
        "other_count":     0,
    }
    for section, items in by_section.items():
        if section in DEV_LOG_ACTIVE_SECTIONS:
            summary["active_sections"][section] = len(items)
        else:
            summary["other_count"] += len(items)

    by_section["_summary"] = summary
    return by_section


def _dev_log_html(by_section: dict) -> str:
    """Build the AI Dev Log section of the HTML brief."""
    if not by_section:
        return ""

    summary = by_section.get("_summary", {})
    total   = summary.get("total", 0)
    other   = summary.get("other_count", 0)

    rows_html = ""
    for section_name in DEV_LOG_ACTIVE_SECTIONS:
        items = by_section.get(section_name, [])
        if not items:
            continue
        rows_html += f"""<tr><td colspan="2" style="padding:8px 14px 4px;
            background:#f0f4f8;font-size:12px;font-weight:700;
            color:#4a6080;text-transform:uppercase;letter-spacing:.5px">
            {section_name} ({len(items)})
        </td></tr>"""
        for item in items:
            due_badge = (
                f'<span style="font-size:11px;color:#888;margin-left:8px">Due: {item["due"]}</span>'
                if item["due"] else ""
            )
            rows_html += f"""<tr>
              <td style="padding:8px 14px;border-bottom:1px solid #f0f0f0;vertical-align:top">
                <a href="{item['url']}" style="color:#1a6bb8;text-decoration:none;font-size:13px">
                  {item['name']}</a>{due_badge}
              </td>
            </tr>"""

    if not rows_html:
        return ""

    other_note = f" &bull; {other} in other sections" if other else ""

    return f"""
    <h3 style="margin:28px 0 8px;color:#5a7a5a;font-size:13px;
               text-transform:uppercase;letter-spacing:.9px">
      &#x1F6E0; AI Dev Log &mdash; Asana AI Knowledge &amp; Prompt Controls
      <span style="font-size:11px;font-weight:normal;color:#888;
                   text-transform:none;letter-spacing:0">
        ({total} total incomplete{other_note})
      </span>
    </h3>
    <table width="100%" cellpadding="0" cellspacing="0"
           style="border:1px solid #c8dac8;border-radius:6px;background:#fff">
      {rows_html}
    </table>"""


# ── Eisenhower audit ─────────────────────────────────────────────────────────
EISENHOWER_SYSTEM = """\
Act as a Strategic Priority Analyst specialising in solopreneur systems using the Eisenhower Matrix.
Treat every task as a choice, not an obligation. Giles Hugo is Purchasing Manager at Sesame Access Systems (UK manufacturer — power doors, access gates, platforms).

PHASE 1 — SUCCESS FILTER
Answer: "What does a successful week look like for this business in one sentence?"
Use this as the definition of "Important".

PHASE 2 — TASK CLASSIFICATION
Assign each task to Q1/Q2/Q3/Q4:
Q1 — Urgent + Important: deadlines, client deliverables, revenue-critical work
Q2 — Important + Not Urgent: strategy, systems, growth, skill-building
Q3 — Urgent + Not Important: interruptions, emails, quick requests
Q4 — Not Urgent + Not Important: busywork, low-leverage admin

Rules: separate revenue-generating vs revenue-adjacent. Do NOT confuse checking with producing.

PHASE 3 — OUTPUT ANALYSIS
1. TIME DISTRIBUTION: estimate % per quadrant + one-line verdict
2. PREVENTABLE FIRES: flag Q1 tasks that became urgent due to delay — label "Preventable Fire"
3. Q3 OPTIMISATION: for every Q3 task assign: Automate / Delegate / Batch / Eliminate
4. Q4 DIRECTIVE: eliminate or cap at ≤20 mins/day total

PHASE 4 — SCHEDULE RESTRUCTURE
1. TOP Q2 PRIORITIES: top 3 highest-leverage Q2 activities
2. PROTECTED Q2 BLOCKS: assign each a fixed recurring time block
3. DAILY STRUCTURE: design ideal day with ≥40% time in Q2
4. Q3 COMPRESSION: batch into 1–2 controlled windows/day

FINAL OUTPUT
One paragraph: the single biggest shift this restructuring creates.

Execution mode: Be direct, not polite. Prioritise truth over comfort.
"""


def run_eisenhower_audit(tasks: list[dict]) -> str:
    """Run Eisenhower matrix analysis on project tasks."""
    compact = [
        {
            "name":    t.get("name", ""),
            "due_on":  t.get("due_on") or "none",
            "section": (t.get("memberships") or [{}])[0].get("section", {}).get("name", "") if t.get("memberships") else "",
            "notes":   (t.get("notes") or "")[:150],
        }
        for t in tasks
    ]
    user_msg = (
        f"Today is {TODAY}. I am Giles Hugo, Purchasing Manager at Sesame Access.\n\n"
        f"Here are my {len(compact)} tasks from my personal task list. "
        f"Run the full Eisenhower Priority Audit:\n\n"
        + json.dumps(compact, indent=2)
    )
    try:
        return _llm_complete(user_msg, system=EISENHOWER_SYSTEM, max_tokens=3000)
    except Exception as e:
        log.warning("Eisenhower audit failed: %s", e)
        return ""


def _unmapped_parts_html(summary: dict) -> str:
    """Parts in parts_control with no assembly pack — i.e. the drawing pipeline
    has probably not been run for them. Flag only; nothing is automated."""
    if not summary or not summary.get("total"):
        return ""

    total = summary["total"]
    by_project = summary.get("by_project", [])
    by_status = summary.get("by_order_status", {})

    shown = by_project[:6]
    remainder = sum(n for _, n in by_project[6:])

    chips = ""
    for project, n in shown:
        label = "unidentified" if project == "unidentified" else f"Project {project}"
        chips += (
            '<span style="display:inline-block;background:#fff;border:1px solid #e0cfa0;'
            'color:#7a5c00;padding:4px 10px;border-radius:4px;margin:0 6px 6px 0;'
            f'font-size:12px"><strong>{n}</strong> &nbsp;{label}</span>'
        )
    if remainder:
        chips += (
            '<span style="display:inline-block;color:#8a7a55;padding:4px 2px;'
            f'font-size:12px">+{remainder} across {len(by_project) - len(shown)} other projects</span>'
        )

    not_ordered = by_status.get("not_ordered", 0)
    awaiting = by_status.get("awaiting_confirmation", 0)
    on_order = by_status.get("on_order", 0)
    order_line = (
        f'<div style="margin-top:12px;padding-top:10px;border-top:1px solid #ecdcae;'
        f'font-size:12px;color:#6b5a2a">'
        f'Of these: <strong>{not_ordered}</strong> not yet on order &bull; '
        f'<strong>{awaiting}</strong> awaiting order confirmation &bull; '
        f'<strong>{on_order}</strong> on order</div>'
    )

    return f"""
    <h3 style="margin:28px 0 8px;color:#8a6d1f;font-size:13px;
               text-transform:uppercase;letter-spacing:.9px">
      &#x1F4D0; Parts Not Mapped To An Assembly Pack
    </h3>
    <div style="border:1px solid #e8d9a8;border-radius:6px;background:#fdfaf0;
                padding:16px 18px;font-size:13px;line-height:1.6;color:#333">
      <div style="margin-bottom:10px">
        Giles to review — <strong>{total}</strong> open part(s) in Parts Control are not
        mapped to an assembly pack, so the drawing pipeline likely still needs running.
      </div>
      {chips}
      {order_line}
    </div>"""


def _eisenhower_html(audit_text: str) -> str:
    if not audit_text:
        return ""
    escaped = (
        audit_text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n\n", "</p><p style='margin:8px 0'>")
        .replace("\n", "<br>")
    )
    return f"""
    <h3 style="margin:28px 0 8px;color:#6a3a9a;font-size:13px;
               text-transform:uppercase;letter-spacing:.9px">
      &#x1F4CA; Eisenhower Priority Audit
    </h3>
    <div style="border:1px solid #d0c0e8;border-radius:6px;background:#faf7fd;
                padding:16px 18px;font-size:13px;line-height:1.7;color:#333">
      <p style="margin:0">{escaped}</p>
    </div>"""


def create_eisenhower_request(audit_text: str) -> None:
    """Write a REQUESTS/ file for SAP to create an Asana task with the Eisenhower audit."""
    try:
        REQUESTS_DIR.mkdir(exist_ok=True)
        ts    = datetime.now().strftime("%Y-%m-%dT%H-%M")
        fname = REQUESTS_DIR / f"{ts}-navigator-eisenhower_audit.md"
        fname.write_text(
            f"---\nFrom: navigator\nTo: asana_agent\nType: asana_write\n"
            f"Status: PENDING\nPriority: normal\nCreated: {NOW_ISO}\n---\n\n"
            f"## Request\nAction: create_task\nProject GID: {PERSONAL_TASK_PROJECT_GID}\n\n"
            f"Details:\nTask name: Eisenhower Priority Audit — {TODAY}\n"
            f"Description:\n{audit_text}\n\n"
            f"## Context\nDaily Eisenhower matrix audit. Generated by daily_brief.py.\n",
            encoding="utf-8",
        )
        log.info("Eisenhower request file created: %s", fname.name)
    except Exception as e:
        log.warning("Could not create Eisenhower request file: %s", e)


# ── AI triage ─────────────────────────────────────────────────────────────────
BASE_SYSTEM = """\
You are a daily briefing assistant for Giles Hugo, Purchasing Manager at Sesame Access \
(UK manufacturer — power doors, access gates, platforms).

BUSINESS PRIORITY (highest to lowest):
1. Active orders — purchase orders with delivery urgency, supplier chases, delivery issues
2. Quotes and servicing — outstanding quotes, service jobs needing action
3. Platform/dev work — system improvements, automation, process builds

GH Significance field mirrors this: CRITICAL/Reactive/URGENT > PROJECT Orders > For Servicing > Dev work > Admin

These tasks are pre-filtered by significance and staleness. Stale abandoned tasks have \
already been removed. Focus on what is genuinely active and actionable today.

Your job: surface only what genuinely needs action today, spot any patterns that suggest \
a recurring process that could be automated or systemised.

RULES:
- Max 5 urgent (due today / overdue AND still active — not just old tasks that were forgotten)
- Max 8 action_mine (Giles must personally do — call supplier, raise PO, approve)
- Max 5 decision_needed (needs judgement, not urgent)
- Everything else: count only
- pipeline_opportunity: 1-2 sentence observation IF you see a recurring pattern \
  (e.g. "Supplier X tasks repeat weekly — candidate for automated chase email"). \
  Leave empty string if nothing obvious.

Return ONLY valid JSON, no markdown fences:
{
  "urgent":              [{"gid":"...","name":"...","action":"..."}],
  "action_mine":         [{"gid":"...","name":"...","action":"..."}],
  "decision_needed":     [{"gid":"...","name":"...","action":"..."}],
  "other_count":         N,
  "focus_recommendation":"2-3 sentences on today's single biggest priority and why.",
  "pipeline_opportunity":"..."
}
"""


def load_triage_rules() -> str:
    if RULES_PATH.exists():
        rules = RULES_PATH.read_text(encoding="utf-8").strip()
        if rules:
            return f"\n\nADDITIONAL RULES:\n{rules}"
    return ""


def triage_with_claude(tasks: list[dict]) -> dict:
    compact = [
        {
            "gid":     t["gid"],
            "name":    t["name"],
            "due_on":  t.get("due_on") or "none",
            "sig":     t.get("gh_significance") or t.get("_gh_sig") or "",
            "section": (t.get("memberships") or [{}])[0].get("section", {}).get("name", "") if t.get("memberships") else "",
            "notes":   (t.get("notes") or "")[:60],
        }
        for t in tasks
    ]

    user_msg = (
        f"Today is {TODAY}. {len(compact)} priority tasks:\n\n"
        + json.dumps(compact, indent=2)
    )

    raw_text = _llm_complete(
        user_msg,
        system=BASE_SYSTEM + load_triage_rules(),
        max_tokens=2048,
    )
    if raw_text.startswith("```"):
        raw_text = raw_text.strip("`").lstrip("json").strip()
    return json.loads(raw_text)


# ── HTML brief ────────────────────────────────────────────────────────────────
def _task_row(item: dict, lookup: dict) -> str:
    gid      = item.get("gid", "")
    meta     = lookup.get(gid, {})
    url      = meta.get("asana_url") or "#"
    name     = item.get("name") or "Unknown task"
    raw_proj = meta.get("projects") or "[]"
    projs    = json.loads(raw_proj) if isinstance(raw_proj, str) else raw_proj
    proj     = projs[0] if projs else "—"
    due      = meta.get("due_on") or "no date"
    act      = item.get("action") or item.get("reason") or ""
    return f"""<tr>
      <td style="padding:10px 14px;border-bottom:1px solid #f0f0f0;vertical-align:top">
        <a href="{url}" style="color:#1a6bb8;font-weight:600;text-decoration:none;
                               font-size:14px">{name}</a><br>
        <span style="font-size:12px;color:#888">{proj} &bull; Due: {due}</span><br>
        <span style="font-size:13px;color:#444;margin-top:4px;display:block">
          &#x2192; {act}
        </span>
      </td>
    </tr>"""


def _section(title: str, colour: str, items: list[dict], lookup: dict) -> str:
    if not items:
        return ""
    rows = "".join(_task_row(i, lookup) for i in items)
    return f"""
    <h3 style="margin:28px 0 8px;color:{colour};font-size:13px;
               text-transform:uppercase;letter-spacing:.9px">{title}</h3>
    <table width="100%" cellpadding="0" cellspacing="0"
           style="border:1px solid #e0e0e0;border-radius:6px;background:#fff">
      {rows}
    </table>"""


def build_html(triage: dict, lookup: dict, total: int, eisenhower_html: str = "",
               unmapped_html: str = "") -> str:
    now_dt   = datetime.now()
    day_name = now_dt.strftime("%A")
    date_str = now_dt.strftime("%d %B %Y")

    urgent_html   = _section("🔴 Urgent — Act Today",      "#c0392b", triage.get("urgent", []),          lookup)
    action_html   = _section("🟠 Your Action Required",    "#e67e22", triage.get("action_mine", []),      lookup)
    decision_html = _section("🟡 Decision / Input Needed", "#c9960a", triage.get("decision_needed", []),  lookup)
    delegate_html = ""

    n_urgent   = len(triage.get("urgent",          []))
    n_action   = len(triage.get("action_mine",     []))
    n_decision = len(triage.get("decision_needed", []))
    n_other    = triage.get("other_count", 0)
    focus      = triage.get("focus_recommendation", "")
    pipeline   = triage.get("pipeline_opportunity", "")

    synced_str = NOW_ISO[:16] + " UTC"

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Navigator Daily Brief</title></head>
<body style="margin:0;padding:0;background:#f4f5f7;
             font-family:Arial,Helvetica,sans-serif;color:#333">

<table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f5f7">
<tr><td align="center" style="padding:28px 16px">

<table width="600" cellpadding="0" cellspacing="0"
       style="max-width:600px;background:#fff;border-radius:8px;overflow:hidden;
              box-shadow:0 2px 10px rgba(0,0,0,.09)">

  <!-- ── Header ── -->
  <tr><td style="background:#1a252f;padding:22px 28px">
    <div style="font-size:20px;font-weight:700;color:#fff">Navigator Daily Brief</div>
    <div style="font-size:13px;color:#7a8fa0;margin-top:5px">{day_name}, {date_str}</div>
  </td></tr>

  <!-- ── Body ── -->
  <tr><td style="padding:26px 28px">

    <!-- Focus recommendation -->
    <div style="background:#eaf3fb;border-left:4px solid #2980b9;
                padding:14px 16px;border-radius:4px;font-size:14px;line-height:1.65">
      <strong style="display:block;margin-bottom:4px">Today's Focus</strong>
      {focus}
    </div>

    <!-- Stat pills -->
    <div style="margin:18px 0;font-size:13px">
      <span style="display:inline-block;background:#fdf2f2;color:#c0392b;
                   padding:5px 11px;border-radius:4px;margin-right:6px;margin-bottom:6px">
        <strong>{n_urgent}</strong> urgent</span>
      <span style="display:inline-block;background:#fdf6ec;color:#e67e22;
                   padding:5px 11px;border-radius:4px;margin-right:6px;margin-bottom:6px">
        <strong>{n_action}</strong> action</span>
      <span style="display:inline-block;background:#fefce8;color:#c9960a;
                   padding:5px 11px;border-radius:4px;margin-right:6px;margin-bottom:6px">
        <strong>{n_decision}</strong> decisions</span>
      <span style="display:inline-block;background:#f5f5f5;color:#555;
                   padding:5px 11px;border-radius:4px;margin-bottom:6px">
        <strong>{n_other}</strong> deprioritised</span>
    </div>

    {urgent_html}
    {action_html}
    {decision_html}

    {unmapped_html}
    {eisenhower_html}

    <!-- Other count + pipeline opportunity -->
    <div style="margin-top:26px;padding:14px 16px;background:#f9f9f9;
                border-radius:6px;font-size:13px;color:#666;line-height:1.8">
      <strong>{n_other}</strong> further tasks deprioritised today
    </div>
    {"" if not pipeline else f'<div style="margin-top:14px;padding:14px 16px;background:#fff8e1;border-left:4px solid #f39c12;border-radius:4px;font-size:13px;color:#555;line-height:1.6"><strong>⚙ Pipeline Opportunity:</strong> ' + pipeline + '</div>'}

  </td></tr>

  <!-- ── Footer ── -->
  <tr><td style="padding:14px 28px;border-top:1px solid #eee">
    <p style="font-size:11px;color:#bbb;margin:0">
      Navigator &bull; OpenClaw &bull; Synced {synced_str} &bull;
      {total} tasks currently assigned to Giles in Asana
    </p>
  </td></tr>

</table>
</td></tr>
</table>
</body></html>"""


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="Navigator Daily Brief Generator")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch and triage but save HTML preview instead of emailing")
    args = parser.parse_args()

    log.info("Daily brief starting — %s", TODAY)

    # 1 · Database
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    init_db(conn)

    # 2 · Fetch from Asana
    log.info("Fetching tasks from Asana …")
    raw_tasks, all_giles_tasks = fetch_giles_tasks()
    if not raw_tasks:
        log.warning("No tasks returned from Asana. Skipping brief.")
        conn.close()
        return

    # 3 · Sync to SQLite
    log.info("Syncing %d tasks to SQLite …", len(raw_tasks))
    sync_tasks(conn, raw_tasks)

    # 4 · Build SQLite lookup for HTML enrichment (URL, project name)
    rows   = conn.execute("SELECT * FROM giles_tasks").fetchall()
    db_all = [dict(r) for r in rows]
    lookup = {t["gid"]: t for t in db_all}
    log.info("Total tasks in SQLite: %d", len(db_all))

    # 5 · Triage with Gemini — use today's filtered raw_tasks (not full SQLite history)
    log.info("Triaging with Claude Haiku …")
    triage = triage_with_claude(raw_tasks)

    # 6 · Persist triage results
    for cat_key, cat_label in [
        ("urgent",         "URGENT"),
        ("action_mine",    "ACTION_MINE"),
        ("decision_needed","DECISION_NEEDED"),
    ]:
        for item in triage.get(cat_key, []):
            conn.execute("""
                UPDATE giles_tasks
                SET triage_category=?, triage_action=?, triaged_at=?
                WHERE gid=?
            """, (cat_label, item.get("action", ""), NOW_ISO, item.get("gid", "")))
    conn.commit()
    conn.close()

    # 7 · Eisenhower audit on all Giles tasks
    # user_task_lists API returns 0 (Asana quirk) — reuse the assignee fetch already done.
    # Cap to EISENHOWER_TASK_CAP to stay within Gemini token limits.
    log.info("Running Eisenhower audit on %d tasks (capped at %d) …",
             len(all_giles_tasks), EISENHOWER_TASK_CAP)
    eisenhower_input    = all_giles_tasks[:EISENHOWER_TASK_CAP]
    eisenhower_text     = ""
    eisenhower_html_section = ""
    if eisenhower_input:
        eisenhower_text = run_eisenhower_audit(eisenhower_input)
        if eisenhower_text:
            eisenhower_html_section = _eisenhower_html(eisenhower_text)
            create_eisenhower_request(eisenhower_text)

    # 7b · Unmapped parts (drawing-pipeline flag). Never allowed to break the
    # brief — a reporting extra must not cost Giles the whole morning email.
    unmapped_html_section = ""
    try:
        import sqlite3 as _sqlite3
        _sys_path = str(Path(__file__).resolve().parents[1] / "purchasing")
        if _sys_path not in sys.path:
            sys.path.insert(0, _sys_path)
        import pack_association as _pa

        _db = _pa.DEFAULT_DB_PATH
        if _db and Path(_db).exists():
            _conn = _sqlite3.connect(_db, timeout=30)
            try:
                unmapped_html_section = _unmapped_parts_html(_pa.unmapped_summary(_conn))
            finally:
                _conn.close()
        else:
            log.warning("Unmapped-parts section skipped: ops.db not found at %s", _db)
    except Exception as exc:
        log.warning("Unmapped-parts section skipped: %s: %s", type(exc).__name__, exc)

    # 8 · Build HTML
    log.info("Building HTML …")
    html = build_html(triage, lookup, len(db_all), eisenhower_html=eisenhower_html_section,
                      unmapped_html=unmapped_html_section)

    # 8 · Subject line
    n_urgent = len(triage.get("urgent", []))
    n_action = len(triage.get("action_mine", []))
    urgent_tag = f" — {n_urgent} URGENT" if n_urgent else ""
    action_tag = f", {n_action} actions" if n_action else ""
    subject = (
        f"Daily Brief — {datetime.now().strftime('%a %d %b')}"
        + urgent_tag + action_tag
    )

    # 9 · Send or preview
    if args.dry_run:
        out = SCRIPT_DIR / f"brief_preview_{TODAY}.html"
        out.write_text(html, encoding="utf-8")
        log.info("[DRY-RUN] Preview saved to %s", out)
        print(f"Dry run complete. Open in browser: {out}")
        return

    log.info("Sending email …")
    send_notification(
        subject=subject,
        body=html,
        is_html=True,
        from_agent="Navigator",
    )
    print(f"Daily brief sent: {subject}")


if __name__ == "__main__":
    main()
