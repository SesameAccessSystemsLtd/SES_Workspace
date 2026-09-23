# drawing_pipeline — Skill Reference

## Purpose
For a given engineering project, downloads assembly drawing PDFs from Asana,
extracts the part BOM from each drawing, and populates the `Build Packs reference`
custom field on Parts Control tasks in Asana — linking each part to the specific
assembly pack drawing it appears on.

## When to Run
Run once per project when:
- A new job has been set up in Drawing Control and Build Schedule
- Parts Control tasks exist in ops.db for the job
- All drawing PDFs are attached to the Drawing Control tasks in Asana

## Job Prefix

> **AGENT RULE — Never guess the job prefix.** The job prefix table below only contains confirmed past runs. For any new job, Giles must provide the prefix explicitly, OR you must read it from the Drawing Control task names in Asana (look for tasks named `NNNN.*` where NNNN is 4 digits). Do not infer the prefix from the project name or number.

Every Sesame job has a numeric prefix that prefixes all part numbers.
| Project | Job Prefix |
|---|---|
| Jenners, Edinburgh | `1575` |
| (next job) | TBD — ask Giles or read from Drawing Control task names |

The prefix appears as the first component of every part number (e.g. `1575.1005.B.001`).

## Entry Point (SAP context — Asana write authorised)
```
C:\Users\GilesHugo\.openclaw\venv\Scripts\python.exe ^
  workspace\skills\drawing_pipeline\scripts\run_drawing_pipeline.py ^
  --job-prefix <JOB_PREFIX> [--dry-run] [--stop-after N]
```

## Pipeline Stages
| # | Script | What it does |
|---|---|---|
| 1 | `01_populate_assembly_packs.py` | Reads Drawing Control project → finds all tasks matching `{job_prefix}.*` → resolves Build Schedule subtask GID → upserts into `ops.db build_assembly_packs` |
| 2 | `02_extract_drawing_boms.py` | For each assembly pack: fetches parent drawing task → downloads latest PDF attachment → runs pdfplumber → writes `data/{job}_drawing_boms.csv` |
| 3 | `02_map_parts_to_packs.py` | Reads drawing_boms.csv → applies 9-rule PN normalizer → matches to `parts_control.sesame_pn` → writes `build_assembly_pack_gid` to ops.db |
| 4 | `03_write_to_asana.py` | For each matched PC task: PUTs Build Packs reference GID, appends pack note to task notes, prepends `*` to task name if multi-pack. **SAP only.** |

## 🔴 The "Assembly Pack" description line — ONE shared contract (all mapping paths)

The pack link is written to TWO places, and the description line is the human/report-readable
half. **Every** path that maps a part to a pack MUST write the identical line, or downstream
reports that read the description see a **false link** (field set, description silent).

| Path | Writes the line? |
|---|---|
| Pipeline (`drawing_pipeline/scripts/03_write_to_asana.py`) | ✅ |
| `apply_pack_assign` (`purchasing/execute_requests.py`) | ✅ |
| Legacy BOM ingress (`legacy_bom_pack_allocation/legacy_bom_pack_ingest.py`) | ✅ |
| **Manual mapping in the Asana UI** (field/drag) | ❌ — **THIS IS THE GAP** |

**Canonical format (copy verbatim — do not vary):**
```
{qty} off in Assembly Pack {pack_reference}
```
e.g. `2 off in Assembly Pack 1037.1014.M.Gate+Support.`
- Uses the pack **reference/name** (`pack_reference`), NOT the GID.
- **Idempotency:** skip if `f"Assembly Pack {pack_reference}"` is already a substring of `notes`.
- **Multi-pack:** join lines with ` | `; prepend `*` to the task name when 2+ packs.
- **Separator** before an appended block: `"\n---\n"` (only if notes already non-empty).
- Helpers to reuse: `_note_line(qty, pack_name)` + `_already_has_pack_note(notes, pack_reference)`
  in `legacy_bom_pack_ingest.py`.

**Requirement:** a reconcile step must run so any part whose pack link is known but whose
description lacks the line gets it written — closing the manual-mapping gap. See
`SES_Workspace/docs/manual-pack-mapping-false-link-2026-09-21.md`.


## Run Protocol

### Step 1 — Dry-run stages 1+2 (check PDF discovery)
```
run_drawing_pipeline.py --job-prefix 1575 --dry-run --stop-after 2
```
Expect: all drawing tasks found, all have Build Schedule subtasks and PDF attachments.

### Step 2 — Run stages 1+2 for real (populate DB + extract BOMs)
```
run_drawing_pipeline.py --job-prefix 1575 --stop-after 2
```
Verify: `build_assembly_packs` table populated, `data/{job}_drawing_boms.csv` written.

### Step 3 — Run stage 3 (map parts in DB, dry-run first)
```
python scripts/02_map_parts_to_packs.py --job-prefix 1575 --reset --dry-run
```
Review match count vs PC row count. Expect ~90% match rate on procured parts.

### Step 4 — Commit stage 3 to DB
```
python scripts/02_map_parts_to_packs.py --job-prefix 1575 --reset
```

### Step 5 — Dry-run Asana write (stage 4)
```
run_drawing_pipeline.py --job-prefix 1575 --dry-run
```
Review `data/{job}_write_log.csv` — confirm task GIDs and pack GIDs look correct.

### Step 6 — Commit to Asana (SAP only)
```
run_drawing_pipeline.py --job-prefix 1575
```

## Output Files
| File | Contents |
|---|---|
| `data/{job}_drawing_boms.csv` | Extracted BOM: pack_reference, part_number, qty, description per drawing |
| `data/{job}_unresolved.csv` | Parts on drawings with no parts_control match (welded assy, fixings, non-procured) |
| `data/{job}_multi_pack.csv` | Parts found in more than one assembly pack |
| `data/{job}_write_log.csv` | Per-task Asana write results (append log — safe to re-run, skips already-written) |

## DB Tables Used
| Table | DB | Stage |
|---|---|---|
| `build_assembly_packs` | ops.db | Written by stage 1 |
| `parts_control` | ops.db | Read+written by stages 2, 3 |

## Confirmed Results — Jenners 1575 (2026-05-08)
- Assembly packs populated: 16
- Drawing BOM rows extracted: 1031
- Parts matched to PC: 418 of 435 (96%)
- Multi-pack parts: 6
- Asana tasks updated: 373
- Unresolved (expected — welded/fixings/not-in-PC): 360

## Key Config Files
| File | Purpose |
|---|---|
| `CLAUDE.md` | GID registry (Drawing Control, Build Schedule, Parts Control, custom field GIDs) |
| `PN_EDGE_CASES.md` | 9 part-number normalisation rules + manual alias format |
| `pn_aliases.json` | Manual PN aliases (e.g. Latch→Lock, garbled PNs) |
| `scripts/lib/pn_normalizer.py` | Normaliser implementation — edit here if new edge case found |

## Asana Write Note
`03_write_to_asana.py` must run in SAP (asana_agent) context. Never call it from
any other agent. Submit as an Asana write request via `workspace/REQUESTS/` if
triggering from a non-SAP context.
