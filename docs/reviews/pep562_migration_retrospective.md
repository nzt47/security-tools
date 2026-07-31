# PEP 562 迁移与 CI 循环依赖守卫 — 技术复盘

> 日期: 2026-08-01
> 范围: `agent.monitoring.__init__` PEP 562 懒加载迁移 + `check_circular_deps.py` 接入 CI
> 关联文档: [循环依赖排查指南](../architecture/circular_dependencies_troubleshooting.md)

## 一、背景与目标

### 1.1 起因

此前 `agent.orchestrator` 与 `agent.digital_life` 之间存在循环导入，导致 CI 单元测试收集阶段
抛出 `ImportError: cannot import name 'Orchestrator' from partially initialized module`。
该问题已通过 PEP 562 懒加载 + 文件末尾导入修复（详见排查指南）。

复盘扫描发现 `agent.monitoring.__init__` 仍在**顶层硬导入 7 个子模块**（tracing/metrics/
error_reporter/decorators/performance/search/prometheus），是全项目潜在循环风险最高的包。
同时缺少自动化守卫，新增双向依赖只能靠人工发现。

### 1.2 目标

1. 将 `agent.monitoring.__init__` 迁移到 PEP 562 懒加载，消除最大风险点。
2. 将 `check_circular_deps.py` 接入 CI，新增顶层双向依赖时自动阻断。
3. 全程零回归：被 27 个文件依赖的 monitoring 包迁移后，所有现有用法必须保持可用。

## 二、PEP 562 迁移方案（以 monitoring 为案例）

### 2.1 迁移前现状

```python
# agent/monitoring/__init__.py (迁移前)
from agent.monitoring.tracing import (TraceContext, get_trace_id, ...)  # 24 个符号
from agent.monitoring.metrics import (MetricsCollector, ...)            # 6 个符号
from agent.monitoring.error_reporter import (ErrorReporter, ...)        # 12 个符号
from agent.monitoring.decorators import (monitor_latency, ...)          # 5 个符号
from agent.monitoring.performance import (Timer, ...)                   # 19 个符号
from agent.monitoring.search import (SearchPerformanceMonitor, ...)     # 8 个符号
from agent.monitoring.prometheus import (_PROMETHEUS_AVAILABLE, ...)    # 5 个符号
```

**问题**：`import agent.monitoring` 会立即触发全部 7 个子模块加载，既拖慢启动，
也让任何反向引用 monitoring 包级符号的代码可能形成循环。

### 2.2 方案选型

| 方案 | 描述 | 优缺点 |
|------|------|-------|
| A. 逐符号映射 | `符号 -> (模块路径, 符号名)`，每个符号一行 | 与 orchestrator 一致；但 80+ 符号映射表冗长 |
| **B. 子模块→符号列表** | `子模块名 -> [符号列表]`，运行时反向索引 | **采用**。紧凑、易维护，增删符号只改一处 |

**选用方案 B** 的关键代码：

```python
_PKG = __name__  # "agent.monitoring"

_LAZY_MODULES = {
    "tracing": ["TraceContext", "get_trace_id", ...],
    "metrics": ["MetricsCollector", ...],
    # ... 共 7 个子模块
}

# 反向映射: 符号名 -> 来源子模块名 (O(1) 查找)
_LAZY_IMPORTS = {}
for _submod, _symbols in _LAZY_MODULES.items():
    for _sym in _symbols:
        _LAZY_IMPORTS[_sym] = _submod

def __getattr__(name):
    submod = _LAZY_IMPORTS.get(name)
    if submod is not None:
        import importlib
        attr = getattr(importlib.import_module(f"{_PKG}.{submod}"), name)
        globals()[name] = attr  # 缓存到全局, 后续访问零开销
        return attr
    raise AttributeError(f"module {_PKG!r} has no attribute {name!r}")
```

### 2.3 关键不变量（不易）

| 不变量 | 验证方式 |
|--------|---------|
| `__all__` 与历史导出完全一致 | 逐项比对迁移前后 `__all__` |
| `from agent.monitoring import X` 100% 可用 | 8 项兼容性测试 |
| 子模块直接访问仍可用（`from agent.monitoring import tracing`） | Python 原生导入机制处理，不走 `__getattr__` |
| `__version__` 保留 | 测试断言 |
| 不存在符号抛 `ImportError`（向后兼容） | `__getattr__` raise `AttributeError`，Python 转为 `ImportError` |

