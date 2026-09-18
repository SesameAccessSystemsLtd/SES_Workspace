<!-- converted from stage4_staging_review.xlsx -->

## Sheet: BOM Review
| Override / Reorder QTY | Low Stock Alert | BOM QTY | BOM Description | vendor | ORDER_PICK | Remaining after BOM | Mapped KAN | Asana Task Name | Part Number | Matched Stock_Number | Stock QTY | min Stock QTY | Asana Task URL | Order reference | Order Status | cost center | material | thickness | stock number | web link | notes | mass | volume | source bom file | rev | authority | item | location | qty | info / link | length | zip | d | b | t | a | Asana Stock Name | Asana URL (Golden) | Match Confidence | Match Type | Index Match Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
|  | NO | 1 | Gate stopper flange | Laser Cut | PICK | 7.0 | KAN-330 | 10 off NBSS002 (Emergency Stop Angle Bracket) Rev_2 | 1365.1014.R.002 | 002 | 8.0 | 5.0 | Open in Asana |  | PICK | Laser Cut | Steel, Mild | 20.000 mm |  |  |  | 0.368 kg | 46931.614 mm^3 | 1365.BOM.2.Entire System.Laser Cut.xlsx |  |  | 1 |  |  |  |  |  |  |  |  |  | 10 off NBSS002 (Emergency Stop Angle Bracket) Rev_2 | https://app.asana.com/1/357939803556123/929000000000000.0 | 90 | Substring | MATCHED |
|  |  | 1 | Gate stopper clamp | Laser Cut | ORDER |  |  |  | 1365.1014.R.003 |  |  |  |  |  | To Order | Laser Cut | Steel, Mild | 20.000 mm |  |  |  | 0.149 kg | 18919.691 mm^3 | 1365.BOM.2.Entire System.Laser Cut.xlsx |  |  | 2 |  |  |  |  |  |  |  |  |  |  |  | 0 |  | UNMATCHED |
|  |  | 2 | Packer | Laser Cut | ORDER |  |  |  | 1365.1014.R.004 |  |  |  |  |  | To Order | Laser Cut | Stainless Steel | 1.000 mm | 304/2B |  |  | 0.003 kg | 332.949 mm^3 | 1365.BOM.2.Entire System.Laser Cut.xlsx |  |  | 3 |  |  |  |  |  |  |  |  |  |  |  | 0 |  | UNMATCHED |
|  |  | 2 | Rubber feet bracket | Machining | ORDER |  |  |  | 1365.1014.R.008 |  |  |  |  |  | To Order | Machining |  |  |  |  |  | 0.054 kg | 6910.310 mm^3 | 1365.BOM.2.Entire System.Machining.xlsx |  |  | 1 |  |  |  |  |  |  |  |  |  |  |  | 0 |  | UNMATCHED |
|  |  | 1 | Machined gate stopper body | Machining | ORDER |  |  |  | 1365.1014.R1 |  |  |  |  |  | To Order | Machining |  |  |  |  |  | 0.626 kg | 79691.197 mm^3 | 1365.BOM.2.Entire System.Machining.xlsx |  |  | 2 |  |  |  |  |  |  |  |  |  |  |  | 0 |  | UNMATCHED |
|  |  | 2 | Levelling foot flat non tilt Steel Zinc plated | Sesame | ORDER |  |  |  | 1365.1014.R.001 |  |  |  |  |  | To Order | Sesame | Generic |  | 673-702 | https://www.wdscomponents.com/en-gb/levelling-foot-flat-non-tilt-steel-zinc-plated |  | 0.008 kg | 8243.616 mm^3 | 1365.BOM.2.Entire System.Sesame.xlsx |  |  | 1 |  |  |  |  |  |  |  |  |  |  |  | 0 |  | UNMATCHED |
|  |  | 2 | Dowel Pin 6x24 | RS Components | ORDER |  |  |  | 1365.1014.R.007 |  |  |  |  |  | To Order | Sesame | Steel |  | 270-647 | https://uk.rs-online.com/web/p/dowel-pins/0270647?cm_mmc=UK-PLA-DS3A-_-google-_-CSS_UK_EN_PMAX_RS+PRO-_--_-270647&matchtype=&&gclsrc=aw.ds&gad_source=1&gad_campaignid=20690300296&gclid=EAIaIQobChMIgPzk-f73lQMVp4BQBh2vXReAEAQYAyABEgLvMfD_BwE |  | 0.005 kg | 666.018 mm^3 | 1365.BOM.2.Entire System.Sesame.xlsx |  |  | 2 |  |  |  |  |  |  |  |  |  |  |  | 0 |  | UNMATCHED |
## Sheet: Summary
| ORDER_PICK | Count |  |
| --- | --- | --- |
| ORDER | 6 |  |
| PICK | 1 |  |
| Total rows | 7 |  |
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