#!/usr/bin/env python3
"""build_nav.py — the OS portal home page, generated from a manifest.

WHY GENERATED
  nav.html was hand-maintained, so it drifted: tiles named after the data they
  showed ("Orders by Pack — build impact") rather than the decision they serve, and
  dead entries survived edits. Generating it from PAGES below means the home page
  cannot fall behind, and renaming a page is a one-line change.

NAMING RULE
  A tile title says WHAT YOU DO THERE, not what the page contains. "Push a BOM
  through the pipeline" beats "Karol BOM-processing Tracker" -- the second is the
  page's name, the first is the reason you clicked it. Titles start with a verb
  wherever the page has actions.

  `acts` marks a page with real action buttons, so the home page tells you up front
  where a click changes something and where it does not.

Usage:
    python build_nav.py            # -> data/os_status/nav.html
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

_WS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_WS / "skills" / "os_portal"))

from portal_ui import esc, foot, head, hero  # noqa: E402

OUT_DEFAULT = _WS / "data" / "os_status" / "nav.html"

# (href, icon, title, what it is for, tag, acts?)
PAGES = [
    ("Act on it — these pages change something", [
        ("bom-tracker.html", "🗂", "Push a BOM through the pipeline",
         "Every inbound BOM with its live stage. Stage a BOM to run to Stage 4, or "
         "stage the drawing pipeline to map parts to packs. Buttons dispatch to "
         "Telegram and file a request.", "BOM · dispatch", True),
        ("giles-worklist.html", "🛒", "Chase your open supplier orders",
         "Your orders → the parts they cover → the build packs waiting on them, "
         "with nudge actions.", "orders · nudge", True),
        ("orders-by-pack.html", "📦", "Unblock a build that is waiting on parts",
         "Seeded by build pack: due date, readiness, which orders are holding it up "
         "and how late. Nudge-build files a request.", "packs · nudge", True),
        ("dev-portal.html", "🧭", "Sign off development work",
         "Every thread an agent has opened across Hermes, Claude and Codex — what is "
         "running, what is blocked, what is waiting on you. Only you can close one.",
         "dev · sign-off", True),
    ]),
    ("Check it — read-only", [
        ("trigger-log.html", "🎛", "Check every button press actually worked",
         "The audit trail for every action button: what was pressed, whether it was "
         "accepted, and whether it completed, failed or went missing. Read back from "
         "the queues, not from what the page claimed at the time.",
         "audit · outcomes", False),
        ("ops-oversight.html", "📊", "Verify the automation is behaving",
         "Order lifecycle heat-map, outstanding confirmations, emails to chase, "
         "unmapped emails, per-order supplier-email ledger.", "QA", False),
        ("index.html", "🧑‍🏭", "See what Andy can build now",
         "Live Asana — open packs, subtasks, vendor orders and advised dates.",
         "build schedule", False),
        ("steel-profile-by-pack.html", "🏗", "See steel profile status per pack",
         "Which packs have their steel profile BOM imported and which are waiting.",
         "steel", False),
        ("deployment-tracker.html", "🚀", "Track the platform rollout (P1–P8)",
         "What is ready, in progress or blocked across the procurement/BOM platform, "
         "plus the edge-case ledger. Reference a stage by ID in chat, "
         "e.g. 'address P3'.", "deployment", False),
        ("token-spend.html", "💸", "See where the AI token spend goes",
         "Per-day / week / month AI token usage and estimated cost, projected month "
         "and year, and which background jobs cost the most — with a watch list of "
         "high-cost, low-value processes.", "cost · tokens", False),
    ]),
    ("Reference", [
        ("america-one-production-report-2026-08-03.html", "🇺🇸",
         "America One (1588) production report",
         "Project-specific snapshot: packs, orders, parts impact, blockers.",
         "project · 1588", False),
        ("../../views/stock_management.html", "📦", "Stock management",
         "Stock and inventory overview.", "inventory", False),
        ("../../views/roadblock_map.html", "🚧", "Roadblock map",
         "Where work is getting stuck across the system.", "systems", False),
        ("../../views/memory_architecture.html", "🧠", "Memory architecture",
         "How the agent estate stores and retrieves what it knows.", "systems", False),
    ]),
]

# Pages deliberately NOT listed, and why -- so nobody "helpfully" re-adds them.
RETIRED = [
    ("Live QA Action Portal (qa_portal.html on :8611)",
     "Removed 2026-09-18 at Giles's request. It needed a second local server "
     "(qa_server.py) running on a different port, which was one more thing to "
     "remember and one more thing to be down."),
]


def tile(href, ico, title, desc, tag, acts) -> str:
    badge = ('<span class="tag" style="background:color-mix(in srgb,var(--green) 16%,'
             'transparent);color:var(--green)">has actions</span>' if acts else "")
    return (f'<a class="tile" href="{esc(href)}"><div class="ico">{ico}</div>'
            f'<h3>{esc(title)}</h3><p>{esc(desc)}</p>'
            f'<span class="tag">{esc(tag)}</span> {badge}</a>')


def build() -> str:
    parts = [
        head("SAS OS — Portal Home", page_id="nav"),
        hero("SAS OS — Portal Home",
             "Every live page, named for what you do there. Each page remembers its "
             "own colour, so you can tell them apart at a glance.",
             badges=[f"{sum(len(p) for _, p in PAGES)} pages",
                     f"{sum(1 for _, p in PAGES for x in p if x[5])} with actions"],
             back=""),
    ]
    for section, pages in PAGES:
        parts.append(f'<div class="cat"><h2>{esc(section)}</h2><div class="grid">')
        parts += [tile(*p) for p in pages]
        parts.append("</div></div>")

    retired = "".join(f"<tr><td>{esc(n)}</td><td class='muted'>{esc(why)}</td></tr>"
                      for n, why in RETIRED)
    parts.append('<div class="cat"><h2>Retired</h2><div class="card"><table>'
                 '<tr><th>page</th><th>why it is gone</th></tr>'
                 f'{retired}</table></div></div>')
    parts.append(f'<p class="muted" style="margin-top:28px;font-size:12px">'
                 f'Generated by <code>build_nav.py</code> '
                 f'{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC — edit the PAGES '
                 f'manifest, not this file.</p>')
    parts.append(foot())
    return "\n".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    a = ap.parse_args()
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build(), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
