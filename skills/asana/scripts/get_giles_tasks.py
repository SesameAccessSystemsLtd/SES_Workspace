import asana
import os
import json
import sys

sys.stdout.reconfigure(encoding='utf-8')

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

def main():
    try:
        client = get_api_client()
        
        workspace_gid = "357939803556123"  # sesameaccess.com
        giles_user_gid = "1142108324926610" # Giles
        gh_significance_cf_gid = "1208690694035909" # gH significance

        tasks_api = asana.TasksApi(client)

        # Retrieve tasks assigned to Giles within the workspace
        # Using limit to manage context window, fetching the first 50 tasks
        tasks = tasks_api.get_tasks(
            opts={'assignee': giles_user_gid, 'workspace': workspace_gid, 'limit': 50, 'opt_fields': "name,gid,projects.name,due_on,custom_fields"}
        )

        print(f"Tasks assigned to Giles (first 50) in workspace {workspace_gid}:")
        for task in tasks:
            task_name = task.get('name', 'N/A')
            task_gid = task.get('gid', 'N/A')
            project_names = ', '.join([p.get('name','N/A') for p in task.get('projects', [])])
            due_date = task.get('due_on', 'N/A')
            
            gh_significance_value = 'N/A'
            for cf in task.get('custom_fields', []):
                if cf.get('gid') == gh_significance_cf_gid:
                    if cf.get('enum_value'):
                        gh_significance_value = cf['enum_value'].get('name', 'N/A')
                    else:
                        gh_significance_value = cf.get('display_value', 'N/A')
                    break

            print(f"---\nTask Name: {task_name}\nTask GID: {task_gid}\nProjects: {project_names}\nDue Date: {due_date}\ngH Significance: {gh_significance_value}\n---")
                
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    main()