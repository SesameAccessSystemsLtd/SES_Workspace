# Execution Plan — Invoice Processing Pipeline (Windows 11 + Outlook)

## Overview

Automate the matching of Outlook invoice emails to Asana tasks, validate delivery + value, sign off with "Signed off by AI - AWAITING APPROVAL", and maintain an auditable local trail.

**Core Principle:** Hard-code rules first. Refine iteratively. The audit database is the control file. Asana is the system of record after sign-off.

---

## Phase 1: Foundation (Current State — Complete)

| Step | Status | Notes |
|------|--------|-------|
| 1.1 Asana connection | ✅ Done | PAT in `.cowork/secrets/config.sh` — authenticated as Richard |
| 1.2 Mapped custom fields | ✅ Done | All GIDs captured for Signed OFF, ORDER Status, Invoice fields, Vendor |
| 1.3 Mapped Signed OFF enums | ✅ Done | 7 options including new "Signed off by AI - AWAITING APPROVAL" (GID: `1215830009352317`) |
| 1.4 Mapped ORDER Status enums | ✅ Done | 19 options — "✅ARRIVED - MOVES TO INVOICES!!" is green light |
| 1.5 Created audit database | ✅ Done | `.cowork/audit/invoice-audit-database.json` with schema + 3 seed tasks |
| 1.6 Config populated | ✅ Done | All GIDs, folder names, PO regex platform-agnostic |
| 1.7 PO matching logic defined | ✅ Done | Regex `^[0-9]{5}-[0-9]{4,6}` matches start of task name |

---

## Phase 2: Outlook Email Access (Blocker — Needs Your Input)

**The mail app opens Outlook** — I need to determine the access method.

### Option A: Microsoft Graph API (Recommended for Outlook/Exchange)
- Uses OAuth2 to access Outlook mail via Microsoft's Graph API
- Can read the "INVOICES TO PROCESS" folder, list unread emails, download attachments (PDFs)
- Requires: Azure App registration + OAuth consent — I can guide you through this step by step
- **Best for:** Full Outlook desktop or Office 365 Exchange accounts
- Files to create: `outlook-graph.py` or `outlook-graph.ps1`

### Option B: IMAP
- If your Outlook email provider supports IMAP, we can use standard IMAP libraries
- Can read folder "INVOICES TO PROCESS", fetch unseen messages, download attachments
- Requires: IMAP server address, credentials
- **Fallback if:** Graph API isn't feasible

### I need 1 thing from you:

> **Is it a Microsoft 365 / Exchange work email account, or a different provider (Gmail, IMAP, etc)?**

That decides whether we go Graph API or IMAP.

---

## Phase 3: PDF Parsing (Awaiting Phase 2)

Once we can access an email with a PDF invoice attachment:
1. Download the PDF to temp
2. Use `parse_document` to extract text
3. Extract: Invoice Number, Invoice Value (Inc VAT), Supplier Name
4. Compare against Asana task data

---

## Phase 4: Matching Logic (Hard-Coded Rules)

```
For each unread email in "INVOICES TO PROCESS":

  1. Extract PO from email subject or body
     Regex: ^[0-9]{5}-[0-9]{4,6}
     → If not found → FLAG for manual review ("No PO found")

  2. Find Asana task by PO (task name starts with PO)
     → If no match → FLAG ("No task for PO")

  3. Check ORDER Status (GID: 1143955334207040)
     IF "✅ARRIVED - MOVES TO INVOICES!!" → Continue
     IF "🔜PARTIAL DELIVERY" → FLAG ("Partial delivery - manual check")
     IF "TO BE RETURNED" / "CANCELLED ORDER" → FLAG ("Order issue")
     ELSE → FLAG ("Not yet delivered")

  4. Extract invoice value from PDF/email body → compare with
     Asana "Invoice Value Inc VAT" (GID: 1204759087326205)
     IF match → Continue
     IF mismatch → FLAG ("Value mismatch: email=£X, Asana=£Y")

  5. WRITE to Asana:
     - "Signed OFF" → "Signed off by AI - AWAITING APPROVAL" (GID: 1215830009352317)
     - "Invoice Number" → extracted invoice number
     WRITE to audit database with result

  6. Move email to "INVOICES TO PROCESS/Invoice Archive"
```

---

## Phase 5: Scheduled Execution

| Method | Approach |
|--------|----------|
| Manual trigger | Run `process-invoices.bat` manually from command line |
| Scheduled | Windows Task Scheduler — run daily at set time |
| Startup | As a Windows Scheduled Task triggered at user logon |

---

## File Structure (Final)

```
.cowork/
├── secrets/
│   ├── config.sh          # PAT, GIDs, folder names — YOUR SECRETS
│   └── README.md
├── audit/
│   └── invoice-audit-database.json   # Master audit trail
└── LORE.md

invoice-processor/
├── README.md              # Overview documentation
├── EXECUTION-PLAN.md      # This file — the build roadmap
├── asana-api.sh           # Asana helper functions
├── outlook-graph.py       # Outlook email reader (Phase 2)
├── pdf-extract.py         # PDF invoice parser (Phase 3)
├── process-invoices.bat   # Main launcher script (Windows batch)
├── process-invoices.ps1   # Main launcher script (PowerShell)
└── config.ps1             # PowerShell configuration loader
```

---

## Decision Register

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-06-18 | Only process incomplete tasks | Completed tasks already invoiced/closed |
| 2026-06-18 | Single sign-off value: "Signed off by AI - AWAITING APPROVAL" | AI flags for human review — human does final approval |
| 2026-06-18 | PO regex: `^[0-9]{5}-[0-9]{4,6}` | Matches all examples: 21021-110526, 21030-1585, 21081-90626 |
| 2026-06-18 | Windows 11 + full Outlook desktop app | Confirmed by user |
| 2026-06-18 | Read-only email access (no sending) | User directive |
| 2026-06-18 | Audit database as local control file | Traceability for any future disputes |
