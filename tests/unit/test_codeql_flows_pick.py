# -*- coding: utf-8 -*-
"""CodeQL 分析选择：**只能取默认分支的分析**（2026-11-07）。

【为什么单开一条】PR head 的分析是增量口径：SARIF 里 results=0
（实测 refs/pull/1488/head 的 python 分析 results=0，同一时刻 master 上 results=89）。
列表 API 按时间倒序，PR 分析永远比 master 新 ⇒ 取第一条就等于永远拿到空 SARIF，
--sources-only 一直空，"按源改"的定位方法随即失效。本轮就踩了：
cache 里 232KB 的 SARIF 一条结果都没有。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _mod():
    import importlib
    return importlib.import_module("scripts.audit.codeql_flows")


def _item(cid: int, language: str, ref: str, results: int = 0):
    return {"id": cid, "category": "/language:" + language, "ref": ref,
            "results_count": results}


class Test选择默认分支分析:
    def test_跳过_PR_分析取默认分支(self):
        items = [
            _item(3, "python", "refs/pull/1488/head", 0),
            _item(2, "python", "refs/heads/master", 89),
            _item(1, "python", "refs/heads/master", 88),
        ]
        picked = _mod()._pick_analysis(items, "python")
        assert picked["id"] == 2
        assert picked["results_count"] == 89

    def test_语言不同不混用(self):
        items = [
            _item(5, "javascript-typescript", "refs/heads/master", 16),
            _item(4, "python", "refs/heads/master", 89),
        ]
        assert _mod()._pick_analysis(items, "python")["id"] == 4

    def test_只有_PR_分析时退化不返回空(self):
        items = [_item(9, "python", "refs/pull/1/head", 0)]
        picked = _mod()._pick_analysis(items, "python")
        assert picked is not None and picked["id"] == 9

    def test_没有该语言返回_None(self):
        assert _mod()._pick_analysis([_item(1, "actions", "refs/heads/master")], "python") is None

    def test_空列表返回_None(self):
        assert _mod()._pick_analysis([], "python") is None
