#!/usr/bin/env python3
"""
collect_token_spend.py — Aggregate AI token usage + estimated cost from Hermes state.db.

READ-ONLY. Reads sessions table, computes cost from tokens × per-model PRICING,
writes data/token_spend.json consumed by build_token_dashboard.py.

Ground truth: token COUNTS come straight from state.db (real).
Estimate: the $ figure uses the PRICING table below (clearly labelled on the page).

Usage:  python collect_token_spend.py [--db PATH] [--out PATH]
"""
import os
import re
import json
import sqlite3
import argparse
import datetime
from collections import defaultdict

HERMES_HOME = os.path.expandvars(r"%LOCALAPPDATA%\hermes")
DEFAULT_DB = os.path.join(HERMES_HOME, "state.db")
OUT_PATH = r"C:\Users\GilesHugo\SES_Workspace\data\token_spend.json"

# ── Pricing (USD per 1,000,000 tokens). Source: https://api-docs.deepseek.com/quick_start/pricing/ ──
# DeepSeek bills peak/off-peak. Off-peak = half of peak. Peak hours are 01:00–04:00 and
# 06:00–10:00 UTC, Mon–Fri (excluding Chinese public holidays); all other times off-peak.
# Tuple = (cache_miss_input, cache_hit_input, output). Peak = 2x these off-peak values.
# NOTE: DeepSeek's "cache miss input" = fresh prompt tokens = Hermes `input_tokens`;
#       "cache hit input"  = cached prompt tokens = Hermes `cache_read_tokens`.
PRICING_OFFPEAK = {
    # legacy deepseek-chat / deepseek-reasoner route to the Flash line → Flash rates
    "deepseek-chat":        (0.15, 0.003, 0.60),
    "deepseek-reasoner":    (0.15, 0.003, 0.60),
    # Exact per-model cards CONFIRMED against DeepSeek's own usage export (2026-09-21):
    #   deepseek-flash     : miss 0.15 / hit 0.003 / out 0.60  (cheaper legacy flash)
    #   deepseek-v4-flash  : miss 0.22 / hit 0.007 / out 0.66  (current flash)
    #   deepseek-v4-pro    : miss 0.66 / hit 0.022 / out 1.98
    "deepseek-flash":       (0.15, 0.003, 0.60),
    "deepseek-v4-flash":    (0.22, 0.007, 0.66),
    "deepseek-v4-pro":      (0.66, 0.022, 1.98),
    # non-DeepSeek fallbacks (no peak/off-peak — flat)
    "gemini-2.5-flash":     (0.30, 0.075, 2.50),
    "gemini-2.0-flash":     (0.10, 0.025, 0.40),
    "gemini-3.1-pro-preview": (1.25, 0.31, 10.00),
    "claude-haiku-4-5-20251001": (1.00, 0.10, 5.00),
    "claude-haiku-4-5":     (1.00, 0.10, 5.00),
}
DEFAULT_OFFPEAK = (0.15, 0.003, 0.60)  # assume Flash-rate for unknown DeepSeek models

# Models that follow DeepSeek peak/off-peak billing (2x at peak). Others are flat.
DEEPSEEK_MODELS = {"deepseek-chat", "deepseek-reasoner", "deepseek-flash",
                   "deepseek-v4-flash", "deepseek-v4-pro"}

# Peak hours (UTC), Mon–Fri: 01:00–04:00 and 06:00–10:00.
PEAK_BLOCKS_UTC = [(1, 4), (6, 10)]  # [start_hour, end_hour)


def _is_peak(dt):
    # dt already tz-naive in UTC (we build it from UTC timestamps)
    if dt.weekday() >= 5:   # Sat/Sun all off-peak
        return False
    for a, b in PEAK_BLOCKS_UTC:
        if a <= dt.hour < b:
            return True
    return False


def compute_cost(inp, out, cr, cw, model, dt):
    """Cost in USD. cw (cache writes) is not separately billed by DeepSeek, so ignored."""
    rates = PRICING_OFFPEAK.get((model or "").strip(), DEFAULT_OFFPEAK)
    cm, ch, o = rates
    mult = 2.0 if (model in DEEPSEEK_MODELS and dt and _is_peak(dt)) else 1.0
    return ((inp / 1e6) * cm + (cr / 1e6) * ch + (out / 1e6) * o) * mult
