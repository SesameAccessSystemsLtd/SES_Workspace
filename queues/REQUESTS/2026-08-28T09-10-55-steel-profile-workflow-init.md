---
From: hermes
To: hermes
Type: asana_write
Status: PENDING
Priority: normal
Created: 2026-08-28T09:10:55
---

## Request
Action: create_task
Project GID: 1147424298774210   (Giles Ai Dev Work)

## Summary
Create a tracking task for the **Steel Profile workflow systemisation** initiative:

Task name: "Steel Profile workflow — init (watcher, DB, price archive, VPS)"
Notes body: full scope of the 5-part initiative:
1. Build-schedule watcher — parent STEEL PROFILE → "All Cut" when Weld+Assemble child subtasks complete (script built, dry-run verified on 2 eligible parents, awaiting approve to go live)
2. Steel Profile memory DB — new SQLite store (steel_parts + material_price_archive), separate format from ops.db
3. Material price archive — price-per-material history, supplier comparison, ordering dates
4. Scripted compile — search local DBs → build part DB → compile qty/cut output; human keeps the material edge-case assessment
5. VPS fallback — Cloudflare webhook-triggered watcher prompt drafted, local-first validation required before remote

Custom fields: Area=VPS/Webhooks OR Ordering/Inventory, Status=Not Started, Priority=High, Effort=L

## Confirmed architecture decision (2026-08-28, Giles)
Steel profile plugs into the BOM pipeline as a **dedicated Stage 6 (Steel Profile
Extract)** — an isolated sink AFTER Stage 1, NOT a fork inside Stage 1. Grounding:
steel BOMs have no Cost Centre column (dropped at Stage 1 today); bom_push already
excludes "Steel Profile"; Stages 2-5 assume standard MRP schema. Steel flow: Stage
1 diverts steel rows → Stage 6 parses → steel_profile.db steel_parts + runs
material-substitution map (halting for Giles on each) → price archive at order.
Material-substitution map (Claude's CSV) to be a table in steel_profile.db.
UNRESOLVED: how Stage 1 detects a steel row (flag / filename / no-Cost-Centre pattern).

## Context
Giles directive 2026-08-28: initiate the steel-profile workflow (currently all-manual). The watcher and skill folder are built locally; this task tracks the full initiative and holds the go-live decision for the watcher.

## Guardrail assessment
- Not a forbidden GID (not Project Status Overview).
- Create (not delete) — safe.
- Tracked in Giles Ai Dev Work (Hermes dev/process-build companion), not the SAS edge-case intake, because this is a new process-build initiative, not a bug/edge case.
