"""
learn.py — the feedback loop. Turns Giles' sheet decisions into rules the matcher uses.

Called by 05_sheet_approver.py after every batch (apply_feedback), and runnable
by hand:
    python learn.py --backfill            # mine the Processed tab history (37+ amendment notes)
    python learn.py --backfill-xlsx F.xlsx  # same, from an exported copy (offline)
    python learn.py --publish             # write data/gateway_rules.json to the Rules tab
    python learn.py --scorecard [--weeks 8]  # match-rate / accept-rate trend from Processed
    python learn.py --show                # print the rules

What a decision teaches (all rules are visible + editable in the Rules tab and
data/gateway_rules.json; nothing here writes to Asana):

  corrected Asana Link (differs from Proposed Task GID, or was NOT MATCHED)
      email  → sender_task   : that sender's next mails go to that task (90 days)
      call   → phone_task    : that phone number's next calls go to that task (90 days)
             + phone_supplier / label_supplier when the task's vendor is a known supplier
      any    → domain_supplier when the sender domain is not in the supplier directory
  R + feedback saying noise / ignore / stop / tracking / marketing
             → noise_domains (never for a domain that is in the supplier directory)
  feedback saying closed / wrong task / already arrived / cancelled
             → blocked_tasks[proposed task]
  any other Feedback / Amendment text → kept as a note (searchable, shown in Rules tab)
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))

from gateway_common import (FEEDBACK_LOG, RuleStore, SupplierDirectory, load_json,  # noqa: E402
                            normalise_phone, parse_ringcentral)

RULE_TTL_DAYS = 90
GENERIC_DOMAINS = {"gmail.com", "outlook.com", "hotmail.com", "yahoo.com", "icloud.com", "ringcentral.com"}
# Vendors with many concurrent orders: a domain→task rule would be wrong more often than right.
MULTI_ORDER_DOMAINS = {"amazon.co.uk", "amazon.com", "ebay.com", "ebay.co.uk", "members.ebay.com", "rswww.com",
                       "rs-components.com", "toolstation.com", "screwfix.com", "cromwell.co.uk", "farnell.com"}
NOISE_WORDS = re.compile(r"\b(noise|no action|ignore|stop (coming|sending)|unsubscribe|tracking|marketing|newsletter|spam|junk|not (a |an )?(supplier|order)|nothing to do)\b", re.I)
BLOCK_WORDS = re.compile(r"\b(closed(?: out)?|wrong task|already (arrived|closed|delivered|received)|cancelled|cencelled|this task has been closed|not the correct task)\b", re.I)
ASANA_URL = re.compile(r"https?://app\.asana\.com/\S+")


def _gid_from_url(u: str) -> str | None:
    if not u:
        return None
    u = u.split("?")[0].rstrip("/")
    parts = u.split("/")
    if "task" in parts and parts.index("task") + 1 < len(parts) and parts[parts.index("task") + 1].isdigit():
        return parts[parts.index("task") + 1]
    return parts[-1] if parts[-1].isdigit() else None


def _until(days: int = RULE_TTL_DAYS) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%d")


def _task_vendor(gid: str) -> tuple[str, str]:
    """(vendor_name, task_name) from ops.db if reachable, else ('','')."""
    try:
        from matcher import Matcher
        m = Matcher()
        c = m._by_gid(gid) if m.conn else None
        return (c.vendor, c.name) if c else ("", "")
    except Exception:
        return "", ""


def learn_from_record(rec: dict, rules: RuleStore, suppliers: SupplierDirectory) -> list[str]:
    """Apply one feedback record; return human-readable list of what was learned."""
    learned: list[str] = []
    src = f"{rec.get('received', '')[:10]} {rec.get('subject', '')[:60]}".strip()
    text = " ".join(x for x in (rec.get("feedback"), rec.get("amendment_notes")) if x)
    domain = (rec.get("sender_domain") or "").lower()
    sender = (rec.get("sender") or "").lower()
    if "<" in sender:
        m = re.search(r"[\w.+-]+@[\w.-]+", sender)
        sender = m.group(0) if m else sender
    proposed_gid = rec.get("proposed_task_gid") or ""
    final_gid = rec.get("final_task_gid") or ""
    if not final_gid and text:  # a link pasted into the notes counts as a correction
        m = ASANA_URL.search(text)
        final_gid = _gid_from_url(m.group(0)) if m else ""
    corrected = bool(final_gid) and (final_gid != proposed_gid) and rec.get("decision", "").upper() in ("A", "M")
    is_call = domain == "ringcentral.com"

    # 1. corrected task → sender/phone → task
    if corrected:
        note = f"Giles: {text[:120]}" if text else "Giles corrected the task link"
        vendor, tname = _task_vendor(final_gid)
        sup_gid = None
        if vendor or tname:
            hits = suppliers.by_label(vendor or tname.split(" - ")[1] if " - " in tname else vendor)
            if hits and hits[0][1] >= 0.7:
                sup_gid = hits[0][0].get("gid")
        if is_call:
            rc = parse_ringcentral(rec.get("subject", ""), "")
            for ph in rc.get("phones") or []:
                if rules.put("phone_task", ph, {"task_gid": final_gid, "note": note, "until": _until(), "learned_from": src}):
                    learned.append(f"calls from {ph} → task {final_gid}")
                if sup_gid and rules.put("phone_supplier", ph, {"supplier_gid": sup_gid, "label": rc.get("contact_label"), "learned_from": src}):
                    learned.append(f"{ph} → supplier {SupplierDirectory.short_name(suppliers.by_gid[sup_gid])}")
            lab = (rc.get("contact_label") or "").strip()
            if lab and sup_gid and rules.put("label_supplier", lab, {"supplier_gid": sup_gid, "learned_from": src}):
                learned.append(f"label '{lab}' → supplier {SupplierDirectory.short_name(suppliers.by_gid[sup_gid])}")
        elif sender:
            if rules.put("sender_task", sender, {"task_gid": final_gid, "note": note, "until": _until(), "learned_from": src}):
                learned.append(f"mail from {sender} → task {final_gid}")
        elif domain and domain not in GENERIC_DOMAINS and domain not in MULTI_ORDER_DOMAINS:
            # no sender address on record (older sheet rows) — the domain is the best key we have
            if rules.put("domain_task", domain, {"task_gid": final_gid, "note": note, "until": _until(), "learned_from": src}):
                learned.append(f"mail from @{domain} → task {final_gid}")
        if sup_gid and domain and not suppliers.by_email_domain(domain) and domain not in GENERIC_DOMAINS:
            if rules.put("domain_supplier", domain, {"supplier_gid": sup_gid, "learned_from": src}):
                learned.append(f"domain {domain} → supplier {SupplierDirectory.short_name(suppliers.by_gid[sup_gid])}")

    # 2. noise
    if rec.get("decision", "").upper() == "R" and text and NOISE_WORDS.search(text) and domain:
        if suppliers.by_email_domain(domain) or is_call:
            rules.add_note(f"[{domain}] R: {text[:140]}", src)
        elif rules.put("noise_domains", domain, {"reason": text[:140], "learned_from": src}):
            learned.append(f"{domain} → noise ({text[:40]})")

    # 3. blocked task
    if text and BLOCK_WORDS.search(text) and proposed_gid and proposed_gid != final_gid:
        if rules.put("blocked_tasks", proposed_gid, {"reason": text[:140], "learned_from": src}):
            learned.append(f"task {proposed_gid} blocked ({text[:40]})")

    # 4. everything else Giles wrote is kept
    if text and not learned:
        if rules.add_note(f"[{domain or 'call'}] {rec.get('decision', '')}: {text[:200]}", src):
            learned.append("note kept")
    return learned


def apply_feedback(records: list[dict]) -> dict[str, list[str]]:
    """Entry point for 05. Returns {email_id: [what was learned]} and saves the rules."""
    rules = RuleStore()
    suppliers = SupplierDirectory(rules=rules)
    out = {}
    changed = False
    for rec in records:
        got = learn_from_record(rec, rules, suppliers)
        out[rec.get("email_id", "")] = got
        changed = changed or bool(got)
    if changed:
        rules.save()
        try:
            publish_rules(rules)
        except Exception as e:  # sheet publishing is a nicety; the JSON is the source of truth
            print(f"  (Rules tab not updated: {e})")
    return out


# ------------------------------------------------------------------ backfill
def _rows_from_sheet():
    from sheet_common import PROCESSED_HEADERS, PROCESSED_TAB, Tab, get_spreadsheet
    return Tab(get_spreadsheet(), PROCESSED_TAB, PROCESSED_HEADERS).read_rows()


def _rows_from_xlsx(path: str):
    import openpyxl
    ws = openpyxl.load_workbook(path)["Processed"]
    hdr = [(c.value or "").strip() if isinstance(c.value, str) else "" for c in ws[1]]
    out = []
    for r in ws.iter_rows(min_row=2, values_only=True):
        if not any(r):
            continue
        d = {hdr[i]: ("" if r[i] is None else str(r[i])) for i in range(min(len(hdr), len(r))) if hdr[i]}
        out.append(d)
    return out


def _record_from_processed_row(row: dict) -> dict:
    dec = (row.get("DECISION") or "").strip().upper()[:1]
    return {
        "email_id": row.get("Email ID", ""), "received": row.get("Received", ""), "subject": row.get("Subject", ""),
        "sender": row.get("Sender", ""), "sender_domain": row.get("Sender Domain", ""),
        "doc_type": row.get("Document Type", ""), "decision": dec,
        "proposed_task_gid": row.get("Proposed Task GID", "") or _gid_from_url(row.get("Asana Link", "")) or "",
        "final_task_gid": row.get("Final Task GID", ""),
        "amendment_notes": row.get("Amendment Notes", ""), "feedback": row.get("Feedback", ""),
    }


def backfill(rows: list[dict]) -> None:
    rules = RuleStore()
    suppliers = SupplierDirectory(rules=rules)
    n = 0
    for row in rows:
        rec = _record_from_processed_row(row)
        if not (rec["amendment_notes"] or rec["feedback"] or rec["final_task_gid"]):
            continue
        got = learn_from_record(rec, rules, suppliers)
        if got:
            n += 1
            print(f"  {rec['received'][:10]} {rec['subject'][:50]!r}: " + "; ".join(got))
    rules.save()
    print(f"Backfill: learned from {n} row(s). Rules v{rules.data['version']} saved to {rules.path}")


# ------------------------------------------------------------------ publish
def rules_as_rows(rules: RuleStore) -> list[dict]:
    rows = []
    for bucket, target_key in (("sender_task", "task_gid"), ("phone_task", "task_gid"), ("domain_task", "task_gid"),
                               ("domain_supplier", "supplier_gid"), ("phone_supplier", "supplier_gid"),
                               ("label_supplier", "supplier_gid"), ("blocked_tasks", None), ("noise_domains", None)):
        for k, v in sorted(rules.data.get(bucket, {}).items()):
            rows.append({"Type": bucket, "Key": k,
                         "Maps To": (v.get(target_key) if target_key else v.get("reason", ""))
                                    if isinstance(v, dict) else "",
                         "Note": (v.get("note") or v.get("label") or v.get("reason") or "") if isinstance(v, dict) else "",
                         "Learned From": v.get("learned_from", "") if isinstance(v, dict) else "",
                         "Learned At": (v.get("learned_at", "") + (f" (until {v['until']})" if v.get("until") else "")) if isinstance(v, dict) else "",
                         "Active": "yes"})
    for p in rules.data.get("noise_subject_patterns", []):
        rows.append({"Type": "noise_subject_pattern", "Key": p.get("pattern", ""), "Maps To": p.get("reason", ""),
                     "Note": "", "Learned From": p.get("learned_from", ""), "Learned At": p.get("learned_at", ""), "Active": "yes"})
    for nte in rules.data.get("notes", []):
        rows.append({"Type": "note", "Key": "", "Maps To": "", "Note": nte.get("text", ""),
                     "Learned From": nte.get("source", ""), "Learned At": nte.get("learned_at", ""), "Active": "yes"})
    return rows


def publish_rules(rules: RuleStore | None = None) -> int:
    from sheet_common import RULES_HEADERS, RULES_TAB, Tab, get_spreadsheet
    rules = rules or RuleStore()
    tab = Tab(get_spreadsheet(), RULES_TAB, RULES_HEADERS)
    rows = rules_as_rows(rules)
    tab.replace_all(rows)
    print(f"Rules tab: {len(rows)} rows (rules v{rules.data.get('version')})")
    return len(rows)


# ------------------------------------------------------------------ scorecard
def scorecard(rows: list[dict], weeks: int = 8) -> str:
    """Per-week: rows, matched %, approved %, corrected count. From Processed rows."""
    by_week = defaultdict(Counter)
    for r in rows:
        d = (r.get("Received") or "")[:10]
        try:
            wk = datetime.strptime(d, "%Y-%m-%d").strftime("%G-W%V")
        except ValueError:
            continue
        c = by_week[wk]
        c["rows"] += 1
        matched = (r.get("Matched Task") or "").strip() not in ("", "NOT MATCHED")
        c["matched"] += matched
        dec = (r.get("DECISION") or "").strip().upper()[:1]
        c[dec or "?"] += 1
        c["noise"] += (r.get("Triage") == "noise")
        c["corrected"] += bool((r.get("Final Task GID") or "") and r.get("Final Task GID") != (r.get("Proposed Task GID") or ""))
    lines = [f"{'week':9} {'rows':>5} {'matched%':>9} {'A%':>5} {'M':>4} {'R':>4} {'fixed':>6}"]
    for wk in sorted(by_week)[-weeks:]:
        c = by_week[wk]
        n = c["rows"] or 1
        lines.append(f"{wk:9} {c['rows']:5d} {100*c['matched']/n:8.0f}% {100*c['A']/n:4.0f}% {c['M']:4d} {c['R']:4d} {c['corrected']:6d}")
    return "\n".join(lines)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--backfill-xlsx", metavar="FILE")
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--scorecard", action="store_true")
    ap.add_argument("--scorecard-xlsx", metavar="FILE")
    ap.add_argument("--weeks", type=int, default=8)
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args()
    if a.backfill_xlsx:
        backfill(_rows_from_xlsx(a.backfill_xlsx))
    elif a.backfill:
        backfill(_rows_from_sheet())
    elif a.publish:
        publish_rules()
    elif a.scorecard_xlsx:
        print(scorecard(_rows_from_xlsx(a.scorecard_xlsx), a.weeks))
    elif a.scorecard:
        print(scorecard(_rows_from_sheet(), a.weeks))
    else:
        import json
        print(json.dumps(RuleStore().data, indent=2, ensure_ascii=False))
