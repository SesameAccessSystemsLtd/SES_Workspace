import os, requests, msal
from dotenv import load_dotenv
load_dotenv(r'C:\Users\GilesHugo\.hermes\.env', override=True)
app = msal.ConfidentialClientApplication(
    os.getenv('Purchaisng_secret_ID'),
    authority=f"https://login.microsoftonline.com/{os.getenv('Purchasing_tenant_ID')}",
    client_credential=os.getenv('purchasing_Email_Api_Key')
)
token = app.acquire_token_for_client(scopes=['https://graph.microsoft.com/.default'])['access_token']
headers = {'Authorization': f'Bearer {token}'}
r = requests.get('https://graph.microsoft.com/v1.0/users/purchasing@sesameaccess.com/mailFolders?$top=100', headers=headers)
for f in r.json().get('value', []):
    name = f['displayName'].encode('ascii', 'ignore').decode()
    print(f"{name!r} | {f['totalItemCount']} items")
