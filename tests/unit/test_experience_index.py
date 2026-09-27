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


# ── 向量缓存（用假适配器，避免加载 4.25 GB 模型）──

class _FakeAdapter:
    """模拟 SkillVectorAdapter 与缓存相关的最小状态。"""

    def __init__(self, n, dim=4, ids=None):
        import numpy as np
        self._model = object()
        self._ids = list(ids) if ids else [f"d{i}" for i in range(n)]
        n = len(self._ids)
        self._st_backend = (self._model, list(self._ids),
                            np.ones((n, dim), dtype="float32"),
                            [{} for _ in range(n)])
        self._indexed_skill_ids = set()
        self._indexed_content_hash = {}
        self.ensure_calls = 0

    def _ensure_vector_store(self):
        return self._st_backend

    def ensure_indexed(self, force=False):
        self.ensure_calls += 1
        self._indexed_skill_ids = {i for i in self._st_backend[1]}
        return len(self._indexed_skill_ids)


def test_fingerprint_changes_with_docs(corpus, tmp_path):
    a = ExperienceIndex(corpus, use_vector=False); a.load()
    b = ExperienceIndex(corpus, use_vector=False); b.load()
    assert a._fingerprint() == b._fingerprint(), "同语料指纹应稳定"
    b._docs["s1"]["description"] = "改过的描述"
    assert a._fingerprint() != b._fingerprint(), "内容变化必须改变指纹"


def test_cache_roundtrip_and_stale(corpus, tmp_path):
    """缓存往返：命中则回灌状态；指纹不符则拒绝。"""
    idx = ExperienceIndex(corpus, persist_dir=str(tmp_path), use_vector=True)
    idx.load()

    # 无缓存 → 0
    assert idx._try_restore(_FakeAdapter(4)) == 0

    # 落缓存（id 必须与语料一致）
    real_ids = sorted(idx._docs)
    fa = _FakeAdapter(len(real_ids), ids=real_ids)
    fa._indexed_content_hash = {k: "h" for k in idx._docs}
    idx._save_cache(fa)
    assert os.path.isfile(os.path.join(str(tmp_path), "vectors.npz"))
    assert os.path.isfile(os.path.join(str(tmp_path), "content_hashes.json"))

    # 命中 → 回灌且短路（ensure_indexed 只被调用一次用于确认）
    fb = _FakeAdapter(len(real_ids), ids=real_ids)
    n = idx._try_restore(fb)
    assert n == len(real_ids), "应恢复全部条目"
    assert fb.ensure_calls == 1
    assert fb._indexed_skill_ids == set(real_ids)

    # 指纹变化 → 拒绝
    idx._docs["s1"]["description"] = "改了"
    assert idx._try_restore(_FakeAdapter(len(real_ids), ids=real_ids)) == 0


def test_cache_rejects_foreign_ids(corpus, tmp_path):
    """缓存的 id 不属于当前语料时必须拒绝（否则 _docs[i] 会 KeyError）。

    注意：id 来自【缓存 JSON】，不是来自适配器 —— 故必须直接伪造缓存文件。
    """
    import numpy as np
    idx = ExperienceIndex(corpus, persist_dir=str(tmp_path), use_vector=True)
    idx.load()
    n = len(idx._docs)
    np.savez_compressed(os.path.join(str(tmp_path), "vectors.npz"),
                        vectors=np.ones((n, 4), dtype="float32"))
    with open(os.path.join(str(tmp_path), "content_hashes.json"), "w", encoding="utf-8") as fh:
        json.dump({"fingerprint": idx._fingerprint(),
                   "ids": ["x1", "x2", "x3", "x4"][:n],
                   "content_hashes": {}}, fh, ensure_ascii=False)
    assert idx._try_restore(_FakeAdapter(n, ids=sorted(idx._docs))) == 0


def test_cache_rejects_corrupt_vectors(corpus, tmp_path):
    """向量行数与 id 数不符时必须拒绝，不能错位复用向量。"""
    import numpy as np
    idx = ExperienceIndex(corpus, persist_dir=str(tmp_path), use_vector=True)
    idx.load()
    real_ids = sorted(idx._docs)
    idx._save_cache(_FakeAdapter(len(real_ids), ids=real_ids))
    # 人为写坏：向量只留 3 行，但 JSON 仍记 4 个 id
    np.savez_compressed(os.path.join(str(tmp_path), "vectors.npz"),
                        vectors=np.ones((3, 4), dtype="float32"))
    assert idx._try_restore(_FakeAdapter(len(real_ids), ids=real_ids)) == 0
