"""
telegram_commander.py — Inbound Telegram command router for SES automation.

Polls the purchasing bot for new messages from Giles and maps commands to
Hermes cron job triggers via the Hermes jobs API.

Run as a Hermes no_agent cron job every 5 minutes.

Commands Giles can send:
    /status         — reply with system health (last run times + errors)
    /run_pipeline   — trigger the email purchasing pipeline (01-04)
    /run_requests   — trigger execute_requests (REQUESTS processor)
    /run_orders     — trigger ordering_compile
    /run_sync       — trigger asana_mirror_sync
    /help           — list available commands
"""

import os
import json
import sys
import requests
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[3] / ".hermes" / ".env")

# ── Telegram config ────────────────────────────────────────────────────────────
BOT_TOKEN = "8581592821:AAEUaZmMxx30pRPBEhqOwxYPnXbW2DXdUwo"
CHAT_ID   = "8551261362"
BASE_URL  = f"https://api.telegram.org/bot{BOT_TOKEN}"

# ── State: track last processed update_id ─────────────────────────────────────
STATE_FILE = Path(__file__).parent / "telegram_commander_state.json"

# ── Hermes jobs.json path ──────────────────────────────────────────────────────
JOBS_FILE = Path(os.environ.get(
    "LOCALAPPDATA",
    str(Path.home() / "AppData" / "Local")
)) / "hermes" / "cron" / "jobs.json"

# ── Command → job name mapping ─────────────────────────────────────────────────
COMMAND_MAP = {
    "/run_pipeline":  "Purchasing Email Parser",
    "/run_requests":  "REQUESTS Processor",
    "/run_orders":    "Ordering - Compile and Create Order Tasks",
    "/run_sync":      "Nightly Asana Mirror Sync",
    "/run_bom_push":  "BOM Push (Karol Generate)",
}

HELP_TEXT = """SES Automation Commands:
/status — system health
/run_pipeline — email purchasing pipeline
/run_requests — process REQUESTS queue
/run_orders — compile and create order tasks
/run_sync — sync Asana mirror
/run_bom_push — push a Karol BOM to Stage 4 review
/help — this message"""


def _send(text: str) -> None:
    try:
        requests.post(
            f"{BASE_URL}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        print(f"Telegram send failed: {e}", file=sys.stderr)


def _load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"last_update_id": 0}


def _save_state(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def _get_updates(offset: int) -> list:
    try:
        r = requests.get(
            f"{BASE_URL}/getUpdates",
            params={"offset": offset, "timeout": 5, "allowed_updates": ["message"]},
            timeout=15,
        )
        r.raise_for_status()
        return r.json().get("result", [])
    except Exception as e:
        print(f"getUpdates failed: {e}", file=sys.stderr)
        return []


def _load_jobs() -> list:
    if not JOBS_FILE.exists():
        return []
    with open(JOBS_FILE) as f:
        return json.load(f).get("jobs", [])


def _save_jobs(jobs: list) -> None:
    data = {"jobs": jobs, "updated_at": datetime.now(timezone.utc).isoformat()}
    with open(JOBS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def _trigger_job(name: str) -> str:
    """Set job's next_run_at to now so it fires on next scheduler tick."""
    jobs = _load_jobs()
    now = datetime.now(timezone.utc).isoformat()
    for job in jobs:
        if job.get("name") == name and job.get("enabled", True):
            job["next_run_at"] = now
            job["state"] = "scheduled"
            _save_jobs(jobs)
            return f"Triggered: {name}"
    return f"Job not found: {name}"


def _status() -> str:
    jobs = _load_jobs()
    lines = ["<b>SES System Status</b>"]
    for job in jobs:
        if not job.get("enabled", True):
            continue
        name = job.get("name", job["id"])[:35]
        status = job.get("last_status") or "never"
        last_run = job.get("last_run_at", "—")
        if last_run and last_run != "—":
            last_run = last_run[:16].replace("T", " ")
        icon = "✅" if status == "ok" else ("❌" if status == "error" else "⏳")
        lines.append(f"{icon} {name}\n   {last_run}")
    return "\n".join(lines)


def main():
    state = _load_state()
    offset = state["last_update_id"] + 1

    updates = _get_updates(offset)
    if not updates:
        return

    for update in updates:
        update_id = update["update_id"]
        msg = update.get("message", {})
        from_id = str(msg.get("from", {}).get("id", ""))
        text = (msg.get("text") or "").strip()

        # Only respond to Giles
        if from_id != CHAT_ID:
            state["last_update_id"] = max(state["last_update_id"], update_id)
            continue

        if not text.startswith("/"):
            state["last_update_id"] = max(state["last_update_id"], update_id)
            continue

        cmd = text.split()[0].lower()

        if cmd == "/help":
            _send(HELP_TEXT)
        elif cmd == "/status":
            _send(_status())
        elif cmd in COMMAND_MAP:
            result = _trigger_job(COMMAND_MAP[cmd])
            _send(result)
        else:
            _send(f"Unknown command: {cmd}\nSend /help for list.")

        state["last_update_id"] = max(state["last_update_id"], update_id)

    _save_state(state)


if __name__ == "__main__":
    main()
