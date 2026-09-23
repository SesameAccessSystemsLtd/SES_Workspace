# DNS Option A - reset Ethernet DNS to DHCP-provided resolvers
$ErrorActionPreference = 'Continue'
$log = "C:\Users\GilesHugo\SES_Workspace\queues\DEV_OPEN\_dns_fix_A.log"
Start-Transcript -Path $log -Force | Out-Null

Write-Output "### BEFORE ###"
Get-DnsClientServerAddress -InterfaceAlias "Ethernet" -AddressFamily IPv4 |
    Select-Object InterfaceAlias,ServerAddresses | Format-List

Write-Output "### APPLYING: ResetServerAddresses ###"
try {
    Set-DnsClientServerAddress -InterfaceAlias "Ethernet" -ResetServerAddresses -ErrorAction Stop
    Write-Output "Set-DnsClientServerAddress: OK"
} catch {
    Write-Output "Set-DnsClientServerAddress FAILED: $($_.Exception.Message)"
}

Write-Output "### FLUSHING DNS CACHE ###"
try { Clear-DnsClientCache; ipconfig /flushdns | Out-Null; Write-Output "Cache cleared" }
catch { Write-Output "Flush FAILED: $($_.Exception.Message)" }

Start-Sleep -Seconds 3

Write-Output "### AFTER ###"
Get-DnsClientServerAddress -InterfaceAlias "Ethernet" -AddressFamily IPv4 |
    Select-Object InterfaceAlias,ServerAddresses | Format-List

Write-Output "### INTEGRITY CHECKS ###"
Write-Output "-- Tailscale routes --"
Get-NetRoute -AddressFamily IPv4 -EA SilentlyContinue |
    Where-Object { $_.InterfaceAlias -match 'Tailscale' } |
    Select-Object DestinationPrefix,NextHop | Format-Table -Auto

Write-Output "-- Default route --"
Get-NetRoute -AddressFamily IPv4 -EA SilentlyContinue |
    Where-Object { $_.DestinationPrefix -eq '0.0.0.0/0' } |
    Select-Object DestinationPrefix,NextHop,InterfaceAlias | Format-Table -Auto

Write-Output "-- Dropbox process count --"
$db = @(Get-Process Dropbox -EA SilentlyContinue)
Write-Output ("Dropbox procs = " + $db.Count)

Write-Output "-- Tailscale procs --"
Get-Process -EA SilentlyContinue | Where-Object { $_.Name -match 'tailscale' } |
    Select-Object Id,Name | Format-Table -Auto

Write-Output "### DONE ###"
Stop-Transcript | Out-Null
