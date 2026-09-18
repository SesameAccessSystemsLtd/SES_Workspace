---
From: hermes
To: sap
Type: asana_write
Status: PENDING
Priority: high
Created: 2026-09-12T09:00:00+00:00
---

## Request
Action: create_task
Project GID: 1147424298774210   (Giles Ai Dev Work)

## Summary
Retire historic steel profile data + complete the Steel Profile BOM ingest (Stage 6)

## Body
The Steel Profile by Pack dashboard (`data/os_status/steel-profile-by-pack.html`)
went live 2026-09-12. It reads material lines from `steel_profile.db / steel_parts`
and resolves each pack's STEEL PROFILE against **live Asana** (3.1 Build Schedule),
falling back to the ops.db mirror only for packs live does not know.

**THE REAL FINDING (diagnosed 2026-09-12, live Asana).** The 133 steel pack refs
in `steel_parts` break down as:

| Bucket | Packs | Metres |
|---|---|---|
| **No live match** — historic / already-completed jobs | **102** | **900.10** |
| live: excluded ("All Cut") | 16 | 140.59 |
| live: pack complete | 6 | 77.56 |
| **live: COUNTED ("All in ready to be cut")** | **7** | **41.45** |
| live: excluded ("Not needed") | 2 | 39.61 |

So the genuine live steel requirement is **41.45 m across 7 packs** — not the
569.92 m the dashboard first reported off the stale ops.db mirror, and not the
1,199 m held in steel_parts overall.

The 102 "no live match" packs are **not a sync bug**. Their projects —
1366, 1501, 1533, 1536, 1566, 1568, 1572, 1578 — have **zero** incomplete
packs in 3.1 Build Schedule at all. They are finished or historic jobs whose
steel BOMs are still sitting in `steel_parts` and being counted as demand.

Only 11 projects appear in both `steel_parts` and live Asana:
1037, 1365, 1555, 1569, 1575, 1580, 1582, 1588, 1591, 1595, 1670.

## Critically — the ops.db steel_profile mirror is unusable
It is KNOWN-STALE vs live (flagged in `steel_profile_outstanding.py`, verified
2026-09-07; re-confirmed 2026-09-12). Divergence measured:

| STEEL PROFILE | live Asana (incomplete) | ops.db mirror |
|---|---|---|
| Not needed | 80 | 103 |
| All Cut | 49 | 64 |
| blank | 45 | (unusable) |
| All in ready to be cut | 16 | 2 |
| Not Assessed | 12 | 68 |

The dashboard now always resolves against live Asana. Do **not** rebuild it
against the mirror. The sync that populates `build_assembly_packs.steel_profile`
needs fixing at source.

## This task
1. **Retire the historic rows.** Decide the fate of the 102 no-live-match packs
   in `steel_parts` — most belong to completed jobs. Either mark them as
   historical (add a flag / separate table) or remove them from the
   "requirement" population. Until then they are held visibly in a
   "Held apart" panel, so they are not silently counted as demand.
2. **Fix the `steel_profile` mirror sync** so `ops.db` stops disagreeing with
   live Asana. Root-cause why it lags, then either repair the sync or drop the
   column from the dashboard path entirely (already done — dashboard uses live).
3. **Complete the Stage 6 ingest** for steel BOMs not yet in `steel_parts`
   (19 projects present; more outstanding).
4. **Resolve 2 parser-integrity failures:** `1572.1004.B.082` and
   `1588.1004.B.415` — both parse their profile as "Ram Support plate 25x4",
   i.e. the parser is treating a descriptive part name as a profile shape.
5. **Work `material_unmapped`** (21 rows) — resolve each ambiguous BOM material
   string to a known material record, or add it as a new material.

## Also unresolved (from the workflow-init task)
How Stage 1 detects a steel row (flag / filename / no-Cost-Centre pattern).
Steel Profile BOMs have no Cost Centre column and are dropped at Stage 1 today.

## Context
Raised from the Steel Profile by Pack dashboard build. Giles directive
2026-09-12: resolve against Asana always; material lines must come from the DB
(live Asana carries no per-pack part context); track the retrospective work as
a dev task.

## Guardrail assessment
- Create (not delete) — safe.
- Not a forbidden GID (not Project Status Overview).
- Tracked in Giles Ai Dev Work: process-build / data-completion initiative.
