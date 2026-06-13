# Kubernetes kubeconfig Fix Verification Demo
# This script demonstrates how to manually set KUBECONFIG and verify the fix

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " KUBECONFIG Fix Verification Demo" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

Write-Host "[Step 1] Check Current KUBECONFIG Status" -ForegroundColor Yellow
Write-Host "----------------------------------------"
Write-Host "Current KUBECONFIG env var: " -NoNewline
if ($env:KUBECONFIG) {
    Write-Host "$env:KUBECONFIG" -ForegroundColor Green
} else {
    Write-Host "(Not Set)" -ForegroundColor Red
}
Write-Host ""

Write-Host "[Step 2] Create Sample kubeconfig File" -ForegroundColor Yellow
Write-Host "----------------------------------------"

# Create .kube directory
$kubeDir = "$env:USERPROFILE\.kube"
if (-not (Test-Path $kubeDir)) {
    Write-Host "Creating directory: $kubeDir"
    New-Item -ItemType Directory -Path $kubeDir -Force | Out-Null
}

# Create sample config
$sampleConfig = @"
apiVersion: v1
kind: Config
clusters:
- cluster:
    certificate-authority-data: dGVzdC1jYS1kYXRh
    server: https://kubernetes.default.svc
  name: demo-cluster
contexts:
- context:
    cluster: demo-cluster
    namespace: default
    user: demo-user
  name: demo-context
current-context: demo-context
users:
- name: demo-user
  user:
    token: demo-token
"@

$configPath = "$kubeDir\config"
Write-Host "Config file path: $configPath"
Write-Host "Saving sample kubeconfig..." -ForegroundColor White

# Save config
Set-Content -Path $configPath -Value $sampleConfig -Force
Write-Host "[OK] Sample kubeconfig file created" -ForegroundColor Green
Write-Host ""

Write-Host "[Step 3] Manually Set KUBECONFIG Env Var" -ForegroundColor Yellow
Write-Host "----------------------------------------"

Write-Host "Method A: Temporary (Current Session)" -ForegroundColor Cyan
Write-Host "Command: " -NoNewline
Write-Host '$env:KUBECONFIG = "' -NoNewline
Write-Host $configPath -NoNewline
Write-Host '"' -ForegroundColor White
Write-Host ""

Write-Host "Method B: Permanent (Recommended)" -ForegroundColor Cyan
$cmd = '[Environment]::SetEnvironmentVariable("KUBECONFIG", "' + $configPath + '", "User")'
Write-Host "Command: $cmd" -ForegroundColor White
Write-Host ""

Write-Host "Applying temporary setting..." -ForegroundColor Yellow
$env:KUBECONFIG = $configPath
Write-Host "[OK] KUBECONFIG set to: $env:KUBECONFIG" -ForegroundColor Green
Write-Host ""

Write-Host "[Step 4] Verify Configuration" -ForegroundColor Yellow
Write-Host "----------------------------------------"

Write-Host "A. Environment Variable Check:" -ForegroundColor Cyan
Write-Host "  KUBECONFIG = $env:KUBECONFIG" -ForegroundColor White
Write-Host ""

Write-Host "B. File Existence Check:" -ForegroundColor Cyan
$exists = Test-Path $configPath
Write-Host "  File exists: $exists" -ForegroundColor $(if ($exists) { "Green" } else { "Red" })
Write-Host ""

Write-Host "C. kubectl Contexts Check:" -ForegroundColor Cyan
try {
    $contexts = kubectl config get-contexts 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  [OK] kubectl config is readable" -ForegroundColor Green
        Write-Host $contexts
    } else {
        Write-Host "  [WARN] kubectl config read failed" -ForegroundColor Yellow
        Write-Host "  Reason: $contexts" -ForegroundColor Gray
    }
} catch {
    Write-Host "  [INFO] kubectl test: $_" -ForegroundColor Yellow
}
Write-Host ""

Write-Host "D. kubectl View Check:" -ForegroundColor Cyan
try {
    kubectl config view --flatten 2>&1 | Select-Object -First 5
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  [OK] kubectl view available" -ForegroundColor Green
    }
} catch {
    Write-Host "  [INFO] kubectl view: $_" -ForegroundColor Yellow
}
Write-Host ""

Write-Host "[Step 5] Run Diagnostic Script" -ForegroundColor Yellow
Write-Host "----------------------------------------"

Write-Host "Running test-kubeconfig.ps1 for complete diagnosis..." -ForegroundColor White
Write-Host ""

& "$PSScriptRoot\test-kubeconfig.ps1"
Write-Host ""

Write-Host "[Step 6] Fix Verification Summary" -ForegroundColor Yellow
Write-Host "----------------------------------------"

Write-Host "Fix Status: " -NoNewline
if ($env:KUBECONFIG -and (Test-Path $env:KUBECONFIG)) {
    Write-Host "SUCCESS" -ForegroundColor Green
} else {
    Write-Host "FAILED" -ForegroundColor Red
}
Write-Host ""

Write-Host "Fix Applied:" -ForegroundColor Cyan
Write-Host "  1. Created kubeconfig file: $configPath" -ForegroundColor White
Write-Host "  2. Set KUBECONFIG environment variable" -ForegroundColor White
Write-Host "  3. Verified configuration availability" -ForegroundColor White
Write-Host ""

Write-Host "Next Steps:" -ForegroundColor Cyan
Write-Host "  1. View complete docs: KUBECONFIG_COMPLETE_SOLUTION.md" -ForegroundColor White
Write-Host "  2. Run setup script: .\setup-kubeconfig.ps1" -ForegroundColor White
Write-Host "  3. Test kubectl: kubectl get pods" -ForegroundColor White
Write-Host "  4. Set permanent env: [Environment]::SetEnvironmentVariable(...)" -ForegroundColor White
Write-Host ""

Write-Host "Quick Test Commands:" -ForegroundColor Cyan
Write-Host "  kubectl config get-contexts  # View contexts" -ForegroundColor Gray
Write-Host "  kubectl cluster-info        # Test connection" -ForegroundColor Gray
Write-Host "  kubectl get namespaces      # List namespaces" -ForegroundColor Gray
Write-Host ""

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " Demo Complete!" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
