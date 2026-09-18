# EDGE CASE — BOM import / pack association: six silent-failure patterns

**Date:** 2026-09-04
**Category:** bom-pipeline / pack-association / data-integrity
**Reported by:** Giles (assessment of 1037 Langley Street legacy BOMs vs Asana pack assignment)
**Logged by:** Claude (Co-Pilot session)
**Evidence base:** 7 legacy BOM xlsx files, `ops.db` (`parts_control`, `build_assembly_packs`, `legacy_bom_handover_log`), `bom_rev_reconcile.py`, `05_reconcile_pack_association.py`

---

## Why this matters

A BOM handover ran against 1037 Langley Street on 2026-08-12, matched nothing,
wrote `status='no_candidates'`, and stopped. Nobody was told. Three weeks later
the parts were still unassigned and a 113-row "outstanding parts" list was
about to be sent to the build team — of which only 31 rows were actually open.

Every pattern below is a **silent** failure: the system did the wrong thing
without raising anything. These are the detection rules so it does not recur.

---

## LESSON 1 — `no_candidates` is a failure, not an outcome

**What happened**

`legacy_bom_handover_log` row for 1037:

```
project_token   : 1037 Langley Street
status          : no_candidates
note            : "3 packs (1016 button stations) resolved to Build Schedule
                   GIDs, but parts_control 1037 holds only 1014 main-assembly
                   parts (30 rows). Zero overlap -> 0 candidates, nothing
                   committed."
```

The matcher compared `1016.*` pack references against `parts_control` rows that
held only `1014.*` parts. Zero intersection. It logged and exited 0.

**Why it is wrong**

Zero overlap between packs and parts for a project that demonstrably has both is
not a legitimate "nothing to do". It is a near-certain sign the two sides were
keyed on different scopes. Exiting 0 made it invisible.

**Detection rule**

> If a handover resolves ≥1 pack AND finds ≥1 part for the same project token,
> but produces 0 candidate links → this is an **ERROR**, not `no_candidates`.
> Raise to HITL. Never exit 0.

**Trigger:** `status='no_candidates'` where `packs_resolved > 0 AND parts_found > 0`.

**Also:** any `legacy_bom_handover_log` row sitting in a non-terminal status
(`prepared`, `no_candidates`) for **> 7 days** must surface in the daily brief.
The 1037 row sat since 2026-08-12 unremarked.

---

## LESSON 2 — Revision suffix is being destroyed on import

**What happened**

`parts_control` stores the rev in the title but strips it from the key:

```
sesame_pn : 1037.1014.M.033
part_name : 1 off 1037. 1014.M.033.02 - Teethed_Plate Laser Cut
                              ^^^ rev captured here, absent from the key
```

Confirmed across the job: BOM says `M.032.02`, `M.034.01`, `M.039.02`,
`M.061.01`, `M.109.01`, `M17w.02`, `M.114.01`, `M.115.01`, `M.116.01`,
`N.010.01`. Asana/`parts_control` key on the un-suffixed form.

**Consequences**

1. Buyers order to a superseded drawing — the task says `M.061`, the current
   part is `M.061.01`.
2. Re-import creates a *second* task rather than matching the first, because
   `M.114` ≠ `M.114.01` under string equality. Both stay open.

**Detection rule**

> Part number identity = `base` + `rev`, stored in **separate fields**. Never
> concatenate them into the matching key. When BOM PN and Asana PN differ only
> by a trailing `.NN`, that is a **RENUMBER of the same part**, never a new part.

**Trigger:** import encounters `<pn>` where `<pn>.NN` or `<pn minus .NN>`
already exists in `parts_control` for the same job prefix.

**Note:** `bom_rev_reconcile.py::classify()` already implements this correctly
(`base = re.sub(r"\.\d{1,2}$", "", pn)` → `RENUMBER`). The *importer* does not.
Fix belongs on the import side.

---

## LESSON 3 — Removal signal depends on a column the design team leaves empty

**What happened**

`bom_rev_reconcile.py::load_new_bom()` documents Column M (Notes) as "the
AUTHORITATIVE change signal", parsing `Removed 13.07.2026 in rev2`.

Every Langley BOM Notes cell holds something else entirely:
`RAL 9017 matt`, `Send to machining`, `Sesame to provide welded component and bearing`.

**Zero removal markers. The authoritative signal was never supplied.**

The `REVISION NUMBER` column is likewise empty on all but one row across all
7 files — revision is encoded in the *filename* (`_rev2`, `_rev3`) instead.

**Detection rule**

