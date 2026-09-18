"""
po_number_allocator.py — Fast, collision-free PO numbers.

Why this exists
---------------
PO numbers are assigned by hand because orders are placed quickly and a number is
needed immediately. The cost is that the number is the only thing tying a part to
its order, and it is created outside every system, so the link is lost at source.

Why the number cannot be inferred from ops.db
---------------------------------------------
procurement_tasks.po_number holds 933 distinct 5-digit prefixes across 1,529 rows.
Supplier order refs and invoice numbers have been typed into the same field, so
"max + 1" over that column would hand out a number in the 29,000s. This module
keeps its own authoritative counter in po_number_ledger, seeded once from the real
PO book.

The model: reserve now, confirm later
-------------------------------------
    reserve()  — atomic, sub-millisecond, no network. Returns a number instantly.
    confirm()  — attaches the number to the Asana order task once it exists.
    void()     — returns an unused number to the record with a reason.

A reserved number is never handed out twice, even if two people reserve at the
same instant, because the allocation is a single SQLite transaction with an
IMMEDIATE lock. Nothing waits on Asana, so the desk speed is unchanged.

Reserved-but-unconfirmed numbers are visible, so a number taken and not used shows
up rather than silently becoming a gap.

Usage:
    python po_number_allocator.py --seed 21620          # once, from the PO book
    python po_number_allocator.py --reserve --project 1597 --vendor "IGUS"
    python po_number_allocator.py --confirm 21621 --task-gid 1218...
    python po_number_allocator.py --void 21621 --reason "supplier quoted instead"
    python po_number_allocator.py --status
"""

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
from path_config import p as _p

try:  # console safety on Windows cp1252 terminals
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DB_PATH = _p("db.ops")

# Reserved numbers older than this with no confirmation are reported as drifting.
STALE_RESERVATION_HOURS = 48

# A manually typed number pushes the counter forward, but only within a sane
# distance. procurement_tasks.po_number is polluted with supplier and invoice
# refs (Amazon order numbers reach 29967), so an unbounded watermark would jump
# the counter by eight thousand.
WATERMARK_MAX_JUMP = 200


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def date_suffix(when: datetime | None = None) -> str:
    """House format: day NOT zero-padded, month and year padded.

    Verified against 400 live POs — "70926" is 7 Sep 26, "250826" is 25 Aug 26.
    Python's %d would render the first as "070926", which does not match the book.
    """
    d = when or datetime.now(timezone.utc)
    return f"{d.day}{d.month:02d}{d.year % 100:02d}"


def build_reference(number: int, project_code: str | None,
                    when: datetime | None = None) -> str:
    """<sequential number>-<project id if there is one, else today's date>."""
    return f"{number}-{project_code or date_suffix(when)}"


def watermark(conn) -> int:
    """Highest live PO number observed in Asana, bounded so pollution cannot jump us."""
    row = conn.execute("SELECT MAX(po_number) AS m FROM po_number_ledger").fetchone()
    ledger_max = int(row["m"]) if row and row["m"] is not None else 0
    seen = conn.execute("""
        SELECT po_number FROM procurement_tasks
         WHERE po_number IS NOT NULL AND po_number <> ''
           AND scope IN ('live','unknown')
    """).fetchall()
    best = ledger_max
    for r in seen:
        head = str(r["po_number"]).split("-", 1)[0].strip()
        if not head.isdigit():
            continue
        n = int(head)
        if ledger_max < n <= ledger_max + WATERMARK_MAX_JUMP:
            best = max(best, n)
    return best


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, isolation_level=None, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def seed(conn, start: int, note: str = "seeded from PO book") -> None:
    """Set the counter. Call once. Refuses to move the counter backwards."""
    row = conn.execute("SELECT MAX(po_number) AS m FROM po_number_ledger").fetchone()
    current = row["m"] or 0
    if start <= current:
        sys.exit(f"Refusing to seed at {start}: ledger already holds {current}")
    conn.execute("""
        INSERT INTO po_number_ledger
            (po_number, state, reserved_at, confirmed_at, note)
        VALUES (?, 'confirmed', ?, ?, ?)
    """, (start, _now(), _now(), note))
    print(f"Seeded. Next reservation will be {start + 1}.")


def project_code_for_task(conn, task_gid: str) -> str | None:
    """Resolve the project id the PO should carry, from what we already mirror.

    A PO takes the project id when the order sits in a manufacture project or
    carries the project id tag; otherwise it takes today's date. This reads the
    mirrored project_code rather than calling Asana, so it stays instant.
    """
    if not task_gid:
        return None
    for sql in (
        "SELECT project_code FROM procurement_tasks WHERE asana_task_gid = ?",
        "SELECT project_code FROM parts_control    WHERE asana_task_gid = ?",
    ):
        row = conn.execute(sql, (task_gid,)).fetchone()
        if row and row["project_code"]:
            return row["project_code"]
    return None


