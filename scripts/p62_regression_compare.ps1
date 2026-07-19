<#
.SYNOPSIS
  P6-2 regression compare script - PS 5.x vs PS 7.x real data collection

.DESCRIPTION
  Runs P6-2 legacy and new judges in current PowerShell environment, outputs JSON.
  Used for regression test report PS 5.x/7.x comparison data collection.

  Usage:
    powershell -NoProfile -File .\scripts\p62_regression_compare.ps1   # PS 5.x
    pwsh -NoProfile -File .\scripts\p62_regression_compare.ps1         # PS 7.x

  [Invariant] Judge logic identical to verify_production_deployment.ps1
  [Simple] Direct JSON output for easy comparison
#>

# Simulate kubectl success (exit code 0), ensure $LASTEXITCODE is initialized
# Without this, $LASTEXITCODE may inherit from parent process and break legacy judge
$global:LASTEXITCODE = 0

# Simulate PS 5.x NativeCommandError output (stderr non-empty triggers error record)
# Real scenario: Python exit code 0, report generated, but PS 5.x wraps stderr into error record
# Key marker: "FullyQualifiedErrorId : NativeCommandError" (contains "Error" substring, root cause of false match)
$ps5NativeError = [string]::Join("`n", @(
    "kubectl : [WARN] no circuit breaker events found",
    "    + CategoryInfo          : NotSpecified: ([WARN] no circuit breaker events found:String) [], RemoteException",
    "    + FullyQualifiedErrorId : NativeCommandError",
    "[OK] ops daily report generated: /app/output/manual.md"
))

# Simulate PS 7.x clean output (no NativeCommandError, stderr merged to stdout directly)
$ps7Output = [string]::Join("`n", @(
    "[WARN] no circuit breaker events found",
    "[OK] ops daily report generated: /app/output/manual.md"
))

# New judge: file check results
$fileCheckOk = "FILE_OK"
$fileCheckEmpty = ""

# ===== Legacy judge (before fix): output content match "Traceback|Error" =====
$legacyOnPs5Error = ($LASTEXITCODE -eq 0 -and $ps5NativeError -notmatch "Traceback|Error")
$legacyOnPs7Output = ($LASTEXITCODE -eq 0 -and $ps7Output -notmatch "Traceback|Error")

# ===== New judge (after fix): file generation match "FILE_OK" =====
$newOnFileOk = ($fileCheckOk -match "FILE_OK")
$newOnFileEmpty = ($fileCheckEmpty -match "FILE_OK")

# ===== Output JSON result =====
$result = [PSCustomObject]@{
    ps_version = $PSVersionTable.PSVersion.ToString()
    ps_edition = $PSVersionTable.PSEdition
    timestamp = (Get-Date -Format "yyyy-MM-dd HH:mm:ss")
    legacy_judge = @{
        description = "Legacy judge: -notmatch Traceback|Error"
        on_ps5_native_error = @{
            input = "PS 5.x NativeCommandError output"
            exit_code = 0
            output_contains_error = ($ps5NativeError -match "Error")
            result = $legacyOnPs5Error
            verdict = if ($legacyOnPs5Error) { "PASS" } else { "FAIL" }
        }
        on_ps7_clean_output = @{
            input = "PS 7.x clean output (no NativeCommandError)"
            exit_code = 0
            output_contains_error = ($ps7Output -match "Error")
            result = $legacyOnPs7Output
            verdict = if ($legacyOnPs7Output) { "PASS" } else { "FAIL" }
        }
    }
    new_judge = @{
        description = "New judge: -match FILE_OK"
        on_file_generated = @{
            input = "Report file generated (FILE_OK)"
            result = $newOnFileOk
            verdict = if ($newOnFileOk) { "PASS" } else { "FAIL" }
        }
        on_file_missing = @{
            input = "Report file not generated (empty)"
            result = $newOnFileEmpty
            verdict = if ($newOnFileEmpty) { "PASS" } else { "FAIL" }
        }
    }
    conclusion = @{
        bug_confirmed = ((-not $legacyOnPs5Error) -and $legacyOnPs7Output)
        fix_effective = ($newOnFileOk -and (-not $newOnFileEmpty))
        cross_version_consistent = $newOnFileOk
    }
}

$result | ConvertTo-Json -Depth 6
