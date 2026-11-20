"""End-to-end flows against in-memory fakes of the Asana and Jibble clients."""
import csv
import json
from datetime import date, timedelta
from pathlib import Path

from jibble_sync.holiday import HolidaySync
from jibble_sync.reports import Reports
from jibble_sync.timesync import TimeSync, jibble_project_id

CFG = json.loads((Path(__file__).resolve().parent.parent / "config.json").read_text())
F = CFG["asana"]["fields"]
OPT = CFG["asana"]["holiday_status_options"]
TODAY = date.today()
D = lambda n: (TODAY - timedelta(days=n)).isoformat()  # noqa: E731


class FakeJibble:
    def __init__(self):
        self.calls = []
        self.people_rows = [
            {"id": "p-adam", "fullName": "Adam Oryl", "email": "adam@x", "groupId": "g-site"},
            {"id": "p-giles", "fullName": "Giles Hugo", "email": "giles@x", "groupId": "g-ws"},
        ]
        self.time_off_rows = []
        self.entries = []
        self.project_rows = []
        self.client_rows = []
        self.activity_rows = [{"id": "a-weld", "name": "Welding / Fabrication"}, {"id": "a-qc", "name": "QC"},
                              {"id": "a-callout", "name": "CALLOUT"}]

    def people(self): return self.people_rows
    def groups(self): return [{"id": "g-site", "name": "Site Team"}, {"id": "g-ws", "name": "Office"}]
    def time_off_policies(self): return [{"id": "pol-hol", "name": "Paid Holiday Request"}]
    def time_offs(self, since): return self.time_off_rows
    def leave_balances(self, person, on): return [{"policyId": "pol-hol", "balance": 12}]
    def set_time_off_status(self, iid, status): self.calls.append(("status", iid, status))
    def clients(self): return self.client_rows
    def create_client(self, cid, name): self.calls.append(("client", name)); self.client_rows.append({"id": cid, "name": name})
    def projects(self): return self.project_rows
    def create_project(self, pid, name, cid): self.calls.append(("project", name)); self.project_rows.append({"id": pid, "name": name})
    def activities(self): return self.activity_rows
    def create_activity(self, name): self.calls.append(("activity", name)); self.activity_rows.append({"id": "a-" + name, "name": name})
    def time_entries(self, d0, d1): return self.entries
    def locations(self): return getattr(self, "location_rows", [])

    def create_location(self, lid, name, address, lat, lng, radius):
        self.calls.append(("location", lid, radius)); self.location_rows = [*self.locations(), {"id": lid, "name": name}]

    def create_project_at(self, pid, name, cid, lid):
        self.calls.append(("project_at", pid, lid)); self.project_rows.append({"id": pid, "name": name})


class FakeAsana:
    def __init__(self):
        self.calls = []
        self.tasks = {}
        self.entries = {}
        self.story_rows = {}

    def me(self): return {"gid": "me"}
    def users_by_email(self, ws): return {"adam@x": "u-adam", "giles@x": "u-giles"}

    def search_tasks(self, ws, params, fields):
        return [t for t in self.tasks.values() if any(c.get("text_value", "") and c["text_value"].startswith("JIBBLE:") for c in t.get("custom_fields", []))]

    def project_tasks_modified_since(self, project, since, fields): return []

    def create_task(self, data, what):
        gid = f"t{len(self.tasks) + 1}"
        cfs = [{"gid": k, "text_value": v if k == F["uid"] else None, "enum_value": {"gid": v} if k == F["holiday_status"] else None}
               for k, v in data["custom_fields"].items()]
        self.tasks[gid] = {"gid": gid, "name": data["name"], "custom_fields": cfs}
        self.calls.append(("create", data))
        return {"gid": gid}

    def update_task(self, gid, data, what): self.calls.append(("update", gid, data))
    def comment(self, gid, text): self.calls.append(("comment", gid, text))
    def add_to_section(self, gid, section): self.calls.append(("section", gid, section))
    def add_to_project(self, gid, project): self.calls.append(("addProject", gid, project))
    def stories(self, gid): return self.story_rows.get(gid, [])

    def task(self, gid, fields):
        return {"gid": gid, "name": "1555.1009.P1.", "parent": {"gid": "d1", "name": "1555.1009.P1.DRAWING"},
                "memberships": [{"project": {"gid": "m1", "name": "1555 - Fifth Ave 854 - Westminster - External Manufacturing"}}],
                "custom_fields": [{"gid": F["project_name"], "display_value": "1555 - Fifth Ave 854"},
                                  {"gid": F["estimated_hours"], "number_value": 2}]}

    def subtasks(self, gid, fields):
        return [{"gid": "s-weld", "name": "Weld  - 1555.1009.P1.", "completed": False, "memberships": [],
                 "custom_fields": [{"gid": F["estimated_hours"], "number_value": 1.5}]},
                {"gid": "s-qc", "name": "QC  - 1555.1009.P1.", "completed": False, "memberships": [], "custom_fields": []}]

    def time_entries(self, gid): return self.entries.get(gid, [])

    def time_entries_attributed(self, project, start):
        return [{**e, "task": {"gid": gid}} for gid, rows in self.entries.items() for e in rows]
    def create_time_entry(self, gid, body): self.calls.append(("entry+", gid, body))
    def update_time_entry(self, egid, minutes): self.calls.append(("entry~", egid, minutes))
    def delete_time_entry(self, egid): self.calls.append(("entry-", egid))


