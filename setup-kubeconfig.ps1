# Kubernetes kubeconfig Setup Automation Script
# Purpose: Auto-configure kubeconfig, solve "kubeconfig not found at" error

param(
    [Parameter(Mandatory=$false)]
    [string]$Source = "prompt",

    [Parameter(Mandatory=$false)]
    [string]$ClusterName,

    [Parameter(Mandatory=$false)]
    [string]$KubeConfigPath = "$env:USERPROFILE\.kube\config",

    [Parameter(Mandatory=$false)]
    [switch]$VerifyOnly,

    [Parameter(Mandatory=$false)]
    [switch]$AutoSetup
)

$colors = @{
    Success = "Green"
    Warning = "Yellow"
    Error = "Red"
    Info = "Cyan"
}

function Write-Section {
    param([string]$Title)
    Write-Host "`n========================================" -ForegroundColor $colors.Info
    Write-Host " $Title" -ForegroundColor $colors.Info
    Write-Host "========================================`n" -ForegroundColor $colors.Info
}

function Write-Step {
    param([string]$Message)
    Write-Host "[*] $Message" -ForegroundColor White
}

function Write-Success {
    param([string]$Message)
    Write-Host "[OK] $Message" -ForegroundColor $colors.Success
}

function Write-Warning {
    param([string]$Message)
    Write-Host "[WARN] $Message" -ForegroundColor $colors.Warning
}

function Write-ErrorMsg {
    param([string]$Message)
    Write-Host "[ERROR] $Message" -ForegroundColor $colors.Error
}

function Test-KubeConfigExists {
    $paths = @(
        $env:KUBECONFIG,
        "$env:USERPROFILE\.kube\config",
        "$env:HOME\.kube\config",
        ".\kubeconfig"
    )

    foreach ($path in $paths) {
        if ($path -and (Test-Path $path)) {
            return $path
        }
    }
    return $null
}

function New-KubeDirectory {
    $kubeDir = "$env:USERPROFILE\.kube"
    if (-not (Test-Path $kubeDir)) {
        Write-Step "Creating .kube directory..."
        New-Item -ItemType Directory -Path $kubeDir -Force | Out-Null
        Write-Success "Directory created: $kubeDir"
    }
    return $kubeDir
}

function Set-KubeConfigEnvironment {
    param([string]$Path)

    Write-Step "Setting KUBECONFIG environment variable..."
    $env:KUBECONFIG = $Path
    [Environment]::SetEnvironmentVariable("KUBECONFIG", $Path, "User")
    Write-Success "Environment variable set: $Path"
}

function Test-KubectlInstalled {
    try {
        $result = Get-Command kubectl -ErrorAction Stop
        return $true
    } catch {
        return $false
    }
}

function Get-KubeConfigFromkubectl {
    if (-not (Test-KubeConfigExists)) {
        Write-Warning "No existing kubeconfig found, skipping kubectl export"
        return $null
    }

    $existing = Test-KubeConfigExists
    Write-Step "Reading existing configuration..."

    try {
        $contexts = kubectl config get-contexts --kubeconfig=$existing 2>$null
        if ($LASTEXITCODE -eq 0) {
            Write-Success "Successfully read kubectl configuration"
            Write-Host $contexts
            return $existing
        }
    } catch {
        Write-ErrorMsg "kubectl configuration read failed"
    }
    return $null
}

function Import-KubeConfigFromFile {
    param([string]$SourcePath)

    $kubeDir = New-KubeDirectory
    $destPath = "$kubeDir\config"

    Write-Step "Importing kubeconfig from: $SourcePath"
    Copy-Item -Path $SourcePath -Destination $destPath -Force
    Set-KubeConfigEnvironment -Path $destPath
    Write-Success "kubeconfig imported to: $destPath"
}

