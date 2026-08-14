# BOM Pipeline — Claude Code Rules

This file covers code-level rules and gotchas for working with the BOM pipeline. For full operational usage (how to run, what each stage does, inputs required) read `SKILL.md` first.

---

## Hard rules

- **Never run `--commit` without explicit Giles go-ahead.** Always stop at Stage 4, report the staging Excel path, wait. No exceptions.
- **`--commit` without `--approve` now halts if an existing ALL BOMS root is found.** If the idempotency guard blocks you, pass `--approve` to confirm you want a new root alongside the existing one.
- **Always confirm `--project-gid` with Giles** before every run. Each job has a unique GID. Never reuse a previous job's GID.
- **Source BOM files may be read from `C:\Users\GilesHugo\SAS Dropbox\`** — Dropbox is read-only. Never delete or modify files there.
- **Use the SES_Workspace venv Python**, not bare `python`: `C:\Users\GilesHugo\SES_Workspace\.venv\Scripts\python.exe`

---

## Golden Index — current format (Rev4)

**File:** `2.Stock Controls/Merged_Golden_Index_Rev4.csv` (relative to this skill folder)  
**Backup copy:** `C:\Users\GilesHugo\OneDrive - SESAME ACCESS AZURE\OneDrive Documents\LAB. SESAME\Asana Scripts\2.Stock Controls\Merged_Golden_Index_Rev4.csv`

Any code referencing `Rev2` or `Merged_Golden_Index_Rev2.csv` is stale — update it.

**Key columns (16 total):**

| Col | Name | Role |
|-----|------|------|
| C | `Stock_Number` | Lookup key — prefix-stripped part number (e.g. `1003.B.087`) |
| I | `Mapped_KAN` | KAN number (e.g. `KAN-330`) |
| J | `Asana_Name` | Asana task name |
| M | `Asana_URL` | Asana task URL |
| N | `Confidence` | Match confidence score |
| O | `Match_Type` | How the match was made |
| P | `Provisional` | YES/NO |

**When updating the index:** use `update_golden_index.py` in `2.Stock Controls/`. Never hand-edit the CSV directly in code — use the upsert script.

**Both copies must stay in sync** — after any update write to both the skill folder copy and the lab copy above.

---

## Part number format rules

| Context | Format | Example |
|---|---|---|
| Full (project-specific) | `{4-digit-project}.{cad_pn}` | `1566.1003.B.087` |
| `Stock_Number` in Golden Index | Prefix stripped | `1003.B.087` |
| `Sesame Part No` field in Asana | **Full number** — never stripped | `1566.1003.B.087` |

**Prefix strip rule:** remove `^NNNN\.` from start (4 digits + dot). Apply this ONLY when writing to the Golden Index. Always write the full number to Asana custom fields.

Stage 2 (`02_golden_index_match.py`) normalises BOM part numbers before matching. Fuzzy matching uses `SequenceMatcher`, default 80% threshold.

---

## Key GIDs

| Resource | GID |
|---|---|
| Build Orders / Project Orders (standard Stage 5 target) | `848657415032331` |
| KANBAN stock project (Stage 3 stock check) | `632603478911321` |
| Consumables stock project | `1200068443534209` |
| Parts Control project | `1211660707080749` |
| **Project Status Overview — NOT a job project, never import target** | `1192686469436889` |
| Sesame Part No custom field (GID) | `1213228090549470` |
| STOCK QTY custom field | `1126782469949969` |
| Trigger Order QTY custom field | `1198179728033183` |
| min Stock QTY custom field (Consumables) | `1205084645800516` |
| ORDER QTY custom field | `1201447044882111` |
| Vendor custom field | `854127216887083` |
| Stock ACTION custom field (enum) | `1199111659373956` |
| Build Packs reference field | `1211660742155293` |

**Per-import job GIDs** — Jenners Edinburgh: `1213587365588623`, purchasing control task: `1213587365685012`. All other jobs: ask Giles.

**Full GID reference** (179 fields): `C:\Users\GilesHugo\SES_Workspace\reference\Asana Custom Field Index GID numbers.xlsx`

---

## Stage map

| Stage | Script path (relative to skill root) | Role |
|---|---|---|
| 1 | `1.BOM IMPORT into Asana/BOM_Consolidator_SLe_v4.py` | Consolidate vendor xlsx files |
| 2 | `2.Stock Controls/02_golden_index_match.py` | Match parts to Golden Index |
| 3 | `2.Stock Controls/03_stock_check.py` | Live stock check against KANBAN |
| 4 | `2.Stock Controls/04_staging_export.py` | HITL staging review Excel |
| 5 | `1.BOM IMPORT into Asana/BOM_IMPORT_4_Asana_v4.py` | Create task hierarchy in Asana |

Entry point: `run_mrp_pipeline.py` (skill root) — do not call stage scripts directly.

---

## Asana task hierarchy produced by Stage 5

```
ALL BOMS - {project_name}          ← top-level task in Build Orders project
├── PICKING BOM                    ← in-stock parts to pick
└── {CostCentre} BOM - {project}
    └── {Vendor} - {project}       ← added to Build Orders project
        ├── {qty} off {part} - {desc} {material} {vendor}
        └── ...
```

Each imported task description must include: project name, Dropbox URL, purchasing control task Asana URL. Stage 5 embeds `--dropbox-url` and `--asana-url` in the root task automatically.

---

## Auth

Scripts read `ASANA_PERSONAL_ACCESS_TOKEN` from environment. Fallback: `ASANAconfig.json` in this skill folder. Never hardcode the token.

---

## Common code mistakes

- **Referencing Rev2 index** — stale, update to Rev4.
- **Hardcoding `--project-gid`** — it changes every job.
- **Using `1192686469436889` as import target** — that is Project Status Overview, not a job project.
- **Calling stage scripts directly** instead of through `run_mrp_pipeline.py` — the orchestrator handles stage ordering and flags.
- **Missing `Cost Centre` column** — rows without it are silently dropped at Stage 1. Check source Excel before running.
- **Steel Profile BOM** — has no Cost Centre column at all; must be manually added before running.
- **`_MIR` parts** — won't match Golden Index and go to ORDER. Expected behaviour, not a bug.
- **Parts Control automation** — project membership in Parts Control (`1211660707080749`) is currently manual. Do not automate this without explicit instruction from Giles.
- **Build Packs reference field** (`1211660742155293`) — requires a task GID, not text. Cannot be set by name.

---

*Maintained by Forge nightly audit.*
*Last sync: 2026-04-27*
