# PLAN: Legacy BOM → Build Pack Allocation pipeline

**Date:** 2026-08-11
**Status:** BUILT & VALIDATED (2026-08-11) — approved by Giles; OpenClaw/Forge retired, built directly
**Owner:** Giles Hugo
**Build agent:** Forge (dev_agent)
**Design sources:** `_drafts/LegacyBOM-Handover-Automation-Design.md`, `_drafts/LegacyBOM-Pack-Assignment-REVISED-PLAN.md`

## What & Why
When Karol (or Giles) hands over a project's design-team **Legacy BOM's** (one `.xlsx` per build pack)
via an Asana task titled `Handover Legacy BOM - <project>` in **1.4 Purchasing Dept**, the parts have
usually already been imported (MRP pipeline) but are linked to their build pack only by **free-text
pack name**. This pipeline **allocates the verified build-pack GID to each already-imported part** and
**dual-writes** it to `ops.db` and Asana — replacing fragile text links with real GIDs. It is a
**pack-allocation** process, NOT a BOM import; it never re-runs the MRP pipeline.

Scope is the **interim** design (chosen 2026-08-11): the autonomous Hermes heartbeat is **Asana-only**
(detect + notify, no Dropbox token); Dropbox link resolution + the allocation run happen in a
Claude/Cowork session (which holds the Dropbox connector — resolution already proven on the 1581 link).

**Dry-run finding (2026-08-11, Admiralty ScissorLift pack):** the pipeline's dominant real action is
**migrating `linked_by_ref` text links → verified pack GID** (15 of 16 tasks in that pack), with new
allocations of `unassociated` parts secondary (1 of 16). So the "732 linked_by_ref migration" is NOT
a separate future task — it happens naturally, pack-by-pack, as handovers are processed. E10 in the
original REVISED-PLAN is reframed accordingly: no bulk migration script needed; the per-handover
ingest does it.

## Scope

### Files to create
- `skills/legacy_bom_pack_allocation/legacy_bom_pack_ingest.py` — the core allocation ingest.
  `--parts-folder <local legacy BOM folder>` + `--scan` (read-only) / `--commit`.
  Behaviour: **filename = pack**; parse each per-pack `.xlsx` `PARTS LIST` sheet directly and assign
  every orderable row to that file's pack. **Do NOT use `build_lookup_map()`** — dry-run (2026-08-11)
  confirmed per-pack handover files have no "Drawing Pack" separator rows; that function is for the
  consolidated import file only (E11). Apply the BOM-item-vs-artifact exclusions
  (E2–E4, E11–E13 in EDGE_CASES.md); resolve the file's pack via `build_assembly_packs.pack_reference`
  and **prefer the Build Schedule project pack**, ignoring the `.DRAWING`/Drawing Control variant
  (E5, resolved live); flag if 0 or ambiguous matches;
  cross-check candidate `sesame_pn` against `parts_control.sesame_pn` (hold if absent, E9; target all
  duplicate rows, E6); stage candidates `(part_task_gid, sesame_pn, old_value, new_pack_gid,
  source_file, confidence)`. On `--commit`: dual-write via `db_writer`
  (`parts_control.build_assembly_pack_gid` + `association_status='linked'`) AND
  `asana_batch_updater.push_updates` (Asana `Build Packs reference` = pack GID); log to `asana_write_log`.
- `skills/legacy_bom_pack_allocation/legacy_bom_handover_watcher.py` — Hermes-side, **Asana-only**.
  Search Purchasing Dept (`850066254549317`) for incomplete tasks assigned to Giles
  (`1142108324926610`) whose title starts `Handover Legacy BOM`, skipping GIDs already in
  `legacy_bom_handover_log`. For each: extract project token (title) + Dropbox URL (notes); write a
  `legacy_bom_handover_log` row (status `prepared`); emit a notification payload (Telegram + task
  comment text). `--dry-run` prints without notifying/writing. Runs NO ingest, touches NO Dropbox.
- `skills/legacy_bom_pack_allocation/SKILL.md` — skill doc: trigger phrases, the two-step interim
  runbook (Cowork resolves the Dropbox link → passes `--parts-folder` to the ingest `--scan` →
  review → `--commit`), command templates, and the guardrails.

### Files to modify
- `db/ops.db` — add table `legacy_bom_handover_log(task_gid TEXT PRIMARY KEY, project_token TEXT,
  dropbox_url TEXT, resolved_folder TEXT, status TEXT, prepared_at TEXT, processed_at TEXT, note TEXT)`.
  Create idempotently via an `ensure_schema()` in the watcher (pattern copied from `pack_association.py`).
  Status ∈ prepared / running / committed / failed / skipped.
- `SKILLS_REGISTRY.md` — add an entry for `legacy_bom_pack_allocation`.
- `skills/purchasing/db_writer.py` — ADDED `update_parts_pack_allocation(task_gid, pack_gid, …)`,
  a targeted UPDATE (not upsert). Required because `parts_control.part_name` is NOT NULL, so a
  partial-column upsert fails the INSERT half before ON CONFLICT resolves (caught in validation).
