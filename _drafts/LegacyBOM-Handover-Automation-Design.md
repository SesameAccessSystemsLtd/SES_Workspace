# Design — Auto-processing of "Handover Legacy BOM" Asana tasks

**Status:** Automation design (investigation + chosen approach)
**Date:** 2026-08-11
**Author:** Claude (Cowork), grounded in live Asana + existing pipeline

---

## 1. How the handover reaches Giles today (verified)

Karol (or Giles) creates a task in **1.4 Purchasing Dept** (`850066254549317`), assigns it to
**Giles** (`1142108324926610`), titled **`Handover Legacy BOM - <project>`**, with a **Dropbox
shared-folder URL** pasted in the task body.

Verified example — `Handover Legacy BOM - 1581 Admiralty House` (task `1213862345336388`):
- Body contains `https://www.dropbox.com/scl/fo/4cs97jjkjsr3jx8m3g9tq/...?rlkey=...&dl=0` (a **shared
  folder** link, `scl/fo/` = folder).
- Also lives in the project-specific manufacturing project ("1580 - Admiralty House ... Manufacturing").
- Custom field `PROJECT NAME` = "580 - Admiralty House". This is **not an error**: the accounts
  department intentionally drops the leading digit (1581 → 580) because the full number is unhelpful
  to them. They are not actively using the field right now, so it is a candidate for future review;
  until then, **trust the title's project token** (e.g. 1581), not the `PROJECT NAME` field.

Volume/pattern (Asana search): ~15 historical `Handover Legacy BOM - <project>` tasks, almost all in
Purchasing Dept, assigned to Giles. Title format: `Handover Legacy BOM - <build#> - <name>`.
Creator is usually Giles (re-created) and sometimes Karol — so **do not filter on creator**.

## 2. Scope — this is BUILD-PACK ALLOCATION, not BOM import

**Explicit boundary (confirmed by Giles):** the Handover automation must **allocate the correct build
pack to each part**, not import the BOM. By the time it runs, the BOM has already been imported (MRP
pipeline, run to ~Stage 4) and the parts already exist in `parts_control`. This automation is the
**pack-allocation** step — nothing to do with re-running the import.

Why the two are easy to confuse — verified in code:
- The MRP import pipeline's **Stage 1** (`BOM_Consolidator_SLe_v4.py → build_lookup_map()`) already
  scans the legacy BOM folder and maps `part_number → pack NAME` using the **"Drawing Pack" identifier
  convention** (Column L / Cost Centre = "Drawing Pack"; Column D = pack name; all following rows with
  a non-empty Column L belong to that pack, until the next identifier row).
- But it associates parts to a pack **name (text)** — which is exactly the free-text / `linked_by_ref`
  weakness this whole effort exists to fix (see `pack_association.py` and the REVISED-PLAN).

**So the allocation step's job** is: read the same legacy BOMs, resolve each part's pack **name → the
verified pack task GID** (via `build_assembly_packs`), apply the BOM-item-vs-artifact exclusions, and
**dual-write** the GID to `parts_control.build_assembly_pack_gid` (via db_writer) **and** the Asana
`Build Packs reference` field — with HITL before any Asana write. This is
`legacy_bom_pack_ingest.py` from `LegacyBOM-Pack-Assignment-REVISED-PLAN.md`, **not**
`run_mrp_pipeline.py`.

**Reuse, don't reinvent:** the ingest should call the existing `build_lookup_map()` from
`BOM_Consolidator_SLe_v4.py` for the parse (proven "Drawing Pack" logic), then add the
name→GID resolution + dual-write + verify on top. This supersedes the column layout sketched in §3 of
the original `LegacyBOM-Pack-Assignment-Process.md` draft.

**So automation is wiring, not a new engine:** detect the trigger, resolve the Dropbox link, run the
allocation ingest in scan mode, stage for HITL.

## 3. Dropbox URL → local folder — RESOLUTION PROVEN

The allocation ingest needs a **local folder** of per-pack xlsx; the task gives a **Dropbox shared
URL** (`scl/fo/<opaque>`). The Dropbox connector resolves this cleanly. **Verified live** on the 1581
handover link:

- `get_shared_link_metadata(url)` → `link_type: folder`, `name: "Legacy BOM's"`,
  `path_lower: /3. projects/admiralty house/2.2. assembly drawings/legacy bom's`.
