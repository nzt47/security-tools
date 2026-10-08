"""分身 LLM 解析守卫（agent/subagent/llm_factory.py）

背景：`SubagentConfig.model_id` 此前**没有任何消费者** —— 界面能填、端点能收，
真委派时一律用母体的 ``Yunshu._llm``。本模块把它接上，本文件锁死接线的四条不变量：

  1. **跟随母体**：空串 / ``inherit`` / ``parent`` / ``跟随母体`` / ``-`` ⇒ 直接用母体实例；
     **指定的模型恰好等于母体当前模型也走这一档**（不为"相同模型"造影子实例）。
  2. **唯一构造入口**：指定别的模型时，实例**只能**由 ``LLMService.with_model(model)`` 派生
     —— 用替身计数钉死；真件那条也单独验（同 provider / 同 api_key / 只换模型名、不改父实例）。
  3. **不抛、但不静默换模型**：无母体、母体不支持派生、派生抛异常 ⇒ 回退母体实例，
     ``source='fallback-after-error'`` 且 ``error`` 非空（调用方据此上屏）。
  4. **投影里没有密钥**：`LlmResolution.to_dict()` 的键集固定，且序列化结果不含任何密钥形态。

【不易】不 mock 被测逻辑：`LLMService.with_model` 用**真件**（构造只需一个假 api_key，
不建客户端、不发网络）；只有"母体 LLM"本身在需要时用替身。
"""

from __future__ import annotations

import json

import pytest

from agent.subagent.llm_factory import (
    LlmResolution,
    llm_options,
    model_name,
    resolve_subagent_llm,
)

#: 一个足够长、格式合法的假 api_key（真 LLMService 构造只做长度/空白校验，不发网络）
FAKE_KEY = "sk-test-0123456789abcdef"


def _real_llm(model: str):
    from memory.llm_service import LLMService

    return LLMService(provider="openai", api_key=FAKE_KEY, model=model,
                      base_url="https://example.invalid/v1")


class SpyLLM:
    """母体 LLM 替身：记录 with_model 调用，返回一个新的替身（模型名为入参）"""

    def __init__(self, model: str = "deepseek-flash"):
        self.model = model
        self.calls: list = []

    def with_model(self, model: str):
        self.calls.append(model)
        return SpyLLM(model)


class BoomLLM(SpyLLM):
    def with_model(self, model: str):
        self.calls.append(model)
        raise RuntimeError("模型目录不可用")


class NoWithModel:
    """只有 model 属性、没有 with_model 的母体（模拟旧/异构 LLM 适配器）"""

    def __init__(self, model: str = "legacy-model"):
        self.model = model


# ════════════════════════════════════════════════════════════
#  1. 跟随母体（含"指定的正是母体模型"）
# ════════════════════════════════════════════════════════════


class TestInherit:
    @pytest.mark.parametrize("value", ["", "   ", "inherit", "PARENT", "follow", "跟随母体", "-"])
    def test_未指定或哨兵值_跟随母体且不派生(self, value):
        parent = SpyLLM("deepseek-flash")
        res = resolve_subagent_llm(value, parent_llm=parent)
        assert res.llm is parent, "跟随母体必须返回同一个实例（不是等价副本）"
        assert res.source == "inherit"
        assert res.model == "deepseek-flash"
        assert res.error == ""
        assert parent.calls == [], "跟随母体不该触发 with_model"

    def test_指定的就是母体当前模型_也走跟随母体(self):
        """不为'相同模型'造影子实例：否则来源会显示成 explicit，误导成本/上下文判断"""
        parent = SpyLLM("deepseek-flash")
        res = resolve_subagent_llm("deepseek-flash", parent_llm=parent)
        assert res.llm is parent
        assert res.source == "inherit"
        assert res.requested == "deepseek-flash", "原样回显用户点的名字"
        assert parent.calls == []

    def test_无母体且未指定_返回空实例且来源仍是inherit(self):
        res = resolve_subagent_llm("", parent_llm=None)
        assert res.llm is None
        assert res.source == "inherit"
        assert res.model == ""


# ════════════════════════════════════════════════════════════
#  2. 指定模型 ⇒ 唯一构造入口 with_model
# ════════════════════════════════════════════════════════════


