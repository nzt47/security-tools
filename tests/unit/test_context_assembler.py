"""ContextAssembler 三级上下文组装器单元测试

覆盖：
- _estimate_tokens / _get_encoder token 估算（tiktoken 与降级路径）
- ContextAssembler 初始化（参数 int 化、L0 token 硬上限回退）
- L0 热数据 / L1 温数据 / L2 冷数据三层组装及全部降级分支
- assemble 主入口与指标埋点

外部依赖（scorer/adapter/syncer/observability/时间）一律 mock。
"""
# pylint: disable=redefined-outer-name,missing-function-docstring
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from agent.memory.context_assembler import (
    ContextAssembler,
    _estimate_tokens,
    _get_encoder,
)


def _run(coro):
    """同步执行协程（不依赖 pytest-asyncio）"""
    return asyncio.run(coro)


# ═══════════════════════════════════════════════════════════
#  token 估算
# ═══════════════════════════════════════════════════════════

class TestEstimateTokens:
    """_estimate_tokens 函数"""

    def test_empty_text_returns_zero(self):
        """空文本应估算为 0 token"""
        assert _estimate_tokens("") == 0

    def test_fallback_without_tiktoken(self):
        """tiktoken 不可用时按 len//3 降级估算（至少 1）"""
        with patch("agent.memory.context_assembler._get_encoder", return_value=None):
            assert _estimate_tokens("abcdef") == 2
            assert _estimate_tokens("a") == 1

    def test_uses_tiktoken_when_available(self):
        """tiktoken 可用时应返回编码器精确计数"""
        enc = Mock()
        enc.encode.return_value = [0] * 5
        with patch("agent.memory.context_assembler._get_encoder", return_value=enc):
            assert _estimate_tokens("text") == 5

    def test_fallback_when_encode_fails(self):
        """编码器抛异常时应降级估算而非抛出"""
        enc = Mock()
        enc.encode.side_effect = RuntimeError("boom")
        with patch("agent.memory.context_assembler._get_encoder", return_value=enc):
            assert _estimate_tokens("abcdef") == 2


class TestGetEncoder:
    """_get_encoder 函数"""

    def test_none_when_tiktoken_unavailable(self):
        """tiktoken 未安装时应返回 None"""
        with patch("agent.memory.context_assembler._TIKTOKEN_AVAILABLE", False):
            assert _get_encoder() is None

    def test_lazy_initialization_and_cache(self):
        """编码器应延迟初始化且只初始化一次"""
        enc = Mock()
        with patch("agent.memory.context_assembler._TIKTOKEN_AVAILABLE", True), \
             patch("agent.memory.context_assembler._tiktoken") as tiktoken_mod, \
             patch("agent.memory.context_assembler._ENCODER", None):
            tiktoken_mod.get_encoding.return_value = enc
            assert _get_encoder() is enc
            # 第二次调用命中缓存，不再初始化
            tiktoken_mod.get_encoding.reset_mock()
            assert _get_encoder() is enc
            tiktoken_mod.get_encoding.assert_not_called()

    def test_none_when_initialization_fails(self):
        """编码器初始化抛异常时应返回 None"""
        with patch("agent.memory.context_assembler._TIKTOKEN_AVAILABLE", True), \
             patch("agent.memory.context_assembler._tiktoken") as tiktoken_mod, \
             patch("agent.memory.context_assembler._ENCODER", None):
            tiktoken_mod.get_encoding.side_effect = RuntimeError("no model")
            assert _get_encoder() is None


# ═══════════════════════════════════════════════════════════
#  初始化
# ═══════════════════════════════════════════════════════════

