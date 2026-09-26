# -*- coding: utf-8 -*-
"""E1-D · AGENT_HYBRID_EMBEDDING 登记语义 + "开关只有一个判据"回归

背景（E1-C 残留 2，与 F11-C-1「死配置」同族）
--------------------------------------------
`agent/settings/registry.py` 把它登记为「工具路由混合检索的**向量模型**」、默认 `""`，
而代码把它当**布尔开关**用；并且 `_ensure_st_checked` 里那条"0 = 禁用"的分支
**全仓无生产调用点**（Q3 §11 / E1-F1 已证）——一条"看着权威、语义却说反了"的旋钮，
外加一条**没有调用点的假通路**。

本文件把两件事钉死
----------------
1. **登记与代码事实逐项对齐**：类型 = 布尔、默认 = 启用（env 缺席时生产确实预热）、
   描述里写明"怎么关、关到什么程度"（抑制预热 ≠ 硬禁用）；
2. **开关只有一处判据**：env 名在归属模块里只被解析**一次**
   （`_resolve_embedding_env_override`），生产 gate 与 `_ensure_st_checked` 都调它
   ⇒ 不允许再出现"第二份解析"这种声明漂移源。
"""
from __future__ import annotations

import re
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.settings import registry as R  # noqa: E402

_MODULE = ROOT / "agent" / "tool_router_hybrid.py"
_KEY = "AGENT_HYBRID_EMBEDDING"


@pytest.fixture(autouse=True)
def _reset_singleton():
    import agent.tool_router_hybrid as mod
    from agent.tool_router_hybrid import reset_hybrid_retriever

    reset_hybrid_retriever()
    mod._PROBE_RESULT = None
    yield
    reset_hybrid_retriever()
    mod._PROBE_RESULT = None


def _source() -> str:
    return _MODULE.read_text(encoding="utf-8")


class TestRegistrationMatchesCodeFacts:
    """登记的类型 / 默认值 / 描述三者都必须与代码事实一致"""

    def test_registered_as_bool_switch(self):
        spec = R.get_spec(_KEY)
        assert spec is not None, "注册表缺少 %s" % _KEY
        assert spec.type == "bool", (
            "代码把它当布尔开关读，登记类型却是 %r ⇒ 开关中心会渲染成文本框"
            "（这就是 E1-C 记的「登记为向量模型」）" % spec.type)
        assert spec.validator.kind == "bool", spec.validator.kind

    def test_default_is_enabled_like_the_code(self):
        spec = R.get_spec(_KEY)
        assert spec.default is True, (
            "env 缺席时 HybridRetriever 照常预热向量腿（代码事实 = 启用），"
            "登记默认值却是 %r" % (spec.default,))

    def test_description_documents_how_to_disable_and_how_far(self):
        desc = R.get_spec(_KEY).description
        assert "开关" in desc and "不是模型名" in desc, (
            "描述必须点明它是开关、且**不是**模型名（模型名另有固定常量）")
        assert "0/false/no/off" in desc and "1/true/yes/on" in desc, (
            "描述必须写出这个开关接受的两种写法")
        assert "预热" in desc and "不是硬禁用" in desc, (
            "描述必须让运维看懂关到什么程度：只**抑制预热**，不是硬禁用")

    def test_env_words_in_description_match_the_code(self):
        """描述里承诺的取值集合必须与代码解析口**逐词一致**（防文档漂移）"""
        src = _source()
        off = re.search(r'if env_val in \(([^)]*)\):\s*\n\s*return False', src)
        on = re.search(r'if env_val in \(([^)]*)\):\s*\n\s*return True', src)
        assert off and on, "未能从 _resolve_embedding_env_override 提取取值集合"
        desc = R.get_spec(_KEY).description
        for word in re.findall(r'"([^"]+)"', off.group(1)):
            assert word in desc, "代码认下的关闭写法 %r 没写进登记描述" % word
        for word in re.findall(r'"([^"]+)"', on.group(1)):
            assert word in desc, "代码认下的开启写法 %r 没写进登记描述" % word


