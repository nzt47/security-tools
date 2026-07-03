# 日志性能守护 CI 流水线文档

> 文档版本：1.1（新增依赖注入单元测试 job 和邮件通知 job）
> 生成时间：2026-07-04
> Workflow 文件：[`.github/workflows/log-perf-guard.yml`](../.github/workflows/log-perf-guard.yml)

## 一、流水线架构

```
┌─────────────────────────────────────────────────────────────────┐
│  触发条件                                                       │
│  - push 到 main/master/develop/release/**                       │
│  - PR 到 main/master/develop                                    │
│  - 每日凌晨 4 点定时（cron: 0 4 * * *）                        │
│  - 手动触发（workflow_dispatch）                                 │
└──────────────────────┬──────────────────────────────────────────┘
                       ▼
   ┌──────────────────┼──────────────────┐
   │                  │                  │
   ▼                  ▼                  ▼
┌──────────────────┐ ┌────────────────────┐ ┌──────────────────┐
│  log-stress-test │ │ double-serialization│ │  di-unit-tests   │
│  日志压力测试    │ │ -guard 双重序列化  │ │  依赖注入单元测试│
│                  │ │  守护扫描          │ │                  │
│  stress_test()   │ │                    │ │  21 个 DI 测试   │
│  + run_stress_   │ │  diff-scan /       │ │  + 114 个完整  │
│    comparison()  │ │  full-scan         │ │  日志测试套件   │
│                  │ │                    │ │                  │
│  阈值断言：      │ │  阻断新增          │ │  覆盖率报告      │
│  - 吞吐量≥5000   │ │  logger.X(json.   │ │  XML 输出        │
│  - p99≤500us     │ │  dumps(...))       │ │                  │
│  - 错误率≤1%     │ │                    │ │                  │
│  - 加速比≥1.2x   │ │                    │ │                  │
└────────┬─────────┘ └─────────┬──────────┘ └────────┬─────────┘
         │                     │                     │
         └─────────────────────┼─────────────────────┘
                               ▼
               ┌───────────────────────┐
               │ log-perf-quality-gate │
               │ 日志性能质量门禁      │
               │ 三 job 均通过才放行   │
               └───────────┬───────────┘
                           ▼
               ┌───────────────────────┐
               │  notify-on-failure    │
               │  失败邮件通知         │
               │  （仅 schedule/push   │
               │   失败时触发）        │
               └───────────────────────┘
```

## 二、Job 详解

### Job 1: `log-stress-test`（日志压力测试）

**职责**：验证日志管道在多线程并发下无错误、无性能回归。

**触发模式**：

| 场景 | 模式 | 配置 |
|------|------|------|
| PR 提交 | quick 模式 | 2 线程 × 1 秒，宽松阈值 |
| push 到 main | 完整模式 | 8 线程 × 3 秒，严格阈值 |
| 定时任务 | 完整模式 | 8 线程 × 3 秒，严格阈值 |

**阈值配置**（完整模式）：

| 阈值 | 默认值 | 失败含义 |
|------|--------|----------|
| `--min-throughput` | 5000 ops/sec | 日志管道吞吐量低于基线 |
| `--max-p99-us` | 500 us | p99 延迟超标 |
| `--max-error-rate` | 0.01 (1%) | 多线程下出现错误 |
| `--min-speedup` | 1.2x | 新模式相比旧模式无显著提升 |

**关键脚本**：[`scripts/run_log_perf_stress_test.py`](../scripts/run_log_perf_stress_test.py)

**Artifacts**：
- `log-perf-stress-test-report`：JSON 格式压力测试报告，保留 30 天

### Job 2: `double-serialization-guard`（双重序列化守护）

**职责**：扫描 PR 中新增的 `logger.X(json.dumps(...))` 反模式，强制使用 `log_dict` 替代。

**扫描模式**：

| 场景 | 模式 | 行为 |
|------|------|------|
| PR 提交 | 增量扫描 | 仅扫描 PR 中变更的文件 |
| push | 增量扫描 | `HEAD~1 → HEAD` |
| 定时任务 | 全量扫描 | 扫描所有文件，更新豁免清单 |

**豁免清单**：[`.trae/double_serialization_exemptions.json`](../.trae/double_serialization_exemptions.json)
- 记录存量违规（基线：1865 处 / 184 文件）
- 新增违规不在豁免清单中即阻断合并

