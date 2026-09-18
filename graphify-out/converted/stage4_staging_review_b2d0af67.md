<!-- converted from stage4_staging_review.xlsx -->

## Sheet: BOM Review
| Override / Reorder QTY | Low Stock Alert | BOM QTY | BOM Description | vendor | ORDER_PICK | Remaining after BOM | Mapped KAN | Asana Task Name | Part Number | Matched Stock_Number | Stock QTY | min Stock QTY | Asana Task URL | Order reference | Order Status | cost center | material | thickness | stock number | web link | notes | mass | volume | source bom file | rev | authority | item | location | Asana Stock Name | Asana URL (Golden) | Match Confidence | Match Type | Index Match Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
|  |  | 1 | Machine Room Cabinet | JAJO | ORDER |  |  |  | 1597.1008.D1.MachineRoomCabinet |  |  |  |  |  | To Order | Long Leading |  |  |  |  |  | 65.047 kg | 32600029.037 mm^3 | 1597.BOM.1.LongLeading.xlsx |  |  | 1 |  |  |  | 0 |  | UNMATCHED |
|  |  | 1 | TBC by Karol - Spec as per PLC BOM | Hydraulic Supplier | ORDER |  |  |  | 1597.1008.D1.MotorPumpUnit |  |  |  |  |  | To Order | Long Leading | Generic |  |  |  |  | 14.000 kg | 14000247.356 mm^3 | 1597.BOM.1.LongLeading.xlsx |  |  | 2 |  |  |  | 0 |  | UNMATCHED |
|  |  | 1 | Valve Block (2 function) | Related Fluid | ORDER |  |  |  | 1597.1008.H6(RF SES447424) |  |  |  |  |  | To Order | Long Leading | Generic |  | RF SES447424 |  |  | 1.927 kg | 1927464.015 mm^3 | 1597.BOM.1.LongLeading.xlsx |  |  | 3 |  |  |  | 0 |  | UNMATCHED |
|  |  | 1 | CC:450.5 mm/ Stroke: 267.5 mm, Single act. Ram | ABCO | ORDER |  |  |  | 1597.1010.I.Actuator |  |  |  |  |  | To Order | Long Leading |  |  |  | www.abcohydraulics.co.uk |  | 2.653 kg | 2652936.236 mm^3 | 1597.BOM.1.LongLeading.xlsx |  |  | 4 | Hydraulics Rack 2  Shelf C |  |  | 0 |  | UNMATCHED |
|  |  | 1 | CC:450.5 mm/ Stroke: 267.5 mm, Single act. Ram | ABCO | ORDER |  |  |  | 1597.1010.I.Actuator_MIR |  |  |  |  |  | To Order | Long Leading |  |  |  | www.abcohydraulics.co.uk |  | 2.653 kg | 2652936.236 mm^3 | 1597.BOM.1.LongLeading.xlsx |  |  | 5 | Hydraulics Rack 2  Shelf C |  |  | 0 |  | UNMATCHED |
|  |  | 1 | Traversing Platform Ram | Hydraulics Supplier | ORDER |  |  |  | 1597.7110.C.TraversingActuator |  |  |  |  |  | To Order | Long Leading |  |  |  | interfluid.co.uk |  | 3.195 kg | 3194926.091 mm^3 | 1597.BOM.1.LongLeading.xlsx |  |  | 6 |  |  |  | 0 |  | UNMATCHED |
|  |  | 1 | Traversing Platform Ram | Hydraulics Supplier | ORDER |  |  |  | 1597.7110.C.TraversingActuator_MIR |  |  |  |  |  | To Order | Long Leading |  |  |  | interfluid.co.uk |  | 3.195 kg | 3194926.091 mm^3 | 1597.BOM.1.LongLeading.xlsx |  |  | 7 |  |  |  | 0 |  | UNMATCHED |
## Sheet: Summary
| ORDER_PICK | Count |  |
| --- | --- | --- |
| ORDER | 7 |  |
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