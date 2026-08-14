# Legacy BOM → Build Pack Allocation — Edge Case Log

Living log. Every anomaly the allocation pipeline hits gets recorded here (date, case, how handled).
Seeded from the investigation on 2026-08-11. Forge and future runs append; do not delete rows.

## Known at design time (2026-08-11)

| # | Edge case | Handling |
|---|---|---|
| E1 | **Number spread** — same job appears as title `1581`, accounts `PROJECT NAME` field `580` (leading digit dropped intentionally), and pack files `1580.xxx`. | Match packs on the **file's `pack_reference`** (e.g. `1580.1010.A`). Title token is a human label only; never match on the `PROJECT NAME` field. |
| E2 | **`_MIR` subtree** — software mirror artifact (e.g. `1588.1025.A13w_MIR`) and rows duplicated beneath it. | Exclude the row and its whole `_MIR` subtree. Reviewable exclusion, never silent-commit. |
| E3 | **"Assembly/Welded by Sesame"** — in-house sub-assemblies, not orderable parts (e.g. `1588.1055.A7`, `...A1w`). | Exclude as design artifact. |
| E4 | **Assembly grouping rows** — no part vendored (e.g. `Scissor lift`, `Sub Assembly`, `Welded assembly`). | Exclude as design artifact. |
| E5 | **`pack_reference` → multiple pack tasks** — 2,483 packs across two projects (Drawing Control, Build Schedule). **Confirmed live (Admiralty):** one file resolves to a *Drawing Control* pack (`…ScissorLift_Single.DRAWING`) AND a *Build Schedule* pack (`…ScissorLift_Single.`). | **RESOLVED RULE:** the allocation target is the **Build Schedule** pack; ignore the `.DRAWING`/Drawing Control variant. Confirmed because existing text links equal the Build Schedule pack_reference exactly. Still flag if neither/both project variants are found. |
| E6 | **Duplicate `parts_control` rows** — same part appears 8–16× (seen in asana_write_log). | Target every matching task GID, or dedupe first. Never update just one silently. |
| E7 | **Dropbox on-demand placeholder** — file not fully downloaded (size not stable). | Skip/retry that file; never parse a partial. Flag in run summary. |
| E8 | **Legacy filename suffix** — `... - Legacy.xlsx`. | Decision pending: map to live pack (strip ` - Legacy`) or only to `_revN`. Until decided, flag. |
| E9 | **Part in legacy BOM not yet in `parts_control`** — build hasn't imported it. | Hold; do not create. Decision pending: silent hold vs "missing part" alert. |
| E10 | **`linked_by_ref` mis-keyed rows** — 732 rows hold a `pack_reference` string under `build_assembly_pack_gid`. | Out of scope for this pipeline's first cut; candidate for a separate migration pass (see REVISED-PLAN). Do not extend text links. |
| E11 | **Parse model — CORRECTED by dry-run.** The per-pack handover file is a single hierarchical assembly BOM (`PARTS LIST` sheet, columns ITEM QTY/ITEM/DESCRIPTION/PART NUMBER/…/VENDOR/…/COST CENTER/…). **The filename IS the pack**; every orderable row belongs to it. It does NOT contain "Drawing Pack" separator rows, so `build_lookup_map()` (built for the *consolidated* import file) does **not** apply here. | Parse the `PARTS LIST` sheet directly; assign all orderable rows to the file's pack. Do not reuse `build_lookup_map` for per-pack handover files. |
| E12 | **Low-confidence row** — has a part number but no vendor/cost-centre/stock. | Flag for human review; never auto-include or auto-exclude. |
| E13 | **ISO/DIN fasteners** — screws/washers with catalogue part numbers (`1580.ISO 4017…`, `1580.DIN 933…`) and no vendor/cost/stock. Dry-run excluded these under the no-vendor rule (part of the 29). | **RESOLVED (Giles, 2026-08-11): ignore fasteners** — leave them excluded from pack allocation (stock-catalogue items). No rule change needed; current exclusion is correct. |
| E14 | **Coverage is partial** — dry-run: 74 orderable rows in one pack file, but only ~16 exist as `parts_control` tasks (the rest hold, E9). `parts_control` tracks a coarser set than the legacy BOM. | Expect one handover to update ~15–25 tasks, not hundreds. Not an error. |