class TestExplicit:
    def test_指定模型_经with_model派生且只派生一次(self):
        parent = SpyLLM("deepseek-flash")
        res = resolve_subagent_llm("deepseek-v4-pro", parent_llm=parent)
        assert parent.calls == ["deepseek-v4-pro"], "必须且只能经 with_model 派生一次"
        assert res.llm is not parent
        assert res.llm.model == "deepseek-v4-pro"
        assert res.source == "explicit"
        assert res.model == "deepseek-v4-pro"
        assert res.error == ""

    def test_指定模型_首尾空格被规范化(self):
        parent = SpyLLM("m-a")
        res = resolve_subagent_llm("  m-b  ", parent_llm=parent)
        assert parent.calls == ["m-b"]
        assert res.requested == "m-b"

    def test_真件_LLMService_with_model_只换模型名(self):
        """真件取证：同 provider / 同 api_key / 同 base_url，模型名换成入参；父实例不被改"""
        parent = _real_llm("m-a")
        child = parent.with_model("m-b")
        assert child is not parent
        assert child.model == "m-b"
        assert parent.model == "m-a", "派生不得改写父实例（并发安全）"
        assert child.provider == parent.provider
        assert child.api_key == parent.api_key
        assert child._base_url == parent._base_url

    def test_真件_经过解析器端到端可用(self):
        parent = _real_llm("m-a")
        res = resolve_subagent_llm("m-b", parent_llm=parent)
        assert res.source == "explicit"
        assert res.model == "m-b"
        assert res.llm is not parent
        assert res.llm.api_key == parent.api_key, "密钥沿用部署配置，不由分身配置提供"

    def test_真件_空模型名被拒(self):
        from memory.llm_service import LLMService

        with pytest.raises(ValueError):
            _real_llm("m-a").with_model("   ")
        assert LLMService is not None  # 保持导入被使用（真件而非替身）


# ════════════════════════════════════════════════════════════
#  3. 派生失败 ⇒ 回退母体 + 如实记账（不抛）
# ════════════════════════════════════════════════════════════


class TestFallback:
    def test_无母体LLM_回退且说明原因(self):
        res = resolve_subagent_llm("m-b", parent_llm=None)
        assert res.llm is None
        assert res.source == "fallback-after-error"
        assert res.requested == "m-b"
        assert "母体" in res.error

    def test_母体不支持换模型_回退并点名with_model(self):
        parent = NoWithModel("legacy-model")
        res = resolve_subagent_llm("m-b", parent_llm=parent)
        assert res.llm is parent
        assert res.source == "fallback-after-error"
        assert res.model == "legacy-model", "生效模型如实回母体模型"
        assert "with_model" in res.error

    def test_派生抛异常_不冒泡且带回原文(self):
        parent = BoomLLM("m-a")
        res = resolve_subagent_llm("m-b", parent_llm=parent)
        assert parent.calls == ["m-b"], "失败路径也确实尝试过派生"
        assert res.llm is parent, "回退到母体实例（本次委派照常进行）"
        assert res.source == "fallback-after-error"
        assert "RuntimeError" in res.error and "模型目录不可用" in res.error
        assert res.model == "m-a"


# ════════════════════════════════════════════════════════════
#  4. 投影与可选清单
# ════════════════════════════════════════════════════════════


class TestProjection:
    def test_to_dict_键集固定且不含密钥(self):
        parent = SpyLLM("m-a")
        payload = resolve_subagent_llm("m-b", parent_llm=parent).to_dict()
        assert set(payload) == {"requested", "model", "source", "error"}
        blob = json.dumps(payload, ensure_ascii=False)
        for forbidden in ("api_key", "sk-", "sk_test", "bearer", FAKE_KEY):
            assert forbidden not in blob

    def test_model_name_取不到时不猜(self):
        assert model_name(None) == ""
        assert model_name(object()) == ""
        assert model_name(SpyLLM("m-z")) == "m-z"

    def test_可选清单_只列有出处的名字_去重且排除哨兵(self):
        view = llm_options("deepseek-flash",
                           ["deepseek-v4-pro", "deepseek-flash", "", " inherit ", "deepseek-v4-pro"])
        assert view["model"] == "deepseek-flash"
        assert view["options"] == [
            {"model": "deepseek-flash", "source": "deployment"},
            {"model": "deepseek-v4-pro", "source": "declared"},
        ], "部署模型在前、已声明去重在后、哨兵值不得进清单"

    def test_可选清单_部署模型未知时不塞空项(self):
        view = llm_options("", ["m-b"])
        assert view["model"] == ""
        assert view["options"] == [{"model": "m-b", "source": "declared"}]

    def test_解析结果是不可变值对象(self):
        res = resolve_subagent_llm("", parent_llm=None)
        assert isinstance(res, LlmResolution)
        with pytest.raises(Exception):
            res.model = "改写"  # type: ignore[misc]
