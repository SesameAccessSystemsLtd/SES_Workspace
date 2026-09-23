# QC Issues / Parts-Needed — Data & Process Flow Design Review

**Status:** DRAFT — for discussion, not yet approved
**Author:** Hermes (orchestration layer)
**Date:** 2026-09-21
**Audience:** Giles Hugo (Purchasing Manager)

---

## 0. Purpose

Review the correct mapping of data and process flows for QC issues / parts-needed /
causing-delays, and identify where automation can genuinely help versus where the
process must remain manual.

Drives from Giles's stated future state:

> "The correct future state is to have a part flagged directly into QC issues. The context
> ideally gets summarised instantaneously... Ultimately it can be 10, 20, 30 items that have
> QC issues as a part, and we would normally wait until the end of a process in order to
> collate all of that data and put it all into one email manually with images and context...
> That kind of still needs to be manual. Where the automation can come in is chasing on
> those parts."

---

## 1. Correcting a critical error

| Item | Value |
|---|---|
| Project `sync_qc_issues.py` points at | `902269769783650` — **WRONG PROJECT** |
| What that project actually is | The purchasing PO log (`POnumber-ddmmyy - Supplier - item`) |
| Correct QC project | **`737971594126804`** — *"1.0 - QC issues / Parts Needed / Causing delays"* |
| Correct project size | 1,905 tasks (1,896 complete, 9 open) |
| Correct project URL | https://app.asana.com/1/357939803556123/project/737971594126804 |

**Consequence:** `ops.db:qc_issues` has been mirroring the wrong project. The 9,070 rows
purged on 2026-09-21 were PO-log rows, not QC rows. The table is currently effectively empty
of QC data.

**Required fix:** repoint `PROJECT_GID` to `737971594126804`. This is a one-line change but
needs sign-off because the field mapping must change with it (the two projects share almost
no fields).

---

## 2. The intended data model (per project notes)

Project notes, 27.06.2025:

> "We are changing the sections to be IMPACT on production. REASON is what the reason was.
> QC Status then is what status the part is in. This should provide better clarity as the
> current custom fields and sections have cross over and the status is in the section not a
> field which makes it much clearer."

**Design intent — three axes:**

| Axis | Implementation | Field GID |
|---|---|---|
| **IMPACT** on production | section (→ field) | `1211173614771815` BUILD IMPACT |
| **REASON** why | multi-select | `1210660559208298` QC ISSUE REASON |
| **STATUS** of the part | single-select | `1171735794722533` QC STATUS |

**Actual reality:** 412 custom fields exist on this project. Status appears in **both**
sections *and* fields. Sections have drifted to ~56 distinct values against 14 defined ones.
The three-axis intent is not enforced anywhere.

---

## 3. The mapping chain — and where it breaks

Giles's process requires this chain to resolve:

```
part raised → which ORDER was it placed on → which SUPPLIER → which BUILD PACK → EDD
```

**Measured population (all 1,905 tasks):**

| Link | Field | GID | Populated | Verdict |
|---|---|---|---|---|
| Part no. | `Sesame Part No` | 1213228090549470 | 70 / 1905 (3%) | ⚠️ broken |
| → Build pack | `Build Packs reference` | 1211660742155293 | 50 / 1905 (2%) | ⚠️ broken |
| → Build pack | `Linked to Buildpack` | 1200825119140954 | **0 (0%)** | ❌ dead |
| → Order | `PO TASK` | 1213595302673057 | **0 (0%)** | ❌ dead |
| → Supplier | `Vendor` | 854127216887083 | 675 / 1905 (35%) | ⚠️ partial |
| → Supplier email | `Email` | 1109691357791838 | **27 / 1905 (1%)** | ❌ dead |
| → SQR | `SQR` | 1211774738554014 | 104 (5%) | ⚠️ partial |
| Status | `QC STATUS` | 1171735794722533 | 1,539 (80%) | ✅ good |
| Status | `ORDER Status` | 1143955334207040 | 446 (23%) | ⚠️ partial |
| EDD | `Needed by` | 1205496988611293 | **0 (0%)** | ❌ dead |

