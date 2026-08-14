# pdf_extractor — Skill Reference

Parses PDF attachments (delivery notes, order acknowledgements, despatch notes) and returns structured data for matching against Asana tasks.

## Script

```
"C:\Users\GilesHugo\.openclaw\venv\Scripts\python.exe" "C:\Users\GilesHugo\.openclaw\workspace\skills\pdf_extractor\pdf_extractor.py" <pdf_path_or_base64>
```

Or import directly:

```python
from pdf_extractor import PDFExtractorSkill
skill = PDFExtractorSkill()
result = skill.extract_from_file("C:\\path\\to\\delivery_note.pdf")
result = skill.extract_from_bytes(base64_bytes)  # for Graph API attachments
```

## Output Format

```json
{
  "document_type": "delivery_note | order_acknowledgement | despatch_note | invoice | unknown",
  "po_number": "PO-2026-0142 or null",
  "order_ref": "supplier's own reference number or null",
  "supplier": "supplier name extracted from document or null",
  "date": "YYYY-MM-DD or null",
  "items": [
    {"part_no": "CAN-1042", "description": "Hydraulic Cylinder 40mm", "qty": 4}
  ],
  "raw_text": "full extracted text — use this if structured fields are insufficient"
}
```

## Requirements

```
pip install pdfplumber
```

Already in venv if installed. To check:
```
"C:\Users\GilesHugo\.openclaw\venv\Scripts\python.exe" -c "import pdfplumber; print('ok')"
```

## Usage Notes

- Works on text-based PDFs (most supplier documents). Scanned/image PDFs return `raw_text: ""` — log as `no_match` and surface to Giles.
- `po_number` extraction looks for patterns: `PO-`, `Purchase Order`, `P.O.`, `Order No`, `Our Ref`.
- `order_ref` extraction looks for: `Your Ref`, `Supplier Ref`, `Order Reference`, `Despatch No`.
- When both `po_number` and `order_ref` are found, use `po_number` first for Asana matching.
- If `raw_text` is non-empty but structured fields are null, pass `raw_text` to the agent for manual extraction.

## Used By

`ops_agent` exclusively — for email→PDF→Order Numbers matching workflow.
