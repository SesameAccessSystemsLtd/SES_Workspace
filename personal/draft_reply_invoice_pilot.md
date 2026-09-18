# Reply to Steven — invoice-pilot validation (task 1218155097481756)

DRAFT v3 — scope reframed per Giles: the SIS ask is a clean, crawlable invoice register/list + DB
we use to VALIDATE invoices against orders. Local reconciliation detail stays with us, not in the
SIS build spec. Ready to post on Giles's final go.

---

Steven — validation replies on the 10-document invoice pilot.

## Framing
I'll keep this at the level of what SIS should build — a **crawlable list / database of supplier
invoices** that we can then query and use to validate invoices against our orders. I'm deliberately
not handing you our internal day-to-day reconciliation formulas — those live with the purchasing
team; the register just needs to hold the fields that let the validation be done against it.

## 1. Intake + what lands in the register
The purchasing intake is `purchasing@sesameaccess.com`. There is meant to be an identifier rule that
categorises every inbound email carrying an invoice as an invoice, but it can miss cases because a
supplier "invoice" is not always a clean attached PDF:

- A **credit-card / payment-company supplier often issues no invoice** — you get a **receipt or an
  order confirmation** instead.
- Sometimes the invoice is only a **hyperlink to download**, not an attachment.
- Fallback today: we **screenshot the document or move it into an "invoices to be processed" folder**,
  then manually download and process it.

So there is a manual-groom step whenever the identifier can't produce a clean attached invoice. The
register should still be able to accept a row in those cases (marked with how it was captured), not
only perfect PDF-attachment invoices.

## 2. The register model that makes validation possible
The register is **one row per document** (an invoice value, or a credit-note value) linked back to
an order. Confirmed business reality: **one purchase order can accrue several invoices AND several
credit notes over its life**, and it can get messy. So each document row needs:

- **Supplier invoice / credit-note number** and **document type** (Invoice / Credit Note / Receipt)
- **Value** (per document)
- A **concept / context column** so a row can say what it's doing (e.g. 2nd of 3 invoices for PO X;
  credit note against invoice Y; receipt from an auto-card supplier, not a real invoice)
- A **date**
- The **order link** (see matching below)

## 3. Matching (how the register connects to orders)
Match by mapping the **supplier's order number or confirmation number against OUR order number**
first, then resolve the invoice against that order. Do not try to match invoice→order purely on a
value or a standalone supplier reference — the order-number/confirmation-number link is the reliable
key.

## 4. Validation anchor (this is the intended use of the register)
The register exists so that, for any order, the **set of documents against it can be validated**:

- **sum of all invoice values MINUS sum of all credit notes** for an order should reconcile to the
  order's **Price Ex VAT** value held on the Asana task (itself the validated quote value).
- This reconciliation is for **validation/verification only — it is never auto-written back** to
  Asana or used to authorise anything. It flags "this order looks fully invoiced / short / over /
  has an open credit note" so a human can look.

## 5. The ten-sample dispositions (in/draft confirmations)
| Document | Disposition |
|---|---|
| **Exact Precision SA-49** | invoice — register normally (has order context) |
| **2× Amazon on PO 21592-10926** | both register → the multiple-invoices-per-PO case; context column distinguishes |
| **KJN credit note 111772** | **credit note** (negative/offset doc, not an invoice) |
| **BSI 97148247** | invoice |
| **Apple 718180256797** | likely **receipt** (auto-card supplier rarely invoices) → type Receipt |
| **Thameside / Midland Battery** (scanned/no-text) | capture row, mark **scanned/no-text → manual entry**, never blank-silent |
| **eBay seller statement** | exclude as non-invoice, but **log the exclusion** with order context |
| **Deliberate duplicate** | **flag as duplicate** in the daily view, never auto-merge |

If any disposition is wrong, call it out and I'll correct.

## 6. Exceptions for the daily review view
None auto-actioned — a validation view only:
1. Missing PO/order match
2. Duplicate invoice number
3. Scanned / no-text (manual entry needed)
4. Context needed (multiple docs on one order unresolved)
5. Over-invoice / value-difference vs the order
6. Credit note present (offsets)
7. Receipt-not-invoice (so auto-card suppliers aren't miscounted)
8. Already-paid / awaiting-payment lifecycle status

## Safety boundary
Validation only. No payment, approval, Sage, bank-detail change, source-doc move, or sign-off is
requested or taken from this reply. Those remain human-controlled.
