#!/usr/bin/env python3
"""build_steel_profile_by_pack.py — SAS OS dashboard: "Steel Profile by Pack" pane.

READ-ONLY. Answers, per build pack: what steel profile material does this pack
need, and what is the total material requirement across all packs that are
still live?

WHAT THIS PAGE IS FOR
  Purchasing needs to order steel profile. Today that means reading each pack's
  steel BOM by hand. This pane rolls every pack's steel requirement up into a
  single orderable summary (the printable table at the top), while keeping the
  per-pack detail available underneath for audit.

DATA SOURCES (both local, both read-only)
  * steel_profile.db / steel_parts    -> the material lines (profile + length)
    Seeded from the "Steel Profile" BOM xlsx files. This is the ONLY place the
    per-pack part context exists; the live Asana tasks do not carry it.
  * ops.db / build_assembly_packs     -> the pack's STEEL PROFILE status,
    completion state, due date and project name.

THE JOIN (load-bearing — easy to get wrong)
  steel_parts.pack_ref is the SHORT form   e.g. '1588.1004.B'
  ops.db pack_reference is the LONG form   e.g. '1588.1004.B.MainStairFrame.'
  The join key is the FIRST THREE dot-segments of each, upper-cased. A pack
  joins when those three segments match. Rows whose ref contains 'DRAWING'
  are drawing-control tasks, not build packs, and are excluded.

EXCLUSION RULE (Giles, 2026-09-12)
  A pack is EXCLUDED from the material summary when its STEEL PROFILE custom
  field is exactly 'All Cut' (1142754335683356) or 'Not needed'
  (1142868352760023). Matching is padding-tolerant (the Asana option names
  carry trailing spaces).

  A pack whose STEEL PROFILE is BLANK is a THIRD state, treated as 'nil':
  it is NOT counted into the material totals (that would inflate the
  requirement from a sync gap rather than a real need), but it IS shown, with
  nil values, so the gap stays visible and nobody assumes the pack is clear.
  Measured 2026-09-12: 69 of the matched packs are blank-status and carry
  693 m of material -- i.e. counting them would have overstated the ferrous
  requirement by more than 10x. Blank != 'All Cut'; it means unknown.

  So three states per pack:
    * excluded  -> 'All Cut' / 'Not needed'   (no material required)
    * nil       -> blank STEEL PROFILE         (unknown -- not counted)
    * counted   -> anything else               (feeds the totals)


COMPLETENESS / COVERAGE CAVEAT (Giles, 2026-09-12)
  Steel profile data is only PARTIALLY ingested: 133 pack refs in
  steel_parts, of which ~100 join to a live pack. Many steel BOMs are not yet
  through Stage 6 (Steel Profile Extract). This page therefore reports its own
  coverage honestly (see the Coverage panel) rather than implying completeness.
  A retrospective backfill is tracked as a dev task.

AMBIGUOUS MATERIAL INPUT (Giles, 2026-09-12)
  The whole point of capturing ambiguity is that we must NOT drop an input
  because it is formatted wrongly. Two signals are surfaced:
    * steel_parts.integrity != 'GOOD'   -> the part could not be parsed cleanly
    * steel_profile.db / material_unmapped -> a BOM material string that did
      not resolve to a known material record
  Both feed the "Ambiguous / unmapped input" panel so nothing is silently lost.

PRINTING
  The summary table is the print target. 'Print summary' triggers a stylesheet
  that hides everything except the summary + coverage, in landscape, so the
  sheet can go to the steel supplier.

ASANA PUSH (staged, not yet wired)
  The push buttons draft a task into queues/REQUESTS for SAP to drain —
  nothing writes to Asana from here. SAP is the only Asana writer (hard rule).
  Titles are 'Ferrous Steel BOM' and 'Non-Ferrous Steel BOM'. A quote request
  is the long-term intent; for now the task is the deliverable.

Governance: reads steel_profile.db + ops.db read-only. Writes NOTHING to
either DB or to Asana. Any push draft is a queues/REQUESTS file.

Usage:
  .venv/Scripts/python.exe build_steel_profile_by_pack.py
  .venv/Scripts/python.exe build_steel_profile_by_pack.py --push ferrous
  .venv/Scripts/python.exe build_steel_profile_by_pack.py --push nonferrous

Output: data/os_status/steel-profile-by-pack.html (+ dated copy).
"""
import os, re, sys, json, html, sqlite3, argparse
from datetime import datetime, timezone
from collections import defaultdict

WS = os.path.expanduser("~/SES_Workspace")
DB_STEEL = os.path.join(WS, "db", "steel_profile.db")
DB_OPS = os.path.join(WS, "db", "ops.db")
OUTDIR = os.path.join(WS, "data", "os_status")

# --- Asana custom-field / option GIDs -----------------------------------------
FIELD_STEEL = "1142754335683355"
OPT_ALL_CUT = "1142754335683356"
OPT_NOT_NEEDED = "1142868352760023"

# Exclusion is by option NAME, padding-tolerant (option names carry stray spaces).
EXCLUDE_NAMES = {"all cut", "not needed"}

# Material families -> the two order buckets.
FERROUS = {"mild steel", "steel", "steel bzp", "bright steel", "bright steel 80a15",
           "carbon steel", "en8", "en1a", "silver steel"}


def esc(v):
    return html.escape(str(v if v is not None else ""), quote=True)


def norm(s):
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


def pack_key(ref):
    """First three dot-segments of a pack reference, upper-cased.

    '1588.1004.B.MainStairFrame.' -> '1588.1004.B'
    '1588.1004.B'                 -> '1588.1004.B'
    """
    parts = (ref or "").strip().rstrip(".").split(".")
    parts = [p for p in parts if p != ""]
    return ".".join(parts[:3]).upper() if len(parts) >= 3 else None


def project_of(ref):
    """Project = first 4 digits of the pack reference."""
    m = re.match(r"^(\d{4})", (ref or "").strip())
    return m.group(1) if m else "----"


def bucket_of(material_type):
    return "ferrous" if norm(material_type) in FERROUS else "nonferrous"


