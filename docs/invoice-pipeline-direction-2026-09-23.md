# Supplier-invoice pipeline — direction and current state

**Date:** 23 September 2026 · **Owner:** Giles (Purchasing) · **Asana:** task 1216455499468283
**Supersedes** the SharePoint-List framing in the original brief. The register is `ops.db`; the files are Dropbox; Asana is the operational view.

---

## 1. Decisions taken

**Extraction is text-first, escalating to Azure AI Document Intelligence (D → A).** The local extractor and the seven-condition gate run on every document. Document Intelligence is called only when the local path cannot produce a confident answer *and* the document is worth the call. One DI call returns typed fields (InvoiceId, PurchaseOrder, VendorName, InvoiceTotal, SubTotal, TotalTax, InvoiceDate, each with a confidence) and the full text layer; the gate re-runs on that text so closed-set PO recognition works on OCR'd documents.

**The accounts / AutoEntry route is not part of this pipeline — and it handles everything purchasing doesn't.** Accounts pull every invoice into Sage through AutoEntry. Documents with a PO in the title resolve there; documents without one (overheads, card payments, PCNs, receipts — 42% of the signed-off corpus) are simply coded on Sage by accounts. Nothing for purchasing to do on those. AutoEntry does not extract PO numbers and cannot match more than one invoice to a PO, so it could never serve purchasing's question; purchasing's question is answered here, and the handoff to accounts is the signed-off Dropbox folder with the `<PO> - <supplier> - <description> - <invoice no>` filename. **That filename is an interface contract with accounts, not a convenience.**

**Traceability lives in Asana (amended 23 Sept, afternoon).** Every processed invoice leaves three things on its order task: a line appended to the description with the invoice number, gross, supplier and the Dropbox link; the new custom field **MAPPED USING AI** (`1218748889949686` — `100%` / `Requires HITL` / `NOT MAPPED`) recording how the mapping was achieved; and, after the human signs off and the file moves, a second description line with the final Dropbox link, who signed off and when. Phase 1 therefore *does* write to Asana — the MAPPED USING AI field and description appends, via REQUESTS/ → SAP, only after Giles's sheet decision. It still never sets Invoice Number or Signed OFF.

**The register is `ops.db` (`invoice_documents`), one row per invoice or credit note, many rows per PO.** Asana keeps one Invoice Number field per task and cannot represent multi-invoice POs or credit notes; the practice had already drifted (slash-separated, then numbers in the description, task name showing only the last invoice). That stops being a problem once the record lives locally and Asana carries a comment + Dropbox link per document.

**Phase 1 posts an Asana comment plus a Dropbox link only.** The machine sets neither Invoice Number nor Signed OFF. When field-writing is authorised it goes via REQUESTS/ → SAP, batch-approved, and always sets "Signed off by AI — AWAITING APPROVAL", never "Signed Off".

**Order-confirmation references are a matching aid, not a match.** `02_llm_parser` already extracts `supplier_order_ref` / `supplier_quote_ref`; the gate surfaces them as a SUGGESTION on documents that carry no PO. Their reliability grows through the sheet feedback loop; they earn the right to satisfy condition 1 per supplier, not globally (see §5).

---

## 2. What is set up today

### Email gateway (`skills/purchasing`, live, hourly 09–16 Mon–Fri)
`01_email_fetcher` (Graph, purchasing@) → `02_llm_parser` (Gemini: document type, Sesame PO, SQR, supplier quote/order/invoice refs, line items) → `03_proposal_engine` (match against `ops.db`) → `04_sheet_writer` (Email Gateway sheet, Pending tab) → Giles A / M / R → `05_sheet_approver` → `queues/REQUESTS/` → SAP (sole Asana writer). Raw emails archived to `data/email_archive/`; every parse lands in `email_documents` + `email_line_items` via `db_writer`. Feedback loop in `learn.py`; rules in `data/gateway_rules.json`.

### Invoice gate (`skills/purchasing`, built 18 Sept, **shadow mode**)
`06_invoice_processor.py run` (hourly at :20) takes invoice PDFs from two intakes — attachments archived by `01`, and PDFs dropped at the top of Dropbox *Invoices - to be signed off* — extracts text via `skills/pdf_extractor`, and runs `invoice_gate.run_gate()` against **live** Asana task fields (`asana_read.py`, GET only).

