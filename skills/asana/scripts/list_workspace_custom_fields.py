import asana
import os
import json
import sys

# Set default encoding for stdout to UTF-8
sys.stdout.reconfigure(encoding='utf-8')

# --- Start of asana_connector.py relevant parts ---
def get_api_client():
    asana_pat = os.environ.get('ASANA_PERSONAL_ACCESS_TOKEN') or os.environ.get('ASANA_PAT')

    if not asana_pat:
        for cfg_name in ('ASANAconfig.json', 'config.json'):
            try:
                with open(cfg_name, 'r') as f:
                    config = json.load(f)
                asana_pat = config.get('ASANA_PERSONAL_ACCESS_TOKEN') or config.get('ASANA_PAT')
                if asana_pat:
                    break
            except FileNotFoundError:
                continue
            except json.JSONDecodeError:
                continue

    if not asana_pat:
        raise ValueError("ASANA personal access token not found. Set ASANA_PERSONAL_ACCESS_TOKEN env var or provide ASANAconfig.json")

    configuration = asana.Configuration()
    configuration.access_token = asana_pat
    return asana.ApiClient(configuration)
# --- End of asana_connector.py relevant parts ---

def main():
    try:
        client = get_api_client()
        workspace_gid = "357939803556123" # GID for sesameaccess.com
        
        custom_fields_api = asana.CustomFieldsApi(client)
        custom_fields = custom_fields_api.get_custom_fields_for_workspace(workspace_gid=workspace_gid, opts={})
        
        print(f"Custom Fields in Workspace {workspace_gid}:")
        for field in custom_fields:
            print(f"  Name: {field.get('name', 'N/A')}, GID: {field.get('gid', 'N/A')}, Type: {field.get('resource_subtype', 'N/A')}")
                
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    main()