function Connect-AzureAKS {
    param(
        [Parameter(Mandatory=$true)]
        [string]$ResourceGroup,
        [Parameter(Mandatory=$true)]
        [string]$AKSName
    )

    Write-Step "Checking Azure CLI..."
    try {
        az --version | Out-Null
    } catch {
        Write-ErrorMsg "Azure CLI is not installed"
        Write-Host "Install: https://docs.microsoft.com/en-us/cli/azure/install-azure-cli"
        return $false
    }

    Write-Step "Logging in to Azure..."
    az login

    Write-Step "Getting AKS credentials..."
    Write-Host "Resource Group: $ResourceGroup"
    Write-Host "Cluster Name: $AKSName"

    try {
        az aks get-credentials `
            --resource-group $ResourceGroup `
            --name $AKSName `
            --overwrite-existing

        Set-KubeConfigEnvironment -Path "$env:USERPROFILE\.kube\config"
        Write-Success "Azure AKS configuration complete"
        return $true
    } catch {
        Write-ErrorMsg "Azure AKS configuration failed: $_"
        return $false
    }
}

function Connect-AWSEKS {
    param(
        [Parameter(Mandatory=$true)]
        [string]$ClusterName,
        [Parameter(Mandatory=$false)]
        [string]$Region = "us-west-2"
    )

    Write-Step "Checking AWS CLI..."
    try {
        aws --version | Out-Null
    } catch {
        Write-ErrorMsg "AWS CLI is not installed"
        Write-Host "Install: https://docs.aws.amazon.com/cli/latest/userguide/install-cliv2.html"
        return $false
    }

    Write-Step "Updating kubeconfig..."
    Write-Host "Cluster Name: $ClusterName"
    Write-Host "Region: $Region"

    try {
        aws eks update-kubeconfig `
            --name $ClusterName `
            --region $Region

        Set-KubeConfigEnvironment -Path "$env:USERPROFILE\.kube\config"
        Write-Success "AWS EKS configuration complete"
        return $true
    } catch {
        Write-ErrorMsg "AWS EKS configuration failed: $_"
        return $false
    }
}

function Connect-GKE {
    param(
        [Parameter(Mandatory=$true)]
        [string]$ClusterName,
        [Parameter(Mandatory=$false)]
        [string]$Region = "us-central1",
        [Parameter(Mandatory=$false)]
        [string]$Project
    )

    Write-Step "Checking Google Cloud SDK..."
    try {
        gcloud --version | Out-Null
    } catch {
        Write-ErrorMsg "Google Cloud SDK is not installed"
        Write-Host "Install: https://cloud.google.com/sdk/docs/install-sdk"
        return $false
    }

    Write-Step "Getting GKE credentials..."
    Write-Host "Cluster Name: $ClusterName"
    Write-Host "Region: $Region"

    try {
        if ($Project) {
            gcloud container clusters get-credentials `
                $ClusterName `
                --region $Region `
                --project $Project
        } else {
            gcloud container clusters get-credentials `
                $ClusterName `
                --region $Region
        }

        Set-KubeConfigEnvironment -Path "$env:USERPROFILE\.kube\config"
        Write-Success "Google GKE configuration complete"
        return $true
    } catch {
        Write-ErrorMsg "GKE configuration failed: $_"
        return $false
    }
}

function Connect-Minikube {
    Write-Step "Checking Minikube..."
    try {
        minikube version | Out-Null
    } catch {
        Write-ErrorMsg "Minikube is not installed"
        Write-Host "Install: https://minikube.sigs.k8s.io/docs/start/"
        return $false
    }

    Write-Step "Starting Minikube..."
    minikube start

    Write-Step "Getting Minikube kubeconfig..."
    $kubeConfig = minikube docker-env | Select-String "KUBECONFIG"
    if ($kubeConfig) {
        Write-Success "Minikube configuration complete"
        return $true
    } else {
        Write-ErrorMsg "Minikube configuration failed"
        return $false
    }
}

function Connect-Kind {
    param(
        [Parameter(Mandatory=$false)]
        [string]$ClusterName = "kind"
    )

    Write-Step "Checking Kind..."
    try {
        kind version | Out-Null
    } catch {
        Write-ErrorMsg "Kind is not installed"
        Write-Host "Install: https://kind.sigs.k8s.io/docs/user/quick-start/"
        return $false
    }

    Write-Step "Creating Kind cluster..."
    if (-not (kind get clusters | Select-String $ClusterName)) {
        kind create cluster --name $ClusterName
    }

    Write-Step "Configuring kubeconfig..."
    $kindConfig = "$env:PATH\.kind\config"
    if (Test-Path $kindConfig) {
        Import-KubeConfigFromFile -SourcePath $kindConfig
    }

    Write-Success "Kind configuration complete"
    return $true
}

function Test-KubeConnection {
    Write-Section "Verifying Kubernetes Connection"

    if (-not (Test-KubeConfigExists)) {
        Write-ErrorMsg "kubeconfig file not found"
        return $false
    }

    $kubeconfig = Test-KubeConfigExists
    Write-Step "Using config: $kubeconfig"

    $env:KUBECONFIG = $kubeconfig

    if (-not (Test-KubectlInstalled)) {
        Write-ErrorMsg "kubectl is not installed"
        Write-Host "Install kubectl:"
        Write-Host "  Windows: choco install kubernetes-cli"
        Write-Host "  Or: https://kubernetes.io/docs/tasks/tools/install-kubectl/"
        return $false
    }

    Write-Step "Testing cluster connection..."
    try {
        kubectl cluster-info 2>$null
        if ($LASTEXITCODE -eq 0) {
            Write-Success "Cluster connection successful"
        } else {
            Write-ErrorMsg "Cluster connection failed"
            return $false
        }
    } catch {
        Write-ErrorMsg "kubectl command execution failed"
        return $false
    }

    Write-Step "Getting cluster nodes..."
    try {
        kubectl get nodes
        Write-Success "Node information retrieved"
    } catch {
        Write-ErrorMsg "Cannot get node information"
        return $false
    }

    Write-Step "Current context..."
    try {
        $context = kubectl config current-context
        Write-Host "Current context: $context" -ForegroundColor $colors.Info

        kubectl config view --context=$context
    } catch {
        Write-Warning "Cannot get context information"
    }

    return $true
}

