# PLAN: Migration Completion — 2026-06-30
# SES_Workspace v1.0 → Fully Operational
# Last updated: 2026-07-03

## Context
Migration from .openclaw to SES_Workspace via Hermes harness.
All Claude Code phases COMPLETE. Blocked on Giles credentials action (Phase 5).

---

## STATUS SNAPSHOT (current)

### Done
- [x] lib/telemetry.py, lib/path_config.py, lib/llm.py (DeepSeek→Gemini→Anthropic chain)
- [x] _copilot/PATH_CONFIG.yaml (migration_state: complete, all paths updated)
- [x] _copilot/SYSTEM_MANIFEST.yaml
- [x] memory/session_log.jsonl
- [x] SES_Workspace/.venv (anthropic, google-genai, PyYAML, deepseek via requests)
- [x] All 11 Hermes wrapper scripts → SES_Workspace paths
- [x] ops.db + crm.db copied to SES_Workspace/db/, PATH_CONFIG updated
- [x] Purchasing pipeline 01–04: .env paths, LLM calls all fixed
- [x] daily_brief.py: all .openclaw refs fixed, LLM chain wired
- [x] rc_email_notes.py: .env path + default dirs fixed
- [x] BOM_Consolidator_SLe_v4.py: reads Dropbox paths from PATH_CONFIG
- [x] Telegram commander: polling loop + Hermes wrapper + cron job (every 5m)
- [x] Graphify: Hermes skill installed, Weekly rebuild cron (Sun 3am)
- [x] Graphify first run: 1,002 nodes, 1,536 edges, 92 communities → SES_Workspace/graphify-out/
- [x] PATH_CONFIG knowledge.graph → graphify-out/graph.json ✓

### Errors (credentials only — not migration)
- email_pipeline: AADSTS7000215 — MS Graph client secret expired → Phase 5
- rc_email_notes: 401 Unauthorized — RC API / MS token → Phase 5

---

## PHASES

### PHASE 1 — Immediate fixes ✅ COMPLETE

| # | Task | Status |
|---|------|--------|
| 1.1 | Kill duplicate Hermes gateway | ✅ DONE |
| 1.2 | Copy ops.db → SES_Workspace/db/ | ✅ DONE |
| 1.3 | Copy crm.db → SES_Workspace/db/ | ✅ DONE |
| 1.4–1.6 | PATH_CONFIG db paths + migration_state | ✅ DONE |
| 1.7 | ⚠️ RESTART Hermes gateway | GILES ACTION |

### PHASE 2 — Graphify ✅ COMPLETE

| # | Task | Status |
|---|------|--------|
| 2.1 | Install graphify as Hermes skill | ✅ DONE |
| 2.2 | Add Weekly Graph Rebuild cron (Sun 3am) | ✅ DONE |
| 2.3 | Run graphify first time | ✅ DONE — 1,002 nodes, 1,536 edges, 92 communities |
| 2.4 | Verify graph.json path in PATH_CONFIG | ✅ DONE — graphify-out/graph.json confirmed |

### PHASE 3 — Telegram inbound commander ✅ COMPLETE

| # | Task | Status |
|---|------|--------|
| 3.1 | telegram_commander.py (skills/purchasing/) | ✅ DONE |
| 3.2 | Hermes wrapper (AppData/Local/hermes/scripts/) | ✅ DONE |
| 3.3 | Hermes cron job (every 5m, no_agent=True) | ✅ DONE — 15 jobs total |
| 3.4 | Test via Telegram: /status → reply | GILES ACTION — after gateway restart |

Commands available: /status, /run_pipeline, /run_requests, /run_orders, /run_sync, /help

### PHASE 4 — Code fixes ✅ COMPLETE

| # | Task | Status |
|---|------|--------|
| 4.1 | daily_brief.py: all .openclaw refs + LLM chain | ✅ DONE |
| 4.2 | 01_email_fetcher.py: .env path | ✅ DONE |
| 4.3 | 02_llm_parser.py: .env path + LLM chain | ✅ DONE |
| 4.4 | rc_email_notes.py: .env + default dirs | ✅ DONE |
| 4.5 | daily_brief LLM: run_eisenhower_audit + triage_with_claude → lib/llm.py | ✅ DONE |
| 4.6 | 02_llm_parser LLM: full parser → lib/llm.py | ✅ DONE |

### PHASE 5 — Credentials ⚠️ GILES ACTION REQUIRED

| # | Task | Where |
|---|------|-------|
| 5.1 | Rotate MS Graph client secret | Azure portal → app a7bd53ba |
| 5.2 | Update MICROSOFT_CLIENT_SECRET in Doppler openclaw/dev | doppler.com |
| 5.3 | Run `refresh_env.ps1` | Pulls into .hermes/.env |
| 5.4 | Re-run email_pipeline to verify | Telegram /run_pipeline or manual |
| 5.5 | Fix RC API credentials | Azure portal or RC dev console |

### PHASE 6 — Validation (after Phase 5)

- [ ] Send /status via Telegram → expect system health summary
- [ ] Send /run_pipeline via Telegram → verify cron fires + reply received
- [ ] Confirm nightly crons fire next night (7 nighttime jobs)
- [ ] Confirm ops.db write path is SES_Workspace (check telemetry records)

---

## OPEN ITEMS (low urgency)

1. **43 remaining scripts** — still reference .openclaw data paths. Address when OpenClaw formally retired.
2. **REQUESTS_DIR in 05_sheet_approver.py** — flip to SES_Workspace/queues/REQUESTS when SAP retires.
3. **Sesame_Brain POC** — seed on OneDrive (separate task, not blocking migration).

---

## KEY PATHS REFERENCE

| Thing | Path |
|-------|------|
| SES_Workspace root | C:\Users\GilesHugo\SES_Workspace |
| PATH_CONFIG | SES_Workspace\_copilot\PATH_CONFIG.yaml |
| Hermes cron jobs | C:\Users\GilesHugo\AppData\Local\hermes\cron\jobs.json |
| Hermes wrapper scripts | C:\Users\GilesHugo\AppData\Local\hermes\scripts\ |
| ops.db | SES_Workspace\db\ops.db |
| crm.db | SES_Workspace\db\crm.db |
| graph.json | SES_Workspace\graphify-out\graph.json |
| LLM chain | SES_Workspace\lib\llm.py |
| Telegram bot token | 8581592821:AAEUaZmMxx30pRPBEhqOwxYPnXbW2DXdUwo |
| Telegram chat ID | 8551261362 |
