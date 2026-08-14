"""
notify.py — Send a notification to Giles from any agent.

Delivery channel: Telegram. Elastic Email retired 2026-07.
A formatted HTML report is also saved to memory/nightly/ for local review.

The CLI interface is unchanged so existing callers (daily_brief,
nightly_audit, etc.) keep working — HTML bodies are converted to
plain text before Telegram delivery.

Usage:
    python notify.py --subject "Daily Review Ready" --body "Here is your summary..."
    python notify.py --subject "Blocker Alert" --body "<p>Something is blocked.</p>" --html
    python notify.py --subject "Weekly Review" --body "..." --from-agent "Navigator"
    python notify.py --subject "Test" --body "Hello" --dry-run
    python notify.py --subject "Status Report" --html-file "C:\\path\\to\\report.html" --from-agent "Navigator"
"""

import argparse
import json
import logging
import re
import sys
from datetime import datetime, timezone
from html import unescape
from pathlib import Path

import requests

# Same bot + chat as the rest of the purchasing pipeline (notify_telegram.py)
BOT_TOKEN = "8581592821:AAEUaZmMxx30pRPBEhqOwxYPnXbW2DXdUwo"
CHAT_ID   = "8551261362"
TELEGRAM_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

# Telegram hard limit is 4096 chars per message
MAX_CHARS = 4000

LOG_PATH = Path(r"C:\Users\GilesHugo\SES_Workspace\db\email_send_log.jsonl")

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)


def _html_to_text(html: str) -> str:
    """Crude but safe HTML → plain text: drop style/script, tags → whitespace."""
    text = re.sub(r"(?is)<(style|script).*?</\1>", "", html)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|tr|h[1-6]|li)>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    # Collapse runs of blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _append_log(subject: str, status: str, error: str | None, agent: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp":    datetime.now(timezone.utc).isoformat(),
        "campaign_tag": f"agent_notify_{agent.lower().replace(' ', '_')}",
        "to":           f"telegram:{CHAT_ID}",
        "subject":      subject,
        "from_agent":   agent,
        "status":       status,
        "error":        error,
    }
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def send_notification(
    subject: str,
    body: str,
    is_html: bool = False,
    from_agent: str = "SES",
    dry_run: bool = False,
) -> None:
    tagged_subject = f"[{from_agent}] {subject}"
    text = _html_to_text(body) if is_html else body

    message = f"📬 {tagged_subject}\n\n{text}"
    truncated = len(message) > MAX_CHARS
    if truncated:
        message = message[:MAX_CHARS] + "\n…(truncated)"

    if dry_run:
        log.info("[DRY-RUN] Would send via Telegram:")
        log.info("  Chat:    %s", CHAT_ID)
        log.info("  Subject: %s", tagged_subject)
        log.info("  Length:  %d chars%s", len(message), " (truncated)" if truncated else "")
        return

    try:
        resp = requests.post(
            TELEGRAM_URL,
            json={"chat_id": CHAT_ID, "text": message},
            timeout=15,
        )
        resp.raise_for_status()
        log.info("Sent via Telegram | subject: %s", tagged_subject)
        _append_log(tagged_subject, "sent", None, from_agent)
    except requests.exceptions.RequestException as e:
        log.error("Send failed: %s", e)
        _append_log(tagged_subject, "failed", str(e), from_agent)
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Send a notification to Giles (Telegram delivery)."
    )
    parser.add_argument("--subject",    required=True, help="Notification subject line.")
    parser.add_argument("--body",       default=None,  help="Body (plain text or HTML string).")
    parser.add_argument("--html-file",  default=None,
                        help="Path to an HTML file to use as the body. Overrides --body.")
    parser.add_argument("--html",       action="store_true",
                        help="Treat --body as HTML (default: plain text). Auto-set when --html-file is used.")
    parser.add_argument("--from-agent", default="SES",
                        help="Agent name shown in subject prefix and log (default: SES).")
    parser.add_argument("--dry-run",    action="store_true",
                        help="Show what would be sent without sending.")
    args = parser.parse_args()

    if args.html_file:
        html_path = Path(args.html_file)
        if not html_path.is_file():
            log.error("--html-file not found: %s", args.html_file)
            sys.exit(1)
        body = html_path.read_text(encoding="utf-8")
        is_html = True
    elif args.body is not None:
        body = args.body
        is_html = args.html
    else:
        log.error("Either --body or --html-file is required.")
        sys.exit(1)

    send_notification(
        subject=args.subject,
        body=body,
        is_html=is_html,
        from_agent=args.from_agent,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
