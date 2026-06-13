# 云枢 V2 监控堆栈快速启动脚本 (Windows PowerShell)
#

param(
    [Parameter(Position=0)]
    [ValidateSet("start", "stop", "restart", "logs", "status")]
    [string]$Action = "start"
)

function Write-Info {
    param([string]$Message)
    Write-Host "[INFO] $Message" -ForegroundColor Cyan
}

function Write-Success {
    param([string]$Message)
    Write-Host "[SUCCESS] $Message" -ForegroundColor Green
}

function Write-Warning {
    param([string]$Message)
    Write-Host "[WARNING] $Message" -ForegroundColor Yellow
}

function Write-ErrorMsg {
    param([string]$Message)
    Write-Host "[ERROR] $Message" -ForegroundColor Red
}

function Test-Docker {
    Write-Info "Checking Docker..."
    $dockerCmd = Get-Command docker -ErrorAction SilentlyContinue
    
    if (-not $dockerCmd) {
        Write-ErrorMsg "Docker not installed. Please install Docker Desktop."
        exit 1
    }
    
    Write-Success "Docker installed"
}

function Start-Stack {
    Write-Info "Starting monitoring stack..."
    Push-Location "$PSScriptRoot"
    docker-compose up -d
    Pop-Location
    Write-Success "Monitoring stack started"
}

function Show-Status {
    Write-Info "Checking service status..."
    Write-Host ""
    Write-Host "+-------------------------------------------+"
    Write-Host "|  Service Status                           |"
    Write-Host "+-------------------------------------------+"
    Write-Host "|  Prometheus:  http://localhost:9090       |"
    Write-Host "|  Grafana:     http://localhost:3000       |"
    Write-Host "|  (admin/admin)                            |"
    Write-Host "+-------------------------------------------+"
    Write-Host ""
}

function Stop-Stack {
    Write-Warning "Stopping monitoring stack..."
    Push-Location "$PSScriptRoot"
    docker-compose down
    Pop-Location
    Write-Success "Monitoring stack stopped"
}

function Show-Logs {
    Write-Info "Viewing logs..."
    Push-Location "$PSScriptRoot"
    docker-compose logs -f
}

function Main {
    switch ($Action) {
        "start" {
            Test-Docker
            Start-Stack
            Show-Status
            Write-Info "To start Yunshu V2 metrics export, run:"
            Write-Host "  python prometheus_example.py" -ForegroundColor Gray
            Write-Host ""
        }
        "stop" {
            Stop-Stack
        }
        "restart" {
            Test-Docker
            Stop-Stack
            Start-Stack
            Show-Status
        }
        "logs" {
            Show-Logs
        }
        "status" {
            Show-Status
        }
    }
}

Main