"""Match a Jibble activity to the Asana subtask it belongs to. Pure functions."""
from __future__ import annotations

import re

# Canonical Jibble activity -> words that start an Asana subtask name.
DEFAULT_ALIASES: dict[str, list[str]] = {
    "Welding / Fabrication": ["weld", "fabricat"],
    "Assembly": ["assembl"],
    "QC": ["qc"],
    "Wiring": ["electric", "wiring", "wire", "re-wire", "rewire"],
    "Cut": ["cut"],
    "Paint": ["paint"],
    "Drill": ["drill"],
    "Rework": [],  # matched by the rework rule below, not by prefix
}

REWORK_WORDS = ("re-work", "rework")


def _norm(text: str) -> str:
    return " ".join((text or "").lower().split())


def part_code(part_name: str) -> str:
    """'1555.1008.D.USMachineRoomCabinet.' -> '1555.1008.d.usmachineroomcabinet'"""
    return _norm(part_name).rstrip(". ")


def activities_for_subtask(name: str, aliases: dict[str, list[str]] | None = None) -> set[str]:
    """Which canonical activities an Asana subtask name stands for."""
    aliases = aliases or DEFAULT_ALIASES
    n = _norm(name)
    if any(w in n for w in REWORK_WORDS) or n.startswith("modify"):
        return {"Rework"}
    # The activity is the text before ' - ' (or '- '), e.g. 'Weld & Assemble  - 1555...'
    head = re.split(r"\s-\s|-\s", n, maxsplit=1)[0]
    found: set[str] = set()
    for piece in re.split(r"&|/|\band\b|\+", head):
        piece = piece.strip()
        if not piece:
            continue
        for canon, keys in aliases.items():
            if any(piece.startswith(k) for k in keys):
                found.add(canon)
    return found


def canonical_activity(jibble_name: str, aliases: dict[str, list[str]] | None = None) -> str | None:
    """Map a Jibble activity name onto a canonical activity."""
    aliases = aliases or DEFAULT_ALIASES
    n = _norm(jibble_name)
    for canon in aliases:
        if _norm(canon) == n:
            return canon
    if any(w in n for w in REWORK_WORDS):
        return "Rework"
    for canon, keys in aliases.items():
        if any(n.startswith(k) for k in keys):
            return canon
    return None


def choose_subtask(
    activity: str,
    subtasks: list[dict],
    part_name: str,
    worker_gid: str | None,
    aliases: dict[str, list[str]] | None = None,
    prefer: set[str] | None = None,
) -> dict | None:
    """Pick the subtask a block of time belongs to, or None when nothing matches.

    Preference when several match: a subtask that already holds this worker's
    synced time for the day (so past time never moves when subtasks are
    completed or reassigned), then open and assigned to this worker, then open,
    then named in the standard 'Activity - <part code>' form, then newest.
    """
    code = part_code(part_name)
    candidates = [s for s in subtasks if activity in activities_for_subtask(s.get("name", ""), aliases)]
    if not candidates:
        return None

    prefer = prefer or set()

    def score(s: dict):
        held = s.get("gid") in prefer
        is_open = not s.get("completed")
        mine = bool(worker_gid) and (s.get("assignee") or {}).get("gid") == worker_gid
        standard = bool(code) and code in _norm(s.get("name", ""))
        gid = str(s.get("gid") or "")
        newest = int(gid) if gid.isdigit() else 0  # Asana gids grow over time
        return (held, is_open and mine, is_open, standard, newest)

    return max(candidates, key=score)
