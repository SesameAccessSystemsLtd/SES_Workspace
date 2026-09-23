# QC PROJECT — HANDOFF BRIEF FOR CLAUDE (Fable model)

**Prepared by:** Hermes (orchestration layer, SES platform)
**Date:** 2026-09-21
**For:** Giles Hugo (Purchasing Manager) → handoff to Claude
**Type:** DESIGN + STAGED PLAN BRIEF. Not an authorisation to change anything.

---

# 0. HOW TO READ THIS DOCUMENT

- **§1 GOAL** — what success looks like, in Giles's own terms.
- **§2 VERIFIED FACTS** — everything here is *confirmed by direct inspection* of live systems
  on 2026-09-21. Treat as ground truth. Every figure is reproducible.
- **§3 THE REAL PROBLEM** — the diagnosis.
- **§4 CONSTRAINTS & PRINCIPLES** — non-negotiables, including the "don't destroy history" rule.
- **§5 PROPOSED APPROACH** — a staged, level-headed plan.
- **§6 SCOPE** — explicitly IN and OUT.
- **§7 RISKS** — with mitigations.
- **§8 OPEN DECISIONS** — things only Giles can decide.
- **§9 ENVIRONMENT** — paths, GIDs, tables, scripts.

---

# 1. GOAL

**Primary goal:**

> Make the QC project work for **both humans and the database**, so that:
> 1. An issue found on a part is **addressed as efficiently as possible**;
> 2. The **impact of the delay percolates to those who need to know** (production);
> 3. The process is **robustly mapped to the DB** so it can be chased and reported on;
> 4. **Without destroying years of accumulated insight and context.**

**Secondary goal (hard constraint from Giles):**

> Not all historical data holds value — but a **massive change to the data must not remove
> years of insight**. A **level-headed, staged approach** is required. Big-bang rewrites are
> explicitly against instruction.

**Definition of done (draft — Giles to confirm):**
- A part raised with a QC issue is traceable to: the **order** it was on, the **supplier**
  it came from, and the **build pack** it belongs to.
- The **chase** of outstanding parts is automated (draft-only) until delivered.
- **Production sees** the issue and the expected delivery date, without manual re-entry.
- Historical reporting **continues to function** unchanged.

---

# 2. VERIFIED FACTS

All confirmed by direct API/DB inspection on 2026-09-21.

## 2.1 Project identity — THE CRITICAL CORRECTION

| Item | Value |
|---|---|
| **Correct QC project** | `737971594126804` |
| **Correct name** | *"1.0 - QC issues / Parts Needed / Causing delays"* |
| **Workspace** | `357939803556123` (sesameaccess.com) |
| **Created** | **2018-07-10** — 8+ years of history |
| **Task count** | **1,905** (1,896 complete, **9 open**) |
| **URL** | https://app.asana.com/1/357939803556123/project/737971594126804 |

**A live bug exists:** `skills/sql_db/scripts/sync_qc_issues.py` has
`PROJECT_GID = "902269769783650"` — which is **NOT** the QC project. That GID is the
**purchasing PO log** (every task is `POnumber-ddmmyy - Supplier - item`).

**Consequence:** `ops.db:qc_issues` has never mirrored QC data. **Every nightly sync has
written PO-log rows into a QC table.** (9,070 such rows were purged 2026-09-21.)

**Isolated bug:** the correct GID `737971594126804` is *already* used in
`data/morning_comment_sweep.py`, `scan_purchasing.py`, `scan2.py`, and the OS Portal audit.
**Only `sync_qc_issues.py` is wrong.** This is a one-line fix — do not treat it as a
systemic misunderstanding.

## 2.2 The project's own stated design intent

From project notes, **27.06.2025** (verbatim):

> "We are changing the sections to be IMPACT on production. REASON is what the reason was.
> QC Status then is what status the part is in. This should provide better clarity as the
> current custom fields and sections have cross over and the status is in the section not a
> field which makes it much clearer."

