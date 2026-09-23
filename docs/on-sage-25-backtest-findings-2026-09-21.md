# Supplier-invoice backtest — "On Sage 25"

**Run date:** 21 September 2026
**Corpus:** 1,000 files from `SAS Dropbox / 4. External Operations / 7.4 Purchasing Dept / Invoices - to be signed off / 3. INVOICES - Signed Off / On Sage 25`, spanning 18 March – 21 September 2026 (the folder holds more than 1,000; this is the 1,000 most recently modified). 297 MB total.
**Ground truth:** the filename convention `<PO> - <supplier> - <description> - <invoice no>`, written by a human from the source document and already validated through the Sage import.
**Companion file:** `on-sage-25-ground-truth.csv` — 573 parsed rows.

---

## Headline numbers

| Measure | Result |
|---|---|
| Files carrying a purchase-order reference | 582 / 1,000 (58.2%) |
| Files fully conforming to the naming convention | 573 (57.3%) |
| Files with **no PO at all** | 418 (41.8%) |
| PO resolves to exactly one Asana task | **18 / 18 sampled (100%)** |
| Invoice-number format stability per supplier, excluding Amazon | **94.5%** |
| Invoice-number format stability including Amazon | 80.1% |
| Distinct suppliers | 175 after name consolidation (191 raw spellings) |
| POs carrying more than one invoice | **94 / 421 (22.3%)** |

---

## 1. PO recognition works, and it works completely

Eighteen POs sampled across both tail forms and all six months. Every one resolved to exactly one Asana task whose name begins with that PO. No misses, no ambiguity.

This settles the architectural question. The trial's zero automatic matches were never a matching problem — recognising a PO against the live Asana index is close to a solved problem on this data. The failure was upstream.

### The PO grammar, confirmed across 582 files

Five-digit head, then a tail that is either a **project number** (4 digits, 1000–1999 — 327 files) or an **order date** (250 files). The date form has variable zero-padding on *both* day and month: `20771-030226`, `20774-30226` and `21056-28526` are all valid dates written three different ways. A fourth variant uses a full four-digit year (`21153-16062026`).

The practical consequence: a 5-digit tail cannot be reliably parsed standalone. It must be validated against the Asana task's Order date or PROJECT NAME field — which is exactly what makes recognition-against-index safer than pattern discovery.

### Separator and prefix variance

Observed in the wild: `-`, `/` (`21578/1595`), and in the document body a `PO` prefix with no separator (`Reference PO21114-1670`). Normalisation must strip a leading `PO` and accept any of these separators.

### Discovery-by-regex would produce false positives

Four Canva invoices in this corpus carry invoice numbers of the form `04936-16135165` — structurally identical to a PO. A regex hunting for PO-shaped strings matches them. Intersecting against the known PO set rejects them for free.

---

## 2. The finding that changes the design: one PO, many invoices

**94 of 421 POs (22.3%) carry more than one invoice.** Those 94 POs account for 246 of the 573 PO-backed files — **43% of the corpus**. The maximum observed is seven invoices against a single PO.

Examples:

```
20920-230326   x7   Amazon
21095-150626   x6   Amazon
21121-290626   x6   Amazon
21635-1597     x5   Aalco
21024-110526   x5   RS Components
```

Asana holds **one** Invoice Number field and **one** Invoice Value Inc VAT field per task. It cannot represent this. In the 18-PO sample, seven of the Asana invoice numbers differed from the Dropbox filename — and on inspection every single one was a multi-invoice PO where Asana recorded a different invoice from the same order.

This is a data-model problem, not an extraction problem. No amount of OCR accuracy fixes it. **It needs a decision before any field-writing phase**, because "write the invoice number into the task" is undefined when a PO has six of them. Options are roughly: a child record per invoice, a delimited list in the field (already happening by hand — one historic task reads `1281223 / 1316536`), or invoice rows held in `ops.db` with only a rollup in Asana.

---

## 3. Money: the reconciliation rule, confirmed and corrected

The Kerry Way invoice `SI-10912` extracts as:

```
Total Net 26.26   (goods 17.76 + Carriage Charge 8.50)
Total VAT  5.25
TOTAL     £31.51
```

Against Asana that would be Price Ex VAT 17.76, Carriage ex VAT 8.50, Inc VAT 31.51 — and `(17.76 + 8.50) × 1.2 = 31.51`.

**Carriage is a line item inside the supplier's net total but a separate field in Asana.** PR 124's described comparison of invoice net against Asana *Price Ex VAT alone* would mismatch by exactly the carriage on every invoice that carries any — which is most of them. Compare against `Price Ex VAT + Carriage ex VAT`, and cross-check `× 1.2` against Invoice Value Inc VAT.

One trap: the Bearing Supplier invoice presents `Subtotal £17.40`, `Tax £4.27`, `Shipping & Handling £4.75`, `Grand Total £25.63`. The shipping figure there is **VAT-inclusive** (3.96 net) while the subtotal is net. Mixed presentation on one document. Reconcile on the gross total, which is unambiguous.

---

## 4. Supplier invoice-number formats are highly predictable — except Amazon

Of 175 consolidated suppliers, **176 of 196 raw supplier keys show exactly one invoice-number shape**. Volume-weighted stability excluding Amazon: **94.5%**.

