# Edge Case — BOM importer / golden-index stock-qty source review (STAGED, awaiting approval)

Staged: 2026-09-07 · Reported by Giles · Prepared by Hermes (read-only audit, no Asana write yet)

## Giles's report
"Review the BOM importer — I think the golden index lookup is pulling stock qty from the wrong GID. It should pull from **1126782469949969**. Log this issue and address so we are checking Asana against the correct live field."

## Hermes audit finding (verified 2026-09-07)
Traced every stock-qty reader across the BOM importer + golden-index pathway.

| Script | Stock GID used | Correct? |
|---|---|---|
| `2.Stock Controls/03_stock_check.py` (Stage 3) | `1126782469949969` (STOCK QTY) | ✅ |
| `BOM_IMPORT_4_Asana_v4.py` (re-query on KAN amend, line 456) | `1126782469949969` (STOCK QTY) | ✅ |
| `golden_index_rev6` DB table | **no stock-qty column** (KAN↔stock mapping only) | n/a |
| purchasing tools (`arrived_stock_processor`, `audit_parts_control_routing`) | `1126782469949969` (STOCK QTY) | ✅ |

Live-probe of 4 KAN-matched tasks confirmed the importer's staged "Stock QTY" EQUALS the live `1126782469949969` value (18/22/8) — NOT the ORDER QTY field (1201447044882111 = 20/24/10). So the PRIMARY stock-qty read is correct; the golden index itself carries no stock quantity.

## Real, reproducible gaps found (candidates for the fix)
1. **PICK path trusts Stage-3 cached stock.** The importer only re-queries LIVE `1126782469949969` when a KAN is manually amended (BOM_IMPORT lines 437-466, and only under `--commit`). If stock moves between Stage 3 (staging) and commit, a part staged as PICK can be already-out. Hardening ask: always re-read live STOCK QTY at commit for PICK parts (not just KAN-amended).
2. **Trigger-vs-min header mislabelling.** `03_stock_check.py` labels `1198179728033183` as "min Stock QTY" but the authoritative registry (`db_init_ops.py`) defines `1198179728033183` = **Trigger Order QTY** (reorder threshold). Low-stock alert uses the right number but the meaning/header is wrong — can mislead interpretation.

## Ask
Do NOT create in Asana until Giles confirms how to characterise it:
- (a) log BOTH gaps as one System-issue edge case, or
- (b) Giles pastes the specific part/job where he saw the wrong value so Hermes can locate any real wrong mapping first.

If "like to log it", destination = Edge Cases `1214604066893979` → New Issues, ACTOR=HUMAN (Giles reported), Issue Type=System Issue, Priority=High.
