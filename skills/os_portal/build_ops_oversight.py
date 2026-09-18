#!/usr/bin/env python3
"""
build_ops_oversight.py — SAS OS dashboard: Ops Oversight & QA pane.

READ-ONLY aggregate page that lets Giles validate the whole automation is doing
the right job across orders, confirmations, email chases, and supplier mapping.
Five panes:

  Pane 1  Order lifecycle heat-map  — per-job counts by order_status bucket.
  Pane 2  Confirmations to chase    — Awaiting CONFIRMATION orders, live status
                                     cross-checked, with the email they went to.
  Pane 3  Emails to chase/answered  — overdue_chase_log draft state.
  Pane 4  Unmapped emails           — partial/unverified matches + suppliers with
                                     no contact email on file.
  Pane 5  Per-order email ledger    — every order row vs the email it's against,
                                     for correctness validation.

Governance: purely reads ops.db + live Asana. NEVER writes to Asana or the DB.
Every data row hyperlinks back to its Asana source task. Reuses the Asana fetch
helpers from build_os_status.py (load_open_orders, paginate, esc, asana_task_link).

Output: data/os_status/ops-oversight.html (+ dated copy).

Usage:
  python build_ops_oversight.py [--out DIR]
"""
import os
import re
import sys
import json
import sqlite3
import argparse
import datetime
from collections import Counter, defaultdict
from pathlib import Path

WS = Path(r"C:/Users/GilesHugo/SES_Workspace")
OPS_DB = r"C:/Users/GilesHugo/SES_Workspace/db/ops.db"
ORDER_NUMBERS = "854650811917168"
# Canonical Asana task deep link (Giles, 2026-09-11) — carries the org segment
# so it actually resolves: https://app.asana.com/1/357939803556123/task/<gid>
ASANA_ORG = "357939803556123"
ASANA_TASK_BASE = f"https://app.asana.com/1/{ASANA_ORG}/task"

# Make sure build_os_status is importable
sys.path.insert(0, str(WS / "skills" / "os_portal"))
try:
    from build_os_status import (
        asana_token, headers, api_get, paginate,
        load_open_orders, orders_by_job, job_project, esc, asana_task_link,
    )
except Exception as e:  # pragma: no cover
    raise SystemExit(f"Could not import build_os_status helpers: {e}")

# CHG-2A/2B: shared validate->fallback helpers (centre-of-truth doctrine).
from centre_of_truth import reconcile_ready, render_pref, flag_span


def task_link(gid, label):
    """Task-agnostic Asana link (resolves by task GID regardless of project)."""
    if not gid:
        return esc(label)
    return f'<a href="{ASANA_TASK_BASE}/{gid}" target="_blank">{esc(label)}</a>'

# ── Status bucketing (procurement_tasks.order_status → semantic bucket) ──────
BUCKET = [
    ("done",        re.compile(r"ARRIVED.*INVOICES|ALL PICKED|CANCELLED|TO BE RETURNED")),
    ("ok_arrived",  re.compile(r"ARRIVED.*QC")),
    ("delivery",    re.compile(r"Delivery Date CONFIRMED|AWAITING DISPATCH")),
    ("confirm",     re.compile(r"Awaiting CONFIRMATION|HOLD ON ORDER")),
    ("quote",       re.compile(r"Quote|Quote / Order")),
    ("action",      re.compile(r"To be ordered|Requires attention|TO BE ASSESSED")),
    ("pick",        re.compile(r"PICKING|Awaiting Position")),
]
BUCKET_ORDER = ["done", "ok_arrived", "delivery", "confirm", "quote", "action", "pick", "other", "none"]


def bucket_of(status):
    s = (status or "").strip()
    for name, rx in BUCKET:
        if rx.search(s):
            return name
    if s:
        return "other"
    return "none"


