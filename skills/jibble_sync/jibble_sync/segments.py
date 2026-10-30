"""Turn Jibble clock events into timed segments. Pure functions."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

MAX_SEGMENT_HOURS = 16


@dataclass(frozen=True)
class Segment:
    person_id: str
    date: str          # the Jibble 'belongsToDate' of the clock-in, YYYY-MM-DD
    project_id: str | None
    activity_id: str | None
    minutes: float
    entry_id: str


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def build_segments(entries: list[dict]) -> tuple[list[Segment], int]:
    """Pair each clock-in with the person's next clock event.

    A clock-in ends at the person's next event of any kind (a switch to
    another project/activity is a new 'In'). Returns the segments and the
    number of clock-ins still open (not yet ended), which are skipped.
    """
    live = [e for e in entries if (e.get("status") or "").lower() != "archived" and e.get("time")]
    by_person: dict[str, list[dict]] = {}
    for e in live:
        by_person.setdefault(e.get("personId"), []).append(e)

    segments: list[Segment] = []
    open_count = 0
    for person, events in by_person.items():
        events.sort(key=lambda e: _ts(e["time"]))
        for i, e in enumerate(events):
            if (e.get("type") or "").lower() != "in":
                continue
            if i + 1 >= len(events):
                open_count += 1
                continue
            minutes = (_ts(events[i + 1]["time"]) - _ts(e["time"])).total_seconds() / 60
            if minutes <= 0 or minutes > MAX_SEGMENT_HOURS * 60:
                continue
            date = (e.get("belongsToDate") or e["time"])[:10]
            segments.append(Segment(person, date, e.get("projectId"), e.get("activityId"), minutes, e.get("id", "")))
    return segments, open_count
