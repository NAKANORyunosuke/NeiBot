# NginxAutoBan-Advanced.ps1  (ASCII only / safe quotes)
# Purpose:
#   - Tail nginx access.log on Windows and auto-BAN abusive clients.
#   - Escalate to /24 CIDR ban when many distinct IPs within a short window.
#   - Progressive ban durations for recidivists.
#   - Optional fallback to nginx "deny map" when firewall rule count grows.
# Notes:
#   - Run in elevated PowerShell.
#   - Save file as UTF-8 with CRLF.

param(
  [switch]$Uninstall,
  [switch]$WhatIf
)

# ===================== User Settings =====================
# Nginx access.log
$logPath            = "C:\Users\Administrator\Desktop\nginx-1.28.0\logs\access.log"

# Whitelist/Blacklist store
$whitelistFile      = "C:\security\whitelist.txt"
$blacklistStoreJson = "C:\security\autoban_blacklist.json"   # IP/CIDR -> expiry (ISO)

# Malicious URI patterns (regex fragments)
$badUriPatterns = @(
  "wp-login","wp-admin","/vendor/phpunit/","/HNAP1","/adminer","/phpmyadmin",
  "/\.env","/\.git","auto_prepend_file","\.\./","%2e%2e%2f",
  "UNION\s+SELECT","information_schema","xp_cmdshell","eval\(","%00"
)

# Per-IP malicious hit threshold (URI-based)
$badHitThreshold    = 3

# 404 burst ban settings
$enable404BurstBan  = $true
$burstWindowSeconds = 60
$burst404Threshold  = 20

# ---- NEW: Rate-limit BAN (e.g., 3 requests within 1 second) ----
$enableRateBan      = $true
$rateWindowSeconds  = 1
$rateThreshold      = 3

# Recidivism-based ban durations (days -> seconds)
$banDurations       = @(1,7,30,90) | ForEach-Object { $_ * 24 * 60 * 60 }

# /24 escalation window and threshold
$subnetEscalationWindowSec = 600  # seconds
$subnetDistinctIPThreshold = 5    # distinct IPs within window
$subnetBanDays             = 7
$subnetBanSeconds          = $subnetBanDays * 24 * 60 * 60

# Whitelist reload interval
$whitelistReloadSec = 60

# Firewall rule prefix
$rulePrefix       = "NginxAutoBan"

# Firewall rule soft limit (fallback to nginx map)
$maxFirewallRules = 1200

# Optional: nginx deny map fallback
$enableNginxMapFallback = $true
$nginxDenyMapPath = "C:\Users\Administrator\Desktop\nginx-1.28.0\conf\deny_ips.map"
$nginxExePath     = "C:\Users\Administrator\Desktop\nginx-1.28.0\nginx.exe"

# ========================================================

# ===================== Internal State ===================
# Common log format regex
$rxLog = [regex]'^(?<ip>\d{1,3}(?:\.\d{1,3}){3})\s+\S+\s+\S+\s+\[[^\]]+\]\s+"(?<method>GET|POST|HEAD|PUT|DELETE|OPTIONS|PATCH)\s+(?<uri>\S+)\s+[^"]+"\s+(?<status>\d{3})\s+'
$rxBadList = $badUriPatterns | ForEach-Object { [regex]::new($_, 'IgnoreCase') }

# Per-IP counters / buffers
$badHits       = @{} # ip -> int (malicious URI count)
$hits404       = @{} # ip -> List[datetime]
$hitsAll       = @{} # ip -> List[datetime] (rate-limit window)
$recidivism    = @{} # ip -> int (how many times banned)

# /24 tracking
$subnetIPs     = @{} # cidr24 -> HashSet[string] (distinct IPs)
$subnetLastSeen= @{} # cidr24 -> datetime

# Whitelist / Blacklist
$whitelist     = @()
$blacklist     = @{} # key(IP or CIDR) -> expiry(datetime)

$lastWhitelistReload = Get-Date
$subnetWindow = [TimeSpan]::FromSeconds($subnetEscalationWindowSec)

