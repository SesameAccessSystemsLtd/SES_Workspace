# Google Sheets Setup — Email Approval Dashboard

One-time setup. Takes about 10 minutes.

## What you need

A **Google Service Account** — a bot credential that lets the scripts
read and write the Google Sheet without needing your Google login.

---

## Step 1 — Go to Google Cloud Console

Open: https://console.cloud.google.com/

If you don't have a project, create one (name it anything, e.g. "OpenClaw").

---

## Step 2 — Enable Google Sheets API

1. In the search bar, type **"Google Sheets API"**
2. Click it → click **Enable**

---

## Step 3 — Create a Service Account

1. In the left menu: **IAM & Admin → Service Accounts**
2. Click **Create Service Account**
3. Name: `openclaw-sheets` (or anything)
4. Click **Create and Continue** → skip the role step → click **Done**

---

## Step 4 — Download the JSON key

1. Click the service account you just created
2. Go to the **Keys** tab
3. Click **Add Key → Create new key**
4. Choose **JSON** → click **Create**
5. A JSON file downloads. **Rename it to:**
   ```
   google_service_account.json
   ```
6. Move it to:
   ```
   C:\Users\GilesHugo\.openclaw\Google_Service_Account.json
   ```

---

## Step 5 — Share the Google Sheet with the service account

1. Open the JSON file with Notepad
2. Find the line that says `"client_email"` — copy that email address
   (it looks like: `openclaw-sheets@your-project.iam.gserviceaccount.com`)
3. Open the Google Sheet:
   https://docs.google.com/spreadsheets/d/1fvoSKnvH0vdM_aZbo2_seY6AU3SZaXABaxRUKSxWVlo/
4. Click **Share** (top right)
5. Paste the service account email → set role to **Editor** → click **Send**

---

## Step 6 — Test it

Run:
```
"C:\Users\GilesHugo\.openclaw\venv\Scripts\python.exe" "C:\Users\GilesHugo\.openclaw\workspace\skills\purchasing\04_sheet_writer.py"
```

If set up correctly, it will write any pending approval items to the sheet.
If credentials are missing or wrong, it will print a clear error message.

---

## Sheet structure (set up automatically on first run)

**Tab: Pending** — items awaiting your review

| Column | Content |
|--------|---------|
| A | Email ID (system key) |
| B | Received date |
| C | Email subject |
| D | Sender domain |
| E | Document type (quote / invoice / dispatch_note / etc.) |
| F | PO Number |
| G | Matched Asana task name |
| H | Asana task link |
| I | Proposed action |
| J | Issues flagged |
| **K** | **YOUR DECISION** — type A, M, or R |
| L | Amendment notes (fill this if K = M) |
| M | Processed at (filled automatically) |

**Decision codes:**
- `A` = **Approve** — execute proposed action as written
- `M` = **Amend** — use your text in column L instead
- `R` = **Reject** — do nothing, just archive

**Tab: Processed** — archived rows after the system acts on them

---

## How the loop works

1. **Every hour (9–16 Mon–Fri):** pipeline runs, new emails appear in Pending tab
2. **You review** the Pending tab and fill in column K (+ column L if amending)
3. **Every 30 min (9–17 Mon–Fri):** the approver script reads your decisions,
   creates action files for SAP, and moves rows to Processed tab
4. **SAP** picks up the action files at its 08:30 heartbeat and updates Asana
