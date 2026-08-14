# Daily Memory — 2026-07-23

## REQUESTS Queue

| File | Status | Age |
|------|--------|-----|
| `2026-07-21-1588-phantom-gids-edge-case.md` | **PENDING** (OpenClaw queue) | 2 days — awaiting Giles action for manual recovery |

- **0 new REQUESTS files** created today
- **0 REQUESTS processed** today
- No REQUESTS_FAILED
- Last REQUESTS_COMPLETE before today: 2026-07-22 (ordering + navigator audit)

## BOM Pipeline

| Project | Status | Last Import |
|---------|--------|-------------|
| **1555 - Fifth Av Tesla Handrail** | `imported` (needs_order) | 2026-07-21 — NEW: 38 requirements, 23+ assembly packs linked (not in prior logs) |
| 1588 - America 1 Entire | `imported` (partial commit) | 2026-07-16 |
| 1588 - America Long Lead | `imported` | 2026-07-15 |
| 1588 - America 1 Scissor BOM | `imported` | 2026-07-02 |
| 1591 - Uni of Cambridge Entire BOM | `imported` | 2026-07-16 |
| 1594 - St Mary Scissor Lift BOM | `imported` (staging) | 2026-07-16 |
| 1580 - Admiralty Single Scissor BOM | `imported` | 2026-07-02 |
| Others | imported | various (May–Jun) |

**Open issues:**
- **1588 phantom GID edge case**: 44 parts_control rows for pack `1588.1025.A` (Low Profile Scissor Lift) still have phantom `asana_task_gid` values (404 from Asana). Unresolved — awaiting Giles manual recovery.
- **1594 St Mary** still in staging since Jul 16 — no progress to commit.
- **1555 Fifth Av Tesla Handrail** is the newest BOM (Jul 21) — 38 requirements across 23+ assembly packs. Not yet committed to Asana.

## Nightly Maintenance

- Status: OK — nothing to action (logged separately in `memory/nightly/2026-07-23.md`)
- No stale logs eligible for archiving
- Workspace structure: clean

## System Activity (Today)

| Metric | Value |
|--------|-------|
| DB writes today | 43,023 (sync batch: procurement_tasks 33k, qc_issues 5k, build_assembly_packs 2.5k) |
| Asana writes today | 0 |
| Asana errors (recent) | 0 |
| Items ordered today | 0 |
| POs created today | 0 |
| Unprocessed emails today | 0 |

## DB State Snapshot

| Domain | Count | Notes |
|--------|-------|-------|
| Parts control total | 2,142 | 1,269 unassociated; 44 phantom GIDs still open |
| Ordering queue open | 86 | Items across various stages |
| Procurement tasks open | ~500 | 213 awaiting confirmation, 91 to be ordered, 77 with confirmed delivery |
| Unverified PO matches | 22 | Email-PO matches not yet verified |
| QC issues total | 8,937 | In system |
| Stock inventory | 119 | Items tracked |
| Suppliers | recorded | Active supplier DB |

## Notes

- Quiet day — no new REQUESTS, no orders placed, no Asana writes. All DB activity was from the normal sync cycle (procurement, QC, parts_control, build_assembly_packs, suppliers).
- 1555 (Fifth Av Tesla Handrail, NYC) BOM imported on Jul 21 with 38 parts — this is a new job not yet tracked in prior memory logs. It has 23+ assembly packs with drawings linked.
- Phantom GID edge case (1588) remains unresolved — no activity on the phantom GID range today.
- The massive `procurement_tasks` sync (33k updates) suggests a full re-sync of the procurement task table from Asana occurred today.
