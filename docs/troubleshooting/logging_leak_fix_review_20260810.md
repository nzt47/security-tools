# logging 全局状态泄漏治理 — 技术复盘（2026-08-10）

> 性质：logging.disable / basicConfig 全局状态泄漏的根因分析、修复方案与验证数据
> 关联提交：`6ada3dc1`（test_orchestrator try/finally）、`305282cf`（test_knowledge_link_perf autouse fixture）、`8fb4fcc0`（CI paths 全覆盖）
> 关联 run：31382787584（全绿）、31382245308（被 concurrency 取消）
> 关联文档：[r1_r4_fix_summary_20260810.md](r1_r4_fix_summary_20260810.md)、[blog_import_side_effect_and_makedirs_rootcause_20260810.md](blog_import_side_effect_and_makedirs_rootcause_20260810.md)

---

## 1. 结论速览（TL;DR）

| # | 问题 | 风险等级 | 状态 |
|---|---|---|---|
| 1 | test_knowledge_link_perf.py 模块级 `logging.disable(CRITICAL)`（import 副作用） | 🔴 高 | ✅ 已修复（autouse fixture） |
| 2 | test_orchestrator 函数内 `logging.disable(WARNING)` 仅在函数末尾恢复，断言失败即泄漏 | 🟠 中 | ✅ 已修复（try/finally，6ada3dc1） |
| 3 | tests/ 下 30+ 处模块级 `basicConfig`（无 force） | 🟢 低（no-op） | 无需修复 |
| 4 | scripts/ 下 4 处模块级 `logging.disable(CRITICAL)` | 🟢 低（独立基准脚本） | 无需修复 |
| 5 | CI paths 漏配（agent/** 未全覆盖 + split_unit_tests.py 未纳入） | 🔴 高 | ✅ 已修复（8fb4fcc0，run 31382787584 验证） |

**核心认知修正**：`logging.disable(level)` 修改的是**进程级全局状态**（`logging.root.manager.disable`），而 tests/conftest.py 的 `reset_global_singletons` 防线只快照/恢复 **root logger 的 handlers 与 level**——**无法覆盖 manager.disable**。这是两处泄漏能穿透现有防线的根本原因。

---

## 2. 根因分析

### 2.1 `logging.disable` 的进程级语义

```python
# CPython logging/__init__.py（摘录）
def disable(level=CRITICAL):
    root.manager.disable = level          # ← 进程级，不是 root logger 属性

def _log(self, level, msg, args, **kwargs):
    if self.manager.disable >= level:      # ← 每次发日志都检查
        return
```

- `manager.disable` 初始为 0（NOTSET），调 `logging.disable(WARNING)` 后变为 30，同进程**所有** logger 的 WARNING 以下日志全部静默
- 仅调 `logging.disable(NOTSET)`（0）才能恢复——**任何测试一旦调用且未恢复，同进程后续所有 caplog / assertLogs / log capture 全部失效**
- 它既不改变 root logger 的 `handlers`，也不改变 `level`，因此 conftest 的 handlers/level 快照恢复**完全感知不到**这次污染

### 2.2 两种泄漏模式

**模式 A：模块级 import 副作用（test_knowledge_link_perf.py，Shard 4 flake 根因）**

```python
# 修复前——模块顶层执行，pytest collection import 该文件时即污染全局
logging.disable(logging.CRITICAL)
```

pytest collection 阶段 import 测试文件即执行模块顶层代码。`test_knowledge_link_perf.py` 在 collection 时把 `manager.disable` 置为 50，同进程（xdist 单 worker 进程内）的所有 serial 日志断言测试全部静默失败。定位手法：pytest `pytest_collectstart` 钩子逐模块监控 `manager.disable`，一次命中 `[POLLUTE] during collection of test_knowledge_link_perf.py: manager.disable 0 -> 50`。

**模式 B：函数内调用但恢复代码不在 finally（test_orchestrator）**

```python
# 修复前——3 个断言在前，恢复在后；任一断言失败（如并发计数 != 250）即跳过恢复
_logging.disable(_logging.WARNING)
...  # 测试主体 + 4 个 assert
Orchestrator._SEM_API_OVERRIDE = None   # 恢复代码在正常路径末尾
_logging.disable(_logging.NOTSET)
```

一旦中间断言失败（fixture 级异常、线程竞争异常、配置不一致），恢复代码不执行，`manager.disable` 永久停留在 30，污染同进程后续测试。虽为并发压测场景（Shard 分片下该文件与 caplog 测试同进程共存），泄漏概率低于模块级模式，但后果相同。

### 2.3 conftest 防线的覆盖边界（关键认知）

tests/conftest.py `reset_global_singletons`（autouse，L375-397）：

```python
_root_logger = logging.getLogger()
_saved_handlers = _root_logger.handlers[:]
_saved_level = _root_logger.level
yield
_root_logger.handlers = _saved_handlers
_root_logger.setLevel(_saved_level)
```

- **覆盖**：root handler 增删（如 `setup_agent_logging()` 的 EmojiFilter/SensitiveDataFilter 替换）、root level 修改 → 有效
- **不覆盖**：`manager.disable`（logging.disable 的落脚点）、handler 内嵌 mutable 状态、`captureWarnings` 开关等 → 全部漏网

> 教训：conftest 的全局恢复防线按"已知污染源"设计，无法防御"进程级、非 handler/level 落脚点"的状态修改。凡使用 `logging.disable` 的测试必须自带恢复（try/finally 或 autouse fixture）。

### 2.4 CI paths 漏配（同批次治理，触发验证缺口）

observability-ci.yml push paths 原仅列 monitoring/observability/health/knowledge/log_system 子目录与 2 个根级文件，**agent/utils、tools、memory、orchestrator、skills_mgmt 等目录及 logging_utils.py 等根级模块改动不触发全项目 6-shard 验证** → safe_logger 缺 makedirs 同类缺陷无法被捕获。另有 `scripts/split_unit_tests.py`（直接决定测试分配）不在触发路径。

---

## 3. 修复方案

### 3.1 模式一：autouse fixture + try/finally（模块级，test_knowledge_link_perf.py）

```python
@pytest.fixture(autouse=True)
def _silence_logging():
    """替代原模块顶层 logging.disable(CRITICAL)：改为每个测试内禁用、
    finally 恢复，消除 collection 阶段的 import 副作用。"""
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(logging.NOTSET)
```

- 模块级副作用 → 函数级生命周期，collection 阶段零污染
- autouse 使文件内所有测试自动获得抑制，无需逐个标记

### 3.2 模式二：函数内 try/finally（test_orchestrator，6ada3dc1）

```python
_logging.disable(_logging.WARNING)
try:
    Orchestrator._SEM_API_OVERRIDE = {"enabled": True, "min_score": 0.3}
    ...  # 全部测试主体与断言
finally:
    # 恢复全局 logging 状态与单例缓存：断言失败时也执行，防污染同进程后续测试
    Orchestrator._SEM_API_OVERRIDE = None
    Orchestrator._clear_semantic_config_cache()
    _logging.disable(_logging.NOTSET)
```

- 恢复代码（全局状态 + 单例缓存）统一移入 `finally:`，断言失败、fixture 异常均必达
- 同时清理 `_SEM_API_OVERRIDE` 单例，与原清理逻辑语义等价、无新增行为

### 3.3 配套：CI paths 修复（8fb4fcc0）

- `agent/**` 全覆盖（合并原分散子目录条目，消除缺口）
- `scripts/split_unit_tests.py` 纳入 paths（分片脚本改动须触发全项目验证）
- 修复验证过程暴露两个 GitHub 机制：
  1. **skip-ci 连带跳过**：带 `[skip ci]` 的提交作为 push HEAD 会跳过整个 push（中间非 skip 提交也不触发）→ 须用无 skip 标记的提交重推
  2. **修改 workflow 文件自身不触发该 workflow 的 push 运行**（防递归机制）→ 用 `gh workflow run` 手动 dispatch 兜底验证

---

## 4. 验证数据

### 4.1 本地验证

| 验证项 | 结果 |
|---|---|
| `python -m py_compile`（test_orchestrator） | ✅ 通过 |
| `pytest test_orchestrator三层路由_e2e.py -k 高并发` | ✅ 1 passed（20.97s，250 次 process + 250 次热更断言） |
| `pytest test_orchestrator三层路由_e2e.py`（全文件） | ✅ 11/11 passed（32.39s） |

### 4.2 CI 验证（run 31382787584，head 6ada3dc1）

| 项 | 结果 |
|---|---|
| 触发方式 | master push（try/finally 提交，**无 skip 标记**） |
| 结论 | ✅ **success，22/22 job 全绿** |
| 全项目测试覆盖率 Shard 1-6 | ✅ 6/6 全部 success |
| 边界覆盖 / 架构影响 / 混沌 / 契约 / 集成 / E2E / 质量门禁 | ✅ 全部 success |
| 触发验证意义 | push 因 `tests/**` 在 paths 内成功触发 → **paths 修复生效，测试改动不再漏触发** |

### 4.3 dispatch run 31382245308 的取消说明

| 项 | 说明 |
|---|---|
| 结论 | cancelled（**非失败**） |
| 原因 | concurrency 配置 `group: ${{ github.workflow }}-${{ github.ref }}` + `cancel-in-progress: true`（P2-4 设计）：同 ref（master）新 push run 31382787584 触发时，自动取消排队/运行中的旧 dispatch run |
| 语义 | 属预期行为：旧 run 结果已过时，仅保留最新。其"验证 paths 修复后 workflow 正常执行"的目标已由新 push run 完整承接 |

---

## 5. 全项目扫描结论（logging 资源泄漏）

### 5.1 `logging.disable` 全量清单（8 处）

| 文件 | 位置 | 性质 | 结论 |
|---|---|---|---|
| tests/performance/test_knowledge_link_perf.py | autouse fixture | 测试 | ✅ 已修复（305282cf） |
| tests/integration/test_orchestrator三层路由_e2e.py | 函数内 try/finally | 测试 | ✅ 已修复（6ada3dc1） |
| scripts/bench_list_cache_compare.py | 模块顶层 | 基准脚本 | ⚪ 不改：独立进程，刻意关闭日志避免 I/O 干扰计时，不进 pytest collection |
| scripts/bench_knowledge_links.py | 模块顶层 | 基准脚本 | ⚪ 不改（同上） |
| scripts/probe_list_100k_perf.py | 模块顶层 | 基准脚本 | ⚪ 不改（同上） |
| scripts/run_p4_benchmark.py | 模块顶层 | 基准脚本 | ⚪ 不改（同上） |

### 5.2 `basicConfig`（弱全局修改）安全性论证

- tests/ 下 30+ 处模块级 `basicConfig(level=...)` **均无 `force=True`**（全项目 grep 0 处）→ Python logging 语义：root logger 已有 handler（conftest `_setup_test_logging` L100 已安装 FileHandler+StreamHandler）时 `basicConfig` 是 **no-op**
- 即使个别生效，conftest `reset_global_singletons` 每测试后恢复 root handlers/level → 残余影响被兜底
- `force=True` 仅出现在生产入口（app_server.py、file_monitor.py、main.py）与独立脚本，均非测试文件

### 5.3 收集范围边界

- observability-ci 全项目分片仅扫 `tests/`（split_unit_tests.py `--root tests`），且 EXCLUDED 排除 tests/performance/、tests/stress/ 等
- agent/tests/、memory/tests/、mcp_services/ 不在全项目分片范围，其模块级 basicConfig 亦无 force，无泄漏语义

---

## 6. 遗留风险与后续建议

| 项 | 建议 | 优先级 |
|---|---|---|
| conftest 防线覆盖不足 | `reset_global_singletons` 增补 `manager.disable` 快照/恢复（`_saved_disable = logging.root.manager.disable`），形成最终兜底 | 🟠 中 |
| 新增测试准入 | 文档/PR 检查项：测试内禁止无保护地调用 `logging.disable` / `basicConfig(force=True)`；如需抑制日志必须 try/finally 或 autouse fixture | 🟢 低 |
| CI 触发回归 | observability-ci paths 已由 run 31382787584 实证生效；后续新增 tests/ 子目录无需再改 paths（`tests/**` 已覆盖） | 🟢 低 |
| 基准脚本 | scripts/bench_*.py 的模块级 disable 保持现状（独立进程，无泄漏语义）；如需被 import 复用，再迁移为上下文管理器 | 🟢 低 |

---

## 7. 认知总结（三义复盘）

- **【不易】** `logging.disable` 是进程级全局状态（manager.disable），其恢复必须与调用同生命周期绑定（try/finally 或 fixture）；conftest 防线按 handler/level 设计，无法兜底。
- **【变易】** 模块级 import 副作用、函数内非 finally 恢复，是同一风险的两种形态，修复模式需分别匹配（autouse fixture / try/finally），不可一刀切。
- **【简易】** 最简充分解：不改测试语义、不删日志抑制、不降断言强度，仅把"恢复"移到必达路径；CI 侧把分片脚本与覆盖目录纳入触发路径，让修复天然被验证。
