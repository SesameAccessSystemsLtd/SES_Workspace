<!-- converted from stage4_staging_review.xlsx -->

## Sheet: BOM Review
| Override / Reorder QTY | Low Stock Alert | BOM QTY | BOM Description | vendor | ORDER_PICK | Remaining after BOM | Mapped KAN | Asana Task Name | Part Number | Matched Stock_Number | Stock QTY | min Stock QTY | Asana Task URL | Order reference | Order Status | cost center | material | thickness | stock number | web link | notes | mass | volume | source bom file | rev | authority | item | location | Asana Stock Name | Asana URL (Golden) | Match Confidence | Match Type | Index Match Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
|  |  | 1 | Machine Room Cabinet | JAJO | ORDER |  |  |  | 1008.D1.MachineRoomCabinet |  |  |  |  |  | To Order | Long Leading |  |  |  |  |  | 66.213 kg | 32872715.717 mm^3 | 1595.BOM.1.LongLeading.xlsx |  |  | 1 |  |  |  | 0 |  | UNMATCHED |
|  |  | 1 | TBC by Karol - Spec as per PLC BOM | Hydraulic Supplier | ORDER |  |  |  | 1595.1008.D1.MotorPumpUnit |  |  |  |  |  | To Order | Long Leading | Generic |  |  |  |  | 14.000 kg | 14000247.356 mm^3 | 1595.BOM.1.LongLeading.xlsx |  |  | 2 |  |  |  | 0 |  | UNMATCHED |
|  |  | 1 | Valve Block (2 function) | Related Fluid | ORDER |  |  |  | 1595.1008.H6(RF SES447424) |  |  |  |  |  | To Order | Long Leading | Generic |  | RF SES447424 |  |  | 1.927 kg | 1927464.015 mm^3 | 1595.BOM.1.LongLeading.xlsx |  |  | 3 |  |  |  | 0 |  | UNMATCHED |
|  |  | 2 | Gate Control Unit | Related Fluid | ORDER |  |  |  | 1595.1008.R.RF SAS4650 |  |  |  |  |  | To Order | Long Leading |  |  | RF SAS4650 |  |  | 1.195 kg | 1194526.930 mm^3 | 1595.BOM.1.LongLeading.xlsx |  |  | 4 | Hydraulics Rack 3  Shelf C |  |  | 0 |  | UNMATCHED |
|  |  | 1 | Double Scissor Lift | Sesame | ORDER |  |  |  | 1595.1011.B.ScissorLift Double |  |  |  |  |  | To Order | Long Leading |  |  |  |  |  | 445.119 kg | 62349447.572 mm^3 | 1595.BOM.1.LongLeading.xlsx |  |  | 5 |  |  |  | 0 |  | UNMATCHED |
|  |  | 1 | Ram: MD 25/18 D 130 SSF   Stroke: 130 mm | Steerforth | ORDER |  |  |  | 1595.1014.N13.Ram |  |  |  |  |  | To Order | Long Leading |  |  | MD 25/18 D 130 SSF | steerforth.co.uk |  | 0.616 lbmass | 279597.257 mm^3 | 1595.BOM.1.LongLeading.xlsx |  |  | 6 |  |  |  | 0 |  | UNMATCHED |
|  |  | 2 | Rod end bearing (Dunlop part number: MP-M12C) | Sesame | ORDER |  |  |  | 1595.1014.P.041 |  |  |  |  |  | To Order | Long Leading | Generic |  | POS12C | https://bepltd.com/products/pos12c-nylon-liner-male-rod-end-bearing-m12x1-25 |  | 0.011 kg | 11441.414 mm^3 | 1595.BOM.1.LongLeading.xlsx |  |  | 7 |  |  |  | 0 |  | UNMATCHED |
|  |  | 1 | Ram: MD 25/18 D 130 SSF   Stroke: 130 mm | Steerforth | ORDER |  |  |  | 1595.1014.P7.Ram |  |  |  |  |  | To Order | Long Leading |  |  | MD 25/18 D 130 SSF | steerforth.co.uk |  | 0.280 kg | 279597.257 mm^3 | 1595.BOM.1.LongLeading.xlsx |  |  | 8 |  |  |  | 0 |  | UNMATCHED |
|  |  | 4 | Magnetic reed switch | Confortinet | ORDER |  |  |  | 1595.1014.P14 |  |  |  |  |  | To Order | Long Leading |  |  | SRSTA | steerforth.co.uk |  | 0.011 lbmass | 4775.924 mm^3 | 1595.BOM.1.LongLeading.xlsx |  |  | 9 |  |  |  | 0 |  | UNMATCHED |
|  |  | 1 | Hydraulic Accumulator - LT - 92mm wide (60 PSI pr-charge) | Hydraulic Supplier | ORDER |  |  |  | 1595.H500 |  |  |  |  |  | To Order | Long Leading | Generic |  | H500 | ISIS FLUID CONTROL LTD |  | 0.751 kg | 750563.595 mm^3 | 1595.BOM.1.LongLeading.xlsx |  |  | 10 |  |  |  | 0 |  | UNMATCHED |
## Sheet: Summary
| ORDER_PICK | Count |  |
| --- | --- | --- |
| ORDER | 10 |  |
| Total rows | 10 |  |
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