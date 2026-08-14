# Invoice Processor — Asana + Outlook Workflow

Connects Outlook invoices to Asana tasks for delivery + value validation and sign-off.

## Workflow

```
1. Scan "INVOICES TO PROCESS" folder in Outlook
       │
2. Parse email + attachment for:
   - Invoice amount
   - PO number (e.g. 21021-110526)
       │
3. Look up Asana task by PO number
   (PO is at the start of the task name)
       │
4. Validate:
   - Is the task status "Delivered"?
   - Does the quoted value match the invoice?
       │
5. Sign off → Update Asana "Sign Off" custom field
   Move email to "Invoice Archive" subfolder
```

## Quick Start

### 1. Set up your Asana PAT

```bash
# In Asana web: Profile → Apps → Manage Developer Apps → +New Access Token
# Then paste it into:
nano .cowork/secrets/config.sh
```

### 2. Run manually

```bash
source .cowork/secrets/config.sh
./process.sh
```

### 3. Schedule it (optional cron)

```bash
# Every weekday at 9am:
crontab -e
0 9 * * 1-5 cd /path/to/invoice-processor && bash -c 'source .cowork/secrets/config.sh && ./process.sh' >> logs/cron.log 2>&1
```

## File Structure

```
.cowork/secrets/
├── config.sh          # PAT + folder names (YOUR SECRETS)
├── README.md          # Secrets documentation
└── .gitignore         # Prevents accidental commit

invoice-processor/
├── README.md          # This file
├── process.sh         # Main automation script
└── asana-api.sh       # Asana REST API helper functions
```

## PO Number Format

```
21021-110526 - UK Welding Supplies - Welding Gas refill
^^^^^ ^^^^^^
  PO    Ref (date or number)

21030-1585 - J&L - Black Buttons for Apple Tileyard
21081-90626 - Spa plating - gold solution

Regex: ^[0-9]{5}-[0-9]{4,6}
```

## Edge Cases Handled

- No matching Asana task found
- Task exists but not marked as delivered
- Invoice amount ≠ task quoted amount
- Missing or unreadable attachment
- PO number not found in email body or subject
