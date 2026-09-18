# Report — Capturing the "PO sent to" supplier email against the order, and validating the parts on that order

**Prepared for:** Giles Hugo (Purchasing Manager)
**Prepared by:** Hermes
**Date:** 2026-09-09
**Status:** PROPOSAL ONLY — no code, config, or DB changes made. Nothing has been written or sent.

---

## 1. Why this report exists

Two findings from today's overdue-chase assessment:

1. **The chase criteria are already correct.** An order qualifies for chasing iff:
   - the **due date has passed**, AND
   - the live Asana `ORDER Status` custom field = **`⏰On Order - Awaiting CONFIRMATION`**, AND
   - the live Asana task is still **incomplete**.
   The live-gated scan already applies exactly this and correctly dropped 106 finished/invoiced POs. No change needed to the criteria.

2. **The real gap is upstream, not in the chase logic.** Of 35 genuinely-open overdue POs, only **8** could resolve a supplier email to chase; **27 had no email matched at all**. The chase tool cannot find a recipient because *the supplier email address we sent the PO to is not recorded against the order anywhere in the DB.*

You described this precisely: when a purchase order is emailed to a supplier from the centre box, the supplier address (and the PO itself) is not written back to the DB against the order — nor is the set of parts on that order validated/recorded.

This report quantifies the gap and proposes the most efficient fix. **Nothing is implemented.**

---

## 2. Current state — what actually exists (verified against `ops.db`, read-only)

| Table | Rows | Relevant columns | Population reality |
|---|---|---|---|
| `procurement_tasks` | **4,087** | `po_number`, `vendor_name`, `due_date`, `order_status`, `asana_task_gid` | The **chase source**. Has **NO email/contact column at all.** 1,536 rows carry a PO number; 3,964 a vendor name; **146** currently `Awaiting CONFIRMATION`. |
| `purchase_orders` | **7** | `po_number`, `supplier_name`, **`supplier_email`**, `asana_task_gid`, `source_email_id` | `supplier_email` **exists but is NULL on all 7 rows**. Written only by the archived `apply_proposals.py` / `db_writer.write_purchase_order()`. Not used in live flow. |
| `po_lines` | **0** | `po_id`, `sesame_part_no`, `ordered_qty`, `asana_task_gid`, `bom_req_id` | The intended **PO → parts** association table. **Completely empty — never populated.** |
| `supplier_contacts` | **210** | `supplier_name`, `email`, `phone`, `source`, `is_primary` | Per-supplier contacts (from the Supplier project sync + captured quote senders). Useful fallback, but a company may have several contacts — not the same as *the address the PO was sent to*. |
| `email_po_matches` | **22** | `email_subject`, `extracted_po_number`, `asana_task_gid_matched`, `match_confidence` | **Inbound-only** email→PO matching. Carries **no supplier email address**. |

### The two structural gaps

- **Gap A — recipient not captured:** no table in the live path holds "the address this PO was emailed to." `purchase_orders.supplier_email` is the right home but is empty; `procurement_tasks` (where chasing reads) has no email column.
- **Gap B — parts not validated against the order:** `po_lines` is empty, so there is no record of *which parts belong to which PO*. This is why you noted we also need to "validate what parts are associated with that order."

### The mechanism gap

**No script reads the purchasing mailbox Sent Items.** Outbound POs are sent **manually from Outlook** (the "centre box"), so the recipient address only ever exists inside the sent message — never returned to the DB. This is the single root cause of the 27/35 unresolvable chases.

---

## 3. Proposed approach — capture at the point of sending, from Sent Items

**Principle:** don't add a new step to your manual workflow. The PO email is *already* sent from the centre box; the durable, zero-effort capture point is **the Sent Items folder of `purchasing@sesameaccess.com`**, which already contains the PO, its recipient, and its attachments.

### 3.1 The recommended design — a "Sent-PO capture" sync (read-only on mail, write-back to DB)

A new script (e.g. `capture_sent_pos.py`) that runs on a schedule (hourly is ample; it can also piggyback the existing hourly purchasing pipeline):

1. **Read Sent Items** via MS Graph (`GET /users/purchasing@sesameaccess.com/mailFolders/sentitems/messages`) since the last cursor — reuses the exact MSAL confident-client auth already proven in `create_rfq_draft.py` / `01_email_fetcher.py`.
2. **Identify PO emails** by a reliable signal — subject containing a PO reference (e.g. `PO 209xx-xxxx`) and/or a PO attachment/`toRecipients`. (Detection rules to be confirmed with you — see §5.)
3. **Extract, per PO email:**
   - the **recipient address(es)** actually sent to (`toRecipients` / `ccRecipients`),
   - the **PO number** (subject/body/attachment),
   - the **sent timestamp** and Graph `internetMessageId`/`conversationId`,
   - any **attachment** that is our PO document.
