# BOT AUTOMATION HANDOVER — 1575 Drawing BOM Import to Asana

## Executive Summary
This test environment demonstrates the complete workflow for extracting BOM data from PDF drawings, matching parts to Asana tasks via **Sesame Part Numbers**, and preparing updates for Asana import.

**Key Identifier**: `Sesame_PN` (e.g., `1575.1006.A.126`)  
**Join Operation**: Drawing extraction → Manifest lookup → Asana task update  
**Output Format**: CSV ready for Stage 7 Asana importer

---

## Phase 1: Setup & Dependencies (For Bots)

### Required Files to Copy/Reference
```
FROM: Current test environment
TO:   Bot automation environment

1. Drawing BOM Extractor Scripts:
   - 3.Drawing_BOM_Extractor/test_pdf_extraction_final.py
   - 3.Drawing_BOM_Extractor/stage6_adapter.py
   - 3.Drawing_BOM_Extractor/run_drawing_extraction_1575.py

2. Stage 5 Manifest (Asana Reference):
   - C:\Users\GilesHugo\.openclaw\workspace\skills\bom_pipeline\
     1.BOM IMPORT into Asana\pipeline_runs\
     1575_Jenners_20260415_1738\
     stage5_import_manifest_20260415_1738.csv

3. PDF Source Data:
   - C:\Users\GilesHugo\SAS Dropbox\3. Projects\Jenners, Edinburgh\
     Design\2.2. Assembly Drawings\3. Uploaded on Asana\
```

### Python Dependencies
```
pdfplumber    # PDF table extraction
pandas        # CSV/data manipulation
openpyxl      # Excel compatibility (legacy BOMs)
```

### Installation
```bash
pip install pdfplumber pandas openpyxl
```

---

## Phase 2: Key Data Structures (Reference for Bots)

### Sesame Part Number Format
```
PATTERN: 1575.[SECTION].[SUBSECTION].[ITEM]
         OR: 1575.ISO[STANDARD]-[SPEC]

EXAMPLES:
  1575.1006.A.126        ← Standard fabricated part
  1575.1010.B.062_MIR    ← Mirrored variant
  1575.ISO 10642 - M6    ← Standard fastener (ISO reference)
  1575.DIN 6799          ← German standard reference

LENGTH: 8-30 characters
UNIQUE: 435+ in current 1575 Jenners manifest
```

### Sesame PN in Asana Task Name (Manifest Format)
```
SOURCE PATTERN in manifest:
  "X off {SESAME_PN} - {DESCRIPTION}"

EXAMPLES from actual manifest:
  "4 off 1575. 1006.A.126 - ^Flat Anodising"
  "2 off 1575. 1010.B.062 - Rod (BRIGHT) Yellow Zinc Plating"
  "1 off 1575. 1008.Q.001 - Channel Laser Cut"

NOTE: Space after first dot (1575. 1006...) must be normalized to (1575.1006...)
```

---

## Phase 3: Bot Workflow (Step by Step)

### Step 1: Extract from PDFs
**Script**: `run_drawing_extraction_1575.py`

**Input**: 
- PDF folder: `C:/Users/GilesHugo/SAS Dropbox/3. Projects/Jenners, Edinburgh/.../Uploaded on Asana`
- 16 PDF files

**Process**:
1. Use `pdfplumber` to detect BOM tables on each PDF page
2. Identify header rows (contains "Part Number", "Description", "QTY")
3. Extract data rows using column mapping
4. Deduplicate by (PN, Description, QTY, Material, Vendor)
5. Normalize part numbers (remove spaces)

**Output**: `extracted_drawing_bom.csv`
```
Part Number | Description | Total Project QTY | Material | Vendor | Calculation Logic
1575.1006.A26w | Welded assembly | 1 | Stainless Steel | Welded by Sesame | Extracted from PDF
...
```

---

### Step 2: Load Manifest & Build Index
**Script**: `stage6_adapter.py` (or bot equivalent)

**Input**: 
- Stage 5 manifest CSV: `stage5_import_manifest_20260415_1738.csv`
- Manifest contains Asana task hierarchy from Build Orders project

