<#
.SYNOPSIS
    Dry-run mode for the Invoice Processing Pipeline
.DESCRIPTION
    Walks through each step WITHOUT making any changes to Asana
    or moving emails. Reports what WOULD happen for each email.
    Safe to run against live data.
#>

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $scriptDir "config.ps1")
. (Join-Path $scriptDir "asana-api.ps1")
. (Join-Path $scriptDir "outlook-graph.ps1")
. (Join-Path $scriptDir "pdf-extract.ps1")

$dryRunLog = Join-Path $scriptDir "logs" ("dryrun-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".log")

function Write-Dry {
    param([string]$Message, [string]$Level = "INFO")
    $line = "[$(Get-Date -Format 'HH:mm:ss')] [$Level] $Message"
    Write-Host $line
    Add-Content -Path $dryRunLog -Value $line
}

$tmpDir = Join-Path $scriptDir "tmp"
if (-not (Test-Path $tmpDir)) { New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null }

Write-Host ""
Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  INVOICE PROCESSOR — DRY RUN MODE" -ForegroundColor Cyan
Write-Host "  No changes will be made to Asana or Outlook" -ForegroundColor Yellow
Write-Host "================================================" -ForegroundColor Cyan
Write-Host ""

# Step 1: Check config loaded
Write-Dry "Config loaded. Asana PAT present: $($global:ASANA_PAT.Length -gt 0)" "CONFIG"
Write-Dry "Project ID: $global:ASANA_PROJECT_ID" "CONFIG"
Write-Dry "Folder inbox: $global:MAIL_FOLDER_INBOX" "CONFIG"
Write-Dry "Folder archive: $global:MAIL_FOLDER_ARCHIVE" "CONFIG"

# Step 2: Test Asana connection
Write-Dry "" "SEPARATOR"
Write-Dry "--- Step 1: Testing Asana Connection ---" "STEP"
$asanaTest = Invoke-AsanaGet "projects/$global:ASANA_PROJECT_ID"
if ($asanaTest -and $asanaTest.data) {
    Write-Dry "Asana connected: Project '$($asanaTest.data.name)'" "OK"
} else {
    Write-Dry "Asana connection FAILED. Check your PAT." "ERROR"
    exit 1
}

# Step 3: Test Graph API connection
Write-Dry "" "SEPARATOR"
Write-Dry "--- Step 2: Testing Graph API Connection ---" "STEP"
$token = Get-GraphAccessToken
if ($token) {
    Write-Dry "Graph API token acquired successfully (first 20 chars: $($token.Substring(0,20))...)" "OK"
} else {
    Write-Dry "Graph API connection FAILED." "ERROR"
    exit 1
}

# Step 4: Find folders
Write-Dry "" "SEPARATOR"
Write-Dry "--- Step 3: Finding Folders ---" "STEP"
$inboxFolder = Find-GraphFolder -FolderName $global:MAIL_FOLDER_INBOX
if ($inboxFolder) {
    Write-Dry "Found inbox folder: $($inboxFolder.displayName) (ID: $($inboxFolder.id))" "OK"
} else {
    Write-Dry "Folder '$global:MAIL_FOLDER_INBOX' NOT FOUND in mailbox." "ERROR"
    Write-Dry "Check that the folder exists in your Outlook mailbox." "HINT"
}

$archiveFolder = Find-GraphFolder -FolderName $global:MAIL_FOLDER_ARCHIVE
if ($archiveFolder) {
    Write-Dry "Found archive folder: $($archiveFolder.displayName) (ID: $($archiveFolder.id))" "OK"
} else {
    Write-Dry "Archive folder '$global:MAIL_FOLDER_ARCHIVE' NOT FOUND." "WARN"
    Write-Dry "Will need to create this folder before live runs (create it in Outlook)." "HINT"
}

# Step 5: List unread emails (no actions taken)
Write-Dry "" "SEPARATOR"
Write-Dry "--- Step 4: Listing Unread Emails ---" "STEP"
if ($inboxFolder) {
    $messages = Get-GraphFolderMessages -FolderId $inboxFolder.id -Top 5
    if ($messages.Count -eq 0) {
        Write-Dry "No unread emails found in '$global:MAIL_FOLDER_INBOX'." "OK"
        Write-Dry "Dry-run complete — folder is empty, nothing to process." "DONE"
    } else {
        Write-Dry "Found $($messages.Count) unread email(s):" "OK"
        foreach ($msg in $messages) {
            Write-Host "  [EMAIL] $($msg.subject)" -ForegroundColor White
            Write-Dry "  From: $($msg.from.emailAddress.address) | Received: $($msg.receivedDateTime)" "EMAIL"

            # Extract PO from subject (read-only)
            $poMatch = [regex]::Match($msg.subject, $global:PO_PATTERN)
            if ($poMatch.Success) {
                Write-Dry "  PO detected: $($poMatch.Value)" "MATCH"

                # Find Asana task (read-only)
                $task = Find-AsanaTaskByPO -PoNumber $poMatch.Value
                if ($task) {
                    $orderStatus = Get-CustomFieldValue -Task $task -FieldGid $global:FIELD_ORDER_STATUS
                    $statusName = if ($orderStatus -and $orderStatus.name) { $orderStatus.name } else { "unknown" }
                    Write-Dry "  Asana task found: $($task.name) | ORDER Status: $statusName" "MATCH"

                    $taskValue = Get-CustomFieldValue -Task $task -FieldGid $global:FIELD_INVOICE_VALUE_VAT
                    if ($taskValue -is [PSCustomObject] -and $taskValue.number_value -ne $null) {
                        $taskValue = $taskValue.number_value
                    }
                    Write-Dry "  Asana Invoice Value (inc VAT): £$taskValue" "VALUE"

                    # Check attachments
                    if ($msg.attachments -and $msg.attachments.Count -gt 0) {
                        Write-Dry "  Attachments: $($msg.attachments.Count) file(s)" "ATTACH"
                        foreach ($att in $msg.attachments) {
                            $isPdf = if ($att.name -match '\.pdf$') { "PDF" } else { "other" }
                            Write-Dry "    - $($att.name) ($isPdf)" "FILE"
                        }
                    } else {
                        Write-Dry "  No attachments found." "ATTACH"
                    }

                    # Show what WOULD happen
                    if ($statusName -like "*ARRIVED*INVOICES*") {
                        Write-Dry "  [DECISION] Would sign off: Delivered + matching." "GREEN"
                    } else {
                        Write-Dry "  [DECISION] Would FLAG: Status = '$statusName'" "FLAG"
                    }

                } else {
                    Write-Dry "  No Asana task found for PO: $($poMatch.Value)" "FLAG"
                }
            } else {
                Write-Dry "  No PO number found in subject — would flag for manual review." "FLAG"
            }
            Write-Dry "" "SEPARATOR"
        }
    }
}

# Summary
Write-Dry "" "SEPARATOR"
Write-Dry "--- DRY RUN SUMMARY ---" "DONE"
Write-Dry "Asana connection: OK" "RESULT"
Write-Dry "Graph API connection: OK" "RESULT"
Write-Dry "Folders found: inbox=$(if($inboxFolder){'YES'}else{'NO'}) archive=$(if($archiveFolder){'YES'}else{'NO'})" "RESULT"
Write-Dry "Dry-run log: $dryRunLog" "RESULT"

Write-Host ""
Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  DRY RUN COMPLETE — No changes made" -ForegroundColor Green
Write-Host "================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Log saved to: $dryRunLog" -ForegroundColor Gray
