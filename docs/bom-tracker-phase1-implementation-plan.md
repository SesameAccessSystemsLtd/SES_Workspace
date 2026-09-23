# BOM Tracker — Phase 1 Implementation Plan (for pro-model agent)

**Target file:** `C:\Users\GilesHugo\SES_Workspace\skills\os_portal\build_bom_tracker.py`
**Repo root / workdir:** `C:\Users\GilesHugo\SES_Workspace`
**Venv python:** `C:\Users\GilesHugo\SES_Workspace\.venv\Scripts\python.exe`
**Regen command:** `cd C:\Users\GilesHugo\SES_Workspace && .\.venv\Scripts\python.exe skills\os_portal\build_bom_tracker.py`
**Output:** `data/os_status/bom-tracker.html` (+ a dated copy)

**Scope:** read-only code change to ONE file. NO Asana writes, NO ops.db writes, NO `--commit`.
Fix the four defects D1–D4 below. Rebuild the page. Report a before/after diff summary.

---

## Background (why these fixes)

The tracker's "Issued BOMs · active jobs with outstanding vendor work" section is
reverse-derived from open vendor groups, but the detector only accepts names that START
with a treatment word. Verified 2026-09-21 on **1580 Admiralty House**: it has 15 open
vendor groups and **0/15 were caught** → the job is entirely missing from the page while 5
smaller jobs (all with `Powder Coating - / Laser Cut - / Sesame - / Anodising -` names)
show. The pipeline now emits vendor groups in two other shapes the test misses.

Two more defects: Issue BOM rows aren't ordered by recency, and the parts-without-pack list
(a) is ordered by backlog size not creation date and (b) shows completed/picked parts as
if still outstanding.

---

## D1 — Broaden the vendor-group detector (HIGH)

**Location:** `build_dataset()`, the `vendor_open` loop; the `prefixes` tuple + the
`startswith` test (currently around lines 364–370).

Current logic:
```python
prefixes = ("laser cut","machining","powder coating","engraving","anodising","finish",
            "nbss","sesame","marine lacquer","cement board","glass","bellows")
for t in build_tasks:
    if t.get("completed"): continue
    nm = t.get("name") or ""; low = nm.strip().lower()
    if not (any(low.startswith(p) for p in prefixes) and re.search(r"\d{3,4}", nm)):
        continue
    ...
```

**Change:** accept a name as a vendor group if ANY of these is true:
1. it starts with one of the existing treatment prefixes (keep the current branch), OR
2. **PO-prefixed:** `re.match(r"^\d{4,6}-\d{3,4}\s*[-:]", nm)` (e.g. `21667-1580 - Blickle - …`), OR
3. **vendor-first:** `re.match(r"^[A-Za-z][A-Za-z0-9&/\.' ]{2,30}\s*-\s*\d{3,4}\s*[-: ]", nm)`
   (e.g. `Galvanising - 1580 - …`, `ALEXANDER POLLOCK - Engraving - 1580 - …`).

**Must EXCLUDE the `ALL BOMS - ` roots** (they are roots, not vendor groups):
- explicitly skip any name where `nm.strip().upper().startswith("ALL BOMS")`.

**Keep** the existing `re.search(r"\d{3,4}", nm)` guard as a final sanity check.

> ⚠️ Be careful the vendor-first regex does not swallow the ALL BOMS roots or the anchor
> cost-centre lines. Test against the live 1580 names before trusting it. Add a small
> helper `is_vendor_group_name(nm) -> bool` so the rule is unit-testable and the loop stays
> readable.

**Acceptance:** after rebuild, the Issued BOMs section includes job **1580** with ~15 open
vendor groups (`Galvanising - 1580 …`, `Yellow Zinc Plating - 1580 …`,
`21667-1580 - Blickle …`, `ALEXANDER POLLOCK - Engraving - 1580 …`, etc.). The 5 jobs that
already showed (1373, 1535, 1569, 1582, 1597) must STILL show.

---

## D2 — Order Issued BOMs by recency (HIGH)

**Location:** `build_dataset()`, the `issued_rows_vg` build loop that sorts `by_job_vg` keys
(currently `sorted(by_job_vg.keys(), key=lambda j: (j or "ZZ"))`).

**Change:**
- For each job row, compute a `recency` timestamp = the newest of:
  (a) the job's open ALL BOMS root `created_at`, and
  (b) the newest `parts_control.created_at` for that job.
  (Use the `build_tasks` root objects which already carry `created_at`; for parts, query a
  read-only copy of ops.db — see DB note below — `SELECT MAX(created_at) FROM parts_control
  WHERE project_code=? OR sesame_pn LIKE ?||'.%'`.)
- Sort `issued_rows_vg` by `recency` DESC (newest first). Jobs with no timestamp sort last.
- Add fields to each row dict: `recency` (ISO date str) and `is_new` (True if the root was
  created within the last 14 days).