### 2.4 迁移后效果

- `import agent.monitoring` 不再触发任何子模块加载（顶层零导入）。
- 首次访问符号时按需加载对应子模块，之后缓存到 `globals()`，零开销。
- `agent.monitoring.__init__` 出现在扫描脚本的 PEP 562 列表中。

## 三、CI 接入步骤

### 3.1 脚本改造：退出码语义

[scripts/check_circular_deps.py](../../scripts/check_circular_deps.py) 的 `main()` 返回发现的循环列表，
`__main__` 据此决定退出码：

```python
if __name__ == "__main__":
    parser = argparse.ArgumentParser(...)
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="打印触发阻断的具体文件路径与行号")
    args = parser.parse_args()
    cycles = main(verbose=args.verbose)
    sys.exit(1 if cycles else 0)
```

**退出码语义**：
- `exit 0`：未发现顶层双向依赖（CI 通过）
- `exit 1`：发现双向依赖（CI 阻断）
- 其他输出（PEP 562 列表、风险表、函数内导入统计）仅信息性，不影响退出码

### 3.2 --verbose 参数（JSON 输出）

`--verbose` 模式下，循环依赖结果以 **JSON 格式输出到 stdout**，人类可读信息走 stderr，
便于 `jq` 或脚本程序化解析循环依赖路径：

```json
{
  "cycles": [
    {
      "modules": ["agent._temp_cycle_a", "agent._temp_cycle_b"],
      "edges": [
        {
          "from": "agent._temp_cycle_b",
          "to": "agent._temp_cycle_a",
          "locations": [
            {"file": "agent/_temp_cycle_b.py", "line": 2, "statement": "from agent._temp_cycle_a import ..."}
          ]
        },
        {
          "from": "agent._temp_cycle_a",
          "to": "agent._temp_cycle_b",
          "locations": [
            {"file": "agent/_temp_cycle_a.py", "line": 2, "statement": "from agent._temp_cycle_b import ..."}
          ]
        }
      ]
    }
  ],
  "summary": {"total_cycles": 1, "total_edges": 2}
}
```

**输出分离策略**：
- 默认模式（无 `--verbose`）：人类可读到 stdout（CI 日志直接可读），退出码 0/1
- `--verbose` 模式：JSON 到 stdout（`python check_circular_deps.py -v | jq '.cycles'`），人类可读到 stderr（终端可见）

实现要点：`extract()` 维护 `import_locations[(src_mod, dst_mod)] -> [(filepath, lineno, statement)]`，
`build_cycles_json()` 将其转为 `cycles[] -> edges[] -> locations[]` 三层嵌套结构。

### 3.3 workflow 接入

在 [.github/workflows/test.yml](../../.github/workflows/test.yml) 的 `code-quality` job
新增"循环依赖守卫"步骤：

```yaml
      - name: 循环依赖守卫
        # Why: 用 AST 扫描 agent/ 顶层双向导入, 发现新增循环时阻断 CI (exit 1).
        # 仅依赖 Python 标准库(ast/os/sys), 无需安装项目依赖.
        run: |
          python scripts/check_circular_deps.py
```

**设计决策**：
- **不用 `|| true`**：与 black/isort/mypy/flake8 等容错步骤不同，循环依赖是硬性约束，必须阻断。
- **放在 code-quality job**：该 job 轻量（不需矩阵、不需安装项目依赖），脚本只用标准库，反馈快。
- **不新增 job**：复用现有 job，符合最小变更原则。

## 四、验证方法与结果

### 4.1 向后兼容性验证（PEP 562 迁移）

8 项测试全部通过：

| 验证项 | 结果 |
|--------|------|
| 包级符号访问（TraceContext 等） | ✅ |
| 子模块访问（from agent.monitoring import tracing） | ✅ |
| import 子模块（import agent.monitoring.metrics） | ✅ |
| 不在 `__all__` 的子模块（alert_evaluator） | ✅ |
| `__version__` 保留 | ✅ |
| 符号缓存到 globals() | ✅ |
| `dir()` 补全 | ✅ |
| 不存在符号抛 ImportError | ✅ |

