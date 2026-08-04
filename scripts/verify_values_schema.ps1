<#
.SYNOPSIS
  values.schema.json 缺失校验与自动生成辅助脚本

.DESCRIPTION
  基于 release_checklist_v1.2.md §3.7 P3 增强建议：
  - 检查 Helm Chart 目录下 values.schema.json 是否存在
  - 缺失时若指定 -AutoGenerate，则用嵌入式 Python 从 values.yaml 推断类型生成 schema
  - Python/PyYAML 不可用时回退到内置静态 schema 模板（基于已知 values.yaml 结构）
  - 生成或存在时可选 -Validate 调 helm lint 验证（helm 不可用时 WARN 跳过）
  - 输出 PASS/FAIL/WARN 报告，退出码 0=全通过，1=有 FAIL

  三义原则：
  - [不易] 守住 Helm values 结构化校验契约：关键字段类型必须明确
  - [变易] Python 可用→动态推断；不可用→静态模板；helm 可用→lint 验证；不可用→跳过
  - [简易] 单脚本 + 嵌入式 Python here-string，零额外文件依赖

.PARAMETER ChartPath
  Helm Chart 根目录路径，默认 ./deploy/helm/tlm-ops-reporter

.PARAMETER AutoGenerate
  缺失时自动生成 values.schema.json（先尝试 Python 推断，失败回退静态模板）

.PARAMETER Validate
  生成后调用 helm lint 验证 schema 是否被 Helm 接受（需 helm 可用）

.PARAMETER Force
  已存在时是否覆盖重新生成（默认不覆盖已存在的 schema）

.EXAMPLE
  # 仅检查是否存在
  .\verify_values_schema.ps1

  # 缺失则自动生成
  .\verify_values_schema.ps1 -AutoGenerate

  # 生成并 helm lint 验证
  .\verify_values_schema.ps1 -AutoGenerate -Validate

  # 强制覆盖重新生成
  .\verify_values_schema.ps1 -AutoGenerate -Force
#>

param(
    [string]$ChartPath = "./deploy/helm/tlm-ops-reporter",
    [switch]$AutoGenerate,
    [switch]$Validate,
    [switch]$Force
)

$ErrorActionPreference = "Continue"

# ===== 全局状态 =====
$script:Results = [System.Collections.ArrayList]@()
$script:PassCount = 0
$script:FailCount = 0
$script:WarnCount = 0

# ===== 辅助函数 =====
function Write-Check {
    param(
        [string]$CheckId,
        [string]$Description,
        [ValidateSet("PASS","FAIL","WARN")]
        [string]$Status,
        [string]$Details = ""
    )
    [void]$script:Results.Add([PSCustomObject]@{
        Check = $CheckId
        Description = $Description
        Status = $Status
        Details = $Details
    })
    $color = if ($Status -eq "PASS") {"Green"} elseif ($Status -eq "FAIL") {"Red"} else {"Yellow"}
    Write-Host "  [$Status] $CheckId : $Description" -ForegroundColor $color
    if ($Details -and $Status -ne "PASS") {
        $short = if ($Details.Length -gt 160) { $Details.Substring(0,160) + "..." } else { $Details }
        Write-Host "         $short" -ForegroundColor Gray
    }
    switch ($Status) {
        "PASS" { $script:PassCount++ }
        "FAIL" { $script:FailCount++ }
        "WARN" { $script:WarnCount++ }
    }
}

# ===== 嵌入式 Python schema 生成器 =====
function Invoke-PythonSchemaGenerator {
    param(
        [string]$ValuesYaml,
        [string]$SchemaOut
    )

    # Python here-string：从 values.yaml 推断类型生成 JSON Schema
    $pyScript = @'
import json
import sys
import yaml


def infer_type(value):
    """递归推断 YAML 值的 JSON Schema 类型"""
    # bool 必须在 int 之前判断（Python 中 bool 是 int 子类）
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string"}
    if isinstance(value, list):
        if value:
            return {"type": "array", "items": infer_type(value[0])}
        return {"type": "array"}
    if isinstance(value, dict):
        return {
            "type": "object",
            "properties": {k: infer_type(v) for k, v in value.items()},
        }
    if value is None:
        return {"type": "null"}
    return {}


def build_schema(values_dict):
    """构建顶层 JSON Schema"""
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "tlm-ops-reporter values schema",
        "description": "Auto-generated from values.yaml by verify_values_schema.ps1",
        "type": "object",
        "properties": {k: infer_type(v) for k, v in values_dict.items()},
    }


