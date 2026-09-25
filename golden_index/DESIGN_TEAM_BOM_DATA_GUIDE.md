# BOM export — how to fill the `stock number` column (design team)

## Why this matters
When a BOM is imported, the system reads the **`stock number`** column of each
line and looks it up against our stock catalogue (the Golden Index) to decide:

- **PICK** — we already hold this in stock, take it from the shelf, or
- **ORDER** — we don't stock it, raise a purchase order.

Right now the `stock number` column is **left blank for in-house / Sesame-made
parts**, so the system can't see that we stock them and **orders parts we already
have on the shelf**. The identity is only in the description, which the importer
doesn't treat as authoritative.

**The fix on your side: populate `stock number` consistently for every line that
has a stock identity.** One reliable value per cell.

## The rules

1. **Every stockable line gets its stock reference in the `stock number`
   column** — not only in the description.
   - **In-house Sesame stock parts:** put the **NBSS code** — the same code you
     already type into the description.
   - **Bought-in parts:** put the **supplier / manufacturer order code** exactly
     as you would search the supplier by.

2. **One value per cell.** No lists, no "or", no `/ 511155`, no notes like
   "PUT ME ON BOM!", no units, no description text mixed in.

3. **Canonical, consistent format every time:**
   - **NBSS:** `NBSS.` + **3-digit, zero-padded** number → `NBSS.008`,
     `NBSS.126`, `NBSS.414`.  Not `NBSS8`, `NBSS 8`, `008`, or `1573.008`.
   - **RS parts:** the bare RS stock number → `724-2677`.  Not
     `RS Stock No 724-2677`.
   - **Manufacturer parts:** the exact orderable part code, nothing appended.

4. **Keep the `part number` column as the project Sesame PN** (`1573.126`) — that
   stays exactly as it is now. `stock number` is a **separate** column.

5. **Leave `stock number` blank only for genuinely bespoke fabricated parts** that
   are never held in stock (unique laser-cut plates, one-off weldments). Those
   correctly route to ORDER — that is the right behaviour.

6. **Don't rely on the interim safety net.** For now, when `cost centre = NBSS`
   the importer derives the stock code from the `part number` (strips the
   `1573.` project prefix and prepends `NBSS.`), and as a secondary it can read
   an `NBSS.xxx` code out of the description. This only covers **NBSS** parts.
   Bought-in supplier codes, and any NBSS line whose cost centre isn't `NBSS`,
   are still missed unless `stock number` is filled. Treat the net as temporary.

7. **Keep `cost centre` correct.** The interim rule keys off `cost centre = NBSS`
   for in-house Sesame parts — please keep that column accurate and consistent.

## Good vs. current

| Part (description) | `part number` (keep) | `stock number` — **fill like this** | current (**wrong**) |
|---|---|---|---|
| NBSS.126 Sprocket Pin Motor | `1573.126` | `NBSS.126` | *(blank)* |
| NBSS.008 Stair Wheel | `1573.008` | `NBSS.008` | *(blank)* / `NBSS8` |
| RS 4-way M8 connector | `1573.1008.x` | `724-2677` | `RS Stock No 724-2677` / *(blank)* |
| Rising-barrier jam spring | `1573.776` | `FC-A180-380-512M` | *(blank)* |
| Bespoke laser-cut plate | `1573.1003.A.060` | *(blank — correct)* | — |

## Format cheat-sheet
- **NBSS:** `NBSS.` + dot + 3 digits (`NBSS.###`).
- **Supplier code:** exactly as orderable, **one per cell**, no prefixes
  ("RS Stock No"), no trailing notes, no lists.
- **Blank** only if truly not a stock item.

## The payoff
Filling this column correctly means the import PICKs everything we hold, ORDERs
only what we don't, links each line to the right stock card automatically, and
removes the manual rework of catching wrongly-ordered stock parts after the fact.
