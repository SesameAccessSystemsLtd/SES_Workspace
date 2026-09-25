# Supplier-chase capability — built vs gap (assessment)

**Date:** 2026-09-24  ·  **For:** Giles  ·  **Status:** read-only assessment, no writes
**Trigger:** "all of those orders are received and no longer in Project Order Numbers" — Rule A high recall, broken precision.

---

## 1. What is BUILT and LIVE (2026-09-23)

| Module | Role | Cron |
|---|---|---|
| `chase_fetch.py` | Shared Graph pull (Inbox + Sent + categories + conversationId) → ops.db | `e5afe528769c` hourly |
| `chase_rules.py` | Rules A / B / C | — |
| `morning_chase_report.py` | 07:00 report | `50cd0e9d1887` daily → Personal :4 |

Schema: `email_touchpoints`, `email_threads`, `chase_fetch_state` (all additive). ~2,605 touchpoints / 1,485 threads.

**What works well:** outbound reply-detection (Rule B), portal-ref capture (Rule C), and **recipient resolution** (sent-thread → supplier_contacts → portal). Those are solid.

---

## 2. THE CORE BUG — Rule A's live-gate is wrong

**Giles's report confirmed live:** of the **12** orders Rule A flagged, **only 3 are genuinely chaseable.** Live probe:

| Flagged | Reality |
|---|---|
| 21031 Interfluid | completed=True, **not in Order Numbers** (moved to Invoices) — ALREADY RECEIVED |
| 21061, 21086, 21135, 21223 | task **deleted (404/403)** |
| 21139 Amazon, 21559, Romans | completed=True, **not in Order Numbers** — ALREADY RECEIVED |
| 21245 Cromwell | completed=True, live="Partial Delivery" |
| 21646 Maytec, 21650 Romans, 21667 Blickle | **genuinely open + in Order Numbers + Awaiting CONFIRMATION** ✅ (1–3 days old) |

**Root cause:** Rule A's gate (`overdue_order_chase.py::fetch_overdue`) drops a task only when the live **`ORDER Status` custom field** is terminal. But orders get received/closed **without the status field being updated** — the task is marked **complete** and **removed from the Order Numbers project** (→ "Invoices" or deleted). The status field still reads "Awaiting CONFIRMATION", so the gate keeps it.

**The gate checks the wrong signal.** It must key on:
1. **`completed == False`** (task still open), AND
2. **still a member of Order Numbers** (`854650811917168`), AND
3. live status = `⏰On Order - Awaiting CONFIRMATION`.

Not status-field-alone.

---

## 3. THE TRUE SET — live Order Numbers project (authoritative)

Live-pulled the **Order Numbers project (`854650811917168`)**: **3,607 tasks, 43 open.** Open by status:

| Live status | Count |
|---|---|
| **⏰On Order - Awaiting CONFIRMATION** | **24** ← THE chaseable set |
| 🚚On Order - Delivery Date CONFIRMED | 12 |
| 🔜PARTIAL DELIVERY | 5 |
| 🔥ARRIVED - NEED TO QC | 1 |
| HOLD ON ORDER | 1 |

Cross-check (DB → live, open + in Order Numbers): **25** confirmed chaseable (rounding/1 new). Sorted by overdue:

| Overdue | PO | Vendor |
|---|---|---|
| **3d** | 21646-1580 | Maytec |
| **3d** | 21650-1591 | Romans |
| **1d** | 21667-1580 | Blickle |
| 0d | 20985-1575 | SG transmission |
| due future | 21582, 21661, 21657, 21655, 21669, 21672, 21591, 21588… | (6+ supplier groups) |

**Only 3–4 are genuinely OVERDUE past due date.** The rest are due-future awaiting confirmation.

---

## 4. THE SECOND CLASS — quotes out (NOT covered today)

Giles: *quotes out for "inside" control — status Automatic or Manual = we're still awaiting the quote.*

**Finding:** the DB values `Quote - Manual` (42) / `Quote - Auto Email` (4) are **ALL stale** — live-checked, **0 of 46 are open in Order Numbers**. So filtering DB status gives nothing real.

**Where quotes-out actually live:** the **Quote Control project (`1207766004159548`)** — **79 tasks, 75 open.** Examples: "21558-1575 Alexander pollock Engraving", "Securitron - 1573", "Glass Works - Actuator Quotes", "RFQ - Cathedral Sq Tapeswitch".

**Rule A does NOT look at this class at all.** This is the second, unbuilt half of Giles's ask.

---

## 5. Built vs Gap summary

| Capability | Built? | Notes |
|---|---|---|
| Outbound no-reply detection (Rule B) | ✅ | solid, 68 threads |
| Portal-ref capture (Rule C) | ✅ | 16 refs |
| Recipient resolution | ✅ | 3-step chain works |
| **Order-confirmation chase (Rule A)** | ⚠️ **recall ok, precision broken** | flags received/closed orders — gate must add `completed` + project membership |
| **Quote-out chase (Quote Control)** | ❌ **not built** | status filter is stale; must read live Quote Control project |
| Due-date + inbound/outbound correlation | ⚠️ partial | exists for A; not wired for quotes |

---

## 6. Recommended fixes (for Giles's go — code changes to the write/assess path)

1. **Fix Rule A's gate** — add two conditions to `fetch_overdue`:
   - `task.completed == False`, AND
   - task is a member of Order Numbers project `854650811917168`.
   This alone cuts ~138 false positives → the honest ~24.
2. **Add Rule D — quotes out:** read the **live Quote Control project**, list open tasks whose quote hasn't returned, cross-reference inbound (any email in that conversation from the supplier). That's the "automatic/manual, awaiting quote" chase class.
3. **Add due-date correlation:** for both classes, join due date + last outbound + last inbound to rank who to chase.

**No Asana writes occurred. This is an assessment only.**
