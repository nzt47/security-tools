"""技能向量检索单元测试 — 守卫 SkillVectorAdapter 核心契约

覆盖任务要求的 6 个用例:
1. test_vector_searcher_build_index — 构建索引成功
2. test_vector_searcher_search_returns_matches — 检索返回 top_k 结果
3. test_vector_searcher_fallback_on_unavailable — is_available=False 时降级
4. test_loader_match_uses_vector_when_available — mock is_available=True 时走向量路径
5. test_loader_match_falls_back_to_tfidf — mock 向量检索失败时降级 TF-IDF
6. test_index_persistence — 索引重建等价持久化（重启后 ensure_indexed 达到相同状态）

设计:
- 用 FakeSentenceTransformer 避免加载真 BGE-m3 模型（首次加载约 11 分钟，CI 必超时）
- FakeModel 基于关键词 bag-of-words 生成向量，让含相同关键词的文本相似度高
- tmp_path 隔离每个测试的 skills_repo，不污染真实数据

【不易】守 SkillVectorAdapter 公开接口契约：search/is_available/ensure_indexed/upsert
【变易】FakeModel 模拟语义检索行为，可扩展关键词列表
【简易】最小测试集，每用例聚焦一个契约
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import yaml

from agent.skills_mgmt.file_store import SkillFileStore
from agent.skills_mgmt.loader import SkillLoader
from agent.skills_mgmt.vector_adapter import SkillVectorAdapter


# ═══════════════════════════════════════════════════════════════════
#  FakeSentenceTransformer — 模拟 BGE-m3 行为（避免加载真模型）
# ═══════════════════════════════════════════════════════════════════

class FakeSentenceTransformer:
    """模拟 SentenceTransformer — 基于关键词 bag-of-words 生成向量

    让包含相同关键词的文本向量相似度高，模拟语义检索行为。
    维度 = 关键词数，每维对应该关键词是否出现在文本中。

    【变易】关键词列表可扩展，模拟不同领域的语义匹配
    【简易】bag-of-words 是最简语义模拟，足够测试检索契约
    """

    KEYWORDS = ["pdf", "反思", "总结", "记忆", "情绪", "安全", "建议", "上下文"]

    def __init__(self, dim: int | None = None):
        self._dim = dim or len(self.KEYWORDS)

    def get_sentence_embedding_dimension(self) -> int:
        return self._dim

    def encode(
        self,
        texts,
        normalize_embeddings: bool = True,
        show_progress_bar: bool = False,
    ) -> np.ndarray:
        """编码文本列表为向量矩阵

        每个文本：命中关键词的维度=1，否则=0；归一化后单位向量
        全零向量兜底为 [0.1, 0, ...] 避免数值问题
        """
        vectors = []
        for text in texts:
            vec = np.zeros(self._dim, dtype=float)
            text_lower = (text or "").lower()
            for i, kw in enumerate(self.KEYWORDS[: self._dim]):
                if kw.lower() in text_lower:
                    vec[i] = 1.0
            # 全零兜底：避免 norm=0 导致归一化 NaN
            if np.linalg.norm(vec) == 0:
                vec[0] = 0.1
            if normalize_embeddings:
                norm = np.linalg.norm(vec)
                if norm > 0:
                    vec = vec / norm
            vectors.append(vec)
        return np.array(vectors)


# ═══════════════════════════════════════════════════════════════════
#  Fixture
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
def adapter_with_fake_model(file_store) -> SkillVectorAdapter:
    """创建 SkillVectorAdapter 并注入 FakeSentenceTransformer

    禁用真模型初始化（use_sentence_transformers=False, use_native_chroma=False），
    直接注入 FakeModel 到 _st_backend，绕过 11 分钟的真模型加载。
    """
    adapter = SkillVectorAdapter(
        file_store=file_store,
        use_sentence_transformers=False,
        use_native_chroma=False,
    )
    fake_model = FakeSentenceTransformer()
    # _st_backend 元组：(model, doc_ids, doc_vectors, doc_metas)
    adapter._st_backend = (fake_model, [], [], [])
    # _vector_store 设为非 None 标识，让 search 走 _st_backend 分支
    adapter._vector_store = (fake_model, [], [], [])
    return adapter


# ═══════════════════════════════════════════════════════════════════
#  测试用例 — 守卫任务要求的 6 个契约
# ═══════════════════════════════════════════════════════════════════

class TestVectorSkillSearcher:
    """SkillVectorAdapter 核心契约测试"""

    def test_vector_searcher_build_index(self, adapter_with_fake_model):
        """用例1: 构建索引成功 — ensure_indexed 返回 > 0 且 is_available=True"""
        adapter = adapter_with_fake_model
        count = adapter.ensure_indexed()

        assert count > 0, f"索引构建应返回 > 0，实际 {count}"
        assert adapter.is_available is True, "构建后 is_available 应为 True"
        assert adapter.indexed_count == count, "indexed_count 应与返回值一致"
        # 3 个测试技能都应被索引
        assert count == 3, f"应索引 3 个技能，实际 {count}"

    def test_vector_searcher_search_returns_matches(
        self, adapter_with_fake_model,
    ):
        """用例2: 检索返回 top_k 结果 — query 关键词匹配 skill description"""
        adapter = adapter_with_fake_model
        adapter.ensure_indexed()

        # "反思" 关键词应让 self_reflection 排第一
        results = adapter.search("帮我反思一下", top_k=3)

        assert isinstance(results, list)
        assert 0 < len(results) <= 3, f"应返回 1~3 条，实际 {len(results)}"
        # 每项结构校验
        for r in results:
            assert "skill_id" in r
            assert "score" in r
            assert "metadata" in r
            assert 0.0 <= r["score"] <= 1.0, f"score 应在 [0,1]，实际 {r['score']}"
        # self_reflection 应在结果中（"反思"关键词匹配，相似度最高）
        skill_ids = [r["skill_id"] for r in results]
        assert "self_reflection" in skill_ids, (
            f"self_reflection 应在结果中，实际 {skill_ids}"
        )
        # 排第一的应是 self_reflection（相似度最高）
        assert results[0]["skill_id"] == "self_reflection", (
            f"top1 应是 self_reflection，实际 {results[0]['skill_id']}"
        )

    def test_vector_searcher_fallback_on_unavailable(self, file_store, monkeypatch):
        """用例3: is_available=False 时降级 — 后端不可用返回空列表

        monkeypatch _ensure_vector_store 返回 None，避免触发第三级 VectorStore
        fallback（会联网加载真 BGE-m3 模型，Windows CPU 环境下 0xC00000005 崩溃，
        参考 project_memory 教训）。
        """
        adapter = SkillVectorAdapter(
            file_store=file_store,
            use_sentence_transformers=False,
            use_native_chroma=False,
        )
        # 阻止第三级 VectorStore fallback（守 project_memory：Embedding 无隔离会崩溃）
        monkeypatch.setattr(adapter, "_ensure_vector_store", lambda: None)

        # 不注入任何后端，_vector_store 保持 None
        assert adapter._vector_store is None, "未初始化应 _vector_store=None"
        assert adapter.is_available is False, "is_available 应为 False"

        results = adapter.search("任意查询", top_k=3)
        assert results == [], f"后端不可用应返回空列表，实际 {results}"

    def test_loader_match_uses_vector_when_available(
        self, file_store, adapter_with_fake_model,
    ):
        """用例4: mock is_available=True 时走向量路径 — retrieval_method=='vector'"""
        adapter = adapter_with_fake_model
        adapter.ensure_indexed()

        # 注入已构建索引的 adapter，避免 _get_vector_adapter 重新初始化
        loader = SkillLoader(file_store=file_store, vector_adapter=adapter)
        result = loader.match("帮我反思一下", top_k=3, use_vector=True)

        assert result.retrieval_method == "vector", (
            f"应走向量路径，实际 {result.retrieval_method}"
        )
        assert result.fallback_used is False, "向量可用时不应 fallback"
        assert len(result.matches) > 0, "向量检索应返回结果"
        # self_reflection 应在匹配结果中
        match_ids = [m.skill_id for m in result.matches]
        assert "self_reflection" in match_ids, (
            f"self_reflection 应在匹配中，实际 {match_ids}"
        )

    def test_loader_match_falls_back_to_tfidf(self, file_store):
        """用例5: mock 向量检索失败时降级 TF-IDF — retrieval_method=='tfidf'"""
        # 创建一个模拟向量检索失败的 adapter（_st_backend=None 触发降级）
        class FailingAdapter:
            """模拟向量后端不可用 — _st_backend/_native_chroma 均为 None

            触发 loader._try_vector_match 中的 BM25 fallback 检测，
            返回 None 让外层降级 TF-IDF。
            """
            _st_backend = None
            _native_chroma = None

            def search(self, *args, **kwargs):
                return []

            @property
            def is_available(self):
                return False

            def ensure_indexed(self, *, force: bool = False) -> int:
                return 0

            def upsert(self, skill_id: str) -> bool:
                return False

        loader = SkillLoader(file_store=file_store, vector_adapter=FailingAdapter())
        result = loader.match("PDF", top_k=3, use_vector=True)

        # 向量检索失败 → 降级 TF-IDF
        assert result.retrieval_method == "tfidf", (
            f"向量失败应降级 TF-IDF，实际 {result.retrieval_method}"
        )
        assert result.fallback_used is True, "降级时 fallback_used 应为 True"

    def test_index_persistence(self, file_store):
        """用例6: 索引重建等价持久化 — 重启后 ensure_indexed 达到相同状态

        _st_backend 模式无磁盘持久化（numpy 数组在内存），靠 ensure_indexed
        全量重建保证一致性。本测试验证：新 adapter 实例（模拟重启）能重建
        出与原 adapter 相同的索引状态和检索结果。
        """
        # adapter1: 构建索引
        adapter1 = SkillVectorAdapter(
            file_store=file_store,
            use_sentence_transformers=False,
            use_native_chroma=False,
        )
        fake_model1 = FakeSentenceTransformer()
        adapter1._st_backend = (fake_model1, [], [], [])
        adapter1._vector_store = (fake_model1, [], [], [])
        count1 = adapter1.ensure_indexed()
        results1 = adapter1.search("反思", top_k=3)

        # adapter2: 模拟重启，全新实例 + 新模型
        adapter2 = SkillVectorAdapter(
            file_store=file_store,
            use_sentence_transformers=False,
            use_native_chroma=False,
        )
        fake_model2 = FakeSentenceTransformer()
        adapter2._st_backend = (fake_model2, [], [], [])
        adapter2._vector_store = (fake_model2, [], [], [])
        count2 = adapter2.ensure_indexed()
        results2 = adapter2.search("反思", top_k=3)

        # 重启后重建索引，技能数应一致
        assert count1 == count2, (
            f"重启后索引数应一致，{count1} vs {count2}"
        )
        assert count2 > 0, "索引数应 > 0"
        # 检索结果应一致（skill_id 顺序相同）
        ids1 = [r["skill_id"] for r in results1]
        ids2 = [r["skill_id"] for r in results2]
        assert ids1 == ids2, (
            f"重启后检索结果应一致，{ids1} vs {ids2}"
        )


# ═══════════════════════════════════════════════════════════════════
#  附加测试 — 守卫新增的 upsert 钩子与维度校验
# ═══════════════════════════════════════════════════════════════════

class TestVectorSearcherUpsertHook:
    """守卫 file_store 写入钩子 → adapter.upsert 增量同步"""

    def test_upsert_on_skill_create(
        self, file_store, adapter_with_fake_model,
    ):
        """skill.md create 后 adapter.upsert 被触发，新技能进入索引"""
        adapter = adapter_with_fake_model
        adapter.ensure_indexed()
        initial_count = adapter.indexed_count

        # 注册钩子（模拟 loader._get_vector_adapter 的注册行为）
        file_store.register_write_hook(
            lambda sid, action: adapter.upsert(sid)
        )

        # 新建技能 → 应触发 upsert 钩子 → 索引数 +1
        new_skill = {
            "id": "new_emotion_skill",
            "name": "情绪识别",
            "description": "识别用户情绪并提供情绪支持",
            "category": "emotion",
            "tags": ["情绪"],
            "version": "1.0.0",
            "enabled": True,
        }
        _write_skill_md(file_store.repo_path, new_skill)
        # create 走 file_store.create 而非直接写文件，触发钩子
        # 这里直接调 _notify_hooks 模拟（因 _write_skill_md 绕过了 create）
        file_store._notify_hooks(new_skill["id"], "create")

        assert adapter.indexed_count == initial_count + 1, (
            f"upsert 后索引数应 +1，"
            f"{initial_count} → {adapter.indexed_count}"
        )
        assert "new_emotion_skill" in adapter._indexed_skill_ids

    def test_upsert_removes_deleted_skill(
        self, file_store, adapter_with_fake_model,
    ):
        """skill.md delete 后 adapter.upsert 清理残留向量"""
        adapter = adapter_with_fake_model
        adapter.ensure_indexed()
        assert "self_reflection" in adapter._indexed_skill_ids

        file_store.register_write_hook(
            lambda sid, action: adapter.upsert(sid)
        )

        # 模拟删除（先删文件，再触发钩子）
        import shutil
        shutil.rmtree(file_store.repo_path / "self_reflection")
        file_store._notify_hooks("self_reflection", "delete")

        assert "self_reflection" not in adapter._indexed_skill_ids, (
            "删除后 self_reflection 应不在索引中"
        )


class TestVectorSearcherDimensionCheck:
    """守卫维度校验重建索引逻辑"""

    def test_dimension_mismatch_triggers_rebuild(
        self, file_store, adapter_with_fake_model,
    ):
        """doc_vectors 维度与模型维度不一致时清空索引重建"""
        adapter = adapter_with_fake_model
        adapter.ensure_indexed()

        # 篡改 _st_backend 的 doc_vectors 维度（模拟模型切换后残留旧向量）
        model, doc_ids, doc_vectors, doc_metas = adapter._st_backend
        # 构造一个维度不匹配的假 doc_vectors（原维度 8，改为 16）
        import numpy as np
        fake_vectors = np.zeros((len(doc_ids), 16), dtype=float)
        adapter._st_backend = (model, doc_ids, fake_vectors, doc_metas)
        adapter._index_built = True  # 标记已构建，避免 ensure_indexed 跳过

        # 再次 ensure_indexed 应检测到维度不匹配，清空并重建
        count = adapter.ensure_indexed()

        assert count > 0, "维度不匹配后应重建索引"
        # 重建后 doc_vectors 维度应与模型一致（8）
        _, _, rebuilt_vectors, _ = adapter._st_backend
        assert rebuilt_vectors.shape[1] == 8, (
            f"重建后维度应为 8，实际 {rebuilt_vectors.shape[1]}"
        )
