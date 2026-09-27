# -*- coding: utf-8 -*-
r"""经验库检索索引（方案 P2a）。

【设计原则】**组合复用，零改动核心检索栈**。
    不修改 bm25_searcher.py / vector_adapter.py / loader.py 任何一行，改为：
      - 词汇腿：直接实例化既有 BM25SkillSearcher（真复用）；
      - 向量腿：实例化既有 SkillVectorAdapter，用独立的 collection_name + persist_dir
        与经验语料隔离（适配器只依赖 file_store 的 load_metadata_index()，
        故提供一个 duck-typed 桩即可）。
      - 融合：沿用技能链同一套 RRF（k=60），与 loader.py:1423 口径一致。

【为什么用组合而非改核心】P0 审计结论：三条腿的分数量纲不同（工具链 alpha 线性
    融合、技能链加权 RRF），强行改造核心会波及既有技能检索。组合方案天然隔离，
    且出问题时可整体摘除本模块而不影响技能链。

【落盘】data/skill_vectors/experience/（技能链真实落盘处，非 skills_repo/.vector_index
    —— 后者经实测是仅含 .gitkeep 的死目录）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any, Dict, Iterable, List, Optional

from agent.logging_utils import log_dict

# ── 模型加载离线化（关键性能修复）──
# 【实测】SentenceTransformer("BAAI/bge-m3") 在联网检查下会对 hf-mirror.com 发起
# 多次 HEAD 请求（每个 config 文件 5 次重试 + 指数退避），实测模型加载耗时
# **约 25 分钟且最终以 Traceback 崩溃**；置 HF_HUB_OFFLINE=1 后为 **2.3 秒**。
# 本地模型权重已存在，网络探测纯属浪费，故默认离线；确需拉取新模型时可显式
# 设 HF_HUB_OFFLINE=0 覆盖。
# 【影响面】技能链（vector_adapter）走同一模型加载路径，存在同样问题 ——
# 建议在部署层 .env 统一设置 HF_HUB_OFFLINE=1。
os.environ.setdefault("HF_HUB_OFFLINE", "1")

logger = logging.getLogger(__name__)

#: RRF 常数，与 agent/skills_mgmt/loader.py:970 _RRF_K 保持一致
_RRF_K = 60
#: 双腿权重（沿用技能链默认口径 tfidf/vector/bm25 → 此处只有 vector/bm25）
_DEFAULT_WEIGHTS = {"vector": 0.6, "bm25": 0.4}

_DEFAULT_PERSIST_DIR = os.path.join("data", "skill_vectors", "experience")
_DEFAULT_COLLECTION = "experience"
#: 相关性下限（BM25 原始分）。低于此分判定"未命中"，返回空列表。
#:
#: ⚠️ **【本阈值已被后续实测证伪其标定依据，勿据其注释信任其有效性】**
#:
#: 原标定（eval/sweep_threshold.py，505 条语料 / 12 正题 / 8 负题）曾显示
#: 正题 top1 落在 36.4~363.0、负题 8.9~24.9，看上去"完全可分，间隔 [24.9, 36.4]"。
#:
#: 但独立复核证明：**那个间隔是"查询长度"造成的，不是"相关 vs 不相关"** ——
#: BM25 原始分是**查询词上的求和、不做长度归一**，所以在同一份语料、同一个期望条目上
#: 只把查询截短就跌破阈值（实测 pos-01：8 词元 71.92 → 4 词元 24.02 → 2 词元 7.34）。
#: 正题被构造成 8 词元、负题 2~6 词元，故"间隔"实为两种查询长度的分界。
#:
#: 真实自然提问的实测分布在 **[7.4, 19.3]**，而离题提问是 **[8.9, 24.9]** ——
#: **前者完全落在后者内部** ⇒ **不存在任何单一阈值能同时放行自然提问与拦截离题提问**。
#:
#: 因此本常量当前的**实际效果**是：自然提问几乎 100% 被拒（P4 注入恒空），
#: 而 eval 仍报 recall@5=100%（因为 eval 的题不是自然提问）。**该 KPI 不可外推。**
#:
#: 待决（见 设计思路/项目结项报告.md §四）：分数长度归一 / 改用向量余弦 / 取消闸门。
#: 在此之前请保持 config.yaml 的 experience_enabled=false。
_DEFAULT_MIN_BM25_SCORE = 30.0

#: 向量缓存文件名（纯内存后端的自救：避免每次重启重编码整个语料）
_VEC_CACHE = "vectors.npz"
_HASH_CACHE = "content_hashes.json"


class _ExperienceMetaStore:
    """Duck-typed SkillFileStore —— 只实现 SkillVectorAdapter 实际调用到的方法。

    实测 SkillVectorAdapter 对 file_store 的全部调用点仅 3 处，且只有一个方法：
        self.fs.load_metadata_index()   （vector_adapter.py:595 / 833 / 1007）
    故无需实现完整 SkillFileStore。
    """

    def __init__(self, docs: Dict[str, Dict[str, Any]]):
        self._docs = docs

    def load_metadata_index(self, refresh: bool = False) -> Dict[str, Dict[str, Any]]:
        return dict(self._docs)

    # 适配器若读取正文，返回空串（经验条目无 skill.md 正文）
    def read_body(self, skill_id: str, **kwargs) -> str:
        return ""

    def read_skill_md(self, skill_id: str, **kwargs) -> str:
        return ""


def _sample_to_meta(s: Dict[str, Any]) -> Dict[str, Any]:
    """把经验样本映射为 loader._meta_to_meta_text 认识的字段集。

    字段口径：name / description / description_zh / tags / category
    （与 bm25_searcher._skill_to_doc → loader._meta_to_meta_text 一致）
    """
    task = (s.get("task") or "").strip().replace("\n", " ")
    stack = s.get("stack") or {}
    paths = [d.get("path", "") for d in (s.get("diffs") or [])][:8]
    tags = [s.get("task_type") or "unknown", stack.get("lang") or "unknown"]
    tags += list(stack.get("frameworks") or [])[:4]
    names = [os.path.basename(p) for p in paths if p][:5]
    return {
        "name": (task[:60] or s.get("id", "")) ,
        "description": task[:1500],
        "description_zh": task[:1500],
        "tags": [t for t in tags if t],
        "category": "experience",
        "file_names": names,
        # 经验侧专有（供检索后过滤/展示，不参与文本匹配）
        "_verified": s.get("verified"),
        "_task_type": s.get("task_type"),
        "_lang": stack.get("lang"),
        "_files_changed": stack.get("files_changed", 0),
        "_created_at": s.get("created_at"),
        "_deprecated_after": s.get("deprecated_after"),
        "_n_diffs": len(s.get("diffs") or []),
        "_n_pitfalls": len(s.get("pitfalls") or []),
    }


class ExperienceIndex:
    """经验库的双腿检索索引。"""

    def __init__(
        self,
        samples_path: str,
        *,
        persist_dir: str = _DEFAULT_PERSIST_DIR,
        collection_name: str = _DEFAULT_COLLECTION,
        use_vector: bool = True,
        weights: Optional[Dict[str, float]] = None,
    ):
        self.samples_path = samples_path
        self.persist_dir = persist_dir
        self.collection_name = collection_name
        self.use_vector = use_vector
        self.weights = dict(weights or _DEFAULT_WEIGHTS)
        self._docs: Dict[str, Dict[str, Any]] = {}
        self._bm25 = None
        self._vector = None
        self._loaded = False

    # ── 载入 ──
    def load(self) -> int:
        docs: Dict[str, Dict[str, Any]] = {}
        if not os.path.isfile(self.samples_path):
            logger.warning(log_dict({"module_name": "experience_index",
                                     "action": "load.missing", "path": self.samples_path}))
            return 0
        with open(self.samples_path, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    s = json.loads(line)
                except Exception:
                    continue
                sid = s.get("id")
                if sid:
                    docs[sid] = _sample_to_meta(s)
        self._docs = docs
        self._loaded = True
        logger.info(log_dict({"module_name": "experience_index", "action": "load.ok",
                              "count": len(docs), "path": self.samples_path}))
        return len(docs)

    # ── 向量缓存（解决纯内存后端冷启动重编码）──
    def _fingerprint(self) -> str:
        """语料指纹：由 id + 真正参与向量化的文本决定。文本变则指纹变。"""
        h = hashlib.sha256()
        for sid in sorted(self._docs):
            h.update(sid.encode("utf-8"))
            h.update(b"\x00")
            h.update(json.dumps(self._docs[sid], sort_keys=True,
                                ensure_ascii=False).encode("utf-8"))
            h.update(b"\x01")
        return h.hexdigest()

    def _cache_files(self):
        return (os.path.join(self.persist_dir, _VEC_CACHE),
                os.path.join(self.persist_dir, _HASH_CACHE))

    def _try_restore(self, adapter: Any) -> int:
        """命中缓存则把向量与增量状态回灌适配器，使 ensure_indexed 短路返回。

        【原理】SkillVectorAdapter 本身就有按 _indexed_content_hash 的增量短路
        （vector_adapter.py:873-874 content_unchanged 直接 return）。故只要把
        _st_backend / _indexed_skill_ids / _indexed_content_hash 恢复成"已索引"状态，
        它就不会重新编码 —— 无需改动适配器一行。
        """
        vec_f, hash_f = self._cache_files()
        if not (os.path.isfile(vec_f) and os.path.isfile(hash_f)):
            return 0
        try:
            with open(hash_f, encoding="utf-8") as fh:
                saved = json.load(fh)
            if saved.get("fingerprint") != self._fingerprint():
                logger.info(log_dict({"module_name": "experience_index",
                                      "action": "cache.stale", "reason": "fingerprint_changed"}))
                return 0
            import numpy as np
            ids = [str(i) for i in (saved.get("ids") or [])]
            # 【防御】缓存可能来自另一份语料：id 必须全部存在于当前 _docs，
            # 否则直接拒绝（否则下面的 _docs[i] 会 KeyError，被吞掉后静默退回全量编码）。
            if not ids or any(i not in self._docs for i in ids):
                logger.warning(log_dict({"module_name": "experience_index",
                                          "action": "cache.id_mismatch", "count": len(ids)}))
                return 0
            vectors = np.load(vec_f, allow_pickle=False)["vectors"]
            if vectors.shape[0] != len(ids) or vectors.shape[0] != len(self._docs):
                return 0

            adapter._ensure_vector_store()          # 载入模型（本地缓存，约数秒）
            backend = getattr(adapter, "_st_backend", None)
            if not backend:
                return 0
            model = backend[0]
            metas = [dict(self._docs[i]) for i in ids]
            adapter._st_backend = (model, list(ids), vectors, metas)
            adapter._indexed_skill_ids = set(ids)
            adapter._indexed_content_hash = dict(saved.get("content_hashes") or {})
            # 【必需】ensure_indexed 的短路条件是
            #   "not dirty_ids and self._index_built"（vector_adapter.py:872），
            # 且 full_rebuild = force or (not _index_built)（:858）。
            # 漏设 _index_built 会导致 full_rebuild=True ⇒ 每次全量重编码、缓存形同虚设；
            # 而两条路径返回值相同（均为 len(_indexed_skill_ids)），无法从返回值察觉。
            adapter._index_built = True
            n = int(adapter.ensure_indexed() or 0)   # 内容未变 ⇒ 短路
            logger.info(log_dict({"module_name": "experience_index",
                                  "action": "cache.restored", "count": n}))
            return n
        except Exception as exc:  # noqa: BLE001 - 缓存失败一律退回全量编码
            logger.warning(log_dict({"module_name": "experience_index",
                                     "action": "cache.restore_failed", "error": str(exc)}))
            return 0

    def _save_cache(self, adapter: Any) -> None:
        try:
            backend = getattr(adapter, "_st_backend", None)
            if not backend:
                return
            _model, ids, vectors, _metas = backend
            if not ids or vectors is None or len(ids) != len(vectors):
                return
            import numpy as np
            vec_f, hash_f = self._cache_files()
            # 【不易】npz 只存 float32 向量：ids 若以 dtype=object 存入，
            # 读取时 allow_pickle=False 会直接报错（实测导致缓存恢复静默失效、
            # 回退全量重编码 1369s）。故 ids 一律走 JSON。
            np.savez_compressed(vec_f, vectors=np.asarray(vectors, dtype="float32"))
            with open(hash_f, "w", encoding="utf-8") as fh:
                json.dump({"fingerprint": self._fingerprint(),
                           "ids": [str(i) for i in ids],
                           "content_hashes": dict(getattr(adapter, "_indexed_content_hash", {})),
                           "count": len(ids)},
                          fh, ensure_ascii=False)
            logger.info(log_dict({"module_name": "experience_index",
                                  "action": "cache.saved", "count": len(ids)}))
        except Exception as exc:  # noqa: BLE001
            logger.warning(log_dict({"module_name": "experience_index",
                                     "action": "cache.save_failed", "error": str(exc)}))

    # ── 双腿构建 ──
    def build(self) -> Dict[str, Any]:
        if not self._loaded:
            self.load()
        out: Dict[str, Any] = {"docs": len(self._docs), "bm25": 0, "vector": 0}

        try:
            from agent.skills_mgmt.bm25_searcher import BM25SkillSearcher
            searcher = BM25SkillSearcher()
            searcher.build_index([dict(v, id=k) for k, v in self._docs.items()])
            self._bm25 = searcher
            out["bm25"] = len(self._docs) if searcher.is_available() else 0
        except Exception as exc:  # noqa: BLE001
            logger.warning(log_dict({"module_name": "experience_index",
                                     "action": "build.bm25_failed", "error": str(exc)}))

        if self.use_vector:
            try:
                from agent.skills_mgmt.vector_adapter import SkillVectorAdapter
                os.makedirs(self.persist_dir, exist_ok=True)
                adapter = SkillVectorAdapter(
                    _ExperienceMetaStore(self._docs),
                    collection_name=self.collection_name,
                    persist_dir=self.persist_dir,
                )
                # 先试向量缓存：命中则免去全量重编码（纯内存后端的冷启动自救）
                n = self._try_restore(adapter)
                out["vector_cached"] = n
                if n == 0:
                    n = int(adapter.ensure_indexed() or 0)
                    if n:
                        self._save_cache(adapter)
                out["vector"] = n
                self._vector = adapter
            except Exception as exc:  # noqa: BLE001
                logger.warning(log_dict({"module_name": "experience_index",
                                         "action": "build.vector_failed", "error": str(exc)}))
        return out

    # ── 检索 ──
    def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        lang: Optional[str] = None,
        task_type: Optional[str] = None,
        include_unverified: bool = False,
        min_bm25_score: float = _DEFAULT_MIN_BM25_SCORE,
    ) -> List[Dict[str, Any]]:
        """RRF 融合检索。返回 [{id, score, meta, legs}]。

        默认只返回 verified=pass 的条目（方案硬约束 ⑤：未验证的不入库）。

        【相关性下限 min_bm25_score】RRF 分数是**排名派生量**（rank1 恒为 w/(k+1)），
        没有绝对意义 ⇒ 不加下限时系统对任何查询都会返回 top-K，
        「命中率」这一方案核心 KPI 因此无法计算（实测：8/8 负题全部"有命中"）。
        故以 BM25 **原始分**作为相关性度量，低于下限即判定未命中、返回空。
        默认值取自 eval/sweep_threshold.py 的实测可分间隔，见常量注释。
        """
        if not query or not self._docs:
            return []

        def _ok(sid: str) -> bool:
            m = self._docs.get(sid) or {}
            if lang and m.get("_lang") != lang:
                return False
            if task_type and m.get("_task_type") != task_type:
                return False
            if not include_unverified and m.get("_verified") != "pass":
                return False
            return True

        ranks: Dict[str, Dict[str, int]] = {}
        raw_bm25: Dict[str, float] = {}
        if self._bm25 is not None:
            try:
                bm25_hits = self._bm25.search(query, top_k=top_k * 4)
                # 相关性下限：原始分不足即判定"未命中"，不返回任何结果。
                # 注意判定用的是**未经 lang/task_type/verified 过滤**的 top1 原始分 ——
                # 下限衡量的是"这条查询是否与本库相关"，与被过滤掉的条目无关。
                if min_bm25_score > 0 and bm25_hits and float(bm25_hits[0].score) < min_bm25_score:
                    logger.info(log_dict({"module_name": "experience_index",
                                          "action": "search.below_threshold",
                                          "top_score": round(float(bm25_hits[0].score), 3),
                                          "threshold": min_bm25_score}))
                    return []
                for r, hit in enumerate(bm25_hits, 1):
                    sid = getattr(hit, "skill_id", None) or (hit.get("skill_id") if isinstance(hit, dict) else None)
                    if sid and _ok(sid):
                        ranks.setdefault(sid, {})["bm25"] = r
                        raw_bm25[sid] = float(getattr(hit, "score", 0.0) or 0.0)
            except Exception as exc:  # noqa: BLE001
                logger.warning(log_dict({"module_name": "experience_index",
                                         "action": "search.bm25_failed", "error": str(exc)}))
        raw_vector: Dict[str, float] = {}
        if self._vector is not None:
            try:
                for r, hit in enumerate(self._vector.search(query, top_k=top_k * 4, enabled_only=False), 1):
                    sid = hit.get("skill_id") if isinstance(hit, dict) else None
                    if sid and _ok(sid):
                        ranks.setdefault(sid, {})["vector"] = r
                        raw_vector[sid] = float(hit.get("score", 0.0) or 0.0)
            except Exception as exc:  # noqa: BLE001
                logger.warning(log_dict({"module_name": "experience_index",
                                         "action": "search.vector_failed", "error": str(exc)}))

        scored = []
        for sid, legs in ranks.items():
            score = 0.0
            for leg, rank in legs.items():
                score += self.weights.get(leg, 0.0) / (_RRF_K + rank)
            scored.append({"id": sid, "score": round(score, 8),
                           "meta": self._docs[sid], "legs": legs,
                           # 原始 BM25 分（非 RRF 排名派生量）—— 供人工抽检/阈值调试判读
                           "raw_bm25": round(raw_bm25.get(sid, 0.0), 3),
                           # 原始余弦相似度（向量腿）—— 与 BM25 分数量纲不同，勿混用
                           "raw_vector": round(raw_vector.get(sid, 0.0), 4)})
        scored.sort(key=lambda x: (-x["score"], x["id"]))
        return scored[:top_k]


_INDEX: Optional[ExperienceIndex] = None


def get_experience_index(samples_path: str = "data/experience/samples.ndjson",
                         **kw) -> ExperienceIndex:
    """进程内单例（与 capregistry.skillsearch 的单例风格一致）。"""
    global _INDEX
    if _INDEX is None:
        _INDEX = ExperienceIndex(samples_path, **kw)
        _INDEX.load()
    return _INDEX
