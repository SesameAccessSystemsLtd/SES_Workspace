# AI Token-Spend Dashboard — Plan & Data Map

**Owner:** Giles (Purchasing) · **Built by:** Hermes · **Status:** Plan → Map complete, building dashboard.

## 1. Goal
A single HTML dashboard that shows where AI tokens are spent and what it costs — with
per-day / per-week / per-month views and an annual projection — and that **flags
background processes burning tokens for little/no value**. Output: `data/os_status/token-spend.html`.

## 2. Data source (ground truth — no estimation of raw usage)
`C:\Users\GilesHugo\AppData\Local\hermes\state.db` → table **`sessions`** (351 rows, 2026-06-29 → present).

Relevant columns (one row per agent/cron/subagent session):
| column | meaning |
|---|---|
| `id` | session id. `cron_<job_id>_<ts>` → ties a run to its cron job |
| `source` | `cron` \| `telegram` \| `subagent` \| `cli` |
| `model` | model used (deepseek-chat, deepseek-v4-pro, …) |
| `input_tokens` | prompt tokens (billed) |
| `output_tokens` | completion tokens (billed) |
| `cache_read_tokens` | prompt cache reads (cheap) |
| `cache_write_tokens` | prompt cache writes |
| `api_call_count` | number of model round-trips |
| `started_at`, `ended_at` | unix ts → day/week/month bucketing |
| `estimated_cost_usd` / `cost_status` | Hermes cost — mostly `unknown`, so we compute our own |
| `title` | human label (e.g. "Nightly System Maintenance · Sep 21") |

**Job names:** job_id → name map from `cronjob(action='list')` (cron_<jobid>_<ts> prefix).

## 3. Cost model (computed, not from Hermes)
Hermes leaves `estimated_cost_usd` unknown for direct providers. We compute:

```
cost = input_tokens/1e6 * in_rate
     + output_tokens/1e6 * out_rate
     + cache_read_tokens/1e6 * cache_read_rate
     + cache_write_tokens/1e6 * cache_write_rate
```
Rates are per-model, in USD per 1M tokens, defined in the script in a single
`PRICING` table (auditable, one place to update). Verified against Hermes
`cost_source: official_docs_snapshot` where available.

### Pricing table (USD / 1M tokens) — UPDATE AS PROVIDERS CHANGE
| model | input | output | cache read | cache write |
|---|---|---|---|---|
| deepseek-chat | $0.27 | $1.10 | $0.07 | $0.27 |
| deepseek-v4-pro | $0.28 | $1.10 | $0.07 | $0.28 |
| gemini-2.5-flash | $0.30 | $2.50 | $0.075 | $0.30 |
| gemini-2.0-flash | $0.10 | $0.40 | $0.025 | $0.10 |
| claude-haiku-4-5 | $1.00 | $5.00 | $0.10 | $1.25 |
| (default/unknown) | $0.27 | $1.10 | $0.07 | $0.27 |

> Rates are the *modelled* estimate (clearly labelled on the page). Token COUNTS are
> real from state.db; only the $ rate is our table.

## 4. Views on the dashboard
1. **Headline strip** — total tokens, total $ spend (period), projected month, projected year.
2. **Per-day** — tokens + $ by day (bar/sparkline), last ~90 days.
3. **Per-week** — buckets by ISO week.
4. **Per-month** — buckets by calendar month + MoM trend.
5. **Annual projection** — 12-mo rolling run-rate × monthly; show "if nothing changes".
6. **By source** — cron vs telegram vs subagent vs cli (token + $).
7. **By model** — token + $ per model.
8. **Top cost processes (cron job attribution)** — each cron job's runs, tokens, $/run, $/month.
9. **⚠ No-value / high-cost watch list** — processes with high $ but low observable output,
   or high runs×cost with no business artefact (heuristic + manual annotations).

## 5. "No value / costing lots" detection heuristic
- Rank cron jobs by **$/month**.
- Flag jobs that are **high-frequency + high token/run + rarely produce a changed artefact**
  (e.g. memory-log style jobs that just summarise). Cross-reference with known purpose.
