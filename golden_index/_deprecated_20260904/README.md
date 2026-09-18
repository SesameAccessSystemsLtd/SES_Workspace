# Deprecated Golden Index artifacts — retired 2026-09-04

One unambiguous source of truth for the Golden Index is now the **`golden_index_rev6`**
table in `SES_Workspace/db/ops.db`, built by:

```
golden_index/build_golden_index.py        # CSVs + CONFLICT UPDATE FOR KAROL.csv -> master.json/.xlsx
golden_index/stage_golden_index.py --load-db   # master.json -> golden_index_rev6 table
```

The files here are the retired legacy path. Nothing live reads them.

| File | Was | Why retired |
|------|-----|-------------|
| `seed_golden_index.py` | wrote the old empty `golden_index` table | table dropped; table was never read by the live matcher |
| `update_golden_index.py` | rewrote `Merged_Golden_Index_Rev4.csv` | CSV path superseded by the rev6 build chain |
| `Merged_Golden_Index_Rev4.csv` | 119-row CSV, read by Stage 5 amendment check + Stage 2 CSV fallback | Stage 5 repointed to `golden_index_rev6`; DB is the source |
| `Merged_Golden_Index_Rev2.csv` | 463-row CAD-PN era CSV | superseded generations ago |
| `golden_index_DROPPED_DDL.sql` | schema of the dropped empty `golden_index` table | kept for reference/rollback |

**The empty `golden_index` table (0 rows) was DROPPED** on 2026-09-04. To restore it, run the
saved DDL. Legacy CSV-fallback code still exists in `02_golden_index_match.py`,
`BOM_IMPORT_4_Asana_v4.py`, and `run_mrp_pipeline.py` (only reached if the DB is unreachable or
`USE_DB_GOLDEN_INDEX=False`); it will now fail loudly rather than silently serve stale data — the
intended behaviour for a single source of truth.
