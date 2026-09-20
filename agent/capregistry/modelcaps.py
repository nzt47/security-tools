"""模型 tool-calling 能力探测（v1.4 §7 职责 3：`/capabilities/tools?model=` 的裁剪依据）

## 现状（TASK-05 预检）

全仓**没有**任何"模型 → 是否支持 tool calling"的声明表（`git grep` 实证：
只有分散的 `tool_calling` 字样，没有能力矩阵）。因此本模块新建一张**显式、
可覆写、保守默认**的表。

## 三条纪律

1. **静态表 + 可选数据覆写**：默认在代码里（可 review、有测试）；
   若存在 `data/model_tool_calling.yaml` 则以它为准（数据侧可改，不必改代码）。
2. **未知模型按"支持"处理**（不裁剪）。理由与 `agent/tools/__init__.py`
   的 `_internal_tool_names` 同一条纪律：**宁可多暴露，不可因判定失败把能力
   静默藏掉** —— 静默隐藏是"能力不见了但没人知道为什么"，比多给几个工具更难查。
3. **显式否定优先**：`none/off/-/no-tools` 这类哨兵值一律判"不支持"，
   让测试与 CI 有一条**确定性**的裁剪路径（不依赖某个具体模型名）。
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

__all__ = [
    "supports_tool_calling",
    "model_capability",
    "model_capability_table",
]

#: 显式"没有 tool calling"的哨兵取值（大小写不敏感）
_NO_TOOL_SENTINELS = frozenset({"none", "off", "-", "no-tools", "no_tools", "text-only"})

#: 已确认**支持** tool calling 的模型名前缀（保守表：只列仓库实际路由到过的族）
_TOOL_CALLING_PREFIXES: Tuple[str, ...] = (
    "gpt-4", "gpt-4o", "gpt-4.1", "gpt-3.5-turbo", "o1", "o3", "o4",
    "claude-3", "claude-4", "claude-sonnet", "claude-opus", "claude-haiku",
    "deepseek-chat", "deepseek-v3", "deepseek-v4", "deepseek-reasoner",
    "qwen", "glm-4", "moonshot", "kimi", "gemini-1.5", "gemini-2",
    "yi-", "minimax", "doubao", "hunyuan", "ernie",
)

#: 已确认**不支持** tool calling 的模型名前缀（纯文本/补全类）
_NO_TOOL_CALLING_PREFIXES: Tuple[str, ...] = (
    "text-davinci", "gpt-3", "babbage", "davinci", "curie", "ada",
    "whisper", "tts-1", "dall-e", "text-embedding", "embedding",
)

#: 可选数据覆写文件（相对仓库根；只读）
_OVERRIDE_REL = os.path.join("data", "model_tool_calling.yaml")

_TABLE_CACHE: Dict[str, Any] = {"loaded": False, "table": None}


def _repo_root() -> str:
    # agent/capregistry/modelcaps.py → 仓库根
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load_override() -> Optional[Dict[str, Any]]:
    """读可选的 `data/model_tool_calling.yaml`（缺失/损坏一律返回 None，不抛）"""
    path = os.path.join(_repo_root(), _OVERRIDE_REL)
    if not os.path.isfile(path):
        return None
    try:
        import yaml  # noqa: PLC0415 惰性：只在有覆写文件时才需要
        with open(path, "r", encoding="utf-8") as f:
            doc = yaml.safe_load(f)
        return doc if isinstance(doc, dict) else None
    except Exception:  # noqa: BLE001  覆写文件坏了 ⇒ 退回静态表（不阻断查询）
        return None


def model_capability_table() -> Dict[str, Any]:
    """当前生效的能力表（静态表 + 可选覆写；进程内缓存）"""
    if _TABLE_CACHE["loaded"]:
        return _TABLE_CACHE["table"] or {}
    override = _load_override() or {}
    table = {
        "supports": list(override.get("supports") or _TOOL_CALLING_PREFIXES),
        "denies": list(override.get("denies") or _NO_TOOL_CALLING_PREFIXES),
        "source": "data/model_tool_calling.yaml" if override else "builtin",
    }
    _TABLE_CACHE["table"] = table
    _TABLE_CACHE["loaded"] = True
    return table


def supports_tool_calling(model: str) -> Tuple[bool, str]:
    """该模型是否支持 tool calling

    Returns:
        `(supported, reason)`；`reason` 是**给人看**的判定依据，会原样出现在
        `/capabilities/tools` 的 `model_capability` 字段里 —— 让调用方能区分
        "这个模型确实不支持"与"我们只是不认识它，按支持处理"。
    """
    raw = str(model or "").strip()
    if not raw:
        return True, "未指定 model：不裁剪（返回全量）"
    low = raw.lower()
    if low in _NO_TOOL_SENTINELS:
        return False, f"model={raw!r} 是显式哨兵值：声明为不支持 tool calling"
    table = model_capability_table()
    for prefix in table.get("denies") or ():
        if low.startswith(str(prefix).lower()):
            return False, f"model={raw!r} 命中不支持前缀 {prefix!r}"
    for prefix in table.get("supports") or ():
        if low.startswith(str(prefix).lower()):
            return True, f"model={raw!r} 命中支持前缀 {prefix!r}"
    return True, (f"model={raw!r} 未知：按支持处理（宁可多暴露，"
                  f"不可因判定失败静默藏能力）")


def model_capability(model: str) -> Dict[str, Any]:
    """`/capabilities/tools` 用的结构化判定结果"""
    supported, reason = supports_tool_calling(model)
    return {
        "model": str(model or ""),
        "supports_tool_calling": bool(supported),
        "reason": reason,
        "table_source": model_capability_table().get("source", "builtin"),
    }


def invalidate_cache() -> None:
    """清空能力表缓存（测试与数据侧热更新用）"""
    _TABLE_CACHE["loaded"] = False
    _TABLE_CACHE["table"] = None


def _prefixes_for_tests() -> Tuple[List[str], List[str]]:  # pragma: no cover - 诊断用
    t = model_capability_table()
    return list(t.get("supports") or []), list(t.get("denies") or [])
