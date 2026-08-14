"""
lib/pdf_bom_extractor.py
pdfplumber-based BOM table extractor.
Returns a list of dicts per PDF — each row tagged with its source drawing pack.

Pass job_prefix to extract_pdf() so the part-number filter and regex are
parameterized. Defaults to None which disables the prefix filter (all rows pass).
"""
import re
import pdfplumber


def _build_part_regex(job_prefix: str) -> re.Pattern:
    p = re.escape(job_prefix)
    return re.compile(
        rf'({p}\.[A-Za-z0-9_\.\-]+|{p}\.ISO [A-Za-z0-9\.\-\s]+?'
        r'(?:\s+Steel|\s+Stainless|\s+Assembly|\s+Screw|\s+Washer|\s+Head|'
        r'\s+Cap|\s+Nut|\s+Bar|\s+Channel|\s+Trim|\s+Plate|\s+Bracket|'
        r'\s+Support|\s+Board|\s+Plate|$))',
        re.I
    )


def is_header_row(row) -> bool:
    text = ' '.join(str(c).strip().lower() for c in row if c)
    if 'part number' in text and 'description' in text and ('qty' in text or 'stock' in text):
        nonempty = sum(1 for c in row if c and str(c).strip())
        return 5 <= nonempty <= 12
    return False


def header_column_map(row) -> dict:
    header = [str(c).strip().lower() if c else '' for c in row]
    idx = {}
    for ci, cell in enumerate(header):
        if 'part number' in cell:
            idx['part_number'] = ci
        elif 'description' in cell or 'desc' in cell:
            idx['description'] = ci
        elif 'qty' in cell or 'quantity' in cell:
            idx['qty'] = ci
        elif 'stock' in cell:
            idx['stock'] = ci
        elif 'material' in cell:
            idx['material'] = ci
        elif 'vendor' in cell:
            idx['vendor'] = ci
        elif cell.strip() == 'item' and 'item' not in idx:
            idx['item'] = ci
    return idx


def parse_raw_line(row, idx_map, job_prefix: str | None = None) -> dict | None:
    row_text = ' '.join(str(c).strip() for c in row if c)
    row_text = re.sub(r'\s+', ' ', row_text).strip()
    if not row_text:
        return None
    if any(term in row_text.lower() for term in
           ['drawnby', 'checkedby', 'drawingpack', 'revision', 'projectname']):
        return None

    # Must contain a job-prefixed part number OR a generic fastener callout
    if job_prefix:
        p = re.escape(job_prefix)
        has_part = re.search(rf'{p}\.|{p}\.ISO|ISO 10642|M\d+x\d+', row_text, re.I)
    else:
        has_part = re.search(r'ISO 10642|M\d+x\d+', row_text, re.I)
    if not has_part:
        return None

    def cell(key):
        i = idx_map.get(key)
        if i is not None and i < len(row) and row[i]:
            return str(row[i]).strip()
        return ''

    part = cell('part_number')
    if not part and job_prefix:
        m = _build_part_regex(job_prefix).search(row_text)
        if m:
            part = m.group(0).strip()

    qty = cell('qty') or cell('stock')
    if not qty:
        m = re.search(r'\b(\d+)\b', row_text)
        if m:
            qty = m.group(1)

    return {
        'part_number': part,
        'description': cell('description'),
        'qty': qty,
        'material': cell('material'),
        'vendor': cell('vendor'),
    }


def parse_table(table, job_prefix: str | None = None) -> list[dict]:
    extracted = []
    for ri, row in enumerate(table):
        if is_header_row(row):
            idx_map = header_column_map(row)
            for row2 in table[ri + 1:]:
                if is_header_row(row2):
                    break
                parsed = parse_raw_line(row2, idx_map, job_prefix)
                if parsed:
                    extracted.append(parsed)
    return extracted


def extract_pdf(pdf_path: str, job_prefix: str | None = None) -> list[dict]:
    """Extract all BOM rows from a PDF file. Returns deduplicated list of dicts."""
    results = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                if table:
                    results.extend(parse_table(table, job_prefix))

    seen, unique = set(), []
    for r in results:
        key = (r['part_number'], r['description'], r['qty'], r['material'], r['vendor'])
        if key not in seen and r['part_number']:
            seen.add(key)
            unique.append(r)
    return unique
