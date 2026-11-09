"""Service visits (TeamUp) -> Jibble locations/projects -> Asana time; and the Everhour target."""
from datetime import date, timedelta

from jibble_sync.targets import EverhourTarget, plan
from jibble_sync.timesync import TimeSync
from jibble_sync.works import ServiceWorks, job_project_id, location_id, metres

from test_flows import CFG, FakeAsana, FakeJibble

TODAY = date.today()
D = lambda n: (TODAY - timedelta(days=n)).isoformat()  # noqa: E731
W = CFG["works"]
ADDR = W["address_fields"][0]


def _task(gid, name, projects, parent=None, assignee=None, due=None, address=None, notes=""):
    return {"gid": gid, "name": name, "due_on": due, "start_on": None, "completed": False, "notes": notes,
            "assignee": {"gid": assignee} if assignee else None, "parent": {"gid": parent} if parent else None,
            "memberships": [{"project": {"gid": p}} for p in projects],
            "custom_fields": [{"gid": ADDR, "text_value": address}] if address else []}


class WorksAsana(FakeAsana):
    def __init__(self, rows):
        super().__init__()
        self.rows = {t["gid"]: t for t in rows}

    def users_by_email(self, ws): return {"adam@x": "u-adam", "josh@x": "u-josh"}
    def task(self, gid, fields): return dict(self.rows[gid])

    def search_tasks(self, ws, params, fields):
        assert params["projects.any"] == W["teamup_project"]
        return [dict(t) for t in self.rows.values() if W["teamup_project"] in {m["project"]["gid"] for m in t["memberships"]}
                and t["gid"] not in getattr(self, "hidden", ())]


def geocode(pcs):
    known = {"EC3V 0BG": {"latitude": 51.5117, "longitude": -0.0843, "region": "London"},
             "M4 1AA": {"latitude": 53.48, "longitude": -2.23, "region": "North West"}}
    return {p: known[p] for p in pcs if p in known}


def _setup():
    S, T = W["service_schedule_project"], W["teamup_project"]
    rows = [
        _task("J1", "1093 - Gracechurch St 20", [S], address="20 Gracechurch St, EC3V 0BG"),
        _task("V1", "Service 1of4", [T], parent="J1", assignee="u-adam", due=D(1)),
        _task("V2", "Second engineer", [T], parent="V1", assignee="u-josh", due=D(1)),
        _task("J2", "Express Manchester", [S], address="M4 1AA"),
        _task("V3", "Service", [T], parent="J2", assignee="u-adam", due=D(2)),
        _task("J3", "No address job", [S]),
        _task("V4", "Service", [T], parent="J3", assignee="u-adam", due=D(2)),
        _task("H1", "Holiday", [T], assignee="u-adam", due=D(0)),
    ]
    a, j = WorksAsana(rows), FakeJibble()
    j.people_rows.append({"id": "p-josh", "fullName": "Josh", "email": "josh@x"})
    return a, j


def test_service_visits_map_by_location_gps_and_project():
    a, j = _setup()
    lid, pid = location_id("EC3V 0BG"), job_project_id("J1")
    d1, d3 = D(1), D(3)
    j.entries = [
        # Adam at the geofenced site, no project picked -> his own visit task V1
        {"id": "1", "personId": "p-adam", "type": "In", "time": f"{d1}T08:00:00Z", "belongsToDate": d1, "locationId": lid},
        {"id": "2", "personId": "p-adam", "type": "Out", "time": f"{d1}T10:00:00Z", "belongsToDate": d1},
        # Josh: no location id, but GPS ~50 m from the site -> his own subtask V2
        {"id": "3", "personId": "p-josh", "type": "In", "time": f"{d1}T08:00:00Z", "belongsToDate": d1,
         "coordinates": {"latitude": 51.5121, "longitude": -0.0841}},
        {"id": "4", "personId": "p-josh", "type": "Out", "time": f"{d1}T09:30:00Z", "belongsToDate": d1},
        # Adam picks the job's project on a day with no visit -> the job task, reported
        {"id": "5", "personId": "p-adam", "type": "In", "time": f"{d3}T08:00:00Z", "belongsToDate": d3, "projectId": pid},
        {"id": "6", "personId": "p-adam", "type": "Out", "time": f"{d3}T09:00:00Z", "belongsToDate": d3},
        # Somewhere else entirely -> ignored
        {"id": "7", "personId": "p-adam", "type": "In", "time": f"{d3}T10:00:00Z", "belongsToDate": d3,
         "coordinates": {"latitude": 51.0, "longitude": 0.0}},
        {"id": "8", "personId": "p-adam", "type": "Out", "time": f"{d3}T11:00:00Z", "belongsToDate": d3},
    ]
    cfg = {**CFG, "asana": {**CFG["asana"], "monitored_parts": []}}
    works = ServiceWorks(cfg, a, print, geocode=geocode)
    ts = TimeSync(cfg, a, j, print)
    stats = ts.run(a.users_by_email(""), works=works)

    assert ("location", lid, 300) in j.calls and ("project_at", pid, lid) in j.calls
    assert ("client", "Service & Repairs") in j.calls
    assert len([c for c in j.calls if c[0] == "location"]) == 1  # Manchester is out of region
    plus = {(c[1], c[2]["assignee"]): c[2] for c in a.calls if c[0] == "entry+"}
    assert plus[("V1", "u-adam")]["duration_minutes"] == 120
    assert plus[("V2", "u-josh")]["duration_minutes"] == 90
    assert plus[("J1", "u-adam")]["duration_minutes"] == 60
    assert all(e["attributable_to"] == CFG["asana"]["beta_project"] for e in plus.values())
    assert len(plus) == 3
    beta = {c[1] for c in a.calls if c[0] == "addProject"}
    assert beta == {"J1", "V1", "V2"}
    assert stats["site_segments_by_location"] == 1 and stats["site_segments_by_gps"] == 1
    assert stats["site_segments_by_project-job"] == 1
    assert stats["jobs_in_region"] == 1 and stats["jobs_outside_region"] == 1
    assert any("J3" in x for x in ts.exceptions)  # job without a postcode is reported
    assert not any(D(1) in x or D(3) in x for x in ts.exceptions)  # public logs: no dates


