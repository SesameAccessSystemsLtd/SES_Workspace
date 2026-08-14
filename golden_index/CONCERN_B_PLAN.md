# Concern B — Multi-home KAN tasks into Stock Visibility + push fields

## Objective
Give every KAN stock item one home for visibility. **Multi-home** (add, not copy /
migrate) the KAN-bearing tasks from Consumables and Kanban into the **Stock
Visibility Parts** project, then populate **Assembly Pack** (dropdown) and **Sesame
Part No** (free text) from the Golden Index.

## Confirmed facts (from live inspection)
| Thing | GID |
|---|---|
| Stock Visibility Parts (target) | `1214420954265392` (was 0 tasks, now 3 after test) |
| Consumables Stock / Order Control | `1200068443534209` (736 tasks) |
| Stock Control / Kanban - Tracking | `632603478911321` (577 tasks) |
| Field: KAN (text) | `1206590288351824` |
| Field: Sesame Part No (text = "part numbers") | `1213228090549470` |
| Field: Assembly Pack (multi-select dropdown) | `1214447985984465` |
| Field: Vendors Part number (text) | `1198507094515419` |

## Capability findings (what the tools can/can't do)
- **Multi-home: YES.** `update_tasks` (Asana connector) supports `add_projects`
  (with optional section) and `remove_projects`. Adding does not remove the task
  from its source project → exactly "multi-home", reversible, no duplication.
- **Field writes: YES.** Same call sets `custom_fields` (text by value, dropdown by
  option GID) — so multi-home + field-set happen in one atomic per-task update,
  batched up to 50 tasks/call.
- **Create dropdown options: NO (via these tools).** The Assembly Pack field
  currently has only 2 options (`1010 - Scissor`, `1004.K - Moto Pump Frame`). The
  Golden Index has 77 pack codes (`1003.B`, `1009.B`, …). Adding enum options is not
  exposed by the MCP — it must be done in the Asana UI (bulk) or via SAP/raw API.

## Resolved settings (from Giles)
- **Sesame Part No: overwrite** (not fill-if-empty). Multiple PNs → comma-delimited.
- **Assembly Pack: keep pack codes as formatted in the index** (`1003.B`, `1016.2`, …).
- **Sections** added to Stock Visibility for routing by origin:
  | Section | GID |
  |---|---|
  | Consumable | `1217486166709509` |
  | Stock | `1217486166709510` |
  | Hydraulics (later) | `1217486166709511` |
  Route Consumables-origin tasks → Consumable; Kanban-origin → Stock.

## Test run (done — small sample, validated)
Multi-homed 3 golden-index KAN tasks into Stock Visibility, placed in the correct
section, and overwrote Sesame Part No from the index:

| KAN | Task GID | Section | Sesame Part No written |
|---|---|---|---|
| KAN-329 | 928895539267445 | Stock | `1016.2.001` |
| KAN-244 | 887610591341502 | Stock | `1003.B.013, 1006.J.080` |
| KAN-1010 | 1203131385985027 | Consumable | `1010.A.089, 1010.B.087, 1011.E.105, 1011.E.106` |

Verified on KAN-244: present in **both** Kanban (source, unchanged) and Stock
Visibility → Stock section, Sesame Part No = the delimited list. **Multi-home +
section routing + Sesame overwrite validated.** Reversible via `remove_projects`.

## Assembly Group field (added) — validated
New multi-select **Assembly Group** (`1217486166709512`) with 10 live options. Giles'
pack→group taxonomy is encoded in `build_assembly_group_map.py`; **all 77 golden packs
map to a group (0 uncovered)**. Outputs: `assembly_group_map.csv` (pack→group) and
`kan_assembly_groups.csv` (KAN→group option GIDs). Validated on the sample: KAN-244 →
`Lift, Stairs`; KAN-329 → `Buttons`. Group option GIDs: Lift `…513`, Stairs `…514`,
Barriers `…515`, Gates `…516`, PIT TRIM `…523`, Buttons `…517`, Traversing `…518`,
Scissor stock `…519`, PLC Stock `…520`, GENERIC STOCK `…521`.

Per-task push now sets, in one `update_tasks` call: `add_projects` (section by origin)
+ Sesame Part No (overwrite) + Assembly Group (option GIDs). Assembly Pack follows once
its options exist.

## Remaining blocker — Assembly Pack options
The dropdown still has only its 2 original options; the index has **77 pack codes**.
The Asana tools available here **cannot create enum options**. So dropdown values
can't be set until the 77 options exist (named as the index codes). Routes:
(i) I generate the exact 77-option list → you bulk-add in the Asana field editor, or
(ii) SAP/raw API creates them. This is independent of, and can run after, the
multi-home + Sesame push.

## Bulk execution plan (once A & B are set)
1. **Gather** the target set: every task in Consumables + Kanban whose `KAN` field
   value is one of the 185 golden KANs. (`search_tasks` per project with
   `1206590288351824.is_set = true`, then intersect with the index.)
2. **Stage** a per-task change list: `task_gid, KAN, add_projects=[StockVisibility],
   Sesame_Part_No, Assembly_Pack option GIDs`. Review file before running.
3. **Apply** in batches of ≤50 via `update_tasks` (multi-home + fields in one call).
4. **Verify**: Stock Visibility task count = target count; spot-check fields; log to
   the audit trail (route via SAP for the write-log if governance requires).
5. **Rollback** if needed: `remove_projects` on the batch.

## Recommended sequencing
Do the multi-home + Sesame Part No push first (no dependency). Create the Assembly
Pack options in parallel, then a second pass sets the dropdown values.
