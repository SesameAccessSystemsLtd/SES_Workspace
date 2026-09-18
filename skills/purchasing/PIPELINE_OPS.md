# PIPELINE_OPS — Parts Impact + Improvement Engine

**Operations manual for the purchasing pipeline built 2026-09-09 → 09-11.**
Written for Hermes (and any future agent) to run, test, validate and maintain
without re-deriving the investigation.

Companion docs: `SKILL.md` (email pipeline, separate concern) ·
`QA_SCORECARD_SCOPE.md` (parser QA) ·
final plan report `reports/purchasing-final-plan-2026-09-11.html`

---

## 1. What this system is for

Four outcomes. Anything that does not serve one of them was deliberately cut.

1. **Know what every live order actually is** — unique number, a delivery date that
   means delivery, a scope that excludes history.
2. **See production impact before it is too late** — which packs are at risk, why,
   what the next action is, whose it is.
3. **Status reaches people without going through Giles** — a change stream and a
   portal, not a question.
4. **Catch friction once and prove it is gone** — edge case → case → action →
   closed only on a measured move.

---

## 2. Components

| Script | Runs | What it does | Writes |
|---|---|---|---|
| `impact_engine.py` | nightly 01:05 | Scores every live build pack for supply risk; forecasts a production date where Asana has none | `production_impact` |
| `improvement_engine.py --run` | nightly 01:20 | 17 harvesters → clustered cases → metric snapshots → validates staged actions | `improvement_cases`, `improvement_observations`, `metric_snapshots` |
| `po_number_allocator.py` | on demand | Issues PO numbers atomically; adopts manual ones; rebinds when lines move | `po_number_ledger` |
| `validate_pipeline.py` | on demand | Read-only health check, 22 assertions | nothing |

Launchers for the two cron jobs live at
`C:\Users\GilesHugo\AppData\Local\hermes\scripts\impact_engine.py` and
`improvement_engine.py` — same pattern as `asana_mirror_sync.py` (venv python,
CREATE_NO_WINDOW, exit code passed through so a failure raises the cron flag).

**Ordering matters.** Mirror sync 01:00 → impact 01:05 → improvement 01:20. The
improvement engine harvests from the impact engine's output; running it first
gives a stale register, not an error, which is worse because it looks fine.

---

## 3. Schema this system owns

Added by `skills/sql_db/scripts/migrate_impact_engine.py` (idempotent, additive
only, safe to re-run — it checks for existence before every statement).

| Table | Purpose |
|---|---|
| `part_supply_events` | The change stream. One row per REAL field change, with a significance grade. This is what `db_write_log` could never be. |
| `production_impact` | One row per live pack per run. Readiness, risk band, reason, next action, owner. History retained for trend. |
| `improvement_cases` | Friction clustered by signature, with a state machine: new → assessing → staged → validating → proven. |
| `improvement_observations` | Raw signals attached to a case. |
| `metric_snapshots` | The validation spine. 14 metrics captured nightly. |
| `po_number_ledger` | Authoritative PO counter. reserved / confirmed / voided, origin reserved / manual. |

Columns added to existing mirror tables: `parts_control` (order link, project
code, supply route, order status, arrived qty, rev, due/completed dates),
`build_assembly_packs` (readiness, stage, steel profile, needs-from-purchasing,
hours, due date), `procurement_tasks` (delivery days, impact, scope, project code).

---

## 4. How to test and validate

### Routine — every time you touch anything here

```
cd C:\Users\GilesHugo\SES_Workspace
.venv\Scripts\python.exe skills\purchasing\validate_pipeline.py
```

22 assertions across 7 sections. Exit code 1 if any FAIL. Every check is a
SELECT — safe to run at any time, including mid-sync.

**Known-good baseline, 2026-09-11: PASS 18 · WARN 4 · FAIL 0.**

The four WARNs are decisions awaiting Giles, not faults:
ledger not seeded · 411 orders with no scope · 327 stale parts rows ·
db_write_log at 3.8M rows.

### The checks that matter most

| Check | Why it is the canary |
|---|---|
| `due_date is a DELIVERY date` | If rows appear BEFORE the order date, the field mapping has regressed to the original bug. Every lateness figure in the estate depends on this. |
| `pack column holds GIDs, not names` | Non-zero means a writer has started putting names in `build_assembly_pack_gid` again. Four writers have historically disagreed. |
| `no null->null artefact events` | Non-zero means the zero/empty collapse bug is back in `_emit_supply_events`. It silently destroys staging-field signal. |
| `change detection holding` | Thousands of write-log rows per day means change detection has been bypassed and the log is noise again. |
| `no scheduled job in an error state` | The improvement engine harvests cron failures. Non-zero here means something has been failing, possibly for days. |

