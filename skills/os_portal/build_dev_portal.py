#!/usr/bin/env python3
"""build_dev_portal.py — the development-work lane of the OS portal.

Renders queues/DEV_OPEN/ as a page: what is in progress, what is waiting on Giles's
sign-off, and what has gone stale. Sign-off happens here or in Asana; both end up in
the same place because dev_register.py sync keeps them paired.

Read-only over the register. The sign-off BUTTONS post to bom_dispatch_server's
/dev/signoff endpoint, which shells out to dev_register.py -- the page never edits
the register directly, so the append-only contract holds.

Usage:
    python build_dev_portal.py                 # -> data/os_status/dev-portal.html
    python build_dev_portal.py --out X.html
    python build_dev_portal.py --stale-hours 48
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_WS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_WS / "skills" / "os_portal"))

from portal_ui import btn, esc, foot, head, hero  # noqa: E402

REGISTER_DIR = _WS / "queues" / "DEV_OPEN"
OUT_DEFAULT = _WS / "data" / "os_status" / "dev-portal.html"
ASANA_ORG = "357939803556123"

LANES = [
    ("awaiting_signoff", "Waiting on you", "Signed-off work leaves the list. "
     "Nothing here is finished until you say so."),
    ("open", "In progress", "Opened by an agent and still being worked."),
    ("blocked", "Blocked", "Stopped on something. Needs a decision or a dependency."),
]


def load() -> list[dict]:
    if not REGISTER_DIR.exists():
        return []
    out = []
    for f in sorted(REGISTER_DIR.glob("*.json")):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as e:
            print(f"[warn] unreadable {f.name}: {e}", file=sys.stderr)
    return out


def state_of(rec) -> str:
    evs = rec.get("events") or []
    return evs[-1].get("status", "open") if evs else "open"


def touched(rec) -> datetime:
    evs = rec.get("events") or []
    ts = evs[-1].get("at") if evs else rec.get("created_at")
    try:
        return datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)


def age_label(dt: datetime) -> str:
    h = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
    if h < 1:
        return f"{int(h * 60)}m"
    if h < 48:
        return f"{h:.0f}h"
    return f"{h / 24:.0f}d"


def card(rec: dict, stale_hours: int) -> str:
    rid = esc(rec["id"])
    t = touched(rec)
    stale = (datetime.now(timezone.utc) - t) > timedelta(hours=stale_hours)
    st = state_of(rec)

    pills = []
    if rec.get("area"):
        pills.append(f'<span class="tag">{esc(rec["area"])}</span>')
    if rec.get("priority"):
        k = {"Critical": "over", "High": "warn", "Medium": "open", "Low": "ok"}.get(
            rec["priority"], "open")
        pills.append(f'<span class="pill {k}">{esc(rec["priority"])}</span>')
    if rec.get("effort"):
        pills.append(f'<span class="pill open">{esc(rec["effort"])}</span>')
    if stale:
        pills.append(f'<span class="pill over">stale {age_label(t)}</span>')

    links = []
    if rec.get("asana_gid"):
        links.append(f'<a href="https://app.asana.com/1/{ASANA_ORG}/task/'
                     f'{esc(rec["asana_gid"])}" target="_blank">Asana</a>')
    else:
        links.append('<span class="muted">not in Asana yet</span>')

    where = "".join(f"<code>{esc(w)}</code> " for w in (rec.get("where") or []))
    evs = rec.get("events") or []
    hist = "".join(
        f"<tr><td class='muted'>{esc((e.get('at') or '')[:16].replace('T', ' '))}</td>"
        f"<td>{esc(e.get('status', ''))}</td><td class='muted'>{esc(e.get('by', ''))}</td>"
        f"<td>{esc(e.get('note', ''))}</td></tr>" for e in evs)

    actions = []
    if st == "awaiting_signoff":
        actions.append(btn("✓ Sign off", f"sasSignoff(this,'{rid}','closed')",
                           "ok", small=True, action=True,
                           title="Closes the thread. Only you can do this."))
        actions.append(btn("↩ Send back", f"sasSignoff(this,'{rid}','open')",
                           "ghost", small=True, action=True,
                           title="Not done — reopen it."))
    elif st in ("open", "blocked"):
        actions.append(btn("Mark for sign-off", f"sasSignoff(this,'{rid}','awaiting_signoff')",
                           "ghost", small=True, action=True))
        actions.append(btn("✕ Cancel", f"sasSignoff(this,'{rid}','cancelled')",
                           "ghost", small=True, action=True,
                           title="Abandon this thread."))

    return f"""<div class="card" id="t-{rid}">
