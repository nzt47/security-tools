# SSH connection diagnostics (Windows native PowerShell version)
# Counterpart: scripts/dev/diagnose_ssh.sh (bash)
# Layered: DNS -> ping -> TCP port -> SSH banner -> SSH auth
# Each layer prints PASS/FAIL + suggestion; exit 0 = all ok, 1 = failure found.
#
# Usage (PowerShell 5.1 / 7+):
#   .\diagnose_ssh.ps1 -Target <ip|host> [-Port 22] [-User root] [-Key C:\Users\x\.ssh\id_rsa]
#   .\diagnose_ssh.ps1 -Target 10.0.0.1 -Port 2222 -User deploy -Key $env:USERPROFILE\.ssh\id_rsa
#
# Note: -Target is used instead of -Host because $Host is a reserved automatic variable.
param(
    [string]$Target = "",
    [int]$Port = 22,
    [string]$User = "",
    [string]$Key = ""
)

$failed = $false

function Write-Step([string]$title) { Write-Host "[$step/5] $title" }

if (-not $Target) { Write-Error "Need -Target <ip|host>"; exit 2 }

Write-Host "== SSH diagnostics: ${Target}:${Port} =="
Write-Host ""
$step = 0

# 1) DNS resolution
$step++
Write-Step "DNS resolution"
if ($Target -match '^\d+(\.\d+){3}$') {
    Write-Host "  [PASS] $Target is an IP (skip DNS)"
} else {
    try {
        $ip = [System.Net.Dns]::GetHostAddresses($Target) | Select-Object -First 1
        if ($ip) { Write-Host "  [PASS] $Target resolves to $($ip.IPAddressToString)" }
        else { Write-Host "  [FAIL] $Target did not resolve"; $failed = $true }
    } catch {
        Write-Host "  [FAIL] $Target did not resolve (typo / DNS config)"
        $failed = $true
    }
}

# 2) ping reachability
$step++
Write-Step "ping reachability"
if (Test-Connection -ComputerName $Target -Count 1 -Quiet -ErrorAction SilentlyContinue) {
    Write-Host "  [PASS] network reachable (ping ok)"
} else {
    Write-Host "  [FAIL] ping failed - unreachable or ICMP blocked"
    Write-Host "    hint: check route/VPN/firewall; port test below still meaningful"
}

# 3) TCP port
$step++
Write-Step "TCP port $Port"
Write-Host "  [log] target ${Target}:${Port}; method TcpClient.ConnectAsync; timeout 3s"
Write-Host "  [log] if stuck here: host unreachable / firewall / port not listening"
$tcp = New-Object System.Net.Sockets.TcpClient
try {
    $task = $tcp.ConnectAsync($Target, $Port)
    $task.Wait(3000) | Out-Null   # swallow AggregateException; verdict = Connected
} catch { }
if ($tcp.Connected) {
    Write-Host "  [PASS] TCP $Port is open"
} else {
    Write-Host "  [FAIL] TCP $Port unreachable (timeout/refused)"
    Write-Host "    hint: 1) open firewall/security group for $Port"
    Write-Host "          2) verify sshd listening: netstat -ano | findstr :$Port"
    Write-Host "          3) use -Port $Port to align non-standard port"
    $failed = $true
}
$tcp.Close()

# 4) SSH banner (SSH-2.0 per RFC 4253)
$step++
Write-Step "SSH service response"
Write-Host "  [log] read first response (banner) on ${Target}:${Port}; timeout 3s; expect prefix SSH-2.0"
Write-Host "  [log] if stuck here: non-SSH service on port / sshd not running / malformed banner"
$banner = ""
$client = New-Object System.Net.Sockets.TcpClient
try {
    $task = $client.ConnectAsync($Target, $Port)
    $task.Wait(3000) | Out-Null
    if ($client.Connected) {
        $stream = $client.GetStream()
        $stream.ReadTimeout = 3000
        $buf = New-Object byte[] 200
        $n = $stream.Read($buf, 0, 200)
        $banner = [System.Text.Encoding]::ASCII.GetString($buf, 0, $n)
    }
} catch { }
$client.Close()
if ($banner -match '^SSH-') {
    $first = ($banner -split "`r?`n")[0]
    $shown = $first.Substring(0, [Math]::Min(60, $first.Length))
    Write-Host "  [PASS] sshd responds: $shown"
} else {
    Write-Host "  [FAIL] no SSH banner on $Port (non-SSH service / sshd not running)"
    Write-Host "    hint: confirm port is SSH; service sshd status"
    $failed = $true
}

# 5) SSH auth (needs -User and -Key)
$step++
Write-Step "SSH authentication"
if ($User -and $Key) {
    if (-not (Test-Path $Key)) {
        Write-Host "  [FAIL] key file not found: $Key"
        $failed = $true
    } else {
        $out = & ssh -p $Port -o ConnectTimeout=5 -o BatchMode=yes -o StrictHostKeyChecking=accept-new `
            -i $Key "${User}@${Target}" "echo auth_ok" 2>&1
        $code = $LASTEXITCODE
        if ($code -eq 0) {
            Write-Host "  [PASS] auth ok (${User}@${Target})"
        } elseif ($out -match 'Permission denied') {
            Write-Host "  [FAIL] auth denied (Permission denied)"
            Write-Host "    hint: 1) correct key (-Key points to private key)"
            Write-Host "          2) private key permission must be 600: icacls $Key /inheritance:r /grant:r `"$($env:USERNAME):R`""
            Write-Host "          3) username spelling ($User)"
            Write-Host "          4) server allows key login"
            $failed = $true
        } elseif ($out -match 'Host key verification failed') {
            Write-Host "  [FAIL] host key verification failed"
            Write-Host "    hint: remove stale entry: ssh-keygen -R $Target"
            $failed = $true
        } else {
            Write-Host "  [FAIL] auth-stage error (exit=$code): $((($out | Select-String -Pattern 'error|denied|closed' | Select-Object -First 1) -as [string]))"
            $failed = $true
        }
    }
} else {
    Write-Host "  [SKIP] -User/-Key not provided (connection/service layers covered)"
}

Write-Host ""
if ($failed) {
    Write-Host "[FAIL] failures found - follow hints of [FAIL] layers"
    exit 1
}
Write-Host "[OK] all diagnostics passed (${Target}:${Port})"
exit 0
