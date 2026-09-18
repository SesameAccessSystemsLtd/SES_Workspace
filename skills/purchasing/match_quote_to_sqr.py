#!/usr/bin/env python3
"""
match_quote_to_sqr.py — Stage P4: attach a returned supplier quote to our issued SQR.

Problem (Giles 2026-08-27): returned quotes often arrive in a SEPARATE email chain from
the RFQ request, so there's no direct reply-thread link. But the quote's SENDER domain
tells us which supplier quoted, and we know which SQRs we issued to that supplier. So we
can match "who sent the quote / from what domain" -> our rfq_outbox SQR -> and then hold
the supplier's own quote reference + confirmation + invoice numbers against it.

Also captures the QUOTE SENDER (email + name) into supplier_contacts as source=quote_sender,
so outbound replies route back to the actual person who quoted, not the generic inbox.

Matching order (best to worst):
  1. Explicit SQR in the email subject/body (e.g. "re: SQR-46787") — exact.
  2. Exact supplier-name match (rfq_outbox.supplier_name == the sender's supplier name).
  3. Domain match: sender domain == rfq_outbox.supplier_email domain, choose the most
     recent OPEN ('draft'/'sent') SQR to that supplier.
  4. Fallback: report unmatched, never guess.

Usage (CLI):
    python match_quote_to_sqr.py \
      --from "jim.kernahan@cogentdistribution.com" --from-name "Jim Kernahan" \
      [--supplier "COGENT"] \
      --quote-ref "Q-12345" \
      [--confirmation "CNF-88"] [--invoice "INV-901"] \
      [--explicit-sqr "SQR-46787"] \
      [--dry-run]

Also importable:  result = process_quote(sender_email, sender_name, supplier,
                   quote_ref, confirmation, invoice, explicit_sqr, dry_run=False)
Read-only except updating rfq_outbox + inserting a quote_sender contact row.
"""
import argparse
import re
import sqlite3
import sys
from pathlib import Path

WS = Path(r"C:/Users/GilesHugo/SES_Workspace")
sys.path.insert(0, str(WS / "lib"))
try:
    from path_config import p as _p
    DEFAULT_DB_PATH = _p("db.ops")
except Exception:
    DEFAULT_DB_PATH = str(WS / "db" / "ops.db")


def domain_of(email: str) -> str:
    m = re.search(r"@([\w.\-]+)", email or "")
    return m.group(1).lower() if m else ""


def match_sqr(conn, *, sender_email, supplier, explicit_sqr, quote_ref):
    """Return the rfq_outbox row(s) this quote belongs to, best match first."""
    rows = [dict(r) for r in conn.execute("SELECT * FROM rfq_outbox ORDER BY id").fetchall()]
    if not rows:
        return [], "no_outbound_rfq_yet"

    # 1) Explicit SQR
    if explicit_sqr:
        hit = [r for r in rows if r["sqr_number"] == explicit_sqr]
        if hit:
            return hit[:1], "explicit_sqr"

    # 2) Exact supplier name
    if supplier:
        name_hit = [r for r in rows if (r["supplier_name"] or "").strip().lower() == supplier.strip().lower()]
        if name_hit:
            return name_hit[:1], "supplier_name"

    # 3) Domain match against rfq_outbox.supplier_email domain — most recent open first.
    sd = domain_of(sender_email)
    if sd:
        cands = [r for r in rows if domain_of(r.get("supplier_email")) == sd]
        if cands:
            # prefer open (not yet received), else most recent
            open_c = [r for r in cands if r["status"] in ("draft", "sent")]
            pool = open_c or cands
            pool.sort(key=lambda r: r["id"], reverse=True)
            return pool[:1], "domain"

    return [], "no_match"


def process_quote(*, sender_email, sender_name="", supplier="", quote_ref="",
                  confirmation="", invoice="", explicit_sqr="", dry_run=False,
                  conn=None):
    """Attach a returned supplier quote to the right issued SQR.

    Importable core used by 03_proposal_engine's auto-hook. Returns a dict:
      {matched: bool, how: str, sqr: str, supplier: str, note: str}
    Mutates rfq_outbox (quote/confirmation/invoice/sender) + supplier_contacts
    (quote_sender) UNLESS dry_run=True. Never guesses on no-match.
    """
    owned = conn is None
    if owned:
        conn = sqlite3.connect(DEFAULT_DB_PATH)
        conn.row_factory = sqlite3.Row

    hits, how = match_sqr(
        conn, sender_email=sender_email, supplier=supplier,
        explicit_sqr=explicit_sqr, quote_ref=quote_ref)

    if not hits:
        note = f"NO_MATCH — couldn't attach quote. sender={sender_email} how={how}"
        if owned:
            conn.close()
        return {"matched": False, "how": how, "sqr": "", "supplier": "", "note": note}

    row = hits[0]
    sqr_num, supplier_name, supplier_email = row["sqr_number"], row["supplier_name"], row.get("supplier_email")

    if dry_run:
        note = f"DRY RUN matched via {how}: {sqr_num} -> {supplier_name} — would set quote_ref/confirmation/invoice/sender"
        if owned:
            conn.close()
        return {"matched": True, "how": how, "sqr": sqr_num, "supplier": supplier_name, "note": note}

    # Update rfq_outbox with the quote/confirmation/invoice + quote sender.
    conn.execute("""
        UPDATE rfq_outbox SET
            supplier_quote_ref=?,
            confirmation_number=?,
            invoice_number=?,
            quote_sender_email=?,
            quote_sender_name=?,
            received_at=COALESCE(received_at, ?),
            status='received'
        WHERE sqr_number=?
    """, (quote_ref or None, confirmation or None, invoice or None,
          sender_email or None, sender_name or None, None, sqr_num))
    conn.commit()

    # Capture the quote sender as a per-contact row (reply routing).
    sender_capture = ""
    if sender_email:
        try:
            from build_supplier_contacts import add_quote_sender
            add_quote_sender(row.get("supplier_gid") or supplier_name, sender_email, sender_name)
            sender_capture = f" Captured sender {sender_email} in supplier_contacts."
        except Exception as exc:
            sender_capture = f" (quote_sender capture skipped: {exc})"

    note = (f"SQR {sqr_num} updated: quote_ref={quote_ref} confirmation={confirmation} "
            f"invoice={invoice} sender={sender_email} -> status=received.{sender_capture if not dry_run else ''}")

    if owned:
        conn.close()
    return {"matched": True, "how": how, "sqr": sqr_num, "supplier": supplier_name, "note": note}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--from", dest="sender_email", required=True)
    ap.add_argument("--from-name", dest="sender_name", default="")
    ap.add_argument("--supplier", default="")
    ap.add_argument("--quote-ref", default="")
    ap.add_argument("--confirmation", default="")
    ap.add_argument("--invoice", default="")
    ap.add_argument("--explicit-sqr", default="")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    result = process_quote(
        sender_email=args.sender_email, sender_name=args.sender_name,
        supplier=args.supplier, quote_ref=args.quote_ref,
        confirmation=args.confirmation, invoice=args.invoice,
        explicit_sqr=args.explicit_sqr, dry_run=args.dry_run)
    print(result["note"])


if __name__ == "__main__":
    main()