# ===================== Functions ========================
function Load-Whitelist {
  param([string]$Path)
  if (Test-Path $Path) {
    Get-Content $Path | Where-Object { $_ -match '\S' -and -not $_.Trim().StartsWith("#") }
  } else { @() }
}

function Load-BlacklistStore {
  param([string]$Path)
  if (Test-Path $Path) {
    try {
      $raw = Get-Content $Path -Raw | ConvertFrom-Json
      $map = @{}
      foreach ($item in $raw) { $map[$item.Key] = [datetime]$item.ExpiresAt }
      return $map
    } catch { @{} }
  } else { @{} }
}

function Save-BlacklistStore {
  param([string]$Path, [hashtable]$Map)
  $arr = @()
  foreach ($kv in $Map.GetEnumerator()) {
    $arr += [pscustomobject]@{ Key = $kv.Key; ExpiresAt = $kv.Value.ToString("o") }
  }
  $arr | ConvertTo-Json -Depth 3 | Set-Content -Path $Path -Encoding UTF8
}

function Test-IpInCidr { param([string]$IP, [string]$CIDR)
  $parts = $CIDR.Split("/")
  if ($parts.Count -ne 2) { return $false }
  $baseIP = $parts[0]; $maskLen = [int]$parts[1]
  $ipBytes   = [System.Net.IPAddress]::Parse($IP).GetAddressBytes()
  $baseBytes = [System.Net.IPAddress]::Parse($baseIP).GetAddressBytes()
  $ipVal   = [System.BitConverter]::ToUInt32(($ipBytes[3],$ipBytes[2],$ipBytes[1],$ipBytes[0]),0)
  $baseVal = [System.BitConverter]::ToUInt32(($baseBytes[3],$baseBytes[2],$baseBytes[1],$baseBytes[0]),0)
  $mask = if ($maskLen -eq 0) { 0 } else { ([uint32]::MaxValue) -shl (32 - $maskLen) }
  return (($ipVal -band $mask) -eq ($baseVal -band $mask))
}

function Is-Whitelisted { param([string]$ip)
  foreach ($w in $whitelist) {
    if ($w -match "/") { if (Test-IpInCidr -IP $ip -CIDR $w) { return $true } }
    else { if ($ip -eq $w) { return $true } }
  }
  return $false
}

function Get-Cidr24 { param([string]$ip)
  $parts = $ip.Split("."); if ($parts.Count -lt 4) { return $null }
  '{0}.{1}.{2}.0/24' -f $parts[0],$parts[1],$parts[2]
}

function Ensure-NginxDenyMapEntry { param([string]$cidr)
  if (-not $enableNginxMapFallback) { return }
  if (-not (Test-Path $nginxDenyMapPath)) {
    "# ip/cidr   1;" | Set-Content -Path $nginxDenyMapPath -Encoding UTF8
  }
  $pattern = ('^\Q{0}\E\s+1;' -f $cidr)
  $exists  = Select-String -Path $nginxDenyMapPath -Pattern $pattern -AllMatches
  if (-not $exists) {
    Add-Content -Path $nginxDenyMapPath -Value ('{0}  1;' -f $cidr)
    if (-not $WhatIf) { & $nginxExePath -s reload | Out-Null }
  }
}

function Count-OurFirewallRules {
  (Get-NetFirewallRule -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -like ('{0} *' -f $rulePrefix) }).Count
}

