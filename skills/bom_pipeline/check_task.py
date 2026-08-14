import json
import requests

with open('ASANAconfig.json', 'r') as f:
    cfg = json.load(f)
token = cfg.get('ASANA_PERSONAL_ACCESS_TOKEN') or cfg.get('ASANA_PAT')
if not token:
    print('No token')
    exit(1)

headers = {'Authorization': f'Bearer {token}'}
# Check the first anodising task
r = requests.get('https://app.asana.com/api/1.0/tasks/1214420934901468?opt_fields=custom_fields.gid,custom_fields.enum_value.name,custom_fields.name,custom_fields.text_value', headers=headers)
if r.status_code == 200:
    data = r.json()['data']
    print('Task: ' + data.get('name', 'Unknown'))
    for cf in data.get('custom_fields', []):
        if cf['gid'] == '1199111659373956':  # Stock ACTION field
            enum_val = cf.get('enum_value', {})
            print(f'Stock ACTION: {enum_val.get("name", "Not set")}')
        elif cf['gid'] == '854127216887083':  # Vendor field
            print(f'Vendor: {cf.get("text_value", "Not set")}')
else:
    print(f'Error: {r.status_code}')