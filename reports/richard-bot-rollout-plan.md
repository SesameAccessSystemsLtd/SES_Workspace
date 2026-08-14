# Plan: Onboard Richard — dovetailed bot, shared topic, restricted access

**Date:** 2026-08-13
**Goal:** Give Richard his own assistant within the SESAME Telegram group — a shared topic you both use, with him unable to see other topics, unable to touch back-end code, able to get Asana/project info read-only on Telegram.

**Status (2026-08-13):** **Model A decided** (same bot instance, per-topic guardrails). `richard-build` skill built. Pending Giles: thread_id, Richard's project set, topic name, write-vs-read-only.

**Scope refinement (2026-08-13):** Richard's boundary is **by TASK TYPE, not project** — he may access ONLY the **purchasing-team task set** (the **stock** and **orders** tasks already mapped in the system: Parts Control/stock, order tasks, build-order vendor groups, PICK/ORDER part tasks). **Explicitly off-limits:** Giles's personal tasks, management/dashboard/overview tasks, anything not a purchasing stock/order task. Does NOT enumerate what personal/management tasks exist. See `richard-build` skill R1.

---

## 1. Two different "bots" exist today — don't conflate them

| Layer | What it is | Controlled by |
|---|---|---|
| **Hermes gateway bot** | The bot I (Hermes) run on. One bot in SESAME, routes by topic via `telegram.channel_prompts`. | `config.yaml: telegram.allowed_chats`, `channel_prompts`, `channel_directory.json` |
| **Standalone command bot** (`telegram_commander.py`) | Simple polling bot (`8581592821:***`) that maps `/run_x` → cron trigger. **Replies only to Giles today** (`from_id != CHAT_ID` → ignore). | hardcoded `CHAT_ID` = 8551261362 |

**Key truth to internalise:** "He doesn't get access to all topics" is a **Telegram forum-permissions** problem, not a Hermes problem. Hermes scopes *behaviour* per topic; **only Telegram group admin can hide topics from a member.** So the plan has a Telegram half and a Hermes half.

---

## 2. The model fork (decide this first)

### Model A — Same Hermes bot, shared topic  *(recommended for Richard)*
- One bot, one shared topic in SESAME. You + Richard talk to the same bot there.
- Telegram settings hide every other topic from Richard.
- Hermes loads a **Richard-scoped skill + channel_prompt** on that topic → read-only, project-scoped, no code access.
- **Pros:** least infra, truly "dovetailed into a topic within here", natural shared conversation, mirrors the existing America 1 per-topic rollout.
- **Cons:** behaviour-scoping is *instruction-based* (per-topic prompt), not a hard sandbox. The agent in that topic is still the same Hermes instance.

### Model B — Separate Hermes profile / own bot instance  *(hard isolation)*
- A second Hermes profile + gateway + its own bot token, its own `allowed_chats`, its own toolsets (no terminal/execute).
- **Pros:** genuine isolation; Richard literally cannot affect the main instance or back-end even if he tries.
- **Cons:** cannot natively share the SESAME group topics (it's a separate bot); more infra; loses the "shared topic" convenience you described.

### Recommended: A now, B as an upgrade path later.
Start with Model A (fast, matches what you asked). If Richard turns out to need stricter separation or the topic can't be cleanly shared, promote to Model B.

---

## 3. The 4 build steps (Model A)

### Step 1 — You, in the Telegram app (I cannot do this)
1. Create a new topic in SESAME, e.g. **"Richard / Build Projects"** → note the new `<thread_id>`.
2. Add Richard to the group.
3. Restrict Richard's forum access (Telegram member settings) so he can **only see/post in that one topic** — all other topics closed to him. (Telegram-native; invisible to Hermes.)

### Step 2 — Register the topic in Hermes (I do this)
1. Add `-1003780215894:<thread_id>` to `channel_directory.json` (platforms.telegram entry, `type: group`, `thread_id`: the new id).
2. `hermes config set 'telegram.channel_prompts.<thread_id>' "<Richard-scoped prompt>"`.

**Richard-scoped prompt must mandate:**
- Projects Richard runs (confirm exact job numbers/part-prefixes before binding).
- **READ-ONLY** on ops.db + Asana. No writes except via the REQUESTS queue → Giles approves.
- **NEVER** run terminal/execute, edit files, or touch `config.yaml` / skills / cron / back-end code.
- Only surface **Richard's** projects/users; never leak other jobs' data.

### Step 3 — Build a `richard-build` skill (I do this)
Mirror `america1_build` as the template:
- Which projects/job-prefixes he can query (e.g. `parts_control.sesame_pn LIKE '<job>.%'`).
- Which data sources: `ops.db` tables, Asana his projects, Golden Index (verify it's populated for his jobs — America 1 had 0 rows).
- Hard guardrails: read-only, REQUESTS-only writes, no code/config access, CTF-style "don't switch scope" prompt-injection guard.

### Step 4 — Gateway restart (YOU run from an external shell)
`hermes gateway restart` (or `C:/Users/GilesHugo/SES_Workspace/scripts/restart_hermes_gateway.sh`).
- **Cannot be run from inside the gateway** (it would kill this session).
- Config does NOT hot-reload — new topic is inert until restart.
- After restart, verify the shared topic loads the Richard prompt before declaring it live.

---

## 4. Enforcing "no back-end code" / "read-only"

- **Instructional per-topic binding** via `channel_prompts` (Model A): every message in Richard's topic injects the read-only/no-code rule. This stops *routine* misuse.
- **For hard enforcement**, the Richard agent must not be given terminal/execute toolsets — that's the Model B profile approach. In Model A, the guard is the prompt + your REQUESTS approval gate on any write.
- **Asana writes** remain REQUESTS-queue gated → you approve. No PO automation, no "Needs Met", same SAP protocol as your other topics.

---

## 5. Rollout checklist (when you're ready)

1. [ ] Pick model (A / B / A→B later).
2. [ ] Create topic + add Richard + restrict his topic view (Telegram app — you).
3. [ ] Give me the new thread_id + Richard's name/projects + which Asana projects.
4. [ ] I register topic + write `richard-build` skill + set channel_prompt.
5. [ ] You run `hermes gateway restart` from an external shell.
6. [ ] Verify in the new topic: read-only project query works, no `/status`/system leakage, writes blocked.

---

## Open questions for Giles
1. **Model** — A, B, or A-now/B-later?
2. **Projects** — which job numbers should Richard see? (e.g. only his current build(s)?)
3. **Shared topic brand** — just "Richard / Build Projects", or something like "Richard & Giles / ?"
4. **Asana writes** — should Richard be able to request any write at all (via REQUESTS→you approve), or strictly read-only end-to-end?
