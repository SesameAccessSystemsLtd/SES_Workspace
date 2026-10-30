"""Asana REST client covering what the sync needs."""
from __future__ import annotations

from .http import request

BASE = "https://app.asana.com/api/1.0"


class Asana:
    def __init__(self, token: str, *, dry_run: bool = False, log=None, http=request):
        self._headers = {"Authorization": f"Bearer {token}"}
        self.dry_run = dry_run
        self.log = log or (lambda *a, **k: None)
        self._http = http
        self._me: dict | None = None

    def _call(self, method: str, path: str, **kw):
        resp = self._http(method, BASE + path, headers=self._headers, **kw)
        return (resp or {}).get("data") if isinstance(resp, dict) else resp

    def _write(self, method: str, path: str, body, what: str):
        if self.dry_run:
            self.log(f"[dry-run] Asana {what}")
            return None
        return self._call(method, path, json_body={"data": body} if body is not None else None)

    def _pages(self, path: str, params: dict) -> list[dict]:
        out: list[dict] = []
        params = {**params, "limit": 100}
        while True:
            resp = self._http("GET", BASE + path, headers=self._headers, params=params)
            out.extend(resp.get("data", []))
            nxt = (resp.get("next_page") or {}).get("offset")
            if not nxt:
                return out
            params = {**params, "offset": nxt}

    # -- users ------------------------------------------------------------
    def me(self) -> dict:
        if self._me is None:
            self._me = self._call("GET", "/users/me", params={"opt_fields": "gid,name,email"})
        return self._me

    def users_by_email(self, workspace: str) -> dict[str, str]:
        rows = self._pages(f"/workspaces/{workspace}/users", {"opt_fields": "gid,email"})
        return {(u.get("email") or "").lower(): u["gid"] for u in rows if u.get("email")}

    # -- tasks ------------------------------------------------------------
    def task(self, gid: str, fields: str) -> dict:
        return self._call("GET", f"/tasks/{gid}", params={"opt_fields": fields})

    def subtasks(self, gid: str, fields: str) -> list[dict]:
        return self._pages(f"/tasks/{gid}/subtasks", {"opt_fields": fields})

    def project_tasks_modified_since(self, project: str, since_iso: str, fields: str) -> list[dict]:
        # GET /tasks documents modified_since; /projects/{gid}/tasks does not.
        return self._pages("/tasks", {"project": project, "opt_fields": fields, "modified_since": since_iso})

    def search_tasks(self, workspace: str, params: dict, fields: str) -> list[dict]:
        """Workspace search, paged backwards by created_at (search has no offset)."""
        out: list[dict] = []
        before = None
        for _ in range(50):
            p = {**params, "opt_fields": fields + ",created_at", "sort_by": "created_at", "sort_ascending": "false", "limit": 100}
            if before:
                p["created_at.before"] = before
            rows = self._call("GET", f"/workspaces/{workspace}/tasks/search", params=p) or []
            out.extend(rows)
            if len(rows) < 100:
                break
            before = rows[-1]["created_at"]
        return out

    def create_task(self, data: dict, what: str):
        return self._write("POST", "/tasks", data, f"create task ({what})")

    def update_task(self, gid: str, data: dict, what: str):
        return self._write("PUT", f"/tasks/{gid}", data, f"update task {gid} ({what})")

    def add_to_project(self, gid: str, project: str):
        return self._write("POST", f"/tasks/{gid}/addProject", {"project": project}, f"add {gid} to project {project}")

    def add_to_section(self, gid: str, section: str):
        return self._write("POST", f"/sections/{section}/addTask", {"task": gid}, f"move {gid} to section {section}")

    def comment(self, gid: str, text: str):
        return self._write("POST", f"/tasks/{gid}/stories", {"text": text}, f"comment on {gid}")

    def stories(self, gid: str) -> list[dict]:
        return self._pages(
            f"/tasks/{gid}/stories",
            {"opt_fields": "resource_subtype,created_at,created_by.gid,custom_field.gid,new_enum_value.gid"},
        )

    # -- time tracking ----------------------------------------------------
    def time_entries(self, task_gid: str) -> list[dict]:
        return self._pages(
            f"/tasks/{task_gid}/time_tracking_entries",
            {"opt_fields": "duration_minutes,entered_on,created_by.gid,assignee.gid,attributable_to.gid"},
        )

    def create_time_entry(self, task_gid: str, body: dict):
        return self._write("POST", f"/tasks/{task_gid}/time_tracking_entries", body, f"add {body.get('duration_minutes')} min to {task_gid}")

    def update_time_entry(self, entry_gid: str, minutes: int):
        return self._write("PUT", f"/time_tracking_entries/{entry_gid}", {"duration_minutes": minutes}, f"set entry {entry_gid} to {minutes} min")

    def delete_time_entry(self, entry_gid: str):
        if self.dry_run:
            self.log(f"[dry-run] Asana delete entry {entry_gid}")
            return None
        return self._call("DELETE", f"/time_tracking_entries/{entry_gid}")
