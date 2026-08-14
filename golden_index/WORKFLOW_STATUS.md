# Golden Index — Workflow Status

## Concern A — Stock check: COMPLETE and wired

**Single source of truth:** `ops.db` table **`golden_index_rev6`** (185 KANs, all
conflicts resolved). Other systems should read this table. The KAN-grain master
JSON (`golden_index_master.json`) is retained as a static point-of-reference /
history snapshot. The Rev4 CSV and `seed_golden_index.py` are **deprecated**.

### What changed in the pipeline
- `2.Stock Controls/02_golden_index_match.py`
  - New `--from-db` source: loads the index from `golden_index_rev6` instead of a
    CSV (CSV path kept as automatic fallback).
  - Matches on the BOM's **`stock number`** column (vendor stock value → KAN), not
    the CAD `part number`. This was the core fix — the old default matched the
    wrong column and returned ~0 hits against Rev6.
  - Added a strong `stock_key` exact match (upper-case, strips `RS`/`Stock No`
    prefixes and all non-alphanumerics), so `RS Stock No 724-2677` and `724-2677`
    match. Fuzzy/substring fallback unchanged.
- `run_mrp_pipeline.py`
  - `USE_DB_GOLDEN_INDEX = True` → Stage 2 now runs `--from-db --part-col "stock number"`.
    Set the flag to `False` to force the legacy CSV path.

### How to run a full BOM import (unchanged for you)
Use the `bom_pipeline` skill exactly as before (`run_mrp_pipeline.py`
`--preflight`, then `--stop-after 4`, etc.). Stage 2 automatically uses
`golden_index_rev6` now. Nothing new to pass.

### To refresh the index after a data change
Deterministic, repeatable:
```
python build_golden_index.py        # CSVs (+ resolutions) -> golden_index_master.json
python stage_golden_index.py --load-db   # JSON -> golden_index_rev6 table + views
```
`stage_golden_index.py --rollback` drops the table (reversible).

### Validation — how well it maps (measured, not assumed)
Replaying all 39 historical BOM runs through the Rev6 matcher
(`validate_bom_matching.py`, report in `bom_matching_report.md`):

| Metric | Value |
|---|---|
| BOM lines | 3,009 |
| Lines carrying a stock number | 1,849 |
| Exact matches (stock key) | 275 |
| Fuzzy matches (≥80) | 89 |
| **Matched of stock-bearing lines** | **364 / 1,849 = 19.7%** |
| Unmatched (→ ORDER) | 1,485 |

Interpretation: the index holds 185 standard reusable KANBAN parts. Most BOM lines
are bespoke project items (laser cut, engraving, project-specific) with no standard
stock number — they correctly route to ORDER. Matching on the correct column now
finds real KANs where the old approach found none. Spot-check the 89 fuzzy matches
before trusting them (threshold 80 can admit a few false positives); tighten with
`--threshold` if needed.

### Legacy cleanup (awaiting your ok)
The old empty `golden_index` table is deprecated and untouched (0 rows). Only the
retired `seed_golden_index.py` writes to it. On your say-so I'll drop the old table
and retire the Rev4 CSV + seeder so there is unambiguously one source of truth.

---

## Concern B — Sesame/Asana task update: NEXT workflow (staged, not run)

Goal: consolidate the stock tasks that currently live in **two** projects
(Consumables + KANBAN) into one new **Stock Visibility** project, then push per-KAN
data onto them.

Target project: https://app.asana.com/1/357939803556123/project/1214420954265392/list/1214447985869249

New fields on the Stock Visibility task:
- **Assembly Packs** — dropdown (multi-select enum; 77 distinct pack codes from the
  index). Selecting e.g. `1005.A` groups every part on that pack.
- **Part Numbers** — free text (the Sesame PNs).

Staging ready: `asana_sesame_task_updates.csv` (per-KAN packs + Sesame PNs, with
KANBAN task links where found — 69/185 auto-linked, 116 need manual match).
Recommended execution: route the writes through the Hermes Asana agent (SAP) for
the write-log + approval trail. Sequence when you're ready:
1. Create the Stock Visibility project + the two custom fields.
2. Migrate/consolidate Consumables + KANBAN stock tasks into it.
3. Resolve the 116 unlinked KANs.
4. Push Assembly Packs (dropdown) + Part Numbers (free text) from the staging file.
