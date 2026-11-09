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

from .http import HttpError, safe_err
from .mapping import canonical_activity, choose_subtask
from .segments import build_segments
from .targets import NotInTarget, plan

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


def owned_entries(existing: list[dict], me_gid: str, window_start: str) -> dict[tuple[str, str], list[dict]]:
    """Entries the sync is allowed to change: created by the sync account, credited
    to another person, dated inside the window. Entries anyone logs for
    themselves (including the sync account's own user) are never touched."""
    owned: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for e in existing:
        if (e.get("created_by") or {}).get("gid") != me_gid:
            continue
        who = (e.get("assignee") or {}).get("gid")
        if not who or who == me_gid:
            continue
        day = (e.get("entered_on") or "")[:10]
        if day < window_start:
            continue
        owned[(who, day)].append(e)
    return owned


def diff_entries(existing: list[dict], desired: dict[tuple[str, str], int], me_gid: str, window_start: str):
    """Plan creates/updates/deletes for one task.

    desired:  {(assignee_gid, 'YYYY-MM-DD'): minutes}
    Returns (creates, updates, deletes): creates = [(assignee, date, minutes)],
    updates = [(entry_gid, minutes)], deletes = [entry_gid].
    """
    owned = owned_entries(existing, me_gid, window_start)
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
            try:
                part = self.asana.task(gid, PART_FIELDS)
                part["gid"] = gid
                subs = self.asana.subtasks(gid, SUB_FIELDS)
                manufacturing = self._manufacturing_project(part)
                for t in [part, *subs]:
                    t["gid"] = t.get("gid") or gid
                    projects = {(m.get("project") or {}).get("gid") for m in t.get("memberships") or []}
                    added = self._ensure_beta(t)
                    self._seed_estimate(t, added or self.beta in projects)
                    # Time counts against the job's manufacturing project when the
                    # task is in it, otherwise against the beta project it was added to.
                    t["_attrib"] = manufacturing if manufacturing in projects else self.beta
                part["_subtasks"] = subs
                part["_client"] = self._display(part, self.f_project_name) or self.fallback_client
                loaded.append(part)
            except Exception as e:  # a deleted or unreadable part must not stop the others
                self.stats["part_errors"] += 1
                self.exceptions.append(f"Part {gid}: {safe_err(e)}")
        return loaded

    @staticmethod
    def _manufacturing_project(part: dict) -> str | None:
        for m in part.get("memberships") or []:
            p = m.get("project") or {}
            if "manufacturing" in (p.get("name") or "").lower():
                return p["gid"]
        return None

    # -- Jibble side ---------------------------------------------------------
    def ensure_jibble(self, parts: list[dict]) -> dict[str, dict]:
        """Clients, projects and activities. Returns {jibble project id: part}."""
        clients = {c.get("name", "").strip().lower(): c["id"] for c in self.jibble.clients()}
        projects = self.jibble.projects()
        by_id = {p["id"]: p for p in projects}
        by_name = {(p.get("name") or "").strip().lower(): p for p in projects}

        mapping: dict[str, dict] = {}
        for part in parts:
            pid = jibble_project_id(part["gid"])
            pname = part["name"].strip()
            if pid in by_id:
                mapping[pid] = part
                continue
            if pname.lower() in by_name:
                mapping[by_name[pname.lower()]["id"]] = part  # adopt an existing project of the same name
                continue
            try:
                cname = part["_client"].strip()
                cid = clients.get(cname.lower())
                if not cid:
                    cid = jibble_client_id(cname)
                    self._create(self.jibble.create_client, cid, cname)
                    clients[cname.lower()] = cid
                    self.stats["jibble_clients_created"] += 1
                self._create(self.jibble.create_project, pid, pname, cid)
                self.stats["jibble_projects_created"] += 1
            except Exception as e:
                self.exceptions.append(f"Jibble project for part {part['gid']}: {safe_err(e)}")
            # The id is deterministic, so time clocked against it maps even if
            # the project was archived or the create was refused as a duplicate.
            mapping[pid] = part

        have = {(a.get("name") or "").strip().lower() for a in self.jibble.activities()}
        for name in self.activity_list:
            if name.lower() not in have:
                try:
                    self._create(self.jibble.create_activity, name)
                    self.stats["jibble_activities_created"] += 1
                except Exception as e:
                    self.exceptions.append(f"Jibble activity '{name}': {safe_err(e)}")
        return mapping

    @staticmethod
    def _create(fn, *args):
        """Create something in Jibble; a 409 means it already exists, which is fine."""
        try:
            return fn(*args)
        except HttpError as e:
            if e.status == 409:
                return None
            raise

    # -- time ----------------------------------------------------------------
    def run(self, users_by_email: dict[str, str], targets: list | None = None, works=None):
        """targets: where time is written (default: Asana native). works: optional
        ServiceWorks loader for site visits (job + dated visit tasks + location)."""
        me_gid = self.asana.me()["gid"]
        if targets is None:
            from .targets import AsanaTarget
            targets = [AsanaTarget(self.asana, me_gid, users_by_email)]
        parts = self.load_parts()
        today = date.today()
        start = (today - timedelta(days=self.window_days)).isoformat()
        jobs = []
        if works:
            part_tasks = {t["gid"] for part in parts for t in [part, *part["_subtasks"]]}
            try:
                # Tasks already holding synced site time are always reconciled (see works.load).
                held_works = set().union(*(t.held_tasks(self.beta, start) for t in targets)) - part_tasks
                jobs = works.load(held_works)
            except Exception as e:
                self.exceptions.append(f"Service works skipped this run: {safe_err(e)}")
                works, jobs = None, []
        for job in jobs:
            for t in [job, *job["_visits"]]:
                try:
                    self._ensure_beta(t)
                except Exception as e:
                    self.exceptions.append(f"Task {t['gid']}: not added to the beta project ({safe_err(e)})")
                t["_attrib"] = self.beta
        project_map = self.ensure_jibble(parts)
        if works:
            try:
                works.ensure_jibble(self.jibble)  # mapping uses deterministic ids, so a failure here only delays setup
            except Exception as e:
                self.exceptions.append(f"Service works Jibble setup: {safe_err(e)}")

        fetch_from = (today - timedelta(days=self.window_days + 1)).isoformat()
        people = {p["id"]: p for p in self.jibble.people()}
        activities = {a["id"]: a.get("name", "") for a in self.jibble.activities()}
        segments, skipped = build_segments(self.jibble.time_entries(fetch_from, today.isoformat()))
        self.stats["open_clock_ins_skipped"] = skipped["open"]
        if skipped["too_long"]:
            self.stats["blocks_over_16h_skipped"] = skipped["too_long"]
            self.exceptions.append(f"{skipped['too_long']} clocked block(s) over 16 h skipped (check for a missed clock-out in Jibble)")

        tasks = {t["gid"]: t for part in parts for t in [part, *part["_subtasks"]]}
        tasks.update({t["gid"]: t for job in jobs for t in [job, *job["_visits"]]})

        # Read what each target already holds, so time already synced stays put.
        owned: dict[str, dict[str, dict]] = {}
        for tgt in targets:
            owned[tgt.name] = {}
            for gid in tasks:
                try:
                    owned[tgt.name][gid] = tgt.owned(gid, start)
                except NotInTarget:
                    self.stats[f"{tgt.name}_tasks_not_found"] += 1
                except Exception as e:
                    self.stats[f"{tgt.name}_task_errors"] += 1
                    self.exceptions.append(f"{tgt.name} task {gid}: {safe_err(e)} (left unchanged)")
        primary = targets[0]
        held: dict[tuple, set[str]] = defaultdict(set)
        for gid, recs in owned[primary.name].items():
            for key in recs:
                held[key].add(gid)

        # Map each clocked block to (Asana task, person email)
        mapped: dict[tuple[str, str, str], float] = defaultdict(float)  # (task, email, day) -> minutes
        for seg in segments:
            if seg.date < start:
                continue
            part = project_map.get(seg.project_id)
            if not part and not (works and jobs and works.jobs_for(seg.project_id, seg.location_id, seg.coords)[0]):
                continue  # not a monitored part and not at a monitored site
            email = (people.get(seg.person_id, {}).get("email") or "").lower()
            worker = users_by_email.get(email)
            if not email:
                self.exceptions.append(f"Jibble person {seg.person_id}: no email; time not written")
                continue
            if part:
                act_name = activities.get(seg.activity_id, "")
                canon = canonical_activity(act_name) if act_name else None
                prefer = held.get((primary.user_for(email), seg.date))
                target = choose_subtask(canon, part["_subtasks"], part["name"], worker, prefer=prefer) if canon else None
                if target is None:
                    self.exceptions.append(f"No subtask for activity '{act_name or 'none'}' on part {part['gid']}; booked to the part task")
                    self.stats["unmatched_segments"] += 1
                task_gid = (target or part)["gid"]
            else:
                prefer = held.get((primary.user_for(email), seg.date))
                site_task, how = works.resolve(seg.project_id, seg.location_id, seg.coords, seg.date, worker, prefer)
                if site_task is None:
                    self.exceptions.append("Site time matched more than one job and no visit that day; not written")
                    self.stats["site_segments_ambiguous"] += 1
                    continue
                self.stats[f"site_segments_by_{how}"] += 1
                if how.endswith("-job"):
                    self.exceptions.append(f"No visit that day for job {site_task['gid']}; booked to the job task")
                task_gid = site_task["gid"]
            mapped[(task_gid, email, seg.date)] += seg.minutes
            self.stats["segments_mapped"] += 1

        for tgt in targets:
            self._apply(tgt, owned[tgt.name], mapped, tasks)
        if works:
            self.exceptions += works.exceptions
            for k, v in works.stats.items():
                self.stats[k] += v
        self.exceptions = sorted(set(self.exceptions))
        return dict(self.stats)

    def _apply(self, tgt, owned: dict[str, dict], mapped: dict, tasks: dict):
        desired: dict[str, dict[tuple, int]] = defaultdict(dict)
        acc: dict[str, dict[tuple, float]] = defaultdict(lambda: defaultdict(float))
        skipped_people = set()
        for (task_gid, email, day), minutes in mapped.items():
            user = tgt.user_for(email)
            if user is None:
                skipped_people.add(email)
                continue
            acc[task_gid][(user, day)] += minutes
        for task_gid, rows in acc.items():
            desired[task_gid] = {k: int(round(v)) for k, v in rows.items()}
        if skipped_people:
            self.stats[f"{tgt.name}_people_not_written"] = len(skipped_people)
            self.exceptions.append(f"{tgt.name}: {len(skipped_people)} person(s) with Jibble time have no {tgt.name} user the sync may write for")

        plans = {gid: plan(owned[gid], desired.get(gid, {})) for gid in owned}
        owned_total = sum(len(v) for recs in owned.values() for v in recs.values())
        delete_total = sum(len(p[2]) for p in plans.values())
        if delete_total > max(10, owned_total // 2):
            # Probably incomplete data: creating the new records while keeping the old would double count.
            self.stats[f"{tgt.name}_writes_held_back"] = delete_total
            self.exceptions.append(
                f"{tgt.name} safety stop: {delete_total} of {owned_total} synced records would be removed; "
                "no changes written this run (check Jibble data)")
            return
        for gid, (creates, updates, deletes) in plans.items():
            try:
                for user, day, minutes in creates:
                    if tgt.blocked(gid, user, day):
                        self.stats[f"{tgt.name}_skipped_hand_logged"] += 1
                        self.exceptions.append(f"{tgt.name} task {gid}: time already logged by hand for that person and day; not added")
                        continue
                    tgt.create(gid, user, day, minutes, tasks[gid].get("_attrib"))
                    self.stats[f"{tgt.name}_created"] += 1
                for record, user, day, minutes in updates:
                    tgt.update(gid, record, user, day, minutes)
                    self.stats[f"{tgt.name}_updated"] += 1
                for record in deletes:
                    tgt.delete(gid, record)
                    self.stats[f"{tgt.name}_deleted"] += 1
            except Exception as e:  # one task failing must not stop the rest
                self.stats[f"{tgt.name}_task_errors"] += 1
                self.exceptions.append(f"{tgt.name} task {gid}: {safe_err(e)}")
