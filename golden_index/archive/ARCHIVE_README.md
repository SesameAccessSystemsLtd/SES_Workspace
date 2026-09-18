# Archived — one-off Concern-B push artifacts (2026-08-14 run)

These are spent execution artifacts from the bulk multi-home + field push to the
Stock Visibility Asana project. Kept for audit; safe to delete.

- `batch_1..4.json`  — ABANDONED first attempt (multi-home + all fields in one call).
  Assembly Group failed here because the field isn't on a task until it's in the
  project. Superseded by the two-pass payA/payB approach.
- `payA_1..4.json`   — Pass A applied: add_projects (section) + Sesame Part No. 185 tasks.
- `payB_1..3.json`   — Pass B applied: Assembly Group multi-select. 134 tasks.
- `staging_push.csv` — intermediate KAN -> (sesame_str, group_gids) used to build the above.

Live/authoritative equivalents remain in the parent folder:
gathered_gids.csv (KAN->task GID, read by the loader), golden_index_master.json,
kan_assembly_groups.csv, assembly_group_map.csv.