### 4.2 回归测试

| 测试套件 | 结果 |
|---------|------|
| test_monitoring.py + test_import_smoke.py | **80 passed** |
| test_digital_life_comprehensive.py | **83 passed** |

### 4.3 阻断逻辑验证（注入临时循环）

创建两个临时文件形成双向依赖，验证脚本行为：

| 场景 | 退出码 | 输出 |
|------|--------|------|
| 正常代码（无循环） | `0` | `[OK] 未发现顶层双向依赖` |
| 注入循环（默认模式） | `1` | `[风险] a <--> b` + 提示加 `--verbose` |
| 注入循环（--verbose） | `1` | `[风险]` + 文件:行号 + 导入语句 + 方向 |

验证后临时文件已删除，脚本恢复 `exit 0`。

## 五、经验总结

### 5.1 做得好的地方

1. **先用 AST 验证安全性再迁移**：参考 orchestrator 修复时的 AST 校验思路，确认 monitoring 的子模块符号无加载时依赖，保证迁移安全。
2. **紧凑映射方案**：用"子模块→符号列表"替代"逐符号映射"，80+ 符号的映射表从冗长变为可读，后续增删符号只改一处。
3. **注入式验证**：不修改真实代码，用临时文件验证阻断逻辑，验证后立即清理，零副作用。
4. **退出码与信息分离**：只有双向依赖影响退出码，其他输出（PEP 562 列表、风险表）仅信息性，避免 CI 因信息性输出误报。

### 5.2 注意事项

1. **PEP 562 与子模块访问的边界**：`from package import submodule` 由 Python 原生导入机制处理，不走 `__getattr__`；`from package import symbol` 才走 `__getattr__`。迁移时无需为子模块在 `__getattr__` 中特殊处理。
2. **`AttributeError` → `ImportError` 转换**：`__getattr__` raise `AttributeError` 后，`from package import nonexistent` 会收到 `ImportError`，这是 Python 的预期行为，无需额外处理。
3. **并行 Edit 的陷阱**：对同一文件的多个并行 Edit 可能因文件状态竞争导致部分丢失（本次 disaster_recovery 标记初次有 4/6 丢失）。对同一文件的多个编辑应串行或验证后补齐。
4. **CI 守卫步骤不加 `|| true`**：与其他容错步骤不同，循环依赖是硬约束，必须阻断。

### 5.3 后续改进

1. **扩大 PEP 562 覆盖**：`extensions`/`cognitive`/`memory` 等子模块数 ≥ 3 的包择机迁移。
2. **pre-commit 接入**：将 `check_circular_deps.py` 也加入 pre-commit hook，本地提交前即拦截。
3. **依赖图可视化**：扩展脚本生成 `agent/` 依赖图（如 graphviz），直观展示模块关系。

## 六、transformers 超时处理与重试策略

### 6.1 根因分析

集成测试完整运行时部分测试超时，根因链（非 monitoring 迁移导致）：

1. 某些集成测试触发 `agent.skills_mgmt.reranker` 的函数执行
2. 函数内 `from sentence_transformers import CrossEncoder`（延迟导入，[reranker.py:367](../../agent/skills_mgmt/reranker.py)）
3. `sentence_transformers` 导入触发 `transformers` 库加载 — Windows 上耗时 **29 秒**
   （`transformers.utils.import_utils.define_import_structure` 扫描 models 目录）
4. `transformers` 尝试从 HuggingFace 下载模型元数据 — 网络不可达时无限重试至超时

> [scripts/download_bge_reranker_v2_m3_modelscope.py](../../scripts/download_bge_reranker_v2_m3_modelscope.py)
> **不是直接原因**（它是手动下载工具，不在测试时运行），但揭示了问题根源：项目依赖
> `sentence_transformers` 加载 reranker 模型，而模型未本地缓存时会触发网络下载。

### 6.2 分层重试策略

按成本从低到高分五层，逐级升级：