## Runtime log (append below)

**2026-08-11 — DRY RUN, Admiralty `1580.1010.A.ScissorLift_Single.xlsx` (read-only, no writes).**
122 rows → 74 orderable / 48 excluded (29 no-vendor incl. fasteners, 11 assembly-weld, 8 `_MIR`).
Pack resolved to Build Schedule GID `1216205136646253`. Would update **16** `parts_control` tasks:
15 `linked_by_ref`→GID migrations + 1 new allocation; 0 already correct; 0 destructive. ~50 orderable
parts held (not in parts_control).

**2026-08-11 — BUILD + VALIDATION (built directly; OpenClaw/Forge retired).** Scripts:
`legacy_bom_pack_ingest.py`, `legacy_bom_handover_watcher.py`, `SKILL.md`; `db_writer` gained
`update_parts_pack_allocation`. Validated: scan is read-only (ops.db `db_write_log` count unchanged);
watcher parse/dedupe/notify verified offline against the 1581 task; commit dual-write verified on a
seeded temp DB with Asana stubbed → all 16 rows became `association_status='linked'` on the correct
pack GID, 16 `asana_write_log` + 16 `db_write_log` rows. **Bug found & fixed by validation (E15).**

**2026-08-12 — FIRST LIVE COMMIT, Admiralty (task 1213862345336388).** Committed all 16 candidates:
Asana `Build Packs reference` set to pack GID `1216205136646253` on all 16 tasks (verified via
`reference_value.gid`; 0 failures); ops.db mirrored to `association_status='linked'` on that GID with
16 `asana_write_log` + 16 `db_write_log` rows. `legacy_bom_handover_log` marked `committed`. Note: the
field is Asana `resource_subtype: reference` — its `display_value` shows the referenced pack task's
NAME (here identical to the old text), so confirm writes via `reference_value.gid`, not `display_value`.

**2026-08-12 — Langley Street (task 1213147826874929): NO CANDIDATES.** Handover folder held 3
button-station packs (`1037.1016.BS.2`, `1037.1016.BS.3`, `1037.1016.X.ButtonStation`) — all resolved
cleanly to Build Schedule GIDs. But `parts_control` for 1037 has only 30 rows, all `1037.1014.*`
(main lift/gate assembly), all `unassociated`. **Zero overlap** with the button-station part numbers →
0 candidates, nothing committed. Correct outcome, not a failure. Two things flagged: (a) the handover
delivered only the button-station sub-packs, not the main `1037.1014.*` assembly BOM (which IS
unassociated and would benefit from its own pack files); (b) surfaced E16–E18 below.

**Edge case E16 — folder naming varies per project.** Admiralty: `…/2.2. Assembly Drawings/Legacy BOM's`.
Langley: `…/Design/2.2. Assembly Drawings/4. Legacy BOMs` (extra `Design` level, `4.` prefix, no
apostrophe). The Dropbox-link resolver (connector `get_shared_link_metadata`) handles this — do NOT
hardcode the folder path; always resolve from the task's shared link.

**Edge case E17 — Dropbox cloud-only placeholders can't hydrate in the Cowork sandbox.** Langley files
were on-demand placeholders; bash/pandas hit `OSError [Errno 22]` and Read rejects binary. The mount
could not materialise them. Not a pipeline flaw — on Giles's machine the files are local. Mitigation:
run the ingest where files are synced, or make the folder available offline first. The DB-driven
cross-check still reached the correct conclusion here without the file bytes.

**Edge case E18 — handover with no matching parts_control tasks.** When a pack's parts aren't tracked
as individual parts_control tasks (e.g. button stations bought as kits), the correct result is 0
candidates (everything held, E9/E14 at 100%). Report it plainly; do not force writes.

**Edge case E15 — partial-column upsert fails on parts_control.** `parts_control.part_name` is
NOT NULL, so `db_writer.upsert_parts_control` with only the changed columns fails the INSERT half of
the UPSERT before ON CONFLICT resolves — and the gateway swallows the error, silently writing nothing.
Fix: added `db_writer.update_parts_pack_allocation()` (targeted UPDATE, no insert). Lesson: never use
the upsert helpers for partial updates of tables with NOT NULL columns.
