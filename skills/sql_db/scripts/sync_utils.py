"""
sync_utils.py — Shared helpers for all Asana → ops.db nightly sync scripts.

Provides: token auth, paginated task fetching, custom field extraction,
          timestamp helpers, and a standard logging setup.
"""

import json
import logging
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

BASE = "https://app.asana.com/api/1.0"
DEFAULT_PAGE_SIZE = 100

# ── Retry policy ───────────────────────────────────────────────────────────
# Asana rate-limits aggressively during business hours. Without retries a
# single 429 aborts the whole run, which fails the pipeline and starves
# every downstream stage (notably the approval-sheet writer).
RETRY_MAX_ATTEMPTS = 5
RETRY_BASE_DELAY = 2.0     # seconds; doubled each attempt
RETRY_MAX_DELAY = 60.0     # ceiling for a single sleep
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


class AsanaFetchError(RuntimeError):
    """Raised when an Asana request still fails after exhausting retries."""


def _retry_after_seconds(resp: "requests.Response | None") -> float | None:
    """Parse Asana's Retry-After header (delta-seconds form) if present."""
    if resp is None:
        return None
    raw = resp.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(0.0, float(raw.strip()))
    except (TypeError, ValueError):
        return None


def request_with_retry(method: str, url: str, **kwargs) -> "requests.Response":
    """Issue an HTTP request, retrying transient failures with backoff + jitter.

    Retries on connection errors, timeouts, 429 and 5xx. Honours Retry-After
    when the server supplies it. Does NOT retry other 4xx responses (bad token,
    missing permission, deleted project) — those are deterministic, so retrying
    only delays a failure that a human needs to see.
    """
    last_exc: Exception | None = None

    for attempt in range(1, RETRY_MAX_ATTEMPTS + 1):
        resp = None
        try:
            resp = requests.request(method, url, **kwargs)
            if resp.status_code not in RETRY_STATUS:
                resp.raise_for_status()
                return resp
            last_exc = requests.HTTPError(
                f"{resp.status_code} {resp.reason} for {url}", response=resp
            )
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
        except requests.HTTPError:
            raise  # non-retryable 4xx — surface immediately

        if attempt == RETRY_MAX_ATTEMPTS:
            break

        delay = _retry_after_seconds(resp)
        if delay is None:
            delay = min(RETRY_BASE_DELAY * (2 ** (attempt - 1)), RETRY_MAX_DELAY)
        # Full jitter: avoids the concurrent purchasing crons retrying in lockstep.
        delay = random.uniform(0, delay)

        logging.warning(
            "Asana request failed (attempt %d/%d): %s — retrying in %.1fs",
            attempt, RETRY_MAX_ATTEMPTS, last_exc, delay,
        )
        time.sleep(delay)

    raise AsanaFetchError(
        f"Asana request failed after {RETRY_MAX_ATTEMPTS} attempts: {last_exc}"
    ) from last_exc


def get_token() -> str:
    token = os.environ.get("ASANA_PERSONAL_ACCESS_TOKEN") or os.environ.get("ASANA_PAT")
    if not token:
        for path in [
            Path(__file__).parent / "ASANAconfig.json",
            Path(r"C:\Users\GilesHugo\SES_Workspace\skills\bom_pipeline\ASANAconfig.json"),
            Path(r"C:\Users\GilesHugo\OneDrive - SESAME ACCESS AZURE\OneDrive Documents\LAB. SESAME\Asana Scripts\ASANAconfig.json"),
        ]:
            try:
                with open(path) as f:
                    cfg = json.load(f)
                token = cfg.get("ASANA_PERSONAL_ACCESS_TOKEN") or cfg.get("ASANA_PAT")
                if token:
                    break
            except (FileNotFoundError, json.JSONDecodeError):
                continue
    if not token:
        logging.critical("ASANA_PERSONAL_ACCESS_TOKEN not found in environment or config files")
        sys.exit(1)
    return token


def _headers() -> dict:
    return {"Authorization": f"Bearer {get_token()}", "Accept": "application/json"}


def paginate_tasks(project_gid: str, opt_fields: str,
                   completed: bool = False, max_pages: int = 50,
                   all_completion_states: bool = False) -> list[dict]:
    """Fetch all tasks (paginated) from an Asana project. Excludes completed by default.

    all_completion_states=True fetches both completed and incomplete tasks.
    max_pages caps the total pages fetched (default 50 = 5000 tasks) to prevent
    runaway pagination on very large historic projects.
    """
    url = f"{BASE}/projects/{project_gid}/tasks"
    params = {
        "opt_fields": opt_fields,
        "limit": DEFAULT_PAGE_SIZE,
    }
    if not all_completion_states:
        params["completed"] = "false" if not completed else "true"

    results = []
    page = 0
    while url:
        if page >= max_pages:
            logging.warning(
                "paginate_tasks: hit max_pages=%d limit for project %s — %d tasks fetched, truncating",
                max_pages, project_gid, len(results),
            )
            break
        r = request_with_retry("GET", url, headers=_headers(), params=params, timeout=30)
        body = r.json()
        results.extend(body.get("data", []))
        nxt = body.get("next_page")
        url = nxt.get("uri") if nxt else None
        params = {}
        page += 1
    return results


def get_custom_field(task: dict, field_gid: str) -> Any:
    """Extract the value of a custom field from an Asana task dict.

    Returns the display_value string for most types, or the enum name for enum fields.
    Returns None if the field is not present or has no value.
    """
    for cf in task.get("custom_fields", []):
        if cf.get("gid") != field_gid:
            continue
        ftype = cf.get("type") or cf.get("resource_subtype", "")
        if ftype == "enum":
            ev = cf.get("enum_value")
            return ev.get("name") if ev else None
        if ftype == "multi_enum":
            return ", ".join(e.get("name", "") for e in cf.get("multi_enum_values", []) if e)
        if ftype == "people":
            return ", ".join(p.get("name", "") for p in cf.get("people_value", []) if p)
        val = cf.get("display_value") or cf.get("number_value") or cf.get("text_value")
        return val
    return None


def get_enum_gid(task: dict, field_gid: str) -> str | None:
    """Return the raw enum GID for an enum custom field (used when writing back to Asana)."""
    for cf in task.get("custom_fields", []):
        if cf.get("gid") == field_gid:
            ev = cf.get("enum_value")
            return ev.get("gid") if ev else None
    return None


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def setup_logging(name: str, level: int = logging.INFO) -> logging.Logger:
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    return logging.getLogger(name)
