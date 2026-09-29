# -*- coding: utf-8 -*-
"""P0-1 收尾：**向量腿预热**入口与两条开关的契约（2026-09-29）

【为什么需要这份守卫】生产的语义层长期跑在 tfidf+bm25 **降级态**：`SkillLoader` 默认不初始化
向量后端，而它的两处 fast-exit（`_try_vector_match` / `_try_rrf_match`）在"后端未初始化"时
**静默跳过**向量腿。本轮补了显式预热入口（`SkillLoader.warm_vector_leg` /
`SkillsMgmtService.warm_vector_leg`，由 `app_server` 启动期在**后台线程**调用）。

本文件锁三件事：
  1. `available` 用的是与腿**完全同一个判据**（后端非 None），不是"我调过 init 了"；
  2. 预热**永不抛异常**（它在启动路径上，挂掉就等于服务起不来）；
  3. 两条开关被否决时**不触碰适配器** —— 这是 CI 不加载 BGE-m3 的保证（实测加载 87.7s）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent.skills_mgmt.loader import SkillLoader


class _FakeAdapter:
    """最小向量适配器替身：只暴露预热路径真正读的那些属性"""

    def __init__(self, *, st_backend=None, native=None, indexed=0, boom=False,
                 degraded=False, degrade_reason=""):
        self._st_backend = st_backend
        self._native_chroma = native
        self._indexed = indexed
        self._boom = boom
        self._active_backend_degraded = degraded
        self._active_degrade_reason = degrade_reason
        self.ensure_calls = []

    def ensure_indexed(self, *, force=False):
        self.ensure_calls.append(bool(force))
        if self._boom:
            raise RuntimeError("模拟后端初始化炸掉")
        return self._indexed


def _loader(adapter):
    return SkillLoader(file_store=None, vector_adapter=adapter)


class TestWarmVectorLegReportsTruthfully:

    def test_后端已就绪_报告在线且给出后端名与索引数(self):
        ad = _FakeAdapter(st_backend=("model", [], [], []), indexed=28)
        st = _loader(ad).warm_vector_leg()
        assert st["available"] is True, st
        assert st["backend"] == "sentence_transformers", st
        assert st["indexed"] == 28, st
        assert ad.ensure_calls == [False], "预热必须真的调过一次 ensure_indexed"

    def test_只有原生chroma后端_也算在线(self):
        st = _loader(_FakeAdapter(native=("client", "coll"))).warm_vector_leg()
        assert st["available"] is True and st["backend"] == "native_chroma", st

    def test_两个后端都没有_报告未在线并给出原因(self):
        st = _loader(_FakeAdapter(indexed=0)).warm_vector_leg()
        assert st["available"] is False, st
        assert st["reason"] == "no_backend", st

    def test_降级后端_如实透出degraded与原因(self):
        ad = _FakeAdapter(native=("c", "x"), degraded=True, degrade_reason="st_failed")
        st = _loader(ad).warm_vector_leg()
        assert st["available"] is True and st["degraded"] is True, st
        assert st["reason"] == "st_failed", st

    def test_适配器炸掉_也不抛异常(self):
        st = _loader(_FakeAdapter(boom=True)).warm_vector_leg()
        assert st["available"] is False, st
        assert str(st["reason"]).startswith("ensure_indexed_failed"), st

    def test_没有适配器_报告不可用(self):
        ld = SkillLoader(file_store=None, vector_adapter=None)
        ld._get_vector_adapter = lambda: None   # 模拟"造不出适配器"
        st = ld.warm_vector_leg()
        assert st["available"] is False and st["reason"] == "adapter_unavailable", st


class TestPrewarmSwitches:
    """两条开关：`SKILLS_OFFLINE`（CI 一直在设，此前**没有代码读**）与 `CP_SKILL_VECTOR_PREWARM`"""

    @pytest.fixture()
    def service_with_spy(self, tmp_path):
        from agent.skills_mgmt.service import SkillsMgmtService

        (tmp_path / "skills_repo").mkdir()
        svc = SkillsMgmtService(
            store_path=str(tmp_path / "skills_mgmt.json"),
            repo_path=str(tmp_path / "skills_repo"),
            class_registry_path=str(tmp_path / "skills_classes.json"))
        ad = _FakeAdapter(st_backend=("model", [], [], []), indexed=3)
        svc.loader._vector_adapter = ad
        return svc, ad

    def test_默认开_会真的去预热(self, service_with_spy, monkeypatch):
        svc, ad = service_with_spy
        monkeypatch.delenv("SKILLS_OFFLINE", raising=False)
        monkeypatch.delenv("CP_SKILL_VECTOR_PREWARM", raising=False)
        st = svc.warm_vector_leg()
        assert st["available"] is True, st
        assert ad.ensure_calls == [False], "默认开 ⇒ 应当真的预热"

    def test_SKILLS_OFFLINE否决_且不触碰适配器(self, service_with_spy, monkeypatch):
        svc, ad = service_with_spy
        monkeypatch.setenv("SKILLS_OFFLINE", "1")
        st = svc.warm_vector_leg()
        assert st["reason"] == "disabled:SKILLS_OFFLINE", st
        assert ad.ensure_calls == [], "被否决时**不得**触碰适配器（CI 不加载模型靠它）"

    def test_预热开关置零_且不触碰适配器(self, service_with_spy, monkeypatch):
        svc, ad = service_with_spy
        monkeypatch.delenv("SKILLS_OFFLINE", raising=False)
        monkeypatch.setenv("CP_SKILL_VECTOR_PREWARM", "0")
        st = svc.warm_vector_leg()
        assert st["reason"] == "disabled:CP_SKILL_VECTOR_PREWARM=0", st
        assert ad.ensure_calls == [], "被否决时不得触碰适配器"

    def test_开关取值宽容_大小写与空格都认(self, service_with_spy, monkeypatch):
        svc, ad = service_with_spy
        monkeypatch.setenv("SKILLS_OFFLINE", " TRUE ")
        assert svc.warm_vector_leg()["reason"] == "disabled:SKILLS_OFFLINE"
        monkeypatch.delenv("SKILLS_OFFLINE")
        monkeypatch.setenv("CP_SKILL_VECTOR_PREWARM", "Off")
        assert svc.warm_vector_leg()["reason"] == "disabled:CP_SKILL_VECTOR_PREWARM=0"


class TestAppServerStartupWiring:
    """启动路径的接线（静态断言，不真起服务）：预热必须是**后台线程**且**不阻塞就绪门**"""

    def test_app_server_启动期调用预热且放在后台线程(self):
        src = (Path(__file__).resolve().parents[2] / "app_server.py").read_text(encoding="utf-8")
        assert "_warm_skill_vector_leg_async()" in src, "启动路径必须调用预热"
        seg = src[src.index("def _warm_skill_vector_leg_async"):]
        seg = seg[:seg.index("if __name__")] if "if __name__" in seg else seg
        assert "threading.Thread(" in seg and "daemon=True" in seg, (
            "预热必须在**后台 daemon 线程**里跑：BGE-m3 实测加载 87.7s，卡住就等于服务起不来")

    def test_预热调用在就绪门之前_但不参与其判定(self):
        src = (Path(__file__).resolve().parents[2] / "app_server.py").read_text(encoding="utf-8")
        i_warm = src.index("_warm_skill_vector_leg_async()")
        i_gate = src.index("from agent.server_port_guard import guarded_startup")
        assert i_warm < i_gate, "预热应在就绪门之前启动（但因为是后台线程，不影响门）"

# ═══════════════════════════════════════════════════════════════════
#  向量腿**在线**时的判据：按来源分档（0.45 / 0.3）
# ═══════════════════════════════════════════════════════════════════
# 为什么这份锁必需：向量腿一旦真的在线（本轮的预热就是干这个），闸第一次同时拿到
# `tfidf_score` 与 `vector_score`。实测（真 BGE-m3，28 技能）：
#   · S10-03 噪声 query 的 top1 breakdown = {tfidf 0.1, vector 0.3528, bm25 4.6614, bm25_rank 1}
#     ⇒ 拿统一的 0.3 去比 ⇒ 0.3528 ≥ 0.3 ⇒ 过闸、**锚红**（实测 5 条假阳）；
#   · 而 16 条正样本的 vector_score 实测 0.5195~0.6876 —— **全部 ≥0.45**。
# ⇒ 判据按来源分档：vector 用 GATE-1 标定的 `_SINGLE_PATH_MIN_TOP1=0.45`，
#   tfidf/rerank 用 `_RRF_QUALITY_MIN=0.3`。下面的用例用**实测数值**复现这个形态，
#   不需要模型 ⇒ CI 上也能跑（真机复算见交付报告 §13）。

NOISE_BREAKDOWN = {"tfidf_rank": 2, "vector_rank": 6, "bm25_rank": 1,
                   "tfidf_score": 0.1, "vector_score": 0.3528, "bm25_score": 4.6614,
                   "rrf_score": 0.0158, "rrf_normalized": 0.9626}
GENUINE_BREAKDOWN = {"tfidf_rank": 4, "vector_rank": 1, "bm25_rank": 1,
                     "tfidf_score": 0.0909, "vector_score": 0.6351, "bm25_score": 2.5441,
                     "rrf_score": 0.0163, "rrf_normalized": 0.9928}


def _mk_match(skill_id, score, breakdown=None):
    from agent.skills_mgmt.loader import SkillMatch
    return SkillMatch(skill_id=skill_id, name=skill_id, description="", score=score,
                      estimated_tokens=10, category="test", tags=[], version="1.0.0",
                      enabled=True, score_breakdown=dict(breakdown) if breakdown else None)


def _loader_with_legs(tmp_path, *, vector_score, tfidf_score):
    """两条腿都用固定输入（含真机实测的 breakdown），向量腿**假装在线**"""
    from agent.skills_mgmt.file_store import SkillFileStore

    d = tmp_path / "skills_repo" / "self_reflection"
    d.mkdir(parents=True)
    (d / "skill.md").write_text(
        "---\nid: self_reflection\nname: self_reflection\ndescription: 测试技能\n"
        "category: meta\ntags: [测试]\nversion: 1.0.0\nenabled: true\n---\n\n正文\n",
        encoding="utf-8")

    class _FakeVectorAdapter:
        _st_backend = ("model", [], [], [])      # 非 None ⇒ 不触发 fast-exit
        _native_chroma = None

        def search(self, intent, top_k=5, enabled_only=True, min_score=0.0):
            return [{"skill_id": "self_reflection", "score": vector_score}]

    ld = SkillLoader(file_store=SkillFileStore(repo_path=str(tmp_path / "skills_repo")),
                     vector_adapter=_FakeVectorAdapter())
    ld._tfidf_scan = lambda **kw: [_mk_match("self_reflection", tfidf_score)]
    ld._try_bm25_match = lambda **kw: [_mk_match("self_reflection", 4.6614),
                                        _mk_match("other", 3.0204)]
    return ld


class TestBoundedEvidenceIsThresholdedBySource:

    def test_向量证据低于向量腿标定门槛_必须拒绝(self, tmp_path):
        """真机形态：噪声 query（vector 0.3528 / tfidf 0.1 / bm25 rank1）⇒ 必须空"""
        ld = _loader_with_legs(tmp_path, vector_score=0.3528, tfidf_score=0.1)
        res = ld.match("2 加 3 等于多少？只回答数字", top_k=5, enabled_only=True,
                       min_score=0.3, use_vector=True, use_bm25=True, fusion_mode="rrf")
        assert res.matches == [], (
            "向量 0.3528 低于向量腿自己的标定门槛 0.45 时不得过闸（改前用统一的 0.3 ⇒ 会放行）")

    def test_向量证据达到向量腿标定门槛_正常放行(self, tmp_path):
        """同一形态、只把 vector 换成真阳性实测值 0.6351 ⇒ 必须过闸（证明上一条不是空转）"""
        ld = _loader_with_legs(tmp_path, vector_score=0.6351, tfidf_score=0.0909)
        res = ld.match("写测试时要避免哪些反模式", top_k=5, enabled_only=True,
                       min_score=0.3, use_vector=True, use_bm25=True, fusion_mode="rrf")
        assert [m.skill_id for m in res.matches][:1] == ["self_reflection"]
        assert res.quality_gate == "bounded_similarity", res.quality_gate

    def test_分档判据本身_按来源取不同门槛(self):
        ld = SkillLoader(file_store=None, vector_adapter=None)
        assert ld._bounded_keys_passing({"vector_score": 0.44}) == [], "向量 0.44 < 0.45 ⇒ 不过"
        assert ld._bounded_keys_passing({"vector_score": 0.45}) == ["vector_score"]
        assert ld._bounded_keys_passing({"tfidf_score": 0.31}) == ["tfidf_score"], "tfidf 仍是 0.3 的线"
        assert ld._bounded_keys_passing({"tfidf_score": 0.29}) == []
        assert ld._bounded_keys_passing({"rerank_score": 0.35}) == ["rerank_score"]
        assert ld._bounded_keys_passing({"vector_score": True}) == [], "bool 不得当 1.0 放行"
        assert ld._bounded_keys_passing(None) == []

    def test_三条门槛常量_未被悄悄改动(self):
        """有界 0.3（tfidf 标定）/ 0.45（单向量路标定）/ 腿级地板 0.01 都必须仍是标定值"""
        assert SkillLoader._RRF_QUALITY_MIN == 0.3
        assert SkillLoader._SINGLE_PATH_MIN_TOP1 == 0.45
        assert SkillLoader._RRF_LEG_MIN_SCORE == 0.01
