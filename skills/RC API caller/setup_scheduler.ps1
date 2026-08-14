# setup_scheduler.ps1
# ====================
# Registers rc_call_notes.py as a Windows Scheduled Task running every hour.
# Run this script ONCE from PowerShell as Administrator.
#
# Usage:
#   Right-click PowerShell > "Run as Administrator"
#   cd to the folder containing this script, then:
#   .\setup_scheduler.ps1

$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonExe  = (Get-Command python).Source
$ScriptPath = Join-Path $ScriptDir "rc_call_notes.py"
$TaskName   = "RingCentral Call Notes Exporter"
$LogPath    = Join-Path $ScriptDir "scheduler.log"

if (-not (Test-Path $ScriptPath)) {
    Write-Error "rc_call_notes.py not found at $ScriptPath"
    exit 1
}

$Action  = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument "`"$ScriptPath`"" `
    -WorkingDirectory $ScriptDir

# Run every hour, starting from the next full hour
$Trigger = New-ScheduledTaskTrigger -RepetitionInterval (New-TimeSpan -Hours 1) `
    -Once -At (Get-Date -Minute 0 -Second 0).AddHours(1)

# Run whether or not the user is logged in, using the current user account
$Principal = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType S4U `
    -RunLevel Highest

$Settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -WakeToRun $false

# Remove existing task if present
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed existing task."
}

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Principal $Principal `
    -Settings $Settings `
    -Description "Fetches RingCentral AI call notes via ACE API and saves to local folder."

Write-Host ""
Write-Host "Scheduled task '$TaskName' registered successfully."
Write-Host "It will run every hour. To test immediately, run:"
Write-Host "  Start-ScheduledTask -TaskName '$TaskName'"
