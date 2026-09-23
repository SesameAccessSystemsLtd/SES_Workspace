"""
morning_chase_report.py — the single 07:00 read-only morning report.

Reads ops.db only (via chase_rules). Replaces the paused Eisenhower daily_brief.
Prints a compact, Telegram-safe report to stdout (kept under ~3.5KB so it fits a
single Telegram message). Prints NOTHING when there is nothing to chase, so the
cron stays silent (empty stdout = no Telegram delivery).

Sections (only shown when non-empty):
  A. Overdue orders (awaiting confirmation)        — with Asana task links
  B. Outbound enquiries with no reply (>=3 working days) — top 12 shown
  C. Portal confirmations needing a PO ref captured — top 12 shown

Deliberate omission: raw Outlook webLinks (500+ char OWA URLs) are dropped because
they would blow the Telegram message limit. Giles finds the email in Outlook by the
recipient + subject shown; rule A keeps a short Asana link for the task itself.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import date

from chase_rules import rule_a, rule_b, rule_c

DEFAULT_DB = r"C:\Users\GilesHugo\SES_Workspace\db\ops.db"

RULE_B_SHOW = 12
RULE_C_SHOW = 12


def _connect(db: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    return conn


def asana_link(gid: str) -> str:
    return f"https://app.asana.com/0/0/{gid}" if gid else ""


def _fmt_date(iso: str) -> str:
    if not iso:
        return ""
    try:
        return date.fromisoformat(iso[:10]).strftime("%d %b").lstrip("0")
    except ValueError:
        return iso[:10]


def build_report(db: str = DEFAULT_DB) -> str:
    conn = _connect(db)
    a, gate_note = rule_a(conn)
    b = rule_b(conn)
    c = rule_c(conn)
    conn.close()

    if not a and not b and not c:
        return ""

    lines: list[str] = []
    lines.append(f"☀️ Chase report — {date.today().strftime('%a %d %b %Y')}")

    # ── A. Overdue orders ───────────────────────────────────────────────────
    if a:
        lines.append(f"\n📦 Overdue orders awaiting confirmation: {len(a)}")
        if gate_note:
            lines.append(f"   ({gate_note.strip().strip('()')})")
        for o in a:
            vendor = o["vendor"] or "?"
            rec = o["recipient"] or "— no recipient —"
            ref = f" ref={o['supplier_ref']}" if o["supplier_ref"] else ""
            lines.append(f"• {o['po']} {vendor[:18]} {o['days_overdue']}d | {o['method']}{ref}")
            lines.append(f"  → {rec[:38]}")
            if o["asana_gid"]:
                lines.append(f"  {asana_link(o['asana_gid'])}")

    # ── B. Outbound no-reply ────────────────────────────────────────────────
    if b:
        lines.append(f"\n📤 Outbound, no reply (≥3 working days): {len(b)}")
        for h in b[:RULE_B_SHOW]:
            lines.append(f"• {h['working_days_elapsed']}wd {h['subject'][:42]} → {h['to'][:32]}")
        if len(b) > RULE_B_SHOW:
            lines.append(f"  … and {len(b) - RULE_B_SHOW} more")

    # ── C. Portal confirmations ─────────────────────────────────────────────
    if c:
        lines.append(f"\n🛒 Portal confirmations, no PO captured: {len(c)}")
        for h in c[:RULE_C_SHOW]:
            lines.append(f"• {h['supplier'][:20]} | ref {h['supplier_ref']} | {h['subject'][:34]}")
        if len(c) > RULE_C_SHOW:
            lines.append(f"  … and {len(c) - RULE_C_SHOW} more")

    lines.append(f"\n— {len(a)} orders · {len(b)} no-reply · {len(c)} portal refs to capture")
    return "\n".join(lines)


def main() -> None:
    # cron-safe output hardening (cp1252 pipe under cron can't encode em-dash/arrows)
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(errors="replace")
        except Exception:
            pass
    try:
        report = build_report()
    except Exception as e:  # noqa: BLE001
        print(f"MORNING CHASE REPORT FAILED: {e}", file=sys.stderr)
        sys.exit(1)
    if report:
        print(report)
    # else: silent — nothing to chase.


if __name__ == "__main__":
    main()