# Statuses that should NOT be chased: the order is resolved / held / re-routed.
_NON_CHASEABLE = [
    re.compile(r"ARRIVED.*INVOICES"),
    re.compile(r"ARRIVED.*QC"),
    re.compile(r"PARTIAL DELIVERY"),
    re.compile(r"ALL PICKED"),
    re.compile(r"CANCELLED"),
    re.compile(r"TO BE RETURNED"),
    re.compile(r"HOLD ON ORDER"),
]


def _is_chaseable(status):
    """True if a live / stored order status still needs chasing.
    ARRIVED, PARTIAL, PICKED, CANCELLED, TO BE RETURNED and HOLD are not chaseable."""
    s = (status or "").strip()
    if not s:
        return False
    return not any(rx.search(s) for rx in _NON_CHASEABLE)


# ── DB builders ──────────────────────────────────────────────────────────────
def load_status_counts(conn):
    """per-job counts by bucket from procurement_tasks.order_status."""
    rows = conn.execute(
        "SELECT order_status, COUNT(*) FROM procurement_tasks GROUP BY order_status"
    ).fetchall()
    per_job = defaultdict(Counter)
    total = Counter()
    exact_counts = {}
    for status, n in rows:
        b = bucket_of(status)
        total[b] += n
        exact_counts[b] = exact_counts.get(b, 0)  # keep per-bucket totals
    # exact_counts recompute
    exact_counts = Counter()
    for status, n in rows:
        exact_counts[bucket_of(status)] += n
    # project -> order_status buckets (job prefix from task_name / po_number)
    tasks = conn.execute(
        "SELECT task_name, order_status, asana_task_gid, vendor_name, po_number "
        "FROM procurement_tasks WHERE order_status IS NOT NULL AND order_status != ''"
    ).fetchall()
    for task_name, status, gid, vendor, po in tasks:
        job = job_project(task_name or "") or _job_from_ref(po or "") or _job_from_ref(task_name or "")
        per_job[job][bucket_of(status)] += 1
    return exact_counts, per_job


def _job_from_ref(ref):
    # a {PO}-{job} like '21223-1588' -> job 1588 ; or bare 4-digit prefix
    m = re.search(r"(\d{4})", ref or "")
    return m.group(1) if m else "?"


def load_confirmation_rows(conn):
    """Rows worth chasing: status 'On Order - Awaiting CONFIRMATION' only.
    HOLD and ARRIVED orders are excluded — they must NOT be chased."""
    rows = conn.execute(
        "SELECT task_name, order_status, asana_task_gid, vendor_name, po_number, due_date "
        "FROM procurement_tasks "
        "WHERE order_status LIKE '%Awaiting CONFIRMATION%'"
    ).fetchall()
    out = []
    for task_name, status, gid, vendor, po, due in rows:
        job = job_project(task_name or "") or _job_from_ref(po or "") or _job_from_ref(task_name or "")
        out.append({
            "job": job, "status": status or "",
            "task_name": task_name or "", "gid": gid or "",
            "vendor": vendor or "", "po": po or "", "due": due or "",
        })
    return out


def load_email_matches(conn):
    return conn.execute(
        "SELECT email_subject, received_at, supplier_name, extracted_po_number, "
        "asana_task_name, match_confidence, comment_written, verified_correct "
        "FROM email_po_matches ORDER BY received_at DESC"
    ).fetchall()


def load_suppliers_without_email(conn):
    return conn.execute(
        "SELECT vendor_name, contact_email, contact_phone "
        "FROM suppliers WHERE contact_email IS NULL OR contact_email = ''"
    ).fetchall()


def load_chase_state(conn):
    return conn.execute(
        "SELECT po_number, vendor_name, due_date, certainty, to_email, status, "
        "draft_web_link, subject, created_at "
        "FROM overdue_chase_log ORDER BY created_at DESC"
    ).fetchall()


# ordering_queue row tuple indices (must match the SELECT column order in load_ordering)
_O_TASK, _O_PN, _O_VENDOR, _O_QTY, _O_STATUS, _O_ORDERED, _O_ARRIVED, _O_PRICE, _O_GID, _O_ORDERGID = range(10)