> Never depend on a single optional supplier-populated column. If the Notes
> column is empty across **> 90%** of rows, the removal signal is ABSENT —
> emit a loud warning and fall back to set-difference detection
> (in-old-BOM AND not-in-new-BOM = candidate removal → HITL).
>
> If `REVISION NUMBER` is empty on > 90% of rows, do not treat the file as
> revision-controlled. Warn.

**Trigger:** `notes_populated_ratio < 0.10` or `rev_populated_ratio < 0.10` at load.

The existing code prints a warning only when the column is *missing entirely* —
not when it is present but empty. That is the gap.

---

## LESSON 4 — Detection without a write path is not a control

**What happened**

`bom_rev_reconcile.py` correctly classifies `REMOVED`, `RENUMBER`, `ADD`,
`UPDATE_QTY`. Then:

```python
if args.commit:
    sys.exit("--commit is not implemented: writes flow through the BOM importer
              + REQUESTS queue -> SAP. Engine is read-only.")
```

Analysis was complete and correct. Nothing could ever act on it. Meanwhile two
parts (`1014.M.062`, `1014.N.010`) were marked `REMOVED` **by hand** — proving
the convention works and that the manual route was the only route.

**Detection rule**

> A detection stage with no commit path is an unfinished control, not a safe
> default. Either wire the write (via REQUESTS → SAP) or register the output as
> a standing HITL queue item that ages and escalates. Silent report-only output
> that nobody drains is equivalent to no control at all.

**Trigger:** any `proposed_changes_*.xlsx` in `bom_pipeline/data/bom_rev/`
older than 7 days with no corresponding REQUESTS file.

---

## LESSON 5 — Two-stage routings are not duplicates

**What happened**

Initial analysis (BOM files alone) flagged 9 "duplicate" part tasks. The DB
showed 8 of them were legitimate process routings:

```
1037.9990.A.002  1217816716091839  done=1  vendor=Laser Cut
1037.9990.A.002  1217816856490516  done=0  vendor=Marine Lacquer
```

Make it, then finish it. Two tasks, one part, both correct.

Only `1037.1014.M.064` was a true duplicate — identical vendor (`Sesame`),
identical title, two GIDs, both complete.

**Detection rule**

> Duplicate test = same `sesame_pn` **AND** same `vendor_name_normalised`.
> Same PN with *different* vendors is a routing sequence — never flag it, never
> merge it. Any dedupe logic keyed on part number alone will destroy finishing
> operations.

**Trigger:** dedupe proposals must be rejected automatically when
`vendor_name_normalised` differs.

---

## LESSON 6 — Counting rows is not counting work

**What happened**

`parts_control` holds 113 rows for 1037. A report described all 113 as
"outstanding". Actual state:

| | rows |
|---|---|
| Total | 113 |
| Open (`is_completed=0`) | **31** |
| Complete | 82 |

A 113-part chase list would have sent the build team after 82 finished parts.

**Detection rule**

> Any outbound "outstanding parts" artefact MUST filter `is_completed=0` and
> MUST state both numbers ("31 open of 113 total"). A bare row count is never a
> workload figure.

**Trigger:** report generation where `is_completed` is not in the WHERE clause.

---

## Structural defects found (fix separately — not detection rules)

| # | Defect | Evidence | Impact |
|---|---|---|---|
| S1 | `parts_control.build_assembly_pack_gid` stores pack **name strings**, not GIDs | `'1037.1014.M.Gate+Support.'` in a column named `_gid` | Joins to `build_assembly_packs.asana_task_gid` silently return nothing |
| S2 | BOM template has no `PACK` column | All 7 Langley files | Pack association can only ever be inferred, never read. Root cause of the whole class of failure |
| S3 | Pack `1037.9992.A.Wheelstop Bracket Front.` holds parts numbered `9992.B.001/.002` | `build_assembly_packs` gid 1213566425248673 vs BOM | Prefix matching mis-routes. Verified 0 dependents → safe to rename |
| S4 | Shared parts claim one pack only | `1016.3.001` in BS.2/BS.3/X → filed under BS.2 | BS.3 and X read as near-complete when they are not |
| S5 | No rule for which BOM level becomes a task | `M14w` has a task; `M18w`, `N24w`, `N2w`, `N6w` do not | Welded assemblies inconsistently tracked |
| S6 | `05_reconcile_pack_association.py --commit` writes `build_assembly_pack_gid` but never updates `association_status` / `association_checked_at` | **176 rows** table-wide held a pack while still reading `unassociated`/null | Status column contradicts the data. Any dashboard filtering on `association_status` under-reports linked parts indefinitely |
| S7 | `build_assembly_pack_gid` held **two incompatible value formats** | Measured 2026-09-04: **1239 rows held a pack NAME**, 44 held a GID. Three writers disagreed — `02_map_parts_to_packs` and `05_reconcile` wrote GIDs, `execute_requests.apply_pack_assign` wrote names | See S8 — this silently broke drawing-pipeline stage 4 |
| S8 | **Stage 4 of the drawing pipeline saw almost no parts.** `03_write_to_asana.py` INNER JOINs `pc.build_assembly_pack_gid = bap.asana_task_gid`, so every NAME row was dropped | `job 1037: 0 of 102` · `job 1575: 0 of 373` · `job 1588: 44 of 168` — **44 of 1283 table-wide** | Silent. No error, no empty-result guard: the JOIN simply returned few rows and the stage reported success |

