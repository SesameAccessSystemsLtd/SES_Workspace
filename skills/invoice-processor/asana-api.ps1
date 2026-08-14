# ==============================================================
# Asana REST API Helper Functions
# ==============================================================
# . .\invoice-processor\config.ps1   # first
# . .\invoice-processor\asana-api.ps1
# ==============================================================

function Invoke-AsanaGet {
    param([string]$Endpoint)
    $url = "https://app.asana.com/api/1.0/$Endpoint"
    $headers = @{
        "Authorization" = "Bearer $global:ASANA_PAT"
        "Accept" = "application/json"
    }
    try {
        $response = Invoke-RestMethod -Uri $url -Headers $headers -Method Get -ErrorAction Stop
        return $response
    } catch {
        Write-Warning "Asana GET $Endpoint failed: $($_.Exception.Message)"
        return $null
    }
}

function Invoke-AsanaPut {
    param([string]$Endpoint, [object]$Body)
    $url = "https://app.asana.com/api/1.0/$Endpoint"
    $headers = @{
        "Authorization" = "Bearer $global:ASANA_PAT"
        "Accept" = "application/json"
        "Content-Type" = "application/json"
    }
    try {
        $jsonBody = $Body | ConvertTo-Json -Depth 5
        $response = Invoke-RestMethod -Uri $url -Headers $headers -Method Put -Body $jsonBody -ErrorAction Stop
        return $response
    } catch {
        Write-Warning "Asana PUT $Endpoint failed: $($_.Exception.Message)"
        return $null
    }
}

# List tasks in the project (incomplete only)
function Get-AsanaProjectTasks {
    param([int]$Limit = 100)
    $endpoint = "projects/$global:ASANA_PROJECT_ID/tasks?opt_fields=gid,name,custom_fields,completed&limit=$Limit&completed=false"
    # May need pagination; collect all pages
    $allTasks = @()
    $nextPage = $endpoint
    while ($nextPage) {
        $result = Invoke-AsanaGet $nextPage
        if ($result -and $result.data) {
            $allTasks += $result.data
            if ($result.next_page -and $result.next_page.uri) {
                $nextPage = $result.next_page.uri -replace "^.*/api/1.0/", ""
            } else {
                $nextPage = $null
            }
        } else {
            break
        }
    }
    return $allTasks
}

# Get a single task with custom fields
function Get-AsanaTask {
    param([string]$TaskGid)
    $endpoint = "tasks/$TaskGid?opt_fields=gid,name,completed,custom_fields,name"
    $result = Invoke-AsanaGet $endpoint
    if ($result) { return $result.data }
    return $null
}

# Find task by PO number (task name starts with PO)
function Find-AsanaTaskByPO {
    param([string]$PoNumber)
    $tasks = Get-AsanaProjectTasks
    if (-not $tasks) { return $null }
    foreach ($task in $tasks) {
        if ($task.name -match "^$PoNumber") {
            return $task
        }
    }
    return $null
}

# Extract a custom field value by field GID
function Get-CustomFieldValue {
    param([object]$Task, [string]$FieldGid)
    if (-not $Task.custom_fields) { return $null }
    foreach ($field in $Task.custom_fields) {
        if ($field.gid -eq $FieldGid) {
            # enum_value has gid + name
            if ($field.enum_value) { return $field.enum_value }
            # number value
            if ($field.number_value -ne $null) { return $field.number_value }
            # text value
            if ($field.text_value) { return $field.text_value }
            # display_value fallback
            return $field.display_value
        }
    }
    return $null
}

# Update a custom field on a task
function Set-AsanaCustomField {
    param([string]$TaskGid, [string]$FieldGid, $Value)
    $body = @{
        data = @{
            custom_fields = @{
                "$FieldGid" = $Value
            }
        }
    }
    return Invoke-AsanaPut "tasks/$TaskGid" $body
}

# Update task name (append invoice number)
function Set-AsanaTaskName {
    param([string]$TaskGid, [string]$NewName)
    $body = @{
        data = @{
            name = $NewName
        }
    }
    return Invoke-AsanaPut "tasks/$TaskGid" $body
}

Write-Host "[OK] Asana API helper loaded." -ForegroundColor Green