def load_ordering(conn):
    """ordering_queue rows, sorted by vendor for readability. Counts by real
    status column (grouped = awaiting order, ordered = placed)."""
    rows = conn.execute(
        "SELECT task_name, sesame_pn, vendor_name, order_qty, status, ordered_at, "
        "arrived_at, unit_price, asana_task_gid, order_task_gid FROM ordering_queue "
        "ORDER BY COALESCE(vendor_name,''), COALESCE(task_name,'')"
    ).fetchall()
    counts = Counter([r[4] for r in rows])  # r[4] = status (was wrongly r[5]=ordered_at)
    return rows, counts


def load_supplier_email_map(conn):
    """supplier/vendor name -> best contact email (prefer quote_sender, then suppliers)."""
    m = {}
    for name, email in conn.execute(
        "SELECT vendor_name, contact_email FROM suppliers "
        "WHERE contact_email IS NOT NULL AND contact_email != ''"
    ):
        if name:
            m[(name or "").lower()] = email
    # supplier_contacts capture is more precise (per-contact actual people)
    for name, email in conn.execute(
        "SELECT supplier_name, email FROM supplier_contacts WHERE email IS NOT NULL"
    ).fetchall():
        if name and email:
            m.setdefault((name or "").lower(), email)
    return m


# ── live Asana cross-check for confirmation rows ----------------------------
def live_order_info(token_h, gid):
    """One live Asana fetch returning (completed, order_status, due_on, project_gid,
    name). Lets the page show ONLY genuinely open orders and link in the nested
    /0/{project}/{task} format (legacy completed tasks drop out of Asana's live view)."""
    if not gid:
        return None
    try:
        r = api_get(f"/tasks/{gid}", {
            "opt_fields": "completed,name,due_on,memberships.project.gid,"
                          "custom_fields.display_value,custom_fields.name"
        })
        task = r if isinstance(r, dict) and "custom_fields" in r else (r.get("data") or {})
        order_status = None
        for cf in task.get("custom_fields", []):
            if cf.get("name") in ("ORDER Status", "Order Status", "ORDER STATUS"):
                order_status = cf.get("display_value")
                break
        proj = None
        for mship in task.get("memberships", []) or []:
            pg = ((mship.get("project") or {}).get("gid"))
            if pg:
                proj = pg
                break
        return {
            "completed": bool(task.get("completed")),
            "live_status": order_status,
            "due": (task.get("due_on") or task.get("due_at") or ""),
            "project_gid": proj,
            "name": task.get("name") or "",
        }
    except Exception:
        return None


# ── rendering ────────────────────────────────────────────────────────────────
def _unwrap_task(r):
    """api_get returns the full {"data": {...}} wrapper; unwrap to the task dict."""
    if isinstance(r, dict) and "data" in r:
        return r.get("data") or {}
    return r or {}


def live_order_task_for_ordering(row, token_h):
    """CHG-2B: live-resolve an ordering_queue row's associated order/group task
    + due date. Uses the DB order_task_gid when present (future rows), else falls
    back to the part task's Asana parent. Returns
    {"order_gid","name","due","completed"} or None on any failure (→ DB fallback).
    """
    part_gid = row[_O_GID]
    order_gid = row[_O_ORDERGID] or None
    if not part_gid and not order_gid:
        return None
    try:
        if order_gid:
            t = _unwrap_task(api_get(f"/tasks/{order_gid}", {"opt_fields": "name,due_on,completed"}))
            return {"order_gid": order_gid, "name": t.get("name") or "",
                    "due": t.get("due_on") or "", "completed": bool(t.get("completed"))}
        p = _unwrap_task(api_get(f"/tasks/{part_gid}", {"opt_fields": "parent.gid,parent.name,completed"}))
        parent = p.get("parent") or {}
        pgid = parent.get("gid")
        if not pgid:
            return {"order_gid": None, "name": "", "due": "", "completed": bool(p.get("completed"))}
        t = _unwrap_task(api_get(f"/tasks/{pgid}", {"opt_fields": "name,due_on,completed"}))
        return {"order_gid": pgid, "name": t.get("name") or "",
                "due": t.get("due_on") or "", "completed": bool(t.get("completed"))}
    except Exception:
        return None