- Maps to the local synced folder:
  `C:\Users\GilesHugo\SAS Dropbox\3. Projects\Admiralty House\2.2. Assembly Drawings\Legacy BOM's\`.
- `list_folder` on that path returns the per-pack file `1580.1010.A.ScissorLift_Single.xlsx`
  (pack_reference `1580.1010.A` — the `build_assembly_packs` convention). Account = team **SIS - Dash**
  (shared@sesameaccess.com).
- Number spread is expected: title says 1581, accounts `PROJECT NAME` says 580, pack files say 1580.
  **Match packs on the file's pack_reference**, use the title only as a human label.

Dropbox stays **read-only** (existing hard rule): resolve/list/read/download only; never write/move/delete.

### Interim scope vs future — the API-token question
The autonomous **Hermes / asana_agent heartbeat** (local, gemini) has **no Dropbox API token**; the
Dropbox connector currently lives in the **Claude/Cowork** layer. Because the chosen behaviour is
**detect + notify only**, this is not a blocker:

- **Interim (ships now, no Hermes token):** Hermes is Asana-only — it detects the task, extracts the
  raw Dropbox URL + project token, logs, and notifies. It never calls Dropbox. The link **resolution +
  allocation ingest** run in a **Claude/Cowork session** (proven working today) — triggered by Giles
  or a scheduled Cowork task.
- **Future (full autonomy):** issue the Hermes agent its own **read-only** Dropbox token (scopes
  `sharing.read`, `files.metadata.read`, `files.content.read`) so the heartbeat resolves the link,
  stages the files, and runs the ingest end-to-end without a Cowork session in the loop.

## 4. Chosen behaviour — detect + notify only (no auto-run)

Per decision: the automation **detects and prepares, but runs no pipeline stages automatically.**
It hooks into the existing **asana_agent (SAP) heartbeat** — no new scheduled job.

### Flow — interim (Hermes = Asana-only; Cowork = Dropbox + allocation)
**Hermes heartbeat (Asana only, no Dropbox):**
1. **Detect.** Search Purchasing Dept (`850066254549317`) for incomplete tasks assigned to Giles whose
   title starts `Handover Legacy BOM`, excluding any task GID already recorded in ops.db.
2. **Parse.** Extract project token from the title and the raw Dropbox URL from `notes`/`html_notes`.
3. **Notify + record.** Telegram Giles + comment on the task ("new Legacy BOM handover ready for pack
   allocation" + the raw Dropbox URL). Mark the GID `prepared` in ops.db so it is not re-notified.

**Claude/Cowork session (has Dropbox connector), on Giles's go:**
4. **Resolve** the Dropbox URL → local legacy-BOM folder (§3, proven).
5. **Run allocation in scan mode** — `legacy_bom_pack_ingest.py --scan` (parse via `build_lookup_map`,
   resolve pack name→GID, cross-check `parts_control`, apply exclusions, stage candidate GID updates).
   **Not** the MRP import pipeline.
6. **HITL.** Present the candidate GID review (Excel/list). On approval, **dual-write** commit
   (ops.db via db_writer + Asana `Build Packs reference` = pack GID), then `pack_association.py --list`
   post-check. Comment the result on the Handover task and mark the GID `committed`.

### Future (full autonomy)
With a Hermes Dropbox token, steps 4–6 fold into the heartbeat, still gated by HITL before any write.

### State tracking (new ops.db table, via db_writer)
`legacy_bom_handover_log(task_gid PK, project_token, dropbox_url, resolved_folder, status,
prepared_at, processed_at, note)` — status ∈ prepared / running / committed / failed / skipped.

## 5. Guardrails (all preserved)
- No Asana writes without Giles: nothing runs automatically; Stage-4 HITL still gates any commit.
- Dropbox read-only.
- Trust title project token over the mismatched `PROJECT NAME` field.
- Idempotent: de-dupe on task GID so a task is prepared/notified once, not every heartbeat.
- No creator filter (Karol or Giles) — filter on project + assignee + title prefix only.

## 6. Build scope
1. `legacy_bom_handover_watcher.py` — detect + parse + resolve + prepare + notify + log. Read-only to
   Asana (comment only) and Dropbox. **Delegate to Forge (dev_agent)** per AGENTS.md.
2. Dropbox link-resolver helper (uses the Dropbox connector).
3. `legacy_bom_pack_ingest.py` — the **allocation** ingest (pack name→GID + dual-write + verify),
   reusing `build_lookup_map()` from `BOM_Consolidator_SLe_v4.py`. This is the actual processor the
   watcher prepares. Per `LegacyBOM-Pack-Assignment-REVISED-PLAN.md`.
4. `legacy_bom_handover_log` table + db_writer methods.
5. Heartbeat hook in asana_agent to call the watcher at the start of its run (same pattern as the
   REQUESTS check).

Effort: **M** for the watcher/resolver/logging + the allocation ingest; parsing and import are
already solved (build_lookup_map, MRP pipeline).

## 7. Open items for Giles
1. **Dropbox connector — DONE.** Connected; link resolution verified live (§3). No Hermes token
   needed for the interim scope.
2. **Future:** issue a read-only Dropbox API token to the Hermes agent (`sharing.read`,
   `files.metadata.read`, `files.content.read`) to fold resolution + allocation into the heartbeat.
3. Confirm the local Dropbox root is `C:\Users\GilesHugo\SAS Dropbox\` on the machine that runs the
   Cowork/allocation step (verified path shape matches: `3. Projects\<Project>\2.2. Assembly
   Drawings\Legacy BOM's\`).
4. Should the watcher also handle handover tasks that appear only in the per-project Manufacturing
   project (not Purchasing Dept)? Historically a few did. Recommend: match on title+assignee across
   both, but start with Purchasing Dept only.
