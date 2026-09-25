# Asana AI Teammates — capability log (logged 2026-09-24)

**Status:** Available and in use
**Logged by:** Hermes, on Giles's instruction
**Registry entry:** `_copilot/SYSTEM_MANIFEST.yaml` → `integrations.asana.ai_teammates`

This is a significant capability improvement in Asana. Recorded here so the workspace
system and all agents know it exists and where it fits.

---

## What changed

Asana now supports **AI teammates** — agents that operate *inside* Asana tasks. This is a
genuine shift in where work can happen: previously the workspace had to pull data out of
Asana to reason about it; now assessment can happen **in place, at the moment the
information arrives**.

## The pipeline, end to end

```
Supplier email
   ↓
Purchasing email gateway  (skills/purchasing, mailbox purchasing@sesameaccess.com)
   ↓
Pushed into Asana "control system"
   ↓
Once the task exists in Asana → THIS is the best moment to assess + surface info
   ↓
Inbound Asana scripts/rules triage · chase · trigger
   ↓
        ┌────────────────────────────┴────────────────────────────┐
        ↓                                                          ↓
  Processed INTERNALLY                                    Processed EXTERNALLY
  by an AI teammate                                       by the workspace
  (in-task: price eval, summary,                          (REQUESTS queue → SAP agent)
   email draft)                                           ← one agent hop max
```

## AI teammate capabilities (as observed)

1. **Price evaluation** — when a task "is being picked up in Asana", an AI bot evaluates
   the price.
2. **Summary into the task description** — compiles a summary directly in the task's
   description field.
3. **Email drafting** — drafts an email ready to send, sent **from the purchasing account**.
4. **Continuous refresh** — the draft **updates every time a new quote comes in** on the
   same task. No manual re-work.

## Why this matters

- **Best time to assess is on arrival.** Once the information is in the task, that's the
  cheapest, most accurate moment to provide information directly within that task.
- **Reduces noise handling.** Combined with the "Confirmation" category tagging all inbound,
  fewer items need Giles's manual attention.

## Cost reality (important)

> **AI teammates are expensive to use.**

They are not a bulk-work tool. The pattern should be:
- **Static rules / scripts** → triage, chase, trigger, bulk classify. Free.
- **AI teammates** → reserved for the high-value moment: price assessment, summary, and
  draft generation on a task that actually matters.

Use the AI-teammate context to decide, per trigger, whether it's worth spending a teammate
call (internal) or handing to the workspace (external).

## Relationship to existing architecture

- Does **not** change the rule that **SAP is the only Asana writer** via REQUESTS — the
  external path still goes through the queue. AI-teammate actions are in-task by nature.
- Emails still flow through the **purchasing gateway** first; this adds a *downstream* step,
  not a parallel one.
- Ties into the "Confirmation" category work: inbound is captured and tagged, then triaged.

## Watch-outs

- **Cost control** — needs a policy for when a teammate call is justified.
- **Email send authority** — teammates can draft in the purchasing account; confirm whether
  drafts are sent or always held for review before sending.
- **Overlap** — ensure an item isn't processed both internally (teammate) and externally
  (workspace), or it'll be double-handled.

---

*Cross-refs: `docs/purchasing-email-gateway-rebuild-2026-09-18.md`,
`docs/invoice-pipeline-direction-2026-09-23.md`, `skills/purchasing/`*
