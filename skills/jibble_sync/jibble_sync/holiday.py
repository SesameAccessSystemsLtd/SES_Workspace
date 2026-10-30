"""Two-way sync between Jibble time off and Asana 'Holiday for Employees'.

Asana decides (Holiday STATUS), Jibble holds the request. State that tells the
two sides apart between runs lives in the task's 'UID for automation' field:

    JIBBLE:<interval id>|h=<content hash>|j=<last Jibble status>|a=<last Asana status>
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

# Asana status codes used internally
REVIEW, ACC_OFFICE, ACC_ENG, MODIFIED, CANCELLED, REJECTED, NONE = (
    "REVIEW", "ACC_OFFICE", "ACC_ENG", "MODIFIED", "CANCELLED", "REJECTED", "NONE",
)
ACCEPTED = {ACC_OFFICE, ACC_ENG}
JIBBLE_STATUSES = {"Pending", "Approved", "Rejected", "Cancelled"}


def asana_to_jibble(code: str) -> str | None:
    if code in ACCEPTED:
        return "Approved"
    return {REJECTED: "Rejected", CANCELLED: "Cancelled", REVIEW: "Pending", MODIFIED: "Pending"}.get(code)


def jibble_to_asana(status: str, engineer: bool) -> str:
    if status == "Approved":
        return ACC_ENG if engineer else ACC_OFFICE
    return {"Rejected": REJECTED, "Cancelled": CANCELLED}.get(status, REVIEW)


def content_hash(iv: dict) -> str:
    raw = "|".join(str(iv.get(k) or "") for k in ("personId", "policyId", "startDate", "endDate", "startTime", "endTime"))
    return hashlib.sha1(raw.encode()).hexdigest()[:8]


def make_uid(interval_id: str, h: str, j: str, a: str) -> str:
    return f"JIBBLE:{interval_id}|h={h}|j={j}|a={a}"


def parse_uid(text: str | None) -> dict | None:
    if not text or not text.startswith("JIBBLE:"):
        return None
    parts = text.split("|")
    out = {"id": parts[0][len("JIBBLE:"):], "h": "", "j": "", "a": ""}
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            out[k] = v
    return out


@dataclass
class Decision:
    create: bool = False
    asana_status: str | None = None       # code to write to Holiday STATUS
    jibble_status: str | None = None      # status to PATCH on the Jibble request
    update_content: bool = False          # rewrite name/dates/leave type
    move_to_cancelled: bool = False
    fill_approved_by: bool = False
    comments: list[str] = field(default_factory=list)
    final_j: str = ""
    final_a: str = ""


def decide(j_status: str, h: str, task: dict | None, engineer: bool) -> Decision | None:
    """Work out what to change for one Jibble request.

    task: None when no Asana task exists yet, else
          {"status": code, "uid": parse_uid(...) or None, "approved_by_set": bool}
    Returns None when nothing should happen.
    """
    if task is None:
        if j_status in ("Cancelled", "Rejected"):
            return None  # closed before Asana ever saw it
        code = jibble_to_asana(j_status, engineer)
        return Decision(create=True, asana_status=code, final_j=j_status, final_a=code)

    a_now = task["status"]
    uid = task.get("uid") or {"h": h, "j": j_status, "a": a_now}
    d = Decision(final_j=j_status, final_a=a_now)

    if j_status == "Cancelled" and uid.get("j") != "Cancelled":
        # The employee cancelled in Jibble.
        if a_now != CANCELLED:
            d.asana_status = CANCELLED
            d.final_a = CANCELLED
            d.comments.append("Cancelled in Jibble.")
        d.move_to_cancelled = True
    elif uid.get("h") and h != uid.get("h"):
        # The request was changed in Jibble: Asana must review it again.
        d.update_content = True
        d.asana_status = MODIFIED
        d.final_a = MODIFIED
        d.comments.append("Changed in Jibble. Holiday STATUS set to MODIFIED - REQUIRES REVIEW; approve or reject again.")
        if j_status != "Pending":
            d.jibble_status = "Pending"
            d.final_j = "Pending"
    elif a_now != uid.get("a"):
        # Someone changed Holiday STATUS in Asana: Asana wins.
        want = asana_to_jibble(a_now)
        if want and want != j_status:
            d.jibble_status = want
            d.final_j = want
        if a_now == CANCELLED:
            d.move_to_cancelled = True
        # Record who approved, once, at the moment the approval is seen.
        if a_now in ACCEPTED and not task.get("approved_by_set"):
            d.fill_approved_by = True
    elif j_status != uid.get("j"):
        # Changed in the Jibble app (e.g. approved there): mirror into Asana.
        code = jibble_to_asana(j_status, engineer)
        if not (code == REVIEW and a_now == MODIFIED) and code != a_now:
            d.asana_status = code
            d.final_a = code
            d.comments.append(f"Set to {j_status} in Jibble.")

    changed = (
        d.asana_status or d.jibble_status or d.update_content or d.move_to_cancelled
        or d.fill_approved_by or uid.get("h") != h or uid.get("j") != d.final_j or uid.get("a") != d.final_a
        or task.get("uid") is None
    )
    return d if changed else None


# -- naming ------------------------------------------------------------------
def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def long_date(iso: str) -> str:
    d = date.fromisoformat(iso[:10])
    return f"{d.strftime('%a %b')} {_ordinal(d.day)} {d.year}"


def task_name(full_name: str, iv: dict) -> str:
    return f"{full_name} - {long_date(iv['startDate'])} to {long_date(iv['endDate'])}"


def task_notes(full_name: str, policy_name: str, iv: dict) -> str:
    first = (full_name or "").split(" ")[0] or "Someone"
    start, end = iv["startDate"][:10], iv["endDate"][:10]
    days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
    when = f"on {long_date(start)}" if start == end else f"between {long_date(start)} and {long_date(end)}"
    times = ""
    if iv.get("startTime") and iv.get("endTime"):
        times = f", {str(iv['startTime'])[:5]} to {str(iv['endTime'])[:5]}"
    lines = [
        f"{first} has requested {days} day{'s' if days != 1 else ''} off {when}{times} ({policy_name}).",
        "Created by the Jibble sync. Approve or reject with Holiday STATUS; the decision is sent back to Jibble.",
    ]
    if iv.get("note"):
        lines += ["", f"Note from Jibble: {iv['note']}"]
    return "\n".join(lines)


class HolidaySync:
    def __init__(self, cfg: dict, asana, jibble, log):
        self.cfg, self.asana, self.jibble, self.log = cfg, asana, jibble, log
        a = cfg["asana"]
        f = a["fields"]
        self.project = a["holiday_project"]
        self.workspace = a["workspace"]
        self.f_status, self.f_leave, self.f_uid = f["holiday_status"], f["leave_type"], f["uid"]
        self.f_approved, self.f_days = f["approved_by"], f.get("days_remaining")
        self.opt = a["holiday_status_options"]               # code -> enum option gid
        self.code_of = {v: k for k, v in self.opt.items()}
        self.leave_opts = a["leave_type_options"]            # name -> enum option gid
        self.cancelled_section = a.get("holiday_cancelled_section")
        j = cfg["jibble"]
        self.policy_leave = j["policy_leave_type"]           # Jibble policy name -> Asana Leave Type
        self.engineer_groups = {g.lower() for g in j.get("engineer_groups", [])}
        self.window_days = cfg.get("holiday_window_days", 60)
        self.stats = {"requests": 0, "created": 0, "asana_updates": 0, "jibble_updates": 0, "errors": 0}

    TASK_FIELDS = "name,completed,start_on,due_on,assignee.gid,custom_fields.gid,custom_fields.text_value,custom_fields.enum_value.gid,custom_fields.people_value.gid"

    def _existing_tasks(self) -> dict[str, dict]:
        """Jibble-linked tasks, keyed by Jibble interval id.

        Search finds older tasks; the recently-modified list covers tasks the
        search index has not caught up with yet, so a fresh task is never
        created twice.
        """
        found: dict[str, dict] = {}
        rows = self.asana.search_tasks(
            self.workspace,
            {"projects.any": self.project, f"custom_fields.{self.f_uid}.contains": "JIBBLE:"},
            self.TASK_FIELDS,
        )
        since = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows += self.asana.project_tasks_modified_since(self.project, since, self.TASK_FIELDS)
        for t in rows:
            uid = parse_uid(self._cf(t, self.f_uid, "text_value"))
            if uid:
                found.setdefault(uid["id"], t)
        return found

    @staticmethod
    def _cf(task: dict, gid: str, key: str):
        for c in task.get("custom_fields") or []:
            if c.get("gid") == gid:
                v = c.get(key)
                return v
        return None

    def _status_code(self, task: dict) -> str:
        ev = self._cf(task, self.f_status, "enum_value") or {}
        return self.code_of.get(ev.get("gid"), NONE)

    def _approver_from_history(self, task_gid: str, me_gid: str) -> str | None:
        accepted = {self.opt[c] for c in ACCEPTED if c in self.opt}
        latest = None
        for s in self.asana.stories(task_gid):
            if s.get("resource_subtype") != "enum_custom_field_changed":
                continue
            if (s.get("custom_field") or {}).get("gid") != self.f_status:
                continue
            if (s.get("new_enum_value") or {}).get("gid") not in accepted:
                continue
            who = (s.get("created_by") or {}).get("gid")
            if who and who != me_gid:
                latest = who
        return latest

    def _days_remaining(self, iv: dict) -> float | None:
        """Leave balance for this person and policy, if Jibble reports one."""
        if not self.f_days or not iv.get("personId"):
            return None
        try:
            for row in self.jibble.leave_balances(iv["personId"], date.today().isoformat()):
                if row.get("policyId") == iv.get("policyId") and row.get("balance") is not None:
                    return float(row["balance"])
        except Exception as e:
            self.log(f"leave balance unavailable for request {iv.get('id')}: {type(e).__name__}")
        return None

    def run(self, users_by_email: dict[str, str]):
        me_gid = self.asana.me()["gid"]
        since = (date.today() - timedelta(days=self.window_days)).isoformat()
        people = {p["id"]: p for p in self.jibble.people()}
        group_names = {g.get("id"): (g.get("name") or "") for g in self.jibble.groups()}
        policies = {p["id"]: p.get("name", "") for p in self.jibble.time_off_policies()}
        existing = self._existing_tasks()

        for iv in self.jibble.time_offs(since):
            self.stats["requests"] += 1
            try:
                self._one(iv, people, group_names, policies, existing, users_by_email, me_gid)
            except Exception as e:  # keep going; one bad request must not stop the rest
                self.stats["errors"] += 1
                self.log(f"holiday request {iv.get('id')}: {type(e).__name__}: {str(e)[:200]}")
        return self.stats

    def _one(self, iv, people, group_names, policies, existing, users_by_email, me_gid):
        iid = iv["id"]
        person = people.get(iv.get("personId")) or (iv.get("person") or {})
        full_name = person.get("fullName") or (iv.get("person") or {}).get("fullName") or "Unknown"
        email = (person.get("email") or "").lower()
        group = person.get("group") if isinstance(person.get("group"), dict) else {}
        group_name = (group or {}).get("name") or group_names.get(person.get("groupId"), "")
        engineer = group_name.lower() in self.engineer_groups
        policy_name = policies.get(iv.get("policyId")) or (iv.get("policy") or {}).get("name", "")
        j_status = iv.get("status") or "Pending"
        h = content_hash(iv)

        task = existing.get(iid)
        state = None
        if task:
            state = {
                "status": self._status_code(task),
                "uid": parse_uid(self._cf(task, self.f_uid, "text_value")),
                "approved_by_set": bool(self._cf(task, self.f_approved, "people_value")),
            }
        d = decide(j_status, h, state, engineer)
        if d is None:
            return

        leave_name = self.policy_leave.get(policy_name)
        leave_opt = self.leave_opts.get(leave_name) if leave_name else None
        uid_text = make_uid(iid, h, d.final_j, d.final_a)

        if d.create:
            data = {
                "name": task_name(full_name, iv),
                "notes": task_notes(full_name, policy_name, iv),
                "projects": [self.project],
                "due_on": iv["endDate"][:10],
                "custom_fields": {self.f_status: self.opt[d.asana_status], self.f_uid: uid_text},
            }
            if iv["startDate"][:10] != iv["endDate"][:10]:
                data["start_on"] = iv["startDate"][:10]
            if leave_opt:
                data["custom_fields"][self.f_leave] = leave_opt
            remaining = self._days_remaining(iv)
            if remaining is not None:
                data["custom_fields"][self.f_days] = remaining
            if users_by_email.get(email):
                data["assignee"] = users_by_email[email]
            created = self.asana.create_task(data, f"Jibble request {iid}")
            self.stats["created"] += 1
            if created and created.get("gid"):
                existing[iid] = created
            return

        gid = task["gid"]
        fields: dict = {self.f_uid: uid_text}
        data: dict = {}
        if d.asana_status:
            fields[self.f_status] = self.opt[d.asana_status]
        if d.update_content:
            data.update({"name": task_name(full_name, iv), "notes": task_notes(full_name, policy_name, iv),
                         "due_on": iv["endDate"][:10]})
            data["start_on"] = iv["startDate"][:10] if iv["startDate"][:10] != iv["endDate"][:10] else None
            if leave_opt:
                fields[self.f_leave] = leave_opt
            remaining = self._days_remaining(iv)
            if remaining is not None:
                fields[self.f_days] = remaining
        if d.fill_approved_by:
            approver = self._approver_from_history(gid, me_gid)
            if approver:
                fields[self.f_approved] = [approver]
        data["custom_fields"] = fields
        self.asana.update_task(gid, data, "holiday sync")
        self.stats["asana_updates"] += 1
        for text in d.comments:
            self.asana.comment(gid, text)
        if d.move_to_cancelled and self.cancelled_section:
            self.asana.add_to_section(gid, self.cancelled_section)
        if d.jibble_status:
            self.jibble.set_time_off_status(iid, d.jibble_status)
            self.stats["jibble_updates"] += 1