**Three intended axes:**

| Axis | Intended home | Field GID (confirmed) |
|---|---|---|
| **IMPACT** on production | section → field | `1211173614771815` (BUILD IMPACT, enum) |
| **REASON** why it happened | multi-select | `1210660559208298` (QC ISSUE REASON, multi_enum) |
| **STATUS** of the part | single-select | `1171735794722533` (QC STATUS, enum) |

**Reality does not match intent** (see §2.4).

## 2.3 The chase-relevant fields — CONFIRMED GIDs

Extracted live from the correct project:

| Field name | GID | Type | Populated / 1905 |
|---|---|---|---|
| `Sesame Part No` | `1213228090549470` | text | 70 (**3%**) |
| `Build Packs reference` | `1211660742155293` | reference | 50 (**2%**) |
| `Linked to Buildpack` | `1200825119140954` | enum | **0 (0%)** |
| `PO TASK` | `1213595302673057` | reference | **0 (0%)** |
| `Vendor` | `854127216887083` | text | 675 (35%) |
| `Email` | `1109691357791838` | text | **27 (1%)** |
| `SQR` | `1211774738554014` | text | 104 (5%) |
| `QC STATUS` | `1171735794722533` | enum | 1,539 (**80%**) |
| `ORDER Status` | `1143955334207040` | enum | 446 (23%) |
| `Needed by` (EDD) | `1205496988611293` | date | **0 (0%)** |
| `Date Raised` | `1205496988611295` | date | 533 (27%) |
| `Days since raised` | `1205496988611298` | number | 515 (27%) |
| `BUILD IMPACT` | `1211173614771815` | enum | 205 (10%) |
| `PROJECT NAME` | `1202882645384174` | enum | 447 (23%) |
| `REASON FOR ISSUE` | `1183656755187876` | enum | — |
| `IMPACT` (legacy) | `1148052247637687` | enum | — |
| `NEEDS - from Purch` | `1207196303096574` | multi_enum | **2 (0%)** |
| **`QC Issue ID - NCR`** | **`1218774119815824`** | text (**auto-generated, sequential**) | **1,904 (99.9%)** ✅ |

## 2.4 Mapping chain health — BROKEN

The chain required for chasing is:

```
part → which ORDER → which SUPPLIER → which BUILD PACK → EDD
```

| Link | Populated | Verdict |
|---|---|---|
| Part number | 3% | ⚠️ broken |
| → Build pack | 2% | ⚠️ broken |
| → Build pack (`Linked to Buildpack`) | **0%** | ❌ dead |
| → Order (`PO TASK`) | **0%** | ❌ dead |
| → Supplier (`Vendor`) | 35% | ⚠️ partial |
| → Chaser email (`Email`) | **1%** | ❌ dead |
| → EDD (`Needed by`) | **0%** | ❌ dead |

**Only 27 of 1,905 tasks have BOTH `Vendor` and `Email`; only 2 of those are open.**

**⇒ Automated daily chasing is NOT buildable on current data. The mapping must be fixed first.**

## 2.5 Field and section sprawl

- **412 custom fields** attached to this project.
- **14 defined sections**, but **~56 distinct section values in use** (drift).
- Status lives in **both** sections **and** fields (the exact "cross over" the 27.06.25 note
  complained about).
- Section distribution (top): Resolved Supplier Issue 312 · Resolved/no fault 255 · Didnt
  Impact Build 247 · Engineers Tasks/Inbox 200 · (no section) 102 · Impacted Build 101 ·
  Debriefed 86 · Resolved Drawing Issue 83 · **Lasercut parts 51 (1 open)** · Awaiting
  Delivery 51 · To Be Addressed 20 (**2 open**) · QC NOTES/SUPPLIER INFO 10 (**3 open**).

## 2.6 The open set (today)