function Add-FirewallBan {
  param([string]$key, [int]$seconds, [switch]$IsSubnet)
  if ($WhatIf) { Write-Host ('[WhatIf] BAN: {0} ({1}s)' -f $key,$seconds); return }

  $expires = (Get-Date).AddSeconds($seconds)
  $blacklist[$key] = $expires
  Save-BlacklistStore -Path $blacklistStoreJson -Map $blacklist

  $curRules = Count-OurFirewallRules
  if ($curRules -ge $maxFirewallRules) {
    if ($enableNginxMapFallback) {
      Ensure-NginxDenyMapEntry -cidr $key
      Write-Host ('[Fallback] nginx deny map: {0}' -f $key)
      return
    }
  }

  $ruleName = '{0} {1}' -f $rulePrefix, $key
  if (-not (Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Block -RemoteAddress $key | Out-Null
    Write-Host ('[FW] BAN: {0} until {1:u}' -f $key, $expires)
  }
}

function Cleanup-ExpiredBans {
  $now = Get-Date
  $toRemove = @()
  foreach ($kv in $blacklist.GetEnumerator()) {
    if ($kv.Value -lt $now) { $toRemove += $kv.Key }
  }
  foreach ($key in $toRemove) {
    if ($WhatIf) { Write-Host ('[WhatIf] Unban: {0}' -f $key) }
    else {
      $ruleName = '{0} {1}' -f $rulePrefix, $key
      $rule = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
      if ($rule) { Remove-NetFirewallRule -DisplayName $ruleName }
      $blacklist.Remove($key) | Out-Null
      Save-BlacklistStore -Path $blacklistStoreJson -Map $blacklist
      Write-Host ('[FW] Unban: {0}' -f $key)
    }
  }
}

function Uninstall-AllBans {
  Get-NetFirewallRule -DisplayName ('{0} *' -f $rulePrefix) -ErrorAction SilentlyContinue | ForEach-Object {
    Remove-NetFirewallRule -Name $_.Name
  }
  if (Test-Path $blacklistStoreJson) { Remove-Item $blacklistStoreJson -Force }
  Write-Host ('[FW] Removed all rules with prefix: {0}' -f $rulePrefix)
}

function Parse-LogLine { param([string]$line)
  $m = $rxLog.Match($line)
  if (-not $m.Success) { return $null }
  [pscustomobject]@{
    IP     = $m.Groups['ip'].Value
    Method = $m.Groups['method'].Value
    URI    = $m.Groups['uri'].Value
    Status = [int]$m.Groups['status'].Value
  }
}

function Register-SubnetHit { param([string]$ip)
  $cidr24 = Get-Cidr24 -ip $ip
  if (-not $cidr24) { return }
  if (-not $subnetIPs.ContainsKey($cidr24)) {
    $subnetIPs[$cidr24] = New-Object System.Collections.Generic.HashSet[string]
  }
  $subnetIPs[$cidr24].Add($ip) | Out-Null
  $subnetLastSeen[$cidr24] = Get-Date

  $now = Get-Date
  foreach ($k in @($subnetLastSeen.Keys)) {
    if (($now - $subnetLastSeen[$k]) -gt $subnetWindow) {
      $subnetIPs.Remove($k) | Out-Null
      $subnetLastSeen.Remove($k) | Out-Null
    }
  }

  if ($subnetIPs[$cidr24].Count -ge $subnetDistinctIPThreshold) {
    if (-not $blacklist.ContainsKey($cidr24)) {
      Add-FirewallBan -key $cidr24 -seconds $subnetBanSeconds -IsSubnet
      $subnetIPs.Remove($cidr24) | Out-Null
      $subnetLastSeen.Remove($cidr24) | Out-Null
    }
  }
}

# ---- NEW: Rate-limit BAN ----
function Register-RateHitAndMaybeBan { param([string]$ip)
  if (-not $enableRateBan) { return }

  if (-not $hitsAll.ContainsKey($ip)) {
    $hitsAll[$ip] = New-Object System.Collections.Generic.List[datetime]
  }
  $lst = $hitsAll[$ip]
  $now = Get-Date
  $lst.Add($now)

  # drop old entries outside the window
  while ($lst.Count -gt 0 -and ($now - $lst[0]).TotalSeconds -gt $rateWindowSeconds) {
    $lst.RemoveAt(0)
  }

  if ($lst.Count -ge $rateThreshold) {
    # already banned? skip
    foreach ($k in $blacklist.Keys) {
      if ($k -match "/") { if (Test-IpInCidr -IP $ip -CIDR $k) { return } }
      elseif ($k -eq $ip) { return }
    }

    $count = ($recidivism[$ip] | ForEach-Object { $_ }); if (-not $count) { $count = 0 }
    $idx = [Math]::Min($count, $banDurations.Count - 1)
    Add-FirewallBan -key $ip -seconds $banDurations[$idx]
    $recidivism[$ip] = $count + 1
    Register-SubnetHit -ip $ip
    $hitsAll.Remove($ip) | Out-Null
  }
}

function Register-OffenseAndMaybeBan {
  param($e)

  $ip = $e.IP
  if (Is-Whitelisted $ip) { return }

  # Rate-limit BAN check (first)
  Register-RateHitAndMaybeBan -ip $ip

  # If already banned (IP/CIDR), skip further checks
  foreach ($k in $blacklist.Keys) {
    if ($k -match "/") { if (Test-IpInCidr -IP $ip -CIDR $k) { return } }
    elseif ($k -eq $ip) { return }
  }

  # Malicious URI threshold
  $matchedBad = $false
  foreach ($rx in $rxBadList) { if ($rx.IsMatch($e.URI)) { $matchedBad = $true; break } }
  if ($matchedBad) {
    if (-not $badHits.ContainsKey($ip)) { $badHits[$ip] = 0 }
    $badHits[$ip]++
    if ($badHits[$ip] -ge $badHitThreshold) {
      $count = ($recidivism[$ip] | ForEach-Object { $_ }); if (-not $count) { $count = 0 }
      $idx = [Math]::Min($count, $banDurations.Count - 1)
      Add-FirewallBan -key $ip -seconds $banDurations[$idx]
      $recidivism[$ip] = $count + 1
      $badHits.Remove($ip) | Out-Null
      Register-SubnetHit -ip $ip
    }
  }

  # 404 burst threshold
  if ($enable404BurstBan -and $e.Status -eq 404) {
    if (-not $hits404.ContainsKey($ip)) {
      $hits404[$ip] = New-Object System.Collections.Generic.List[datetime]
    }
    $lst = $hits404[$ip]
    $now = Get-Date
    $lst.Add($now)
    while ($lst.Count -gt 0 -and ($now - $lst[0]).TotalSeconds -gt $burstWindowSeconds) {
      $lst.RemoveAt(0)
    }
    if ($lst.Count -ge $burst404Threshold) {
      $count = ($recidivism[$ip] | ForEach-Object { $_ }); if (-not $count) { $count = 0 }
      $idx = [Math]::Min($count, $banDurations.Count - 1)
      Add-FirewallBan -key $ip -seconds $banDurations[$idx]
      $recidivism[$ip] = $count + 1
      $hits404.Remove($ip) | Out-Null
      Register-SubnetHit -ip $ip
    }
  }
}

# ===================== Main ==============================
if ($Uninstall) { Uninstall-AllBans; exit 0 }

Write-Host ('[Start] NginxAutoBan: {0}' -f $logPath)
if (-not (Test-Path $logPath)) { Write-Error ('Access log not found: {0}' -f $logPath); exit 1 }

# Initial load
$whitelist = Load-Whitelist -Path $whitelistFile
$blacklist = Load-BlacklistStore -Path $blacklistStoreJson

# Restore existing firewall rules for known blacklist
foreach ($k in $blacklist.Keys) {
  $ruleName = '{0} {1}' -f $rulePrefix, $k
  if (-not (Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue)) {
    if (-not $WhatIf) {
      New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Block -RemoteAddress $k | Out-Null
    } else {
      Write-Host ('[WhatIf] restore: {0}' -f $k)
    }
  }
}

# Tail -f (start from EOF)
Get-Content -Path $logPath -Tail 0 -Wait -Encoding UTF8 | ForEach-Object {
  if ((Get-Date) - $lastWhitelistReload -gt [TimeSpan]::FromSeconds($whitelistReloadSec)) {
    $whitelist = Load-Whitelist -Path $whitelistFile
    $lastWhitelistReload = Get-Date
    Cleanup-ExpiredBans
  }

  $e = Parse-LogLine -line $_
  if ($null -ne $e) { Register-OffenseAndMaybeBan -e $e }
}
