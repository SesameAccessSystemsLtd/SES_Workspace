# morning_reporter Skill

## Description
This skill is responsible for generating automated morning reports, providing a concise summary of relevant information to the user. Initially, it focuses on unread emails from the 'purchasing@sesameaccess.com' mailbox.

## Usage
This skill is primarily triggered by a scheduled cron job to deliver daily morning updates. It uses the `email_reader` skill to gather email data and formats it for easy consumption.

## Actions
- Retrieve unread emails.
- Summarize key details from unread emails.
- Deliver the summary as a system event or message.

## Configuration
Relies on the `email_reader` skill for email access. Future enhancements may include filtering options based on user preferences.