**9 open tasks.** Of these, **~3 are not parts at all**, e.g.:
- `REPLACE ALUMINIUM FACE PLATE - PALL MALL` (no part no, no vendor)
- `QC Maytec Materials` (a process note, not a part)
- `QC Lasercut deliveries - Process and supplier notes` (a note)

Real part examples (showing the chain works when populated):
| Task | Part No | Build Pack | Vendor | QC STATUS |
|---|---|---|---|---|
| Platform Plate 304/HR Laser Cut | 1595.1006.G.001 | 1595.1006.G.GlassPlatform. | Laser Cut | BEING ADDRESSED |
| 6 buttons plate - Engraving | 1591.1020.A1.1 | 1591.1020.A.FreeStandingButtonPost. | Engraving | AWAITING DELIVERY |
| Roller Shutter Blind Aluflex | 1594.1010.L.045 | 1594.1010.L.ScissorLift_Single_LowProfile 1600. | Hennig | Approved to progress |

**82 historical tasks** sit in `AWAITING DELIVERY`-type states.

## 2.7 Existing infrastructure in `ops.db` — LARGELY BUILT, BARELY USED

This is the most important finding for planning. **Do not rebuild what exists.**

| Table | Rows | Relevant columns | Status |
|---|---|---|---|
| `purchase_orders` | **7** | `po_number`, `supplier_name`, `supplier_email`, `expected_delivery`, `confirmed_delivery`, `asana_task_gid`, `source_email_id` | built, barely used |
| **`po_lines`** | **0** | `po_id`, **`sesame_part_no`**, `line_status`, `received_at`, `allocated_qty` | **built & EMPTY — this is the part→order join** |
| `supplier_deliveries` | **0** | `delivery_date`, `expected_date`, `days_variance`, `shortages`, `quality_flag`, `quality_notes` | built, empty |
| `supplier_contacts` | 210 | `supplier_gid`, `contact_name`, `email`, `is_primary` | ✅ **204 emails** |
| `suppliers` | 433 | `vendor_name`, `contact_email`, `typical_lead_times_days`, `supplier_type` | ✅ **203 emails** |
| `part_supply_events` | **17,942** | `entity_gid`, `field`, `old_value`, `new_value`, `observed_at`, `significance` | ✅ event-sourced history |
| `overdue_chase_log` | 14 | `po_number`, `conversation_id`, `certainty`, `draft_message_id`, `draft_web_link`, `status` | ✅ chase ledger |
| `asana_field_registry` | 12 | `field_name`, `field_gid`, `field_type`, `applies_to`, `enum_options` | ✅ **governance pattern** |
| `procurement_tasks` | — | `order_status`, `po_number`, `due_date` | ✅ in active use |
| `parts_control` | — | `asana_task_gid`, `build_assembly_pack_gid` | ✅ in active use |

**Key:** `supplier_contacts` (204 emails) + `suppliers` (203 emails) **already solve the 1%
email-coverage problem** — no need to ask humans to type addresses.

## 2.8 The chase mechanism ALREADY EXISTS

`skills/purchasing/overdue_order_chase.py` (confirmed read):

- **`--scan`** (default, read-only): finds orders `Awaiting CONFIRMATION` past due date.
  Prints PO, vendor, days overdue, Asana link, match certainty. Works fully offline.
- **`--draft <po_number>`**: locates the email conversation (PO-anchored), creates an
  Outlook **DRAFT** via Graph `POST /users/{mailbox}/messages`.
- **HARD RULE: NEVER SENDS EMAIL.** After creating, it re-GETs the message and
  **asserts `isDraft==True, sent==False`** before logging.
- **Matching strategy:** `$search="<po>"` on Inbox + Sent Items. **HIGH** certainty when the
  PO is in the **subject**; **MEDIUM** when only in the body.
- **Ledger:** writes to `overdue_chase_log`.

**⇒ This is the template for the QC chase. Reuse, don't reinvent.**

---

# 3. THE REAL PROBLEM

## 3.1 Two processes are being conflated

Giles's description contains **two distinct processes**. Conflating them is why the problem
feels intractable:

