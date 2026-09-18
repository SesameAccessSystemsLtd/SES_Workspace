# Change Register — Centre-of-Truth Build (Options A + B approved)
**Date:** 2026-09-04 · Author: Hermes · Status: APPROVED DIRECTION (A+B), NO CODE CHANGED YET
**Scope:** Option A (event-push DB write at the state transition) + Option B (every generator
rendering order/part/QC state validates live against Asana, falls back to ops.db, and flags).
Option C rejected. Full scoping context: `docs/os-portal-centre-of-truth-data-routing-2026-09-04.md`.

This is the definitive, code-pointed change register. Each item states WHY, WHAT, WHICH FILE/
function, data impact, risks, and a verification step. Ordered by dependency. **Nothing below
has been implemented.**

---

## PHASE 0 — foundational shared core (build once, everything else consumes it)

### CHG-0A · New shared helper: `centre_of_truth.py`
**New file** `skills/os_portal/centre_of_truth.py`, exposing:
- `reconcile_ready(db_rows, live_fetch_row, key, token_h, cap=200)` → `[(row, live_or_None), …]`.
  Generalises ops-oversight's `confirmations_render_ready` — takes DB rows, live-fetches each by
  key, drops rows Asana says are closed/non-relevant, tags survivors with live-or-None.
- `render_pref(live, db_val)` → live-first, DB fallback (`live.get(field) or row[field]`), plus an
  `unreconciled=True` marker when `live is None` (token off / row failed / Asana down) so the pane
  can visibly say "live check failed — showing DB snapshot".
- `async_or_cached(fetch_fn, db_row)` — Option B accessor under the epoch disk cache.
- A wrapped `safe_api_get(...)` that catches `raise_for_status` and returns None on failure instead
  of raising (fixes the crash-hole).
- `flag_span(field, unreconciled)` → tiny HTML pill `<span class="unrec" title="live check failed — showing DB">`.
Why: only ops-oversight Pane-2 obeys the validate→fallback doctrine today; 3 live generators
hard-crash on an Asana outage. **Build once, consume everywhere.**
Verify: import it from one generator; with `ASANA_PERSONAL_ACCESS_TOKEN` unset in env it renders DB
rows + the flag and does NOT raise.
Risks: none (new, additive, read-only).

### CHG-0B · Wrap `api_get` so an Asana outage degrades instead of crashing
**File:** `skills/os_portal/build_os_status.py`, function `api_get` (~L194-203).
Change: wrap `requests.get` + `r.raise_for_status()` in try/except → on failure return `None` (or
raise a typed `AsanaUnavailable`) rather than propagating a raw exception. Keep the epoch disk-cache
as the first layer (unchanged). Callers then branch on `None` → serve cache → serve DB + flag.
Why: CHG-0A needs a defined failure signal; today a broken pipe aborts the whole generator.
Verify: kill network / point token at garbage → generator still emits page from cache+DB + flags.
Risk: LOW but touches the ONE fn all 5 shares-cache — must not break the cache hit/epoch logic;
test warm+epoch paths before cold.

---

## PHASE 1 — Option A: write the DB at the transition moment

### CHG-1A · Add `update_parts_control_stock_action()` to the DB gateway
**File:** `skills/purchasing/db_writer.py` (+ a tiny `upsert_parts_control_stock_action` stub if a
full row is preferred — targeted UPDATE is safer, no NOT-NULL risk).
New fn: `update_parts_control_stock_action(task_gid: str, new_stock_action: str, source: str)`
→ `UPDATE parts_control SET stock_action = ? , updated_source=source WHERE asana_task_gid = ?` +
`_log_write(...)` to `db_write_log` (audit). Returns rows-affect 0/1.
Why: so the propagator can push just the flipped field without needing the full task row.
Verify: run once against a dry-run task gid → `db_write_log` gains a `parts_control` row; value
updates; second run idempotent.

### CHG-1B · Propagator pushes the flip to ops.db after applying to Asana
**File:** `skills/asana/scripts/parts_control_propagator.py`.
- Import `db_writer.update_parts_control_stock_action`.
- In `_watchdog_commit(...)` AND the interactive `--commit` path, AFTER `ok, fail = push_updates(
  updates, token)` and the audit log: loop `updates` and for each call
  `update_parts_control_stock_action(u["task_gid"], u["new_stock_action"], source="parts_control_propagator")`.
- Count DB-write failures into `problems`/audit_errors so a **clean run stays silent** (watchdog
  must NOT break its quiet-on-success contract); a DB push failure surfaces as an alert.
