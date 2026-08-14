import asana
import os
import json
import sys

# Set default encoding for stdout to UTF-8
sys.stdout.reconfigure(encoding='utf-8')

# Assuming asana_connector.py is in the same directory or accessible via PYTHONPATH
# For simplicity, I'm going to assume it's directly importable from the current context.
# In a real scenario, you might need to adjust the import path.

# --- Start of asana_connector.py relevant parts (if not directly importable) ---
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
        
        # Get workspaces
        workspaces_api = asana.WorkspacesApi(client)
        workspaces = workspaces_api.get_workspaces(opts={})
        
        print("Workspaces:")
        for workspace in workspaces:
            print(f"  Name: {workspace['name']}, GID: {workspace['gid']}")
            
            # Get users for the workspace
            users_api = asana.UsersApi(client)
            users_in_workspace = users_api.get_users(opts={'workspace': workspace['gid']})
            
            print(f"    Users in {workspace['name']}:")
            for user in users_in_workspace:
                print(f"      Name: {user.get('name', 'N/A')}, Email: {user.get('email', 'N/A')}, GID: {user.get('gid', 'N/A')}")

            # Get projects for the workspace
            projects_api = asana.ProjectsApi(client)
            projects_in_workspace = projects_api.get_projects_for_workspace(workspace_gid=workspace['gid'], opts={})

            print(f"    Projects in {workspace['name']}:")
            for project in projects_in_workspace:
                print(f"      Name: {project.get('name', 'N/A')}, GID: {project.get('gid', 'N/A')}")
                
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    main()