def reserve(conn, project_code: str | None = None, vendor: str | None = None,
            requested_by: str = "purchasing", task_gid: str | None = None) -> dict:
    """Take the next number atomically. No network, no Asana call, no waiting.

    The number may be applied to a task that already exists (a NEEDS item, a
    project order, an item moved off another order) or to one raised fresh. The
    ledger does not care which — it issues the number and records what it was
    bound to, and the binding can change later via rebind().
    """
    if task_gid and not project_code:
        project_code = project_code_for_task(conn, task_gid)
    # Absorb anything typed by hand BEFORE taking the next number. Without this
    # the watermark simply jumps the counter and every number between the ledger
    # and the manual one vanishes with no record — the opposite of the promise
    # that a gap is always explicable.
    adopt_observed(conn)
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT MAX(po_number) AS m FROM po_number_ledger").fetchone()
        if not row or row["m"] is None:
            conn.execute("ROLLBACK")
            sys.exit("Ledger is empty — run --seed <number> first, from the real PO book.")
        number = max(int(row["m"]), watermark(conn)) + 1
        suffix = project_code or date_suffix()
        full = build_reference(number, project_code)
        conn.execute("""
            INSERT INTO po_number_ledger
                (po_number, suffix, full_reference, state, reserved_at,
                 requested_by, vendor_name, project_code, asana_task_gid, origin)
            VALUES (?,?,?, 'reserved', ?,?,?,?,?, 'reserved')
        """, (number, suffix, full, _now(), requested_by, vendor, project_code, task_gid))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return {"po_number": number, "suffix": suffix, "full_reference": full,
            "project_code": project_code, "asana_task_gid": task_gid}


def adopt(conn, number: int, full_reference: str, task_gid: str | None,
          vendor: str | None = None, project_code: str | None = None) -> bool:
    """Record a number that was typed by hand without reserving one.

    This is what makes the transition safe: manual numbering keeps working, the
    ledger absorbs it, and the counter moves forward so the same number is never
    issued twice. Returns True if a new row was adopted.
    """
    exists = conn.execute(
        "SELECT state FROM po_number_ledger WHERE po_number = ?", (number,)).fetchone()
    if exists:
        return False
    suffix = full_reference.split("-", 1)[1] if "-" in full_reference else ""
    conn.execute("""
        INSERT INTO po_number_ledger
            (po_number, suffix, full_reference, state, reserved_at, confirmed_at,
             requested_by, vendor_name, project_code, asana_task_gid, origin, note)
        VALUES (?,?,?, 'confirmed', ?,?, 'manual', ?,?,?, 'manual',
                'adopted from Asana — issued outside the ledger')
    """, (number, suffix, full_reference, _now(), _now(), vendor,
          project_code, task_gid))
    return True


def adopt_observed(conn) -> int:
    """Adopt every live PO number seen in Asana that the ledger does not hold.

    This is the transition mechanism. Manual numbering keeps working; the ledger
    absorbs each number as it appears, with origin='manual', so the counter moves
    forward honestly instead of jumping and leaving holes behind it.
    """
    row = conn.execute("SELECT MAX(po_number) AS m FROM po_number_ledger").fetchone()
    ledger_max = int(row["m"]) if row and row["m"] is not None else 0
    if not ledger_max:
        return 0
    n = 0
    for r in conn.execute("""
        SELECT asana_task_gid, po_number, project_code FROM procurement_tasks
         WHERE po_number IS NOT NULL AND po_number <> ''
           AND scope IN ('live','unknown')
    """).fetchall():
        head = str(r["po_number"]).split("-", 1)[0].strip()
        if not head.isdigit():
            continue
        num = int(head)
        if ledger_max < num <= ledger_max + WATERMARK_MAX_JUMP:
            if adopt(conn, num, str(r["po_number"]), r["asana_task_gid"],
                     project_code=r["project_code"]):
                n += 1
    return n


def gaps(conn) -> list[int]:
    """Numbers below the ledger maximum that were never issued or observed.

    A gap is not an error — it may be a number in someone's hand right now — but
    it must be visible rather than silently absorbed.
    """
    rows = [int(r["po_number"]) for r in
            conn.execute("SELECT po_number FROM po_number_ledger ORDER BY po_number")]
    if len(rows) < 2:
        return []
    held = set(rows)
    return [n for n in range(rows[0], rows[-1]) if n not in held]


