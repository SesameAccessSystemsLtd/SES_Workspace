"""
orchestrator.py — SesameAccess Contracts Finder Pipeline
=========================================================
Master runner that coordinates the full pipeline:

  1. Scrape Contracts Finder API for new relevant notices
  2. Enrich leads lacking contact emails via Apollo.io
  3. Push qualified leads into HubSpot (contacts, companies, lists, sequences)
  4. Optionally sweep for newly engaged contacts and promote to 'Interested'
  5. Export a CSV snapshot and update the run log

CAN BE TRIGGERED BY:
  a) OpenClaw SAD agent:  Drop a JSON file into ./REQUESTS/ (see REQUESTS_SCHEMA.json)
  b) Cowork (Claude):     Call run_pipeline() directly
  c) Cron / scheduler:   python orchestrator.py --mode=full
  d) Cowork schedule:    Configured via Cowork Schedule skill

DEDUPLICATION IS BELT-AND-BRACES:
  - Contract refs  → seen_contracts.json   (skip re-processing same notice)
  - Contact emails → seen_contacts_log.json (skip re-emailing in cooldown window)
  - HubSpot        → cf_last_outreach property on contact (source of truth)
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from config import (
    LOG_DIR, LOCK_FILE, OUTPUT_CSV,
    OUTREACH_COOLDOWN_DAYS,
)

# ─────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────
Path(LOG_DIR).mkdir(exist_ok=True)
log_path = Path(LOG_DIR) / f"orchestrator_{date.today().isoformat()}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_path),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger("cf_orchestrator")


# ─────────────────────────────────────────────
# Lock file — prevents overlapping runs
# ─────────────────────────────────────────────
def acquire_lock() -> bool:
    p = Path(LOCK_FILE)
    if p.exists():
        age_mins = (time.time() - p.stat().st_mtime) / 60
        if age_mins < 60:  # Consider stale after 60 minutes
            log.warning(f"Pipeline already running (lock age: {age_mins:.1f} min) — aborting")
            return False
        else:
            log.warning(f"Stale lock file found ({age_mins:.1f} min old) — removing and continuing")
            p.unlink()
    p.write_text(datetime.utcnow().isoformat())
    return True


def release_lock() -> None:
    p = Path(LOCK_FILE)
    if p.exists():
        p.unlink()


# ─────────────────────────────────────────────
# Run log (tracks every pipeline execution)
# ─────────────────────────────────────────────
RUN_LOG_FILE = Path(LOG_DIR) / "pipeline_run_history.json"


def load_run_history() -> list:
    if RUN_LOG_FILE.exists():
        return json.loads(RUN_LOG_FILE.read_text()).get("runs", [])
    return []


def save_run_record(record: dict) -> None:
    runs = load_run_history()
    runs.append(record)
    RUN_LOG_FILE.write_text(json.dumps({"runs": runs[-100:]}, indent=2))  # Keep last 100


# ─────────────────────────────────────────────
# Determine 'since' date for incremental runs
# ─────────────────────────────────────────────
def get_since_date(mode: str, explicit_since: str = None) -> str:
    """
    Return an ISO date string to use as the published_from filter.

    Modes:
      full        — last 180 days (initial population or reset)
      incremental — since last successful run (or 7 days if no history)
      date        — explicit date string passed in
    """
    if mode == "date" and explicit_since:
        return explicit_since

    if mode == "full":
        return (date.today() - timedelta(days=180)).isoformat()

    # Incremental — find last successful run
    runs = [r for r in load_run_history() if r.get("status") == "success"]
    if runs:
        last_date = runs[-1].get("run_date", "")
        if last_date:
            # Go back 1 day from last run to avoid gaps
            last = datetime.fromisoformat(last_date).date()
            return (last - timedelta(days=1)).isoformat()

    # No history — default to 7 days
    return (date.today() - timedelta(days=7)).isoformat()


# ─────────────────────────────────────────────
# REQUESTS folder handler (OpenClaw SAD integration)
# ─────────────────────────────────────────────
REQUESTS_DIR = Path("REQUESTS")


def check_for_agent_request() -> dict:
    """
    Check the REQUESTS/ folder for a pending pipeline trigger from another OpenClaw agent.
    Returns the parsed request dict, or {} if none found.
    Marks the file as processed by renaming to .done
    """
    REQUESTS_DIR.mkdir(exist_ok=True)
    pending = sorted(REQUESTS_DIR.glob("contracts_finder_*.json"))
    if not pending:
        return {}

    req_file = pending[0]  # Process oldest first
    try:
        req = json.loads(req_file.read_text())
        # Mark as processed
        done_path = req_file.with_suffix(".done")
        req_file.rename(done_path)
        log.info(f"Picked up agent request: {req_file.name}")
        return req
    except Exception as e:
        log.error(f"Could not read request file {req_file}: {e}")
        return {}


def write_agent_response(request_id: str, stats: dict, leads_count: int) -> None:
    """Write a response file back to REQUESTS/ so the requesting agent knows we're done."""
    REQUESTS_DIR.mkdir(exist_ok=True)
    resp = {
        "request_id":    request_id,
        "status":        "complete",
        "completed_at":  datetime.utcnow().isoformat(),
        "leads_found":   leads_count,
        "hubspot_stats": stats,
    }
    out = REQUESTS_DIR / f"RESPONSE_{request_id}_{date.today().isoformat()}.json"
    out.write_text(json.dumps(resp, indent=2))
    log.info(f"Response written: {out.name}")


