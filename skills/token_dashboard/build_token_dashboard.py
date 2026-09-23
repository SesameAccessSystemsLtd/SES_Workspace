#!/usr/bin/env python3
"""
build_token_dashboard.py — Render the AI token-spend dashboard (self-contained HTML).

Reads data/token_spend.json (produced by collect_token_spend.py) and writes
data/os_status/token-spend.html — served by the OS portal at
http://127.0.0.1:8765/data/os_status/token-spend.html

No external JS/CSS deps — inline styles + inline SVG bars so it renders offline.

Usage:  python build_token_dashboard.py [--in JSON] [--out HTML]
"""
import os
import json
import html
import argparse
import datetime

WS = r"C:\Users\GilesHugo\SES_Workspace"
IN_PATH = os.path.join(WS, "data", "token_spend.json")
OUT_PATH = os.path.join(WS, "data", "os_status", "token-spend.html")


def esc(x):
    return html.escape(str(x if x is not None else ""))


def _n(x):
    return f"{int(x):,}"


def _usd(x):
    return f"${x:,.2f}"


def _usd4(x):
    return f"${x:,.4f}"


def money(x):
    return f"${x:,.2f}"


def bar(value, maxv, width=140, color="#38bdf8"):
    """Inline SVG horizontal bar scaled to maxv."""
    if maxv <= 0:
        maxv = 1
    w = max(2, int(width * (value / maxv)))
    return (f'<svg width="{width}" height="12" style="vertical-align:middle">'
            f'<rect x="0" y="0" width="{width}" height="12" fill="#1e293b"/>'
            f'<rect x="0" y="0" width="{w}" height="12" fill="{color}"/></svg>')


def spark(days, field="cost"):
    """Tiny sparkline SVG for daily series."""
    vals = [d.get(field, 0) for d in days]
    if not vals:
        return ""
    W, H = 900, 60
    mx = max(vals) or 1
    n = len(vals)
    step = W / max(1, n - 1)
    pts = " ".join(f"{i*step:.1f},{H - (v/mx)*H:.1f}" for i, v in enumerate(vals))
    area = f"0,{H} " + pts + f" {W},{H}"
    return (f'<svg viewBox="0 0 {W} {H}" width="100%" height="60" preserveAspectRatio="none">'
            f'<polygon points="{area}" fill="#0e2a3d"/>'
            f'<polyline points="{pts}" fill="none" stroke="#38bdf8" stroke-width="2"/></svg>')


def table(rows_html, head_html):
    return f"<table><tr>{head_html}</tr>{rows_html}</table>"


def render(data):
    t = data["totals"]
    rng = data["range"]
    head = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI Token Spend — Sesame Access</title>
