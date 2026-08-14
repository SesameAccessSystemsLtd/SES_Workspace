import os

path = r'C:\Users\GilesHugo\.openclaw\workspace\skills\purchasing\purchase_email_parser.py'
with open(path, 'r', encoding='utf-8') as f:
    text = f.read()

import re
old_func_pattern = r'def get_graph_token\(cfg\):.*?return r\.json\(\)\["access_token"\]'

new_func = '''def get_graph_token(cfg):
    tenant_id = os.environ.get("Purchasing_tenant_ID", "3bfd9e7d-219f-48db-bd9e-e63b3b990a1a")
    client_id = os.environ.get("Purchaisng_secret_ID")
    client_secret = os.environ.get("purchasing_Email_Api_Key")
    
    r = requests.post(
        f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
        data={
            "client_id":     client_id,
            "client_secret": client_secret,
            "scope":         "https://graph.microsoft.com/.default",
            "grant_type":    "client_credentials",
        },
    )
    r.raise_for_status()
    return r.json()["access_token"]'''

text = re.sub(old_func_pattern, new_func, text, flags=re.DOTALL)
with open(path, 'w', encoding='utf-8') as f:
    f.write(text)
print("Patched get_graph_token")
