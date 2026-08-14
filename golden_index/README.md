# Golden Index — Unified BOM Build

A deterministic, auditable pipeline that consolidates the Golden Index Rev6
mapping and the Karol assembly-pack list into one master reference where
**each KAN number maps to exactly one stock number**, with enumerated
assembly packs and Asana/Sesame part numbers.

## Files

| File | Purpose |
|------|---------|
| `build_golden_index.py` | The build + validation script (run this). |
| `Golden Index Rev 6   - Karol_Stock_Match.csv` | Input 1 — KAN ⇄ stock ⇄ Asana part refs. |
| `KAROL MASTER MATCH - Assembly Packs.csv` | Input 2 — stock ⇄ assembly packs. |
| `golden_index_master.xlsx` | Output — multi-tab workbook (see below). |
| `golden_index_master.json` | Output — LLM-ready master (resolved KANs only). |

## Run

```bash
python3 build_golden_index.py
# optional overrides:
python3 build_golden_index.py --golden PATH --assembly PATH --outdir DIR
```

Requires Python 3.9+ and `openpyxl` (`pip install openpyxl`).

## How it works

1. **Load** both CSVs with encoding fallback (`utf-8-sig` → `latin-1`) to
   survive the BOM in the assembly file and Latin-1 bytes in the golden file.
2. **Normalise** every stock number to a comparison key: upper-case, strip
   `RS` / `Stock No` prefixes, remove all non-alphanumerics. So
   `RS: 690-3292` and `690-3292` collapse to the same key; `700-0802` and
   `10060610` do not.
3. **Aggregate** golden rows by KAN. Exact-duplicate rows are collapsed and
   counted. Asana part-number cells like `1004.B3.108 , 431` are split into
   individual part numbers.
4. **Resolve vs. flag** — if a KAN's stock numbers all share one normalised
   key → **resolved** (one canonical stock number chosen deterministically:
   most frequent → shortest → alphabetical). If two or more distinct keys
   remain → **true conflict**, withheld from the master and reported.
5. **Join assembly packs** onto each resolved KAN's stock number by
   normalised key.
6. **Emit** the workbook and JSON. Everything is sorted, so identical inputs
   always produce identical outputs.

## Conflict policy — no silent guessing

A KAN that genuinely maps to more than one stock number is **flagged in the
`Conflicts` tab and excluded from `Master`** until a human resolves it. The
script never picks a winner among genuinely different parts. Formatting-only
variants that it *does* auto-merge are logged in `Auto-merged variants` so
every decision is auditable.

## Applying conflict resolutions

Human decisions are supplied via a resolutions CSV (default
`CONFLICT UPDATE FOR KAROL.csv`, auto-detected next to the script). It needs a
`KAN` column and a column whose header contains `Resolution` — exactly the
`Conflicts` tab exported to CSV with the `Resolution (fill in)` column filled.

On the next run, each resolved KAN is promoted into `Master` using the chosen
stock number (packs re-joined by that stock number), and the decision is
logged in the `Resolved conflicts` tab. Blank resolutions are ignored, so a
partially-completed file is safe to run. If a resolution introduces a stock
value that wasn't among the flagged candidates, it is accepted but marked
`NO (new value)` in the audit tab and printed to the console — a guard against
typos.

```bash
python3 build_golden_index.py                          # auto-detects the file
python3 build_golden_index.py --resolutions PATH.csv   # explicit
```

## Workbook tabs

- **Summary** — run metrics + methodology note.
- **Master** — the clean index: one row per resolved KAN, with stock number,
  assembly packs, Asana Sesame PNs, vendor PNs, task names, description.
- **Conflicts** — KANs with multiple candidate stock numbers, plus a blank
  `Resolution` column for a human to fill in.
- **Auto-merged variants** — formatting-only merges (audit trail).
- **Data quality** — resolved KANs without a pack, assembly stocks not linked
  to any KAN, and KANs with no stock number.

## Current results (as built)

- 186 KANs in source → **185 resolved** (167 automatic + 18 via human conflict
  resolution), 0 conflicts outstanding, 1 with no stock (KAN-1031).
- 4 formatting variants auto-merged; 78 duplicate rows collapsed.
- 51 resolved KANs have no assembly pack; 92 assembly stocks link to no KAN.
- One resolution introduced a new value: KAN-380 → `NBSS.014` (flagged for
  confirmation in the `Resolved conflicts` tab).

## Resolving conflicts

Fill in the `Resolution` column in the `Conflicts` tab (or fix the source
CSV) and re-run. Once a KAN resolves to a single normalised stock number it
moves automatically into `Master`. This makes the process safe to run
repeatedly as the underlying data is cleaned — the intended behaviour for the
Hermes/Asana webhook pipeline.