- Only write the DB for updates Asana actually accepted (`ok` set), not failed ones.
Why: closes the ~24h lag — DB parts_control.stock_action is current within one ~30-min propagator
cycle, matching the live Asana flip (this is "the PO records updated when identified on order" for
the parts side).
Verify: run `--watchdog` twice on a job with a freshly-on-order parent; first run shows the flip
applied live + parts_control row updated; second run is silent/no-op.
Risk: MEDIUM — touching the silent watchdog; preserve quiet-on-success; DB failure must still
trigger the error path (test by pointing at a read-only DB path).
Note (honesty): the propagator flips **child parts** (`parts_control.stock_action`). The parent
**order** record's `procurement_tasks.order_status` is set by the order-flow/QA scripts + the
nightly Order-Numbers mirror, NOT this propagator — so A covers the parts table; the parent-order
"on order" is covered by B (live-read) + the order-create flow. Don't conflate the two.

### CHG-1C · [Option A hardening] capture supplier "update-to" vendor → learn table
**File:** new `skills/purchasing/supplier_vendor_learn.py` + table `supplier_vendor_map`.
Triggers when the order-from vendor changes on a part/order (order-flow OR after propagator flip).
Writes `(source_vendor/process, actual_vendor, sesame_or_order_ref, ts, confidence)` — feeds §4b
(the vendor-update base-assumption learning loop). Logged, idempotent upsert, dry-run first.
Why: turns each vendor-update into a training signal for the §6b route reassessment.
Verify: after one real vendor update the table gains a mapped row; a re-run doesn't dup.
Risk: LOW; new table + optional hook. (Do this only after confirming CHG-1B is stable.)

---

## PHASE 2 — Option B: live-reconcile where order/part/QC state is displayed

### CHG-2A · ops-oversight Pane-2 (confirmations) → route through the CHG-0A helper
**File:** `skills/os_portal/build_ops_oversight.py` (`live_order_info`, `confirmations_render_ready`,
`confirmations_html`).
Refactor to call `centre_of_truth.reconcile_ready` + `render_pref` so the ~200-row live validate and
the DB-fallback+flag reuse the shared path (keeps the already-correct behaviour, removes duplication).
Why: single implementation of the doctrine; reduces drift.
Verify: page still drops completed legacy rows to ~5; check a live-unreachable run shows flags.
Risk: LOW — pure refactor of working code; guard the `render_pref` fallback exactly as today.

### CHG-2B · ops-oversight Pane-5 (ordering ledger) — add the order→task live link
**File:** `build_ops_oversight.py` ordering-ledger renderer (~L390-408).
Today `ordering_queue.order_task_gid` is NULL everywhere → the "which order/group task + its due
date" column can't render from DB. Per Giles's requirement and to make §3staged accurate: enrich
each row's associated order task + due date by a live Asana reconcile (bounded, per CHG-0A), showing
DB rows + live order link when live succeeds, flag when not. Keep PN column removed.
Why: the ledger and its "staged/ordered" staging column (Giles: Section 3 of ops-oversight)
benefit directly from a non-stale order link + arrival/order date.
Verify: ledger rows show an associated order task + live due; unreachable run flags rather than
fabricating. Cost decision flagged (N+1 ~127/refresh) — accept under B.

### CHG-2C · build_os_status per-part view — §6c arrived/QC flag (with live reconcile + DB fallback)
**File:** `skills/os_portal/build_os_status.py` (`parts_readiness`, `render_project_sections`,
per-part rows).
Add the arrived/QC rule:
- if a part's order **has arrived** and QC is **"awaiting QC"** → mark the part **HERE** (arrived,
  pack unblocked on it);
- if that arrived part's subtasks are **incomplete AND raised into the QC-issues project** →
  **flag an outstanding/QC-issue item that will cause delay** on the pack (with task link).
Both signals live-reconciled against Asana (CHG-0A) with the DB snapshot + flag as fallback; only
orders still to-quote / to-be-ordered / on-order show as impactors (the overdue-arrived filter already
documented).
Why: stops a pack showing "awaiting part" when it's physically here in QC, while still warning when
that QC is expected to defect — the exact build_os_status update Giles requested.
Verify: pick job 1037; the `.BS.2` pack shows the laser-cut backing box as HERE (arrived) not
awaiting; any QC-issue pack shows the delay flag with link. Confirm against live 21567 (lasercut
arrived + in QC) — it should NOT appear as an open impacter and, if its QC is red, should show the
delay flag.
Risk: MEDIUM — touches core render; build the flag as an additive column, keep old attributes until
Giles approves in situ.