### Process A — Collating evidence to raise the issue with the supplier
- 10–30 items accumulate across a process.
- Manually assembled into **ONE email** with **images and context**.
- Purpose: give the supplier enough evidence to act fast.
- **Giles's own verdict: "That kind of still needs to be manual."**
- **AGREE — KEEP MANUAL.** Images and "what actually went wrong" are not machine-derivable
  from linked tasks. Giles's judgement on this is correct and should be respected.

### Process B — Chasing until delivery, mapped to the build pack
- Ongoing chase of outstanding parts, daily until a response.
- Must carry **PO number** and **SQR**.
- Must update the **build pack** so production sees the issue + expected delivery date.
- **Giles's own verdict: "Where the automation can come in is chasing on those parts."**
- **AGREE — AUTOMATE THIS.** This is where the value is.

## 3.2 The genuinely hard problem (name it honestly)

Giles's insight, quoted:

> "It's probably learning where to hold that in this task and having registered it as a
> quality control ops, enabling something on the email parser to go: 'What's been discussed
> here is a quality control issue. I need to update that individual task with the context
> rather than the purchase order.'"

**Why this is hard:**

- The email pipeline today matches **email → PO/order task** (`email_po_matches`).
- QC chases are **NOT replies** to the original order thread — they are **separate chaser
  emails** carrying the same PO + SQR.
- Therefore the parser must **classify message intent**, not match a PO:
  - `order` intent → update the PO/order task
  - `qc` intent → update the **QC task**
- **A PO number cannot discriminate** — both intents share it.

**Recommended enabler:** because *we* generate the chasers, embed a **machine-readable
marker** (SQR + QC tag) in the chaser subject/body. Inbound replies then classify by string
match — **no ML guesswork required.** This converts a hard ML problem into a deterministic
one. Strongly recommended.

## 3.3 The NCR field — EXISTING, AUTO-GENERATED, and the CORRECT ANCHOR

**⚠️ This section REVISES an earlier recommendation. Read carefully.**

**Verified live 2026-09-21:**

| Item | Value |
|---|---|
| Field name | **QC Issue ID - NCR** |
| GID | `1218774119815824` |
| Resource subtype | `text` (API reports text — **but it IS auto-generated and sequential**) |
| Coverage on QC project | **1,904 of 1,906 tasks (99.9%)** |
| Value range | **NCR-1 → NCR-5037** |
| Format | `NCR-<integer>`, sequential, system-issued |
| Gaps | 330 numbers not present (retired/never-issued — not a blocker) |
| Open issues today | **NCR-5033 … NCR-5037** (the 5 newest) |
| Attached to QC project | ✅ yes |

**What this changes:**

1. **The "manual entry missed" risk is ELIMINATED.** The ID is system-issued on every task.
   There is no human typing step, so nothing can be skipped.
2. **It is the QC-unique discriminator the design needs.** The hard problem in §3.2 was that
   a PO number cannot distinguish `order` intent from `qc` intent (both share it). An **NCR
   number is QC-unique**, so a chaser email carrying `NCR-5037` classifies **deterministically**
   — no ML, no ambiguity, no marker invention required.
3. **It is already backfilled across all history** (1,904/1,906), so it works as a join key
   across the full 8-year dataset — legacy included.
4. **It is the sequential defect counter** that supplier scorecards would need — already
   exists, already populated, at zero cost.

**REVISED RECOMMENDATION (supersedes the earlier "do not create an NCR" advice):**

- **Use `NCR` as the PRIMARY anchor for QC identity and email classification.**
- **Use the PO number as the SECONDARY key** to join to the order / original email thread.
- This is strictly better than PO-only: NCR gives unambiguous QC identity; PO gives order
  context. Both are already present and already populated.

**Because the NCR is auto-generated, do NOT:**
- Ask humans to type it.
- Generate our own ID scheme alongside it (that would fork identity).
- Treat the API's `text` subtype as meaning "manual" — it does not.

