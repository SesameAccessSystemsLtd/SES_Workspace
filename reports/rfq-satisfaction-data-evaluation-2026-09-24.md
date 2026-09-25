# RFQ-out satisfaction — data-structure evaluation

**Date:** 2026-09-24 · **For:** Giles · **Status:** read-only evaluation, no writes
**Question:** how can we detect, from the data, an outbound request-for-quote (RFQ) that has **not been satisfied** — particularly the ones **without an SQR** — with a safe dedup against existing Quote Control tasks?

---

## 1. What the data actually looks like (measured)

| Source | Size | Notes |
|---|---|---|
| `email_touchpoints` (outbound) | 1,176 | all from `purchasing@sesameaccess.com` |
| `rfq_outbox` (SQR ledger) | **1 row** | effectively unused — almost no RFQ is logged |
| Outbound subjects carrying `SQR-#####` | **69 distinct** | SQR *is* used, just in the subject line, not the ledger |
| Quote Control project tasks (`1207766004159548`) | **79** (75 open) | |
| QC tasks carrying an **SQR field** | **79 / 79** | SQR field is the QC anchor |
| QC tasks with a **Quote Status** | **1 / 79** | `Quote Status` is unmaintained — cannot drive chase |
| **Overlap**: outbound-subject SQR ↔ QC-task SQR | **12** | 57 outbound-SQRs have **no** QC task |
| RFQ-keyword subjects **with** SQR | ~19 | our originated RFQs |
| RFQ-keyword subjects **without** SQR | ~105 | mostly `Re:` to inbound website enquiries (sales leads, not our RFQs) |

**Two hard facts:**
1. **The SQR is the natural key** — it's in the outbound subject *and* it's the one field every QC task carries. When present, it's a clean join.
2. **The ledger↔project sync is broken/marginal.** Only 12 SQRs reconcile between sent emails and Quote Control. `rfq_outbox` (1 row) has never been populated by the manual send flow.

---

## 2. Why "not satisfied" is detectable — and where it breaks

An outbound RFQ is **unsatisfied** when: an outbound message went out, and **no inbound reply exists in that conversation** after it (Rule B logic already built), **OR** no quote was logged back.

**The break is anchoring.** There are three classes:

| Class | Anchor available | Detectable today? |
|---|---|---|
| **A. RFQ with SQR** (our originated) | SQR in subject → thread → reply check | ✅ yes (SQR is reliable) |
| **B. RFQ without SQR** (evaluation exercises, "off the records") | no key — only subject/recipient | ⚠️ only by conversation-reply, no task anchor |
| **C. Inbound website leads** (`Re: General enquiry from website`) | not ours | ❌ must NOT be treated as RFQs |

The current engine conflates B and C in keyword matching, and has no task anchor for either.

---

## 3. The dedup / "mess" problem — quantified

Giles's worry is exactly right. If we **auto-create a Quote Control task per outbound RFQ**:

- **12 would duplicate** existing QC tasks (the 12 reconciliations).
- Only **~19–69 carry an SQR**; the rest have no key → we'd create tasks we can't later reconcile → the mess, multiplied by every re-send.
- The same RFQ goes to **multiple suppliers** (e.g. `Lift PCB Enquiry SQR-45454` → 5+ suppliers) → a naive "1 email = 1 task" rule creates 5 tasks for one requirement.

**Conclusion: auto-creating Quote Control tasks on send is UNSAFE as-is.** The dedup key must exist *before* any auto-create.

---

## 4. Recommended design (safe, staged)

**Anchor first, create second.** Do not auto-create QC tasks until an SQR is guaranteed.

1. **Guarantee the SQR at send time.** The send flow should allocate the next SQR and stamp it on the subject (the RFQ draft tool already issues `SQR-<max+1>`). If every originated RFQ carries an SQR, class A becomes 100% reconcilable.
2. **Reconcile, don't duplicate.** A daily job joins outbound-SQR → QC-SQR and:
   - SQR has a QC task → **link/update** it (never create).
   - SQR has **no** QC task → that's a *candidate* for creation (a log, not a blind insert).
3. **Satisfaction check = conversation reply** (Rule B mechanic, already built): outbound sent, no inbound reply in the same `conversation_id` after `sent_at` → unsatisfied.
4. **Only auto-create QC tasks for SQR-anchored RFQs with no existing task** — never for no-SQR emails (class B stays a manual/flagged list; class C excluded).
5. **For no-SQR evaluation RFQs** (the "off the records" exercise): don't create a task; put them on a **flagged review list** — "outbound enquiry, no SQR, no reply ≥N days" — and let Giles decide whether they should have been logged.

**The cron** (twice daily, mid-afternoon + evening): runs the outbound-reply sweep (Rule B mechanic), groups by conversation, flags unsatisfied; the SQR-anchored ones get reconciled to Quote Control; the no-SQR ones go to a review list. Read-only except the reconcile step (which is a REQUESTS-file if it's an Asana write).

---

## 5. The core recommendation on SQR coverage

**Yes — capture the off-record RFQs, but via an SQR allocated at send, not a post-hoc task.** The data proves the SQR is the only field that both (a) travels with the email and (b) exists on every Quote Control task. Making SQR allocation universal at send time is what closes the gap *without* creating duplicates — it converts class B (undetectable) into class A (cleanly reconcilable). Without it, any auto-create is the mess Giles predicted.

**Net:** the loop is buildable and the machinery (conversation-reply detection, SQR allocation, QC SQR field) already exists — the missing piece is **enforcing SQR-on-send** + a **reconcile-not-create** pass. No Asana writes occurred.
