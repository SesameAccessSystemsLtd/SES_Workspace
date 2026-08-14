<!-- converted from stage4_staging_review.xlsx -->

## Sheet: BOM Review
| Override / Reorder QTY | Low Stock Alert | BOM QTY | BOM Description | vendor | ORDER_PICK | Remaining after BOM | Mapped KAN | Asana Task Name | Part Number | Matched Stock_Number | Stock QTY | min Stock QTY | Asana Task URL | Order reference | Order Status | cost center | material | thickness | stock number | web link | notes | mass | volume | source bom file | rev | authority | item | location | thumbnail | qty | info / link | length | zip | d | b | t | a | Asana Stock Name | Asana URL (Golden) | Match Confidence | Match Type | Index Match Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
|  |  | 1 | Cover plate | Laser Cut | ORDER |  |  |  | 1366.1015.E.221 |  |  |  |  |  | To Order | Laser Cut | Aluminum 5152 | 1.200 mm | Alu 5152 |  |  | 1.456 kg | 539383.763 mm^3 | 1366.BOM.9.FrontSupportFrame.LaserCut.xlsx |  |  | 1 |  |  |  |  |  |  |  |  |  |  |  |  | 0 |  | UNMATCHED |
|  |  | 2 | Cover plate | Laser Cut | ORDER |  |  |  | 1366.1015.E.222 |  |  |  |  |  | To Order | Laser Cut | Aluminum 5152 | 1.200 mm | Alu 5152 |  |  | 1.958 kg | 725148.850 mm^3 | 1366.BOM.9.FrontSupportFrame.LaserCut.xlsx |  |  | 2 |  |  |  |  |  |  |  |  |  |  |  |  | 0 |  | UNMATCHED |
|  |  | 1 | Cover plate | Laser Cut | ORDER |  |  |  | 1366.1015.E.223 |  |  |  |  |  | To Order | Laser Cut | Aluminum 5152 | 1.200 mm | Alu 5152 |  |  | 1.635 kg | 605620.567 mm^3 | 1366.BOM.9.FrontSupportFrame.LaserCut.xlsx |  |  | 3 |  |  |  |  |  |  |  |  |  |  |  |  | 0 |  | UNMATCHED |
|  |  | 1 | Cover plate | Laser Cut | ORDER |  |  |  | 1366.1015.E.225 |  |  |  |  |  | To Order | Laser Cut | Aluminum 5152 | 1.200 mm | Alu 5152 |  |  | 2.643 kg | 978948.643 mm^3 | 1366.BOM.9.FrontSupportFrame.LaserCut.xlsx |  |  | 4 |  |  |  |  |  |  |  |  |  |  |  |  | 0 |  | UNMATCHED |
|  |  | 1 | Cover plate | Marine Lacquer | ORDER |  |  |  | 1366.1015.E.221 |  |  |  |  |  | To Order | Finishes BOM | Aluminum 5152 | 1.200 mm | Alu 5152 |  |  | 1.456 kg | 539383.763 mm^3 | 1366.BOM.9.FrontSupportFrame.MarineLacquer.xlsx |  | Marine Lacquer | 1 |  |  |  |  |  |  |  |  |  |  |  |  | 0 |  | UNMATCHED |
|  |  | 2 | Cover plate | Marine Lacquer | ORDER |  |  |  | 1366.1015.E.222 |  |  |  |  |  | To Order | Finishes BOM | Aluminum 5152 | 1.200 mm | Alu 5152 |  |  | 1.958 kg | 725148.850 mm^3 | 1366.BOM.9.FrontSupportFrame.MarineLacquer.xlsx |  | Marine Lacquer | 2 |  |  |  |  |  |  |  |  |  |  |  |  | 0 |  | UNMATCHED |
|  |  | 1 | Cover plate | Marine Lacquer | ORDER |  |  |  | 1366.1015.E.223 |  |  |  |  |  | To Order | Finishes BOM | Aluminum 5152 | 1.200 mm | Alu 5152 |  |  | 1.635 kg | 605620.567 mm^3 | 1366.BOM.9.FrontSupportFrame.MarineLacquer.xlsx |  | Marine Lacquer | 3 |  |  |  |  |  |  |  |  |  |  |  |  | 0 |  | UNMATCHED |
|  |  | 1 | Cover plate | Marine Lacquer | ORDER |  |  |  | 1366.1015.E.225 |  |  |  |  |  | To Order | Finishes BOM | Aluminum 5152 | 1.200 mm | Alu 5152 |  |  | 2.643 kg | 978948.643 mm^3 | 1366.BOM.9.FrontSupportFrame.MarineLacquer.xlsx |  | Marine Lacquer | 4 |  |  |  |  |  |  |  |  |  |  |  |  | 0 |  | UNMATCHED |
## Sheet: Summary
| ORDER_PICK | Count |  |
| --- | --- | --- |
| ORDER | 8 |  |
| Total rows | 8 |  |
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