# --- load ---------------------------------------------------------------------
def load(live_map=None):
    if not os.path.exists(DB_STEEL):
        sys.exit(f"steel_profile.db not found at {DB_STEEL}")
    s = sqlite3.connect(f"file:{DB_STEEL}?mode=ro", uri=True)
    s.row_factory = sqlite3.Row
    o = sqlite3.connect(f"file:{DB_OPS}?mode=ro", uri=True)
    o.row_factory = sqlite3.Row

    # ops packs, keyed by short ref (DRAWING rows are not build packs).
    # NOTE: ops.db steel_profile is KNOWN-STALE vs live Asana, so it is used
    # only to supply pack identity (gid, due date, project) and as a fallback
    # status. The authoritative status comes from live_map (steel_status_live).
    ops = defaultdict(list)
    for r in o.execute(
        "select pack_reference, pack_name, steel_profile, is_completed, due_on,"
        " project_name, asana_task_gid, source_project_gid, status"
        " from build_assembly_packs"
    ):
        ref = r["pack_reference"] or ""
        if "DRAWING" in ref.upper():
            continue
        k = pack_key(ref)
        if k:
            ops[k].append(dict(r))

    # steel material lines, grouped by pack short-ref then material name
    packs = defaultdict(lambda: defaultdict(lambda: {
        "material_type": None, "profile": None, "mm": 0.0, "off": 0.0,
        "lines": 0, "short": 0, "cut_mm": 0.0, "parts": [],
    }))
    integrity_bad = []
    for r in s.execute(
        "select part_uid, part_no, project_no, pack_ref, description, material_raw,"
        " material_type, profile_type, full_material_name, depth_mm, breadth_mm,"
        " thickness_mm, cut_length_mm, no_off, total_mm, integrity from steel_parts"
    ):
        k = pack_key(r["pack_ref"])
        if not k:
            continue
        name = (r["full_material_name"] or "").strip() or "(unnamed material)"
        cell = packs[k][name]
        cell["material_type"] = r["material_type"]
        cell["profile"] = r["profile_type"]
        cell["mm"] += float(r["total_mm"] or 0)
        cell["off"] += float(r["no_off"] or 0)
        cell["lines"] += 1
        # cut length per piece: keep the longest single cut in this spec, which
        # is what the cut list reads as ("N off at Xmm").
        cl = float(r["cut_length_mm"] or 0)
        if cl > cell["cut_mm"]:
            cell["cut_mm"] = cl
        cell["parts"].append({
            "part_no": r["part_no"], "description": r["description"],
            "cut_length_mm": r["cut_length_mm"], "no_off": r["no_off"],
            "total_mm": r["total_mm"],
        })
        if (r["integrity"] or "").upper() != "GOOD":
            integrity_bad.append({
                "part_no": r["part_no"], "pack_ref": r["pack_ref"],
                "description": r["description"], "material_raw": r["material_raw"],
                "profile_type": r["profile_type"], "integrity": r["integrity"],
                "total_mm": r["total_mm"],
            })

    unmapped = []
    try:
        for r in s.execute(
            "select bom_material, material_type, occurrences, classification,"
            " status, notes from material_unmapped order by occurrences desc, bom_material"
        ):
            unmapped.append(dict(r))
    except sqlite3.OperationalError:
        pass

    s.close(); o.close()
    return ops, packs, integrity_bad, unmapped


# --- assemble model -----------------------------------------------------------
def build_model(ops, packs, live_map=None):
    """Return (project_groups, summary_rows, stats).

    Only INCOMPLETE packs are ever seeded — completed packs are done.

    Each pack lands in one of three states (see EXCLUSION RULE in module doc):
      excluded / nil / counted

    STATUS SOURCE: the steel status is taken from LIVE Asana (live_map) when
    available, falling back to the (known-stale) ops.db mirror only when the
    pack is absent from live. stats['status_source'] records which was used so
    the page can be honest about it.
    """
    live_map = live_map or {}
    stats = {
        "steel_pack_refs": len(packs),
        "matched": 0, "unmatched": 0,
        "included_packs": 0, "excluded_packs": 0, "nil_packs": 0,
        "nil_mm": 0.0, "completed_packs": 0,
        "from_live": 0, "from_mirror": 0,
    }
    by_project = defaultdict(list)

    for k, materials in packs.items():
        rows = ops.get(k)
        if not rows:
            stats["unmatched"] += 1
            continue
        stats["matched"] += 1

        # A pack may appear more than once (drawing vs build rows); keep the
        # non-DRAWING build rows, and prefer the one carrying a steel status.
        live = [p for p in rows if not p["is_completed"]]
        if not live:
            continue
        ref_long = sorted((p["pack_reference"] or "" for p in live), key=len)[-1]
        due = next((p["due_on"] for p in live if p["due_on"]), None)
        gid = next((p["asana_task_gid"] for p in live if p["asana_task_gid"]), None)
        pgid = next((p["source_project_gid"] for p in live if p["source_project_gid"]), None)

        # LIVE status wins. The mirror is only a fallback for packs live does
        # not know about. A live pack marked completed is done -> excluded.
        lv = live_map.get(k)
        if lv is not None:
            steel_status = (lv.get("status") or "")
            stats["from_live"] += 1
            if lv.get("completed"):
                # completed pack: done, so it is NOT shown anywhere (Giles,
                # 2026-09-12). Counted in stats only so the numbers reconcile.
                stats["excluded_packs"] += 1
                stats["completed_packs"] += 1
                continue
        else:
            statuses = [(p["steel_profile"] or "") for p in live]
            steel_status = next((x for x in statuses if x.strip()), "")
            stats["from_mirror"] += 1

        material_mm = sum(v["mm"] for v in materials.values())
        if norm(steel_status) in EXCLUDE_NAMES:
            state = "excluded"
            stats["excluded_packs"] += 1
        elif not steel_status.strip():
            # Blank status => unknown, NOT counted. Showing it with nil values
            # keeps the gap visible without inflating the requirement.
            state = "nil"
            stats["nil_packs"] += 1
            stats["nil_mm"] += material_mm
        else:
            state = "counted"
            stats["included_packs"] += 1

        by_project[project_of(ref_long)].append({
            "pack_key": k, "ref": ref_long, "due": due, "steel_status": steel_status,
            "state": state, "material_mm": material_mm,
            "materials": materials, "gid": gid, "project_gid": pgid,
        })

    # order packs within a project by due date (soonest first, undated last)
    for proj in by_project:
        by_project[proj].sort(key=lambda p: (p["due"] is None, p["due"] or "", p["ref"]))
    groups = sorted(by_project.items(),
                    key=lambda kv: (min((p["due"] for p in kv[1] if p["due"]), default="9999"), kv[0]))

    # summary = aggregate by material from COUNTED packs only, carrying the
    # constituent parts so each material row can expand to what makes it up.
    summary = defaultdict(lambda: {"mm": 0.0, "off": 0.0, "packs": set(),
                                   "material_type": None, "profile": None,
                                   "parts": []})
    for proj, plist in groups:
        for p in plist:
            if p["state"] != "counted":
                continue
            for name, cell in p["materials"].items():
                s = summary[name]
                s["mm"] += cell["mm"]
                s["off"] += cell["off"]
                s["packs"].add(p["pack_key"])
                s["material_type"] = cell["material_type"]
                s["profile"] = cell["profile"]
                for pt in cell["parts"]:
                    s["parts"].append({
                        "part_no": pt["part_no"], "description": pt["description"],
                        "cut_length_mm": pt["cut_length_mm"], "no_off": pt["no_off"],
                        "total_mm": pt["total_mm"], "pack_key": p["pack_key"],
                        "pack_ref": p["ref"], "gid": p["gid"],
                        "project_gid": p["project_gid"],
                    })
    summary_rows = [
        {"material": n, "material_type": v["material_type"], "profile": v["profile"],
         "mm": v["mm"], "off": v["off"], "packs": len(v["packs"]),
         "bucket": bucket_of(v["material_type"]), "parts": v["parts"]}
        for n, v in summary.items()
    ]
    summary_rows.sort(key=lambda r: (r["bucket"] != "ferrous", -r["mm"], r["material"]))
    return groups, summary_rows, stats


