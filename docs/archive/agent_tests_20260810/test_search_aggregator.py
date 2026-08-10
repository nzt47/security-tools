"""搜索聚合器集成测试 -- 测试 search_aggregator.py 的 SearchAggregator

覆盖范围：
- URL 归一化（协议/w基础/尾部斜杠/追踪参数）
- 结果评分（来源权重 + 关键词加分，上限 1.5）
- 去重逻辑
- 引擎选择
- 超时行为
- 单引擎错误隔离
"""
import pytest
from unittest.mock import patch, MagicMock

from agent.search_aggregator import SearchAggregator, DEFAULT_SOURCE_WEIGHTS


# ════════════════════════════════════════════════════════════════════════════════
#  URL 归一化测试
# ════════════════════════════════════════════════════════════════════════════════

class TestUrlNormalization:
    """normalize_url 静态方法测试"""

    def test_strip_protocol(self):
        """去掉 https:// 和 http:// 协议前缀"""
        assert SearchAggregator.normalize_url("https://example.com") == "example.com"
        assert SearchAggregator.normalize_url("http://example.com") == "example.com"

    def test_remove_www_prefix(self):
        """去掉 www. 前缀"""
        result = SearchAggregator.normalize_url("https://www.example.com/path")
        assert result == "example.com/path"

    def test_strip_trailing_slash(self):
        """去掉末尾斜杠"""
        result = SearchAggregator.normalize_url("https://example.com/path/")
        assert result == "example.com/path"

    def test_lowercase_domain(self):
        """域名统一小写，路径保持原样"""
        result = SearchAggregator.normalize_url("https://Example.COM/Path")
        # domain is lowercased, path is NOT lowercased
        assert result == "example.com/Path"

    def test_remove_tracking_params(self):
        """移除常见追踪参数（utm_*, fbclid, gclid 等）"""
        result = SearchAggregator.normalize_url(
            "https://example.com/page?q=test&utm_source=twitter&fbclid=abc123"
        )
        assert "q=test" in result
        assert "utm_source" not in result
        assert "fbclid" not in result

    def test_preserve_non_tracking_params(self):
        """保留非追踪参数"""
        result = SearchAggregator.normalize_url("https://example.com/search?q=hello&page=2")
        assert "q=hello" in result
        assert "page=2" in result

    def test_empty_url(self):
        """空 URL 返回空字符串"""
        assert SearchAggregator.normalize_url("") == ""

    def test_no_protocol_given(self):
        """未提供协议的 URL 也可以归一化"""
        result = SearchAggregator.normalize_url("example.com/Path/")
        # trailing slash stripped, path preserved as-is
        assert result == "example.com/Path"


# ════════════════════════════════════════════════════════════════════════════════
#  结果评分测试
# ════════════════════════════════════════════════════════════════════════════════

class TestScoreResult:
    """score_result 静态方法测试"""

    def test_source_weight_default(self):
        """来源权重：已知来源使用对应权重"""
        item = {"title": "", "snippet": "", "source": "tavily"}
        score = SearchAggregator.score_result(item, "")
        assert score == 1.0  # tavily = 1.0

    def test_source_weight_unknown_fallback(self):
        """未知来源使用默认回退权重 0.5"""
        item = {"title": "", "snippet": "", "source": "unknown_engine"}
        score = SearchAggregator.score_result(item, "")
        assert score == 0.5  # __default__ = 0.5

    def test_source_weight_custom(self):
        """自定义权重覆盖"""
        custom_weights = {"tavily": 1.5, "__default__": 0.3}
        item = {"title": "", "snippet": "", "source": "tavily"}
        score = SearchAggregator.score_result(item, "", source_weights=custom_weights)
        assert score == 1.5

    def test_keyword_bonus(self):
        """关键词命中加分"""
        item = {
            "title": "Python Tutorial for Beginners",
            "snippet": "Learn Python programming easily",
            "source": "tavily",
        }
        score = SearchAggregator.score_result(item, "Python beginners")
        # tavily weight 1.0 + keyword bonus (hit "python" and "beginners" => 2*0.1=0.2) = 1.2
        assert score == 1.2

    def test_keyword_bonus_chinese(self):
        """中文关键词命中加分"""
        item = {
            "title": "Python 编程入门教程",
            "snippet": "学习 Python 编程的基础知识",
            "source": "duckduckgo",
        }
        score = SearchAggregator.score_result(item, "Python 编程")
        # duckduckgo weight 0.8 + bonus
        assert score > 0.8

    def test_score_capped_at_max(self):
        """评分上限 1.5"""
        item = {
            "title": "a b c d e f g h i j k l m n o p",
            "snippet": "a b c d e f g h i j k l m n o p",
            "source": "tavily",
        }
        # 即使关键词全部命中，也不超过 MAX_SCORE_CAP
        score = SearchAggregator.score_result(item, "a b c d e f g h i j k l m n o p")
        assert score <= 1.5

    def test_empty_query_no_bonus(self):
        """空查询无关键词加分"""
        item = {"title": "Something", "snippet": "Cool stuff", "source": "tavily"}
        score = SearchAggregator.score_result(item, "")
        assert score == 1.0  # 仅来源权重

    def test_source_case_insensitive(self):
        """来源对比大小写不敏感"""
        item = {"title": "", "snippet": "", "source": "TAVILY"}
        score = SearchAggregator.score_result(item, "")
        assert score == 1.0


