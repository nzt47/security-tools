# Kubernetes kubeconfig Update Script
# Helps you replace demo configuration with real cluster credentials

param(
    [Parameter(Mandatory=$false)]
    [switch]$Backup,

    [Parameter(Mandatory=$false)]
    [switch]$Restore,

    [Parameter(Mandatory=$false)]
    [string]$From,

    [Parameter(Mandatory=$false)]
    [string]$Server,

    [Parameter(Mandatory=$false)]
    [string]$CAData,

    [Parameter(Mandatory=$false)]
    [string]$Token,

    [Parameter(Mandatory=$false)]
    [switch]$Test
)

$configPath = "$env:USERPROFILE\.kube\config"
$backupPath = "$env:USERPROFILE\.kube\config.backup"

function Write-Section {
    param([string]$Title)
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host " $Title" -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor Cyan
}

function Write-Step {
    param([string]$Message)
    Write-Host "[*] $Message" -ForegroundColor White
}

function Write-Success {
    param([string]$Message)
    Write-Host "[OK] $Message" -ForegroundColor Green
}

function Write-Warning {
    param([string]$Message)
    Write-Host "[WARN] $Message" -ForegroundColor Yellow
}

function Write-Error {
    param([string]$Message)
    Write-Host "[ERROR] $Message" -ForegroundColor Red
}

function Backup-Config {
    Write-Section "Backing Up Current Configuration"
    if (Test-Path $configPath) {
        Copy-Item -Path $configPath -Destination $backupPath -Force
        Write-Success "Configuration backed up to: $backupPath"
    } else {
        Write-Warning "No existing configuration to backup"
    }
}

function Restore-Config {
    Write-Section "Restoring Configuration"
    if (Test-Path $backupPath) {
        Copy-Item -Path $backupPath -Destination $configPath -Force
        Write-Success "Configuration restored from: $backupPath"
    } else {
        Write-Error "Backup file not found: $backupPath"
    }
}

function Update-From-File {
    param([string]$SourcePath)
    Write-Section "Updating from File: $SourcePath"
    if (-not (Test-Path $SourcePath)) {
        Write-Error "File not found: $SourcePath"
        return $false
    }

    Backup-Config
    Copy-Item -Path $SourcePath -Destination $configPath -Force
    Write-Success "Configuration updated from: $SourcePath"
    return $true
}

function Update-Manual {
    param([string]$Server, [string]$CAData, [string]$Token)
    Write-Section "Creating New Configuration"
    
    Backup-Config

    $config = @"
apiVersion: v1
kind: Config
clusters:
- cluster:
    certificate-authority-data: $CAData
    server: $Server
  name: my-real-cluster
contexts:
- context:
    cluster: my-real-cluster
    namespace: default
    user: my-real-user
  name: my-real-context
current-context: my-real-context
users:
- name: my-real-user
  user:
    token: $Token
"@

    Set-Content -Path $configPath -Value $config
    Write-Success "New configuration created"
    return $true
}

function Test-Connection {
    Write-Section "Testing Connection"
    
    $env:KUBECONFIG = $configPath

    if (-not (Test-Path $configPath)) {
        Write-Error "Configuration file not found: $configPath"
        return $false
    }

    Write-Step "Checking configuration..."
    try {
        $contexts = kubectl config get-contexts 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Success "Configuration is readable"
            Write-Host $contexts
        } else {
            Write-Error "Cannot read configuration: $contexts"
            return $false
        }
    } catch {
        Write-Error "kubectl not found or failed: $_"
        return $false
    }

    Write-Host ""
    Write-Step "Attempting connection to cluster..."
    try {
        $clusterInfo = kubectl cluster-info 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Success "Connection successful!"
            Write-Host $clusterInfo
        } else {
            Write-Error "Connection failed: $clusterInfo"
            Write-Host ""
            Write-Host "Possible causes:" -ForegroundColor Yellow
            Write-Host "  1. API server address incorrect"
            Write-Host "  2. CA certificate invalid"
            Write-Host "  3. Token expired or invalid"
            Write-Host "  4. Network connectivity issues"
            return $false
        }
    } catch {
        Write-Error "Connection test failed: $_"
        return $false
    }

    Write-Host ""
    Write-Step "Listing nodes..."
    try {
        $nodes = kubectl get nodes 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Success "Node list retrieved"
            Write-Host $nodes
        }
    } catch {
        Write-Warning "Could not list nodes: $_"
    }

    Write-Host ""
    Write-Step "Listing namespaces..."
    try {
        $namespaces = kubectl get namespaces 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Success "Namespace list retrieved"
            Write-Host $namespaces
        }
    } catch {
        Write-Warning "Could not list namespaces: $_"
    }

    return $true
}

