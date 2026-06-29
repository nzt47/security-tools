# GitHub Actions CI 模拟运行日志 — 关键字参数冲突扫描

> **模拟场景**: 开发者提交了包含 HIGH 风险的代码（`**payload` 未过滤保留键），
> CI 自动检测并阻断。以下是完整的 CI 运行日志。

---

## Run Details

| Field | Value |
|-------|-------|
| **Workflow** | 关键字参数冲突扫描 |
| **Event** | push |
| **Branch** | feature/add-track-event |
| **Commit** | `a3f7b2c1` |
| **Commit Message** | feat: 新增用户行为埋点 trackEvent |
| **Triggered** | 2026-06-30T01:42:17Z |
| **Status** | ❌ FAILURE |

---

## Job 1: kwarg-high-risk-scan (HIGH 风险扫描 - 阻断)

### Setup

```
Run actions/checkout@v4
  with:
    fetch-depth: 0
✓ Success - Fetching the repository
```

```
Run actions/setup-python@v5
  with:
    python-version: 3.10
    cache: pip
✓ Success - Installed Python 3.10.12
✓ Success - Setup pip cache
```

### Step: 验证扫描器存在

```
Run if [ ! -f scripts/scan_kwarg_conflicts.py ]; then
    echo "::error::扫描器脚本不存在: scripts/scan_kwarg_conflicts.py"
    exit 1
  fi
  echo "✓ 扫描器脚本存在"
✓ 扫描器脚本存在
✓ Success
```

### Step: 运行 HIGH 风险扫描 ❌ FAILED

```
Run echo "=== 关键字参数冲突扫描 (HIGH) ===" && python scripts/scan_kwarg_conflicts.py \
    --path agent/ \
    --min-risk HIGH \
    --format text \
    --output kwarg-high-risk-report.txt

=== 关键字参数冲突扫描 (HIGH) ===
{"trace_id": "f3a7b2c1d4e5f6a7", "module_name": "scan_kwarg_conflicts", "action": "scan.start", "duration_ms": 0.0, "path": "agent/", "min_risk": "HIGH"}
{"trace_id": "b8c9d0e1f2a3b4c5", "module_name": "scan_kwarg_conflicts", "action": "scan_directory.done", "duration_ms": 3421.5, "root": "agent/", "files_scanned": 341, "findings_count": 3}
{"trace_id": "d6e7f8a9b0c1d2e3", "module_name": "scan_kwarg_conflicts", "action": "scan.exit", "duration_ms": 0.0, "high_count": 1, "exit_code": 1}

================================================================================
关键字参数冲突风险扫描报告
================================================================================
扫描时间: 2026-06-30 01:42:19
总发现数: 1


────────────────────────────────────────────────────────────────────────────────
🔴 HIGH (1 处)
────────────────────────────────────────────────────────────────────────────────

  📍 agent/observability/new_module.py:28:8
     函数: _emit_structured_log
     显式 kwargs: ['trace_id', 'duration_ms', 'level']
     **展开: **(payload or {})
     冲突参数: ['trace_id', 'duration_ms', 'level']
     原因: 函数 _emit_structured_log 接受 **kwargs，显式参数
           ['trace_id', 'duration_ms', 'level'] 可能与 **(payload or {})
           中的同名键冲突
     建议: 在展开前过滤保留键:
           _RESERVED = {'level', 'trace_id', 'duration_ms'};
           safe = {k: v for k, v in (payload or {}).items()
                   if k not in _RESERVED};
           func(..., **safe)

================================================================================
汇总统计
================================================================================
  HIGH:   1 处
  MEDIUM: 0 处
  LOW:    2 处
  总计:   3 处

Process completed with exit code 1.
❌ Failure - Run python scripts/scan_kwarg_conflicts.py --path agent/ --min-risk HIGH...
```

### Step: 上传 HIGH 风险报告

```
Run actions/upload-artifact@v4
  with:
    name: kwarg-high-risk-report
    path: kwarg-high-risk-report.txt
    retention-days: 30
✓ Success - Artifact uploaded
```

### Step: HIGH 风险阻断检查

```
Run if: failure()
Run echo "::error::检测到 HIGH 级别关键字参数冲突风险，CI 已阻断"
    echo "::error::请修复后重新提交，或使用 safe_kwargs 过滤保留键"
    echo "::error::扫描命令: python scripts/scan_kwarg_conflicts.py --min-risk HIGH"
    exit 1

::error::检测到 HIGH 级别关键字参数冲突风险，CI 已阻断
::error::请修复后重新提交，或使用 safe_kwargs 过滤保留键
::error::扫描命令: python scripts/scan_kwarg_conflicts.py --min-risk HIGH
❌ Failure - HIGH 风险阻断检查
```

### Job Result

```
❌ kwarg-high-risk-scan - FAILURE (exit code 1)
Duration: 18s
```

