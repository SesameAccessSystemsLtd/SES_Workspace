"""Service works: site visits in TeamUp -> Jibble locations and projects -> Asana time.

For the next `days_ahead` days (and back over the time window), every TeamUp
task with a due date is a candidate visit. Its job is the nearest ancestor in
the 4.0 Service Schedule (falling back to the top of its parent chain). The
site postcode comes from the job's address fields ("Lift Address", "Site
Address", "Address") or a "SITE ADDRESS:" line in its notes, and is turned into
coordinates with postcodes.io. Only sites in the configured regions (London)
are kept.

In Jibble each site gets a Location with a geofence and each job a Project at
that location. A clocked block is matched to a job by its project, else by the
Jibble location, else by GPS distance to a site, and then to the visit task on
that date, preferring the one assigned to the worker. With no visit that day,
time is booked to the job task and reported as an exception.

Nothing here is written to the logs except counts and task IDs (public repo).
"""
from __future__ import annotations

import math
import re
import uuid
from collections import defaultdict
from datetime import date, timedelta

from .geo import find_postcode, lookup
from .http import HttpError, safe_err

ID_NAMESPACE = uuid.UUID("3f6b7c1e-9a52-4f0e-8c2d-5b1e7a9d4c30")  # same namespace as timesync

TASK_FIELDS = (
    "name,completed,due_on,start_on,assignee.gid,parent.gid,notes,memberships.project.gid,"
    "custom_fields.gid,custom_fields.display_value,custom_fields.text_value"
)
_SITE_LINE = re.compile(r"SITE ADDRESS\s*:\s*(.+)", re.I)


def location_id(postcode: str) -> str:
    return str(uuid.uuid5(ID_NAMESPACE, f"location:{postcode.upper()}"))


def job_project_id(job_gid: str) -> str:
    return str(uuid.uuid5(ID_NAMESPACE, f"asana-job:{job_gid}"))