PERIOD_LABEL = {"cron": "Background (cron)", "telegram": "Interactive (Telegram)",
                "subagent": "Sub-agents (delegation)", "cli": "CLI",
                "workspace-llm": "Workspace scripts (email parser, brief)", "none": "Other"}

# cron job_id -> human name (from cronjob list; keep in sync)
JOB_NAMES = {
    "02f75e6f8c82": "Nightly Asana Mirror Sync",
    "75e173a5ac10": "Purchasing Email Parser",
    "5253b0ca584d": "Nightly Codebase Audit",
    "0d81d0a0897b": "Email Approval Sheet Processor",
    "5316c57ffbe5": "Parts Control Propagator",
    "eb59aca7658d": "REQUESTS Processor",
    "fcdffdd011f5": "RingCentral Email Notes Sync",
    "d3a3719dfd3b": "Audit Parts Control Routing",
    "dd9fe4ed4a74": "Compile & Create Order Tasks",
    "2643ab11e841": "Arrived Stock Processor",
    "d7b6d25a21f0": "Navigator Daily Brief",
    "ceed0bf46bec": "Nightly System Maintenance",
    "4133be14b0f8": "Nightly Memory Log",
    "e7828bf1a14b": "Telegram Commander",
    "082ad1e02501": "Weekly Graph Rebuild",
    "45de2861006d": "Pack Association Reconcile",
    "82a59dc8cc1c": "BOM Push (Karol)",
    "c1d757352bb7": "OS Portal Status Refresh",
    "5bc5789adbd5": "OS Portal Giles Worklist",
    "798749daec84": "Parser QA Scorecard",
    "10c8bd2d0738": "BOM Release Watcher",
    "7d58e255c09d": "Gateway Restart Nudge",
    "cbbb2153eaa8": "Weekly Overdue Chase",
    "2be59faa8f13": "Weekly Email Gateway Reviewer",
    "2c544245ddfc": "Daily Picking & Booking Review",
    "57fbe5f8d633": "OS Portal BOM Tracker",
    "47fbad9b5a8f": "OS Portal Ops Oversight",
    "17ba09f7af52": "(retired/one-off cron)",
    "072f444a0e5c": "(retired/one-off cron)",
    "62f77518792d": "(retired/one-off cron)",
}

# Jobs flagged by intent as heavy / review candidates (best-effort; Giles confirms).
NO_VALUE_SUSPECT = {
    "4133be14b0f8": "Runs daily, highest cron token use — verify the memory log is actually read/used.",
    "ceed0bf46bec": "Runs daily; review whether nightly maintenance needs a full LLM pass.",
    "082ad1e02501": "Weekly graph rebuild — heavy cache reads; confirm value vs cost.",
}


def _job_of(session_id):
    m = re.match(r"cron_([0-9a-f]+)_", session_id or "")
    return m.group(1) if m else None


