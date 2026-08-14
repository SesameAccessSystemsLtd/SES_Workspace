from email_reader import EmailReaderSkill
skill = EmailReaderSkill()
import requests
import json
access_token = skill._get_access_token()
user_principal_name = "purchasing@sesameaccess.com"
graph_url = f"https://graph.microsoft.com/v1.0/users/{user_principal_name}/messages"
headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
params = {
    "$top": 100,
    "$orderby": "receivedDateTime desc",
    "$select": "subject,from,receivedDateTime,bodyPreview",
    "$filter": "isRead eq false"
}
response = requests.get(graph_url, headers=headers, params=params)
if response.status_code == 200:
    for e in response.json().get('value', []):
        subj = e.get('subject', '')
        if not subj.startswith('GPSLive'):
            date = e.get('receivedDateTime')
            if date.startswith('2026-04-15'):
                print(f"Date: {date} | From: {e.get('from',{}).get('emailAddress',{}).get('name','')} | Subj: {subj}".encode('ascii', 'ignore').decode())
                print(f"Preview: {e.get('bodyPreview')[:200]}...\n".encode('ascii', 'ignore').decode().replace('\r', '').replace('\n', ' '))
