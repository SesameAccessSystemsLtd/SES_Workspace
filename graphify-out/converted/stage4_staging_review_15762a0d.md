<!-- converted from stage4_staging_review.xlsx -->

## Sheet: BOM Review
| Override / Reorder QTY | Low Stock Alert | BOM QTY | BOM Description | vendor | ORDER_PICK | Remaining after BOM | Mapped KAN | Asana Task Name | Part Number | Matched Stock_Number | Stock QTY | min Stock QTY | Asana Task URL | Order reference | Order Status | cost center | material | thickness | stock number | web link | notes | mass | volume | source bom file | rev | authority | item | location | Asana Stock Name | Asana URL (Golden) | Match Confidence | Match Type | Index Match Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
|  |  | 4 | Bush | Machining | ORDER |  |  |  | 7003.E.085 |  |  |  |  |  | To Order | Machining | Stainless Steel |  |  |  |  | 0.013 kg | 1590.431 mm^3 | 7003.TraversingLowProfile.BOM.2.SlidersModifications.Machining.xlsx |  |  | 1 |  |  |  | 0 |  | UNMATCHED |
|  |  | 4 | Thrust Washer | IGUS | ORDER |  |  |  | 7003.E.082 |  |  |  |  |  | To Order | Sesame | Generic |  | GTM-1018-015 | https://www.igus.co.uk/iglidur-ibh/thrust-washers/product-details/iglidur-g-m?artnr=GTM-1018-015 |  | 0.000 kg | 263.894 mm^3 | 7003.TraversingLowProfile.BOM.2.SlidersModifications.Sesame.xlsx |  |  | 1 |  |  |  | 0 |  | UNMATCHED |
|  |  | 2 | Plain Bearing | IGUS | ORDER |  |  |  | 7003.E.083 |  |  |  |  |  | To Order | Sesame | ABS Plastic |  | GSM-1012-10 | https://www.igus.co.uk/iglidur-ibh/sleeve-bearings/product-details/iglidur-g-m?artnr=GSM-1012-10 |  | 0.000 kg | 345.575 mm^3 | 7003.TraversingLowProfile.BOM.2.SlidersModifications.Sesame.xlsx |  |  | 2 |  |  |  | 0 |  | UNMATCHED |
|  |  | 4 | 7379-10-M8-20-NI | berger-tools | ORDER |  |  |  | 7003.E.086 |  |  |  |  |  | To Order | Sesame | Generic |  | 7379-10-M8-20-NI | https://www.berger-tools.co.uk/Iso7379_Hexagon_Socket_Head_Shoulder_Screw_Ssb_Stainless/ |  | 0.008 lbmass | 3439.062 mm^3 | 7003.TraversingLowProfile.BOM.2.SlidersModifications.Sesame.xlsx |  |  | 3 |  |  |  | 0 |  | UNMATCHED |
## Sheet: Summary
| ORDER_PICK | Count |  |
| --- | --- | --- |
| ORDER | 4 |  |
| Total rows | 4 |  |
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