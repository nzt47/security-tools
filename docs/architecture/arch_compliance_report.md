# 架构合规性报告

> **生成时间**: 2026-06-26 18:33:16
> **Trace ID**: `5561e1b79fc9496d`
> **扫描根目录**: `agent`
> **校验耗时**: 3134 ms

## 一、执行摘要

**总体状态**: ✅ 通过

| 指标 | 数值 |
|------|------|
| 校验规则数 | 7 |
| 违规总数 | 2 |
| 未豁免违规（需修复） | 0 |
| 已豁免违规（存量技术债务） | 2 |
| 扫描文件数 | 256 |
| 模块节点数 | 215 |
| 依赖边数 | 439 |
| 跨层调用数 | 261 |
| 跨层违规数 | 0 |
| 动态 import 数 | 1 |

## 二、规则概览

| 规则 ID | 描述 | 类别 | 严重度 | 命中数 | 未豁免 | 已豁免 | 状态 |
|---------|------|------|--------|--------|--------|--------|------|
| `no_orchestrator_to_dao` | 禁止 orchestrator 直接访问 dao 层 | 跨层调用 | 🔴 high | 0 | 0 | 0 | ✅ 无违规 |
| `no_cognitive_to_server_routes` | 禁止 cognitive 直接访问 server_routes | 跨层调用 | 🔴 high | 0 | 0 | 0 | ✅ 无违规 |
| `no_cognitive_to_dao` | 禁止 cognitive 直接访问 dao 层 | 跨层调用 | 🔴 high | 0 | 0 | 0 | ✅ 无违规 |
| `no_tools_to_dao` | 禁止 tools 直接访问 dao 层 | 跨层调用 | 🟡 medium | 0 | 0 | 0 | ✅ 无违规 |
| `no_guardrails_to_server_routes` | 禁止 guardrails 直接访问 server_routes | 跨层调用 | 🟡 medium | 0 | 0 | 0 | ✅ 无违规 |
| `no_circular_dependency` | 禁止循环依赖（A->B->A） | 循环依赖 | 🔴 high | 2 | 0 | 2 | 🚫 全部豁免 |
| `no_agent_import_tests` | 禁止 agent/ 下模块直接 import tests/ | 反向依赖 | 🔴 high | 0 | 0 | 0 | ✅ 无违规 |

## 三、违规项详情

### 3.1 未豁免违规

✅ **无未豁免违规。** 所有检测到的违规均已登记在存量豁免清单中。

### 3.2 已豁免违规（存量技术债务）

以下违规已登记在 `docs/architecture/legacy_exemptions.json` 中，作为已知技术债务跟踪。

#### 豁免 #1: `no_circular_dependency` 🔴 high

- **规则**: `no_circular_dependency`
- **描述**: 禁止循环依赖（A→B→A）: agent.monitoring.error_reporter → agent.error_handler → agent.monitoring.error_reporter
- **循环路径**: `agent.error_handler` <-> `agent.monitoring.error_reporter`
- **源文件**: `agent\error_handler.py:1176`
- **严重度**: high
- **技术债务工单**: `ARCH-DEBT-002`
- **登记日期**: 2026-06-26
- **负责人**: architecture-team
- **豁免原因**: 存量循环依赖：error_handler 在异常上报时延迟 import error_reporter 的 ErrorReporter 多通道能力，error_reporter 在 WebhookReporter 初始化时反向延迟 import error_handler 的 with_retry 与 TemporaryNetworkError。双向均使用函数内延迟 import，运行期可正常工作。
- **缓解措施**: error_handler.py:1176 与 error_reporter.py:158 均使用函数内延迟 import 打破模块加载时循环。后续应将 with_retry/TemporaryNetworkError 下沉到独立的基础设施模块（如 agent.utils.retry），消除 error_handler 与 monitoring 之间的双向耦合。
- **修复建议**: 通过依赖倒置或中间层解耦，或使用 lazy_loader 延迟加载

#### 豁免 #2: `no_circular_dependency` 🔴 high

- **规则**: `no_circular_dependency`
- **描述**: 禁止循环依赖（A→B→A）: agent.graceful_degrade → agent.cognitive.critic → agent.graceful_degrade
- **循环路径**: `agent.cognitive.critic` <-> `agent.graceful_degrade`
- **源文件**: `agent\cognitive\critic.py:30`
- **严重度**: high
- **技术债务工单**: `ARCH-DEBT-001`
- **登记日期**: 2026-06-26
- **负责人**: architecture-team
- **豁免原因**: 存量循环依赖：critic 依赖 graceful_degrade 进行降级，graceful_degrade 通过函数内延迟 import 反向引用 critic。已通过延迟加载缓解，列入技术债务清单。
- **缓解措施**: graceful_degrade.py:464 使用函数内延迟 import（from agent.cognitive.critic import CriticEvaluator）打破模块加载时循环，运行期可正常工作。后续应通过接口抽象或事件解耦彻底消除循环。
- **修复建议**: 通过依赖倒置或中间层解耦，或使用 lazy_loader 延迟加载

