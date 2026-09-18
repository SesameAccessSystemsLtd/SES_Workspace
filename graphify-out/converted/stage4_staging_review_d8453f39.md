<!-- converted from stage4_staging_review.xlsx -->

## Sheet: BOM Review
| Override / Reorder QTY | Low Stock Alert | BOM QTY | BOM Description | vendor | ORDER_PICK | Remaining after BOM | Mapped KAN | Asana Task Name | Part Number | Matched Stock_Number | Stock QTY | min Stock QTY | Asana Task URL | Order reference | Order Status | cost center | material | thickness | stock number | web link | notes | mass | volume | source bom file | rev | authority | item | location | Asana Stock Name | Asana URL (Golden) | Match Confidence | Match Type | Index Match Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
|  |  | 1 | Gate stopper flange | Laser Cut | ORDER |  |  |  | 1365.1014.R.002 |  |  |  |  |  | To Order | Laser Cut | Steel, Mild | 20.000 mm |  |  |  | 0.368 kg | 46931.614 mm^3 | 1365.BOM.2.Entire System.Laser Cut.xlsx |  |  | 1 |  |  |  | 0 |  | UNMATCHED |
|  |  | 1 | Gate stopper clamp | Laser Cut | ORDER |  |  |  | 1365.1014.R.003 |  |  |  |  |  | To Order | Laser Cut | Steel, Mild | 20.000 mm |  |  |  | 0.149 kg | 18919.691 mm^3 | 1365.BOM.2.Entire System.Laser Cut.xlsx |  |  | 2 |  |  |  | 0 |  | UNMATCHED |
|  |  | 2 | Packer | Laser Cut | ORDER |  |  |  | 1365.1014.R.004 |  |  |  |  |  | To Order | Laser Cut | Stainless Steel | 1.000 mm | 304/2B |  |  | 0.003 kg | 332.949 mm^3 | 1365.BOM.2.Entire System.Laser Cut.xlsx |  |  | 3 |  |  |  | 0 |  | UNMATCHED |
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