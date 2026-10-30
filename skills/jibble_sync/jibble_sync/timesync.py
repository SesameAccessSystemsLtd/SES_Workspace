"""Jibble clocked time -> Asana Actual time on the matching build subtask.

For each monitored part task (config: asana.monitored_parts):
  * the part and its subtasks are added to the beta project that carries
    Asana's Estimated time / Actual time fields;
  * Estimated time is seeded from Estimated Hours where it is still empty;
  * a Jibble client (the job) and a Jibble project (the part) exist;
  * Jibble time on that project is matched to a subtask by activity and
    written to Asana as one entry per worker, per task, per day.

Asana entries created by the integration account are owned by the sync and
reconciled every run, so edits and deletions in Jibble flow through.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, timedelta

from .mapping import canonical_activity, choose_subtask
from .segments import build_segments

ID_NAMESPACE = uuid.UUID("3f6b7c1e-9a52-4f0e-8c2d-5b1e7a9d4c30")

PART_FIELDS = (
    "name,completed,parent.gid,parent.name,memberships.project.gid,memberships.project.name,"
    "custom_fields.gid,custom_fields.number_value,custom_fields.display_value"
)
SUB_FIELDS = (
    "name,completed,assignee.gid,memberships.project.gid,"
    "custom_fields.gid,custom_fields.number_value"
)


def jibble_project_id(task_gid: str) -> str:
    return str(uuid.uuid5(ID_NAMESPACE, f"asana-task:{task_gid}"))


def jibble_client_id(name: str) -> str:
    return str(uuid.uuid5(ID_NAMESPACE, f"client:{name.strip().lower()}"))


def diff_entries(existing: list[dict], desired: dict[tuple[str, str], int], me_gid: str, window_start: str):
    """Plan creates/updates/deletes for one task.

    existing: Asana entries on the task. Only entries created by the sync
              account and dated inside the window are touched.
    desired:  {(assignee_gid, 'YYYY-MM-DD'): minutes}
    Returns (creates, updates, deletes): creates = [(assignee, date, minutes)],
    updates = [(entry_gid, minutes)], deletes = [entry_gid].
    """
    owned: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for e in existing:
        if (e.get("created_by") or {}).get("gid") != me_gid:
            continue
        day = (e.get("entered_on") or "")[:10]
        if day < window_start:
            continue
        who = (e.get("assignee") or {}).get("gid") or me_gid
        owned[(who, day)].append(e)

    creates, updates, deletes = [], [], []
    for key, minutes in desired.items():
        rows = owned.pop(key, [])
        if minutes <= 0:
            deletes += [r["gid"] for r in rows]
            continue
        if not rows:
            creates.append((key[0], key[1], minutes))
            continue
        keep, extra = rows[0], rows[1:]
        if int(keep.get("duration_minutes") or 0) != minutes:
            updates.append((keep["gid"], minutes))
        deletes += [r["gid"] for r in extra]
    for rows in owned.values():  # no longer backed by Jibble time
        deletes += [r["gid"] for r in rows]
    return creates, updates, deletes


class TimeSync:
    def __init__(self, cfg: dict, asana, jibble, log):
        self.cfg, self.asana, self.jibble, self.log = cfg, asana, jibble, log
        a = cfg["asana"]
        self.beta = a["beta_project"]
        self.parts = a.get("monitored_parts", [])
        f = a["fields"]
        self.f_project_name = f["project_name"]
        self.f_est_hours = f["estimated_hours"]
        self.f_est_time = f["estimated_time"]
        self.seed_estimates = cfg.get("seed_estimated_time_from_estimated_hours", True)
        self.window_days = cfg.get("time_window_days", 21)
        self.fallback_client = cfg["jibble"].get("fallback_client", "Jibble trial")
        self.activity_list = cfg["jibble"].get("activities", [])
        self.stats = defaultdict(int)
        self.exceptions: list[str] = []

    @staticmethod
    def _num(task: dict, gid: str):
        for c in task.get("custom_fields") or []:
            if c.get("gid") == gid:
                return c.get("number_value")
        return None

    @staticmethod
    def _display(task: dict, gid: str):
        for c in task.get("custom_fields") or []:
            if c.get("gid") == gid:
                return c.get("display_value")
        return None

    # -- Asana side ----------------------------------------------------------
    def _ensure_beta(self, task: dict):
        projects = {(m.get("project") or {}).get("gid") for m in task.get("memberships") or []}
        if self.beta not in projects:
            self.asana.add_to_project(task["gid"], self.beta)
            self.stats["added_to_beta"] += 1
            return True
        return False

    def _seed_estimate(self, task: dict, in_beta: bool):
        if not self.seed_estimates or not in_beta:
            return
        hours = self._num(task, self.f_est_hours)
        if hours and self._num(task, self.f_est_time) in (None, 0):
            minutes = int(round(float(hours) * 60))
            self.asana.update_task(task["gid"], {"custom_fields": {self.f_est_time: minutes}}, "seed Estimated time")
            self.stats["estimates_seeded"] += 1

    def load_parts(self) -> list[dict]:
        """Read every monitored part with its subtasks; make sure they sit in the beta project."""
        loaded = []
        for gid in self.parts:
            part = self.asana.task(gid, PART_FIELDS)
            part["gid"] = gid
            subs = self.asana.subtasks(gid, SUB_FIELDS)
            for t in [part, *subs]:
                added = self._ensure_beta(t)
                self._seed_estimate(t, True if added else self.beta in {(m.get("project") or {}).get("gid") for m in t.get("memberships") or []})
            part["_subtasks"] = subs
            part["_client"] = self._display(part, self.f_project_name) or self.fallback_client
            part["_attrib"] = self._attribution_project(part)
            loaded.append(part)
        return loaded

    def _attribution_project(self, part: dict) -> str:
        """Count time against the job's manufacturing project when there is one."""
        for m in part.get("memberships") or []:
            p = m.get("project") or {}
            if "manufacturing" in (p.get("name") or "").lower():
                return p["gid"]
        return self.beta

    # -- Jibble side ---------------------------------------------------------
    def ensure_jibble(self, parts: list[dict]) -> dict[str, dict]:
        """Clients, projects and activities. Returns {jibble project id: part}."""
        clients = {c.get("name", "").strip().lower(): c["id"] for c in self.jibble.clients()}
        projects = self.jibble.projects()
        by_id = {p["id"]: p for p in projects}
        by_name = {(p.get("name") or "").strip().lower(): p for p in projects}

        mapping: dict[str, dict] = {}
        for part in parts:
            cname = part["_client"].strip()
            cid = clients.get(cname.lower())
            if not cid:
                cid = jibble_client_id(cname)
                self.jibble.create_client(cid, cname)
                clients[cname.lower()] = cid
                self.stats["jibble_clients_created"] += 1
            pid = jibble_project_id(part["gid"])
            pname = part["name"].strip()
            if pid in by_id:
                mapping[pid] = part
            elif pname.lower() in by_name:
                mapping[by_name[pname.lower()]["id"]] = part  # adopt an existing project of the same name
            else:
                self.jibble.create_project(pid, pname, cid)
                mapping[pid] = part
                self.stats["jibble_projects_created"] += 1

        have = {(a.get("name") or "").strip().lower() for a in self.jibble.activities()}
        for name in self.activity_list:
            if name.lower() not in have:
                self.jibble.create_activity(name)
                self.stats["jibble_activities_created"] += 1
        return mapping

    # -- time ----------------------------------------------------------------
    def run(self, users_by_email: dict[str, str]):
        me_gid = self.asana.me()["gid"]
        parts = self.load_parts()
        project_map = self.ensure_jibble(parts)

        today = date.today()
        start = (today - timedelta(days=self.window_days)).isoformat()
        fetch_from = (today - timedelta(days=self.window_days + 1)).isoformat()
        people = {p["id"]: p for p in self.jibble.people()}
        activities = {a["id"]: a.get("name", "") for a in self.jibble.activities()}
        segments, open_count = build_segments(self.jibble.time_entries(fetch_from, today.isoformat()))
        self.stats["open_clock_ins_skipped"] = open_count

        # desired[task_gid][(assignee, day)] = minutes (float until rounded)
        desired: dict[str, dict[tuple[str, str], float]] = defaultdict(lambda: defaultdict(float))
        for seg in segments:
            if seg.date < start or seg.project_id not in project_map:
                continue
            part = project_map[seg.project_id]
            email = (people.get(seg.person_id, {}).get("email") or "").lower()
            worker = users_by_email.get(email)
            if not worker:
                self.exceptions.append(f"Jibble person {seg.person_id} has no Asana user; time credited to the sync account")
            act_name = activities.get(seg.activity_id, "")
            canon = canonical_activity(act_name) if act_name else None
            target = None
            if canon:
                target = choose_subtask(canon, part["_subtasks"], part["name"], worker)
            if target is None:
                target_gid = part["gid"]
                self.exceptions.append(
                    f"No subtask for activity '{act_name or 'none'}' on part {part['gid']}; booked to the part task"
                )
                self.stats["unmatched_segments"] += 1
            else:
                target_gid = target["gid"]
            desired[target_gid][(worker or me_gid, seg.date)] += seg.minutes
            self.stats["segments_mapped"] += 1

        # Reconcile every task the sync owns, so removed time is removed in Asana too.
        attrib = {}
        for part in parts:
            for t in [part, *part["_subtasks"]]:
                attrib[t["gid"]] = part["_attrib"]
        for task_gid in attrib:
            want = {k: int(round(v)) for k, v in desired.get(task_gid, {}).items()}
            try:
                existing = self.asana.time_entries(task_gid)
                creates, updates, deletes = diff_entries(existing, want, me_gid, start)
                for who, day, minutes in creates:
                    body = {"duration_minutes": minutes, "entered_on": day, "attributable_to": attrib[task_gid]}
                    if who != me_gid:
                        body["assignee"] = who
                    self.asana.create_time_entry(task_gid, body)
                    self.stats["entries_created"] += 1
                for entry_gid, minutes in updates:
                    self.asana.update_time_entry(entry_gid, minutes)
                    self.stats["entries_updated"] += 1
                for entry_gid in deletes:
                    self.asana.delete_time_entry(entry_gid)
                    self.stats["entries_deleted"] += 1
            except Exception as e:  # one task failing must not stop the rest
                self.stats["task_errors"] += 1
                self.exceptions.append(f"Task {task_gid}: {type(e).__name__}: {str(e)[:200]}")
        # De-duplicate repeated exception lines
        self.exceptions = sorted(set(self.exceptions))
        return dict(self.stats)
