# -*- coding: utf-8 -*-
"""「这一步算不算绑定完成」的唯一口径（叶模块，不 import 任何同层模块）。

【为什么单独一个文件】同一条判据有**四处**在用：
  · `autobind.apply_bindings` 数 bound/total；
  · `autobind.build_learned_workflow` 决定能不能登记；
  · `drafts.list_drafts` 给界面报 bound_steps/total_steps；
  · `drafts.save_workflow` 写回后重算 requires_binding。
2026-11-08 之前它们各写一份 `if tool_name`，于是「判断/评审/写作」这类
**没有工具、由模型自己完成**的步骤（need_llm=True）被永久判成未绑定 ——
code-review 草稿 19 步只能绑 10 步，登记被硬拒（Operator 实测：AI 绑定"总是做不起来"）。

【口径】一步算"已落地"有两种合法方式，二者**等价**：
  ① 绑到了真实工具（tool_name 非空，且要通过工具目录校验 —— 那一步由调用方查）；
  ② 标了 `need_llm=True`：由 executor 的步骤级 llm_step_runner 执行
     （`WorkflowStep.tool_name` 的字段说明本来就写着"need_llm=True 时可为空"）。
既没工具、也没标 need_llm ⇒ 才是真的未绑定。
"""
from __future__ import annotations

from typing import Any, Dict

#: 模型/前端给的"真"值写法（JSON 一多，`"true"` 和 True 都会出现）
TRUE_TEXTS = ("1", "true", "yes", "y", "on")


def as_bool(value: Any) -> bool:
    """把 need_llm 这类标记解析成布尔（缺省 False，不猜）。"""
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in TRUE_TEXTS


def step_is_bound(step: Dict[str, Any]) -> bool:
    """这一步是否已经可以执行（有工具，或标了 need_llm）。"""
    if not isinstance(step, dict):
        return False
    if str(step.get("tool_name") or "").strip():
        return True
    return as_bool(step.get("need_llm"))
