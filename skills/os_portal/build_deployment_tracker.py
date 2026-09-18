#!/usr/bin/env python3
"""
build_deployment_tracker.py — SAS OS: Procurement & BOM Deployment Tracker.

Renders data/os_status/deployment-tracker.html from
data/os_status/deployment-state.json. This page is the single view of where we
are in the full procurement/BOM platform deployment:

  * One card per pipeline process (P1..Pn) with a SHORT REFERENCE ID so Giles can
    say "let's address P3" (or "EC-P3-1") in chat and Hermes knows exactly which
    stage / edge case he means.
  * Each card: status badge, data-routing note, outstanding items, verify-with
    validation criteria, edge-case ledger (open/signed-off), and a sign-off marker.

STYLE: matches the OS portal theming (light/stock/neon CSS vars, hero header,
nav button). Read-only — it never writes to Asana or ops.db.

Usage:  python build_deployment_tracker.py [--out DIR] [--state PATH]
Writes: data/os_status/deployment-tracker.html (+ dated copy).
"""
import argparse
import datetime
import json
import sys
from pathlib import Path

WS = Path(r"C:/Users/GilesHugo/SES_Workspace")
OUT_DEFAULT = WS / "data" / "os_status"

# ── Theme CSS (matches nav.html / portal pages) ─────────────────────────────
THEME_CSS = """
:root{--bg:#f4f6fa;--card:#fff;--ink:#1a1a2e;--muted:#6b7280;--line:#e5e7eb;
--brand:#0f766e;--amber:#d97706;--red:#dc2626;--green:#16a34a;--blue:#2563eb;
--brand2:#0f766e;--hero-grad:linear-gradient(135deg,#0f172a 0%,#134e4a 100%);
--hover:#f9fafb;--subbg:#f8fafc;--accbg:#ecfdf5;--accink:#065f46}
:root.theme-stock{--bg:#0f1117;--card:#1a1d27;--ink:#e2e8f0;--muted:#94a3b8;
--line:#2d3250;--brand:#6c8ef7;--amber:#fbbf24;--red:#f87171;--green:#34d399;--blue:#6c8ef7;
--brand2:#a78bfa;--hero-grad:linear-gradient(135deg,#1e2a5e 0%,#2d1f6e 100%);
--hover:#21253a;--subbg:#1f2436;--accbg:#0c2b22;--accink:#34d399}
:root.theme-neon{--bg:#05060f;--card:#0d101f;--ink:#e6f1ff;--muted:#8a93b8;
--line:#1e2a4a;--brand:#00f0ff;--amber:#ffd100;--red:#ff3b6b;--green:#00ff9d;--blue:#5f7cff;
--brand2:#b14aff;--hero-grad:linear-gradient(135deg,#060014 0%,#1a0040 60%,#004b73 100%);
--hover:#131a33;--subbg:#0b0f24;--accbg:#001a1f;--accink:#00ff9d}
*{box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--ink);margin:0;line-height:1.5;transition:background .4s,color .4s}
.wrap{max-width:1000px;margin:0 auto;padding:32px 24px 80px}
header.hero{background:var(--hero-grad);color:#fff;border-radius:16px;padding:30px;margin-bottom:24px;transition:background .4s}
header.hero h1{margin:0 0 6px;font-size:28px}
header.hero .sub{opacity:.85;font-size:14px;margin-bottom:16px}
.thememenu{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.tbtn{background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.25);color:#fff;border-radius:20px;padding:5px 14px;font-size:12.5px;cursor:pointer;font-weight:600}
.tbtn.on{background:var(--brand);border-color:var(--brand)}
.tbtn .dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px;vertical-align:middle}
.navbtn{position:fixed;top:16px;right:16px;z-index:9999;background:#0f172a;color:#fff;text-decoration:none;border-radius:20px;padding:8px 16px;font-size:13px;font-weight:600;box-shadow:0 2px 8px rgba(0,0,0,.2)}
.summary{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:22px}
.pill{display:inline-block;border-radius:14px;padding:4px 12px;font-size:12px;font-weight:600;vertical-align:middle}
.pill.ready{background:color-mix(in srgb,var(--green) 15%,transparent);color:var(--green);border:1px solid var(--green)}
.pill.progress{background:color-mix(in srgb,var(--blue) 15%,transparent);color:var(--blue);border:1px solid var(--blue)}
.pill.blocked{background:color-mix(in srgb,var(--red) 15%,transparent);color:var(--red);border:1px solid var(--red)}
.pill.done{background:color-mix(in srgb,var(--green) 18%,transparent);color:var(--green);border:1px solid var(--green)}
.stat{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 16px;font-size:13px}
.stat b{font-size:20px;display:block}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:20px 22px;margin-bottom:16px;transition:background .4s}
.card .row1{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.refid{font-family:ui-monospace,Menlo,Consolas,monospace;letter-spacing:.5px;background:color-mix(in srgb,var(--brand) 15%,transparent);color:var(--brand);border:1px solid var(--brand);border-radius:8px;padding:3px 10px;font-size:13px;font-weight:700}
.card h2{margin:0;font-size:17px;flex:1;min-width:220px}
.card .summary{margin:6px 0 0;color:var(--muted);font-size:13px}
.block{margin-top:14px}
.block h4{margin:0 0 6px;font-size:12px;text-transform:uppercase;letter-spacing:.5px;color:var(--muted)}
.block ul{margin:0;padding-left:18px}
.block li{margin:2px 0;font-size:13.5px}
.mark{font-size:13px;line-height:1.7}
.mark b{color:var(--brand)}
.routing{font-size:13px;color:var(--muted);border-left:3px solid var(--line);padding-left:10px;margin-top:10px}
.ec{display:flex;gap:10px;align-items:flex-start;font-size:13px;padding:7px 10px;border-radius:8px;margin-top:5px;background:var(--subbg)}
.ec .ecid{font-family:ui-monospace,Menlo,Consolas,monospace;font-weight:700;white-space:nowrap}
.ec.open .ecid{color:var(--red)}
.ec.done .ecid{color:var(--green)}
.ec .ett{flex:1}
.ec .est{white-space:nowrap;font-size:11px;font-weight:600}
.ec .est.open{color:var(--red)}
.ec .est.done{color:var(--green)}
.signed{margin-top:12px;font-size:13px;font-weight:600}
.signed.no{color:var(--amber)}
.signed.yes{color:var(--green)}
footer{color:var(--muted);font-size:12px;text-align:center;margin-top:30px}
h3.sec{font-size:15px;text-transform:uppercase;letter-spacing:.6px;color:var(--muted);margin:26px 0 12px}
"""

