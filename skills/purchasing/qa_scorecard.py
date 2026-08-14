#!/usr/bin/env python3
"""
qa_scorecard.py — Parser QA Scorecard for the purchasing email pipeline.

Reads the 'Processed' tab of the Google Sheet (ground-truth history), computes
match-accuracy metrics (TP/FP/TN/FN, precision, recall, F1) stratified by
document type and sender domain, codes error classes, surfaces the most
actionable FP/FN rows and thin-L rows, and emits a self-contained HTML report.

Ground-truth columns (Processed tab):
  E = Document Type   G = Matched Task   K = DECISION (A/M/R)
  L = Amendment Notes  H = Asana Link     N = Outcome

SCOPE / DECISIONS (2026-08-12, per Giles):
  - Weekly driven (cron). Reuse A/M/R + L as labels. Surface insight so Giles
    enriches L over time. Output = portable HTML (inline CSS, no external deps)
    for later portal upload.

Classification model (label = Giles's decision; G = matcher output):
  TP  matched AND (A or M)                 -> proposed match accepted/correct
  FP  matched AND R                        -> proposed match rejected (wrong task / noise leak)
  TN  NOT MATCHED AND R                    -> correctly filtered
  FN  NOT MATCHED AND (A or M)             -> matcher missed; Giles acted anyway

AUTHOR: Hermes (email processing). Built 2026-08-12.
"""

import argparse
import datetime as dt
import html
import json
import os
import re
from collections import Counter, defaultdict

import gspread

SHEET_ID = "1fvoSKnvH0vdM_aZbo2_seY6AU3SZaXABaxRUKSxWVlo"
SERVICE_ACCOUNT_PATH = r"C:\Users\GilesHugo\.hermes\Google_Service_Account.json"
PROCESSED_TAB = "Processed"

OUT_DIR = r"C:\Users\GilesHugo\SES_Workspace\data\qa"

# Column indexes (1-based -> 0-based): A=0 ... N=13
COL = {
    "email_id": 0, "received": 1, "subject": 2, "sender_domain": 3,
    "doc_type": 4, "po_number": 5, "matched_task": 6, "asana_link": 7,
    "proposed_action": 8, "issues": 9, "decision": 10, "amendment_notes": 11,
    "processed_at": 12, "outcome": 13,
}

# Error taxonomy heuristics — keyed on subject + matched task + amendment.
def classify_error(subject, sender_domain, matched_task, amendment):
    """Return a list of error-class tags for an FP/FN row (best-effort)."""
    tags = []
    s = (subject or "").lower()
    a = (amendment or "").lower()
    g = (matched_task or "").lower()
    # auto-reply / noise
    if re.search(r"auto-?reply|automatic reply|out of office|do not reply|noreply|no-reply", s):
        tags.append("auto-reply/noreply leakage")
    # vendor-name inconsistency
    if re.search(r"\b(thameside|t allen|metals ?4u|amazon)\b", s) and g and g != "not matched":
        pass  # heuristic too weak to tag reliably; surfaced for manual triage
    # project-name-vs-number: task matched but email named project (job not in task)
    if re.search(r"\b(st mary|st\.? mary|jenners|fifth ave|russell sq|manchester)\b", s):
        tags.append("project-name reference (job number not literal)")
    # subtask / description / dependency target hints in amendment
    if re.search(r"\bsubtask\b|\bdepends\b|\bdependency\b|\bdescription\b", a):
        tags.append("task found via description/dependency (subtask target)")
    # normalisation (spaced/hyphen refs)
    if re.search(r"(sqr\s|-?\d{4,})", s.lower()):
        tags.append("reference-normalisation candidate")
    if not tags:
        tags.append("manual triage")
    return tags


def gstat(matched_task):
    g = (matched_task or "").strip()
    if not g or g.upper() == "NOT MATCHED":
        return "NOTMATCHED"
    return "MATCHED"


def dec_class(decision):
    d = (decision or "").strip().upper()
    return d if d in ("A", "M", "R") else "?"


