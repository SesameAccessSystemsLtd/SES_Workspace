# Pack → order join demo — 1037.1016.BS.2 (job 1037)

Pack ref: `1037.1016.bs.2` · 8 part(s) in parts_control.
Method: order task → subtasks (Sesame Part No) → parts_control part→pack.

## Orders for job 1037 (10) — which genuinely impact this pack

| Order | Vendor | Parts in this pack | Impacts pack? |
|---|---|---|---|
| 21598 · IDL Powder Coating - 1037 - Langley Stre | IDL Powder Coating - 1037 - Langley Stre |  | no |
| 21594 · Foam seal - 1037 - Langley Entire BOM | Foam seal - 1037 - Langley Entire BOM |  | no |
| 21591 · Alexander Pollock Engraving - 1037 - 5.B | Alexander Pollock Engraving - 1037 - 5.B | 1037.1016.1.2 | **YES** (1 part(s)) |
| 21590 · Direct plastics - machining langley | Direct plastics - machining langley |  | no |
| 21587 · Lasershape - large part for langley | Lasershape - large part for langley |  | no |
| 21580 · Machguard - Bellows Supplier - 1037 - La | Machguard - Bellows Supplier - 1037 - La |  | no |
| 21576 · Thameside - Machining - 1037 - Langley E | Thameside - Machining - 1037 - Langley E |  | no |
| 21567 · Exact Laser Cut BOM - 1037 - Langley Ent | Exact Laser Cut BOM - 1037 - Langley Ent |  | no |
| 21247 · Steerforth - 1037 - 1.Long Leading Time | Steerforth - 1037 - 1.Long Leading Time |  | no |
| 21249 · Exact Laser Cut BOM - 1037 - 5.Button St (completed) | Exact Laser Cut BOM - 1037 - 5.Button St | 1037.1016.2.002 | **YES** (1 part(s)) |

## Conclusion

Of **10** job-level orders, the part-level join attributes **2** as genuinely impacting `1037.1016.bs.2`:
- [21591-1037 - Alexander Pollock Engraving - 1037 - 5.Button Stations](https://app.asana.com/0/854650811917168/1217419145218370)
- [21249-1037 - Exact Laser Cut BOM - 1037 - 5.Button Stations Langley - SA-28](https://app.asana.com/0/854650811917168/1217419145717808)

### Unresolved finish parts (pending CHG-3A pack inheritance)
These finish/process parts have NULL `build_assembly_pack_gid`, so they cannot yet be attributed to this pack:
- `1037.1016.1.003`
- `1037.1016.1.007`
- `1037.1016.X.001`

_Read-only demo — no Asana/DB writes. Production `orders_by_job` left unchanged as the labelled job-level fallback._
