# l2_p99_monitor

通用 P99 监控告警包：解析性能日志 + 阈值检查 + 多渠道告警。

## 特性

- ✅ **多格式解析**：bench log / comparison log / Markdown 报告（自动检测）
- ✅ **可配置阈值**：默认 1000ms，支持自定义
- ✅ **多渠道告警**：控制台彩色输出 + JSONL 日志 + Slack webhook
- ✅ **安全设计**：webhook URL 环境变量传入 + SSL 强制验证 + 异常不泄露 URL
- ✅ **零依赖**：仅使用 Python 标准库
- ✅ **可复用**：支持作为库导入或 CLI 工具使用

## 安装

```bash
# 从源码安装
cd packages/l2_p99_monitor
pip install -e .

# 或直接使用（无需安装）
python -m l2_p99_monitor --input bench_ci.log
```

## 快速开始

### CLI 使用

```bash
# 基本用法
python -m l2_p99_monitor --input bench_ci.log

# 自定义阈值 + 告警日志
python -m l2_p99_monitor --input bench_ci.log --threshold 500 --alert-log alerts.jsonl

# 指定场景和格式
python -m l2_p99_monitor --input bench_ci.log --scenario C --format bench
```

### 库导入

```python
from l2_p99_monitor import (
    P99Monitor, create_parser,
    ConsoleChannel, JsonlLogChannel, SlackChannel,
)

# 创建监控器
monitor = P99Monitor(
    parser=create_parser(scenario="C"),
    threshold=1000,
    channels=[
        ConsoleChannel(),
        JsonlLogChannel("alerts.jsonl"),
        SlackChannel(),  # 自动检测 SLACK_WEBHOOK_URL 环境变量
    ],
)

# 检查文件
result = monitor.check_file("bench_ci.log")
if result:
    print(f"状态: {'告警' if result.alerted else '正常'}")
    print(f"P99: {result.data.p99:.2f}ms")
    print(f"余量: {result.margin_pct:.1f}%")
```

### 自定义解析器

```python
from l2_p99_monitor import LogParser, ScenarioData, P99Monitor
import re

class CustomParser(LogParser):
    """自定义日志解析器"""

    def parse(self, content: str) -> ScenarioData | None:
        # 实现自定义解析逻辑
        match = re.search(r"my_p99:\s*([\d.]+)", content)
        if match:
            return ScenarioData(
                scenario="custom",
                p99=float(match.group(1)),
            )
        return None

# 使用自定义解析器
monitor = P99Monitor(parser=CustomParser(), threshold=500)
```

## 输入格式

### 1. bench log（压测原始日志）

```
【场景 C】高并发（5 个 assemble 并发，同步 IO）
  [concurrency=5]
    样本数: 5
    P50:    22.28ms
    P99:    390.10ms
    Max:    390.10ms
```

### 2. comparison log（性能对比日志）

```
── 全部场景概览 ──
  场景     P50(ms)          P99(ms)          Max(ms)
  --------------------------------------------------
  C        22.28            390.10           390.10
```

### 3. Markdown 报告

```markdown
| 场景 | 描述 | P50 (ms) | P99 (ms) | Max (ms) |
|------|------|----------|----------|----------|
| C | 高并发同步串行 | 22.28 | 390.10 | 390.10 |
```

## 告警渠道

### 控制台输出

```
============================================================
  P99 监控报告
============================================================
  场景:     C - 高并发同步串行
  P99:      390.10ms
  阈值:     1000ms
  状态:     ✅ OK（余量 61.0%）
============================================================
```

### JSONL 告警日志

```json
{"timestamp": "2026-07-27T01:42:19+08:00", "status": "OK", "scenario": "C", "p99": 5.91, "threshold": 1000.0, "margin": 99.4}
```

### Slack webhook

设置环境变量：
```bash
# 替换为你的实际 Slack webhook URL（从 Slack App 配置获取）
export SLACK_WEBHOOK_URL="<your-slack-webhook-url>"
```

超阈值时自动发送 Slack 告警。

## 退出码

| 退出码 | 含义 |
|--------|------|
| 0 | P99 正常（未超阈值） |
| 1 | P99 告警（超阈值） |
| 2 | 解析失败（日志格式不识别或场景不存在） |

## CI 集成

```yaml
- name: 监控 P99 告警
  env:
    SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK_URL }}
  run: |
    python -m l2_p99_monitor \
      --input test_reports/bench_ci.log \
      --threshold 1000 \
      --alert-log test_reports/p99_alerts.jsonl || true
```

## 安全设计

- ✅ webhook URL 通过环境变量传入，**不硬编码**
- ✅ 强制 SSL 证书验证（`ssl.create_default_context()`）
- ✅ 异常信息不泄露 webhook URL（只打印 HTTP 状态码）
- ✅ 短超时（5 秒，避免 CI 卡住）

## 许可证

MIT
