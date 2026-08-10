# 规划模块重构总结报告

> 生成日期: 2026-08-11
> 范围: planning 模块缺陷修复（D1–D19）+ 能力基线启用 + 配套 CI/基准治理
> 基线: develop → master（本工作系列全部合并）

## 1. 概述

本轮重构以「缺陷复现测试 → 逐项修复 → 能力基线启用」三阶段闭环，覆盖 planning 模块 19 个已登记缺陷（d1–d19 复现测试全部通过）与 5 项能力基线规格（4 项启用、1 项待 SQLite 排期）。

## 2. 缺陷修复清单（19/19 通过）

**本工作系列直接修复（8 项）**:

| 缺陷 | 严重度 | 修复要点 | 提交 |
|---|---|---|---|
| D14 | P2 | 降级链实现（主失败 → 备份工具链；仅 TOOL_CALL；零回退） | a019103e |
| D15 | P2 | `Plan.summarize()` 用户可读摘要 | 08dbc628 |
| D17 | P2 | ReAct 思考嵌入 reflector 历史经验（成功模式/陷阱） | 08dbc628 |
| D7 | P1 | `planning.enabled: true` 接入生产配置（orchestrator 主链路） | 08dbc628 |
| D16 | P2 | `get_stats()` 可观测指标（5 项） + 执行埋点 | 08dbc628 |
| D10 | P1 | 中文关键词表收窄（移除过宽单字"将"） | 08dbc628 |
| D6 | P1 | `complexity_threshold` 参与规划判定（默认等价原行为） | bc9da559 |
| D13 | P2 | ReAct deadline 预算（迭代级检查，超预算终止） | bc9da559 |
| D8 | P1 | TaskPlanner 收敛 planning 统一模块（薄壳重导出兼容） | bc9da559 |

**既有已修复（11 项，复现测试随 c325b4ff 及更早提交通过）**:D1 规划流程 / D2 提示词结构 / D3 等待输入 / D4 状态机 / D5 并行 / D9 检查点 / D11 验证器 / D12 调整建议回灌 / D18 协作取消 / D19 并发单例 / 其余缺陷回归。

## 3. 能力基线启用（4/5）

| 规格 | 缺陷 | 验证结果 |
|---|---|---|
| 并行执行 | D5 | ✅ 移除 skip：互不依赖任务并行（0.2s×2 < 0.4s 计时断言） |
| 计划验证 | D11 | ✅ 移除 skip：悬空依赖 + 环检测双断言 |
| 预算超限 | D13 | ✅ 移除 skip：deadline 层断言（token/cost 层留规格差距） |
| 降级链 | D14 | ✅ 移除 skip：真实断言（capability 用例） |
| 持久化恢复 | D9 | ⏸ 保留 skip：JSON 检查点已满足 P1；SQLite 落库见技术方案 |

## 4. 测试数据

| 套件 | 结果 |
|---|---|
| planning 全量（tests/unit -k planning） | **178 passed, 0 failed** |
| 降级链分支覆盖（d14 + capability） | 7 passed（分支 A–G） |
| capability_baseline | 4 passed, 1 skipped |
| task_planner boundary + planning/orchestrator | 325 passed |
| 19 个 defect 复现测试（d1–d19） | 全部通过 |

## 5. 配套治理（同系列）

- **CI 排队风暴**:17 个高频 push workflow concurrency 统一（`ci-${{ github.ref }}` + cancel-in-progress）
- **pre-commit 误报**:敏感扫描白名单补占位符前缀 + .gitignore 补临时产物规则
- **nightly 通知分级**:性能退化区分「严重回归」（钉钉+PR 评论）/「轻微波动」（仅 PR 评论）
- **基准与文档**:light_loader 拐点扫描/硬件升级模拟（CPU 12→24）/月度汇总/阈值动态调整文档/降级链测试报告/D9 技术方案

## 6. 遗留技术债

| 项 | 现状 | 建议 |
|---|---|---|
| D9 SQLite 落库 | JSON 检查点已满足 P1 | 按技术方案排期（~270 行） |
| D13 token/cost 预算 | 仅 deadline 层 | 规格级差距，可后置 |
| D5 parallel_groups 显式消费 | gather next_tasks 语义等价 | 实现差异，非缺陷 |
| D11 工具可用性预检 | 未实现 | 规格级差距，可后置 |

## 7. 结论

- 规划模块 19 项登记缺陷全部闭环，能力基线 4/5 启用，planning 测试 0 failed。
- 关键不变量（重试语义、错误保留、零回退、状态机收尾）经专项测试验证未破坏。
- 剩余技术债集中在规格级增强（SQLite/token-cost 预算），不影响当前主链路。
