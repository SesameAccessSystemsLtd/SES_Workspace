#!/usr/bin/env python3
"""
Extract Sesame Part Numbers from Stage 5 manifest for bot handover reference.
Shows the key structure that bots need to understand for automated Asana updates.
"""
import pandas as pd
import re

manifest_path = "C:/Users/GilesHugo/.openclaw/workspace/skills/bom_pipeline/1.BOM IMPORT into Asana/pipeline_runs/1575_Jenners_20260415_1738/stage5_import_manifest_20260415_1738.csv"

manifest = pd.read_csv(manifest_path, dtype=str).fillna('')

print('╔════════════════════════════════════════════════════════════════╗')
print('║  SESAME PART NUMBERS — BOT HANDOVER REFERENCE                 ║')
print('╚════════════════════════════════════════════════════════════════╝')
print()

sesame_parts = []
for idx, row in manifest.iterrows():
    task_name = row['Name']
    if not task_name or not task_name.strip():
        continue
    
    # Extract: "4 off 1575.XXXX..." pattern
    match = re.search(r'(\d+)\s+off\s+(1575\.[\s]?[A-Za-z0-9_\.\-]+)', task_name)
    if match:
        qty = match.group(1)
        pn = re.sub(r'\.\s+', '.', match.group(2))
        vendor = row.get('Vendor', '')
        sesame_parts.append({
            'sesame_pn': pn,
            'qty': qty,
            'vendor': vendor,
            'task_name': task_name,
            'description': row.get('Description', '')[:100],
        })

print(f'Total Sesame Part Numbers in manifest: {len(sesame_parts)}')
print()
print('Sample mappings (bot reference):')
print('─' * 120)
for sp in sesame_parts[:15]:
    print(f'PN: {sp["sesame_pn"]:25s} | Qty: {sp["qty"]:2s} | Vendor: {sp["vendor"]:20s}')
    print(f'    Task: {sp["task_name"][:70]}')
    print()

print('─' * 120)
print()
print('🔑 KEY IDENTIFIER: Sesame_PN (e.g., "1575.1006.A.126")')
print('   - Used to join extracted drawing data to manifest')
print('   - Maps to Asana task name pattern: "X off {SESAME_PN} - {Description}"')
print('   - Ready for Asana update workflow')
print()
