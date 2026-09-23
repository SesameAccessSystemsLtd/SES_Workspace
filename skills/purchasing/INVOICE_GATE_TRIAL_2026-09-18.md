# Invoice sign-off gate — sandbox trial and risk assessment

**Date:** 18 September 2026 · **For:** Giles (Purchasing) · **Mode:** read-only. Nothing was written to Asana, ops.db, the Email Gateway sheet or Dropbox.

## What was tested

Twelve signed-off supplier invoices from `3. INVOICES - Signed Off` (top level only), text extracted from the PDFs, run through the seven-condition gate with the matching Asana order task as the answer key. Suppliers: DQR, Erodatools, Southern Sheeting, BES, CPC, IronmongeryDirect, Metals4u (×2), Next Day Paint, ParcelHero/DHL, Peter Cooper, Amazon. A thirteenth row is a BES history stub (Asana fields only) so the supplier-profile mechanism could be exercised leave-one-out.

Harness: `skills/purchasing/invoice_gate.py` (fixtures in `skills/purchasing/fixtures/`). Re-run with `python invoice_gate.py --fixtures fixtures/signed_off_invoices.json`.

## Result

| # | document | PO→task | PO | status | supplier | inv. no. | gross | verdict |
|---|---|---|---|---|---|---|---|---|
| 1 | DQR Bellows — 12341 | 21016-1575 | ✓ | ✓ | ✓ | ✓ 12341 | ✓ £1,064.87 | **AUTO** |
| 2 | Erodatools proforma — 9815 | 21223-1588 | ✓ | ✓ | ✓ | ✓ 9815 | ✓ £1,518.05 | **AUTO** (flag PROFORMA) |
| 3 | Southern Sheeting — IN0262304 | — | ✗ | | | | | EXCEPTION: no PO in document |
| 4 | BES — 3638229 | 21623-100926 | ✓ | ✓ | ✓* | ✓* 3638229 | ✓ £34.32 | **AUTO** (*needs one prior BES invoice in history) |
| 5 | CPC — 7033849 | 21609-80926 | ✓ | ✓ | ✓ | ✓ 7033849 | ✓ £99.12 | **AUTO** |
| 6 | IronmongeryDirect — 8778653 | — | ✗ | | | | | EXCEPTION: no PO in document ("Your Order Number: NEWORDER") |
| 7 | Metals4u — 001830325 | 21620-1595 | ✓ | ✓ | ✓ | ✓ | ✓ £155.15 | **AUTO** |
| 8 | Next Day Paint — NDP566146 | — | ✗ | | | | ✗ | EXCEPTION: no PO in document; Asana values suspect (see below) |
| 9 | ParcelHero/DHL — 1520340 | — | ✗ | | | | | EXCEPTION: no PO in document |
| 10 | Peter Cooper — 79829 | 21571-250826 | ✓ | ✓ | ✓ | ✓ 79829 | ✓ £60.00 | **AUTO** |
| 11 | Amazon — GB6003GIJWSCXI | 21605-70926 | ✓ | ✓ | ✓ | ✓ | ✗ | EXCEPTION: one of four invoices on the PO — needs a sum-of-invoices rule |
| 12 | Metals4u — 001831681 | 21634-1594 | ✓ | ✓ | ✓ | ✓ | ✓ £33.40 | **AUTO** |

**7 of the 12 real documents would have cleared the gate automatically; every one of the 5 exceptions is a correct refusal, not a matcher miss.** On the documents where the gate passed, the chosen invoice number agreed with the Asana field in 7 of 7 and the gross reconciled to the penny in 7 of 7.

## What the trial confirmed

The three-problems reframe holds on real paper. Closed-set PO recognition worked on every document that carried the PO at all, including "Customer Ref : 21016-1575 Jenners" (DQR), "Delivery Instructions: 21623-100926" (BES), "PO # 21605-70926" (Amazon) and a column-scrambled proforma where the PO sat three lines below its label. The money check reconciled `(Price Ex VAT + Carriage ex VAT) × 1.2` to the penny on all seven passes. The invoice number was the only field that needed extraction, and the supplier profile rescued the one unlabelled case (BES prints it under "Invoice Date" with no label): with one prior BES invoice in history, the shape `#######` identified it.

Vendor normalisation is mandatory, as predicted: Asana holds "Bellows Supplier" for DQR, "Bas" for BES, "DHL" for a ParcelHero invoice, "cpc"/"metals4u"/"Iron Mongery direct " in mixed case with trailing spaces. The harness learns vendor↔sender-domain pairs from history and that resolved every mismatch in the set; `vendor_normalise.csv` covers the Laser Cut group but none of these.

## What the trial found that the design did not anticipate

