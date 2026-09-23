# DNS Option A' - set STATIC DNS on Ethernet: router primary, Cloudflare fallback
$ErrorActionPreference = 'Continue'
$log = "C:\Users\GilesHugo\SES_Workspace\queues\DEV_OPEN\_dns_fix_Aprime.log"
Start-Transcript -Path $log -Force | Out-Null

Write-Output "### BEFORE ###"
Get-DnsClientServerAddress -InterfaceAlias "Ethernet" -AddressFamily IPv4 |
    Select-Object InterfaceAlias,ServerAddresses | Format-List

Write-Output "### APPLYING: static DNS 192.168.0.1 then 1.1.1.1 ###"
try {
    Set-DnsClientServerAddress -InterfaceAlias "Ethernet" -ServerAddresses ("192.168.0.1","1.1.1.1") -ErrorAction Stop
    Write-Output "Set-DnsClientServerAddress: OK"
} catch {
    Write-Output "FAILED: $($_.Exception.Message)"
}

Write-Output "### FLUSHING DNS CACHE ###"
Clear-DnsClientCache
ipconfig /flushdns | Out-Null
Start-Sleep -Seconds 2

Write-Output "### AFTER ###"
Get-DnsClientServerAddress -InterfaceAlias "Ethernet" -AddressFamily IPv4 |
    Select-Object InterfaceAlias,ServerAddresses | Format-List

Write-Output "### RESOLUTION TEST after change ###"
foreach($h in @('outlook.office.com','login.microsoftonline.com','www.google.com')){
  try {
    $ips = (Resolve-DnsName $h -Type A -EA Stop | Where-Object {$_.IPAddress} | Select-Object -First 3 -ExpandProperty IPAddress)
    Write-Output ("  {0,-30} -> {1}" -f $h, ($ips -join ', '))
  } catch { Write-Output ("  {0,-30} -> FAILED: {1}" -f $h, $_.Exception.Message) }
}

Write-Output "### INTEGRITY CHECKS ###"
Write-Output "-- Local gateway reachable --"
$t = Test-Connection 192.168.0.1 -Count 2 -Quiet -EA SilentlyContinue
Write-Output ("  ping 192.168.0.1 : " + $t)

Write-Output "-- Tailscale routes --"
Get-NetRoute -AddressFamily IPv4 -EA SilentlyContinue |
    Where-Object { $_.InterfaceAlias -match 'Tailscale' } |
    Select-Object DestinationPrefix,NextHop | Format-Table -Auto

Write-Output "-- Default route --"
Get-NetRoute -AddressFamily IPv4 -EA SilentlyContinue |
    Where-Object { $_.DestinationPrefix -eq '0.0.0.0/0' } |
    Select-Object DestinationPrefix,NextHop,InterfaceAlias | Format-Table -Auto

Write-Output "-- Dropbox procs --"
Write-Output ("Dropbox = " + @(Get-Process Dropbox -EA SilentlyContinue).Count)

Write-Output "-- Tailscale VPS reachable (100.103.163.5) --"
$tv = Test-Connection 100.103.163.5 -Count 2 -Quiet -EA SilentlyContinue
Write-Output ("  ping 100.103.163.5 : " + $tv)

Write-Output "### DONE ###"
Stop-Transcript | Out-Null
