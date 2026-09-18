"""``web_search(preset="news")`` 的**多查询合并**语义（2026-09-18 行为变更）

背景：新闻模式原实现逐条尝试 3 条查询，但**第一条成功就 break**（"首个成功查询即采用"），
于是后两条形同虚设 —— 只要第一条返回 1 条结果，最终结果集就只有那 1 条，
``seen_urls`` 去重也因此毫无意义。现改为**合并三条查询的结果**（按 url 去重 → 来源优先级
排序 → 截断），并保留两条边界：够一页即停、总耗时超预算即停（避免 3×单查询超时叠加）。

本文件把这几条**行为契约**钉住：合并、去重、排序、提前停止、逐查询容错、如实披露。
"""
from __future__ import annotations

from agent.tools import web_tools as W

DEFAULT_Q = W._NEWS_DEFAULT_QUERIES


def _item(url: str, title: str = "T", snippet: str = "S", **extra) -> dict:
    return {"title": title, "url": url, "snippet": snippet, **extra}


class _FakeSearcher:
    """按查询返回预设结果，并记录被真正发起的查询"""

    def __init__(self, per_query: dict, fail_on: tuple = ()) -> None:
        self.per_query = per_query
        self.fail_on = tuple(fail_on)
        self.calls: list = []

    def search(self, q, engine=None, num_results=10, timeout=10):
        self.calls.append(q)
        if q in self.fail_on:
            raise RuntimeError("engine down")
        return {"ok": True, "results": list(self.per_query.get(q, []))}


class _FakeDL:
    def __init__(self, searcher) -> None:
        self._searcher = searcher

    def _get_web_search(self):
        return self._searcher


def _news(searcher, topic: str = "", limit: int = 10) -> dict:
    return W._news_search(_FakeDL(searcher), topic, "", limit)


class TestNewsQueriesAreMerged:

    def test_三条查询的结果合并成一个结果集(self):
        searcher = _FakeSearcher({
            DEFAULT_Q[0]: [_item("https://bbc.com/a")],
            DEFAULT_Q[1]: [_item("https://cnn.com/b")],
            DEFAULT_Q[2]: [_item("https://example.com/c")],
        })

        result = _news(searcher)

        assert result["count"] == 3, f"三条查询的结果没有合并: {result}"
        assert len(searcher.calls) == 3
        # 来源优先级排序：bbc < cnn < 其它
        order = [line for line in result["result"].splitlines() if "链接" in line]
        assert order == ["   - 链接: https://bbc.com/a",
                         "   - 链接: https://cnn.com/b",
                         "   - 链接: https://example.com/c"], order

    def test_同一url在两条查询里出现只保留一条(self):
        dup = "https://reuters.com/same"
        searcher = _FakeSearcher({
            DEFAULT_Q[0]: [_item(dup), _item("https://a.com/1")],
            DEFAULT_Q[1]: [_item(dup)],
        })

        result = _news(searcher)

        assert result["count"] == 2
        assert result["result"].count(dup) == 1

    def test_够一页就提前停止不再打多余请求(self):
        """合并 ≠ 无脑打满三条：第一条已给够 limit 就不再发后续请求"""
        searcher = _FakeSearcher({
            DEFAULT_Q[0]: [_item(f"https://a.com/{i}") for i in range(10)],
        })

        result = _news(searcher, limit=5)

        assert result["count"] == 5
        assert searcher.calls == [DEFAULT_Q[0]], searcher.calls

    def test_单条查询失败不影响其余合并且如实披露(self):
        searcher = _FakeSearcher({
            DEFAULT_Q[0]: [_item("https://bbc.com/a")],
            DEFAULT_Q[1]: [_item("https://cnn.com/b")],   # 会被 fail_on 覆盖
            DEFAULT_Q[2]: [_item("https://example.com/c")],
        }, fail_on=(DEFAULT_Q[1],))

        result = _news(searcher)

        assert result["count"] == 2, "失败的那条查询不该拖垮整体"
        assert searcher.calls == list(DEFAULT_Q), "失败的查询不该中断后续查询"
        assert DEFAULT_Q[1] in result["queries_used"]
        assert result["queries_failed"] == [DEFAULT_Q[1]]

    def test_全部无结果时如实披露发起过的查询(self):
        searcher = _FakeSearcher({})

        result = _news(searcher)

        assert result["count"] == 0
        assert result["queries_used"] == list(DEFAULT_Q)
        assert result["queries_failed"] == []

    def test_查询组合与既有实现一致(self):
        """无 topic 用通用查询；有 topic 用 topic 三条（行为不变）"""
        s1 = _FakeSearcher({})
        _news(s1, topic="")
        assert s1.calls == list(DEFAULT_Q)

        s2 = _FakeSearcher({})
        _news(s2, topic="AI 芯片")
        assert s2.calls == ["latest AI 芯片 news", "AI 芯片 breaking news",
                            "AI 芯片 today"]


class TestNewsFieldsAreHonest:

    def test_没有发布时间时标注unknown而不是伪造运行时刻(self):
        searcher = _FakeSearcher({DEFAULT_Q[0]: [_item("https://bbc.com/no-date")]})

        result = _news(searcher)

        assert "时间: unknown" in result["result"]

    def test_有发布时间时用发布时间(self):
        searcher = _FakeSearcher({DEFAULT_Q[0]: [
            _item("https://bbc.com/dated", published_date="2026-09-18T08:00:00Z")]})

        result = _news(searcher)

        assert "时间: 2026-09-18T08:00:00Z" in result["result"]

    def test_来源按域名识别(self):
        searcher = _FakeSearcher({DEFAULT_Q[0]: [_item("https://www.reuters.com/x")]})

        result = _news(searcher)

        assert "来源: Reuters" in result["result"]