# --- render -------------------------------------------------------------------
CSS = """
:root{--bg:#0f1115;--card:#171a21;--line:#272c37;--fg:#e6e9ef;--dim:#98a1b3;
--accent:#4da3ff;--warn:#ffb020;--ok:#37c98a;--bad:#ff6b6b;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1400px;margin:0 auto;padding:20px}
h1{font-size:20px;margin:0 0 4px}
h2{font-size:16px;margin:26px 0 10px;border-bottom:1px solid var(--line);padding-bottom:6px}
.sub{color:var(--dim);font-size:12px;margin-bottom:16px}
.bar{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0 18px}
button,.btn{background:var(--card);color:var(--fg);border:1px solid var(--line);
padding:7px 13px;border-radius:7px;cursor:pointer;font-size:13px;text-decoration:none}
button:hover,.btn:hover{border-color:var(--accent);color:var(--accent)}
button.primary{border-color:var(--accent);color:var(--accent)}
button.ghost{border-color:var(--line);color:var(--dim)}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{padding:6px 9px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{background:#12151b;color:var(--dim);font-weight:600;font-size:11.5px;
text-transform:uppercase;letter-spacing:.03em;position:sticky;top:0}
tr:hover td{background:#1b1f27}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.card{background:var(--card);border:1px solid var(--line);border-radius:9px;
padding:12px 14px;margin:7px 0}
details.card{padding:0;overflow:hidden}
details.card>summary{cursor:pointer;padding:11px 14px;list-style:none;
display:flex;gap:12px;align-items:center;flex-wrap:wrap}
details.card>summary::-webkit-details-marker{display:none}
details.card>summary::before{content:"\\25B8";color:var(--dim);font-size:11px;
transition:transform .15s;flex:0 0 auto}
details.card[open]>summary::before{transform:rotate(90deg)}
details.card>summary:hover{background:#1b1f27}
.body{padding:0 14px 14px;border-top:1px solid var(--line)}
.ref{font-weight:600;flex:1 1 320px;min-width:220px}
.chip{font-size:11px;padding:2px 8px;border-radius:99px;border:1px solid var(--line);
color:var(--dim);white-space:nowrap}
.chip.ok{color:var(--ok);border-color:#1e5c44}
.chip.warn{color:var(--warn);border-color:#5c4a17}
.chip.bad{color:var(--bad);border-color:#5c2626}
.chip.acc{color:var(--accent);border-color:#1e3f5c}
.proj{font-size:12px;color:var(--dim);font-family:ui-monospace,Menlo,Consolas,monospace;
text-transform:none;font-weight:600}
.empty{color:var(--dim);font-style:italic;padding:8px 0}
.legend{color:var(--dim);font-size:12px;margin:8px 0 0}
.tot td{font-weight:700;background:#12151b;border-top:2px solid var(--line)}
.matrow td{border-bottom:none}
.detrow td{padding:0 0 4px 0;border-bottom:1px solid var(--line)}
details.sub>summary{cursor:pointer;list-style:none;color:var(--dim);font-size:11.5px;
padding:3px 9px;display:inline-block}
details.sub>summary::-webkit-details-marker{display:none}
details.sub>summary::before{content:"\\25B8 ";font-size:10px}
details.sub[open]>summary::before{content:"\\25BE "}
details.sub>summary:hover{color:var(--accent)}
.subbody{padding:4px 6px 10px 20px}
.subbody table{font-size:12px}
.subbody th{background:#0f1115;font-size:10.5px}
.nil td{opacity:.55}
a{color:var(--accent)}
.dim{color:var(--dim)}
.small{font-size:11px}

/* --- tight material summary ------------------------------------------- */
table.sum{font-size:12.5px}
table.sum th{padding:5px 8px}
table.sum td{padding:3px 8px;border-bottom:1px solid #1d212a}
tr.buckethero td{background:#12151b;color:var(--accent);font-weight:700;font-size:11px;
letter-spacing:.05em;text-transform:uppercase;padding:5px 8px;border-bottom:1px solid var(--line)}
tr.typehero td{background:#151922;color:var(--fg);font-weight:600;font-size:11.5px;
padding:4px 8px 4px 14px;border-bottom:1px solid #1d212a}
tr.sumrow{cursor:pointer}
tr.sumrow:hover td{background:#1b1f27}
/* Detail panels collapse by default. Driven by a class on the panel itself,
   NOT by an adjacent-sibling selector -- the panel holds a nested table, whose
   own <tr>s would break "+" adjacency under HTML parsing. */
tr.detrow{display:none}
tr.detrow.open{display:table-row}
tr.detrow .subbody{padding:6px 10px 10px 26px}
.caret{color:var(--dim);font-size:10px;display:inline-block;width:10px}
.caret.big{font-size:13px;width:14px;color:var(--accent);transition:transform .15s}
tr.sumrow .caret.big{transform:rotate(0deg)}
tr.sumrow.open .caret.big{transform:rotate(90deg)}
.lenbox{width:66px;background:#0f1115;color:var(--fg);border:1px solid var(--line);
border-radius:5px;padding:2px 6px;font-size:12px;text-align:right}
.lenbox::placeholder{color:#5c6472}
.confirm{width:15px;height:15px;accent-color:var(--accent);cursor:pointer}
tr.tot.grand td{border-top:2px solid var(--accent)}
.mtype{color:var(--accent);font-size:11px;font-weight:700;text-transform:uppercase;
letter-spacing:.04em;margin:10px 0 4px}

/* --- Pack Cutting: aligned cut-line columns ---------------------------- */
table.cut{width:100%;border-collapse:collapse;font-size:12.5px;margin:0 0 2px}
table.cut td{padding:2px 6px;border-bottom:1px solid #1d212a;vertical-align:middle}
table.cut tr.cutline:hover td{background:#1b1f27}
td.c-tick{width:22px;padding-right:0}
td.c-tick input{width:15px;height:15px;accent-color:var(--ok);cursor:pointer}
td.c-off{width:78px;text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
td.c-at{width:22px;color:var(--dim);font-size:11px;padding:0 2px}
td.c-len{width:92px;text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
td.c-mat{min-width:230px}
td.c-pno{width:32%;border-left:1px solid #1d212a;padding-left:10px}
td.c-pno .pno{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:11.5px;
color:var(--dim);word-break:break-word}
td.c-qty{width:74px;text-align:right;color:var(--dim);font-variant-numeric:tabular-nums;
white-space:nowrap}
td.c-ord{width:104px;text-align:right}
tr.cutline.done td{opacity:.45}
tr.cutline.done td.c-mat,tr.cutline.done td.c-pno{text-decoration:line-through}
.chip.ordered{border-color:#5c4a17;color:var(--warn)}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media(max-width:900px){.grid2{grid-template-columns:1fr}}
.panel{background:var(--card);border:1px solid var(--line);border-radius:9px;padding:12px 14px}
.panel h3{margin:0 0 8px;font-size:13px;color:var(--warn)}
.panel.info h3{color:var(--accent)}
.panel.ok h3{color:var(--ok)}
.panel.nil h3{color:var(--dim)}
.mono{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px}
.profile{width:100%;border-collapse:collapse}
@media print{
  @page{size:A4 landscape;margin:10mm}
  body{background:#fff;color:#000}
  .bar,noprint,.projsec{display:none!important}
  .card,#coverage,.panel{display:none!important}
  #summary{display:block!important}
  /* detail stays collapsed in print — the printed sheet is the audit list */
  tr.detrow{display:none!important}
  .lenbox,.confirm{display:none!important}
  tr.buckethero td{background:#ddd!important;color:#000!important}
  tr.typehero td{background:#f2f2f2!important;color:#000!important}
  th{background:#eee!important;color:#000!important;position:static}
  th,td{border-bottom:1px solid #999}
  .printtitle{display:block!important}
  a{color:#000;text-decoration:none}
}
.printtitle{display:none;font-size:17px;font-weight:700;margin:0 0 10px}
"""