function Show-Menu {
    Write-Section "Kubernetes kubeconfig Configuration Tool"
    Write-Host "Current Status:" -ForegroundColor White
    $existing = Test-KubeConfigExists
    if ($existing) {
        Write-Success "kubeconfig is configured: $existing"
    } else {
        Write-ErrorMsg "kubeconfig is NOT configured"
    }
    Write-Host ""

    Write-Host "Please select an option:" -ForegroundColor Cyan
    Write-Host "  1. Import kubeconfig from file" -ForegroundColor White
    Write-Host "  2. Azure AKS configuration" -ForegroundColor White
    Write-Host "  3. AWS EKS configuration" -ForegroundColor White
    Write-Host "  4. Google GKE configuration" -ForegroundColor White
    Write-Host "  5. Minikube configuration" -ForegroundColor White
    Write-Host "  6. Kind configuration" -ForegroundColor White
    Write-Host "  7. Verify existing configuration" -ForegroundColor White
    Write-Host "  8. Run all tests" -ForegroundColor White
    Write-Host "  0. Exit" -ForegroundColor White
    Write-Host ""
}

# Main program
function Main {
    if ($VerifyOnly) {
        Test-KubeConnection
        return
    }

    Show-Menu

    if ($Source -eq "prompt") {
        $choice = Read-Host "Enter option"

        switch ($choice) {
            "1" {
                Write-Host "`nImport kubeconfig file" -ForegroundColor Yellow
                $filePath = Read-Host "Enter kubeconfig file path"
                if (Test-Path $filePath) {
                    Import-KubeConfigFromFile -SourcePath $filePath
                    Test-KubeConnection
                } else {
                    Write-ErrorMsg "File not found: $filePath"
                }
            }
            "2" {
                Write-Host "`nAzure AKS Configuration" -ForegroundColor Yellow
                $rg = Read-Host "Resource Group name"
                $name = Read-Host "Cluster name"
                Connect-AzureAKS -ResourceGroup $rg -AKSName $name
            }
            "3" {
                Write-Host "`nAWS EKS Configuration" -ForegroundColor Yellow
                $name = Read-Host "Cluster name"
                $region = Read-Host "Region (default: us-west-2)"
                if (-not $region) { $region = "us-west-2" }
                Connect-AWSEKS -ClusterName $name -Region $region
            }
            "4" {
                Write-Host "`nGoogle GKE Configuration" -ForegroundColor Yellow
                $name = Read-Host "Cluster name"
                $region = Read-Host "Region (default: us-central1)"
                if (-not $region) { $region = "us-central1" }
                Connect-GKE -ClusterName $name -Region $region
            }
            "5" {
                Write-Host "`nMinikube Configuration" -ForegroundColor Yellow
                Connect-Minikube
            }
            "6" {
                Write-Host "`nKind Configuration" -ForegroundColor Yellow
                $name = Read-Host "Cluster name (default: kind)"
                if (-not $name) { $name = "kind" }
                Connect-Kind -ClusterName $name
            }
            "7" {
                Test-KubeConnection
            }
            "8" {
                Write-Section "Complete Test"
                $kubeconfig = Test-KubeConfigExists
                if ($kubeconfig) {
                    Write-Success "Found kubeconfig: $kubeconfig"
                    Test-KubeConnection
                } else {
                    Write-ErrorMsg "kubeconfig not found"
                    Show-Menu
                }
            }
            "0" {
                Write-Host "Exiting..." -ForegroundColor Yellow
                return
            }
            default {
                Write-ErrorMsg "Invalid option"
                Main
            }
        }
    } else {
        # Non-interactive mode
        switch ($Source) {
            "file" {
                if ($ClusterName) {
                    Import-KubeConfigFromFile -SourcePath $ClusterName
                }
            }
            "aks" {
                if ($ClusterName) {
                    Connect-AzureAKS -ResourceGroup $ClusterName -AKSName $ClusterName
                }
            }
            "eks" {
                if ($ClusterName) {
                    Connect-AWSEKS -ClusterName $ClusterName
                }
            }
            "gke" {
                if ($ClusterName) {
                    Connect-GKE -ClusterName $ClusterName
                }
            }
            "minikube" {
                Connect-Minikube
            }
            "kind" {
                Connect-Kind -ClusterName $ClusterName
            }
        }
    }
}

# Start
Main
