"""Jibble API client (client-credentials auth, OData collections)."""
from __future__ import annotations

from .http import HttpError, request

IDENTITY_URL = "https://identity.prod.jibble.io/connect/token"
WORKSPACE = "https://workspace.prod.jibble.io/v1"
TIME_TRACKING = "https://time-tracking.prod.jibble.io/v1"


class Jibble:
    def __init__(self, client_id: str, client_secret: str, *, dry_run: bool = False, log=None, http=request):
        self._id = client_id
        self._secret = client_secret
        self._token: str | None = None
        self.dry_run = dry_run
        self.log = log or (lambda *a, **k: None)
        self._http = http

    # -- plumbing ---------------------------------------------------------
    def _auth(self) -> str:
        if not self._token:
            resp = self._http(
                "POST",
                IDENTITY_URL,
                form={"grant_type": "client_credentials", "client_id": self._id, "client_secret": self._secret},
            )
            self._token = resp["access_token"]
        return self._token

    def _call(self, method: str, url: str, **kw):
        for attempt in (0, 1):
            headers = {"Authorization": f"Bearer {self._auth()}"}
            try:
                return self._http(method, url, headers=headers, **kw)
            except HttpError as e:
                if e.status == 401 and attempt == 0:
                    self._token = None  # token expired mid-run
                    continue
                raise

    def _write(self, method: str, url: str, body, what: str):
        if self.dry_run:
            self.log(f"[dry-run] Jibble {what}")
            return None
        return self._call(method, url, json_body=body)

    def get_all(self, url: str, params: dict | None = None) -> list[dict]:
        """Read every page of an OData collection."""
        out: list[dict] = []
        params = dict(params or {})
        page_size = 200
        skip = 0
        next_url = None
        seen_first: set = set()
        for _ in range(500):  # hard stop if an endpoint ignores $skip
            if next_url:
                resp = self._call("GET", next_url)
            else:
                resp = self._call("GET", url, params={**params, "$top": page_size, "$skip": skip})
            rows = (resp or {}).get("value", [])
            first = rows[0].get("id") if rows and isinstance(rows[0], dict) else None
            if first is not None and first in seen_first:
                break  # same page again: the endpoint is not paging
            if first is not None:
                seen_first.add(first)
            out.extend(rows)
            next_url = (resp or {}).get("@odata.nextLink")
            if next_url:
                continue
            if not rows:
                break
            # The server may cap pages below page_size, so a short page is not
            # proof of the end: keep skipping until a page comes back empty.
            skip += len(rows)
        return out

    # -- people -----------------------------------------------------------
    def people(self) -> list[dict]:
        return self.get_all(f"{WORKSPACE}/People")

    def groups(self) -> list[dict]:
        try:
            return self.get_all(f"{WORKSPACE}/Groups")
        except HttpError as e:
            self.log(f"Jibble groups unavailable ({e.status}); engineer detection falls back to Office")
            return []

    # -- time off ---------------------------------------------------------
    def time_off_policies(self) -> list[dict]:
        return self.get_all(f"{WORKSPACE}/TimeOffPolicies")

    def time_offs(self, since_iso: str) -> list[dict]:
        return self.get_all(
            f"{TIME_TRACKING}/TimeOffOverview",
            {
                "$filter": f"endDate ge {since_iso}",
                "$expand": "person($select=fullName,id),policy($select=name,compensation,kind,id)",
            },
        )

    def set_time_off_status(self, interval_id: str, status: str):
        return self._write(
            "PATCH", f"{TIME_TRACKING}/TimeOffIntervals({interval_id})", {"status": status},
            f"time off {interval_id} -> {status}",
        )

    def leave_balances(self, person_id: str, on_date: str) -> list[dict]:
        return self.get_all(
            f"{TIME_TRACKING}/LeaveBalances",
            {"$filter": f"(personId eq {person_id} and isAssigned eq true)", "requestDate": on_date},
        )

    # -- projects, clients, activities -------------------------------------
    def clients(self) -> list[dict]:
        return self.get_all(f"{WORKSPACE}/Clients")

    def create_client(self, client_id: str, name: str):
        return self._write("POST", f"{WORKSPACE}/Clients", {"id": client_id, "name": name}, f"create client {client_id}")

    def projects(self) -> list[dict]:
        return self.get_all(f"{WORKSPACE}/Projects")

    def create_project(self, project_id: str, name: str, client_id: str | None):
        body = {"id": project_id, "name": name}
        if client_id:
            body["clientId"] = client_id
        return self._write("POST", f"{WORKSPACE}/Projects", body, f"create project {project_id}")

    def activities(self) -> list[dict]:
        return self.get_all(f"{WORKSPACE}/Activities")

    def create_activity(self, name: str):
        return self._write("POST", f"{WORKSPACE}/Activities", {"name": name}, f"create activity '{name}'")

    # -- time entries -----------------------------------------------------
    def time_entries(self, date_from: str, date_to: str) -> list[dict]:
        return self.get_all(
            f"{TIME_TRACKING}/TimeEntries",
            {"$filter": f"(belongsToDate ge {date_from} and belongsToDate le {date_to})"},
        )