4. **Write back:**
   - `purchase_orders` — upsert by `po_number`: set `supplier_email` (the real address sent to), `supplier_name`, `source_email_id` (the sent message id), `order_date`.
   - `po_lines` — populate the PO→parts association (see §3.2).
   - (Optionally) mirror the resolved address onto `procurement_tasks` — or, cleaner, resolve it at chase time via a join. **Recommend a JOIN, not denormalisation** (see §4).
5. **Idempotent + auditable:** dedupe on the sent message id; log every write via the existing `db_write_log` (`db_writer.py` already does this). Never overwrite a human-confirmed address without logging.

**Why this is the most efficient option:** it requires **no change to how you send POs** (you keep sending from Outlook exactly as now), it captures the *true* address rather than a guessed "primary contact," and it reuses auth + write plumbing that already exists.

### 3.2 Validating the parts associated with the order

To close Gap B, pair the capture with a **parts-validation step**:

- **Source of truth for "what's on the PO":** the PO document attached to the sent email (or the PO body). Parse it with `pdfplumber` (already the planned tool for invoice PDFs) / openpyxl where the PO is a workbook.
- **Cross-check against what the system already knows:** the order's part tasks. For a PO task, its part lines already exist as Asana subtasks (the `{qty} off {part} - …` tasks) and/or as rows in `ordering_queue` (141 rows, with `sesame_pn`, `order_task_gid`, `vendor_name_at_order`) and `bom_requirements`.
- **Write the association into `po_lines`:** one row per part on the PO (`po_id` → `sesame_part_no`, `ordered_qty`, `unit_price`, `asana_task_gid`, `bom_req_id`).
- **Flag mismatches rather than silently accept:** where the PO document's parts disagree with the parts recorded against the order (missing, extra, qty mismatch), emit a **review item** — do not auto-correct. This is the "validate what parts are associated with that order" half of your ask, done as *validation + flag*, not silent write.

This also unlocks later value you already wanted: PO↔parts↔BOM traceability, and per-part delivery/QC status.

---

## 4. Design decisions (recommended, for your sign-off)

| Decision | Recommendation | Rationale |
|---|---|---|
| Where to store the sent-to address | **`purchase_orders.supplier_email`** (already exists) | Purpose-built column; avoids schema churn. |
| Whether to also copy it onto `procurement_tasks` | **No — resolve at query time via JOIN** (`procurement_tasks.po_number` → `purchase_orders.po_number` → `supplier_email`) | Single source of truth; avoids the DB-lag problem already seen with `order_status`/`due_date`. The chase tool joins instead of trusting a duplicated field. |
| Capture trigger | **Sent Items sync** (scheduled) | No change to your manual send habit; captures the address *actually used*. |
| Fallback when no sent email found | **`supplier_contacts`** (primary contact) marked `source='fallback'` — surfaced as "unverified address — confirm before chasing" | Keeps a path forward without silently guessing. |
| Parts association | **`po_lines`** (exists, empty) | Purpose-built; enables traceability + validation. |
| Mismatch handling | **Flag for review, never auto-correct** | Matches your HITL preference and the "flag before infer" rule. |
| Writes to Asana | **None in this design** | This is DB-only capture + validation; any Asana write stays behind the REQUESTS queue as usual. |

---

## 5. Open questions for you (needed before any build)

1. **PO email identification:** what reliably marks an outgoing PO in Sent Items — a fixed subject pattern (e.g. `PO <number>`), a specific attachment name, or a specific template? (Best rule to detect them without false positives.)
2. **One PO per email?** Are POs always one-per-email, or can a single send cover multiple POs (which would need split handling)?
3. **Centre box scope:** is `purchasing@sesameaccess.com` the only outbound PO mailbox, or do POs also leave from personal/other mailboxes?
4. **PO document format:** is the attached PO a PDF, an Excel workbook, or the email body itself? (Determines the parts-parsing route.)
5. **Historical backfill:** do you want the Sent Items scanned **back in time** to backfill `supplier_email` and `po_lines` for open orders (the 27 unmatched chases would largely become resolvable), or forward-only from go-live?
6. **Refresh cadence:** hourly sync acceptable, or tied to the existing purchasing pipeline schedule?

---

## 6. What this fixes (expected outcome)

- Overdue chases resolve the **actual supplier address** instead of `match=none` — the 27/35 gap largely closes.
- `po_lines` gives true **PO ↔ parts** traceability and a **validation** surface for missing/extra/qty-mismatched parts.
- Removes the current reliance on guessing from `supplier_contacts`, and avoids the stale-duplicate problem by joining to a single source rather than copying fields around.
- Unlocks later, already-requested capability: per-part PO delivery/QC status and invoice↔PO reconciliation against a real parts list.

---

## 7. Explicit statement of scope

- **No changes were made.** This document is a proposal only.
- Nothing was written to `ops.db`, Asana, Outlook, or any config.
- No emails were drafted or sent.
- Any build described here would come to you as a scoped proposal first, and would run under the usual guardrails (read-only mail; DB writes logged; Asana behind the REQUESTS queue).
