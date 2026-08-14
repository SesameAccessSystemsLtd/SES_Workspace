"""
pdf_extractor.py — Extract structured data from supplier PDF documents.

Parses delivery notes, order acknowledgements, and despatch notes to extract
PO numbers, order references, supplier details, and line items.

Usage:
    python pdf_extractor.py <path_to_pdf>
    python pdf_extractor.py --base64 <base64_encoded_pdf_bytes>

Or import:
    from pdf_extractor import PDFExtractorSkill
"""

import argparse
import base64
import io
import json
import re
import sys
import tempfile
import os


def _try_import_pdfplumber():
    try:
        import pdfplumber
        return pdfplumber
    except ImportError:
        sys.stderr.write(
            "pdfplumber not installed. Run: "
            "\"C:\\Users\\GilesHugo\\SES_Workspace\\.venv\\Scripts\\python.exe\" "
            "-m pip install pdfplumber\n"
        )
        sys.exit(1)


# Patterns for PO / order reference extraction
_PO_PATTERNS = [
    r'P\.?O\.?\s*[-:#]?\s*([A-Z0-9][-A-Z0-9/]{3,})',
    r'Purchase\s+Order\s*[-:#]?\s*([A-Z0-9][-A-Z0-9/]{3,})',
    r'Order\s+No\.?\s*[-:#]?\s*([A-Z0-9][-A-Z0-9/]{3,})',
    r'Our\s+Ref\.?\s*[-:#]?\s*([A-Z0-9][-A-Z0-9/]{3,})',
]

_ORDER_REF_PATTERNS = [
    r'Your\s+Ref\.?\s*[-:#]?\s*([A-Z0-9][-A-Z0-9/]{3,})',
    r'Supplier\s+Ref\.?\s*[-:#]?\s*([A-Z0-9][-A-Z0-9/]{3,})',
    r'Order\s+Ref(?:erence)?\.?\s*[-:#]?\s*([A-Z0-9][-A-Z0-9/]{3,})',
    r'Despatch\s+No\.?\s*[-:#]?\s*([A-Z0-9][-A-Z0-9/]{3,})',
    r'Delivery\s+Note\s*[-:#]?\s*([A-Z0-9][-A-Z0-9/]{3,})',
]

_DATE_PATTERN = re.compile(
    r'\b(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{2,4})\b'
    r'|\b(\d{4})[/\-\.](\d{1,2})[/\-\.](\d{1,2})\b'
)

_DOC_TYPE_KEYWORDS = {
    'delivery_note': ['delivery note', 'delivery advice', 'goods received', 'despatch note'],
    'order_acknowledgement': ['order acknowledgement', 'order confirmation', 'order accepted'],
    'despatch_note': ['despatch note', 'dispatch note', 'shipping note'],
    'invoice': ['invoice', 'tax invoice', 'vat invoice'],
}


def _extract_first_match(text, patterns):
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None


def _detect_document_type(text):
    text_lower = text.lower()
    for doc_type, keywords in _DOC_TYPE_KEYWORDS.items():
        for kw in keywords:
            if kw in text_lower:
                return doc_type
    return 'unknown'


def _extract_date(text):
    match = _DATE_PATTERN.search(text)
    if not match:
        return None
    groups = match.groups()
    if groups[0]:  # DD/MM/YYYY or DD/MM/YY
        d, m, y = groups[0], groups[1], groups[2]
        if len(y) == 2:
            y = '20' + y
        return f"{y}-{m.zfill(2)}-{d.zfill(2)}"
    else:  # YYYY-MM-DD
        y, m, d = groups[3], groups[4], groups[5]
        return f"{y}-{m.zfill(2)}-{d.zfill(2)}"


def _extract_supplier(text):
    # Heuristic: supplier name often appears in first 5 lines
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    # Skip very short lines and lines that look like addresses/numbers
    candidates = [l for l in lines[:8] if len(l) > 4 and not re.match(r'^[\d\s,]+$', l)]
    return candidates[0] if candidates else None


def _extract_line_items(text):
    """
    Attempt to extract line items from tabular data.
    Looks for rows with a part number pattern followed by description and quantity.
    Returns list of {part_no, description, qty} dicts.
    """
    items = []
    # Part number pattern: alphanumeric with hyphens, 4-20 chars
    line_item_re = re.compile(
        r'([A-Z]{2,}[-/]?\d{3,}[\w/-]*)\s+(.{5,60}?)\s+(\d+(?:\.\d+)?)\s*(?:ea|pcs|off|no|units?)?',
        re.IGNORECASE
    )
    for line in text.split('\n'):
        match = line_item_re.search(line)
        if match:
            part_no, description, qty = match.group(1), match.group(2).strip(), match.group(3)
            try:
                qty_int = int(float(qty))
            except ValueError:
                qty_int = None
            items.append({'part_no': part_no, 'description': description, 'qty': qty_int})
    return items


class PDFExtractorSkill:

    def extract_from_file(self, pdf_path):
        pdfplumber = _try_import_pdfplumber()
        try:
            with pdfplumber.open(pdf_path) as pdf:
                pages_text = [page.extract_text() or '' for page in pdf.pages]
        except Exception as e:
            return {'error': str(e), 'raw_text': '', 'document_type': 'unknown',
                    'po_number': None, 'order_ref': None, 'supplier': None,
                    'date': None, 'items': []}

        raw_text = '\n'.join(pages_text)
        return self._parse(raw_text)

    def extract_from_bytes(self, pdf_bytes):
        """Accept raw bytes (e.g. from base64.b64decode of Graph API contentBytes)."""
        pdfplumber = _try_import_pdfplumber()
        try:
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                pages_text = [page.extract_text() or '' for page in pdf.pages]
        except Exception as e:
            return {'error': str(e), 'raw_text': '', 'document_type': 'unknown',
                    'po_number': None, 'order_ref': None, 'supplier': None,
                    'date': None, 'items': []}

        raw_text = '\n'.join(pages_text)
        return self._parse(raw_text)

    def _parse(self, raw_text):
        return {
            'document_type': _detect_document_type(raw_text),
            'po_number': _extract_first_match(raw_text, _PO_PATTERNS),
            'order_ref': _extract_first_match(raw_text, _ORDER_REF_PATTERNS),
            'supplier': _extract_supplier(raw_text),
            'date': _extract_date(raw_text),
            'items': _extract_line_items(raw_text),
            'raw_text': raw_text,
        }


def main():
    parser = argparse.ArgumentParser(description='Extract structured data from a supplier PDF.')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('pdf_path', nargs='?', help='Path to PDF file')
    group.add_argument('--base64', dest='b64', help='Base64-encoded PDF bytes')
    args = parser.parse_args()

    skill = PDFExtractorSkill()

    if args.b64:
        pdf_bytes = base64.b64decode(args.b64)
        result = skill.extract_from_bytes(pdf_bytes)
    else:
        result = skill.extract_from_file(args.pdf_path)

    # Print raw_text separately to keep JSON clean
    output = dict(result)
    raw = output.pop('raw_text', '')
    print(json.dumps(output, indent=2))
    if raw:
        print('\n--- RAW TEXT (first 500 chars) ---')
        print(raw[:500])


if __name__ == '__main__':
    main()
