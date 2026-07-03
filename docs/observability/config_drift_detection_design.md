# 配置漂移检测 MVP — 详细实现设计

> **任务编号**：Phase 4 Task 4
> **预计工时**：2h
> **优先级**：P2
> **依赖**：Task 1 完成 ✅
> **设计文档版本**：v1.0
> **生成时间**：2026-07-04

---

## 一、问题背景与目标

### 1.1 问题定义

当前 `ObservabilityConfig` 系统（36 个配置项）存在两类不可见风险：

| 风险类型 | 描述 | 现有防护 |
|----------|------|----------|
| **默认值漂移** | `OBSERVABILITY_VALIDATION_RULES[i].default` 在代码提交中被修改 | git diff（人工 review） |
| **运行时漂移** | 部署后通过 `config.set("http.timeout_sec", 60)` 修改了运行时值，但未记录到任何持久化存储 | `_change_log`（仅内存，重启丢失） |

**核心问题**：当生产环境出现"配置被改了但没人知道"的情况时，缺乏可观测性和审计能力。

### 1.2 MVP 目标

1. **快照生成**：在部署/CI 阶段导出"已知良好状态"的配置快照
2. **漂移检测**：对比运行时配置与快照，识别 modified/removed/added 三类差异
3. **CI 集成**：在 PR 阶段自动检测默认值漂移，防止未授权修改
4. **生产可观测**：提供 CLI 命令在生产环境运行漂移检测，输出结构化报告

### 1.3 非目标（MVP 不实现）

- 配置回滚机制（列入 Phase 5）
- 实时漂移监控（仅做批量/按需检测）
- 多环境快照对比（仅单快照对比）
- 漂移告警通知（Phase 5 集成到 alert_notifier）

---

## 二、架构设计

### 2.1 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                    配置漂移检测 MVP 架构                     │
└─────────────────────────────────────────────────────────────┘

┌──────────────────┐    生成快照    ┌──────────────────────┐
│ observability_   │ ─────────────→ │ config_snapshot.json │
│ config.py        │                │ (已知良好状态)        │
│ (36 个默认值)    │                └──────────┬───────────┘
└──────────────────┘                           │
                                               │ 对比
                                               ▼
┌──────────────────┐    运行时读取   ┌──────────────────────┐
│ 运行时 Config    │ ─────────────→ │ check_config_drift   │
│ (可能被 set()    │                │ .py 检测脚本         │
│  修改过)         │                └──────────┬───────────┘
└──────────────────┘                           │
                                               ▼
                                    ┌──────────────────────┐
                                    │ 漂移报告（JSON/文本）│
                                    │ - modified           │
                                    │ - removed            │
                                    │ - added              │
                                    └──────────────────────┘
                                               │
                              ┌────────────────┼────────────────┐
                              ▼                ▼                ▼
                        ┌──────────┐     ┌──────────┐     ┌──────────┐
                        │ CI       │     │ 生产 CLI │     │ 日志/告警│
                        │ (PR 检查)│     │ (按需运行)│     │ (Phase 5)│
                        └──────────┘     └──────────┘     └──────────┘