**Process**:
1. Parse each task name to extract Sesame_PN
2. Normalize: Remove spaces after dots (`1575. 1006` → `1575.1006`)
3. Build index: `{Sesame_PN} → {Task Info}`
4. Extract quantity from task name (e.g., "4 off" → qty=4)

**Output**: In-memory index (dict)
```python
{
  '1575.1006.A.126': {
    'task_name': '4 off 1575. 1006.A.126 - ^Flat Anodising',
    'qty': 4,
    'vendor': 'Anodising',
    'row_idx': 123
  },
  '1575.1010.B.062': { ... },
  ...
}  # Total: 435 entries
```

---

### Step 3: Match Extracted to Manifest
**Script**: `stage6_adapter.py` matching logic

**Join Operation**:
```
FOR each extracted_part IN extracted_drawing_bom.csv:
  1. Try exact match: extracted_part.pn IN manifest_index
  2. If no match, try fuzzy match: similarity(extracted_part.pn, manifest_pn) >= 75%
  3. Return: (extracted_part, manifest_task, match_confidence)
```

**Match Status Categories**:
- `EXACT` — Direct Sesame_PN match found (433 cases)
- `FUZZY(X%)` — Similarity match above threshold (311 cases)
- `NOMATCH` — No match found; ISO/DIN fastener or out-of-scope (264 cases)

**Output**: `stage6_drawing_pack_parts.csv`
```
Extracted_PN | Extracted_DESC | Extracted_QTY | ... | Manifest_PN | Manifest_Vendor | Match_Status
1575.1006.A26w | Welded ass... | 1 | ... | 1575.1006.A26w | Anodising | EXACT
1575.1010.B.062 | Rod | 2 | ... | 1575.1010.B.062 | Yellow Zinc Plating | EXACT
```

---

### Step 4: Prepare Asana Update Payload
**Next Stage (Stage 7)**: Reference updates using matched data

**Bot Action**:
1. Read `stage6_drawing_pack_parts.csv`
2. FOR each row WHERE Match_Status = "EXACT" or "FUZZY":
   - Get Sesame_PN (key identifier)
   - Lookup Asana task GID from manifest
   - Prepare description update with:
     - Drawing pack source
     - Extracted quantity vs manifest quantity
     - Material, vendor, links
   - Send to Asana API using task GID

---

## Phase 4: Data Flow Diagram

```
┌─────────────────┐
│  PDF Drawings   │  (16 files, 1575 Jenners project)
│  Dropbox folder │
└────────┬────────┘
         │
         v
┌──────────────────────────────┐
│ STAGE 5.5 EXTRACTION         │
│ run_drawing_extraction_1575  │
│ - pdfplumber table detect    │
│ - Regex part number extract  │
└────────┬─────────────────────┘
         │
         v
┌─────────────────────────┐
│ extracted_drawing_bom   │   (Raw: 1,008 rows)
│ .csv                    │
└────────┬────────────────┘
         │
         v     + joins with
         │
         ├─────────────────────────────────────────┐
         │                                         │
         │  ┌──────────────────────────────┐ │
         │  │ SESAME PART NUMBER KEY        │ │
         │  │ (435 unique in manifest)      │ │
         │  └──────────────────────────────┘ │
         │                                   │
         v                                   v
┌──────────────────────────┐  ┌─────────────────────┐
│ STAGE 6 MATCHING         │  │ Stage 5 Manifest    │
│ stage6_adapter.py        │  │ CSV from Asana      │
│ - Exact + Fuzzy match    │  │ (Build Orders proj) │
└────────┬─────────────────┘  └─────────────────────┘
         │
         v
┌──────────────────────────┐
│ stage6_drawing_pack_    │   (744 matched, 73.8%)
│ parts.csv              │
│                        │
│ Match_Status: EXACT    │   ← Ready for Asana
│ or FUZZY(>75%)         │
└────────┬─────────────────┘
         │
         v
┌──────────────────────────┐
│ STAGE 7 ASANA UPDATE     │
│ (Not yet in test env)    │
│ - Push via Asana API     │
│ - Use Sesame_PN + GID    │
└──────────────────────────┘
```

---

## Phase 5: Bot Integration Checklist

