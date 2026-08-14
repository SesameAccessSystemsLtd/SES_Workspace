#!/usr/bin/env python3
"""
build_bom_tracker.py — SAS OS: Karol BOM-processing lifecycle tracker (v3).

v3 restructure (2026-08-13, after the new 1595 St George's CAL release exposed the
data-location gap):

  * SOURCE FIX — inbound Karol/Handover/BOM/PLC control tasks are read from the
    1.4 Purchasing Dept project (850066254549317), NOT Build/Project Orders. The
    ALL BOMS roots + vendor-groups (processed OUTPUT) still live in Build/Project
    Orders (848657415032331). This removes the duplication/legacy noise.
  * ONLY INCOMPLETE — legacy/completed inbound tasks are not listed.
  * WATCHER-STYLE — any task under an unticked (open) Karol Generate Entire BOM
    control is surfaced regardless of assignee (Karol often forgets to assign to
    Giles). Open sub-BOMs = needs-action with a Stage-to-pipeline button.
  * LAYOUT — all secondary sections (PLC, Handover Legacy, Steel, vendor-open,
    no-assembly-pack) as expandable blocks at the top; the issued-BOM cards follow,
    each with an inline expandable 'outstanding items' (un-ordered parts) block.

Output: data/os_status/bom-tracker.html (+ dated copy).
Usage:  python build_bom_tracker.py [--out DIR]
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

from build_os_status import (
    WS, OPS_DB, ORDER_NUMBERS, ASANA_BASE, API_BASE, CSS,
    asana_token, headers, paginate, esc, load_open_orders,
)

OUT_DEFAULT = WS / "data" / "os_status"
PURCH_DEPT = "850066254549317"        # 1.4 Purchasing Dept — inbound Karol/Handover/PLC/BOM controls
BUILD_ORDERS = "848657415032331"      # Project Orders — ALL BOMS roots + vendor-groups (output)
PARTS_CONTROL = "1211660707080749"

# NEW CAL release (2026-08-13) — the concrete live case to surface.
NEW_RELEASE = {"gid": "1215905006904419",
               "label": "Karol Generate Entire BOM - 1595 - St George's House"}

# ── classification ──────────────────────────────────────────────────────────
def classify_inbound(name):
    low = (name or "").strip().lower()
    if re.match(r"^karol generate entire bom", low): return "KAROL"
    if re.match(r"^handover legacy bom", low):       return "HANDOVER"
    if re.match(r"^bom\s*:", low):                   return "BOM:"
    if re.match(r"^issue plc bom", low):             return "PLC"
    if re.match(r"^rfq bom", low):                   return "RFQ"
    if re.match(r"^long[ \-]?lead", low) or re.match(r"^long leading", low): return "LONGLEAD"
    return None

def job_of(name):
    m = re.search(r"(?:^|[-\s_])(\d{3,4})(?=[\s\-:._]|$)", name or "")
    return m.group(1) if m else None

FILLER = {"entire","bom","generated","replacement","new","task","to","giles","the","for",
          "rev","single","traversing","scissor","lift","street","house","church"}
def bom_family(control_name):
    low = re.sub(r"^(all boms\s*[-:]?|karol generate entire bom|bom\s*:|issue plc bom|rfq bom|long lead[^\w]*|long leading[^\w]*)",
                 "", (control_name or "").lower())
    low = re.sub(r"\b\d{3,4}\b", " ", low)
    low = re.sub(r"[^a-z0-9]+", " ", low).strip()
    return " ".join([w for w in low.split() if w not in FILLER and len(w) > 2])

# ── stage ───────────────────────────────────────────────────────────────────
STG_COLORS = {1:"var(--amber)",2:"var(--blue)",3:"var(--blue)",4:"var(--brand)",
              5:"var(--amber)",6:"var(--green)"}
def stage_from_signals(ctl, root):
    nimg = (ctl.get("notes") or "").lower()
    done = bool(ctl.get("completed"))
    dbx = "dropbox.com" in (ctl.get("notes") or "")
    proc = ("processed in https://app.asana.com" in nimg or "processed" in nimg)
    if root:
        if root.get("completed") and done: return (6, "Control + root complete")
        if root.get("completed"):          return (6, "Root completed")
        return (4, "Root open — parts stand up in Parts Control")
    if done:  return (5, "Control complete, no root on Asana")
    if proc:  return (3, "Pipeline marked processed on control")
    if dbx:   return (2, "Dropbox BOM present, awaiting processing")
    return (1, "Inbound BOM request")

# ── custom-field helpers ────────────────────────────────────────────────────
# Called-on fields: ORDER Status (1143955334207040) + BOM STATUS (1201093757113231)
# carry the vendor/PLC status Giles wants surfaced. We read by name so we don't
# hardcode gids in render (the ASANA bulk fetch already carries enum_value.name).
def cf_value(task, name):
    """Return the string value of a named enum custom field on a raw Asana task."""
    for cf in (task.get("custom_fields") or []):
        if (cf.get("name") or "").strip().lower() == (name or "").strip().lower():
            ev = cf.get("enum_value")
            if ev:
                return ev.get("name") or ""
            # number/text fields
            if cf.get("display_value") is not None:
                return str(cf.get("display_value"))
    return ""

BOM_STATUS_FIELD  = "BOM STATUS"
ORDER_STATUS_FIELD = "ORDER Status"

def load_jajo_by_job(build_tasks):
    """Map {job: [JAJO vendor tasks]}. JAJO orders are vendor tasks nested under
    the 'ALL BOMS ... Long Lead' master task in Build/Project Orders (1.3), named
    '{PO}-{job} - JAJO - {desc}'. Detect them by traversing each Long-Lead master's
    subtree and collecting any task whose name contains '- JAJO -' with the job."""
    kids = defaultdict(list)
    for t in build_tasks:
        p = (t.get("parent") or {}).get("gid")
        if p:
            kids[p].append(t)
    out = defaultdict(list)
    for master in build_tasks:
        nm = master.get("name") or ""
        low = nm.lower()
        if not re.match(r"^all boms", low) or "long lead" not in low:
            continue
        j = job_of(nm)
        if not j:
            continue
        seen = set()
        def walk(gid):
            if gid in seen:
                return
            seen.add(gid)
            for c in kids.get(gid, []):
                cn = c.get("name") or ""
                if " - jajo - " in cn.lower():
                    out[j].append(c)
                walk(c.get("gid"))
        walk(master.get("gid"))
    return dict(out)


def load_project_tasks(gid):
    return paginate(f"/projects/{gid}/tasks", {
        "opt_fields": "name,gid,completed,due_on,assignee.name,notes,parent.name,"
                      "custom_fields.name,custom_fields.gid,custom_fields.enum_value.name,custom_fields.display_value"})

def subtask_list(gid, fields="name,gid,completed,due_on,assignee.name,notes,parent.name"):
    try:
        return paginate(f"/tasks/{gid}/subtasks", {"opt_fields": fields})
    except Exception:
        return []

def _task_notes(gid):
    try:
        j = requests.get(f"{API_BASE}/tasks/{gid}",
                         headers=headers(), params={"opt_fields": "notes"}).json()
        return (j.get("data") or {}).get("notes") or ""
    except Exception:
        return ""

def _extract_dropbox(notes):
    m = re.search(r"https://(?:www\.)?dropbox\.com/\S+", notes or "")
    return m.group(0).rstrip(",.)\"'") if m else ""

def count_open_parts(root_gid, depth=2):
    seen = set()
    def walk(gid, d):
        if d < 0 or gid in seen: return
        seen.add(gid)
        try: subs = paginate(f"/tasks/{gid}/subtasks", {"opt_fields": "name,gid,completed,due_on"})
        except Exception: return
        for s in subs:
            if s.get("completed"): continue
            yield s
            yield from walk(s["gid"], d-1)
    return list(walk(root_gid, depth))

def load_ops_pack_links():
    c = sqlite3.connect(OPS_DB); c.row_factory = sqlite3.Row
    rows = c.execute("SELECT sesame_pn, build_assembly_pack_gid FROM parts_control").fetchall()
    c.close()
    per = defaultdict(list)
    for r in rows:
        pn = (r["sesame_pn"] or "").strip(); bp = (r["build_assembly_pack_gid"] or "").strip()
        m = re.match(r"^(\d{4})", pn); job = m.group(1) if m else "?"
        linked = bool(bp) and bp.lower() not in ("", "-", "null", "none")
        per[job].append((pn, linked))
    out = {}
    for job, items in per.items():
        bypn = defaultdict(list)
        for pn, ln in items: bypn[pn].append(ln)
        parts = {pn: any(bypn[pn]) for pn in bypn}
        no_pack = [pn for pn, ln in parts.items() if pn and (pn[0].isdigit() or '.' in pn) and not ln]
        out[job] = {"total": len(parts), "no_pack": len(no_pack)}
    return out

def bom_priority(r):
    """Tier so in-flight / urgent BOMs float to the top, bare one-off inbound
    requests sink to the bottom (Giles 2026-08-13): root open (picking / parts
    standing up) OR a flagged NEW release first, then orders-feeding, then
    ready-with-dropbox, then any-with-due, then no-signal backlog last."""
    if r["root"] and not r["root"].get("completed"): return 0   # parts stand up in Parts Control
    if r.get("is_new"):                              return 0   # new CAL release (St George's)
    if r["orders"]:                                  return 1   # orders feeding the job
    if r["root"]:                                    return 2   # root exists (post-picking)
    if r.get("has_dropbox"):                         return 3   # BOM ready, awaiting processing
    if r.get("due_on"):                              return 4   # bare inbound w/ a date
    return 5                                                       # bare inbound, no date → push down

# ── build dataset ───────────────────────────────────────────────────────────
BUILD_JOBS = {"1037","1366","1373","1501","1514","1535","1536","1555","1572","1575",
              "1578","1579","1580","1581","1582","1588","1591","1594","1595","1669","1670"}

def build_dataset(purch_tasks, build_tasks, open_orders):
    # inbound from Purchasing Dept; roots + vendor groups from Build/Project Orders
    inbound, handover, steel, plc_all = [], [], [], []
    for t in purch_tasks:
        nm = t.get("name") or ""
        fam = classify_inbound(nm)
        if fam == "HANDOVER": handover.append(t)
        elif fam: inbound.append(t)
    for t in build_tasks:
        nm = t.get("name") or ""
        low = nm.strip().lower()
        if re.match(r"^all boms", low): plc_all.append(("ROOT", t))
        elif re.match(r"^steel profile bom", low): steel.append(t)

    roots = [t for k, t in plc_all if k == "ROOT"]
    roots_by_job = defaultdict(list)
    for r in roots:
        j = job_of(r.get("name") or "")
        if j: roots_by_job[j].append(r)
    orders_by_job = defaultdict(list)
    for o in open_orders:
        if o.get("job"): orders_by_job[o["job"]].append(o)
    # JAJO vendor orders live under the ALL BOMS * Long Lead master (per Giles).
    jajo_by_job = load_jajo_by_job(build_tasks)

    # steel profile BOMs -> match to Karol sub-BOMs BY JOB. A steel profile BOM is
    # 'imported' if it has >=1 subtask (its subtasks ARE the vendor order tasks — an
    # imported/processed steel BOM always has subtasks). If the task is COMPLETE we
    # don't surface it, but we still track whether one exists+imported per Karol BOM.
    steel_by_job = {}
    for s in steel:
        j = job_of(s.get("name") or "")
        if not j: continue
        subs = subtask_list(s["gid"], "name,gid,completed")
        steel_by_job[j] = {
            "task": s, "completed": bool(s.get("completed")),
            "n_subtasks": len(subs),
            "open_subs": sum(1 for x in subs if not x.get("completed")),
            "imported": len(subs) > 0,
        }

    # inbound rows — INCOMPLETE ONLY
    rows = []
    for ctl in inbound:
        if ctl.get("completed"): continue
        job = job_of(ctl.get("name") or "")
        fam = bom_family(ctl.get("name") or ""); ff = (fam or "").split()[0] if fam else ""
        best = None
        for r in roots_by_job.get(job or "", []):
            rf = (bom_family(r.get("name") or "").split()[0] if bom_family(r.get("name") or "") else "")
            if fam and (ff == rf or ff in bom_family(r.get("name") or "") or rf in fam):
                best = r; break
        open_parts = count_open_parts(best["gid"]) if best else []
        st_i, st_d = stage_from_signals(ctl, best)
        # outstanding/un-ordered items: open parts in root, else order-visible signal
        rows.append({
            "ctrl_gid": ctl.get("gid"), "ctrl_name": ctl.get("name") or "", "job": job,
            "family": classify_inbound(ctl.get("name") or ""),
            "assignee": ((ctl.get("assignee") or {}).get("name") or ""),
            "notes": ctl.get("notes") or "", "has_dropbox": "dropbox.com" in (ctl.get("notes") or ""),
            "due_on": ctl.get("due_on"), "is_new": ctl.get("gid") == NEW_RELEASE["gid"],
            "root": best, "open_parts": open_parts,
            "orders": orders_by_job.get(job or "", []),
            "jajo": jajo_by_job.get(job or "", []),
            "bom_status": cf_value(ctl, BOM_STATUS_FIELD),
            "order_status": cf_value(ctl, ORDER_STATUS_FIELD),
            "stage_idx": st_i, "stage_detail": st_d,
        })
    rows.sort(key=lambda r: (
        bom_priority(r), r.get("due_on") or "9999", r["job"] or "ZZ"))

    plc_rows = [r for r in rows if r["family"] == "PLC"]

    # Karol open sub-BOMs (watcher-style: ignore assignee, any unticked Karol ctl)
    karol_action = []
    for t in purch_tasks:
        if not re.match(r"^karol generate entire bom", (t.get("name") or ""), re.I): continue
        if t.get("completed"): continue
        os_ = [s for s in subtask_list(t["gid"]) if not s.get("completed")]
        if os_:
            subs = []
            for s in os_:
                sn = _task_notes(s["gid"])
                subs.append({"gid": s["gid"], "name": s.get("name") or "",
                             "due_on": s.get("due_on"),
                             "dropbox": _extract_dropbox(sn)})
            karol_action.append({"ctl": t, "subtasks": subs, "job": job_of(t.get("name") or "")})
    # attach steel-profile imported status per Karol sub-BOM (matched by job)
    for k in karol_action:
        k["steel"] = steel_by_job.get(k["job"])

    # vendor groups open (from Build/Project Orders) with open unticked parts
    vendor_open = []
    prefixes = ("laser cut","machining","powder coating","engraving","anodising","finish",
                "nbss","sesame","marine lacquer","cement board","glass","bellows")
    for t in build_tasks:
        if t.get("completed"): continue
        nm = t.get("name") or ""; low = nm.strip().lower()
        if not (any(low.startswith(p) for p in prefixes) and re.search(r"\d{3,4}", nm)):
            continue
        os_ = [s for s in subtask_list(t["gid"], "name,gid,completed,due_on") if not s.get("completed")]
        if os_:
            vendor_open.append({
                "task": t, "job": job_of(nm), "open_subs": os_,
                "due_on": t.get("due_on"),
                "order_status": cf_value(t, ORDER_STATUS_FIELD),
                "bom_status": cf_value(t, BOM_STATUS_FIELD),
            })
    # alphabetize vendor groups so same treatment/cost-centre tasks group together
    vendor_open.sort(key=lambda v: (v["task"].get("name") or "").strip().lower())

    # ── REVERSE-DERIVED issued view (Giles 2026-08-13) ────────────────────────
    # The old "Issued BOMs" started from inbound Karol/'Karol Generate Entire BOM'
    # controls — but those controls (and their source BOM) get CLOSED when orders
    # go in-process, so any job with still-outstanding vendor deliveries vanished
    # from Issued. Fix: derive Issued from OPEN VENDOR GROUPS and resolve BACK up
    # to the job + source BOM. Each job row carries a Parts-Control search link
    # (Giles's pattern) + its open vendor groups + the ALL BOMS root.
    issued_rows_vg = []
    by_job_vg = defaultdict(list)
    for v in vendor_open:
        by_job_vg[v["job"]].append(v)
    for job in sorted(by_job_vg.keys(), key=lambda j: (j or "ZZ")):
        groups = by_job_vg[job]
        # source-BOM / project name: strip treatment prefix + job from a group name
        proj = ""
        for v in groups:
            nm = (v["task"].get("name") or "")
            # e.g. 'Laser Cut - 1591 - Traversing replacement Feet' -> after job.
            # Handle '-',':','.',' ' separators after the job number.
            m = re.search(r"\d{3,4}\s*[-:.\s]\s*(.+)$", nm)
            if m:
                cand = m.group(1).strip()
                # drop trailing price/notes tokens like '= £170 ex VAT', '£2600'
                cand = re.sub(r"\s*=\s*[£$€][\d,\.]+.*$", "", cand).strip()
                cand = re.sub(r"\s+£[\d,\.]+.*$", "", cand).strip()
                if cand:
                    proj = cand
                    break
        roots = roots_by_job.get(job, [])
        root = roots[0] if roots else None
        # open vendor total + overdue count
        issues = []
        overdue = 0
        for v in groups:
            due = v.get("due_on")
            if due and due < datetime.date.today().isoformat():
                overdue += 1
            issues.append(v)
        issued_rows_vg.append({
            "job": job, "project": proj,
            "root": root, "roots": roots, "groups": issues, "overdue": overdue,
            "total_groups": len(issues),
            # Giles's parts-control search link, per job
            "pc_url": f"https://app.asana.com/0/search?q={job}&sort=last_modified&searched_type=task&completion=incomplete&deleted=no_trashed&locatedIn=inAnyOfTheseProjects&any_projects.ids={PARTS_CONTROL}",
        })

    pack_links = load_ops_pack_links()
    no_pack_jobs = []
    for job, info in sorted(pack_links.items(), key=lambda kv: (kv[1]["no_pack"], kv[0])):
        if job in ("?",) or job not in BUILD_JOBS or info["no_pack"] <= 3: continue
        hand = next((h for h in handover if job_of(h.get("name") or "") == job), None)
        no_pack_jobs.append({"job": job, "no_pack": info["no_pack"],
                             "handover": hand, "anchor": hand["name"] if hand else None})

    counts = {
        "issued": len([r for r in rows if r["family"] in ("KAROL","BOM:","RFQ","LONGLEAD")]),
        "plc": len(plc_rows), "handover_open": len([h for h in handover if not h.get("completed")]),
        "karol_action": len(karol_action), "vendor_open": len(vendor_open),
        "no_pack_parts": sum(i["no_pack"] for i in no_pack_jobs), "steel": len(steel_by_job),
    }
    return {"rows": rows, "plc": plc_rows, "handover": handover, "karol_action": karol_action,
            "vendor_open": vendor_open, "steel_by_job": steel_by_job, "no_pack_jobs": no_pack_jobs,
            "issued_rows_vg": issued_rows_vg,
            "counts": counts, "roots": roots}

# ── render ──────────────────────────────────────────────────────────────────
SEC_CSS = """
.sec-card{margin-top:4px}
details.sec{background:var(--subbg);border:1px solid var(--line);border-radius:12px;padding:12px 16px;margin:12px 0}
details.sec summary{cursor:pointer;font-size:14.5px;font-weight:700;color:var(--ink);user-select:none}
details.sec .cnt{color:var(--brand)}
.stack{display:flex;flex-direction:column;gap:10px}
.steelbox{border-radius:8px;padding:7px 12px;font-size:12.5px;margin-top:8px;font-weight:600}
.steelbox.ok{background:color-mix(in srgb,var(--green) 12%,transparent);border:1px solid var(--green);color:var(--green)}
.steelbox.open{background:color-mix(in srgb,var(--amber) 12%,transparent);border:1px solid var(--amber);color:var(--amber)}
"""
BTN_JS = """
<script>
var BOM_DISPATCH_URL = 'http://127.0.0.1:8765/dispatch';
// REAL dispatch: POST the payload to the local dispatch server, which posts the
// compiled prompt into the Purchasing Telegram topic and drops an auditable
// REQUESTS file. No fake 'Staged' — the button reports the true result.
function stageAction(payloadJson, kind) {
  var p = JSON.parse(payloadJson);
  var b = event ? event.target : null;
  if (b) { b.disabled = true; b.textContent = 'Dispatching…'; }
  fetch(BOM_DISPATCH_URL, {method:'POST', headers:{'Content-Type':'application/json'}, body: payloadJson})
    .then(function(r){ return r.json().then(function(j){ return {ok:r.ok, j:j}; }); })
    .then(function(res){
      if (res.ok && res.j && res.j.ok) {
        if (b) { b.textContent = 'Dispatched \\u2713'; }
        var l = JSON.parse(localStorage.getItem('bom_tracker_staged')||'[]');
        l.push(Object.assign({kind:kind, ts:new Date().toISOString(), request_file:res.j.request_file}, p));
        localStorage.setItem('bom_tracker_staged', JSON.stringify(l));
        alert(kind + ' DISPATCHED to the Purchasing topic.\\n'
            + (res.j.topic_sent ? 'Posted to Telegram \\u2713' : 'Telegram post FAILED (request still queued for run)')
            + '\\nRequest: ' + res.j.request_file
            + '\\n\\nRuns preflight \\u2192 Stage 4 (no writes). Review Stage 4, then approve the commit.');
      } else {
        if (b) { b.disabled = false; b.textContent = '\\u25B6 retry dispatch'; }
        alert('Dispatch FAILED: ' + ((res.j && res.j.error) || 'unexpected response'));
      }
    })
    .catch(function(e){
      if (b) { b.disabled = false; b.textContent = '\\u25B6 retry dispatch'; }
      alert('Dispatch server unreachable \\u2014 start bom_dispatch_server.py on the host.\\n' + e);
    });
}
function showStagedBom(){var l=JSON.parse(localStorage.getItem('bom_tracker_staged')||'[]');if(!l.length){alert('None.');return;}alert('Dispatched ('+l.length+'):\\n'+l.map(function(r,i){return (i+1)+' ['+r.kind+'] '+(r.job||'')+(r.name?' '+r.name:'');}).join('\\n'));}
function clearStagedBom(){localStorage.removeItem('bom_tracker_staged');alert('Cleared local dispatch log.');}
</script>
"""
def _jsesc(s):
    """Escape a JSON payload for safe embedding in an HTML onclick attribute.

    The payload is embedded as a JS single-quoted string literal inside a
    double-quoted HTML attribute:
        onclick="stageAction('<json>', 'kind')"
    Therefore we must escape for BOTH contexts:
      * HTML attribute:  '&' -> '&amp;', '"' -> '&quot;', '<' -> '&lt;', '>' -> '&gt;'
      * JS string:       '\\' -> '\\\\', "'" -> "\\'", and strip newlines
    ORDER: escape '&' FIRST (so we never double-escape a later-written entity).
    Without the HTML side, a raw '"' inside the payload terminates the onclick
    attribute early and the browser drops the handler (verified root cause of the
    button doing nothing, 2026-08-14)."""
    s = s or ""
    return (s.replace("&", "&amp;")            # must come first — else we double-escape entities
             .replace('"', "&quot;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
             .replace("\\", "\\\\")            # JS escaping
             .replace("'", "\\'")
             .replace("\n", " ").replace("\r", " "))

def esj(j): return esc(j or "?")
def stage_badge(i, d):
    return f'<span class="pill" style="background:{STG_COLORS.get(i,"var(--amber)")}22;color:{STG_COLORS.get(i,"var(--amber)")}">{i} · {esc(d)}</span>'

def due_cell(due, today):
    """HTML pill for a due date, red when overdue."""
    if not due:
        return '<span class="muted">—</span>'
    if due < today:
        return f'<span class="pill over">due {esc(due)} · OVERDUE</span>'
    return f'<span class="pill open">due {esc(due)}</span>'

def order_cell(orders, job, today):
    if not orders: return f'<span class="muted">none feeding {esj(job)}</span>'
    lis = []
    for o in orders[:5]:
        due = o.get("due_on")
        due_tag = ""
        if due:
            cls = "over" if due < today else "open"
            due_tag = f' <span class="pill {cls}">{esc(due)}</span>'
        lis.append(f'<a href="{ASANA_BASE}/{ORDER_NUMBERS}/{o["gid"]}">{esc(o.get("name","")[:40])}</a>{due_tag}')
    if len(orders) > 5: lis.append(f'<span class="muted">+{len(orders)-5}</span>')
    return "<br>".join(lis)

def outstanding_block(r, today):
    """Inline expandable per-issued-BOM outstanding (un-ordered) items.
    Shows each part's due date, red-highlighted when overdue."""
    if not r["open_parts"]:
        return '<div class="muted">No outstanding open parts (root fully picked or no root).</div>'
    rows = []
    for s in r["open_parts"][:12]:
        due = s.get("due_on")
        if due and due < today:
            due_cell = f'<span class="pill over">due {esc(due)} · OVERDUE</span>'
        elif due:
            due_cell = f'<span class="pill open">due {esc(due)}</span>'
        else:
            due_cell = '<span class="muted">no due</span>'
        rows.append(
            f'<tr><td><a href="{ASANA_BASE}/{PARTS_CONTROL}/{s["gid"]}">{esc(s.get("name","")[:46])}</a></td>'
            f'<td>{due_cell}</td><td><span class="pill open">open</span></td></tr>')
    more = f'<tr><td colspan="3" class="muted">+{len(r["open_parts"])-12} more</td></tr>' if len(r["open_parts"])>12 else ""
    return f"""<details class="sec"><summary>Outstanding · {len(r['open_parts'])} un-picked / un-ordered part(s)</summary>
<table><tr><th>Part task</th><th>Due</th><th>State</th></tr>{''.join(rows)}{more}</table></details>"""

