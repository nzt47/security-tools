# PR #634 L3 容器 sqlite_vec 降级修复复盘

- 日期：2026-08-15
- 范围：L3 Docker 回归测试 `expected sqlite_vec, got json` 全链路根因分析与 4 轮修复（修复点 16-19）
- 前置：修复点 13-15（HF 缓存根语义统一、VOLUME 移除）已落地，build 镜像内三模型 `_is_model_fully_cached=True`，但容器内 pytest 运行时仍 `sentence-transformers 编码器加载失败，降级`

---

## 1. 问题现象

| 阶段 | 现象 |
|---|---|
| build-image | 三模型 `fully_cached=True`，`hub_exists=True`（修复点 15 生效） |
| 容器内 pytest | `AssertionError: expected sqlite_vec, got json`（`tests/unit/test_vector_store_sqlite_vec.py` 8 errors + 1 failed） |
| 日志 | `sentence-transformers 编码器加载失败，降级`，**无任何 traceback**（异常被 `except Exception` 静默吞掉） |

## 2. 完整根因链（4 层叠加）

### 层 1：容器内无 `CI` 环境变量 → `_HAS_ST=True` → 集成测试不 skip

[test_vector_store_sqlite_vec.py:38](../tests/unit/test_vector_store_sqlite_vec.py#L38)：

```python
_HAS_ST = False
if not sys.platform.startswith('linux') or not os.environ.get('CI'):
    _HAS_ST = _ilu.find_spec("sentence_transformers") is not None
```

- 设计意图：Linux CI 上探测 `find_spec` 不触发真实 import（防 torch 拉取），故 Linux+CI 时 `_HAS_ST=False` → `skipif` 跳过集成测试。
- **容器内 `docker run` 不继承 runner 的 `CI=true` 环境变量** → `os.environ.get('CI')` 为空 → 走探测分支 → 镜像内装了 ST → `_HAS_ST=True` → 集成测试**不 skip、真实执行**。

### 层 2：`MagicMock()` 模块占位 → 模块级 Mock 检测提前 return None

[test_vector_store_sqlite_vec.py:62](../tests/unit/test_vector_store_sqlite_vec.py#L62) `_enable_st_module_for_patch`（autouse fixture）在 `sys.modules` 无真实 ST 模块时：

```python
_sys.modules["sentence_transformers"] = MagicMock()
```

本意：让 `patch('sentence_transformers.SentenceTransformer')` 的 resolve_name 命中 sys.modules，**不触发真实 import**（防 Windows 0xC0000005：真实 import ST → torch C 扩展崩溃）。

副作用：[vector_store.py](../memory/vector_store/vector_store.py) `_get_shared_encoder` 的防污染检测：

```python
import sentence_transformers as _st_mod
if hasattr(_st_mod, "mock_calls"):      # MagicMock 模块有 mock_calls
    return None                          # ← 提前返回，mock_encoder 根本用不上
```

→ 集成测试降级 json。本地 Windows 全量跑通过是因为前序测试已真实 import ST（`_mod` 非 None → 不占位）；**容器内是首个触碰 ST 的文件 → 必被占位**。

### 层 3：类级 Mock 检测误伤测试合法 `patch`

修复点 17 将占位限定 Windows 后，容器 Linux 不再 mock 模块，但**仍失败**：

```python
from sentence_transformers import SentenceTransformer
if hasattr(SentenceTransformer, "mock_calls"):   # patch 后类必为 MagicMock
    return None
```

`patch('sentence_transformers.SentenceTransformer', return_value=mock_encoder)` 把类替换成 MagicMock → 类级检测判定"污染"→ return None。**该检测无法区分"reranker 测试泄漏的污染"与"本测试主动 patch 的合法用法"**——模块级检测已覆盖 reranker 污染，类级检测纯属冗余且误伤。

### 层 4：移除类级检测后 → 真实 import torch → Segmentation fault

修复点 18 移除类级检测后集成测试真跑（`test_backend_is_sqlite_vec_when_available` 转 PASSED），但下一个测试 `Fatal Python error: Segmentation fault`（exit 139）：

- `patch('sentence_transformers.SentenceTransformer')` 在 Linux 容器内 resolve 时触发**真实 import sentence_transformers → torch 2.12.0+cu130**；
- 容器无 GPU/CUDA 运行时，torch 深层初始化段错误——**与 Windows 0xC0000005 同源**（torch C 扩展崩溃），只是换了平台。

## 3. 4 轮修复步骤

### 修复点 16：异常日志可见化（commit ae8629ef）

`_get_shared_encoder` 的 except 分支从静默 `return None` 改为输出异常详情 + 完整堆栈：

```python
except Exception as e:
    logger.warning("编码器加载失败(model=%s): %r，降级 json 后端", model_name, e, exc_info=True)
    return None
```

作用：把"无 traceback 的降级"变成可诊断的失败现场。

### 修复点 17：ST mock 占位仅限 Windows（commit d5c76dab）

`_enable_st_module_for_patch` 的占位条件加 `sys.platform.startswith('win')`。

- 当时判断"Linux 占位误伤集成测试"，将占位限定 Windows；
- 实测 failed 从 1 → 3（行为变了，根因未除）——**事后证明只是表象，真实根因在类级检测（层 3）**。

### 修复点 18：移除类级 Mock 检测（commit 8c0e4a6c）

删除 `hasattr(SentenceTransformer, "mock_calls")` 分支，保留模块级检测：

```python
if hasattr(_st_mod, "mock_calls"):      # 保留：防 reranker 模块级 MagicMock 残留污染
    return None
```

作用：`patch('...SentenceTransformer', return_value=mock_encoder)` 的 mock encoder 生效 → `test_backend_is_sqlite_vec_when_available` 转 PASSED → 暴露层 4（torch Segfault）。

### 修复点 19：占位模块改用 `types.ModuleType`（最终方案，commit 6ab56ed4）

```python
if _need_mock:
    _placeholder = _types.ModuleType("sentence_transformers")   # 无 mock_calls
    _placeholder.SentenceTransformer = MagicMock()              # patch resolve 命中，不真实 import
    _sys.modules["sentence_transformers"] = _placeholder
yield
if _need_mock:
    _sys.modules.pop("sentence_transformers", None)             # teardown 清理，防残留
```

三个关键点同时满足：

1. **占位模块无 `mock_calls`**（types.ModuleType）→ 模块级检测不拦截 → mock encoder 生效；
2. **`SentenceTransformer` 属性为 MagicMock** → `patch` 的 resolve_name 命中 sys.modules 占位，**不触发真实 import torch** → 无 Segfault（Windows 0xC0000005 与 Linux Segfault 同因，全平台统一）；
3. **teardown pop** → conftest #12b 只清理 `mock_calls` 模块，占位模块需自清。

## 4. 验证结果

| 验证 | 结果 |
|---|---|
| L3 run 31856114570（build + 回归 + 覆盖率 + 总结） | **全绿** |
| L3 回归测试 | **124 passed, 6 skipped**（57.77s，无 failed/error） |
| 本地 `test_vector_store_sqlite_vec.py` 单独跑 | 27 passed / 0 failed（此前 1 failed + 8 errors） |
| 守卫测试 `test_pr634_ci_fixes.py` | 33 passed（新增修复点 16-19 守卫） |
| 逻辑模拟验证 | 模块真实+类patch → 返回 mock encoder；模块 MagicMock → None（防污染保留） |

## 5. 核心教训

1. **【不易】Mock 检测必须只防真实污染**：类级检测 `hasattr(cls, "mock_calls")` 无法区分测试合法 `patch` 与污染泄漏，宁可移除（模块级检测已足够）也不误伤合法用法。
2. **【变易】环境变量差异是跨环境隐性开关**：`docker run` 不继承 runner 的 `CI` env，导致测试的"Linux+CI 探测豁免"逻辑失效——环境判断需显式传递，不能依赖隐式继承。
3. **【简易】占位模块用普通模块对象而非 Mock**：`types.ModuleType`（无 mock_calls）与 `MagicMock()`（有 mock_calls）对下游 Mock 检测语义完全不同，选型即正确性。
4. torch C 扩展崩溃（Windows 0xC0000005 / Linux Segfault）是**平台无关的同类问题**，防护逻辑应跨平台统一而非加平台分支。

## 6. 遗留问题（并行会话领域，非本修复范围）

- 可观测性 Shard 2：`test_eval_stats_aggregates_task02_counters` 未解决（conftest `_DummyCollector` 缺 `increment_counter`，见 [tests/unit/conftest.py:489](../tests/unit/conftest.py#L489)）。
- 新引入：`resolve_autonomy_level` NameError（[agent/orchestrator/orchestrator.py:348](../agent/orchestrator/orchestrator.py#L348) 调用未 import，chat 审计埋点引入）。
- 可观测性 Shard 1：`test_dry_run_uses_config_default_true` 状态随最新 run 复核。
