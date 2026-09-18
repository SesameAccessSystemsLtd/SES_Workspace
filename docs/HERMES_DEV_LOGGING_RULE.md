# Hermes rule — log development work before writing code

Paste the block below into the Hermes agent's rules / prompt-injection script, for
**every channel that can write code**. It is written to be pasted verbatim.

Governed by `docs/dev-work-register-decision-2026-09-17.md`.
Implemented by `skills/os_portal/dev_register.py`.

---

## THE RULE (paste from here)

**Development-work logging rule — applies whenever you begin writing or modifying
code, scripts, configuration or automation, in any channel.**

Before you write the first line, log the thread:

```
C:/Users/GilesHugo/SES_Workspace/.venv/Scripts/python.exe \
  C:/Users/GilesHugo/SES_Workspace/skills/os_portal/dev_register.py log \
  --objective "<one sentence: what DONE looks like, not what you are doing>" \
  --where "<absolute paths and/or repo+branch you will touch, comma-separated>" \
  --channel "<this channel, e.g. hermes:purchasing>" \
  --area "<one of: Paths / Migration | Data Health | Ordering / Inventory | VPS / Webhooks | Documentation | Review / Feedback | Edge Cases>" \
  --effort "<S|M|L|XL>" --priority "<Critical|High|Medium|Low>"
```

It prints an `id`. Keep it for the rest of the session.

**Rules of use:**

1. **Objective states the finished condition, not the activity.** "BOM tracker
   buttons post same-origin and dispatch succeeds from the hosted page" — not
   "working on the buttons". If you cannot state what done looks like, you are not
   ready to write code; say so instead.
2. **Log once per thread, not per message.** If the command reports `resumed`, that
   objective is already open — carry on under that id, do not open a second.
3. **When the work reaches a state a human could review**, append:
   `dev_register.py event <id> --status awaiting_signoff --note "<one line: what to check>"`
4. **If blocked**, append `--status blocked --note "<what is blocking>"`. Do not
   leave it silently open.
5. **Never close anything.** `closed` and `cancelled` are Giles's alone. If you
   believe work is finished, set `awaiting_signoff` and stop.
6. **If the log command fails, tell Giles in the channel and do not proceed** to
   write code. An unlogged thread is the exact failure this rule exists to prevent.
7. **Do not log** trivial one-off edits with no follow-up: answering a question,
   reading a file, running an existing script unchanged, a typo fix. The test is
   whether someone would need to come back to it.
8. **Do not call the Asana API yourself.** Sync is a separate scheduled step.

## END OF RULE (paste to here)

---

### Notes for whoever installs this

- The rule deliberately requires no Asana token at log time. Logging must never fail
  because a credential is missing.
- `--channel` defaults to the `HERMES_CHANNEL` environment variable if omitted, so
  setting that per channel makes the rule shorter and the attribution more reliable.
- The same rule applies to Claude CLI and Codex sessions. For those, `--channel
  claude-cli` / `--channel codex`.
- ChatGPT sessions cannot run the command. For those, Giles logs the thread by hand
  at the start, or the work is treated as unlogged and will not appear in the
  nightly report.