<style>
:root {{ --bg:#0f172a; --ink:#e2e8f0; --muted:#94a3b8; --panel:#1e293b;
        --line:#334155; --acc:#38bdf8; --good:#4ade80; --bad:#f87171; --warn:#fbbf24; }}
body {{ margin:0; font-family:Segoe UI,system-ui,sans-serif; background:var(--bg); color:var(--ink); }}
.wrap {{ max-width:1280px; margin:0 auto; padding:22px; }}
h1 {{ font-size:22px; }} h2 {{ font-size:17px; border-bottom:1px solid var(--line);
     padding-bottom:5px; color:var(--acc); margin-top:30px; }}
.cards {{ display:flex; flex-wrap:wrap; gap:12px; margin:14px 0; }}
.card {{ background:var(--panel); border:1px solid var(--line); border-radius:10px;
        padding:12px 16px; min-width:150px; }}
.card .v {{ font-size:22px; font-weight:700; color:var(--acc); }}
.card .l {{ font-size:11px; color:var(--muted); text-transform:uppercase; letter-spacing:.5px; }}
table {{ border-collapse:collapse; width:100%; margin:8px 0 16px; font-size:13px; }}
th,td {{ border:1px solid var(--line); padding:5px 8px; text-align:left; vertical-align:middle; }}
th {{ background:#111c31; color:var(--muted); font-weight:600; }}
td.n,th.n {{ text-align:right; }}
.num {{ text-align:right; }}
.muted {{ color:var(--muted); font-size:12px; }}
.flag {{ color:var(--warn); }}
.badge {{ display:inline-block; padding:1px 8px; border-radius:10px; font-size:11px; }}
.b-cut {{ background:#3b1616; color:#fca5a5; }}
.b-review {{ background:#3b2f16; color:#fcd34d; }}
.back {{ position:fixed; top:14px; right:16px; z-index:9; background:#0f172a; color:#fff;
        border:1px solid var(--line); border-radius:20px; padding:6px 14px; text-decoration:none; }}
a {{ color:var(--acc); text-decoration:none; }}
</style></head><body><div class="wrap">
<a class="back" href="nav.html">&larr; SAS OS portal</a>
<h1>💸 AI Token Spend</h1>
<p class="muted">Tokens are real (Hermes session ledger). $ is a modelled estimate from per-model rates.
Range {esc(rng['start'])} → {esc(rng['end'])} ({rng['days']} days). Generated {esc(data['generated_at'])}.</p>
"""

    # Headline
    cards = f"""
<div class="cards">
  <div class="card"><div class="v">{_n(t['sessions'])}</div><div class="l">Sessions ({rng['days']}d)</div></div>
  <div class="card"><div class="v">{_n(t['in']+t['out'])}</div><div class="l">Billed tokens (in+out)</div></div>
  <div class="card"><div class="v">{_n(t['cr'])}</div><div class="l">Cache-read tokens</div></div>
  <div class="card"><div class="v">{_usd(t['cost'])}</div><div class="l">Est. spend ({rng['days']}d)</div></div>
  <div class="card"><div class="v">{_usd(t['proj_month'])}</div><div class="l">Projected / month</div></div>
  <div class="card"><div class="v">{_usd(t['proj_year'])}</div><div class="l">Projected / year</div></div>
</div>"""

    # Daily spark + table (last 30 for readability)
    days = data["day"]
    daily = f"<h2>Per-day spend</h2>{spark(days,'cost')}"
    drows = ""
    for d in days[-30:]:
        drows += (f"<tr><td>{esc(d['k'])}</td><td class=n>{_n(d['in']+d['out'])}</td>"
                  f"<td class=n>{_n(d['cr'])}</td><td class=n>{_usd(d['cost'])}</td>"
                  f"<td class=n>{d['sessions']}</td></tr>")
    daily += table(drows, "<th>Day</th><th class=n>Tok in+out</th><th class=n>Cache-read</th><th class=n>Est $</th><th class=n>Sessions</th>")
    daily += f"<p class='muted'>Showing last 30 of {len(days)} days.</p>"

    # Weekly
    wrows = ""
    wmax = max([w['cost'] for w in data['week']] or [1])
    for w in data["week"]:
        wrows += (f"<tr><td>{esc(w['k'])}</td><td>{bar(w['cost'], wmax)}</td>"
                  f"<td class=n>{_usd(w['cost'])}</td><td class=n>{_n(w['in']+w['out'])}</td>"
                  f"<td class=n>{w['sessions']}</td></tr>")
    weekly = "<h2>Per-week spend</h2>" + table(wrows, "<th>Week</th><th>Relative</th><th class=n>Est $</th><th class=n>Tokens</th><th class=n>Sessions</th>")

    # Monthly + projection
    mrows = ""
    mmax = max([m['cost'] for m in data['month']] or [1])
    for m in data["month"]:
        mrows += (f"<tr><td>{esc(m['k'])}</td><td>{bar(m['cost'], mmax, color='#4ade80')}</td>"
                  f"<td class=n>{_usd(m['cost'])}</td><td class=n>{_n(m['in']+m['out'])}</td>"
                  f"<td class=n>{m['sessions']}</td></tr>")
    monthly = "<h2>Per-month spend</h2>" + table(mrows, "<th>Month</th><th>Relative</th><th class=n>Est $</th><th class=n>Tokens</th><th class=n>Sessions</th>")
    monthly += f"""<div class="cards">
  <div class="card"><div class="v">{_usd(t['cost_per_day'])}</div><div class="l">$/day run-rate</div></div>
  <div class="card"><div class="v">{_usd(t['proj_month'])}</div><div class="l">Projected month</div></div>
  <div class="card"><div class="v">{_usd(t['proj_year'])}</div><div class="l">Projected year</div></div>
</div>
<p class="muted">Projection = $/day run-rate over the whole observed range × 30.4 (month) / 365 (year). Assumes usage holds steady.</p>"""

    # By source
    srows = ""
    smax = max([s['cost'] for s in data['by_source']] or [1])
    for s in data["by_source"]:
        srows += (f"<tr><td>{esc(s['label'])}</td><td>{bar(s['cost'], smax, color='#fbbf24')}</td>"
                  f"<td class=n>{_usd(s['cost'])}</td><td class=n>{_n(s['in']+s['out'])}</td>"
                  f"<td class=n>{_n(s['cr'])}</td><td class=n>{s['sessions']}</td></tr>")
    bysrc = "<h2>Where the spend comes from</h2>" + table(srows, "<th>Source</th><th>Relative</th><th class=n>Est $</th><th class=n>Tok in+out</th><th class=n>Cache-read</th><th class=n>Sessions</th>")

    # By model
    mrows2 = ""
    mm2 = max([m['cost'] for m in data['by_model']] or [1])
    for m in data["by_model"]:
        mrows2 += (f"<tr><td>{esc(m['k'])}</td><td>{bar(m['cost'], mm2, color='#a78bfa')}</td>"
                   f"<td class=n>{_usd(m['cost'])}</td><td class=n>{_n(m['in'])}</td>"
                   f"<td class=n>{_n(m['out'])}</td><td class=n>{m['sessions']}</td></tr>")
    bymodel = "<h2>By model</h2>" + table(mrows2, "<th>Model</th><th>Relative</th><th class=n>Est $</th><th class=n>Tok in</th><th class=n>Tok out</th><th class=n>Sessions</th>")

    # Top cost cron jobs + watchlist
    jrows = ""
    jmax = max([j['cost'] for j in data['by_job']] or [1])
    for j in data["by_job"]:
        flag = f"<span class='flag'>{esc(j['flag'])}</span>" if j.get("flag") else ""
        jrows += (f"<tr><td>{esc(j['name'])}</td><td>{bar(j['cost'], jmax, color='#f87171')}</td>"
                  f"<td class=n>{j['runs']}</td><td class=n>{_usd4(j['cost_per_run'])}</td>"
                  f"<td class=n>{_usd(j['cost'])}</td><td class=n>{_usd(j['cost_per_month'])}</td>"
                  f"<td>{flag}</td></tr>")
    jobs = "<h2>Background cost by cron job (highest first)</h2>" + table(
        jrows, "<th>Cron job</th><th>Relative</th><th class=n>Runs</th><th class=n>$/run</th><th class=n>Est $ (total)</th><th class=n>Est $/month</th><th>Watch</th>")

    # Watchlist summary
    flagged = [j for j in data["by_job"] if j.get("flag")]
    wl = ""
    if flagged:
        li = "".join(f"<li><b>{esc(j['name'])}</b> — {_usd(j['cost_per_month'])}/mo, {j['runs']} runs. {esc(j['flag'])}</li>" for j in flagged)
        wl = f"<h2>⚠ No-value / high-cost watch list</h2><ul>{li}</ul><p class='muted'>Heuristic flags — confirm each before cutting. High token use is not automatically waste.</p>"

    foot = f"""<h2>Method &amp; caveats</h2>
<p class="muted">{esc(data['pricing_note'])}<br>
Projection is a straight run-rate, not a forecast of the work — a large interactive day lifts it.<br>
Interactive (Telegram) sessions re-send the full conversation each turn, so they dominate input+cache tokens: this is the single biggest lever on spend.<br>
Cost rises with task size and context length, not with the number of jobs per se.</p>
</div></body></html>"""

    # Value assessment (if the collector emitted it)
    assess = ""
    a = data.get("assessment")
    if a:
        ep = a.get("email_parser", {})
        nl = a.get("nightly", {})
        assess = f"""<h2>Value assessment</h2>
<div class="card" style="background:#10263b;border-color:#25476b;margin-bottom:12px">
  <div style="font-size:15px;font-weight:600">{esc(a.get('headline',''))}</div>
</div>
<div class="cards">
  <div class="card"><div class="v">{_usd4(ep.get('usd_per_email',0))}</div><div class="l">Email parser $/email</div></div>
  <div class="card"><div class="v">{_usd(ep.get('usd_per_month',0))}</div><div class="l">Email parser /month @ {ep.get('emails_per_day','?')}/day</div></div>
  <div class="card"><div class="v">{_usd(nl.get('monthly',0))}</div><div class="l">All nightly jobs /month</div></div>
</div>
<table>
<tr><th>Item</th><th>Finding</th></tr>
<tr><td><b>Email parsing</b></td><td>{esc(ep.get('note',''))}</td></tr>
<tr><td><b>Nightly runs</b><br><span class="muted">{esc(', '.join(nl.get('jobs',[])))}</span></td><td>{esc(nl.get('note',''))}</td></tr>
</table>"""

    # Reconciliation vs DeepSeek billed usage (if the collector emitted it)
    recon = ""
    rc = data.get("reconciliation")
    if rc and not rc.get("error"):
        rows = ""
        for m, c in (rc.get("actual_by_model") or {}).items():
            rows += f"<tr><td>{esc(m)}</td><td class=n>{_usd(c)}</td></tr>"
        cov = rc.get("coverage_pct")
        covtxt = f"{cov}%" if cov is not None else "n/a"
        recon = f"""<h2>Actual vs modelled (DeepSeek billed export)</h2>
<div class="cards">
  <div class="card" style="border-color:#b45309"><div class="v" style="color:#fbbf24">{_usd(rc.get('actual_month',0))}</div><div class="l">Billed / month (TRUE)</div></div>
  <div class="card" style="border-color:#b45309"><div class="v" style="color:#fbbf24">{_usd(rc.get('actual_year',0))}</div><div class="l">Billed / year (TRUE)</div></div>
  <div class="card"><div class="v">{_usd(rc.get('modelled_same_window',0))}</div><div class="l">Modelled (local view)</div></div>
  <div class="card"><div class="v">{covtxt}</div><div class="l">Local coverage</div></div>
</div>
<p class="muted">Export <b>{esc(rc.get('export_file',''))}</b> · window {esc(rc.get('window',''))} ({rc.get('days','?')} days) · billed total {_usd(rc.get('actual_billed',0))}.</p>
<table>
<tr><th>Billed model</th><th class=n>Actual $</th></tr>
{rows}
</table>
<p class="muted">⚠️ {esc(rc.get('gap_note',''))}</p>"""

    return head + cards + assess + recon + daily + weekly + monthly + bysrc + bymodel + jobs + wl + foot


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default=IN_PATH)
    ap.add_argument("--out", default=OUT_PATH)
    args = ap.parse_args()
    with open(args.inp, encoding="utf-8") as f:
        data = json.load(f)
    html_out = render(data)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html_out)
    print(f"OK wrote {args.out} ({len(html_out)} bytes)")


if __name__ == "__main__":
    main()
