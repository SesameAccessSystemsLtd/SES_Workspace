# OS Portal — Centre-of-Truth & Data-Routing Architecture Scoping
**Date:** 2026-09-04 · **Author:** Hermes (scoping — NO build changes made)
**Status:** SCOPING DRAFT — for Giles review before any architectural change

## Purpose

Fully map **which scripts pull data**, **which scripts populate data fields**, how data
should flow into the HTML dashboards (from local resources, validated against Asana, with a
DB-only fallback), and what architectural changes + script updates are needed to give every
dashboard a **robust centre of truth**. Captures the pack↔part↔order linkage requirements and
the three new edge-case/rule items Giles raised 2026-09-04.

This is a scoping document only. Nothing below has been built or changed.

---

## 1. The architecture doctrine (Giles's governing rule, 2026-09-04)

> "When we are pulling data for the HTML inputs it needs to call from local resources.
> Then, where possible, validate against the Asana truth before populating the tables in
> the HTML. This would be the ideal scenario. If any step in that process fails, we need
> a fallback scenario that highlights that or ratifies it back against the DB only."

Translated into a routing rule **every future dashboard must follow**:

1. **HTML inputs read from a local resource first** (the epoch disk-cache of live Asana
   GETs, or ops.db) — never a fresh synchronous abseil to Asana per pane.
2. **Where a displayed field is a stale surface truth** (order status, completion, due
   dates), **validate it against live Asana before rendering** — a bounded, per-row
   reconcile like ops-oversight already does for confirmations.
3. **If validation fails** (token off / Asana unreachable / a row 404s), **fall back to the
   ops.db snapshot AND visibly flag that the row is un-reconciled** — never silently drop
   or silently trust.
4. The page must always **hyper-link back to the source Asana task** — portal = context,
   Asana = author of the human-facing truth.

**Today only ops-oversight's Pane-2 "to chase" obeys this.** Everything else either trusts
pristine live Asana (which crashes if Asana is down — `api_get` has no catch) or trusts a
stale DB snapshot with no live check. See section 5.

---

## 2. The verified data-flow map (pull vs populate)

### 2a. HTML *generators* (PULL / read → render). All under `skills/os_portal/`.

| Script | HTML emitted | Asana lists PULLED (all via shared epoch disk-cache `api_get`) | ops.db tables READ | Centre of truth per pane |
|---|---|---|---|---|
| `build_os_status.py` | `index.html` (Andy worklist) | Build Schedule packs (8529…855) + Order Numbers (8546…168) + per-pack subtasks | `parts_control` (counts per pack) | pack/subtask/order truth = **live Asana**; procurement counts = DB |
| `build_giles_worklist.py` | `giles-worklist.html` (order-centric) | Order Numbers + Build Schedule (warm, shares os_status cache) | `parts_control` (parts per job×vendor) | order truth = **live**; parts = DB |
| `build_bom_tracker.py` | `bom-tracker.html` (Karol) | 1.4 Purchasing Dept (8500…317) + Project Orders (8486…331) + Order Numbers + subtasks | `parts_control` (part→pack / no-pack gap) | stage truth = **live**; pack-link gap from DB |
| `build_ops_oversight.py` | `ops-oversight.html` | Order Numbers (shared) + **live per-row check ONLY on confirmations pane** | `procurement_tasks, email_po_matches, suppliers, overdue_chase_log, ordering_queue, supplier_contacts` (most DB surface) | **mixed** — see 5 |
| `build_deployment_tracker.py` | `deployment-tracker.html` | none | none (reads `deployment-state.json`) | static JSON |

All five are pure **read/render** — none writes ops.db or Asana. All route every Asana GET
through the single epoch disk cache in `build_os_status.api_get()` (TTL 600 s, epoch-stamped
per cron tick, purges >30 min) — the **local-first baseline** already in place.

### 2b. Data *populators* (WRITE → which fields). The centre-of-truth writers.

