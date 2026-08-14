<!-- converted from stage4_staging_review.xlsx -->

## Sheet: BOM Review
| Override / Reorder QTY | Low Stock Alert | BOM QTY | BOM Description | vendor | ORDER_PICK | Remaining after BOM | Mapped KAN | Asana Task Name | Part Number | Matched Stock_Number | Stock QTY | min Stock QTY | Asana Task URL | Order reference | Order Status | cost center | material | thickness | stock number | web link | notes | mass | volume | source bom file | rev | authority | item | location | Asana Stock Name | Asana URL (Golden) | Match Confidence | Match Type | Index Match Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| FORCE_ORDER | NO | 1 | Pit Trim Side Panel | Anodising | PICK | 7.0 | KAN-330 | 10 off NBSS002 (Emergency Stop Angle Bracket) Rev_2 | 1572.1002.G.013.01 | 002 | 8.0 | 5.0 | Open in Asana |  | PICK | Finishes BOM | Aluminum 5152 | 3.000 mm |  |  |  | 3.634 kg | 1346075.590 mm^3 | 1572.BOM.4.Pit Trim Panels Update.Finishes.xlsx | 1 | Anodising | 1 |  | 10 off NBSS002 (Emergency Stop Angle Bracket) Rev_2 | https://app.asana.com/1/357939803556123/929000000000000.0 | 90 | Substring | MATCHED |
| FORCE_ORDER | NO | 1 | Pit Trim Side Panel | Anodising | PICK | 7.0 | KAN-330 | 10 off NBSS002 (Emergency Stop Angle Bracket) Rev_2 | 1572.1002.G.017.01 | 002 | 8.0 | 5.0 | Open in Asana |  | PICK | Finishes BOM | Aluminum 5152 | 3.000 mm |  |  |  | 3.714 kg | 1375655.303 mm^3 | 1572.BOM.4.Pit Trim Panels Update.Finishes.xlsx | 1 | Anodising | 2 |  | 10 off NBSS002 (Emergency Stop Angle Bracket) Rev_2 | https://app.asana.com/1/357939803556123/929000000000000.0 | 90 | Substring | MATCHED |
| FORCE_ORDER | NO | 1 | Pit Trim Side Panel | Laser Cut | PICK | 7.0 | KAN-330 | 10 off NBSS002 (Emergency Stop Angle Bracket) Rev_2 | 1572.1002.G.013.01 | 002 | 8.0 | 5.0 | Open in Asana |  | PICK | Laser Cut | Aluminum 5152 | 3.000 mm |  |  |  | 3.634 kg | 1346075.590 mm^3 | 1572.BOM.4.Pit Trim Panels Update.Laser Cut.xlsx | 1 | Anodising | 1 |  | 10 off NBSS002 (Emergency Stop Angle Bracket) Rev_2 | https://app.asana.com/1/357939803556123/929000000000000.0 | 90 | Substring | MATCHED |
| FORCE_ORDER | NO | 1 | Pit Trim Side Panel | Laser Cut | PICK | 7.0 | KAN-330 | 10 off NBSS002 (Emergency Stop Angle Bracket) Rev_2 | 1572.1002.G.017.01 | 002 | 8.0 | 5.0 | Open in Asana |  | PICK | Laser Cut | Aluminum 5152 | 3.000 mm |  |  |  | 3.714 kg | 1375655.303 mm^3 | 1572.BOM.4.Pit Trim Panels Update.Laser Cut.xlsx | 1 | Anodising | 2 |  | 10 off NBSS002 (Emergency Stop Angle Bracket) Rev_2 | https://app.asana.com/1/357939803556123/929000000000000.0 | 90 | Substring | MATCHED |
## Sheet: Summary
| ORDER_PICK | Count |  |
| --- | --- | --- |
| PICK | 4 |  |
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