# Overdue Order Chase — Assessment (2026-09-09)

Live-Asana-gated scan of `Awaiting CONFIRMATION` POs. Cross-checked each candidate against live
Asana `ORDER Status` and dropped everything already FINISHED/invoiced. **Result: 35 genuinely open,
un-confirmed, past-due POs** (106 finished/invoiced dropped).

## 🔴 Draft-recipient reality

Only **8 of 35** have a real supplier email thread the tool could thread a chase onto
(`match=high/medium`). The other **27 show `match=none`** — no recipient auto-resolved. Creating a
draft for those requires a recipient decision (I will not guess supplier addresses).

## Priority tiers (most → least urgent)

### Tier 1 — Worst offenders (overdue 30+ days) — chase first
| PO | Supplier | Overdue | Thread | Live PO no / matches |
|---|---|---|---|---|
| 20715-150126 | PARAGON | **237d** | none | — |
| 20985-1575 | SG transmission | **142d** | high → katie.hirst@specialistglass.co.uk | 20985 |
| 21031-1575 | Interfluid | **119d** | none | — |
| 21061 | (unnamed) | **103d** | none | 21061 |
| 21086 | (unnamed) | **90d** | none | 21086 |
| 21135 | (unnamed) | **68d** | medium → alisonl@sesameaccess.com ⚠️ **internal** | 21135 |
| 21139-60726 | amazon | **65d** | none | 21139 |
| 21223 | (unnamed) | **34d** | none | 21223 |
| 21226-080826 | OpenAI | **32d** | none | 21226 |

### Tier 2 — 14–29 days overdue
| PO | Supplier | Overdue | Thread |
|---|---|---|---|
| 21243-1578 | Anodising | 23d | high → quality@mercuryelectroplating.com |
| 21248-1595 | Steerforth | 22d | none |
| 21247-1037 | Steerforth | 22d | none |
| 21256-1365 | Parcel Hero | 20d | none |
| 21559-1434 | (unnamed) | 16d | none |
| 21569-250826 | CPS | 15d | high → cpssteve69@gmail.com |
| (PO None) | Romans | 15d | none |
| 21574-1591 | Weber Cox | 14d | none |
| 21576-1037 | Machining | 14d | none |
| 21578-1595 | Rubber Supplier | 14d | none |
| 21580-1037 | Bellows Supplier | 13d | none |
| 21584-1588 | JAJO | 12d | none |
| 21582-1595 | Bellows Supplier | 12d | none |
| 21583-1595 | JAJO | 12d | none |
| 21591-1037 | Engraving | 8d | none |
| 21588-1591 | Engraving | 8d | none ⚠️ tagged -1591 but is Cambridge work |

### Tier 3 — ≤6 days overdue (newest — confirm a chase is warranted)
| PO | Supplier | Overdue | Thread |
|---|---|---|---|
| 21598-1037 | Powder Coating | 6d | none |
| 21601-042926 | Peter Cooper | 5d | high → peterdavidcooper@hotmail.com |
| 21600-1420 | Royal mail | 5d | none |
| 21603-70926 | RELATED FLUID | 2d | high → auto-generated address ⚠️ verify |
| 21608-1597 | Gapp Automation | 2d | high → mweight@gapp.co.uk (1597 Forrest Rd!) |
| 21606-70926 | Drill services | 2d | none |
| 21612-1597 | Bellows Supplier | 1d | none (1597) |
| 21611-1597 | Tape Switch | 1d | none (1597) |
| 21610-1171 | Wessex Galvanising | 1d | high → wessex@wggltd.co.uk |

## Clean HIGH-match draft candidates (real recipient + thread) — 8
1. 20985-1575 SG transmission → katie.hirst@specialistglass.co.uk
2. 21243-1578 Anodising → quality@mercuryelectroplating.com
3. 21569-250826 CPS → cpssteve69@gmail.com
4. 21601-042926 Peter Cooper → peterdavidcooper@hotmail.com
5. 21608-1597 Gapp Automation → mweight@gapp.co.uk
6. 21610-1171 Wessex Galvanising → wessex@wggltd.co.uk
(Excluded: #6 21135 → alisonl@sesameaccess.com = INTERNAL, not a supplier; #30 21603-70926 RELATED FLUID → auto-generated Exchange address, verify before use.)

## Special attention flags
- **21135** (68d): the only thread found is `alisonl@sesameaccess.com` — an internal address, likely a misfiled/sent-to-self PO, not a supplier chase.
- **21603-70926 RELATED FLUID** (2d): only recipient is an auto-generated MS Exchange address — must resolve the real supplier contact before any draft.
- **21588-1591 Engraving**: PO suffix says `-1591` but it's Cambridge (project 591) work — verify before a per-project chase.
- **21608-1597 Gapp Automation + Tier-3 1597 orders** (Bellows, Tape Switch): these are the Forrest Road (1597) POs just created from today's BOM commits — chasing at 1-2 days overdue may be premature; likely supplier already engaged on the Long Lead items.
- **No-PO entries** (Romans, #25): cannot anchor a chase by PO reference — manual handling.

## Next step (awaiting your pick)
State which set to draft into **purchasing@sesameaccess.com** Drafts:
`draft all → 2 tier-1 high + tier-2 high → 7 clean-high only → specific PO list`. Drafts sit in
Outlook for your manual send; nothing auto-sends.