def _iv(**kw):
    base = {"id": "iv1", "personId": "p-adam", "policyId": "pol-hol", "startDate": D(-5), "endDate": D(-6),
            "status": "Pending", "note": None}
    base["endDate"] = base["startDate"]
    base.update(kw)
    return base


def test_holiday_create_then_approve_then_modify():
    cfg = {**CFG, "asana": {**CFG["asana"], "monitored_parts": []}}
    j, a = FakeJibble(), FakeAsana()
    j.time_off_rows = [_iv()]
    hs = HolidaySync(cfg, a, j, print)
    stats = hs.run(a.users_by_email(""))
    assert stats["created"] == 1
    data = a.calls[0][1]
    assert data["assignee"] == "u-adam" and data["custom_fields"][F["holiday_status"]] == OPT["REVIEW"]
    assert data["custom_fields"][F["days_remaining"]] == 12.0
    assert data["name"].startswith("Adam Oryl - ")

    # Steve accepts in Asana
    t = a.tasks["t1"]
    for c in t["custom_fields"]:
        if c["gid"] == F["holiday_status"]:
            c["enum_value"] = {"gid": OPT["ACC_ENG"]}
    t["custom_fields"].append({"gid": F["approved_by"], "people_value": []})
    a.story_rows["t1"] = [{"resource_subtype": "enum_custom_field_changed", "custom_field": {"gid": F["holiday_status"]},
                           "new_enum_value": {"gid": OPT["ACC_ENG"]}, "created_by": {"gid": "u-steve"}}]
    a.calls.clear()
    HolidaySync(cfg, a, j, print).run(a.users_by_email(""))
    assert ("status", "iv1", "Approved") in j.calls
    upd = [c for c in a.calls if c[0] == "update"][0]
    assert upd[2]["custom_fields"][F["approved_by"]] == ["u-steve"]
    uid = upd[2]["custom_fields"][F["uid"]]
    assert "|j=Approved|a=ACC_ENG" in uid

    # Employee changes the dates in Jibble
    for c in t["custom_fields"]:
        if c["gid"] == F["uid"]:
            c["text_value"] = uid
    t["custom_fields"][-1]["people_value"] = [{"gid": "u-steve"}]
    j.time_off_rows = [_iv(status="Approved", endDate=D(-7), startDate=D(-5))]
    j.calls.clear(); a.calls.clear()
    HolidaySync(cfg, a, j, print).run(a.users_by_email(""))
    upd = [c for c in a.calls if c[0] == "update"][0]
    assert upd[2]["custom_fields"][F["holiday_status"]] == OPT["MODIFIED"]
    assert ("status", "iv1", "Pending") in j.calls
    assert any(c[0] == "comment" for c in a.calls)


def test_time_sync_maps_and_reconciles():
    j, a = FakeJibble(), FakeAsana()
    pid = jibble_project_id("p1")
    cfg = {**CFG, "asana": {**CFG["asana"], "monitored_parts": ["p1"]}}
    day = D(1)
    j.entries = [
        {"id": "1", "personId": "p-adam", "type": "In", "time": f"{day}T08:00:00Z", "belongsToDate": day, "projectId": pid, "activityId": "a-weld"},
        {"id": "2", "personId": "p-adam", "type": "In", "time": f"{day}T10:00:00Z", "belongsToDate": day, "projectId": pid, "activityId": "a-callout"},
        {"id": "3", "personId": "p-adam", "type": "Out", "time": f"{day}T11:00:00Z", "belongsToDate": day},
    ]
    a.entries["s-qc"] = [{"gid": "old", "created_by": {"gid": "me"}, "assignee": {"gid": "u-adam"}, "entered_on": day, "duration_minutes": 30}]
    ts = TimeSync(cfg, a, j, print)
    stats = ts.run(a.users_by_email(""))
    names = [c[1] for c in j.calls if c[0] in ("client", "project")]
    assert "1555 - Fifth Ave 854" in names and "1555.1009.P1." in names
    assert ("activity", "Rework") in j.calls
    plus = {c[1]: c[2] for c in a.calls if c[0] == "entry+"}
    assert plus["s-weld"]["duration_minutes"] == 120 and plus["s-weld"]["assignee"] == "u-adam"
    assert plus["s-weld"]["attributable_to"] == CFG["asana"]["beta_project"]  # subtask is not in the job project
    assert plus["p1"]["attributable_to"] == "m1"
    assert plus["p1"]["duration_minutes"] == 60  # CALLOUT has no subtask -> part task
    assert ("entry-", "old") in a.calls
    assert ("addProject", "p1", CFG["asana"]["beta_project"]) in a.calls
    seeded = [c for c in a.calls if c[0] == "update" and F["estimated_time"] in c[2]["custom_fields"]]
    assert {c[1]: c[2]["custom_fields"][F["estimated_time"]] for c in seeded} == {"p1": 120, "s-weld": 90}
    assert stats["unmatched_segments"] == 1 and ts.exceptions


