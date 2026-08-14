# SHARED_CONTEXT.md — Operational Picture

**Last updated: 2026-06-29**

This file provides a high-level overview of the current operational state of the agent system. It is updated by Commander Claw.

---

## Active Campaigns / Projects

### Email pipeline (purchasing)
- Status: Operational. Runs hourly 09:00-16:00 Mon-Fri.
- Summary delivered to Telegram (8551261362) after each run.

### IHBC Email Campaign
- Status: Resumed, awaiting manual review/approval. Previously blocked by a scraper error, which is now resolved.
- Next steps: Awaiting next steps in workflow or manual approval.

---

## Data Freshness Status (High-Level)

(Refer to DATAMAP.md for detailed status. Generally, most critical datasets are considered FRESH. The scraper agent is responsible for maintaining DATAMAP.md.)

---

## Cross-Agent Blockers

- **`@heartbeat` Telegram Error:** The cron framework attempts to notify `@heartbeat` which causes delivery failures. Scripts themselves are functional. This is a framework-level issue requiring investigation.
- **Stale `REQUESTS/` files:** No stale Asana write request files were found in the `REQUESTS/` directory during the current check.
- **SAP Notification Configuration:** SAP needs to be configured to use Giles's numeric Telegram ID (`8551261362`) for notifications.

---

## Recent Agent Activity

- Commander Claw: Performed heartbeat check, read HEARTBEAT.md, CADENCE.md, created daily memory log for 2026-06-29, and updated SHARED_CONTEXT.md.
- (Other agents' activities would be logged here as observed)

---

## Outstanding Maintenance

- **Forge nightly audit:** Status is unknown. Needs verification.
- **Fix `@heartbeat` cron config:** Needs investigation.
- **Daily memory log:** Created for 2026-06-29.

---

_This document is a living document. Update it whenever the operational picture changes significantly._
