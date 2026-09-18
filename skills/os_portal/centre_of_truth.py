#!/usr/bin/env python3
"""centre_of_truth.py — shared validate->fallback helper for SAS OS portal generators.

Implements the Option B "centre of truth" doctrine (docs/os-portal-centre-of-truth
-data-routing-2026-09-04.md):

  1. HTML inputs read from a LOCAL resource first (ops.db + the epoch Asana disk
     cache in build_os_status.api_get) -- never a fresh synchronous abseil per pane.
  2. Where a displayed field is a STALE surface truth (order status, completion,
     due dates), VALIDATE it against live Asana before rendering -- a bounded,
     per-row reconcile (this module).
  3. If validation fails (token off / Asana unreachable / a row 404s), FALL BACK to
     the ops.db snapshot AND visibly flag that the row is un-reconciled -- never
     silently drop or silently trust.

Read-only. Never writes to Asana or ops.db. Never calls live Asana at import time.
"""

from __future__ import annotations

import requests  # noqa: F401  (used by safe_api_get's default fetch path)


class AsanaUnavailable(Exception):
    """Raised when live Asana cannot be reached (network / HTTP / token failure).

    The typed signal CHG-0B introduces: a full Asana outage degrades to cache +
    DB + flag instead of propagating a raw requests exception up the stack.
    """


def _row_get(row, key):
    """Key access that tolerates dicts, sqlite3.Row, and other mappings."""
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return None


def reconcile_ready(db_rows, live_fetch_row, key="gid", token_h=None, cap=200):
    """Enrich DB rows with a live Asana reconcile -> [(row, live_or_None), ...].

    db_rows        : list of dicts / sqlite3.Row rows from ops.db.
    live_fetch_row : callable (row) -> live dict | None. Returns None when the
                     live check FAILED (unreachable / 404 / token off).
    key            : mapping key holding the Asana task gid for the live lookup.
    token_h        : headers dict. If None the live check is DISABLED and every
                     row is returned as (row, None) -- pure DB fallback.
    cap            : max rows to live-fetch (bounded N+1). Rows beyond cap are
                     passed through unreconciled (live=None).

    Returns (row, live_or_None) for ALL input rows, order preserved.

    NOTE: this function ENRICHES only -- it NEVER drops rows. Deciding which rows
    to drop (e.g. a live 'completed' legacy task) is a per-pane semantic decision
    the caller makes by inspecting the returned `live` dict. (ops-oversight Pane-2
    drops completed legacy rows; the ordering ledger keeps every row.)
    """
    rows = list(db_rows)
    if token_h is None or not live_fetch_row:
        return [(r, None) for r in rows]

    out = []
    for r in rows[:cap]:
        gid = _row_get(r, key)
        live = live_fetch_row(r) if gid else None
        out.append((r, live))
    for r in rows[cap:]:
        out.append((r, None))
    return out


def render_pref(live, db_val):
    """Live-first with DB fallback. Returns (value, unreconciled).

    unreconciled=True when `live` is None/empty (live check failed or token off),
    so the pane can visibly say "live check failed -- showing DB snapshot".
    """
    if live is not None and live != "":
        return live, False
    return db_val, True


def flag_span(unreconciled):
    """Tiny HTML pill marking an unreconciled (DB-fallback) cell. Empty when reconciled."""
    if not unreconciled:
        return ""
    return ('<span class="unrec" title="live check failed — showing DB">'
            '⚠ db</span>')


def safe_api_get(fetch_fn, *args, **kwargs):
    """Call a (usually epoch-cached) GET helper and return None on ANY failure.

    Converts AsanaUnavailable / requests exceptions into a defined None fallback
    signal -- the "degrade instead of crash" primitive for a full Asana outage.
    Callers branch on None to serve cache + DB + flag.
    """
    try:
        return fetch_fn(*args, **kwargs)
    except Exception:
        return None
