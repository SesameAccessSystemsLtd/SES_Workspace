# 📓 Daily Log — Giles Hugo

> Passive dump of what you've been up to. No processing, no actions, just logged.
> End-of-day recall: ask me and I'll ping it back.

---

## 2026-07-09

- Emails: checked and assigned tasks to stay on top
- Alison: sort/tidy meeting room for apprentices
- Alison: help with Asana project to manage apprentices
- Finding drawings from a couple years ago that Reddapt was doing for design (separate)
- Toolbox talks management with Shauna — setting up evidence for contractors
- Discussion around 10th machining BOM and cost reduction
- Still chasing water jet profiling of parts
- Identified: cam wheels not worth doing outside of machining
- Identified: expensive cost is the heat treatment service applied to them
- Long chat with Steve around C: Sys development
- Processing priority: feeding Ramsan with materials for America 1 BOM (received yesterday)
- Completed steel profile checklist
- Still need: agglomerate what's needing to be ordered + place outstanding steel orders
- Processing entire America 1 BOM as import — will mark complete items from stock
- Drawing BOM still needs syncing (aiming to do today)
- Need to tell Steve optimal way to get up-to-date information
- Need to develop system: robust way to ensure Parts Control tasks are updated with order due dates

## 2026-07-16

- Feeling immense pressure — multiple BOMs arriving simultaneously
- St Mary's BOM imported 3 times (guard rail issue) — had to problem-solve and cross-check
- 3 recent BOM imports need syncing up
- Steel profile BOM still needs running
- Pressure mounting: system not robust, Asana projects need updating, Golden Index not set up right and needs significant work
- Steve wants everything done and resilient — not dependent on any single flow/process
- But it's all interwoven and I was told to stop the workflow 3 months ago, making pickup harder
- Constantly juggling: building + processing + managing + orchestrating + emails + phone calls + requests for missing parts + incoming items + updating tasks + identifying edge cases in Hermes, process from Carol, and Asana
- Need more hardcoded/extreme guardrails set up and correctly deployed — not continuous iterative fixing

## 2026-07-17

- Large production meeting — many BOMs from last Thursday in work and being processed
- Brooks Theatre handrails: polishing options and ordering decisions addressed
- Helped Sean with Chanel Madrid set
- Network troubleshooting: changed adapter to different subnet to connect; identified router WiFi misconfiguration
- Urgent servicing items addressed — unusual/different pumps
- Analysed servicing component issues: QC issues reconfigured and readdressed; unable to anodize so sent for powder coating and dispatched
- In-house learnings: managing material availability for in-house work
- Cambridge laser ship BOM changes — updated, distributed, communicated ahead of revised quote
- Set up van MOT and service for start of August; negotiated 2 new service contracts (need signing tomorrow)
- Ropes and slings certificates booked in for end of month (ISO 9001 compliance)
- Manchester panels came in ready for install
- Numerous orders and cross-checks in progress

## 2026-08-18

- **VPS Hermes instance up & running (for both Steve and me)** but effectively turned off while we evaluate: what keys it needs, how it will operate, how well the scripts function on a Linux-based system.
- Recently asked the VPS agent to **test/evaluate a product called "Gibble"** — geofencing + timestamping of engineers.
- **On holiday tomorrow** — will pick this up **Monday** to evaluate how Gibble compares against Clockwise or any other alternatives.
- **Asana task created: "gibble clockwright alternative"** — log notes/evaluations against it, monitor, keep any notes and evaluations inside it. Started making notes but possibly not posted as comments yet on what needs testing and what is possible.
- **What's not possible currently:** managing expenses through Gibble. Currently using **Expensify**, which has an **open API endpoint** we can also integrate.
- **Biggest learning today:** insight into what Steve has been building using essentially two plain interfaces — one in **Codex**, one in **ChatGPT with all its plugins**. These are two windows either side of his main work view maintaining one chapter thread; in the background both systems run to test and validate all theories and hypotheses and evaluate whether they are building the right thing (and if not, how they should build the right thing), documenting the entire build process and what happens as a result.
- **Steve's golden rules (implemented from the start):** system embeds rich contextual data and makes it dynamically searchable. He's built his entire system on this. Now looking to understand the semantics inside language to correctly identify the project and task that needs to be updated — clever, a lot to learn from, but a very different infrastructure to what Giles has been building.
- **The bedrock (the "genius mode"):** the guardrails Steve has built that allow him to literally hand off the entire building process to a Codex bot which iteratively builds code, tests that it works, validates that it meets all the guardrails and works against embedded assumptions from previous work. This will allow him to build almost any product in the future and creates a very well-controlled guardrail system that becomes the intellectual property of Sesame.

## 2026-09-14

- **Discussion with Steve this morning — using Asana differently + the interface question.**
- Core idea under discussion: **one task per person per day** as the daily attention surface — a "what's my focus today" summary per person, rather than the flat task board.
  - Easy for the **workshop team** (physical, visible work; significance is obvious).
  - Much **harder for admin/management** — knowing what is genuinely *significant* (vs busywork) is the hard part. Agreed we can put things in place to address, then **review and revise**.
- Steve has set up a governance system he rates highly — **SIS** (his ChatGPT account has access to everything: everyone's emails and work, etc.).
- What Steve wants (as Giles reads it): Steve provides a **steer**; everyone else's tasks and attention are **focused through their own chat** on that steer.
- **The interface question:** to do work people need an interface. Options on the table:
  - **ChatGPT** (business account) — moving fast, has all the connectors, build ability; risk = becoming more complicated.
  - **Cowork** — full access to GPT, less complicated than the current product.
  - The **current product** (Asana itself).
- Framing: **a scoping mission** to understand the **true value of the Asana platform** and how best to **interface** with it going forward.

**Planning / Upcoming**
- Develop the scoping frame for "true value of Asana + how to interface it" — one-task-per-person-per-day model, significance definition for admin/management, and the interface comparison (ChatGPT business vs Cowork vs current).
