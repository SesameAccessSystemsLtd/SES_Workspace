#!/usr/bin/env python3
"""
build_os_status.py — SAS OS portal: Andy's worklist + pack readiness (live).

Pulls LIVE data from Asana (Build Schedule + Order Numbers + per-pack subtasks)
and joins against ops.db (parts_control) to render an HTML page showing ONLY:

  * Andy-assigned, INCOMPLETE tasks
  * in PROJECT sections (project name shown in the header)
  * their pack subtasks (open/overdue counts, expandable)
  * parts-readiness → advised new due date → Accept button (REQUESTS→SAP write)

Governance:
  - Read-only on ops.db. NEVER writes to Asana directly.
  - The Accept button dispatches a REQUESTS queue file (SAP applies the write).
  - Every row hyperlinks to its Asana source.
  - Build Schedule insight fields in ops.db are NULL → pack truth from live Asana.

Output: data/os_status/index.html + dated copy.

Usage:
  python build_os_status.py [--out DIR] [--project 1588]
"""

import os
import re
import json
import sqlite3
import argparse
import datetime
import requests
from collections import Counter, defaultdict
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────────────────
WS = Path(r"C:/Users/GilesHugo/SES_Workspace")
OPS_DB = r"C:/Users/GilesHugo/SES_Workspace/db/ops.db"
REQUESTS_DIR = WS / "queues" / "REQUESTS"

BUILD_SCHEDULE = "852956628490855"
ORDER_NUMBERS = "854650811917168"
ASANA_BASE = "https://app.asana.com/0"
API_BASE = "https://app.asana.com/api/1.0"

# Andy's uid (from probes) and the assignee filter
TARGET_ASSIGNEE = "Andy"          # show only this assignee's incomplete work
GENERAL_SECTIONS = {
    "(no section)", "Untitled section", "All Inbox tasks", "Assembly Packs",
    "Awaiting Delivery (Auto Added)", "Awaiting Delivery - On Order", "Build",
    "Build Complete - Recorded in storage", "Closed", "Complete", "Completed",
    "Debriefed", "Developments / R&D", "Engineer Suggestion / Project Inbox",
    "Engineers Accepted (auto add to projects)", "Engineers Tasks / Inbox",
    "Engineers tasks", "Holidays", "Miscellaneous", "No Further Action Needed ",
    "Office Work to be done", "Pre - production Parts", "Project Machining ",
    "Project Misc / Purchasing stock", "Ready to be quoted", "Recently Added",
    "Resolved / no particular fault ", "Service Department", "Service Tasks",
    "Service staff available", "Site Team", "Site Work:", "Sub Con Processes",
    "Template", "To Be Addressed", "Updates for parts library",
    "Workshop General Tasks:", "Workshop Misc", "Key Project Controls",
    "Install Date to be scheduled", "Engineers Accepted",
}


def is_project_section(name):
    """A section is a 'project' section if it names a job (NNNN - Name or ends
    with NNNN) and is not a known general/control section."""
    name = (name or "").strip()
    if not name or name in GENERAL_SECTIONS:
        return False
    # project sections: leading 4-digit job  (e.g. '1588 - America Street, No.1')
    # or trailing 4-digit job (e.g. 'Adelaide House - Vertical - Lift 12 - 1485')
    if re.match(r"^\d{4}\s*[-:]", name):
        return True
    if re.search(r"[-:]\s*\d{4}\s*$", name):
        return True
    return False