class TestSingleSourceOfTruthForTheSwitch:
    """开关只许有**一处**判据；生产路径必须调它（"死分支"的根治判据）"""

    def test_env_name_is_parsed_in_exactly_one_place(self):
        src = _source()
        hits = re.findall(r'os\.environ\.get\(\s*"' + _KEY + r'"', src)
        assert len(hits) == 1, (
            "归属模块里有 %d 处各自解析 %s —— 两份实现就是「声明与事实漂移」的温床"
            "（E1-D 原状：探针函数一处、生产 gate 一处）" % (len(hits), _KEY))

    def test_production_gate_calls_the_shared_resolver(self):
        src = _source()
        start = src.index("class HybridRetriever")
        init = src.index("def __init__(", start)
        end = src.index("def _load_and_build_index", init)
        assert "_resolve_embedding_env_override()" in src[init:end], (
            "HybridRetriever.__init__ 没有调用共用解析口 ⇒ 生产判据又变成私有的一份")

    def test_probe_helper_reuses_the_same_resolver(self):
        src = _source()
        start = src.index("def _ensure_st_checked")
        end = src.index("def ", start + 10)
        body = src[start:end]
        assert "_resolve_embedding_env_override()" in body, (
            "_ensure_st_checked 里的 env 分支又变回自己那份判断 ⇒ 死分支的假通路回来了")
        assert 'os.environ.get("' + _KEY + '"' not in body, (
            "_ensure_st_checked 又自己解析了一遍 env")

    def test_off_words_disable_and_on_words_enable(self, monkeypatch):
        from agent.tool_router_hybrid import _resolve_embedding_env_override

        for val in ("0", "false", "no", "off", " OFF "):
            monkeypatch.setenv(_KEY, val)
            assert _resolve_embedding_env_override() is False, val
        for val in ("1", "true", "yes", "on", " YES "):
            monkeypatch.setenv(_KEY, val)
            assert _resolve_embedding_env_override() is True, val
        for val in ("", "sentence-transformers/paraphrase"):
            monkeypatch.setenv(_KEY, val)
            assert _resolve_embedding_env_override() is None, val
        monkeypatch.delenv(_KEY, raising=False)
        assert _resolve_embedding_env_override() is None, "env 缺席必须交回默认路径"

    def test_probe_helper_agrees_with_the_resolver(self, monkeypatch):
        import agent.tool_router_hybrid as trh

        for val, expect in (("0", False), ("1", True)):
            monkeypatch.setenv(_KEY, val)
            trh._PROBE_RESULT = None
            assert trh._ensure_st_checked() is expect, val


class TestProductionGateHonoursTheSwitch:
    """**行为**判据：生产 gate（HybridRetriever.__init__）真的按开关启动/不启动预热"""

    @pytest.fixture
    def small_index(self, tmp_path):
        import json

        p = tmp_path / "tool_index.json"
        p.write_text(json.dumps({"tools": [
            {"name": "alpha_tool", "description": "alpha 描述", "parameter_names": ["q"]},
            {"name": "beta_tool", "description": "beta 描述", "parameter_names": ["p"]},
        ]}, ensure_ascii=False), encoding="utf-8")
        return p

    def _construct(self, index, monkeypatch, started: threading.Event):
        import agent.tool_router_hybrid as trh

        def fake_preheat(self):
            started.set()

        monkeypatch.setattr(trh.EmbeddingIndex, "preheat", fake_preheat)
        r = trh.HybridRetriever(index_path=str(index))
        assert r.available, "BM25 索引未就绪，本用例会空转"
        return r

    def test_disabled_switch_does_not_start_preheat(self, small_index, monkeypatch):
        import agent.tool_router_hybrid as trh

        monkeypatch.setenv(_KEY, "0")
        started = threading.Event()
        self._construct(small_index, monkeypatch, started)
        assert not started.wait(0.5), (
            "%s=0 仍然拉起了向量腿预热 —— 注册表里承诺的「关」没有生效" % _KEY)

    def test_default_switch_starts_preheat(self, small_index, monkeypatch):
        monkeypatch.delenv(_KEY, raising=False)
        started = threading.Event()
        self._construct(small_index, monkeypatch, started)
        assert started.wait(5.0), (
            "env 缺席时向量腿不再预热 ⇒ 登记默认值 True 与代码事实不符")