JS = """
function setAll(open){
  document.querySelectorAll('details.card').forEach(function(d){d.open=open;});
  document.getElementById('tg').textContent = open ? 'Collapse all' : 'Expand all';
  document.getElementById('tg').dataset.open = open ? '0':'1';
}
function toggleAll(){
  var t=document.getElementById('tg');
  setAll(t.dataset.open==='1');
}
document.addEventListener('DOMContentLoaded',function(){
  setAll(false);
});

/* Toggle a summary row's part-line detail panel.
   Sets display inline rather than relying on any CSS rule, so nothing about
   selector specificity, adjacency, or a nested <table> can break it. */
function tog(row){
  var id=row.getAttribute('data-target');
  var panel=id ? document.getElementById(id) : row.nextElementSibling;
  if(!panel) return;
  var showing = panel.style.display === 'table-row';
  panel.style.display = showing ? 'none' : 'table-row';
  row.classList.toggle('open', !showing);
}

/* Tick a material line as cut (placeholder — nothing is written). */
function markCut(cb){
  var row=cb.closest('tr');
  if(row) row.classList.toggle('done', cb.checked);
}

/* Submit the confirmed order lines (placeholder — nothing is written).
   Later: gather ticked rows + entered lengths, draft an Asana task listing the
   materials needed, for use as a request for quote. */
function submitOrder(){
  var picked=[];
  document.querySelectorAll('tr.sumrow').forEach(function(r){
    var cb=r.querySelector('.confirm');
    if(cb && cb.checked){
      picked.push({
        material: r.querySelector('td').innerText.trim(),
        lengths: (r.querySelector('.lenbox')||{}).value || ''
      });
    }
  });
  if(!picked.length){ alert('Nothing confirmed yet — tick the Confirm box on the materials you need.'); return; }
  var txt=picked.map(function(p){return '• '+p.material+(p.lengths?' — '+p.lengths+' length(s)':'');}).join('\\n');
  alert('Would create an order request for:\\n\\n'+txt+
        '\\n\\n(Placeholder — nothing written. Wiring to Asana comes later.)');
}

/* Weld check. Runs the local steel_weld_flip.py in DRY-RUN and shows what
   WOULD change. It never writes to Asana from the browser -- committing is a
   deliberate, separate act (SAP is the only Asana writer, hard rule). */
function weldCheck(){
  var out=document.getElementById('weldout');
  out.style.display='block';
  out.innerHTML='<h3>Checking welds…</h3><div class="legend">Running DRY-RUN. No writes.</div>';
  fetch('steel-weld-check.json?_='+Date.now())
    .then(function(r){ if(!r.ok) throw new Error('HTTP '+r.status); return r.json(); })
    .then(function(j){
      var s=j.summary||{}, sk=s.skipped||{};
      var rows='';
      (j.targets||[]).forEach(function(t){
        rows+='<tr><td class="mono">'+t.gid+'</td><td>'+t.from+
              '</td><td>'+t.name+'</td></tr>';
      });
      var tbl = rows ? '<table><thead><tr><th>Task</th><th>From</th><th>Pack</th>'
        +'</tr></thead><tbody>'+rows+'</tbody></table>'
        : '<div class="empty">No packs are ready to flip right now.</div>';
      out.innerHTML='<h3>Weld check — DRY-RUN ('+(j.targets||[]).length
        +' ready to flip)</h3>'
        +'<div class="legend">Outstanding packs checked: '+(s.checked||0)
        +' · Weld still open: '+(sk.weld_open||0)
        +' · no Weld subtask: '+(sk.no_weld||0)+'</div>'+tbl
        +'<div class="legend">Nothing written. To apply: '
        +'<span class="mono">.venv/Scripts/python.exe skills/os_portal/steel_weld_flip.py --commit</span>'
        +' (requires approval).</div>';
    })
    .catch(function(e){
      out.innerHTML='<h3>Weld check unavailable</h3><div class="legend">'+e.message
        +' — run <span class="mono">steel_weld_flip.py --json > steel-weld-check.json</span> '
        +'to refresh.</div>';
    });
}
function pushDraft(kind){
  alert('Push "'+kind+'" drafts a task into queues/REQUESTS for SAP to drain.\\n'
        +'Run: build_steel_profile_by_pack.py --push '+kind);
  return false;
}
"""


