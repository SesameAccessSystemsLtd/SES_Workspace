# Session: Commander Claw SOUL.md Fix
**Date:** 2026-06-11
**Prompted by:** Giles — Commander Claw going rogue with responses and planning

---

## Diagnosis / Root Causes

1. **No memory continuity** — last memory log was `agents/main/memory/2026-05-11.md`. Nothing written since. Every session started blind to a month of decisions.
2. **Two conflicting SOUL.md files** — `workspace/SOUL.md` was stale (old 2-pipeline table, missing June 2026 exit-code rule). `agents/main/SOUL.md` was the correct canonical version.
3. **No enforced session startup order** — "read these files" was scattered across SOUL.md in vague language. No mandatory boot checklist ran before first response.
4. **Self-modification permission too open** — Section 4 said "you MUST propose an edit... you are a guest with a brain" which invited the agent to rewrite its own constraints.
5. **Write permissions not stated** — no explicit list of what main could/couldn't write inside SOUL.md itself.

---

## Decisions Made

- Add mandatory Section 0 boot sequence to `agents/main/SOUL.md`
- Add explicit write restrictions (Section 2a)
- Add hard memory-logging rule to Section 4
- Soften self-modification language in Section 4
- Replace `workspace/SOUL.md` with redirect to canonical file

---

## Files Changed

| File | Change |
|------|--------|
| `C:\Users\GilesHugo\.openclaw\agents\main\SOUL.md` | Added Section 0 (session boot), Section 2a (write restrictions), rewrote Section 4 (memory logging hard rule + no self-edit) |
| `C:\Users\GilesHugo\.openclaw\workspace\SOUL.md` | Replaced with 4-line redirect to `agents/main/SOUL.md` |

---

## What Commander Claw Should Do Next Session

Section 0 is now the first thing loaded. It will:
1. Read IDENTITY.md, HEARTBEAT.md, CADENCE.md, SHARED_CONTEXT.md, TASKS.md, most recent memory file — in that order
2. Surface any CADENCE overdue items before answering Giles's first message
3. Know exactly what it can and cannot write
4. Write a memory log at end of session

---

## No follow-up scripts required. No Asana changes. No pipeline runs.
