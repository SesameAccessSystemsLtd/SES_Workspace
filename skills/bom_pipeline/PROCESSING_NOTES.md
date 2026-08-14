# BOM Pipeline — Processing Notes

Living document for bots running this pipeline. Captures learnings from real runs that are not obvious from SKILL.md or CLAUDE.md. Read those first, then this.

---

## How to process a BOM import (quick reference)

1. Collect from Giles: project name, `--parts-folder` path, Dropbox URL, Asana control task URL.
2. Run Stages 1–4 (`--stop-after 4`). Report the Stage 4 Excel path. Stop.
3. Wait for explicit go-ahead from Giles before `--commit`. No exceptions.
4. Run Stage 5 with `--stage4-file <path> --commit`.
5. Report the created task GIDs. Done.

Do NOT offer or attempt to set `ALL BOMS - {project}` as a subtask of the purchasing control task. This breaks the Asana hierarchy. Giles manages that link manually if he wants it.

---

## Golden Index — current state (as of May 2026)

The Golden Index (Rev4) is being rebuilt and is not yet reliable. Unmatched parts at Stage 2 are **expected and not an error**. Do not flag unmatched parts as a problem or suggest Golden Index updates unless Giles specifically asks. Once the index is properly populated, matched PICK parts will appear naturally.

---

## Long Lead BOM specifics

- Source is a **single combined Excel** (not a folder of vendor files) — place it in its own folder and pass that folder as `--parts-folder`.
- Do **not** pass `--legacy-folder` — drawing pack lookup does not apply.
- All parts will currently route to ORDER because the Golden Index does not yet contain these bespoke project items. This is correct.
- Part numbers follow `{project}.{section}.{subsection}` format — e.g. `1582.1008.D1.MachineRoomCabinet`.

---

## Standard BOM specifics

- Source is a **folder of vendor xlsx files** — pass the folder directly.
- Stage 1 consolidates all xlsx files found in the folder.
- `Cost Centre` column (col L) must be populated — rows without it are silently dropped.
- Steel Profile BOMs have no Cost Centre column at all — add it manually before running.

---

## Paths that must exist before running

| Path | What it is |
|---|---|
| `--parts-folder` | Folder containing BOM xlsx(s) — may be in SAS Dropbox (read-only) |
| `3.Drawing_BOM_Extractor/Drawing_Legacy_BOMS/` | Legacy BOM folder (default) — must exist even if empty; dummy.xlsx placeholder is sufficient |
| `2.Stock Controls/Merged_Golden_Index_Rev4.csv` | Golden Index — must exist |

The pipeline validates all three at startup and exits if any are missing.

---

## Russell Square 50-51 (Project 1582) — import log

| BOM | Run date | Status |
|---|---|---|
| Long Leading Time BOM | 2026-05-01 | Done — 4 parts, all ORDER, GIDs created |
| TBC | — | Pending |
| TBC | — | Pending |

Giles confirmed there are at least 2 more BOMs to import for this project. Update this table as each is completed.

---

## Part number format reminder

- Full number (project-specific): `{4-digit-project}.{cad_pn}` — e.g. `1582.1008.D1.MachineRoomCabinet`
- Golden Index `Stock_Number`: prefix stripped — e.g. `1008.D1.MachineRoomCabinet`
- Asana `Sesame Part No` custom field: **always the full number**, never stripped
- Task names in Asana contain a space after the first dot: `1582. 1008.D1...` — normalise when matching

---

## Known quirks

- Stage 1 uses `importlib` (runs in-process) so its stdout appears out of order relative to subprocess stages in terminal output. The pipeline is still running correctly.
- Stage 3 (stock check) makes Asana API calls to query KANBAN. If all parts are unmatched at Stage 2, Stage 3 completes quickly with no API calls needed.
- The `GI_revision_pending_*.xlsx` file written at Stage 4.5 is a log of parts that have a `Mapped KAN` value — review it before running `update_golden_index.py` to apply mappings.
- Rev2 Golden Index (`Merged_Golden_Index_Rev2.csv`) is stale — always use Rev4.

---

*Updated by Claude Code after each pipeline run. Last updated: 2026-05-01*