```

### 2.2 三类漂移定义

| 类型 | 定义 | 严重等级 | 处理方式 |
|------|------|----------|----------|
| `modified` | 快照中有值 X，运行时为值 Y（X ≠ Y） | **high** | 阻断 CI + 告警 |
| `removed` | 快照中存在路径 P，运行时缺失 | **critical** | 阻断 CI + 告警 |
| `added` | 快照中不存在路径 P，运行时存在 | **low** | 仅警告（可能是新增配置项） |

### 2.3 快照存储策略

**MVP 选择**：单快照文件，存储在仓库内

| 方案 | 路径 | 优点 | 缺点 | MVP |
|------|------|------|------|-----|
| A | `docs/observability/config_snapshot_master.json` | 随仓库版本化，CI 易访问 | 修改快照需提交 | ✅ |
| B | `~/.agent/config_snapshot.json` | 不污染仓库 | 跨机器不可用 | ❌ |
| C | `data/config/snapshots/<timestamp>.json` | 支持历史快照 | 管理复杂 | Phase 5 |

**选择方案 A**：快照提交到仓库，master 分支保持"权威快照"，PR 分支对比。

---

## 三、数据结构设计

### 3.1 快照文件格式（config_snapshot_master.json）

```json
{
  "version": "1.0",
  "generated_at": "2026-07-04T01:30:00",
  "generated_from": "observability_config.py@<git_sha>",
  "total_paths": 36,
  "config": {
    "tracing": {
      "env": "production",
      "log_level": "INFO",
      "sampler_ratio": 0.1
    },
    "http": {
      "max_retries": 3,
      "timeout_sec": 30,
      "connect_timeout_sec": 10,
      "pool_size": 20
    },
    "cache": {
      "l1_max_size": 1000
    },
    "scheduler": {
      "check_interval_sec": 10,
      "command_timeout_sec": 300,
      "max_history_lines": 1000,
      "heartbeat_interval_sec": 60,
      "max_heartbeat_history": 1440
    },
    "llm_monitor": {
      "max_records": 500
    },
    "loki": {
      "push_timeout_sec": 10,
      "query_timeout_sec": 30
    },
    "alert": {
      "timeout_sec": 30
    }
  },
  "metadata": {
    "tracing.env": {
      "default": "production",
      "validator": "non_empty_string",
      "description": "追踪环境标识"
    },
    "http.timeout_sec": {
      "default": 30,
      "validator": "range(1, 300)",
      "description": "HTTP 客户端默认超时（秒）"
    }
  }
}
```

**字段说明**：
- `version`：快照格式版本（语义化版本，便于后续升级）
- `generated_at`：ISO 8601 时间戳
- `generated_from`：源码 git SHA，便于追溯
- `config`：嵌套 dict，与 `ObservabilityConfig.get_all()` 输出一致
- `metadata`：每个配置项的元信息（默认值/验证器/描述），用于漂移报告

### 3.2 漂移报告格式（drift_report.json）

```json
{
  "version": "1.0",
  "scan_at": "2026-07-04T02:00:00",
  "snapshot_source": "docs/observability/config_snapshot_master.json",
  "snapshot_generated_at": "2026-07-04T01:30:00",
  "current_config_source": "runtime",
  "summary": {
    "total_paths": 36,
    "drift_count": 2,
    "modified": 1,
    "removed": 0,
    "added": 1
  },
  "drifts": [
    {
      "path": "http.timeout_sec",
      "type": "modified",
      "severity": "high",
      "snapshot_value": 30,
      "current_value": 60,
      "description": "HTTP 客户端默认超时（秒）",
      "suggestion": "如需修改默认值，请更新 observability_config.py 的 ValidationRule.default 并重新生成快照"
    },
    {
      "path": "feature.new_flag",
      "type": "added",
      "severity": "low",
      "snapshot_value": null,
      "current_value": true,
      "description": "(新增配置项，未在快照中)",
      "suggestion": "如为新增配置项，请添加 ValidationRule 并重新生成快照"
    }
  ]
}
```

---

## 四、实现方案

### 4.1 scripts/config_snapshot.py（快照生成脚本）

**职责**：导出当前 `ObservabilityConfig` 的默认配置 + 元信息到 JSON 文件。

**核心代码设计**：

```python
#!/usr/bin/env python3
"""配置快照生成工具

导出当前 observability_config.py 的默认配置和元信息到 JSON 文件，
作为配置漂移检测的基准快照。

使用方式：
  # 生成快照到默认路径
  python scripts/config_snapshot.py

  # 指定输出路径
  python scripts/config_snapshot.py --output docs/observability/config_snapshot_master.json

  # 包含运行时值（默认仅导出默认值）
  python scripts/config_snapshot.py --include-runtime
"""

import json
import os
import sys
import argparse
from datetime import datetime
from pathlib import Path


def generate_snapshot(include_runtime: bool = False) -> dict:
    """生成配置快照

    Args:
        include_runtime: 是否包含运行时值（True 则同时记录默认值和运行时值）

    Returns:
        快照 dict，符合 3.1 节定义的格式
    """
    # 延迟导入，避免脚本启动时的循环依赖
    from agent.monitoring.observability_config import (
        OBSERVABILITY_VALIDATION_RULES,
        get_observability_config,
        reset_observability_config,
    )

    # 重置配置（确保读取的是默认值，而非被测试污染的运行时值）
    reset_observability_config()
    config = get_observability_config()
    config_tree = config.get_all()  # 嵌套 dict

    # 构建 metadata：每个配置路径的元信息
    metadata = {}
    for rule in OBSERVABILITY_VALIDATION_RULES:
        metadata[rule.path] = {
            "default": rule.default,
            "description": rule.description,
            "error_message": rule.error_message,
        }

    # 获取 git SHA（用于追溯）
    git_sha = _get_git_sha()

    return {
        "version": "1.0",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "generated_from": f"observability_config.py@{git_sha}",
        "total_paths": len(OBSERVABILITY_VALIDATION_RULES),
        "config": config_tree,
        "metadata": metadata,
    }


