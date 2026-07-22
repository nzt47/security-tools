"""Cross-Encoder Reranker 适配器 — 在 RRF 召回后做精排

设计目的:
    RRF 召回 top-N 候选后，用 Cross-Encoder 精排到 top-K。
    Cross-Encoder 把 (query, doc) 拼接输入模型，输出相关性分数，
    精度高于 Bi-Encoder（BGE-m3）但慢。

架构层级:
    SkillLoader._try_rrf_match (loader.py)
        ↓ RRF 召回 top-N
    SkillReranker (本模块)
        ↓ Cross-Encoder predict
    精排后的 top-K

策略:
    - 模型: BAAI/bge-reranker-v2-m3 (多语言，与 BGE-m3 配套)
    - 输入: query + 技能 description（避免长 body 拖慢推理）
    - 失败降级: Cross-Encoder 不可用时直接返回原顺序
    - 本地缓存优先: 优先从 modelscope/HF 本地缓存加载，避免网络下载

【不易】不修改 RRF 召回逻辑，仅作为可选精排层
【变易】模型名、max_length 可配置；本地缓存路径自动探测
【简易】单一职责：rerank(query, candidates) → sorted_candidates
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("agent.skills_mgmt.reranker")

# 默认配置
_DEFAULT_RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"
_DEFAULT_RERANKER_MAX_LENGTH = 512
_DEFAULT_RERANK_TOP_N = 10  # RRF 召回数量（rerank 前的候选池大小）

# 【变易】rerank_score 阈值：低于此分数的候选视为低置信度，从最终结果中剔除
# 来源：v4 评估数据显示真匹配 rerank_score 0.06~0.99，负样本 ≤0.0005
# 0.05 阈值能拒绝 case_042 "帮我订一张机票" 这类误召回（rerank_score=0.0005）
# 可通过环境变量 SKILL_RERANK_MIN_SCORE 覆盖
_DEFAULT_RERANK_MIN_SCORE = 0.05


def _env_float(name: str, default: float) -> float:
    """从环境变量读取 float，失败时返回默认值（守【简易】）"""
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning(json.dumps({
            "module_name": "reranker",
            "action": "env_parse_failed",
            "env_name": name,
            "raw_value": raw,
            "fallback": default,
        }, ensure_ascii=False))
        return default


# 【变易】本地缓存路径候选（按优先级排序）
# 先查 modelscope 缓存（国内下载更稳定），再查 HF 缓存
def _candidate_local_paths(model_name: str) -> List[str]:
    """枚举本地可能的模型缓存路径"""
    repo_dir = model_name.replace("/", "--")
    home = Path.home()
    candidates = [
        # modelscope 缓存（snapshot 形式）
        home / ".cache" / "modelscope" / "models" / repo_dir / "snapshots" / "master",
        # HF 缓存（snapshot 形式，commit hash 不确定，用通配）
    ]
    # HF 缓存：枚举 snapshots 子目录
    hf_snapshot_root = home / ".cache" / "huggingface" / "hub" / f"models--{repo_dir}" / "snapshots"
    if hf_snapshot_root.exists():
        for sub in hf_snapshot_root.iterdir():
            if sub.is_dir():
                candidates.append(sub)
    return [str(p) for p in candidates if p.exists()]


class SkillReranker:
    """技能检索 Cross-Encoder 精排器

    用法:
        reranker = SkillReranker()
        reranked = reranker.rerank("query", candidates)
        # candidates: [{"skill_id": str, "score": float, ...}, ...]
        # reranked: 同结构，按 Cross-Encoder 分数降序

    线程安全:
        - 模型加载由 threading.Lock 保护
        - predict 本身是只读操作，可并发

    可观测性:
        - 加载耗时 / 推理耗时通过日志上报
        - 失败时降级返回原顺序（不抛异常）
    """

    def __init__(
        self,
        *,
        model_name: str = _DEFAULT_RERANKER_MODEL,
        max_length: int = _DEFAULT_RERANKER_MAX_LENGTH,
        rerank_top_n: int = _DEFAULT_RERANK_TOP_N,
        rerank_min_score: Optional[float] = None,
    ):
        """初始化 Cross-Encoder 精排器

        Args:
            model_name: Cross-Encoder 模型名（默认 BGE-reranker-v2-m3）
            max_length: 输入 token 长度上限（query+doc 总长度）
            rerank_top_n: 召回候选池大小（建议 >= 2*top_k）
            rerank_min_score: rerank_score 阈值，低于此值的候选将被剔除
                None 时读取环境变量 SKILL_RERANK_MIN_SCORE，默认 0.05
                设为负数（如 -1.0）可禁用阈值过滤
        """
        self.model_name = model_name
        self.max_length = max_length
        self.rerank_top_n = rerank_top_n
        # 【变易】阈值过滤：默认从环境变量读取，参数显式传入则覆盖
        if rerank_min_score is None:
            self.rerank_min_score = _env_float(
                "SKILL_RERANK_MIN_SCORE", _DEFAULT_RERANK_MIN_SCORE,
            )
        else:
            self.rerank_min_score = rerank_min_score

        # 延迟加载模型
        self._model = None
        self._lock = threading.Lock()
        self._init_failed = False  # 标记初始化失败，避免重复尝试

    def _ensure_model(self) -> Optional[Any]:
        """延迟加载 Cross-Encoder 模型

        优先策略:
            1. 从本地缓存加载（modelscope/HF 缓存），避免网络下载
            2. 本地缓存不可用时，让 sentence-transformers 走默认下载流程

        失败降级:
            - 模型不可用 → 返回 None，rerank 时跳过精排
            - 初始化失败一次后标记 _init_failed，避免每次都尝试加载

        【变易】可通过环境变量 HF_ENDPOINT 配置镜像
        【简易】本地路径优先，避免网络往返
        """
        if self._init_failed:
            return None
        if self._model is not None:
            return self._model

        with self._lock:
            if self._model is not None:
                return self._model
            try:
                os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
                os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

                from sentence_transformers import CrossEncoder
                import time
                t0 = time.time()

                # 【变易】优先从本地缓存加载，避免网络下载
                local_paths = _candidate_local_paths(self.model_name)
                load_source = None
                if local_paths:
                    # 找到第一个包含 config.json 的目录
                    for p in local_paths:
                        if (Path(p) / "config.json").exists():
                            load_source = p
                            break

                if load_source:
                    logger.info(json.dumps({
                        "module_name": "reranker",
                        "action": "model.init.local_cache",
                        "model": self.model_name,
                        "local_path": load_source,
                    }, ensure_ascii=False))
                    self._model = CrossEncoder(
                        load_source,
                        max_length=self.max_length,
                    )
                else:
                    # 无本地缓存，走默认下载流程
                    logger.info(json.dumps({
                        "module_name": "reranker",
                        "action": "model.init.remote_download",
                        "model": self.model_name,
                    }, ensure_ascii=False))
                    self._model = CrossEncoder(
                        self.model_name,
                        max_length=self.max_length,
                    )
                load_time = time.time() - t0
                logger.info(json.dumps({
                    "module_name": "reranker",
                    "action": "model.init.ok",
                    "model": self.model_name,
                    "max_length": self.max_length,
                    "load_time_sec": round(load_time, 2),
                    "load_source": load_source or "remote",
                }, ensure_ascii=False))
                return self._model
            except Exception as e:  # noqa: BLE001
                logger.warning(json.dumps({
                    "module_name": "reranker",
                    "action": "model.init.failed",
                    "model": self.model_name,
                    "error": str(e)[:300],
                }, ensure_ascii=False))
                self._init_failed = True
                return None

    def rerank(
        self,
        query: str,
        candidates: List[Dict[str, Any]],
        *,
        top_k: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """对 RRF 召回的候选做 Cross-Encoder 精排

        Args:
            query: 用户意图
            candidates: RRF 召回的候选列表（已按 RRF 分数降序）
                       每个 dict 至少包含 skill_id, score, metadata
            top_k: 最终返回数量，None 则返回全部

        Returns:
            精排后的候选列表，按 Cross-Encoder 分数降序
            每个 dict 增加 rerank_score 字段
            rerank_score < rerank_min_score 的候选会被剔除（阈值过滤）
            失败时返回原列表（保留原顺序，不过滤）

        【不易】不修改 candidates 中的原始字段（除新增 rerank_score）
        【变易】rerank_min_score 阈值过滤，剔除低置信度候选
        【简易】单次 batch predict，避免逐条推理
        """
        if not candidates:
            return candidates

        model = self._ensure_model()
        if model is None:
            # 模型不可用，降级返回原顺序（不应用阈值过滤）
            logger.info(json.dumps({
                "module_name": "reranker",
                "action": "rerank.skipped",
                "reason": "model_unavailable",
                "candidate_count": len(candidates),
            }, ensure_ascii=False))
            return candidates[:top_k] if top_k else candidates

        # 取前 rerank_top_n 个候选（按原顺序，已是 RRF 排序）
        pool = candidates[:self.rerank_top_n]

        # 构造 (query, doc) pairs
        # 文档内容用 description（避免长 body 拖慢推理）
        # 【变易】description 为空时用 name 兜底
        pairs = []
        for c in pool:
            meta = c.get("metadata", {}) or {}
            doc_text = meta.get("description") or c.get("name") or c.get("skill_id", "")
            pairs.append((query, doc_text))

        try:
            import time
            t0 = time.time()
            # Cross-Encoder predict 返回相关性分数（可能为负）
            scores = model.predict(pairs)
            elapsed = (time.time() - t0) * 1000

            # 按 rerank 分数降序排序
            indexed = list(enumerate(scores))
            indexed.sort(key=lambda x: -x[1])

            result = []
            filtered_count = 0
            for orig_idx, rerank_score in indexed:
                # 【变易】阈值过滤：rerank_score 低于阈值的候选剔除
                # 仅对排序后的结果过滤，避免阈值过滤导致 top_k 不足时填入低分候选
                if rerank_score < self.rerank_min_score:
                    filtered_count += 1
                    continue
                # 复制原 dict 并添加 rerank_score
                item = dict(pool[orig_idx])
                item["rerank_score"] = float(rerank_score)
                item["original_rank"] = orig_idx + 1  # RRF 中的原排名
                result.append(item)

            logger.info(json.dumps({
                "module_name": "reranker",
                "action": "rerank.ok",
                "query": query[:50],
                "candidate_count": len(pool),
                "filtered_count": filtered_count,
                "remaining_count": len(result),
                "min_score_threshold": self.rerank_min_score,
                "duration_ms": round(elapsed, 2),
                "top1_skill": result[0]["skill_id"] if result else None,
                "top1_rerank_score": round(result[0]["rerank_score"], 4) if result else None,
            }, ensure_ascii=False))

            # top_k 截断（阈值过滤后再截断，避免低分候补填入）
            if top_k:
                return result[:top_k]
            return result

        except Exception as e:  # noqa: BLE001
            logger.warning(json.dumps({
                "module_name": "reranker",
                "action": "rerank.exception",
                "error": str(e)[:300],
            }, ensure_ascii=False))
            # 推理失败，降级返回原顺序（不应用阈值过滤）
            return candidates[:top_k] if top_k else candidates

    def health(self) -> Dict[str, Any]:
        """健康检查"""
        return {
            "ok": self._model is not None,
            "model": self.model_name,
            "max_length": self.max_length,
            "rerank_top_n": self.rerank_top_n,
            "rerank_min_score": self.rerank_min_score,
            "init_failed": self._init_failed,
        }