### CHG-2D · Fix pack→vendor attribution to PART level (the core link) under B
**File:** `skills/os_portal/build_os_status.py` (`orders_by_job` → per-pack list; `render_project_sections`
~619-648).
Rewrite the per-pack vendor dropdown to part-level: order Asana **subtasks** (sesame parts, epoch-
cached) × `parts_control.build_assembly_pack_gid` (part→pack) → the pack's genuine impacting orders.
Requires the §6a subcon-pack backfill (CHG-3A) so finish parts resolve to their pack; applies the
overdue/arrived filter (CHG-2C); falls back to part-level DB proof, DB+flag, else — only as a last
resort — the current job-level list labelled "N open job orders (part-level unresolved)".
Why: 1037.1016.BS.2 must show its true 21591 Engraving + the finish pack → not all 9 job orders.
**Explicitly the "essential" item. Do NOT do before CHG-3A validates on one pack in front of Giles.**
Verify: after 3A, on job 1037 the vendor dropdown for `.BS.2` lists only the orders whose parts map;
compare against Giles's known 2 — iterate until he's satisfied, then roll to other jobs.
Risk: HIGH — this is the renderer change that must not over/under-shrink; gate hard on Giles sign-off.

---

## PHASE 3 — structural data backfills that unblock the link (Option A+B path)

### CHG-3A · Subcon/powder parts inherit their parent blank's pack (§6a)
**File:** `skills/os_portal/` new `backfill_subcon_pack.py` (or extend drawing/reconcile scripts).
Logic: for `parts_control` rows whose `build_assembly_pack_gid` is NULL but whose `vendor_name` is a
finish/process (`Powder Coating`, `Engraving`, `Laser Cut`, machining…) and which are a subcon of a
parent blank **sesame_pn** that HAS a pack → set the subcon's pack = parent blank's pack. Ambiguous
cases (one finish order coats parts for several packs) attribute to ALL, never guess one; log
ambiguity as an edge case (EC-form). **Dry-run → CSV review → Giles approve → apply.** Direct UPDATE
or via `db_writer` with audit (`update_parts_pack_allocation` precedent exists).
Why: the powder-coat row for `1037.1016.1.007` must resolve to `.BS.2` (currently `pack=None`); this
is the crux per Giles ("essential to the success of this system") and a hard pre-req for CHG-2D.
Verify: dry-run CSV shows the planned NULL→`.BS.2`-style assignments; Giles validates one pack's set
against real-world knowledge; apply; re-run CHG-2D validation.
Risk: **MEDIUM-HIGH** — writes to `parts_control.build_assembly_pack_gid`; HITL gated, reversible
(backed-up DB + audit log), never delete.

### CHG-3B · Linking gaps: `ordering_queue.order_task_gid` and `bom_requirements.po_id`
**File:** the writers that create ordering-queue rows (`create_order_tasks.py`,
`audit_parts_control_routing.py`) and the BOM import (`BOM_IMPORT_4_Asana_v4.py`).
Capture the order/group task GID on each `ordering_queue` row, and the PO on each `bom_requirements`
part, at creation time so the DB holds the order↔line↔part link instead of all-NULL. (Structural
backfill of existing rows optional.)
Why: CHG-2B and the pack↔part↔order join need a reliable DB-side link; today both are NULL, forcing
live reconcile. Correct the writers so future data is populated, reconcile covers legacy.
Verify: after one order pipeline run, a new ordering_queue row + a new bom_requirements row carry the
order/po id.
Risk: LOW-MEDIUM — touches create-flow writers; additive capture, never destructive.

### CHG-3C · Vendor/process name normalisation table (§6b join need)
**File:** new `skills/purchasing/vendor_normalise.csv` (source_vendor/process ↔ actual vendor aliases)
consumed by the CHG-2D join.
Why: order names say "Alexander Pollock Engraving" / "IDL Powder Coating" but parts_control vendors
say "Engraving" / "Powder Coating"; CHG-2D cannot join order→part without it. Seeded from existing
orders, grows via CHG-1C.
Verify: CHG-2D resolves the 2 correct orders on job 1037 without the 7 job-noise ones.
Risk: LOW.

---

## PHASE 4 — roll-out hygiene (do not skip)

- **CHG-4A** Update cron split owners (`os_refresh_light.py`, `os_refresh_bom.py`, `os_refresh_ops.py`
  in `~/AppData/Local/hermes/scripts/`) only if 2B/2C add live cost — re-check the 300 s budget and
  staggering.
- **CHG-4B** Update `os-portal` SKILL + `references/pack-parts-orders-linkage.md` + this register +
  deployment tracker P-stages/edge-cases so future work references the shipped state (bake-don't-inject
  discipline).
- **CHG-4C** Add a **feedback channel check** per new data column (per governing continuous-improvement
  principle) so wrong/misattributed pack figures are capturable to the audit ledger.

---

## Dependency order (the sequence to build & validate)

