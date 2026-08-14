# Skill: dev

Governance protocol for all code development and agent/skill build tasks.

## When to load

Load this skill when asked to:
- Build, create, or modify a script, skill, or agent
- Add a new feature to an existing tool
- Refactor or fix code in the workspace or Asana Scripts
- Set up a new integration, pipeline, or scheduled job

Do **not** load for: one-off exec commands, read-only queries, file edits under 10 lines with no architectural impact.

---

## The Three Gates

Every build passes through three gates in order. **Do not skip ahead.**

```
PLAN → [Giles approves] → BUILD → VALIDATE
```

---

## Gate 1: PLAN

Before writing a single line of code, produce a plan and write it to:

```
workspace/plans/PLAN-YYYY-MM-DD-<slug>.md
```

Where `<slug>` is 2–4 words describing the build (e.g. `asana-compact-flag`, `qa-skill`, `email-dedup`).

**Tell Giles the plan file has been written and ask for approval. Do not proceed until he says go.**

### Plan file format

```markdown
# PLAN: <title>

**Date:** YYYY-MM-DD
**Status:** PENDING_APPROVAL

## What & Why
One paragraph. What is being built and why it's needed.

## Scope

### Files to create
- `path/to/new_file.py` — what it does

### Files to modify
- `path/to/existing_file.md` — what changes and why

### Files left untouched
- Any related files that are explicitly NOT changing

## Dependencies
- Skills or tools required (e.g. venv, specific packages)
- External APIs or data sources

## Success criteria
Numbered list. Each item must be verifiable — not "it works" but "running X returns Y".

1. `python script.py test` exits 0
2. `SKILLS_REGISTRY.md` contains entry for new skill
3. Commander Claw can invoke the skill with a one-line trigger

## Risks & rollback
- Risk: [what could go wrong]
- Rollback: [how to undo — file deletion, git revert, etc.]

## Out of scope
Explicit list of things that are NOT being built here. Prevents scope creep.
```

---

## Gate 2: BUILD

Proceed only after Giles has approved the plan. During build:

**Code hygiene:**
- Write the minimum code that satisfies the success criteria. No extra features, no future-proofing.
- Scripts must have a clear single responsibility. If it does two things, split it.
- No recursive directory listings (`ls -R`, `Get-ChildItem -Recurse`) — these bloat context. Use shallow `ls` or read the index file.
- No hardcoded secrets. Auth reads from env vars or config files already in place.

**File hygiene:**
- Only create/modify files listed in the plan. If you discover you need an unlisted file, **stop and update the plan first**.
- New skills go in `workspace/skills/<name>/SKILL.md` (+ `scripts/` subdirectory if they have executable code).
- New agents go in `agents/<name>/` with the standard file set: `IDENTITY.md`, `SOUL.md`, `AGENTS.md`, `TOOLS.md`, `USER.md`, `HEARTBEAT.md`.

**Reporting:**
- As you complete each file, say so. Do not batch everything and report at the end.
- If you hit a blocker (missing dep, unexpected file state, API error), stop and surface it. Do not brute-force through.

---

## Gate 3: VALIDATE

After the build is complete, work through this checklist **before** telling Giles it's done.

### Scope check
- [ ] Every file listed in the plan was created or modified
- [ ] No files were created or modified that weren't in the plan
- [ ] `Out of scope` items were not touched

### Code check
- [ ] Each script can be invoked without error (run the test command from success criteria)
- [ ] No hardcoded paths or credentials that should be env vars
- [ ] Output size is reasonable — no tool result will exceed ~50KB in normal use

### Registry check
- [ ] If a new skill was created: `SKILLS_REGISTRY.md` has been updated
- [ ] If a new agent was created: `openclaw.json` agent list and `AGENTS.md` roster table are updated
- [ ] If new Asana Scripts were added: `ASANA_SCRIPTS_INDEX.md` is updated

### Plan check
- [ ] Update the plan file: set `Status: COMPLETE` and add a one-line outcome summary

Report the checklist result to Giles. If any item fails, fix it before declaring done.

---

## Spawning Forge (Commander Claw delegation template)

`sessions_spawn` does NOT support `agentId`. The correct pattern loads Forge's identity via instructions so the spawned agent reads its own soul files and behaves as Forge.

Commander Claw writes the plan file first, gets Giles approval, then spawns:

```
sessions_spawn:
  instructions: |
    You are Forge, the OpenClaw dev agent. Before doing anything else, read these files:
    - C:\Users\GilesHugo\.openclaw\agents\dev_agent\IDENTITY.md
    - C:\Users\GilesHugo\.openclaw\agents\dev_agent\SOUL.md
    - C:\Users\GilesHugo\.openclaw\agents\dev_agent\TOOLS.md
    - C:\Users\GilesHugo\.openclaw\agents\dev_agent\AGENTS.md
    - C:\Users\GilesHugo\.openclaw\workspace\skills\dev\SKILL.md

    Your task is defined in the plan file at:
    C:\Users\GilesHugo\.openclaw\workspace\plans\PLAN-<YYYY-MM-DD>-<slug>.md

    The plan has already been approved by Giles. Execute Gate 2: BUILD, then Gate 3: VALIDATE.
    Report each file as you complete it. Do not skip the VALIDATE checklist.
    When done, write your outcome to agents/dev_agent/LOG.md and report a summary.
  model: google/gemini-2.5-pro
  maxTurns: 30
```

Replace `<YYYY-MM-DD>-<slug>` with the actual plan file name before spawning.

---

## Independent QA review (optional — use for complex builds)

For builds that touch multiple agents, modify shared protocols, or carry real deployment risk, spawn a one-shot QA subagent after Gate 3 self-validation:

```
sessions_spawn:
  instructions: |
    You are a QA reviewer. Read the plan at workspace/plans/PLAN-<date>-<slug>.md.
    Then read each file listed under "Files to create" and "Files to modify".

    Your job: verify that what was built matches what was planned.

    Return a structured verdict:

    VERDICT: PASS | FAIL | PASS_WITH_NOTES

    Issues (if any):
    - [file] — [what's wrong or missing]

    Confirm (for each success criterion):
    - [criterion] — MET | NOT MET | CANNOT VERIFY

    Do not fix anything. Report only.
  model: google/gemini-2.5-flash
  maxTurns: 3
```

Pass the verdict back to Giles before closing out the task.

---

## Quick reference — when to use QA subagent

| Build type | Self-validate only | Spawn QA subagent |
|---|---|---|
| Single script, < 100 lines | ✓ | |
| New skill (SKILL.md + script) | ✓ | |
| New agent (full file set) | | ✓ |
| Modifying shared protocol (AGENTS.md, SOUL.md, etc.) | | ✓ |
| Multi-file refactor across 3+ files | | ✓ |
| Cron job or heartbeat change | | ✓ |
