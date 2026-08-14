"""
notify_telegram.py — Telegram push notifications for the Purchasing agent.

Sends messages to Giles via the default OpenClaw Telegram bot.
Import send_alert() or send_summary() from other purchasing scripts.

Usage (standalone test):
    python notify_telegram.py --message "Test alert from purchasing agent"
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[3] / ".env")

# ── Telegram config (from openclaw.json) ─────────────────────────────────────
# Default bot token — routes to main agent / Giles DM
BOT_TOKEN = "8581592821:AAEUaZmMxx30pRPBEhqOwxYPnXbW2DXdUwo"
# Giles' Telegram user ID (confirmed from session history) — DM default
CHAT_ID   = "8551261362"

# Purchasing forum TOPIC — BOM-processing runs must post here (not the DM).
# Bot-API group chat id = -100 + internal id (3780215894). Thread/topic id = 3,
# matching the live BOM-push + REQUESTS crons (telegram:-1003780215894:3).
# Both env-overridable.
#
# ⚠️ RICHARD'S GROUP (requested 2026-08-14): Giles wants BOM-push notifications to
# go to Richard's Purchasing team. Chat = -1003927793083. NOTE: the t.me link
# https://t.me/c/3927793083/1 does NOT map to a live topic — topic 1 returns
# "message thread not found". Verified live: the bot @sessal_bot IS now a member
# (Giles added it as admin) and CAN post to General AND to topic 53 = "Private to
# giles" (Richard's private thread). To activate, set in ~/.hermes/.env:
#     PURCHASING_CHAT_ID=-1003927793083
#     PURCHASING_TOPIC_ID=53
# and restart bom_dispatch_server.py. PENDING GILES' CONFIRMATION on the target
# topic. Until then notifications run on the SESAME default below.
PURCHASING_CHAT_ID  = os.environ.get("PURCHASING_CHAT_ID", "-1003780215894")
PURCHASING_TOPIC_ID = os.environ.get("PURCHASING_TOPIC_ID", "3")

TELEGRAM_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"


def send_telegram(text: str, parse_mode: str = "HTML",
                  chat_id: str | None = None,
                  message_thread_id: str | None = None) -> bool:
    """
    Send a Telegram message. Defaults to Giles' DM (backward compatible).
    Pass chat_id + message_thread_id to target a group forum topic.
    Returns True on success, False on failure (never raises — notifications
    must never crash the main pipeline).
    """
    payload = {"chat_id": chat_id or CHAT_ID, "text": text, "parse_mode": parse_mode}
    if message_thread_id:
        payload["message_thread_id"] = int(message_thread_id)
    try:
        r = requests.post(TELEGRAM_URL, json=payload, timeout=10)
        r.raise_for_status()
        return True
    except Exception as e:
        # Log to stderr but do not propagate — notification failure is not fatal
        print(f"[notify_telegram] Failed to send: {e}", file=sys.stderr)
        return False


def send_purchasing_topic(text: str, parse_mode: str = "HTML") -> bool:
    """Post to the Purchasing forum topic. Use for any BOM-processing run notice."""
    return send_telegram(text, parse_mode=parse_mode,
                         chat_id=PURCHASING_CHAT_ID,
                         message_thread_id=PURCHASING_TOPIC_ID)


def send_alert(subject: str, detail: str, task_name: str = "", task_gid: str = "") -> bool:
    """
    Send a ⚠️ alert — for issues needing immediate attention.
    Optional task_name/task_gid adds a clickable Asana link.
    """
    lines = [f"⚠️ <b>[Purchasing Alert]</b>", f"<b>{subject}</b>", ""]
    if detail:
        lines.append(detail)
    if task_name and task_gid:
        url = f"https://app.asana.com/0/{task_gid}/{task_gid}"
        lines.append(f'📋 <a href="{url}">{task_name}</a>')
    lines.append(f"\n<i>{datetime.now(timezone.utc).strftime('%d %b %Y %H:%M')} UTC</i>")
    return send_telegram("\n".join(lines))


def send_summary(stats: dict) -> bool:
    """
    Send end-of-run summary.
    stats keys: emails_scanned, proposals, alerts, unmatched, invoices_skipped
    """
    proposals    = stats.get("proposals", 0)
    alerts       = stats.get("alerts", 0)
    unmatched    = stats.get("unmatched", 0)
    scanned      = stats.get("emails_scanned", 0)
    inv_skipped  = stats.get("invoices_skipped", 0)

    status_icon = "✅" if alerts == 0 else "⚠️"
    lines = [
        f"{status_icon} <b>[Purchasing] Email parser run complete</b>",
        f"",
        f"📧 Emails scanned: {scanned}",
        f"📋 Proposals generated: {proposals}",
        f"🔔 Alerts raised: {alerts}",
        f"❓ Unmatched senders: {unmatched}",
        f"🧾 Invoices skipped: {inv_skipped}",
        f"",
        f"<i>{datetime.now(timezone.utc).strftime('%d %b %Y %H:%M')} UTC</i>",
    ]
    return send_telegram("\n".join(lines))


def main():
    parser = argparse.ArgumentParser(description="Send a test Telegram notification")
    parser.add_argument("--message", required=True, help="Message to send")
    args = parser.parse_args()
    ok = send_telegram(args.message)
    print("Sent." if ok else "Failed — check stderr.")


if __name__ == "__main__":
    main()
