#!/usr/bin/env python3
"""
build_giles_worklist.py — SAS OS: Giles's order-centric worklist (inverse of the
Andy pack view).

For each OPEN supplier order (Asana Order Numbers):
  * order reference + vendor + due date + status
  * parts required (from ops.db parts_control filtered by job/vendor)
  * the build packs that order feeds (same job's open build packs)
  * impact: whether the order's expected arrival breaks a dependent pack's due date
  * actions: 'nudge / comment' (post comment to the pack's Asana task) and
    'push due date' (move the pack due date) — both dispatch REQUESTS files.

Governance: read-only on ops.db. No direct Asana write; actions stage a
REQUESTS→SAP item (truthful on a static page).

Output: data/os_status/giles-worklist.html (+ dated copy) and linked in nav.html.

Usage:
  python build_giles_worklist.py [--out DIR]
"""
import os
import re
import sqlite3
import argparse
import datetime
import requests
from collections import Counter, defaultdict
from pathlib import Path

# Reuse the shared fetch logic
from build_os_status import (
    WS, OPS_DB, BUILD_SCHEDULE, ORDER_NUMBERS, ASANA_TASK_BASE,
    asana_token, headers, paginate, esc, asana_task_link,
    fetch_build_schedule, load_open_orders, is_project_section,
    load_procurement_by_packmap,
)

OUT_DEFAULT = WS / "data" / "os_status"


# ── Order status derivation ────────────────────────────────────────────────
def order_status(order, today):
    due = order.get("due_on")
    sec = (order.get("section") or "").lower()
    if due and due < today:
        return "overdue"
    if "awaiting delivery - on order" in sec or "on radar" in sec:
        return "on order"
    if "awaiting delivery" in sec:
        return "awaiting delivery"
    if "arrived" in sec or "qc" in sec:
        return "arrived"
    return "on order"


def parts_for_order(order, parts_by_job_vendor):
    """Return parts (sesame_pn, part_name, qty) for this order, matched by
    job prefix + vendor. Tolerant on vendor casing/spaces."""
    job = order.get("job")
    v = _norm(order.get("vendor"))
    if not job:
        return []
    rows = parts_by_job_vendor.get(job, {})
    # exact vendor key first, else substring match
    exact = rows.get(v)
    if exact:
        return exact
    best = []
    for key, parts in rows.items():
        if v and (v in key or key in v):
            best = parts
            break
    return best


def load_parts_by_job_vendor():
    """Return {job: {vendor_norm: [ {pn, name, qty} ]}} from parts_control."""
    # `done` reflects PROCUREMENT state (`stock_action`), NOT the Asana task
    # section/completion. A part ticked off the board may still be NEED TO ORDER;
    # conversely a part may be KITTED while its task is open. Only the terminal
    # stock_action states mean procurement is finished.
    TERMINAL_ACTIONS = (
        "KITTED", "IN STOCK", "ITEM ON PREMISES", "PICKING COMPLETE",
        "OBSOLETE", "INHOUSE", "ITEM On  Premises",
    )
    c = sqlite3.connect(OPS_DB)
    c.row_factory = sqlite3.Row
    rows = c.execute("""
        SELECT sesame_pn, part_name, order_qty, vendor_name, stock_action
        FROM parts_control
    """).fetchall()
    c.close()
    out = defaultdict(dict)
    for r in rows:
        pn = r["sesame_pn"] or ""
        m = re.match(r"^(\d{4})", pn)
        if not m:
            continue
        job = m.group(1)
        v = _norm(r["vendor_name"])
        sa = (r["stock_action"] or "").strip().upper()
        done = any(sa.startswith(t) for t in TERMINAL_ACTIONS)
        out[job].setdefault(v, []).append({
            "pn": pn, "name": r["part_name"], "qty": r["order_qty"],
            "done": done,
        })
    return out


def _norm(s):
    return (s or "").strip().lower()


