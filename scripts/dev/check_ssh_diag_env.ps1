# Check prerequisites for SSH diagnostics (diagnose_ssh.ps1 / local_ops.ps1 diag)
# Colorized PASS/FAIL/WARN/SKIP output (Write-Host -ForegroundColor).
#
# Checks:
#   1. PowerShell version >= 5.1
#   2. ssh client available (Windows OpenSSH)
#   3. diagnostic scripts exist (diagnose_ssh.ps1, local_ops.ps1)
#   4. if -Key given: file exists + permission not overly broad
#   5. if -Target given: DNS resolves (optional precheck)
#
# Usage (PowerShell 5.1 / 7+):
#   .\check_ssh_diag_env.ps1                          # env only
#   .\check_ssh_diag_env.ps1 -Target 10.0.0.1         # env + DNS
#   .\check_ssh_diag_env.ps1 -Key C:\Users\x\.ssh\id_rsa
#   .\check_ssh_diag_env.ps1 -Target 10.0.0.1 -Key C:\Users\x\.ssh\id_rsa
#
# Exit code: 0 = all pass; 1 = failures found
param(
    [string]$Target = "",
    [string]$Key = ""
)

$failed = $false
$devDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# Colorized check line: [PASS] green / [FAIL] red / [WARN] yellow / [SKIP] dark gray
function Write-Check {
    param([string]$Status, [string]$Msg)
    $color = switch ($Status) {
        'PASS' { 'Green' }
        'FAIL' { 'Red' }
        'WARN' { 'Yellow' }
        'SKIP' { 'DarkGray' }
        default { 'Gray' }
    }
    Write-Host "  [$Status]" -NoNewline -ForegroundColor $color
    Write-Host " $Msg"
}

Write-Host "== SSH diagnostics prerequisites check ==" -ForegroundColor Cyan
Write-Host ""

# 1) PowerShell version
Write-Host "[1/5] PowerShell version" -ForegroundColor Cyan
if ($PSVersionTable.PSVersion.Major -ge 5) {
    Write-Check PASS "PowerShell $($PSVersionTable.PSVersion) (>= 5.1 required)"
} else {
    Write-Check FAIL "PowerShell $($PSVersionTable.PSVersion) - 5.1+ required (upgrade or use pwsh 7+)"
    $failed = $true
}

# 2) ssh client
Write-Host "[2/5] ssh client (OpenSSH)" -ForegroundColor Cyan
$ssh = Get-Command ssh -ErrorAction SilentlyContinue
if ($ssh) {
    # ssh -V 输出到 stderr；PS 5.1 的 2>&1 会产生 NativeCommandError，改用 cmd /c 规避
    $ver = (& cmd /c "ssh -V 2>&1") -join ' '
    if (-not $ver) { $ver = 'unknown' }
    Write-Check PASS "ssh available: $ver"
} else {
    Write-Check FAIL "ssh not found - install OpenSSH Client:"
    Write-Host "         Get-WindowsCapability -Online | Where-Object Name -like 'OpenSSH.Client*' | Add-WindowsCapability -Online" -ForegroundColor DarkGray
    Write-Host "         or: winget install Microsoft.OpenSSH" -ForegroundColor DarkGray
    $failed = $true
}

# 3) diagnostic scripts
Write-Host "[3/5] diagnostic scripts" -ForegroundColor Cyan
$scripts = @('diagnose_ssh.ps1', 'local_ops.ps1')
foreach ($s in $scripts) {
    $p = Join-Path $devDir $s
    if (Test-Path $p) {
        Write-Check PASS "$s exists"
    } else {
        Write-Check FAIL "$s missing at $p"
        $failed = $true
    }
}

# 4) key file (if provided)
Write-Host "[4/5] SSH private key" -ForegroundColor Cyan
if ($Key) {
    if (-not (Test-Path $Key)) {
        Write-Check FAIL "key not found: $Key"
        $failed = $true
    } else {
        # Windows ACL check: warn if other users have access (common cause of OpenSSH refusal)
        $acl = (Get-Acl $Key).Access | Where-Object { $_.IdentityReference -notmatch "^(SYSTEM|Administrators|$([Environment]::UserName)$)" }
        if ($acl) {
            Write-Check WARN "key readable by other principals - tighten ACL:"
            Write-Host "         icacls $Key /inheritance:r /grant:r `"$([Environment]::UserName):R`"" -ForegroundColor DarkGray
        } else {
            Write-Check PASS "key exists and ACL restricted to current user"
        }
    }
} else {
    Write-Check SKIP "-Key not provided (auth layer not checkable)"
}

# 5) DNS (if target provided)
Write-Host "[5/5] target DNS" -ForegroundColor Cyan
if ($Target) {
    if ($Target -match '^\d+(\.\d+){3}$') {
        Write-Check PASS "$Target is an IP (skip DNS)"
    } else {
        try {
            $ip = [System.Net.Dns]::GetHostAddresses($Target) | Select-Object -First 1
            if ($ip) { Write-Check PASS "$Target resolves to $($ip.IPAddressToString)" }
            else { Write-Check FAIL "$Target did not resolve"; $failed = $true }
        } catch {
            Write-Check FAIL "$Target did not resolve ($($_.Exception.Message))"
            $failed = $true
        }
    }
} else {
    Write-Check SKIP "-Target not provided"
}

Write-Host ""
if ($failed) {
    Write-Host "[FAIL] prerequisites incomplete - fix [FAIL] items above, then run diagnostics" -ForegroundColor Red
    exit 1
}
Write-Host "[OK] all prerequisites satisfied - ready for SSH diagnostics" -ForegroundColor Green
exit 0
