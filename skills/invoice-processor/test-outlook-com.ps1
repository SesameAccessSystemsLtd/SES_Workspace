<#
.SYNOPSIS
    Test Outlook COM access for the Invoice Processing workflow.
    Run as Administrator. No special characters / emoji used.
#>

Write-Host "=== Outlook COM Access Test ===" -ForegroundColor Cyan
Write-Host ("Testing: " + (Get-Date))
Write-Host ""

try {
    # Step 1: Create Outlook COM object
    Write-Host "Creating Outlook.Application object..." -NoNewline
    $outlook = New-Object -ComObject Outlook.Application
    Write-Host " OK" -ForegroundColor Green
    
    # Step 2: Get MAPI namespace
    Write-Host "Connecting to MAPI namespace..." -NoNewline
    $namespace = $outlook.GetNamespace("MAPI")
    Write-Host " OK" -ForegroundColor Green
    
    # Step 3: List all mail accounts/stores
    Write-Host ""
    Write-Host "Mail Accounts / Stores:" -ForegroundColor Yellow
    foreach ($store in $namespace.Folders) {
        Write-Host (" [F] " + $store.Name) -ForegroundColor White
        
        # List subfolders 1 level deep
        foreach ($sub in $store.Folders) {
            $unread = ""
            if ($sub.UnReadItemCount -gt 0) { $unread = (" [UNREAD: " + $sub.UnReadItemCount + "]") }
            Write-Host ("   [S] " + $sub.Name + $unread) -ForegroundColor Gray
        }
    }
    
    # Step 4: Search for "INVOICES TO PROCESS"
    Write-Host ""
    Write-Host "Searching for 'INVOICES TO PROCESS'..." -ForegroundColor Yellow
    $found = $null
    
    function Search-Folders {
        param($folderObj, $depth = 0)
        foreach ($f in $folderObj.Folders) {
            if ($f.Name -like "*INVOICES*") { return $f }
            if ($depth -lt 3) {
                $r = Search-Folders -folderObj $f -depth ($depth + 1)
                if ($r -ne $null) { return $r }
            }
        }
        return $null
    }
    
    foreach ($store in $namespace.Folders) {
        $found = Search-Folders $store
        if ($found -ne $null) { break }
    }
    
    if ($found) {
        Write-Host (" FOUND: " + $found.FolderPath) -ForegroundColor Green
        Write-Host (" Items: " + $found.Items.Count + " | Unread: " + $found.UnReadItemCount) -ForegroundColor White
        
        # Show first 3 items
        $count = 0
        foreach ($item in $found.Items) {
            if ($count -ge 3) { break }
            $attCount = $item.Attachments.Count
            $attInfo = ""
            if ($attCount -gt 0) {
                $attNames = @()
                foreach ($att in $item.Attachments) { $attNames += ($att.FileName + " (" + $att.Size + " bytes)") }
                $attInfo = (" | Attachments: " + ($attNames -join ", "))
            }
            Write-Host (" " + $item.Subject + $attInfo) -ForegroundColor White
            $count++
        }
    } else {
        Write-Host " Not found. Checking Inbox subfolders instead..." -ForegroundColor Yellow
        $inbox = $namespace.GetDefaultFolder(6)
        foreach ($f in $inbox.Folders) {
            Write-Host ("  " + $f.Name) -ForegroundColor Gray
        }
    }
    
    Write-Host ""
    Write-Host "=== COM Test Complete ===" -ForegroundColor Green
    
} catch {
    Write-Host ""
    Write-Host "COM Access FAILED" -ForegroundColor Red
    Write-Host ("Error: " + $_.Exception.Message) -ForegroundColor Red
    Write-Host ""
    Write-Host "Possible reasons:" -ForegroundColor Yellow
    Write-Host " 1. Outlook is not installed" -ForegroundColor Gray
    Write-Host " 2. Outlook is not currently running" -ForegroundColor Gray
    Write-Host " 3. PowerShell execution policy blocks COM" -ForegroundColor Gray
    Write-Host " 4. 32-bit vs 64-bit mismatch" -ForegroundColor Gray
    Write-Host ""
    Write-Host "=== COM Test Failed ===" -ForegroundColor Red
}

Write-Host ""
Write-Host "Press Enter to exit..."
Read-Host