- In `render()`, the Issued section header/card should show the recency date and a **"NEW"**
  pill when `is_new`. Keep the existing per-job card layout; only add the date + pill.

**Acceptance:** **1580** (root created 2026-09-15) sorts to the TOP of Issued BOMs and shows
a NEW pill.

---

## D3 — Order parts-without-pack by task creation date (MED)

**Location:** `build_dataset()`, the `no_pack_jobs` loop sort (currently
`sorted(pack_links.items(), key=lambda kv: (kv[1]["no_pack"], kv[0]))`).

**Change:**
- Compute each job's newest unmapped-part creation date from ops.db
  (`SELECT MAX(created_at) FROM parts_control WHERE project_code=? OR sesame_pn LIKE ?||'.%'`
  — restrict to rows where `build_assembly_pack_gid` is null/empty if easy).
- Sort `no_pack_jobs` by that date **DESC** (newest import first), then by job number.
- Add `newest_created` (date str) to each `no_pack_jobs` entry.
- In `render()`, the parts-without-pack table's rows must show the created date column and
  read newest-first.

**Acceptance:** 1580 (parts created 2026-09-15) is at/near the TOP of the parts-without-pack
list (currently 12th).

---

## D4 — Exclude completed/picked parts from the no-pack view (MED)

**Location:** `load_ops_pack_links()` — the per-job aggregation.

**Change:** when counting `no_pack`, EXCLUDE rows that are already done/satisfied:
- skip `parts_control` rows where `is_completed = 1`, **and/or**
- `stock_action` indicates present/picked/done (`KITTED`, `ITEM On  Premises`,
  `IN STOCK`, `INHOUSE`, `PLEASE PICK`, `PICKING`, `SUBCON TASKS`).
Reason (worked example): `1595.1000.F.Winkel.Bearing_(4.053+PL00)` is `is_completed=1` and
`order_status='PICKING / checking'` yet still renders as unmapped/outstanding.

**Nuance:** keep a SEPARATE count `done_unmapped` per job so the anomaly is still *visible*
(a finished part whose pack link is genuinely missing is worth showing), but it must NOT
inflate the "needs mapping" to-do count. Render the `done_unmapped` count in muted text.

**Acceptance:** the Winkel part no longer counts in 1580's "parts needing reconciliation"
total; 1580's actionable no-pack count reflects only genuinely-open parts.

---

## DB access rule (non-negotiable)

- **Read ops.db READ-ONLY.** Use `sqlite3.connect("file:...?mode=ro", uri=True)` against
  `C:\Users\GilesHugo\SES_Workspace\db\ops.db`. Do NOT write, UPDATE, or DELETE anything.
- Do NOT touch Asana, REQUESTS, or run any `--commit`/`--push`/`--sync`.
- If you need to exercise any DB write for a test, use a TEMP copy — never the live DB.

---

## Build & verify steps (do all of these)

1. `py_compile` the edited `build_bom_tracker.py` — must pass.
2. Run the builder: `cd C:\Users\GilesHugo\SES_Workspace && .\.venv\Scripts\python.exe skills\os_portal\build_bom_tracker.py`
3. Confirm exit 0 and that `data/os_status/bom-tracker.html` was rewritten (check mtime).
4. **Assert in the output HTML:**
   - `1580` appears in the Issued BOMs section (grep the section, not the whole file).
   - The Issued section job count went from 5 → higher, and 1580's card has a NEW pill.
   - 1580 appears near the TOP of the parts-without-pack table.
   - The `1595.1000.F.Winkel.Bearing` part is NOT counted in 1580's actionable no-pack total.
5. Capture a **before/after** summary: Issued-BOM job list before vs after; 1580's position
   in the parts-without-pack list before vs after; the 1580 no-pack count before vs after.

## Guardrails

- Change ONLY `build_bom_tracker.py`. Do not modify other builders or shared helpers, except
  if a helper is genuinely required — then keep it local to this file.
- If any fix cannot be completed cleanly, **revert that file to known-good**
  (`git checkout -- skills/os_portal/build_bom_tracker.py` if it is tracked; else restore
  from `__pycache__`? no — copy the file to a `.bak` BEFORE editing) and report BLOCKED.
- Take a `.bak` copy of the file before editing: `cp build_bom_tracker.py build_bom_tracker.py.bak-20260921`.
- Do NOT commit anything. Leave the working tree with the edited file + rebuilt HTML.

## Final report (return this)

- Files changed (+ `.bak` created).
- py_compile result.
- Regen exit code + output mtime.
- Before/after: Issued-BOM job list; 1580 no-pack count; 1580 rank in parts-without-pack.
- Any fix that was BLOCKED or reverted, and why.
