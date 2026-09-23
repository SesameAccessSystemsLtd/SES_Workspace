"""
invoice_gate.py — SANDBOX harness for the seven-condition invoice sign-off gate. READ-ONLY.

Nothing here writes to Asana, ops.db, the sheet or Dropbox. It exists to answer one
question on real documents: "given the invoice text and what Asana already holds,
would this invoice have cleared every gate condition automatically — and if not,
which one stopped it and why?"

The design it tests (agreed with Giles 2026-09-18):
  * the PO is closed-set RECOGNITION against the index of POs we issued, never a
    pattern hunt (and never from the filename);
  * the money is VERIFICATION: expected gross = (Price Ex VAT + Carriage ex VAT) x 1.2
    must appear in the document to the penny;
  * the invoice number is the only true extraction, narrowed by a per-supplier
    format profile learned from history;
  * seven conditions, all deterministic, all auditable; any failure = exception
    with a named reason; no partial writes.

Run:
    python invoice_gate.py --fixtures fixtures/signed_off_invoices.json     # sandbox trial
    python invoice_gate.py --text invoice.txt --sender-domain x.co.uk        # one document vs ops.db
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from gateway_common import SupplierDirectory, load_json, name_tokens  # noqa: E402

ARRIVED = "✅ARRIVED - MOVES TO INVOICES!!"
AI_SIGNED = "Signed off by AI - AWAITING APPROVAL "
VALUE_THRESHOLD_GBP = 2000.0     # above this a human always looks, whatever the gate says

PO_TOKEN = re.compile(r"(?<![\d.])(2\d{4})\s*[-/_ ]\s*(\d{4,6})(?![\d.])")
PO_BARE = re.compile(r"(?<![\d.\-/])(2\d{4})(?![\d.\-/])")
AMOUNT = re.compile(r"(?<![\d.])(\d{1,3}(?:,\d{3})*(?:\.\d{2})|\d+\.\d{2})(?![\d])")


def norm_po(a: str, b: str | None = None) -> str:
    return f"{a}-{b}" if b else a


def to_money(s: str) -> float:
    return round(float(s.replace(",", "")), 2)


# ------------------------------------------------------------------ PO index
@dataclass
class POTask:
    gid: str
    po: str
    name: str
    vendor: str = ""
    price_ex_vat: float | None = None
    carriage_ex_vat: float | None = None
    inc_vat: float | None = None
    invoice_number: str | None = None
    order_status: str = ""
    signed_off: str | None = None
    completed: bool = False

    @property
    def expected_gross(self) -> float | None:
        if self.price_ex_vat is None:
            return None
        return round((self.price_ex_vat + (self.carriage_ex_vat or 0.0)) * 1.2, 2)


class POIndex:
    """Every PO we have issued → its task. Full PO ('21222-1313') is the key; the
    5-digit base is NOT unique (21222-1313 and 21222-1374 both exist)."""

    def __init__(self, tasks: list[POTask]):
        self.by_po: dict[str, list[POTask]] = {}
        self.by_base: dict[str, list[POTask]] = {}
        for t in tasks:
            self.by_po.setdefault(t.po, []).append(t)
            self.by_base.setdefault(t.po.split("-")[0], []).append(t)

    @classmethod
    def from_records(cls, recs: list[dict]) -> "POIndex":
        out = []
        for r in recs:
            a = r["asana"]
            m = PO_TOKEN.search(a["name"])
            out.append(POTask(gid=a["gid"], po=norm_po(m.group(1), m.group(2)) if m else a["name"][:11],
                              name=a["name"], vendor=a.get("vendor") or "", price_ex_vat=a.get("price_ex_vat"),
                              carriage_ex_vat=a.get("carriage_ex_vat"), inc_vat=a.get("inc_vat"),
                              invoice_number=a.get("invoice_number"), order_status=a.get("order_status") or "",
                              signed_off=a.get("signed_off"), completed=True))
        return cls(out)

    @classmethod
    def from_ops_db(cls, db_path: str) -> "POIndex":
        import sqlite3
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cols = {r[1] for r in conn.execute("PRAGMA table_info(procurement_tasks)")}
        want = [c for c in ("asana_task_gid", "po_number", "task_name", "vendor_name", "price_ex_vat", "carriage_ex_vat",
                            "invoice_number", "order_status", "signed_off", "is_completed") if c in cols]
        out = []
        for r in conn.execute(f"SELECT {', '.join(want)} FROM procurement_tasks WHERE po_number IS NOT NULL AND po_number != ''"):
            po = (r["po_number"] or "").strip()
            m = PO_TOKEN.search(po) or PO_TOKEN.search(r["task_name"] or "")
            if not m:
                continue
            out.append(POTask(gid=str(r["asana_task_gid"]), po=norm_po(m.group(1), m.group(2)), name=r["task_name"] or "",
                              vendor=r["vendor_name"] or "" if "vendor_name" in cols else "",
                              price_ex_vat=r["price_ex_vat"] if "price_ex_vat" in cols else None,
                              carriage_ex_vat=r["carriage_ex_vat"] if "carriage_ex_vat" in cols else None,
                              invoice_number=r["invoice_number"] if "invoice_number" in cols else None,
                              order_status=r["order_status"] or "" if "order_status" in cols else "",
                              signed_off=r["signed_off"] if "signed_off" in cols else None,
                              completed=bool(r["is_completed"]) if "is_completed" in cols else False))
        return cls(out)


# ------------------------------------------------------------------ supplier profiles
def invoice_shape(inv: str) -> str:
    """'SI-10927' → 'SI-#####', '001830325' → '#########', 'GB6003GIJWSCXI' → 'GB############'.
    Letters are kept literally when they are a prefix; digits become '#', other letters '@'."""
    s = inv.strip()
    m = re.match(r"^([A-Za-z]{0,4}[-/ ]?)(.*)$", s)
    prefix, rest = (m.group(1), m.group(2)) if m else ("", s)
    body = "".join("#" if ch.isdigit() else ("@" if ch.isalpha() else ch) for ch in rest)
    return prefix.upper() + body


class SupplierProfiles:
    """Per supplier: the invoice-number shapes seen in Asana history and their count, plus the
    Vendor spellings that history has paired with each sender domain ('Bas' ↔ bes.co.uk)."""

    def __init__(self):
        self.shapes: dict[str, dict[str, int]] = {}
        self.vendor_aliases: dict[str, set[str]] = {}   # sender_domain -> {vendor spellings}

    def learn_vendor(self, sender_domain: str, vendor: str | None):
        if sender_domain and vendor and vendor.strip():
            self.vendor_aliases.setdefault(sender_domain.lower(), set()).add(vendor.strip().lower())

    def learn(self, supplier_key: str, invoice_field: str | None):
        if not invoice_field:
            return
        for inv in re.split(r"\s*/\s*|\s*,\s*", invoice_field):
            inv = inv.strip()
            if len(inv) >= 3:
                d = self.shapes.setdefault(supplier_key.lower(), {})
                d[invoice_shape(inv)] = d.get(invoice_shape(inv), 0) + 1

    def scan(self, supplier_key: str, text: str) -> list[str]:
        """When no labelled candidate exists, find tokens that fit a known shape for this supplier
        (e.g. BES prints the invoice number under 'Invoice Date' with no label)."""
        d = self.shapes.get((supplier_key or "").lower())
        if not d:
            return []
        out = []
        for tok in re.findall(r"\b[A-Z]{0,4}[-/]?\d{3,12}[A-Z0-9]*\b", text):
            if invoice_shape(tok) in d and tok not in out and not PO_TOKEN.fullmatch(tok):
                out.append(tok)
        return out

    def matches(self, supplier_key: str, candidate: str) -> bool | None:
        """True/False against a known profile; None when we have no history for the supplier."""
        d = self.shapes.get((supplier_key or "").lower())
        if not d:
            return None
        return invoice_shape(candidate) in d


# ------------------------------------------------------------------ extraction
INV_LABELS = [
    r"invoice\s*(?:no\.?|number|#)\s*[:.]?\s*([A-Z]{0,4}[-/ ]?\d{3,12}[A-Z0-9]*)",
    r"sales\s+invoice\s+no\.?\s*[:.]?\s*([A-Z]{0,4}[-/ ]?\d{3,12}[A-Z0-9]*)",
    r"invoice\s+for\s+([A-Z]{0,4}\d{3,12}[A-Z0-9]*)",
    r"invoice\s+([A-Z]{0,4}\d{4,12})(?![\d])",          # 'Invoice 79829Sesame…'
    r"([A-Z]{0,4}[-/ ]?\d{3,12}[A-Z0-9]*)\s*invoice\s*number",   # '8778653Invoice Number:'
    r"proforma\s*no\s*(?:\n[^\n]*){0,3}\n(\d{3,10})\n",  # column-scrambled proforma layout
    r"reference\s*#\s*([A-Z0-9]{6,20})",
]


def find_invoice_numbers(text: str) -> list[tuple[str, str]]:
    """→ [(candidate, how)] in order of confidence."""
    out, seen = [], set()
    for pat in INV_LABELS:
        for m in re.finditer(pat, text, re.I):
            c = m.group(1).strip().rstrip(".:")
            if c and c not in seen and not re.fullmatch(r"\d{1,2}", c):
                seen.add(c)
                out.append((c, pat[:28]))
    return out


def find_pos(text: str, index: POIndex) -> tuple[list[POTask], list[str]]:
    """Closed-set recognition. Full tokens first; a bare 5-digit base only when it sits next
    to an order/PO label and resolves to exactly one PO."""
    notes, hits = [], []
    seen = set()
    for m in PO_TOKEN.finditer(text):
        po = norm_po(m.group(1), m.group(2))
        if po in seen:
            continue
        seen.add(po)
        if po in index.by_po:
            hits += index.by_po[po]
        elif m.group(1) in index.by_base:
            notes.append(f"{po} not issued; base {m.group(1)} exists as {[t.po for t in index.by_base[m.group(1)]]}")
    if not hits:
        for m in re.finditer(r"(?:order|po|purchase order|ref(?:erence)?)[^\n]{0,25}?" + PO_BARE.pattern, text, re.I):
            base = m.group(1)
            cands = index.by_base.get(base, [])
            if len(cands) == 1:
                hits += cands
                notes.append(f"bare PO {base} accepted (unique, next to a label)")
            elif len(cands) > 1:
                notes.append(f"bare PO {base} is ambiguous: {[t.po for t in cands]}")
    return hits, notes


def find_amounts(text: str) -> set[float]:
    return {to_money(m.group(1)) for m in AMOUNT.finditer(text)}


# ------------------------------------------------------------------ local history
class InvoiceHistory:
    """Read-only view over ops.db for the live gate: the duplicate guard, the multi-invoice
    running total and the 'a confirmation carried this supplier ref' suggestion."""

    def __init__(self, db_path: str):
        import sqlite3
        self.conn = None
        try:
            self.conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            self.conn.row_factory = sqlite3.Row
            self.tables = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        except Exception:
            self.tables = set()

    def duplicate(self, supplier_key: str, invoice_number: str, sender_domain: str) -> str | None:
        if not self.conn:
            return None
        n = invoice_number.strip().upper()
        if "invoice_documents" in self.tables:
            r = self.conn.execute("SELECT filed_name, filed_folder, seen_at FROM invoice_documents WHERE UPPER(invoice_number) = ? AND (sender_domain = ? OR supplier = ?) LIMIT 1",
                                  (n, sender_domain, supplier_key)).fetchone()
            if r:
                return f"already filed {r['filed_folder']}/{r['filed_name']} ({r['seen_at'][:10]})"
        if "procurement_tasks" in self.tables:
            cols = {c[1] for c in self.conn.execute("PRAGMA table_info(procurement_tasks)")}
            if "invoice_number" in cols:
                r = self.conn.execute("SELECT task_name FROM procurement_tasks WHERE UPPER(invoice_number) LIKE ? AND LOWER(vendor_name) LIKE ? LIMIT 1",
                                      (f"%{n}%", f"%{(supplier_key or 'zzz').lower()[:6]}%")).fetchone()
                if r:
                    return f"invoice number already on task '{r['task_name'][:50]}'"
        return None

    def prior_total(self, task_gid: str, exclude_invoice: str | None = None) -> float:
        if not self.conn or "invoice_documents" not in self.tables:
            return 0.0
        r = self.conn.execute("SELECT COALESCE(SUM(doc_gross),0) FROM invoice_documents WHERE task_gid = ? AND verdict = 'AUTO' AND (invoice_number IS NULL OR invoice_number != ?)",
                              (task_gid, exclude_invoice or "")).fetchone()
        return float(r[0] or 0.0)

    def suggest_task(self, text: str, sender_domain: str) -> dict | None:
        """No PO in the document: does an order confirmation from this supplier carry a reference
        that appears on the invoice? Returns a SUGGESTION for the human, never a match."""
        if not self.conn or "email_documents" not in self.tables:
            return None
        rows = self.conn.execute(
            """SELECT supplier_order_ref, supplier_quote_ref, COALESCE(final_task_gid, proposed_task_gid) AS gid, proposed_task_name, received_at
               FROM email_documents WHERE sender_domain = ? AND document_type IN ('order_confirmation','quote','dispatch_note')
                 AND (supplier_order_ref IS NOT NULL OR supplier_quote_ref IS NOT NULL)
                 AND COALESCE(final_task_gid, proposed_task_gid) IS NOT NULL
               ORDER BY received_at DESC LIMIT 50""", (sender_domain,)).fetchall()
        up = text.upper()
        for r in rows:
            for ref in (r["supplier_order_ref"], r["supplier_quote_ref"]):
                if ref and len(str(ref)) >= 4 and str(ref).upper() in up:
                    return {"gid": r["gid"], "name": r["proposed_task_name"] or "",
                            "why": f"their ref {ref} was on the {r['received_at'][:10]} confirmation matched to {(r['proposed_task_name'] or r['gid'])[:45]}"}
        return None


# ------------------------------------------------------------------ the gate
@dataclass
class GateResult:
    file: str
    task: POTask | None = None
    conditions: dict = field(default_factory=dict)   # name -> (bool, reason)
    invoice_candidates: list = field(default_factory=list)
    chosen_invoice: str | None = None
    flags: list[str] = field(default_factory=list)
    doc_gross: float | None = None
    suggested_task: dict | None = None      # {gid, name, why} when no PO but a confirmation ref points somewhere
    hard_stops: list[str] = field(default_factory=list)   # PROFORMA / DEPOSIT / STATEMENT / DUPLICATE / VALUE

    @property
    def auto(self) -> bool:
        return bool(self.conditions) and all(v[0] for v in self.conditions.values()) and not self.hard_stops

    def failed(self) -> list[str]:
        return [k for k, v in self.conditions.items() if not v[0]]


def run_gate(text: str, sender_domain: str, index: POIndex, profiles: SupplierProfiles,
             suppliers: SupplierDirectory, file: str = "", live: bool = False,
             live_task: "callable | None" = None, history: "InvoiceHistory | None" = None) -> GateResult:
    """live=False: history/backtest mode (conditions 3 and 4 are reported, not enforced).
    live=True : conditions 3 and 4 are enforced against the task fields (live_task(gid) -> dict from
                asana_read.get_task_fields, or the index row if no callable is given), the duplicate
                guard and the multi-invoice running total use `history` (InvoiceHistory)."""
    res = GateResult(file=file)
    c = res.conditions
    low = text.lower()
    # Hard stops: documents a human deliberately does not sign off as VAT invoices (trial 2026-09-18:
    # a proforma cleared all seven conditions; a deposit re-issue reconciled net of the deposit).
    if "proforma" in low or "pro forma" in low or "pro-forma" in low:
        res.flags.append("PROFORMA"); res.hard_stops.append("PROFORMA — not a VAT invoice")
    if re.search(r"\bdeposit\b", low):
        res.flags.append("DEPOSIT"); res.hard_stops.append("DEPOSIT on document — totals may be net of a deposit")
    if re.search(r"\bstatement\b", low) and "invoice" not in low[:400]:
        res.flags.append("STATEMENT"); res.hard_stops.append("STATEMENT, not an invoice")
    if re.search(r"credit note", low):
        res.flags.append("CREDIT_NOTE"); res.hard_stops.append("CREDIT NOTE — needs a human")
    amounts_all = find_amounts(text)
    res.doc_gross = max(amounts_all) if amounts_all else None

    # 1. PO recognised from document text → exactly one task
    tasks, notes = find_pos(text, index)
    uniq = {t.gid: t for t in tasks}
    if len(uniq) == 1:
        res.task = next(iter(uniq.values()))
        c["1_po_resolves"] = (True, f"PO {res.task.po} → {res.task.name[:45]}" + (f" ({'; '.join(notes)})" if notes else ""))
    elif not uniq:
        c["1_po_resolves"] = (False, "no issued PO found in document text" + (f" ({'; '.join(notes)})" if notes else ""))
    else:
        c["1_po_resolves"] = (False, f"{len(uniq)} POs in document: {[t.po for t in uniq.values()]}")
    t = res.task
    if not t:
        for k in ("2_status_arrived", "3_not_signed_off", "4_invoice_field_empty", "5_supplier_matches", "6_invoice_format", "7_gross_reconciles"):
            c[k] = (False, "no task")
        res.invoice_candidates = find_invoice_numbers(text)
        if history:
            res.suggested_task = history.suggest_task(text, sender_domain)
            if res.suggested_task:
                c["1_po_resolves"] = (False, c["1_po_resolves"][1] + f"; SUGGESTION: {res.suggested_task['why']}")
        return res

    # 2. ORDER Status = ARRIVED
    c["2_status_arrived"] = ((t.order_status or "").strip() == ARRIVED.strip(), f"status '{(t.order_status or '').strip()}'")
    if live:
        fresh = None
        if live_task:
            try:
                fresh = live_task(t.gid)
            except Exception as e:
                res.flags.append("LIVE_READ_FAILED")
                res.reasons_extra = f"live Asana read failed ({e}); mirror values used"
        if fresh:
            t.order_status = (fresh.get("order_status") or t.order_status) or ""
            t.signed_off = fresh.get("signed_off")
            t.invoice_number = fresh.get("invoice_number")
            t.completed = bool(fresh.get("completed"))
            if fresh.get("price_ex_vat") is not None:
                t.price_ex_vat = float(fresh["price_ex_vat"])
            if fresh.get("carriage_ex_vat") is not None:
                t.carriage_ex_vat = float(fresh["carriage_ex_vat"])
            if fresh.get("vendor"):
                t.vendor = fresh["vendor"]
            c["2_status_arrived"] = ((t.order_status or "").strip() == ARRIVED.strip(), f"live status '{(t.order_status or '').strip()}'")
        so = (t.signed_off or "").strip()
        c["3_not_signed_off"] = (not so and not t.completed, f"Signed OFF '{so or 'empty'}', completed={t.completed}")
        inv_field = (t.invoice_number or "").strip()
        c["4_invoice_field_empty"] = (not inv_field, f"Invoice Number field '{inv_field or 'empty'}'" + ("" if not inv_field else " — a second invoice on this PO needs the multi-invoice rule"))
    else:
        # history/backtest: report what the row holds; these two are enforced only live
        c["3_not_signed_off"] = (True, f"history row (was '{(t.signed_off or '').strip()}'); live check = field empty AND not completed")
        c["4_invoice_field_empty"] = (True, f"history row (holds '{t.invoice_number}'); live check = empty")

    # 5. supplier normalises to the task's Vendor
    sup_hits = suppliers.by_email_domain(sender_domain)
    sup_name = SupplierDirectory.short_name(sup_hits[0]) if sup_hits else ""
    vendor = (t.vendor or "").strip()
    v_tok, s_tok = name_tokens(vendor), name_tokens(sup_name) | name_tokens(sup_hits[0].get("vendor_field", "") if sup_hits else "")
    text_tok = name_tokens(text[:1500])
    if vendor and (v_tok & s_tok):
        c["5_supplier_matches"] = (True, f"vendor '{vendor}' ≈ directory '{sup_name}' (domain {sender_domain})")
    elif vendor and vendor.lower() in profiles.vendor_aliases.get(sender_domain.lower(), set()):
        c["5_supplier_matches"] = (True, f"vendor '{vendor}' paired with {sender_domain} on earlier signed-off invoices")
    elif vendor and (v_tok & text_tok):
        c["5_supplier_matches"] = (True, f"vendor '{vendor}' named in document")
    elif not vendor:
        c["5_supplier_matches"] = (False, "task has no Vendor")
    else:
        c["5_supplier_matches"] = (False, f"vendor '{vendor}' vs sender {sender_domain} ('{sup_name or 'not in directory'}') — no overlap")

    # 6. invoice number of the supplier's learned shape
    res.invoice_candidates = find_invoice_numbers(text)
    key = sup_name or vendor or sender_domain
    chosen, how = None, ""
    for cand, src in res.invoice_candidates:
        ok = profiles.matches(key, cand)
        if ok:
            chosen, how = cand, f"'{cand}' matches {key} profile ({src})"
            break
    if not chosen and not res.invoice_candidates:
        for cand in profiles.scan(key, text):
            chosen, how = cand, f"'{cand}' unlabelled but fits {key} profile"
            res.invoice_candidates.append((cand, "profile-shape scan"))
            break
    if not chosen and res.invoice_candidates:
        cand, src = res.invoice_candidates[0]
        prof = profiles.matches(key, cand)
        chosen = cand
        how = f"'{cand}' first labelled candidate ({src}); profile: {'none for supplier' if prof is None else 'MISMATCH'}"
        c["6_invoice_format"] = (prof is None, how) if prof is None else (False, how)
    elif chosen:
        c["6_invoice_format"] = (True, how)
    else:
        c["6_invoice_format"] = (False, "no invoice number found")
    res.chosen_invoice = chosen
    # sanity vs the answer key when we have it
    if t.invoice_number and chosen and chosen not in t.invoice_number and not re.search(re.escape(chosen), t.name):
        res.flags.append(f"CHOSEN≠ASANA({t.invoice_number})")

    # 7. gross reconciles to the penny
    amounts = find_amounts(text)
    exp = t.expected_gross
    net = round((t.price_ex_vat or 0) + (t.carriage_ex_vat or 0), 2)
    if exp is None:
        c["7_gross_reconciles"] = (False, "task has no Price Ex VAT")
    elif exp in amounts:
        c["7_gross_reconciles"] = (True, f"£{exp:.2f} = (£{t.price_ex_vat:.2f} + £{t.carriage_ex_vat or 0:.2f}) × 1.2 found")
    elif net in amounts:
        # the ex-VAT figures were entered from VAT-inclusive prices, or the document is net-only
        c["7_gross_reconciles"] = (False, f"expected gross £{exp:.2f} absent; net £{net:.2f} present — Asana values probably entered INC VAT, or invoice is one of several")
        res.flags.append("ASANA_VALUES_SUSPECT")
    else:
        doc_totals = sorted(a for a in amounts if a > 0)[-3:]
        if t.inc_vat and any(abs(a - t.inc_vat) < 0.01 for a in amounts):
            c["7_gross_reconciles"] = (False, f"Asana inc-VAT £{t.inc_vat:.2f} present but (price+carriage)×1.2=£{exp:.2f} is not — field inconsistency")
        elif "/" in (t.invoice_number or ""):
            c["7_gross_reconciles"] = (False, f"multi-invoice PO: task total £{exp:.2f} vs this document's largest amounts {doc_totals} — needs sum-of-invoices rule")
            res.flags.append("MULTI_INVOICE_PO")
        else:
            c["7_gross_reconciles"] = (False, f"expected £{exp:.2f} not in document (largest: {doc_totals})")
    if exp and exp > VALUE_THRESHOLD_GBP:
        res.flags.append(f"VALUE>{VALUE_THRESHOLD_GBP:.0f}")
        res.hard_stops.append(f"value £{exp:,.2f} above the £{VALUE_THRESHOLD_GBP:,.0f} always-look threshold")
    if history and res.chosen_invoice:
        dup = history.duplicate(key, res.chosen_invoice, sender_domain)
        if dup:
            res.flags.append("DUPLICATE"); res.hard_stops.append(f"DUPLICATE: {dup}")
        # multi-invoice running total: earlier invoices on this task + this one must not exceed the task gross
        prior = history.prior_total(t.gid, exclude_invoice=res.chosen_invoice)
        if prior and exp is not None and res.doc_gross is not None:
            if abs(prior + res.doc_gross - exp) < 0.01:
                c["7_gross_reconciles"] = (True, f"multi-invoice: £{prior:.2f} already recorded + this £{res.doc_gross:.2f} = task gross £{exp:.2f}")
                if "MULTI_INVOICE_PO" not in res.flags:
                    res.flags.append("MULTI_INVOICE_PO")
            elif prior + res.doc_gross < exp - 0.01 and not c["7_gross_reconciles"][0]:
                c["7_gross_reconciles"] = (False, f"multi-invoice in progress: £{prior:.2f} + £{res.doc_gross:.2f} = £{prior + res.doc_gross:.2f} of task gross £{exp:.2f} — not the last invoice yet")
    return res


# ------------------------------------------------------------------ report
def report(results: list[GateResult]) -> str:
    lines = ["| # | document | PO→task | 1 | 2 | 5 | 6 | 7 | verdict | invoice no. (gate) | flags |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    n_auto = 0
    for i, r in enumerate(results, 1):
        def s(k):
            return "✓" if r.conditions.get(k, (False,))[0] else "✗"
        verdict = "AUTO" if r.auto else "EXCEPTION: " + ", ".join([k.split("_", 1)[1] for k in r.failed()] + [h.split(" ")[0] for h in r.hard_stops])
        n_auto += r.auto
        lines.append(f"| {i} | {r.file[:52]} | {(r.task.po if r.task else '—')} | {s('1_po_resolves')} | {s('2_status_arrived')} | {s('5_supplier_matches')} | {s('6_invoice_format')} | {s('7_gross_reconciles')} | {verdict} | {r.chosen_invoice or '—'} | {', '.join(r.flags)} |")
    lines.append("")
    lines.append(f"**{n_auto} of {len(results)} would have cleared the gate automatically.**")
    lines.append("")
    for i, r in enumerate(results, 1):
        lines.append(f"**{i}. {r.file}**")
        for k, (ok, why) in r.conditions.items():
            lines.append(f"- {'PASS' if ok else 'FAIL'} {k}: {why}")
        if r.invoice_candidates:
            lines.append(f"- invoice-number candidates: {[c for c, _ in r.invoice_candidates][:5]}")
        lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixtures", help="labelled JSON (text + asana answer key)")
    ap.add_argument("--text", help="a single extracted invoice text file")
    ap.add_argument("--sender-domain", default="")
    ap.add_argument("--db", help="ops.db for the PO index (default: from path_config)")
    ap.add_argument("--out", help="write the markdown report here")
    a = ap.parse_args()

    suppliers = SupplierDirectory()
    profiles = SupplierProfiles()
    results = []
    if a.fixtures:
        recs = load_json(a.fixtures, [])
        index = POIndex.from_records(recs)
        # learn supplier invoice shapes from the answer key of OTHER records (leave-one-out),
        # so a supplier with a single historical invoice is honestly 'no profile'.
        for r in recs:
            hits = suppliers.by_email_domain(r["sender_domain"])
            key = SupplierDirectory.short_name(hits[0]) if hits else (r["asana"].get("vendor") or r["sender_domain"])
            others = [x for x in recs if x is not r and x["sender_domain"] == r["sender_domain"]]
            prof = SupplierProfiles()
            for o in others:
                prof.learn(key, o["asana"].get("invoice_number"))
                prof.learn_vendor(o["sender_domain"], o["asana"].get("vendor"))
            results.append(run_gate(r["text"], r["sender_domain"], index, prof, suppliers, file=r["file"]))
    else:
        text = Path(a.text).read_text(encoding="utf-8")
        db = a.db
        if not db:
            try:
                sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
                from path_config import p as _p
                db = _p("db.ops")
            except Exception:
                db = ""
        index = POIndex.from_ops_db(db)
        results.append(run_gate(text, a.sender_domain, index, profiles, suppliers, file=a.text))
    md = report(results)
    print(md)
    if a.out:
        Path(a.out).write_text(md, encoding="utf-8")


if __name__ == "__main__":
    main()