def _order_task_cell(live):
    """Render the 'Order task · due' cell from a live reconcile result (CHG-2B).
    Returns (html, unreconciled)."""
    if live is None:
        return "—", True  # live check failed → DB fallback, flagged
    gid = live.get("order_gid")
    if not gid:
        return '<span class="muted">no order task</span>', False
    name = (live.get("name") or "")[:45]
    due = (live.get("due") or "")[:10]
    link = f'<a href="{ASANA_TASK_BASE}/{gid}" target="_blank">{esc(name or gid)}</a>'
    cell = link + (f" · {esc(due)}" if due else "")
    return cell, False


def heatmap_html(buckets, per_job):
    col = {
        "done": "#0f9d58", "ok_arrived": "#57bb8a", "delivery": "#1e88e5",
        "confirm": "#f9a825", "quote": "#fb8c00", "action": "#d93025",
        "pick": "#9e9d24", "other": "#757575", "none": "#9e9e9e",
    }
    labels = {"done": "Done", "ok_arrived": "Arrived/QC", "delivery": "Delivery ok",
              "confirm": "Awaiting confirm", "quote": "Out for quote",
              "action": "Needs action", "pick": "Picking", "other": "Other", "none": "No status"}
    jobs = sorted(per_job.keys())
    th = "".join(f"<th class=n>{labels[b]}</th>" for b in BUCKET_ORDER)
    th = f"<tr><th>Job</th>{th}<th>Σ</th></tr>"
    body = []
    for job in jobs:
        c = per_job[job]
        tot = sum(c[b] for b in BUCKET_ORDER)
        tds = "".join(
            f'<td class="n" style="background:{col[b]}' + (
                f'66"><b>{c.get(b,0)}</b>' if c.get(b, 0) else '22;color:#888">0')
            for b in BUCKET_ORDER)
        body.append(f"<tr><td><b>{esc(job)}</b></td>{tds}<td class=n><b>{tot}</b></td></tr>")
    return f"<table>{th}{''.join(body)}</table>"


def confirmations_render_ready(rows, token_h):
    """Pre-pass: enrich each row with live Asana state and DROP legacy/completed
    tasks (tasks Asana has completed no longer sit in 'Awaiting CONFIRMATION').
    Returns a list of (row, live_or_None) pairs for genuine chase candidates only.

    CHG-2A: the bounded live enrich is delegated to centre_of_truth.reconcile_ready;
    the drop-a-completed-legacy-row decision stays here (it is Pane-2's semantic,
    not a property of the reconcile helper).
    """
    def _fetch(r):
        return live_order_info(token_h, r["gid"]) if r.get("gid") else None

    prepped = reconcile_ready(rows, _fetch, key="gid", token_h=token_h, cap=200)
    out = []
    for r, live in prepped:
        if live is None:
            out.append((r, None))
            continue
        if live.get("completed"):
            continue  # legacy/completed task — drop from the chase list
        if live.get("live_status") and not _is_chaseable(live.get("live_status")):
            continue  # live status now arrived / held / cancelled
        out.append((r, live))
    return out


