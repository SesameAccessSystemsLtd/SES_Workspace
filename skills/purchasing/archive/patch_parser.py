import os
import json

path = r'C:\Users\GilesHugo\.openclaw\workspace\skills\purchasing\purchase_email_parser.py'
with open(path, 'r', encoding='utf-8') as f:
    text = f.read()

old_str = 'asana_token  = os.environ.get("ASANA_PERSONAL_ACCESS_TOKEN")'
new_str = '''asana_token = os.environ.get("ASANA_PERSONAL_ACCESS_TOKEN") or os.environ.get("ASANA_PAT")
    if not asana_token:
        try:
            with open(r"C:\\Users\\GilesHugo\\OneDrive - SESAME ACCESS AZURE\\OneDrive Documents\\LAB. SESAME\\Asana Scripts\\ASANAconfig.json") as f:
                asana_token = json.load(f).get("ASANA_PERSONAL_ACCESS_TOKEN")
        except:
            pass'''

if old_str in text:
    text = text.replace(old_str, new_str)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)
    print("Replaced!")
else:
    print("Not found.")
