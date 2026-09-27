# -*- coding: utf-8 -*-
"""experience_index 单测（方案 P2a）。

覆盖：
  - 样本 → 元数据映射的字段口径（必须含 loader._meta_to_meta_text 认识的字段）
  - BM25 腿复用（不加载向量模型）
  - RRF 融合打分形状
  - verified / lang / task_type 过滤（方案硬约束 ⑤：未验证不入库）
  - 缺文件时优雅降级
"""
from __future__ import annotations

import json
import os

import pytest

from agent.skills_mgmt.experience_index import ExperienceIndex, _sample_to_meta


def _sample(sid, task, *, verified="pass", lang="python", ttype="bugfix",
            n_diffs=2, n_pitfalls=0):
    return {
        "id": sid,
        "task": task,
        "task_type": ttype,
        "stack": {"lang": lang, "frameworks": ["pytest"], "files_changed": n_diffs},
        "diffs": [{"path": "a/b.py", "op": "edit", "diff": "x", "bytes": 1} for _ in range(n_diffs)],
        "pitfalls": [{"symptom": "s"} for _ in range(n_pitfalls)],
        "verified": verified,
        "created_at": "2026-09-27T00:00:00",
        "deprecated_after": None,
    }


@pytest.fixture()
def corpus(tmp_path):
    p = tmp_path / "samples.ndjson"
    rows = [
        _sample("s1", "修复 pytest 失败 用例 断言错误", n_pitfalls=3),
        _sample("s2", "新增 前端 React 组件 并补 单元测试", lang="typescript", ttype="feature"),
        _sample("s3", "重构 检索 向量 索引 逻辑", ttype="refactor"),
        _sample("s4", "未验证的样本 不应 默认 返回", verified="unverified"),
    ]
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    return str(p)


def test_sample_to_meta_has_loader_fields():
    """映射结果必须含 loader._meta_to_meta_text 认识的字段，否则 BM25 文档为空。"""
    m = _sample_to_meta(_sample("x", "任务文本"))
    for k in ("name", "description", "description_zh", "tags", "category"):
        assert k in m, k
    assert m["category"] == "experience"
    assert isinstance(m["tags"], list)


def test_load_counts(corpus):
    idx = ExperienceIndex(corpus, use_vector=False)
    assert idx.load() == 4
    assert len(idx._docs) == 4


def test_build_bm25_leg(corpus):
    idx = ExperienceIndex(corpus, use_vector=False)
    idx.load()
    st = idx.build()
    assert st["docs"] == 4
    assert st["bm25"] == 4, "BM25 腿应复用 BM25SkillSearcher 并建成索引"


def test_search_returns_scored_hits(corpus):
    idx = ExperienceIndex(corpus, use_vector=False)
    idx.load()
    idx.build()
    hits = idx.search("pytest 失败", top_k=3)
    assert hits, "应有命中"
    for h in hits:
        assert set(h) >= {"id", "score", "meta", "legs"}
        assert h["score"] > 0
        assert "bm25" in h["legs"]
    # 打分应随排名单调不增
    scores = [h["score"] for h in hits]
    assert scores == sorted(scores, reverse=True)


def test_unverified_excluded_by_default(corpus):
    """方案硬约束 ⑤：未验证的不入库（默认不返回）。"""
    idx = ExperienceIndex(corpus, use_vector=False)
    idx.load()
    idx.build()
    plain = idx.search("未验证", top_k=10)
    assert all(h["id"] != "s4" for h in plain)
    wide = idx.search("未验证", top_k=10, include_unverified=True)
    assert any(h["id"] == "s4" for h in wide)


def test_lang_and_task_type_filter(corpus):
    idx = ExperienceIndex(corpus, use_vector=False)
    idx.load()
    idx.build()
    for h in idx.search("组件", top_k=10, lang="typescript"):
        assert h["meta"]["_lang"] == "typescript"
    for h in idx.search("重构", top_k=10, task_type="refactor"):
        assert h["meta"]["_task_type"] == "refactor"


def test_missing_file_degrades(tmp_path):
    idx = ExperienceIndex(str(tmp_path / "nope.ndjson"), use_vector=False)
    assert idx.load() == 0
    st = idx.build()
    assert st["docs"] == 0
    assert idx.search("任意") == []


def test_empty_query_returns_empty(corpus):
    idx = ExperienceIndex(corpus, use_vector=False)
    idx.load()
    idx.build()
    assert idx.search("") == []