```
CHG-0A (helper) ──► CHG-0B (api_get catch)
      │
      ├─► CHG-1A (db_writer fn) ──► CHG-1B (propagator push) ──► CHG-1C (vendor learn, later)
      │
      ├─► CHG-2A (ops pg2 refactor) ──► CHG-2B (ops ledger live link)
      │
      └─► CHG-3A (subcon pack backfill, HITL) ──► CHG-2C (arrived/QC flag in os_status) ──►
             CHG-3B/C (links + normalise) ──► CHG-2D (part-level pack→orders)  ← validate on 1037
```
Build 0 first; 1A→1B proves automatic DB currency; 3A gates the flagship 2D. Everything is gated on
Giles sign-off at the numbered decision points (3A dry-run review, 2D one-pack validation).

---

## Summary of files touched (changed vs new)
**Changed:** `build_os_status.py`, `build_ops_oversight.py`, `parts_control_propagator.py`,
`db_writer.py`, `create_order_tasks.py`, `audit_parts_control_routing.py`,
`BOM_IMPORT_4_Asana_v4.py`, os-portal SKILL references, deployment state JSON.
**New:** `skills/os_portal/centre_of_truth.py`, `skills/os_portal/backfill_subcon_pack.py`,
`skills/purchasing/supplier_vendor_learn.py`, `skills/purchasing/vendor_normalise.csv`.
(Sync scripts / nightly mirror stay as the authoritative wholesale re-hydrator.)
```

## Explicit non-goals (Option A+B)
- NOT switching the nightly `sync_all_mirrors.py` wholesale-hydrate cadence (Option C rejected).
- NOT deleting `orders_by_job` until CHG-2D validated; it remains the labelled fallback.
- NOT changing Asana as a source of truth; the DB and cache exist to serve dashboards locally with
  reconcile + fallback, per the doctrine.

---

## Build status (2026-09-04) — SAFE set built + tested, gated items staged

Built + tested by the approved build agent (deepseek-v4-pro). No Asana writes, no live-DB
writes; tests ran against a temp SQLite DB + read-only Asana/DB.

**DONE (py_compile + light functional test):**
- CHG-0A — `skills/os_portal/centre_of_truth.py` (reconcile_ready / render_pref / flag_span /
  safe_api_get / AsanaUnavailable). Import + behaviour test passed.
- CHG-0B — `build_os_status.api_get` raises typed `AsanaUnavailable`; epoch disk cache / TTL /
  in-proc memo preserved; main() degrades to DB + visible flag (no raw crash).
- CHG-1A — `db_writer.update_parts_control_stock_action()` targeted UPDATE + db_write_log audit;
  temp-DB test passed (writes stock_action + audit, idempotent, safe skip on missing gid).
- CHG-1B — `parts_control_propagator.py` pushes accepted flips to ops.db after --commit / watchdog;
  silent-on-success preserved (DB-write failures count into the alert path). Writes only when fail==0.
- CHG-2A — `build_ops_oversight` Pane-2 confirmations now routes through reconcile_ready + render_pref
  (drop-completed-legacy behaviour preserved).
- CHG-2B — ordering-ledger adds live order-task + due link via reconcile_ready (bounded, DB+flag fallback).
- CHG-2C — `build_os_status` additive arrived/QC signal (HERE + QC-issue delay flag, live-reconciled,
  DB+flag fallback). Existing attributes untouched.
- CHG-3B — capture of `ordering_queue.order_task_gid` (create_order_tasks append path) +
  `bom_requirements.po_id` (Stage-5 import, via new db_writer po_id support) + explicit NULL
  order-task fields in audit_parts_control_routing.
- CHG-3C — `skills/purchasing/vendor_normalise.csv` seeded from existing order naming (uncertain rows commented).

**STAGED (artifacts produced, NOT applied):**
- CHG-3A — `backfill_subcon_pack.py` dry-run → `data/os_status/review/subcon_pack_backfill_plan.csv`
  (791 NULL-pack finish rows: 551 direct / 102 ambiguous / 138 unresolved).
- CHG-2D — `demo_pack_parts_orders.py` → `data/os_status/review/pack_parts_orders_1037_BS2.md`.
  Result: 1037.1016.BS.2 resolves to **2** genuine orders (21591 Engraving + 21249 Exact Laser Cut)
  of 10 job-level orders — the part-level join works.

**Left for Giles / live validation:** CHG-2B/2C live-reconcile cost vs the staggered cron budget;
CHG-3A pack-inheritance ambiguity (powder-coat 1037.1016.1.007 must resolve to .BS.2 specifically);
`push_updates()` returns counts not a per-item ok-set (so CHG-1B writes the DB only when fail==0).
CHG-1C, CHG-2D production rollout, CHG-4A/C remain open.
