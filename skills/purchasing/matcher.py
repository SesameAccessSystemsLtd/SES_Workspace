"""
matcher.py — resolve an inbound email / call to the right Asana order task.

Design (2026-09-18, from Giles' 37 amendment notes):
  1. Resolve WHO first: sender domain → supplier directory (data/suppliers.json),
     then learned rules (domain / phone / contact label → supplier). Never guess
     a company from a word in the domain.
  2. Then WHICH TASK: exact refs (SQR, PO) → supplier + job number → supplier
     refs in task notes → the supplier's single open order → subject reference.
  3. Closed / arrived / cancelled tasks are never auto-matched for quotes,
     confirmations, dispatch notes or updates. Invoices may land on ARRIVED tasks.
     Tasks Giles has blocked ("21202 is closed out, stop matching it") are skipped.
  4. Every result carries reasons, confidence and up to 5 alternatives with
     their status, so the sheet row shows WHY and what else it could have been.

Read-only against ops.db. Schema-tolerant: only uses columns that exist.
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
from gateway_common import (RuleStore, SupplierDirectory, extract_refs, name_tokens,
                            normalise_phone, parse_ringcentral)

try:
    from path_config import p as _p
    DEFAULT_DB = os.environ.get("OPENCLAW_OPS_DB_PATH") or _p("db.ops")
except Exception:  # sandbox / tests
    DEFAULT_DB = os.environ.get("OPENCLAW_OPS_DB_PATH") or str(Path(__file__).resolve().parents[2] / "db" / "ops.db")

CLOSED_STATUSES = {
    "✅ARRIVED - MOVES TO INVOICES!!", "🔥ARRIVED - NEED TO QC", "CANCELLED ORDER ",
    "ALL PICKED / No Further action ", "No Further action required", "LINE ITEM DELIVERED",
}
ARRIVED_STATUSES = {"✅ARRIVED - MOVES TO INVOICES!!", "🔥ARRIVED - NEED TO QC", "🔜PARTIAL DELIVERY"}
QUOTE_STATUSES = {"💢 Quote / Order (check BOM Status)", "Quote - Auto Email ", "Quote - Manual ", "TO BE ASSESSED"}
ON_ORDER_STATUSES = {"⏰On Order - Awaiting CONFIRMATION", "🚚On Order - Delivery Date CONFIRMED",
                     "AWAITING DISPATCH ", "Awaiting Position ", "🔜PARTIAL DELIVERY", "HOLD ON ORDER "}


def _norm(v: str) -> str:
    return "".join(ch for ch in str(v or "").upper() if ch.isalnum())


@dataclass
class Candidate:
    gid: str
    name: str
    status: str = ""
    completed: bool = False
    due: str = ""
    po: str = ""
    sqr: str = ""
    vendor: str = ""
    price: float | None = None
    how: str = ""          # why this candidate was found
    score: float = 0.0

    @property
    def url(self) -> str:
        return f"https://app.asana.com/0/0/{self.gid}"

    @property
    def is_closed(self) -> bool:
        return self.completed or (self.status or "").strip() in {s.strip() for s in CLOSED_STATUSES}

    @property
    def is_arrived(self) -> bool:
        return (self.status or "").strip() in {s.strip() for s in ARRIVED_STATUSES}

    def short(self) -> str:
        st = "closed" if self.completed else (self.status or "no status").strip()
        return f"{self.name[:70]} [{st}] {self.url}"


@dataclass
class MatchResult:
    task: Candidate | None = None
    confidence: str = "NONE"           # HIGH / MEDIUM / LOW / NONE
    matched_by: str = ""               # one-line basis, e.g. "SQR-46913 (exact)"
    supplier: dict | None = None       # supplier directory row
    supplier_how: str = ""             # "domain appliedautomation.co.uk" / "phone rule" / "label"
    alternatives: list[Candidate] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    refs: dict = field(default_factory=dict)

    @property
    def supplier_name(self) -> str:
        return SupplierDirectory.short_name(self.supplier) if self.supplier else ""

    def to_dict(self) -> dict:
        return {
            "task_gid": self.task.gid if self.task else None,
            "task_name": self.task.name if self.task else None,
            "task_url": self.task.url if self.task else None,
            "task_status": self.task.status if self.task else None,
            "task_completed": self.task.completed if self.task else None,
            "task_due": self.task.due if self.task else None,
            "task_price_ex_vat": self.task.price if self.task else None,
            "task_vendor": self.task.vendor if self.task else None,
            "confidence": self.confidence,
            "matched_by": self.matched_by,
            "supplier_gid": self.supplier.get("gid") if self.supplier else None,
            "supplier_name": self.supplier_name,
            "supplier_how": self.supplier_how,
            "alternatives": [{"gid": c.gid, "name": c.name, "status": c.status, "url": c.url, "how": c.how}
                             for c in self.alternatives],
            "reasons": self.reasons,
            "refs": self.refs,
        }


class Matcher:
    def __init__(self, db_path: str = DEFAULT_DB, suppliers: SupplierDirectory | None = None,
                 rules: RuleStore | None = None):
        self.rules = rules or RuleStore()
        self.suppliers = suppliers or SupplierDirectory(rules=self.rules)
        self.db_path = db_path
        self.conn = None
        self.cols: set[str] = set()
        if db_path and os.path.exists(db_path):
            self.conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            self.conn.row_factory = sqlite3.Row
            self.cols = {r[1] for r in self.conn.execute("PRAGMA table_info(procurement_tasks)")}

    # ------------------------------------------------------------------ sql
    def _col(self, name: str, default="''") -> str:
        return name if name in self.cols else f"{default} AS {name}"

    def _select(self) -> str:
        want = ["asana_task_gid", "task_name", "order_status", "is_completed", "due_date",
                "po_number", "sqr_number", "vendor_name", "price_ex_vat", "is_subtask", "last_synced_at"]
        parts = []
        for w in want:
            if w in self.cols:
                parts.append(w)
            elif w in ("is_completed", "is_subtask"):
                parts.append(f"0 AS {w}")
            elif w == "price_ex_vat":
                parts.append(f"NULL AS {w}")
            else:
                parts.append(f"'' AS {w}")
        if "notes" in self.cols:
            parts.append("notes")
        return "SELECT " + ", ".join(parts) + " FROM procurement_tasks"

    def _rows(self, where: str, params: tuple, limit: int = 8) -> list[Candidate]:
        if not self.conn:
            return []
        sql = f"{self._select()} WHERE {where} ORDER BY is_completed ASC, is_subtask ASC, last_synced_at DESC LIMIT {limit}"
        out = []
        for r in self.conn.execute(sql, params):
            out.append(Candidate(
                gid=str(r["asana_task_gid"]), name=r["task_name"] or "", status=r["order_status"] or "",
                completed=bool(r["is_completed"]), due=r["due_date"] or "", po=r["po_number"] or "",
                sqr=r["sqr_number"] or "", vendor=r["vendor_name"] or "",
                price=(float(r["price_ex_vat"]) if r["price_ex_vat"] not in (None, "") else None),
            ))
        return out

    def _by_gid(self, gid: str) -> Candidate | None:
        rows = self._rows("asana_task_gid = ?", (str(gid),), 1)
        return rows[0] if rows else None

    # ------------------------------------------------------------ supplier
    def resolve_supplier(self, sender_email: str, sender_domain: str, rc: dict | None,
                         llm_company: str = "") -> tuple[dict | None, str, list[dict]]:
        """→ (supplier, how, other_candidates)."""
        # explicit learned rule on the exact sender wins
        if rc:  # RingCentral: phone / label rules, then fuzzy label
            for ph in rc.get("phones") or []:
                s = self.suppliers.by_phone(ph)
                if s:
                    return s, f"phone {ph} (learned rule)", []
            lab = rc.get("contact_label") or ""
            gid = self.rules.label_supplier(lab)
            if gid and gid in self.suppliers.by_gid:
                return self.suppliers.by_gid[gid], f"contact label '{lab}' (learned rule)", []
            hits = self.suppliers.by_label(lab)
            if hits:
                best, score = hits[0]
                if score >= 0.7 and (len(hits) == 1 or hits[1][1] < score - 0.2):
                    return best, f"contact label '{lab}' ≈ {SupplierDirectory.short_name(best)}", [h for h, _ in hits[1:3]]
                return None, f"contact label '{lab}' is ambiguous", [h for h, _ in hits[:3]]
            if llm_company:
                hits = self.suppliers.by_label(llm_company)
                if hits and hits[0][1] >= 0.7:
                    return hits[0][0], f"company named in call notes ≈ {SupplierDirectory.short_name(hits[0][0])}", []
            return None, f"no supplier for label '{lab}' / phones {rc.get('phones')}", []

        hits = self.suppliers.by_email_domain(sender_domain)
        if len(hits) == 1:
            return hits[0], f"domain {sender_domain}", []
        if len(hits) > 1:
            # several supplier records share a domain (Applied Automation / MayTec). Prefer the one
            # whose name/vendor appears in the LLM's company guess, else the first.
            if llm_company:
                for h in hits:
                    if name_tokens(llm_company) & name_tokens(h.get("name", "")):
                        return h, f"domain {sender_domain} + name '{llm_company}'", [x for x in hits if x is not h]
            return hits[0], f"domain {sender_domain} ({len(hits)} records share it)", hits[1:]
        if llm_company:  # gmail / generic senders: "Kerry Way Ltd"
            hits = self.suppliers.by_label(llm_company)
            if hits and hits[0][1] >= 0.7:
                return hits[0][0], f"company '{llm_company}' in email ≈ {SupplierDirectory.short_name(hits[0][0])}", []
        return None, f"domain {sender_domain} not in supplier directory", []

    # ------------------------------------------------------------ helpers
    def _supplier_where(self, supplier: dict) -> tuple[str, tuple]:
        """SQL restricting procurement_tasks to a supplier: vendor field exact-ish OR
        company tokens in task_name. Returns ('(…)', params)."""
        clauses, params = [], []
        vf = (supplier.get("vendor_field") or "").strip()
        if vf:
            clauses.append("LOWER(vendor_name) = LOWER(?)")
            params.append(vf)
            clauses.append("task_name LIKE ?")
            params.append(f"%{vf}%")
        short = SupplierDirectory.short_name(supplier)
        toks = [t for t in name_tokens(short) if len(t) >= 4] or list(name_tokens(short))
        for t in toks[:2]:
            clauses.append("task_name LIKE ?")
            params.append(f"%{t}%")
            clauses.append("vendor_name LIKE ?")
            params.append(f"%{t}%")
        if not clauses:
            return "0", ()
        return "(" + " OR ".join(clauses) + ")", tuple(params)

    @staticmethod
    def _word_hit(c: Candidate, supplier: dict | None) -> bool:
        """LIKE '%idl%' also matches 'Midland' — insist on a whole-word hit for the supplier."""
        if not supplier:
            return True
        hay = f"{c.name} {c.vendor}".lower()
        vf = (supplier.get("vendor_field") or "").strip().lower()
        if vf and re.search(rf"(?<![a-z0-9]){re.escape(vf)}(?![a-z0-9])", hay):
            return True
        toks = [t for t in name_tokens(SupplierDirectory.short_name(supplier)) if len(t) >= 3]
        return any(re.search(rf"(?<![a-z0-9]){re.escape(t)}(?![a-z0-9])", hay) for t in toks)

    def _filter(self, cands: list[Candidate], doc_type: str, how: str, supplier: dict | None = None) -> tuple[list[Candidate], list[str]]:
        """Drop blocked / closed tasks (doc-type aware) and false LIKE hits; annotate how found."""
        keep, notes = [], []
        for c in cands:
            if supplier and not self._word_hit(c, supplier):
                continue
            c.how = how
            why = self.rules.is_blocked_task(c.gid)
            if why:
                notes.append(f"skipped {c.name[:40]} — blocked: {why}")
                continue
            if c.completed or (c.is_closed and not (doc_type == "invoice" and c.is_arrived)):
                notes.append(f"skipped {c.name[:40]} — {('closed' if c.completed else c.status.strip())}")
                continue
            keep.append(c)
        return keep, notes

    # ------------------------------------------------------------ main
    def match(self, *, sender_email: str, sender_domain: str, subject: str, body_text: str,
              insights: dict, doc_type: str = "") -> MatchResult:
        res = MatchResult()
        doc_type = doc_type or insights.get("document_type") or ""
        rc = parse_ringcentral(subject, body_text) if sender_domain in ("ringcentral.com",) else None
        refs = extract_refs(body_text[:6000], subject=subject)
        # LLM-supplied refs take precedence but the regex pass fills gaps
        for k, v in (("sqr_numbers", insights.get("sqr_number")), ("po_numbers", insights.get("po_number"))):
            if v and v not in refs[k]:
                refs[k].insert(0, v)
        for j in insights.get("job_numbers") or []:
            if j not in refs["job_numbers"]:
                refs["job_numbers"].append(j)
        res.refs = refs

        # 0. explicit learned mappings on the exact sender / phone
        direct = self.rules.sender_task(sender_email) or (None if rc else self.rules.domain_task(sender_domain))
        if not direct and rc:
            for ph in rc.get("phones") or []:
                direct = self.rules.phone_task(ph)
                if direct:
                    break
        if direct:
            c = self._by_gid(direct["task_gid"])
            if c and not c.completed and not c.is_closed:
                c.how = "learned rule"
                res.task, res.confidence = c, "HIGH"
                res.matched_by = f"learned rule ({direct.get('note') or sender_email})"
                res.reasons.append(res.matched_by)
            elif c:
                res.reasons.append(f"learned rule points at {c.name[:40]} but it is closed — ignored")
            else:
                res.reasons.append(f"learned rule points at task {direct['task_gid']} which is not in ops.db — ignored")

        # supplier
        sup, how, others = self.resolve_supplier(sender_email, sender_domain, rc, insights.get("company_name") or "")
        res.supplier, res.supplier_how = sup, how
        res.reasons.append(f"supplier: {how}")
        if others:
            res.reasons.append("other supplier candidates: " + ", ".join(SupplierDirectory.short_name(o) for o in others))
        if res.task:
            return res

        # 1. SQR
        for sqr in refs["sqr_numbers"]:
            n = _norm(sqr)
            rows = self._rows(
                "UPPER(REPLACE(REPLACE(REPLACE(REPLACE(sqr_number,'-',''),' ',''),'#',''),'/','')) = ? AND sqr_number != ''",
                (n,))
            keep, notes = self._filter(rows, doc_type, f"SQR {sqr}")
            res.reasons += notes
            if keep:
                return self._finish(res, keep, "HIGH", f"{sqr} (exact SQR)")
            if rows:
                res.alternatives += rows[:3]

        # 2. PO exact then base
        for po in refs["po_numbers"]:
            rows = self._rows("po_number = ?", (po,))
            keep, notes = self._filter(rows, doc_type, f"PO {po}")
            res.reasons += notes
            if keep:
                return self._finish(res, keep, "HIGH", f"PO {po} (exact)")
            if rows:
                res.alternatives += rows[:3]
            base = po.split("-")[0]
            if len(base) == 5:
                rows = self._rows("po_number LIKE ? OR task_name LIKE ?", (base + "-%", base + "-%"))
                # The 5-digit base is NOT unique (21222-1313 and 21222-1374 both exist — invoice
                # gate trial 2026-09-18). Only trust it when the base maps to ONE PO overall,
                # open or closed; otherwise list the candidates for Giles.
                distinct = {r.po.split("-")[0] + "-" + r.po.split("-")[1] if "-" in r.po else r.name[:11] for r in rows}
                keep, notes = self._filter(rows, doc_type, f"PO {base}-*")
                res.reasons += notes
                if len(keep) == 1 and len(distinct) <= 1:
                    return self._finish(res, keep, "HIGH", f"PO {base} (base number, unique)")
                if keep and len(distinct) > 1:
                    res.reasons.append(f"PO base {base} is not unique: {sorted(distinct)} — not auto-matched")
                if keep:
                    res.alternatives += keep[:5]
                    res.reasons.append(f"PO base {base} matches {len(keep)} open tasks — needs a pick")

        # 3. supplier + job number
        if sup and refs["job_numbers"]:
            sw, sp = self._supplier_where(sup)
            for job in refs["job_numbers"]:
                rows = self._rows(f"task_name LIKE ? AND {sw}", (f"%{job}%",) + sp)
                keep, notes = self._filter(rows, doc_type, f"{res.supplier_name} + job {job}", sup)
                res.reasons += notes
                if len(keep) == 1:
                    return self._finish(res, keep, "HIGH", f"{res.supplier_name} + job {job}")
                if len(keep) > 1:
                    pref = self._prefer_by_status(keep, doc_type)
                    if pref:
                        return self._finish(res, [pref] + [k for k in keep if k is not pref], "MEDIUM",
                                            f"{res.supplier_name} + job {job} ({len(keep)} open; picked by status)")
                    res.alternatives += keep[:5]

        # 3b. the supplier's own references seen on an EARLIER email (local memory):
        #     the invoice quoting order ref 000759386 lands on the task the confirmation went to.
        hist = self._history_by_refs(insights, sender_email, sender_domain)
        if hist:
            c = self._by_gid(hist["task_gid"])
            if c:
                keep, notes = self._filter([c], doc_type, f"earlier {hist['document_type']} ({hist['ref']})")
                res.reasons += notes
                if keep:
                    return self._finish(res, keep, "HIGH", f"their ref {hist['ref']} seen on {hist['document_type']} {hist['received'][:10]}")

        # 4. supplier refs (their quote / order / invoice number) in task name or notes
        sref = [r for r in (insights.get("supplier_quote_ref"), insights.get("supplier_order_ref"),
                            insights.get("supplier_invoice_ref")) if r and len(str(r)) >= 4]
        if sref:
            for r in sref:
                where = "task_name LIKE ?" + (" OR notes LIKE ?" if "notes" in self.cols else "")
                params = (f"%{r}%",) * (2 if "notes" in self.cols else 1)
                rows = self._rows(where, params)
                keep, notes = self._filter(rows, doc_type, f"supplier ref {r}")
                res.reasons += notes
                if len(keep) == 1:
                    return self._finish(res, keep, "HIGH", f"supplier ref {r} found on task")
                res.alternatives += keep[:3]

        # 5. supplier's open orders
        if sup:
            sw, sp = self._supplier_where(sup)
            rows = self._rows(f"is_subtask = 0 AND {sw}", sp, limit=12)
            keep, notes = self._filter(rows, doc_type, f"{res.supplier_name} open order", sup)
            res.reasons += notes[:3]
            if len(keep) == 1:
                return self._finish(res, keep, "MEDIUM", f"{res.supplier_name}'s only open order")
            if keep:
                pref = self._prefer_by_status(keep, doc_type)
                res.alternatives += ([pref] if pref else []) + [k for k in keep if k is not pref][:5]
                res.reasons.append(f"{res.supplier_name} has {len(keep)} open orders — needs a pick")
                res.confidence = "LOW"
            elif rows:
                res.reasons.append(f"{res.supplier_name}: only closed orders found")

        # 6. subject reference ("Order #61894", "Quote 12345")
        m = re.search(r"(?:Order|Quote|Ref|PO|Invoice)\s*(?:No\.?|Number|#|:)?\s*([A-Z0-9][A-Z0-9_\-/]{3,})", subject or "", re.I)
        if m and not res.task:
            ref = m.group(1)
            where = "(task_name LIKE ? OR po_number LIKE ?" + (" OR notes LIKE ?" if "notes" in self.cols else "") + ") AND is_subtask = 0"
            params = (f"%{ref}%",) * (3 if "notes" in self.cols else 2)
            rows = self._rows(where, params)
            keep, notes = self._filter(rows, doc_type, f"subject ref {ref}")
            if len(keep) == 1:
                return self._finish(res, keep, "MEDIUM", f"subject reference '{ref}'")
            res.alternatives += keep[:3]

        # dedupe alternatives
        seen, alts = set(), []
        for c in res.alternatives:
            if c.gid not in seen:
                seen.add(c.gid)
                alts.append(c)
        res.alternatives = alts[:5]
        if not res.task:
            res.confidence = "LOW" if alts else "NONE"
            res.matched_by = "no task found" if not alts else f"{len(alts)} candidate(s), none certain"
        return res

    def _history_by_refs(self, insights: dict, sender_email: str, sender_domain: str) -> dict | None:
        """Look in email_documents for a previous email carrying the same supplier reference
        whose task is known (Giles decided A/M, or it was a HIGH match). Returns
        {task_gid, ref, document_type, received} or None."""
        if not self.conn or "email_documents" not in self._tables():
            return None
        refs = [(k, insights.get(k)) for k in ("supplier_order_ref", "supplier_quote_ref", "supplier_invoice_ref")
                if insights.get(k) and len(str(insights.get(k))) >= 4]
        if not refs:
            return None
        for _, ref in refs:
            n = _norm(ref)
            rows = self.conn.execute(
                """SELECT email_message_id, received_at, document_type, sender_domain,
                          COALESCE(final_task_gid, CASE WHEN match_confidence = 'HIGH' THEN proposed_task_gid END) AS task_gid,
                          supplier_order_ref, supplier_quote_ref, supplier_invoice_ref
                   FROM email_documents
                   WHERE (outcome IN ('APPROVED','AMENDED') OR match_confidence = 'HIGH')
                     AND (UPPER(REPLACE(REPLACE(supplier_order_ref,'-',''),' ','')) = ?
                          OR UPPER(REPLACE(REPLACE(supplier_quote_ref,'-',''),' ','')) = ?
                          OR UPPER(REPLACE(REPLACE(supplier_invoice_ref,'-',''),' ','')) = ?)
                   ORDER BY received_at DESC LIMIT 5""", (n, n, n)).fetchall()
            for r in rows:
                if not r["task_gid"]:
                    continue
                # same supplier only (a 5-digit order number is not unique across suppliers)
                if sender_domain and r["sender_domain"] and r["sender_domain"] != sender_domain and sender_domain != "ringcentral.com":
                    continue
                return {"task_gid": r["task_gid"], "ref": ref, "document_type": r["document_type"] or "email",
                        "received": r["received_at"] or ""}
        return None

    def _tables(self) -> set[str]:
        if not hasattr(self, "_tables_cache"):
            self._tables_cache = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")} if self.conn else set()
        return self._tables_cache

    def _prefer_by_status(self, keep: list[Candidate], doc_type: str) -> Candidate | None:
        want = QUOTE_STATUSES if doc_type == "quote" else ON_ORDER_STATUSES if doc_type in ("order_confirmation", "dispatch_note", "update") else ARRIVED_STATUSES if doc_type == "invoice" else set()
        want = {s.strip() for s in want}
        hits = [c for c in keep if c.status.strip() in want]
        return hits[0] if len(hits) == 1 else None

    def _finish(self, res: MatchResult, keep: list[Candidate], conf: str, how: str) -> MatchResult:
        res.task = keep[0]
        res.confidence = conf
        res.matched_by = how
        res.reasons.append(f"matched: {how}")
        res.alternatives = [c for c in (keep[1:] + res.alternatives) if c.gid != res.task.gid][:5]
        return res
