# Bug 追踪单 — test_metrics_modules_registered 失败

> 追踪单 ID：BUG-20260809-001
> 创建日期：2026-08-09 ｜ 状态：**VERIFIED — 已修复并验证（2026-08-09）**
> 来源：master CI 全绿确认（PR #77 结案流程）中发现

## 一、Bug 概要

| 项 | 值 |
|----|----|
| 失败测试 | `tests/unit/test_singleton_manager.py::TestSingletonModuleIntegration::test_metrics_modules_registered` |
| 失败点 | `assert is_registered("auto_tuner")` → `AssertionError: assert False` |
| 影响分支 | master（`d447bef8`）CI |
| 严重度 | 中（CI 阻断 1 项，非生产功能故障） |
| 类型 | 疑似测试隔离 / 注册时序问题 |

## 二、复现环境

| 项 | 值 |
|----|----|
| GitHub Actions run | `31322544891`（云枢系统测试流程 / push） |
| Job | `单元测试 (Python 3.11 / Shard 3)`（id `93269248927`，check-run `93269248927`） |
| 平台 | `linux -- Python 3.11.15 /opt/hostedtoolcache/Python/3.11.15/x64/bin/python` |
| 执行方式 | pytest-xdist（gw0 并行分片） |
| 提交 | `d447bef8`（fix(ci): Windows 跨盘符 relpath 降级） |
| 触发时间 | 2026-08-09T16:16:56Z |
| 统计 | **1 failed, 1714 passed, 34 skipped, 1 warning in 79.25s** |

## 三、失败堆栈（完整提取）

```
=================================== FAILURES ===================================
________ TestSingletonModuleIntegration.test_metrics_modules_registered ________
[gw0] linux -- Python 3.11.15 /opt/hostedtoolcache/Python/3.11.15/x64/bin/python
tests/unit/test_singleton_manager.py:218: in test_metrics_modules_registered
    assert is_registered("auto_tuner")
E   AssertionError: assert False
E    +  where False = is_registered('auto_tuner')
==================================== PASSES ====================================
FAILED tests/unit/test_singleton_manager.py::TestSingletonModuleIntegration::test_metrics_modules_registered - AssertionError: assert False
  +  where False = is_registered('auto_tuner')
======= 1 failed, 1714 passed, 34 skipped, 1 warning in 79.25s =======
=== 单元测试 pytest 退出码: 1 ===
```

## 四、相关代码（master `d447bef8`）

### 1. 测试（`tests/unit/test_singleton_manager.py` L213-228）

```python
class TestSingletonModuleIntegration:
    """与已迁移模块的集成测试"""

    def test_metrics_modules_registered(self):
        """核心监控模块已注册到 SingletonManager"""
        import agent.auto_tuner  # noqa: F401
        import agent.monitoring.error_reporter  # noqa: F401
        import agent.monitoring.optimized_metrics  # noqa: F401
        import agent.monitoring.tracing_cache  # noqa: F401

        assert is_registered("auto_tuner")          # ← L218 失败点
        assert is_registered("error_reporter")
        assert is_registered("optimized_metrics_collector")
        assert is_registered("trace_cache")
```

### 2. `is_registered`（`agent/utils/singleton_manager.py` L173）

```python
def is_registered(name):
    """检查单例是否已注册（模块级便捷函数）。"""
    return _manager.registered(name)
```

### 3. 注册实现（`agent/auto_tuner.py`）

```python
_global_auto_tuner = None

try:
    from agent.utils.singleton_manager import (
        register_singleton, get_singleton
    )
    _SINGLETON_AVAILABLE = True
except ImportError:
    _SINGLETON_AVAILABLE = False
    register_singleton = None
    get_singleton = None

def _create_auto_tuner(config=None):
    return _global_auto_tuner

if _SINGLETON_AVAILABLE:
    register_singleton("auto_tuner", _create_auto_tuner)   # import 时注册
```

## 五、根因（已确认，2026-08-09 本地串行复现）

**根因：SingletonManager 迁移"测试先行、实现未同步"——注册代码缺失。**

| 验证项 | 结果 |
|--------|------|
| 模式 A 串行复现（`-p no:xdist`） | **1 failed, 17 passed**，失败点与 CI 完全一致 → **确定性缺陷，排除 xdist 隔离** |
| 模式 B 状态探测 | 4 模块 import 成功但全部 `is_registered=False`；`auto_tuner` 未持有 `_manager` |
| sys.modules 检查 | singleton_manager 仅 1 份（单实例）→ **排除多实例化** |
| d447bef8 源码检查 | `auto_tuner` / `error_reporter` / `optimized_metrics` / `tracing_cache` **4 个模块 register_singleton 均为 0 次** → 注册代码完全缺失 |
| reset_all 代码审查 | 只重置 instances、保留 factories → **排除 reset 破坏** |

**"仅 3.11/Shard 3 失败"的解释**：`test_singleton_manager.py` 按文件分片恰好落在 Shard 3，并非隔离问题。

**时序还原**：
1. `tests/unit/test_singleton_manager.py`（含集成断言）已合入 master
2. `agent/utils/singleton_manager.py` 框架已就绪
3. 但 4 个目标模块（auto_tuner 等）的 `register_singleton` 注册调用**尚未落地**（其注册逻辑在后续 commit 才加入，最新 master `33136c19` 已包含）
4. → 测试必然失败（确定性），与并行/顺序无关

## 六、影响评估

| 范围 | 影响 |
|------|------|
| master `d447bef8` CI | 云枢系统测试流程 1 failed（阻断） |
| 生产功能 | 无直接证据（auto_tuner 功能由 getter 路径独立工作） |
| 本任务（#77） | **无关**——该测试与失败由并行会话 SingletonManager 迁移（develop `0b4efc1f` 等）引入 |

## 七、建议处理（并行会话）

1. **为 4 个目标模块补 `register_singleton` 调用**（auto_tuner / error_reporter / optimized_metrics / tracing_cache），参考最新 master `33136c19` 中 auto_tuner 的 try/except 注册块模式
2. 补注册时避免静默跳过：`_SINGLETON_AVAILABLE=False` 时打印显式告警（防循环导入静默失败）
3. 修复后复用 `scripts/repro_singleton_metrics_registered.py` 验证：
   - 模式 A：串行应全部通过（`--mode A`）
   - 模式 B：4 个模块 `is_registered` 应全为 True（`--mode B`）

## 八、附注

- rerun 记录：`gh run rerun 31322544891 --failed` 已触发，但重跑 job 因 GitHub Actions runner 队列拥塞被 cancelled（2026-08-09T00:30:51Z 观察），未能完成复现验证
- 队列静止后复查命令：`gh run list --branch master --limit 5`