function Show-Help {
    Write-Section "Kubernetes kubeconfig Update Script"
    Write-Host "Usage:"
    Write-Host "  .\update-kubeconfig.ps1 -Backup               # Backup current config"
    Write-Host "  .\update-kubeconfig.ps1 -Restore              # Restore from backup"
    Write-Host "  .\update-kubeconfig.ps1 -From <file>          # Update from file"
    Write-Host "  .\update-kubeconfig.ps1 -Server <URL> -CAData <base64> -Token <token>  # Manual update"
    Write-Host "  .\update-kubeconfig.ps1 -Test                 # Test current connection"
    Write-Host ""
    Write-Host "Examples:"
    Write-Host "  .\update-kubeconfig.ps1 -From 'C:\path\to\real\kubeconfig'"
    Write-Host "  .\update-kubeconfig.ps1 -Server 'https://api.my-cluster.com:6443' -CAData '...' -Token '...'"
    Write-Host "  .\update-kubeconfig.ps1 -Test"
}

function Show-Menu {
    Write-Section "Kubernetes kubeconfig Update Tool"
    Write-Host "Current config: $configPath"
    if (Test-Path $configPath) {
        try {
            $current = kubectl config current-context 2>$null
            if ($LASTEXITCODE -eq 0) {
                Write-Host "Current context: $current" -ForegroundColor Green
            }
        } catch {}
    }
    Write-Host ""
    Write-Host "Select an option:"
    Write-Host "  1. Backup current configuration"
    Write-Host "  2. Restore from backup"
    Write-Host "  3. Update from another kubeconfig file"
    Write-Host "  4. Enter credentials manually"
    Write-Host "  5. Test current connection"
    Write-Host "  0. Exit"
}

function Get-Credentials-Manually {
    Write-Section "Enter Cluster Credentials"
    Write-Host "You need the following information from your cluster:"
    Write-Host "  1. API Server URL (e.g., https://api.my-cluster.com:6443)"
    Write-Host "  2. CA Certificate Data (Base64 encoded)"
    Write-Host "  3. Authentication Token"
    Write-Host ""

    $server = Read-Host "Enter API Server URL"
    $caData = Read-Host "Enter CA Certificate Data (Base64)"
    $token = Read-Host "Enter Authentication Token"

    if ([string]::IsNullOrEmpty($server) -or [string]::IsNullOrEmpty($caData) -or [string]::IsNullOrEmpty($token)) {
        Write-Error "All fields are required"
        return $null
    }

    return @{
        Server = $server
        CAData = $caData
        Token = $token
    }
}

# Main
if ($Backup) {
    Backup-Config
} elseif ($Restore) {
    Restore-Config
} elseif ($From) {
    if (Update-From-File -SourcePath $From) {
        if ($Test) {
            Test-Connection
        }
    }
} elseif ($Server -and $CAData -and $Token) {
    if (Update-Manual -Server $Server -CAData $CAData -Token $Token) {
        if ($Test) {
            Test-Connection
        }
    }
} elseif ($Test) {
    Test-Connection
} else {
    Show-Menu
    $choice = Read-Host "Enter option"

    switch ($choice) {
        "1" { Backup-Config }
        "2" { Restore-Config }
        "3" {
            $filePath = Read-Host "Enter path to kubeconfig file"
            if (Update-From-File -SourcePath $filePath) {
                Test-Connection
            }
        }
        "4" {
            $creds = Get-Credentials-Manually
            if ($creds) {
                if (Update-Manual -Server $creds.Server -CAData $creds.CAData -Token $creds.Token) {
                    Test-Connection
                }
            }
        }
        "5" { Test-Connection }
        "0" { return }
        default { Write-Error "Invalid option" }
    }
}

Write-Host ""
Write-Step "Done"
