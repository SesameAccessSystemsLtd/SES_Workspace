import os
import argparse
import asana
import logging
import asyncio

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

# --- Configuration ---
# 2026-09-17: hardcoded token fallback removed (leaked in git history; rotate it).
# The token must come from the environment. Never hardcode it.
ASANA_CUSTOM_FIELD_PROJECT_NAME_GID = "1202882645384174"

# --- Asana Client Initialization ---
def get_asana_client():
    token = os.getenv("ASANA_PERSONAL_ACCESS_TOKEN")
    if not token:
        raise RuntimeError(
            "ASANA_PERSONAL_ACCESS_TOKEN not set. Export it (or put it in "
            "C:/Users/GilesHugo/.hermes/.env) before running. Do NOT hardcode it."
        )
    return asana.Client.access_token(token)

# --- Helper Functions ---
def strip_project_name(project_name):
    """
    Strips the leading '1' from a 4-digit code in the project name.
    Example: "1582 - Russel Square" -> "582 - Russel Square"
    """
    parts = project_name.split(' - ', 1)
    if len(parts) > 1 and parts[0].isdigit() and len(parts[0]) == 4 and parts[0].startswith('1'):
        return parts[0][1:] + ' - ' + parts[1]
    return project_name

async def get_or_create_enum_option(client, stripped_project_name, commit):
    """
    Checks if the stripped project name exists as an enum option for the global
    "PROJECT NAME" custom field. If not, it creates it.
    Returns the GID of the enum option.
    """
    dry_run_prefix = "[DRY-RUN] " if not commit else ""
    enum_options_api = client.custom_field_enum_options

    try:
        # Fetch existing enum options for the custom field
        result = await client.custom_fields.get_enum_options_for_custom_field(
            ASANA_CUSTOM_FIELD_PROJECT_NAME_GID, opt_fields=["name"]
        )
        enum_options = [option for option in result]

        # Check if stripped_project_name exists
        for option in enum_options:
            if option["name"] == stripped_project_name:
                logging.info(f"{dry_run_prefix}Found existing enum option for '{stripped_project_name}' (GID: {option['gid']})")
                return option["gid"]

        # If not found, create it
        logging.info(f"{dry_run_prefix}Enum option for '{stripped_project_name}' not found. Attempting to create...")
        if commit:
            new_enum_option = await enum_options_api.create_enum_option_for_custom_field(
                ASANA_CUSTOM_FIELD_PROJECT_NAME_GID, {"data": {"name": stripped_project_name}}
            )
            logging.info(f"Created new enum option '{stripped_project_name}' (GID: {new_enum_option['gid']})")
            return new_enum_option["gid"]
        else:
            logging.info(f"{dry_run_prefix}Would create new enum option for '{stripped_project_name}'.")
            # In dry-run, we cannot get a real GID, so return a placeholder
            return "DRY_RUN_ENUM_GID"

    except asana.error.APIError as e:
        logging.error(f"Asana API Error while getting/creating enum option: {e}")
        return None
    except Exception as e:
        logging.error(f"An unexpected error occurred: {e}")
        return None

async def get_all_tasks_in_project_recursive(client, project_gid):
    """
    Fetches all top-level tasks and their subtasks recursively within a project.
    Returns a flat list of all task GIDs.
    """
    all_task_gids = []
    tasks_api = client.tasks

    async def _get_tasks_and_subtasks(parent_gid=None, project_to_add=None):
        query_params = {
            "opt_fields": "gid,name,subtasks.gid,subtasks.name"
        }
        if project_to_add:
            query_params["project"] = project_to_add
        if parent_gid:
            query_params["parent"] = parent_gid
            
        tasks = await tasks_api.get_tasks(**query_params)
        
        for task in tasks:
            all_task_gids.append(task["gid"])
            
            # If the task has subtasks, recursively fetch them
            if "subtasks" in task and task["subtasks"]:
                for subtask_summary in task["subtasks"]:
                    # Fetch full subtask to ensure it's not already in the main list
                    await _get_tasks_and_subtasks(parent_gid=subtask_summary["gid"])
            
    # Start by getting top-level tasks in the project
    top_level_tasks_in_project = await tasks_api.get_tasks(
        project=project_gid,
        opt_fields="gid,name,subtasks.gid,subtasks.name"
    )

    for task in top_level_tasks_in_project:
        all_task_gids.append(task["gid"])
        if "subtasks" in task and task["subtasks"]:
             for subtask_summary in task["subtasks"]:
                    await _get_tasks_and_subtasks(parent_gid=subtask_summary["gid"]) # Recursively fetch subtasks of top-level tasks


    # Deduplicate GIDs as tasks might appear in multiple queries (e.g., parent and then directly)
    return list(set(all_task_gids))


