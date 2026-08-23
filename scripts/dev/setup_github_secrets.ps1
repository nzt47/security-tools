# Write alert-related secrets (SMTP_* + AUDIT_ALERT_THRESHOLD) from .env to GitHub Secrets
# Purpose: CI daily alert (audit-alert.yml Job1) reads these via $GITHUB_ENV at runtime.
#
# Prereq: gh CLI installed and authenticated (gh auth login); or add manually via web UI
#         (see docs/zh/GitHubSecrets配置清单_20260816.md).
#
# Usage (PowerShell 5.1 / 7+):
#   .\setup_github_secrets.ps1                              # infer repo, write 7 keys
#   .\setup_github_secrets.ps1 -Repo owner/repo             # explicit repo
#   .\setup_github_secrets.ps1 -EnvFile .\custom.env        # explicit env file
#   .\setup_github_secrets.ps1 -DryRun                      # preview only, no gh call
#
# Security: sensitive values (SMTP_PASS etc.) are never printed; dry-run shows key names only.
param(
    [string]$Repo = "",          # owner/repo; inferred from git remote if empty
    [string]$EnvFile = "",       # .env path; defaults to project root .env
    [switch]$DryRun              # preview only, do not write
)

$ErrorActionPreference = 'Stop'
# scripts/dev -> project root (two levels up)
$root = (Get-Item $PSScriptRoot).Parent.Parent.FullName
$envFile = if ($EnvFile) { $EnvFile } else { Join-Path $root '.env' }

if (-not (Test-Path $envFile)) {
    Write-Error "env file not found: $envFile"
    exit 1
}

if (-not $DryRun) {
    gh --version | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Error "gh CLI not installed (https://cli.github.com); or use web UI to add manually."
        exit 1
    }
}

if (-not $Repo) {
    $remote = git -C $root remote get-url origin 2>$null
    if ($remote -match '[:/]([^/:]+/[^/.]+)(\.git)?$') {
        $Repo = $matches[1]
    }
}
if (-not $Repo) {
    Write-Error "cannot infer repo; pass -Repo owner/repo explicitly."
    exit 1
}

# Target keys (1:1 with secrets injected in .github/workflows/audit-alert.yml)
$targetKeys = @('SMTP_HOST', 'SMTP_PORT', 'SMTP_USER', 'SMTP_PASS',
                'SMTP_TO', 'SMTP_SSL', 'AUDIT_ALERT_THRESHOLD')

# Parse .env: extract target keys only (supports quoted values)
$values = @{}
foreach ($line in Get-Content $envFile -Encoding UTF8) {
    $t = $line.Trim()
    if ($t -match '^(SMTP_[A-Z]+|AUDIT_ALERT_THRESHOLD)=(.*)$') {
        $name, $val = $matches[1], $matches[2]
        $values[$name] = $val.Trim().Trim('"').Trim("'")
    }
}

Write-Host "target repo: $Repo"
Write-Host "source file: $envFile"
$setCount = 0
foreach ($k in $targetKeys) {
    if (-not $values.ContainsKey($k) -or [string]::IsNullOrEmpty($values[$k])) {
        Write-Host "  [skip] $k (missing or empty in .env)"
        continue
    }
    if ($DryRun) {
        Write-Host "  [dry-run] gh secret set $k --repo $Repo"
    } else {
        gh secret set $k --body $values[$k] --repo $Repo
        if ($LASTEXITCODE -ne 0) {
            Write-Error "failed to set $k (exit=$LASTEXITCODE)"
            exit 1
        }
        Write-Host "  [set] $k"
        $setCount++
    }
}

if ($DryRun) {
    Write-Host ""
    Write-Host "[dry-run] preview done (gh NOT called). Re-run without -DryRun to apply."
} else {
    Write-Host ""
    Write-Host "done: wrote $setCount/$($targetKeys.Count) secrets (rest skipped)."
    Write-Host "verify: gh secret list --repo $Repo"
}
