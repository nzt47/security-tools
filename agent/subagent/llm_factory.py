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

    temperature: Optional[float] = None
    """实际生效的生成温度；``None`` = **未干预**（执行器默认），不是 0.0。"""

    def to_dict(self) -> Dict[str, Any]:
        """投影给 HTTP/UI 的字段（**绝不含 llm 对象与任何密钥**）"""
        return {
            "requested": self.requested,
            "model": self.model,
            "source": self.source,
            "error": self.error,
            "temperature": self.temperature,
        }


class TemperaturePinnedLLM:
    """把生成温度钉在某个值的 LLM 包装（**只包 chat / chat_stream 一层**）

    【为什么用包装而不是改 LLMService】温度在本仓是 ``chat(..., temperature=...)`` 的
    **逐次调用参数**（`memory/llm_service.py`），执行器调用时用的是缺省值。要让"某个分身
    固定用 0.2"生效，只能在**调用面**注入 —— 不能去改 ``LLMService`` 的默认值：那是
    provider 级全局项，改它等于把母体与所有其它分身的温度一起改了。

    【只碰 temperature】provider / api_key / base_url 仍来自被包装的实例（部署级权威）；
    本类不读配置、不建客户端、不碰密钥。
    【__getattr__ 透传】调用方（执行器、日志、`model_name()`）会读 ``model`` / ``provider``
    等属性；不透明转发会把"实际模型名"读丢（那正是本轮要如实显示的东西）。
    """

    def __init__(self, inner: Any, temperature: float):
        self._inner = inner
        self._temperature = float(temperature)

    @property
    def temperature(self) -> float:
        return self._temperature

    def chat(self, messages: Any, system_prompt: str = "",
             max_tokens: int = 1024, temperature: float = 0.7) -> Any:
        return self._inner.chat(messages, system_prompt=system_prompt,
                                max_tokens=max_tokens, temperature=self._temperature)

    def chat_stream(self, messages: Any, system_prompt: str = "",
                    max_tokens: int = 1024, temperature: float = 0.7) -> Any:
        """流式对话：同样只把温度钉住

        【为什么**不**收 `**kw` 转发】本仓的关键字参数冲突扫描（`scripts/scan_kwarg_conflicts.py`，
        pre-commit HIGH 阻断）把"显式 kwargs + `**dict` 转发"判为 HIGH：一旦上游字典里出现
        同名键，就会 `TypeError: got multiple values for keyword argument`。
        本包装只需要钉温度一个参数，故**只声明已知形参、不转发未知 kwargs** —— 少一个转发点，
        就少一处"上游加参数时这里悄悄炸"的地方（实测该模式被扫描器拦下）。
        """
        return self._inner.chat_stream(messages, system_prompt=system_prompt,
                                       max_tokens=max_tokens,
                                       temperature=self._temperature)

    def __getattr__(self, name: str) -> Any:
        # 只在实例属性找不到时走到这里（_inner / _temperature 是实例属性，不会递归）
        return getattr(self._inner, name)


def _pin_temperature(llm: Any, temperature: Optional[float]) -> Any:
    """温度未表态（None）⇒ 原样返回（**身份不变**，便于"没表态"与"表态了"区分）；否则包一层"""
    if temperature is None:
        return llm
    return TemperaturePinnedLLM(llm, temperature)


def resolve_subagent_llm(model_id: Any = "", parent_llm: Any = None,
                        temperature: Optional[float] = None) -> LlmResolution:
    """按分身的 ``model_id`` / ``llm_temperature`` 解析出它该用的 LLM（不抛；三档来源见模块 docstring）

    Args:
        model_id: 分身配置里的模型名；空/``inherit``/``跟随母体`` ⇒ 跟随母体。
        parent_llm: 母体当前 LLM（缺省 None ⇒ 无法派生，回 ``fallback-after-error``）。
        temperature: 分身的生成温度；``None`` = 不干预（执行器默认）。
            给了值 ⇒ 返回的实例是 `TemperaturePinnedLLM` 包装（**继承档也包**：
            温度是逐次调用参数，与"用哪个模型实例"是两件事）。

    Returns:
        `LlmResolution`（``temperature`` 为实际生效值；``None`` 表示未干预）。
    """
    requested = str(model_id or "").strip()
    parent_model = model_name(parent_llm)

    if _is_inherit(requested):
        return LlmResolution(llm=_pin_temperature(parent_llm, temperature), requested=requested,
                             model=parent_model, source="inherit", temperature=temperature)

    if parent_llm is None:
        return LlmResolution(
            llm=None, requested=requested, model="", source="fallback-after-error",
            error="无母体 LLM 可派生（配置未就绪）：已回退，本次委派不会用指定模型",
            temperature=None)

    # 指定的就是母体当前模型 ⇒ 复用母体实例（不为"相同模型"造一个影子，免得来源显示成 explicit）
    if requested == parent_model:
        return LlmResolution(llm=_pin_temperature(parent_llm, temperature), requested=requested,
                             model=parent_model, source="inherit", temperature=temperature)

    with_model = getattr(parent_llm, "with_model", None)
    if not callable(with_model):
        return LlmResolution(
            llm=_pin_temperature(parent_llm, temperature), requested=requested, model=parent_model,
            source="fallback-after-error", temperature=temperature,
            error=("母体 LLM 不支持按模型派生（缺 with_model，见 memory/llm_service.py）："
                   f"已回退到母体模型 {parent_model or '<未知>'}"))

    try:
        child = with_model(requested)
    except Exception as e:  # noqa: BLE001 派生失败不得让委派挂掉（但必须被看见）
        return LlmResolution(
            llm=_pin_temperature(parent_llm, temperature), requested=requested, model=parent_model,
            source="fallback-after-error", temperature=temperature,
            error=(f"派生模型 {requested} 失败（{type(e).__name__}: {e}）："
                   f"已回退到母体模型 {parent_model or '<未知>'}"))

    return LlmResolution(llm=_pin_temperature(child, temperature), requested=requested,
                         model=model_name(child) or requested, source="explicit",
                         temperature=temperature)


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


__all__ = ["LlmResolution", "TemperaturePinnedLLM", "llm_options", "model_name",
           "resolve_subagent_llm"]