def read_rows():
    gc = gspread.service_account(filename=SERVICE_ACCOUNT_PATH)
    ws = gc.open_by_key(SHEET_ID).worksheet(PROCESSED_TAB)
    vals = ws.get_all_values()
    return [r for r in vals[1:] if any((cell or "").strip() for cell in r)]


def compute(data):
    """Return metrics dict + per-row classification + by-doc/domain breakdown."""
    rows = []
    agg = Counter()            # class -> count
    by_doc = defaultdict(Counter)
    by_domain = defaultdict(Counter)
    by_doc_tot = Counter()
    by_domain_tot = Counter()

    for r in data:
        cls = None
        g = gstat(r[COL["matched_task"]] if len(r) > COL["matched_task"] else "")
        k = dec_class(r[COL["decision"]] if len(r) > COL["decision"] else "")
        doc = (r[COL["doc_type"]] if len(r) > COL["doc_type"] else "").strip() or "?"
        dom = (r[COL["sender_domain"]] if len(r) > COL["sender_domain"] else "").strip() or "?"

        if k in ("A", "M") and g == "MATCHED":
            cls = "TP"
        elif k == "R" and g == "MATCHED":
            cls = "FP"
        elif k == "R" and g == "NOTMATCHED":
            cls = "TN"
        elif k in ("A", "M") and g == "NOTMATCHED":
            cls = "FN"
        elif k == "?":
            cls = "NOLABEL"
        else:
            cls = "AMBIG"

        agg[cls] += 1
        by_doc[doc][cls] += 1
        by_doc_tot[doc] += 1
        by_domain[dom][cls] += 1
        by_domain_tot[dom] += 1

        rows.append({"row": r, "class": cls, "doc": doc, "domain": dom})

    tp, fp, tn, fn = agg["TP"], agg["FP"], agg["TN"], agg["FN"]
    prec = tp / (tp + fp) if (tp + fp) else None
    rec = tp / (tp + fn) if (tp + fn) else None
    f1 = 2 * prec * rec / (prec + rec) if (prec and rec) else None

    metrics = {
        "total": len(data), "TP": tp, "FP": fp, "TN": tn, "FN": fn,
        "precision": prec, "recall": rec, "f1": f1,
        "by_doc": {d: {"tot": by_doc_tot[d], **dict(c)} for d, c in by_doc.items()},
        "by_domain": {d: {"tot": by_domain_tot[d], **dict(c)} for d, c in by_domain.items()},
    }
    return metrics, rows


def enrich_rows(rows):
    """Attach error tags + surface flags to FP/FN/TN rows for the report."""
    enriched = []
    for rec in rows:
        r = rec["row"]
        cls = rec["class"]
        subject = r[COL["subject"]] if len(r) > COL["subject"] else ""
        matched = r[COL["matched_task"]] if len(r) > COL["matched_task"] else ""
        amendment = r[COL["amendment_notes"]] if len(r) > COL["amendment_notes"] else ""
        link = r[COL["asana_link"]] if len(r) > COL["asana_link"] else ""
        rec["subject"] = subject
        rec["matched"] = matched
        rec["amendment"] = amendment
        rec["link"] = link
        # flags
        rec["surfaced"] = False
        if cls in ("FP", "FN"):
            rec["tags"] = classify_error(subject, rec["domain"], matched, amendment)
            rec["surfaced"] = True
        if cls in ("TP",) and len(amendment.strip()) < 3:
            rec["thin_l"] = True  # approved but no amendment detail -> prompt Giles
        else:
            rec["thin_l"] = False
        enriched.append(rec)
    return enriched


# ---------------- HTML REPORT ----------------

def htmlesc(s):
    return html.escape(s or "")


def fmt_pct(x):
    return f"{x*100:.1f}%" if x is not None else "n/a"


