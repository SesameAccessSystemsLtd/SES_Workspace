---
name: legacy_bom_pack_allocation
description: "Allocate the correct build-pack GID to already-imported parts from a design-team Legacy BOM folder, and dual-write it to ops.db + Asana. Use when a 'Handover Legacy BOM' task arrives, or when Giles asks to allocate/assign build packs, fix pack references, or migrate free-text pack links to verified GIDs. NOT for importing a BOM (that is bom_pipeline)."
---

# Skill: legacy_bom_pack_allocation

Maps each already-imported `parts_control` part to its **verified build-pack task GID**,
replacing fragile free-text pack-name links. **This is pack allocation, NOT BOM import.**
By the time it runs, parts already exist in `parts_control` (imported by `bom_pipeline`).

## When to use
- A task titled **`Handover Legacy BOM - <project>`** appears in **1.4 Purchasing Dept**
  (`850066254549317`), assigned to Giles, with a Dropbox folder link in the body.
- Giles asks to "allocate build packs", "assign pack GIDs", "fix Build Packs reference",
  or "migrate the text pack links to GIDs".

## The two moving parts

### 1. Watcher (interim — Asana-only, runs on the heartbeat)
`legacy_bom_handover_watcher.py` detects new handover tasks and notifies Giles. It does
**not** resolve Dropbox or run the allocation.
```
python legacy_bom_handover_watcher.py --dry-run    # detect + print, no writes/notify
python legacy_bom_handover_watcher.py              # detect + log + Telegram + task comment
python legacy_bom_handover_watcher.py --no-comment # Telegram only
```
Dedupes on `task_gid` via the `legacy_bom_handover_log` table (status: prepared → committed).

### 2. Allocation ingest (runs in a Cowork session, on Giles's go)
Because the interim heartbeat has no Dropbox token, resolution + allocation run where the
Dropbox connector is available (Claude/Cowork):

**Step A — resolve the Dropbox link to a local folder** (Cowork, Dropbox connector):
`get_shared_link_metadata(url)` → `path_lower` → local `C:\Users\GilesHugo\SAS Dropbox\<path>`.

**Step B — scan (read-only):**
```
python legacy_bom_pack_ingest.py --parts-folder "<local Legacy BOM's folder>" --scan \
    --stage-file <scan.json>
```
Prints candidate updates and writes `<scan.json>`. Nothing is written to Asana or the DB.

**Step C — review** `<scan.json>` (HITL). Confirm the pack GID and the migrate/new actions.

**Step D — commit (dual-write, only after review):**
```
python legacy_bom_pack_ingest.py --parts-folder "<...>" --commit --stage-file <scan.json>
```
Sets `parts_control.build_assembly_pack_gid` + `association_status='linked'` (via `db_writer`)
AND the Asana `Build Packs reference` field (GID `1211660742155293`) to the pack task GID
(via `asana_batch_updater`). Every write is logged to `asana_write_log`.

**Step E — post-check:** `python ../purchasing/pack_association.py --list` (open `unassociated`
count should drop; targets flip to `linked`).

## Rules (non-negotiable)
- **Filename = pack.** Every orderable row in a per-pack file belongs to that one pack. Do NOT
  use `build_lookup_map` (that is for the consolidated import file).
- **Pack target = Build Schedule project pack.** Ignore the `.DRAWING` / Drawing Control variant.
  If 0 or ambiguous matches → flagged, file skipped. Never guess.
- **Exclude artifacts:** `_MIR` subtrees, "Assembly/Welded by Sesame", grouping rows, and rows
  with no vendor/cost/stock (ISO/DIN fasteners are intentionally excluded — Giles, 2026-08-11).
- **No writes without review.** `--scan` is read-only; `--commit` applies ONLY the reviewed
  stage file. Dropbox is read-only.
- Parts in the BOM not present in `parts_control` are **held**, never created.

## Auth
`ASANA_PERSONAL_ACCESS_TOKEN` from `.env` / `ASANAconfig.json`. DB path from `PATH_CONFIG.yaml`
(`db.ops`) or `OPENCLAW_OPS_DB_PATH`.

## Files
- `legacy_bom_pack_ingest.py` — allocation ingest (scan/commit, dual-write).
- `legacy_bom_handover_watcher.py` — Asana-only detect + notify + log.
- `EDGE_CASES.md` — living log; append every anomaly.

## Edge cases
See `EDGE_CASES.md`. Known: pack ambiguity (Build Schedule vs Drawing Control), `_MIR`,
in-house welds, duplicate parts_control rows, Dropbox on-demand placeholders, partial coverage
(one pack file updates ~15–25 tasks, not hundreds), number spread (title 1581 / field 580 / pack 1580).