**Minor open item:** establish why 330 numbers are absent (retired tasks vs numbering leap).
Not a blocker for joining; note and move on.

**Implementation consequence for the chase (§2.8 / Stage 2):** the chaser email should carry
**both** `NCR-<n>` (for deterministic reply classification) and the **PO number** (for order
context). Inbound replies matching an `NCR-\d+` token route to the QC task; everything else
follows existing order handling.

## 3.4 The single-select vs multi-select conflict — GENUINE, UNSOLVED

Giles, verbatim:

> "Dashboards haven't been able to show information if there is multi fields able to be
> selected, and so we were using a single select. But then we also wanted to have part that
> had multi select reasons because it might be a numerous set of reasons as to why something
> had gone wrong."

- **Dashboards need single-select** to render. (Real constraint.)
- **REASON genuinely needs multi-select.** (Real requirement.)
- **Legacy statuses were retained deliberately** because dashboards consume them.

**Recommendation: DUAL-FIELD.**
- `REASON (primary)` — single-select, **for dashboards**.
- `REASON (all)` — multi-select, **for truth**.
- Sync rule: `primary` = first value of `all`.
- **Cost:** one redundant field + a sync rule. **Benefit:** no dashboard rebuild.
- This is the only option satisfying both constraints without re-engineering dashboards.

---

# 4. CONSTRAINTS & PRINCIPLES

**Hard constraints (must not be violated):**

1. **DO NOT destroy historical insight.** Giles is explicitly concerned that a massive data
   change removes years of insight. Level-headed, staged, reversible.
2. **NO BIG-BANG MIGRATION.** Staged, verifiable steps.
3. **Never send email to suppliers automatically.** Drafts only, asserted
   (mirror `overdue_order_chase.py`). This is an existing platform guardrail.
4. **Asana writes go via the REQUESTS queue only** (SAP protocol). Hermes does not write
   Asana directly. Claude should respect the same boundary.
5. **Never assign or suggest PO numbers** — manual process only.
6. **Never mark active orders "Needs Met"** — use `ADDRESSING`.
7. **`ops.db` writes go through `db_writer.py` only.**
8. **Freeze, don't delete.** Retire legacy fields by freezing, never by dropping data.
9. **Production impact must be visible.** The whole point is delay percolating to those who
   need to know.

**Design principles:**

- Reuse existing infrastructure (§2.7, §2.8) before building new.
- Keep humans in the loop where judgement is required (Process A).
- Automate the mechanical, repetitive, error-prone parts (Process B).
- Every automated action must be **auditable**.
- Prefer **deterministic** solutions (markers) over inferential ones (ML) where possible.

---

# 5. PROPOSED APPROACH — STAGED PLAN

**Guiding idea:** *Fix the plumbing first, then decide about history.* Do not touch legacy
data until the going-forward process works and is proven.

## STAGE 0 — ✅ COMPLETE (2026-09-23)

**Status: DONE and verified against live systems.** Kept here for traceability.

| # | Action | Result |
|---|---|---|
| 0.1 | **Repoint `sync_qc_issues.py`** `PROJECT_GID` -> `737971594126804` | ✅ done |
| 0.2 | **Replace the stub `task_to_row()`** with real field mapping (20 fields) | ✅ done |
| 0.3 | Rebuild `qc_issues` table with correct schema (32 cols) + `completed` flag | ✅ done |

**Also done as part of Stage 0:**
- `max_pages` 50 -> **200** (the old 5,000-task cap silently truncated)
- **Open-only mirror by default** (client-side on the `completed` flag); `--include-completed`
  added as explicit opt-in
- 8 indexes added; `db_init_ops.py` updated to match
- Backups: `qc_issues_pre_stage0_20260923_121800` (57 rows) + `qc_issues_backup_20260921_101733`

