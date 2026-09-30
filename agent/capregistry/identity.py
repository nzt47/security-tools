"""调用身份轴（call_id / run_id）—— 编排层预分配、下游原样带回（INV-04 / V2.0 §3.4 §3.11）

## 为什么需要这个模块（实测病灶）

本次审计实测（详见审计报告 H8 / §4.2 #10）：

- `agent/` 全域 `call_id` = **0 命中**（仅 LLM 提供方给的 `tool_call_id`）；
- `run_id` / `parent_run_id` 在 `observability` / `orchestrator` / `subagent` 全域 **0 命中**；
- 调用身份由 **20+ 处**在**执行体内部**现场自造 12 位 id
  （`agent/tools/__init__.py:494`、`permission_system.py:644`、`llm_monitor.py:158/237`、
  `task_planner/enhanced_planner.py:214/405/552`、`scheduling.py:249`、`async_executor.py:84` …）；
- 仓库内实测文档自述：「三套互不相通的 id 体系，工具层自造 12 位 id」
  （`docs/perf/可观测性实测.md`）。

后果（对齐 V2.0 INV-04「call_id 是幂等、归位、去重的唯一根基」）：

1. 同一次调用在**审计链 / 统一 Trace / 成本台账**里没有共同主键 ⇒ **无法归位**；
2. 重试产生新 id ⇒ **幂等键无法成立**（INV-09「写操作重试必须带幂等键」落不了地）；
3. 跨层无法回答「这一次调用到底是谁发起的」。

本模块提供**唯一的** call_id / run_id 生成与传播入口：**编排层 alloc 一次，全链路只读**。

## 不易（三条不变量，改动时不得违反）

1. **不新建配置项（零 env 开关）**。派生规则唯一，没有合法的第二种取值；因此也**不触碰**
   `agent/settings/registry.py` 的零缺口守卫（`tests/unit/test_settings_registry.py`）。
2. **不替换既有 `trace_id`**。两者**并存**：`call_id` 是**调用**粒度，`trace_id` 是**轨迹**粒度。
   本模块只补前者，绝不改后者语义与既有调用点（D2 不破坏）。
3. **取不到父身份时显式标注 `derived=True`**，不静默假装是同一次调用（INV-08：
   永远保留降级标注）。ContextVar 在子线程 / 子进程不会自动继承，这正是需要标注的场景。

## 变易（改哪里）

- id 前缀 / 位数：改 `_CALL_PREFIX` / `_HEX_LEN`（两者都被 `_ID_RE` 约束，改一处要同步）。
- 要新增身份维度（如 `tenant_id`）：默认**不要加** —— 租户在云枢是 workspace-hash 派生的
  单值维度（`agent/security/tenant.py`），加进来只会制造第二真相源。

## 简易（为什么这么小）

纯标准库 + `contextvars`，无 IO、无网络、无单例状态；`call_scope()` 用 try/finally 恢复
外层值，支持嵌套与并发线程（每个线程各自一份 ContextVar 副本）。

## id 格式

`call_<32hex>`：全局唯一、**不含时间戳**（避免时钟依赖，也避免从 id 反推调用时刻）。
`run_<32hex>` 同构。
"""

from __future__ import annotations

import re
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Dict, Iterator, Optional, Tuple

__all__ = [
    "CALL_ID_PREFIX",
    "RUN_ID_PREFIX",
    "new_call_id",
    "new_run_id",
    "is_valid_call_id",
    "current_call_id",
    "current_run_id",
    "ensure_call_id",
    "ensure_run_id",
    "call_scope",
    "run_scope",
    "call_context_snapshot",
    "reset_context",
]

CALL_ID_PREFIX = "call_"
RUN_ID_PREFIX = "run_"
_HEX_LEN = 32

_ID_RE = re.compile(r"^(?:call|run)_[0-9a-f]{%d}$" % _HEX_LEN)

#: 当前 call_id（唯一权威）。默认空串 = "尚未由编排层预分配"。
_call_id_var: ContextVar[str] = ContextVar("yunshu_call_id", default="")
#: 当前 run_id。默认空串。
_run_id_var: ContextVar[str] = ContextVar("yunshu_run_id", default="")


def new_call_id() -> str:
    """生成一个新的 call_id（`call_<32hex>`）。

    **只应由编排层在发出调用前调用一次**（INV-04）；执行体不得自建。
    """
    return CALL_ID_PREFIX + uuid.uuid4().hex


def new_run_id() -> str:
    """生成一个新的 run_id（`run_<32hex>`）。"""
    return RUN_ID_PREFIX + uuid.uuid4().hex


def is_valid_call_id(value: Any) -> bool:
    """校验是否为合法 call_id / run_id（供契约测试与审计链落账前校验）。"""
    return isinstance(value, str) and bool(_ID_RE.match(value))


def current_call_id() -> str:
    """取当前上下文的 call_id；未预分配时返回空串（**不生成**）。

    返回空串是**有意的**：调用方据此区分「编排层已预分配」与「无上下文」，
    而不是拿到一个看起来合法、实则自造的 id（那正是本次要消灭的形态）。
    """
    return _call_id_var.get()


def current_run_id() -> str:
    """取当前上下文的 run_id；未设置时返回空串。"""
    return _run_id_var.get()


def ensure_call_id() -> Tuple[str, bool]:
    """取 call_id；取不到则**生成并标注**。

    Returns:
        `(call_id, derived)`。`derived=True` 表示本次调用**没有**编排层预分配的 id，
        当前 id 是就地派生的降级结果，调用方应把它写进日志/审计以便事后归因（INV-08）。
    """
    existing = _call_id_var.get()
    if existing:
        return existing, False
    fresh = new_call_id()
    return fresh, True


def ensure_run_id() -> Tuple[str, bool]:
    """取 run_id；取不到则生成并标注（语义同 :func:`ensure_call_id`）。"""
    existing = _run_id_var.get()
    if existing:
        return existing, False
    return new_run_id(), True


@contextmanager
def call_scope(call_id: Optional[str] = None) -> Iterator[str]:
    """在作用域内绑定一个 call_id；退出时**恢复**外层值（支持嵌套）。

    Args:
        call_id: 编排层预分配的 id。None ⇒ 就地派生（降级路径，调用方应记录该事实）。

    Yields:
        生效的 call_id。

    ```python
    with call_scope() as cid:          # 编排层预分配
        tools.call("read_file", path="x")   # 执行体读 current_call_id()，不自建
    ```
    """
    token = _call_id_var.set(call_id or new_call_id())
    try:
        yield _call_id_var.get()
    finally:
        _call_id_var.reset(token)


@contextmanager
def run_scope(run_id: Optional[str] = None) -> Iterator[str]:
    """在作用域内绑定一个 run_id；退出时恢复外层值（支持嵌套）。"""
    token = _run_id_var.set(run_id or new_run_id())
    try:
        yield _run_id_var.get()
    finally:
        _run_id_var.reset(token)


def call_context_snapshot() -> Dict[str, str]:
    """当前身份的只读快照，供审计 / Trace / 成本台账统一落账。

    键名与 V2.0 §3.4 `CallContext` 对齐（只取本模块负责的两个字段）。
    未预分配的字段返回空串，**不填占位符**——占位符会被下游误当成真值。
    """
    return {
        "call_id": _call_id_var.get(),
        "run_id": _run_id_var.get(),
    }


def reset_context() -> None:
    """清空当前上下文的身份（**仅供测试夹具使用**，勿在生产路径调用）。"""
    _call_id_var.set("")
    _run_id_var.set("")