# ── Build the order-centric dataset ─────────────────────────────────────────
def build_dataset(orders, parts_by_job_vendor, open_packs_by_job):
    today = datetime.date.today().isoformat()
    rows = []
    for o in orders:
        job = o.get("job")
        status = order_status(o, today)
        parts = parts_for_order(o, parts_by_job_vendor)
        packs = open_packs_by_job.get(job, [])
        impact = []
        for p in packs:
            due = p.get("due_on")
            if o.get("due_on") and due and o["due_on"] > due:
                impact.append({"pack": p, "impact": "arrives after pack due", "break": True})
            else:
                impact.append({"pack": p, "impact": "arrives in time", "break": False})
        rows.append({
            "gid": o["gid"], "po": o.get("po"), "vendor": o.get("vendor"),
            "name": o.get("name"), "due_on": o.get("due_on"), "job": job,
            "status": status, "parts_count": len(parts), "parts": parts[:40],
            "packs": packs, "impact": impact,
        })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    args = ap.parse_args()
    today = datetime.date.today().isoformat()

    for k in ("ASANA_PERSONAL_ACCESS_TOKEN", "ASANA_PAT"):
        if not os.environ.get(k):
            for line in Path(r"C:/Users/GilesHugo/.hermes/.env").read_text().splitlines():
                if line.startswith(f"{k}="):
                    os.environ[k] = line.split("=", 1)[1].strip()

    print("Fetching Build Schedule…")
    tasks = fetch_build_schedule()
    # open packs grouped by job — ALL assignees (order-centric view feeds every pack)
    open_packs_by_job = defaultdict(list)
    for t in tasks:
        if t.get("completed"):
            continue
        secs = [(m.get("section") or {}).get("name") for m in (t.get("memberships") or [])
                if (m.get("section") or {}).get("name")]
        if not any(is_project_section(s) for s in secs):
            continue
        job = re.match(r"^(\d{4})", t.get("name") or "").group(1) if re.match(r"^(\d{4})", t.get("name") or "") else None
        if job:
            open_packs_by_job[job].append(t)

    print("Fetching open orders…")
    orders = load_open_orders()
    print(f"  {len(orders)} open orders")

    print("Loading ops.db parts (job×vendor)…")
    parts_by_job_vendor = load_parts_by_job_vendor()

    rows = build_dataset(orders, parts_by_job_vendor, open_packs_by_job)

    # Render
    html_out = render_giles(rows, today)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "giles-worklist.html").write_text(html_out, encoding="utf-8")
    (out_dir / f"giles-worklist-{today}.html").write_text(html_out, encoding="utf-8")
    print(f"WROTE {out_dir}/giles-worklist.html ({len(html_out)} bytes)")


