# P1 项排查方案 — B-2 collect ERROR 归因（2026-08-14）

**目标**: `test_evolution_loop.py` / `test_evolver_real_eval.py` 基线 collect ERROR 归因与修复
**状态前置**: 冒烟验证（2026-08-14）当前文件状态单跑 **58 passed / 0 failed / 4.91s**，collect ERROR **未复现**
**依据**: `docs/zh/B类遗留项修复执行计划_20260814.md` P1 节 + `failures_baseline.txt`（T-0 固化 2 项 ERROR）

---

## 一、现象与已知信息

| 项 | 文件 | 基线现象 | 当前单跑 |
|---|---|---|---|
| B-2a | `tests/unit/test_evolution_loop.py` | ERROR（collect） | 13 passed |
| B-2b | `tests/unit/test_evolver_real_eval.py` | ERROR（collect） | 12 passed |

- 基线（2026-08-13 `-p no:randomly` 全量）：`ERROR tests/unit/test_evolution_loop.py`、`ERROR tests/unit/test_evolver_real_eval.py`（见 `failures_baseline.txt` 前 2 行）
- ⚠️ 两文件当前为 **M 状态（并行会话改动）**，本会话禁动，排查前先确认提交归属（硬约束）
- ⚠️ `test_evolver_real_eval.py` 曾依赖 transformers 环境链（与 vector_store_sqlite_vec 同批问题域），已确认 `find_spec` 修复未覆盖此文件——需验证其 import 链

---

## 二、排查步骤（按序执行）

### Step 1: 全量固定 seed 复现（T-4 前置，确认存活状态）

```bash
# 全量固定 seed（1-2 小时，4×1 串行语义由 pytest 单进程保证随机顺序）
python -m pytest tests/ -q --randomly-seed=20260813 --tb=short 2>&1 | Select-String "ERROR|evolution_loop|evolver_real_eval"
```

- **命中 ERROR** → 进入 Step 2 归因
- **未命中** → 标记 B-2 连带消失（疑因 find_spec / logging.disable 修复），更新总账待办清单，本项闭环

### Step 2: 最小复现（仅当 Step 1 命中）

```bash
# ① 单独收集验证（--co 只收集不执行，秒级）
python -m pytest tests/unit/test_evolution_loop.py --co -q
python -m pytest tests/unit/test_evolver_real_eval.py --co -q

# ② 若单文件 --co 无 ERROR → 顺序污染：全量固定 seed 下与前置文件共存时触发
#    二分定位前置模块（tests/ 下按字母序一半一半裁剪，结合 --randomly-seed）
```

### Step 3: 归因分类（collect ERROR 五大根因）

| 根因 | 特征 | 定位方法 |
|---|---|---|
| a. 模块顶层 import 失败 | `ModuleNotFoundError` / `ImportError` | 读取 collect 错误栈首个 traceback；`python -c "import tests.unit.test_evolution_loop"` 直测 |
| b. transformers/torch 重型 import 冷启动 | 收集卡死或 C 扩展崩溃（0xC0000005） | 检查模块顶层是否真实 `import sentence_transformers/torch` → find_spec 修复模板 |
| c. 并行会话代码签名变更 | `TypeError: __init__() got an unexpected keyword` | 对比 `agent/skills_mgmt/evolution.py` 等签名；确认 M 状态改动归属 |
| d. conftest/fixture 冲突 | `fixture not found` / 重复注册 | `--co` 错误栈含 conftest 帧；检查 tests/unit/conftest.py 与根 conftest 双份定义 |
| e. sys.path 污染 | 同名模块被错误解析 | `--import-mode=importlib` 已启用；`python -c "import sys; print([p for p in sys.path])"` |

### Step 4: 修复实施（模板见下节）

### Step 5: 验证

```bash
# 单文件
python -m pytest tests/unit/test_evolution_loop.py tests/unit/test_evolver_real_eval.py -q --randomly-seed=20260813
# 全量固定 seed 复查
python -m pytest tests/ -q --randomly-seed=20260813
```

---

## 三、预期修复代码框架

### 模板 1: 顶层重型 import 检测（根因 b，对齐 `test_vector_store_sqlite_vec` 已落地模式）

```python
# 【不易】模块顶层禁真实 import 重型 C 扩展（torch/transformers/sentence_transformers）。
# 冷启动时真实加载会卡死收集（thread 超时无法中断系统调用），用 find_spec 仅查注册。
import importlib.util

_HAVE_SENTENCE_TRANSFORMERS = (
    importlib.util.find_spec("sentence_transformers") is not None
)

pytestmark = pytest.mark.skipif(
    not _HAVE_SENTENCE_TRANSFORMERS,
    reason="sentence_transformers 不可用（环境缺依赖），跳过真实推理路径",
)
```

### 模板 2: import 依赖缺失（根因 a，防御式降级）

```python
# 模块顶层：
try:
    from agent.skills_mgmt.evolution import EvolutionRecord  # noqa: F401
except ImportError as _e:  # 【变易】缺依赖时降级为 skip，而非 collect ERROR
    import pytest
    pytest.skip(f"evolution 模块依赖缺失: {_e}", allow_module_level=True)
```

### 模板 3: 签名对齐（根因 c，仅经协调确认后可改）

```python
# 若 EvolutionRecord.__init__ 基线报 'params' 参数不匹配：
# 先确认 agent/skills_mgmt/evolution.py 与调用方（parent_selection.py 等）签名一致，
# 跨会话文件改动须先协调提交归属（硬约束），本会话不代改。
```

---

## 四、判定与闭环

| 情形 | 动作 | 验收 |
|---|---|---|
| Step 1 未命中 | B-2 标记连带消失，更新总账 | 全量固定 seed 0 ERROR |
| 根因 a/b | 按模板 1/2 修复 | 单文件 + 全量固定 seed 通过 |
| 根因 c | 协调并行会话提交后复跑 | 签名一致 + 全量通过 |
| 根因 d/e | 修复 conftest / 路径注入 | 单文件 + 全量固定 seed 通过 |

**最终验收**: `python -m pytest tests/ -q --randomly-seed=20260813` → 0 failed / 0 errors

---

## 五、关联文档

- 执行计划: `docs/zh/B类遗留项修复执行计划_20260814.md`
- 总账: `docs/zh/剩余基线遗留项待办清单_20260814.md`
- 失败基线: `failures_baseline.txt`
- 修复先例: `398bb32e`（test_vector_store_sqlite_vec find_spec）