**The 5-digit PO base is not unique.** `21222-1313` (Southern Sheeting) and `21222-1374` (Mark Bevins transport) both exist. Any matcher that trusts a bare `21222` — including the base-PO fallback in the email gateway's `03` before today — could land on the wrong task. The gate now accepts a bare base only when it resolves to exactly one PO and sits next to an order/PO label.

**Web-shop and courier invoices often carry no PO at all** (IronmongeryDirect, Next Day Paint, ParcelHero, Southern Sheeting: 4 of 12). The PO lives only in the Dropbox filename Giles typed. These can never clear condition 1 from document text; the correct behaviour is an exception that offers the filename PO as a *suggestion* for human confirmation, never as a match.

**Asana values are not always right.** Next Day Paint: the shop shows VAT-inclusive prices; Asana holds Price Ex VAT £279.96 + carriage £3.99 (the inclusive figures), so the derived gross £340.74 is £56.79 higher than the £283.95 actually paid. The gate refused it, which is what you want — but it means the gate will surface historical data-entry errors as exceptions, and the accounts batch should treat that as a feature.

**Multi-invoice POs are common on Amazon** (four invoice numbers on 21605-70926, three refs on the Southern Sheeting task). A single document cannot reconcile to the task total; the rule has to be "sum of invoices recorded so far + this invoice ≤ task gross, equal on the last one", with the running total kept locally (`email_documents` is the natural place).

**Proformas.** Erodatools' proforma passed all seven conditions, yet a proforma is not a VAT invoice and Asana's Invoice Number field was correctly left empty by the human. The gate must treat `PROFORMA` as a hard exception regardless of the seven conditions, or the automation will sign off documents a human deliberately did not.

**Deposits and re-issues.** Southern Sheeting's document is a re-issue with a deposit claimed; the totals on the page are net of the deposit. Any "DEPOSIT" / "Total Paid" / "Remaining to Pay" language should also force an exception.

## Risk register for going live

| Risk | Likelihood | Impact | Control |
|---|---|---|---|
| Wrong task signed off (PO mis-resolved) | Low after this trial; **high** if bare base POs are trusted | Wrong payment authorised | Full-PO closed-set only; bare base only when unique + labelled; supplier condition is an independent check |
| Duplicate invoice authorised (same supplier + number twice) | Medium — supplier resends, or one PDF processed from two folders | Duplicate payment | Hard uniqueness check on (supplier, invoice number) against Asana history *and* `email_documents` before any write |
| Proforma / deposit / statement signed off as an invoice | Medium (2 of 12 here) | Paying against non-VAT paperwork | PROFORMA / DEPOSIT / STATEMENT keywords → exception, always |
| Asana values wrong, gate passes anyway | Very low (needs the wrong value to appear on the document) | Incorrect amount recorded | Gross must match a printed total; net-only or inc-VAT-entered cases are refused and flagged `ASANA_VALUES_SUSPECT` |
| Large value cleared without eyes | — | Material payment error | Value threshold (£2,000 proposed) above which the row is always CHECK |
| Partial writes on failure | Low | Inconsistent task | All-or-nothing per task: Invoice Number + Signed OFF set together via the existing REQUESTS/SAP route, never direct |
| Human and machine sign-offs indistinguishable in audit | — | Payment-run audit fails | Machine always sets "Signed off by AI - AWAITING APPROVAL", never "Signed Off " |
| Text extraction fails on scans / column-scrambled PDFs | Medium (Erodatools' layout was scrambled but still passed) | Missed automation, not wrong automation | Failure = exception; benchmark Azure Document Intelligence only if exception rate on scans is material |
| Governance: this is a Tier 2 write under the SIS contract | — | Uncontrolled side effect | Batch-level approval (per accounts batch), not per line; approval token bound to the batch; run receipt per batch |

## Recommended sequence

1. **Backtest at scale, still read-only.** Point `invoice_gate.py` at the last 12 months of `3. INVOICES - Signed Off` (all subfolders, including "On Sage 25") with Asana as the answer key. The number that matters is the auto-clear rate and, more importantly, zero false AUTOs. This trial suggests roughly 55–65% auto on supplier invoices and near-zero on web-shop/courier paperwork.
2. **Mirror the missing fields into ops.db.** `procurement_tasks` needs `carriage_ex_vat`, `invoice_number` and `signed_off` for the gate to run locally; the harness is schema-tolerant and will use them as soon as the sync provides them.
3. **Wire the running total.** Record each invoice in `email_documents` (already built) so multi-invoice POs reconcile cumulatively and the duplicate guard has local history.
4. **Then a shadow run on live inbound invoices**: the gate writes its verdict into the Email Gateway sheet as `Suggested = A (sign-off)` with the seven reasons, and Giles approves the batch. No Asana field changes by the machine until two clean shadow weeks.
5. **Only then** the AI-AWAITING-APPROVAL write, via SAP, batch-approved.
