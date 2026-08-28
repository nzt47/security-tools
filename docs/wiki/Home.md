# 云枢知识库 Wiki 首页

> 项目 Wiki 导航 + 轻量检测视图插件（light_loader）安装与版本兼容性说明。

## 一、light_loader 独立包安装说明

审计五类检测（孤儿/断链/index 漂移/过期/未裁决矛盾）只需每张卡六个字段
（slug/status/type/date/links/contradictions）。`light-loader` 独立 pip 包
只解析这六字段，丢弃正文/insight（单卡内存降 5~10 倍），并支持保序并行扫描。

### 安装

```bash
# 方式一：直接安装（生产）
pip install ./packages/light_loader

# 方式二：可编辑安装（开发）
pip install -e ./packages/light_loader
```

### 快速使用

```python
from light_loader import scan_light_cards

cards = scan_light_cards("path/to/wiki", parallel=True)
for c in cards:                      # type: CardLight
    print(c.slug, c.status, c.links)
```

公开 API：`CardLight`（六字段视图）· `parse_light(text)`（单文件解析，损坏抛
ValueError）· `scan_light_cards(root, *, type_dirs, parallel)`（全量扫描，
损坏卡跳过、并行保序）。

完整版：[docs/reports/light_loader_package_install_20260811.md](../reports/light_loader_package_install_20260811.md)

## 二、版本兼容性说明

### 兼容矩阵（light-loader v0.1.0）

| 依赖项 | 最低版本 | 说明 |
|---|---|---|
| Python | 3.11 | `pyproject.toml` `requires-python = ">=3.11,<3.13"`（2026-08-28 校准：numpy 2.4/scipy 1.17 已要求 >=3.11） |
| PyYAML | 6.0 | 唯一运行时依赖（frontmatter 解析） |
| libyaml（C 扩展） | 可选 | 有则 `CSafeLoader` 加速（约 7.6x）；缺失自动回退纯 Python `SafeLoader`，功能等价 |

### 与仓库内 vendored 副本的同步

| 入口 | 路径 | 用途 |
|---|---|---|
| 独立包 | `packages/light_loader/src/light_loader/core.py` | 发布 / 外部项目复用 |
| vendored | `agent/knowledge/light_loader.py` | agent 内部使用（`CardStore.list_light` / `lint_all` 默认检测路径） |

两者为**同一份源码的双入口**（当前均为 v0.1.0）。变更任一侧后**必须同步另一侧**；
agent 内部保持零安装依赖，外部项目通过 `pip install light-loader` 获得同款能力。

### 版本演进策略

- 六字段契约（slug/status/type/date/links/contradictions）为**不易**边界，
  任何版本不得重命名或变更语义；
- 排序契约（类型目录序 + 组内 slug 字典序）与损坏卡跳过语义同属不易；
- 未来升版本（0.2+）如新增字段/参数，须向后兼容（新参数默认值保持旧行为）。

### 验证入口

- 极端场景压力测试：`scripts/dev/stress_light_loader_parallel.py`
  （5000 卡 / 60% 损坏率，断言串行=并行顺序，nightly 已接入）；
- 性能基准：`scripts/dev/bench_light_loader_serial_parallel.py`
  （1000–10000 卡曲线，最新结果见 `docs/reports/light_loader_serial_parallel_bench_20260811.md`）；
- 单元回归：`tests/unit/test_knowledge_light_loader.py`。

## 三、Wiki 文档导航

- 知识模块：[knowledge_optimization_phase2_wiki.md](knowledge_optimization_phase2_wiki.md)
- 入链索引架构演进：[knowledge_optimization_phase2_evolution_wiki.md](knowledge_optimization_phase2_evolution_wiki.md)
- 并发缺陷修复：[concurrency_fixes_wiki.md](concurrency_fixes_wiki.md)
- Git Detached 提交悬空修复：[git_detached_commit_fix_wiki.md](git_detached_commit_fix_wiki.md)
- Git Detached 提交操作手册（含 §6 工程经验 CheckList）：[git_detached_commit_ops_manual.md](git_detached_commit_ops_manual.md)
- TASK-06 全链路复盘（并行会话提交事故）：[../zh/智能体学习机制重构计划/TASK-06_全链路复盘_20260815.md](../zh/智能体学习机制重构计划/TASK-06_全链路复盘_20260815.md)
- TASK-06 操作日志归档：[../zh/audit-evidence/TASK-06_操作日志归档_20260815.md](../zh/audit-evidence/TASK-06_操作日志归档_20260815.md) · [命令级凭证](../zh/audit-evidence/TASK-06_命令级操作凭证_20260815.md)
- 死代码修复与边界测试：[deadcode_fix_and_boundary_tests_wiki.md](deadcode_fix_and_boundary_tests_wiki.md)
- 单例管理：[singleton_manager_wiki.md](singleton_manager_wiki.md)
- 限流器迁移：[rate_limiter_migration_wiki.md](rate_limiter_migration_wiki.md)
- 安全配置：[security_config_wiki.md](security_config_wiki.md)
- CI 安全扫描：[ci_security_scan_wiki.md](ci_security_scan_wiki.md)
- Release 流程：[release_workflow_wiki.md](release_workflow_wiki.md)
- 工作区维护规范：[workspace_maintenance_wiki.md](workspace_maintenance_wiki.md)
- 仓库状态快照：[REPOSITORY_SNAPSHOT_REPORT.md](REPOSITORY_SNAPSHOT_REPORT.md)
- 可见性改造总结报告（D2/D3/D5 指标改造全过程与修复记录）：[../observability/visibility_improvement_summary.md](../observability/visibility_improvement_summary.md)
- entry_assigned 异常时序监控方案：[../observability/entry_assigned_monitoring_plan.md](../observability/entry_assigned_monitoring_plan.md)
- 告警阈值生产基线校准建议：[../observability/alert_threshold_calibration_plan.md](../observability/alert_threshold_calibration_plan.md)
- BM25 相关：[BM25_OPTIMIZATION_WIKI.md](BM25_OPTIMIZATION_WIKI.md) ·
  [BM25_TECHNICAL_RETROSPECTIVE.md](BM25_TECHNICAL_RETROSPECTIVE.md) ·
  [BM25_RELEASE_LOG.md](BM25_RELEASE_LOG.md)