def test_choose_visit_prefers_own_task_and_allows_a_day_either_side_only_for_it():
    v_adam = {"gid": "2", "due_on": "2026-11-10", "assignee": {"gid": "u-adam"}}
    v_josh = {"gid": "1", "due_on": "2026-11-10", "assignee": {"gid": "u-josh"}}
    jobs = [{"_visits": [v_josh, v_adam]}]
    assert ServiceWorks.choose_visit(jobs, "2026-11-10", "u-adam") is v_adam
    assert ServiceWorks.choose_visit(jobs, "2026-11-10", "u-other") is v_josh  # lowest gid
    assert ServiceWorks.choose_visit(jobs, "2026-11-11", "u-adam") is v_adam  # overran into the next day
    assert ServiceWorks.choose_visit(jobs, "2026-11-11", "u-other") is None
    assert ServiceWorks.choose_visit(jobs, "2026-11-13", "u-adam") is None


def test_metres():
    assert 40 < metres((51.5117, -0.0843), (51.5121, -0.0841)) < 60


class FakeEverhour:
    def __init__(self):
        self.calls = []
        self.records = {"as:T1": [
            {"id": 11, "user": 7, "date": D(1), "time": 3600, "comment": "Jibble"},  # synced earlier for Adam
            {"id": 12, "user": {"id": 9}, "date": D(1), "time": 1800},  # someone not moved to Jibble
        ]}

    def users(self): return [{"id": 7, "email": "Adam@x"}, {"id": 9, "email": "other@x"}]
    def task_time(self, task, d0, d1): return self.records.get(task, [])
    def add_time(self, task, user, day, seconds): self.calls.append(("add", task, user, day, seconds))
    def set_time(self, rid, task, user, day, seconds): self.calls.append(("set", rid, seconds))
    def delete_time(self, rid): self.calls.append(("del", rid))


def test_everhour_target_only_touches_listed_people():
    eh = FakeEverhour()
    tgt = EverhourTarget(eh, ["adam@x"], TODAY.isoformat())
    assert tgt.user_for("ADAM@x") == 7 and tgt.user_for("other@x") is None
    owned = tgt.owned("T1", D(21))
    assert list(owned) == [(7, D(1))]  # the other person's record is not ours
    creates, updates, deletes = plan(owned, {(7, D(1)): 90, (7, D(2)): 30})
    for r, u, d, m in updates:
        tgt.update("T1", r, u, d, m)
    for u, d, m in creates:
        tgt.create("T1", u, d, m, None)
    assert ("set", 11, 5400) in eh.calls and ("add", "as:T1", 7, D(2), 1800) in eh.calls and not deletes
    assert plan(owned, {}) == ([], [], [{"id": 11, "minutes": 60}])
    # Adam's own timer entry on another day is not ours, and nothing is added beside it.
    eh.records["as:T1"].append({"id": 13, "user": 7, "date": D(3), "time": 600, "comment": "fixed hinge"})
    assert (7, D(3)) not in tgt.owned("T1", D(21)) and tgt.blocked("T1", 7, D(3))


