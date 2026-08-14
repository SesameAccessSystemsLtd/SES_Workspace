# Navigator Triage Rules — Learned Preferences

This file is loaded into the AI triage prompt every morning.
Navigator (and Giles) should update this file as patterns emerge.
Add rules here that refine how tasks are categorised.

---

## Project-Specific Rules

- Tasks in "Project Orders" or "Build Orders" projects with due dates → usually ACTION_MINE
- Tasks in "NEEDS - from Workshop" → DECISION_NEEDED unless already on order, then MONITOR
- Tasks in "Goods In" → MONITOR unless overdue
- Tasks in "Quote Control" → ACTION_MINE if awaiting Giles's response to a quote
- Tasks in "KANBAN" stock projects → ACTION_MINE only if flagged with Stock ACTION = Order

## Keyword Signals

- Task name contains "URGENT", "ASAP", "blocking" → bump to URGENT if not already
- Task name contains "review", "check", "monitor" → MONITOR
- Task name contains "admin", "schedule", "meeting", "catch up" → ADMIN

## Delegation Candidates

- Any task that doesn't require Giles's specific authority or supplier relationship
- Routine filing, data entry, status updates → DELEGATABLE

## What to Ignore

- Completed tasks accidentally included → SKIP
- Duplicate tasks (same name, different GID) → SKIP the second one
- Tasks clearly assigned to Giles by mistake (no relation to his role) → SKIP

---
_Last updated: 2026-04-16 — Initial setup_
