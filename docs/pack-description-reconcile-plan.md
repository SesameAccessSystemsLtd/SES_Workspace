# Pack-Description Reconcile — Implementation Plan (pro-model agent)

**Goal:** any part that is linked to an assembly pack (by ANY path — pipeline, legacy ingress, or
manual Asana edit) must end up with the canonical description line written into its Asana task, so no
report sees a "false link". Runs after the nightly sync AND a midday pass.

**Repo / workdir:** `C:\Users\GilesHugo\SES_Workspace`
**Venv python:** `C:\Users\GilesHugo\SES_Workspace\.venv\Scripts\python.exe`
**New script:** `C:\Users\GilesHugo\SES_Workspace\skills\purchasing\reconcile_pack_descriptions.py`

---

## 0. The canonical contract (do NOT invent a format)

From `skills/legacy_bom_pack_allocation/legacy_bom_pack_ingest.py` — reuse this EXACTLY:

- Description line: `f"{qty} off in Assembly Pack {pack_reference}"`
  (e.g. `2 off in Assembly Pack 1037.1014.M.Gate+Support.`)
  - `qty` = `parts_control.order_qty`; if falsy, the line is `"in Assembly Pack {pack_reference}"`.
    (Legacy `_note_line` uses `"{q} off in Assembly Pack {pack_name}"` where `q=(qty or "").strip()`;
    mirror that behaviour so a blank qty yields `" off in Assembly Pack …"` — prefer to SPECIAL-CASE
    a missing qty to omit "off" cleanly, but keep the algorithm identical to legacy. Document whichever
    you pick.)
  - `pack_reference` = the pack NAME (e.g. `1591.7003.B.WheelStop-MT_MOD1.`), NOT the GID.
- **Idempotency key:** skip the write if `f"Assembly Pack {pack_reference}"` is already a substring
  of the task's `notes`. Reuse `_already_has_pack_note(notes, pack_reference)` logic.
- **Separator** before an appended block: `"\n---\n"` (only if notes already non-empty).
- **Multi-pack:** if a part maps to 2+ packs, append one line per pack, joined with `" | "`, and
  prepend `*` to the task name (only if not already starting with `*`). (For this reconcile, most
  parts are single-pack; still handle multi-pack to match legacy.)
- **Never overwrite `notes`** — read live, append only (guardrail Rule 9).

**Reuse, don't copy-paste blindly:** the helpers `_note_line` and `_already_has_pack_note` live in
`legacy_bom_pack_ingest.py`. You may import them (if import-safe) OR replicate them verbatim with a
comment pointing at the source. Do NOT change their output string.

---

## 1. Source of truth for the pack name (IMPORTANT)

The pack name must be resolvable from EITHER:
- `parts_control.build_pack_ref_name` (1,478 rows have it), OR
- **derived from `parts_control.build_assembly_pack_gid`** by joining
  `build_assembly_packs.asana_task_gid` → `pack_reference` (190 linked rows have a GID but NO
  ref_name — e.g. project 1580). Prefer `build_pack_ref_name` when present; else join to get the
  name from the GID.

Query shape:
```sql
SELECT p.asana_task_gid, p.sesame_pn, p.order_qty, p.project_code,
       COALESCE(NULLIF(p.build_pack_ref_name,''), b.pack_reference) AS pack_reference
FROM parts_control p
LEFT JOIN build_assembly_packs b ON b.asana_task_gid = p.build_assembly_pack_gid
WHERE p.build_assembly_pack_gid IS NOT NULL AND p.build_assembly_pack_gid <> ''
  AND COALESCE(NULLIF(p.build_pack_ref_name,''), b.pack_reference) IS NOT NULL
```

## 2. Scope filter (Giles's decision)

- **Include:** parts created/linked within the **last ~31 days**, PLUS any newly-mapped parts.
- Use `parts_control.created_at >= date('now','-31 days')` as the window (the mapping itself is
  detected by "gid set AND description lacks the line" — the description check is the real filter).
- Do NOT restrict to a hard job list; let the description-missing check decide. The 31-day window
  just bounds the batch (measured: **234** candidates by created_at; only those whose description
  lacks the line need a write — sampling showed most recent ones ALREADY have it, so the real batch
  is small).

