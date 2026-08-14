# =============================================================
# config-loader.ps1 — Load invoice processor configuration
# =============================================================
# Source this from other scripts:
#   . .\config-loader.ps1
# =============================================================

$script:Config = @{}

# --- Asana ---
$Config.ASANA_PAT         = "***REMOVED***"
$Config.ASANA_PROJECT_ID  = "902269769783650"
$Config.ASANA_WORKSPACE_ID = "357939803556123"
$Config.ASANA_API_BASE    = "https://app.asana.com/api/1.0"

# --- Email ---
$Config.MAIL_FOLDER_INBOX  = "INVOICES TO PROCESS"
$Config.MAIL_FOLDER_ARCHIVE = "INVOICES TO PROCESS/Invoice Archive"
$Config.GRAPH_TENANT_ID    = "3bfd9e7d-219f-48db-bd9e-e63b3b990a1a"
$Config.GRAPH_CLIENT_ID    = "a7bd53ba-a635-4b2a-bc08-7ae7b1a3ec17"
$Config.GRAPH_CLIENT_SECRET = "***REMOVED***"
$Config.GRAPH_USER_EMAIL   = "purchasing@sesameaccess.com"
$Config.GRAPH_API_BASE     = "https://graph.microsoft.com/v1.0"

# --- PO Regex ---
$Config.PO_PATTERN = "^[0-9]{5}-[0-9]{4,6}"

# --- Custom Field GIDs ---
$Config.FIELD_SIGNED_OFF         = "887966711229634"
$Config.FIELD_ORDER_STATUS       = "1143955334207040"
$Config.FIELD_INVOICE_NUMBER     = "855654251787627"
$Config.FIELD_INVOICE_VALUE_VAT  = "1204759087326205"
$Config.FIELD_PRICE_EX_VAT       = "854330340727040"
$Config.FIELD_CARRIAGE_EX_VAT    = "861798159849607"
$Config.FIELD_VENDOR             = "854127216887083"

# --- Sign-off value ---
$Config.SIGNED_OFF_ENUM_GID = "1215830009352317"

# --- ORDER Status "ready" GID ---
$Config.STATUS_READY_FOR_INVOICE_GID = "1149418840110590"

# --- Paths ---
$Config.AUDIT_DB_PATH   = "$PSScriptRoot\..\.cowork\audit\invoice-audit-database.json"
$Config.TEMP_DIR        = "$PSScriptRoot\..\.cowork\tmp\invoice-attachments"
$Config.LOG_DIR         = "$PSScriptRoot\..\.cowork\tmp\logs"

# Ensure directories exist
if (-not (Test-Path $Config.TEMP_DIR)) { New-Item -ItemType Directory -Path $Config.TEMP_DIR -Force | Out-Null }
if (-not (Test-Path $Config.LOG_DIR))  { New-Item -ItemType Directory -Path $Config.LOG_DIR -Force  | Out-Null }

Write-Host "[OK] Config loaded. Inbox: $($Config.MAIL_FOLDER_INBOX)" -ForegroundColor Green
