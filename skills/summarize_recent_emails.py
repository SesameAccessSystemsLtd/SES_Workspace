import sys
import os

# Add the skills directory to the Python path to allow importing email_reader
sys.path.append(os.path.join(os.path.dirname(__file__), 'email_reader'))

from email_reader import read_emails

def summarize_recent_emails(count=4):
    report_summary = []
    report_summary.append(f"Summary of the {count} most recent emails from purchasing@sesameaccess.com:\n")

    # Retrieve recent emails (no filter for read status)
    emails_data = read_emails(top=count, filter_str=None)

    if emails_data:
        for i, email in enumerate(emails_data):
            report_summary.append(f"  {i+1}. From: {email.get("from", {}).get("emailAddress", {}).get("address")}")
            report_summary.append(f"     Subject: {email.get("subject")}")
            report_summary.append(f"     Received: {email.get("receivedDateTime")}")
            report_summary.append(f"     Preview: {email.get("bodyPreview")[:100]}...\n")
        report_summary.append(f"Total recent emails: {len(emails_data)}\n")
    else:
        report_summary.append(f"No recent emails found in purchasing@sesameaccess.com.\n")
        
    return "\n".join(report_summary)

if __name__ == "__main__":
    report = summarize_recent_emails(count=4)
    sys.stdout.buffer.write(report.encode('utf-8'))