class TestContextAssemblerInit:
    """ContextAssembler 构造函数"""

    def test_defaults(self):
        """无参构造应使用全部默认配置且组件均为 None"""
        a = ContextAssembler()
        assert a.adapter is None
        assert a.scorer is None
        assert a.syncer is None
        assert a.l0_token_limit == 300
        assert a.l0_top_n == 5
        assert a.l1_top_k == 8
        assert a.l2_top_k == 3
        assert a.l2_max_chars == 500

    def test_int_conversion(self):
        """数值参数应为 int 类型（字符串自动转换）"""
        a = ContextAssembler(
            l0_token_limit="100", l0_top_n="5", l1_top_k="8",
            l2_top_k="3", l2_max_chars="500",
        )
        assert a.l0_token_limit == 100
        assert a.l0_top_n == 5
        assert a.l1_top_k == 8
        assert a.l2_top_k == 3
        assert a.l2_max_chars == 500

    def test_l0_limit_forced_back_to_hard_cap(self):
        """L0 token 上限超过 300 硬上限时应强制回退"""
        a = ContextAssembler(l0_token_limit=500)
        assert a.l0_token_limit == 300

    def test_l0_limit_below_cap_kept(self):
        """L0 token 上限低于硬上限时应保留配置值"""
        a = ContextAssembler(l0_token_limit=100)
        assert a.l0_token_limit == 100

    def test_components_stored(self):
        """adapter/scorer/syncer 应原样保存"""
        adapter, scorer, syncer = object(), object(), object()
        a = ContextAssembler(adapter=adapter, scorer=scorer, syncer=syncer)
        assert a.adapter is adapter
        assert a.scorer is scorer
        assert a.syncer is syncer


# ═══════════════════════════════════════════════════════════
#  L0 热数据层
# ═══════════════════════════════════════════════════════════

class TestBuildL0:
    """_build_l0 方法"""

    def test_no_scorer_returns_empty(self):
        """scorer 未注入时应返回空字符串"""
        assert ContextAssembler()._build_l0() == ""

    def test_scorer_failure_returns_empty(self):
        """scorer.get_hot_records 抛异常时应降级为空字符串"""
        scorer = Mock()
        scorer.get_hot_records.side_effect = RuntimeError("boom")
        assert ContextAssembler(scorer=scorer)._build_l0() == ""

    def test_empty_records_returns_empty(self):
        """无热数据记录时应返回空字符串"""
        scorer = Mock()
        scorer.get_hot_records.return_value = []
        assert ContextAssembler(scorer=scorer)._build_l0() == ""

    def test_joins_hot_records(self):
        """正常应拼接为 key: data 摘要行，并按 top_n 查询"""
        scorer = Mock()
        scorer.get_hot_records.return_value = [
            {"key": "k1", "data": "d1"},
            {"key": "k2", "data": "d2"},
        ]
        text = ContextAssembler(scorer=scorer)._build_l0()
        assert text == "- [k1] d1\n- [k2] d2"
        scorer.get_hot_records.assert_called_once_with(top_n=5)

    def test_long_data_truncated_to_120_chars(self):
        """单条 data 超过 120 字符时应截断并追加省略号"""
        scorer = Mock()
        scorer.get_hot_records.return_value = [{"key": "k1", "data": "x" * 200}]
        text = ContextAssembler(scorer=scorer)._build_l0()
        assert text.endswith("...")
        assert len(text) == len("- [k1] ") + 120 + 3

    def test_token_limit_truncates(self):
        """token 超限时应按条目粒度截断且不拆条目内部"""
        scorer = Mock()
        scorer.get_hot_records.return_value = [
            {"key": "k1", "data": "a"},
            {"key": "k2", "data": "b"},
            {"key": "k3", "data": "c"},
        ]
        # 每条估算 100 token，硬上限 150 → 只能容纳 1 条
        with patch("agent.memory.context_assembler._estimate_tokens",
                   side_effect=[100, 100, 100]):
            text = ContextAssembler(scorer=scorer, l0_token_limit=150)._build_l0()
        assert text == "- [k1] a"


# ═══════════════════════════════════════════════════════════
#  L1 温数据层
# ═══════════════════════════════════════════════════════════

class TestBuildL1:
    """_build_l1 方法"""

    def test_no_adapter_returns_empty(self):
        """adapter 未注入时应返回空列表"""
        assert _run(ContextAssembler()._build_l1("q", 2000)) == []

    def test_empty_query_returns_empty(self):
        """空查询时应返回空列表"""
        adapter = SimpleNamespace(search=AsyncMock())
        assert _run(ContextAssembler(adapter=adapter)._build_l1("", 2000)) == []

    def test_search_failure_returns_empty(self):
        """adapter.search 抛异常时应降级为空列表"""
        adapter = SimpleNamespace(search=AsyncMock(side_effect=RuntimeError("boom")))
        assert _run(ContextAssembler(adapter=adapter)._build_l1("q", 2000)) == []

    def test_filters_and_keeps_results(self):
        """应按 access_count>=1 过滤：非 dict 跳过、缺省保留、已访问保留"""
        adapter = SimpleNamespace(search=AsyncMock(return_value=[
            SimpleNamespace(metadata={"access_count": 2}),  # 保留
            SimpleNamespace(metadata={"access_count": 0}),  # 过滤
            SimpleNamespace(metadata="not-dict"),           # 跳过
            SimpleNamespace(),                              # 无 metadata → 保留
        ]))
        items = _run(ContextAssembler(adapter=adapter)._build_l1("q", 2000))
        assert len(items) == 2
        assert items[0].metadata["access_count"] == 2
        adapter.search.assert_called_once_with("q", top_k=8)


