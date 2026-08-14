# ==============================================================
# Invoice Processor — PowerShell Configuration Loader
# ==============================================================
# Sources config.sh and exposes variables to PowerShell.
# Run before any other script:
#   . .\invoice-processor\config.ps1
# ==============================================================

function Load-InvoiceConfig {
    $configPath = Join-Path $PSScriptRoot "..\.cowork\secrets\config.sh"
    if (-not (Test-Path $configPath)) {
        Write-Error "Config not found at: $configPath"
        exit 1
    }

    $content = Get-Content $configPath -Raw
    $global:ASANA_PAT = if ($content -match 'export ASANA_PAT="(.+?)"') { $Matches[1] } else { "" }
    $global:ASANA_PROJECT_ID = if ($content -match 'export ASANA_PROJECT_ID="(.+?)"') { $Matches[1] } else { "" }
    $global:ASANA_WORKSPACE_ID = if ($content -match 'export ASANA_WORKSPACE_ID="(.+?)"') { $Matches[1] } else { "" }

    $global:MAIL_FOLDER_INBOX = if ($content -match 'export MAIL_FOLDER_INBOX="(.+?)"') { $Matches[1] } else { "INVOICES TO PROCESS" }
    $global:MAIL_FOLDER_ARCHIVE = if ($content -match 'export MAIL_FOLDER_ARCHIVE="(.+?)"') { $Matches[1] } else { "INVOICES TO PROCESS/Invoice Archive" }

    $global:PO_PATTERN = if ($content -match 'export PO_PATTERN="(.+?)"') { $Matches[1] } else { "^[0-9]{5}-[0-9]{4,6}" }

    $global:SIGNED_OFF_ENUM_GID = if ($content -match 'export SIGNED_OFF_ENUM_GID="(.+?)"') { $Matches[1] } else { "" }
    $global:FIELD_SIGNED_OFF = if ($content -match 'export FIELD_SIGNED_OFF="(.+?)"') { $Matches[1] } else { "" }
    $global:FIELD_ORDER_STATUS = if ($content -match 'export FIELD_ORDER_STATUS="(.+?)"') { $Matches[1] } else { "" }
    $global:FIELD_INVOICE_NUMBER = if ($content -match 'export FIELD_INVOICE_NUMBER="(.+?)"') { $Matches[1] } else { "" }
    $global:FIELD_INVOICE_VALUE_VAT = if ($content -match 'export FIELD_INVOICE_VALUE_VAT="(.+?)"') { $Matches[1] } else { "" }
    $global:FIELD_VENDOR = if ($content -match 'export FIELD_VENDOR="(.+?)"') { $Matches[1] } else { "" }

    $global:STATUS_READY_FOR_INVOICE_GID = if ($content -match 'export STATUS_READY_FOR_INVOICE_GID="(.+?)"') { $Matches[1] } else { "" }

    $global:GRAPH_TENANT_ID = if ($content -match 'export GRAPH_TENANT_ID="(.+?)"') { $Matches[1] } else { "" }
    $global:GRAPH_CLIENT_ID = if ($content -match 'export GRAPH_CLIENT_ID="(.+?)"') { $Matches[1] } else { "" }
    $global:GRAPH_CLIENT_SECRET = if ($content -match 'export GRAPH_CLIENT_SECRET="(.+?)"') { $Matches[1] } else { "" }
    $global:GRAPH_USER_EMAIL = if ($content -match 'export GRAPH_USER_EMAIL="(.+?)"') { $Matches[1] } else { "" }

    $global:AUDIT_DB_PATH = Join-Path (Join-Path $PSScriptRoot "..\.cowork\audit") "invoice-audit-database.json"

    Write-Host "[OK] Invoice processor config loaded." -ForegroundColor Green
}

Load-InvoiceConfig
