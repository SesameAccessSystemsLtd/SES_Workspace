# Manual Pack Mapping — the "false link" issue + recommendation
**Date:** 2026-09-21 · **Author:** Hermes

## The problem (confirmed)

A part can be linked to an assembly pack in **two places**, and only the pipeline keeps them in sync:

| Where | Field | Written by |
|---|---|---|
| Pack link (machine-readable) | `Build Packs reference` custom field `1211660742155293` → `parts_control.build_pack_ref_name` | field set (pipeline OR you, by hand) |
| Pack name line (human/report-readable) | part **description** `notes` — `"{qty} off in Assembly Pack {pack_name} [{gid}]"` | **ONLY the pipeline** (`apply_pack_assign` in `execute_requests.py`) |

**If you (or anyone) map a part by hand in the Asana UI** — drag/set the Build Packs reference field —
only the **field** gets set. The **description never gets the "in Assembly Pack …" line**.

**Consequence:** any report/probe that resolves the pack **from the description text** finds nothing for a
manually-mapped part → it reads as unlinked → a **false link**. The custom field says linked; the
description says nothing.

**Worked example:** `1591.7003.G.013` (mapped to `1591.7003.B.WheelStop-MT_MOD1.`, gid `1217486419062901`).
- Its custom field is set (or will be after the next nightly sync).
- Its description currently contains PN / cost centre / BOM link / mass / volume / Project Task — but **no
  "Assembly Pack" line**.
- Even parts that ARE correctly mapped (e.g. `1591.1016.H1`, `1591.1020.A1.1`) have **no** Assembly Pack
  line in their description — because they were mapped by field, not by the pipeline's dual-write.

## Two separate gaps

### Gap 1 — the resolver isn't in the nightly chain (from the previous turn)
`sync_parts_control.py` (nightly, 01:00, cron `02f75e6f8c82`) reads the field → `build_pack_ref_name`.
But the step that turns `build_pack_ref_name` → `build_assembly_pack_gid` is `resolve_pack_gids()` in
`skills/purchasing/impact_engine.py`, which is **not scheduled anywhere**. So a manually-set field is
picked up as a name but never becomes the GID the tracker reads.

### Gap 2 — the description write-back only happens on the pipeline path
Manual mapping never writes the description line. There is no reconciliation that says "this part now
has a pack link — does its description name the pack? If not, write it."

## The canonical description-write contract (from legacy BOM ingress)

The **legacy BOM ingress skill** (`skills/legacy_bom_pack_allocation/legacy_bom_pack_ingest.py`)
already implements the exact dual-write we need to mirror. Its contract:

| Item | Value |
|---|---|
| Description line | `f"{qty} off in Assembly Pack {pack_reference}"` (e.g. `2 off in Assembly Pack 1037.1014.M.Gate+Support.`) |
| Uses | the pack **reference/name** (NOT the GID) |
| Idempotency check | `f"Assembly Pack {pack_reference}" in notes` (skip if present) |
| Multi-pack | join lines with ` | `; prepend `*` to the task name when 2+ packs |
| Separator before appended block | `"\n---\n"` (only if notes already non-empty) |
| Field write | Build Packs reference (`1211660742155293`) via batch API |
| DB mirror | `db_writer.update_parts_pack_allocation(task_gid, pack_gid, source, checked_at)` |
| Audit | `db_writer.log_asana_write({...})` |

Helpers: `_note_line(qty, pack_name)`, `_already_has_pack_note(notes, pack_reference)`.

**Requirement:** the manual-mapping path must produce the **identical** line and use the **identical**
idempotency key, so a manual mapping and a pipeline mapping are indistinguishable downstream.

## Recommendation (updated)

Reuse the legacy ingress's own helpers — do NOT reinvent the line format. Add a reconcile step that
appends `{qty} off in Assembly Pack {pack_reference}` (via `_note_line` / `_already_has_pack_note` logic)
for any part whose pack link is known but whose description lacks that line. This keeps ONE definition of
the line across pipeline, legacy ingress, and manual mapping.

```
01:00 nightly:
  sync_parts_control.py         → build_pack_ref_name populated
  resolve_pack_gids()           → build_assembly_pack_gid populated   (Gap 1)
  reconcile_pack_descriptions() → append the SAME line legacy ingress writes,
                                  for any part whose description lacks it (Gap 2)
```


### Behaviour of the reconcile step
- For each part with a resolved pack link (`build_assembly_pack_gid` not null) AND
  `build_pack_ref_name` not already present in its description:
  - append `"\n{qty} off in Assembly Pack {build_pack_ref_name}"` (idempotent — skip if the pack name is
    already somewhere in `notes`, so re-runs never double-append).
- **Read the live `notes` first** (per write-guardrail Rule 9) — append, never overwrite.
- **Rate-limit** ~0.35s/call.
- **Log** every write to `data/asana_write_log.jsonl`.
- **Batch/safe:** run as a background job (long batch, per Rule 7c).

### Why nightly (not real-time)
Mapping in Asana is a deliberate human act; a nightly reconcile is the right cadence and matches the sync.
If a same-day turnaround is wanted, add a midday pass (like the token-dashboard option B).

## ⚠️ Governance — this is an Asana WRITE
Writing the description line back to Asana is a **write**. Per the guardrails:
- It must go through the approved path (REQUESTS → SAP/processor) **or** a clearly-scoped, audited job.
- It touches part **descriptions** only (never order/PO tasks; never a QTY field on an order — Rule 7b).
- Needs **Giles's explicit go-ahead** before it is scheduled to run unattended.

## Decision needed from Giles
1. **Do it at all?** (write pack line into descriptions automatically) — recommended yes.
2. **Cadence:** nightly only, or nightly + midday?
3. **Scope:** only parts whose link was set manually since last run, or backfill ALL currently-linked
   parts missing the line (there may be many)?
4. **Delivery:** run inside the nightly chain (silent) or as its own logged job?

## Open question for Giles
Which report(s) actually read the description to resolve the pack? If NO live report reads the
description (they all read the GID), then Gap 2 is cosmetic and only Gap 1 needs fixing. The exact
consumer determines whether the description write-back is required or merely nice-to-have.