def test_time_sync_safety_rules():
    j, a = FakeJibble(), FakeAsana()
    pid = jibble_project_id("p1")
    cfg = {**CFG, "asana": {**CFG["asana"], "monitored_parts": ["p1"]}}
    day = D(1)
    # A Jibble person with no Asana user, and a read that lost every segment.
    j.people_rows.append({"id": "p-x", "fullName": "X", "email": "nobody@x"})
    j.entries = [
        {"id": "1", "personId": "p-x", "type": "In", "time": f"{day}T08:00:00Z", "belongsToDate": day, "projectId": pid, "activityId": "a-weld"},
        {"id": "2", "personId": "p-x", "type": "Out", "time": f"{day}T09:00:00Z", "belongsToDate": day},
    ]
    a.entries["s-weld"] = [{"gid": f"e{i}", "created_by": {"gid": "me"}, "assignee": {"gid": "u-adam"},
                            "entered_on": D(i + 1), "duration_minutes": 60} for i in range(12)]
    ts = TimeSync(cfg, a, j, print)
    stats = ts.run(a.users_by_email(""))
    assert not [c for c in a.calls if c[0] in ("entry+", "entry-")]  # nobody credited, nothing mass-deleted
    assert stats["asana_people_not_written"] == 1 and stats["asana_writes_held_back"] == 12


def test_holiday_missing_request_flagged():
    cfg = {**CFG, "asana": {**CFG["asana"], "monitored_parts": []}}
    j, a = FakeJibble(), FakeAsana()
    j.time_off_rows = [_iv(), _iv(id="iv2")]
    HolidaySync(cfg, a, j, print).run(a.users_by_email(""))
    for t in a.tasks.values():
        t["due_on"] = D(-5)
    j.time_off_rows = [_iv()]  # iv2 deleted in Jibble
    a.calls.clear()
    HolidaySync(cfg, a, j, print).run(a.users_by_email(""))
    assert [c[2] for c in a.calls if c[0] == "comment"] and any(
        c[0] == "update" and c[2]["custom_fields"].get(F["holiday_status"]) == OPT["MODIFIED"] for c in a.calls)


def test_reports_write_everhour_layout(tmp_path):
    j, a = FakeJibble(), FakeAsana()
    pid = jibble_project_id("p1")
    cfg = {**CFG, "asana": {**CFG["asana"], "monitored_parts": ["p1"]}}
    day = D(1)
    j.entries = [
        {"id": "1", "personId": "p-adam", "type": "In", "time": f"{day}T08:00:00Z", "belongsToDate": day, "projectId": pid, "activityId": "a-weld"},
        {"id": "2", "personId": "p-adam", "type": "Out", "time": f"{day}T09:30:00Z", "belongsToDate": day},
    ]
    j.time_off_rows = [_iv(status="Approved", startDate=D(3), endDate=D(3))]
    rep = Reports(cfg, a, j, print)
    out = tmp_path / "payroll.csv"
    rep.payroll(D(10), D(0), str(out), a.users_by_email(""))
    rows = list(csv.reader(out.open()))
    assert rows[2] == ["Member", "Time", "Day", "Parent Task", "Task"]
    body = rows[3:-1]
    assert ["Adam Oryl", "1.5"] == body[[r[3] for r in body].index("1555.1009.P1.DRAWING")][:2]
    if date.fromisoformat(D(3)).weekday() < 5:
        assert any(r[3] == "0010 Annual Leave" and r[1] == "9" for r in body)
    out2 = tmp_path / "mfg.csv"
    rep.manufacturing(D(10), D(0), str(out2), "1555 - Fifth Ave 854")
    rows2 = list(csv.reader(out2.open()))
    assert rows2[3][0] == "1555.1009.P1.DRAWING" and rows2[3][1] == "1.5"
    assert rows2[-1][0] == "Total"