# ─────────────────────────────────────────────
# Main pipeline
# ─────────────────────────────────────────────
def run_pipeline(
    mode: str = "incremental",
    since: str = None,
    skip_hubspot: bool = False,
    skip_apollo: bool = False,
    engagement_sweep: bool = True,
    request_id: str = None,
) -> dict:
    """
    Run the full pipeline.

    Args:
        mode:             "incremental" | "full" | "date"
        since:            ISO date string (used when mode="date")
        skip_hubspot:     Dry-run — scrape + enrich but don't push to HubSpot
        skip_apollo:      Skip Apollo enrichment (faster, no API cost)
        engagement_sweep: Run engagement sweep after push
        request_id:       Optional ID for inter-agent request tracking

    Returns:
        Summary dict with stats.
    """
    run_start = datetime.utcnow()
    log.info("=" * 70)
    log.info(f"SesameAccess Contracts Finder Pipeline — {run_start.strftime('%Y-%m-%d %H:%M UTC')}")
    log.info(f"Mode: {mode} | Apollo: {not skip_apollo} | HubSpot: {not skip_hubspot}")
    log.info("=" * 70)

    if not acquire_lock():
        return {"error": "pipeline_already_running"}

    summary = {
        "run_date":       run_start.isoformat(),
        "mode":           mode,
        "status":         "running",
        "scraped":        0,
        "enriched":       0,
        "hubspot_pushed": 0,
        "errors":         [],
    }

    try:
        # ── Step 1: Determine date range ─────────────────────────────────
        since_date = get_since_date(mode, explicit_since=since)
        log.info(f"\nStep 1: Scraping contracts since {since_date}")

        # ── Step 2: Scrape ────────────────────────────────────────────────
        from contracts_finder_scraper import run_scrape, export_csv
        leads = run_scrape(since_date=since_date, respect_seen=True)
        summary["scraped"] = len(leads)

        if not leads:
            log.info("No new leads found — pipeline complete")
            summary["status"] = "success"
            return summary

        # ── Step 3: Apollo enrichment ─────────────────────────────────────
        if not skip_apollo:
            log.info(f"\nStep 3: Apollo enrichment for leads missing contact email")
            from apollo_enrichment import enrich_leads
            leads = enrich_leads(leads)
            enriched = sum(1 for l in leads if l.get("found_via") == "apollo")
            summary["enriched"] = enriched
            log.info(f"  Apollo added {enriched} contacts")
        else:
            log.info("Step 3: Skipping Apollo (--no-apollo flag)")

        # ── Step 4: Export CSV ────────────────────────────────────────────
        export_csv(leads, OUTPUT_CSV)

        # ── Step 5: Push to HubSpot ───────────────────────────────────────
        if not skip_hubspot:
            log.info(f"\nStep 5: Pushing {len(leads)} leads to HubSpot")
            from hubspot_pipeline import push_leads_to_hubspot
            hs_stats = push_leads_to_hubspot(leads)
            summary["hubspot_pushed"] = hs_stats.get("pushed", 0)
            summary["hubspot_cooldown_skipped"] = hs_stats.get("skipped_cooldown", 0)
            summary["hubspot_no_email"] = hs_stats.get("skipped_no_email", 0)
            summary["hubspot_queued_to_email"] = hs_stats.get("pushed", 0)
        else:
            log.info("Step 5: Skipping HubSpot push (--dry-run flag)")

        # ── Step 6: Send queued emails ─────────────────────────────────────
        # After contacts are pushed as TO_EMAIL, send the actual outreach
        # emails via SMTP and update HubSpot to EMAILED. HubSpot's two
        # workflows then handle replies and NOT_Interested removals.
        if not skip_hubspot:
            log.info("\nStep 6: Sending queued outreach emails via SMTP")
            from email_sender import run_send_queue
            email_stats = run_send_queue(dry_run=False)
            summary["emails_sent"]   = email_stats.get("sent", 0)
            summary["emails_failed"] = email_stats.get("failed", 0)

        summary["status"] = "success"

    except Exception as e:
        log.exception(f"Pipeline error: {e}")
        summary["status"]  = "error"
        summary["errors"].append(str(e))

    finally:
        release_lock()
        run_end = datetime.utcnow()
        summary["duration_seconds"] = (run_end - run_start).total_seconds()
        save_run_record(summary)

        if request_id:
            write_agent_response(request_id, summary, summary.get("scraped", 0))

        log.info("\n" + "=" * 70)
        log.info("PIPELINE COMPLETE")
        log.info(f"  Status         : {summary['status']}")
        log.info(f"  Scraped        : {summary.get('scraped', 0)} new leads")
        log.info(f"  Apollo enriched: {summary.get('enriched', 0)}")
        log.info(f"  HubSpot pushed : {summary.get('hubspot_pushed', 0)}")
        log.info(f"  Duration       : {summary.get('duration_seconds', 0):.1f}s")
        log.info("=" * 70)

    return summary


