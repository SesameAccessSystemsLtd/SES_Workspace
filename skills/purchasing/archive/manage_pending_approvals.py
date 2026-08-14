
import json
import os

QUEUE_FILE = os.path.join("data", "pending_approval_queue.json")

def load_queue():
    """Loads the pending approval queue from the JSON file."""
    if not os.path.exists(QUEUE_FILE):
        return []
    with open(QUEUE_FILE, 'r') as f:
        return json.load(f)

def save_queue(queue):
    """Saves the pending approval queue to the JSON file."""
    with open(QUEUE_FILE, 'w') as f:
        json.dump(queue, f, indent=2)

def display_pending_items(queue):
    """Displays items with 'pending_review' status."""
    pending_items = [item for item in queue if item.get('status') == 'pending_review']
    if not pending_items:
        print("No items currently pending review.")
        return [], {}

    print("\n--- Pending Approval Queue ---")
    print("Index | Email ID            | Subject                        | Asana Task Name          | Proposed Action")
    print("------------------------------------------------------------------------------------------------------")
    
    indexed_items = {}
    for i, item in enumerate(pending_items):
        index = i + 1
        email_id_short = item.get('email_id', 'N/A')[:20] + "..." if len(item.get('email_id', 'N/A')) > 20 else item.get('email_id', 'N/A')
        subject = item.get('subject', 'N/A')
        asana_task_name = item.get('asana_task_name', 'N/A')
        proposed_action = item.get('proposed_action', 'N/A')

        print(f"{index:<5} | {email_id_short:<20} | {subject:<30} | {asana_task_name:<25} | {proposed_action[:40]:<40}")
        indexed_items[str(index)] = item
        indexed_items[item.get('email_id')] = item
        if item.get('asana_task_gid'):
            indexed_items[item.get('asana_task_gid')] = item
    print("------------------------------------------------------------------------------------------------------")
    return pending_items, indexed_items

def get_selection(indexed_items):
    """Gets user selection for items to act upon."""
    while True:
        selection_input = input("\nEnter indices (e.g., '1 3'), email_ids, asana_task_gids, or 'q' to quit: ").strip()
        if selection_input.lower() == 'q':
            return []

        selected_items = []
        identifiers = selection_input.split()
        for identifier in identifiers:
            item = indexed_items.get(identifier)
            if item:
                selected_items.append(item)
            else:
                print(f"Warning: Identifier '{identifier}' not found. Please try again.")
                selected_items = [] # Clear selection if any invalid identifier
                break
        if selected_items:
            return selected_items

def main():
    queue = load_queue()
    if not queue:
        print(f"The queue file '{QUEUE_FILE}' is empty or does not exist.")
        return

    while True:
        pending_items, indexed_items = display_pending_items(queue)
        if not pending_items:
            print("All pending items have been processed. Exiting.")
            break

        selected = get_selection(indexed_items)
        if not selected:
            print("No items selected. Exiting.")
            break

        action = input("Enter action for selected items (approve/reject/skip/q): ").strip().lower()
        if action == 'q':
            print("No action taken. Exiting.")
            break
        elif action in ['approve', 'reject', 'skip']:
            for item in selected:
                original_status = item.get('status')
                if original_status == 'pending_review':
                    item['status'] = action
                    print(f"Item '{item.get('email_id', 'N/A')}' status changed from '{original_status}' to '{action}'.")
                else:
                    print(f"Skipping item '{item.get('email_id', 'N/A')}': current status is '{original_status}', not 'pending_review'.")
            save_queue(queue)
            print("Changes saved successfully!")
        else:
            print("Invalid action. Please choose 'approve', 'reject', 'skip', or 'q'.")

if __name__ == "__main__":
    main()