| Writer | Writes to ops.db | From source | When |
|---|---|---|---|
| `db_writer.py` (gateway, upsert_*) | `produced many tables`: parts_control, build_assembly_packs, suppliers, procurement_tasks, stock_inventory, qc_issues, golden_index, ordering_queue, po_lines, bom_requirements, purchase_orders, email_po_matches | live Asana via the per-table sync scripts below | on-demand by each sync |
| `sync_all_mirrors.py` (nightly 01:00, via `sync_procurement_tasks`, `sync_parts_control`, `sync_build_schedule`, `sync_stock_inventory`, etc.) | **`procurement_tasks.order_status/po_number`, `parts_control.stock_action`** and every other mirrored table | live Asana Order Numbers / Parts Control / Build Schedule | **NIGHTLY** |
| `parts_control_propagator.py` (the "on order" propagator) | **only `propagator_audit_log`** (log row) | live Asana | ~30-min cycle |
| `sync_suppliers.py` / `sync_stock_inventory.py` | `suppliers` / `stock_inventory` | live Asana | nightly / scheduled |
| `legacy_bom_pack_ingest.py`, `02_map_parts_to_packs.py`, `apply_pack_assign`, `pack_association.py` | `parts_control.build_assembly_pack_gid` / association (direct UPDATE, **bypass db_writer**) | Asana + scripts | per pipeline |
| `qa_sync.py --push` | **NOT ops.db** — Google Sheet | Purchasing sheet decisions | HITL push |
| `03_proposal_engine` / `create_order_tasks` / `arrived_stock_processor` | Asana writes + some `ordering_queue` status via db_writer | live/proposals | pipeline |

**The BOM-pipeline Stage 5 import (`BOM_IMPORT_4_Asana_v4.py`) → `bom_requirements`** is the
only place sesame parts get a `po_id`-style DB row at import; and (per earlier diagnosis)
`bom_requirements.po_id` is currently NULL everywhere for job 1037 — the part↔PO link is not
captured at import.

---

## 3. THE central finding: nothing pushes DB order records at the "on order" moment

Giles's opening premise was: *"the BOM Propagator cron should update the PO records in ops.db
once an item is on order. If not happening, we have a historical backlog to attribute."*

**Verified verdict: it is NOT happening.** `parts_control_propagator.py` flips the Stock-ACTION
custom field **in Asana only** and writes one audit-log row to ops.db. The DB's `order_status`
(`procurement_tasks`) and `stock_action` (`parts_control`) are refreshed **only by the passive
nightly 01:00 Asana mirror sync** — so ops.db learns about a live "on order"/"arrived" flip with
**up to 24h lag** (and only if the sync runs clean).

**Consequence:** every HTML pane that reads `procurement_tasks.order_status` /
`parts_control.stock_action` is showing a snapshot that can be a day stale, and can disagree
with live Asana by a whole lifecycle stage. This is exactly the "arrived-but-DB-says-awaiting"
leak that haunts the ops-oversight chases (259 false → ~5 real) and the 21567
"lasercut arrived+in-QC but not ticked complete" case Giles hit on 1037.

### 3b. Options for closing the gap (design decision for Giles)

