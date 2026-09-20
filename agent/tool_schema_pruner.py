"""工具 Schema 动态裁剪器

在 tool_router 选定工具之后、传给 LLM 之前,对 OpenAI 格式 tool_def 做保守裁剪,
降低 tools 参数 token 占用。

【不易】
  - required 字段必保留(即便该属性 deprecated:true,LLM 缺参会调用失败)
  - 深拷贝,绝不修改传入的原始 tool_def
  - 工具级 deprecated(function.deprecated=true)→ 整工具跳过(返回 None)
  - 任何异常降级返回原 tool_def,不阻塞主路径
【变易】
  - 裁剪规则 4 个旋钮可配(.env):
      SCHEMA_DESC_MAX_LEN / SCHEMA_PROP_DESC_MAX_LEN /
      SCHEMA_PRUNE_ADDITIONAL_PROPS / SCHEMA_PRUNE_DEPRECATED
  - intent_context 为预留扩展点(当前仅 selected_tools,未来可加 user_intent 做精细裁剪)
【简易】
  - 纯函数,无状态,无副作用
  - 递归处理 properties / items,单入口 prune_schema

裁剪规则(保守,不做激进字段移除):
  1. 移除非 required 的 deprecated:true 属性(含嵌套 items.properties)
  2. 截断 function.description 超过 SCHEMA_DESC_MAX_LEN
  3. 截断属性 description 超过 SCHEMA_PROP_DESC_MAX_LEN
  4. 移除冗余 additionalProperties:true(JSON Schema 默认即 true,显式写出冗余)
  5. 保留 enum/format/minItems/maxItems 等所有约束

═══════════════════════════════════════════════════════════════
 TASK-08 E8：**裁剪误伤率 = 0**（v1.4 §7）
═══════════════════════════════════════════════════════════════

本模块的裁剪分两层,**只有第二层会"裁掉整个工具"**:

  · **字段层**（规则 1–5）：只截断描述 / 移除废弃的单字段。
    它**不使工具消失**,且守 `[不易] required 字段必保留` ⇒ 
    **字段层不设保护**（给 description 截断加保护会让保护失去意义:
    受保护工具的描述照样会挤占 token 预算,而模型仍能调它）。
  · **工具层**（整条 tool_def 被移除）：
      ① 声明为 `deprecated: true` ⇒ 移除 —— 这是**人工的显式淘汰声明**,
         不是"裁剪误伤"(声明为废弃就等于声明"别再用它"),故**不设保护**;
      ② **token 预算裁剪**（`budget_tokens` / `CP_TOOLSET_SCHEMA_TOKEN_BUDGET`）
         ⇒ **受保护工具一律不裁**（v1.4 §7）。

**保护判据只有一处实现**：`agent/capregistry/pruning.py`（`risk >= high`
或 `confirm_level >= L2` ⇒ 不参与裁剪）。本模块只做接线,不复制判据 ——
同一判据的另一消费点是 `agent/lines/assembler.py` 的名额截断。

【降级方向】取不到判据模块（import 失败）时**不裁剪**（而不是"无保护地裁"）:
宁可少裁,不可误裁一个高危工具。这与 E8 的指标方向一致。
"""
from __future__ import annotations

import os
import copy
import logging
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════
#  配置(.env 读取,模块加载时一次性,避免每次裁剪都查 env)
# ════════════════════════════════════════════════════════════
def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, str(default)))
    except Exception:
        return default


def _env_bool(key: str, default: bool) -> bool:
    return os.environ.get(key, "1" if default else "0").strip().lower() in (
        "1", "true", "yes", "on",
    )


SCHEMA_DESC_MAX_LEN = _env_int("SCHEMA_DESC_MAX_LEN", 200)
SCHEMA_PROP_DESC_MAX_LEN = _env_int("SCHEMA_PROP_DESC_MAX_LEN", 120)
SCHEMA_PRUNE_ADDITIONAL_PROPS = _env_bool("SCHEMA_PRUNE_ADDITIONAL_PROPS", True)
SCHEMA_PRUNE_DEPRECATED = _env_bool("SCHEMA_PRUNE_DEPRECATED", True)


# ════════════════════════════════════════════════════════════
#  裁剪保护（TASK-08 E8）—— 判据在 capregistry.pruning，这里只接线
# ════════════════════════════════════════════════════════════

def _pruning_module():
    """惰性取裁剪保护模块；不可用 ⇒ `None`（调用方据此**不裁剪**，见模块 docstring）"""
    try:
        from agent.capregistry import pruning as _p  # noqa: PLC0415  惰性：避免 import 环
        return _p
    except Exception as e:  # noqa: BLE001  保护不可得 ⇒ 不裁剪（安全方向）
        logger.debug("[SchemaPruner] 裁剪保护模块不可用,跳过工具级预算裁剪: %s", e)
        return None


# ════════════════════════════════════════════════════════════
#  核心裁剪逻辑
# ════════════════════════════════════════════════════════════

