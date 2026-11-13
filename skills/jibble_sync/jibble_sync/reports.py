"""Everhour-layout exports built from Jibble (read-only: nothing is written to Jibble or Asana).

payroll        Member, Time, Day, Parent Task, Task   (what the payroll scripts read)
manufacturing  per top-level task: member hours, Estimated Hours, Actual Cost
"""
from __future__ import annotations

import csv
from collections import defaultdict
from datetime import date, datetime, timedelta

from .mapping import canonical_activity, choose_subtask
from .segments import build_segments
from .timesync import PART_FIELDS, SUB_FIELDS, jibble_project_id


def _hours(minutes: float) -> str:
    return f"{minutes / 60:.2f}".rstrip("0").rstrip(".") or "0"


def _uk(d: str) -> str:
    return date.fromisoformat(d).strftime("%d/%m/%Y")


def _period(d0: str, d1: str) -> str:
    a, b = date.fromisoformat(d0), date.fromisoformat(d1)
    return f"Period: {a.strftime('%b')} {a.day} - {b.strftime('%b')} {b.day}, {b.year}"


class Reports:
    def __init__(self, cfg: dict, asana, jibble, log):
        self.cfg, self.asana, self.jibble, self.log = cfg, asana, jibble, log
        f = cfg["asana"]["fields"]
        self.f_est_time, self.f_est_hours, self.f_cost = f["estimated_time"], f["estimated_hours"], f["actual_cost"]
        self.f_project_name = f["project_name"]
        people_cfg = cfg.get("people", {})
        self.default_hours = float(people_cfg.get("default_hours_per_day", 9))
        self.hours_by_name = {k.lower(): float(v) for k, v in (people_cfg.get("hours_per_day") or {}).items()}
        self.leave_codes = cfg["jibble"].get("payroll_leave_codes", {})
        self.bank_holidays = set(cfg.get("bank_holidays", []))
        self.bank_holiday_label = cfg["jibble"].get("payroll_bank_holiday_label", "0014 Bank Holiday")
        self.fallback_client = cfg["jibble"].get("fallback_client", "Jibble trial")

    # -- shared loading ------------------------------------------------------
    def _num(self, task, gid):
        for c in task.get("custom_fields") or []:
            if c.get("gid") == gid:
                return c.get("number_value")
        return None

    def _parts(self) -> dict[str, dict]:
        """{jibble project id: part with subtasks} for the monitored parts (read-only)."""
        out = {}
        for gid in self.cfg["asana"].get("monitored_parts", []):
            part = self.asana.task(gid, PART_FIELDS)
            part["gid"] = gid
            part["_subtasks"] = self.asana.subtasks(gid, SUB_FIELDS)
            client = None
            for c in part.get("custom_fields") or []:
                if c.get("gid") == self.f_project_name:
                    client = c.get("display_value")
            part["_client"] = client or self.fallback_client
            part["_top"] = ((part.get("parent") or {}).get("name") or part["name"]).strip()
            out[jibble_project_id(gid)] = part
        return out

    def _est_hours(self, task) -> float:
        e_min = self._num(task, self.f_est_time)
        return float(e_min) / 60 if e_min else float(self._num(task, self.f_est_hours) or 0)

    def _segments(self, d0: str, d1: str):
        fetch_from = (date.fromisoformat(d0) - timedelta(days=1)).isoformat()
        segs, _ = build_segments(self.jibble.time_entries(fetch_from, d1))
        return [s for s in segs if d0 <= s.date <= d1]

    def _hours_per_day(self, name: str) -> float:
        return self.hours_by_name.get((name or "").lower(), self.default_hours)

    # -- payroll ---------------------------------------------------------------
    def payroll(self, d0: str, d1: str, out_path: str, users_by_email: dict[str, str] | None = None) -> int:
        users_by_email = users_by_email or {}
        people = {p["id"]: p for p in self.jibble.people()}
        projects = {p["id"]: p for p in self.jibble.projects()}
        activities = {a["id"]: a.get("name", "") for a in self.jibble.activities()}
        parts = self._parts()
        rows: dict[tuple, float] = defaultdict(float)  # (member, day, parent, task) -> minutes

        for s in self._segments(d0, d1):
            member = (people.get(s.person_id) or {}).get("fullName", s.person_id)
            act = activities.get(s.activity_id, "")
            if s.project_id in parts:
                part = parts[s.project_id]
                canon = canonical_activity(act) if act else None
                worker = users_by_email.get(((people.get(s.person_id) or {}).get("email") or "").lower())
                sub = choose_subtask(canon, part["_subtasks"], part["name"], worker) if canon else None
                task = (sub or part)["name"].strip()
                parent, task_col = part["_top"], f"{task} < {part['_top']}"
            else:
                proj = projects.get(s.project_id) or {}
                pname = (proj.get("name") or "(no project)").strip()
                parent = f"{proj['code']} - {pname}" if proj.get("code") else pname
                task_col = f"{act} < {parent}" if act else parent
            rows[(member, s.date, parent, task_col)] += s.minutes

        # Approved time off, converted from days to hours. Weekends and bank
        # holidays are not leave days; Jibble's duration (in days) scales part days.
        policies = {p["id"]: p.get("name", "") for p in self.jibble.time_off_policies()}
        people_in_period = {k[0] for k in rows}
        for iv in self.jibble.time_offs(d0):
            if iv.get("status") != "Approved":
                continue
            member = (people.get(iv.get("personId")) or {}).get("fullName") or (iv.get("person") or {}).get("fullName", "")
            people_in_period.add(member)
            pol = policies.get(iv.get("policyId")) or (iv.get("policy") or {}).get("name", "")
            label = self.leave_codes.get(pol, pol)
            start, end = date.fromisoformat(iv["startDate"][:10]), date.fromisoformat(iv["endDate"][:10])
            working = [start + timedelta(days=i) for i in range((end - start).days + 1)]
            working = [d for d in working if d.weekday() < 5 and d.isoformat() not in self.bank_holidays]
            if not working:
                continue
            hourly = iv.get("startTime") and iv.get("endTime")
            if hourly:
                t0 = datetime.strptime(str(iv["startTime"])[:5], "%H:%M")
                t1 = datetime.strptime(str(iv["endTime"])[:5], "%H:%M")
                per_day = max((t1 - t0).total_seconds() / 60, 0)
            else:
                fraction = 1.0
                try:
                    if iv.get("duration") is not None:
                        fraction = min(float(iv["duration"]) / len(working), 1.0)
                except (TypeError, ValueError):
                    pass
                per_day = self._hours_per_day(member) * 60 * fraction
            for day in working:
                if d0 <= day.isoformat() <= d1:
                    rows[(member, day.isoformat(), label, label)] += per_day

        # Bank holidays, as Everhour recorded them, for everyone with time in the period.
        for bh in sorted(self.bank_holidays):
            if d0 <= bh <= d1 and date.fromisoformat(bh).weekday() < 5:
                for member in people_in_period:
                    rows[(member, bh, self.bank_holiday_label, self.bank_holiday_label)] += self._hours_per_day(member) * 60

        ordered = sorted(rows.items(), key=lambda kv: (kv[0][2].lower(), kv[0][0].lower(), kv[0][1]))
        with open(out_path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["Payroll Export", "", "", "", ""])
            w.writerow([_period(d0, d1), "", "", "", ""])
            w.writerow(["Member", "Time", "Day", "Parent Task", "Task"])
            for (member, day, parent, task), minutes in ordered:
                w.writerow([member, _hours(minutes), _uk(day), parent, task])
            total = sum(rows.values())
            w.writerow(["Total", _hours(total), f"{len({k[1] for k in rows})} days",
                        f"{len({k[2] for k in rows})} tasks", f"{len({k[3] for k in rows})} tasks"])
        return len(rows)

    # -- manufacturing ---------------------------------------------------------
    def manufacturing(self, d0: str, d1: str, out_path: str, client: str | None = None) -> int:
        people = {p["id"]: p for p in self.jibble.people()}
        parts = {pid: p for pid, p in self._parts().items() if not client or p["_client"].lower() == client.lower()}
        minutes: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))  # top task -> member -> min
        est: dict[str, float] = defaultdict(float)
        cost: dict[str, float] = defaultdict(float)
        for part in parts.values():
            top = part["_top"]
            sub_est = [self._est_hours(t) for t in part["_subtasks"]]
            # A part's own estimate usually sums its subtasks: use it only when no subtask has one.
            est[top] += sum(sub_est) if any(sub_est) else self._est_hours(part)
            for t in [part, *part["_subtasks"]]:
                cost[top] += float(self._num(t, self.f_cost) or 0)
            minutes.setdefault(top, defaultdict(float))
        for s in self._segments(d0, d1):
            if s.project_id in parts:
                member = (people.get(s.person_id) or {}).get("fullName", s.person_id)
                minutes[parts[s.project_id]["_top"]][member] += s.minutes

        title = client or "Monitored parts"
        with open(out_path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow([f"{title} - Manufacturing Hours"])
            w.writerow([_period(d0, d1)])
            w.writerow(["Member", "Time", "Estimated Hours", "Actual Cost", "Task", "Date"])
            grand = 0.0
            for top in sorted(minutes):
                total = sum(minutes[top].values())
                grand += total
                e, c = f"{est[top]:.2f}".rstrip("0").rstrip("."), f"{cost[top]:.2f}".rstrip("0").rstrip(".")
                w.writerow([top, _hours(total), e or "0", c or "0"])
                for member in sorted(minutes[top]):
                    w.writerow([member, _hours(minutes[top][member]), e or "0", c or "0", top, ""])
                w.writerow(["Subtotal", _hours(total), e or "0", c or "0", "1 task", "0 items"])
            w.writerow(["Total", _hours(grand), f"{sum(est.values()):.2f}", f"{sum(cost.values()):.2f}",
                        f"{len(minutes)} tasks", "0 items"])
        return len(minutes)
