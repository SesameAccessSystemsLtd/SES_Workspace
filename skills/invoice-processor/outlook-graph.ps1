# ==============================================================
# Outlook Email Reader — Microsoft Graph API
# ==============================================================
# . .\invoice-processor\config.ps1         # first
# . .\invoice-processor\outlook-graph.ps1
# ==============================================================
# Depends on: config.ps1 (GRAPH_TENANT_ID, GRAPH_CLIENT_ID, etc.)

function Get-GraphAccessToken {
    $body = @{
        client_id = $global:GRAPH_CLIENT_ID
        client_secret = $global:GRAPH_CLIENT_SECRET
        scope = "https://graph.microsoft.com/.default"
        grant_type = "client_credentials"
    }
    $url = "https://login.microsoftonline.com/$global:GRAPH_TENANT_ID/oauth2/v2.0/token"
    try {
        $response = Invoke-RestMethod -Uri $url -Method Post -Body $body -ErrorAction Stop
        return $response.access_token
    } catch {
        Write-Error "Graph token acquisition failed: $($_.Exception.Message)"
        return $null
    }
}

function Invoke-GraphGet {
    param([string]$Endpoint)
    $token = Get-GraphAccessToken
    if (-not $token) { return $null }

    $url = "https://graph.microsoft.com/v1.0/$Endpoint"
    $headers = @{
        "Authorization" = "Bearer $token"
        "Accept" = "application/json"
    }
    try {
        $response = Invoke-RestMethod -Uri $url -Headers $headers -Method Get -ErrorAction Stop
        return $response
    } catch {
        Write-Warning "Graph GET $Endpoint failed: $($_.Exception.Message)"
        return $null
    }
}

# Find folder by display name (recursive through mailbox)
function Find-GraphFolder {
    param([string]$FolderName)
    $response = Invoke-GraphGet "users/$global:GRAPH_USER_EMAIL/mailFolders"
    if (-not $response -or -not $response.value) { return $null }

    # Search top-level and children
    function Search-FoldersRecursive {
        param([array]$Folders, [string]$Target)
        foreach ($f in $Folders) {
            if ($f.displayName -eq $Target) {
                return $f
            }
            if ($f.childFolderCount -gt 0) {
                $children = Invoke-GraphGet "users/$global:GRAPH_USER_EMAIL/mailFolders/$($f.id)/childFolders"
                if ($children -and $children.value) {
                    $found = Search-FoldersRecursive $children.value $Target
                    if ($found) { return $found }
                }
            }
        }
        return $null
    }

    return Search-FoldersRecursive $response.value $FolderName
}

# Move email to archive folder
function Move-GraphEmail {
    param([string]$MessageId, [string]$DestinationFolderId)
    $token = Get-GraphAccessToken
    if (-not $token) { return $false }

    $url = "https://graph.microsoft.com/v1.0/users/$global:GRAPH_USER_EMAIL/messages/$MessageId/move"
    $headers = @{
        "Authorization" = "Bearer $token"
        "Accept" = "application/json"
        "Content-Type" = "application/json"
    }
    $body = @{ destinationId = $DestinationFolderId } | ConvertTo-Json

    try {
        $null = Invoke-RestMethod -Uri $url -Headers $headers -Method Post -Body $body -ErrorAction Stop
        return $true
    } catch {
        Write-Warning "Move email failed: $($_.Exception.Message)"
        return $false
    }
}

# List messages in a folder (unread only, top 10)
function Get-GraphFolderMessages {
    param([string]$FolderId, [int]$Top = 10)
    $response = Invoke-GraphGet "users/$global:GRAPH_USER_EMAIL/mailFolders/$FolderId/messages?`$filter=isRead eq false&`$top=$Top&`$expand=attachments(`$top=5)"
    if ($response -and $response.value) {
        return $response.value
    }
    return @()
}

# Download a specific attachment by message ID and attachment ID
function Save-GraphAttachment {
    param([string]$MessageId, [string]$AttachmentId, [string]$OutputPath)
    $token = Get-GraphAccessToken
    if (-not $token) { return $false }

    $url = "https://graph.microsoft.com/v1.0/users/$global:GRAPH_USER_EMAIL/messages/$MessageId/attachments/$AttachmentId"
    $headers = @{
        "Authorization" = "Bearer $token"
        "Accept" = "application/json"
    }
    try {
        $response = Invoke-RestMethod -Uri $url -Headers $headers -Method Get -ErrorAction Stop
        if ($response.'@odata.type' -eq "#microsoft.graph.fileAttachment" -and $response.contentBytes) {
            $bytes = [Convert]::FromBase64String($response.contentBytes)
            [System.IO.File]::WriteAllBytes($OutputPath, $bytes)
            return $true
        }
        return $false
    } catch {
        Write-Warning "Download attachment failed: $($_.Exception.Message)"
        return $false
    }
}

Write-Host "[OK] Outlook Graph helper loaded." -ForegroundColor Green