def collect(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id,source,model,input_tokens,output_tokens,cache_read_tokens,"
        "cache_write_tokens,reasoning_tokens,api_call_count,started_at,ended_at,"
        "title,estimated_cost_usd FROM sessions"
    ).fetchall()
    conn.close()

    day = defaultdict(lambda: {"in": 0, "out": 0, "cr": 0, "cw": 0, "cost": 0.0, "sessions": 0, "calls": 0})
    week = defaultdict(lambda: {"in": 0, "out": 0, "cr": 0, "cw": 0, "cost": 0.0, "sessions": 0, "calls": 0})
    month = defaultdict(lambda: {"in": 0, "out": 0, "cr": 0, "cw": 0, "cost": 0.0, "sessions": 0, "calls": 0})
    by_source = defaultdict(lambda: {"in": 0, "out": 0, "cr": 0, "cw": 0, "cost": 0.0, "sessions": 0})
    by_model = defaultdict(lambda: {"in": 0, "out": 0, "cr": 0, "cw": 0, "cost": 0.0, "sessions": 0})
    by_job = defaultdict(lambda: {"in": 0, "out": 0, "cr": 0, "cw": 0, "cost": 0.0, "runs": 0, "calls": 0,
                                  "name": None, "last": None})

    total = {"in": 0, "out": 0, "cr": 0, "cw": 0, "cost": 0.0, "sessions": 0, "calls": 0}
    tmin = tmax = None

    for r in rows:
        if not r["started_at"]:
            continue
        ts = float(r["started_at"])
        dt = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)  # UTC for peak + bucketing
        inp = r["input_tokens"] or 0
        out = r["output_tokens"] or 0
        cr = r["cache_read_tokens"] or 0
        cw = r["cache_write_tokens"] or 0
        calls = r["api_call_count"] or 0
        c = compute_cost(inp, out, cr, cw, r["model"], dt)
        model = (r["model"] or "unknown")
        src = (r["source"] or "none")

        dkey = dt.strftime("%Y-%m-%d")
        wkey = f"{dt.isocalendar()[0]}-W{dt.isocalendar()[1]:02d}"
        mkey = dt.strftime("%Y-%m")

        for bucket, key in ((day, dkey), (week, wkey), (month, mkey)):
            b = bucket[key]
            b["in"] += inp; b["out"] += out; b["cr"] += cr; b["cw"] += cw
            b["cost"] += c; b["sessions"] += 1; b["calls"] += calls

        s = by_source[src]
        s["in"] += inp; s["out"] += out; s["cr"] += cr; s["cw"] += cw; s["cost"] += c; s["sessions"] += 1

        md = by_model[model]
        md["in"] += inp; md["out"] += out; md["cr"] += cr; md["cw"] += cw; md["cost"] += c; md["sessions"] += 1

        if src == "cron":
            jid = _job_of(r["id"])
            if jid:
                j = by_job[jid]
                j["in"] += inp; j["out"] += out; j["cr"] += cr; j["cw"] += cw
                j["cost"] += c; j["runs"] += 1; j["calls"] += calls
                j["name"] = JOB_NAMES.get(jid, jid)
                j["last"] = max(j["last"] or 0, ts)

        total["in"] += inp; total["out"] += out; total["cr"] += cr; total["cw"] += cw
        total["cost"] += c; total["sessions"] += 1; total["calls"] += calls
        tmin = ts if tmin is None else min(tmin, ts)
        tmax = ts if tmax is None else max(tmax, ts)

    # ── Fold in workspace LLM calls (lib/llm.py → DeepSeek etc.) ──────────────
    # These run OUTSIDE Hermes (email parser, daily brief), so they are NOT in
    # state.db. They are logged to data/llm_usage_log.jsonl by lib/llm.py.
    usage_log = os.path.join(os.path.dirname(OUT_PATH), "llm_usage_log.jsonl")
    ws_calls = 0
    if os.path.exists(usage_log):
        with open(usage_log, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                try:
                    dt = datetime.datetime.fromisoformat(rec["ts"])
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=datetime.timezone.utc)
                except Exception:
                    continue
                # DeepSeek usage: prompt_tokens = cache_hit + cache_miss. We bill the
                # miss at cache-miss rate and the hit at cache-hit rate. inp=miss,
                # cr=hit (matches Hermes column semantics), out=completion.
                p = rec.get("prompt_tokens") or 0
                cm_hit = rec.get("cache_hit_tokens") or 0
                cm_miss = rec.get("cache_miss_tokens") or 0
                if cm_hit or cm_miss:
                    inp = cm_miss
                    cr = cm_hit
                else:
                    inp = p
                    cr = 0
                out = rec.get("completion_tokens") or 0
                model = rec.get("model") or "deepseek-chat"
                c = compute_cost(inp, out, cr, 0, model, dt)
                ts = dt.timestamp()
                dkey = dt.strftime("%Y-%m-%d"); wkey = f"{dt.isocalendar()[0]}-W{dt.isocalendar()[1]:02d}"; mkey = dt.strftime("%Y-%m")
                for bucket, key in ((day, dkey), (week, wkey), (month, mkey)):
                    b = bucket[key]; b["in"] += inp; b["out"] += out; b["cr"] += cr; b["cost"] += c; b["calls"] += 1
                s = by_source["workspace-llm"]; s["in"] += inp; s["out"] += out; s["cr"] += cr; s["cost"] += c; s["sessions"] += 1
                md = by_model[model]; md["in"] += inp; md["out"] += out; md["cr"] += cr; md["cost"] += c; md["sessions"] += 1
                total["in"] += inp; total["out"] += out; total["cr"] += cr; total["cost"] += c; total["calls"] += 1
                ws_calls += 1
                tmin = ts if tmin is None else min(tmin, ts)
                tmax = ts if tmax is None else max(tmax, ts)

    # Days elapsed for run-rate (inclusive)
    if tmin and tmax:
        days = max(1, int((tmax - tmin) / 86400) + 1)
    else:
        days = 1
    per_day = total["cost"] / days

    jobs = []
    for jid, j in by_job.items():
        runs = max(1, j["runs"])
        jobs.append({
            "job_id": jid,
            "name": j["name"] or jid,
            "runs": j["runs"],
            "in": j["in"], "out": j["out"], "cr": j["cr"], "cw": j["cw"],
            "tokens": j["in"] + j["out"],
            "cost": round(j["cost"], 4),
            "cost_per_run": round(j["cost"] / runs, 5),
            "cost_per_month": round(j["cost"] / days * 30.4, 3),
            "last": j["last"],
            "flag": NO_VALUE_SUSPECT.get(jid, ""),
        })
    jobs.sort(key=lambda x: -x["cost"])

    # ── Value assessment (safe, additive; drives the on-page verdict block) ────
    # Email-parser per-email cost was measured live from lib/llm.py telemetry:
    # 5 sample calls → prompt≈1,122 (cache hit ~922 + miss ~200), completion ~238
    # tokens → ≈ $0.000176/email at Flash off-peak. Monthly scaled by email volume.
    EMAIL_USD = 0.000176
    email_volume = 30  # emails/day observed (email pipeline runs hourly, 9–16 weekdays)
    src = {s["k"]: s for s in
           [{"k": k, "label": PERIOD_LABEL.get(k, k), **v, "cost": round(v["cost"], 4)}
            for k, v in by_source.items()]}
    inter = src.get("telegram", {}).get("cost", 0.0)
    cron = src.get("cron", {}).get("cost", 0.0)
    wsllm = src.get("workspace-llm", {}).get("cost", 0.0)
    nightly = [j for j in jobs if j["name"].startswith("Nightly")]
    nightly_month = round(sum(j["cost_per_month"] for j in nightly), 3)
    assessment = {
        "headline": (f"Total AI spend is ~${round(per_day * 30.4, 2)}/month. Interactive Telegram "
                     f"usage is the single biggest lever ({round(100 * inter / (total['cost'] or 1))}% of spend); "
                     f"background cron is only {round(100 * cron / (total['cost'] or 1))}%."),
        "email_parser": {
            "usd_per_email": EMAIL_USD,
            "emails_per_day": email_volume,
            "usd_per_month": round(EMAIL_USD * email_volume * 30.4, 3),
            "note": ("Email parsing runs via lib/llm.py OUTSIDE Hermes, so it never touched state.db "
                     "until instrumented. Per-email cost is near-zero; only volume matters."),
        },
        "nightly": {
            "jobs": [j["name"] for j in nightly],
            "monthly": nightly_month,
            "note": ("Nightly summarisers run daily and are the highest cron token users, BUT nothing "
                     "reads their output — value is human-read-only. Cheap enough to keep, strongest "
                     "review candidate if the logs are never opened."),
        },
        "workspace_llm_month": round(wsllm / days * 30.4, 4),
    }

    # ── Reconciliation vs DeepSeek's own usage export (if present) ─────────────
    # state.db covers ONLY local Hermes agent sessions — it misses the VPS fleet,
    # the workspace scripts (email parser/brief) before instrumentation, and any
    # other client on the same API key. When Giles drops a DeepSeek usage export
    # into data/deepseek_usage/, we reconcile modelled-vs-billed so the page can
    # state the true monthly figure and the coverage ratio honestly.
    recon = None
    usage_dir = os.path.join(os.path.dirname(OUT_PATH), "deepseek_usage")
    try:
        import csv as _csv
        import glob as _glob
        cost_files = sorted(_glob.glob(os.path.join(usage_dir, "cost-*.csv")))
        if cost_files:
            latest = cost_files[-1]
            actual = 0.0
            billed_by_model = {}
            dates = set()
            with open(latest, encoding="utf-8-sig") as fh:
                for r in _csv.DictReader(fh):
                    c = float(r["cost"])
                    actual += c
                    billed_by_model[r["model"]] = billed_by_model.get(r["model"], 0.0) + c
                    dates.add(r["start_time_iso"][:10])
            ndays = max(1, len(dates))
            # modelled cost over the SAME window (by date overlap)
            start_d, end_d = min(dates), max(dates)
            modelled_win = 0.0
            for k, v in day.items():
                if start_d <= k <= end_d:
                    modelled_win += v["cost"]
            recon = {
                "export_file": os.path.basename(latest),
                "window": f"{start_d}..{end_d}",
                "days": ndays,
                "actual_billed": round(actual, 4),
                "actual_by_model": {m: round(c, 4) for m, c in sorted(billed_by_model.items(), key=lambda x: -x[1])},
                "modelled_same_window": round(modelled_win, 4),
                "coverage_pct": round(100 * modelled_win / actual, 1) if actual else None,
                "actual_per_day": round(actual / ndays, 4),
                "actual_month": round(actual / ndays * 30.4, 2),
                "actual_year": round(actual / ndays * 365, 2),
                "gap_note": ("state.db sees only LOCAL Hermes sessions; the billed total also "
                             "includes the VPS fleet, workspace scripts, and other clients on "
                             "the same API key. Treat 'actual' as the true spend and 'modelled' "
                             "as the local-coverage subset."),
            }
    except Exception as e:
        recon = {"error": f"reconcile failed: {e}"}

    out = {
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "db": db_path,
        "range": {
            "start": datetime.datetime.fromtimestamp(tmin, datetime.timezone.utc).strftime("%Y-%m-%d") if tmin else None,
            "end": datetime.datetime.fromtimestamp(tmax, datetime.timezone.utc).strftime("%Y-%m-%d") if tmax else None,
            "days": days,
        },
        "pricing_note": ("Token counts are real (Hermes state.db). $ = modelled from DeepSeek's official "
                         "peak/off-peak rates (api-docs.deepseek.com/quick_start/pricing), applied at each "
                         "session's actual UTC timestamp. deepseek-chat is billed at Flash rates "
                         "(legacy name routes to the Flash line). Cache-read tokens billed at the cache-hit rate."),
        "pricing": {"deepseek-chat (Flash rates)": {"offpeak_cache_miss": 0.15, "offpeak_cache_hit": 0.003, "offpeak_output": 0.60, "peak_multiplier": 2.0},
                    "deepseek-v4-pro": {"offpeak_cache_miss": 0.66, "offpeak_cache_hit": 0.022, "offpeak_output": 1.98, "peak_multiplier": 2.0}},
        "totals": {
            **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in total.items()},
            "cost_per_day": round(per_day, 4),
            "proj_month": round(per_day * 30.4, 2),
            "proj_year": round(per_day * 365, 2),
        },
        "day": sorted([{"k": k, **v, "cost": round(v["cost"], 4)} for k, v in day.items()], key=lambda x: x["k"]),
        "week": sorted([{"k": k, **v, "cost": round(v["cost"], 4)} for k, v in week.items()], key=lambda x: x["k"]),
        "month": sorted([{"k": k, **v, "cost": round(v["cost"], 4)} for k, v in month.items()], key=lambda x: x["k"]),
        "by_source": sorted([{"k": k, "label": PERIOD_LABEL.get(k, k), **v, "cost": round(v["cost"], 4)} for k, v in by_source.items()], key=lambda x: -x["cost"]),
        "by_model": sorted([{"k": k, **v, "cost": round(v["cost"], 4)} for k, v in by_model.items()], key=lambda x: -x["cost"]),
        "by_job": jobs,
        "assessment": assessment,
        "reconciliation": recon,
    }
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(f"OK wrote {OUT_PATH}")
    print(f"[spend] sessions={total['sessions']} range={out['range']['start']}..{out['range']['end']} "
          f"tok_in={total['in']:,} tok_out={total['out']:,} cacheR={total['cr']:,} "
          f"est_total=${total['cost']:.2f} proj_month=${out['totals']['proj_month']} proj_year=${out['totals']['proj_year']}")
    print(f"[spend] top cron jobs: " + " | ".join(f"{j['name']} ${j['cost']:.2f}" for j in jobs[:5]))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--out", default=OUT_PATH)
    args = ap.parse_args()
    if args.out != OUT_PATH:
        OUT_PATH = args.out
    collect(args.db)
