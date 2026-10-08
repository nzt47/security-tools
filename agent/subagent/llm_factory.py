"""分身 LLM 解析 —— 让 ``SubagentConfig.model_id`` **真的生效**

【为什么需要这个模块】
    `SubagentConfig` 从第一天就有 ``model_id`` 字段，创建/热更新端点也一直收它，
    但**全仓没有任何消费者**：它只进容器状态 dict、`execute()` 的占位文案与热更新
    变更日志；真跑委派时一律用母体的 ``Yunshu._llm``（`routes_subagent.py`、
    `agent/tools/subagent_tools.py`、`agent/tools/fan_out_tools.py` 三处都这么写）。
    于是"给这个分身配一个便宜/更快/更强的模型"在界面上是个**能填但不生效**的承诺 ——
    这正是本仓最忌讳的那类"字段在、消费者不在"。

    本模块把那条线接上，并且**只接一处**：

        resolution = resolve_subagent_llm(model_id, parent_llm=母体 LLM)

【唯一构造入口（本模块最关键的纪律）】
    模型实例**只从母体现有 LLM 派生**：``LLMService.with_model(model)``
    （其内部就是既有降级链用的 ``_shadow_service``：复用同一 provider / api_key /
    base_url 与已建客户端，只改模型名）。本模块**不**自己 new ``LLMService``、
    不读 ``.env``、不碰 api_key —— 否则就会出现"第二份 provider/密钥从哪来"的口径。

【安全边界（分身配置**不能**覆盖的三项）】
    ``provider`` / ``api_key`` / ``base_url`` **恒取部署配置**；分身只能选**同一 provider
    下的模型名**。跨 provider 需要 per-分身 凭据，属于后续阶段（复用
    `agent/subagent/credentials.py` 的 TTL 凭据），本轮明确不做、也不假装支持。

【失败语义：不抛，但**绝不静默换模型**】
    ``source`` 三档如实回给调用方（并随委派响应上屏）：
      · ``inherit``              未指定模型（空串 / inherit / parent / 跟随母体），
                                 或**指定的模型正是母体当前模型** ⇒ 直接用母体实例；
      · ``explicit``             指定且成功派生独立实例（影子实例）；
      · ``fallback-after-error`` 指定但派生失败（无母体 LLM / 母体 LLM 不支持换模型 /
                                 派生抛异常）⇒ **回退母体实例并带回 error 原文**。
    "回退仍然跑"是刻意的：一个坏模型名不该让整次委派失败；但**换了模型必须看得见**
    （响应里的 ``llm`` 段、列表页的来源徽章、委派历史），所以 error 一定非空。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

#: 视为"未指定模型"的取值（大小写不敏感；含中文同义写法，界面直接写"跟随母体"也能被识别）
_INHERIT_SENTINELS: Tuple[str, ...] = ("", "inherit", "parent", "follow", "跟随母体", "-")


#: 分身模块里"模型名取不到"时的**展示占位**（`agent/tools/subagent_tools.py::_model_id`
#: 的兜底返回值，用于状态文案）。它是标签、不是模型名 —— 本模块**不**把它当成生效模型，
#: 否则界面会声称跑着一个并不存在的模型。
_DISPLAY_PLACEHOLDER = "subagent-delegate"


def model_name(llm: Any) -> str:
    """取 LLM 实例的模型名；**取不到返回空串（不猜、不用展示占位）**

    【与 `subagent_tools._model_id` 的关系】候选属性顺序（``model`` → ``model_id`` →
    ``model_name``）与之**一致**（同一个事实只该有一种读法）；区别只有一处，且是刻意的：
    `_model_id` 取不到时返回展示占位 ``subagent-delegate``（给状态文案用），
    而本函数返回空串 —— 本函数的返回值会进"实际生效的模型"这一栏，
    填占位就等于**声称跑着一个并不存在的模型**（实测该占位就是那串字）。
    回归用例：`tests/unit/test_subagent_llm_factory.py::test_model_name_取不到时不猜`。
    """
    if llm is None:
        return ""
    for attr in ("model", "model_id", "model_name"):
        value = getattr(llm, attr, None)
        if isinstance(value, str) and value.strip() and value.strip() != _DISPLAY_PLACEHOLDER:
            return value.strip()
    return ""


def _is_inherit(model_id: Any) -> bool:
    return str(model_id or "").strip().lower() in _INHERIT_SENTINELS


@dataclass(frozen=True)
class LlmResolution:
    """一次"分身 → LLM"的解析结果（只读；**不含任何密钥**）"""

    llm: Any = None
    """实际要用的 LLM 实例（``inherit`` / ``fallback-after-error`` 时就是母体实例）。"""

    requested: str = ""
    """分身配置里点的模型名（原样回显，便于界面显示"你点的是哪个"）。"""

    model: str = ""
    """**实际生效**的模型名（取自实例本身，不取配置 —— 配置可能没生效）。"""

    source: str = "inherit"
    """``inherit`` | ``explicit`` | ``fallback-after-error``（见模块 docstring）。"""

    error: str = ""
    """``fallback-after-error`` 时的原因原文；其余档为空串。"""

    def to_dict(self) -> Dict[str, Any]:
        """投影给 HTTP/UI 的字段（**绝不含 llm 对象与任何密钥**）"""
        return {
            "requested": self.requested,
            "model": self.model,
            "source": self.source,
            "error": self.error,
        }


def resolve_subagent_llm(model_id: Any = "", parent_llm: Any = None) -> LlmResolution:
    """按分身的 ``model_id`` 解析出它该用的 LLM（不抛；三档来源见模块 docstring）

    Args:
        model_id: 分身配置里的模型名；空/``inherit``/``跟随母体`` ⇒ 跟随母体。
        parent_llm: 母体当前 LLM（缺省 None ⇒ 无法派生，回 ``fallback-after-error``）。

    Returns:
        `LlmResolution`。
    """
    requested = str(model_id or "").strip()
    parent_model = model_name(parent_llm)

    if _is_inherit(requested):
        return LlmResolution(llm=parent_llm, requested=requested, model=parent_model,
                             source="inherit")

    if parent_llm is None:
        return LlmResolution(
            llm=None, requested=requested, model="", source="fallback-after-error",
            error="无母体 LLM 可派生（配置未就绪）：已回退，本次委派不会用指定模型")

    # 指定的就是母体当前模型 ⇒ 复用母体实例（不为"相同模型"造一个影子，免得来源显示成 explicit）
    if requested == parent_model:
        return LlmResolution(llm=parent_llm, requested=requested, model=parent_model,
                             source="inherit")

    with_model = getattr(parent_llm, "with_model", None)
    if not callable(with_model):
        return LlmResolution(
            llm=parent_llm, requested=requested, model=parent_model,
            source="fallback-after-error",
            error=("母体 LLM 不支持按模型派生（缺 with_model，见 memory/llm_service.py）："
                   f"已回退到母体模型 {parent_model or '<未知>'}"))

    try:
        child = with_model(requested)
    except Exception as e:  # noqa: BLE001 派生失败不得让委派挂掉（但必须被看见）
        return LlmResolution(
            llm=parent_llm, requested=requested, model=parent_model,
            source="fallback-after-error",
            error=(f"派生模型 {requested} 失败（{type(e).__name__}: {e}）："
                   f"已回退到母体模型 {parent_model or '<未知>'}"))

    return LlmResolution(llm=child, requested=requested, model=model_name(child) or requested,
                         source="explicit")


def llm_options(deployment_model: str,
                declared_models: Optional[Any] = None) -> Dict[str, Any]:
    """可选模型清单（**不编造模型目录**：只列"有出处"的名字）

    【为什么不做一份模型目录】模型名是**提供商的事实**，写死在仓里就是"硬编码模型名"
    （本仓 P0 自检明令禁止），而且必然过时。这里只列两类**有出处**的候选：
      · ``deployment`` —— 部署配置里母体在用的模型（``.env`` / ``config.yaml``）；
      · ``declared``   —— 现存分身已经声明过的模型名（去重保序）。
    其余由使用者按自己 provider 实际支持的模型名手填（界面是 datalist，不挡手填）。

    Returns:
        ``{"model": 部署模型, "options": [{"model": 名字, "source": "deployment"|"declared"}]}``
    """
    model = str(deployment_model or "").strip()
    options: list = []
    seen: set = set()
    if model:
        options.append({"model": model, "source": "deployment"})
        seen.add(model)
    for name in (declared_models or ()):
        candidate = str(name or "").strip()
        if not candidate or candidate.lower() in _INHERIT_SENTINELS or candidate in seen:
            continue
        options.append({"model": candidate, "source": "declared"})
        seen.add(candidate)
    return {"model": model, "options": options}


__all__ = ["LlmResolution", "llm_options", "model_name", "resolve_subagent_llm"]