# ═══════════════════════════════════════════════════════════
#  L2 冷数据层
# ═══════════════════════════════════════════════════════════

class TestBuildL2:
    """_build_l2 方法"""

    def _assembler(self, adapter=None, syncer=None, **kwargs):
        return ContextAssembler(adapter=adapter, syncer=syncer, **kwargs)

    def test_no_adapter_or_syncer_returns_empty(self):
        """adapter/syncer 任一未注入时应返回空列表"""
        assert _run(ContextAssembler()._build_l2("q")) == []
        assert _run(ContextAssembler(adapter=object())._build_l2("q")) == []
        assert _run(ContextAssembler(syncer=Mock())._build_l2("q")) == []

    def test_empty_query_returns_empty(self):
        """空查询时应返回空列表"""
        adapter = SimpleNamespace(_vec_available=True, _embedding_func=lambda q: [1.0])
        syncer = Mock()
        assert _run(self._assembler(adapter, syncer)._build_l2("")) == []

    def test_vec_unavailable_returns_empty(self):
        """向量层不可用（_vec_available=False）时应返回空列表"""
        adapter = SimpleNamespace(_vec_available=False)
        assert _run(self._assembler(adapter, Mock())._build_l2("q")) == []

    def test_no_embedding_func_returns_empty(self):
        """adapter 未注入 _embedding_func 时应返回空列表"""
        adapter = SimpleNamespace(_vec_available=True)
        assert _run(self._assembler(adapter, Mock())._build_l2("q")) == []

    def test_embedding_failure_returns_empty(self):
        """embedding 函数抛异常时应降级为空列表"""
        def bad_emb(query):
            raise RuntimeError("emb boom")
        adapter = SimpleNamespace(_vec_available=True, _embedding_func=bad_emb)
        assert _run(self._assembler(adapter, Mock())._build_l2("q")) == []

    def test_empty_embedding_returns_empty(self):
        """embedding 函数返回空向量时应返回空列表"""
        adapter = SimpleNamespace(_vec_available=True, _embedding_func=lambda q: [])
        assert _run(self._assembler(adapter, Mock())._build_l2("q")) == []

    def test_vector_search_failure_returns_empty(self):
        """向量检索抛异常时应降级为空列表"""
        adapter = SimpleNamespace(
            _vec_available=True, _embedding_func=lambda q: [1.0],
            search_vector=AsyncMock(side_effect=RuntimeError("boom")),
        )
        assert _run(self._assembler(adapter, Mock())._build_l2("q")) == []

    def test_assembles_fragments(self):
        """命中 key 时应从 Markdown 归档懒加载 fragment"""
        adapter = SimpleNamespace(
            _vec_available=True, _embedding_func=lambda q: [0.1, 0.2],
            search_vector=AsyncMock(return_value=[
                SimpleNamespace(metadata={"key": "k1"}),
                SimpleNamespace(metadata={"key": "k2"}),
            ]),
        )
        syncer = Mock()
        syncer.read_fragment.side_effect = lambda key, max_chars: f"frag-{key}"
        items = _run(self._assembler(adapter, syncer)._build_l2("q"))
        assert items == [
            {"key": "k1", "fragment": "frag-k1", "source": "markdown_archive"},
            {"key": "k2", "fragment": "frag-k2", "source": "markdown_archive"},
        ]
        syncer.read_fragment.assert_any_call("k1", max_chars=500)
        syncer.read_fragment.assert_any_call("k2", max_chars=500)

    def test_skip_result_without_key(self):
        """命中结果无 key 时应跳过"""
        adapter = SimpleNamespace(
            _vec_available=True, _embedding_func=lambda q: [1.0],
            search_vector=AsyncMock(return_value=[
                SimpleNamespace(metadata={}),
                SimpleNamespace(metadata={"key": "k1"}),
            ]),
        )
        syncer = Mock()
        syncer.read_fragment.return_value = "frag"
        items = _run(self._assembler(adapter, syncer)._build_l2("q"))
        assert [i["key"] for i in items] == ["k1"]

    def test_fragment_read_failure_skipped(self):
        """read_fragment 抛异常时应跳过该条而不中断"""
        adapter = SimpleNamespace(
            _vec_available=True, _embedding_func=lambda q: [1.0],
            search_vector=AsyncMock(return_value=[
                SimpleNamespace(metadata={"key": "k1"}),
                SimpleNamespace(metadata={"key": "k2"}),
            ]),
        )
        syncer = Mock()
        syncer.read_fragment.side_effect = RuntimeError("read boom")
        assert _run(self._assembler(adapter, syncer)._build_l2("q")) == []

    def test_empty_fragment_skipped(self):
        """read_fragment 返回空字符串时应跳过该条"""
        adapter = SimpleNamespace(
            _vec_available=True, _embedding_func=lambda q: [1.0],
            search_vector=AsyncMock(return_value=[
                SimpleNamespace(metadata={"key": "k1"}),
            ]),
        )
        syncer = Mock()
        syncer.read_fragment.return_value = ""
        assert _run(self._assembler(adapter, syncer)._build_l2("q")) == []


