from jibble_sync.holiday import (ACC_ENG, ACC_OFFICE, CANCELLED, MODIFIED, REJECTED, REVIEW, decide,
                                 long_date, make_uid, parse_uid, task_name)
from jibble_sync.mapping import activities_for_subtask, canonical_activity, choose_subtask
from jibble_sync.segments import build_segments
from jibble_sync.timesync import diff_entries


# -- mapping -----------------------------------------------------------------
def test_activity_names_from_real_subtasks():
    assert activities_for_subtask("Weld  - 1555.1009.P1.") == {"Welding / Fabrication"}
    assert activities_for_subtask("Assemble  - 1555.1009.P1.") == {"Assembly"}
    assert activities_for_subtask("QC  - 1555.1009.P1.") == {"QC"}
    assert activities_for_subtask("Weld & Assemble  - 1555.1092.C.PlatformLift.") == {"Welding / Fabrication", "Assembly"}
    assert activities_for_subtask("Electrics - 1555.1092.C.PlatformLift.") == {"Wiring"}
    assert activities_for_subtask("Re-Wire slave panel") == {"Wiring"}
    assert activities_for_subtask("P 1-3 & Assemble- 1555.1092.K.RP_MT_ButtonStation. Rev 1") == {"Assembly"}
    assert activities_for_subtask("Modify-weld additional extension to lower post 99mm") == {"Rework"}
    assert activities_for_subtask("QC  - 1555.1092.C.PlatformLift.  re-work locks for barriers") == {"Rework"}
    assert activities_for_subtask("WELDING JIBBLE TEST") == {"Welding / Fabrication"}
    assert activities_for_subtask("ASSEMBLY JIBBLE TEST") == {"Assembly"}
    assert activities_for_subtask("Drill/Do not countersink  - 1037.9991.A.TapeSwitch.") == {"Drill"}
    assert activities_for_subtask("Wire and test - X") == {"Wiring"}
    assert activities_for_subtask("Brass aged plating to be applied by Dan") == set()


def test_canonical_activity():
    assert canonical_activity("Welding / Fabrication") == "Welding / Fabrication"
    assert canonical_activity("Assembly") == "Assembly"
    assert canonical_activity("Wiring") == "Wiring"
    assert canonical_activity("Rework") == "Rework"
    assert canonical_activity("CALLOUT") is None


def _sub(gid, name, completed=False, assignee=None):
    return {"gid": gid, "name": name, "completed": completed, "assignee": {"gid": assignee} if assignee else None}


def test_choose_prefers_open_then_mine_then_standard():
    part = "1555.1014.B.UpperGateFrame."
    subs = [
        _sub("100", "Assemble  - 1555.1014.B.UpperGateFrame.", completed=False, assignee="paul"),
        _sub("200", "Assemble  - 1555.1014.B.UpperGateFrame.", completed=True),
        _sub("300", "Modify-weld additional extension to lower post 99mm", completed=True),
    ]
    assert choose_subtask("Assembly", subs, part, None)["gid"] == "100"
    assert choose_subtask("Rework", subs, part, None)["gid"] == "300"
    assert choose_subtask("QC", subs, part, None) is None


def test_choose_standard_name_beats_one_off():
    part = "1555.1092.C.PlatformLift."
    subs = [
        _sub("1209854002969067", "Weld & Assemble  - 1555.1092.C.PlatformLift.", completed=True),
        _sub("1211099221100567", "Fabricate transport frame for shipping", completed=True),
    ]
    assert choose_subtask("Welding / Fabrication", subs, part, None)["gid"] == "1209854002969067"


def test_choose_open_assigned_to_worker_wins():
    part = "P."
    subs = [_sub("1", "Weld - P.", False, "a"), _sub("2", "Weld - P.", False, "b")]
    assert choose_subtask("Welding / Fabrication", subs, part, "b")["gid"] == "2"


# -- segments ------------------------------------------------------------------
def test_segments_pair_switches_and_skip_open():
    entries = [
        {"id": "1", "personId": "p", "type": "In", "time": "2026-10-30T08:00:00Z", "belongsToDate": "2026-10-30", "projectId": "A", "activityId": "w"},
        {"id": "2", "personId": "p", "type": "In", "time": "2026-10-30T10:30:00Z", "belongsToDate": "2026-10-30", "projectId": "A", "activityId": "q"},
        {"id": "3", "personId": "p", "type": "Out", "time": "2026-10-30T12:00:00Z", "belongsToDate": "2026-10-30"},
        {"id": "4", "personId": "p", "type": "In", "time": "2026-10-30T13:00:00Z", "belongsToDate": "2026-10-30", "projectId": "B"},
        {"id": "5", "personId": "x", "type": "Out", "time": "2026-10-30T12:00:00Z", "status": "Archived"},
    ]
    segs, open_count = build_segments(entries)
    assert [(s.activity_id, s.minutes) for s in segs] == [("w", 150.0), ("q", 90.0)]
    assert open_count == 1