| 层级 | 策略 | 实施 | 适用场景 |
|------|------|------|---------|
| **L1** | 离线模式 | `HF_HUB_OFFLINE=1` + `TRANSFORMERS_OFFLINE=1` | 本地已缓存模型，跳过网络检查 |
| **L2** | 超时放宽 | `--timeout=300`（从 60 秒调到 300 秒） | 容忍 transformers 导入 29 秒 + 余量 |
| **L3** | 模型预下载 | 运行 BGE 下载脚本 / `huggingface-cli download` | 首次部署或 CI 冷启动 |
| **L4** | 测试标记 | `@pytest.mark.requires_model` + CI `-m "not requires_model"` | 隔离需模型的测试，常规 CI 跳过 |
| **L5** | 缓存持久化 | GitHub Actions `actions/cache` 缓存 `~/.cache/huggingface` | CI 跨 run 复用模型，避免重复下载 |

**推荐组合**：本地用 L1+L2，CI 用 L1+L2+L5，发布前用 L3+L4 跑全量。

### 6.3 具体实施

#### 本地运行集成测试

```bash
# 推荐: 离线模式 + 300 秒超时 (L1+L2)
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  pytest tests/integration/ --timeout=300 -m "not slow and not requires_llm"
```

#### 预下载模型（L3，一次性）

```bash
# 方式 1: modelscope 镜像 (推荐, huggingface.co 不可达时)
python scripts/download_bge_reranker_v2_m3_modelscope.py

# 方式 2: huggingface-cli (网络通畅时)
huggingface-cli download BAAI/bge-reranker-v2-m3
```

#### CI 环境配置（L1+L2+L5）

```yaml
# .github/workflows/test.yml
- name: 缓存 HuggingFace 模型
  uses: actions/cache@v4
  with:
    path: ~/.cache/huggingface
    key: hf-cache-${{ hashFiles('**/requirements.txt') }}
    restore-keys: hf-cache-

- name: 运行集成测试
  env:
    HF_HUB_OFFLINE: "1"
    TRANSFORMERS_OFFLINE: "1"
  run: |
    pytest tests/integration/ \
      --timeout=300 \
      -m "not slow and not requires_llm and not requires_model"
```

#### 隔离需模型的测试（L4）

```python
# tests/integration/test_reranker_integration.py
import pytest

@pytest.mark.requires_model  # 常规 CI 跳过, 仅发布前手动运行
def test_reranker_chinese_discrimination():
    from sentence_transformers import CrossEncoder
    model = CrossEncoder("BAAI/bge-reranker-v2-m3")
    # ...
```

### 6.4 重试策略的失败模式与兜底

| 失败场景 | 现象 | 兜底方案 |
|---------|------|---------|
| 模型未缓存 + 离线模式 | `OSError: Cannot load model` | 退出 L1，升级到 L3 预下载 |
| modelscope 镜像也不可达 | 下载脚本失败 | 用 `git lfs clone` 或手动下载 |
| transformers 导入 > 300 秒 | `+++ Timeout +++` | 检查磁盘 I/O；或用 L4 跳过该测试 |
| CI 缓存未命中 | 首次 run 仍慢 | 可接受；后续 run 命中缓存后正常 |

### 6.5 验证结果

| 场景 | 配置 | 结果 |
|------|------|------|
| 默认（60 秒超时） | `--timeout=60` | ❌ transformers 导入 29 秒 + 网络下载超时 |
| 离线 + 300 秒 | `HF_HUB_OFFLINE=1 --timeout=300` | ✅ 1979 个集成测试全部通过（exit 0） |
| 轻量子集 | `--timeout=60` 跳过重型文件 | ✅ 73 个测试通过（11 个文件） |

## 七、变更清单

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| [agent/monitoring/__init__.py](../../agent/monitoring/__init__.py) | 重写 | 顶层硬导入 → PEP 562 懒加载 |
| [scripts/check_circular_deps.py](../../scripts/check_circular_deps.py) | 新增 + 改造 | 新增扫描脚本；退出码语义 + --verbose JSON 输出（stdout/stderr 分离） |
| [.github/workflows/test.yml](../../.github/workflows/test.yml) | 新增步骤 | code-quality job 加"循环依赖守卫" |
| [docs/architecture/circular_dependencies_troubleshooting.md](../architecture/circular_dependencies_troubleshooting.md) | 更新 | 附录 A 同步 monitoring 迁移状态 |
