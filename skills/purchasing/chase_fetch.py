"""
chase_fetch.py — the ONE shared Graph fetch for the SAS purchasing chase architecture.

Pulls Inbox + Sent Items + the named supplier folders from the purchasing mailbox and
caches metadata (categories, conversationId, direction, sender/recipients) into ops.db
tables `email_touchpoints` + `email_threads`. Every chase rule and the morning report
then read ops.db only — nothing re-hits Graph.

Net-new schema (additive — does NOT touch any existing table):
    email_touchpoints   one row per message (id, conversationId, categories, direction…)
    email_threads       one row per conversation, aggregated from touchpoints
    chase_fetch_state   durable watermark (last_success_at_utc)

Behaviour:
  * Incremental after the first run (watermark), with an overlap to be crash-safe.
  * First run (or --days N) backfills N days so rule A can match old Sent Items.
  * Idempotent: upsert by Graph message id. Re-runnable any number of times.
  * Read-only with respect to the mailbox (Graph GET only) and to other tables.

Env creds are resolved by chase_env.get_graph_creds() — the single normaliser that
copes with the transposed-typo / duplicate key names. Key names only are logged.

Run:  python chase_fetch.py [--days 120] [--folder-overlap]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import requests

from chase_env import get_graph_creds, describe_usage

MAILBOX = "purchasing@sesameaccess.com"
GRAPH = "https://graph.microsoft.com/v1.0"
INTERNAL_DOMAINS = {"sesameaccess.com", "sesame-access.com"}

# Inbox + Sent Items are the anchors. Named folders are supplier-moved copies; they are
# fetched too (to catch messages already filed out of Inbox) but deduped by internet id.
FOLDERS = [
    ("inbox", "Inbox", "inbound"),
    ("sentitems", "Sent Items", "outbound"),
]
NAMED_FOLDERS = ["1.3 Confirmations"]

# First-run / explicit backfill window. 120d covers Sent Items for old POs in rule A.
DEFAULT_BACKFILL_DAYS = 120
# Incremental overlap behind the watermark so a message arriving mid-run is re-seen
# next run (dedup by id makes the overlap harmless).
WATERMARK_OVERLAP_MINUTES = 90

# Fields we cache. bodyPreview is the lightweight text signal; categories is the key
# inbound-triage signal. We deliberately do NOT pull full bodies/attachments here —
# that is the email-parser pipeline's job (email_documents), not the chase cache's.
SELECT_FIELDS = (
    "id,internetMessageId,conversationId,subject,sender,from,toRecipients,ccRecipients,"
    "bodyPreview,categories,sentDateTime,receivedDateTime,webLink"
)

DEFAULT_DB = r"C:\Users\GilesHugo\SES_Workspace\db\ops.db"


# ── DB ──────────────────────────────────────────────────────────────────────
def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS email_touchpoints (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id           TEXT UNIQUE,
            internet_message_id  TEXT,
            conversation_id      TEXT,
            folder               TEXT,
            direction            TEXT,
            sender_email         TEXT,
            sender_name          TEXT,
            sender_domain        TEXT,
            to_recipients        TEXT,
            cc_recipients        TEXT,
            subject              TEXT,
            body_preview         TEXT,
            categories           TEXT,
            sent_at              TEXT,
            received_at          TEXT,
            web_link             TEXT,
            is_internal          INTEGER DEFAULT 0,
            fetched_at           TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_tp_conv      ON email_touchpoints(conversation_id);
        CREATE INDEX IF NOT EXISTS idx_tp_inet      ON email_touchpoints(internet_message_id);
        CREATE INDEX IF NOT EXISTS idx_tp_dir_time  ON email_touchpoints(direction, received_at);
        CREATE INDEX IF NOT EXISTS idx_tp_domain    ON email_touchpoints(sender_domain);
        CREATE INDEX IF NOT EXISTS idx_tp_received  ON email_touchpoints(received_at);

        CREATE TABLE IF NOT EXISTS email_threads (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id       TEXT UNIQUE,
            subject               TEXT,
            first_at              TEXT,
            last_at               TEXT,
            num_touchpoints       INTEGER DEFAULT 0,
            num_inbound           INTEGER DEFAULT 0,
            num_outbound          INTEGER DEFAULT 0,
            last_inbound_at       TEXT,
            last_outbound_at      TEXT,
            updated_at            TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_th_last ON email_threads(last_at);

        CREATE TABLE IF NOT EXISTS chase_fetch_state (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
        """
    )
    conn.commit()