def test_lookup_falls_back_to_terminated_postcodes():
    from jibble_sync import geo
    from jibble_sync.http import HttpError

    def http(method, url, json_body=None, params=None, **kw):
        if method == "POST":
            return {"result": [{"query": "SW1A 2EU", "result": None}, {"query": "ZZ1 1ZZ", "result": None}]}
        if url.endswith("SW1A%202EU"):
            return {"result": {"latitude": 51.5056, "longitude": -0.1254}}
        if url.startswith(geo.TERMINATED):
            raise HttpError(404, "", "GET", url)
        assert params["lat"] == 51.5056
        return {"result": [{"region": "London", "admin_district": "Westminster"}]}

    out = geo.lookup(["SW1A 2EU", "ZZ1 1ZZ"], http=http)
    assert out == {"SW1A 2EU": {"latitude": 51.5056, "longitude": -0.1254, "region": "London",
                                "admin_district": "Westminster", "terminated": True}}


def test_overlapping_geofences_find_the_workers_visit_next_door():
    w = ServiceWorks.__new__(ServiceWorks)
    w.radius = 300
    a = {"gid": "JA", "_visits": []}
    b = {"gid": "JB", "_visits": [{"gid": "VB", "due_on": "2026-11-10", "assignee": {"gid": "u-adam"}}]}
    w.sites = {"LA": {"coords": (51.510052, -0.13899)}, "LB": {"coords": (51.510106, -0.139016)}}
    w.by_location = {"LA": [a], "LB": [b]}
    w.by_project = {}
    # Jibble tagged the clock-in with the neighbouring site's location
    assert w.resolve(None, "LA", None, "2026-11-10", "u-adam") == (b["_visits"][0], "location")
    # No visit anywhere that day: the matched site's job
    assert w.resolve(None, "LA", None, "2026-11-12", "u-adam") == (a, "location-job")


def _adam_at_site(j, day, minutes=120, **extra):
    j.entries += [
        {"id": f"in{day}{len(j.entries)}", "personId": "p-adam", "type": "In", "time": f"{day}T08:00:00Z", "belongsToDate": day, **extra},
        {"id": f"out{day}{len(j.entries)}", "personId": "p-adam", "type": "Out", "time": f"{day}T{8 + minutes // 60:02d}:00:00Z", "belongsToDate": day},
    ]


def _run(a, j):
    cfg = {**CFG, "asana": {**CFG["asana"], "monitored_parts": []}}
    ts = TimeSync(cfg, a, j, print)
    return ts, ts.run(a.users_by_email(""), works=ServiceWorks(cfg, a, print, geocode=geocode))


def test_visit_missing_from_search_keeps_its_time_and_no_duplicate():
    a, j = _setup()
    _adam_at_site(j, D(1), locationId=location_id("EC3V 0BG"))
    a.entries["V1"] = [{"gid": "e1", "created_by": {"gid": "me"}, "assignee": {"gid": "u-adam"}, "entered_on": D(1), "duration_minutes": 120}]
    a.hidden = {"V1"}  # search lags behind an edit
    ts, stats = _run(a, j)
    assert not [c for c in a.calls if c[0] in ("entry+", "entry-", "entry~")]
    assert stats["held_visits_added_back"] == 1


def test_jibble_setup_failure_does_not_remove_site_time():
    a, j = _setup()
    _adam_at_site(j, D(1), locationId=location_id("EC3V 0BG"))
    a.entries["V1"] = [{"gid": "e1", "created_by": {"gid": "me"}, "assignee": {"gid": "u-adam"}, "entered_on": D(1), "duration_minutes": 120}]

    def down():
        from jibble_sync.http import HttpError
        raise HttpError(503, "", "GET", "https://workspace.prod.jibble.io/v1/Locations")
    j.locations = down
    ts, stats = _run(a, j)
    assert not [c for c in a.calls if c[0] in ("entry+", "entry-", "entry~")]
    assert any("Jibble setup" in x for x in ts.exceptions)


def test_time_on_another_project_is_not_taken_by_location():
    a, j = _setup()
    _adam_at_site(j, D(1), locationId=location_id("EC3V 0BG"), projectId="some-other-project")
    ts, stats = _run(a, j)
    assert not [c for c in a.calls if c[0] == "entry+"]


def test_failed_held_lookup_skips_site_work():
    a, j = _setup()
    _adam_at_site(j, D(1), locationId=location_id("EC3V 0BG"))

    def broken(project, start):
        raise RuntimeError("boom")
    a.time_entries_attributed = broken
    ts, stats = _run(a, j)
    assert not [c for c in a.calls if c[0] in ("entry+", "entry-", "addProject")]
    assert any("Service works skipped" in x for x in ts.exceptions)
