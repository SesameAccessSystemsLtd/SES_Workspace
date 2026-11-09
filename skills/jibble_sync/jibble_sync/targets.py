"""Where synced time is written: Asana native Actual time, Everhour, or both.

Each target keeps one record per (person, task, day) and is reconciled to the
Jibble totals every run. A target only ever changes records it owns:

* Asana: entries created by the sync account and credited to someone else.
* Everhour: records on monitored tasks for the people listed in
  `everhour.users` (people moved from Everhour timers to Jibble clock-in).
  Everhour keeps one record per person, task and day, so anyone on that list
  must stop logging those tasks in Everhour by hand, or the sync overwrites it.
"""
from __future__ import annotations

from collections import defaultdict

from .http import request

EVERHOUR = "https://api.everhour.com"


class AsanaTarget:
    name = "asana"

    def __init__(self, asana, me_gid: str, users_by_email: dict[str, str]):
        self.asana, self.me, self.users = asana, me_gid, users_by_email

    def user_for(self, email: str) -> str | None:
        gid = self.users.get((email or "").lower())
        return gid if gid and gid != self.me else None  # never write the sync account's own time

    def owned(self, task_gid: str, window_start: str) -> dict[tuple[str, str], list[dict]]:
        owned: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for e in self.asana.time_entries(task_gid):
            if (e.get("created_by") or {}).get("gid") != self.me:
                continue
            who = (e.get("assignee") or {}).get("gid")
            day = (e.get("entered_on") or "")[:10]
            if not who or who == self.me or day < window_start:
                continue
            owned[(who, day)].append({"id": e["gid"], "minutes": int(e.get("duration_minutes") or 0)})
        return owned

    def create(self, task_gid: str, user: str, day: str, minutes: int, attributable_to: str | None):
        body = {"duration_minutes": minutes, "entered_on": day, "assignee": user}
        if attributable_to:
            body["attributable_to"] = attributable_to
        self.asana.create_time_entry(task_gid, body)

    def update(self, task_gid: str, record: dict, user: str, day: str, minutes: int):
        self.asana.update_time_entry(record["id"], minutes)

    def delete(self, task_gid: str, record: dict):
        self.asana.delete_time_entry(record["id"])


class Everhour:
    """Minimal Everhour API client (X-Api-Key of an Everhour Admin)."""

    def __init__(self, api_key: str, *, dry_run: bool = False, log=None, http=request):
        self._h = {"X-Api-Key": api_key, "X-Accept-Version": "1.2"}
        self.dry_run, self.log, self._http = dry_run, log or (lambda *a: None), http

    def _get_pages(self, path: str, params: dict) -> list[dict]:
        out, page = [], 1
        while True:
            rows = self._http("GET", EVERHOUR + path, headers=self._h, params={**params, "limit": 500, "page": page}) or []
            out.extend(rows)
            if len(rows) < 500:
                return out
            page += 1

    def users(self) -> list[dict]:
        return self._http("GET", EVERHOUR + "/team/users", headers=self._h) or []

    def task_time(self, task_id: str, date_from: str, date_to: str) -> list[dict]:
        return self._get_pages(f"/tasks/{task_id}/time", {"from": date_from, "to": date_to})

    def _write(self, method: str, path: str, body, what: str):
        if self.dry_run:
            self.log(f"[dry-run] Everhour {what}")
            return None
        return self._http(method, EVERHOUR + path, headers=self._h, json_body=body)

    def add_time(self, task_id: str, user_id: int, day: str, seconds: int):
        return self._write("POST", "/time", {"task": task_id, "user": user_id, "date": day, "time": seconds,
                                             "comment": "Jibble"}, f"add {seconds}s to {task_id}")

    def set_time(self, record_id, task_id: str, user_id: int, day: str, seconds: int):
        return self._write("PUT", f"/time/{record_id}", {"task": task_id, "user": user_id, "date": day, "time": seconds},
                           f"set record {record_id} to {seconds}s")

    def delete_time(self, record_id):
        return self._write("DELETE", f"/time/{record_id}", None, f"delete record {record_id}")


class EverhourTarget:
    name = "everhour"

    def __init__(self, everhour: Everhour, allowed_emails: list[str], today: str):
        self.eh, self.today = everhour, today
        allowed = {e.lower() for e in allowed_emails}
        self.users = {u["email"].lower(): u["id"] for u in everhour.users()
                      if u.get("email") and u["email"].lower() in allowed and u.get("status", "active") != "removed"}
        self.allowed_ids = set(self.users.values())

    @staticmethod
    def task_id(task_gid: str) -> str:
        return f"as:{task_gid}"

    def user_for(self, email: str) -> int | None:
        return self.users.get((email or "").lower())

    def owned(self, task_gid: str, window_start: str) -> dict[tuple, list[dict]]:
        owned: dict[tuple, list[dict]] = defaultdict(list)
        for r in self.eh.task_time(self.task_id(task_gid), window_start, self.today):
            user = r.get("user")
            user = user.get("id") if isinstance(user, dict) else user
            day = (r.get("date") or "")[:10]
            if user in self.allowed_ids and day >= window_start and int(r.get("time") or 0) > 0:
                owned[(user, day)].append({"id": r["id"], "minutes": int(round(int(r["time"]) / 60))})
        return owned

    def create(self, task_gid: str, user, day: str, minutes: int, attributable_to: str | None):
        self.eh.add_time(self.task_id(task_gid), user, day, minutes * 60)

    def update(self, task_gid: str, record: dict, user, day: str, minutes: int):
        self.eh.set_time(record["id"], self.task_id(task_gid), user, day, minutes * 60)

    def delete(self, task_gid: str, record: dict):
        self.eh.delete_time(record["id"])


def plan(owned: dict[tuple, list[dict]], desired: dict[tuple, int]):
    """creates [(user, day, minutes)], updates [(record, user, day, minutes)], deletes [record]."""
    owned = {k: list(v) for k, v in owned.items()}
    creates, updates, deletes = [], [], []
    for key, minutes in desired.items():
        rows = owned.pop(key, [])
        if minutes <= 0:
            deletes += rows
            continue
        if not rows:
            creates.append((key[0], key[1], minutes))
            continue
        keep, extra = rows[0], rows[1:]
        if keep["minutes"] != minutes:
            updates.append((keep, key[0], key[1], minutes))
        deletes += extra
    for rows in owned.values():
        deletes += rows
    return creates, updates, deletes
