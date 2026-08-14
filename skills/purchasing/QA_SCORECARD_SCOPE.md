# Parser QA Scorecard — Scope (2026-08-12)

**Owner:** Hermes (email processing)
**Status:** SCOPED — awaiting Giles go-ahead to build
**DataSource (ground truth):** Google Sheet `1fvoSKnvH0vdM_aZbo2_seY6AU3SZaXABaxRUKSxWVlo` → **`Processed`** tab (gid=1657618603, 353 data rows)

---

## Why

Giles: reinstating the parser review/extraction scorecard OpenClaw used, *improved* based on what we now understand:
1. **Match accuracy** must be measured, not asserted — whole-email → one task is the current ceiling; we need **line-item → subtask cost mapping** (subtasks, descriptions, dependencies).
2. The SQR normalisation fix (2026-08-12) addresses one class of false negative; the scorecard tells us how many more there are.

---

## Ground-truth columns in `Processed` tab

| Col | K | Meaning | Role in scorecard |
|---|---|---|---|
| **E** | Document Type | `update` / `order_confirmation` / `dispatch_note` / `quote` / `invoice` / `other` | Stratify accuracy by type |
| **G** | Matched Task | real task `task_name` OR literal `NOT MATCHED` | **Prediction** (the matcher's output) |
| **K** | DECISION | `A` / `M` / `R` (Giles's human decision) | **Ground-truth label** for whether the proposal was right |

Supporting cols: **L** = Amendment Notes (what Giles actually corrected — the gold answer), **H** = Asana link, **N** = Outcome (`AMENDED` etc.).

---

## Observed baseline (353 rows)

| DocType | A | M | R | matched | NOT MATCHED | Notes |
|---|---|---|---|---|---|---|
| update | 20 | 1 | 103 | 33 | 91 | R mostly noise |
| order_confirmation | 25 | 6 | 37 | 42 | 26 | **highest-value for matching** |
| dispatch_note | 10 | 0 | 62 | 47 | 25 | |
| quote | 10 | 2 | 13 | 16 | 9 | **SQR fix targets this** |
| invoice | 9 | 0 | 16 | 16 | 9 | |
| other | 0 | 1 | 38 | 8 | 31 | mostly noise |

- Approved (A) rows are almost always `matched=Y` → matcher usually correct when it proposes.
- Rejected (R) rows skew `NOT MATCHED` → matcher noise-filtering works for true noise.
- **False negatives** (actionable but `NOT MATCHED`, then A/M): rare but real — e.g. metals4U order-confirmation price detail.
- **False positives** (wrong task matched, then M): e.g. Amazon tools-for-Greg vs actual anchors order.
- **Noise leak**: M#9 auto-reply was matched & proposed — matcher should drop auto-replies.

---

## Scorecard design (improved from OpenClaw version)

### 1. Metrics (per batch of N emails)
For each of the four correctness classes, tally and report:
- **True Positive (TP)** — task matched AND correct (K = A, or M with only minor L edits not changing the task).
- **False Positive (FP)** — task matched but WRONG task (K = M where L changes the task; or R where G had a task but wrong).
- **True Negative (TN)** — NOT MATCHED and correctly not actionable (K = R, content is noise).
- **False Negative (FN)** — NOT MATCHED but SHOULD have matched (K = A/M, or G blank on actionable mail).
- Composite: **precision** = TP/(TP+FP), **recall** = TP/(TP+FN), **F1**, and **task-accuracy** = (TP+TN)/N.

### 2. Stratification
Report precision/recall **by document type** (E) and **by source domain** (D), so we know where to improve.

### 3. Error taxonomy (categorise each FP/FN)
- **normalisation** — SQR/PO spaced-vs-hyphen / case (the 08-12 fix class).
- **project-name-vs-number** — call/email says "St Mary's" not "1594".
- **vendor-name inconsistency** — "Thameside"/"Thameside Eng"/"thameside eng".
- **subtask target** — the right task is a **subtask**, matcher returned parent or missed.
- **description/dependency reference** — task only discoverable via description text or a dependency link.
- **auto-reply/OTP noise** — should be dropped, not matched.

### 4. Human-labelling protocol
Because G alone can't prove *correctness*, each batch is sampled and Giles/JII labels the true task via H. The sheet's existing `A/M/R` + `L` amendment notes provide historical labels without re-labelling from scratch.

### 5. Tooling (to build)
- `qa_scorecard.py` — reads `Processed` tab, computes metrics+stratification, emits a short report (one line per metric + top-10 items to fix).
- `qa_review_batch.py` — pulls the latest un-processed email window, runs 01→03, lets Giles label TP/FP/TN/FN quickly (reuse sheet or a flat table).
- **Rerun cadence:** weekly, after the Friday run; publish scorecard to the purchases Telegram thread.

### 6. Future (line-item → subtask)
Extend 03 to map `line_items[] → matching subtask` (by part_ref / description / dependency) and propose per-line cost. The scorecard's FP/FN taxonomy already codes subtask-target misses so we can track this improving.

---

## Open questions for Giles — RESOLVED (2026-08-12)
1. **Rerun cadence:** WEEKLY improvement-report process (fixed weekly run).
2. **Labels:** REUSE existing `A/M/R + L` amendment notes as ground truth (no new QA column).
3. **Surface insights:** Hermes surfaces insight; Giles will feed back more detail into the amendment notes (L) when rows are Modifed or when learning occurs — so L becomes richer ground truth over time. Scorecard should call out rows where L is thin/empty to prompt that.
4. **Report format:** a WRITTEN HTML file, standalone, ready for review; later to be uploaded onto a portal OS. Design output as a portable self-contained HTML page.

## Delivery notes (resolved scope)
- Weekly job (cron) runs `qa_scorecard.py` against the `Processed` tab → writes a self-contained **HTML report** (inline CSS, no external deps → portal-portable).
- HTML report sections:
  - Summary headline (precision/recall/F1 overall + by doc-type).
  - Error taxonomy tally (normalisation / project-name-vs-number / vendor-inconsistency / subtask-target / description-dependency / auto-reply).
  - **Insight surface**: top-N FN/FP rows with links, prompting Giles to enrich L.
  - Thin-L callout: rows that were A/M but lack amendment detail — invite more note.
- Stored under `SES_Workspace/data/qa/` (with symlink-friendly naming later for portal) — not in chat.
- Next step once built: wire a weekly cron; first report run on the current 353-row history.