## 3. Algorithm (per run — READ-ONLY against DB, WRITES only Asana descriptions + DB audit)

1. Connect ops.db **READ-ONLY** (`file:...?mode=ro`).
2. Select candidate rows (query above) filtered to `created_at >= date('now','-31 days')`.
   Also allow a `--all` flag to ignore the window (for a one-off wider backfill if approved).
3. For each candidate (group by `asana_task_gid`; a task may appear once):
   a. `GET /tasks/{gid}?opt_fields=name,notes` (rate-limit ~0.35 s).
   b. Compute the line from the resolved `pack_reference` + `order_qty`.
   c. If `_already_has_pack_note(notes, pack_reference)` → **skip** (no write).
   d. Else `PUT /tasks/{gid}` `{"data":{"notes": notes + separator + line}}`.
      - Handle multi-pack if the same task has 2+ pack refs.
   e. On success → `db_writer.log_asana_write({...})` (action_type `update_description`,
      value_applied = the line) + bump `parts_control.association_checked_at`.
   f. On 404 → skip + report (phantom GID, never guess).
4. Print a one-line summary: `checked=N wrote=M skipped=K failed=F`.
5. `--dry-run` prints the would-be writes (task gid, pn, line) WITHOUT any PUT — run this FIRST.

## 4. Cadence wiring (Giles: nightly + midday)

- **Nightly:** append this reconcile to the nightly chain so it runs at 01:00 after the resolver.
  Preferred: add it to `skills/sql_db/scripts/sync_all_mirrors.py`'s `SYNC_SCRIPTS` list OR create a
  thin wrapper + cron at 01:30. **Also add `resolve_pack_gids` (impact_engine) to the nightly chain**
  — that is Gap 1 (the name→GID resolver currently never runs on schedule).
- **Midday:** a second cron at ~13:00 weekdays running the same reconcile (so a morning manual
  mapping is visible by afternoon).
- Create BOTH crons as `no_agent` jobs pointing at a wrapper in `~/.hermes/scripts/` (bare filename,
  NEVER an absolute path — the 0xC000013A trap). Watchdog: silent on success, report only on error.

## 5. Guardrails (non-negotiable)

- This WRITES Asana task descriptions. It is description-only — NEVER touch name/fields/orders,
  NEVER write a QTY onto an order/PO task (Rule 7b).
- Read live `notes` before appending; never overwrite existing content (Rule 9).
- Rate-limit ~0.35 s/call; log every write to `data/asana_write_log.jsonl` (Rule 5).
- Run as a background batch if the set is large (Rule 7c).
- Idempotent: re-running must NOT double-append (the substring check guarantees this).
- If the batch exceeds ~150 writes in one run, SPLIT or run in background with a log redirect.

## 6. Build & verify steps

1. `python -m py_compile` the new script → PASS.
2. Run `--dry-run` → show the would-be writes; confirm `1591.7003.G.013` (manual example) appears
   IFF it is now linked with a resolvable pack name. Report the count.
3. Confirm idempotency: run `--dry-run` twice; the second must show 0 new (once the first real run
   has written them).
4. On the FIRST real run, cap to a small batch (e.g. `--limit 10`) and verify one task on live Asana
   (`GET /tasks/{gid}` → the line is present exactly once) before a full run.
5. Do NOT run the full real write unattended as part of this build unless Giles confirms — build +
   dry-run + wire the crons, then report and let Giles say go.

## 7. Final report

- Files changed / created.
- dry-run count: candidates, would-write, already-have.
- Whether `1591.7003.G.013` is in scope and what line would be written.
- Cron jobs created (ids + schedule).
- Any BLOCKED / ambiguous item.

---

## Reference — key facts
- Build Packs reference custom field GID: `1211660742155293`
- Legacy ingress line/helpers: `legacy_bom_pack_allocation/legacy_bom_pack_ingest.py`
  (`_note_line`, `_already_has_pack_note`, `commit()` ~line 400–517)
- `db_writer.log_asana_write({...})` for audit
- Feasibility measured 2026-09-21: linked parts with ref/serviceable name ≈ 1,478; within-31-day
  candidates ≈ 234; sample of 40 recent-linked parts ALL already have the line → real batch is small
  (mostly manual one-offs like `1591.7003.G.013`).