def main():
    values_path = sys.argv[1]
    schema_path = sys.argv[2]
    try:
        with open(values_path, "r", encoding="utf-8") as f:
            values = yaml.safe_load(f)
    except Exception as e:
        sys.stderr.write(f"YAML parse error: {e}\n")
        sys.exit(2)

    if not isinstance(values, dict):
        sys.stderr.write("values.yaml root is not a mapping\n")
        sys.exit(3)

    schema = build_schema(values)
    with open(schema_path, "w", encoding="utf-8") as f:
        json.dump(schema, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("SCHEMA_OK:" + schema_path)


if __name__ == "__main__":
    main()
'@

    # 写入临时 .py 文件执行（避免 python -c 多行字符串在 Windows 下的换行截断问题）
    $tempPy = [System.IO.Path]::GetTempFileName() + "_schema_gen.py"
    try {
        # 用 UTF-8 无 BOM 写入，避免 Python 读取 BOM 报错
        $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllText($tempPy, $pyScript, $utf8NoBom)
        $pyOutput = & python $tempPy $ValuesYaml $SchemaOut 2>&1
        $pyStr = ($pyOutput | Out-String)
        if ($pyStr -match "SCHEMA_OK:") {
            return $true
        } else {
            Write-Host "  [WARN] Python 输出异常: $($pyStr.Trim())" -ForegroundColor Yellow
            return $false
        }
    } catch {
        Write-Host "  [WARN] Python 调用异常: $_" -ForegroundColor Yellow
        return $false
    } finally {
        if (Test-Path $tempPy) { Remove-Item $tempPy -Force -ErrorAction SilentlyContinue }
    }
}

# ===== 静态 schema 模板（Python 不可用时的回退） =====
function Write-StaticSchemaTemplate {
    param([string]$SchemaOut)

    # 基于 deploy/helm/tlm-ops-reporter/values.yaml 已知结构手工编写
    $staticSchema = @'
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "tlm-ops-reporter values schema (static fallback)",
  "description": "Static template generated by verify_values_schema.ps1 when Python/PyYAML unavailable",
  "type": "object",
  "properties": {
    "namespace": { "type": "string" },
    "nameOverride": { "type": "string" },
    "fullnameOverride": { "type": "string" },
    "image": {
      "type": "object",
      "properties": {
        "repository": { "type": "string" },
        "tag": { "type": "string" },
        "pullPolicy": { "type": "string", "enum": ["Always", "IfNotPresent", "Never"] }
      }
    },
    "reporter": {
      "type": "object",
      "properties": {
        "mode": { "type": "string", "enum": ["cron", "once"] },
        "schedule": {
          "type": "object",
          "properties": {
            "hour": { "type": "integer", "minimum": 0, "maximum": 23 },
            "minute": { "type": "integer", "minimum": 0, "maximum": 59 }
          }
        },
        "logDir": { "type": "string" },
        "outputDir": { "type": "string" },
        "resources": {
          "type": "object",
          "properties": {
            "limits": { "type": "object" },
            "requests": { "type": "object" }
          }
        }
      }
    },
    "logsVolume": {
      "type": "object",
      "properties": {
        "existingClaim": { "type": "string" },
        "create": { "type": "boolean" },
        "size": { "type": "string" },
        "storageClassName": { "type": "string" }
      }
    },
    "outputVolume": {
      "type": "object",
      "properties": {
        "create": { "type": "boolean" },
        "size": { "type": "string" },
        "storageClassName": { "type": "string" },
        "accessMode": { "type": "string", "enum": ["ReadWriteOnce", "ReadWriteMany", "ReadOnlyMany"] }
      }
    },
    "alerts": {
      "type": "object",
      "properties": {
        "enabled": { "type": "boolean" },
        "configMapName": { "type": "string" },
        "fileName": { "type": "string" }
      }
    },
    "serviceMonitor": {
      "type": "object",
      "properties": {
        "enabled": { "type": "boolean" },
        "interval": { "type": "string" },
        "labels": { "type": "object" }
      }
    },
    "podSecurityContext": {
      "type": "object",
      "properties": {
        "runAsNonRoot": { "type": "boolean" },
        "runAsUser": { "type": "integer" },
        "fsGroup": { "type": "integer" }
      }
    },
    "containerSecurityContext": {
      "type": "object",
      "properties": {
        "readOnlyRootFilesystem": { "type": "boolean" },
        "allowPrivilegeEscalation": { "type": "boolean" },
        "capabilities": {
          "type": "object",
          "properties": {
            "drop": { "type": "array", "items": { "type": "string" } }
          }
        }
      }
    },
    "networkPolicy": {
      "type": "object",
      "properties": {
        "enabled": { "type": "boolean" }
      }
    }
  }
}
'@
    try {
        Set-Content -Path $SchemaOut -Value $staticSchema -Encoding UTF8
        return $true
    } catch {
        Write-Host "  [WARN] 静态模板写入失败: $_" -ForegroundColor Yellow
        return $false
    }
}
# ===== 路径解析 =====
$ResolvedChartPath = (Resolve-Path $ChartPath -ErrorAction SilentlyContinue).Path
if (-not $ResolvedChartPath) {
    Write-Host "[FATAL] Chart 路径不存在: $ChartPath" -ForegroundColor Red
    exit 1
}