def render_html(metrics, rows, generated_at):
    now = generated_at
    tp, fp, tn, fn = metrics["TP"], metrics["FP"], metrics["TN"], metrics["FN"]
    tol = metrics["total"]

    blocks = []
    blocks.append(f"<h1>Purchasing Pipeline — Parser QA Scorecard</h1>")
    blocks.append(f"<p class='meta'>Generated {now.strftime('%Y-%m-%d %H:%M')} · {tol} processed emails · source: <code>Processed</code> tab</p>")

    # Headline metrics
    blocks.append("<div class='card'><h2>Headline</h2><table>")
    blocks.append("<tr><th>Metric</th><th>Value</th></tr>")
    blocks.append(f"<tr><td>True Positives (match accepted)</td><td>{tp}</td></tr>")
    blocks.append(f"<tr><td>False Positives (match rejected)</td><td>{fp}</td></tr>")
    blocks.append(f"<tr><td>True Negatives (correctly filtered)</td><td>{tn}</td></tr>")
    blocks.append(f"<tr><td>False Negatives (missed match)</td><td>{fn}</td></tr>")
    blocks.append(f"<tr><td><b>Precision</b> TP/(TP+FP)</td><td><b>{fmt_pct(metrics['precision'])}</b></td></tr>")
    blocks.append(f"<tr><td><b>Recall</b> TP/(TP+FN)</td><td><b>{fmt_pct(metrics['recall'])}</b></td></tr>")
    blocks.append(f"<tr><td><b>F1</b></td><td><b>{metrics['f1']:.3f}</b></td></tr>")
    blocks.append("</table>")
    blocks.append("<p class='note'>Precision = % of proposed matches you actually accepted (low = over-proposing/wrong tasks). "
                  "Recall = % of actionable mail successfully matched (low = missing real quotes/orders). "
                  "'Matched + Rejected' conflates wrong-match with rejected-action — surfaced rows let us tell them apart.</p></div>")

    # By document type
    blocks.append("<div class='card'><h2>By Document Type</h2><table><tr><th>Type</th><th>Total</th>"
                  "<th>TP</th><th>FP</th><th>TN</th><th>FN</th><th>Precision</th><th>Recall</th></tr>")
    for doc, d in sorted(metrics["by_doc"].items()):
        tot = d["tot"]
        tp_, fp_, tn_, fn_ = d.get("TP", 0), d.get("FP", 0), d.get("TN", 0), d.get("FN", 0)
        prec = tp_ / (tp_ + fp_) if (tp_ + fp_) else None
        rec = tp_ / (tp_ + fn_) if (tp_ + fn_) else None
        blocks.append(f"<tr><td>{htmlesc(doc)}</td><td>{tot}</td><td>{tp_}</td><td>{fp_}</td>"
                      f"<td>{tn_}</td><td>{fn_}</td><td>{fmt_pct(prec)}</td><td>{fmt_pct(rec)}</td></tr>")
    blocks.append("</table></div>")

    # By domain (top 15)
    blocks.append("<div class='card'><h2>By Sender Domain (top 15)</h2><table><tr><th>Domain</th><th>Total</th>"
                  "<th>TP</th><th>FP</th><th>TN</th><th>FN</th></tr>")
    for dom in sorted(metrics["by_domain"], key=lambda d: metrics["by_domain"][d]["tot"], reverse=True)[:15]:
        d = metrics["by_domain"][dom]
        blocks.append(f"<tr><td>{htmlesc(dom)}</td><td>{d['tot']}</td><td>{d.get('TP',0)}</td>"
                      f"<td>{d.get('FP',0)}</td><td>{d.get('TN',0)}</td><td>{d.get('FN',0)}</td></tr>")
    blocks.append("</table></div>")

    # Surfaces: FP/FN rows
    surfaced = [r for r in rows if r["surfaced"]]
    surfaced.sort(key=lambda r: (r["class"], r["subject"] or ""))
    blocks.append("<div class='card'><h2>Surfaced — False Positives &amp; False Negatives (review &amp; enrich)</h2>")
    if not surfaced:
        blocks.append("<p>No FP/FN rows in this sample.</p>")
    else:
        blocks.append("<table><tr><th>Class</th><th>Doc</th><th>Subject</th><th>Matched</th><th>Tag(s)</th><th>Link</th></tr>")
        for r in surfaced[:60]:
            link = r["link"] or r["row"][COL["asana_link"]] if len(r["row"]) > COL["asana_link"] else ""
            lh = f"<a href='{htmlesc(link)}'>Asana</a>" if link else "—"
            blocks.append(f"<tr><td>{r['class']}</td><td>{htmlesc(r['doc'])}</td>"
                          f"<td class='subj'>{htmlesc((r['subject'] or '')[:70])}</td>"
                          f"<td class='subj'>{htmlesc((r['matched'] or '')[:45])}</td>"
                          f"<td>{htmlesc('; '.join(r.get('tags', [])))}</td><td>{lh}</td></tr>")
        blocks.append("</table>")
        if len(surfaced) > 60:
            blocks.append(f"<p class='note'>…and {len(surfaced)-60} more.</p>")
    blocks.append("</div>")

    # Thin-L callout
    thin = [r for r in rows if r.get("thin_l")]
    blocks.append("<div class='card'><h2>Thin amendment notes (approved but no detail — please enrich)</h2>")
    if not thin:
        blocks.append("<p>No thin-L rows.</p>")
    else:
        blocks.append("<table><tr><th>Doc</th><th>Subject</th></tr>")
        for r in thin[:20]:
            blocks.append(f"<tr><td>{htmlesc(r['doc'])}</td><td class='subj'>{htmlesc((r['subject'] or '')[:70])}</td></tr>")
        blocks.append("</table>")
        if len(thin) > 20:
            blocks.append(f"<p class='note'>…and {len(thin)-20} more.</p>")
    blocks.append("</div>")

    css = """
    body{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#222;max-width:1100px}
    h1{color:#0b3d66;border-bottom:2px solid #0b3d66;padding-bottom:8px}
    h2{color:#0b3d66;margin-top:6px}
    .meta{color:#666;font-style:italic}
    .card{background:#f7f9fb;border:1px solid #dde3ea;border-radius:8px;padding:14px 18px;margin:16px 0}
    table{border-collapse:collapse;width:100%;font-size:14px}
    th,td{border:1px solid #ccd4dc;padding:6px 8px;text-align:left;vertical-align:top}
    th{background:#0b3d66;color:#fff}
    tr:nth-child(even) td{background:#eef2f6}
    .subj{font-family:Consolas,monospace;font-size:12.5px}
    .note{color:#555;font-size:13px;margin-top:6px}
    """
    return ("<!DOCTYPE html><html><head><meta charset='utf-8'><title>Parser QA Scorecard</title>"
            f"<style>{css}</style></head><body>" + "\n".join(blocks) + "</body></html>")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None, help="Output HTML path. Default: data/qa/qa_report_<date>.html")
    ap.add_argument("--print", action="store_true", help="Also print headline to stdout")
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    data = read_rows()
    if not data:
        print("No data in Processed tab.")
        return

    metrics, rows = compute(data)
    rows = enrich_rows(rows)
    generated = dt.datetime.now()

    # most-recent (or today) filename + keep a stable 'latest.html' for portal
    stamp = generated.strftime("%Y%m%d_%H%M")
    out = args.out or os.path.join(OUT_DIR, f"qa_report_{stamp}.html")
    html_doc = render_html(metrics, rows, generated)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html_doc)
    with open(os.path.join(OUT_DIR, "latest.html"), "w", encoding="utf-8") as f:
        f.write(html_doc)

    if args.print:
        t = metrics
        prec = fmt_pct(t["precision"]); rec = fmt_pct(t["recall"])
        print(f"QA Scorecard — {t['total']} rows · P={prec} R={rec} F1={t['f1']:.3f} "
              f"(TP={t['TP']} FP={t['FP']} TN={t['TN']} FN={t['FN']})")
        print(f"Report written: {out}")
        print(f"  also: {os.path.join(OUT_DIR, 'latest.html')}")


if __name__ == "__main__":
    main()