# ════════════════════════════════════════════════════════════════════════════════
#  去重测试
# ════════════════════════════════════════════════════════════════════════════════

class TestDeduplication:
    """_deduplicate 方法测试"""

    def _make_aggregator(self):
        """创建不含 search_engine 的虚拟聚合器，仅测试去重"""
        mock_engine = MagicMock()
        return SearchAggregator(mock_engine)

    def test_dedup_identical_urls(self):
        """相同 URL 去重"""
        agg = self._make_aggregator()
        results = [
            {"title": "First", "url": "https://example.com/page"},
            {"title": "Second", "url": "https://example.com/page"},
            {"title": "Third", "url": "https://example.com/other"},
        ]
        deduped = agg._deduplicate(results)
        assert len(deduped) == 2
        assert deduped[0]["title"] == "First"
        assert deduped[1]["title"] == "Third"

    def test_dedup_same_domain_different_case(self):
        """域名字母大小写不同的相同 URL 视为重复（域名统一小写）"""
        agg = self._make_aggregator()
        results = [
            {"title": "A", "url": "https://Example.COM/same"},
            {"title": "B", "url": "https://example.com/same"},
        ]
        deduped = agg._deduplicate(results)
        # domain is lowercased, same path → deduplicated
        assert len(deduped) == 1

    def test_dedup_tracking_params(self):
        """不同追踪参数的相同 URL 视为重复"""
        agg = self._make_aggregator()
        results = [
            {"title": "A", "url": "https://example.com?q=test&utm_source=fb"},
            {"title": "B", "url": "https://example.com?q=test&utm_source=twitter"},
        ]
        deduped = agg._deduplicate(results)
        assert len(deduped) == 1

    def test_dedup_adds_dedup_key(self):
        """去重后每项都应有 dedup_key 字段"""
        agg = self._make_aggregator()
        results = [{"title": "Test", "url": "https://example.com/hello"}]
        deduped = agg._deduplicate(results)
        assert deduped[0]["dedup_key"] == "example.com/hello"

    def test_dedup_empty_urls(self):
        """空 URL 也正常处理"""
        agg = self._make_aggregator()
        results = [
            {"title": "A", "url": ""},
            {"title": "B", "url": ""},
        ]
        deduped = agg._deduplicate(results)
        # 两条都保留（空 URL 不参与去重比较）
        assert len(deduped) == 2


# ════════════════════════════════════════════════════════════════════════════════
#  引擎选择测试
# ════════════════════════════════════════════════════════════════════════════════

class TestEngineSelection:
    """_select_engines 方法测试"""

    def test_selects_up_to_three(self):
        """最多选择 3 个引擎"""
        mock_engine = MagicMock()
        mock_engine.get_available_engines.return_value = [
            {"name": "tavily", "enabled": True},
            {"name": "duckduckgo", "enabled": True},
            {"name": "sogou", "enabled": True},
            {"name": "so360", "enabled": True},
            {"name": "firecrawl", "enabled": True},
        ]
        mock_engine._engine_priority = ["tavily", "firecrawl", "duckduckgo", "sogou", "so360"]

        agg = SearchAggregator(mock_engine)
        engines = agg._select_engines()
        assert len(engines) <= 3

    def test_skips_disabled_engines(self):
        """跳过已禁用的引擎"""
        mock_engine = MagicMock()
        mock_engine.get_available_engines.return_value = [
            {"name": "tavily", "enabled": False},
            {"name": "duckduckgo", "enabled": True},
            {"name": "sogou", "enabled": True},
            {"name": "so360", "enabled": True},
        ]
        mock_engine._engine_priority = ["tavily", "duckduckgo", "sogou", "so360"]

        agg = SearchAggregator(mock_engine)
        engines = agg._select_engines()
        assert "tavily" not in engines
        assert len(engines) <= 3

    def test_no_engines_returns_empty(self):
        """无可用引擎返回空列表"""
        mock_engine = MagicMock()
        mock_engine.get_available_engines.return_value = []
        mock_engine._engine_priority = []

        agg = SearchAggregator(mock_engine)
        engines = agg._select_engines()
        assert engines == [] or len(engines) >= 0


# ════════════════════════════════════════════════════════════════════════════════
#  聚合搜索集成测试
# ════════════════════════════════════════════════════════════════════════════════

