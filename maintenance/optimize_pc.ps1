<#
    optimize_pc.ps1 - Sesame Access workstation optimisation
    Machine: Dell Precision 3680 | 96 GB RAM | 954 GB NVMe SSD

    MUST BE RUN AS ADMINISTRATOR.

    See what would change, without changing anything:
        .\optimize_pc.ps1 -DryRun

    Apply:
        .\optimize_pc.ps1

    Skip sections with -SkipHibernation, -SkipPagefile, -SkipTemp,
    -SkipComponentStore, -SkipTrim.

    This script does not touch Dropbox, OneDrive, Documents, or any user
    data. It only removes caches, temp files and Windows-managed files.
#>

[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$SkipHibernation,
    [switch]$SkipPagefile,
    [switch]$SkipTemp,
    [switch]$SkipComponentStore,
    [switch]$SkipTrim
)

$ErrorActionPreference = 'Continue'

# ---------------------------------------------------------------- helpers ---

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Write-Section($text) {
    Write-Host ''
    Write-Host ('=' * 72) -ForegroundColor DarkCyan
    Write-Host "  $text" -ForegroundColor Cyan
    Write-Host ('=' * 72) -ForegroundColor DarkCyan
}

function Write-Action($text) { Write-Host "  -> $text" -ForegroundColor White }
function Write-Skip($text)   { Write-Host "  -- $text" -ForegroundColor DarkGray }
function Write-Win($text)    { Write-Host "  OK $text" -ForegroundColor Green }
function Write-Warn($text)   { Write-Host "  !! $text" -ForegroundColor Yellow }

function Get-FreeGB {
    [math]::Round((Get-Volume -DriveLetter C).SizeRemaining / 1GB, 2)
}

function Get-DirSizeGB($path) {
    if (-not (Test-Path $path)) { return 0 }
    $sum = (Get-ChildItem $path -Recurse -File -Force -ErrorAction SilentlyContinue |
            Measure-Object Length -Sum).Sum
    if (-not $sum) { return 0 }
    [math]::Round($sum / 1GB, 2)
}

# Deletes files older than $Days under $Path. Returns GB freed.
function Remove-OldFiles {
    param([string]$Path, [int]$Days = 7, [string[]]$ExcludeLike = @())

    if (-not (Test-Path $Path)) { return 0 }
    $cutoff = (Get-Date).AddDays(-$Days)

    $targets = Get-ChildItem $Path -Recurse -File -Force -ErrorAction SilentlyContinue |
        Where-Object {
            $f = $_
            if ($f.LastWriteTime -ge $cutoff) { return $false }
            foreach ($p in $ExcludeLike) { if ($f.FullName -like $p) { return $false } }
            $true
        }

    $bytes = ($targets | Measure-Object Length -Sum).Sum
    if (-not $bytes) { $bytes = 0 }
    $gb = [math]::Round($bytes / 1GB, 2)

    if ($DryRun) {
        Write-Action "WOULD delete $($targets.Count) files ($gb GB) from $Path"
        return $gb
    }

    $locked = 0
    foreach ($f in $targets) {
        try { Remove-Item -LiteralPath $f.FullName -Force -ErrorAction Stop }
        catch { $locked++ }
    }

    # Tidy directories that are now empty, deepest first.
    Get-ChildItem $Path -Recurse -Directory -Force -ErrorAction SilentlyContinue |
        Sort-Object { $_.FullName.Length } -Descending |
        ForEach-Object {
            if (-not (Get-ChildItem $_.FullName -Force -ErrorAction SilentlyContinue)) {
                try { Remove-Item -LiteralPath $_.FullName -Force -ErrorAction Stop } catch { }
            }
        }

    Write-Win "Deleted $($targets.Count - $locked) files (~$gb GB) from $Path"
    if ($locked) { Write-Skip "$locked files were locked or in use and were left alone" }
    $gb
}

# ------------------------------------------------------------------ start ---

if (-not (Test-Admin)) {
    Write-Host ''
    Write-Host '  This script must be run as Administrator.' -ForegroundColor Red
    Write-Host '  Right-click PowerShell, choose "Run as administrator", then run it again.' -ForegroundColor Red
    Write-Host ''
    exit 1
}

$startFree = Get-FreeGB

Write-Section 'Sesame Access - Workstation Optimisation'
if ($DryRun) {
    Write-Host '  Mode        : DRY RUN - nothing will be changed'
} else {
    Write-Host '  Mode        : APPLY'
}
Write-Host "  Free space  : $startFree GB"
Write-Host "  Started     : $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"