async def multi_home_task_to_project(client, task_gid, project_gid, commit):
    """
    Multi-homes a task to the specified project.
    """
    dry_run_prefix = "[DRY-RUN] " if not commit else ""
    tasks_api = client.tasks
    try:
        # Check if the task is already in the project to avoid redundant calls or errors
        task_info = await tasks_api.get_task(task_gid, opt_fields="projects.gid")
        current_project_gids = [p["gid"] for p in task_info.get("projects", [])]

        if project_gid in current_project_gids:
            logging.info(f"{dry_run_prefix}Task {task_gid} is already multi-homed to project {project_gid}.")
            return True

        if commit:
            await tasks_api.add_project(task_gid, {"data": {"project": project_gid}})
            logging.info(f"Multi-homed task {task_gid} to project {project_gid}.")
            return True
        else:
            logging.info(f"{dry_run_prefix}Would multi-home task {task_gid} to project {project_gid}.")
            return True
    except asana.error.APIError as e:
        # Handle cases where task might already be in project or other errors
        if "already in project" in str(e).lower(): # Asana API sometimes returns 400 for this
             logging.warning(f"{dry_run_prefix}Task {task_gid} is already multi-homed to project {project_gid} (caught via APIError).")
             return True
        logging.error(f"Asana API Error while multi-homing task {task_gid} to project {project_gid}: {e}")
        return False
    except Exception as e:
        logging.error(f"An unexpected error occurred while multi-homing task {task_gid}: {e}")
        return False

async def update_task_custom_field(client, task_gid, enum_option_gid, commit):
    """
    Updates a task's global "PROJECT NAME" custom field with the given enum option GID.
    """
    dry_run_prefix = "[DRY-RUN] " if not commit else ""
    tasks_api = client.tasks
    try:
        if commit:
            await tasks_api.update_task(task_gid, {
                "data": {
                    "custom_fields": {
                        ASANA_CUSTOM_FIELD_PROJECT_NAME_GID: enum_option_gid
                    }
                }
            })
            logging.info(f"Updated custom field for task {task_gid} with enum option {enum_option_gid}.")
            return True
        else:
            logging.info(f"{dry_run_prefix}Would update custom field for task {task_gid} with enum option {enum_option_gid}.")
            return True
    except asana.error.APIError as e:
        logging.error(f"Asana API Error while updating custom field for task {task_gid}: {e}")
        return False
    except Exception as e:
        logging.error(f"An unexpected error occurred while updating custom field for task {task_gid}: {e}")
        return False

# --- Main Logic ---
async def main():
    parser = argparse.ArgumentParser(description="Initialize Asana Project with Custom Fields and Multi-homing.")
    parser.add_argument("--project-gid", required=True, help="GID of the project to initialize.")
    parser.add_argument("--project-name", required=True, help="Name of the project (e.g., '1582 - Russel Square').")
    parser.add_argument("--commit", action="store_true", help="Commit changes to Asana (default is dry-run).")
    args = parser.parse_args()

    client = get_asana_client()
    dry_run_prefix = "[DRY-RUN] " if not args.commit else ""

    logging.info(f"{dry_run_prefix}Starting project initialization for project GID: {args.project_gid}, name: '{args.project_name}'")

    # 1. Strip Project Name
    stripped_name = strip_project_name(args.project_name)
    logging.info(f"{dry_run_prefix}Original project name: '{args.project_name}' -> Stripped name: '{stripped_name}'")

    # 2. Get or Create Enum Option
    enum_option_gid = await get_or_create_enum_option(client, stripped_name, args.commit)
    if not enum_option_gid:
        logging.error("Failed to get or create enum option. Exiting.")
        return

    # 3. Recursive Crawl & Multi-homing & 4. Apply Custom Field
    logging.info(f"{dry_run_prefix}Fetching all tasks and subtasks in project {args.project_gid}...")
    all_task_gids = await get_all_tasks_in_project_recursive(client, args.project_gid)
    logging.info(f"{dry_run_prefix}Found {len(all_task_gids)} tasks/subtasks.")

    for task_gid in all_task_gids:
        # Multi-home all subtasks (and potentially top-level tasks if they aren't already explicitly in the project via other means)
        # It's safer to attempt multi-homing all tasks in the list rather than filtering,
        # as the multi-home function handles existing links.
        await multi_home_task_to_project(client, task_gid, args.project_gid, args.commit)

        # Apply Custom Field to all tasks/subtasks
        await update_task_custom_field(client, task_gid, enum_option_gid, args.commit)

    logging.info(f"{dry_run_prefix}Project initialization complete.")