def metres(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


def visit_window(task: dict) -> tuple[str, str]:
    due = task.get("due_on") or ""
    return (task.get("start_on") or due, due)


class ServiceWorks:
    def __init__(self, cfg: dict, asana, log, *, today: date | None = None, geocode=lookup):
        w = cfg.get("works") or {}
        self.asana, self.log, self.geocode = asana, log, geocode
        self.workspace = cfg["asana"]["workspace"]
        self.teamup = w["teamup_project"]
        self.schedule = w["service_schedule_project"]
        self.address_fields = w.get("address_fields", [])
        self.regions = {r.lower() for r in w.get("regions", ["London"])}
        self.radius = int(w.get("radius_m", 300))
        self.client_name = w.get("jibble_client", "Service & Repairs")
        self.generic_names = {n.strip().lower() for n in w.get("generic_projects", [])}
        self.generic_ids: set[str] = set()
        today = today or date.today()
        back = int(cfg.get("time_window_days", 21)) + 1  # visits stay loaded for the whole time window
        self.d0 = (today - timedelta(days=back)).isoformat()
        self.d1 = (today + timedelta(days=int(w.get("days_ahead", 30)))).isoformat()
        self.stats: dict[str, int] = defaultdict(int)
        self.exceptions: list[str] = []
        self._cache: dict[str, dict] = {}
        self.jobs: list[dict] = []
        self.by_project: dict[str, dict] = {}
        self.by_location: dict[str, list[dict]] = defaultdict(list)
        self.sites: dict[str, dict] = {}  # location id -> {"coords", "postcode"}

    # -- Asana ---------------------------------------------------------------
    def _task(self, gid: str) -> dict:
        if gid not in self._cache:
            t = self.asana.task(gid, TASK_FIELDS) or {}
            t["gid"] = gid
            self._cache[gid] = t
        return self._cache[gid]

    @staticmethod
    def _projects(task: dict) -> set[str]:
        return {(m.get("project") or {}).get("gid") for m in task.get("memberships") or []}

    def _ancestors(self, task: dict, depth: int = 4) -> list[dict]:
        out, seen = [], {task["gid"]}
        cur = task
        for _ in range(depth):
            pg = (cur.get("parent") or {}).get("gid")
            if not pg or pg in seen:
                break
            seen.add(pg)
            cur = self._task(pg)
            out.append(cur)
        return out

    def _address(self, tasks: list[dict]) -> str | None:
        """First postcode in the address fields of the job and its relatives, then 'SITE ADDRESS:' lines."""
        for t in tasks:
            for c in t.get("custom_fields") or []:
                if c.get("gid") in self.address_fields:
                    pc = find_postcode(c.get("text_value") or c.get("display_value"))
                    if pc:
                        return pc
        for t in tasks:
            for line in _SITE_LINE.findall(t.get("notes") or ""):
                pc = find_postcode(line)
                if pc:
                    return pc
        return None

    def candidate_visits(self) -> list[dict]:
        # due_on.after/before are exclusive, so widen by a day and filter here.
        after = (date.fromisoformat(self.d0) - timedelta(days=1)).isoformat()
        before = (date.fromisoformat(self.d1) + timedelta(days=60)).isoformat()
        rows = self.asana.search_tasks(
            self.workspace, {"projects.any": self.teamup, "due_on.after": after, "due_on.before": before}, TASK_FIELDS)
        out = []
        for t in rows:
            start, due = visit_window(t)
            if due and start <= self.d1 and due >= self.d0 and self.teamup in self._projects(t):
                self._cache[t["gid"]] = t
                out.append(t)
        return out

    def _job_of(self, task: dict) -> tuple[dict | None, list[dict]]:
        """(job, the job's own ancestors). The job is the nearest 4.0 Service Schedule record
        above the task (or the task itself), else the top of its parent chain."""
        chain = [task, *self._ancestors(task)]
        for i, t in enumerate(chain):
            if self.schedule in self._projects(t):
                return t, chain[i + 1:]
        if len(chain) > 1:
            return chain[-1], []
        return None, []

    def load(self, held: set[str] = frozenset()) -> list[dict]:
        """Jobs with their visits and site, London only. Each job: task dict + '_visits', '_postcode', '_coords'.

        `held` are tasks that already hold time the sync wrote. They are always
        loaded, even when search misses them (it lags behind edits) or they have
        moved out of the window, so their time is reconciled rather than duplicated.
        """
        try:
            visits = {t["gid"]: t for t in self.candidate_visits()}
        except Exception as e:
            self.exceptions.append(f"Service visits could not be read: {safe_err(e)}")
            return []
        self.stats["visits_in_window"] = len(visits)
        job_only = []
        for gid in sorted(held - set(visits)):
            try:
                t = self._task(gid)
            except Exception as e:
                self.exceptions.append(f"Task {gid} with synced time is unreadable: {safe_err(e)}")
                raise  # its time could not be reconciled; the caller skips works this run
            if self.teamup in self._projects(t):
                visits[gid] = t
                self.stats["held_visits_added_back"] += 1
            else:
                job_only.append(t)

        by_job: dict[str, dict] = {}
        postcodes: dict[str, str | None] = {}
        for t, is_visit in [*((v, True) for v in visits.values()), *((t, False) for t in job_only)]:
            try:
                job, above = self._job_of(t)
            except Exception as e:
                self.exceptions.append(f"Task {t['gid']}: parent unreadable ({safe_err(e)})")
                if t["gid"] in held:
                    raise
                continue
            if not is_visit:
                job, above = (t, above) if job is None or job["gid"] == t["gid"] else (None, [])
                if job is None:  # not a job task (e.g. a build subtask): left as it is
                    self.stats["held_tasks_not_works"] += 1
                    continue
            elif job is None or job["gid"] == t["gid"]:
                self.stats["visits_without_job"] += 1
                continue
            if job["gid"] not in postcodes:
                # Only the job and what sits above it decide the site, so it never depends on which visits are loaded.
                postcodes[job["gid"]] = self._address([job, *above])
                if not postcodes[job["gid"]] and self.schedule in self._projects(job):
                    self.exceptions.append(f"Job {job['gid']}: no postcode in its address fields; its visits are not mapped")
            if not postcodes[job["gid"]]:
                self.stats["visits_without_postcode"] += int(is_visit)
                continue
            entry = by_job.setdefault(job["gid"], {**job, "_visits": []})
            if is_visit and t["gid"] not in {x["gid"] for x in entry["_visits"]}:
                entry["_visits"].append(t)

        try:
            geo = self.geocode(sorted({p for p in postcodes.values() if p})) if by_job else {}
        except Exception as e:
            self.exceptions.append(f"Postcode lookup failed: {safe_err(e)}; service works skipped this run")
            return []
        jobs = []
        for gid, job in sorted(by_job.items()):
            pc = postcodes[gid]
            g = geo.get(pc)
            if not g or g.get("latitude") is None:
                self.exceptions.append(f"Job {gid}: postcode not recognised; not mapped")
                continue
            if (g.get("region") or "").lower() not in self.regions:
                self.stats["jobs_outside_region"] += 1
                continue
            job["_postcode"], job["_coords"] = pc, (g["latitude"], g["longitude"])
            jobs.append(job)
        self.stats["jobs_in_region"] = len(jobs)
        self.stats["visits_in_region"] = sum(len(j["_visits"]) for j in jobs)
        self.jobs = jobs
        # The index uses the deterministic ids, so mapping does not depend on Jibble answering this run.
        for job in jobs:
            lid = location_id(job["_postcode"])
            self.sites.setdefault(lid, {"coords": job["_coords"], "postcode": job["_postcode"]})
            self.by_location[lid].append(job)
            self.by_project[job_project_id(job["gid"])] = job
        return jobs

    # -- Jibble --------------------------------------------------------------
    @staticmethod
    def _create(fn, *args):
        try:
            return fn(*args)
        except HttpError as e:
            if e.status == 409:
                return None
            raise

    def ensure_jibble(self, jibble) -> None:
        """Create any missing Location (per site) and Project (per job), with deterministic ids.

        Existing projects are never adopted by name: a name clash is reported instead,
        so one job can never take over another's project.
        """
        if not self.jobs:
            return
        projects = jibble.projects()
        self.generic_ids = {p["id"] for p in projects if (p.get("name") or "").strip().lower() in self.generic_names}
        have_loc = {l["id"] for l in jibble.locations()}
        have_proj = {p["id"] for p in projects}
        cid = None
        created = False
        for lid, site in self.sites.items():
            if lid in have_loc:
                continue
            job = self.by_location[lid][0]
            try:
                name = f"{site['postcode']} {job['name'].strip()}"[:100]
                self._create(jibble.create_location, lid, name, site["postcode"], site["coords"][0], site["coords"][1], self.radius)
                self.stats["jibble_locations_created"] += 1
                created = True
            except Exception as e:
                self.exceptions.append(f"Jibble location for job {job['gid']}: {safe_err(e)}")
        for pid, job in self.by_project.items():
            if pid in have_proj:
                continue
            try:
                if cid is None:
                    clients = {(c.get("name") or "").strip().lower(): c["id"] for c in jibble.clients()}
                    cid = clients.get(self.client_name.lower())
                    if not cid:
                        cid = str(uuid.uuid5(ID_NAMESPACE, f"client:{self.client_name.strip().lower()}"))
                        self._create(jibble.create_client, cid, self.client_name)
                        self.stats["jibble_clients_created"] += 1
                self._create(jibble.create_project_at, pid, job["name"].strip()[:100], cid, location_id(job["_postcode"]))
                self.stats["jibble_projects_created"] += 1
                created = True
            except Exception as e:
                self.exceptions.append(f"Jibble project for job {job['gid']}: {safe_err(e)}")
        if created:  # a create refused as a name clash also answers 409: check what exists now
            have_loc = {l["id"] for l in jibble.locations()}
            have_proj = {p["id"] for p in jibble.projects()}
            for lid in self.sites:
                if lid not in have_loc:
                    self.exceptions.append(f"Jibble location for job {self.by_location[lid][0]['gid']} missing (name already used?)")
            for pid, job in self.by_project.items():
                if pid not in have_proj:
                    self.exceptions.append(f"Jibble project for job {job['gid']} missing (name already used?)")

    # -- mapping -------------------------------------------------------------
    def site_allowed(self, project_id: str | None) -> bool:
        """Location/GPS matching applies only with no project, a job's project, or a generic one
        (works.generic_projects). Time clocked to any other project is left to that project."""
        return not project_id or project_id in self.by_project or project_id in self.generic_ids

    def jobs_for(self, project_id: str | None, location: str | None, coords: tuple | None):
        """(jobs at the matched site, jobs at every site whose geofence covers it, how).

        Central London geofences overlap (300 m), so a visit is looked for at every
        site near the matched one; the job fallback only uses the matched site.
        """
        if project_id and project_id in self.by_project:
            job = self.by_project[project_id]
            return [job], [job], "project"
        if not self.site_allowed(project_id):
            return [], [], ""
        if location and location in self.by_location:
            here = self.sites[location]["coords"]
            return self.by_location[location], self._near(here), "location"
        if coords:
            near = sorted((metres(coords, s["coords"]), lid) for lid, s in self.sites.items())
            near = [x for x in near if x[0] <= self.radius]
            if near:
                return self.by_location[near[0][1]], self._near(coords), "gps"
        return [], [], ""

    def _near(self, here: tuple) -> list[dict]:
        return [j for lid, s in self.sites.items() if metres(here, s["coords"]) <= self.radius for j in self.by_location[lid]]

    @staticmethod
    def choose_visit(jobs: list[dict], day: str, worker: str | None, prefer: set[str] | None = None) -> dict | None:
        """The visit on that day: one already holding this time first, then the worker's own
        task, then the lowest ID; a day either side only for a held or the worker's own task."""
        prefer = prefer or set()

        def pick(slack: int):
            d = date.fromisoformat(day)
            latest, earliest = (d + timedelta(days=slack)).isoformat(), (d - timedelta(days=slack)).isoformat()
            hits = []
            for job in jobs:
                for v in job["_visits"]:
                    start, due = visit_window(v)
                    if start <= latest and due >= earliest:  # the visit overlaps day +/- slack
                        mine = worker is not None and (v.get("assignee") or {}).get("gid") == worker
                        hits.append((v["gid"] not in prefer, not mine, v["gid"], v))
            hits.sort(key=lambda h: h[:3])
            if not hits:
                return None
            if slack and hits[0][0] and hits[0][1]:
                return None
            return hits[0][3]
        return pick(0) or pick(1)

    def resolve(self, project_id, location, coords, day: str, worker: str | None, prefer: set[str] | None = None):
        """(task dict, how) for a clocked block, or (None, reason)."""
        here, nearby, how = self.jobs_for(project_id, location, coords)
        if not here:
            return None, ""
        visit = self.choose_visit(nearby, day, worker, prefer)
        if visit:
            return visit, how
        held_job = next((j for j in nearby if j["gid"] in (prefer or ())), None)
        if held_job:
            return held_job, how + "-job"
        if len(here) == 1:
            return here[0], how + "-job"
        return None, "ambiguous"
