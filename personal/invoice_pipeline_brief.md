# Invoice Processing Pipeline — Implementation Brief

**Prepared for:** Giles Hugo (Purchasing Manager)
**Date:** 2026-09-02
**Status:** Draft for review
**Purpose:** Define what must happen, end-to-end, so inbound supplier invoices become a reliable, auto-populated live register we can act on.

---

## 1. The core enabler (the dependency everything sits on)

**SIS must attach every supplier invoice into a single SharePoint repository.**

Invoices reach us today as email attachments in the `purchasing@sesameaccess.com` mailbox and flow through the 5-stage email pipeline. That path is **unreliable for invoicing**:

- Attached invoice PDFs are **never parsed** — nothing reads them today.
- The `INVOICES TO PROCESS` mailbox folder the pipeline expects is **missing** (observed 30-Aug = "folder not found, skipping").
- Parsed email quality has regressed (30 parse-level errors on the active 1037 thread) — emails are a lossy, fragile inbound channel to bank on for the financial register.

**Therefore the durable source of truth for inbound invoices is a SharePoint folder, fed by SIS, not the email inbox.**

### What "SIS attaches" means (the ask of SIS / the automation owner)
- Every invoice (supplier PDF + any supporting doc) lands in one agreed SharePoint library/folder.
- One invoice per file (or a predictable per-invoice grouping) so files map 1:1 to invoice documents.
- Standardised file name — the minimum to make registration reliable without opening the file.
- Attachment is **automatic** (part of SIS's post-purchase flow), not a manual "remember to upload" task.

### Proposed SharePoint structure
```
Invoice Inbox  (sharepoint library)
├── _Inbound/          ← SIS drops invoices here (the trigger folder)
├── _Registered/       ← pipeline moves processed files here (post-registration)
└── _Queries/          ← manual/exception handling working area
```

---

## 2. The end state we're building toward

A **live register** (SharePoint List is the natural home since the files live there) that auto-populates from the inbound folder, so Giles can at a glance see:

> **What invoices have come in, their number + gross value + supplier, what they relate to, and their status** — without opening a single PDF.

The register is **add-only and reliable**: it reflects exactly what SIS attached, nothing silently dropped.

---

## 3. Recommended implementation sequence (staged, each independently valuable)

### Stage 1 — Architecture decision: which list?
Giles is actively reviewing **Asana vs ops.db/SharePoint as source of truth** (the SIS fundamental-principles work). Invoicing is a live case that should **follow that decision** — do not lock the register into Asana if the strategic answer is "financial/post-purchase records live in SharePoint/ops.db." **Recommend: physically co-locate the register List with the invoice files in SharePoint** (one system of record for the inbound financial doc), and have Hermes read it (not write a parallel store).

### Stage 2 — Stand up the repository + SIS auto-attach (the hard dependency)
1. Confirm SIS can reach/write SharePoint (which tenant, which app identity, which permissions — read/write on the library).
2. SIS issues a POST to create the file + an item in the List at the moment an invoice is generated/approved.
3. Validate a real end-to-end pilot: 10 live invoices uploaded by SIS, confirm each produces one list row.

### Stage 3 — Ingest + auto-populate the register
- Hermes/ops polls the List (or a webhook fires) for new items.
- On a new item: parse the attached PDF (`pdfplumber`) to extract the financial fields (see below).
- Write/extend the List row → **live register of invoice number + value + supplier + match**.
- Optionally mirror `invoice_number` + `gross_value` into `ops.db` for reporting joins.

### Stage 4 — Act on it
- Status lifecycle: **Received → Matched to order/PO → Cross-checked → Paid/pending → Exception**.
- Match the invoice to its order (via supplier + PO/SQR ref) so it can be *acted on*, not just *registered*.

---

## 4. List fields to auto-populate

This is the schema the register needs. Split into what the **processor extracts from the PDF** vs what it **derives/matches**.

### Section A — From the invoice PDF itself (auto-extracted)
| Field | Why | Confidence |
|---|---|---|
| **Invoice Number** | Primary identity of the document | High (parser) |
| **Invoice Date** | Register chronology + overdue logic | High |
| **Gross Value (£)** | The headline financial number ("invoice values") | High |
| **Net Value (£)** | Detail/margin sanity | High |
| **VAT (£)** | VAT reclaim + split | Medium |
| **Supplier Name** | Who billed us | High |
| **Supplier Ref / PO Ref** | Link to our order | Medium-High |
| **Currency** | Non-GBP handling | High |

### Section B — Derived / matched by the pipeline
| Field | Source / basis |
|---|---|
| **SQS / our PO number** | Matched from supplier ref against `procurement_tasks` / `rfq_outbox` ledger |
| **Job / Project (1037, 1595…)** | Via the matched order |
| **Linked Asana order task** | The order the invoice belongs to (for the "act on it" step) |
| **Status** | Received → Matched → OK → Exception (lifecycle) |
| **Days open / overdue** | Computed from invoice date |
| **Attachment (PDF link)** | Link back to the source file in SharePoint |

### Section C — Manual / exception (not auto)
| Field |
|---|
| **Payment status / paid date** |
| **Discrepancy note** (value mismatch, wrong supplier, returns) |
| **Approved/paid by** |

---

## 5. Known gaps today that this resolves (grounding)
- Invoice PDFs are not parsed anywhere (nothing reads attachments).
- `INVOICES TO PROCESS` mailbox folder is missing → email-only routing is broken/absent for invoices.
- The email parser regressed on the live 1037 thread → don't make the financial register depend on that channel.
- The **invoice-vs-confirmation** handling rule (Giles, 2026-08-28) already exists conceptually but is **not built** — this brief is the concrete build spec for the invoice half.

---

## 6. What I need back from you
1. **Confirm SIS is the intended automatic uploader** and who owns enabling its SharePoint write (SIS admin / you).
2. **Confirm strategic choice** for the register home (recommend: **SharePoint List co-located with the files**, per SIS-as-source-of-truth direction) vs ops.db-vs-Asana if that decision lands differently.
3. **Sign off the list fields** (Section A/B/C) or tell me what to add/remove.
4. **Value precision**: is "value" always gross-with-VAT, or do you need it split (net / VAT / gross)?

---

## 7. The "act on it" note (why register-first matters)
Once invoices are reliably registered with number + value + supplier + order link, **two downstream actions unlock that are impossible today**:
- **Overdue / un-confirmed work**: match invoices against `procurement_tasks` the same way the chase logic already does (live-Asana-gated) so a PO that has been *invoiced* stops being chased.
- **Forecasting + management reporting**: a clean inbound-invoice register is the financial counter-signal the whole 2026 "purchasing done properly" objective needs — the same story as the stock-add-vs-remove gap: you can't manage what you can't reliably see inbound.
