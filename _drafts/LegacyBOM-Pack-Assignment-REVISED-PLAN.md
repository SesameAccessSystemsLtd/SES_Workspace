# Revised Implementation Plan — Legacy BOM → Build Pack assignment (DB + Asana dual-write)

**Status:** Implementation plan (supersedes commit mechanics in `LegacyBOM-Pack-Assignment-Process.md`)
**Date:** 2026-08-11
**Reviewer:** Claude (Cowork), against live ops.db + existing scripts

---

## 0. What changed vs the original draft

The original proposal (`LegacyBOM-Pack-Assignment-Process.md`) is sound on ingest, the
BOM-item-vs-artifact rule (§3), HITL staging, and Dropbox read-only guardrails. Keep all of that.
This plan corrects three things and adds the DB write:

1. **Dual-write, not Asana-only.** Original §4 Step 4 sets only the Asana `Build Packs reference`
   field. Requirement is to update **both** `ops.db` and Asana. `parts_control.build_assembly_pack_gid`
   must be set via `db_writer` (the sole ops.db write gateway used by `parts_control_propagator.py`),
   never raw SQL, and `association_status` set to `linked`.
2. **Reuse the real write path.** Original §4 describes "sequential updates ~350ms pause". Use the
   existing, retry-hardened `skills/asana/scripts/asana_batch_updater.py` (Batch API, 10/req, 429 retry).
3. **Refresh the data snapshot.** See §1.

## 1. Live data snapshot (ops.db, 2026-08-11)

| association_status | all rows | open/active |
|---|---|---|
| linked (real GID)          | 103   | 85  |
| linked_by_ref (mis-keyed)  | 732   | 16  |
| unassociated               | 1,269 | 442 |
| NULL (never classified)    | 43    | 26  |

`build_assembly_packs`: 2,483 rows across two projects — Drawing Control (2,055), Build Schedule (428).

## 2. Architecture — mirror parts_control_propagator.py

- Entry point: `skills/purchasing/legacy_bom_pack_ingest.py` with `--scan` (dry) / `--commit`.
- ops.db writes: **`db_writer` only** (same gateway parts_control_propagator uses).
- Asana writes: `from asana_batch_updater import push_updates, get_asana_token`.
- Audit: append to `asana_write_log`; new `legacy_bom_ingest_log` table for candidate/decision trail.
- Post-check: `pack_association.py --list`.

## 3. Pipeline steps

**Step 1 — Ingest (read-only).** For each `*.xlsx` in the Legacy BOM folder: confirm file is fully
downloaded (size stabilises — on-demand sync caveat), parse `PARTS LIST`, apply §3 heuristics of the
source doc (part-number pattern AND vendor/cost-centre/stock present; exclude assembly groupings,
"Assembly/Welded by Sesame", `_MIR` subtrees). Emit `(sesame_pn, source_file, confidence)`.

**Step 2 — Resolve file → pack GID.** Look up filename against `build_assembly_packs`:
`pack_reference` exact → `pack_name` fuzzy → `1588.xxxx` prefix. If >1 pack matches, do NOT auto-pick;
flag for review (duplicate pack tasks are known — see asana_write_log dup notes).

**Step 3 — Cross-check parts_control (read-only).** Join candidate `sesame_pn` → `parts_control.sesame_pn`.
Match ⇒ stage; no match ⇒ hold; low confidence ⇒ review queue. Duplicate parts_control rows (8–16x
observed): target every matching `asana_task_gid`, or dedupe first.

**Step 4 — Stage for HITL (mandatory).** Write a REQUESTS `asana_write` file (format per
`queues/REQUESTS_COMPLETE/2026-08-10T16-00-00--legacybom-pack-assignment-edgecase.md`) + a review Excel
(part task GID, sesame_pn, old value, new pack GID, source file, confidence). No writes without approval.

**Step 5 — Dual commit (on approval).**
- ops.db via db_writer: `build_assembly_pack_gid = <pack task GID>`, `association_status = 'linked'`.
- Asana via asana_batch_updater: set `Build Packs reference` field to the **pack task GID** (never text).
- Log each to `asana_write_log`.

**Step 6 — Post-check.** `pack_association.py --list`; confirm open `unassociated` drops and targets
are now `linked` by GID.

## 4. Bonus — migrate the 732 mis-keyed linked_by_ref rows

These already point at a valid pack via `pack_reference` string under `build_assembly_pack_gid`.
Same resolver rewrites them to real `asana_task_gid` (DB + Asana), retiring the deliberately-unfixed
mis-keying documented in `pack_association.py`. Runs independently of the Dropbox ingest.

## 5. Guardrails (unchanged from source doc)

Dropbox read-only; never auto-push every row; no Asana write without HITL; GIDs are source of truth;
check file fully synced before parsing.

## 6. Open decisions for Giles

1. Nightly ingest vs watch-folder on change?
2. `... - Legacy.xlsx` → map to live pack (strip ` - Legacy`) or only to `_revN`/current?
3. Legacy part not yet in parts_control → silent hold or "missing part" alert?
4. **NEW:** pack_reference resolves to multiple pack tasks → auto-pick newest revision, or always review?
   (Recommend: always review.)

## 7. Scope / effort

`legacy_bom_pack_ingest.py` (parse + resolve + stage) + dual-commit step reusing db_writer and
asana_batch_updater + one migration pass for the 732 rows. Effort: **L**. No changes to the BOM
pipeline or sync scripts. Delegate build to Forge (dev_agent) per AGENTS.md.