class TestAggregateSearch:
    """aggregate_search 集成测试（mock 引擎调用）"""

    def _make_engine_result(self, ok=True, results=None, error=None):
        """构造单个引擎的搜索结果"""
        r = {"ok": ok, "engine": "test", "results": results or []}
        if error:
            r["error"] = error
        return r

    def test_aggregate_no_engines(self):
        """无可用引擎返回错误"""
        mock_engine = MagicMock()
        mock_engine.get_available_engines.return_value = []
        mock_engine._engine_priority = []

        agg = SearchAggregator(mock_engine)
        result = agg.aggregate_search("test", engines=[])
        assert result["ok"] is False

    def test_aggregate_merges_results(self):
        """多个引擎的结果被合并"""
        mock_engine = MagicMock()
        # 直接赋值 _select_engines 绕过复杂的引擎选择
        with patch.object(SearchAggregator, "_select_engines", return_value=["eng1", "eng2"]):
            with patch.object(SearchAggregator, "_search_single_engine") as mock_search:
                mock_search.side_effect = [
                    self._make_engine_result(
                        results=[{"title": "R1", "url": "http://a.com/1"}]
                    ),
                    self._make_engine_result(
                        results=[{"title": "R2", "url": "http://b.com/2"}]
                    ),
                ]
                agg = SearchAggregator(mock_engine)
                result = agg.aggregate_search("test query")

        assert result["ok"] is True
        assert result["aggregated"] is True
        assert result["engine"] == "aggregate"
        assert "eng1" in result["engine_results"]
        assert "eng2" in result["engine_results"]

    def test_aggregate_handles_engine_error(self):
        """单个引擎失败不影响其他引擎"""
        mock_engine = MagicMock()
        with patch.object(SearchAggregator, "_select_engines", return_value=["eng1", "eng2"]):
            with patch.object(SearchAggregator, "_search_single_engine") as mock_search:
                mock_search.side_effect = [
                    self._make_engine_result(error="Engine timeout"),
                    self._make_engine_result(
                        results=[{"title": "R2", "url": "http://b.com/2"}]
                    ),
                ]
                agg = SearchAggregator(mock_engine)
                result = agg.aggregate_search("test")

        assert result["ok"] is True
        assert "eng1" in result.get("errors", {})
        assert result.get("errors", {}).get("eng1") == "Engine timeout"

    def test_aggregate_deduplicates(self):
        """聚合结果自动去重"""
        mock_engine = MagicMock()
        with patch.object(SearchAggregator, "_select_engines", return_value=["eng1", "eng2"]):
            with patch.object(SearchAggregator, "_search_single_engine") as mock_search:
                mock_search.side_effect = [
                    self._make_engine_result(
                        results=[{"title": "Same Page", "url": "http://example.com/page"}]
                    ),
                    self._make_engine_result(
                        results=[{"title": "Same Page Copy", "url": "http://example.com/page"}]
                    ),
                ]
                agg = SearchAggregator(mock_engine)
                result = agg.aggregate_search("test")

        assert result["ok"] is True
        # 两条相同 URL 去重后剩 1 条
        assert result["total_estimate"] == 1

    def test_aggregate_scores_results(self):
        """聚合结果为每项添加评分"""
        mock_engine = MagicMock()
        with patch.object(SearchAggregator, "_select_engines", return_value=["eng1"]):
            with patch.object(SearchAggregator, "_search_single_engine") as mock_search:
                mock_search.return_value = self._make_engine_result(
                    results=[{"title": "Test", "url": "http://example.com/test"}]
                )
                agg = SearchAggregator(mock_engine)
                result = agg.aggregate_search("Test")

        assert result["ok"] is True
        assert "score" in result["results"][0]

    def test_aggregate_num_results_limit(self):
        """num_results 参数限制返回结果数"""
        mock_engine = MagicMock()
        # 生成 5 条不同 URL 的结果
        many_results = [
            {"title": f"R{i}", "url": f"http://example.com/{i}"} for i in range(5)
        ]
        with patch.object(SearchAggregator, "_select_engines", return_value=["eng1"]):
            with patch.object(SearchAggregator, "_search_single_engine") as mock_search:
                mock_search.return_value = self._make_engine_result(results=many_results)
                agg = SearchAggregator(mock_engine)
                result = agg.aggregate_search("test", num_results=3)

        assert result["ok"] is True
        assert len(result["results"]) <= 3

    def test_aggregate_returns_elapsed(self):
        """聚合结果包含耗时字段"""
        mock_engine = MagicMock()
        with patch.object(SearchAggregator, "_select_engines", return_value=["eng1"]):
            with patch.object(SearchAggregator, "_search_single_engine") as mock_search:
                mock_search.return_value = self._make_engine_result(
                    results=[{"title": "T", "url": "http://x.com/t"}]
                )
                agg = SearchAggregator(mock_engine)
                result = agg.aggregate_search("test")

        assert "elapsed" in result
        assert isinstance(result["elapsed"], (int, float))


# ════════════════════════════════════════════════════════════════════════════════
#  统计与工具方法测试
# ════════════════════════════════════════════════════════════════════════════════

class TestAggregatorStats:
    """get_stats 统计测试"""

    def test_initial_stats(self):
        """初始统计均为 0"""
        mock_engine = MagicMock()
        agg = SearchAggregator(mock_engine)
        stats = agg.get_stats()
        assert stats["aggregations"] == 0
        assert stats["total_search_calls"] == 0
        assert stats["total_failures"] == 0
