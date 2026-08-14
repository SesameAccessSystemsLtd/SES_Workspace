# email_reader Skill

## Description
This skill provides read-only access to emails via Microsoft Graph API, authenticated through Azure Active Directory. It is designed to retrieve email content for monitoring and coordination purposes, adhering strictly to a read-only policy.

## Usage
This skill is primarily intended for internal use by the agent to access email data for analysis and to inform procurement tasks. It will not perform any actions like sending emails or modifying mailboxes.

## Configuration
The following Azure AD application details are required for this skill:
- Application (client) ID
- Object ID
- Directory (tenant) ID
- Client Secret

These details are stored securely within the agent's configuration.

## Actions
- Retrieve unread emails from a specified mailbox.
- Parse email content and metadata for analysis.

## Security
- Strictly read-only access.
- Authentication uses the OAuth 2.0 client credentials flow.
- Client secret is handled securely.
