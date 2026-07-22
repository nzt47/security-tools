"""技能向量检索适配器 — 复用 memory/vector_store/VectorStore

设计目的:
    为 SkillLoader 第一层匹配提供向量检索能力，解决 TF-IDF 对
    description 为空技能（如 self_reflection / memory_summary）的召回缺陷。

架构层级:
    SkillLoader (loader.py)
        ↓ 注入
    SkillVectorAdapter (本模块)
        ↓ 复用
    VectorStore (memory/vector_store/vector_store.py)
        ↓ 自动选择
    ChromaDB / BM25 倒排索引 / 字符匹配 fallback

核心策略:
    - 向量化输入 = front matter（name + description + tags + category）
                    + body 摘要（前 500 字符）
      → 解决 description 空白导致的字面匹配失效
    - 增量索引：首次调用 search() 时延迟构建索引，避免 SkillLoader 初始化时拉起 ChromaDB
    - 失败降级：向量检索失败时返回空列表，由 SkillLoader 决定是否回退 TF-IDF
    - 与 SkillFileStore 解耦：通过 SkillFileStore 实例读元数据 + body，不直接访问磁盘

【不易】不修改 VectorStore 类，不修改 skill.md 定义
【变易】collection_name / model_name / body_summary_chars 可配置
【简易】单一职责：embed skills + search by vector
"""
from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any, Dict, List, Optional, Tuple

from .file_store import SkillFileStore
from .observability import emit_metric

logger = logging.getLogger("agent.skills_mgmt.vector_adapter")

# 默认配置
_DEFAULT_COLLECTION = "skill_metadata"
# 【变易】默认模型从 paraphrase-multilingual-MiniLM-L12-v2 升级为 BGE-m3
# 原因：BGE-m3 多语言能力强，中文 query 召回质量显著优于 MiniLM
# 验证：test_bge_m3_load.py 显示中文相似度判别 0.62+ vs 0.22~0.39
_DEFAULT_MODEL = "BAAI/bge-m3"
_DEFAULT_PERSIST_DIR = "./data/skill_vectors"
_DEFAULT_BODY_SUMMARY_CHARS = 500  # body 摘要长度，平衡向量化质量与性能

# BGE-m3 向量维度（用于校验）
_BGE_M3_DIM = 1024


