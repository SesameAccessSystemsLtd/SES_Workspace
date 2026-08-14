# git_daily_backup.ps1 — Daily commit and push of .openclaw to GitHub
# Runs via Windows Task Scheduler at 11:30 PM daily.
# Repo: git@github.com:SesameAccessSystemsLtd/ComanderClaw.git

$ErrorActionPreference = "Stop"
$repoPath = "C:\Users\GilesHugo\.openclaw"
$logFile  = "$repoPath\logs\git_backup.log"

function Log($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "$ts  $msg" | Tee-Object -FilePath $logFile -Append
}

Set-Location $repoPath

$dateLabel = Get-Date -Format "yyyy-MM-dd"

# Stage everything tracked or new (gitignore excludes secrets, venv, db, sessions)
& git add -A
if ($LASTEXITCODE -ne 0) { Log "ERROR: git add failed ($LASTEXITCODE)"; exit 1 }

# Check if there's anything to commit
$status = & git status --porcelain
if (-not $status) {
    Log "No changes to commit — workspace up to date."
    exit 0
}

& git commit -m "Daily backup $dateLabel"
if ($LASTEXITCODE -ne 0) { Log "ERROR: git commit failed ($LASTEXITCODE)"; exit 1 }

& git push origin
if ($LASTEXITCODE -ne 0) { Log "ERROR: git push failed ($LASTEXITCODE)"; exit 1 }

Log "Backup complete — committed and pushed ($dateLabel)."