### S6 / S7 / S8 — RESOLVED 2026-09-04

All three were found by *doing* the pack backfill, not by reading the code.
A first measurement of S7 under-reported it 28x by testing `glob '[0-9]*'` —
pack names start with a digit too (`1037.9992...`), so a leading-digit test
matches them. **A GID test must be `value.isdigit()`, all digits.**

Fixes applied:

| File | Change |
|---|---|
| `05_reconcile_pack_association.py` | `--commit` now writes `association_status='linked'` + `association_checked_at` alongside the pack GID |
| `execute_requests.py` (`apply_pack_assign`) | writes `pack_gid`, **not** `pack_name`; also stamps `association_checked_at` |
| `build_pack_health_check.py` | resolves GID → `pack_reference` at read time, so it works on either format; warns on a GID with no pack row |
| `06_normalise_pack_gid.py` | **new** one-off migration, dry-run by default, rollback ledger |

Migration result: **1234 rows name → GID, 0 ambiguous, 5 orphans skipped,
176 statuses repaired.** Stage 4 afterwards: 1037 `102/102`, 1575 `373/373`,
1588 `168/168`.

**GID is canonical for this column, never the pack name.** A name is mutable —
renaming a pack orphans every row keyed on the old string. This matters
immediately: a rename of `1037.9992.A.Wheelstop Bracket Front.` was pending at
the time of the migration and would have orphaned rows had names still been in
use.

The 5 orphans are one data-entry defect on job 1591 — two pack names typed into
a single cell:
`'1591.7003.TraversingLowProfileShort_MOD1., 1594.1010.L.ScissorLift_Single_LowProfile 1600.'`

---

## LESSON 7 — "unassigned" is three different problems

The BOM tracker showed one bucket, "Parts without assembly pack", with a single
`▶ Stage drawing` action. That action can only help parts where a pack already
exists. It was being offered for jobs where **no pack exists at all** — job 1373
(29 of 29 parts) and job 1555 (37 of 37) could never have been fixed by pressing it.

**Detection rule**

> Never present an unresolved part as one state. Classify by *why*:
>
> - `resolvable` — a pack exists for the code; automation can link it
> - `ambiguous` — 2+ packs claim the code; needs a human ruling
> - `blocked` — no pack exists for the code; **no re-run can fix it**, the BOM
>   never said which pack the part belongs to
> - `short_pn` — PN too short to carry a pack code (NBSS-style)
>
> Offer an action ONLY for `resolvable`. A button implying a fix exists, where
> none does, is worse than no button.

**Trigger:** any unresolved-parts view that renders a single count or a single action.

Also fixed: the tracker suppressed jobs with ≤3 no-pack parts, so a job with 2
permanently-blocked parts never appeared at all. Blocked and ambiguous now
bypass that threshold.

Table-wide as at 2026-09-04: **155 blocked, 17 ambiguous** across 11 jobs.

---

## Ingest summary for Hermes

Six triggers to implement, in priority order:

1. `no_candidates` + packs>0 + parts>0 → **ERROR + HITL** (Lesson 1)
2. Handover log row non-terminal > 7 days → **daily brief** (Lesson 1)
3. `notes_populated_ratio < 0.10` → **warn + fallback to set-difference** (Lesson 3)
4. PN differs only by trailing `.NN` → **RENUMBER, never new part** (Lesson 2)
5. Dedupe proposal with differing vendor → **auto-reject** (Lesson 5)
6. Outstanding-parts artefact without `is_completed` filter → **block** (Lesson 6)
7. Unresolved-parts view rendering one bucket / one action → **classify resolvable vs ambiguous vs blocked** (Lesson 7)
8. `build_assembly_pack_gid` value failing `.isdigit()` → **non-canonical format written, reject at source** (S7/S8)
9. A pipeline stage whose main JOIN returns < 20% of candidate rows → **warn; do not report success** (S8)