## 四、依赖图统计

### 4.1 总体统计

| 指标 | 数值 |
|------|------|
| 扫描文件数 | 256 |
| 模块节点数 | 215 |
| 依赖边数 | 439 |
| 跨层调用数 | 261 |
| 跨层违规数 | 0 |
| 动态 import 数 | 1 |
| 依赖图构建耗时 | 1808 ms |

### 4.2 层级分布

| 层级 | 模块数 | 占比 |
|------|--------|------|
| core | 48 | 22.3% |
| monitoring | 23 | 10.7% |
| server_routes | 21 | 9.8% |
| tools | 18 | 8.4% |
| extensions | 14 | 6.5% |
| cognitive | 10 | 4.7% |
| memory | 10 | 4.7% |
| orchestrator | 9 | 4.2% |
| guardrails | 6 | 2.8% |
| log_system | 6 | 2.8% |
| subagent | 6 | 2.8% |
| task_planner | 6 | 2.8% |
| observability | 4 | 1.9% |
| model_router | 4 | 1.9% |
| workflow_engine | 4 | 1.9% |
| p6 | 4 | 1.9% |
| health | 3 | 1.4% |
| human_in_the_loop | 3 | 1.4% |
| network | 3 | 1.4% |
| web | 3 | 1.4% |
| lazy_loader | 2 | 0.9% |
| audit | 2 | 0.9% |
| prompt_manager | 2 | 0.9% |
| caching | 1 | 0.5% |
| utils | 1 | 0.5% |
| unknown | 1 | 0.5% |
| response_workflows | 1 | 0.5% |

## 五、建议和后续行动

### 5.1 紧急行动

✅ 无未豁免违规，无需紧急行动。

### 5.2 技术债务清零计划

以下已豁免违规应制定清零计划：

- [ ] ARCH-DEBT-002: 解耦 `agent.error_handler` <-> `agent.monitoring.error_reporter` 循环依赖
- [ ] ARCH-DEBT-001: 解耦 `agent.cognitive.critic` <-> `agent.graceful_degrade` 循环依赖

**建议方案**:
- 将共享的基础设施代码（如 `with_retry`、`TemporaryNetworkError`）下沉到独立模块（如 `agent.utils.retry`）
- 通过依赖倒置（接口抽象）解耦循环依赖
- 使用事件驱动架构替代直接调用

### 5.3 定期审查

- 每季度评审豁免清单，推动技术债务清零
- 每次新增模块时运行架构规则校验
- 关注跨层调用数增长趋势，防止架构腐化
- 定期更新依赖图文档 `docs/architecture/module_dependency_graph.md`

## 六、附录

### 6.1 相关文件

| 文件 | 说明 |
|------|------|
| `agent/observability/arch_rules.py` | 架构规则校验器 |
| `agent/observability/dependency_graph.py` | 依赖图生成器 |
| `docs/architecture/legacy_exemptions.json` | 存量豁免清单 |
| `docs/architecture/legacy_exemptions_guide.md` | 豁免清单配置指南 |
| `docs/architecture/dependency_graph.json` | 依赖图 JSON 数据 |
| `docs/architecture/module_dependency_graph.md` | 依赖图 Mermaid 图 |
| `docs/architecture/arch_rules_report.json` | 校验结果 JSON |
| `config.yaml` | 架构规则配置（arch_rules 段） |
| `.github/workflows/observability-ci.yml` | CI 工作流 |

### 6.2 CLI 命令

```bash
# 运行架构规则校验
python -m agent.observability.arch_rules --check \
  --root agent \
  --exemptions docs/architecture/legacy_exemptions.json \
  --config config.yaml \
  --json-report docs/architecture/arch_rules_report.json \
  --md-report docs/architecture/arch_rules_report.md

# 生成模块依赖图
python -m agent.observability.dependency_graph \
  --root agent \
  --output docs/architecture/module_dependency_graph.md \
  --json-output docs/architecture/dependency_graph.json

# 变更影响分析
python scripts/impact_analysis.py \
  --base origin/main --head HEAD \
  --output docs/architecture/impact_report.md

# 生成合规性报告（本脚本）
python scripts/generate_arch_compliance_report.py
```
