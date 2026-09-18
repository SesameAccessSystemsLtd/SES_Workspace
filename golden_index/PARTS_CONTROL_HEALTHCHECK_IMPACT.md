# Parts Control overlap "health check" — impact of retiring it

## What it measures today
It compares the Golden Index's part numbers against `parts_control` live
`sesame_pn` and treats high overlap as "healthy". Current reading: **2 of 335**.

## Why it reads near-zero (structural, not a data fault)
1. **Different grain.** Golden Index part numbers are generic, prefix-stripped
   templates (`1016.2.001`, `1000.A.001`). Parts Control live part numbers are
   project-specific instances — **644 of 651 carry a 4-digit project prefix**
   (`1582.1016.1.1`, `1575.1000.E.TD7654`). Template vs instance never string-match.
2. **Different objects.** Parts Control is a separate Asana project (BOM/procurement
   lines). Only **14 of its 2,765 task GIDs** overlap the KANBAN/Consumables stock
   cards the index is built from. The two sets are *supposed* to be near-disjoint.

So the check reads ~0 whether the index is healthy or broken — it has no
diagnostic power for this index, and it manufactures a false alarm.

## Impact of retiring it
- **Lost signal: none real.** It cannot detect an actual index defect; it would
  stay ~0 either way. Removing it removes a false alarm, nothing more.
- **Risk: low**, provided it is replaced by the invariants below (which *do* have
  diagnostic power).

## Replace it with these invariants (all pass right now)
| Invariant | Meaning | Current |
|---|---|---|
| Every row has an `asana_task_gid` | Index anchors to a real stock task | **177/177** |
| GIDs are 1:1 with rows | No two KANs collide onto one task | **177 distinct** |
| `stock_number` unique per KAN | BOM match is unambiguous | **0 ambiguous** |
| KAN round-trips to the Asana KAN field | The anchor is real, not guessed | sourced from the KAN-field gather |
| `needs_split` queue size | Ambiguous KANs awaiting a split | **8** (tracked, withheld) |

## If you still want a Parts-Control cross-check, measure the right thing
Not raw PN string equality — measure **BOM demand coverage**: of live Parts
Control lines, how many resolve to a Golden Index stock item *through the Stage-2
matcher* (vendor stock value → KAN). That is a real KPI ("what % of demand is
covered by catalogued stock"), and it runs on the matcher, not on `sesame_pn`
equality. Build it there if wanted; the current overlap check should be retired.

## Recommendation
Safe to retire the raw-overlap check. Adopt the five invariants above as the
index health check. Optionally add BOM-demand-coverage on the matcher later.

## Implemented (2026-09-11)
- The raw Parts-Control overlap check is **retired** (it was never committed code —
  only ad-hoc queries — so nothing to delete; it is simply not adopted).
- The five invariants are now the canonical check: **`healthcheck_golden_index.py`**
  (read-only, exits non-zero on any hard-invariant failure so it can gate
  cron/pipeline). Current result: **PASS** — 177/177 GIDs, 177 distinct (1:1),
  stock unique per KAN, all KANs verified against the gather; WARN lists the 8
  withheld needs-split KANs.
- The GID-corruption root cause is also fixed: `stage_golden_index.load_db` now
  sources `asana_task_gid` from the authoritative KAN-field gather
  (`gathered_gids.csv`), not the fuzzy `stock_inventory` heuristic — so reloads no
  longer re-introduce blank/wrong GIDs (verified: authoritative=177, heuristic=0,
  blank=0 on reload).
