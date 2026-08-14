# Golden Index — Plumbing Assessment & Staging

**Question you asked:** do we need to reorganise the plumbing, and how do we push
assembly packs + Sesame part numbers into the KAN records?

**Short answer:** yes, but surgically. The *data* and its *grain* need to change;
the Stage 2 matcher and its column contract can stay almost exactly as they are.
The assembly-pack and Sesame-PN work is a **separate concern** from stock checking
and should not touch the matching path at all.

---

## The two concerns (kept strictly separate)

**Concern A — Stock check (the Golden Index's actual job).**
A BOM comes in carrying a stock value. That value is matched against the Golden
Index to pull the KAN, and the KAN identifies the rest for the stock check.
Assembly packs and Sesame PNs are irrelevant here — confirmed.

**Concern B — Sesame / Asana task update (a different job).**
Push each KAN's build-pack code(s) and Sesame part number(s) onto the Asana stock
task, where the build pack becomes a **groupable dropdown** (select `1005.A` → see
every part on that pack). This never feeds the stock check.

Mixing these two is what made the old index wrong. The new design keeps them apart.

---

## Do we need to reorganise the plumbing? — Yes, in one place only

### What was wrong (old `golden_index` table)
The existing table is keyed by `sesame_part_no`, one row per part, with no vendor
stock number and no pack columns. It encodes the *part* as the identity. But your
flow is **stock value → KAN → identity**, so the identity is the **KAN**, and the
match key is the **vendor stock number**. The old grain can't express that. You've
already said the previous table was incorrect and can be discarded — agreed, it
should be.

### What changes (data + grain)
The authoritative Golden Index becomes **KAN-grain**: one KAN → one vendor stock
number, plus (for Concern B only) its packs and Sesame PNs. This is what the Rev6
build produces (185 clean KANs, all conflicts resolved).

### What stays (the matcher — no code rewrite)
Stage 2 (`02_golden_index_match.py`) already matches a BOM string against a
`Stock_Number` column and returns `Mapped_KAN`. That is *exactly* the flow you
described. So instead of rewriting it, we feed it a **derived lookup view** in the
same column contract it already reads:

```
Stock_Number, Mapped_KAN, Asana_URL, Confidence, Match_Type, Asana_TYPE_OF_STOCK
```

The only real change is *what* `Stock_Number` now means (vendor stock number, not
CAD PN) and *where the rows come from* (the KAN-grain master, deduped). No new
matcher logic required. `seed_golden_index.py` reads the same columns, so it also
keeps working if you prefer the DB path.

**Net:** reorganise the store and the grain; leave the pipeline contract intact.

---

## Recommended architecture

```
          Rev6 build (resolved, KAN-grain)          <- source of truth
                     |
        golden_index_master.json  (authoritative)
                     |
        ┌────────────┴───────────────┐
   CONCERN A                     CONCERN B
   stock check                  sesame task update
        |                             |
 golden_index_rev6_lookup.csv   asana_sesame_task_updates.csv
 (Stock_Number -> KAN)          (KAN -> packs + Sesame PNs -> Asana)
        |                             |
 Stage 2 matcher (unchanged)    Asana writer (SAP / staged)
        |
 golden_index_rev6 table (ops.db, optional convenience store)
```

Storage mechanism (SQL table vs JSON vs CSV) is interchangeable — the master JSON
is the source of truth and every view is regenerated from it deterministically by
`stage_golden_index.py`. You are not locked into the DB.

---

## Concern B — pushing packs + Sesame PNs to Asana

### Build pack = a dropdown value, not a GID
Correct — there is no single GID for a pack like `1005.A`; it's a **string label**
that groups parts. In Asana this should be a **multi-select dropdown (multi_enum)
custom field** whose options are the distinct pack codes. Tag each part/KAN task
with its pack(s); then filtering/grouping by that field shows every part on a pack.
A single-select enum won't do — a KAN can sit on several packs (e.g. KAN-1014 →
`1003 | 1003.A | 1003.B`). There are **77 distinct pack codes** across the set,
which become the dropdown's option list.

### New fields/columns needed
| Where | Field | Type | Why |
|---|---|---|---|
| Asana (stock task) | Build Pack | **multi-select dropdown** | Group parts by pack; 77 options |
| Asana (stock task) | KAN Number | text | Clean `KAN-###` (today it's free-text in `kan_number`) |
| Asana (stock task) | Vendor Stock Number | text | The BOM match key (maps to existing `vendor_part_number`) |
| Asana (stock task) | Sesame Part No | text (exists) | Already registered; may hold multiple → keep delimited |
| ops.db | `golden_index_rev6` (new) | table | KAN-grain store — **created, see below** |

`Build Packs reference` already exists in the field registry but is a *reference*
type (wants a GID). For this use it should be a **new multi_enum field**, not that
one — I'd leave `Build Packs reference` alone.

### Linkage status (staged, needs review before any write)
Each KAN was best-effort linked to its existing KANBAN Asana task:
**69 of 185 linked automatically**, 116 unlinked (the KANBAN `kan_number` field is
currently messy free-text, so many can't be matched by rule). The staging file
flags every unlinked KAN as `UNLINKED - needs manual match`. **No Asana write
should happen until those are resolved.**

### Who executes — recommendation
Route the push through the **Hermes Asana agent (SAP)**, not ad-hoc writes. SAP
already owns the `asana_write_log`, the field registry, and the approval gate that
your DATAMAP governance requires. Plan: this staging file → SAP creates the
multi_enum field and applies values → results land in `asana_write_log`. I can
prepare the exact field-creation + write payloads for SAP when you're ready.

---

## What I've done in this pass

- Built the KAN-grain master (185 resolved KANs) — source of truth.
- Generated `golden_index_rev6_lookup.csv` — matcher-ready, **0 ambiguous
  stock→KAN** (safe for BOM matching).
- Generated `asana_sesame_task_updates.csv` — Concern-B staging, packs as dropdown
  values + Sesame PNs + KANBAN links, all `Review_Status = PENDING`.
- Loaded `golden_index_rev6` into `ops.db` (**additive; old `golden_index` left at
  0 rows, untouched**). Verified the stock-check flow: `NBSS.011 → KAN-329`,
  `690-3292 → KAN-1350`, etc.; misses fall through to ORDER.

### Reversible
`python stage_golden_index.py --rollback` drops `golden_index_rev6`. Nothing else
in ops.db was modified.

---

## Decisions still yours

1. **Adopt the derived lookup as the Stage 2 source?** (Recommended — no matcher
   rewrite.) Or point Stage 2 at the DB table instead.
2. **Retire the old `golden_index` table** (drop it) now that Rev6 supersedes it?
3. **Concern B go-ahead:** shall I prepare the SAP field-creation + write payloads
   for the 69 linked KANs, and a worklist to resolve the 116 unlinked ones?