$ValuesYamlPath = Join-Path $ResolvedChartPath "values.yaml"
$SchemaPath     = Join-Path $ResolvedChartPath "values.schema.json"
$ChartYamlPath  = Join-Path $ResolvedChartPath "Chart.yaml"

Write-Host "`n=== values.schema.json 校验脚本 ===" -ForegroundColor Cyan
Write-Host "Chart 路径: $ResolvedChartPath" -ForegroundColor Gray
Write-Host "values.yaml: $ValuesYamlPath" -ForegroundColor Gray
Write-Host "schema 路径: $SchemaPath`n" -ForegroundColor Gray

# ===== 检查 1: values.yaml 是否存在 =====
Write-Host "[1/5] 检查 values.yaml..." -ForegroundColor Cyan
if (Test-Path $ValuesYamlPath) {
    Write-Check "V1" "values.yaml 存在" "PASS"
} else {
    Write-Check "V1" "values.yaml 存在" "FAIL" "缺少 values.yaml，无法生成 schema"
    Write-Host "`n[FATAL] 无法继续，退出" -ForegroundColor Red
    exit 1
}

# ===== 检查 2: Chart.yaml 是否存在 =====
Write-Host "`n[2/5] 检查 Chart.yaml..." -ForegroundColor Cyan
if (Test-Path $ChartYamlPath) {
    Write-Check "V2" "Chart.yaml 存在" "PASS"
} else {
    Write-Check "V2" "Chart.yaml 存在" "WARN" "缺少 Chart.yaml（不影响 schema 生成，但建议补充）"
}

# ===== 检查 3: values.schema.json 是否存在 =====
Write-Host "`n[3/5] 检查 values.schema.json..." -ForegroundColor Cyan
$schemaExists = Test-Path $SchemaPath
if ($schemaExists) {
    $size = (Get-Item $SchemaPath).Length
    Write-Check "V3" "values.schema.json 已存在" "PASS" "大小: $size bytes"
    if (-not $Force) {
        Write-Host "  [INFO] schema 已存在且未指定 -Force，跳过生成" -ForegroundColor Gray
    }
} else {
    Write-Check "V3" "values.schema.json 存在性" "FAIL" "缺失（对应 release_checklist §3.7 P3 增强项）"
}

# ===== 检查 4: 缺失时自动生成 =====
Write-Host "`n[4/5] 自动生成 schema..." -ForegroundColor Cyan

$shouldGenerate = $false
if (-not $schemaExists) {
    if ($AutoGenerate) {
        $shouldGenerate = $true
        Write-Host "  [INFO] schema 缺失 + -AutoGenerate 已指定，准备生成" -ForegroundColor Gray
    } else {
        Write-Check "V4" "自动生成 schema" "WARN" "未指定 -AutoGenerate，跳过生成（建议加 -AutoGenerate 参数）"
    }
} elseif ($Force) {
    $shouldGenerate = $true
    Write-Host "  [INFO] -Force 已指定，覆盖重新生成" -ForegroundColor Gray
} else {
    Write-Check "V4" "自动生成 schema" "PASS" "schema 已存在，无需生成"
}

