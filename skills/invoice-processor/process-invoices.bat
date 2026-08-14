@echo off
REM ==============================================================
REM Invoice Processing Pipeline — Windows Batch Launcher
REM ==============================================================
REM Double-click this file or run from command prompt.
REM ==============================================================

echo ========================================
echo  Invoice Processor — Windows Launcher
echo ========================================
echo.

REM Set working directory to script location
cd /d "%~dp0"

REM Run the PowerShell orchestrator
powershell.exe -ExecutionPolicy Bypass -File "%~dp0process-invoices.ps1"

REM Pause so output is visible when double-clicked
echo.
echo Press any key to exit...
pause >nul