def _get_git_sha() -> str:
    """获取当前 git HEAD SHA（失败返回 'unknown'）"""
    import subprocess
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def main():
    parser = argparse.ArgumentParser(description="配置快照生成工具")
    parser.add_argument(
        "--output", "-o",
        default="docs/observability/config_snapshot_master.json",
        help="输出文件路径（默认: docs/observability/config_snapshot_master.json）",
    )
    parser.add_argument(
        "--include-runtime",
        action="store_true",
        help="包含运行时值（默认仅导出默认值）",
    )
    args = parser.parse_args()

    snapshot = generate_snapshot(include_runtime=args.include_runtime)

    # 确保输出目录存在
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    print(f"✓ 配置快照已生成: {output_path}")
    print(f"  版本: {snapshot['version']}")
    print(f"  生成时间: {snapshot['generated_at']}")
    print(f"  源: {snapshot['generated_from']}")
    print(f"  配置项总数: {snapshot['total_paths']}")


if __name__ == "__main__":
    main()
```

**关键设计点**：
1. **调用 `reset_observability_config()`**：确保读取的是默认值，而非被测试用例修改过的运行时值
2. **延迟导入**：避免脚本启动时的循环依赖
3. **git SHA 追溯**：快照中记录源码版本，便于后续比对
4. **metadata 完整**：不仅记录值，还记录每个配置项的描述和默认值，便于生成可读的漂移报告

---

### 4.2 scripts/check_config_drift.py（漂移检测脚本）

**职责**：对比快照与当前运行时配置，输出漂移报告。

**核心代码设计**：

```python
#!/usr/bin/env python3
"""配置漂移检测工具

对比当前运行时配置与快照文件，识别 modified/removed/added 三类漂移。

使用方式：
  # 控制台报告
  python scripts/check_config_drift.py

  # JSON 报告（CI 使用）
  python scripts/check_config_drift.py --json --output drift_report.json

  # 阻断模式（检测到 high/critical 漂移时退出码 1）
  python scripts/check_config_drift.py --fail-on-drift

  # 指定快照文件
  python scripts/check_config_drift.py --snapshot path/to/snapshot.json

CI 集成：
  --fail-on-drift 参数使脚本在检测到 high/critical 漂移时以非零退出码退出
"""

import json
import sys
import argparse
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple


def _flatten_config(config: Dict[str, Any], prefix: str = "") -> Dict[str, Any]:
    """将嵌套 dict 展平为 {path: value} 形式

    示例：
      {"http": {"timeout": 30}} → {"http.timeout": 30}
    """
    result = {}
    for key, value in config.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            result.update(_flatten_config(value, path))
        else:
            result[path] = value
    return result


