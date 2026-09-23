#!/usr/bin/env python3
"""build_trigger_log.py — did every button press actually work?

THE QUESTION THIS ANSWERS
  Buttons on the portal stage actions. Until now nothing ever read back whether a
  press succeeded: the page said "Dispatched ✓" the moment the server accepted the
  POST, and what happened afterwards -- did execute_requests.py run it? did it
  fail? -- was invisible unless you went looking in the queue directories.

HOW OUTCOME IS RESOLVED (no new plumbing required)
  bom_dispatch_server logs every press to queues/TRIGGER_LOG/YYYY-MM.jsonl with the
  REQUESTS file it created. execute_requests.py already encodes the outcome by
  MOVING that file:

      queues/REQUESTS/            -> still queued, nothing has run it yet
      queues/REQUESTS_COMPLETE/   -> ran, succeeded
      queues/REQUESTS_FAILED/     -> ran, failed  (reason in the .md front matter)
      queues/REQUESTS_HELD/       -> parked
      (nowhere)                   -> file vanished; treat as LOST, never as success

  So the press log plus a directory lookup gives true end-to-end state without
  editing execute_requests.py at all.

  A press that was REJECTED at the server (bad action, auth, exception) never
  produced a request file; its outcome is the rejection itself and it shows as
  REJECTED, which is a real failure the old UI hid behind a retry button.

Usage:
    python build_trigger_log.py                 # -> data/os_status/trigger-log.html
    python build_trigger_log.py --days 14
    python build_trigger_log.py --json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_WS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_WS / "skills" / "os_portal"))

from portal_ui import btn, esc, foot, head, hero  # noqa: E402

LOG_DIR = _WS / "queues" / "TRIGGER_LOG"
QUEUES = {
    "QUEUED": _WS / "queues" / "REQUESTS",
    "COMPLETE": _WS / "queues" / "REQUESTS_COMPLETE",
    "FAILED": _WS / "queues" / "REQUESTS_FAILED",
    "HELD": _WS / "queues" / "REQUESTS_HELD",
}
OUT_DEFAULT = _WS / "data" / "os_status" / "trigger-log.html"

# outcome -> (pill class, human label)
STATE_UI = {
    "COMPLETE": ("ok", "completed"),
    "QUEUED": ("open", "queued — not run yet"),
    "HELD": ("warn", "held"),
    "FAILED": ("over", "FAILED"),
    "REJECTED": ("over", "rejected at the server"),
    "LOST": ("over", "LOST — request file is gone"),
    "NO-FILE": ("open", "no queue file (direct action)"),
}


def load_presses(days: int) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out = []
    if not LOG_DIR.exists():
        return out
    for f in sorted(LOG_DIR.glob("*.jsonl")):
        try:
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                try:
                    if datetime.fromisoformat(rec["at"]) < cutoff:
                        continue
                except (KeyError, ValueError):
                    pass
                out.append(rec)
        except OSError as e:
            print(f"[warn] {f.name}: {e}", file=sys.stderr)
    out.sort(key=lambda r: r.get("at", ""), reverse=True)
    return out


def resolve(rec: dict) -> tuple[str, str]:
    """(state, detail). Never guesses success -- a missing file is LOST, not done."""
    if not rec.get("accepted"):
        return "REJECTED", rec.get("error") or "server rejected the press"
    rf = rec.get("request_file") or ""
    if not rf:
        return "NO-FILE", "action completed inline; nothing queued"
    stem = Path(rf).name
    for state, d in QUEUES.items():
        p = d / stem
        if p.exists():
            detail = ""
            if state in ("FAILED", "COMPLETE", "HELD"):
                try:
                    txt = p.read_text(encoding="utf-8", errors="replace")
                    m = re.search(r"^Note:\s*(.+)$", txt, re.MULTILINE)
                    detail = m.group(1).strip() if m else ""
                except OSError:
                    pass
            return state, detail
    return "LOST", f"{stem} is not in any queue directory"


def build(days: int) -> str:
    presses = [dict(r, _state=resolve(r)[0], _detail=resolve(r)[1])
               for r in load_presses(days)]
    n = len(presses)
    counts: dict[str, int] = {}
    for p in presses:
        counts[p["_state"]] = counts.get(p["_state"], 0) + 1
    bad = sum(counts.get(k, 0) for k in ("FAILED", "REJECTED", "LOST"))
    pending = sum(counts.get(k, 0) for k in ("QUEUED", "HELD"))
    done = counts.get("COMPLETE", 0) + counts.get("NO-FILE", 0)

    rows = []
    for p in presses:
        cls, label = STATE_UI.get(p["_state"], ("open", p["_state"]))
        when = (p.get("at") or "")[:16].replace("T", " ")
        summ = " ".join(f"{k}={v}" for k, v in (p.get("summary") or {}).items())
        rows.append(
            f"<tr><td class='muted' style='white-space:nowrap'>{esc(when)}</td>"
            f"<td><code>{esc(p.get('action') or p.get('endpoint'))}</code></td>"
            f"<td class='muted'>{esc(summ)}</td>"
            f"<td><span class='pill {cls}'>{esc(label)}</span></td>"
            f"<td class='muted' style='font-size:12px'>{esc(p.get('_detail') or '')}</td>"
            f"<td class='muted' style='font-size:12px'>{esc(p.get('actor') or '')}</td></tr>")

    healthy = bad == 0
    banner = (f'<div class="note">Every press in the last {days} days reached a '
              f'definite outcome. Nothing failed, nothing was lost.</div>'
              if healthy and n else
              f'<div class="note warn"><b>{bad}</b> press(es) did not succeed — '
              f'failed, rejected, or the queue file has gone missing. Those rows are '
              f'red below. A press that "said Dispatched ✓" can still appear here: '
              f'the tick only ever meant the server accepted it.</div>'
              if n else
              '<div class="note">No presses logged yet. The log starts filling the '
              'first time anyone clicks an action button.</div>')

    return "\n".join([
        head("SAS OS — Button & Trigger Log", page_id="trigger-log"),
        hero("🎛 Button & Trigger Log",
             "Every action button press, and whether it actually completed. "
             "Outcome is read back from where the request was filed — not from what "
             "the page said at the time.",
             badges=[f"last {days}d: {n}", f"completed: {done}",
                     f"pending: {pending}", f"not succeeded: {bad}"]),
        banner,
        '<div class="card"><table>'
        '<tr><th>when</th><th>action</th><th>detail</th><th>outcome</th>'
        '<th>note</th><th>who</th></tr>' + "".join(rows) + '</table></div>',
        '<div class="cat"><h2>How outcome is decided</h2><div class="card">'
        '<table><tr><th>shown as</th><th>means</th></tr>'
        '<tr><td><span class="pill ok">completed</span></td><td>request file is in '
        '<code>REQUESTS_COMPLETE/</code> — it ran and succeeded</td></tr>'
        '<tr><td><span class="pill open">queued</span></td><td>still in '
        '<code>REQUESTS/</code> — accepted but nothing has run it yet</td></tr>'
        '<tr><td><span class="pill warn">held</span></td><td>parked in '
        '<code>REQUESTS_HELD/</code></td></tr>'
        '<tr><td><span class="pill over">FAILED</span></td><td>in '
        '<code>REQUESTS_FAILED/</code> — reason taken from the file\'s Note:</td></tr>'
        '<tr><td><span class="pill over">rejected</span></td><td>the server refused '
        'the press; no request was ever created</td></tr>'
        '<tr><td><span class="pill over">LOST</span></td><td>accepted, a file was '
        'named, and it is in none of the queue directories. Never treated as '
        'success.</td></tr></table></div></div>',
        f'<p class="muted" style="margin-top:28px;font-size:12px">Generated '
        f'{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC from '
        f'<code>queues/TRIGGER_LOG/</code> · resolved against the REQUESTS queues</p>',
        foot()])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if a.json:
        print(json.dumps([dict(r, state=resolve(r)[0], detail=resolve(r)[1])
                          for r in load_presses(a.days)], indent=2))
        return
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build(a.days), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