# --- Cloud Run / Webhook Notes ---
# This section provides guidance on how this script would be integrated
# into a Cloud Run Webhook Engine and includes a stub for webhook registration.

# The `project_initializer.py` script is designed to be triggered by an external event,
# likely an Asana webhook, or a scheduled job, in a Cloud Run environment.

# ----------------------------------------------------------------------------------
# CLOUD RUN WEBHOOK ENGINE INTEGRATION NOTES
# ----------------------------------------------------------------------------------
# 1.  Webhook "Watcher" Service:
#     A separate Cloud Run service (or a component within the main webhook engine)
#     would act as a "Watcher". Its responsibilities would include:
#     a.  Listening for specific Asana events (e.g., 'project_added', 'task_added_to_project').
#     b.  Registering webhooks for new projects if they meet certain criteria.
#     c.  Handling the initial handshake (verification) for Asana webhooks.
#     d.  Parsing incoming webhook payloads.
#
# 2.  Triggering this Script:
#     When a relevant webhook event is received by the Watcher, it would invoke
#     this `project_initializer.py` script. This could be done by:
#     a.  Passing the relevant project_gid and project_name as command-line arguments
#         to a separate Cloud Run job that executes this script.
#     b.  Invoking a specific HTTP endpoint on *this* service (if this script
#         were wrapped in a lightweight Flask/FastAPI app) with the project details
#         in the request body.
#
# 3.  Authentication in Cloud Run:
#     The `ASANA_PERSONAL_ACCESS_TOKEN` would be securely stored as an environment
#     variable in the Cloud Run service's configuration.
#
# 4.  Idempotency:
#     The script is designed with idempotency in mind:
#     a.  Enum option creation checks for existence first.
#     b.  Multi-homing checks if the task is already in the project.
#     c.  Custom field updates overwrite, which is fine.
#     This prevents issues if the webhook fires multiple times for the same event.
# ----------------------------------------------------------------------------------

def register_webhook():
    """
    STUB FUNCTION for registering an Asana webhook.
    In a production Cloud Run Webhook Engine, this logic would typically
    reside in a dedicated "Watcher" service that manages webhook lifecycles.

    This function illustrates the intent: to set up a listener for Asana events.
    """
    logging.info("----------------------------------------------------------------")
    logging.info("CLOUD RUN / WEBHOOK INTEGRATION NOTE:")
    logging.info("This `register_webhook` function is a placeholder.")
    logging.info("In a live Cloud Run environment, a separate 'Watcher' component")
    logging.info("would handle Asana webhook registration and event listening.")
    logging.info("Example Webhook Setup (conceptual):")
    logging.info("  1. Asana sends a POST request to your Cloud Run endpoint for verification.")
    logging.info("  2. Your endpoint returns the X-Hook-Secret header.")
    logging.info("  3. Your endpoint then listens for subsequent events (e.g., 'task added').")
    logging.info("  TODO: Implement actual webhook registration logic in the Watcher service,")
    logging.info("        pointing to an endpoint that triggers this project_initializer.")
    logging.info("----------------------------------------------------------------")
    pass

if __name__ == "__main__":
    # When this script is run, it will execute the main asynchronous function.
    # In a Cloud Run environment, this might be triggered directly as a job
    # or wrapped in a web server (e.g., Flask) to accept HTTP POST requests
    # from the webhook watcher.
    asyncio.run(main())
    # Optionally, call the stub to show the webhook notes
    register_webhook()
