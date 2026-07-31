"""技能 BM25 检索单元测试 — 守卫 BM25SkillSearcher 核心契约与三路融合

覆盖任务要求的 5 个用例:
1. test_bm25_build_index — 索引构建成功
2. test_bm25_search_exact_match — 精确匹配技能标题时 BM25 得分最高
3. test_bm25_search_partial_match — 部分匹配场景
4. test_loader_match_fuses_three_paths — 三路融合结果正确
5. test_bm25_unavailable_falls_back_to_two_paths — rank_bm25 未安装时降级两路

设计:
- 复用 test_vector_skill_searcher.py 的 _TEST_SKILLS / skills_repo / file_store fixture 模式
- FakeVectorAdapter 模拟向量路（避免加载真 BGE-m3 模型），让三路融合可测
- monkeypatch _RANK_BM25_AVAILABLE 模拟 rank_bm25 未安装降级

【不易】守 BM25SkillSearcher 公开接口契约：build_index/search/is_available
【变易】FakeVectorAdapter 模拟向量路，三路融合可独立验证
【简易】最小测试集，每用例聚焦一个契约
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agent.skills_mgmt.bm25_searcher import BM25SkillSearcher, BM25Match
from agent.skills_mgmt.file_store import SkillFileStore
from agent.skills_mgmt.loader import SkillLoader, SkillMatch


# ═══════════════════════════════════════════════════════════════════
#  测试技能数据（与 test_vector_skill_searcher.py 同源，保证三路同尺度）
# ═══════════════════════════════════════════════════════════════════

_TEST_SKILLS = [
    {
        "id": "pdf_parser",
        "name": "PDF解析",
        "description": "解析PDF文件内容，提取文本和表格",
        "category": "document",
        "tags": ["pdf", "parse"],
        "version": "1.0.0",
        "enabled": True,
    },
    {
        "id": "self_reflection",
        "name": "自我反思",
        "description": "主动检查回答合理性，反思并改进",
        "category": "meta",
        "tags": ["反思", "improve"],
        "version": "1.0.0",
        "enabled": True,
    },
    {
        "id": "memory_summary",
        "name": "记忆总结",
        "description": "总结对话记忆，压缩历史信息",
        "category": "memory",
        "tags": ["总结", "记忆"],
        "version": "1.0.0",
        "enabled": True,
    },
]


def _write_skill_md(repo: Path, skill: dict) -> None:
    """写入单个 skill.md（YAML front matter + body）"""
    skill_dir = repo / skill["id"]
    skill_dir.mkdir(parents=True, exist_ok=True)
    yaml_block = yaml.safe_dump(
        skill, allow_unicode=True, default_flow_style=False, sort_keys=False,
    ).strip()
    body = f"# {skill['name']}\n\n{skill['description']}技能使用说明。"
    md = f"---\n{yaml_block}\n---\n\n{body}"
    (skill_dir / "skill.md").write_text(md, encoding="utf-8")


@pytest.fixture
def skills_repo(tmp_path) -> Path:
    """创建临时 skills_repo 并写入 3 个测试技能"""
    repo = tmp_path / "skills_repo"
    repo.mkdir()
    for skill in _TEST_SKILLS:
        _write_skill_md(repo, skill)
    return repo


@pytest.fixture
def file_store(skills_repo) -> SkillFileStore:
    return SkillFileStore(repo_path=str(skills_repo))


@pytest.fixture
def bm25_searcher(file_store) -> BM25SkillSearcher:
    """构建好索引的 BM25SkillSearcher"""
    index = file_store.load_metadata_index()
    skills = []
    for skill_id, meta in index.items():
        meta_with_id = dict(meta)
        meta_with_id.setdefault("id", skill_id)
        skills.append(meta_with_id)
    s = BM25SkillSearcher()
    s.build_index(skills)
    return s


# ═══════════════════════════════════════════════════════════════════
#  FakeVectorAdapter — 模拟向量路（避免加载真 BGE-m3 模型）
# ═══════════════════════════════════════════════════════════════════

class FakeVectorAdapter:
    """模拟向量适配器 — 基于关键词重叠返回伪向量匹配

    让三路融合测试无需加载真模型。score 范围 0.6~0.8（高于 min_score），
    模拟 BGE-m3 语义检索的相似度量级。

    【变易】关键词列表可扩展，模拟不同领域语义匹配
    【简易】关键词重叠度作为相似度，足够测试三路融合契约
    """

    _KEYWORDS = ["pdf", "反思", "总结", "记忆"]

    def __init__(self, file_store: SkillFileStore):
        self.fs = file_store
        # 非 None 标识，避免 _try_vector_match 的 BM25-fallback 检测误判
        # （_try_rrf_match 不检查此字段，但保持一致以防路由变更）
        self._st_backend = "fake"
        self._native_chroma = None

    def search(self, intent: str, top_k: int = 5,
               enabled_only: bool = True, min_score: float = 0.0):
        intent_lower = (intent or "").lower()
        index = self.fs.load_metadata_index()
        results = []
        for skill_id, meta in index.items():
            if enabled_only and not meta.get("enabled", True):
                continue
            text = (
                meta.get("name", "") + meta.get("description", "")
                + " ".join(meta.get("tags", []) or [])
            ).lower()
            overlap = sum(1 for kw in self._KEYWORDS if kw in intent_lower and kw in text)
            if overlap == 0:
                continue
            score = 0.6 + 0.1 * overlap  # 0.7~1.0
            if score < min_score:
                continue
            results.append({"skill_id": skill_id, "score": score, "metadata": meta})
        # 按 score 降序
        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:top_k]

    @property
    def is_available(self) -> bool:
        return True


# ═══════════════════════════════════════════════════════════════════
#  测试用例 — 守卫任务要求的 5 个契约
# ═══════════════════════════════════════════════════════════════════

class TestBM25SkillSearcher:
    """BM25SkillSearcher 核心契约测试"""

    def test_bm25_build_index(self, bm25_searcher):
        """用例1: 索引构建成功 — is_available=True 且索引 3 个技能"""
        s = bm25_searcher
        assert s.is_available() is True, "构建后 is_available 应为 True"
        assert len(s._skill_ids) == 3, (
            f"应索引 3 个技能，实际 {len(s._skill_ids)}"
        )
        # 三个技能 id 都应在索引中
        assert set(s._skill_ids) == {"pdf_parser", "self_reflection", "memory_summary"}, (
            f"索引技能 id 不符，实际 {s._skill_ids}"
        )
        # 每个文档都应已分词
        assert len(s._tokenized_docs) == 3
        assert all(len(tokens) > 0 for tokens in s._tokenized_docs), (
            "每个文档应至少有一个 token"
        )

    def test_bm25_search_exact_match(self, bm25_searcher):
        """用例2: 精确匹配技能标题时 BM25 得分最高

        query="PDF解析" 完全匹配 pdf_parser 的 name，
        pdf_parser 应排第一且得分严格高于其他技能。
        """
        s = bm25_searcher
        results = s.search("PDF解析", top_k=3)

        assert isinstance(results, list)
        assert len(results) > 0, "精确匹配应返回结果"
        # 每项结构校验
        for r in results:
            assert isinstance(r, BM25Match)
            assert r.skill_id
            assert r.score > 0, f"BM25 得分应 > 0，实际 {r.score}"
        # pdf_parser 应排第一（标题完全匹配，BM25 得分最高）
        assert results[0].skill_id == "pdf_parser", (
            f"精确匹配 pdf_parser 应排第一，实际 top1={results[0].skill_id}"
        )
        # 若有多个结果，top1 得分应严格最高
        if len(results) > 1:
            assert results[0].score > results[1].score, (
                f"top1 得分应严格高于 top2，"
                f"{results[0].score} vs {results[1].score}"
            )

    def test_bm25_search_partial_match(self, bm25_searcher):
        """用例3: 部分匹配场景 — query 仅含部分词项仍能召回

        query="PDF" 只匹配 pdf_parser 的 tags/name 中的 "pdf" 词项，
        应召回 pdf_parser；query="反思" 应召回 self_reflection。
        """
        s = bm25_searcher

        # "PDF" 部分匹配 pdf_parser
        results_pdf = s.search("PDF", top_k=3)
        pdf_ids = [r.skill_id for r in results_pdf]
        assert "pdf_parser" in pdf_ids, (
            f"query='PDF' 应召回 pdf_parser，实际 {pdf_ids}"
        )

        # "反思" 部分匹配 self_reflection
        results_reflect = s.search("反思", top_k=3)
        reflect_ids = [r.skill_id for r in results_reflect]
        assert "self_reflection" in reflect_ids, (
            f"query='反思' 应召回 self_reflection，实际 {reflect_ids}"
        )

        # 无任何词项命中的 query 应返回空列表（守防御性要求：零分不召回）
        empty_results = s.search("xyz_not_exist", top_k=3)
        assert empty_results == [], (
            f"无匹配 query 应返回空列表，实际 {empty_results}"
        )

    def test_bm25_search_empty_index(self):
        """补充: 索引为空时返回空列表（不报错，守防御性要求）"""
        s = BM25SkillSearcher()
        # 未构建索引
        assert s.is_available() is False
        assert s.search("任意查询", top_k=3) == [], "索引为空应返回空列表"
        # 构建空索引
        s.build_index([])
        assert s.is_available() is False, "空技能列表构建后应不可用"
        assert s.search("任意查询", top_k=3) == [], "空索引检索应返回空列表"

    def test_bm25_search_empty_query(self, bm25_searcher):
        """补充: 空 query 返回空列表（不报错）"""
        s = bm25_searcher
        assert s.search("", top_k=3) == [], "空 query 应返回空列表"
        assert s.search("   ", top_k=3) == [], "空白 query 应返回空列表"


class TestLoaderThreePathFusion:
    """三路融合（tfidf+vector+bm25）契约测试"""

    def test_loader_match_fuses_three_paths(self, file_store):
        """用例4: 三路融合结果正确 — use_bm25=True + use_vector=True

        场景: query="PDF解析文件"，三路都应召回 pdf_parser
        预期:
            - retrieval_method == "rrf"（三路加权融合）
            - fallback_used == False
            - pdf_parser 在结果中且排前列
            - score_breakdown 包含 bm25_rank（证明 BM25 路参与融合）
        """
        fake_adapter = FakeVectorAdapter(file_store)
        loader = SkillLoader(file_store=file_store, vector_adapter=fake_adapter)

        result = loader.match(
            "PDF解析文件", top_k=3,
            use_vector=True, use_bm25=True,
        )

        assert result.retrieval_method == "rrf", (
            f"三路融合应 retrieval_method='rrf'，实际 {result.retrieval_method}"
        )
        assert result.fallback_used is False, "三路可用时不应 fallback"
        assert len(result.matches) > 0, "三路融合应返回结果"

        # pdf_parser 应在结果中（三路都匹配它）
        match_ids = [m.skill_id for m in result.matches]
        assert "pdf_parser" in match_ids, (
            f"pdf_parser 应在三路融合结果中，实际 {match_ids}"
        )

        # 验证 BM25 路参与了融合（score_breakdown 含 bm25_rank）
        pdf_match = next(m for m in result.matches if m.skill_id == "pdf_parser")
        assert pdf_match.score_breakdown is not None, "应有 score_breakdown"
        assert "bm25_rank" in pdf_match.score_breakdown, (
            f"score_breakdown 应含 bm25_rank，实际 {pdf_match.score_breakdown}"
        )
        # pdf_parser 在 BM25 路应命中（bm25_rank 非 None）
        assert pdf_match.score_breakdown["bm25_rank"] is not None, (
            f"pdf_parser 在 BM25 路应命中，bm25_rank 不应为 None，"
            f"实际 {pdf_match.score_breakdown}"
        )
        # 向量路也应命中
        assert pdf_match.score_breakdown.get("vector_rank") is not None, (
            f"pdf_parser 在向量路应命中，实际 {pdf_match.score_breakdown}"
        )

    def test_loader_match_bm25_only_without_vector(self, file_store):
        """补充: use_bm25=True + use_vector=False → tfidf+bm25 两路融合

        验证 BM25 可独立于 vector 工作（向量适配器未注入）。
        """
        # 不注入 vector_adapter，_get_vector_adapter 会尝试创建真适配器
        # 但 _try_rrf_match 中 vector 路不可用 + 有 bm25 兜底 → 走 tfidf+bm25
        # 为避免真模型加载，注入一个返回空的 adapter
        class EmptyAdapter:
            _st_backend = None
            _native_chroma = None

            def search(self, *args, **kwargs):
                return []

            @property
            def is_available(self):
                return False

        loader = SkillLoader(file_store=file_store, vector_adapter=EmptyAdapter())
        result = loader.match(
            "PDF解析", top_k=3,
            use_vector=True, use_bm25=True,
        )

        # 向量路空 + bm25 兜底 → tfidf+bm25 融合
        assert result.retrieval_method == "rrf", (
            f"tfidf+bm25 融合应 retrieval_method='rrf'，实际 {result.retrieval_method}"
        )
        assert len(result.matches) > 0, "tfidf+bm25 应返回结果"
        match_ids = [m.skill_id for m in result.matches]
        assert "pdf_parser" in match_ids, (
            f"pdf_parser 应在结果中，实际 {match_ids}"
        )

    def test_loader_match_custom_weights(self, file_store):
        """补充: 自定义权重生效 — 提高 bm25 权重让精确匹配优先

        场景: query="PDF解析"，bm25 权重提到 0.8，tfidf/vector 降到 0.1
        预期: pdf_parser 仍排第一（三路都匹配），且 rrf_score 受权重影响
        """
        fake_adapter = FakeVectorAdapter(file_store)
        loader = SkillLoader(file_store=file_store, vector_adapter=fake_adapter)

        result = loader.match(
            "PDF解析", top_k=3,
            use_vector=True, use_bm25=True,
            retrieval_weights={"tfidf": 0.1, "vector": 0.1, "bm25": 0.8},
        )

        assert result.retrieval_method == "rrf"
        assert len(result.matches) > 0
        assert result.matches[0].skill_id == "pdf_parser", (
            f"pdf_parser 应排第一，实际 top1={result.matches[0].skill_id}"
        )


class TestBM25Fallback:
    """BM25 降级契约测试"""

    def test_bm25_unavailable_falls_back_to_two_paths(
        self, file_store, monkeypatch,
    ):
        """用例5: rank_bm25 未安装时降级为 tfidf+vector 两路融合

        模拟 rank_bm25 不可用（monkeypatch _RANK_BM25_AVAILABLE=False），
        验证 use_bm25=True 时不阻塞，仍返回 tfidf+vector 融合结果。

        预期:
            - retrieval_method == "rrf"（仍走加权融合，bm25 路空被过滤）
            - 结果非空（tfidf+vector 两路工作）
            - score_breakdown 中 bm25_rank 为 None（BM25 路未贡献）
        """
        # 模拟 rank_bm25 未安装
        import agent.skills_mgmt.bm25_searcher as bm25_mod
        monkeypatch.setattr(bm25_mod, "_RANK_BM25_AVAILABLE", False)

        fake_adapter = FakeVectorAdapter(file_store)
        loader = SkillLoader(file_store=file_store, vector_adapter=fake_adapter)

        result = loader.match(
            "PDF解析", top_k=3,
            use_vector=True, use_bm25=True,
        )

        # 降级到 tfidf+vector 两路融合，仍返回结果
        assert result.retrieval_method == "rrf", (
            f"降级后仍应 retrieval_method='rrf'，实际 {result.retrieval_method}"
        )
        assert len(result.matches) > 0, (
            "BM25 不可用时 tfidf+vector 应仍返回结果"
        )

        # pdf_parser 应在结果中（tfidf+vector 都匹配）
        match_ids = [m.skill_id for m in result.matches]
        assert "pdf_parser" in match_ids, (
            f"pdf_parser 应在降级结果中，实际 {match_ids}"
        )

        # BM25 路未贡献：bm25_rank 应为 None
        pdf_match = next(m for m in result.matches if m.skill_id == "pdf_parser")
        assert pdf_match.score_breakdown is not None
        assert pdf_match.score_breakdown.get("bm25_rank") is None, (
            f"BM25 不可用时 bm25_rank 应为 None，实际 "
            f"{pdf_match.score_breakdown.get('bm25_rank')}"
        )

    def test_rank_bm25_not_installed_returns_empty(self, monkeypatch):
        """补充: rank_bm25 未安装时 BM25SkillSearcher.is_available()=False

        守【不易】纯 Python 依赖降级：rank_bm25 是可选依赖，未安装不报错。
        """
        import agent.skills_mgmt.bm25_searcher as bm25_mod
        monkeypatch.setattr(bm25_mod, "_RANK_BM25_AVAILABLE", False)

        s = BM25SkillSearcher()
        s.build_index([
            {"id": "test_skill", "name": "测试", "description": "测试技能",
             "tags": [], "category": ""},
        ])
        assert s.is_available() is False, (
            "rank_bm25 未安装时 is_available 应为 False"
        )
        assert s.search("测试", top_k=3) == [], "不可用时 search 应返回空列表"


# ═══════════════════════════════════════════════════════════════════
#  _rrf_fuse_weighted 权重归一化与 max_possible 上界回归测试
# ═══════════════════════════════════════════════════════════════════

def _mk_match(skill_id: str, score: float = 0.5) -> SkillMatch:
    """构造最小 SkillMatch（仅供 _rrf_fuse_weighted 单元测试）"""
    return SkillMatch(
        skill_id=skill_id,
        name=skill_id,
        description="",
        score=score,
        estimated_tokens=10,
        category="",
        tags=[],
        version="1.0.0",
        enabled=True,
        score_breakdown={},
    )


class TestRRFFuseWeightedNormalization:
    """权重归一化与 max_possible 上界正确性测试

    守卫任务要求：三路融合时若某路失败，用其余两路的结果（不阻塞），
    且权重归一化不能导致总权重计算错误或排序区分度丢失。
    """

    @pytest.fixture
    def loader(self, file_store):
        return SkillLoader(file_store=file_store)

    def test_failed_path_excluded_from_total_weight(self, loader):
        """某路失败（空结果）时，其权重不参与 total_weight，剩余路自动重分配

        场景: vector 失败（matches=[]），只剩 tfidf:0.2 + bm25:0.2
        预期: total_weight=0.4，归一化后 tfidf=0.5, bm25=0.5（自动重分配）
        """
        fused = loader._rrf_fuse_weighted([
            ("tfidf", [_mk_match("a"), _mk_match("b")], 0.2),
            ("vector", [], 0.6),  # vector 失败
            ("bm25", [_mk_match("a")], 0.2),
        ], k=60)

        assert len(fused) > 0, "vector 失败不应阻塞融合"
        # "a" 在 tfidf+bm25 两路命中，应排第一
        assert fused[0].skill_id == "a"
        # 验证归一化后 "a" 的 score 合理（两路 rank=1，接近 1.0）
        bd = fused[0].score_breakdown
        # rrf_score = (0.2/0.4)/61 + (0.2/0.4)/61 = 0.5/61 + 0.5/61 = 1.0/61
        # max_possible = 1.0/61，所以 normalized = 1.0
        assert abs(bd["rrf_normalized"] - 1.0) < 0.01, (
            f"两路 rank=1 应接近 1.0，实际 {bd['rrf_normalized']}"
        )

    def test_all_paths_fail_returns_empty(self, loader):
        """三路全失败时返回空列表（不报错）"""
        fused = loader._rrf_fuse_weighted([
            ("tfidf", [], 0.2),
            ("vector", [], 0.6),
            ("bm25", [], 0.2),
        ], k=60)
        assert fused == [], "三路全空应返回空列表"

    def test_zero_weight_path_excluded(self, loader):
        """weight=0 的路被过滤（用户显式禁用某路）"""
        fused = loader._rrf_fuse_weighted([
            ("tfidf", [_mk_match("a")], 0.2),
            ("vector", [_mk_match("b")], 0.0),  # 显式禁用
            ("bm25", [_mk_match("a")], 0.2),
        ], k=60)
        # "b" 仅在 weight=0 的 vector 路，应不出现在结果中
        ids = [m.skill_id for m in fused]
        assert "b" not in ids, f"weight=0 的路不应贡献，实际 {ids}"
        assert "a" in ids

    def test_multipath_match_not_capped_to_single_path(
        self, loader,
    ):
        """【不易修复回归】多路命中文档 score 应严格高于单路命中文档

        修复前 bug: max_possible = max_weight/(k+1)（单路上界），
        导致多路命中文档的 rrf_score 超过上界被 min(1.0,...) 错误截断，
        与单路命中文档 score 相等，丢失排序区分度。

        场景（两路均等 0.5/0.5，vector 失败）:
            - tfidf: [A, B]  (A rank=1, B rank=2)
            - bm25:  [B, A]  (B rank=1, A rank=2)
            - A 两路都命中（rank 1+2），B 两路都命中（rank 2+1）
            rrf_score 相同 → score 应相等（对称场景，验证不 crash）

        非对称场景（验证区分度）:
            - tfidf: [A]     (A rank=1)
            - vector: [B, A] (B rank=1, A rank=2)
            - 权重 0.5/0.5
            A: rrf = 0.5/61 + 0.5/62（两路命中）
            B: rrf = 0.5/61（单路命中）
            修复前: max_possible=0.5/61 → A=min(1.0, >1)=1.0, B=1.0 → A==B ❌
            修复后: max_possible=1.0/61 → A≈0.99, B=0.5 → A>B ✓
        """
        fused = loader._rrf_fuse_weighted([
            ("tfidf", [_mk_match("A")], 0.5),
            ("vector", [_mk_match("B"), _mk_match("A")], 0.5),
        ], k=60)

        by_id = {m.skill_id: m for m in fused}
        a_score = by_id["A"].score_breakdown["rrf_normalized"]
        b_score = by_id["B"].score_breakdown["rrf_normalized"]

        # A 两路命中，应严格高于 B 单路命中
        assert a_score > b_score, (
            f"多路命中文档 A({a_score}) 应严格高于单路命中 B({b_score})，"
            f"若相等说明 max_possible 上界仍被错误 cap"
        )
        # B 单路 rank=1 的 score 应精确为 0.5（=0.5/61 ÷ 1.0/61）
        assert abs(b_score - 0.5) < 0.01, (
            f"B 单路 rank=1 应为 0.5，实际 {b_score}"
        )

    def test_weights_need_not_sum_to_one(self, loader):
        """权重不必和为 1，内部自动归一化（用户传 2/6/2 等价于 0.2/0.6/0.2）"""
        fused_a = loader._rrf_fuse_weighted([
            ("tfidf", [_mk_match("x")], 0.2),
            ("vector", [_mk_match("x")], 0.6),
            ("bm25", [_mk_match("x")], 0.2),
        ], k=60)
        fused_b = loader._rrf_fuse_weighted([
            ("tfidf", [_mk_match("x")], 2.0),
            ("vector", [_mk_match("x")], 6.0),
            ("bm25", [_mk_match("x")], 2.0),
        ], k=60)
        # 归一化后等价，score 应相同
        assert abs(fused_a[0].score - fused_b[0].score) < 1e-6, (
            f"权重 0.2/0.6/0.2 应等价于 2/6/2，实际 "
            f"{fused_a[0].score} vs {fused_b[0].score}"
        )


# ═══════════════════════════════════════════════════════════════════
#  分层配置优先级与 mtime 缓存契约测试
# ═══════════════════════════════════════════════════════════════════

def _write_test_config_yaml(path, bm25: float) -> None:
    """写入临时 config.yaml（仅 fusion.weights 部分）"""
    import yaml as _yaml
    config = {
        "skills_mgmt": {
            "retrieval": {
                "fusion": {
                    "weights": {
                        "tfidf": 0.2,
                        "vector": 0.6,
                        "bm25": bm25,
                    }
                }
            }
        }
    }
    with open(path, "w", encoding="utf-8") as f:
        _yaml.dump(config, f, allow_unicode=True, default_flow_style=False)


_ENV_NAMES = [
    "SKILLS_FUSION_WEIGHT_TFIDF",
    "SKILLS_FUSION_WEIGHT_VECTOR",
    "SKILLS_FUSION_WEIGHT_BM25",
]


class TestLayeredConfig:
    """分层配置优先级与 mtime 缓存契约测试

    守卫 _get_default_weights 的分层优先级:
        .env > config.yaml > 硬编码默认值

    以及 mtime 缓存自动失效逻辑.

    【不易】优先级链路: .env > config.yaml > 硬编码默认值
    【变易】mtime 变化触发缓存失效，文件删除清除缓存
    【简易】每测试独立隔离，autouse fixture 清除缓存
    """

    @pytest.fixture(autouse=True)
    def _clear_caches(self, monkeypatch):
        """每个测试前后清除所有缓存，避免测试间污染"""
        SkillLoader._clear_all_caches()
        # 清除环境变量（monkeypatch 会在测试后自动恢复）
        for env_name in _ENV_NAMES:
            monkeypatch.delenv(env_name, raising=False)
        yield
        SkillLoader._clear_all_caches()

    def test_config_yaml_overrides_hardcoded(self, tmp_path, monkeypatch):
        """config.yaml 覆盖硬编码默认值

        场景: config.yaml bm25=0.5, .env 未设置
        预期: 最终 bm25=0.5（config.yaml 覆盖硬编码 0.2）
        """
        config_path = tmp_path / "config.yaml"
        _write_test_config_yaml(config_path, bm25=0.5)
        monkeypatch.setattr(SkillLoader, "_CONFIG_YAML_PATH", config_path)

        weights = SkillLoader._get_default_weights()
        assert abs(weights["bm25"] - 0.5) < 1e-9, (
            f"config.yaml bm25=0.5 应覆盖硬编码 0.2，实际 {weights['bm25']}"
        )

    def test_env_overrides_config_yaml(self, tmp_path, monkeypatch):
        """ .env 覆盖 config.yaml（核心优先级验证）

        场景: config.yaml bm25=0.5, .env SKILLS_FUSION_WEIGHT_BM25=0.8
        预期: 最终 bm25=0.8（.env 优先级最高）
        """
        config_path = tmp_path / "config.yaml"
        _write_test_config_yaml(config_path, bm25=0.5)
        monkeypatch.setattr(SkillLoader, "_CONFIG_YAML_PATH", config_path)
        monkeypatch.setenv("SKILLS_FUSION_WEIGHT_BM25", "0.8")

        weights = SkillLoader._get_default_weights()
        assert abs(weights["bm25"] - 0.8) < 1e-9, (
            f".env bm25=0.8 应覆盖 config.yaml 0.5，实际 {weights['bm25']}"
        )

    def test_invalid_env_falls_back_to_config_yaml(self, tmp_path, monkeypatch):
        """ .env 非法值降级到 config.yaml

        场景: config.yaml bm25=0.5, .env SKILLS_FUSION_WEIGHT_BM25='invalid'
        预期: 最终 bm25=0.5（.env 非法值被跳过，降级到 config.yaml）
        """
        config_path = tmp_path / "config.yaml"
        _write_test_config_yaml(config_path, bm25=0.5)
        monkeypatch.setattr(SkillLoader, "_CONFIG_YAML_PATH", config_path)
        monkeypatch.setenv("SKILLS_FUSION_WEIGHT_BM25", "not_a_number")

        weights = SkillLoader._get_default_weights()
        assert abs(weights["bm25"] - 0.5) < 1e-9, (
            f".env 非法值应降级到 config.yaml 0.5，实际 {weights['bm25']}"
        )

    def test_config_yaml_not_exist_falls_back_to_hardcoded(
        self, tmp_path, monkeypatch,
    ):
        """config.yaml 不存在时降级到硬编码默认值

        场景: config.yaml 不存在, .env 未设置
        预期: 最终 bm25=0.2（硬编码默认值兜底）
        """
        config_path = tmp_path / "nonexistent.yaml"
        monkeypatch.setattr(SkillLoader, "_CONFIG_YAML_PATH", config_path)

        weights = SkillLoader._get_default_weights()
        assert abs(weights["bm25"] - 0.2) < 1e-9, (
            f"config.yaml 不存在应降级到硬编码 0.2，实际 {weights['bm25']}"
        )

    def test_cache_hit_returns_same_value(self, tmp_path, monkeypatch):
        """缓存命中：mtime 未变时返回缓存值

        场景: 连续两次调用 _get_default_weights，config.yaml 未修改
        预期: 两次返回相同的 weights（缓存命中）
        """
        config_path = tmp_path / "config.yaml"
        _write_test_config_yaml(config_path, bm25=0.5)
        monkeypatch.setattr(SkillLoader, "_CONFIG_YAML_PATH", config_path)

        # 首次读取建立缓存
        weights1 = SkillLoader._get_default_weights()
        assert abs(weights1["bm25"] - 0.5) < 1e-9

        # 再次读取应返回相同值（缓存命中）
        weights2 = SkillLoader._get_default_weights()
        assert weights1 == weights2, (
            f"缓存命中应返回相同值，实际 {weights1} vs {weights2}"
        )

    def test_cache_invalidation_on_mtime_change(self, tmp_path, monkeypatch):
        """缓存失效：mtime 变化后重新读取新值

        场景: 首次读取 bm25=0.5，修改 config.yaml 为 bm25=0.8，再次读取
        预期: 第二次读到 bm25=0.8（mtime 变化触发缓存失效）
        """
        import time as _time
        config_path = tmp_path / "config.yaml"
        _write_test_config_yaml(config_path, bm25=0.5)
        monkeypatch.setattr(SkillLoader, "_CONFIG_YAML_PATH", config_path)

        # 首次读取
        weights1 = SkillLoader._get_default_weights()
        assert abs(weights1["bm25"] - 0.5) < 1e-9

        # 修改 config.yaml（确保 mtime 变化）
        _time.sleep(0.01)
        _write_test_config_yaml(config_path, bm25=0.8)

        # 再次读取应读到新值（mtime 变化触发缓存失效）
        weights2 = SkillLoader._get_default_weights()
        assert abs(weights2["bm25"] - 0.8) < 1e-9, (
            f"mtime 变化后应读到新值 0.8，实际 {weights2['bm25']}"
        )
