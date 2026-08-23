# Local ops entry: one-click diagnostics / tests for Yunshu local workflow
# Integrates: diagnose_ssh.ps1 (diag), run_ci_test_local.ps1 (test),
#             check_service_ready.sh (ready), simulate_ssh_deploy.sh (deploy-sim)
#
# Usage (PowerShell 5.1 / 7+):
#   .\local_ops.ps1 diag -Target <ip> -User root -Key <key>     # SSH 5-layer diagnostics
#   .\local_ops.ps1 diag -Target <ip> -User root -Key <key> -Port 2222
#   .\local_ops.ps1 test [-Quick]                               # local CI test simulation
#   .\local_ops.ps1 ready [-Port 5678]                          # service readiness (needs bash)
#   .\local_ops.ps1 deploy-sim [-DryRun]                        # deploy simulation (needs bash)
#   .\local_ops.ps1 list                                        # list available actions
param(
    [Parameter(Position = 0)]
    [ValidateSet('diag', 'test', 'ready', 'deploy-sim', 'env', 'list')]
    [string]$Action = 'list',
    [string]$Target = "",
    [int]$Port = 22,
    [string]$User = "",
    [string]$Key = "",
    [switch]$Quick,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$devDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $devDir

function Show-List {
    Write-Host @"
Available actions:
  diag        SSH 5-layer diagnostics (diagnose_ssh.ps1)
              .\local_ops.ps1 diag -Target <ip> -User <user> -Key <key> [-Port 22]
  test        local CI test simulation (run_ci_test_local.ps1)
              .\local_ops.ps1 test [-Quick]
  ready       service readiness check (check_service_ready.sh, needs bash)
              .\local_ops.ps1 ready [-Port 5678]
  deploy-sim  deploy simulation (simulate_ssh_deploy.sh, needs bash)
              .\local_ops.ps1 deploy-sim [-DryRun]
  env         SSH diagnostics prerequisites (check_ssh_diag_env.ps1)
              .\local_ops.ps1 env [-Target <ip>] [-Key <path>]
"@
}

switch ($Action) {
    'diag' {
        if (-not $Target) { Write-Error "diag needs -Target <ip|host>"; exit 2 }
        $diagPath = Join-Path $devDir 'diagnose_ssh.ps1'
        Write-Host "== local_ops: SSH diagnostics (diagnose_ssh.ps1) =="
        # 显式传参（避免数组 splat 在 PS 5.1 下的参数名绑定歧义）
        if ($User -and $Key) {
            & $diagPath -Target $Target -Port $Port -User $User -Key $Key
        } else {
            & $diagPath -Target $Target -Port $Port
        }
        exit $LASTEXITCODE
    }
    'test' {
        Write-Host "== local_ops: local CI test (run_ci_test_local.ps1) =="
        $testPath = Join-Path $devDir 'run_ci_test_local.ps1'
        if ($Quick) { & $testPath -Quick } else { & $testPath }
        exit $LASTEXITCODE
    }
    'env' {
        Write-Host "== local_ops: SSH diagnostics prerequisites (check_ssh_diag_env.ps1) =="
        $envPath = Join-Path $devDir 'check_ssh_diag_env.ps1'
        if ($Target -and $Key) {
            & $envPath -Target $Target -Key $Key
        } elseif ($Target) {
            & $envPath -Target $Target
        } elseif ($Key) {
            & $envPath -Key $Key
        } else {
            & $envPath
        }
        exit $LASTEXITCODE
    }
    'ready' {
        Write-Host "== local_ops: service readiness (check_service_ready.sh) =="
        if (-not (Get-Command bash -ErrorAction SilentlyContinue)) {
            Write-Error "bash not found (needed for ready). Use check_service_ready.sh in Git Bash/WSL."
            exit 2
        }
        & bash (Join-Path $devDir 'check_service_ready.sh') @("--port", "$Port")
        exit $LASTEXITCODE
    }
    'deploy-sim' {
        Write-Host "== local_ops: deploy simulation (simulate_ssh_deploy.sh) =="
        if (-not (Get-Command bash -ErrorAction SilentlyContinue)) {
            Write-Error "bash not found (needed for deploy-sim). Use simulate_ssh_deploy.sh in Git Bash/WSL."
            exit 2
        }
        $simArgs = @()
        if ($DryRun) { $simArgs += '--dry-run' }
        & bash (Join-Path $devDir 'simulate_ssh_deploy.sh') @simArgs
        exit $LASTEXITCODE
    }
    default {
        Show-List
        exit 0
    }
}