| Supplier | n | Pattern | Stability |
|---|---|---|---|
| Aalco | 13 | `ARD#######UE` | 100% |
| JAJO | 15 | `IJAS####` | 100% |
| Goodman | 13 | `########` | 100% |
| Drill Services | 12 | `INV######` | 100% |
| Tapeswitch | 19 | `AAA#####` | 100% |
| Ebay | 6 | `##-#####-#####` | 100% |
| RS Components | 26 | `##########` | 96% |
| Royal Mail | 22 | `####` | 82% |
| **Amazon** | **100** | 54 distinct shapes | **12%** |

Amazon is 17% of all PO-backed invoices and effectively unpredictable — `GB604KKPICAT5I`, `1QGN-FDRN-GC1T`, `INV-GB-143023051-2022-108938` all appear. It needs its own multi-pattern profile, and it is the single largest source of residual uncertainty in the whole pipeline.

Supplier naming also needs normalising before profiles are built: `tapeswitch`/`tape switch`, `thameside`/`thameside machining`/`thameside machining bom`, `exact`/`exact laser cut bom` are each one supplier under several spellings. Consolidation reduced 191 keys to 175.

---

## 5. Text extraction: mostly clean, but scans exist

A four-file probe (not yet a rate — the full pass over 573 files is the next step):

| File | Size | Result |
|---|---|---|
| Kerry Way `SI-10912` | 7 KB | Clean text; PO, invoice number, net/VAT/gross all present |
| Bearing Supplier `1000117353` | 2.5 MB | Clean text despite size — digital PDF with heavy images |
| Tracy Tools `147617` | 586 KB | Clean text |
| Thameside Machining `5711` | 4.6 MB | **Empty — image-only scan** |

Size is a usable predictor but not a reliable one. Only 2% of the corpus exceeds 1 MB and 14% exceeds 400 KB, so the scanned population is probably small. **Worth measuring properly before committing to any OCR spend** — if the scan rate is 2–3%, local OCR or manual handling covers it and no cloud OCR service is needed.

---

## 6. Two document classes the current design has nowhere to put

### Overheads with no PO — 418 files, 41.8% of the corpus

```
overhead invoice ("Invoice <no> <supplier>.pdf")   289
travel booking                                      32
PCN / penalty charge notice                         29
receipt                                             15
AutoEntry feed                                      12
credit note                                          6
advertising (Facebook)                               5
hotel folio, vehicle VAT schedules, mandates, other 30
```

These are Everhour, Google Cloud, RingGo, Allstar, Canva, Booking.com and similar. They have no purchase order and never will. They are **not match failures** — they are a separate class needing their own route, and neither the Azure worker nor the design as it stands has a home for them. Left unhandled they will dominate the exception queue and make it useless.

### Credit notes invert the convention

Three credit notes in the corpus put the PO at the **end** of the filename, not the start:

```
CREGB020022022 - Credit for replacing swage nuts with clinch nute - 20741-220126.pdf
Credit Note - Fabory 20551-111125 - 92612061.pdf
GB65Y63OAEUR - credit note - 20886-60326 Amazon.pdf
```

Another reason to scan the whole string for known POs rather than anchoring to position.

---

## 7. Some suppliers never quote the PO at all

The Bearing Supplier invoice contains **no reference to PO `21090-1670` anywhere in its text**. It carries only the supplier's own `Order # 1000123206` and `Invoice # 1000117353`. The human made that link from context, not from the document.

For this class, PO recognition from the invoice is impossible in principle. The only route is matching via the supplier's order reference — which validates the decision already taken to parse order confirmations for supplier references and keep them as a live reference list. On this evidence that is not an enhancement; it is required for a meaningful share of suppliers.

---

## 8. Duplicate-payment exposure is real, not theoretical

Seven invoice numbers in this corpus appear under **two different POs**:

```
GB61NFDKABEY   21121-290626 / 21121-300626
SA-42          21066-1010   / 21133-1580
INVGB020338524 20910-190326 / 21079-90626
W043877124     21149-1504   / 21150-70726
IN38970        21027-1572   / 21086-1572
GB61SKINABEY   21162-130726 / 21164-140726
```

Some will be genuine invoices split across two orders; some will be filing duplicates. Either way, a `(supplier, invoice number)` uniqueness check across both Asana history and prior payment batches is a required control, and it must be able to flag rather than silently reject, because the split case is legitimate.

---

## What this means for the build

**Confirmed and safe to build on.** Recognition against the live Asana PO index; the seven-condition high-confidence gate; per-supplier invoice-format profiles derived from history; gross-total reconciliation at `(Price Ex VAT + Carriage) × 1.2`.

**Needs a decision before any field-writing phase.** How one PO with several invoices is represented. This blocks phase two, not phase one — the agreed first phase of posting an Asana comment plus a Dropbox link is unaffected, since a comment per invoice carries no such conflict. That is a good reason to keep phase one as decided.

**Needs designing from scratch.** A route for the 42% of documents that have no purchase order.

**Needs measuring next.** The true text-extraction rate across all 573 PO-backed files. That single number decides whether OCR spend is warranted at all, and on a 2% scan rate the answer is probably no — which keeps the whole pipeline inside the local `ops.db` design already agreed.

**Open question for Giles.** In the `Inv3638229` case raised earlier, Asana's Invoice Number field holds `3638229`. Is that the document number recorded in error, or the genuine invoice number? It decides whether that row is a clean backtest case or a known-bad one.