# -- holiday decisions ----------------------------------------------------------
def _task(status, h="h1", j="Pending", a=REVIEW, approved=False):
    return {"status": status, "uid": {"id": "i", "h": h, "j": j, "a": a}, "approved_by_set": approved}


def test_new_request_creates_task():
    d = decide("Pending", "h1", None, engineer=False)
    assert d.create and d.asana_status == REVIEW
    assert decide("Cancelled", "h1", None, False) is None


def test_asana_approval_goes_to_jibble_and_fills_approver():
    d = decide("Pending", "h1", _task(ACC_OFFICE), engineer=False)
    assert d.jibble_status == "Approved" and d.fill_approved_by and d.final_a == ACC_OFFICE


def test_asana_rejection_goes_to_jibble():
    d = decide("Pending", "h1", _task(REJECTED), False)
    assert d.jibble_status == "Rejected"


def test_jibble_change_marks_modified_and_reopens():
    d = decide("Approved", "h2", _task(ACC_ENG, h="h1", j="Approved", a=ACC_ENG, approved=True), True)
    assert d.update_content and d.asana_status == MODIFIED and d.jibble_status == "Pending"


def test_employee_cancel_in_jibble_moves_task():
    d = decide("Cancelled", "h1", _task(ACC_OFFICE, j="Approved", a=ACC_OFFICE, approved=True), False)
    assert d.asana_status == CANCELLED and d.move_to_cancelled and d.jibble_status is None


def test_asana_cancel_pushes_to_jibble():
    d = decide("Approved", "h1", _task(CANCELLED, j="Approved", a=ACC_OFFICE, approved=True), False)
    assert d.jibble_status == "Cancelled" and d.move_to_cancelled


def test_approved_in_jibble_app_mirrors_to_asana():
    d = decide("Approved", "h1", _task(REVIEW, j="Pending", a=REVIEW), engineer=True)
    assert d.asana_status == ACC_ENG and d.jibble_status is None


def test_nothing_changed_is_noop():
    assert decide("Approved", "h1", _task(ACC_OFFICE, j="Approved", a=ACC_OFFICE, approved=True), False) is None


def test_uid_round_trip_and_names():
    uid = make_uid("abc", "h1", "Pending", REVIEW)
    assert parse_uid(uid) == {"id": "abc", "h": "h1", "j": "Pending", "a": REVIEW}
    assert long_date("2026-11-20") == "Fri Nov 20th 2026"
    assert long_date("2026-11-01") == "Sun Nov 1st 2026"
    assert long_date("2026-11-12") == "Thu Nov 12th 2026"
    assert task_name("Richard Smalldridge", {"startDate": "2026-11-20", "endDate": "2026-11-20"}) == \
        "Richard Smalldridge - Fri Nov 20th 2026 to Fri Nov 20th 2026"


# -- time entry reconcile ---------------------------------------------------------
def test_diff_entries_create_update_delete_and_ignore_others():
    existing = [
        {"gid": "e1", "created_by": {"gid": "me"}, "assignee": {"gid": "w"}, "entered_on": "2026-10-29", "duration_minutes": 60},
        {"gid": "e2", "created_by": {"gid": "me"}, "assignee": {"gid": "w"}, "entered_on": "2026-10-28", "duration_minutes": 30},
        {"gid": "e3", "created_by": {"gid": "someone"}, "assignee": {"gid": "w"}, "entered_on": "2026-10-29", "duration_minutes": 99},
        {"gid": "e4", "created_by": {"gid": "me"}, "assignee": {"gid": "w"}, "entered_on": "2026-09-01", "duration_minutes": 10},
        {"gid": "e5", "created_by": {"gid": "me"}, "assignee": {"gid": "w"}, "entered_on": "2026-10-29", "duration_minutes": 5},
    ]
    desired = {("w", "2026-10-29"): 90, ("w", "2026-10-30"): 45}
    creates, updates, deletes = diff_entries(existing, desired, "me", "2026-10-09")
    assert creates == [("w", "2026-10-30", 45)]
    assert updates == [("e1", 90)]
    assert sorted(deletes) == ["e2", "e5"]  # e3 is a person's own entry, e4 is outside the window