if ($shouldGenerate) {
    # 优先尝试 Python 动态推断
    $pythonOk = $false
    $pythonExe = (Get-Command python -ErrorAction SilentlyContinue)
    if ($pythonExe) {
        $pyCheck = & python -c "import yaml, json, sys; print('PYOK')" 2>&1
        if ($pyCheck -match "PYOK") {
            $pythonOk = $true
        }
    }

    if ($pythonOk) {
        Write-Host "  [INFO] Python + PyYAML 可用，动态推断类型..." -ForegroundColor Gray
        $genResult = Invoke-PythonSchemaGenerator -ValuesYaml $ValuesYamlPath -SchemaOut $SchemaPath
        if ($genResult) {
            Write-Check "V4" "自动生成 schema (Python 动态推断)" "PASS" "已生成: $SchemaPath"
        } else {
            Write-Check "V4" "自动生成 schema (Python 动态推断)" "FAIL" "Python 生成失败，回退静态模板"
            # 回退静态模板
            $fallbackResult = Write-StaticSchemaTemplate -SchemaOut $SchemaPath
            if ($fallbackResult) {
                Write-Check "V4b" "回退静态 schema 模板" "PASS" "已生成静态模板"
            } else {
                Write-Check "V4b" "回退静态 schema 模板" "FAIL" "静态模板写入失败"
            }
        }
    } else {
        Write-Host "  [INFO] Python/PyYAML 不可用，使用静态模板..." -ForegroundColor Gray
        $fallbackResult = Write-StaticSchemaTemplate -SchemaOut $SchemaPath
        if ($fallbackResult) {
            Write-Check "V4" "自动生成 schema (静态模板)" "PASS" "已生成静态模板: $SchemaPath"
        } else {
            Write-Check "V4" "自动生成 schema (静态模板)" "FAIL" "静态模板写入失败"
        }
    }
}

# ===== 检查 5: helm lint 验证（可选） =====
Write-Host "`n[5/5] helm lint 验证..." -ForegroundColor Cyan
if (Test-Path $SchemaPath) {
    $helmExe = (Get-Command helm -ErrorAction SilentlyContinue)
    if ($helmExe) {
        if ($Validate) {
            Write-Host "  [INFO] helm 可用 + -Validate 已指定，执行 helm lint..." -ForegroundColor Gray
            $lintOut = & helm lint $ResolvedChartPath 2>&1
            $lintStr = ($lintOut | Out-String)
            if ($LASTEXITCODE -eq 0 -and $lintStr -match "1 chart\(s\) linted, 0 chart\(s\) failed") {
                Write-Check "V5" "helm lint 验证" "PASS" "schema 通过 Helm 校验"
            } else {
                Write-Check "V5" "helm lint 验证" "FAIL" $lintStr.Trim()
            }
        } else {
            Write-Check "V5" "helm lint 验证" "WARN" "未指定 -Validate，跳过 helm lint"
        }
    } else {
        Write-Check "V5" "helm lint 验证" "WARN" "helm CLI 不可用（在 WSL/容器或安装 helm 后可验证）"
    }
} else {
    Write-Check "V5" "helm lint 验证" "WARN" "schema 仍未存在，无验证对象"
}

# ===== 报告输出 =====
Write-Host "`n=== 校验报告 ===" -ForegroundColor Cyan
Write-Host "PASS: $script:PassCount  FAIL: $script:FailCount  WARN: $script:WarnCount" -ForegroundColor $(if ($script:FailCount -eq 0) {"Green"} else {"Yellow"})
Write-Host ""
$script:Results | Format-Table -AutoSize

# ===== 退出码 =====
if ($script:FailCount -gt 0) {
    Write-Host "`n[RESULT] 存在 FAIL 项，退出码 1" -ForegroundColor Yellow
    exit 1
} else {
    Write-Host "`n[RESULT] 无 FAIL 项，退出码 0" -ForegroundColor Green
    exit 0
}