### For Bot Developers
- [ ] Copy extraction scripts to bot environment
- [ ] Verify Python dependencies installed
- [ ] Update file paths (PDFs, manifest,  output)
- [ ] Test extraction on first 3 PDFs
- [ ] Validate match rate (expect 70-80%)
- [ ] Confirm Sesame_PN format in output
- [ ] Set up manifest refresh schedule (if needed)
- [ ] Create Asana API integration for Stage 7

### Configuration Variables (Bot Should Accept)
```python
PDF_SOURCE_FOLDER = "C:/Users/.../Uploaded on Asana"
MANIFEST_PATH = "C:/.../stage5_import_manifest_*.csv"
OUTPUT_DIR = "C:/.../stage6_output"
FUZZY_MATCH_THRESHOLD = 75  # Similarity percentage
```

### Monitoring & Validation
- Extract row count should be > 500
- Match rate should be 60-80%
- Unmatched count should be < 400 (mostly fasteners)
- All output CSVs should have same row count (1,008)

---

## Phase 6: File Inventory for Handover

### Core Scripts (Tested ✓)
| File | Lines | Purpose | Mode |
|------|-------|---------|------|
| `test_pdf_extraction_final.py` | 220 | PDF extraction engine | Module |
| `stage6_adapter.py` | 290 | Manifest matching | Module |
| `run_drawing_extraction_1575.py` | 380 | Complete workflow | Script |
| `bot_handover_reference.py` | 80 | Key identifier reference | Reference |

### Input Files
| File | Size | Rows | Refresh |
|------|------|------|---------|
| PDF Drawings folder | ~500 MB | 16 files | Annual |
| Stage 5 Manifest | 2.1 MB | ~950 | Per BOM import |

### Output Files (Generated)
| File | Size | Rows | Lifecycle |
|------|------|------|-----------|
| `extracted_drawing_bom.csv` | 156 KB | 1,008 | Temp (for validation) |
| `stage6_drawing_pack_parts.csv` | 144 KB | 1,008 | Prod (feeds Stage 7) |

---

## Phase 7: Key Takeaways for Bot Handover

### The Core Join Key
```
Sesame_PN = "1575." + [SECTION].[SUBSECTION].[ITEM]

This is YOUR PRIMARY KEY for:
✓ Matching drawing extracts to Asana tasks
✓ Identifying which parts go on which pack
✓ Preparing Asana updates
✓ Tracking part quantities and vendors
```

### Success Metrics
- ✓ 1,008 parts extracted from 16 PDFs
- ✓ 744 matched to Asana manifest (73.8%)
- ✓ Unmatched = standard fasteners (expected spillover)
- ✓ Zero data loss in extraction pipeline
- ✓ All scripts run in <2 minutes

### Ready for Automation
- [x] Extraction logic tested and working
- [x] Matching algorithm validated
- [x] Output format documented
- [x] Error handling in place
- [x] Sesame_PN key identifier defined
- [x] Scripts ready for bot integration

---

## Appendix: Example Join Operation

### Input Row (Extracted from Drawing)
```json
{
  "Part Number": "1575.1006.A26w",
  "Description": "Welded assembly",
  "Total Project QTY": "1",
  "Material": "Stainless Steel",
  "Vendor": "Welded by Sesame"
}
```

### Manifest Reference (Sesame_PN as Key)
```json
{
  "1575.1006.A26w": {
    "task_name": "1 off 1575. 1006.A26w - Welded assembly Anodising",
    "qty": 1,
    "vendor": "Anodising",
    "order_qty": "1"
  }
}
```

### Matched Output (Ready for Asana)
```json
{
  "Extracted_PN": "1575.1006.A26w",
  "Manifest_PN": "1575.1006.A26w",
  "Sesame_PN_Key": "1575.1006.A26w",
  "Match_Status": "EXACT",
  "Vendor_Extract": "Welded by Sesame",
  "Vendor_Manifest": "Anodising",
  "Ready_for_Asana": true
}
```

---

## Contact & Questions
For bot developers: See script docstrings and inline comments for detailed logic.  
For Asana team: Output is ready for Stage 7 API importer.  
Date Generated: 17 April 2026