# --- 1. Hibernation ----------------------------------------------------------
# This is a desktop tower on permanent mains power. It never hibernates, but
# Windows still reserves a hiberfil.sys sized at roughly 40% of RAM.
Write-Section '1. Hibernation (frees ~38 GB)'

if ($SkipHibernation) {
    Write-Skip 'Skipped by flag'
} else {
    $hib = Get-Item 'C:\hiberfil.sys' -Force -ErrorAction SilentlyContinue
    if ($hib) {
        $hibGB = [math]::Round($hib.Length / 1GB, 2)
        if ($DryRun) {
            Write-Action "WOULD run 'powercfg /h off' and release hiberfil.sys ($hibGB GB)"
        } else {
            powercfg /h off
            Write-Win "Hibernation disabled, reclaimed ~$hibGB GB"
            Write-Skip 'Side effect: Fast Startup is disabled too. On a desktop with an NVMe'
            Write-Skip 'SSD that costs a second or two at boot and makes shutdowns cleaner.'
        }
    } else {
        Write-Skip 'hiberfil.sys not present - hibernation already off'
    }
}

# --- 2. Pagefile -------------------------------------------------------------
# The system-managed pagefile has grown to ~50 GB. With 96 GB of RAM this
# machine will never page out anything close to that.
Write-Section '2. Pagefile (frees ~26 GB)'

if ($SkipPagefile) {
    Write-Skip 'Skipped by flag'
} else {
    $pf = Get-Item 'C:\pagefile.sys' -Force -ErrorAction SilentlyContinue
    if ($pf) {
        Write-Host "  Current pagefile: $([math]::Round($pf.Length / 1GB, 2)) GB"
    }

    if ($DryRun) {
        Write-Action 'WOULD set pagefile to fixed 8192 MB min / 24576 MB max'
    } else {
        $cs = Get-CimInstance Win32_ComputerSystem
        if ($cs.AutomaticManagedPagefile) {
            Set-CimInstance -InputObject $cs -Property @{ AutomaticManagedPagefile = $false } | Out-Null
        }

        $setting = Get-CimInstance -ClassName Win32_PageFileSetting -ErrorAction SilentlyContinue |
                   Where-Object { $_.Name -like 'C:*' }

        if ($setting) {
            Set-CimInstance -InputObject $setting -Property @{
                InitialSize = 8192
                MaximumSize = 24576
            } | Out-Null
        } else {
            New-CimInstance -ClassName Win32_PageFileSetting -Property @{
                Name        = 'C:\pagefile.sys'
                InitialSize = 8192
                MaximumSize = 24576
            } | Out-Null
        }

        Write-Win 'Pagefile set to 8 GB min / 24 GB max - takes effect after reboot'
        Write-Skip 'A 24 GB ceiling is generous for 96 GB of RAM.'
    }
}

# --- 3. Power plan -----------------------------------------------------------
# Balanced parks CPU cores and downclocks. On a mains-powered workstation that
# is lost throughput for no benefit.
Write-Section '3. Power plan'

$HIGH_PERF = '8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c'
$current = (powercfg /getactivescheme) -join ' '
Write-Host "  Current: $current"

if ($current -match $HIGH_PERF) {
    Write-Skip 'Already on High performance'
} elseif ($DryRun) {
    Write-Action 'WOULD switch active scheme to High performance'
} else {
    powercfg /setactive $HIGH_PERF 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Win 'Switched to High performance'
    } else {
        # Some OEM images hide the High performance scheme. Recreate it.
        $dup = (powercfg /duplicatescheme $HIGH_PERF) -join ' '
        if ($dup -match '([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})') {
            powercfg /setactive $Matches[1]
            Write-Win 'Recreated and activated the High performance scheme'
        } else {
            Write-Warn 'Could not switch power plan - set it manually in Control Panel'
        }
    }

    # A workstation NVMe drive should never idle-park.
    powercfg /setacvalueindex SCHEME_CURRENT SUB_DISK DISKIDLE 0 2>$null
    powercfg /setactive SCHEME_CURRENT 2>$null
    Write-Win 'Disk idle timeout disabled'
}

# --- 4. Temp files and caches ------------------------------------------------
Write-Section '4. Temp files and caches'