def confirmations_html(prepped, email_map):
    """Render confirmation rows from a pre-enriched list (row, live). No live fetch
    here — main() already filtered completed/legacy tasks."""
    if not prepped:
        return "<p class=muted>No live outstanding confirmations right now.</p>"
    head = ("<tr><th>Order</th><th>Vendor</th><th>Ref</th>"
            "<th>Due</th><th>Status</th><th>Email on file</th></tr>")
    body = []
    for r, live in prepped:
        vendor = esc(r["vendor"])
        po = r["po"] or r["job"] or ""
        # CHG-2A: live-first with DB fallback + unreconciled flag (shared helper).
        status_shown, unrec = render_pref((live or {}).get("live_status"), r["status"])
        due, _unrec_due = render_pref((live or {}).get("due"), r["due"])
        due_txt = (due or "")[:10]
        badge = "over" if status_shown and "CONFIRMATION" in status_shown else ""
        em = esc(email_map.get((r["vendor"] or "").lower(), "—"))
        lbl = (live and live.get("name")) or r["task_name"] or ("PO " + po)
        # Canonical Asana task deep link (org segment required to resolve).
        href = f"{ASANA_TASK_BASE}/{r['gid']}" if r.get("gid") else ""
        order_url = f'<a href="{esc(href)}" target="_blank">{esc((lbl or "")[:80])}</a>'
        ref = f'<a href="{esc(href)}" target="_blank">{esc(po) or "—"}</a>' if po else "—"
        body.append(
            "<tr class=\"{bad}\"><td>{order_url}</td>"
            "<td>{vendor}</td>"
            "<td>{ref}</td>"
            "<td>{due}</td>"
            "<td>{status_shown}{flag}</td>"
            "<td>{em}</td></tr>".format(
                bad=badge, order_url=order_url, vendor=vendor,
                ref=ref, due=esc(due_txt), status_shown=esc(status_shown),
                flag=flag_span(unrec), em=em))
    return "<table>" + head + "".join(body) + "</table>"


def chase_html(rows, cur_status):
    # cur_status: po_number -> current order_status (None if not found). Skip any
    # PO whose order has since arrived / been cancelled / held — no point chasing.
    if not rows:
        return "<p class=muted>No overdue chase drafts on record.</p>"
    head = "<tr><th>Order ref</th><th>Current status</th><th>Vendor</th><th>Due</th><th>Certainty</th><th>To</th><th>Draft</th></tr>"
    body = []
    for po, vendor, due, cert, to, status, link, subj, created in rows:
        if po:
            live = cur_status.get(str(po).strip())
            if live and not _is_chaseable(live):
                continue  # order resolved since the draft — do not show as chaseable
        link_html = f'<a href="{esc(link)}" target="_blank">draft</a>' if link else "—"
        cst = (cur_status.get(str(po).strip()) if po else None) or status or ""
        body.append(f"<tr><td>{esc(po or '')}</td><td>{esc((cst or '')[:45]) or '—'}</td>"
                    f"<td>{esc(vendor or '')}</td>"
                    f"<td>{esc(due or '')}</td><td>{esc(cert or '')}</td>"
                    f"<td>{esc(to or '—')}</td>"
                    f"<td>{link_html}</td></tr>")
    return f"<table>{head}{''.join(body)}</table>"


def suppliers_no_email_html(no_email):
    """Standalone section (rendered at the very bottom) for Giles to fill in."""
    if not no_email:
        return "<p class=muted>No suppliers missing a contact email on file.</p>"
    head = "<tr><th>Supplier</th><th>Phone</th></tr>"
    body = "".join(
        f"<tr><td>{esc(n or '')}</td><td>{esc(p or '—')}</td></tr>"
        for n, e, p in no_email)
    return f"<h2>Suppliers with no email on file ({len(no_email)})</h2>" \
           "<p>To chase these orders by email we need a contact. Add to the supplier record — I'll pick it up on the next refresh.</p>" \
           + "<table>" + head + body + "</table>"