def rebind(conn, number: int, task_gid: str, reason: str = "") -> None:
    """Move a PO to a different task.

    Orders are messy: an item comes back unavailable and moves to another order,
    a NEEDS item takes a direct PO, a line is re-homed onto a project task or a
    new one. The number stays; the task it points at changes. The previous
    binding is kept in the note so the move is auditable.
    """
    row = conn.execute(
        "SELECT asana_task_gid, note FROM po_number_ledger WHERE po_number = ?",
        (number,)).fetchone()
    if not row:
        sys.exit(f"PO {number} is not in the ledger — adopt it first.")
    trail = (row["note"] or "")
    if row["asana_task_gid"] and row["asana_task_gid"] != task_gid:
        trail = (trail + f" | rebound {row['asana_task_gid']} -> {task_gid} "
                         f"on {_now()}{(': ' + reason) if reason else ''}").strip(" |")
    conn.execute(
        "UPDATE po_number_ledger SET asana_task_gid = ?, note = ? WHERE po_number = ?",
        (task_gid, trail, number))
    print(f"PO {number} now bound to {task_gid}")


def confirm(conn, number: int, task_gid: str | None = None,
            vendor: str | None = None) -> None:
    cur = conn.execute("""
        UPDATE po_number_ledger
           SET state='confirmed', confirmed_at=?,
               asana_task_gid=COALESCE(?, asana_task_gid),
               vendor_name=COALESCE(?, vendor_name)
         WHERE po_number=? AND state='reserved'
    """, (_now(), task_gid, vendor, number))
    if not cur.rowcount:
        sys.exit(f"PO {number} is not in a reserved state — nothing confirmed.")
    print(f"PO {number} confirmed.")


def void(conn, number: int, reason: str) -> None:
    cur = conn.execute("""
        UPDATE po_number_ledger
           SET state='voided', voided_at=?, void_reason=?
         WHERE po_number=? AND state='reserved'
    """, (_now(), reason, number))
    if not cur.rowcount:
        sys.exit(f"PO {number} is not in a reserved state — nothing voided.")
    print(f"PO {number} voided: {reason}")


def status(conn) -> None:
    row = conn.execute("""
        SELECT MAX(po_number) AS last,
               SUM(state='reserved')  AS reserved,
               SUM(state='confirmed') AS confirmed,
               SUM(state='voided')    AS voided
          FROM po_number_ledger
    """).fetchone()
    if not row or row["last"] is None:
        print("Ledger empty. Run --seed <number>.")
        return
    print(f"Last issued : {row['last']}")
    print(f"Next number : {int(row['last']) + 1}")
    print(f"Reserved    : {row['reserved'] or 0}")
    print(f"Confirmed   : {row['confirmed'] or 0}")
    print(f"Voided      : {row['voided'] or 0}")

    stale = conn.execute("""
        SELECT po_number, vendor_name, project_code, reserved_at
          FROM po_number_ledger
         WHERE state='reserved'
           AND (julianday('now') - julianday(reserved_at)) * 24 > ?
         ORDER BY po_number
    """, (STALE_RESERVATION_HOURS,)).fetchall()
    if stale:
        print(f"\nReserved over {STALE_RESERVATION_HOURS}h with no order attached:")
        for s in stale:
            print(f"  {s['po_number']}  {s['vendor_name'] or '-'}  "
                  f"{s['project_code'] or '-'}  reserved {s['reserved_at'][:10]}")

    g = gaps(conn)
    if g:
        print(f"\nUnexplained gaps ({len(g)}) — issued outside the ledger, or never used:")
        print("  " + ", ".join(str(x) for x in g[:30]) + (" ..." if len(g) > 30 else ""))


def main() -> None:
    ap = argparse.ArgumentParser(description="Fast PO number allocation")
    ap.add_argument("--db-path", default=DB_PATH)
    ap.add_argument("--seed", type=int, metavar="N")
    ap.add_argument("--reserve", action="store_true")
    ap.add_argument("--project")
    ap.add_argument("--vendor")
    ap.add_argument("--by", default="purchasing")
    ap.add_argument("--confirm", type=int, metavar="N")
    ap.add_argument("--task-gid")
    ap.add_argument("--void", type=int, metavar="N")
    ap.add_argument("--rebind", type=int, metavar="N")
    ap.add_argument("--reason", default="not used")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    conn = _connect(args.db_path)
    try:
        if args.seed:
            seed(conn, args.seed)
        elif args.reserve:
            result = reserve(conn, args.project, args.vendor, args.by, args.task_gid)
            print(result["full_reference"])
        elif args.confirm:
            confirm(conn, args.confirm, args.task_gid, args.vendor)
        elif args.rebind:
            if not args.task_gid:
                sys.exit("--rebind requires --task-gid")
            rebind(conn, args.rebind, args.task_gid, args.reason)
        elif args.void:
            void(conn, args.void, args.reason)
        else:
            status(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