---

## Job 2: kwarg-medium-risk-scan (MEDIUM 风险扫描 - 跳过)

```
⏭️ Skipped — Job 'kwarg-medium-risk-scan' was skipped because it depends on
   job 'kwarg-high-risk-scan' which failed.
```

---

## Job 3: fix-report (变更清单报告 - 跳过)

```
⏭️ Skipped — Job 'fix-report' was skipped because it depends on
   job 'kwarg-high-risk-scan' which failed.
   (Also only runs on pull_request events)
```

---

## Annotations

### Errors (1)

```
::error::检测到 HIGH 级别关键字参数冲突风险，CI 已阻断
  at .github/workflows/kwarg-conflict-check.yml:line 57
```

### Warnings (0)

No warnings.

---

## Artifacts

| Name | Size | Retention |
|------|------|-----------|
| `kwarg-high-risk-report` | 2.4 KB | 30 days |

### Report Content (kwarg-high-risk-report.txt)

```
================================================================================
关键字参数冲突风险扫描报告
================================================================================

🔴 HIGH (1 处)

  📍 agent/observability/new_module.py:28:8
     函数: _emit_structured_log
     显式 kwargs: ['trace_id', 'duration_ms', 'level']
     **展开: **(payload or {})
     冲突参数: ['trace_id', 'duration_ms', 'level']

修复建议:
  在展开前过滤保留键:
    _RESERVED = {"trace_id", "duration_ms", "level"}
    safe_payload = {k: v for k, v in (payload or {}).items() if k not in _RESERVED}
    _emit_structured_log(action, trace_id=tid, duration_ms=0.0, **safe_payload)
```

---

## 开发者修复流程

### 1. 查看 CI 报错

GitHub PR 页面显示：
```
❌ 关键字参数冲突扫描 / HIGH 风险扫描 (阻断) — failure (18s)
   ::error::检测到 HIGH 级别关键字参数冲突风险，CI 已阻断
```

### 2. 下载报告 Artifact

从 CI 运行页面下载 `kwarg-high-risk-report.txt`，查看详细风险位置和修复建议。

### 3. 修复代码

```python
# 修复前 (HIGH 风险):
def track_event(event_name, payload=None):
    _emit_structured_log(
        f"track.{event_name}",
        trace_id="xxx",
        duration_ms=0.0,
        **(payload or {}),  # ← 冲突！
    )

# 修复后 (安全):
def track_event(event_name, payload=None):
    _RESERVED = {"action", "trace_id", "duration_ms", "level", "module_name"}
    safe_payload = {k: v for k, v in (payload or {}).items() if k not in _RESERVED}
    _emit_structured_log(
        f"track.{event_name}",
        trace_id="xxx",
        duration_ms=0.0,
        **safe_payload,  # ← 已过滤
    )
```

### 4. 本地验证

```bash
# 提交前本地验证
python scripts/scan_kwarg_conflicts.py --path agent/ --min-risk HIGH
# ✓ HIGH 风险扫描通过（0 处发现）

# 或使用 pre-commit hook
pre-commit run kwarg-conflict-scan --all-files
# ✓ Passed
```

### 5. 重新提交

```bash
git add agent/observability/new_module.py
git commit -m "fix: 过滤 trackEvent 的保留键，修复 kwarg 冲突"
git push
# CI 重新运行 → ✅ SUCCESS
```

---

## 修复后 CI 运行日志（成功）

```
Run echo "=== 关键字参数冲突扫描 (HIGH) ===" && python scripts/scan_kwarg_conflicts.py \
    --path agent/ \
    --min-risk HIGH \
    --format text \
    --output kwarg-high-risk-report.txt

=== 关键字参数冲突扫描 (HIGH) ===
{"trace_id": "a1b2c3d4e5f6a7b8", "module_name": "scan_kwarg_conflicts", "action": "scan.start", ...}
{"trace_id": "c9d0e1f2a3b4c5d6", "module_name": "scan_kwarg_conflicts", "action": "scan_directory.done", "files_scanned": 341, "findings_count": 2}
{"trace_id": "e7f8a9b0c1d2e3f4", "module_name": "scan_kwarg_conflicts", "action": "scan.exit", "high_count": 0, "exit_code": 0}

================================================================================
关键字参数冲突风险扫描报告
================================================================================
扫描时间: 2026-06-30 01:48:22
总发现数: 0
================================================================================
汇总统计
================================================================================
  HIGH:   0 处
  MEDIUM: 0 处
  LOW:    0 处
  总计:   0 处

✓ HIGH 风险扫描通过（0 处发现）
✓ Success

✅ kwarg-high-risk-scan - SUCCESS (16s)
✅ kwarg-medium-risk-scan - SUCCESS (14s)
   ✓ 无 MEDIUM 级别风险
```