**PR 评论**：自动在 PR 添加评论，显示扫描结果和迁移建议。

**关键脚本**：[`scripts/check_double_serialization.py`](../scripts/check_double_serialization.py)

### Job 3: `log-perf-quality-gate`（质量门禁）

**职责**：汇总前置 job 结果，双 job 均通过才放行。

**依赖**：`needs: [log-stress-test, double-serialization-guard]`
**策略**：`if: always()`，即使前置 job 失败也运行检查

## 三、本地模拟 PR 环境运行测试

### 3.1 环境准备

```bash
# 1. 进入项目根目录
cd c:\Users\Administrator\agent

# 2. 确保 Python 环境可用（Python 3.11+）
python --version

# 3. 安装项目依赖（首次运行）
pip install -e .

# 4. 设置环境变量（可选，启用性能埋点）
# Windows PowerShell
$env:AGENT_PERF_LOGGING = "1"
# Linux/macOS
export AGENT_PERF_LOGGING=1
```

### 3.2 模拟 PR 增量扫描（双重序列化守护）

```bash
# 1. 创建测试分支
git checkout -b test/migration-check

# 2. 故意在某文件中添加一行违规代码
# 例如在 agent/example.py 中添加：
#   logger.info(json.dumps({"trace_id": "x", "message": "test"}, ensure_ascii=False))

# 3. 提交变更
git add agent/example.py
git commit -m "test: 添加违规代码验证守护规则"

# 4. 模拟 PR 增量扫描（与 main 分支对比）
python scripts/check_double_serialization.py \
    --diff-scan \
    --base origin/main \
    --head HEAD \
    --exemption-file .trae/double_serialization_exemptions.json

# 退出码 0 = 无新增违规
# 退出码 1 = 发现新增违规（CI 会阻断合并）
```

### 3.3 模拟完整日志压力测试

```bash
# 快速模式（PR 场景，2 线程 × 1 秒）
python scripts/run_log_perf_stress_test.py --quick

# 完整模式（main 分支场景，8 线程 × 3 秒）
python scripts/run_log_perf_stress_test.py \
    --threads 8 \
    --duration 3 \
    --min-throughput 5000 \
    --max-p99-us 500 \
    --max-error-rate 0.01 \
    --min-speedup 1.2 \
    --json-report logs/log_perf_stress_test.json

# 自定义阈值（严格场景）
python scripts/run_log_perf_stress_test.py \
    --threads 16 \
    --duration 5 \
    --min-throughput 10000 \
    --max-p99-us 200 \
    --min-speedup 1.5
```

### 3.4 模拟每日定时全量扫描

```bash
# 全量扫描所有文件（不阻断，仅审计）
python scripts/check_double_serialization.py --full-scan

# 全量扫描并更新豁免清单（迁移进度跟踪）
python scripts/check_double_serialization.py \
    --full-scan \
    --update-exemptions \
    --exemption-file .trae/double_serialization_exemptions.json
```

### 3.5 一键模拟完整 CI 流程

```bash
# 完整模拟 PR 提交时的 CI 检查（5 步）
echo "=== 步骤 1/5：日志压力测试（快速模式）==="
python scripts/run_log_perf_stress_test.py --quick

echo "=== 步骤 2/5：双重序列化增量扫描 ==="
python scripts/check_double_serialization.py \
    --diff-scan \
    --base HEAD~1 \
    --head HEAD

echo "=== 步骤 3/5：单元测试验证 ==="
python -m pytest tests/unit/test_perf_monitor.py \
    tests/unit/test_log_dict_refactor.py \
    tests/unit/test_log_dict_performance.py \
    tests/unit/test_memory_comparison.py \
    -q --tb=short

echo "=== 步骤 4/5：Top 20 文件迁移预览 ==="
python scripts/migrate_top20_batch.py --dry-run

echo "=== 步骤 5/5：完成 ==="
echo "✅ 所有 CI 检查通过，可安全提交 PR"
```

## 四、迁移工作流（开发者指南）

### 4.1 当 CI 阻断你的 PR 时

**场景**：PR 评论显示 "发现新增违规"

**处理步骤**：

