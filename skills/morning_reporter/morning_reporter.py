import sys
import os

# Add the skills directory to the Python path to allow importing email_reader
sys.path.append(os.path.join(os.path.dirname(__file__), '..' , 'email_reader'))

from email_reader import EmailReaderSkill

def generate_morning_report():
    email_reader_instance = EmailReaderSkill()
    report_summary = []
    report_summary.append("Morning Update:\n")

    # Retrieve unread emails
    emails_data = email_reader_instance.read_emails(top=5, filter_str="isRead eq false")

    if emails_data:
        report_summary.append("Unread Emails from purchasing@sesameaccess.com:\n")
        for i, email in enumerate(emails_data):
            report_summary.append(f"  {i+1}. From: {email.get("from", {}).get("emailAddress", {}).get("address")}")
            report_summary.append(f"     Subject: {email.get("subject")}")
            report_summary.append(f"     Received: {email.get("receivedDateTime")}\n")
        report_summary.append(f"\nTotal unread emails: {len(emails_data)}\n")
    else:
        report_summary.append("No new unread emails in purchasing@sesameaccess.com.\n")
        
    # Convert list of strings to a single string for the report
    return "\n".join(report_summary)

if __name__ == "__main__":
    report = generate_morning_report()
    # Send the report to the parent session
    # In a cron job, this would be an isolated agent, so it needs to send to the parent session.
    # The sessionKey would typically be available in the environment or passed as an argument.
    # For demonstration, we'll assume a way to send it back to the 'main' session.
    try:
        # Assuming 'default_api' is available in the isolated session's context
        # and can send messages back to the parent. This often requires a mechanism
        # to get the parent session's key or relying on OpenClaw's internal routing.
        # For now, we will simply print it to stdout for debugging within the isolated session.
        # A production setup would involve a direct tool call to sessions_send here.
        sys.stdout.buffer.write(report.encode('utf-8'))

        # In a real isolated agent scenario, you'd do something like:
        # default_api.sessions_send(message=report, sessionKey="<parent_session_key>")
        # For now, let's make it print for visibility when run directly.
        pass
    except Exception as e:
        sys.stdout.buffer.write(f"Error sending report to parent session: {e}\n".encode('utf-8'))
