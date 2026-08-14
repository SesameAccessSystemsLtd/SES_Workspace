import os
import requests
import json
import sys
from dotenv import load_dotenv

ENV_PATH = "C:\\Users\\GilesHugo\\.hermes\\.env"
load_dotenv(ENV_PATH, override=True)

TENANT_ID = "3bfd9e7d-219f-48db-bd9e-e63b3b990a1a"

class EmailReaderSkill:
    def __init__(self, config_path="C:\\Users\\GilesHugo\\.hermes\\config.json"):
        self.CONFIG_PATH = config_path
        self.TENANT_ID = None
        self.CLIENT_ID = None
        self.CLIENT_SECRET = None
        self.SCOPES = []
        self._load_config()

    def _load_config(self):
        # Prefer .env credentials; fall back to config.json
        self.CLIENT_ID = os.environ.get("Purchaisng_secret_ID")
        self.CLIENT_SECRET = os.environ.get("PURCHASING_API_KEY") or os.environ.get("purchasing_Email_Api_Key")
        self.TENANT_ID = TENANT_ID
        self.SCOPES = ["https://graph.microsoft.com/.default"]

        if not all([self.CLIENT_ID, self.CLIENT_SECRET]):
            # Fall back to config.json
            try:
                with open(self.CONFIG_PATH, 'r') as f:
                    config = json.load(f)
                self.TENANT_ID = config.get("tenantId", TENANT_ID)
                self.CLIENT_ID = self.CLIENT_ID or config.get("clientId")
                self.CLIENT_SECRET = self.CLIENT_SECRET or config.get("clientSecret")
                self.SCOPES = [config.get("endpoints", {}).get("scope", "https://graph.microsoft.com/.default")]
            except FileNotFoundError:
                sys.stdout.buffer.write(f"Error: config.json not found at {self.CONFIG_PATH}\n".encode('utf-8'))
            except json.JSONDecodeError:
                sys.stdout.buffer.write(f"Error: Could not decode JSON from {self.CONFIG_PATH}\n".encode('utf-8'))
        
        if not all([self.TENANT_ID, self.CLIENT_ID, self.CLIENT_SECRET, self.SCOPES]):
            sys.stdout.buffer.write(b"FATAL: Required Azure AD configuration missing or incomplete. Cannot proceed.\n")
            exit(1)

    def _get_access_token(self):
        token_url = f"https://login.microsoftonline.com/{self.TENANT_ID}/oauth2/v2.0/token"
        headers = {
            "Content-Type": "application/x-www-form-urlencoded"
        }
        data = {
            "client_id": self.CLIENT_ID,
            "scope": " ".join(self.SCOPES),
            "client_secret": self.CLIENT_SECRET,
            "grant_type": "client_credentials"
        }

        try:
            response = requests.post(token_url, headers=headers, data=data)
            response.raise_for_status()
            token_data = response.json()
            return token_data.get("access_token")
        except requests.exceptions.RequestException as e:
            sys.stdout.buffer.write(f"Error obtaining access token: {e}\n".encode('utf-8'))
            if response and response.text:
                sys.stdout.buffer.write(f"Response content: {response.text}\n".encode('utf-8'))
            return None


    def read_emails(self, top=5, filter_str="isRead eq false and inferenceClassification eq 'focused'"):
        access_token = self._get_access_token()
        if not access_token:
            sys.stdout.buffer.write(b"Failed to get access token, cannot read emails.\n")
            return None

        user_principal_name = "purchasing@sesameaccess.com"
        graph_url = f"https://graph.microsoft.com/v1.0/users/{user_principal_name}/messages"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        
        params = {
            "$top": top,
            "$select": "subject,from,receivedDateTime,bodyPreview"
        }
        if filter_str:
            params["$filter"] = filter_str

        try:
            response = requests.get(graph_url, headers=headers, params=params)
            response.raise_for_status()
            emails = response.json()
            return emails.get("value")
        except requests.exceptions.RequestException as e:
            sys.stdout.buffer.write(f"Error reading emails: {e}\n".encode('utf-8'))
            if response and response.text:
                sys.stdout.buffer.write(f"Response content: {response.text}\n".encode('utf-8'))
            return None

    def read_flagged_emails(self, top=5):
        access_token = self._get_access_token()
        if not access_token:
            sys.stdout.buffer.write(b"Failed to get access token, cannot read flagged emails.\n".encode('utf-8'))
            return None

        user_principal_name = "purchasing@sesameaccess.com"
        graph_url = f"https://graph.microsoft.com/v1.0/users/{user_principal_name}/messages"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        
        params = {
            "$top": top,
            "$filter": "flag/status eq 'flagged'",
            "$select": "subject,from,receivedDateTime,body"
        }

        try:
            response = requests.get(graph_url, headers=headers, params=params)
            response.raise_for_status()
            emails = response.json()
            return emails.get("value")
        except requests.exceptions.RequestException as e:
            sys.stdout.buffer.write(f"Error reading flagged emails: {e}\n".encode('utf-8'))
            if response and response.text:
                sys.stdout.buffer.write(f"Response content: {response.text}\n".encode('utf-8'))
            return None


    def read_emails_full_body(self, top=10, filter_str="isRead eq false and inferenceClassification eq 'focused'"):
        access_token = self._get_access_token()
        if not access_token:
            sys.stdout.buffer.write(b"Failed to get access token, cannot read emails.\n")
            return None

        user_principal_name = "purchasing@sesameaccess.com"
        graph_url = f"https://graph.microsoft.com/v1.0/users/{user_principal_name}/messages"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        params = {
            "$top": top,
            "$select": "subject,from,receivedDateTime,body,hasAttachments"
        }
        if filter_str:
            params["$filter"] = filter_str

        try:
            response = requests.get(graph_url, headers=headers, params=params)
            response.raise_for_status()
            return response.json().get("value")
        except requests.exceptions.RequestException as e:
            sys.stdout.buffer.write(f"Error reading emails: {e}\n".encode('utf-8'))
            if response and response.text:
                sys.stdout.buffer.write(f"Response content: {response.text}\n".encode('utf-8'))
            return None

    def get_email_attachments(self, message_id):
        """Fetch attachment metadata and content for a message.
        Returns list of dicts with: id, name, contentType, size, contentBytes (base64).
        PDF bytes can be decoded with base64.b64decode(attachment['contentBytes']).
        """
        access_token = self._get_access_token()
        if not access_token:
            sys.stdout.buffer.write(b"Failed to get access token, cannot fetch attachments.\n")
            return None

        user_principal_name = "purchasing@sesameaccess.com"
        graph_url = (
            f"https://graph.microsoft.com/v1.0/users/{user_principal_name}"
            f"/messages/{message_id}/attachments"
        )
        headers = {"Authorization": f"Bearer {access_token}"}

        try:
            response = requests.get(graph_url, headers=headers)
            response.raise_for_status()
            return response.json().get("value")
        except requests.exceptions.RequestException as e:
            sys.stdout.buffer.write(f"Error fetching attachments: {e}\n".encode('utf-8'))
            if response and response.text:
                sys.stdout.buffer.write(f"Response content: {response.text}\n".encode('utf-8'))
            return None


if __name__ == "__main__":
    skill = EmailReaderSkill()
    
    sys.stdout.buffer.write(b"--- Reading Unread Emails from Focused Inbox ---\n")
    unread_emails_data = skill.read_emails()
    if unread_emails_data:
        sys.stdout.buffer.write(json.dumps(unread_emails_data, indent=2).encode('utf-8'))
    else:
        sys.stdout.buffer.write(b"No unread emails found or an error occurred.\n")
    
    sys.stdout.buffer.write(b"\n\n--- Reading Flagged Emails ---\n")
    flagged_emails_data = skill.read_flagged_emails()
    if flagged_emails_data:
        sys.stdout.buffer.write(json.dumps(flagged_emails_data, indent=2).encode('utf-8'))
    else:
        sys.stdout.buffer.write(b"No flagged emails found or an error occurred.\n")