def render(data, NEW, today):
    c = data["counts"]
    issued_badge = len(data.get("issued_rows_vg") or [])
    badges = "".join(f'<span class="badge">{k}: {v}</span>' for k, v in [
        ("active jobs (vendor work)", issued_badge), ("PLC", c["plc"]), ("Handover open", c["handover_open"]),
        ("Karol action", c["karol_action"]), ("vendor groups", c["vendor_open"]),
        ("no-pack parts", c["no_pack_parts"]), ("Steel", c["steel"])])
    new_alert = ""
    nrow = next((r for r in data["rows"] if r.get("is_new")), None)
    if nrow:
        new_alert = f"""<div class="callout" style="border-color:var(--red)">
<div class="t">🚨 NEW CAL RELEASE — {esc(nrow['ctrl_name'])}</div>
Open sub-BOMs: {esc(', '.join(s['name'] for s in next((k['subtasks'] for k in data['karol_action'] if k['job']==nrow['job']), [])) or 'n/a')}
· Unassigned to Giles (Karol didn't assign) — the watcher now catches this.
· <a href="{ASANA_BASE}/850066254549317/{NEW['gid']}">Open task →</a></div>"""

    # PLC section — moved to the BOTTOM of the page (spec-sheet handover, feeds a
    # Karol sub-BOM item task "Machine Room Cabinet" with vendor JAJO). It does NOT
    # function like a Karol sub-BOM. Shows each Issue PLC task's BOM STATUS field
    # and, if imported, the JAJO vendor task (which lives under ALL BOMS * Long Lead).
    plc_html = ""
    if data["plc"]:
        rows = ""
        for r in data["plc"]:
            bom = r.get("bom_status") or ""
            if not bom:
                bom_badge = '<span class="muted">—</span>'
            elif "ready to order" in bom.lower():
                bom_badge = f'<span class="pill warn">READY TO ORDER</span>'
            elif "on hold" in bom.lower():
                bom_badge = f'<span class="pill over">{esc(bom)}</span>'
            else:
                bom_badge = f'<span class="pill open">{esc(bom)}</span>'
            # JAJO task if imported for this job (vendor task under Long Lead master)
            jajo_cell = ""
            if r.get("jajo"):
                links = []
                for j in r["jajo"]:
                    jst = cf_value(j, ORDER_STATUS_FIELD) or cf_value(j, BOM_STATUS_FIELD)
                    jtag = f' · <span class="muted">{esc(jst)}</span>' if jst else ""
                    links.append(f'<a href="{ASANA_BASE}/{BUILD_ORDERS}/{j["gid"]}">{esc(j.get("name","")[:38])}</a>{jtag}')
                jajo_cell = "<br>".join(links)
            else:
                jajo_cell = '<span class="muted">no JAJO order imported yet</span>'
            rows += (f'<tr><td><a href="{ASANA_BASE}/{BUILD_ORDERS}/{r["ctrl_gid"]}">{esc(r["ctrl_name"])}</a></td>'
                     f'<td>{esj(r["job"])}</td><td>{bom_badge}</td>'
                     f'<td>{esc(r["due_on"] or "—")}</td><td>{jajo_cell}</td></tr>')
        plc_html = (f'<details class="sec" open><summary><span class="cnt">PLC BOMs (spec handover) · {len(data["plc"])} open</span></summary>'
                    f'<p class="muted">Issue PLC BOM is a spec-sheet handover that later feeds the Karol sub-BOM item task '
                    f'"Machine Room Cabinet" (vendor usually JAJO). BOM STATUS = READY TO ORDER → the JAJO order is on order. '
                    f'JAJO column shows the imported JAJO vendor task (under ALL BOMS * Long Lead), else none.</p>'
                    f'<table><tr><th>Task</th><th>Job</th><th>BOM STATUS</th><th>Due</th><th>JAJO order</th></tr>{rows}</table></details>')
    hand_html = ""
    ho = [h for h in data["handover"] if not h.get("completed")]
    if ho:
        items = "".join(
            f'<tr><td><a href="{ASANA_BASE}/850066254549317/{h["gid"]}">{esc(h["name"])}</a></td>'
            f'<td>{esj(job_of(h["name"]))}</td><td>{"✓" if _extract_dropbox(h.get("notes") or "") else "—"}</td></tr>' for h in ho)
        hand_html = f'<details class="sec"><summary><span class="cnt">Handover Legacy BOMs · {len(ho)} open</span></summary><table><tr><th>Task</th><th>Job</th><th>Dropbox</th></tr>{items}</table></details>'
    # Karol needs-action (open sub-BOMs under unticked Karol ctls)
    karol_html = ""
    if data["karol_action"]:
        cards = ""
        for k in data["karol_action"]:
            subs = ""
            for s in k["subtasks"]:
                # Button payload matches how Giles manually triggers a BOM push:
                #   project_name = job + name (ONE entity), control = the control
                #   task (gid + name), dropbox_url, parts_folder (resolved by the
                #   bridge on the host — the browser has no Dropbox path).
                proj_name = f"{k['job']} - {s['name']}" if k["job"] else s["name"]
                pl = {
                    "action": "run_bom_pipeline",
                    "project_name": proj_name,
                    "job": k["job"],
                    "control_name": k["ctl"]["name"],
                    "control_gid": k["ctl"]["gid"],
                    "sub_bom": s["name"],
                    "sub_bom_gid": s["gid"],
                    "dropbox_url": s["dropbox"],
                    "parts_folder": None,   # bridge resolves dropbox_url -> local path
                }
                btn = f'<button class="acc" onclick="stageAction(\'{_jsesc(json.dumps(pl))}\', \'BOM pipeline push\')">▶ Stage to pipeline</button>' if s.get("dropbox") else '<span class="muted">no Dropbox link</span>'
                subs += (f'<li><b style="color:var(--red)">OPEN</b> '
                         f'<a href="{ASANA_BASE}/850066254549317/{s["gid"]}">{esc(s["name"])}</a> '
                         f'<span class="muted">({esc(s.get("due_on") or "no due")})</span> · {btn}</li>')
            # steel-profile box per Karol sub-BOM: has a Steel Profile BOM been
            # created and imported for this BOM? (imported = steel task has >=1
            # subtask = its vendor-order tasks). No dropdown — just a status box.
            st = k.get("steel")
            if st:
                steel_txt = (f'✅ Steel Profile BOM imported <a href="{ASANA_BASE}/{BUILD_ORDERS}/{st["task"]["gid"]}" style="color:var(--green)">({st["task"]["name"]})</a> '
                             f'· {st["n_subtasks"]} vendor order task(s), {st["open_subs"]} open')
                steel_class = "ok"
            else:
                steel_txt = f'❌ No Steel Profile BOM imported for job {esj(k["job"])} — source material from Carol not yet processed'
                steel_class = "open"
            steel_box = f'<div class="steelbox {steel_class}">{steel_txt}</div>'
            cards += f'<div class="card"><h2><span class="num">{esj(k["job"])}</span> {esc(k["ctl"]["name"])}</h2><ul>{subs}</ul>{steel_box}</div>'
    # debug removed
    karol_html = f'<details class="sec" open><summary><span class="cnt">Karol sub-BOMs needing action · {len(data["karol_action"])}</span></summary><p class="muted">Any task under an unticked Karol Generate Entire BOM control (assignee ignored — Karol often forgets to assign). Each open sub carries its own Dropbox BOM link. Steel box = whether the Steel Profile BOM for this job is imported (has vendor-order subtasks).</p><div class="stack">{cards}</div></details>'

    # vendor-open expandable (due date + status field per vendor group)
    vendor_html = ""
    if data["vendor_open"]:
        def vstat(vg):
            st = vg.get("order_status") or vg.get("bom_status") or ""
            if not st: return '<span class="muted">—</span>'
            low = st.lower()
            cls = "over" if ("hold" in low or "rejected" in low or "cancelled" in low or "return" in low) else ("open" if low.startswith(("ready","schedule","on order")) else "warn")
            return f'<span class="pill {cls}">{esc(st)}</span>'
        v = "".join(
            f'<details class="vendor" style="margin-top:6px"><summary>{esc(vg["task"]["name"])} '
            f'<span class="pill open">{len(vg["open_subs"])} open</span>'
            f' · due {esc(vg.get("due_on") or "—")} · {vstat(vg)}</summary><table><tr><th>Part</th><th>Due</th></tr>'
            + "".join(
                f'<tr><td><a href="{ASANA_BASE}/{BUILD_ORDERS}/{s["gid"]}">{esc(s.get("name","")[:46])}</a></td>'
                f'<td>{due_cell(s.get("due_on"), today)}</td></tr>'
                for s in vg["open_subs"][:8])
            + (f'<tr><td colspan="2" class="muted">+{len(vg["open_subs"])-8} more</td></tr>' if len(vg["open_subs"])>8 else "") + "</table></details>"
            for vg in data["vendor_open"][:20])
        vendor_html = f'<details class="sec"><summary><span class="cnt">Vendor groups not closed · {len(data["vendor_open"])} with un-ordered subtasks</span></summary><p class="muted">Status = ORDER Status (or BOM STATUS) custom field on the vendor-group task — may be on hold / pending / scheduled.</p><div style="margin-top:6px">{v}</div></details>'

    # no-pack (drawing trigger) expandable
    np_html = ""
    if data["no_pack_jobs"]:
        rows = "".join(
            f'<tr><td><b>{esj(j["job"])}</b></td><td>{j["no_pack"]}</td>'
            f'<td>{esc(j["anchor"] or "no Handover — fallback drawing")}</td>'
            f'<td><button class="acc" onclick="stageAction(\'{_jsesc(json.dumps({"action":"run_drawing_pipeline","job":j["job"],"no_pack_parts":j["no_pack"],"anchor":j["anchor"]}))}\', \'Drawing pipeline\')">▶ Stage drawing</button></td></tr>'
            for j in data["no_pack_jobs"][:20])
        np_html = f'<details class="sec"><summary><span class="cnt">Parts without assembly pack · {c["no_pack_parts"]} distinct parts · {len(data["no_pack_jobs"])} jobs</span></summary><p class="muted">Stage the drawing pipeline — anchor = Handover Legacy BOM if present, else the job drawing task → PDF.</p><table><tr><th>Job</th><th>No-pack</th><th>Anchor</th><th>Action</th></tr>{rows}</table></details>'

    # ── Issued BOMs section — REVERSE-DERIVED from open vendor groups ──────────
    # (Giles 2026-08-13). The old control-driven view hid jobs whose Karol control
    # + source BOM were CLOSED when orders went in-process. Now we start from the
    # OPEN VENDOR GROUPS and resolve back up to job + source BOM + ALL BOMS root.
    # Each job row links to its Parts-Control search of outstanding parts.
    issued_rows = data.get("issued_rows_vg") or []
    cards = ""
    for v in issued_rows:
        proj = esc(v["project"] or "—")
        # all ALL BOMS roots for this job (a job can span multiple BOMs, e.g. 1591
        # has 'Traversing replacement Feet' + 'Uni of Cambridge Entire BOM')
        root_links = [f'<a href="{ASANA_BASE}/{BUILD_ORDERS}/{r["gid"]}">{esc(r["name"])}</a>'
                      for r in v["roots"]]
        root_cell = "<br>".join(root_links) if root_links else "-"
        # open vendor groups table (name · due · status)
        group_rows = ""
        for g in v["groups"]:
            due = g.get("due_on")
            due_txt = (f'<span class="pill over">due {esc(due)} · OVERDUE</span>'
                       if due and due < today else due_cell(due, today))
            st = g.get("order_status") or g.get("bom_status") or ""
            st_txt = (f'<span class="pill open">{esc(st)}</span>' if st else '<span class="muted">—</span>')
            group_rows += (f'<tr><td><a href="{ASANA_BASE}/{BUILD_ORDERS}/{g["task"]["gid"]}">{esc(g["task"]["name"])}</a>'
                           f' ({len(g["open_subs"])} open)</td><td>{due_txt}</td><td>{st_txt}</td></tr>')
        overdue_badge = (f'<span class="pill over">{v["overdue"]} overdue</span>' if v["overdue"]
                         else '<span class="pill ok">0 overdue</span>')
        cards += f"""<div class="card"><h2><span class="num">{esj(v['job'])}</span> {proj} <span class="pill open">{v['total_groups']} open vendor group(s)</span> {overdue_badge}</h2>
<table>
<tr><th style="width:150px">ALL BOMS root</th><td>{root_cell}</td></tr>
<tr><th>Parts Control</th><td><a href="{esc(v['pc_url'])}" target="_blank">🔍 Outstanding parts for job {esj(v['job'])} →</a></td></tr>
<tr><th>Open vendor groups</th><td><table class="nested"><tr><th>Group</th><th>Due</th><th>Status</th></tr>{group_rows}</table></td></tr>
</table></div>"""

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>SAS OS — Karol BOM-processing Tracker</title><style>{CSS}{SEC_CSS}</style>{BTN_JS}</head>
<body><div class="wrap">
<header class="hero"><h1>🗂 Karol BOM-processing Tracker</h1>
<div class="sub">Incomplete inbound BOMs (source: 1.4 Purchasing Dept 850066254549317) · pipeline stage · expandable gaps · Generated {today} · Live Asana + ops.db</div>
<a href="nav.html" class="navbtn">&larr; SAS OS portal</a>
<div class="note" style="margin-top:10px">💡 To use the <b>action buttons</b>, open this page as
<a href="http://127.0.0.1:8765/bom-tracker.html" target="_blank">http://127.0.0.1:8765/bom-tracker.html</a>
(not via file://). The buttons POST to the local dispatch server, which resolves the
parts-folder path and sends the stage request to the <b>Purchasing</b> Telegram topic.</div>
<div class="badges">{badges}</div>
<div style="margin-top:12px;display:flex;gap:10px;flex-wrap:wrap">
<button onclick="showStagedBom()" style="background:var(--brand);color:#fff;border:none;border-radius:8px;padding:6px 14px;font-size:13px;cursor:pointer">Show staged BOM actions</button>
<button onclick="clearStagedBom()" style="background:#555;color:#fff;border:none;border-radius:8px;padding:6px 14px;font-size:13px;cursor:pointer">Clear staged</button></div></header>
<div class="note">Only INCOMPLETE BOMs shown. Stage = live Asana signal. Action buttons STAGE for REQUESTS→SAP (never applied from the page).</div>
{new_alert}
{karol_html}
{hand_html}
{vendor_html}
{np_html}
{plc_html}
<div class="sec-card"><h2>Issued BOMs · active jobs with outstanding vendor work ({len(issued_rows)})</h2>
<p class="muted">Reverse-derived from OPEN VENDOR GROUPS → resolves up to the source BOM, so a job with outstanding deliveries stays visible even after its Karol control + source BOM were closed for in-process orders. Each card links to its Parts-Control outstanding parts + lists the open vendor groups (due/status, overdue red).</p>
{cards}</div>
<footer>SAS OS · Karol BOM tracker v3 · read-only · every row → its Asana source</footer></div></body></html>"""

def _ensure_dispatch_server(port=8765):
    """Best-effort: keep the BOM dispatch server up so tracker buttons work.
    Runs on every portal refresh; never breaks the tracker build."""
    import sys, subprocess
    try:
        requests.get(f"http://127.0.0.1:{port}/health", timeout=1.5)
        return  # already up
    except Exception:
        pass
    try:
        server = Path(__file__).with_name("bom_dispatch_server.py")
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen([sys.executable, str(server)], creationflags=flags,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
        print(f"  [dispatch] started bom_dispatch_server on :{port}")
    except Exception as e:
        print(f"  [dispatch] could not auto-start server: {e}")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", default=str(OUT_DEFAULT))
    args = ap.parse_args()
    _ensure_dispatch_server()
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    today = datetime.date.today().isoformat()
    purch = load_project_tasks(PURCH_DEPT)
    build_tasks = load_project_tasks(BUILD_ORDERS)
    open_orders = load_open_orders()
    data = build_dataset(purch, build_tasks, open_orders)
    html = render(data, NEW_RELEASE, today)
    target = out / "bom-tracker.html"; target.write_text(html, encoding="utf-8")
    dated = out / f"bom-tracker-{today}.html"; dated.write_text(html, encoding="utf-8")
    c = data["counts"]
    print(f"Wrote {target} ({len(html)//1024} KB)")
    print(f"  issued(pending)={c['issued']} PLC={c['plc']} HandoverOpen={c['handover_open']} "
          f"KarolAction={c['karol_action']} VendorOpen={c['vendor_open']} "
          f"NoPackParts={c['no_pack_parts']} Steel={c['steel']}")
    print("  Issued list:")
    for r in data["rows"]:
        print(f"    [{r['family']:7}] {r['job'] or '?':4} asg={r['assignee'] or 'NONE':8} {r['stage_idx']} {r['ctrl_name'][:46]}")

if __name__ == "__main__":
    main()
