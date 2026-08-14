# drawing_pipeline — Claude Code Rules

## GID Registry (confirmed live 2026-05-08)

| Resource | GID |
|---|---|
| Drawing Status/Control project | `1142754335688166` |
| Build Schedule project (3.1) | `852956628490855` |
| Parts Control project | `1211660707080749` |
| `Sesame Part No` custom field | `1213228090549470` |
| `Build Packs reference` custom field | `1211660742155293` |
| `Linked to Buildpack` enum flag | `1200825119140954` |
| `AI Processed` (assembly pack task) | `1142754335688200` |
| `URL to Drawing` (assembly pack task) | `1198560873229035` |

## Database

All reads/writes use `ops.db`:
```
C:\Users\GilesHugo\.openclaw\workspace\db\ops.db
```

Key tables:
- `parts_control` — 901 rows; `sesame_pn` + `asana_task_gid` populated; `build_assembly_pack_gid` = NULL (target field)
- `build_assembly_packs` — 0 rows; populated by `01_populate_assembly_packs.py`

## Sesame PN Format

Two formats exist in `parts_control`:

| Format | Example | Meaning |
|---|---|---|
| Full pack-specific | `1575.6000.A.070` | job.pack.rev.item — pack code = `1575.6000.A` |
| Short NBSS | `1575.003` | Generic/stock part — no pack encoded |

Pack code is the first 3 dot-separated components: `job.pack.rev`
- `1575.6000.A.070` → pack = `1575.6000.A`
- `1575.1005.B.001` → pack = `1575.1005.B`
- `1575.1005.B33.001` → pack = `1575.1005.B33` (revision variants exist)

## Assembly Pack Task Structure

```
Drawing Control project
└── "1575.6000.A.Stairs_Rev.1.DRAWING"  (drawing task, has PDF)
    └── "1575.6000.A.Stairs."            (assembly pack task — lives in Build Schedule)
        GID: 1214059103848019
```

Pack reference derived from drawing task name: take everything up to `.DRAWING` suffix,
then strip the `_Rev.N` or `_MOD_N` portion and any description, leaving `job.pack.rev`.

## Write Protocol (Option A)

These scripts are intended to run inside SAP (asana_agent) context which has Asana write
authorisation. Do not run the Asana write step from any other agent.

## Hard Rules

- Never write to SAS Dropbox
- `build_assembly_pack_gid` in `parts_control` is updated via SQLite first, then synced to Asana
- PDF extraction is for NBSS parts only (47 rows) — full-format PNs map via DB join
- Always dry-run (`--dry-run`) first; commit only after reviewing report