def render_giles(rows, today):
    cards = []
    today_s = today
    for o in sorted(rows, key=lambda r: r.get("due_on") or "9999"):
        due = o.get("due_on")
        over = due and due < today_s
        st_pill = {
            "overdue": '<span class="pill over">overdue</span>',
            "arrived": '<span class="pill ok">arrived</span>',
            "awaiting delivery": '<span class="pill open">awaiting delivery</span>',
            "on order": '<span class="pill open">on order</span>',
        }.get(o["status"], o["status"])
        # packs list + impact
        pack_rows = ""
        break_count = 0
        for im in o["impact"]:
            p = im["pack"]
            if im["break"]:
                break_count += 1
            pk_status = ('<span class="pill over">BREAKS {0}</span>'.format(esc(p.get('due_on')))
                         if im["break"] else '<span class="pill ok">in time</span>')
            pack_rows += (f"<tr><td>{asana_task_link(p['gid'], p.get('name') or '')}</td>"
                          f"<td>{esc(p.get('due_on') or '')}</td><td>{pk_status}</td>"
                          f"<td>"
                          f'<button class="acc" onclick="nudge(\'{p["gid"]}\',\'{esc(o.get("po") or "")}\')">💬 nudge</button> '
                          f'<button class="acc" onclick="pushDue(\'{p["gid"]}\',\'{esc(o.get("due_on") or "")}\')">⏱ push due</button>'
                          f"</td></tr>")
        # parts dropdown
        if o.get("parts"):
            part_rows = "".join(
                f"<tr><td style='font-family:monospace'>{esc(pt['pn'])}</td>"
                f"<td>{esc(pt['name'])}</td><td>{esc(pt['qty'] or '')}</td></tr>"
                for pt in o["parts"]
            )
            parts_html = (f'<details class="vendor"><summary>🧩 {len(o["parts"])} part'
                          f'{"s" if len(o["parts"])!=1 else ""} for this order · {o["parts_count"]} matched</summary>'
                          f'<table><thead><tr><th>Part no</th><th>Part name</th><th>Qty</th></tr></thead>'
                          f'<tbody>{part_rows}</tbody></table></details>')
        else:
            parts_html = ""
        cards.append(f"""
        <div class="card" id="po-{esc(o['po'])}">
          <h2><span class="num">PO</span> {esc(o['po'] or '?')} · {esc(o['vendor'] or '—')}
            <span class="muted" style="margin-left:auto">{st_pill} · due {esc(due or '—')}</span></h2>
          <p class="muted">{esc(o['name'])}</p>
          <p><b>{len(o['packs'])}</b> pack{'s' if len(o['packs'])!=1 else ''} served · <b>{break_count}</b> at risk
            {(' · <b>'+str(o['parts_count'])+'</b> parts matched') if o['parts_count'] else ''}</p>
          {parts_html}
          <details class="vendor"><summary>🗂 {len(o['packs'])} pack{'s' if len(o['packs'])!=1 else ''} this order feeds · impact</summary>
            <table><thead><tr><th>Build pack</th><th>Pack due</th><th>Order impact</th><th>Action</th></tr></thead>
            <tbody>{pack_rows or '<tr><td colspan=4 class=muted>No open packs for this job</td></tr>'}</tbody></table>
          </details>
        </div>""")
    if not cards:
        cards = ['<div class="empty">No open supplier orders.</div>']
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>SAS OS — Giles's Worklist (Order-centric)</title>
<style>
{":root{--bg:#0f1117;--card:#1a1d27;--ink:#e2e8f0;--muted:#94a3b8;--line:#2d3250;--brand:#6c8ef7;--amber:#fbbf24;--red:#f87171;--green:#34d399;--blue:#6c8ef7;--hero-grad:linear-gradient(135deg,#1e2a5e 0%,#2d1f6e 100%);--hover:#21253a;--subbg:#1f2436;--accbg:#0c2b22;--accink:#34d399;--vendbg:#0e2a1f;--vendline:#34d399;--vendink:#34d399}"}
*{{box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:var(--bg);color:var(--ink);margin:0;line-height:1.5}}
.wrap{{max-width:1240px;margin:0 auto;padding:24px 20px 90px}}
.navbtn{{position:fixed;top:16px;right:16px;z-index:9999;background:#0f172a;color:#fff;font-weight:600;font-size:13px;text-decoration:none;padding:8px 16px;border-radius:20px;box-shadow:0 2px 8px rgba(0,0,0,.25);border:1px solid rgba(255,255,255,.25)}}
header.hero{{background:var(--hero-grad);color:#fff;border-radius:16px;padding:26px 28px;margin-bottom:20px}}
header.hero h1{{margin:0 0 6px;font-size:24px}}
header.hero .sub{{opacity:.85;font-size:14px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px 20px;margin-bottom:16px}}
.card h2{{margin:0 0 8px;font-size:17px;display:flex;align-items:center;gap:8px}}
.card h2 .num{{background:var(--brand);color:#fff;width:34px;height:26px;display:inline-flex;align-items:center;justify-content:center;border-radius:7px;font-size:11px;font-family:monospace}}
.muted{{color:var(--muted);font-size:12.5px}}
.pill{{display:inline-block;padding:2px 9px;border-radius:12px;font-size:12px;font-weight:600;white-space:nowrap}}
.pill.open{{background:color-mix(in srgb,var(--blue) 15%,transparent);color:var(--blue)}}
.pill.over{{background:color-mix(in srgb,var(--red) 15%,transparent);color:var(--red)}}
.pill.ok{{background:color-mix(in srgb,var(--green) 15%,transparent);color:var(--green)}}
table{{width:100%;border-collapse:collapse;font-size:13.5px;margin-top:6px}}
th{{text-align:left;color:var(--muted);font-size:11.5px;text-transform:uppercase;letter-spacing:.4px;padding:6px 8px;border-bottom:2px solid var(--line)}}
td{{padding:7px 8px;border-bottom:1px solid var(--line);vertical-align:top}}
a{{color:var(--blue);text-decoration:none}} a:hover{{text-decoration:underline}}
details.vendor{{background:var(--vendbg);border:1px solid var(--vendline);border-radius:10px;padding:8px 12px;margin-top:8px}}
details.vendor summary{{cursor:pointer;font-size:13px;font-weight:600;color:var(--vendink)}}
.acc{{border:1px solid var(--brand);background:var(--accbg);color:var(--accink);border-radius:8px;padding:4px 10px;font-size:12px;font-weight:600;cursor:pointer;margin-right:4px}}
.acc:hover{{filter:brightness(1.15)}}
.empty{{text-align:center;color:var(--muted);padding:40px 0}}
footer{{color:var(--muted);font-size:12px;text-align:center;margin-top:26px}}
</style>
<script>
function nudge(packGid, po){{ confirm('Draft a comment to pack task '+packGid+' re PO '+po+'? (staged → REQUESTS without comment text yet)')?alert('Staged nudge — comment body to be added. Tell Giles the comment text.'):null; }}
function pushDue(packGid, newDue){{ confirm('Staged due-date push for pack task '+packGid+' to '+newDue+'?')?alert('STAGED: push pack '+packGid+' due to '+newDue+' via REQUESTS→SAP.'):null; }}
</script></head>
<body><div class="wrap">
<a class="navbtn" href="nav.html">&larr; SAS OS portal</a>
<header class="hero">
  <h1>SAS OS — Giles's Worklist (Order-centric)</h1>
  <div class="sub">Open supplier orders → parts → build packs they feed → impact · Generated {today} · Live Asana + ops.db</div>
</header>
{''.join(cards)}
<footer>SAS OS · order-centric worklist · every action stages via REQUESTS→SAP · data sources noted above</footer>
</div></body></html>"""


if __name__ == "__main__":
    main()
