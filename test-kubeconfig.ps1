# Kubernetes kubeconfig Test Script
# Purpose: Verify kubeconfig configuration

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " Kubernetes kubeconfig Verification Tool" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Test 1: Check kubectl
Write-Host "[Test 1] kubectl Installation Status" -ForegroundColor Yellow
try {
    $kubectlVersion = kubectl version --client 2>&1 | Select-Object -First 1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  [OK] kubectl is installed" -ForegroundColor Green
        Write-Host "  Version: $kubectlVersion" -ForegroundColor White
    } else {
        Write-Host "  [FAIL] kubectl not properly installed" -ForegroundColor Red
    }
} catch {
    Write-Host "  [FAIL] kubectl not installed" -ForegroundColor Red
    Write-Host "  Install: choco install kubernetes-cli" -ForegroundColor White
}
Write-Host ""

# Test 2: Check kubeconfig file
Write-Host "[Test 2] kubeconfig File Check" -ForegroundColor Yellow
$found = $false

# Check KUBECONFIG environment variable
if ($env:KUBECONFIG) {
    Write-Host "  KUBECONFIG env var: $($env:KUBECONFIG)" -ForegroundColor White
    if (Test-Path $env:KUBECONFIG) {
        Write-Host "  [OK] File exists" -ForegroundColor Green
        $found = $true
    } else {
        Write-Host "  [FAIL] File does not exist" -ForegroundColor Red
    }
}

# Check default location
$defaultPath = "$env:USERPROFILE\.kube\config"
if (Test-Path $defaultPath) {
    Write-Host "  Default location ~/.kube/config: [OK] Exists" -ForegroundColor Green
    $found = $true
} else {
    Write-Host "  Default location ~/.kube/config: [FAIL] Does not exist" -ForegroundColor Red
}
Write-Host ""

# Test 3: Check contexts
Write-Host "[Test 3] kubectl Contexts" -ForegroundColor Yellow
if ($found) {
    try {
        $contexts = kubectl config get-contexts 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  [OK] kubectl contexts:" -ForegroundColor Green
            Write-Host $contexts
        } else {
            Write-Host "  [FAIL] Cannot get contexts" -ForegroundColor Red
        }
    } catch {
        Write-Host "  [FAIL] Context read failed" -ForegroundColor Red
    }
} else {
    Write-Host "  [SKIP] kubeconfig file not found" -ForegroundColor Yellow
}
Write-Host ""

# Test 4: Cluster connection test
Write-Host "[Test 4] Cluster Connection Test" -ForegroundColor Yellow
if ($found) {
    try {
        $result = kubectl cluster-info 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  [OK] Cluster is reachable" -ForegroundColor Green
            Write-Host $result
        } else {
            Write-Host "  [FAIL] Cannot connect to cluster" -ForegroundColor Red
            Write-Host "  Error: $result" -ForegroundColor White
        }
    } catch {
        Write-Host "  [FAIL] Connection test failed" -ForegroundColor Red
        Write-Host "  Error: $_" -ForegroundColor White
    }
} else {
    Write-Host "  [SKIP] kubeconfig file not found" -ForegroundColor Yellow
}
Write-Host ""

# Test 5: Python kubernetes library
Write-Host "[Test 5] Python kubernetes Library" -ForegroundColor Yellow
try {
    $kubernetesPkg = pip list | Select-String kubernetes
    if ($kubernetesPkg) {
        Write-Host "  [OK] kubernetes library installed:" -ForegroundColor Green
        $kubernetesPkg | ForEach-Object { Write-Host "    $_" -ForegroundColor White }
    } else {
        Write-Host "  [WARN] kubernetes library not installed" -ForegroundColor Yellow
        Write-Host "    Install: pip install kubernetes" -ForegroundColor White
    }
} catch {
    Write-Host "  [WARN] Cannot check kubernetes library" -ForegroundColor Yellow
}
Write-Host ""

# Test 6: Local Kubernetes environments
Write-Host "[Test 6] Local Kubernetes Environment" -ForegroundColor Yellow
$localK8s = $false

# Docker Desktop
$dockerDesktop = Get-Process "Docker Desktop" -ErrorAction SilentlyContinue
if ($dockerDesktop) {
    Write-Host "  [OK] Docker Desktop is running" -ForegroundColor Green
    $localK8s = $true
}

# Minikube
try {
    $minikubeStatus = minikube status 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  [OK] Minikube is running" -ForegroundColor Green
        Write-Host "  Status: $minikubeStatus" -ForegroundColor White
        $localK8s = $true
    }
} catch {}

# Kind
try {
    $kindClusters = kind get clusters 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  [OK] Kind clusters:" -ForegroundColor Green
        Write-Host "  Clusters: $kindClusters" -ForegroundColor White
        $localK8s = $true
    }
} catch {}

if (-not $localK8s) {
    Write-Host "  [WARN] No local Kubernetes environment detected" -ForegroundColor Yellow
    Write-Host "    Tip: Install Docker Desktop or Minikube to get started" -ForegroundColor White
}
Write-Host ""

# Summary
Write-Host "========================================" -ForegroundColor Cyan
Write-Host " Verification Summary" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
if ($found) {
    Write-Host "[OK] kubeconfig is configured" -ForegroundColor Green
    $kubePath = if ($env:KUBECONFIG) { $env:KUBECONFIG } else { $defaultPath }
    Write-Host "  Path: $kubePath" -ForegroundColor White
} else {
    Write-Host "[FAIL] kubeconfig is NOT configured" -ForegroundColor Red
    Write-Host ""
    Write-Host "Quick Fix:" -ForegroundColor Yellow
    Write-Host "  1. Create ~/.kube directory" -ForegroundColor White
    Write-Host "  2. Get kubeconfig from your cluster" -ForegroundColor White
    Write-Host "  3. Or run: .\setup-kubeconfig.ps1" -ForegroundColor White
}
Write-Host ""

# Next steps
Write-Host "Next Steps:" -ForegroundColor Yellow
Write-Host "  1. View complete docs: KUBECONFIG_COMPLETE_SOLUTION.md" -ForegroundColor White
Write-Host "  2. Run setup script: .\setup-kubeconfig.ps1" -ForegroundColor White
Write-Host "  3. Test kubectl: kubectl get pods" -ForegroundColor White
Write-Host "  4. View example: kubeconfig.example" -ForegroundColor White
Write-Host ""