- asana_agent heartbeat definition (in the openclaw agents tree,
  `agents/asana_agent/HEARTBEAT.md`) — add a start-of-run call to `legacy_bom_handover_watcher.py`,
  mirroring the existing REQUESTS/ check. (If the heartbeat file is not reachable from the build
  environment, STOP and update this plan — do not guess its location.)

### Files left untouched
- `skills/bom_pipeline/run_mrp_pipeline.py` and all MRP stages — import is out of scope; only *read*
  `build_lookup_map()` from `BOM_Consolidator_SLe_v4.py`, do not modify it.
- `skills/purchasing/pack_association.py` — used unchanged for the post-check.
- `skills/asana/scripts/asana_batch_updater.py` — imported unchanged.
- The 732 `linked_by_ref` migration — separate future task (E10).

## Dependencies
- `.venv` python; `requests`, `pandas`, `openpyxl` (already used by bom_pipeline).
- `db_writer` (sole ops.db write gateway), `asana_batch_updater` (Asana Batch API + 429 retry),
  `build_lookup_map` (parse).
- `ASANA_PERSONAL_ACCESS_TOKEN` from env/`ASANAconfig.json` (existing).
- Dropbox link resolution is performed by the Claude/Cowork session via the Dropbox connector
  (`get_shared_link_metadata` → `path_lower` → local `C:\Users\GilesHugo\SAS Dropbox\...`), NOT by
  these scripts. No Dropbox token is added to Hermes in this build.

## Success criteria
1. `python legacy_bom_pack_ingest.py --parts-folder "<Admiralty Legacy BOM's>" --scan` exits 0 and
   prints a candidate `(sesame_pn → pack_gid, source_file, confidence)` list, writing nothing.
2. Scan excludes `_MIR` subtrees, "Assembly/Welded by Sesame", and no-vendor grouping rows; low-
   confidence rows (part number but no vendor/cost-centre/stock) are listed as `review`, not dropped.
3. Every committed candidate maps a `pack_reference` → a real `build_assembly_packs.asana_task_gid`;
   any `pack_reference` with 0 or >1 pack matches is listed as `unresolved`/`ambiguous`, never guessed.
4. `--commit` on an approved candidate set: (a) `parts_control.build_assembly_pack_gid` is set and
   `association_status` becomes `linked` for the target part(s), including duplicate rows; (b) the
   Asana `Build Packs reference` field is set to the pack GID; (c) rows appear in `asana_write_log`.
5. `python skills/purchasing/pack_association.py --list` after commit shows the open `unassociated`
   count reduced by the number committed.
6. `python legacy_bom_handover_watcher.py --dry-run` detects the seeded `Handover Legacy BOM` task,
   would-write one `legacy_bom_handover_log` row, prints a notification payload, runs no ingest.
7. `SKILLS_REGISTRY.md` contains the new skill entry.
8. Nothing writes to Asana or ops.db without `--commit` (scan and watcher-dry-run are read-only).

## Risks & rollback
- **Risk:** wrong pack GID allocated to a part. **Rollback:** `parts_control.build_assembly_pack_gid`
  is restorable from `db/parts_control_backup_*.json`; Asana field revert uses the `old_value` recorded
  in `asana_write_log`. Watcher writes are just log rows (safe).
- **Risk:** `pack_reference` ambiguity (E5) causes mis-allocation. **Mitigation:** ambiguous → review,
  never auto-pick; HITL gate before any commit.
- **Risk:** partial Dropbox file parsed (E7). **Mitigation:** size-stability check before parse.
- **Risk:** duplicate parts_control rows under-updated (E6). **Mitigation:** update all matching GIDs.

## Out of scope
- Any BOM import / MRP pipeline change.
- Giving Hermes a Dropbox API token or autonomous link resolution (future phase).
- Migrating the 732 `linked_by_ref` rows (future, E10).
- Changing the accounts `PROJECT NAME` field convention (E1 — parked).
- Allocating packs to ISO/DIN fasteners (E13 — Giles: ignore; leave excluded).
- Auto-committing without HITL.

## Hand-off to Forge (after Giles approves)
Spawn per `skills/dev/SKILL.md` template:
```
sessions_spawn:
  instructions: |
    You are Forge, the OpenClaw dev agent. Before doing anything else, read your soul files
    (agents/dev_agent/IDENTITY.md, SOUL.md, TOOLS.md, AGENTS.md) and skills/dev/SKILL.md.
    Your task is defined in the plan file:
    SES_Workspace/plans/PLAN-2026-08-11-legacy-bom-pack-allocation.md
    The plan is approved. Execute Gate 2: BUILD then Gate 3: VALIDATE. Report each file as you
    complete it. Append any anomalies to skills/legacy_bom_pack_allocation/EDGE_CASES.md.
    Write your outcome to agents/dev_agent/LOG.md and report a summary.
  model: google/gemini-2.5-pro
  maxTurns: 30
```