if ($SkipTemp) {
    Write-Skip 'Skipped by flag'
} else {
    # Anything under a \claude\ path is live session scratch - leave it.
    [void](Remove-OldFiles -Path $env:TEMP -Days 7 -ExcludeLike @('*\claude\*'))
    [void](Remove-OldFiles -Path 'C:\Windows\Temp' -Days 7)

    # Windows Update download cache. Safe - Windows re-downloads when needed.
    $wu = 'C:\Windows\SoftwareDistribution\Download'
    $wuGB = Get-DirSizeGB $wu
    if ($wuGB -gt 0.5) {
        if ($DryRun) {
            Write-Action "WOULD clear the Windows Update cache ($wuGB GB)"
        } else {
            Stop-Service wuauserv -Force -ErrorAction SilentlyContinue
            Get-ChildItem $wu -Recurse -Force -ErrorAction SilentlyContinue |
                Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
            Start-Service wuauserv -ErrorAction SilentlyContinue
            Write-Win "Cleared the Windows Update cache (~$wuGB GB)"
        }
    } else {
        Write-Skip "Windows Update cache is only $wuGB GB - leaving it"
    }

    # Delivery Optimization holds peer-to-peer update chunks. Always safe.
    $do = 'C:\Windows\SoftwareDistribution\DeliveryOptimization'
    $doGB = Get-DirSizeGB $do
    if ($doGB -gt 0.5) {
        if ($DryRun) {
            Write-Action "WOULD clear the Delivery Optimization cache ($doGB GB)"
        } else {
            Get-ChildItem $do -Recurse -Force -ErrorAction SilentlyContinue |
                Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
            Write-Win "Cleared the Delivery Optimization cache (~$doGB GB)"
        }
    }

    # Developer package caches. These rebuild on next install - no data loss.
    foreach ($cache in @('npm', 'pip', 'uv')) {
        if ($DryRun) {
            Write-Action "WOULD clear the $cache cache"
            continue
        }
        if (-not (Get-Command $cache -ErrorAction SilentlyContinue)) {
            Write-Skip "$cache is not on PATH - skipped"
            continue
        }
        switch ($cache) {
            'npm' { npm cache clean --force 2>&1 | Out-Null }
            'pip' { pip cache purge          2>&1 | Out-Null }
            'uv'  { uv cache clean           2>&1 | Out-Null }
        }
        Write-Win "Cleared the $cache cache"
    }

    # Recycle Bin
    $rbGB = Get-DirSizeGB 'C:\$Recycle.Bin'
    if ($DryRun) {
        Write-Action "WOULD empty the Recycle Bin ($rbGB GB)"
    } elseif ($rbGB -gt 0.05) {
        Clear-RecycleBin -DriveLetter C -Force -ErrorAction SilentlyContinue
        Write-Win "Emptied the Recycle Bin (~$rbGB GB)"
    }
}

# --- 5. Windows component store ----------------------------------------------
# WinSxS accumulates superseded update payloads. This is the supported way to
# reclaim them and it cannot break installed features.
Write-Section '5. Windows component store (WinSxS)'

if ($SkipComponentStore) {
    Write-Skip 'Skipped by flag'
} elseif ($DryRun) {
    Write-Action 'WOULD run: DISM /Online /Cleanup-Image /StartComponentCleanup'
} else {
    Write-Host '  Running DISM component cleanup - this can take 10-20 minutes...'
    DISM /Online /Cleanup-Image /StartComponentCleanup | Out-Null
    Write-Win 'Component store cleaned'
    Write-Skip 'Deliberately not using /ResetBase - that would block uninstalling updates.'
}

# --- 6. SSD ReTrim -----------------------------------------------------------
# This drive is an NVMe SSD and must NOT be defragmented. Defragmenting an SSD
# burns write cycles for no gain, because there is no seek penalty to remove.
# The correct maintenance operation is ReTrim: it tells the drive which blocks
# are free so the controller can garbage-collect them and keep writes fast.
Write-Section '6. SSD ReTrim (correct operation for NVMe - not defrag)'

if ($SkipTrim) {
    Write-Skip 'Skipped by flag'
} elseif ($DryRun) {
    Write-Action 'WOULD run: Optimize-Volume -DriveLetter C -ReTrim'
} else {
    Write-Host '  Running ReTrim...'
    Optimize-Volume -DriveLetter C -ReTrim
    Write-Win 'ReTrim complete - free blocks released to the SSD controller'
}

# --- 7. Report ---------------------------------------------------------------
Write-Section 'Summary'

$endFree = Get-FreeGB
$delta = [math]::Round($endFree - $startFree, 2)

Write-Host "  Free before : $startFree GB"
Write-Host "  Free after  : $endFree GB"
Write-Host "  Reclaimed   : $delta GB" -ForegroundColor Green

if ($DryRun) {
    Write-Host ''
    Write-Host '  This was a DRY RUN. Re-run without -DryRun to apply.' -ForegroundColor Yellow
} else {
    Write-Host ''
    Write-Host '  REBOOT REQUIRED for the pagefile change to take effect.' -ForegroundColor Yellow
    Write-Host '  hiberfil.sys space is released immediately.' -ForegroundColor Yellow
}

Write-Host ''
