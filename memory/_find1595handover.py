import os, requests

env = 'C:/Users/GilesHugo/AppData/Local/hermes/.env'
tok = None
with open(env) as f:
    for line in f:
        if line.startswith('ASANA_PERSONAL_ACCESS_TOKEN'):
            tok = line.split('=', 1)[1].strip().strip('"').strip("'")
            break
h = {'Authorization': f'Bearer {tok}'}

r = requests.get('https://app.asana.com/api/1.0/projects/850066254549317/tasks',
                 headers=h, params={'opt_fields': 'name,completed,notes,memberships.section.name', 'limit': 200}).json()

for t in r.get('data', []):
    n = t.get('name', '')
    if '1595' in n and ('handover' in n.lower() or 'legacy' in n.lower() or 'BOM' in n.upper()):
        print(f"[{t['gid']}] {n[:90]} | completed={t.get('completed')}")
        print('  NOTE:', (t.get('notes') or '')[:250].replace(chr(10), ' | '))
        print()