1. **Event-push at the transition** (ideal, matches Giles's intent): extend
   `parts_control_propagator` (or the order-status setter) to also upsert the mirror target —
   e.g. call `db_writer.upsert_procurement_tasks`/touch `order_status` **when it flips an order
   to/from on-order**. DB learns within ~30 min, not ~24 h. Requires the propagator to carry the
   source project/PO data to the writer.
2. **Expose the live state instead** (surgical, no backfill): the generators that show order
   status call the live-per-row check (ops-oversight Pane-2 pattern) rather than trusting the DB.
   Cost: bounded N+1 (≤200) per refresh; zero DB-write risk; but doesn't fix the DB itself for
   other consumers/reports.
3. **A dedicated "order state" mirror bolt** that runs more frequently (e.g. every 30–60 min on
   Order Numbers + Parts Control only) rather than only the 01:00 batch — narrows the lag window
   without touching the system-of-record write path or the propagator.

Giles's ask to "scope first" is best served by these being **options, not decided here**. The
chosen option determines whether the "historical backfill" in §4 is even needed, and whether
downstream dashboards trust DB or live.

---

## 4. Historical data & backfill

Because the DB only ever learns passively, **anything the propagator flipped in Asana before the
last successful mirror sync is reflected only when the mirror next re-pulls the whole Order
Numbers + Parts Control projects** — there is no per-row delta to lose. So:

- The mirror syncs **whole projects**, not deltas → the DB does NOT need a bespoke retroactive
  backfill to "catch up": re-running `sync_all_mirrors.py` re-hydrates every order/part from
  current Asana in one clean pass.
- What IS genuinely missing/backlog is not order-status history but the **structural links** that
  were never captured: `parts_control.build_assembly_pack_gid` NULLs on finish/subcon rows
  (the §6a powder-coat gap), `ordering_queue.order_task_gid` all-NULL (no order↔line link), and
  `bom_requirements.po_id` all-NULL. Those need targeted remediation, not a re-pull of order
  status.

So: **postpone a "backfill from cron logs" effort** — verify with a fresh `sync_all_mirrors.py`
first; the order-status history reconstructs from current state. The *structural* link backfills
are the real backlog.

---

## 5. Where dashes trust live vs DB today (and the fallback reality)

| Pane/surface | Source | Live-validated? | DB-only fallback? |
|---|---|---|---|
| os_status packs/subtasks/sections | live Asana | n/a (pristine) | **NO** — crashes if Asana down |
| os_status procurement counts | `parts_control` DB | no | yes (DB is the source) |
| giles order cards | live Asana | n/a | **NO** |
| bom_tracker stages | live Asana | n/a | **NO** |
| ops Pane-1 heat map | `procurement_tasks.order_status` DB | **NO** | yes (DB source) |
| ops Pane-2 confirmations | DB + live reconcile | **YES** (per-row ≤200, `task.completed` check drops completed legacy) | **YES** — token-off/row-fail → trust DB `(r,None)` + render stored status. THE only place the full doctrine already works |
| ops Pane-3 email chases | `overdue_chase_log` DB + `cur_status` DB gate | DB-derived gate | yes (DB) |
| ops Pane-4 unmapped emails | `email_po_matches` DB | partial | yes (DB) |
| ops Pane-5 ordering ledger | `ordering_queue` DB (+ suppliers/contacts) | no | yes (DB) |

**Key architectural truth:** the current Asana access layer (`api_get`) has **no catch around
`raise_for_status`** — so os_status / giles / bom_tracker all hard-crash if Asana is unreachable
after their 600 s cache expires. Only ops-oversight Pane-2 implements the "trust DB when live
fails" doctrine. **A shared, reusable validate→fallback helper is the core infrastructure this
doctrine needs** — built once, used by every pane (§7).

---

## 6. The three NEW requirements (2026-09-04) + the pack↔part↔order linkage

### 6a. Edge case — sub-con task not attributed to a pack (design the scenario)

**Problem:** a finish/subcon part (powder-coat, engrave, machine) whose blank belongs to a build
pack is frequently NOT itself pack-linked in `parts_control` — e.g. the powder-coat row for
`1037.1016.1.007` (vertical-5-btn faceplate, a `.BS.2` component) has `build_assembly_pack_gid = None`.
It is "not currently possible" to express "this subcon serves that pack."

**What must be designed:**
- A **pack-inheritance rule**: a subcon roll inherits its parent **blank part's** pack
  (`1037.1016.1.007` powder row → parent `1037.1016.1.007`/`.1.2` blank → `.BS.2`).
- A backfill of existing NULL subcon rows from their parent blanks (data-driven, dry-run first).
- A guard/warning where inheritance is ambiguous (a finish process that coats parts for several
  packs in one order — must attribute to all, not guess one).
- Capture this as an **edge-case scenario** in deployment tracker `EC-Pn-N` form rather than ad
  hoc — it is a repeatable class, not a one-off.

This is the crux per Giles ("essential to the success of this system") — it is what lets order
21598 (powder coat) correctly surface against `.BS.2`.

### 6b. Vendor-name mismatch ↔ "order-from vendor" base assumption (route remap / learning loop)

**Problem:** an imported part records its **process/source** vendor (`Engraving`, `Powder Coating`,
`Laser Cut`), but the **order** names the vendor we actually buy from (`Alexander Pollock
Engraving`, `IDL Powder Coating`). Today these don't join without a normalisation map.

**Giles's base assumption to keep:** *"the vendor gets updated to who we actually order it from."*
That update is the live workflow and should remain the source of truth.

**The new round (learning):** when a vendor is updated to the actual order-of vendor, the system
should **assess & attribute which vendors we'll order that part from in future**, i.e. build and
retain the `process/source-vendor → actual ordered-from vendor(s)` correspondence over time. Each
vendor update becomes a training signal. This is the **route reassessment** piece (a part class
through Thameside machining is always a machining order; a call mentioning a part number can be
linked to the correct order task). Needs: a small mapping/learn table in ops.db, populated on
vendor update, consulted at order→vendor→part join time.

### 6c. Arrived / QC-state flagging (update to `build_os_status.py`)

Giles's exact logic request:
- If an order **has arrived** and its QC state is **"awaiting QC"** → **mark it as HERE**
  (arrived, physically present — the pack is unblocked on that part).
- If its **subtasks are still incomplete AND have been raised into the QC-issues project** →
  **flag an outstanding item inside that pack that will cause a delay** (the arrived part has a
  QC issue → it may need rework/re-order → schedule risk).

This is a subtle but high-value pane update: it stops a pack from showing as "awaiting part" when
the part is physically here in QC, while still warning when that QC is expected to defect. It
hinges on (i) a reliable arrival/QC-state signal and (ii) the QC-issues project link, both of which
need the centre-of-truth plumbing from §3 to be non-stale.

### 6d. The pack ↔ part ↔ order link (the core requirement)

The essential join chain (validated feasible — order Asana tasks carry their sesame parts as
subtasks; `parts_control` maps sesame_pn → pack):
```
order Asana task --subtasks--> [sesame part ids]
sesame part id --parts_control.build_assembly_pack_gid--> build pack
→ order IMPACTS pack P iff one of its subtask parts maps to P
```
Gaps that block it: (1) finish/subcon `build_assembly_pack_gid` NULLs (6a); (2) vendor-name
normalisation (6b); (3) the DB never learns an "on order" transition in real time (3), so the
order→part→pack view can be stale; (4) the "only still to-quote / to-be-ordered / on-order, NOT
overdue-arrived tasks" filter (see previous pack-parts-orders linkage reference) must be applied.

---

## 7. Proposed target architecture (change map — for approval, NOT built)

### New reusable core (built once, used by all future dashboards)
1. **`centre_of_truth.py`** (skills/os_portal/): a shared render-input service with:
   - `asana_or(fetch, db_row)` — try live per-row reconcile, **fall back to the DB row AND emit
     an `unreconciled` flag** (surfaces on the page as a small "live check failed — showing DB")
     instead of crashing or silently trusting.
   - The **epoch-disk-cache** caller (already in `api_get`) stays the local-first transport.
   - Optionally: wrap `api_get` to catch `raise_for_status` so a full Asana outage degrades to
     cache + DB instead of crashing all three live-generators (fixes §5 hole).
2. **A shared "live-vs-DB reconcile" pre-pass** generalising ops-oversight Pane-2's
   `confirmations_render_ready` → reusable `validate_rows(db_rows, live_fetcher)` so every pane
   that shows order/part/QC status gets the same treatment.

### Script-level changes (each gated on §3 option + these being approved)
| Script | Change |
|---|---|
| `parts_control_propagator.py` | **(if §3-opt1)** also push the flipped status to the DB mirror via `db_writer` at transition time; **(always)** learn/record the updated order-from vendor into the §6b mapping. |
| `sync_all_mirrors.py` (+ siblings) | keep as the authoritative wholesale re-hydrator; run on demand before diagnosing staleness. |
| `build_ops_oversight.py` | add/repair the ordering-ledger order↔task link once `ordering_queue.order_task_gid` linkage lands; apply §6c arrival/QC flag rendering with live validation + DB fallback. |
| `build_os_status.py` | §6c arrived/QC flag on the per-part view; fix the pack→vendor attribution to part-level (§6d) once 1–2 validate on one pack in front of Giles; add §3-opt2 live reconcile if chosen. |
| `build_bom_tracker.py` | use the shared reconcile pre-pass for its no-pack surfacing; route through `centre_of_truth` fallback. |
| `build_giles_worklist.py` | adopt shared helpers; live reconcile order status per §3-opt2. |
| `build_deployment_tracker.py` | track the §6a EC + §6b learn-table + the chosen §3 option as deployment items (P-staged/EC-tracked). |

### Data-model additions
- Vendor/process **learn table** (source_vendor → actual vendors, counts/confidence) for §6b.
- **(§3-opt1 only)** a write hook so `order_status`/`stock_action` reflects transitions promptly.
- **(§6a)** subcon-pack inheritance capture on `parts_control` (or a derived join table).

### Downstream automations that already depend on this info
- The **email-purchasing HITL** (qa_sync → Google Sheet) reads current quote/PO state.
- The **ordering_queue / create_order_tasks → draft-email** flow (parts grouped to order → email to
  review). Giles notes lasercut BOMs are too big for the vendor task — what's wanted is **just the
  draft email with the Dropbox link**, and he completes the order. This draft-email-with-link-only is
  itself a build item under this umbrella.
- **Opens Oversight §3 staged column** benefits directly from a non-stale on-order/arrived signal.

---

## 8. Recommended build order (validate-then-rollout, per Giles's scoping-first rule)

1. **Decide §3 option** (event-push vs live-expose vs faster mirror) — this gates everything.
2. **`centre_of_truth.py` shared core** + wrap `api_get` fallback (fixes the crash-if-Asana-down
   hole and gives every future dashboard the validate→DB-fallback doctrine in one place).
3. **§6a subcon-pack backfill** (dry-run → review on one pack → commit), the backbone of the link.
4. **§6b vendor learn-mapping** (seed from existing orders; grow on updates).
5. **§6c arrival/QC flag** in build_os_status (validated against Asana, DB fallback).
6. **§6d pack→vendor attribution** rewritten to part-level ONLY after 3–5 validate clean on ONE
   pack in front of Giles.
7. **§7 lasercut draft-email-with-Dropbox-link + ordering-ledger order-link** as the consuming
   surfaces.

**Explicitly NOT building:** any of the above until Giles has reviewed this doc and picked the §3
option + confirmed scope. No code changed to produce this document.

---

## 9. Open decisions for Giles

1. **§3 option** for closing the DB-lag: (a) event-push at transition, (b) always live-reconcile
   in the generator with DB fallback, (c) more-frequent Order-Numbers mirror bolt, or (d) a mix.
2. **Backfill**: agree we do NOT chase a cron-log backfill but instead (i) re-run a clean
   `sync_all_mirrors.py` to re-hydrate order status, then (ii) do targeted STRUCTURAL backfills
   (§6a packs, ordering_queue order-task link, bom_requirements.po_id) — confirm.
3. Priority ordering of §8 once the §3 option is chosen.
4. Whether the sub-con edge-case is captured now as an Edge-Case intake/EC item (name it) so it's
   tracked rather than ad hoc.
