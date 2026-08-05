# PR 描述 — CI 必挂隐患修复 + agent/knowledge 契约层重建

> 用途：本次工作已直接推送 master（用户确认），本文档供团队评审/复盘存档。
> 状态：✅ 已合并（master 4c7bfe71）| 所有验证全绿

---

## 一、建议的最终 Commit Message

```text
fix(ci): 修复 CI 必挂四类隐患 + 重建 agent/knowledge 契约层

背景:
  本地模拟 CI 时发现 run_ci_guard.py 依赖两个从未入库的模块, 触发
  "本地假绿, CI 必挂" 隐患。全仓库巡检发现同类问题共 4 类:
  未入库依赖 / .pyc 缓存陷阱 / stdout 污染 / BOM 编码污染。

修复:
  - 重建未入库模块: simulate_pr_merge_guard / safe_git_revert /
    ci_guard_types (a422a64f, e859f22e)
  - stdout 纯净化: safe_git_revert dry-run 日志改走 stderr (e859f22e)
  - 恢复被误覆盖的 simulate_ci_pipeline.py, 新模拟脚本改名
    simulate_ci_guard_pipeline (bec04269)
  - 巡检工具转正: scan_missing_deps.py (未入库依赖/.pyc 陷阱检测)
  - 新增交付工具: publish_fix_to_docs.py (修复记录 → 文档站)
  - 重建 agent/knowledge 契约层: 依据 .pyc 反编译提取结构还原
    __init__/lifecycle/schema 三文件 (4c7bfe71)

验证:
  - 回归测试 19/19 通过 (tests/unit/test_ci_guard_fix_regression.py)
  - 本地完整 CI 流水线 ALL PASS (simulate_ci_guard_pipeline)
  - BOM 编码检查 BLOCK 0 / 核心不变量 12/12
```

## 二、PR 描述

### Title
`fix(ci): 修复 CI 必挂隐患（未入库依赖/缓存陷阱/stdout 污染）+ 重建知识契约层`

### 变更概览

| 类别 | 变更 | 文件 |
|------|------|------|
| 未入库依赖 | 重建 3 个从未入库模块 | scripts/{simulate_pr_merge_guard,safe_git_revert,ci_guard_types}.py |
| stdout 污染 | dry-run 日志走 stderr，保 JSON 纯净 | scripts/safe_git_revert.py |
| 改名冲突 | 恢复原版 + 新脚本改名 | scripts/simulate_ci_pipeline.py / simulate_ci_guard_pipeline.py |
| 巡检工具 | 未入库依赖/.pyc 陷阱检测转正 | scripts/scan_missing_deps.py |
| 交付工具 | 修复记录自动推送文档站 | scripts/publish_fix_to_docs.py |
| 文档 | 避坑指南 / 修复记录 / 交付清单 / 索引 | docs/{developer-guides,observability}/ |
| 测试 | 回归测试 19 用例 | tests/unit/test_ci_guard_fix_regression.py |
| 契约层重建 | agent/knowledge 三源文件还原 | agent/knowledge/{__init__,lifecycle,schema}.py |

### 验证记录

| 检查 | 结果 |
|------|------|
| 回归测试 `pytest tests/unit/test_ci_guard_fix_regression.py` | ✅ 19/19 |
| 本地 CI 模拟 `simulate_ci_guard_pipeline.py --json` | ✅ overall pass |
| 编码检查 `check_ps1_encoding.py` | ✅ BLOCK 0 / WARN 0 |
| 不变量 `verify_core_invariants.py` | ✅ 12/12 |
| 巡检 `scan_missing_deps.py` | ✅ workflow 引用无缺失 |
| 文档站部署 | ✅ Pages 已上线修复记录索引 |

### 测试覆盖详情

- **stdout 纯净**：safe_git_revert dry-run 零 stdout 输出（防 CI `json.load` 必挂）
- **契约校验**：ci_guard_types 对 run_ci_guard 报告结构 5 项校验
- **全流程 JSON**：--json 可解析 / stdout 首字符 `{` / --validate / --force-fail
- **改名一致性**：原版与新脚本共存且功能独立
- **巡检/推送**：扫描结构化结果 / 索引生成+去重+幂等

### 遗留事项（非本次范围，已记录交付清单）

- 15 项 LOST 脚本/测试（elk_import_logs、perf_regression_monitor 等）无 CI 影响，待分类
- `security-tools/` 嵌套仓库副本（.gitignore 已忽略）
- 并发会话持续工作，提交前按 Git 操作安全指南核对暂存区

---

## 三、合并说明

- 实际合并方式：**直接推送 master**（非 PR），因工作分支已快进 master
- 推送范围：`b12f82a6..4c7bfe71`（5 commit，含本会话 2 个 + 并发会话 3 个）
- pre-push 核心不变量校验 12/12 通过后放行
