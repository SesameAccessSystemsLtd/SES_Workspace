# Part Number Edge Cases — Drawing Pipeline

Documented from Jenners (1575) validation, 2026-05-08.

---

## Rule 1 — Leading noise / duplicate job prefix
**Observed:** `11575.1005.B.059`
**Cause:** Extra digit captured before the job prefix.
**Fix:** Find first occurrence of `{job_prefix}.` in the string; discard everything before it.
**Result:** `1575.1005.B.059`

## Rule 2 — Multiple part numbers merged into one cell
**Observed:** Cell value like `1575.6000.A.0551575.6000.A.056`
**Cause:** pdfplumber collapses adjacent cells when columns merge across rows.
**Fix:** Split on `(?={job_prefix}\.)` when more than one job prefix found in a single value.
**Result:** Two separate rows emitted for each PN.

## Rule 3 — Underscore suffix is noise
**Observed:** `1575.1000.B.30x30_M_M8_70daRN`
**Cause:** Additional specification data runs on from description column.
**Fix:** Truncate at first `_`.
**Result:** `1575.1000.B.30x30`

## Rule 4 — Truncate to 4 dot-components max
**Observed:** `1575.6000.A.055.01P`, `1575.1006.A1w.Bellows.TapeSwitch`
**Cause:** Sub-item counter (.01) and descriptive suffixes bleed in as extra dot components.
**Fix:** Keep only the first 4 dot-separated components. Anything after the 4th dot is noise.
**Result:** `1575.6000.A.055`, `1575.1006.A1w.Bellows`
**Note:** Rule 5 then handles `A1w` → `A`.

## Rule 5 — Description letter bleed into item component
**Observed:** `1575.1005.B.019A`, `1575.1008.N1SL`, `1575.1005.B.102S`
**Cause:** First letter(s) of description cell captured as part of part number.
**Fix:** Strip trailing UPPERCASE letters that follow digits in the last component: `re.sub(r'(\d+)[A-Z]+$', r'\1', last_component)`
**Result:** `1575.1005.B.019`, `1575.1008.N1`, `1575.1005.B.102`
**Important:** Only strip uppercase letters. Lowercase `w` suffix (e.g. `B8w`, `44w`) is a valid welded-assembly designator present in PC sesame_pn — must be preserved for direct matching.

## Rule 6 — Leading-zero normalisation on item component
**Observed:** `1575.1016.1.3` extracted vs `1575.1016.1.003` in Asana
**Cause:** Drawing shows item `3`, Asana stores `003`.
**Fix:** When last component is purely numeric, zero-pad to 3 digits for matching.
**Result:** `1575.1016.1.003`

## Rule 7 — NBSS parts with leading quantity digit
**Observed:** `2 1575.132`
**Cause:** Quantity from adjacent cell bleeds into the part number cell.
**Format:** NBSS parts are always `{job_prefix}.{3-digit-ref}` — two components only.
**Fix:** Strip leading `\d+\s+` before the job prefix.
**Result:** `1575.132`

## Rule 8 — Revision variant stripping (progressive fallback)
**Observed:** `1575.1006.A1w.Bellows` (after Rule 4) → `1575.1006.A.Bellows` in Asana
**Cause:** `A1w` is a welded/mirrored sub-revision of pack `A`.
**Fix (progressive):** Strip trailing `w`/`_MIR`, then strip trailing digits from revision component.
`A1w` → strip `w` → `A1` → strip digits → `A`
**Result:** `1575.1006.A.Bellows`

## Rule 9 — Descriptive item names mismatched between drawing and Asana
**Observed:** `1575.1000.W.Latch` extracted vs `1575.1000.W.Lock` in Asana
**Cause:** Drawing uses a different label for the same physical part.
**Fix:** Cannot be auto-resolved by normalization alone. Add to `pn_aliases.json`.
**Status:** MANUAL — add alias entries as discovered.

---

## Edge Case 10 — Late-Added Drawing Task (post-run addition to Asana)

