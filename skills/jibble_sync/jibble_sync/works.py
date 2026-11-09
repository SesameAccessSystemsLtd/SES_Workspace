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
        rows = self.asana.search_tasks(
            self.workspace,
            {"projects.any": self.teamup, "due_on.after": self.d0, "due_on.before": (date.fromisoformat(self.d1) + timedelta(days=30)).isoformat()},
            TASK_FIELDS,
        )
        out = []
        for t in rows:
            start, due = visit_window(t)
            if due and start <= self.d1 and due >= self.d0 and self.teamup in self._projects(t):
                self._cache[t["gid"]] = t
                out.append(t)
        return out

    def load(self) -> list[dict]:
        """Jobs with their visits and site, London only. Each job: task dict + '_visits', '_postcode', '_coords'."""
        try:
            visits = self.candidate_visits()
        except Exception as e:
            self.exceptions.append(f"Service visits could not be read: {safe_err(e)}")
            return []
        self.stats["visits_in_window"] = len(visits)
        by_job: dict[str, dict] = {}
        postcodes: dict[str, str] = {}
        for v in visits:
            try:
                anc = self._ancestors(v)
            except Exception as e:
                self.exceptions.append(f"Visit {v['gid']}: parent unreadable ({safe_err(e)})")
                continue
            job = next((a for a in anc if self.schedule in self._projects(a)), None) or (anc[-1] if anc else None)
            if job is None:
                self.stats["visits_without_job"] += 1
                continue
            pc = postcodes.get(job["gid"]) or self._address([job, *anc, v])
            if not pc:
                self.stats["visits_without_postcode"] += 1
                if self.schedule in self._projects(job):
                    self.exceptions.append(f"Job {job['gid']}: no postcode in its address fields; visit {v['gid']} not mapped")
                continue
            postcodes[job["gid"]] = pc
            entry = by_job.setdefault(job["gid"], {**job, "_visits": []})
            if v["gid"] not in {x["gid"] for x in entry["_visits"]}:
                entry["_visits"].append(v)

        try:
            geo = self.geocode(sorted(set(postcodes.values()))) if postcodes else {}
        except Exception as e:
            self.exceptions.append(f"Postcode lookup failed: {safe_err(e)}; service works skipped this run")
            return []
        jobs = []
        for gid, job in by_job.items():
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
        """A Location per site and a Project per job, with deterministic ids."""
        if not self.jobs:
            return
        locations = jibble.locations()
        loc_ids = {l["id"] for l in locations}
        loc_by_name = {(l.get("name") or "").strip().lower(): l["id"] for l in locations}
        projects = jibble.projects()
        proj_ids = {p["id"] for p in projects}
        proj_by_name = {(p.get("name") or "").strip().lower(): p["id"] for p in projects}
        clients = {(c.get("name") or "").strip().lower(): c["id"] for c in jibble.clients()}
        cid = clients.get(self.client_name.lower())

        for job in self.jobs:
            pc = job["_postcode"]
            lid = location_id(pc)
            lname = f"{pc} {job['name'].strip()}"[:100]
            try:
                if lid not in loc_ids:
                    existing = loc_by_name.get(lname.lower())
                    if existing:
                        lid = existing
                    else:
                        self._create(jibble.create_location, lid, lname, pc, job["_coords"][0], job["_coords"][1], self.radius)
                        self.stats["jibble_locations_created"] += 1
                    loc_ids.add(lid)
            except Exception as e:
                self.exceptions.append(f"Jibble location for job {job['gid']}: {safe_err(e)}")
            self.sites.setdefault(lid, {"coords": job["_coords"], "postcode": pc})
            self.by_location[lid].append(job)

            pid = job_project_id(job["gid"])
            pname = job["name"].strip()[:100]
            try:
                if pid not in proj_ids:
                    existing = proj_by_name.get(pname.lower())
                    if existing:
                        pid = existing
                    else:
                        if not cid:
                            cid = str(uuid.uuid5(ID_NAMESPACE, f"client:{self.client_name.strip().lower()}"))
                            self._create(jibble.create_client, cid, self.client_name)
                            self.stats["jibble_clients_created"] += 1
                        self._create(jibble.create_project_at, pid, pname, cid, lid)
                        self.stats["jibble_projects_created"] += 1
                    proj_ids.add(pid)
            except Exception as e:
                self.exceptions.append(f"Jibble project for job {job['gid']}: {safe_err(e)}")
            self.by_project[pid] = job

    # -- mapping -------------------------------------------------------------
    def jobs_for(self, project_id: str | None, location: str | None, coords: tuple | None):
        """(jobs at the matched site, jobs at every site whose geofence covers it, how).

        Central London geofences overlap (300 m), so a visit is looked for at every
        site near the matched one; the job fallback only uses the matched site.
        """
        if project_id and project_id in self.by_project:
            job = self.by_project[project_id]
            return [job], [job], "project"
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
    def choose_visit(jobs: list[dict], day: str, worker: str | None) -> dict | None:
        """The visit on that day, preferring the worker's own task; then a day either side."""
        def pick(slack: int):
            d = date.fromisoformat(day)
            latest, earliest = (d + timedelta(days=slack)).isoformat(), (d - timedelta(days=slack)).isoformat()
            hits = []
            for job in jobs:
                for v in job["_visits"]:
                    start, due = visit_window(v)
                    if start <= latest and due >= earliest:  # the visit overlaps day +/- slack
                        mine = worker is not None and (v.get("assignee") or {}).get("gid") == worker
                        hits.append((not mine, v["gid"], v))
            hits.sort(key=lambda h: (h[0], h[1]))
            if not hits:
                return None
            if slack and hits[0][0]:  # a day either side only counts for the worker's own visit
                return None
            return hits[0][2]
        return pick(0) or pick(1)

    def resolve(self, project_id, location, coords, day: str, worker: str | None):
        """(task dict, how) for a clocked block, or (None, reason)."""
        here, nearby, how = self.jobs_for(project_id, location, coords)
        if not here:
            return None, ""
        visit = self.choose_visit(nearby, day, worker)
        if visit:
            return visit, how
        if len(here) == 1:
            return here[0], how + "-job"
        return None, "ambiguous"
