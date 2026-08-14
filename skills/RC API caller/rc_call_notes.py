"""
rc_call_notes.py
================
Fetches AI call notes and transcripts from RingCentral ACE (AI Conversation Expert)
for all account calls and saves them as .txt files to OUTPUT_FOLDER.

Designed to run as a scheduled task (Windows Task Scheduler).
Configuration is read from a .env file in the same directory.

Required .env variables:
    RC_CLIENT_ID      - From the RC Developer Console (app credentials)
    RC_CLIENT_SECRET  - From the RC Developer Console (app credentials)
    RC_JWT            - JWT credential token from RC Developer Console
    RC_SERVER         - https://platform.ringcentral.com
    OUTPUT_FOLDER     - Local path where .txt files will be saved
    STATE_FILE        - Path to JSON file tracking processed calls (auto-created)
"""

import os
import json
import base64
import logging
import requests
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dotenv import load_dotenv

# ── Load config ────────────────────────────────────────────────────────────────

load_dotenv(dotenv_path=Path(__file__).parents[3] / ".env")

RC_CLIENT_ID     = os.getenv("RC_CLIENT_ID")
RC_CLIENT_SECRET = os.getenv("RC_CLIENT_SECRET")
RC_JWT           = os.getenv("RC_JWT")
RC_SERVER        = os.getenv("RC_SERVER", "https://platform.ringcentral.com")
OUTPUT_FOLDER    = Path(os.getenv("OUTPUT_FOLDER", "./call_notes"))
STATE_FILE       = Path(os.getenv("STATE_FILE", Path(__file__).parent / "processed_calls.json"))

# ── Logging ────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(Path(__file__).parent / "rc_call_notes.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

# ── Auth ───────────────────────────────────────────────────────────────────────

def get_access_token() -> str:
    """Authenticate via JWT and return a bearer access token."""
    credentials = base64.b64encode(
        f"{RC_CLIENT_ID}:{RC_CLIENT_SECRET}".encode()
    ).decode()
    resp = requests.post(
        f"{RC_SERVER}/restapi/oauth/token",
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": RC_JWT,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]

# ── State management ───────────────────────────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"processed_record_ids": [], "last_run": None}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

# ── RingCentral API calls ──────────────────────────────────────────────────────

def get_call_log(token: str, since: datetime) -> list:
    """
    Fetch all account voice calls with recordings since the given datetime.
    Uses the account-level endpoint so all team members' calls are included.
    """
    headers = {"Authorization": f"Bearer {token}"}
    params = {
        "type": "Voice",
        "withRecording": "true",
        "dateFrom": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "perPage": 250,
    }
    records = []
    url = f"{RC_SERVER}/restapi/v1.0/account/~/call-log"

    while url:
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        records.extend(data.get("records", []))
        # Follow pagination
        nav = data.get("navigation", {})
        next_page = nav.get("nextPage", {}).get("uri")
        url = next_page
        params = {}  # params are encoded in the next page URI

    log.info(f"Found {len(records)} call(s) with recordings since {since.date()}")
    return records


def get_ace_insights(token: str, record_id: str) -> dict | None:
    """Fetch ACE insights for a recording. Returns None if not yet available."""
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(
        f"{RC_SERVER}/ai/ringsense/v1/public/accounts/~/domains/pbx/records/{record_id}/insights",
        headers=headers,
        timeout=30,
    )
    if resp.status_code == 404:
        return None  # Notes not processed yet for this call
    resp.raise_for_status()
    return resp.json()

# ── File formatting ────────────────────────────────────────────────────────────

def format_notes_file(call: dict, insights: dict) -> str:
    """Format all available ACE data into a readable .txt file."""
    lines = []
    dt = datetime.fromisoformat(call["startTime"].replace("Z", "+00:00"))
    insight_data = insights.get("insights", {})
    speakers = {
        s["speakerId"]: s.get("name", "Unknown")
        for s in insights.get("speakerInfo", [])
    }

    lines += [
        "=" * 60,
        f"  CALL NOTES",
        f"  {dt.strftime('%A %d %B %Y  %H:%M')}",
        "=" * 60,
        f"Title:     {insights.get('title', 'Unknown')}",
        f"Direction: {insights.get('callDirection', 'Unknown')}",
        f"Duration:  {insights.get('recordingDurationMs', 0) // 1000}s",
        f"Record ID: {insights.get('sourceRecordId', '')}",
        "",
    ]

    if "Summary" in insight_data:
        lines.append("─── SUMMARY " + "─" * 48)
        for s in insight_data["Summary"]:
            lines.append(s.get("value", ""))
        lines.append("")

    if "CallNotes" in insight_data:
        lines.append("─── AI NOTES " + "─" * 47)
        for note in insight_data["CallNotes"]:
            lines.append(note.get("value", ""))
        lines.append("")

    if "BulletedSummary" in insight_data:
        lines.append("─── KEY POINTS " + "─" * 45)
        for item in insight_data["BulletedSummary"]:
            lines.append(f"• {item.get('value', '')}")
        lines.append("")

    if "NextSteps" in insight_data:
        lines.append("─── NEXT STEPS " + "─" * 45)
        for step in insight_data["NextSteps"]:
            speaker = speakers.get(step.get("speakerId", ""), "")
            prefix = f"[{speaker}] " if speaker else ""
            lines.append(f"• {prefix}{step.get('value', '')}")
        lines.append("")

    if "Sentiment" in insight_data:
        for s in insight_data["Sentiment"]:
            lines.append(f"Sentiment: {s.get('value', '')}")
        lines.append("")

    if "Transcript" in insight_data:
        lines.append("─── TRANSCRIPT " + "─" * 45)
        for utt in insight_data["Transcript"]:
            speaker = speakers.get(utt.get("speakerId", ""), "Unknown")
            start = utt.get("start", 0)
            mins, secs = divmod(int(start), 60)
            lines.append(f"[{mins:02d}:{secs:02d}] {speaker}: {utt.get('text', '')}")
        lines.append("")

    return "\n".join(lines)


def safe_filename(call: dict, insights: dict) -> str:
    """Generate a safe, descriptive filename for the .txt file."""
    dt = datetime.fromisoformat(call["startTime"].replace("Z", "+00:00"))
    title = insights.get("title", "Unknown Call")
    # Strip the "Call from X to Y" boilerplate to get the contact name
    for prefix in ("Call from ", "Call to ", "Incoming call from ", "Outgoing call to "):
        title = title.replace(prefix, "")
    safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in title).strip()
    safe = safe[:60]  # cap length
    return f"{dt.strftime('%Y-%m-%d_%H-%M')}_{safe}.txt"

# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    # Validate config
    missing = [v for v in ("RC_CLIENT_ID", "RC_CLIENT_SECRET", "RC_JWT") if not os.getenv(v)]
    if missing:
        log.error(f"Missing required environment variables: {', '.join(missing)}")
        raise SystemExit(1)

    state = load_state()
    processed = set(state["processed_record_ids"])

    # Look back further on first run; otherwise from last run (with 10-min overlap)
    if state["last_run"]:
        since = datetime.fromisoformat(state["last_run"]) - timedelta(minutes=10)
    else:
        since = datetime.now(timezone.utc) - timedelta(hours=48)
        log.info("First run — checking last 48 hours of calls")

    log.info("Authenticating with RingCentral...")
    token = get_access_token()

    calls = get_call_log(token, since)
    OUTPUT_FOLDER.mkdir(parents=True, exist_ok=True)
    new_count = 0

    for call in calls:
        recording = call.get("recording") or {}
        record_id = recording.get("id")
        if not record_id:
            continue
        if record_id in processed:
            continue

        log.info(f"Fetching ACE insights for recording {record_id}...")
        insights = get_ace_insights(token, record_id)

        if not insights:
            log.info(f"  → No ACE data yet for {record_id}, skipping")
            continue

        insight_data = insights.get("insights", {})
        if not any(k in insight_data for k in ("CallNotes", "Transcript", "Summary")):
            log.info(f"  → ACE data empty for {record_id}, skipping")
            processed.add(record_id)
            continue

        filename = safe_filename(call, insights)
        content = format_notes_file(call, insights)
        output_path = OUTPUT_FOLDER / filename
        output_path.write_text(content, encoding="utf-8")

        processed.add(record_id)
        new_count += 1
        log.info(f"  → Saved: {filename}")

    # Persist state (cap list to last 2000 IDs to avoid unbounded growth)
    state["processed_record_ids"] = list(processed)[-2000:]
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    save_state(state)

    log.info(f"Complete. {new_count} new call note(s) saved to {OUTPUT_FOLDER}")


if __name__ == "__main__":
    main()