**Observed:** Drawing pipeline ran for job 1670. Task `1670.1055.A.Hand Held` did not exist
in the Asana Build Schedule project at run time. 6 parts (`1670.1055.A.004/.030/.031/.032/.037/A17`)
had `build_assembly_pack_gid = NULL` and were never written to Asana.

**Root cause:** Stage 1 scans the **Build Schedule** project only. A task that exists solely
in **Drawing Control** (project GID `1142754335688166`) is invisble to Stage 1 — no error is
raised, the task is simply absent from results.

**Detection:** After every Stage 1 run, `data/{job}_missing_packs.csv` is written. It lists
every pack prefix derived from `parts_control` that has no registered row in
`build_assembly_packs`. Review this file before proceeding to Stage 2.

**Recovery — step by step:**

```bash
# 1. Find the Asana task GID (last number in the task URL).
#    e.g. https://app.asana.com/.../task/1215575033042714 → GID = 1215575033042714

# 2. Register the task (works for Drawing Control OR Build Schedule tasks):
python scripts/01_populate_assembly_packs.py --job-prefix 1670 --task-gid 1215575033042714

#    Output tells you which project the task belongs to:
#    "drawing_control"  → Stage 1 normal scan would have missed this — correct recovery
#    "build_schedule"   → Stage 1 would find it on next normal scan too
#    "both"             → multi-homed; normal scan would have found it on next run

# 3. Re-extract drawing BOMs (picks up new drawing's PDF):
python run_drawing_pipeline.py --job-prefix 1670 --stop-after 2

# 4. Re-map with reset (clears and re-maps all pack GIDs for the job):
python run_drawing_pipeline.py --job-prefix 1670 --reset --stop-after 3

#    Dry-run first is strongly recommended:
python scripts/02_map_parts_to_packs.py --job-prefix 1670 --reset --dry-run
#    Confirm the 1670.1055.A.* parts appear as "matched", then run live.

# 5. Write to Asana (Stage 4 dedup skips already-written parts):
python scripts/03_write_to_asana.py --job-prefix 1670 --dry-run
#    Confirm ~6 pending parts, then run live:
python scripts/03_write_to_asana.py --job-prefix 1670
```

**Why Stage 4 re-run is safe:** `03_write_to_asana.py` loads the write log and skips any
`asana_task_gid` already recorded with `result=ok`. Previously written parts are never
double-written. Only newly matched parts (those added by the recovery run) are sent to Asana.

**Project disambiguation note:** When given a Drawing Control task URL by a user, always
extract the GID (last segment of the URL) and pass it via `--task-gid`. Do not assume the
task is also in Build Schedule without checking — that assumption is what caused this incident.

---

## Manual Alias Override File
`workspace/skills/drawing_pipeline/pn_aliases.json`
Format: `{"extracted_pn": "canonical_sesame_pn"}`

```json
{
  "1575.1000.W.Latch": "1575.1000.W.Lock"
}
```

---

## Application Order
Rules 3, 4, 6, 8 are fallbacks — the base form (Rule 1 + whitespace collapse) is
always tried first as it directly matches many PC sesame_pns that contain
underscores (`_M_M8_70daN`), mirror suffixes (`_MIR`), and `w`-suffix welded PNs.

Per-cell processing:
1. Rule 2: split multi-PN cell → yields 1+ single PNs
2. Rule 1: strip leading noise (per-PN)

Per-PN candidate chain (highest priority first):
1. Base: Rule 1 + whitespace collapse only
2. + Rule 5 (strip uppercase bleed from last component)
3. + Rule 4 (truncate to 4 components, for 5+ component PNs)
4. + Rule 5 on Rule 4 result
5. + Rule 3 (strip at underscore, for underscore-noisy PNs)
6. + Rule 5 on Rule 3 result
7. + Rule 4 on Rule 3 result
For each of the above: also try Rule 8 variants and Rule 6 (zero-pad) variants.
Rule 9 (alias lookup) applied to every candidate.