def get_state(conn: sqlite3.Connection, key: str) -> str:
    r = conn.execute("SELECT value FROM chase_fetch_state WHERE key=?", (key,)).fetchone()
    return r["value"] if r else ""


def set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO chase_fetch_state(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()


# ── Graph auth ──────────────────────────────────────────────────────────────
def get_token(creds: dict) -> str:
    r = requests.post(
        f"https://login.microsoftonline.com/{creds['tenant_id']}/oauth2/v2.0/token",
        data={
            "grant_type": "client_credentials",
            "client_id": creds["client_id"],
            "client_secret": creds["client_secret"],
            "scope": "https://graph.microsoft.com/.default",
        },
        timeout=30,
    )
    if r.status_code != 200 or "access_token" not in r.json():
        raise RuntimeError(f"Graph token failed {r.status_code}: {r.text[:300]}")
    return r.json()["access_token"]


def _get_all(url: str, headers: dict, params: dict) -> list[dict]:
    """Follow @odata.nextLink until exhausted."""
    out: list[dict] = []
    while url:
        r = requests.get(url, headers=headers, params=params, timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"Graph GET failed {r.status_code}: {r.text[:300]}")
        j = r.json()
        out += j.get("value") or []
        url = j.get("@odata.nextLink")
        params = None
    return out


def fetch_folder_messages(token: str, folder_path: str, since: str,
                          time_field: str) -> list[dict]:
    """folder_path is a Graph folder id or well-known name; filter by time_field >= since."""
    headers = {"Authorization": f"Bearer {token}"}
    url = f"{GRAPH}/users/{MAILBOX}/mailFolders/{folder_path}/messages"
    params = {
        "$filter": f"{time_field} ge {since}",
        "$select": SELECT_FIELDS,
        "$orderby": f"{time_field} desc",
        "$top": 100,
    }
    return _get_all(url, headers, params)


def resolve_named_folder_id(token: str, name: str) -> str | None:
    """Resolve a folder display name to its Graph id. Searches root folders and one
    level of child folders (e.g. '1.3 Confirmations' is a child of Inbox)."""
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.get(f"{GRAPH}/users/{MAILBOX}/mailFolders",
                     headers=headers, params={"$top": 100}, timeout=30)
    if r.status_code != 200:
        return None
    roots = r.json().get("value", [])
    for f in roots:
        if (f.get("displayName") or "").lower() == name.lower():
            return f["id"]
    # one level of child folders
    for f in roots:
        try:
            cr = requests.get(f"{GRAPH}/users/{MAILBOX}/mailFolders/{f['id']}/childFolders",
                              headers=headers, params={"$top": 100}, timeout=30)
            if cr.status_code != 200:
                continue
            for child in cr.json().get("value", []):
                if (child.get("displayName") or "").lower() == name.lower():
                    return child["id"]
        except Exception:  # noqa: BLE001
            continue
    return None


# ── parsing ─────────────────────────────────────────────────────────────────
def _addr_list(recipients) -> list[str]:
    out = []
    for r in recipients or []:
        a = (r.get("emailAddress") or {}).get("address")
        if a:
            out.append(a.lower())
    return out


def _domain(email: str) -> str:
    return email.split("@")[-1].lower() if "@" in email else ""


def message_to_row(m: dict, folder: str, direction: str, fetched_at: str) -> dict:
    sender_obj = (m.get("sender") or m.get("from") or {}).get("emailAddress") or {}
    sender_email = (sender_obj.get("address") or "").lower()
    sender_name = sender_obj.get("name") or ""
    sdom = _domain(sender_email)
    cats = m.get("categories") or []
    row = {
        "message_id": m.get("id"),
        "internet_message_id": m.get("internetMessageId") or m.get("id"),
        "conversation_id": m.get("conversationId"),
        "folder": folder,
        "direction": direction,
        "sender_email": sender_email,
        "sender_name": sender_name,
        "sender_domain": sdom,
        "to_recipients": json.dumps(_addr_list(m.get("toRecipients"))),
        "cc_recipients": json.dumps(_addr_list(m.get("ccRecipients"))),
        "subject": m.get("subject") or "",
        "body_preview": m.get("bodyPreview") or "",
        "categories": json.dumps(cats),
        "sent_at": m.get("sentDateTime"),
        "received_at": m.get("receivedDateTime") or m.get("sentDateTime"),
        "web_link": m.get("webLink"),
        "is_internal": 1 if sdom in INTERNAL_DOMAINS else 0,
        "fetched_at": fetched_at,
    }
    return row


def upsert_touchpoints(conn: sqlite3.Connection, rows: list[dict]) -> int:
    """Insert-or-replace by message_id. Returns count of new rows."""
    new = 0
    for r in rows:
        cur = conn.execute(
            """
            INSERT INTO email_touchpoints
                (message_id, internet_message_id, conversation_id, folder, direction,
                 sender_email, sender_name, sender_domain, to_recipients, cc_recipients,
                 subject, body_preview, categories, sent_at, received_at, web_link,
                 is_internal, fetched_at)
            VALUES
                (:message_id,:internet_message_id,:conversation_id,:folder,:direction,
                 :sender_email,:sender_name,:sender_domain,:to_recipients,:cc_recipients,
                 :subject,:body_preview,:categories,:sent_at,:received_at,:web_link,
                 :is_internal,:fetched_at)
            ON CONFLICT(message_id) DO UPDATE SET
                categories=excluded.categories,
                received_at=excluded.received_at,
                folder=excluded.folder,
                fetched_at=excluded.fetched_at
            """,
            r,
        )
        if cur.lastrowid and cur.rowcount > 0:
            new += 1
    conn.commit()
    return new


def recompute_threads(conn: sqlite3.Connection, now: str) -> int:
    """Rebuild email_threads as a pure aggregate of email_touchpoints."""
    conn.execute("DELETE FROM email_threads")
    conn.execute(
        """
        INSERT INTO email_threads
            (conversation_id, subject, first_at, last_at, num_touchpoints,
             num_inbound, num_outbound, last_inbound_at, last_outbound_at, updated_at)
        SELECT
            conversation_id,
            (SELECT t2.subject FROM email_touchpoints t2
              WHERE t2.conversation_id = t.conversation_id
              ORDER BY COALESCE(t2.received_at,'') DESC LIMIT 1),
            MIN(COALESCE(received_at,'')),
            MAX(COALESCE(received_at,'')),
            COUNT(*),
            SUM(CASE WHEN direction='inbound' THEN 1 ELSE 0 END),
            SUM(CASE WHEN direction='outbound' THEN 1 ELSE 0 END),
            MAX(CASE WHEN direction='inbound' THEN received_at END),
            MAX(CASE WHEN direction='outbound' THEN received_at END),
            ?
        FROM email_touchpoints t
        WHERE conversation_id IS NOT NULL AND conversation_id != ''
        GROUP BY conversation_id
        """,
        (now,),
    )
    conn.commit()
    return conn.execute("SELECT COUNT(*) FROM email_threads").fetchone()[0]


# ── main fetch ──────────────────────────────────────────────────────────────
def window_since(conn: sqlite3.Connection, backfill_days: int | None) -> tuple[str, str]:
    """Return (since_iso, watermark_note)."""
    wm = get_state(conn, "last_success_at_utc")
    if backfill_days is not None:
        since = (datetime.now(timezone.utc) - timedelta(days=backfill_days)).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        return since, f"explicit backfill {backfill_days}d"
    if wm:
        try:
            t = datetime.strptime(wm, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            since = (t - timedelta(minutes=WATERMARK_OVERLAP_MINUTES)).strftime(
                "%Y-%m-%dT%H:%M:%SZ")
            return since, f"watermark {wm} -{WATERMARK_OVERLAP_MINUTES}m overlap"
        except ValueError:
            pass
    since = (datetime.now(timezone.utc) - timedelta(days=DEFAULT_BACKFILL_DAYS)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    return since, f"no watermark -> first run {DEFAULT_BACKFILL_DAYS}d backfill"


def run_fetch(db_path: str = DEFAULT_DB, backfill_days: int | None = None,
              no_network: bool = False) -> dict:
    conn = connect(db_path)
    ensure_schema(conn)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    creds = get_graph_creds()
    if not (creds["client_id"] and creds["client_secret"] and creds["tenant_id"]):
        raise RuntimeError("Missing Graph credentials. " + describe_usage(creds))

    since, note = window_since(conn, backfill_days)
    result = {
        "since": since, "window_note": note,
        "sent": 0, "inbox": 0, "folders": 0,
        "new_rows": 0, "total_touchpoints": 0, "total_threads": 0,
        "offline": no_network,
    }

    if no_network:
        # schema-only verification path (for tests without network)
        result["total_touchpoints"] = conn.execute("SELECT COUNT(*) FROM email_touchpoints").fetchone()[0]
        result["total_threads"] = recompute_threads(conn, now)
        conn.close()
        return result

    token = get_token(creds)
    all_rows: list[dict] = []

    # 1. Inbox (inbound anchor)
    inbox = fetch_folder_messages(token, "inbox", since, "receivedDateTime")
    for m in inbox:
        all_rows.append(message_to_row(m, "Inbox", "inbound", now))
    result["inbox"] = len(inbox)

    # 2. Sent Items (outbound anchor)
    sent = fetch_folder_messages(token, "sentitems", since, "sentDateTime")
    for m in sent:
        all_rows.append(message_to_row(m, "Sent Items", "outbound", now))
    result["sent"] = len(sent)

    # 3. Named folders (dedup by internet_message_id — only add if not already seen)
    seen_inet = {r["internet_message_id"] for r in all_rows}
    for name in NAMED_FOLDERS:
        try:
            fid = resolve_named_folder_id(token, name)
        except Exception as e:  # noqa: BLE001
            print(f"  [chase_fetch] folder '{name}' resolve skipped: {e}", file=sys.stderr)
            continue
        if not fid:
            print(f"  [chase_fetch] folder '{name}' not found — skipped", file=sys.stderr)
            continue
        try:
            msgs = fetch_folder_messages(token, fid, since, "receivedDateTime")
        except Exception as e:  # noqa: BLE001
            print(f"  [chase_fetch] folder '{name}' fetch skipped: {e}", file=sys.stderr)
            continue
        for m in msgs:
            r = message_to_row(m, name, "inbound", now)
            if r["internet_message_id"] in seen_inet:
                continue
            seen_inet.add(r["internet_message_id"])
            all_rows.append(r)
        result["folders"] += len(msgs)

    result["new_rows"] = upsert_touchpoints(conn, all_rows)

    # Advance watermark (only when we actually reached the network for all anchors).
    set_state(conn, "last_success_at_utc", now)

    result["total_touchpoints"] = conn.execute("SELECT COUNT(*) FROM email_touchpoints").fetchone()[0]
    result["total_threads"] = recompute_threads(conn, now)
    conn.close()
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=None,
                    help="Backfill N days (overrides the watermark).")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--no-network", action="store_true",
                    help="Only ensure schema + recompute threads; no Graph calls.")
    args = ap.parse_args()

    try:
        res = run_fetch(args.db, backfill_days=args.days, no_network=args.no_network)
    except Exception as e:  # noqa: BLE001
        print(f"CHASE FETCH FAILED: {e}", file=sys.stderr)
        sys.exit(1)

    creds = get_graph_creds()
    print(f"[chase_fetch] {describe_usage(creds)}")
    print(f"[chase_fetch] window: {res['window_note']}")
    print(f"[chase_fetch] sent={res['sent']} inbox={res['inbox']} folders={res['folders']} "
          f"new_rows={res['new_rows']}")
    print(f"[chase_fetch] cache now: {res['total_touchpoints']} touchpoints, "
          f"{res['total_threads']} threads")


if __name__ == "__main__":
    main()