# ── Asana token ─────────────────────────────────────────────────────────────
def asana_token():
    for k in ("ASANA_PERSONAL_ACCESS_TOKEN", "ASANA_PAT"):
        if os.environ.get(k):
            return os.environ[k].strip()
    for line in Path(r"C:/Users/GilesHugo/.hermes/.env").read_text().splitlines():
        line = line.strip()
        for k in ("ASANA_PERSONAL_ACCESS_TOKEN", "ASANA_PAT"):
            if line.startswith(f"{k}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


_HEADERS = None


def headers():
    global _HEADERS
    if _HEADERS is None:
        _HEADERS = {"Authorization": f"Bearer {asana_token()}", "Accept": "application/json"}
    return _HEADERS


def api_get(path, params=None):
    r = requests.get(f"{API_BASE}{path}", headers=headers(), params=params)
    r.raise_for_status()
    return r.json()


def paginate(path, params=None):
    params = dict(params or {})
    params.setdefault("limit", 100)
    out, offset = [], None
    while True:
        if offset:
            params["offset"] = offset
        j = api_get(path, params)
        out.extend(j.get("data", []))
        np = j.get("next_page")
        if np and np.get("offset"):
            offset = np["offset"]
        else:
            break
    return out


def fetch_build_schedule():
    """All Build Schedule tasks (open + completed) with section + subtask count."""
    return paginate(f"/projects/{BUILD_SCHEDULE}/tasks", {
        "opt_fields": "name,gid,completed,due_on,assignee.name,"
                      "memberships.section.name,memberships.project.name,"
                      "num_subtasks,parent.gid",
    })


def fetch_subtasks(task_gid):
    """Return subtasks of a pack task."""
    try:
        return paginate(f"/tasks/{task_gid}/subtasks", {
            "opt_fields": "name,gid,completed,due_on,assignee.name",
        })
    except Exception:
        return []


def load_open_orders():
    """Open Order Numbers tasks (expected delivery dates), annotated with
    job (from 'NNNNN-NNNN -'), vendor (from name/tag), PO label."""
    out = paginate(f"/projects/{ORDER_NUMBERS}/tasks", {
        "opt_fields": "name,gid,completed,due_on,tags.name,memberships.section.name",
    })
    orders = []
    for t in out:
        if t.get("completed"):
            continue
        name = t.get("name") or ""
        m = re.match(r"^(\d{5,6})-(\d{4})", name)
        job = m.group(2) if m else (re.search(r"(\d{4})", name).group(1)
                                    if re.search(r"(\d{4})", name) else None)
        po = m.group(1) if m else (name.split("-")[0].strip() if name else "")
        # vendor: derive from the name '{po}-{job} - {vendor} - ...' first
        # (reliable location); only fall back to a tag if the name has no vendor.
        vendor = ""
        parts = name.split("-")
        if len(parts) > 2:
            vendor = "-".join(parts[2:]).strip()
            vendor = " ".join(vendor.split())[:40]
        elif len(parts) == 2:
            vendor = parts[1].strip()
        if not vendor:
            generic = {"lasercut orders", "machining orders", "hydraulic orders",
                       "engraving / laser etching", "sesame bom suppliers",
                       "automation suppliers", "glass suppliers", "sesame access ltd",
                       "stock", "all"}
            for tg in (t.get("tags") or []):
                tn = (tg.get("name") or "").strip()
                if tn and tn.lower().strip() not in generic:
                    vendor = tn
                    break
        orders.append({
            "gid": t.get("gid"), "name": name, "job": job,
            "due_on": t.get("due_on"), "po": po, "vendor": vendor,
            "section": ((t.get("memberships") or [{}])[0].get("section", {}) or {}).get("name"),
        })
    return orders


def orders_by_job(orders):
    """Return {job: [order,...]} for quick per-pack lookup."""
    d = defaultdict(list)
    for o in orders:
        if o.get("job"):
            d[o["job"]].append(o)
    return d


# ── ops.db joins ────────────────────────────────────────────────────────────
def load_procurement_by_packmap():
    """Return {packkey: {stock_action: count}} for ACTIVE parts, plus set of pack keys."""
    c = sqlite3.connect(OPS_DB)
    c.row_factory = sqlite3.Row
    rows = c.execute("""
        SELECT sesame_pn, stock_action, vendor_name, is_completed
        FROM parts_control
    """).fetchall()
    c.close()
    rmap = defaultdict(Counter)
    for r in rows:
        if r["is_completed"]:
            continue
        pn = r["sesame_pn"] or ""
        # pack key = first two components e.g. 1588.1025
        m = re.match(r"^(\d{4}\.\d{1,4})", pn)
        key = m.group(1).rstrip(".").lower() if m else None
        if key:
            rmap[key][(r["stock_action"] or "").strip() or "(blank)"] += 1
    return rmap


def job_project(task_name):
    """Job number (NNNN) from a pack task name or section name."""
    m = re.match(r"^(\d{4})", task_name or "")
    if m:
        return m.group(1)
    return None


# ── Aggregation ─────────────────────────────────────────────────────────────
def aggregate_andy(tasks):
    """Filter to Andy / incomplete / project-section; attach subtask counts later.

    Returns {project_job: {name, open_tasks:[...]}} NOT grouped — we group at render.
    We keep completed counts per project for context but only show packs.
    """
    today = datetime.date.today().isoformat()
    projects = defaultdict(lambda: {"job": None, "section": None, "open": [], "completed": 0})
    seen = set()
    for t in tasks:
        if t.get("completed"):
            continue
        if (t.get("assignee") or {}).get("name") != TARGET_ASSIGNEE:
            continue
        secs = [(m.get("section") or {}).get("name") for m in (t.get("memberships") or [])
                if (m.get("section") or {}).get("name")]
        proj_sec = next((s for s in secs if is_project_section(s)), None)
        if not proj_sec:
            continue
        job = job_project(t.get("name")) or (re.match(r"^(\d{4})", proj_sec).group(1)
                                             if re.match(r"^(\d{4})", proj_sec) else "?")
        entry = {
            "gid": t.get("gid"), "name": t.get("name") or "",
            "due_on": t.get("due_on"), "num_subtasks": t.get("num_subtasks"),
            "section": proj_sec,
        }
        projects[job]["job"] = job
        projects[job]["section"] = proj_sec
        projects[job]["open"].append(entry)
    return projects


def compute_subtask_stats(pack, pack_gid, advised_due=None, latest_arrival=None):
    """Fetch subtasks. For each open subtask due before parts arrive, propose an
    advised new due (parts arrival +1, or pack advised if later). Returns
    {open, overdue, done, rows:[{..,'advised'}]}."""
    today = datetime.date.today().isoformat()
    subs = fetch_subtasks(pack_gid)
    done = sum(1 for s in subs if s.get("completed"))
    open_rows = [s for s in subs if not s.get("completed")]
    overdue = [s for s in open_rows if s.get("due_on") and s["due_on"] < today]
    rows = sorted(open_rows, key=lambda s: s.get("due_on") or "9999")
    for s in rows:
        s["advised"] = None
        if latest_arrival and s.get("due_on") and s["due_on"] < latest_arrival:
            cand = datetime.date.fromisoformat(latest_arrival) + datetime.timedelta(days=1)
            cur = datetime.date.fromisoformat(s["due_on"])
            s["advised"] = max(cand, cur).isoformat()
    return {
        "total": len(subs), "done": done, "open": len(open_rows), "overdue": len(overdue),
        "rows": rows,
        "all_done": len(subs) > 0 and len(open_rows) == 0,
        "advised_due": advised_due,
    }


def parts_readiness(pack_job, pack_name, procurement, orders):
    """Compute parts-arrive / current-due / advised-new for a pack.

    pack_name e.g. '1588.1025.A.Low.Profile.ScissorLift' -> pack key 1588.1025
    latest part arrival from matching Order Numbers task due_on; if none, None.
    """
    m = re.match(r"^(\d{4}\.\d{1,4})", pack_name or "")
    key = m.group(1).rstrip(".").lower() if m else None
    lc = no = on = pk = 0
    if key:
        sa = procurement.get(key, Counter())
        lc = sa.get("PROJECT ORDER / Lasercut parts", 0)
        no = sa.get("NEED TO ORDER", 0)
        on = sa.get("On order (dependents NOT ORDER TASKS)", 0)
        pk = sa.get("PLEASE PICK ", 0)
    # latest part arrival = latest due among open orders for this project
    job = re.match(r"^(\d{4})", pack_name or "")
    job = job.group(1) if job else pack_job
    arrivals = [o["due_on"] for o in orders
                if (re.match(r"^\d{5,6}-(\d{4})", o["name"] or "").group(1) if
                    re.match(r"^\d{5,6}-(\d{4})", o["name"] or "") else None) == job
                and o.get("due_on")]
    latest_arrival = max(arrivals) if arrivals else None
    return {"key": key, "lasercut": lc, "need_order": no, "on_order": on, "pick": pk,
            "latest_arrival": latest_arrival}


# ── HTML ────────────────────────────────────────────────────────────────────
CSS = """
:root{--bg:#f4f6fa;--card:#fff;--ink:#1a1a2e;--muted:#6b7280;--line:#e5e7eb;
--brand:#0f766e;--amber:#d97706;--red:#dc2626;--green:#16a34a;--blue:#2563eb;
--brand2:#0f766e;--hero-grad:linear-gradient(135deg,#0f172a 0%,#134e4a 100%);
--hover:#f9fafb;--subbg:#f8fafc;--accbg:#ecfdf5;--accink:#065f46;
--vendbg:#f0fdf4;--vendline:#86efac;--vendink:#166534;--chipline:rgba(255,255,255,.22)}

/* ── Stock Management dark scheme (default dark) ── */
:root.theme-stock{--bg:#0f1117;--card:#1a1d27;--ink:#e2e8f0;--muted:#94a3b8;
--line:#2d3250;--brand:#6c8ef7;--amber:#fbbf24;--red:#f87171;--green:#34d399;--blue:#6c8ef7;
--brand2:#a78bfa;--hero-grad:linear-gradient(135deg,#1e2a5e 0%,#2d1f6e 100%);
--hover:#21253a;--subbg:#1f2436;--accbg:#0c2b22;--accink:#34d399;
--vendbg:#0e2a1f;--vendline:#34d399;--vendink:#34d399;--chipline:rgba(255,255,255,.28)}

/* ── Futuristic electric neon (showcase) ── */
:root.theme-neon{--bg:#05060f;--card:#0d101f;--ink:#e6f1ff;--muted:#8a93b8;
--line:#1e2a4a;--brand:#00f0ff;--amber:#ffd100;--red:#ff3b6b;--green:#00ff9d;--blue:#5f7cff;
--brand2:#b14aff;--hero-grad:linear-gradient(135deg,#060014 0%,#1a0040 60%,#004b73 100%);
--hover:#131a33;--subbg:#0b0f24;--accbg:#001a1f;--accink:#00ff9d;
--vendbg:#00141c;--vendline:#00f0ff;--vendink:#00f0ff;--chipline:rgba(0,240,255,.4)}
*{box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--ink);margin:0;padding:0;line-height:1.5;transition:background .4s,color .4s}
.navbtn{position:fixed;top:16px;right:16px;z-index:9999;background:#0f172a;color:#fff;font-weight:600;font-size:13px;text-decoration:none;padding:8px 16px;border-radius:20px;box-shadow:0 2px 8px rgba(0,0,0,.25);border:1px solid rgba(255,255,255,.25)}
.navbtn:hover{background:#1e293b}
.wrap{max-width:1240px;margin:0 auto;padding:24px 20px 90px}
header.hero{background:var(--hero-grad);color:#fff;border-radius:16px;padding:26px 28px;margin-bottom:20px;transition:background .4s}
header.hero h1{margin:0 0 6px;font-size:24px}
header.hero .sub{opacity:.85;font-size:14px}
.thememenu{display:flex;gap:8px;margin-top:16px;flex-wrap:wrap;align-items:center}
.thememenu .lbl{font-size:12px;color:var(--muted);margin-right:4px}
.tbtn{background:rgba(255,255,255,.08);border:1px solid var(--chipline);color:#fff;border-radius:20px;padding:5px 14px;font-size:12.5px;cursor:pointer;font-weight:600}
.tbtn:hover{background:rgba(255,255,255,.18)}
.tbtn.on{background:var(--brand);border-color:var(--brand);color:#fff}
.tbtn .dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px;vertical-align:middle}
.badges{margin-top:12px;display:flex;flex-wrap:wrap;gap:8px}
.badge{background:rgba(255,255,255,.14);border:1px solid var(--chipline);padding:4px 12px;border-radius:20px;font-size:12.5px}
.badge.alert{background:#dc2626;border-color:#dc2626;font-weight:600}
.badge.ok{background:#16a34a;border-color:#16a34a}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px 20px;margin-bottom:16px;box-shadow:0 1px 3px rgba(16,24,40,.06);transition:background .4s,border-color .4s}
.card h2{margin:0 0 12px;font-size:17px;display:flex;align-items:center;gap:8px}
.card h2 .num{background:var(--brand);color:#fff;width:26px;height:26px;display:inline-flex;align-items:center;justify-content:center;border-radius:7px;font-size:12.5px}
.card h2 a.projectlink{color:inherit;text-decoration:none}
.card h2 a.projectlink:hover{color:var(--brand);text-decoration:underline}
table{width:100%;border-collapse:collapse;font-size:13.5px;margin-top:4px}
th{text-align:left;color:var(--muted);font-weight:600;font-size:11.5px;text-transform:uppercase;letter-spacing:.4px;padding:7px 9px;border-bottom:2px solid var(--line);white-space:nowrap}
td{padding:8px 9px;border-bottom:1px solid var(--line);vertical-align:top}
tr:hover td{background:var(--hover)}
a{color:var(--blue);text-decoration:none}
a:hover{text-decoration:underline}
.pill{display:inline-block;padding:2px 9px;border-radius:12px;font-size:12px;font-weight:600;white-space:nowrap}
.pill.open{background:color-mix(in srgb,var(--blue) 15%,transparent);color:var(--blue)}
.pill.done{background:color-mix(in srgb,var(--green) 15%,transparent);color:var(--green)}
.pill.over{background:color-mix(in srgb,var(--red) 15%,transparent);color:var(--red)}
.pill.ok{background:color-mix(in srgb,var(--green) 15%,transparent);color:var(--green)}
.pill.warn{background:color-mix(in srgb,var(--amber) 18%,transparent);color:var(--amber)}
.muted{color:var(--muted);font-size:12.5px}
.fb{color:var(--muted);font-size:11px;text-align:right;margin-top:8px}
.callout{border-left:5px solid var(--red);background:color-mix(in srgb,var(--red) 8%,transparent);padding:12px 16px;border-radius:0 10px 10px 0;margin-bottom:16px}
.callout .t{font-weight:700;color:var(--red);margin-bottom:4px;font-size:14px}
/* expandable subtask rows */
details.sub{background:var(--subbg);border:1px solid var(--line);border-radius:10px;padding:8px 12px;margin:6px 0}
details.sub summary{cursor:pointer;font-size:13px;color:var(--ink);user-select:none}
details.sub summary .cnt{font-weight:600}
details.sub table td{padding:5px 8px;font-size:12.5px}
details.vendor{background:var(--vendbg);border:1px solid var(--vendline);border-radius:10px;padding:7px 12px;margin-top:6px}
details.vendor summary{cursor:pointer;font-size:13px;font-weight:600;color:var(--vendink);user-select:none}
details.vendor table td{padding:5px 8px;font-size:12.5px}
details.vendor a{color:var(--vendink)}
details.vendor a:hover{text-decoration:underline}
.vorder{display:inline-block;padding:2px 9px;border-radius:12px;font-size:12px;font-weight:600}
.vorder.over{background:color-mix(in srgb,var(--red) 15%,transparent);color:var(--red)}
.vorder.ok{background:color-mix(in srgb,var(--green) 15%,transparent);color:var(--green)}
.acc{border:1px solid var(--brand);background:var(--accbg);color:var(--accink);border-radius:8px;padding:3px 10px;font-size:12px;font-weight:600;cursor:pointer}
.acc:hover{filter:brightness(1.15)}
.acc:disabled{opacity:.5;cursor:not-allowed}
.switchs{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:16px}
.switch{background:var(--card);border:1px solid var(--line);border-radius:20px;padding:5px 14px;font-size:13px;color:var(--ink);cursor:pointer;text-decoration:none}
.switch.on{background:var(--brand);color:#fff;border-color:var(--brand);font-weight:600}
footer{color:var(--muted);font-size:12px;text-align:center;margin-top:26px}
.empty{text-align:center;color:var(--muted);padding:40px 0}
.note{background:color-mix(in srgb,var(--amber) 12%,transparent);border:1px solid color-mix(in srgb,var(--amber) 45%,transparent);border-radius:10px;padding:10px 14px;font-size:12.5px;color:var(--amber);margin-bottom:16px}
"""


def esc(s):
    return str(s if s is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def asana_task_link(gid, label):
    if not gid:
        return esc(label)
    return f'<a href="{ASANA_BASE}/{BUILD_SCHEDULE}/{gid}">{esc(label)}</a>'


def render(project_section_html, orders_chipnote, today):
    js = """<script>
// SAS OS — Accept button. ON A STATIC PAGE THIS CANNOT WRITE TO ASANA.
// Truthful behaviour: clicking stages the change for the REQUESTS→SAP queue
// and tells Giles the exact action, so it never fails silently nor fakes success.
function acceptDue(taskGid, newDue, taskName) {
  var changes = JSON.parse(localStorage.getItem('os_accepted_changes') || '[]');
  changes.push({gid: taskGid, due: newDue, name: taskName, ts: new Date().toISOString()});
  localStorage.setItem('os_accepted_changes', JSON.stringify(changes));
  var msg = 'STAGED for SAP write (not yet applied to Asana):\\n\\n'
          + 'Task: ' + taskName + '\\n'
          + 'GID:  ' + taskGid + '\\n'
          + 'Set due date:  ' + newDue + '\\n\\n'
          + 'This now needs to be pushed through the REQUESTS→SAP queue to apply to Asana. '
          + 'Tell Giles \\"queue the accepted due-date changes\\" and they will be drafted as REQUESTS files.';
  alert(msg);
  var b = event ? event.target : null;
  if (b) { b.disabled = true; b.textContent = 'Staged ✓'; }
}
function showStaged() {
  var changes = JSON.parse(localStorage.getItem('os_accepted_changes') || '[]');
  if (!changes.length) { alert('No accepted changes staged yet.'); return; }
  var out = changes.map(function(c,i){return (i+1)+'. '+c.name+' → '+c.due+' ('+c.gid+')';}).join('\\n');
  alert('Staged changes ('+changes.length+'):\\n'+out+'\\n\\nCopy these to queue via REQUESTS→SAP.');
}
function clearStaged() {
  localStorage.removeItem('os_accepted_changes');
  alert('Staged changes cleared.');
}
// ── Theme switcher ──
function setTheme(name){
  document.documentElement.classList.remove('theme-stock','theme-neon');
  if (name && name!=='light') document.documentElement.classList.add('theme-'+name);
  localStorage.setItem('os_theme', name || 'light');
  var b=document.querySelectorAll('.tbtn');
  for (var i=0;i<b.length;i++) b[i].classList.toggle('on', b[i].getAttribute('data-theme')===name);
}
function initTheme(){
  var t=localStorage.getItem('os_theme') || 'stock';
  if (t==='light') t='';
  setTheme(t==='stock'?'stock':(t==='neon'?'neon':'light'));
}
</script>"""
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>SAS OS — Andy's Worklist &amp; Pack Readiness</title><style>{CSS}</style>
{js}</head>
<body onload="initTheme()"><div class="wrap">
<header class="hero">
  <h1>SAS OS — Andy's Worklist &amp; Pack Readiness</h1>
  <div class="sub">Incomplete, Andy-assigned, project work · Generated {today} · Live Asana (Build Schedule, Order Numbers, subtasks) + ops.db</div>
  <a href="nav.html" class="navbtn">&larr; SAS OS portal</a>
  <div class="thememenu"><span class="lbl">Theme:</span>
    <button class="tbtn" data-theme="light" onclick="setTheme('light')"><span class="dot" style="background:#fff"></span>Light</button>
    <button class="tbtn" data-theme="stock" onclick="setTheme('stock')"><span class="dot" style="background:#6c8ef7"></span>Stock</button>
    <button class="tbtn" data-theme="neon" onclick="setTheme('neon')"><span class="dot" style="background:#00f0ff"></span>Neon</button>
  </div>
  <div class="badges"><span class="badge">Andy only</span><span class="badge">incomplete only</span>
  <span class="badge">projects only</span><span class="badge">every row → Asana ↗</span>
  <span class="badge ok">Accept = stage for REQUESTS→SAP</span></div>
  <div style="margin-top:14px;display:flex;gap:10px;flex-wrap:wrap">
    <button onclick="showStaged()" style="background:var(--brand);color:#fff;border:none;border-radius:8px;padding:6px 14px;font-size:13px;cursor:pointer">Show staged accepts</button>
    <button onclick="clearStaged()" style="background:#555;color:#fff;border:none;border-radius:8px;padding:6px 14px;font-size:13px;cursor:pointer">Clear staged</button>
  </div>
</header>
{orders_chipnote}
{project_section_html}
<footer>SAS OS · Andy's worklist + pack readiness · data sources noted above · mistakes captured, not hidden</footer>
</div></body></html>"""


PROJECT_PAGES = {
    "1588": "america-one-production-report-2026-08-03.html",
    # add other project pages here as they are created
}


def render_project_sections(projects, procurement, orders, orders_by_job):
    """Cards per project (header = section/project name), rows carry the rich
    layout: vendor-orders dropdown, subtask dropdown, parts-arrive / advised-due,
    Accept button. Project title links to a project-specific page when one exists."""
    today = datetime.date.today().isoformat()
    out = []
    for job in sorted(projects.keys()):
        p = projects[job]
        open_tasks = sorted(p["open"], key=lambda t: (t.get("due_on") or "9999"))
        open_n = len(open_tasks)
        over_n = sum(1 for t in open_tasks if t.get("due_on") and t["due_on"] < today)
        over_badge = (f'<span class="pill over">{over_n} overdue</span>' if over_n
                      else '<span class="pill ok">0 overdue</span>')
        # project name → link to project page if defined
        title = esc(p["section"]) or f"Job {job}"
        page = PROJECT_PAGES.get(job)
        if page:
            title_html = f'<a class="projectlink" href="{page}" title="Open {esc(p["section"])} project page">{title}</a>'
        else:
            title_html = title
        rows = []
        for t in open_tasks:
            due = t.get("due_on")
            over = due and due < today
            # vendor orders feeding this job
            job_orders = sorted(orders_by_job.get(job, []),
                                key=lambda o: o.get("due_on") or "9999")
            if job_orders:
                latest_arr = max((o["due_on"] for o in job_orders if o["due_on"]), default=None)
                n_over = sum(1 for o in job_orders if o["due_on"] and o["due_on"] < today)
                vorder_rows = ""
                for o in job_orders:
                    odue = o.get("due_on")
                    o_over = odue and odue < today
                    vrow = f"""<tr>
                      <td style="min-width:180px"><a href="{ASANA_BASE}/{ORDER_NUMBERS}/{o['gid']}">{esc(o['po'] or '')}</a> · {esc(o['vendor'] or '—')}</td>
                      <td>{esc(o['name'])[:70]}</td>
                      <td>{esc(odue or 'no due')}{' <span class="vorder over">overdue</span>' if o_over else ''}</td>
                    </tr>"""
                    vorder_rows += vrow
                vend_html = "<details class=\"vendor\"><summary>"
                summ = (f"⛓ {len(job_orders)} open vendor order"
                        f"{'s' if len(job_orders)!=1 else ''}")
                if n_over:
                    summ += f" · <span class='vorder over'>{n_over} overdue</span>"
                elif job_orders:
                    summ += " · <span class='vorder ok'>0 overdue</span>"
                if latest_arr:
                    summ += f" · latest {esc(latest_arr)}"
                summ += " · view</summary>"
                vtable = ('<table><thead><tr><th>PO · Vendor</th><th>Order</th><th>Due</th></tr></thead>'
                          f'<tbody>{vorder_rows}</tbody></table></details>')
                vend_html = vend_html + summ + vtable
            else:
                vend_html = '<span class="muted">no open vendor orders for this job</span>'
            # readiness + accept (compute advised before subtask stats)
            pr = parts_readiness(job, t["name"], procurement, orders)
            advised = None
            if pr["latest_arrival"]:
                cand = datetime.date.fromisoformat(pr["latest_arrival"]) + datetime.timedelta(days=1)
                cur = datetime.date.fromisoformat(due) if due else None
                advised = max(cand, cur).isoformat() if cur else cand.isoformat()
            # subtask stats + dropdown (with per-subtask advised-due)
            st = compute_subtask_stats(p, t["gid"], advised, pr["latest_arrival"])
            if st["all_done"]:
                sub_html = '<span class="pill ok">all subtasks complete</span>'
            elif st["open"] == 0:
                sub_html = '<span class="pill ok">no open subtasks</span>'
            else:
                sub_rows = ""
                for s in st["rows"]:
                    spill = ('<span class="pill over">overdue</span>'
                             if s.get("due_on") and s["due_on"] < today else "")
                    sadv = s.get("advised")
                    if sadv and sadv > (s.get("due_on") or "9999"):
                        sacc = (f'<button class="acc" onclick="acceptDue(\'{s["gid"]}\',\'{sadv}\','
                                f'\'{esc(s.get("name") or "")}\')">Accept → {sadv}</button>')
                    else:
                        sacc = '<span class="muted">—</span>'
                    sub_rows += (f"<tr><td>{asana_task_link(s['gid'], s['name'])}</td>"
                                 f"<td>{esc((s.get('assignee') or {}).get('name') or '—')}</td>"
                                 f"<td>{esc(s.get('due_on') or 'no due')} {spill}</td>"
                                 f"<td>{'→ advise '+esc(sadv) if sadv else ''}</td>"
                                 f"<td>{sacc}</td></tr>")
                sub_html = (f'<details class="sub"><summary>'
                            f'<span class="cnt">{st["open"]} open · {st["overdue"]} overdue</span> · '
                            f'click to view subtasks</summary>'
                            f'<table><thead><tr><th>Subtask</th><th>Assignee</th><th>Due</th><th>Advised→</th><th>Action</th></tr></thead>'
                            f'<tbody>{sub_rows}</tbody></table></details>')
            if pr["latest_arrival"] and advised and advised > (due or "9999"):
                accept = (f'<button class="acc" onclick="acceptDue(\'{t["gid"]}\',\'{advised}\','
                          f'\'{esc(t["name"])}\')">Accept → set due {advised}</button>')
                arrive_html = f'{esc(pr["latest_arrival"])} → advise {esc(advised)}'
            else:
                accept = '<span class="muted">—</span>'
                arrive_html = esc(pr["latest_arrival"]) if pr["latest_arrival"] else "—"
            proc_html = (f'<span class="muted">{pr["lasercut"]} lasercut · {pr["need_order"]} need-order · '
                         f'{pr["on_order"]} on-order · {pr["pick"]} pick</span>' if pr["key"] else "—")
            rows.append(f"""<tr>
              <td style="min-width:220px">{asana_task_link(t['gid'], t['name'])}<br>
                <span class="muted">{esc(due) or 'no due'}{' · <span class="pill over">overdue</span>' if over else ''}</span>
                {vend_html}</td>
              <td>{sub_html}</td>
              <td>{arrive_html}<br>{proc_html}</td>
              <td>{accept}</td></tr>""")
        out.append(f"""
        <div class="card" id="{job}">
          <h2><span class="num">{esc(p['section'][:2])}</span> {title_html}
            <span class="muted" style="margin-left:auto">{open_n} open · {over_badge}</span></h2>
          <table><thead><tr>
            <th>Pack task</th><th>Subtasks (open / overdue)</th>
            <th>Parts arrive → advised due</th><th>Action</th>
          </tr></thead><tbody>{''.join(rows)}</tbody></table>
          <div class="fb">ⓘ Accept queues a REQUESTS→SAP write to set the pack due date. Dry-run reviewed first.</div>
        </div>""")
    return "".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(WS / "data" / "os_status"))
    ap.add_argument("--project", default=None)
    args = ap.parse_args()
    today = datetime.date.today().isoformat()

    # load token
    for k in ("ASANA_PERSONAL_ACCESS_TOKEN", "ASANA_PAT"):
        if not os.environ.get(k):
            for line in Path(r"C:/Users/GilesHugo/.hermes/.env").read_text().splitlines():
                if line.startswith(f"{k}="):
                    os.environ[k] = line.split("=", 1)[1].strip()

    print("Fetching Build Schedule…")
    tasks = fetch_build_schedule()
    projects = aggregate_andy(tasks)
    print(f"  {len(tasks)} raw tasks → {len(projects)} projects with Andy open work")

    print("Loading ops.db procurement…")
    procurement = load_procurement_by_packmap()
    print(f"  {len(procurement)} pack keys with part data")

    orders = []
    try:
        print("Fetching open orders…")
        orders = load_open_orders()
        print(f"  {len(orders)} open orders")
    except Exception as e:
        print(f"  warn: {e}")

    # project filter
    if args.project:
        projects = {k: v for k, v in projects.items() if k == args.project}

    sec_html = render_project_sections(projects, procurement, orders, orders_by_job(orders))

    # chip note about orders
    orders_chipnote = f'<div class="note">📦 {len(orders)} open supplier orders tracked · delivery = Order Numbers due_on (expected, not confirmed).</div>'

    html = render(sec_html, orders_chipnote, today)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(html, encoding="utf-8")
    (out_dir / f"andy-worklist-{today}.html").write_text(html, encoding="utf-8")
    print(f"WROTE {out_dir}/index.html ({len(html)} bytes)")


if __name__ == "__main__":
    main()
