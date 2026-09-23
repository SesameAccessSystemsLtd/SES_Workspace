# BOM Tracker — Forward Assessment
**Date:** 2026-09-21 · **Author:** Hermes · **Scope:** 1580 Admiralty House · parts-without-pack ordering · 1595 Winkel mapping failure

---

## 1. Why the tracker is "blind" to the most important work (1580)

### Finding — the tracker's vendor-group detector catches 0 of 15 of 1580's open vendor groups

`build_bom_tracker.py` decides what counts as an "open vendor group" with this test
(lines 364–370):

```python
prefixes = ("laser cut","machining","powder coating","engraving","anodising","finish",
            "nbss","sesame","marine lacquer","cement board","glass","bellows")
... if any(low.startswith(p) for p in prefixes) and re.search(r"\d{3,4}", nm)
```

It only accepts names that **BEGIN** with a treatment word. But 1580's real open vendor
groups are named two other ways the pipeline now emits:

| Actual 1580 open vendor group | Starts with | Caught? |
|---|---|---|
| `Galvanising - 1580 - Admiralty House 2.Entire System BOM` | "galvanising" (not in list) | ❌ |
| `Yellow Zinc Plating - 1580 - …` | "yellow zinc plating" (not in list) | ❌ |
| `ALEXANDER POLLOCK - Engraving - 1580 - …` | "alexander pollock" | ❌ |
| `21667-1580 - Blickle - 1580 - …` | "21667-1580" (PO-prefixed) | ❌ |
| `21665-1580 - IGUS - …` | PO-prefixed | ❌ |
| `21651-1580 - Exact Laser Cut BOM - …` | PO-prefixed | ❌ |
| `Related Fluid - 1580 - …` / `HOLDING JAJO - 1580 - …` | vendor-first | ❌ |

**Result:** 15/15 MISSED → 1580 never enters the "Issued BOMs · active jobs with
outstanding vendor work" section, which today shows only **5 jobs** (1373, 1535, 1569,
1582, 1597). The single largest and newest BOM on the board is invisible at the top of
the page.

### The 1580 timeline (the confusion Giles flagged)

Two BOM eras exist for 1580, and BOTH controls are correctly ticked:

| Task | GID | Created | Completed |
|---|---|---|---|
| `Karol Generate Entire BOM - 1580 Admiralty House` (new) | 1213862345336384 | 2026-03-30 | **2026-09-15 10:01** |
| `Handover Legacy BOM - 1581 Admiralty House` (legacy) | 1213862345336388 | 2026-03-30 | **2026-07-24 15:32** |

**The legacy BOM was closed out on 2026-07-24** — it is genuinely done, not a stale tick.
Proof: its output root `ALL BOMS - 1580 - Admiralty Single Scissor BOM` (created
2026-07-02) is **completed 2026-08-17**, and all its vendor groups are closed.

**The new BOM (imported 2026-09-15) is what's live and open:**
- `ALL BOMS - 1580 - Admiralty House 1.Long Leading BOM` — **OPEN**
- `ALL BOMS - 1580 - Admiralty House 2.Entire System BOM` — **OPEN**
- ~15 open vendor groups under them
- 161 parts_control rows created `2026-09-15 10:04:58`, **not yet mapped to packs**

So: nothing needs un-ticking. The issue is purely that the **tracker isn't surfacing the
new open 1580 work as the priority.**

### Data note (worth a separate flag)
The legacy control is named `Handover Legacy BOM - **1581** Admiralty House` but lives in
the `1580` project and its output is a 1580 root. A job-number mismatch in the task name —
harmless today but it will confuse every job-number-based join.

---

## 2. Parts-without-assembly-pack — ordering + the numbers

### Current behaviour
`no_pack_jobs` (build_bom_tracker.py line 430) sorts by **`(no_pack_count ASC, job ASC)`**:

```python
for job, info in sorted(pack_links.items(), key=lambda kv: (kv[1]["no_pack"], kv[0])):
```

Score of the current render order:
`1366, 1595, 1037, 1582, 1670, 1536, 1373, 1555, 1591, 1575, 1588, **1580**, 1594, …`

**1580 — the newest import — is 12th.** The ordering optimises for *smallest backlog*, not
*most recent / most important*.

### What Giles wants
Order by **when the parts' tasks were created, newest first**, so the most recent BOM
import floats to the top as the priority. The data to do this already exists:
`parts_control.created_at` / `first_seen_at` (1580's new rows are all stamped
`2026-09-15 10:04:58`).

### The 1580 numbers (reconciliation backlog)
- **183 parts_control rows** for 1580; **178 distinct** sesame_pn.
- **19 linked** to the `1580.1010.A.ScissorLift_Single` pack (all from the legacy era, all done).
- **164 unlinked rows / 162 distinct parts** — the new-import backlog.
- Tracker shows `no_pack_parts: 106` for 1580 (after its dedup/short-PN rules).
- **`association_status = 'unassociated'`** for the whole new batch.

Cross-check vs the tracker's own taxonomy (`load_ops_pack_links`):
`resolvable` (pack exists for the code → automatable), `ambiguous` (2+ packs → human
call), `blocked` (no pack carries the code → needs the design PACK column). 1580's new
parts need this split surfaced per-row so Giles knows which the pipeline can fix vs which
need design input.

---

## 3. 1595.1000.F.Winkel.Bearing — why it did not map

