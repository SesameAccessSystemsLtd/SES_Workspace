# 🛠️ Dev Update Log — Giles Ai Dev Work

> Log of development work, changes, and progress on the Hermes architecture / SES system.
> Linked to Asana project 1147424298774210 (Giles Ai Dev Work).
> Each entry references the relevant task GID where applicable.

---

## 2026-08-18

### VPS Hermes build-out — LIVE ✅ (Deployment)
- **Deployed** a fresh Hermes Agent instance (Docker) to VPS host **srv1903976 / 191.215.40.116** (Ubuntu 24.04, 8GB RAM, ~90GB free).
- **Status:** bootable, running, Telegram pairing approved, workspace mounted, 0 restarts. SSH alias `vps` + hardened (root key-only, PW auth disabled, ufw deny-default + allow 22 only; sudo user `sasops`).
- **Model/secrets:** deepseek (`deepseek-chat`) + Telegram bot **@SES_Production_bot**; secrets on-VPS only, sourced from Doppler project `vps_hermes/prd`.
- **Migrated to VPS `/opt/ses/.hermes`:** SOUL.md, memories/MEMORY+USER, skills/ (6 dirs), cron/ (22 jobs), config telegram block **merged** (local config NOT bulk-copied — it holds a live Gemini key + Windows paths).
- **Workspace on VPS:** `/opt/ses/workspace` mounted → `/workspace`; `ops.db` (969.8MB, integrity OK) + `crm.db` copied; `terminal.cwd = /workspace`.
- **Skills saved for recur-ability:** `system/vps-bootstrap-hardening`, `system/hermes-on-vps-docker`.
- **Full detail:** `SES_Workspace/plans/vps-hermes-deployment-status-2026-08-18.md` (84 lines) + `SES_Workspace/plans/_outstanding-activities-2026-08-18.md` (91 lines).

### VPS close-out decisions — 2026-08-18 ✅
- **Gemini key rotated** on the local Hermes Doppler instance (live key that was in config plaintext no longer an exposure).
- **ops.db authority decision: LOCAL remains the live writer.** VPS holds a read-only shadow; no divergence risk until VPS is brought online as a writer (testing phase).
- **VPS agent: all updates + cron tasks DISABLED** — it's in validation, confirming exactly what it can/cannot access before it acts autonomously.
- **Docker management constraint:** running in Docker with limited remote management from Giles's side; working around those processes while validating.
- **VPS Telegram bot now live & working** (its own bot instance, separate from local).

---

## 2026-07-09

### Set up personal assistant structure
- Created `SES_Workspace/personal/DAILY_LOG.md` and `PRIORITIES.md`
- Defined passive log mode: absorb & hold, no processing unless instructed
- Established P0-P3 priority ladder with end-of-day recall capability

### Mapped out SIS Development programme
- Reviewed Sys Knowledge project (GID 1216056595306936)
- Identified the SIS Phase 1 lifecycle: Created → Stored → Filed → Adopted → Available
- Documented 14 SIS Fundamental Principles, key ones: P004 (SharePoint permanent source), P009 (one lift identity), P012 (reconcile evidence)
- Mapped the tension between Asana as current SOR vs ops.db as potential operational SOR

### Initial thread identification for cron delivery remap
- Identified 4 Telegram thread destinations (:2, :3, :4, :5) needing channel mapping
- Currently deliver: Purchasing crons to :3/:5, Navigator brief to :4, various to :3
- Awaiting Giles to identify which thread becomes "System Updates"

### Non-procurement task audit
- Identified 3 overdue Build Orders (Button Station, Harley Street, Traversing Slider MOD)
- Found 24 open Edge Cases (8 overdue)
- Found 1 QC ISSUE item in KANBAN
- Found 13 Consumables items needing stock action
- Documents logged on Giles Ai Dev Work projects as summary tasks

---

---
Created: 2026-07-17

### Build Intelligence System — architecture spec
- Created `SES_Workspace/skills/build_intelligence/ARCHITECTURE_SPEC.md`
- Full spec: engineer morning brief, end-of-day check-in, hours capture, performance visuals, critical path
- Identified key blocker: `build_assembly_packs` insight fields empty (2,480 rows; status/readiness/stage/ai all NULL)
- Parked: historical Delay Destroyer/Learning → part mapping (awaiting Giles's solicitation model)
- Deferred to MD/Foreman: notification targets, schedule-confirmation capture method

## Previous entries

*(No prior entries — this log starts 2026-07-09)*