BTN_JS = """
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
  if (t) document.documentElement.classList.add('theme-'+t);
  var b=document.querySelectorAll('.tbtn');
  for (var i=0;i<b.length;i++) b[i].classList.toggle('on', b[i].getAttribute('data-theme')===t);
}
"""


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def status_pill(st):
    m = {
        "ready": ("ready", "READY"),
        "in_progress": ("progress", "IN PROGRESS"),
        "blocked": ("blocked", "BLOCKED"),
        "done": ("done", "SIGNED OFF"),
    }
    cls, label = m.get(st, ("progress", st.replace("_", " ").upper()))
    return f'<span class="pill {cls}">{label}</span>'


def ec_block(ec):
    st = ec.get("status", "open")
    cls = "done" if st == "done" else "open"
    est = "SIGNED OFF" if st == "done" else "OPEN"
    owner = ec.get("owner", "—")
    verified = ec.get("verified_by") and f' · verified by {ec.get("verified_by")}' or ""
    notes = ec.get("notes") and f' <span style="color:var(--muted)">({esc(ec["notes"])})</span>' or ""
    return (f'<div class="ec {cls}"><span class="ecid">{esc(ec.get("id","EC-?"))}</span>'
            f'<span class="ett">{esc(ec.get("title",""))}{notes}</span>'
            f'<span class="est {cls}">{est}</span></div>'
            f'<div style="font-size:11px;color:var(--muted);margin:2px 0 6px 4px">owner {esc(owner)}{verified}</div>')