def _prune_node(node: Dict[str, Any], required_set: set) -> Dict[str, Any]:
    """递归裁剪 JSON Schema 节点(原地操作于深拷贝后的节点)

    Args:
        node: JSON Schema 对象(需已深拷贝)
        required_set: 当前节点的 required 字段集合
    """
    if not isinstance(node, dict):
        return node

    # 处理 properties
    props = node.get("properties")
    if isinstance(props, dict):
        for prop_name, prop_schema in list(props.items()):
            if not isinstance(prop_schema, dict):
                continue
            # 移除非 required 的 deprecated 属性(守 [不易]: required 即便 deprecated 也保留)
            if SCHEMA_PRUNE_DEPRECATED and prop_schema.get("deprecated") is True:
                if prop_name not in required_set:
                    del props[prop_name]
                    continue
            # 截断属性 description
            prop_desc = prop_schema.get("description")
            if isinstance(prop_desc, str) and len(prop_desc) > SCHEMA_PROP_DESC_MAX_LEN:
                prop_schema["description"] = prop_desc[:SCHEMA_PROP_DESC_MAX_LEN].rstrip() + "..."
            # 递归处理嵌套对象/数组
            _prune_node(prop_schema, set(prop_schema.get("required") or []))

    # 处理数组 items(递归)
    items = node.get("items")
    if isinstance(items, dict):
        _prune_node(items, set(items.get("required") or []))

    return node


def prune_schema(tool_def: Any, intent_context: Optional[dict] = None) -> Optional[dict]:
    """裁剪单个 OpenAI 格式 tool_def。

    Args:
        tool_def: {"type": "function", "function": {"name", "description", "parameters"}}
        intent_context: 意图上下文(预留,含 selected_tools;未来可做意图级精细裁剪)

    Returns:
        裁剪后的 tool_def;工具级 deprecated → None(整工具移除);异常降级返回原 tool_def
    """
    try:
        if not isinstance(tool_def, dict):
            return tool_def
        pruned = copy.deepcopy(tool_def)
        func = pruned.get("function")
        if not isinstance(func, dict):
            return pruned

        # 工具级 deprecated → 整工具移除(返回 None)
        if SCHEMA_PRUNE_DEPRECATED and func.get("deprecated") is True:
            return None

        # 截断 function.description
        desc = func.get("description")
        if isinstance(desc, str) and len(desc) > SCHEMA_DESC_MAX_LEN:
            func["description"] = desc[:SCHEMA_DESC_MAX_LEN].rstrip() + "..."

        # 递归裁剪 parameters
        params = func.get("parameters")
        if isinstance(params, dict):
            _prune_node(params, required_set=set(params.get("required") or []))
            if SCHEMA_PRUNE_ADDITIONAL_PROPS and params.get("additionalProperties") is True:
                params.pop("additionalProperties", None)

        return pruned
    except Exception as e:
        logger.debug("[SchemaPruner] 裁剪异常,降级返回原 tool_def: %s", e)
        return tool_def


def prune_tool_defs(tool_defs: List[dict], intent_context: Optional[dict] = None,
                    *, budget_tokens: Optional[int] = None,
                    prunable: Optional[Iterable[str]] = None) -> List[dict]:
    """批量裁剪工具定义列表。

    Args:
        tool_defs: OpenAI 格式 tool_def 列表
        intent_context: 意图上下文(透传给 prune_schema)
        budget_tokens: **工具级 token 预算**（TASK-08 E8）。`None` ⇒ 用模块开关
            `agent.capregistry.pruning.default_token_budget()`（默认 0 = 不裁剪，
            保持既有行为）。≤0 ⇒ 不裁剪。
        prunable: **显式声明可裁**的工具名（逃逸口；只有这里列出的工具才可能
            在高危判定下仍被裁）

    Returns:
        裁剪后的列表;整工具 deprecated 的已移除。
        若发生工具级预算裁剪,`intent_context["prune_plan"]` 里会带上完整账目
        （`kept` / `dropped` / `protected_kept` / `protected_dropped` /
        `misprune_rate`）—— **"裁剪确实发生了"必须可被外部看到**,否则
        "高危零误伤"是假绿（见 `agent/capregistry/pruning.py::BudgetPlan`）。
    """
    if not isinstance(tool_defs, list):
        return tool_defs
    pruned = []
    for td in tool_defs:
        result = prune_schema(td, intent_context)
        if result is not None:
            pruned.append(result)

    # ── 工具级 token 预算裁剪（E8：受保护工具不参与）──
    _p = _pruning_module()
    if _p is None:
        return pruned
    budget = budget_tokens
    if budget is None:
        budget = _p.default_token_budget()
    try:
        budget = int(budget or 0)
    except Exception:  # noqa: BLE001  非法预算 ⇒ 不裁剪（默认不误伤）
        budget = 0
    if budget <= 0:
        return pruned
    kept, plan = _p.prune_tool_defs_for_budget(
        pruned, budget_tokens=budget, prunable=prunable or (),
        protect=_p.protect_enabled())
    if isinstance(intent_context, dict):
        # 账目回填给调用方（trace / 测试断言用）；**不吞掉**任何字段
        intent_context["prune_plan"] = plan.to_dict()
    logger.info("[SchemaPruner] token 预算裁剪: %d → %d 个工具 "
                "(预算 %d, 受保护保留 %d, 误伤 %d)",
                len(pruned), len(kept), budget, len(plan.protected_kept),
                len(plan.protected_dropped))
    return kept
