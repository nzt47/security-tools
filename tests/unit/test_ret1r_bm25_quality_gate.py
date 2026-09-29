# -*- coding: utf-8 -*-
"""RET-1R 护栏 · R-1：启用 BM25 腿时，BM25 的证据必须能**影响最终结果**

背景（RET-1R 实测，见 docs/audit_skill_governance/RET1.md §2）:
    SkillLoader.match(..., use_bm25=True) 的中文命中是 **4/8**，而**只用 TF-IDF 是 8/8**
    —— 打开 BM25 这条腿反而更差。根因：RRF 质量闸只拿"有界相似度"
    （= tfidf_score = query-token 命中率）与 0.3 比；BM25 把正解顶到
    bm25_rank=1（rrf_normalized 0.9866）也**不参与**判定 ⇒ 整单 reject、返回 []。

本文件钉死四件事（全部用**真实检索结果**断言，不是断言配置）:
    1. 场景前置：合成语料上确实复现出"有界相似度 < 阈值、BM25 判得果断"的形态；
    2. 修好后：同一条 query 必须**真的返回**期望技能（改前返回 []）；
    3. 非空转：把新判据的常量抬到 ∞ ⇒ 立刻退回 []（证明是"BM25 证据"救的，
       不是别的改动顺手救的）；
    4. 不放松：BM25 腿"判得很平"（top1/top2 ≈ 1.0）的负样本**仍然**被拒；
       且判据常量与有界阈值都没有被改小（不是"调阈值"）。
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest
import yaml

from agent.skills_mgmt.file_store import SkillFileStore
from agent.skills_mgmt.loader import (
    SkillLoader,
    _BOUNDED_QUALITY_KEYS,
)

#: 正样本：长中文 query —— 命中率类指标天然偏低（11 个 bigram 里只命中 2 个）
POSITIVE_QUERY = "写测试时要避免哪些反模式"
POSITIVE_EXPECTED = "testing-anti-patterns"

#: 负样本：BM25 腿"判得很平"的无关 query（top1/top2 == 1.0）
FLAT_QUERY = "技能整理评估标准流程归档"

#: 【P0-1 · 2026-09-28】S10-03 的真机噪声 query（有界证据弱但非 None、BM25 果断、
#: 但命中 token 在真库里只覆盖 1 条技能）—— 用于锁"证据单点偶然不得作为 BM25 证据"。
S10_03_NOISE_QUERY = "2 加 3 等于多少？只回答数字"

#: 真技能库（**入库**，CI 干净检出上存在）——真库形态那几条用它，不依赖合成夹具
_REAL_REPO = Path(__file__).resolve().parents[2] / "data" / "skills_repo"

_SKILLS = [
    # 目标技能：文档里只含 query 的 2 个 bigram（反模/模式）⇒ 有界相似度 2/11 = 0.18 < 0.3
    {"id": "testing-anti-patterns", "name": "testing anti patterns",
     "description": "交付前请检查反模式", "category": "general",
     "tags": ["交付"], "version": "1.0.0", "enabled": True},
    # BM25 腿的第二名：只命中 query 的 1 个 bigram（测试），且文档更长 ⇒ 长度归一化扣分
    {"id": "echo-test", "name": "echo test",
     "description": "测试用例覆盖代码审查条目",
     "category": "general", "tags": ["审查"], "version": "1.0.0", "enabled": True},
    # 平局簇：4 条完全同形的无关技能（负样本的 BM25 分数完全相同 ⇒ 裕度 1.0）
    {"id": "decoy-alpha", "name": "alpha helper", "description": "通用技能：整理说明",
     "category": "general", "tags": ["通用"], "version": "1.0.0", "enabled": True},
    {"id": "decoy-beta", "name": "beta helper", "description": "通用技能：整理说明",
     "category": "general", "tags": ["通用"], "version": "1.0.0", "enabled": True},
    {"id": "decoy-gamma", "name": "gamma helper", "description": "通用技能：整理说明",
     "category": "general", "tags": ["通用"], "version": "1.0.0", "enabled": True},
    {"id": "decoy-delta", "name": "delta helper", "description": "通用技能：整理说明",
     "category": "general", "tags": ["通用"], "version": "1.0.0", "enabled": True},
]


@pytest.fixture
def file_store(tmp_path) -> SkillFileStore:
    repo = tmp_path / "skills_repo"
    repo.mkdir()
    for skill in _SKILLS:
        d = repo / skill["id"]
        d.mkdir(parents=True, exist_ok=True)
        block = yaml.safe_dump(skill, allow_unicode=True, sort_keys=False).strip()
        (d / "skill.md").write_text(
            "---\n%s\n---\n\n# %s\n\n%s\n"
            % (block, skill["name"], skill["description"]),
            encoding="utf-8",
        )
    return SkillFileStore(repo_path=str(repo))


@pytest.fixture
def loader(file_store) -> SkillLoader:
    return SkillLoader(file_store=file_store)


def _gate_records(caplog) -> list:
    """取生产日志里的 rrf.quality_gate.* 记录（dict 消息体）"""
    out = []
    for rec in caplog.records:
        msg = rec.msg
        if isinstance(msg, dict) and str(msg.get("action", "")).startswith("rrf.quality_gate"):
            out.append(msg)
    return out


def _run(caplog, loader, query, **kw):
    with caplog.at_level(logging.INFO, logger="agent.skills_mgmt"):
        caplog.clear()
        result = loader.match(query, top_k=5, use_bm25=True, **kw)
    return result, _gate_records(caplog)


# ═══════════════════════════════════════════════════════════════════
#  1. 场景前置 + 2. BM25 证据影响最终结果
# ═══════════════════════════════════════════════════════════════════

class TestBm25EvidenceReachesTheGate:

    def test_precondition_scenario_is_reproduced(self, loader, caplog):
        """前置：有界相似度 < 阈值，而 BM25 腿 rank1 且判得果断（新判据的适用形态）

        这条同时是"为什么不能靠调阈值"的证据：被拒的正样本有界相似度只有 0.18。
        """
        result, records = _run(caplog, loader, POSITIVE_QUERY)
        checks = [r for r in records if r["action"] == "rrf.quality_gate.check"]
        assert checks, "质量闸应当被触发（否则本测试没测到 R-1）"
        gate = checks[0]
        assert gate["bounded_similarity"] is not None
        assert gate["bounded_similarity"] < loader._RRF_QUALITY_MIN, (
            "前置不成立：本场景要求'有界相似度低于阈值'（R-1 的误拒形态），"
            f"实际 {gate['bounded_similarity']} vs {loader._RRF_QUALITY_MIN}"
        )
        assert gate["top1_skill_id"] == POSITIVE_EXPECTED, (
            f"前置不成立：融合 top1 应为 {POSITIVE_EXPECTED}，实际 {gate['top1_skill_id']}"
        )
        assert gate["bm25_rank1_evidence"] is True, (
            "BM25 腿必须被判为 rank1 果断证据（bm25_rank=1 且裕度 >= 常量）："
            f"ratio={gate.get('bm25_decision_ratio')} min={gate.get('bm25_decision_ratio_min')}"
        )
        assert gate["decision"] == "pass"
        # 真实结果：期望技能必须被返回（改前这里返回 []）
        assert [m.skill_id for m in result.matches][:1] == [POSITIVE_EXPECTED]

    def test_result_is_not_empty_when_bm25_leg_is_decisive(self, loader, caplog):
        """改前红：use_bm25=True 时整单被质量闸 reject ⇒ 返回 []（中文 4/8 的成因）"""
        result, _ = _run(caplog, loader, POSITIVE_QUERY)
        ids = [m.skill_id for m in result.matches]
        assert ids, (
            "启用 BM25 腿且该腿判得果断时，BM25 的证据必须能影响最终结果（不得整单 reject）"
        )
        assert ids[0] == POSITIVE_EXPECTED

    def test_without_the_escape_the_rejection_returns(self, loader, caplog, monkeypatch):
        """非空转自证（进程内）：把新判据的常量抬到 ∞ ⇒ 立刻退回 []（改前行为）"""
        monkeypatch.setattr(SkillLoader, "_RRF_QUALITY_BM25_DECISION_RATIO", 1e9)
        result, records = _run(caplog, loader, POSITIVE_QUERY)
        assert result.matches == [], (
            "常量被抬到 ∞ 时应当退回改前的拒绝行为（证明结果是被 BM25 判据救的）"
        )
        rejected = [r for r in records if r["action"] == "rrf.quality_gate.rejected"]
        assert rejected and rejected[0]["reason"] == "bounded_similarity_below_threshold"

    def test_removing_the_ratio_evidence_restores_the_rejection(self, loader, caplog, monkeypatch):
        """非空转自证（第二条路）：把裕度计算打桩为 None ⇒ 同样退回 []"""
        monkeypatch.setattr(
            SkillLoader, "_bm25_decision_ratio", staticmethod(lambda matches: None)
        )
        result, _ = _run(caplog, loader, POSITIVE_QUERY)
        assert result.matches == []


# ═══════════════════════════════════════════════════════════════════
#  3. 不放松：腿很平的负样本仍然被拒（假阳代价的守卫）
# ═══════════════════════════════════════════════════════════════════

class TestFlatBm25LegIsStillRejected:

    def test_flat_bm25_leg_is_not_evidence(self, loader, caplog):
        """BM25 腿 top1/top2 == 1.0（"判得很平"）⇒ 不构成证据 ⇒ 仍然整单拒绝"""
        result, records = _run(caplog, loader, FLAT_QUERY)
        checks = [r for r in records if r["action"] == "rrf.quality_gate.check"]
        assert checks, "前置：该 query 必须走到质量闸"
        gate = checks[0]
        # 行为断言（改前改后都必须成立）：整单拒绝 —— 这条守卫的是"新判据不许太松"
        assert result.matches == [], "腿很平的负样本不得因为新判据而被放行"
        assert gate.get("decision") == "reject"
        # 机制断言：该腿被判为**不构成**果断证据（改前没有这些字段 ⇒ get 默认值也成立）
        assert not gate.get("bm25_rank1_evidence")
        assert gate.get("bm25_decision_ratio", 0.0) < loader._RRF_QUALITY_BM25_DECISION_RATIO, (
            f"前置：该负样本的 BM25 裕度应当低于常量，实际 {gate.get('bm25_decision_ratio')}"
        )

    def test_rejection_is_the_gate_not_empty_candidates(self, loader, caplog):
        """对照：同一条负样本在 TF-IDF 单路上**有**候选 ⇒ 上面的 [] 是闸挡的，不是没候选"""
        single = loader.match(FLAT_QUERY, top_k=5, use_vector=False, use_bm25=False)
        assert single.matches, "前置：TF-IDF 单路对该 query 有候选（否则对照无意义）"
        result, _ = _run(caplog, loader, FLAT_QUERY)
        assert result.matches == []


# ═══════════════════════════════════════════════════════════════════
#  3b. 边界：**只有 BM25 命中**（有界腿全空）时不得放行
# ═══════════════════════════════════════════════════════════════════

class TestBm25AloneIsNotEnough:
    """BM25 只能**佐证**，不能单独成立 —— 本类锁的是这条属性（不是某个具体前置形态）。

    【P0-1 · 2026-09-28 机制换代说明（不是放宽守卫）】
      本类原用例的前置断言是 `assert gate["bounded_similarity"] is None`，其成立**完全依赖**
      「调用方的 min_score 被当作腿级过滤器」这一**量纲/职责混用**（原注释逐字写着：
      「min_score=0.3 ⇒ TF-IDF 腿的候选全被自己的阈值挡掉」）。P0-1 把腿级过滤改成召回地板
      `_RRF_LEG_MIN_SCORE` 之后，**这个前置形态在结构上不可能再出现**（腿不再被调用方阈值清空）
      ⇒ 断言必须换承重结构，否则它测的就不是属性、而是一个已消失的实现细节。

      属性本身**未放宽、反而更严**：现在要求"**即使**有界腿给出了相似度（非 None）**且**
      BM25 腿判得果断（裕度 ≥ 1.2），只要证据是**单点偶然**（命中 1 处且该处在语料里孤立），
      仍必须整单拒绝"。原用例只覆盖"有界腿全空"这一种更弱的前提。
      真实锚同形：S10-03 噪声「2 加 3 等于多少？只回答数字」在真库上 bounded=0.1（非 None）、
      BM25 裕度 1.543 ≥ 1.2、命中 token 只有「回答」且只覆盖 1 条技能 ⇒ 必须拒绝。
    """

    @pytest.mark.skipif(not _REAL_REPO.is_dir(), reason="真技能库 data/skills_repo 缺失")
    def test_bm25_only_evidence_is_not_enough(self, caplog):
        """真库形态：有界相似度低但非 None + BM25 果断 + **证据单点偶然** ⇒ 仍然拒绝"""
        from agent.skills_mgmt.loader import SkillLoader as _SL
        ldr = _SL(file_store=SkillFileStore(repo_path=str(_REAL_REPO)))
        with caplog.at_level(logging.INFO, logger="agent.skills_mgmt"):
            caplog.clear()
            result = ldr.match(S10_03_NOISE_QUERY, top_k=5, enabled_only=True,
                               min_score=0.3, use_bm25=True, fusion_mode="rrf")
        checks = [r for r in _gate_records(caplog)
                  if r["action"] == "rrf.quality_gate.check"]
        assert checks, "前置：该 query 必须走到质量闸"
        gate = checks[0]
        assert gate["bounded_similarity"] is not None, (
            "前置（P0-1 后的新形态）：噪声也有有界证据（腿不再被调用方阈值清空），"
            f"实际 {gate['bounded_similarity']}"
        )
        assert gate["bounded_similarity"] < ldr._RRF_QUALITY_MIN, (
            f"前置：有界相似度必须低于阈值，实际 {gate['bounded_similarity']}"
        )
        assert gate["bm25_decision_ratio"] >= ldr._RRF_QUALITY_BM25_DECISION_RATIO, (
            "前置：BM25 腿必须判得果断（否则测不到「只靠 BM25」这条边界），"
            f"实际 {gate['bm25_decision_ratio']}"
        )
        assert gate["in_domain"] is False, (
            "本场景的证据必须是**单点偶然**（命中 1 处且该处在语料里孤立）："
            f"reach={gate.get('evidence_reach')} tokens={gate.get('evidence_tokens')}"
        )
        assert gate["bm25_rank1_evidence"] is False, (
            "单点偶然的证据不得让 BM25 单独构成证据（否则量纲混用陷阱复发）"
        )
        assert gate["decision"] == "reject"
        assert result.matches == [], "只有 BM25 命中且证据孤立的查询不得放行"


# ═══════════════════════════════════════════════════════════════════
#  4. 判据本身：无量纲 + 未改阈值
# ═══════════════════════════════════════════════════════════════════

class TestCriterionIsDimensionlessAndThresholdUntouched:

    class _M:
        def __init__(self, score):
            self.score = score

    def test_ratio_is_scale_invariant(self):
        """判决裕度与分数**量纲无关**：同比例放大不改变判据（不是拿无界分比阈值）"""
        r1 = SkillLoader._bm25_decision_ratio([self._M(10.0), self._M(5.0)])
        r2 = SkillLoader._bm25_decision_ratio([self._M(2000.0), self._M(1000.0)])
        assert r1 == pytest.approx(2.0)
        assert r2 == pytest.approx(2.0)

    def test_ratio_needs_two_candidates(self):
        """该腿只有 1 条候选时不构成"果断"证据（返回 None ⇒ 不放行）"""
        assert SkillLoader._bm25_decision_ratio([self._M(9.9)]) is None
        assert SkillLoader._bm25_decision_ratio([]) is None
        assert SkillLoader._bm25_decision_ratio(None) is None

    def test_bounded_threshold_and_keys_are_unchanged(self):
        """次级守卫：本次不是"把阈值调小"，也不是"往有界键里塞无界分" """
        assert SkillLoader._RRF_QUALITY_MIN == 0.3, "有界相似度阈值不得被改动"
        assert _BOUNDED_QUALITY_KEYS == ("tfidf_score", "vector_score", "rerank_score"), (
            "有界键集合不得被改动（bm25_score 无界，不能进这个集合）"
        )
        assert SkillLoader._RRF_QUALITY_BM25_DECISION_RATIO > 1.0, (
            "新判据的常量必须是 >1 的比值（1.0 等于'腿是平的'也放行）"
        )
