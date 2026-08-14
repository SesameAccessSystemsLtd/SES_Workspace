
import json
import os
from datetime import datetime
import sys
import io

# Set stdout encoding to UTF-8 for emoji support on Windows
    # Remove the stdout encoding fix, as it's causing issues with emojis in this environment.
    # The plan is to remove emojis and rely on default system encoding.
    # sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Paths (relative to workspace root, not script location)
PENDING_APPROVAL_QUEUE_PATH = r"C:\Users\GilesHugo\.openclaw\workspace\data\pending_approval_queue.json"
EMAIL_LOG_PATH = r"C:\Users\GilesHugo\.openclaw\workspace\data\email_pipeline_log.json" # Assuming this log exists/will exist

def generate_summary():
    emails_scanned = 0
    proposals_generated = 0
    alerts_raised = 0
    unmatched_senders = 0
    invoices_skipped = 0
    asana_tasks_updated = 0
    emails_tagged = 0
    pending_items = []

    # Try to read email pipeline log (if it exists)
    if os.path.exists(EMAIL_LOG_PATH):
        try:
            with open(EMAIL_LOG_PATH, 'r') as f:
                log_data = json.load(f)
                # Aggregate values from the log. This is illustrative; actual log structure may vary.
                emails_scanned = log_data.get('total_emails_fetched_in_run', 0)
                # Assuming 'proposals_generated' and 'alerts_raised' are also logged
                proposals_generated = log_data.get('proposals_generated_in_run', 0)
                alerts_raised = log_data.get('alerts_raised_in_run', 0)
                # Add more fields as your log structure evolves
        except Exception as e:
            print(f"WARNING: Could not read email pipeline log: {e}")

    # Read pending approval queue
    if os.path.exists(PENDING_APPROVAL_QUEUE_PATH):
        try:
            with open(PENDING_APPROVAL_QUEUE_PATH, 'r') as f:
                queue = json.load(f)
                for item in queue:
                    # Items that were approved and processed by Layer 4 won't be here
                    # So 'pending_review' or 'ignored' items are what remain.
                    # We only care about truly actionable pending items for the summary
                    if item.get('status') == 'pending_review' and not item.get('asana_task_gid'):
                        pending_items.append({
                            'subject': item.get('subject', 'Unknown Subject'),
                            'proposed_action': item.get('proposed_action', 'No action proposed')
                        })
                    
                    # For metrics on processed items, we'd need to read a 'processed_items_log'
                    # For now, we assume the executer script prints its own success.
        except Exception as e:
            print(f"WARNING: Could not read pending approval queue: {e}")

    # Generate summary message
    summary_parts = []
    summary_parts.append("SUMMARY: Email Pipeline Run Complete")
    summary_parts.append(f"- Emails scanned: {emails_scanned}")
    summary_parts.append(f"- Proposals generated: {proposals_generated}")
    summary_parts.append(f"- Alerts raised: {alerts_raised}")
    summary_parts.append(f"Asana tasks updated: {asana_tasks_updated}") # Placeholder, needs actual count from 04_asana_executer output
    summary_parts.append(f"Emails tagged: {emails_tagged}") # Placeholder, needs actual count from 04_asana_executer output

    if pending_items:
        summary_parts.append(f"- Items awaiting manual review: {len(pending_items)}")
        for i, item in enumerate(pending_items):
            summary_parts.append(f"    - {item['subject']} (Action: {item['proposed_action']})")
    else:
        summary_parts.append("✨ No items currently require manual review.")

    return "\n".join(summary_parts)

if __name__ == "__main__":
    # Create a dummy log file for testing if it doesn't exist
    if not os.path.exists(EMAIL_LOG_PATH):
        dummy_log = {
            "last_run_at": datetime.now().isoformat(),
            "total_emails_fetched_in_run": 15,
            "proposals_generated_in_run": 5,
            "alerts_raised_in_run": 1
        }
        with open(EMAIL_LOG_PATH, 'w') as f:
            json.dump(dummy_log, f, indent=2)

    print(generate_summary())