### The row
```
sesame_pn  = 1595.1000.F.Winkel.Bearing_(4.053+PL00)   (two rows)
pack_gid   = (empty)      association_status = unassociated
is_completed = 1 (done)   stock_action = ITEM On Premises / On order
order_status = PICKING/checking  ·  On Order - Awaiting CONFIRMATION
```

### Root cause — the part number encodes the WRONG pack code
The mapping runs drawing-BOM → parts_control and derives the candidate pack from the PN's
**first 3 dot-components** (`_pack_code()`), i.e. `job.pack.rev`:

- `1595.1000.F.Winkel.Bearing_(4.053+PL00)` → code **`1595.1000.F`**
- The pack it *actually* belongs to is **`1595.1006.G.GlassPlatform`** → code `1595.1006.G`

`1595.1000.F` has **no pack** in `build_assembly_packs` → the part classifies as
**`blocked`** ("no pack carries this code; the BOM never said which pack the part belongs
to"). No amount of re-running the pipeline can fix it — the pack identity is baked wrong
into the part number.

**Contrast — the mechanism works for a sibling:** `1595.021` (NBSS WinkelBearingShims) IS
mapped to `1595.1006.G.GlassPlatform` (`linked_by_name`). So the pack and the linkage path
are healthy; this specific part's PN is the problem.

### Why it still shows as "not mapped" after completion — the SECOND bug
The tracker's no-pack view does **not filter on completion**. The Winkel row is
`is_completed=1` and has already been picked (`order_status = PICKING/checking`), yet it
still renders under "parts without assembly pack" as unmapped. Two independent defects
compound:
1. the mapping genuinely failed (wrong pack code), and
2. the tracker keeps showing a finished/picked part in the "needs mapping" list,
   so the page implies work remains that is actually done.

---

## 4. What is actually broken (summary)

| # | Defect | Severity | Fixable by |
|---|---|---|---|
| D1 | Vendor-group detector only matches treatment-prefixed names → 1580 + all PO/vendor-first groups missing from Issued BOMs | **HIGH** — hides the priority work | Code (broaden detector) |
| D2 | Issued BOMs not ordered by recency → newest import not at top | HIGH | Code (sort by root/parts created_at) |
| D3 | Parts-without-pack ordered by count, not creation date | MED | Code (sort newest-first) |
| D4 | No-pack view ignores completion → finished/picked parts shown as outstanding | MED | Code (exclude is_completed) |
| D5 | 1595 Winkel PN carries wrong pack code (`1000.F` vs `1006.G`) → permanently `blocked` | LOW (1 part) | Design input (correct the PACK column) |
| D6 | Legacy control mislabelled `1581` inside the `1580` project | LOW (data hygiene) | Asana rename |

---

## 5. Recommended forward plan

### Phase 1 — Make the tracker tell the truth (code, no Asana writes)
1. **Broaden vendor-group detection (D1).** Replace the `startswith(prefix)` test with a
   rule that accepts the pipeline's real naming shapes:
   - PO-prefixed: `^\d{4,6}-\d{3,4}\b` (e.g. `21667-1580 - Blickle`)
   - vendor-first: `^<word> - \d{3,4} - ` (e.g. `Galvanising - 1580 -`)
   - keep the existing treatment-prefix set as one branch
   Exclude the `ALL BOMS -` roots themselves (those are roots, not vendor groups).
   Verify: 1580 appears in Issued BOMs with ~15 open groups.
2. **Order Issued BOMs by recency (D2).** Sort job rows by the newest
   root/parts `created_at` desc, so 1580 (2026-09-15) is first. Add a "NEW" pill for any
   job whose root was created in the last N days.
3. **Order parts-without-pack by task creation date (D3)** desc, and show the created date
   on each row so "1580 is the most recent import" is self-evident.
4. **Exclude completed/picked parts from the no-pack view (D4)** — or add a
   `done but unmapped` sub-list so the anomaly is visible without polluting the to-do list.

### Phase 2 — Close the 1580 intake properly
5. Run the **pack reconciler** for 1580 to split the 162 unlinked parts into
   resolvable / ambiguous / blocked and map the resolvable ones automatically.
6. Surface the **blocked** list (e.g. the Winkel class) with a clear ask: *"these need the
   design PACK column — the part number's pack code doesn't match any pack."* That is the
   only path for D5.

### Phase 3 — Prevent recurrence
7. **Validate PN pack-code at import.** At Stage 5, flag any part whose `_pack_code()`
   resolves to no pack, so `blocked` parts are caught at import, not weeks later.
8. **Add a "most-recent import" banner** to the tracker top (job + date + open-part count),
   so the newest BOM is always the first thing Giles sees.
9. **Fix the 1581/1580 name mismatch** in Asana (D6) — prevents future join confusion.

---

## Appendix — key GIDs
- New 1580 Karol control: `1213862345336384` (completed 2026-09-15)
- Legacy 1580 control: `1213862345336388` (completed 2026-07-24)
- Open roots: `ALL BOMS - 1580 - … 1.Long Leading BOM`, `… 2.Entire System BOM`
- 1580 new parts created: `2026-09-15 10:04:58` (161 rows)
- 1595 Winkel pack (target): `1595.1006.G.GlassPlatform` = gid `1217543695788656`
- Tracker: `skills/os_portal/build_bom_tracker.py` (vendor detect ln 364-370; no-pack sort ln 430)