- Show $/run so an every-30-min heavy job is obvious vs a daily useful one.
- Manual annotation column (VALUE: useful / review / candidate to cut) — start with a
  best-effort classification, Giles to confirm.

## 6. Build steps
1. `collect_token_spend.py` — read state.db, aggregate day/week/month, by source/model/job,
   compute $ from PRICING, write `data/token_spend.json`.
2. `build_token_dashboard.py` — render `data/os_status/token-spend.html` (self-contained HTML+inline
   SVG/CSS bars, no external deps), served by the existing OS portal on :8765.
3. Verify against real state.db numbers.
4. Wire a refresh into the OS Portal staggered refresh (and/or daily System Updates summary).

## 7. Governance
- READ-ONLY against state.db and config. No writes to Asana/DB. No secrets printed.
- Cost is a *model estimate*; clearly labelled. Token counts are ground truth.

## 8. BUILT (outcomes)
- `skills/token_dashboard/collect_token_spend.py` → `data/token_spend.json`
- `skills/token_dashboard/build_token_dashboard.py` → `data/os_status/token-spend.html`
- Cron: `AI Token Spend Dashboard Refresh` (job `8166d9c7c14d`), daily 09:07, wrapper
  `~/AppData/hermes/scripts/token_spend_refresh.py` — **silent on success**, alerts only on failure.
- Nav tile added to `build_nav.py` PAGES ("See where the AI token spend goes").

### First-run findings (2026-09-21, 84-day window, 351 sessions)
- Total est. **$104.18** / 84d → **$37.70/mo**, **$452.67/yr** projected.
- **Interactive (Telegram) = $77.27 (74%)** — the biggest lever; large context re-sent each turn.
- Background (cron) = $9.95; sub-agents = $6.00; CLI = $10.96.
- Top cron: Nightly Memory Log $4.29 (81 runs), Weekly Graph Rebuild $2.89, Nightly System Maintenance $1.76.
- Token counts real; $ modelled from PRICING table (update as rates change).

### CORRECTED PRICING (2026-09-21) — official DeepSeek rates applied
Source: api-docs.deepseek.com/quick_start/pricing. deepseek-chat = Flash rates
(cache-miss $0.15 / cache-hit $0.003 / output $0.60 off-peak; peak 2x). Peak = 01:00–04:00
& 06:00–10:00 UTC Mon–Fri. deepseek-v4-pro = $0.66 / $0.022 / $1.98 off-peak.

**Revised totals: $17.22 / 84d → $6.23/mo → $74.82/yr.** (The earlier figures were ~6x too
high because the old cache-read rate was wrong — cache hits bill at $0.003/1M, near-free.)

| Source | Est $ (84d) |
|---|---|
| Interactive (Telegram) | $8.81 |
| Sub-agents (delegation) | $4.01 |
| Background (cron) | $2.68 |
| CLI | $1.72 |
| Workspace scripts (email parser, brief) | ~$0 (instrumented 2026-09-21) |

### Nightly run value
- **Nightly Memory Log** (job `4133be14b0f8`, 22:00 daily): agent-driven; reads REQUESTS/ +
  BOM status, writes `memory/YYYY-MM-DD.md`. Cost **$0.35/mo** ($0.0118/run). Value: it DID
  catch a real failure (Weekly Graph Rebuild FAILED, 2026-09-20). But the memory files are
  NOT read by any script — value is human-read only. Very low cost, so keep unless unused.
- **Nightly System Maintenance** (job `ceed0bf46bec`, 02:00): $0.27/mo. Low cost.

### Email parser cost (now instrumented)
- `lib/llm.py` now logs every call to `data/llm_usage_log.jsonl` (these calls are OUTSIDE
  Hermes, so previously invisible to state.db).
- Measured: **~$0.000176/email**. At 30/day ≈ **$0.16/mo**; 60/day ≈ $0.32/mo. Negligible.
- Volume driver: `02_llm_parser.py` makes 1 LLM call per email (~200 miss + ~922 hit + ~238 out tokens).