### Dry-run everything that writes

```
python impact_engine.py --dry-run          # prints the at-risk list, stores nothing
python improvement_engine.py --report      # prints the register, harvests nothing
python migrate_impact_engine.py --dry-run  # lists pending DDL, applies nothing
python po_number_allocator.py --status     # ledger state, gaps, stale reservations
```

---

## 5. How to maintain

### Adding a harvester to the improvement engine

Write `h_<name>(conn) -> list[dict]`, add it to `HARVESTERS`, add its category to
`CATEGORY_WEIGHT`. Each returns observation dicts with `signature`, `category`,
`title`, `source`, and optionally `mode`.

**`mode: "state"` matters.** A harvester reporting a standing backlog (43
unassessed packs) sets it, so occurrences become the current count. A harvester
reporting discrete events (a vendor was renamed) omits it, so occurrences
accumulate. Get this wrong and a static backlog inflates its own score every
night until it buries everything else.

Harvesters are failure-isolated — one raising an exception is caught, logged and
skipped. Never let a harvester write.

### Adding a metric

Add to `METRICS` as `key: sql` returning one number. It is snapshotted nightly
from then on. Metrics are how staged actions are validated, so a metric that
cannot move is worse than no metric.

### Staging an action against a case

```
python improvement_engine.py --stage <case_id> \
  --action "what will be done" --type eliminate|simplify|automate|delegate|retain \
  --owner <who> --metric <metric_key> --target <number>
```

Captures the baseline at that moment. The case then closes **only** when the
metric reaches target AND every reading since staging held it, for at least 14
days. If time passes and the metric has not moved, the case is marked NOT
WORKING with the numbers attached. Nothing closes on assertion.

### Tuning the impact forecast

Named constants at the top of `impact_engine.py`, each with the reasoning:

- `LABOUR_QUEUE_DAYS = 3` — working days from parts-ready to buildable. Confirmed
  by Giles 2026-09-10. This is a **labour** allowance, not a materials lead time.
- `ROUTE_LEAD_DAYS` — lasercut 2, steel 2, subcon 12, buy 14, inhouse 5, stock 0.
  Lasercut and steel are near-immediate in practice.
- `READY_STATES` — the PARTS-Readiness values that mean schedulable now.

Change these and every forecast moves. They are assumptions, stated as such.

---

## 6. Traps — read before changing anything here

These are real defects that were found and fixed. Each one was invisible and
plausible, which is what made it expensive.

1. **Never trust a mirrored column without reading the sync's field map.**
   `procurement_tasks.due_date` was populated from the "Order date " custom field
   for years. Every lateness figure measured days-since-ordered.

2. **Asana reference fields return the referenced task's NAME, not its GID.**
   This is how `build_assembly_pack_gid` came to hold 1,470 names. Store the name
   honestly, resolve the GID by lookup, and skip ambiguous names rather than
   guessing — 12 pack names resolve to 2+ tasks.

3. **A named-parameter INSERT with an optional column will raise if a caller
   omits it.** `write_ordering_edge_case` did, every caller omitted
   `asana_edge_case_gid`, and it crashed the nightly audit and the order-creation
   loop for two months while looking like a quiet system.

4. **Do not collapse "0" to NULL.** `_norm(x) or None` destroyed the `0 → value`
   transition that staging fields are made of. Arrived Stock Qty is exactly such
   a field.

5. **Pack notes are keyed on a mutable name.** ~100-150 parts on job 1595 carry
   the same pack written two ways (stub + full reference), so a
   description-scanning report reads them double. Confined to 1595; 0 elsewhere
   in a 130-part sample.

6. **`procurement_tasks.po_number` is not a clean key.** 933 distinct five-digit
   prefixes across 1,529 rows — supplier refs and invoice numbers were typed into
   the same field. Never derive the next PO number from it. The ledger's
   watermark is bounded by `WATERMARK_MAX_JUMP = 200` for this reason.

7. **327 `parts_control` rows no longer exist in the Asana project** and still
   read as live. They inflate every live count. Identify them by
   `supply_route IS NULL`.

---

## 7. Domain rules that shape the design

Confirmed by Giles, not inferred. Changing code against these will produce
correct-looking output that is wrong.