**Only 27 of 1,905 tasks have BOTH Vendor and Email — and only 2 of those are open.**

**Conclusion: the chain is broken at every link the chase depends on.** Daily auto-chase
cannot be built on current data.

---

## 4. Legacy reality — nine years, multiple life cycles

Giles's context is decisive here:

- Project created **2018-07-10**; oldest tasks date from then.
- Numerous life cycles; many tasks are legacy and **no longer carry the context** modern
  tasks carry.
- **Volume has decreased** in modern times — fewer QC issues now (better supply chain
  management), so the *active* set is small.
- Legacy statuses were **retained deliberately** because different dashboards consume them.
- **Why single-select was used:** dashboards can't display multi-select fields. But REASON
  genuinely needs multi-select (multiple reasons can apply). This is a real, unresolved
  tooling constraint — not an oversight.

**Active set today:** 9 open tasks, of which ~3 are not parts at all (e.g. "QC Maytec
Materials", "REPLACE ALUMINIUM FACE PLATE - PALL MALL"). 82 historical tasks sit in
`AWAITING DELIVERY` states.

---

## 5. What already exists in ops.db (major finding)

Most of the required infrastructure **already exists and is nearly unused**:

| Table | Rows | Relevant columns | Status |
|---|---|---|---|
| `purchase_orders` | **7** | `po_number`, `supplier_name`, `supplier_email`, `expected_delivery`, `confirmed_delivery`, `asana_task_gid`, `source_email_id` | built, barely used |
| `po_lines` | **0** | `po_id`, `sesame_part_no`, `line_status`, `received_at`, `allocated_qty` | **built, EMPTY — this is the part→order join** |
| `supplier_deliveries` | **0** | `delivery_date`, `expected_date`, `days_variance`, `shortages`, `quality_flag`, `quality_notes` | built, empty |
| `supplier_contacts` | 210 | `supplier_gid`, `contact_name`, `email`, `is_primary` | ✅ populated (204 emails) |
| `suppliers` | 433 | `vendor_name`, `contact_email`, `typical_lead_times_days`, `supplier_type` | ✅ 203 emails |
| `part_supply_events` | **17,942** | event-sourced: `entity_gid`, `field`, `old_value`, `new_value`, `observed_at` | ✅ rich history |
| `overdue_chase_log` | 14 | `po_number`, `conversation_id`, `certainty`, `draft_message_id`, `draft_web_link`, `status` | ✅ chase ledger exists |
| `asana_field_registry` | 12 | `field_name`, `field_gid`, `enum_options` | ✅ registry pattern exists |

**`overdue_order_chase.py` already implements the chase pattern** and is the right template:

- Stages: `--scan` (read-only) → `--draft <po>` (creates Outlook DRAFT)
- **Hard rule: NEVER sends email.** Creates a Graph draft; then re-GETs the message and
  **asserts `isDraft==True, sent==False`** before logging.
- PO-anchored matching: `$search="<po>"` against Inbox + Sent Items; HIGH certainty when
  PO is in the SUBJECT, MEDIUM when in the body.

This is exactly the guardrail shape the QC chase should reuse.

---

## 6. Separating the two concerns

Giles's description contains **two distinct processes**. Conflating them is what makes the
problem feel intractable.

### Process A — Collating evidence to raise the issue with the supplier
- 10–30 items accumulated across a process
- Manually assembled into **one email**, with **images and context**
- Purpose: give the supplier enough evidence to act quickly
- **Verdict: KEEP MANUAL.** Giles is right. Images, nuance, and "what actually went wrong"
  are not machine-derivable from linked tasks.

### Process B — Chasing until delivery, mapped to the build pack
- Ongoing chase of outstanding parts, daily until a response
- Must carry **PO number + SQR**
- Must update the **build pack** so production sees the issue and the expected delivery date
- **Verdict: AUTOMATE — this is where the value is.**

Giles's own assessment matches: *"That kind of still needs to be manual. Where the
automation can come in is chasing on those parts."*

---

## 7. The NCR question — REVISED: the NCR already exists and is the correct anchor

> ⚠️ **This section has been REVISED.** The original recommendation (do not create an NCR;
> use the PO number) is **superseded**. Giles has an existing auto-generated NCR field.

**Verified live 2026-09-21:**

| Item | Value |
|---|---|
| Field | **QC Issue ID - NCR** — `1218774119815824` |
| Behaviour | **auto-generated, sequential** (API reports `text` subtype, but it is system-issued) |
| Range | **NCR-1 → NCR-5037** |
| Coverage | **1,904 / 1,906 tasks (99.9%)** |
| Open today | **NCR-5033 … NCR-5037** |

**Implications:**

1. **The "manual entry missed" risk is eliminated** — no human types the ID.
2. **It solves the email-classification problem deterministically.** The hard part was that a
   PO number cannot distinguish order intent from QC intent. An NCR number is **QC-unique**,
   so a chaser carrying `NCR-5037` classifies by string match — no ML needed.
3. **It is backfilled across all 8 years**, so it joins legacy and modern data alike.
4. **It is the defect counter** supplier scorecards would want — already populated.

**Recommendation: NCR is the PRIMARY anchor; PO number is the SECONDARY key.**
NCR gives unambiguous QC identity; PO gives order/email-thread context. Do not fork identity
by inventing a parallel scheme.

---

## 8. The email-parser problem — the genuinely hard part

Giles's insight:

> "It's probably learning where to hold that in this task and having registered it as a
> quality control ops, enabling something on the email parser to go: 'What's been discussed
> here is a quality control issue. I need to update that individual task with the context
> rather than the purchase order.'"

This is the real complexity, and it should be named honestly:

- The email pipeline today matches **email → PO/order task** (`email_po_matches`).
- QC chases are **not replies** to a pre-existing thread — they are **separate chaser emails**
  holding the same PO + SQR.
- So the parser must **classify** an inbound message as either:
  - `order` context → update the PO/order task, **or**
  - `qc` context → update the **QC task** for that part
- Both share a PO number. **The discriminator is not the PO — it is the message intent.**

**This requires:** (a) a QC chaser email that is machine-identifiable, and (b) a
classification step in the parser. This is a real build, not a configuration change.

**Low-risk enabler:** if chaser emails are generated by our own system, we can embed a
machine-readable marker in the subject/body (e.g. the SQR + a QC thread tag) so inbound
replies are trivially classifiable — no ML guesswork needed.

---

## 9. Options for the legacy problem

Giles raised three paths. Assessed:

### Option 1 — New clean QC project, ignore history
- ✅ Clean fields, clean sections, no drift, fast to reason about
- ✅ Active set is small (9 open), so migration burden is low
- ❌ Loses 9 years of reporting context
- ❌ Creates inconsistency between old and new

### Option 2 — Bulk-migrate legacy tasks via extract → transform → push
- ✅ Preserves history and reporting continuity
- ⚠️ The mapping fields are 0–3% populated; there is **very little to migrate into**.
  Migration would mostly produce empty new fields.
- ⚠️ Requires heavy manual enrichment to be worthwhile — likely not justified given
  declining volume.

### Option 3 — Hybrid (**recommended**)
- Keep the legacy project **frozen and read-only for reporting** (dashboards keep working).
- Create a **new clean QC project** with enforced required fields for going-forward use.
- Migrate only the **currently open items** (9, of which ~6 are real parts) — a manual,
  minutes-long task.
- Retire duplicate/conflicting fields in the **new** project only.
- Revisit bulk migration later **only if** reporting genuinely demands continuity.

**Why hybrid wins:** the active set is tiny (better supply chain = fewer QC issues), so the
migration burden is genuinely low. Freezing legacy preserves dashboards without forcing a
risky bulk rewrite. And it stops the field sprawl immediately.

---

## 10. Target architecture (proposed)

```
NEW QC project (clean)
  QC task (one per part raised)
    ├── Sesame Part No ....... REQUIRED → joins ops.db parts_control
    ├── PO number ............ REQUIRED → joins purchase_orders / po_lines
    ├── SQR .................. optional reference
    ├── Supplier ............. AUTO-LOOKED-UP from suppliers (never typed)
    ├── Build Pack ref ....... REQUIRED → drives production update
    ├── EDD (Needed by) ...... REQUIRED → drives chase cadence
    ├── IMPACT ............... single-select (dashboards) ← see §11
    ├── REASON ............... multi-select (reality)     ← see §11
    └── QC STATUS ............ single source of truth

Daily cron (chase, draft-only):
  open QC tasks where status ∈ (AWAITING DELIVERY, ON ORDER, BEING ADDRESSED)
    → resolve supplier email via suppliers table (203 available)
    → compose chaser holding PO + SQR
    → create Outlook DRAFT (NEVER send) + assert isDraft==True
    → log to overdue_chase_log
    → write EDD + status to build pack → production view

Inbound email parser:
  classify message → order context  → update PO/order task
                   → qc context     → update QC task (via SQR/PO + marker)
```

---

## 11. The single-select vs multi-select constraint (unsolved)

Real conflict, stated plainly by Giles:

- **Dashboards need single-select** to render reliably.
- **REASON genuinely needs multi-select** (multiple causes often apply).

Options:
1. **Dual-field:** `REASON (primary)` single-select for dashboards + `REASON (all)`
   multi-select for truth. Dashboards read 1, humans/AI read 2. Sync primary = first of all.
2. **Dashboard-side aggregation:** keep multi-select, change dashboards to aggregate.
   Higher effort, cleaner data.
3. **Keep status quo** (single-select only, losing secondary reasons).

**Recommendation: Option 1 (dual-field).** It is the only approach that satisfies both
constraints without a dashboard rebuild. The cost is one redundant field and a sync rule.

---

## 12. Open questions for Giles

1. **Repoint `sync_qc_issues.py`** to `737971594126804`? (Current target is simply wrong.)
2. **Which legacy option** — freeze+new (recommended), bulk migrate, or new-without-history?
3. **NCR** — drop in favour of PO number (recommended), or genuinely needed for supplier
   defect counts?
4. **Chase cadence** — daily is stated. Should it be business-day only? Escalation after N
   days? Draft-only (recommended) or auto-send?
5. **Dual-field REASON (primary + all)** — acceptable, or change the dashboards instead?
6. **Who writes the QC task?** Human at QC, or auto-created from a flagged part?
   (Giles's stated future state is "a part flagged directly into QC issues".)

---

## 13. Recommended immediate next steps

Nothing below should be actioned without Giles's explicit go-ahead.

1. **P0 — Repoint the sync** to the correct project (`737971594126804`); fix field mapping to
   match the real fields (§3 table). Without this, every QC sync mirrors the wrong data.
2. **P1 — Decide the legacy path** (§9). Recommend hybrid: freeze + new project.
3. **P1 — Freeze legacy project** as read-only for reporting; do not add fields.
4. **P2 — Create the new QC project** with required fields (§10).
5. **P2 — Build the QC chaser** modelled on `overdue_order_chase.py` (draft-only, asserted,
   PO+SQR anchored).
6. **P3 — Extend the email parser** with order-vs-QC classification (§8).

---

## 14. Notes on approach

- This document is a **design review**, not a change request. No automation, Asana, or
  database changes have been made.
- The QC project is **read-only** for Hermes; all Asana writes go via the REQUESTS queue.
- Any chase capability must follow the `overdue_order_chase.py` precedent: **creates drafts,
  never sends**, and asserts draft state before logging.
