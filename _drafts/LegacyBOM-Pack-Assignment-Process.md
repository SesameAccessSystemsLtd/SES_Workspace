# Process Proposal — Parts → Build Pack assignment from design-team Legacy BOM spreadsheets

**Status:** Development edge case proposal (to be filed in Giles Ai Dev Work → Edge Cases)
**Date:** 2026-08
**Author:** Hermes (agent)

---

## 1. The problem

Parts in the PARTS CONTROL project reference their build pack via a **free-text pack name** in the
`Build Packs reference` field, not a task GID. `pack_association.py` resolves these by text-name match
(`linked_by_ref`), which works while pack names are stable but breaks on any pack-name revision
(e.g. `MainStairFrame.` → `MainStairFrame_MOD1`) and provides no verified GID link.

Current ops.db state (2146 parts_control rows):
- `linked` (by GID): **44**
- `linked_by_ref` (by pack name text): **959**
- `unassociated` (no resolvable pack): **1143** (of which **424 are open/active**)

## 2. The design-team source — validated link data

The design team maintains spreadsheets in:
`C:\Users\GilesHugo\SAS Dropbox\3. Projects\America St No.1\2.2. Assembly Drawings\Legacy BOM's\`

- **One `.xlsx` per build pack**; filename = pack name (e.g. `1588.1006.A.WheelStopPlatform-SS.xlsx`,
  `1588.1025.A.Low.Profile.ScissorLift - Legacy.xlsx`).
- Single `PARTS LIST` sheet, columns:
  `ITEM | QTY | ITEM | DESCRIPTION | PART NUMBER | REVISION NUMBER | THICKNESS | MATERIAL | STOCK NUMBER | VENDOR | WEBLINK | AUTHORITY | COST CENTER | NOTES | MASS`.
- Hierarchy via `ITEM` (`1`, `1.1`, `1.6.10.1`, …) — parent = assembly, children = components.

## 3. What counts as a BOM item vs a design-model artifact

This is the critical rule. **Never push all rows from a Legacy BOM file.**

| Row type | Example | Map to pack? |
|---|---|---|
| **Orderable component** | `1588.1025.A.076` Bracket … Laser Cut; `1588.1025.A.020` Oil Bearing … Bearing Supplier; `1588.1025.A.091` … Generic IGUS | ✅ YES |
| Assembly grouping (no part vendored) | `Scissor lift`, `1588.1055.A.Hand Held Post`, `Sub Assembly`, `1.6.10 Wheel Assembly`, `Welded assembly` | ❌ NO — design artifact |
| "Assembly by Sesame" / "Welded by Sesame" | `1588.1055.A7`, `1588.1055.A1w` | ❌ NO — in-house sub-assembly, not an orderable part |
| `_MIR` duplicate | `1588.1025.A13w_MIR` | ❌ NO — software mirror artifact |
| _MIR-adjacent rows duplicated under it | `1.6.12.1 Flat` (dup of 1.6.11.1) | ❌ NO — skip the whole `_MIR` subtree |

### Detection heuristics (automation)
A row is a **BOM item** if it has:
1. A `PART NUMBER` matching the Sesame pattern `\d{4}\.[\w.]+\.[A-Z0-9._]+` (e.g. `1588.1025.A.076`), **AND**
2. A `VENDOR` **or** `COST CENTER` **or** `STOCK NUMBER` value present

A row is an **artifact** if:
- The description contains `Assembly by`, `Welded by`, `Sub Assembly`, `assembly`, or
- `VENDOR`/`COST CENTER`/`STOCK NUMBER` are all empty (pure design grouping), or
- The part number matches `.{1,40}_MIR` (skip the whole MIR subtree).

### Confidence + review loop
Automation must **flag low-confidence rows** (has a part number but no vendor/cost-centre/stock) for
human review rather than silently including or excluding them. Never auto-commit; always stage for HITL.

## 4. Proposed process

**Step 1 — Ingest (nightly or on-change, dry-run first)**
For each `*.xlsx` in the Legacy BOM folder:
- Parse `PARTS LIST` sheet.
- Deterministically select BOM items using the heuristics in §3.
- Map file → pack: filename → pack task GID (looked up in `build_assembly_packs` by `pack_reference`).
- Produce candidate list: `(sesame_part_no, pack_gid, source_file, confidence)`.

**Step 2 — Cross-check against PARTS CONTROL (read-only)**
Join candidate Sesame part numbers against `parts_control.sesame_pn`:
- **Match** → propose setting `Build Packs reference` = pack GID on that part.
- **No match in parts_control** → the part isn't in the build yet; hold, don't create.
- **Confidence low** → send to review queue, not auto-applied.

**Step 3 — Stage for Giles (HITL) — mandatory**
Export the full proposed GID updates (part task GID, old value, new pack GID, source file) for review.
No Asana write happens without explicit approval.

**Step 4 — Commit (REQUESTS → SAP)**
Per `asana-write-guardrails` Rule 7a: sequential updates with ~350ms pause, logged to
`asana_write_log.jsonl`. Set the field to the **pack task GID**, never text.

**Step 5 — Post-check**
Re-run `pack_association.py --list`; confirm `unassociated` open count drops and the target pack's
parts are now `linked` (by GID).

## 5. Guardrails / non-negotiables

- **Dropbox is read-only** — the Legacy BOM's folder is a source; never write/delete/modify files there.
- **Never auto-push every row** from a Legacy BOM file — artifacts (assembly groupings, `_MIR`,
  in-house welds) must be excluded and the exclusion must be reviewable.
- **No Asana writes without HITL** — the candidate GID list is presented to Giles first.
- **Source of truth remains verified GIDs** — text-name links should be migrated to GIDs, not extended.
- **On-demand sync caveat** — files in this Dropbox may not be fully synced (OneDrive/Dropbox on-demand
  placeholders). The ingest step must check the file is fully downloaded (size stabilises) before parsing,
  and retry/skip files that are still syncing rather than blocking.

## 6. Open questions for Giles

1. Is a single nightly ingest right, or should it run when files change (watch folder)?
2. The Low Profile pack file (and others) may be named `... - Legacy.xlsx` — should Legacy files be
   mapped to the live pack (strip ` - Legacy`), or mapped only to `_revN`/current packs when a suffix exists?
3. When a part number in the Legacy BOM doesn't exist yet in PARTS CONTROL, hold silently or send a
   "missing part" alert?

## 7. What I'd build (scope)

A standalone `legacy_bom_pack_ingest.py` (read-only parse + stage, no Asana writes) + a thin
REQUESTS-driven commit step. Estimated **L** effort. No changes to the BOM pipeline or the sync scripts
are required; `pack_association.py` already provides the post-check.