def write_weld_artifact():
    """Run the weld check in DRY-RUN and drop its JSON beside the page, so the
    page's weld button has fresh data without the browser writing anything."""
    try:
        import steel_weld_flip
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            targets, summary = steel_weld_flip.scan()
        payload = {"mode": "DRY-RUN", "generated_at": datetime.now(timezone.utc).isoformat(),
                   "targets": targets, "summary": summary}
        os.makedirs(OUTDIR, exist_ok=True)
        with open(os.path.join(OUTDIR, "steel-weld-check.json"), "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        return len(targets)
    except Exception as e:
        os.makedirs(OUTDIR, exist_ok=True)
        with open(os.path.join(OUTDIR, "steel-weld-check.json"), "w", encoding="utf-8") as f:
            json.dump({"mode": "DRY-RUN", "error": str(e), "targets": [], "summary": {}}, f, indent=2)
        return None


def _validate_js(js):
    """Syntax-check the emitted JS with a real engine (node).

    A single unterminated string literal kills the WHOLE <script> block, so
    every handler on the page dies silently and no click does anything. That
    happened once (a .join/alert line was split across lines) and cost three
    rounds of guessing -- a hand-rolled checker then false-positived on an
    apostrophe in a block comment.

    So: don't guess. Ask node. Returns a list of error strings, empty if clean.
    Falls back to a no-op if node is missing (the page still builds).
    """
    import subprocess, shutil, tempfile
    node = shutil.which("node")
    if not node:
        return []
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                         encoding="utf-8") as f:
            f.write(js)
            path = f.name
        r = subprocess.run([node, "--check", path], capture_output=True,
                           text=True, timeout=30)
        os.unlink(path)
        if r.returncode != 0:
            return [x for x in (r.stderr or r.stdout).strip().split("\n") if x]
        return []
    except Exception as e:
        return [f"validator error: {e}"]