def load_snapshot(snapshot_path: str) -> Dict[str, Any]:
    """加载快照文件"""
    with open(snapshot_path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_current_config() -> Dict[str, Any]:
    """获取当前运行时配置（展平为 {path: value} 形式）"""
    from agent.monitoring.observability_config import get_observability_config
    config = get_observability_config()
    return _flatten_config(config.get_all())


def _classify_severity(drift_type: str, path: str) -> str:
    """根据漂移类型和路径分类严重等级

    Returns:
        critical / high / medium / low
    """
    if drift_type == "removed":
        return "critical"
    if drift_type == "modified":
        # 关键基础设施配置（HTTP/缓存/调度器）的修改视为 high
        critical_prefixes = ("http.", "cache.", "scheduler.", "tracing_cache.")
        if any(path.startswith(p) for p in critical_prefixes):
            return "high"
        return "medium"
    if drift_type == "added":
        return "low"
    return "low"


def detect_drift(
    snapshot: Dict[str, Any],
    current: Dict[str, Any],
    metadata: Dict[str, Any] = None,
) -> List[Dict[str, Any]]:
    """检测配置漂移

    Args:
        snapshot: 快照配置（展平后的 {path: value}）
        current: 当前运行时配置（展平后的 {path: value}）
        metadata: 可选，配置项元信息（用于生成可读报告）

    Returns:
        漂移列表，每项包含 path/type/severity/snapshot_value/current_value/description/suggestion
    """
    drifts = []
    metadata = metadata or {}

    # 检测 modified 和 removed
    for path, snapshot_value in snapshot.items():
        if path not in current:
            drifts.append({
                "path": path,
                "type": "removed",
                "severity": _classify_severity("removed", path),
                "snapshot_value": snapshot_value,
                "current_value": None,
                "description": metadata.get(path, {}).get("description", "(无描述)"),
                "suggestion": f"配置项 {path} 在运行时缺失，请检查 observability_config.py 是否已移除该 ValidationRule",
            })
        elif current[path] != snapshot_value:
            drifts.append({
                "path": path,
                "type": "modified",
                "severity": _classify_severity("modified", path),
                "snapshot_value": snapshot_value,
                "current_value": current[path],
                "description": metadata.get(path, {}).get("description", "(无描述)"),
                "suggestion": f"如需修改默认值，请更新 observability_config.py 的 ValidationRule.default 并重新生成快照",
            })

    # 检测 added
    for path, current_value in current.items():
        if path not in snapshot:
            drifts.append({
                "path": path,
                "type": "added",
                "severity": _classify_severity("added", path),
                "snapshot_value": None,
                "current_value": current_value,
                "description": "(新增配置项，未在快照中)",
                "suggestion": "如为新增配置项，请添加 ValidationRule 并重新生成快照",
            })

    # 按严重等级排序：critical > high > medium > low
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    drifts.sort(key=lambda d: (severity_order.get(d["severity"], 99), d["path"]))
    return drifts


def print_console_report(snapshot: Dict[str, Any], drifts: List[Dict[str, Any]]):
    """打印控制台友好的漂移报告"""
    print("\n" + "=" * 70)
    print("  配置漂移检测报告")
    print("=" * 70)
    print(f"  快照源:       {snapshot.get('generated_from', 'unknown')}")
    print(f"  快照生成时间: {snapshot.get('generated_at', 'unknown')}")
    print(f"  配置项总数:   {snapshot.get('total_paths', 0)}")
    print(f"  漂移数量:     {len(drifts)}")
    print("-" * 70)

    if not drifts:
        print("  ✓ 无漂移检测到，运行时配置与快照一致")
        print("=" * 70)
        return

    # 按类型分组统计
    by_type = {"modified": 0, "removed": 0, "added": 0}
    for d in drifts:
        by_type[d["type"]] += 1
    print(f"  modified: {by_type['modified']}  removed: {by_type['removed']}  added: {by_type['added']}")
    print("-" * 70)

    for d in drifts:
        severity_icon = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}.get(d["severity"], "⚪")
        print(f"\n  {severity_icon} [{d['severity'].upper()}] {d['path']} ({d['type']})")
        print(f"      描述: {d['description']}")
        if d["type"] == "modified":
            print(f"      快照值: {d['snapshot_value']}")
            print(f"      当前值: {d['current_value']}")
        elif d["type"] == "removed":
            print(f"      快照值: {d['snapshot_value']} (运行时已移除)")
        elif d["type"] == "added":
            print(f"      当前值: {d['current_value']} (快照中不存在)")
        print(f"      建议: {d['suggestion']}")

    print("\n" + "=" * 70)


