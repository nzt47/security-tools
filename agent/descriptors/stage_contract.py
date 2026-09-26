"""S3-01 首次入轨的**叶子契约**（RUNBOOK-1 的架构修正）

【为什么存在这个模块】—— 一次真实的门禁回归
    `agent/descriptors/backfill.py::s3_01_followup()` 在 `auto=True` 时需要调用
    `agent.digestion.stage.backfill_stages`。原实现用「函数内懒加载」写法，本以为
    无碍，实测却把 CI 的 **架构规则校验** 从 0 违规顶到 **2 违规**（本批实测：
    master `rc=0` → 分支 `total_violations=2`）：

        descriptors.backfill → digestion.stage        ← 新增的这一条
        digestion.stage      → descriptors.bridge     （既有）
        descriptors.bridge   → skills_mgmt.store      （既有）
        skills_mgmt.store    → skills_mgmt.registry   （既有）
        skills_mgmt.registry → skills_mgmt.service    （既有）
        skills_mgmt.service  → descriptors.backfill   （既有）
        ⇒ 成环 ⇒ `no_circular_dependency`（high）

    **「挪进函数体」对本规则无效**：`dependency_graph._parse_imports` 用 `ast.walk`
    遍历整棵树含函数体，连 `importlib.import_module('x.y')` / `__import__('x.y')`
    这类**字面量**动态 import 也记边（`is_dynamic` 从不参与筛选）。见
    `agent/observability/arch_rules.py:126-137`（S11-09 的订正）。

【本模块做什么】
    `arch_rules` 给的真正有效路径是「依赖倒置 / 把共享符号下沉到**无依赖的叶子
    契约模块**，让底层模块依赖契约而非上层包」。本模块就是那个叶子：
      · **不 import 任何 agent 包**（纯 stdlib）⇒ 指向它的边永远不会成环；
      · `agent.digestion.stage`（上层）在自己的**导入期**把 `backfill_stages`
        注册进来；
      · `agent.descriptors.backfill`（叶子侧）只从本契约取用，不再反向 import
        `agent.digestion`。

    注册是**幂等**的（重复注册以最后一次为准，实现是同一个函数对象）。
    调用方也可以**显式注入**（`run_backfill(..., stage_runner=...)` /
    `s3_01_followup(..., stage_runner=...)`）—— 显式注入优先于注册表，
    这样 CLI / 测试不依赖导入顺序。
"""

from __future__ import annotations

from typing import Any, Callable, Optional

__all__ = ["register_stage_runner", "get_stage_runner", "reset_stage_runner"]

#: S3-01 首次入轨实现；签名与 `agent.digestion.stage.backfill_stages` 一致。
_STAGE_RUNNER: Optional[Callable[..., Any]] = None


def register_stage_runner(fn: Callable[..., Any]) -> None:
    """注册 S3-01 首次入轨实现（由 `agent.digestion.stage` 在其导入期调用）。"""
    global _STAGE_RUNNER
    _STAGE_RUNNER = fn


def get_stage_runner() -> Optional[Callable[..., Any]]:
    """取当前注册的实现；未注册返回 `None`（调用方须**如实报错**，不得静默跳过）。"""
    return _STAGE_RUNNER


def reset_stage_runner() -> None:
    """清空注册（仅供测试模拟"实现未接入"的场景）。"""
    global _STAGE_RUNNER
    _STAGE_RUNNER = None