# ═══════════════════════════════════════════════════════════
#  assemble 主入口与指标
# ═══════════════════════════════════════════════════════════

class TestAssemble:
    """assemble 主入口"""

    def test_all_none_components(self):
        """组件全缺省时应返回空三层与完整 meta"""
        assembler = ContextAssembler()
        with patch.object(assembler, "_emit_metrics"):
            result = _run(assembler.assemble("q"))
        assert result["L0"] == ""
        assert result["L1"] == []
        assert result["L2"] == []
        meta = result["meta"]
        assert meta["l0_tokens"] == 0
        assert meta["l1_count"] == 0
        assert meta["l2_count"] == 0
        assert meta["elapsed_ms"] >= 0
        assert meta["l2_elapsed_ms"] >= 0

    def test_assembles_three_layers(self):
        """注入组件时应逐层组装并汇总 meta"""
        assembler = ContextAssembler(adapter=object(), scorer=object(), syncer=object())
        with patch.object(assembler, "_build_l0", return_value="L0 text"), \
             patch.object(assembler, "_build_l1", new=AsyncMock(return_value=["r1", "r2"])), \
             patch.object(assembler, "_build_l2", new=AsyncMock(return_value=[{"key": "k"}])), \
             patch.object(assembler, "_emit_metrics"):
            result = _run(assembler.assemble("q", max_tokens=100))
        assert result["L0"] == "L0 text"
        assert result["L1"] == ["r1", "r2"]
        assert result["L2"] == [{"key": "k"}]
        meta = result["meta"]
        assert meta["l1_count"] == 2
        assert meta["l2_count"] == 1


class TestEmitMetrics:
    """_emit_metrics 指标埋点"""

    def test_tracks_all_metrics(self):
        """observability 提供埋点函数时应上报 L0/L1/L2 token 与整体延迟"""
        import agent.memory.observability as obs_mod
        assembler = ContextAssembler()
        with patch.object(obs_mod, "track_tlm_context_assembled_tokens",
                          create=True) as m1, \
             patch.object(obs_mod, "track_tlm_retrieval_latency", create=True) as m2:
            assembler._emit_metrics(10, 3, 2, 15.5)
        m1.assert_any_call("L0", 10)
        m1.assert_any_call("L1", 300)
        m1.assert_any_call("L2", 200)
        m2.assert_called_once_with("hybrid", 15.5)

    def test_metrics_failure_ignored(self):
        """埋点依赖缺失（真实模块无此函数）时应被吞掉不影响调用方"""
        assembler = ContextAssembler()
        # 当前 agent.memory.observability 未提供这两个函数，
        # _emit_metrics 内部 import 失败被 except 吞掉，不应抛异常
        assembler._emit_metrics(1, 1, 1, 1.0)