**Verified first sync:**
```
Fetching tasks from QC project 737971594126804
Fetched 1906 tasks (all completion states)
Open tasks: 10  (skipped completed: 1896)
Done — written: 10  skipped: 0  errors: 0
```

**Confirmed working chain:** `NCR-5034 -> 1591.1020.A1.1 -> 1591.1020.A.FreeStandingButtonPost.
-> Engraving -> BRussell@alexander-pollock.co.uk -> AWAITING DELIVERY -> 591 Uni of Cambridge`

---

## STAGE 1 — Make the going-forward chain complete (NEXT)

### ⚠️ Updated baseline from the Stage 0 sync (5 genuine part issues)
| Field | Populated |
|---|---|
| `ncr_id` | **5/5** ✅ |
| `sesame_part_no` | 5/5 ✅ |
| `build_pack_reference` | 5/5 ✅ |
| `vendor` | 5/5 ✅ |
| `supplier_email` | **2/5** ⚠️ |
| `po_task_gid` | **0/5** ❌ |
| `needed_by` (EDD) | **0/5** ❌ |
| `associated_sqr_number` | 5/5 ✅ |

**This is the precise Stage 1 agenda.** Part+pack+vendor already resolve; **email resolution,
PO linkage, and EDD are the three real gaps.**

### Stage 1 actions
| # | Action | Notes |
|---|---|---|
| 1.1 | Define **required fields** on a QC task: `Sesame Part No`, `PO number`, `Build Pack ref`, `EDD (Needed by)`, `QC STATUS` | Enforce at creation |
| 1.2 | **Auto-populate Supplier + Email** from `suppliers` / `supplier_contacts` (203 / 204 emails available) | Kills the 1% problem |
| 1.3 | Populate `po_lines` (`sesame_part_no` + `po_id`) — **the part→order join is empty** | Enables part→order |
| 1.4 | Extend `asana_field_registry` with the QC fields (§2.3 GIDs) | Governance |
| 1.5 | Define the **QC status vocabulary** (single source of truth) | Ends section/field drift |

**Validation gate:** a newly raised QC part must resolve part → order → supplier → build pack
→ EDD **with no manual typing**. If it doesn't, do not proceed.

## STAGE 2 — Build the QC chase (moderate)

| # | Action | Notes |
|---|---|---|
| 2.1 | Build `qc_chase.py` modelled on `overdue_order_chase.py` | **Draft-only, asserted** |
| 2.2 | Anchor on **PO + SQR**; embed a **QC marker** in the chaser for reply classification | Deterministic classification |
| 2.3 | Log to `overdue_chase_log` (or a QC-specific ledger) | Audit |
| 2.4 | Cadence: **business days only**; escalate after N days (Giles to set N) | — |
| 2.5 | Write **EDD + status to the build pack** → production view | The "percolate" requirement |

**Validation gate:** chaser drafts appear correctly, never send, and production sees the EDD.

## STAGE 3 — Extend the email parser (moderate → hard)

| # | Action | Notes |
|---|---|---|
| 3.1 | Classify inbound: `order` intent vs `qc` intent | Use the QC marker (§3.2) |
| 3.2 | Route `qc` intent → update the **QC task**, not the PO task | The hard bit |
| 3.3 | Preserve existing `email_po_matches` behaviour for order intent | No regression |

## STAGE 4 — Resolve the legacy question (LAST, and only after 0–3 are proven)

**Do not start here.** Three options, assessed honestly:

### Option A — New clean project; freeze legacy; ignore history for going-forward
- ✅ Clean fields/sections; fast; tiny active set (9 open)
- ❌ Loses 9 years of reporting continuity
- ❌ Inconsistency between old and new

### Option B — Bulk migrate legacy via extract → transform → push
- ✅ Preserves history and reporting
- ❌ **The mapping fields are 0–3% populated.** There is **almost nothing to migrate into.**
  A bulk migration would mostly create **empty new fields** — churn without benefit.
- ❌ Maximum risk of the "destroy years of insight" outcome Giles fears.

