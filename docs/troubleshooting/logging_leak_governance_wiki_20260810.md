# logging 全局状态泄漏治理 — 根因分析与技术复盘（2026-08-10）

> **适用读者**：测试维护者 / CI 治理 / 日志链路开发者
> **一句话结论**：`logging.disable()` 是进程级全局状态（`manager.disable`），调用后从不自动恢复；测试中必须用 try/finally 或 autouse fixture 保证恢复，否则同进程后续所有 caplog/assertLogs 静默失效。conftest 原有的 handlers/level 快照兜底覆盖不到该状态，已补丁修复。
>
> 本文整合技术复盘 + 根因分析，可直接提交至 Wiki / 知识库。

---

## 1. 背景与结论速览

master 分支 CI 治理期间，Shard 4 反复 flake（serial 标记后仍失败）、Shard 1 报 `FileNotFoundError`，两条线索最终汇聚到 **logging 全局状态泄漏** 与 **CI 触发路径漏配** 两类问题。

| # | 问题 | 风险等级 | 状态 |
|---|---|---|---|
| 1 | test_knowledge_link_perf.py 模块级 `logging.disable(CRITICAL)`（collection import 副作用） | 🔴 高 | ✅ 已修复（autouse fixture） |
| 2 | test_orchestrator 函数内 `logging.disable(WARNING)` 恢复代码不在 finally，断言失败即泄漏 | 🟠 中 | ✅ 已修复（try/finally） |
| 3 | conftest `reset_global_singletons` 快照仅覆盖 handlers/level，覆盖不到 `manager.disable` | 🟠 中 | ✅ 已修复（补 manager.disable 快照） |
| 4 | tests/ 下 30+ 处模块级 `basicConfig`（均无 force=True） | 🟢 低（no-op） | 无需修复 |
| 5 | scripts/ 下 4 处模块级 `logging.disable(CRITICAL)` | 🟢 低（独立基准脚本） | 无需修复 |
| 6 | observability-ci push paths 漏配（agent/** 未全覆盖、分片脚本未纳入） | 🔴 高 | ✅ 已修复 |
| 7 | 泄漏无法被自动化捕获（无扫描器） | 🟠 中 | ✅ 已修复（扫描器 + 双防线） |

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

- `manager.disable` 初始为 0（NOTSET），调用 `logging.disable(WARNING)` 后变为 30，同进程**所有** logger 的 WARNING 以下日志全部静默。
- 仅调 `logging.disable(NOTSET)`（0）才能恢复——**任何测试一旦调用且未恢复，同进程后续所有 caplog / assertLogs / log capture 全部失效**。
- 它既不改变 root logger 的 `handlers`，也不改变 `level`，因此 conftest 的 handlers/level 快照恢复**完全感知不到**这次污染。

### 2.2 两种泄漏模式

**模式 A：模块级 import 副作用**（test_knowledge_link_perf.py，Shard 4 flake 根因）

```python
# 修复前——模块顶层执行，pytest collection import 该文件时即污染全局
logging.disable(logging.CRITICAL)
```

pytest collection 阶段 import 测试文件即执行模块顶层代码，`manager.disable` 被置为 50，同进程所有 serial 日志断言测试静默失败。定位手法：pytest `pytest_collectstart` 钩子逐模块监控 `manager.disable`，一次命中 `[POLLUTE] during collection of test_knowledge_link_perf.py: manager.disable 0 -> 50`。

**模式 B：函数内调用但恢复不在 finally**（test_orchestrator）

```python
# 修复前——断言在前，恢复在后；任一断言失败即跳过恢复
_logging.disable(_logging.WARNING)
...  # 测试主体 + 多个 assert
Orchestrator._SEM_API_OVERRIDE = None   # 恢复代码在正常路径末尾
_logging.disable(_logging.NOTSET)
```

### 2.3 conftest 防线的覆盖边界（关键认知）

`reset_global_singletons`（autouse，tests/conftest.py）：

```python
_root_logger = logging.getLogger()
_saved_handlers = _root_logger.handlers[:]
_saved_level = _root_logger.level
yield
_root_logger.handlers = _saved_handlers
_root_logger.setLevel(_saved_level)
```

- **覆盖**：root handler 增删、root level 修改 → 有效。
- **不覆盖**：`manager.disable`（logging.disable 的落脚点）、handler 内嵌 mutable 状态、`captureWarnings` 开关 → 全部漏网。

> 教训：conftest 的全局恢复防线按"已知污染源"设计，无法防御"进程级、非 handlers/level 落脚点"的状态修改。凡使用 `logging.disable` 的测试必须自带恢复。

### 2.4 CI paths 漏配（触发验证缺口）

observability-ci.yml push paths 原仅列 monitoring/observability/health/knowledge/log_system 子目录与 2 个根级文件，**agent/utils、tools、memory、orchestrator、skills_mgmt 等目录及 logging_utils.py 等根级模块改动不触发全项目 6-shard 验证** → safe_logger 缺 makedirs 同类缺陷无法被捕获。另有 `scripts/split_unit_tests.py`（直接决定测试分配）不在触发路径。

---

## 3. 修复方案

### 3.1 测试修复：两种模式

**模式一：autouse fixture + try/finally**（test_knowledge_link_perf.py，替代模块级副作用）

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

**模式二：函数内 try/finally**（test_orchestrator）

```python
_logging.disable(_logging.WARNING)
try:
    Orchestrator._SEM_API_OVERRIDE = {"enabled": True, "min_score": 0.3}
    ...  # 全部测试主体与断言
finally:
    # 恢复全局 logging 状态与单例缓存：断言失败时也执行
    Orchestrator._SEM_API_OVERRIDE = None
    Orchestrator._clear_semantic_config_cache()
    _logging.disable(_logging.NOTSET)
```

### 3.2 conftest 兜底补丁（最终防线）

```python
# 0.1 快照进程级 logging.disable 阈值：manager.disable 是进程级全局状态
#     （logging.disable() 落脚点），不在 handlers/level 内，上述快照覆盖不到
_saved_manager_disable = logging.root.manager.disable
yield
logging.root.manager.disable = _saved_manager_disable
```

### 3.3 CI paths 修复

- `agent/**` 全覆盖（合并原分散子目录条目，消除缺口）
- `scripts/split_unit_tests.py` 纳入 paths（分片脚本改动须触发全项目验证）
- 修复验证过程暴露两个 GitHub 机制：
  1. **skip-ci 连带跳过**：带 `[skip ci]` 的提交作为 push HEAD 会跳过整个 push → 须用无 skip 标记的提交重推
  2. **修改 workflow 文件自身不触发该 workflow 的 push 运行**（防递归）→ 用 `gh workflow run` 手动 dispatch 兜底验证

### 3.4 扫描器 + 双防线集成

**脚本** `scripts/check_logging_disable_leak.py`：
- AST 静态分析：识别 `import logging [as X]` 别名，判定每个 `X.disable()` 是否被 try/finally 配对恢复或函数级 finally 恢复保护
- CLI：`--root` / `--exclude` / `--only-under`（限定风险目录）/ `--exit-nonzero-on-risk`

**双防线**：
- pre-commit hook `logging-disable-leak-scan`：commit 阶段阻断 tests/ 下未受保护调用（本地防线）
- ci.yml `code-quality` job 追加同名 step：防 `--no-verify` 绕过（远端兜底，参照 BOM 污染监控既有模式）

> `--only-under tests` 设计：仅对 tests/ 强制；scripts/ 独立基准脚本的模块级 disable 属刻意设计（避免日志 I/O 干扰计时），不误伤。

---

## 4. 验证数据

### 4.1 本地验证

| 验证项 | 结果 |
|---|---|
| test_orchestrator 全文件 | ✅ 11/11 passed |
| test_orchestrator + test_knowledge_card（caplog 密集） | ✅ 76/76 passed（conftest 补丁后） |
| conftest 兜底有效性（故意泄漏 disable → 后续 caplog 仍可捕获） | ✅ 通过 |
| hook 拦截（故意写错文件，`pre-commit run` + 真实 `git commit`） | ✅ 均被阻断（exit 1，HEAD 未变） |

### 4.2 CI 验证矩阵

| Run | 触发 | 结论 |
|---|---|---|
| 31382787584（head 6ada3dc1，try/finally 修复） | master push | ✅ **success，22/22 job 全绿**，Shard 1-6 全过 |
| 31382245308（paths 修复验证） | workflow_dispatch | cancelled（被同 ref 新 push run 按 concurrency 取消，设计内） |
| 31385091083（head a5f49d32，conftest 兜底） | master push | ✅ **success，Shard 1-6 全过 + 质量门禁通过** |

### 4.3 扫描器实测

- 全项目 `logging.disable` 共 8 处：2 处测试（已修复，判定为受保护）+ 4 处 scripts/ 独立基准脚本（模块顶层，刻意设计，不拦截）+ 2 处脚本恢复调用
- `--only-under tests` 下：0 处未受保护 → exit 0（不误伤）

---

## 5. 附带教训：import 副作用与隐式目录依赖（博客浓缩）

**教训一：模块级 import 副作用**

pytest collection 阶段 import 测试文件时执行模块顶层代码。任何"修改全局状态且不恢复"的模块级调用（`logging.disable`、`basicConfig(force=True)`、全局 mock、改 env）都会污染同进程。防御：全局状态修改必须与生命周期绑定（fixture 或 try/finally）。

**教训二：FileHandler 不创建父目录**

`logging.FileHandler(log_path)` 不会创建父目录，CI 全新 checkout 无 `logs/` 时直接抛 `FileNotFoundError`。修复：`os.makedirs(os.path.dirname(log_path), exist_ok=True)` 前置。排查手法：hook 替换 `logging.disable`/`FileHandler` 打堆栈，一次定位。

---

## 6. 遗留风险与后续建议

| 项 | 建议 | 优先级 |
|---|---|---|
| 本地 hook 未部署 | 各开发机需执行 `pre-commit install` 启用本地防线（本仓库 git hook 含 legacy guard，Windows 下 pre-commit 解析 `#!/bin/sh` 受限，建议仓库外 `core.hooksPath` 或修复 legacy shebang） | 🟠 中 |
| 新增测试准入 | 测试内禁止无保护地调用 `logging.disable` / `basicConfig(force=True)`；如需抑制日志必须 try/finally 或 autouse fixture（扫描器已强制 tests/ 范围） | 🟢 低 |
| CI 触发回归 | observability-ci paths 已实证生效；`tests/**` 已覆盖全部测试子目录 | 🟢 低 |
| 基准脚本 | scripts/bench_*.py 模块级 disable 保持现状（独立进程，无泄漏语义） | 🟢 低 |

---

## 7. 关联提交

| 提交 | 内容 |
|---|---|
| 305282cf | test_knowledge_link_perf autouse fixture 修复 |
| 8fb4fcc0 | CI paths 全覆盖（agent/** + split_unit_tests.py） |
| 6ada3dc1 | test_orchestrator try/finally 修复 |
| a5f49d32 | conftest manager.disable 快照兜底 |
| cd82ab6b | 扫描脚本 check_logging_disable_leak.py |
| dbf9d576 | pre-commit hook + ci.yml 双防线集成 |