def render(groups, summary, stats, integrity_bad, unmapped):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lm = stats.get("live_meta") or {}
    src = lm.get("source", "unknown")
    src_note = {
        "live": "live Asana",
        "cache": f"live Asana (cached {lm.get('age_s')}s ago)",
        "stale-cache": f"STALE cache ({lm.get('age_s')}s old) — live pull failed",
        "unavailable": "UNAVAILABLE — live pull failed, no cache",
        "mirror-forced": "ops.db mirror (forced, KNOWN-STALE)",
    }.get(src, src)
    src_cls = "ok" if src == "live" else ("warn" if src in ("cache", "mirror-forced") else "bad")
    fer = [r for r in summary if r["bucket"] == "ferrous"]
    non = [r for r in summary if r["bucket"] != "ferrous"]
    tot_fer = sum(r["mm"] for r in fer)
    tot_non = sum(r["mm"] for r in non)

    def m(v):
        return f"{v/1000:,.2f}"

    p = []
    p.append(f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Steel Profile by Pack — SES</title><style>{CSS}</style></head><body><div class="wrap">""")

    p.append(f"""<h1>Steel Profile by Pack</h1>
<div class="sub">Material requirements per build pack · generated {esc(ts)} ·
status resolved against <span class="chip {src_cls}">{esc(src_note)}</span></div>
<div class="printtitle">SES Steel Profile — Material Requirements ({esc(ts)})</div>""")

    p.append(f"""<div class="bar noprint">
<button id="tg" class="primary" data-open="1" onclick="toggleAll()">Expand all</button>
<button onclick="window.print()">🖨 Print summary</button>
<a class="btn" href="steel-ferrous-bom-request.txt" onclick="return pushDraft('ferrous')">Push Ferrous Steel BOM</a>
<a class="btn" href="steel-nonferrous-bom-request.txt" onclick="return pushDraft('nonferrous')">Push Non-Ferrous Steel BOM</a>
<button onclick="weldCheck()">🔍 Check welds → flip 'All Cut'</button>
</div>
<div id="weldout" class="panel info noprint" style="display:none"></div>
<div class="legend">Summary excludes packs whose STEEL PROFILE is <b>All Cut</b> or <b>Not needed</b>,
and excludes packs with a <b>blank</b> STEEL PROFILE (shown separately with nil values, since a
blank means unknown rather than clear).</div>""")

    # ---- summary -------------------------------------------------------------
    def pack_link(pt):
        """Hyperlink a part's parent pack to Asana when we have the gids."""
        ref = pt.get("pack_ref") or pt.get("pack_key") or ""
        if pt.get("gid"):
            proj = pt.get("project_gid") or "0"
            return (f'<a href="https://app.asana.com/0/{esc(proj)}/{esc(pt["gid"])}/f" '
                    f'target="_blank" class="mono" onclick="event.stopPropagation()">'
                    f'{esc(ref)}</a>')
        return f'<span class="mono">{esc(ref)}</span>'

    p.append(f"""<h2 id="summary">Material summary — audit list</h2>
<div class="legend">A tidy list of material and quantity to print and audit against.
Grouped by material type, then spec (A–Z). Click any row to review the part numbers
and the packs behind it. Counts come from <b>counted</b> packs only.</div>
<table class="sum"><thead><tr>
<th style="width:30%">Material</th><th>Type</th>
<th class="num">Total needed</th><th class="num">No. off</th>
<th class="num">Order lengths</th><th style="width:80px">Confirm</th>
</tr></thead><tbody>""")
    seq = iter(range(1, 100000))
    if not summary:
        p.append('<tr><td colspan="6" class="empty">No material in scope.</td></tr>')
    for grp, label in ((fer, "Ferrous"), (non, "Non-Ferrous")):
        if not grp:
            continue
        p.append(f'<tr class="buckethero"><td colspan="6">{esc(label)}</td></tr>')
        # inside the bucket: group by material type, then material A-Z
        by_type = defaultdict(list)
        for r in grp:
            by_type[r["material_type"] or "(unknown)"].append(r)
        for mtype in sorted(by_type, key=lambda x: x.lower()):
            sub_mm = sum(r["mm"] for r in by_type[mtype])
            p.append(f'<tr class="typehero"><td colspan="6">{esc(mtype)} '
                     f'<span class="dim">— {m(sub_mm)} m</span></td></tr>')
            for r in sorted(by_type[mtype], key=lambda x: x["material"].lower()):
                nparts = len(r["parts"])
                rid = f"d{next(seq)}"
                p.append(
                    f'<tr class="sumrow" data-target="{rid}" onclick="tog(this)">'
                    f'<td><span class="caret big">▸</span> {esc(r["material"])}</td>'
                    f'<td class="dim">{esc(r["material_type"])}</td>'
                    f'<td class="num"><b>{m(r["mm"])} m</b></td>'
                    f'<td class="num">{r["off"]:,.0f}</td>'
                    f'<td class="num"><input class="lenbox" type="number" min="0" step="1" '
                    f'placeholder="lengths" onclick="event.stopPropagation()"></td>'
                    f'<td><input type="checkbox" class="confirm" '
                    f'title="Confirm we need to order this" '
                    f'onclick="event.stopPropagation()"></td></tr>')
                # detail row: the part lines that make up this material
                p.append(f'<tr class="detrow" id="{rid}" style="display:none"><td colspan="6">'
                         f'<span class="dim small">{nparts} part line(s)</span>'
                         f'<div class="subbody">'
                         f'<table><thead><tr><th>Part number</th><th>Description</th>'
                         f'<th>Build pack</th><th class="num">Cut (mm)</th>'
                         f'<th class="num">No. off</th><th class="num">Total (mm)</th>'
                         f'</tr></thead><tbody>')
                for pt in sorted(r["parts"], key=lambda x: (x["pack_key"], x["part_no"] or "")):
                    p.append(f'<tr><td class="mono">{esc(pt["part_no"])}</td>'
                             f'<td>{esc(pt["description"])}</td>'
                             f'<td>{pack_link(pt)}</td>'
                             f'<td class="num">{pt["cut_length_mm"] if pt["cut_length_mm"] is not None else ""}</td>'
                             f'<td class="num">{pt["no_off"] if pt["no_off"] is not None else ""}</td>'
                             f'<td class="num">{pt["total_mm"] if pt["total_mm"] is not None else ""}</td></tr>')
                p.append("</tbody></table></div></td></tr>")
        sub = tot_fer if grp is fer else tot_non
        p.append(f'<tr class="tot"><td colspan="2">{esc(label)} subtotal</td>'
                 f'<td class="num">{m(sub)} m</td><td colspan="3"></td></tr>')
    p.append(f'<tr class="tot grand"><td colspan="2">TOTAL</td>'
             f'<td class="num">{m(tot_fer+tot_non)} m</td><td colspan="3"></td></tr>')
    p.append("</tbody></table>")
    p.append(f"""<div class="legend">Ferrous <b>{m(tot_fer)} m</b> → "Ferrous Steel BOM" ·
Non-ferrous <b>{m(tot_non)} m</b> → "Non-Ferrous Steel BOM"</div>""")
    p.append(f"""<div class="bar noprint" style="margin-top:14px">
<button class="primary" onclick="submitOrder()">📋 Submit — create order request</button>
<span class="dim small">Placeholder. Later this creates an Asana task listing the
confirmed materials + lengths, ready to send as a request for quote.</span>
</div>""")

    # ---- Pack Cutting (per-pack detail) --------------------------------------
    p.append("<h2>Pack Cutting</h2>")
    p.append('<div class="legend">Incomplete packs only — completed packs are not shown. '
             'Grouped by project (first 4 digits), ordered by build due date — soonest first. '
             'Click anywhere on a row to open it; the pack reference links to Asana. Cut lines '
             'are grouped by material type, then spec.</div>')
    for proj, plist in groups:
        vis = [x for x in plist if x["state"] != "completed"]
        if not vis:
            continue
        counted = [x for x in vis if x["state"] == "counted"]
        nil = [x for x in vis if x["state"] == "nil"]
        p.append(f'<div class="projsec"><h2 class="proj">{esc(proj)} '
                 f'<span style="color:var(--dim);font-weight:400">— {len(vis)} pack(s), '
                 f'{len(counted)} counted, {len(nil)} held</span></h2>')
        for pk in vis:
            st = (pk["steel_status"] or "").strip()
            if pk["state"] == "excluded":
                cls, label = "ok", st
            elif pk["state"] == "nil":
                cls, label = "warn", "no live status"
            else:
                cls, label = "acc", st
            due = pk["due"] or "no due date"
            linkref = (f'<a href="https://app.asana.com/0/{esc(pk["project_gid"])}/'
                       f'{esc(pk["gid"])}/f" target="_blank" class="mono ref" '
                       f'onclick="event.stopPropagation()">{esc(pk["ref"])}</a>'
                       if pk.get("gid") else f'<span class="ref mono">{esc(pk["ref"])}</span>')
            p.append(f'<details class="card"><summary>'
                     f'<span class="caret">▸</span>'
                     f'{linkref}'
                     f'<span class="chip {cls}">STEEL: {esc(label)}</span>'
                     f'<span class="chip">{esc(due)}</span>'
                     f'<span class="chip">{m(pk["material_mm"])} m · '
                     f'{len(pk["materials"])} spec(s)</span>'
                     f'</summary><div class="body">')
            if pk["state"] == "excluded":
                p.append('<div class="empty">Excluded from the summary — STEEL PROFILE is '
                         f'{esc(st)}.</div>')
            elif pk["state"] == "nil":
                p.append('<div class="empty">Held apart — no live STEEL PROFILE status, so this '
                         'pack is <b>not</b> counted in the summary. Material shown for reference.</div>')
            if not pk["materials"]:
                p.append('<div class="empty">No material lines for this pack.</div>')
            else:
                # group inside the pack: material TYPE -> material spec (alpha).
                # Each cut line is rendered as ALIGNED COLUMNS so the part list
                # reads straight down the page (Giles, 2026-09-12).
                by_type = defaultdict(list)
                for name, cell in pk["materials"].items():
                    by_type[cell["material_type"] or "(unknown)"].append((name, cell))
                for mtype in sorted(by_type, key=lambda x: x.lower()):
                    p.append(f'<div class="mtype">{esc(mtype)}</div>')
                    p.append('<table class="cut"><tbody>')
                    for name, cell in sorted(by_type[mtype], key=lambda x: x[0].lower()):
                        # part numbers for this material line, comma-joined, in
                        # a fixed column so they line up between rows
                        pnos = sorted({pt["part_no"] for pt in cell["parts"] if pt.get("part_no")})
                        pno_cell = (f'<span class="pno">{esc(", ".join(pnos))}</span>'
                                    if pnos else '<span class="pno dim">—</span>')
                        p.append(
                            f'<tr class="cutline">'
                            f'<td class="c-tick">'
                            f'<input type="checkbox" title="Mark as cut (placeholder)" '
                            f'onchange="markCut(this)"></td>'
                            f'<td class="c-off"><b>{cell["off"]:,.0f}</b> off</td>'
                            f'<td class="c-at">at</td>'
                            f'<td class="c-len"><b>{cell["cut_mm"]:,.0f}</b>mm</td>'
                            f'<td class="c-mat">{esc(name)}</td>'
                            f'<td class="c-pno">{pno_cell}</td>'
                            f'<td class="c-qty">{m(cell["mm"])} m</td>'
                            f'<td class="c-ord">'
                            f'<span class="chip ordered" title="Ordered indicator (placeholder)">'
                            f'not ordered</span></td>'
                            f'</tr>')
                    p.append("</tbody></table>")
            p.append("</div></details>")
        p.append("</div>")

    # ---- coverage ------------------------------------------------------------
    p.append(f"""<h2 id="coverage">Coverage &amp; data quality</h2>""")

    # Ambiguous input FIRST — it is the thing that most needs acting on.
    p.append(f"""<div class="panel"><h3>⚠ Ambiguous / unmapped material input
    ({len(integrity_bad)} integrity, {len(unmapped)} unmapped)</h3>""")
    if integrity_bad:
        p.append('<table><thead><tr><th>Part</th><th>Pack</th><th>Description</th>'
                 '<th>Raw material</th><th>Parsed profile</th><th>Flag</th></tr></thead><tbody>')
        for r in integrity_bad:
            p.append(f'<tr><td class="mono">{esc(r["part_no"])}</td><td class="mono">{esc(r["pack_ref"])}</td>'
                     f'<td>{esc(r["description"])}</td><td>{esc(r["material_raw"])}</td>'
                     f'<td>{esc(r["profile_type"])}</td>'
                     f'<td><span class="chip bad">{esc(r["integrity"])}</span></td></tr>')
        p.append("</tbody></table>")
    else:
        p.append('<div class="empty">No parser-integrity failures.</div>')
    if unmapped:
        p.append('<div style="margin-top:10px"><table><thead><tr><th>BOM material string</th>'
                 '<th>Type</th><th class="num">Seen</th><th>Classification</th></tr></thead><tbody>')
        for r in unmapped:
            p.append(f'<tr><td class="mono">{esc(r["bom_material"])}</td><td>{esc(r["material_type"])}</td>'
                     f'<td class="num">{r["occurrences"]}</td>'
                     f'<td><span class="chip warn">{esc(r["classification"])}</span></td></tr>')
        p.append("</tbody></table></div>")
    else:
        p.append('<div class="empty">No unmapped material strings.</div>')
    p.append('<div class="legend">These inputs are <b>not dropped</b> — they are held here so a '
             'wrongly-formatted material is never silently excluded from the requirement.</div></div>')

    p.append(f"""<div class="grid2">
<div class="panel info"><h3>Ingest coverage</h3><table class="profile">
<tr><td>Steel pack refs in steel_parts</td><td class="num">{stats['steel_pack_refs']}</td></tr>
<tr><td>Matched to a live build pack</td><td class="num">{stats['matched']}</td></tr>
<tr><td>Not matched (no live pack)</td><td class="num">{stats['unmatched']}</td></tr>
<tr><td>Packs COUNTED in summary</td><td class="num">{stats['included_packs']}</td></tr>
<tr><td>Packs NIL (blank STEEL PROFILE)</td><td class="num">{stats['nil_packs']}</td></tr>
<tr><td>Packs excluded (All Cut / Not needed / complete)</td><td class="num">{stats['excluded_packs']}</td></tr>
<tr><td>Status from live Asana</td><td class="num">{stats['from_live']}</td></tr>
<tr><td>Status from ops.db mirror</td><td class="num">{stats['from_mirror']}</td></tr>
</table><div class="legend">Status is resolved against <b>{esc(src_note)}</b>.
Steel profile ingestion is <b>partial</b> — many BOMs have not yet been through
Stage 6. Counted totals are a floor, not the finished requirement.</div></div>

<div class="panel nil"><h3>Held apart — {stats['nil_packs']} packs with no
live status ({m(stats['nil_mm'])} m)</h3>
<div class="legend" style="margin:0 0 6px">These packs carry material in <b>steel_parts</b> but
have <b>no</b> live STEEL PROFILE status — overwhelmingly because the pack itself has no
incomplete row in 3.1 Build Schedule, i.e. it belongs to a <b>historic or already-completed
job</b>. They are deliberately <b>not</b> summed, because counting them would report finished
builds as outstanding demand. Listed so the backlog stays visible. Treat these as history to be
retired, not as material to order.</div>""")
    nil_packs = [(proj, pk) for proj, pl in groups for pk in pl if pk["state"] == "nil"]
    if nil_packs:
        p.append('<table><thead><tr><th>Project</th><th>Pack</th><th class="num">Length (m)</th>'
                 '</tr></thead><tbody>')
        for proj, pk in sorted(nil_packs, key=lambda x: -x[1]["material_mm"]):
            link = (f'<a href="https://app.asana.com/0/{esc(pk["project_gid"])}/{esc(pk["gid"])}/f" '
                    f'target="_blank" class="mono">{esc(pk["ref"])}</a>'
                    if pk.get("gid") else f'<span class="mono">{esc(pk["ref"])}</span>')
            p.append(f'<tr><td class="mono">{esc(proj)}</td><td>{link}</td>'
                     f'<td class="num">{m(pk["material_mm"])}</td></tr>')
        p.append("</tbody></table>")
    else:
        p.append('<div class="empty">No packs without a live status.</div>')
    p.append("</div></div>")

    p.append(f'<div class="legend" style="margin-top:24px">Generated {esc(ts)} · '
             f'sources: steel_profile.db/steel_parts + live Asana STEEL PROFILE · read-only</div>')
    p.append(f"</div><script>{JS}</script></body></html>")

    bad = _validate_js(JS)
    if bad:
        raise SystemExit(
            "REFUSING TO WRITE: emitted JS does not parse. Every click handler "
            "on the page would silently die.\n" + "\n".join("  " + b for b in bad))

    htmlout = "\n".join(p)
    os.makedirs(OUTDIR, exist_ok=True)
    main_path = os.path.join(OUTDIR, "steel-profile-by-pack.html")
    with open(main_path, "w", encoding="utf-8") as f:
        f.write(htmlout)
    dated = os.path.join(OUTDIR, f"steel-profile-by-pack-{datetime.now().strftime('%Y%m%d')}.html")
    with open(dated, "w", encoding="utf-8") as f:
        f.write(htmlout)
    return main_path, len(htmlout)


# --- push drafts --------------------------------------------------------------
def push_draft(bucket, summary, groups):
    want = "ferrous" if bucket == "ferrous" else "nonferrous"
    rows = [r for r in summary if (r["bucket"] == "ferrous") == (want == "ferrous")]
    title = "Ferrous Steel BOM" if want == "ferrous" else "Non-Ferrous Steel BOM"
    total = sum(r["mm"] for r in rows)
    body = [f"Material | Type | Length (m) | No. off | Packs"]
    body.append("---|---|---|---|---")
    for r in rows:
        body.append(f"{r['material']} | {r['material_type']} | {r['mm']/1000:,.2f} | "
                    f"{r['off']:,.0f} | {r['packs']}")
    body.append(f"**Total | | {total/1000:,.2f} m | |**")
    rd = os.path.join(WS, "queues", "REQUESTS")
    os.makedirs(rd, exist_ok=True)
    fn = os.path.join(rd, f"{datetime.now().strftime('%Y-%m-%dT%H-%M-%S')}-steel-bom-{want}.md")
    with open(fn, "w", encoding="utf-8") as f:
        f.write(f"""---
From: hermes
To: sap
Type: asana_write
Status: PENDING
Priority: normal
Created: {datetime.now(timezone.utc).isoformat()}
---

## Request
Action: create_task

## Summary
Procurement request: **{title}**

## Body
{chr(10).join(body)}

## Context
Raised from the Steel Profile by Pack dashboard. Excludes packs whose STEEL
PROFILE is 'All Cut' or 'Not needed'. Ingestion is partial — these totals are a
floor. Long-term intent: this task should also send a quote request; for now the
task itself is the deliverable.

## Guardrail assessment
- Create (not delete) — safe.
- Asana write routed via SAP (only Asana writer). Nothing posted until Giles
  approves the drain.
""")
    return fn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--push", choices=["ferrous", "nonferrous"])
    ap.add_argument("--no-live", action="store_true",
                    help="skip the live Asana pull and use the ops.db mirror (debug only)")
    args = ap.parse_args()

    if args.no_live:
        live_map, live_meta = {}, {"source": "mirror-forced", "fetched_at": None,
                                   "age_s": None, "error": "disabled via --no-live"}
    else:
        import steel_status_live
        live_map, live_meta = steel_status_live.load_live()

    ops, packs, integrity_bad, unmapped = load(live_map)
    groups, summary, stats = build_model(ops, packs, live_map)
    stats["live_meta"] = live_meta

    if args.push:
        fn = push_draft(args.push, summary, groups)
        print(f"Draft written: {fn}")
        print("Nothing has been posted to Asana. SAP drains queues/REQUESTS.")
        return

    path, size = render(groups, summary, stats, integrity_bad, unmapped)
    n_ready = write_weld_artifact()
    print(f"Wrote {path}  ({size:,} bytes)")
    print(f"  status source: {live_meta['source']} (age {live_meta['age_s']}s)")
    print(f"  packs: {stats['included_packs']} counted, {stats['nil_packs']} nil, "
          f"{stats['excluded_packs']} excluded, {stats['unmatched']} unmatched")
    print(f"  status from live: {stats['from_live']}  from mirror: {stats['from_mirror']}")
    if n_ready is not None:
        print(f"  weld check: {n_ready} pack(s) ready to flip to 'All Cut'")
    fer = sum(r['mm'] for r in summary if r['bucket'] == 'ferrous')
    non = sum(r['mm'] for r in summary if r['bucket'] != 'ferrous')
    print(f"  ferrous {fer/1000:,.2f} m | non-ferrous {non/1000:,.2f} m")


if __name__ == "__main__":
    main()