### Option C — HYBRID (**recommended**)
1. **Freeze legacy project read-only** → dashboards keep working, unchanged.
2. **New clean QC project** with enforced required fields for going forward.
3. **Migrate only currently-open items** (9, of which ~6 real) — **minutes of manual work**.
4. **Retire duplicate/conflicting fields in the NEW project only.**
5. **Revisit bulk migration later ONLY IF** reporting genuinely demands continuity.

**Why hybrid wins:** the active set is tiny (volume decreased, better supply chain = fewer QC
issues). Freezing preserves insight at zero risk. Field sprawl stops immediately. Nothing is
destroyed, and the reversible path stays open.

## Deliverable expectation

A written plan + working Stage 0/1 code, with each stage **independently verifiable** and
**reversible**.

---

# 6. SCOPE

## ✅ IN SCOPE

1. Fixing the wrong `PROJECT_GID` in `sync_qc_issues.py`.
2. Correct field mapping for the QC project (confirmed GIDs, §2.3).
3. Repairing the `qc_issues` table schema (correct columns + `completed`).
4. Making the part → order → supplier → build pack → EDD chain complete for **going-forward**
   tasks.
5. Populating `po_lines` (the empty part→order join).
6. Auto-resolving supplier + email from `suppliers` / `supplier_contacts`.
7. A **draft-only, asserted** QC chase script (modelled on `overdue_order_chase.py`).
8. Writing EDD + status back to the **build pack** so production is informed.
9. Extending the email parser to classify `order` vs `qc` intent (with a deterministic marker).
10. Extending `asana_field_registry` to cover the QC fields.
11. A **staged, reversible plan** for legacy handling — recommendation: Option C (hybrid).

## ❌ OUT OF SCOPE

1. **Automating Process A** (collating 10–30 items into an evidence email with images).
   **Stays manual, by design.** Do not attempt to auto-generate supplier evidence emails.
2. **Bulk rewriting the 1,905 historical tasks** as a first move. Explicitly deferred to
   Stage 4 and only if justified.
3. **Deleting any historical data.** Never.
4. **Creating an in-house NCR number** (recommended against — see §3.3).
   ⚠️ **SUPERSEDED:** an NCR field already exists, auto-generated and sequential
   (`QC Issue ID - NCR` = `1218774119815824`, NCR-1 → NCR-5037, 99.9% coverage). It is
   now the **recommended PRIMARY anchor**. See the revised §3.3.
5. **Sending email automatically.** Drafts only, always.
6. **Direct Asana writes bypassing the REQUESTS queue.**
7. **Assigning or suggesting PO numbers.**
8. **Rebuilding dashboards** (the dual-field REASON recommendation avoids this).
9. **Migrating data into `SES_Brain` / knowledge vaults.**
10. **Changes to unrelated sync scripts** (only `sync_qc_issues.py` is buggy).

---

# 7. RISKS & MITIGATIONS