def unmapped_html(matches):
    out = []
    head = "<tr><th>Received</th><th>Subject</th><th>Supplier</th><th>PO</th><th>Matched</th><th>Confidence</th><th>Comment?</th></tr>"
    body = []
    for subj, rec, sup, po, task, conf, cw, vc in matches:
        if vc == 1:
            continue  # verified correct -> not unmapped
        if conf == "exact":
            continue
        body.append(f"<tr><td>{esc(rec or '')}</td><td>{esc((subj or '')[:60])}</td>"
                    f"<td>{esc(sup or '')}</td><td>{esc(po or '—')}</td>"
                    f"<td>{esc((task or '')[:45]) or '—'}</td>"
                    f"<td>{esc(conf or '—')}</td><td>{'✓' if cw else '—'}</td></tr>")
    if body:
        out.append(f"<h4>Unmapped emails</h4>{head}{''.join(body)}</table>")
    else:
        out.append("<p class=muted>No unmapped emails.</p>")
    return "\n".join(out)


_LEDGER_STATUS_LABEL = {
    "grouped": "Grouped — awaiting order",
    "ordered": "Ordered — sent to supplier",
}


def _order_status_label(raw):
    r = (raw or "").strip()
    return _LEDGER_STATUS_LABEL.get(r, r or "—")


def email_ledger_html(prepped, email_map):
    # Slender per-item view: task, qty, stage. Part number is omitted per Giles —
    # it's inside the task name and adds clutter.
    if not prepped:
        return "<p class=muted>No ordering-queue rows.</p>"
    head = ("<tr><th>Item (task)</th><th>Vendor</th><th>Qty</th>"
            "<th>Stage</th><th>Ordered</th><th>Order task · due</th><th>Email on file</th></tr>")
    body = []
    for row, live in prepped:
        task_name = row[_O_TASK]
        vendor = row[_O_VENDOR]
        qty = row[_O_QTY]
        status = row[_O_STATUS]
        ordered_at = row[_O_ORDERED]
        em = email_map.get((vendor or "").lower(), "—")
        # CHG-2B: associated order task + due (live-reconciled, DB+flag fallback).
        order_cell, unrec = _order_task_cell(live)
        body.append(f"<tr><td>{esc((task_name or '')[:60])}</td>"
                    f"<td>{esc(vendor or '')}</td><td class=n>{qty or '—'}</td>"
                    f"<td>{esc(_order_status_label(status))}</td>"
                    f"<td>{esc((ordered_at or '')[:10])}</td>"
                    f"<td>{order_cell}{flag_span(unrec)}</td>"
                    f"<td>{esc(em)}</td></tr>")
    return (f"<p class=muted>Stage: <b>Grouped — awaiting order</b> (not yet sent to "
            f"supplier) vs <b>Ordered — sent to supplier</b>. Once lines are placed "
            f"into a purchase order they get a due date shown against that order.</p>"
            + f"<table>{head}{''.join(body)}</table>")


