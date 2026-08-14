<#
.SYNOPSIS
    Invoice Processing Pipeline — Main Orchestrator
.DESCRIPTION
    1. Read unread emails from "INVOICES TO PROCESS" via Graph API
    2. Download PDF attachments
    3. Extract PO from email subject
    4. Find matching Asana task
    5. Check ORDER Status (delivered?)
    6. Compare invoice value to task value
    7. Sign off in Asana + log to audit database
    8. Move email to "Invoice Archive"
.NOTES
    Run: powershell -ExecutionPolicy Bypass -File invoice-processor/process-invoices.ps1
#>

# Bootstrap
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $scriptDir "config.ps1")
. (Join-Path $scriptDir "asana-api.ps1")
. (Join-Path $scriptDir "outlook-graph.ps1")
. (Join-Path $scriptDir "pdf-extract.ps1")

# --- Logging ---
$logDir = Join-Path $scriptDir "logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
$logFile = Join-Path $logDir ("run-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".log")
function Write-Log {
    param([string]$Message, [string]$Level = "INFO")
    $line = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] [$Level] $Message"
    Write-Host $line
    Add-Content -Path $logFile -Value $line
}

# --- Audit Database ---
function Read-AuditDb {
    if (Test-Path $global:AUDIT_DB_PATH) {
        return Get-Content $global:AUDIT_DB_PATH -Raw | ConvertFrom-Json
    }
    return $null
}

function Write-AuditDb {
    param([object]$Data)
    $json = $Data | ConvertTo-Json -Depth 10
    Set-Content -Path $global:AUDIT_DB_PATH -Value $json
}

function Append-AuditEntry {
    param([hashtable]$Entry)
    $db = Read-AuditDb
    if (-not $db) {
        Write-Log "Audit DB not found at $global:AUDIT_DB_PATH" "ERROR"
        return $false
    }
    $entryObj = New-Object PSObject -Property $Entry
    $db.tasks += $entryObj
    Write-AuditDb $db
    Write-Log "Audit entry appended for PO: $($Entry.po_number)" "OK"
    return $true
}

# --- Temp directory for attachments ---
$tmpDir = Join-Path $scriptDir "tmp"
if (-not (Test-Path $tmpDir)) { New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null }

# ============================================================
# MAIN PIPELINE
# ============================================================
Write-Log "=== Invoice Processing Pipeline Started ==="

# 1. Find the inbox folder
Write-Log "Looking for folder: $global:MAIL_FOLDER_INBOX"
$inboxFolder = Find-GraphFolder -FolderName $global:MAIL_FOLDER_INBOX
if (-not $inboxFolder) {
    Write-Log "Folder '$global:MAIL_FOLDER_INBOX' not found in mailbox." "ERROR"
    exit 1
}
Write-Log "Found folder: $($inboxFolder.displayName) (ID: $($inboxFolder.id))" "OK"

# Find the archive folder
$archiveFolder = Find-GraphFolder -FolderName $global:MAIL_FOLDER_ARCHIVE
if (-not $archiveFolder) {
    Write-Log "Archive folder '$global:MAIL_FOLDER_ARCHIVE' not found. Will create if needed." "WARN"
}

# 2. Get unread messages
$messages = Get-GraphFolderMessages -FolderId $inboxFolder.id -Top 10
if ($messages.Count -eq 0) {
    Write-Log "No unread emails in folder. Nothing to process."
    Write-Log "=== Pipeline Complete (Idle) ==="
    exit 0
}
Write-Log "Found $($messages.Count) unread email(s) to process."

# 3. Process each email
$processedCount = 0
$flagCount = 0