- **"Actual Cost" on a build pack is unplanned ENGINEER HOURS, not money.** It is
  the closest thing to a direct friction meter and is the north-star metric.
- **Ordered quantity is SUPPOSED to exceed the pack total.** BOM import writes the
  total required; pack ingest adds the per-pack split and a marker; ordering buys
  the sum plus extra for stock listing or contingency. A mismatch flag between
  ordered qty and pack sum would fire on correct buying. Per-pack allocation was
  cancelled for this reason — it belongs to the picking report, not purchasing.
- **The PO number is APPLIED to a task that already exists.** The system never
  creates the order task. Format: `<sequential>-<project id | DDMMYY>`, and the
  **day is not zero-padded** (`70926` = 7 Sep 26). Verified on 400 live POs:
  230 project-suffixed, 122 date-suffixed.
- **Orders are messy by nature.** A line comes back unavailable and moves; a NEEDS
  item takes a direct PO; a line is re-homed to a project task or a new one. All
  are `rebind()`. The allocation follows the PART, never the order.
- **The Arrived Stock Qty path is deliberately NOT in use yet**, pending correct
  stock mapping from the Golden Index BOM import. An empty field is the field
  working, not failing.
- **Golden Index coverage is measured on the STOCK NUMBER, not the part number.**
  Part numbers are generic templates; Parts Control holds project-specific
  instances. They can never string-match. Current: 361 of 1,946 stock-bearing BOM
  lines = 18.6%. See `golden_index/bom_matching_report.md`.
- **For LASER-CUT parts the ORDER is the PARENT OF THE PARENT (grandparent), not
  the parent.** Verified live 2026-09-11.
  ```
  Laser-cut part  "6 off 1575.6000.A.070.01 - Rubber buffer plate Laser Cut"
     parent        "Laser Cut - 1575 Jenners"        ← VENDOR GROUP, not an order
        grandparent "20978-1575 - Lasershape ..."     ← THE ORDER (PO 20978)

  Normal part     "1 off 1569.6000.A.37 MayTec assembly"
     parent        "20705-1569 - MayTec - ..."        ← THE ORDER (PO 20705)
  ```
  Rationale (Giles): laser-cut subtasks are deliberately **NOT** adopted under the
  order task — if they were, the Order Numbers project would be flooded with
  hundreds of tasks that suddenly adopt all of its fields. So the part's parent is
  a vendor group and the real order sits one level higher.
  **Discriminator:** does the parent's name match a PO prefix (`^\d{4,6}-\d`)?
  Yes → `order = parent` (normal). No → `order = grandparent` (lasercut).
  Related: `parts_control.stock_action = "On order (dependents NOT ORDER TASKS)"`
  (331 rows) means the order lives on an ancestor task — resolve it up the tree.
  Scale: 1,244 parts have a Laser Cut vendor (¼ of all parts); 383 resolve to a
  true grandparent order. Any part→order join that ignores this rule misattributes
  all of them — same defect class as Trap #2 (plausible, invisible).

---

## 8. Hard rules inherited from the estate

- SAP is the only Asana writer. Everything else files a request in
  `queues/REQUESTS` and the REQUESTS Processor drains it.
- `db_writer.py` is the sole write gateway for ops.db. No raw sqlite3 writes.
- `SAS Dropbox\` is read-only source data.
- PO number assignment stays a human act. The ledger issues the number; a person
  applies it.
- Never set "Needs Met" on an active order — use ADDRESSING.

---

## 9. If something breaks

| Symptom | First look |
|---|---|
| Impact engine produces 0 packs | `build_assembly_packs.project_code` null — the sync did not run, or `is_completed` is wrong |
| A pack scores with 0 parts | Pack association broken; check `build_assembly_pack_gid` is numeric |
| Register floods with one category | A harvester is missing `mode: "state"` |
| Write log growing by thousands | Change detection bypassed — check `_changed_columns` is still called in `_upsert_rows` |
| Ledger issues a number already used | Watermark not running; check `adopt_observed()` is called at the top of `reserve()` |
| A cron job silently does nothing | Check `last_status` in `AppData\Local\hermes\cron\jobs.json`; the improvement engine harvests these as `pipeline_failure` |

Rollback: pre-migration copy of ops.db at
`%TEMP%\claude\ops_backup_preimpact.db` (2026-09-09, 1.21 GB). The migration is
additive only, so restoring is rarely the right answer — prefer fixing forward.

---

_Last updated 2026-09-11. Baseline: PASS 18 · WARN 4 · FAIL 0._