def stage_card(stage):
    st = stage.get("status", "in_progress")
    o = stage.get("outstanding") or []
    ecs = stage.get("edge_cases") or []
    open_ec = sum(1 for e in ecs if e.get("status") != "done")
    signed = stage.get("signed_off", False)
    signed_mark = (
        '<div class="signed no">⛔ NOT SIGNED OFF' +
        (f' — {esc(stage["signed_off_at"])}' if stage.get("signed_off_at") else '') + '</div>'
        if not signed else
        f'<div class="signed yes">✅ SIGNED OFF'
        + (f' by {esc(stage["signed_off_by"])}' if stage.get("signed_off_by") else '')
        + (f' on {esc(stage["signed_off_at"])}' if stage.get("signed_off_at") else '') + '</div>'
    )
    out_html = "".join(f"<li>{esc(x)}</li>" for x in o) or "<li><i>None outstanding</i></li>"
    ec_html = "".join(ec_block(e) for e in ecs)
    ec_count = f'<span style="color:var(--muted)"> · {len(ecs)} edge case(s), {open_ec} open</span>' if ecs else ""
    verify = stage.get("verify_with") or ""
    routing = stage.get("data_routing") or ""
    return f"""
<div class="card" id="{esc(stage['id'])}">
  <div class="row1">
    <span class="refid">{esc(stage['id'])}</span>
    <h2>{esc(stage['name'])}</h2>
    {status_pill(st)}
  </div>
  <p class="summary">{esc(stage.get('summary',''))}</p>
  <div class="block">
    <h4>Outstanding</h4>
    <ul>{out_html}</ul>
  </div>
  {('<div class="block routing"><b>Data routing: </b>' + esc(routing) + '</div>') if routing else ''}
  <div class="block">
    <h4>Verification / validation criteria (Hermes + Giles){ec_count}</h4>
    <div class="mark">{esc(verify)}</div>
    {ec_html}
  </div>
  {signed_mark}
</div>"""


def build(state: dict) -> str:
    stages = state.get("stages", [])
    today = datetime.date.today().strftime("%Y-%m-%d")
    n = len(stages)
    ready = sum(1 for s in stages if s.get("status") == "ready")
    prog = sum(1 for s in stages if s.get("status") == "in_progress")
    blocked = sum(1 for s in stages if s.get("status") == "blocked")
    signed = sum(1 for s in stages if s.get("signed_off"))
    open_ec = sum(1 for s in stages for e in s.get("edge_cases", []) if e.get("status") != "done")

    the_hero = f"""
<header class="hero">
  <h1>🚀 Procurement &amp; BOM Platform — Deployment Tracker</h1>
  <div class="sub">Full-deployment status · reference any process by ID in chat (e.g. "address P3" or "EC-P4-1") · verification + sign-off ledger. · Generated {today} · state: data/os_status/deployment-state.json</div>
  <div class="thememenu">
    <button class="tbtn" data-theme="light" onclick="setTheme('light')"><span class="dot" style="background:#fff"></span>Light</button>
    <button class="tbtn" data-theme="stock" onclick="setTheme('stock')"><span class="dot" style="background:#6c8ef7"></span>Stock</button>
    <button class="tbtn" data-theme="neon" onclick="setTheme('neon')"><span class="dot" style="background:#00f0ff"></span>Neon</button>
  </div>
</header>

<div class="summary">
  <div class="stat"><b style="color:var(--blue)">{n}</b>{n} pipeline process(es)</div>
  <div class="stat"><b style="color:var(--green)">{ready}</b>ready</div>
  <div class="stat"><b style="color:var(--blue)">{prog}</b>in progress</div>
  <div class="stat"><b style="color:var(--red)">{blocked}</b>blocked</div>
  <div class="stat"><b style="color:var(--green)">{signed}</b>signed off</div>
  <div class="stat"><b style="color:var(--red)">{open_ec}</b>open edge case(s)</div>
</div>
"""
    cards = "\n".join(stage_card(s) for s in stages)

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>SAS OS — Procurement &amp; BOM Deployment Tracker</title>
<style>{THEME_CSS}</style></head>
<body onload="initTheme()"><a class="navbtn" href="nav.html">&larr; SAS OS portal</a><div class="wrap">
{the_hero}
<h3 class="sec">Pipeline stages — deploy, verify, sign off</h3>
{cards}
<footer>SAS OS · Procurement &amp; BOM deployment tracker · reference IDs: P1-P8 stages · EC-Pn-N edge cases · read-only · every item hyperlinks to its source</footer>
</div>
<script>{BTN_JS}</script>
</body></html>"""


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=OUT_DEFAULT)
    ap.add_argument("--state", default=OUT_DEFAULT / "deployment-state.json")
    args = ap.parse_args()
    out = Path(args.out)
    state_path = Path(args.state)
    if not state_path.exists():
        raise SystemExit(f"ERROR: state file not found at {state_path}")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    html = build(state)
    out.mkdir(parents=True, exist_ok=True)
    target = out / "deployment-tracker.html"
    target.write_text(html, encoding="utf-8")
    dated = out / f"deployment-tracker-{datetime.date.today().strftime('%Y-%m-%d')}.html"
    try:
        dated.write_text(html, encoding="utf-8")
    except OSError:
        pass
    print(f"deployment-tracker.html written ({len(html)} bytes) — {len(state.get('stages', []))} stages, "
          f"{sum(1 for s in state.get('stages',[]) if not s.get('signed_off'))} not yet signed off")


if __name__ == "__main__":
    main()