class SkillVectorAdapter:
    """技能向量检索适配器 — 把 SkillFileStore 的元数据/正文向量化并支持语义搜索

    用法:
        adapter = SkillVectorAdapter(file_store=SkillFileStore())
        adapter.ensure_indexed()  # 延迟构建索引（首次调用 search 会自动触发）
        results = adapter.search("帮我反思刚才的回答", top_k=5, enabled_only=True)
        # results: [{"skill_id": "self_reflection", "score": 0.85, "metadata": {...}}]

    线程安全:
        - 索引构建由 threading.Lock 保护，避免并发重复构建
        - VectorStore 自身的搜索/添加已内部加锁

    可观测性:
        - 关键操作通过 emit_metric 上报业务指标
        - 结构化日志记录 trace 链路
    """

    def __init__(
        self,
        file_store: SkillFileStore,
        *,
        collection_name: str = _DEFAULT_COLLECTION,
        model_name: str = _DEFAULT_MODEL,
        persist_dir: str = _DEFAULT_PERSIST_DIR,
        body_summary_chars: int = _DEFAULT_BODY_SUMMARY_CHARS,
        vector_store: Optional[Any] = None,
        use_native_chroma: bool = True,
        use_sentence_transformers: bool = True,
    ):
        """初始化向量适配器

        Args:
            file_store: SkillFileStore 实例，用于读取技能元数据与 body
            collection_name: 向量集合名称
            model_name: Sentence Transformers 模型名（默认 BGE-m3，多语言支持中文）
            persist_dir: 向量持久化目录
            body_summary_chars: body 摘要长度（向量化输入的一部分）
            vector_store: 可选的 VectorStore 实例（测试注入），None 则延迟创建
            use_native_chroma: True 则用 chromadb 原生 API（仅当 sentence-transformers
                不可用时启用，使用 onnxruntime embedding all-MiniLM-L6-v2）
            use_sentence_transformers: True 则优先用 BGE-m3 via sentence-transformers
                （已验证 Windows 上无 DLL 冲突，中文召回质量最优）；
                False 则跳过此路径，回退 native_chroma

        优先级（【变易】可配置）:
            1. use_sentence_transformers=True → BGE-m3 + 本地 numpy 向量库（最优）
            2. use_native_chroma=True → chromadb + onnxruntime（兜底）
            3. VectorStore → sentence-transformers + chromadb（最后兜底）
            4. 全部失败 → 返回 None，外层降级 TF-IDF
        """
        self.fs = file_store
        self.collection_name = collection_name
        self.model_name = model_name
        self.persist_dir = persist_dir
        self.body_summary_chars = body_summary_chars
        self.use_native_chroma = use_native_chroma
        self.use_sentence_transformers = use_sentence_transformers

        # 延迟创建 VectorStore，避免 SkillLoader 启动时拉起 ChromaDB/torch
        self._vector_store = vector_store
        self._native_chroma: Optional[tuple] = None  # (client, collection)
        # 【变易】BGE-m3 sentence-transformers 模式：自管理向量库
        # 存储 (model, doc_ids, doc_vectors, doc_metas)
        self._st_backend: Optional[tuple] = None
        self._indexed_skill_ids: set = set()  # 已索引的技能 ID（用于增量同步）
        self._lock = threading.Lock()
        self._index_built = False

    # ──────────────────────────────────────────────
    #  索引构建
    # ──────────────────────────────────────────────

    def _build_vector_text(self, meta: Dict[str, Any], skill_id: str) -> str:
        """构建单个技能的向量化输入文本

        策略：front matter（核心）+ body 摘要（补充语义）
        - front matter: name + description + tags + category
        - body 摘要: 前 N 字符（弥补 description 为空的缺陷）

        【变易】body 摘要长度可配置，平衡向量化质量与性能
        """
        parts = [
            meta.get("name", skill_id),
            meta.get("description", ""),
            " ".join(meta.get("tags", []) or []),
            meta.get("category", ""),
        ]
        front_text = " ".join(p for p in parts if p)

        # 读 body 摘要（第二层），失败不影响向量化（用 front_text 兜底）
        body_summary = ""
        try:
            body = self.fs.load_instruction(skill_id)
            if body:
                body_summary = body[: self.body_summary_chars]
        except Exception as e:  # noqa: BLE001
            logger.debug(
                f"load_instruction failed for {skill_id}: {e}, "
                f"using front matter only"
            )

        if body_summary:
            return f"{front_text}\n{body_summary}"
        return front_text

    def _ensure_vector_store(self) -> Any:
        """延迟创建向量后端实例

        优先策略（按质量与稳定性排序）:
            1. BGE-m3 via sentence-transformers（多语言，中文召回最优，已验证无 DLL 冲突）
            2. chromadb 原生 API（onnxruntime embedding，all-MiniLM-L6-v2 兜底）
            3. VectorStore（sentence-transformers + chromadb，最后兜底）
            4. 全部失败 → 返回 None，外层降级 TF-IDF

        【变易】可通过 use_sentence_transformers / use_native_chroma 参数控制
        【简易】每个后端独立 try/except，失败降级下一档
        """
        if self._vector_store is not None:
            return self._vector_store

        # 优先尝试 BGE-m3 via sentence-transformers（最优路径）
        if self.use_sentence_transformers:
            st_backend = self._try_init_sentence_transformers()
            if st_backend is not None:
                self._st_backend = st_backend
                # 返回一个标识，让 ensure_indexed/search 走 _st_backend 分支
                # 这里返回 st_backend 本身作为非 None 标识
                self._vector_store = st_backend
                return st_backend

        # 回退到 chromadb 原生 API（避开 sentence-transformers/torch）
        if self.use_native_chroma:
            native = self._try_init_native_chroma()
            if native is not None:
                self._vector_store = native
                self._native_chroma = native
                return native

        # 最后回退到 VectorStore（可能 DLL 冲突，但保留作为兼容路径）
        try:
            import sys
            from pathlib import Path

            project_root = Path(__file__).resolve().parent.parent.parent
            if str(project_root) not in sys.path:
                sys.path.insert(0, str(project_root))

            from memory.vector_store.vector_store import VectorStore  # noqa: E402

            self._vector_store = VectorStore(
                collection_name=self.collection_name,
                persist_dir=self.persist_dir,
                model_name=self.model_name,
                cache_size=50,
                cache_ttl=600,
                enable_inverted_index=True,  # fallback 模式下用 BM25
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"VectorStore initialization failed: {e}, "
                f"vector search will return empty"
            )
            self._vector_store = None
        return self._vector_store

    def _try_init_sentence_transformers(self) -> Optional[Any]:
        """初始化 BGE-m3 via sentence-transformers

        使用 BAAI/bge-m3 模型（1024 维，多语言）。
        自管理一个简单的内存向量库（numpy 数组），避免 chromadb 依赖。

        已验证（test_bge_m3_load.py）:
            - Windows 上无 DLL 冲突（torch 2.13 + onnxruntime 1.20 共存）
            - 中文 query 召回质量显著优于 all-MiniLM-L6-v2
            - 模型加载耗时约 11 分钟（首次），后续从缓存加载 < 10s

        Returns:
            (model, [], [], []) 元组：(SentenceTransformer, doc_ids, doc_vectors, doc_metas)
            初始时 doc 列表为空，ensure_indexed 后填充
            失败返回 None
        """
        try:
            import os
            # 设置 HF 镜像（国内访问优化）
            os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
            os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

            from sentence_transformers import SentenceTransformer

            # device="cpu" 避免 CUDA 依赖；normalize_embeddings=True 让相似度=点积
            model = SentenceTransformer(self.model_name, device="cpu")
            logger.info(json.dumps({
                "module_name": "vector_adapter",
                "action": "sentence_transformers.init.ok",
                "model": self.model_name,
                "dim": model.get_sentence_embedding_dimension(),
            }, ensure_ascii=False))
            # 返回 4 元组：(model, doc_ids, doc_vectors, doc_metas)
            # doc_ids: List[str] - 技能 ID 顺序
            # doc_vectors: np.ndarray (N, dim) - 归一化后的文档向量
            # doc_metas: List[Dict] - 技能元数据
            return (model, [], [], [])
        except Exception as e:  # noqa: BLE001
            logger.warning(json.dumps({
                "module_name": "vector_adapter",
                "action": "sentence_transformers.init.failed",
                "model": self.model_name,
                "error": str(e)[:300],
            }, ensure_ascii=False))
            return None

    def _try_init_native_chroma(self) -> Optional[Any]:
        """初始化 chromadb 原生 API 后端（避开 sentence-transformers）

        使用 chromadb 默认的 onnxruntime embedding (all-MiniLM-L6-v2)，
        已验证在 Windows 上无 DLL 冲突。

        返回 (client, collection) 元组，或 None（初始化失败）
        """
        try:
            import os
            from pathlib import Path

            os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

            import chromadb
            from chromadb.config import Settings

            persist_path = str(Path(self.persist_dir) / "native_chroma")
            Path(persist_path).mkdir(parents=True, exist_ok=True)

            client = chromadb.PersistentClient(
                path=persist_path,
                settings=Settings(anonymized_telemetry=False),
            )
            collection = client.get_or_create_collection(
                name=self.collection_name,
                metadata={"description": "Skill retrieval index"},
            )
            logger.info(json.dumps({
                "module_name": "vector_adapter",
                "action": "native_chroma.init.ok",
                "persist_path": persist_path,
                "collection": self.collection_name,
            }, ensure_ascii=False))
            return (client, collection)
        except Exception as e:  # noqa: BLE001
            logger.warning(json.dumps({
                "module_name": "vector_adapter",
                "action": "native_chroma.init.failed",
                "error": str(e),
            }, ensure_ascii=False))
            return None

    def ensure_indexed(self, *, force: bool = False) -> int:
        """构建/刷新技能向量索引

        策略：
        - 首次调用：构建全量索引
        - 后续调用：增量同步（仅索引新增的技能）
        - force=True：强制重建全量索引

        Returns: 已索引的技能数量
        """
        vs = self._ensure_vector_store()
        if vs is None:
            return 0

        with self._lock:
            index = self.fs.load_metadata_index()
            current_ids = set(index.keys())

            if force:
                # 强制重建：清空已索引集合，重新全量构建
                self._indexed_skill_ids.clear()
                self._index_built = False

            new_ids = current_ids - self._indexed_skill_ids
            if not new_ids and self._index_built:
                # 无新增，跳过
                return len(self._indexed_skill_ids)

            # 批量构建向量文本并添加
            items_to_add = []
            for skill_id in new_ids:
                meta = index[skill_id]
                content = self._build_vector_text(meta, skill_id)
                metadata = {
                    "skill_id": skill_id,
                    "name": meta.get("name", skill_id),
                    "description": meta.get("description", ""),
                    "category": meta.get("category", ""),
                    "tags": ",".join(meta.get("tags", []) or []),
                    "enabled": meta.get("enabled", True),
                    "version": meta.get("version", ""),
                }
                items_to_add.append({
                    "content": content,
                    "metadata": metadata,
                    # 用 skill_id 作为稳定 ID，避免重复索引
                    "id": f"skill_{skill_id}",
                })

            if items_to_add:
                if self._st_backend is not None:
                    # BGE-m3 sentence-transformers 模式：自管理向量库
                    try:
                        model, doc_ids, doc_vectors, doc_metas = self._st_backend
                        # 批量编码新增技能的向量
                        contents = [item["content"] for item in items_to_add]
                        # normalize_embeddings=True 让相似度 = 点积
                        new_vectors = model.encode(
                            contents, normalize_embeddings=True,
                            show_progress_bar=False,
                        )
                        import numpy as np
                        if len(doc_vectors) == 0:
                            doc_vectors = new_vectors
                        else:
                            doc_vectors = np.vstack([doc_vectors, new_vectors])
                        for item, vec in zip(items_to_add, new_vectors):
                            doc_ids.append(item["metadata"]["skill_id"])
                            doc_metas.append(item["metadata"])
                            self._indexed_skill_ids.add(item["metadata"]["skill_id"])
                        # 更新 backend 元组
                        self._st_backend = (model, doc_ids, doc_vectors, doc_metas)
                    except Exception as e:  # noqa: BLE001
                        logger.warning(json.dumps({
                            "module_name": "vector_adapter",
                            "action": "sentence_transformers.add.failed",
                            "error": str(e),
                        }, ensure_ascii=False))
                elif self._native_chroma is not None:
                    # chromadb 原生模式：用 collection.add()
                    try:
                        _, collection = self._native_chroma
                        collection.add(
                            ids=[item["id"] for item in items_to_add],
                            documents=[item["content"] for item in items_to_add],
                            metadatas=[item["metadata"] for item in items_to_add],
                        )
                        for skill_id in new_ids:
                            self._indexed_skill_ids.add(skill_id)
                    except Exception as e:  # noqa: BLE001
                        logger.warning(json.dumps({
                            "module_name": "vector_adapter",
                            "action": "native_chroma.add.failed",
                            "error": str(e),
                        }, ensure_ascii=False))
                else:
                    # VectorStore 模式（fallback）
                    try:
                        added_ids = vs.batch_add(items_to_add)
                        for skill_id, _ in zip(new_ids, added_ids):
                            self._indexed_skill_ids.add(skill_id)
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"batch_add failed: {e}, partial index may exist")

            self._index_built = True
            count = len(self._indexed_skill_ids)

            emit_metric(
                "yunshu_skill_vector_index_count",
                value=count, kind="gauge",
                labels={"success": "true"},
            )
            return count

    # ──────────────────────────────────────────────
    #  搜索
    # ──────────────────────────────────────────────

    def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        enabled_only: bool = True,
        min_score: float = 0.01,
    ) -> List[Dict[str, Any]]:
        """向量搜索 — 返回 Top-K 候选技能

        Args:
            query: 用户意图文本
            top_k: 返回数量
            enabled_only: True 时过滤禁用技能（与 SkillLoader.match 一致）
            min_score: 最低相似度阈值，低于此分数的结果被丢弃
                      默认 0.01 与 SkillLoader.match 保持一致

        Returns:
            [{"skill_id": str, "score": float, "metadata": dict}, ...]
            失败时返回空列表（由调用方决定是否回退 TF-IDF）

        失败降级策略:
            - VectorStore 不可用 → 返回空列表
            - ChromaDB 搜索失败 → VectorStore 内部自动降级 BM25
            - BM25 也失败 → 返回空列表（外层回退 TF-IDF）

        负样本防御（【变易】针对 BM25 fallback 模式的启发式过滤）:
            - 纯数字 / 过短查询 / 编程关键字 → 不应召回任何技能
            - 这些场景在 ChromaDB 真实向量模式下不会误召回（语义不相似）
            - 但 BM25 字面匹配可能误命中，需要 adapter 层兜底
        """
        # ── 负样本启发式过滤（解决 BM25 fallback 模式的负样本误召回）──
        # 设计原则（【不易】）：
        # - 不影响 ChromaDB 真实向量模式（该模式下向量已能正确区分语义）
        # - 仅针对 BM25 fallback 模式的字面匹配缺陷做兜底
        # - 启发式规则保守：只过滤明确无语义的查询
        if self._is_negative_query(query):
            logger.info(json.dumps({
                "module_name": "vector_adapter",
                "action": "search.negative_query_filtered",
                "query": query[:50],
                "reason": "matched negative heuristic pattern",
            }, ensure_ascii=False))
            return []

        # 延迟构建索引
        if not self._index_built:
            self.ensure_indexed()

        vs = self._vector_store
        if vs is None:
            logger.warning("VectorStore unavailable, returning empty results")
            return []

        # ── BGE-m3 sentence-transformers 模式：自管理向量库 ──
        if self._st_backend is not None:
            return self._search_sentence_transformers(
                query, top_k=top_k, enabled_only=enabled_only, min_score=min_score,
            )

        # ── chromadb 原生模式：用 collection.query() ──
        if self._native_chroma is not None:
            return self._search_native_chroma(
                query, top_k=top_k, enabled_only=enabled_only, min_score=min_score,
            )

        # ── VectorStore 模式 ──
        try:
            items = vs.search(query, top_k=top_k * 2)  # 多取一些用于 enabled 过滤
        except Exception as e:  # noqa: BLE001
            logger.warning(f"vector search failed: {e}")
            return []

        results: List[Dict[str, Any]] = []
        seen_skills: set = set()
        for item in items:
            metadata = item.metadata or {}
            skill_id = metadata.get("skill_id")
            if not skill_id or skill_id in seen_skills:
                continue

            # enabled_only 过滤（与 SkillLoader.match 保持一致）
            if enabled_only and not metadata.get("enabled", True):
                continue

            # 提取相似度得分（VectorStore 在 metadata 中放 _score）
            score = metadata.get("_score", 0.5)
            # 归一化到 [0, 1] 范围（ChromaDB 距离越小越相似，已转为相似度）
            try:
                score = float(score)
                if score < 0:
                    score = 0.0
                elif score > 1:
                    score = 1.0
            except (TypeError, ValueError):
                score = 0.5

            if score < min_score:
                continue

            results.append({
                "skill_id": skill_id,
                "score": score,
                "metadata": metadata,
            })
            seen_skills.add(skill_id)

            if len(results) >= top_k:
                break

        return results

    def _search_sentence_transformers(
        self,
        query: str,
        *,
        top_k: int,
        enabled_only: bool,
        min_score: float,
    ) -> List[Dict[str, Any]]:
        """BGE-m3 sentence-transformers 模式查询

        使用 numpy 矩阵乘法计算 query 与所有文档的相似度（点积，已归一化）。
        BGE-m3 输出已经是归一化向量，相似度 ∈ [0, 1]。
        """
        if self._st_backend is None:
            return []
        model, doc_ids, doc_vectors, doc_metas = self._st_backend

        if not doc_ids:
            return []

        try:
            import numpy as np
            # 编码 query
            q_vec = model.encode(
                [query], normalize_embeddings=True,
                show_progress_bar=False,
            )[0]  # (dim,)

            # 计算相似度（点积，已归一化）
            sims = doc_vectors @ q_vec  # (N,)

            # 取 top_k * 3 用于 enabled 过滤
            n_candidates = min(len(sims), top_k * 3)
            top_idx = np.argsort(-sims)[:n_candidates]
        except Exception as e:  # noqa: BLE001
            logger.warning(json.dumps({
                "module_name": "vector_adapter",
                "action": "sentence_transformers.query.failed",
                "error": str(e),
            }, ensure_ascii=False))
            return []

        results: List[Dict[str, Any]] = []
        seen_skills: set = set()
        for idx in top_idx:
            idx = int(idx)
            skill_id = doc_ids[idx]
            if skill_id in seen_skills:
                continue

            metadata = dict(doc_metas[idx] or {})
            # enabled_only 过滤
            if enabled_only and not metadata.get("enabled", True):
                continue

            similarity = float(sims[idx])
            # BGE-m3 cosine 相似度可能为负，截断到 [0, 1]
            similarity = max(0.0, min(1.0, similarity))

            if similarity < min_score:
                continue

            results.append({
                "skill_id": skill_id,
                "score": similarity,
                "metadata": metadata,
            })
            seen_skills.add(skill_id)

            if len(results) >= top_k:
                break

        return results

    def _search_native_chroma(
        self,
        query: str,
        *,
        top_k: int,
        enabled_only: bool,
        min_score: float,
    ) -> List[Dict[str, Any]]:
        """chromadb 原生 API 查询

        chromadb 返回 distance（越小越相似），需转为相似度分数：
            similarity = 1 - distance / 2  (cosine distance ∈ [0, 2])
        或更直观：
            similarity = max(0, 1 - distance)  (距离 0 = 完全相同 = 1.0)
        """
        if self._native_chroma is None:
            return []
        _, collection = self._native_chroma

        try:
            # 多取一些用于 enabled 过滤
            qr = collection.query(
                query_texts=[query],
                n_results=top_k * 3,
                include=["metadatas", "distances"],
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(json.dumps({
                "module_name": "vector_adapter",
                "action": "native_chroma.query.failed",
                "error": str(e),
            }, ensure_ascii=False))
            return []

        if not qr["ids"] or not qr["ids"][0]:
            return []

        ids = qr["ids"][0]
        distances = qr["distances"][0]
        metadatas = qr["metadatas"][0] if qr["metadatas"] and qr["metadatas"][0] else [{}] * len(ids)

        results: List[Dict[str, Any]] = []
        seen_skills: set = set()
        for i, (doc_id, dist, meta) in enumerate(zip(ids, distances, metadatas)):
            metadata = dict(meta or {})
            skill_id = metadata.get("skill_id")
            if not skill_id or skill_id in seen_skills:
                continue

            # enabled_only 过滤
            if enabled_only and not metadata.get("enabled", True):
                continue

            # 距离转相似度：cosine distance ∈ [0, 2]，相似度 = 1 - distance/2
            try:
                dist = float(dist)
                similarity = max(0.0, 1.0 - dist / 2.0)
            except (TypeError, ValueError):
                similarity = 0.0

            if similarity < min_score:
                continue

            results.append({
                "skill_id": skill_id,
                "score": similarity,
                "metadata": metadata,
            })
            seen_skills.add(skill_id)

            if len(results) >= top_k:
                break

        return results

    # ──────────────────────────────────────────────
    #  负样本启发式识别
    # ──────────────────────────────────────────────

    # 纯数字 / 纯符号 / 过短查询正则
    # 【不易】保守策略：只过滤明确无语义的查询，避免误伤合法短查询
    _NEGATIVE_PATTERNS = (
        re.compile(r"^[\d\s\W]+$"),                # 纯数字/符号组合：12345, 1 2 3
        re.compile(r"^[a-zA-Z_][a-zA-Z0-9_ ]*$"),  # 纯英文标识符：def print_hello
    )

    # 编程关键字（出现这些词的查询通常不是技能检索意图）
    _PROGRAMMING_KEYWORDS = {
        "def", "function", "class", "import", "return", "print",
        "python", "java", "javascript", "c++", "golang", "rust",
        "programming", "coding", "algorithm",
    }

    def _is_negative_query(self, query: str) -> bool:
        """识别负样本查询 — 用于 BM25 fallback 模式的负样本误召回防御

        判定规则（保守策略，逐条短路）:
            1. 空或单字符 → True
            2. 纯数字/符号组合 → True
            3. 纯英文标识符且无空格分隔（看起来像变量名）→ True
            4. 全部 token 都是编程关键字 → True
            5. 其他情况 → False（保留，走正常向量检索）

        【变易】此规则只在 BM25 fallback 模式下生效兜底作用；
                ChromaDB 真实向量模式下不影响（语义已能区分）
        """
        if not query or not query.strip():
            return True

        q = query.strip()

        # 长度 < 2 的查询（单字符如 "啊" "a"）
        if len(q) < 2:
            return True

        # 匹配纯数字/符号模式
        for pattern in self._NEGATIVE_PATTERNS:
            if pattern.match(q):
                # 但要排除合法的中文短语（如 "安全"、"建议"）
                # 如果包含中文字符，不算负样本
                if not re.search(r"[\u4e00-\u9fa5]", q):
                    return True
                # 包含中文的纯符号/数字组合（如 "123 安全"）仍可能是合法查询
                # 但纯数字/纯符号（无中文）一定是负样本

        # 全部 token 都是编程关键字
        tokens = set(q.lower().split())
        if tokens and tokens.issubset(self._PROGRAMMING_KEYWORDS):
            return True

        return False

    # ──────────────────────────────────────────────
    #  状态查询
    # ──────────────────────────────────────────────

    @property
    def is_available(self) -> bool:
        """向量检索是否可用（后端已初始化且索引已构建）"""
        return self._vector_store is not None and self._index_built

    @property
    def indexed_count(self) -> int:
        """已索引技能数量"""
        return len(self._indexed_skill_ids)

    def health(self) -> Dict[str, Any]:
        """健康检查 — 用于 SkillLoader.health() 聚合"""
        vs_available = self._vector_store is not None
        # 判断实际使用的引擎
        if self._native_chroma is not None:
            engine = "chromadb_native"
        elif vs_available and hasattr(self._vector_store, "_use_chroma"):
            engine = "chromadb" if self._vector_store._use_chroma else "bm25_fallback"
        else:
            engine = "unknown"
        return {
            "vector_available": vs_available,
            "engine": engine,
            "indexed_count": self.indexed_count,
            "collection_name": self.collection_name,
            "model_name": self.model_name if self._native_chroma is None else "all-MiniLM-L6-v2 (onnx)",
        }