# ─────────────────────────────────────────────
# Agent request listener (poll loop for SAD)
# ─────────────────────────────────────────────
def listen_for_requests(poll_interval_secs: int = 60) -> None:
    """
    Continuously poll the REQUESTS/ folder for trigger files from other
    OpenClaw agents (e.g. SAD, SAM). Used when running as a long-lived process.

    For cron-based use, just call run_pipeline() directly instead.
    """
    log.info(f"Listening for agent requests in {REQUESTS_DIR}/ (every {poll_interval_secs}s)")
    while True:
        req = check_for_agent_request()
        if req:
            mode    = req.get("mode", "incremental")
            since   = req.get("since", None)
            req_id  = req.get("request_id", "unknown")
            log.info(f"Agent request received — mode={mode}, since={since}, id={req_id}")
            run_pipeline(
                mode=mode,
                since=since,
                skip_hubspot=req.get("dry_run", False),
                request_id=req_id,
            )
        time.sleep(poll_interval_secs)


# ─────────────────────────────────────────────
# Status report
# ─────────────────────────────────────────────
def print_status() -> None:
    """Print a human-readable pipeline status report."""
    runs = load_run_history()
    print("\n── SesameAccess Contracts Finder Pipeline ─────────────────────")
    if not runs:
        print("  No runs recorded yet.")
        return

    last = runs[-1]
    print(f"  Last run     : {last.get('run_date', 'unknown')[:16]}")
    print(f"  Status       : {last.get('status', '?')}")
    print(f"  Scraped      : {last.get('scraped', 0)} leads")
    print(f"  Enriched     : {last.get('enriched', 0)} via Apollo")
    print(f"  HS pushed    : {last.get('hubspot_pushed', 0)}")
    print(f"  HS enrolled  : {last.get('hubspot_enrolled', 0)}")
    print(f"  Duration     : {last.get('duration_seconds', 0):.1f}s")
    print()
    print(f"  Total runs   : {len(runs)}")
    success = sum(1 for r in runs if r.get("status") == "success")
    print(f"  Successful   : {success}/{len(runs)}")
    print("─────────────────────────────────────────────────────────────────\n")


# ─────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="SesameAccess × Contracts Finder pipeline orchestrator"
    )
    parser.add_argument(
        "--mode", default="incremental",
        choices=["incremental", "full", "date", "status", "listen", "sweep"],
        help=(
            "incremental: since last run (default) | "
            "full: last 180 days | "
            "date: use --since | "
            "status: show run history | "
            "listen: poll REQUESTS/ folder | "
            "sweep: engagement sweep only"
        )
    )
    parser.add_argument("--since",       default=None, help="ISO date e.g. 2025-01-01 (mode=date only)")
    parser.add_argument("--dry-run",     action="store_true", help="Scrape + enrich but don't push to HubSpot")
    parser.add_argument("--no-apollo",   action="store_true", help="Skip Apollo enrichment")
    parser.add_argument("--no-sweep",    action="store_true", help="Skip engagement sweep")
    parser.add_argument("--poll-secs",   type=int, default=60, help="Polling interval for --mode=listen")
    args = parser.parse_args()

    if args.mode == "status":
        print_status()
        sys.exit(0)

    if args.mode == "listen":
        listen_for_requests(poll_interval_secs=args.poll_secs)
        sys.exit(0)

    if args.mode == "sweep":
        logging.basicConfig(level=logging.INFO)
        from hubspot_pipeline import run_engagement_sweep
        run_engagement_sweep(days_back=7)
        sys.exit(0)

    result = run_pipeline(
        mode=args.mode,
        since=args.since,
        skip_hubspot=args.dry_run,
        skip_apollo=args.no_apollo,
        engagement_sweep=not args.no_sweep,
    )
    sys.exit(0 if result.get("status") == "success" else 1)