Seven conditions, all deterministic: (1) PO recognised in document text against the live PO index → exactly one task · (2) ORDER Status = ✅ARRIVED · (3) Signed OFF empty and task not completed · (4) Invoice Number empty · (5) supplier ≈ task Vendor (learned vendor↔sender-domain pairs + `vendor_normalise.csv`) · (6) invoice number fits the supplier's learned shape · (7) `(Price Ex VAT + Carriage ex VAT) × 1.2` printed on the document.

Hard stops regardless of the seven: PROFORMA · DEPOSIT · STATEMENT · CREDIT NOTE · DUPLICATE (supplier + number, against Asana history and `invoice_documents`) · value > £2,000.

AUTO → *AI SIGNED OFF/* · CHECK → *2. invoice queries/* (with a SUGGESTED task when a confirmation ref matches) · row on the sheet's **Invoices** tab with the seven reasons, flags, Dropbox link and proposed comment · Giles decides A / M / R / S · `06 approve` (every 30 min) → REQUESTS/ → SAP posts the comment + link · human sets Signed OFF · `07_invoice_mover.py` (nightly 01:30) moves the PDF to *3. INVOICES - Signed Off* and queues the follow-up comment.

`GATEWAY_SHADOW=1`: everything runs into the sheet only; no `ops.db`, Dropbox or Asana effect; shadow rows kept in `invoice_gate_shadow_rows.json` for later replay. `INVOICE_FILING_ENABLED=1` turns Dropbox filing on. Trial and risk register: `INVOICE_GATE_TRIAL_2026-09-18.md` — 7 of 12 real documents cleared AUTO, 5 correct refusals, 7/7 invoice numbers and 7/7 gross values agreed with Asana.

### Azure read-only worker (SIS, PR 124 — parked)
`sis-email-1595-monitor-v01` ran two scheduled read-only shadow checks on 10–11 Sept (Report 0087) and is restored to Manual, unscheduled, retry 0. PR 124 is draft, open, unmerged; `PERMANENT_RELEASE: NOT_ELIGIBLE`. It implements the same gate in a different codebase with no OCR and a Price-Ex-VAT-only value comparison. **Recommendation:** the local gate is the production path; PR 124 stays parked. If Steven wants a redundant hosted monitor later it should consume the same `invoice_gate.py` logic rather than a parallel implementation.

### Delivered today
`skills/purchasing/extract_di.py` — the D → A escalation module. Text-first policy (`should_escalate`), F0 limits enforced locally (4 MB, first 2 pages), SHA-256 result cache (a document is never analysed twice), monthly page ledger with a cap (`DI_MONTHLY_PAGE_CAP`, default 450 under F0's 500), `DI_MODE=off|dry|live`, typed `DIResult` with `.hints()` for the gate. `test_extract_di.py` passes (parse, policy, cache, cap, mode handling). **Not yet wired into `06`** — see §4.

---

## 3. What the On Sage 25 backtest established (1,000 files, Mar–Sep 2026)

| Measure | Result |
|---|---|
| Files carrying a PO | 582 / 1,000 (58%) |
| Files with **no PO at all** — overheads, card payments, PCNs, receipts, AutoEntry feed | 418 (42%) |
| PO resolves to exactly one Asana task | 18 / 18 sampled |
| POs carrying more than one invoice | 94 / 421 (22%) — 43% of all PO-backed files; max 7 on one PO |
| Supplier invoice-number shape stability, ex-Amazon | 94.5% |
| Amazon (17% of PO-backed volume) | 54 shapes in 100 files; 12% stable |
| Same invoice number under two different POs | 7 cases |
| Text extraction probe | 3 of 4 clean incl. a 2.5 MB file; one 4.6 MB scan empty; 2% of corpus > 1 MB |

PO grammar confirmed across 582 files: 5-digit head; tail is a 4-digit project number (1000–1999) **or** an order date DMY with variable zero-padding on day *and* month (`030226`, `30226`, `28526`), occasionally a full year (`16062026`). Separators `-` and `/`; `PO` prefix with no separator in document bodies. Four Canva invoice numbers (`04936-16135165`) are PO-shaped — closed-set recognition rejects them, regex discovery would not. Three credit notes carry the PO at the *end* of the filename.

Full findings: `docs/on-sage-25-backtest-findings-2026-09-21.md` · ground truth: `docs/on-sage-25-ground-truth.csv`.

---

## 4. D → A rollout — concrete wiring

**In `06_invoice_processor.cmd_run`, after `text = pdf_text(item["path"])` and the first `run_gate()`:**

```
esc, why = extract_di.should_escalate(text, has_po=bool(res.task), has_known_supplier=bool(sd),
                                      gate_reasons=[v[1] for v in res.conditions.values() if not v[0]])
if esc:
    d = extract_di.analyze(item["path"].read_bytes(), item["sha256"], why=why)
    if d.source in ("live", "cache") and d.content.strip():
        res = run_gate(d.content, sd, index, profiles, suppliers, file=item["original"], live=True,
                       hints=d.hints())           # hints: candidate invoice no. + printed gross, never authority
        res.flags.append(f"DI:{d.source}")
```

**In `invoice_gate.run_gate`:** accept an optional `hints` dict; when condition 6 finds no candidate, try `hints["invoice_number"]` *only if* it fits the supplier's learned shape; when condition 7 finds no printed total, accept `hints["printed_gross"]` *only if* it equals the Asana-derived gross to the penny. DI never overrides a document that already passed.

**Grammar:** `PO_TOKEN` currently `(2\d{4})…(\d{4,6})`. Widen the head to `[12]\d{4}` (older POs on late invoices, e.g. `17914-1496`, `19369-1533`) and the tail to `\d{4,8}` (full-year dates). The index still decides validity, so the wider net costs nothing.

**Config** in `.hermes/.env`: `AZURE_DI_ENDPOINT`, `AZURE_DI_KEY`, `DI_MODE=dry` to start. Creating the Azure resource is a Steven gate (new resource, F0 tier, zero spend). Verify `DI_API_VERSION` against the current docs before `live`.

**Sequence:** `DI_MODE=dry` for one week inside shadow mode → read `data/di_usage.json` and the dry-run log to see how many documents *would* escalate and why → Steven approves the F0 resource → `DI_MODE=live`, still shadow → compare AUTO rate with and without DI on the same documents → only then leave shadow.

---

## 5. Lining up the confirmation parsing

Today's chain: `02` extracts `supplier_order_ref` / `supplier_quote_ref` from confirmations, quotes and dispatch notes → stored in `email_documents` (keyed by sender domain, linked to the proposed/final task) → `invoice_gate.InvoiceHistory.suggest_from_confirmations()` looks those refs up when an invoice carries no PO → returned as a SUGGESTION on the CHECK row, never as a match. Bearing Supplier's `Order # 1000123206`, IronmongeryDirect's "NEWORDER", ParcelHero — this is the only route by which those can ever be matched.

**Graduated trust, per supplier, not global.** The refs are LLM-extracted and Giles's own assessment is "not fully reliable but will grow". So: a confirmation ref may satisfy condition 1 as `PO_BY_CONFIRMATION` only when (a) the same sender domain has had ≥ N (propose 5) SUGGESTED rows approved unchanged by Giles, (b) the supplier matches (condition 5), and (c) the money reconciles (condition 7). All three, or it stays a suggestion. `learn.py` already records A/M/R per row; the counter is a query on that history, not new state.

**Feed the gate's outcome back to the parser.** When Giles approves a SUGGESTED row, the (sender domain, supplier ref → task) pair is confirmed truth; write it back to `email_documents.final_task_gid` (already the field `05` uses) so the next confirmation from that supplier proposes better. That closes the loop in both directions with no new tables.

**Two data-hygiene items that will bite the join otherwise.** `procurement_tasks.po_number` is not a clean key (supplier refs and invoice numbers were typed into it — PIPELINE_OPS trap #6); the PO index must keep building from the task *name* prefix. And `vendor_normalise.csv` covers the laser-cut group only; the learned domain↔vendor pairs in `data/suppliers.json` are what actually resolves supplier identity — keep extending those from feedback rather than the CSV.

---

## 6. Gaps, in order

1. **Classify no-PO documents as `NOT_OURS` or `CHECK`, never as a purchasing list.** Accounts handle the no-PO population through AutoEntry, so the gate's only job on those is to stay out of the way — but it must not throw away the ones that *are* purchasing's. Rule in `06` before `run_gate`: no PO token **and** no known purchasing supplier (not in `data/suppliers.json`, no vendor↔domain pair in history, no confirmation from that domain in `email_documents`) → `NOT_OURS`: a log row on the sheet so the "no email missed" requirement is auditable, no Dropbox filing, no DI call, no Asana action. No PO token **but** a known purchasing supplier → `CHECK` with the confirmation-ref suggestion, exactly as today — this is the Bearing Supplier / IronmongeryDirect class and dropping it would be the missed-email failure Giles flagged. The Payment field ("Credit Card" vs "Account (DEFAULT)") is the corroborating signal for the card class.
1a. **Asana traceability (small, and first, because it's what Giles sees).** (i) In `06 approve`, replace the `comment_only` request with `append_to_description` — SAP already supports it — carrying `Invoice <no> · £<gross> inc VAT · <supplier> · file: <dropbox url> · mapped: <100%|HITL|not mapped> (<n>/7) · <date>`. (ii) In the same request, set MAPPED USING AI via the existing `custom_field_enum` op: sheet decision A on an AUTO row → `100%` (`1218748889949687`); A/M on a CHECK row → `Requires HITL` (`1218748889949688`); S (Giles picked the task by hand, machine found nothing) → `NOT MAPPED` (`1218748889949689`). Never written before a sheet decision. (iii) In `07`, replace the follow-up comment with a second `append_to_description` line: `Signed off by <name> on <date> · filed: <final dropbox url>` — the name and date come from the task's stories (GET only, `asana_read`), the story where Signed OFF changed to "Signed Off ". (iv) If Giles also wants the link in the task's Attachments pane, that is one new SAP action (`attach_external_link`, POST /attachments with `resource_subtype=external`) — optional, the description line is the searchable one and matches Richard's current practice.
1b. **Assert the filename contract.** Add a test in `test_gateway.py` that `filed_name()` produces exactly `<PO> - <Vendor> - <description> - <invoice no>.pdf` for a known task — accounts' AutoEntry intake depends on it.
2. **Sum-of-invoices rule for multi-invoice POs** (22% of POs). Already identified in the trial; the running total belongs in `invoice_documents`. Rule: recorded-so-far + this ≤ task gross, equal on the last one; a single document reconciling on its own is AUTO, a cumulative reconciliation is AUTO with flag `PARTIAL_n_of_m`.
3. **Amazon profile with multiple accepted shapes.** 17% of volume, 54 shapes; a single-shape profile refuses most of them. Accept any shape seen ≥ 2 times in history for that domain.
4. **Duplicate-across-POs.** Seven invoice numbers appear under two POs in six months. The duplicate guard must flag, not silently reject — some are legitimate splits.
5. **`extract_di` wiring** (§4) and the grammar widening.
6. **Full extraction pass over the 573 PO-backed On Sage 25 files** with `invoice_gate.py --fixtures` to get the real AUTO rate and the real scan rate. Run once with DI off, once with `DI_MODE=dry`. That number sizes the DI dependency and is the evidence for leaving shadow mode.

---

## 7. Gates before any Asana field write

Two clean shadow weeks · zero false AUTOs on the full backtest · `NOT_OURS` classification in place so the exception list is readable · batch-level approval by Giles on the sheet · every write via REQUESTS/ → SAP · "Signed off by AI — AWAITING APPROVAL" only · run receipt per batch.

Already in scope for Phase 1 (after a sheet decision, via SAP): MAPPED USING AI and description appends. Not in scope until the gates above pass: Invoice Number, Signed OFF.

## 8. Housekeeping

Log this thread before any further code lands (`HERMES_DEV_LOGGING_RULE.md`):

```
.venv\Scripts\python.exe skills\os_portal\dev_register.py log ^
  --objective "Invoice gate escalates text-first to Document Intelligence; no-PO documents routed to their own list; confirmation refs earn per-supplier trust; full On Sage 25 backtest run" ^
  --where "skills/purchasing/extract_di.py, 06_invoice_processor.py, invoice_gate.py" ^
  --channel claude-cowork --area "Ordering / Inventory" --effort L --priority High
```

`config-loader.ps1` in `skills/invoice-processor` still holds live credentials in plain text and is not git-ignored. That skill is superseded by `skills/purchasing`; rotate the two secrets and retire the folder.
