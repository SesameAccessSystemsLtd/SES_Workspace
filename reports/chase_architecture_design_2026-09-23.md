# Chase Architecture — Design Note (2026-09-23)

## Giles's ask
1. "What emails do I need to chase?" — orders AND outbound enquiries (requests awaiting reply).
2. How do I decide? Can I see Sent, detect a request, and whether a reply came after 3–5 days?
3. Do it efficiently — no duplicate processing. Sent folder is the primary source.
4. Capture online-portal order confirmations (eBay/Toolstation/Amazon/RS…) → we learn the
   supplier's own order ref (often no field for our PO).
5. Use Outlook categories (QUOTES / INVOICE / Confirm) as inbound triage + history context.
6. Pipe to a cron → live morning report of "issues to address", replacing the Eisenhower brief.

## Verified capabilities (read-only, live 2026-09-23)
- CAN read purchasing mailbox **Sent Items** via Graph (245 sent / 21d). ✔
- CAN group by **conversationId** and detect inbound reply AFTER the send. ✔
- CAN read **message.categories**. ✔
- 50 outbound threads with no reply ≥3d (raw). ~15 are internal/self — need filtering.

## Measured: overdue un-confirmed orders
`overdue_order_chase.py --scan` → **13 strictly-overdue unconfirmed POs**
(live-Asana gate already dropped **121** finished/invoiced — the gate is doing heavy lifting).
Email-thread resolution quality:
- 2 / 13 matched (21135 medium, 21245 Cromwell high)
- **11 / 13 = none** → no resolvable recipient/thread

### Root cause of the 11/13 misses
The chase matcher searches by **our Asana PO number** appearing in the email. For
**online-portal orders** (Amazon 21139, eBay, Toolstation, Cromwell) the email contains the
**supplier's own order number**, not our PO → no match. This is precisely the gap the
**"Confirm" category** fills.

## Category reality (inbox, 60d, 616 msgs)
| Category | Count | Note |
|---|---|---|
| QUOTES | 159 | primary triage category, heavily used |
| INVOICE | 12 | |
| giles hugo | 10 | self-flag |
| Confirm | 2 | underused — but both ARE portal order confirmations (eBay, Toolstation) |

→ Confirms Giles's plan: stand up a **Confirmation** category; it maps directly to the
portal-order gap. Categories are a strong inbound triage signal + history context.

## Outbound ledger reality
| Table | Rows | Meaning |
|---|---|---|
| rfq_outbox | 1 | outbound RFQs essentially untracked |
| response_outbox | 0 | " |
| overdue_chase_log | 14 | chase drafts logged |
| supplier_contacts | 210 | vendor address book (recipient resolution) |

## Proposed design (to discuss — nothing built yet)

### One shared fetch, many consumers (no duplication)
Single hourly Graph pull caches: Inbox + Sent Items + categories + conversationId index into
ops.db (`email_threads` / `email_touchpoints`). Every downstream report reads ops.db — nothing
re-hits Graph. Sent Items is the anchor for outbound; Inbox is the anchor for inbound.

### Three "chase" rules
- **A. Orders** — due passed + live Asana `ORDER Status = Awaiting CONFIRMATION` + incomplete.
  Recipient resolution: (1) Sent thread by PO, (2) supplier_contacts, (3) **portal match by
  supplier + Confirm category + supplier-order-ref**.
- **B. Outbound enquiries** — Sent item, no inbound reply in-conversation after **3–5 days**.
  Filter internal (@sesameaccess.com recipients). Anchor to project/PO/SQR where possible.
- **C. Portal confirmations** — inbound "Confirm"-categorised (or supplier-confirm detected)
  with no matching task/PO → surface as "order placed online, ref = X, no PO field" so the
  reference gets captured. Feeds A's recipient resolution.

### Live morning report (replaces Eisenhower)
One cron, ~07:00, read-only, delivers to this topic:
- Orders overdue + unconfirmed (rule A)
- Outbound no-reply ≥3d (rule B, internal filtered)
- Portal confirmations needing a ref captured (rule C)
- Counts + links; silent when nothing to report.

### Retire
- `daily_brief.py` (Eisenhower) — already paused since 2026-09-07. Recommend REMOVE so it
  never resurfaces. Its unique value (unmapped-parts flag, dev-log) folds into the new report.

## Open questions for Giles
1. Reply window: 3 days or 5? Configurable per thread type?
2. Confirm category name: "Confirm" (existing) or rename to "Confirmation"?
3. Morning report time + which Telegram topic (currently :4 = this one).
4. Build order: shared fetch first, then rule B (outbound, highest new value), then C, then A.
