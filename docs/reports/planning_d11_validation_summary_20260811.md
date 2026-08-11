# 规划模块 D11 工具可用性预检落地总结报告

> 生成日期: 2026-08-11
> 范围: planning 模块缺陷修复与能力基线收尾（D11 为最后一项规格缺口）
> 关联: [executor.py](../../planning/executor.py) · [test_planning_defect_d11.py](../../tests/unit/test_planning_defect_d11.py)

## 1. D11 工具可用性预检落地

### 1.1 实现

[validate_plan L219-228](file:///c:/Users/Administrator/agent/planning/executor.py#L219-L228) 追加第三类检查（此前仅悬空依赖/环检测）：

| 项 | 内容 |
|---|---|
| 触发条件 | **无 LLM 的纯工具执行路径**（`llm is None`） |
| 检查逻辑 | 每个任务的描述经 `find_tool`（英文工具名子串 + 中文关键词映射）必须可解析到已注册工具 |
| 拦截行为 | 不可解析 → `PlanValidationError`（"任务 'x' 引用的工具不可用"）→ 计划 FAILED |
| LLM 豁免 | 有 LLM 服务时跳过预检——任务可由推理灵活完成，避免误拦截"请思考并回答"类描述 |

### 1.2 设计取舍

- **不拦截 LLM 路径**：工具可用性预检若对所有任务严格生效，会破坏"思考型"任务（无工具、纯推理），故仅限无 LLM 执行器。
- **复用 find_tool 匹配策略**：零新增匹配逻辑，与执行阶段 `_determine_action` 的解析口径一致，预检与执行行为对齐。

## 2. 能力基线全量收尾

规划模块能力基线 5 项至此**全部启用**（无剩余 skip / xfail）：

| 规格 | 缺陷 | 状态 |
|---|---|---|
| 并行执行 | D5 | ✅ 已启用 |
| 计划验证（依赖/环/工具可用性） | D11 | ✅ 本批启用（最后一项） |
| 持久化恢复（SQLite） | D9 | ✅ 已启用 |
| 预算超限（deadline/token/cost + 硬超时） | D13 | ✅ 已启用 |
| 降级链 | D14 | ✅ 已启用 |

## 3. 测试数据

### 3.1 专项验证

```text
python -m pytest tests/unit/test_planning_defect_d11.py \
    tests/unit/test_planning_defect_d14.py \
    tests/unit/test_planning_defect_d5.py \
    tests/unit/test_planning_defect_d9.py -q
→ 10 passed（d11 悬空/环/工具预检 3 用例 + 既有 defect 回归）
```

### 3.2 planning 全量（本地）

```text
python -m pytest tests/unit -k planning -q
→ 192 passed, 0 failed（含 d11 预检新用例）
```

### 3.3 全量测试（tests/unit 非 planning，本地 17.5 分钟完整跑完）

```text
python -m pytest tests/unit -q -k "not planning"
→ 10084 passed, 44 skipped, 13 xfailed, 4 xpassed
→ 6 failed（全部为环境/既有问题，与 D11 零交集）
```

6 个失败明细（均非本批引入，与 planning 模块无代码交集）：

| 失败用例 | 原因 | 性质 |
|---|---|---|
| test_vector_store_sqlite_vec ×4 | `ModuleNotFoundError: transformers.configuration_utils`（本地 transformers 安装损坏） | 环境依赖 |
| test_ci_guard_fix_regression | CLI 子进程需连接 localhost:5678 搜索服务（服务未启动） | 环境服务 |
| test_snapshot_comprehensive | `.pytest_tmp` 共享目录被并行测试残留污染 | 测试串扰 |

### 3.4 chaos / e2e

- chaos：全部 skip（既有设计）
- e2e：`test_online_chat` 需本地服务 + 外部网络，本地不可跑（CI 环境覆盖），与 D11 无关

## 4. 结论

- D11 工具可用性预检落地，规划模块缺陷复现测试（d1–d19）与能力基线（5/5）全部闭环。
- 预检仅作用于无 LLM 纯工具路径，既有测试（d5/d9/d14 等）零破坏。
- 规划模块技术债清单归零（除文档化已知限制：token 估算精度、同步工具硬超时不可达）。
