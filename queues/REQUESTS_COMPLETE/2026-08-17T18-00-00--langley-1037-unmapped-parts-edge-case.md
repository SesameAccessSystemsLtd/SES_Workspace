---
From: hermes
To: hermes
Type: asana_write
Status: COMPLETE
Completed: 2026-08-25T14:06:00.511328+00:00
Note: Edge case task created GID 1217827390379336 in New Issues section
Priority: high
Created: 2026-08-17T18:00:00+01:00
---

## Request
Action: create_task
Project GID: 1214604066893979
Section GID: 1214603906356078
Task name: Langley 1037: 41 legacy-BOM parts not pack-mapped (Gate+Support `+`, Cover_rev3, consolidated bellows/tapeswitch/trims)
Custom Fields JSON: {"1216261763561518":"1216261763561520","1214603906356101":"1214603906356103","1214603906356092":"1214603906356094"}

Description:
SAS Edge Case (Agent-discovered, 2026-08-17).

## Problem
41 parts in the compiled Langley (1037) Legacy BOM exist in ops.db `parts_control` with EMPTY
`build_assembly_pack_gid`. They were never pack-linked by `legacy_bom_pack_ingest.py --scan/--commit`
because their source files failed to resolve to a single Build Schedule pack.

Project: 1670 Blackfriars? No - 1037 Langley Street. Build: Langley gate lift.
Control: legacy_bom_pack_allocation skill; scan run 2026-08-17.

## Root cause (4 buckets)
1. `1037.1014.M.Gate+Support_rev2.xlsx` (~18 parts: M.041/057/064/104-118, M14w, N.010.01, N.106)
   - The `+` in the filename breaks the pack-reference match. Build Schedule pack
     `1037.1014.M.Gate+Support.` EXISTS (GID 1216065135265568) but was not matched -> file skipped.
2. `1037.9990.A.Cover_rev3.xlsx` (~10 parts: 9990.A.001-016, A1w, 1016.H1)
   - The `_rev3` suffix / rev marker broke the match. Build Schedule pack `1037.9990.A.Cover.`
     EXISTS (GID 1213566425247576) but scan matched only the `.DRAWING` sibling -> file skipped.
3. `1037.Langley street_05.03.2026(bellows+tapeswitch+trims)_rev2.xlsx` (~10 parts:
   Bellows.01, TapeSwitch.01, 9991.C.001/002, 9992.A.001/002, 9992.B.001/002)
   - CONSOLIDATED multi-pack file spanning Bellows / TapeSwitch / PTFE slider / Wheelstop packs.
     Ingest assigns to a SINGLE pack only (filename=pack); no single Build Schedule match -> skipped.
4. PN not verbatim in any file: 1037.1016.X.001, 1037.1016.1.003, 1037.1016.1.007, 1037.034.001
   (formatting/version mismatch in the xlsx cell, though present in parts_control).

## Impact
These 41 parts cannot be pack-linked by the current auto-scan. Their owning Build Schedule packs
(Gate+Support due 28 Aug, Cover due 31 Aug, Bellows/TapeSwitch/Wheelstop due 28 Aug) therefore
cannot report correct readiness in ops.db / dashboard. Blocks "which build packs are ready when"
for Langley.

## Missing parts (41 distinct)
1037.1006.A.Bellows.01, 1037.1016.H1, 1037.1014.M.057, 1037.9991.C.001, 1037.9991.C.002,
1037.9990.A.002, 1037.9990.A.003, 1037.9990.A.012, 1037.034.001, 1037.1014.M.064,
1037.9990.A.011, 1037.9990.A.010, 1037.9990.A.004, 1037.1014.M.114.01, 1037.1014.M.105,
1037.1014.M.112, 1037.1014.N.010.01, 1037.1014.M.118, 1037.1014.M.115.01, 1037.1014.M.116.01,
1037.1014.M.113, 1037.9992.B.002, 1037.1014.N.106, 1037.9990.A.001, 1037.1014.M.107,
1037.9992.A.001, 1037.9990.A.016, 1037.1014.M.110, 1037.9992.A.002, 1037.1014.M.106,
1037.1014.M.111, 1037.9992.B.001, 1037.9991.A.TapeSwitch.01, 1037.9990.A.014, 1037.9990.A1w,
1037.1016.1.003, 1037.1016.1.007, 1037.1016.X.001, 1037.1014.M14w, 1037.1014.M.104, 1037.1014.M.041

## Proposed fix (next steps)
- Fix `resolve_pack()` in legacy_bom_pack_ingest.py to treat `+` and `_revN`/`_revNN` and
  trailing suffix-version patterns as pack-reference mnemonics, so Gate+Support and Cover resolve.
- For the consolidated multi-pack file: derive per-part pack from the part NUMBER's first 3
  dot-components (e.g. 1037.9991.C -> pack) rather than the filename; or split the file.
- These are mappable -- the Build Schedule packs exist. Priority High: unblocks pack readiness.

---
## Context
Discovered during Langley 1037 legacy-BOM pack allocation (2026-08-17) when 37 parts from clean
per-pack files committed successfully but these 41 (across skipped/consolidated files) stayed
unassociated. Logged to SAS Edge Cases for visibility + fix workflow.