def main():
    parser = argparse.ArgumentParser(description="配置漂移检测工具")
    parser.add_argument(
        "--snapshot", "-s",
        default="docs/observability/config_snapshot_master.json",
        help="快照文件路径（默认: docs/observability/config_snapshot_master.json）",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="输出 JSON 格式报告（默认: 控制台文本）",
    )
    parser.add_argument(
        "--output", "-o",
        help="输出文件路径（仅 --json 模式有效，默认输出到 stdout）",
    )
    parser.add_argument(
        "--fail-on-drift",
        action="store_true",
        help="检测到 high/critical 漂移时以非零退出码退出（CI 模式）",
    )
    args = parser.parse_args()

    # 加载快照
    if not Path(args.snapshot).exists():
        print(f"错误: 快照文件不存在: {args.snapshot}", file=sys.stderr)
        print(f"请先运行: python scripts/config_snapshot.py --output {args.snapshot}", file=sys.stderr)
        sys.exit(2)

    snapshot_data = load_snapshot(args.snapshot)
    snapshot_config = _flatten_config(snapshot_data["config"])
    metadata = snapshot_data.get("metadata", {})

    # 获取当前运行时配置
    current_config = get_current_config()

    # 检测漂移
    drifts = detect_drift(snapshot_config, current_config, metadata)

    # 输出报告
    if args.json:
        report = {
            "version": "1.0",
            "scan_at": datetime.now().isoformat(timespec="seconds"),
            "snapshot_source": args.snapshot,
            "snapshot_generated_at": snapshot_data.get("generated_at"),
            "current_config_source": "runtime",
            "summary": {
                "total_paths": snapshot_data.get("total_paths", 0),
                "drift_count": len(drifts),
                "modified": sum(1 for d in drifts if d["type"] == "modified"),
                "removed": sum(1 for d in drifts if d["type"] == "removed"),
                "added": sum(1 for d in drifts if d["type"] == "added"),
            },
            "drifts": drifts,
        }
        report_json = json.dumps(report, ensure_ascii=False, indent=2)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(report_json)
            print(f"✓ 漂移报告已写入: {args.output}", file=sys.stderr)
        else:
            print(report_json)
    else:
        print_console_report(snapshot_data, drifts)

    # CI 阻断模式
    if args.fail_on_drift:
        critical_count = sum(1 for d in drifts if d["severity"] == "critical")
        high_count = sum(1 for d in drifts if d["severity"] == "high")
        if critical_count > 0 or high_count > 0:
            print(f"\n错误: 检测到 {critical_count} 个 critical + {high_count} 个 high 漂移", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
```

**关键设计点**：
1. **`_flatten_config` 工具函数**：将嵌套 dict 展平为 `{path: value}`，便于对比（如 `{"http": {"timeout": 30}}` → `{"http.timeout": 30}`）
2. **严重等级分类**：根据漂移类型 + 配置路径前缀分类，关键基础设施（http/cache/scheduler）的修改视为 high
3. **可读的漂移建议**：每条漂移都附带 `suggestion` 字段，告知用户如何处理
4. **CI 阻断模式**：`--fail-on-drift` 仅在 critical/high 漂移时退出码 1，medium/low 不阻断

---

### 4.3 .github/workflows/config-drift-guard.yml（CI 集成）

**职责**：在 PR 阶段自动运行漂移检测，防止未授权的默认值修改。

**workflow 设计**：

```yaml
# Config Drift Guard — 配置漂移检测
# 在 PR 合并前自动检测 observability_config.py 的默认值漂移
# 详见: docs/observability/config_drift_detection_design.md

name: Config Drift Guard

on:
  pull_request:
    paths:
      - 'agent/monitoring/observability_config.py'
      - 'docs/observability/config_snapshot_master.json'
      - 'scripts/config_snapshot.py'
      - 'scripts/check_config_drift.py'
      - '.github/workflows/config-drift-guard.yml'
  workflow_dispatch:

permissions:
  contents: read
  pull-requests: write

jobs:
  drift-detection:
    name: 配置漂移检测
    runs-on: ubuntu-latest
    timeout-minutes: 5

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: 安装依赖
        run: |
          pip install -e . --quiet || true

      - name: 重新生成快照（基于当前分支代码）
        run: |
          python scripts/config_snapshot.py \
            --output /tmp/current_snapshot.json

      - name: 加载 master 快照
        id: master
        run: |
          if [ ! -f docs/observability/config_snapshot_master.json ]; then
            echo "::warning::master 快照不存在，跳过漂移检测"
            echo "skip=true" >> $GITHUB_OUTPUT
            exit 0
          fi
          echo "skip=false" >> $GITHUB_OUTPUT

      - name: 检测默认值漂移
        if: steps.master.outputs.skip != 'true'
        id: drift
        run: |
          # 对比 master 快照（已知良好状态）与当前分支生成的快照
          python scripts/check_config_drift.py \
            --snapshot docs/observability/config_snapshot_master.json \
            --json \
            --output /tmp/drift_report.json \
            --fail-on-drift || EXIT_CODE=$?

          # 提取漂移统计
          DRIFT_COUNT=$(python -c "import json; d=json.load(open('/tmp/drift_report.json')); print(d['summary']['drift_count'])")
          echo "drift_count=$DRIFT_COUNT" >> $GITHUB_OUTPUT
          echo "::notice::漂移检测完成 — 发现 $DRIFT_COUNT 个漂移"

          # --fail-on-drift 模式下，high/critical 漂移会以非零退出码退出
          exit ${EXIT_CODE:-0}

      - name: 上传漂移报告
        if: always() && steps.master.outputs.skip != 'true'
        uses: actions/upload-artifact@v4
        with:
          name: config-drift-report
          path: /tmp/drift_report.json
          retention-days: 30

      - name: PR 评论（漂移详情）
        if: always() && steps.master.outputs.skip != 'true' && steps.drift.outputs.drift_count > 0
        uses: actions/github-script@v7
        with:
          script: |
            const fs = require('fs');
            const report = JSON.parse(fs.readFileSync('/tmp/drift_report.json', 'utf-8'));
            const summary = report.summary;

            let body = `## 📊 配置漂移检测报告\n\n`;
            body += `| 类型 | 数量 |\n|------|------|\n`;
            body += `| modified | ${summary.modified} |\n`;
            body += `| removed | ${summary.removed} |\n`;
            body += `| added | ${summary.added} |\n`;
            body += `| **合计** | **${summary.drift_count}** |\n\n`;

            if (report.drifts.length > 0) {
              body += `### 漂移详情（前 10 个）\n\n`;
              body += `| 路径 | 类型 | 严重 | 快照值 | 当前值 |\n`;
              body += `|------|------|------|--------|--------|\n`;
              for (const d of report.drifts.slice(0, 10)) {
                body += `| \`${d.path}\` | ${d.type} | ${d.severity} | ${d.snapshot_value} | ${d.current_value} |\n`;
              }
              if (report.drifts.length > 10) {
                body += `\n_...还有 ${report.drifts.length - 10} 个漂移，详见 artifact_\n`;
              }
            }

            body += `\n### 处理建议\n`;
            body += `- **modified**: 如确需修改默认值，请同时更新 \`docs/observability/config_snapshot_master.json\`\n`;
            body += `- **removed**: 移除配置项时，请同步删除快照中的对应路径\n`;
            body += `- **added**: 新增配置项时，请运行 \`python scripts/config_snapshot.py\` 更新快照\n`;

            github.rest.issues.createComment({
              issue_number: context.issue.number,
              owner: context.repo.owner,
              repo: context.repo.repo,
              body: body
            });
```

**关键设计点**：
1. **触发条件**：仅在 `observability_config.py` / `config_snapshot_master.json` / 脚本本身变更时触发，避免无谓的 CI 开销
2. **两阶段检测**：
   - Step 1: 基于当前分支代码重新生成快照（`/tmp/current_snapshot.json`）
   - Step 2: 对比 master 快照与当前分支快照
3. **PR 评论**：检测到漂移时自动评论，附带 markdown 表格
4. **artifact 上传**：完整报告作为 artifact 保留 30 天

---

## 五、实现步骤

### 5.1 实现顺序

| 步骤 | 文件 | 工时 | 依赖 |
|------|------|------|------|
| 1 | `scripts/config_snapshot.py`（快照生成） | 0.5h | 无 |
| 2 | 生成初始快照 `docs/observability/config_snapshot_master.json` | 0.1h | 步骤 1 |
| 3 | `scripts/check_config_drift.py`（漂移检测） | 1h | 步骤 1 |
| 4 | `.github/workflows/config-drift-guard.yml`（CI 集成） | 0.3h | 步骤 2-3 |
| 5 | 验证：模拟漂移 + 运行检测 + 检查 CI 输出 | 0.1h | 步骤 1-4 |

**合计**：2h

### 5.2 验证方案

**单元测试**（可选，MVP 阶段暂不强制）：
```python
# tests/unit/test_config_drift.py
def test_no_drift_when_unchanged():
    """快照与运行时一致时，无漂移"""

def test_modified_drift_detected():
    """修改 http.timeout_sec 后，检测到 modified 漂移"""

def test_removed_drift_detected():
    """从运行时移除配置项后，检测到 removed 漂移"""

def test_added_drift_detected():
    """运行时新增配置项后，检测到 added 漂移"""

def test_severity_classification():
    """验证严重等级分类逻辑"""
```

**手动验证步骤**：
```bash
# 1. 生成初始快照
python scripts/config_snapshot.py

# 2. 验证无漂移（此时运行时 = 快照）
python scripts/check_config_drift.py
# 期望输出: ✓ 无漂移检测到

# 3. 模拟漂移：临时修改运行时配置
python -c "
from agent.monitoring.observability_config import get_observability_config
config = get_observability_config()
config.set('http.timeout_sec', 999)  # 修改运行时值
"

# 4. 再次检测，应发现 modified 漂移
python scripts/check_config_drift.py
# 期望输出: 🟠 [HIGH] http.timeout_sec (modified) ...

# 5. 验证 CI 阻断模式
python scripts/check_config_drift.py --fail-on-drift
echo "Exit code: $?"
# 期望输出: Exit code: 1
```

---

## 六、与现有系统的关系

### 6.1 与 `_change_log` 的关系

| 维度 | `_change_log`（现有） | 快照 + 漂移检测（新增） |
|------|----------------------|--------------------------|
| 数据源 | 运行时 `config.set()` 调用 | 快照文件 vs 运行时配置 |
| 持久化 | 否（内存，重启丢失） | 是（JSON 文件，git 版本化） |
| 检测时机 | 实时（每次 set 记录） | 批量（按需运行 / CI 触发） |
| 检测能力 | 单次变更追溯 | 配置状态对比 |
| 适用场景 | 调试单次变更 | 审计整体配置状态 |

**互补关系**：
- `_change_log` 回答："谁在什么时候改了什么？"（事件视角）
- 漂移检测回答："当前配置与已知良好状态的差异是什么？"（状态视角）

### 6.2 与 `check_hardcoded_boundaries.py` 的关系

| 维度 | `check_hardcoded_boundaries.py` | `check_config_drift.py` |
|------|----------------------------------|--------------------------|
| 检测对象 | 源码中的硬编码数值 | 运行时配置值 |
| 检测时机 | CI 阶段（静态分析） | CI + 运行时（动态对比） |
| 检测目标 | 防止新增硬编码 | 防止配置被未授权修改 |
| 数据源 | AST 解析 `.py` 文件 | 快照 JSON vs 运行时 dict |

**协同关系**：
- `check_hardcoded_boundaries.py` 防止"代码层面的硬编码"
- `check_config_drift.py` 防止"运行时层面的配置漂移"
- 两者共同构成"配置治理双防线"

---

## 七、限制与未来演进

### 7.1 MVP 已知限制

1. **单快照对比**：MVP 仅支持与单一快照对比，无法追溯多次变更历史
2. **无自动回滚**：检测到漂移后仅告警，不自动恢复
3. **无实时监控**：仅在 CI / 手动触发时检测，非实时
4. **快照需手动更新**：修改 `ValidationRule.default` 后需手动运行 `config_snapshot.py`
5. **类型对比限制**：JSON 序列化后，`int` 和 `float` 可能被误判（如 `30` vs `30.0`）

### 7.2 Phase 5 演进方向

| 演进项 | 描述 | 优先级 |
|--------|------|--------|
| 多环境快照 | 支持 dev/staging/prod 多快照对比 | P1 |
| 自动回滚 | 检测到 critical 漂移时自动恢复 | P2 |
| 实时监控 | 集成 Prometheus 指标，实时暴露漂移状态 | P1 |
| 漂移告警 | 集成 alert_notifier，critical 漂移触发告警 | P1 |
| 配置审计日志 | 将漂移事件推送至 Loki，长期保留 | P2 |
| 智能推荐 | 基于历史负载数据推荐最优配置值 | P3 |

---

## 八、附录

### 8.1 命令速查

```bash
# 生成快照（master 分支）
python scripts/config_snapshot.py

# 检测漂移（控制台）
python scripts/check_config_drift.py

# 检测漂移（JSON 输出）
python scripts/check_config_drift.py --json --output drift_report.json

# CI 阻断模式
python scripts/check_config_drift.py --fail-on-drift

# 指定快照文件
python scripts/check_config_drift.py --snapshot path/to/snapshot.json
```

### 8.2 相关文档

- [Phase 4 行动计划](phase4_plan.md) — Task 4 配置漂移检测 MVP
- [Phase 3 最终执行总结](phase3_final_summary.md) — 前置阶段成果
- [ObservabilityConfig 源码](../agent/monitoring/observability_config.py) — 配置系统实现