# ── page shell ───────────────────────────────────────────────────────────────
def render(panes, today):
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SAS Ops Oversight — {today}</title>
<style>
:root {{ --bg:#0f172a; --ink:#e2e8f0; --muted:#94a3b8; --panel:#1e293b;
        --line:#334155; --acc:#38bdf8; --good:#4ade80; --bad:#f87171; --warn:#fbbf24; }}
body {{ margin:0; font-family:Segoe UI,system-ui,sans-serif; background:var(--bg); color:var(--ink); }}
.wrap {{ max-width:1400px; margin:0 auto; padding:24px; }}
h1 {{ font-size:22px; }} h2 {{ font-size:18px; border-bottom:1px solid var(--line);
      padding-bottom:6px; color:var(--acc); margin-top:34px; }}
h4 {{ color:var(--warn); margin-bottom:6px; }}
table {{ border-collapse:collapse; width:100%; margin:8px 0 18px; font-size:13px; }}
th,td {{ border:1px solid var(--line); padding:5px 8px; text-align:left; vertical-align:top; }}
th {{ background:#111c31; color:var(--muted); font-weight:600; position:sticky; top:0; }}
tr.over td {{ background:#3b1616; }}
td.n {{ text-align:right; }} th.n {{ text-align:right; }}
a {{ color:var(--acc); text-decoration:none; }} a:hover {{ text-decoration:underline; }}
.muted {{ color:var(--muted); font-style:italic; }}
.meta {{ color:var(--muted); font-size:12px; }}
.pill {{ display:inline-block; padding:1px 8px; border-radius:10px; margin:2px 2px; font-size:11px; }}
.unrec {{ display:inline-block; margin-left:4px; padding:0 6px; border-radius:8px; background:#3b1616; color:var(--warn); font-size:10px; }}
.back {{ position:fixed; top:14px; right:16px; z-index:999; background:#0f172a; color:#fff;
        border:1px solid var(--line); border-radius:20px; padding:6px 14px; text-decoration:none; }}
</style></head>
<body><div class="wrap">
<a class="back" href="nav.html">&larr; SAS OS portal</a>
<h1>📊 SAS Ops Oversight &amp; QA</h1>
<p class="meta">Read-only aggregate. Last generated: {today} UTC · Every row links to its Asana source · Nothing writes to Asana or the DB.</p>
{panes}
</div></body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(WS / "data" / "os_status"))
    args = ap.parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    token_h = None
    try:
        token_h = headers()
    except Exception:
        token_h = None

    conn = sqlite3.connect(OPS_DB)
    today = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M")

    conf_rows = load_confirmation_rows(conn)
    matches = load_email_matches(conn)
    no_email = load_suppliers_without_email(conn)
    chase = load_chase_state(conn)
    ordering, ordering_counts = load_ordering(conn)
    email_map = load_supplier_email_map(conn)

    # Deep-filter confirmations against Asana (drop completed legacy tasks).
    prepped_conf = confirmations_render_ready(conf_rows[:200], token_h)

    # CHG-2B: enrich ordering-ledger rows with their associated order task + due
    # date via a bounded live reconcile (DB+flag fallback on failure).
    prepped_ordering = reconcile_ready(
        ordering, lambda r: live_order_task_for_ordering(r, token_h),
        key=_O_GID, token_h=token_h, cap=200)

    # po_number -> current order_status, to hide arrived/held/cancelled from chases
    cur_status = {str(po).strip(): st for po, st in conn.execute(
        "SELECT po_number, order_status FROM procurement_tasks "
        "WHERE po_number IS NOT NULL AND po_number != ''")}

    # Panes — all survivors are genuinely outstanding after the live check above.
    p1 = (f"<h2>1 · Awaiting supplier CONFIRMATION — to chase ({len(prepped_conf)})</h2>"
          "<p>Live check against Asana — completed legacy orders are excluded, as are "
          "ARRIVED / HOLD / CANCELLED. Only genuinely open orders are listed.</p>"
          f"{confirmations_html(prepped_conf, email_map)}")
    p2 = f"<h2>2 · Emails to chase / answered</h2>{chase_html(chase, cur_status)}"
    p3 = (f"<h2>3 · Ordering ledger ({len(ordering)} lines, by vendor)</h2>"
          f"{email_ledger_html(prepped_ordering, email_map)}")
    p4 = f"<h2>4 · Unmapped emails</h2>{unmapped_html(matches)}"
    p5 = suppliers_no_email_html(no_email)

    panes = p1 + p2 + p3 + p4 + p5
    html = render(panes, today)

    out_html = out_dir / "ops-oversight.html"
    out_html.write_text(html, encoding="utf-8")
    dated = out_dir / f"ops-oversight-{datetime.datetime.utcnow().strftime('%Y%m%d_%H%M')}.html"
    dated.write_text(html, encoding="utf-8")
    print(f"OK wrote {out_html} ({len(html)} bytes) + dated copy")
    print(f"[pane counts] confirmations={len(conf_rows)} chase={len(chase)} "
          f"matches={len(matches)} no_email={len(no_email)} ordering={len(ordering)} "
          f"ordered={ordering_counts.get('ordered',0)} grouped={ordering_counts.get('grouped',0)}")
    conn.close()


if __name__ == "__main__":
    main()