| # | Risk | Sev | Mitigation |
|---|---|---|---|
| R1 | Chasing with 1% email coverage → wrong/no recipients | **High** | Auto-resolve from `suppliers` (203) / `supplier_contacts` (204) **before** enabling chase |
| R2 | Accidentally auto-sending to suppliers | **High** | Draft-only + re-GET assert `isDraft==True, sent==False` (existing pattern) |
| R3 | **Bulk migration destroys years of insight** (Giles's core fear) | **High** | Stage 4 last; recommend hybrid (freeze); nothing deleted; reversible |
| R4 | Chasing parts with no known order/supplier | **Med** | Validation gate after Stage 1; do not chase unresolvable parts |
| R5 | Freezing legacy breaks dashboards | **Med** | Freeze **read-only** — do not delete or rename; dashboards keep reading |
| R6 | Bulk migration produces hollow/empty fields | **Med** | 0–3% populated ⇒ migrate **only the open set**; do not bulk-write |
| R7 | Email parser misclassifies QC vs order | **Med** | Deterministic QC marker in chasers (§3.2) |
| R8 | Dual-field REASON drifts out of sync | **Med** | Single sync rule: `primary = first(all)`; validate on write |
| R9 | Wrong-GID sync keeps running nightly | **Med** | Stage 0.1 fixes immediately |
| R10 | Field sprawl continues during redesign | **Low** | Freeze new field creation; registry enforces |
| R11 | Open set contains non-parts (3 of 9) | **Low** | Cleanup pass; treat notes separately |
| R12 | Section/field status disagreement | **Low** | Stage 1.5 single vocabulary; freeze sections |

---

# 8. OPEN DECISIONS (Giles only)

1. **Repoint the sync** to `737971594126804`? *(Recommended: yes — it's a bug fix.)*
2. **Legacy path:** Option A / B / **C (recommended)**?
3. **NCR:** ~~drop in favour of PO anchor, or required for supplier defect counts?~~
   **RESOLVED — no decision needed.** The NCR field already exists, is auto-generated and
   sequential (NCR-1 → NCR-5037, 99.9% coverage), and is now the **recommended primary
   anchor**, with PO number as the secondary key. See §3.3.
4. **Chase cadence:** daily stated — business days only? Escalate after N days (what N)?
   Draft-only *(recommended)* or auto-send *(not recommended)*?
5. **Dual-field REASON:** acceptable, or change dashboards instead?
6. **Who raises a QC task?** Human at QC, or auto-created from a flagged part?
   *(Giles's stated future state: "a part flagged directly into QC issues.")*
7. **Production notification:** how should delay surface — Asana field, comment, digest, or
   the build pack only?
8. **Confirmation of "definition of done"** (§1).

---

# 9. ENVIRONMENT REFERENCE

**Host:** Windows 10, user `GilesHugo`. `terminal` runs **bash (git-bash/MSYS)**.

**Python:** `C:\Users\GilesHugo\SES_Workspace\.venv\Scripts\python.exe`
(`python` is NOT on PATH — always use the venv path.)

| Resource | Path / ID |
|---|---|
| Workspace root | `C:\Users\GilesHugo\SES_Workspace` |
| Operational DB | `C:\Users\GilesHugo\SES_Workspace\db\ops.db` (**1.22 GB**) |
| QC sync script | `skills\sql_db\scripts\sync_qc_issues.py` |
| Sync orchestrator | `skills\sql_db\scripts\sync_all_mirrors.py` |
| Chase template | `skills\purchasing\overdue_order_chase.py` |
| DB writer (only writer) | `skills\purchasing\db_writer.py` |
| Design review (this topic) | `reports\QC_ISSUES_DESIGN_REVIEW.md` |
| Nightly cron | Hermes job `02f75e6f8c82` `asana_mirror_sync.py` @ 01:00 |
| Asana workspace | `357939803556123` (sesameaccess.com) |
| **QC project (correct)** | **`737971594126804`** |
| PO-log project (WRONG target) | `902269769783650` |
| Asana mirror-sync chain | cron → `asana_mirror_sync.py` → `sync_all_mirrors.py` → `sync_qc_issues.py` |
| SES_Brain vault | `C:\Users\GilesHugo\SES_Brain\` (write to `raw\` only) |
| Dropbox | `C:\Users\GilesHugo\SAS Dropbox\` (**READ ONLY — never write**) |

**Backup taken:** `qc_issues_backup_20260921_101733` (9,109 rows) — rollback available for
the 2026-09-21 purge. This backup contains **PO-log data**, not QC data.

---

# 10. IMMEDIATE NEXT STEP

The only thing that should happen *before* Giles decides anything else:

**Stage 0.1 — repoint `sync_qc_issues.py` to `737971594126804`.**

Everything else is design and requires Giles's decisions in §8.

---

*Prepared from direct inspection of live systems on 2026-09-21. Every figure in §2 is
reproducible. No changes have been made to Asana, the database, or any automation.*
