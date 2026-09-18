<!-- converted from stage4_staging_review.xlsx -->

## Sheet: BOM Review
| Override / Reorder QTY | Low Stock Alert | BOM QTY | BOM Description | vendor | ORDER_PICK | Remaining after BOM | Mapped KAN | Asana Task Name | Part Number | Matched Stock_Number | Stock QTY | min Stock QTY | Asana Task URL | Order reference | Order Status | cost center | material | thickness | stock number | web link | notes | mass | volume | source bom file | rev | authority | item | location | Asana Stock Name | Asana URL (Golden) | Match Confidence | Match Type | Index Match Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
|  |  | 1 | Pit Trim Side Panel | Laser Cut | ORDER |  |  |  | 1582.1002.G.013.01 |  |  |  |  |  | To Order | Laser Cut | Stainless Steel | 3.000 mm | 304/DP1 |  |  | 2.645 kg | 330570.625 mm^3 | 1582.BOM.5.SS Pit Trim Panels.Laser Cut.xlsx |  |  | 1 |  |  |  | 0 |  | UNMATCHED |
|  |  | 1 | Pit Trim Side Panel | Laser Cut | ORDER |  |  |  | 1582.1002.G.017.01 |  |  |  |  |  | To Order | Laser Cut | Stainless Steel | 3.000 mm | 304/DP1 |  |  | 2.881 kg | 360150.338 mm^3 | 1582.BOM.5.SS Pit Trim Panels.Laser Cut.xlsx |  |  | 2 |  |  |  | 0 |  | UNMATCHED |
|  |  | 1 | Pit Trim Back Panel | Laser Cut | ORDER |  |  |  | 1582.1002.G.028.01 |  |  |  |  |  | To Order | Laser Cut | Stainless Steel | 3.000 mm | 304/DP1 |  |  | 6.790 kg | 848736.507 mm^3 | 1582.BOM.5.SS Pit Trim Panels.Laser Cut.xlsx |  |  | 3 |  |  |  | 0 |  | UNMATCHED |
## Sheet: Summary
| ORDER_PICK | Count |  |
| --- | --- | --- |
| ORDER | 3 |  |
| Total rows | 3 |  |
| HOW TO OVERRIDE  —  edit Column A on the BOM Review sheet |  |  |
| Override Value | Effect | When to use |
| FORCE_ORDER | Force to ORDER | Part matched Golden Index as PICK but is bespoke / must be ordered. Use for any Substring match where the stock name looks unrelated to this part. |
| SKIP | Exclude entirely | Part should not appear in Asana at all (duplicate, error row, or not needed for this job). |
| HOLD | Exclude (same as SKIP) | Part deferred — not required yet. Treated identically to SKIP in Stage 5. |
| {number}  e.g. 12 | Override order quantity | Force a specific reorder qty instead of BOM qty. Leave ORDER_PICK column unchanged. |
| (leave blank) | No change | Pipeline uses the ORDER_PICK result as-is. Default for most rows. |
| MATCH TYPE WARNING — verify Column K (Matched Stock_Number) on BOM Review |  |  |
| Match Type | Risk | Action |
| Substring | HIGH — false positive likely | Does Column I (Asana Task Name) make sense for this part? If unrelated → enter FORCE_ORDER in Column A immediately. |
| Exact / Token | Low | Part number matched precisely. Spot-check but usually safe to leave. |
| No match → ORDER | None (expected) | Bespoke part with no Golden Index entry. Routed to ORDER automatically. No action needed unless you want to add it to the index for future runs. |