<div style="display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap">
  <div style="flex:1;min-width:280px">
    <div style="font-weight:600;font-size:14.5px">{esc(rec['objective'])}</div>
    <div class="muted" style="font-size:12.5px;margin-top:4px">
      {esc(rec.get('channel', '?'))} · {age_label(t)} since last event · {' · '.join(links)}
    </div>
    <div style="margin-top:8px">{' '.join(pills)}</div>
  </div>
  <div class="btnrow" style="align-items:flex-start">{''.join(actions)}</div>
</div>
<details class="vendor" style="margin-top:10px">
  <summary class="muted">touching {where or '<span class="muted">—</span>'} · {len(evs)} event{'' if len(evs)==1 else 's'}</summary>
  <table><tr><th>when</th><th>state</th><th>by</th><th>note</th></tr>{hist}</table>
</details>
</div>"""


SIGNOFF_JS = """
function sasSignoff(el, id, status){
  if(status==='closed' || status==='cancelled'){
    if(!confirm('This is final and only you can do it.\\n\\nSet '+id+' to '+status+'?')) return;
  }
  sasTrigger(el, {action:'dev_signoff', id:id, status:status}, 'Sign-off', '\\u2713 saved');
}
function sasOnSuccess(kind, res){
  // The register changed on disk; the page is a generated snapshot, so say so
  // rather than pretending the DOM is now authoritative.
  var n=document.getElementById('rebuild-note');
  if(n){ n.style.display='block'; }
}
"""


def build(stale_hours: int) -> str:
    recs = load()
    by_state: dict[str, list] = {}
    for r in recs:
        by_state.setdefault(state_of(r), []).append(r)
    for v in by_state.values():
        v.sort(key=touched)

    live = sum(len(by_state.get(k, [])) for k, _, _ in LANES)
    waiting = len(by_state.get("awaiting_signoff", []))
    stale_n = sum(1 for k, _, _ in LANES for r in by_state.get(k, [])
                  if (datetime.now(timezone.utc) - touched(r)) > timedelta(hours=stale_hours))
    unsynced = sum(1 for k, _, _ in LANES for r in by_state.get(k, [])
                   if not r.get("asana_gid"))

    parts = [
        head("SAS OS — Development Work", extra_js=SIGNOFF_JS),
        hero("🧭 Development Work in Progress",
             "Every thread an agent has opened across Hermes, Claude and Codex — "
             "what is running, what is waiting on you. Sign-off is yours alone.",
             badges=[f"live: {live}", f"awaiting sign-off: {waiting}",
                     f"stale >{stale_hours}h: {stale_n}", f"not in Asana: {unsynced}"]),
        '<div class="note" id="rebuild-note" style="display:none">'
        'Saved. This page is a generated snapshot — re-run <code>build_dev_portal.py</code> '
        '(or wait for the next refresh) to see it move lane.</div>',
    ]

    if not recs:
        parts.append('<div class="note">The register is empty. Threads appear here as '
                     'soon as an agent logs one with <code>dev_register.py log</code>.</div>')

    for key, title, blurb in LANES:
        rows = by_state.get(key, [])
        if not rows:
            continue
        parts.append(f'<div class="cat"><h2>{esc(title)} · {len(rows)}</h2>'
                     f'<p class="muted" style="font-size:12.5px;margin:-4px 0 12px">'
                     f'{esc(blurb)}</p>')
        parts += [card(r, stale_hours) for r in rows]
        parts.append("</div>")

    closed = by_state.get("closed", []) + by_state.get("cancelled", [])
    if closed:
        closed.sort(key=touched, reverse=True)
        items = "".join(
            f"<tr><td>{esc(r['objective'][:90])}</td>"
            f"<td class='muted'>{esc(state_of(r))}</td>"
            f"<td class='muted'>{esc(r.get('channel', ''))}</td>"
            f"<td class='muted'>{age_label(touched(r))} ago</td></tr>"
            for r in closed[:40])
        parts.append(f'<div class="cat"><h2>Closed · {len(closed)}</h2>'
                     f'<div class="card"><table>'
                     f'<tr><th>objective</th><th>state</th><th>channel</th><th>when</th></tr>'
                     f'{items}</table></div></div>')

    parts.append(f'<p class="muted" style="margin-top:32px;font-size:12px">'
                 f'Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC from '
                 f'<code>queues/DEV_OPEN/</code> · {len(recs)} threads total</p>')
    parts.append(foot())
    return "\n".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    ap.add_argument("--stale-hours", type=int, default=48)
    a = ap.parse_args()
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build(a.stale_hours), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