foreach ($msg in $messages) {
    Write-Log "--- Processing: $($msg.subject) (ID: $($msg.id)) ---"
    Write-Host "  From: $($msg.from.emailAddress.address)" -ForegroundColor Gray
    Write-Host "  Received: $($msg.receivedDateTime)" -ForegroundColor Gray

    $result = @{
        email_subject = $msg.subject
        email_from = $msg.from.emailAddress.address
        email_received = $msg.receivedDateTime
        processed_at = (Get-Date -Format "o")
    }

    # 3a. Extract PO number from subject
    $poMatch = [regex]::Match($msg.subject, $global:PO_PATTERN)
    if (-not $poMatch.Success) {
        Write-Log "NO PO NUMBER found in subject. FLAGGING for manual review." "FLAG"
        $result.status = "FLAGGED"
        $result.flag_reason = "No PO number in subject: '$($msg.subject)'"
        $flagCount++
        Append-AuditEntry $result
        continue
    }
    $poNumber = $poMatch.Value
    Write-Log "PO Number extracted: $poNumber" "OK"
    $result.po_number = $poNumber

    # 3b. Download PDF attachment(s)
    $attachmentSaved = $false
    $pdfPath = $null
    if ($msg.attachments -and $msg.attachments.Count -gt 0) {
        foreach ($att in $msg.attachments) {
            if ($att.name -match '\.pdf$' -or $att.contentType -eq 'application/pdf') {
                $pdfPath = Join-Path $tmpDir ("$poNumber-" + $att.name)
                $saved = Save-GraphAttachment -MessageId $msg.id -AttachmentId $att.id -OutputPath $pdfPath
                if ($saved) {
                    Write-Log "Attachment saved: $pdfPath" "OK"
                    $attachmentSaved = $true
                    break
                } else {
                    Write-Log "Failed to download attachment: $($att.name)" "WARN"
                }
            }
        }
    }

    # 3c. Parse invoice from PDF (or email body as fallback)
    $invoiceNumber = $null
    $invoiceValue = $null

    if ($attachmentSaved -and $pdfPath -and (Test-Path $pdfPath)) {
        $parsed = Parse-InvoicePdf $pdfPath
        $invoiceNumber = $parsed.invoice_number
        $invoiceValue = $parsed.invoice_value
    }

    # Fallback: try extracting from email body
    if (-not $invoiceNumber -and $msg.body -and $msg.body.content) {
        $bodyText = $msg.body.content
        # Simple regex on body for invoice number
        $bodyMatch = [regex]::Match($bodyText, '(?:Invoice|INVOICE)\s*(?:No|#|Number)?\s*[:.\-#]?\s*([A-Z0-9][A-Z0-9/\-]+)', [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
        if ($bodyMatch.Success) { $invoiceNumber = $bodyMatch.Groups[1].Value }

        $valMatch = [regex]::Match($bodyText, '(?:Total|Amount|Value)\s*(?:due|£|:)?\s*(?:£|GBP)?\s*([\d,]+\.\d{2})', [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
        if ($valMatch.Success) { $invoiceValue = [decimal]($valMatch.Groups[1].Value -replace ',', '') }
    }

    $result.invoice_number = $invoiceNumber
    $result.invoice_value = $invoiceValue
    Write-Log "Invoice#: $invoiceNumber | Value: £$invoiceValue"

    # 3d. Find Asana task by PO
    $task = Find-AsanaTaskByPO -PoNumber $poNumber
    if (-not $task) {
        Write-Log "No Asana task found matching PO: $poNumber. FLAGGING." "FLAG"
        $result.status = "FLAGGED"
        $result.flag_reason = "No Asana task for PO: $poNumber"
        $result.task_gid = $null
        $flagCount++
        Append-AuditEntry $result
        continue
    }
    Write-Log "Asana task found: $($task.name) (GID: $($task.gid))" "OK"
    $result.task_gid = $task.gid
    $result.task_name = $task.name

    # 3e. Check ORDER Status
    $orderStatus = Get-CustomFieldValue -Task $task -FieldGid $global:FIELD_ORDER_STATUS
    $statusName = if ($orderStatus -and $orderStatus.name) { $orderStatus.name } else { "unknown" }
    $statusGid = if ($orderStatus -and $orderStatus.gid) { $orderStatus.gid } else { "" }

    Write-Log "ORDER Status: $statusName (GID: $statusGid)"

    if ($statusGid -eq $global:STATUS_READY_FOR_INVOICE_GID) {
        Write-Log "ORDER Status = 'ARRIVED - MOVES TO INVOICES' -- Proceeding." "OK"
    } elseif ($statusName -like "*PARTIAL*") {
        Write-Log "ORDER Status = PARTIAL DELIVERY. FLAGGING for manual review." "FLAG"
        $result.status = "FLAGGED"
        $result.flag_reason = "Partial delivery - manual check needed"
        $result.order_status = $statusName
        $flagCount++
        Append-AuditEntry $result
        continue
    } elseif ($statusName -like "*RETURN*" -or $statusName -like "*CANCEL*") {
        Write-Log "ORDER Status = $statusName. FLAGGING (order issue)." "FLAG"
        $result.status = "FLAGGED"
        $result.flag_reason = "Order issue: $statusName"
        $result.order_status = $statusName
        $flagCount++
        Append-AuditEntry $result
        continue
    } else {
        Write-Log "ORDER Status = $statusName -- not ready for invoicing. FLAGGING." "FLAG"
        $result.status = "FLAGGED"
        $result.flag_reason = "Not yet delivered / ready for invoicing (status: $statusName)"
        $result.order_status = $statusName
        $flagCount++
        Append-AuditEntry $result
        continue
    }

    $result.order_status = $statusName

    # 3f. Compare invoice value to task value
    $taskValue = Get-CustomFieldValue -Task $task -FieldGid $global:FIELD_INVOICE_VALUE_VAT
    if ($taskValue -is [PSCustomObject] -and $taskValue.number_value -ne $null) {
        $taskValue = $taskValue.number_value
    }

    Write-Log "Invoice value from email: £$invoiceValue | Asana task value: £$taskValue"

    if ($invoiceValue -ne $null -and $taskValue -ne $null -and $taskValue -gt 0) {
        $tolerance = 0.02  # allow 2p rounding difference
        if ([Math]::Abs($invoiceValue - $taskValue) -gt $tolerance) {
            Write-Log "VALUE MISMATCH: email=£$invoiceValue vs Asana=£$taskValue. FLAGGING." "FLAG"
            $result.status = "FLAGGED"
            $result.flag_reason = "Value mismatch: email=£$invoiceValue, Asana=£$taskValue"
            $result.task_value = $taskValue
            $flagCount++
            Append-AuditEntry $result
            continue
        }
        Write-Log "Values match within tolerance. Proceeding." "OK"
    } elseif ($invoiceValue -ne $null -and $taskValue -eq $null) {
        Write-Log "Asana task has no Invoice Value set. Using email value £$invoiceValue as reference." "WARN"
    } elseif ($invoiceValue -eq $null) {
        Write-Log "Could not extract invoice value from email/PDF. Will still sign off with available data." "WARN"
    }

    # 3g. SIGN OFF — update Asana
    Write-Log "SIGNING OFF: Setting Signed OFF = 'Signed off by AI - AWAITING APPROVAL'" "ACTION"

    # Update Signed OFF field
    $signOffResult = Set-AsanaCustomField -TaskGid $task.gid -FieldGid $global:FIELD_SIGNED_OFF -Value $global:SIGNED_OFF_ENUM_GID
    if ($signOffResult) {
        Write-Log "Signed OFF field updated successfully." "OK"
    } else {
        Write-Log "Failed to update Signed OFF field." "ERROR"
    }

    # Update Invoice Number field
    if ($invoiceNumber) {
        $invResult = Set-AsanaCustomField -TaskGid $task.gid -FieldGid $global:FIELD_INVOICE_NUMBER -Value $invoiceNumber
        if ($invResult) {
            Write-Log "Invoice Number field updated to: $invoiceNumber" "OK"
        } else {
            Write-Log "Failed to update Invoice Number field." "WARN"
        }
    }

    # Update task name: append invoice number
    $newName = "$($task.name) - $invoiceNumber"
    $nameResult = Set-AsanaTaskName -TaskGid $task.gid -NewName $newName
    if ($nameResult) {
        Write-Log "Task name updated to: $newName" "OK"
    } else {
        Write-Log "Failed to update task name." "WARN"
    }

    # 3h. Log to audit database
    $result.status = "SIGNED_OFF_AWAITING_APPROVAL"
    $result.task_value = $taskValue
    $result.invoice_number_set = $invoiceNumber
    Append-AuditEntry $result

    # 3i. Move email to archive
    if ($archiveFolder) {
        $moved = Move-GraphEmail -MessageId $msg.id -DestinationFolderId $archiveFolder.id
        if ($moved) {
            Write-Log "Email moved to archive: $global:MAIL_FOLDER_ARCHIVE" "OK"
        } else {
            Write-Log "Failed to move email to archive." "WARN"
        }
    }

    # 3j. Clean up temp PDF
    if ($pdfPath -and (Test-Path $pdfPath)) {
        Remove-Item $pdfPath -Force
        Write-Log "Temp PDF cleaned up." "OK"
    }

    $processedCount++
    Write-Log "--- Email processed successfully ---"
}

# --- Summary ---
Write-Log "=== Pipeline Complete ==="
Write-Log "Processed (signed off): $processedCount"
Write-Log "Flagged for review: $flagCount"
Write-Log "Total emails found: $($messages.Count)"
Write-Log "Log saved to: $logFile"

# Output summary to console
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Invoice Processing Pipeline Complete" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Signed Off (awaiting approval): $processedCount" -ForegroundColor Green
Write-Host "  Flagged for manual review:       $flagCount" -ForegroundColor Yellow
Write-Host "  Total emails in folder:          $($messages.Count)" -ForegroundColor White
Write-Host "  Log: $logFile" -ForegroundColor Gray
Write-Host "========================================" -ForegroundColor Cyan