```bash
# 1. 查看具体违规位置
python scripts/check_double_serialization.py \
    --diff-scan \
    --base origin/main \
    --head HEAD

# 2. 使用迁移工具自动重构
python scripts/migrate_to_log_dict.py --dry-run agent/your_file.py
python scripts/migrate_to_log_dict.py agent/your_file.py

# 3. 验证迁移无回归
python -m pytest tests/unit/test_log_dict_refactor.py -q

# 4. 提交并推送
git add agent/your_file.py
git commit -m "refactor: 迁移 your_file 到 log_dict 消除双重序列化"
git push
```

### 4.2 批量迁移 Top 20 文件

```bash
# 预览迁移效果（不写入）
python scripts/migrate_top20_batch.py --dry-run

# 执行批次 1（高风险模块）
python scripts/migrate_top20_batch.py --batch 1

# 执行全部 Top 20 文件迁移
python scripts/migrate_top20_batch.py --all

# 如果测试失败，回滚
python scripts/migrate_top20_batch.py --rollback
```

## 五、依赖注入模式（解耦 perf_monitor）

`stress_test()` 支持依赖注入，可完全脱离 `logging_utils` 独立运行：

```python
from agent.utils.perf_monitor import stress_test

# 自定义 filter 链工厂（避免 import logging_utils）
class FakeFilter:
    def filter(self, record):
        return True

def fake_filter_factory():
    return [FakeFilter()]

# 自定义 log_dict 工厂
def fake_log_dict(payload):
    data = dict(payload)
    data.setdefault('trace_id', 'fake')
    return data

# 完全解耦的压力测试
result = stress_test(
    num_threads=4,
    duration_seconds=2.0,
    filter_chain_factory=fake_filter_factory,
    log_dict_factory=fake_log_dict,
)
```

## 六、故障排查

### 6.1 压力测试失败

| 错误 | 可能原因 | 解决方案 |
|------|---------|---------|
| 吞吐量 < 5000 | 环境性能差 / filter 链过重 | 用 `--quick` 模式验证；检查是否有新增 filter |
| p99 > 500us | GC 抖动 / 线程竞争 | 增加 `--duration`；检查是否在共享 CI runner 上 |
| 加速比 < 1.2x | 旧模式已足够快 / 测试噪声 | 用 `--duration 5` 增加测试时长 |
| 错误率 > 1% | 多线程 bug / 资源泄漏 | 检查 stress_test 内部异常处理 |

### 6.2 守护扫描误报

```bash
# 误报：合规代码被标记为违规
# 解决：检查是否真的需要 json.dumps，考虑：
#   1. 是否可改为 log_dict(payload)
#   2. 是否是第三方库兼容场景（加入豁免清单）
#   3. 是否是 perf_monitor/logging_utils 自身（已在 DEFAULT_EXEMPTION_FILES 中）

# 漏报：违规代码未被发现
# 解决：检查 .trae/double_serialization_exemptions.json 是否过度豁免
python scripts/check_double_serialization.py --full-scan
```

### 6.3 CI YAML 语法验证

```bash
# 本地验证 YAML 语法
python -c "import yaml; data = yaml.safe_load(open('.github/workflows/log-perf-guard.yml', encoding='utf-8')); print('YAML OK'); print('jobs:', list(data.get('jobs', {}).keys()))"
```

## 七、配置文件清单

| 文件 | 作用 | 维护方 |
|------|------|--------|
| `.github/workflows/log-perf-guard.yml` | CI workflow 配置 | 平台团队 |
| `scripts/run_log_perf_stress_test.py` | 压力测试运行器 | 平台团队 |
| `scripts/check_double_serialization.py` | 守护规则扫描器 | 平台团队 |
| `scripts/migrate_to_log_dict.py` | 单文件迁移工具 | 平台团队 |
| `scripts/migrate_top20_batch.py` | Top 20 批量迁移脚本 | 平台团队 |
| `.trae/double_serialization_exemptions.json` | 存量违规豁免清单 | 自动维护（定时任务） |
| `.trae/migration_backups/` | 迁移备份目录 | 自动维护 |

## 八、相关文档

- [日志双重序列化反模式迁移路线图](./log_dict_migration_roadmap.md)
- [P0 安全修复归档 20260703](./security/p0_security_fix_archive_20260703.md)
