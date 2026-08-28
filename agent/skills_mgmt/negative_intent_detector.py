"""v6.2 非技能意图语义检测器 — 基于 BGE-m3 prototype 余弦相似度

设计目的:
    在 v6.1 正则规则未命中后，用语义相似度再判一次。
    覆盖正则无法泛化的句式变化（如"明天会下雨吗"无需写新规则）。

策略:
    - 离线计算每类 prototype 的均值向量，缓存为 numpy 矩阵 (K, 1024)
    - query 来时与所有 prototype 计算余弦相似度
    - max sim > τ → 拒绝（返回类别名 + 相似度）

架构层级:
    SkillLoader.match (loader.py)
        ↓ v6.1 _match_query_pattern 未命中
    SkillLoader._match_intent_by_embedding (loader.py)
        ↓ 调用
    NegativeIntentDetector.detect (本模块)
        ↓ encode_query
    SkillVectorAdapter.encode_query (vector_adapter.py)
        ↓ BGE-m3 model.encode
    query 归一化向量 → 与 prototype 矩阵点积 → max sim

【不易】不修改 SkillVectorAdapter/SkillReranker，仅作为新增可选层
【变易】prototype 数据外部化（JSON），阈值可通过环境变量调整
【简易】单文件单类，无新依赖（复用 numpy + SkillVectorAdapter 的模型）
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from agent.logging_utils import log_dict

logger = logging.getLogger("agent.skills_mgmt.negative_intent_detector")

# 默认配置
_DEFAULT_PROTOTYPES_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "tests" / "eval" / "negative_intent_prototypes.json"
)
_DEFAULT_THRESHOLD = 0.75  # BGE-m3 中文相似度经验值，需 calibrate_v62_threshold.py 校准


def _env_float(name: str, default: float) -> float:
    """从环境变量读取 float，失败时返回默认值（守【简易】）"""
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning(log_dict({'module_name': 'negative_intent_detector', 'action': 'env_parse_failed', 'env_name': name, 'raw_value': raw, 'fallback': default}))
        return default


class NegativeIntentDetector:
    """非技能意图语义检测器

    用法:
        adapter = SkillVectorAdapter(...)
        detector = NegativeIntentDetector(
            vector_adapter=adapter,
            prototypes_path="tests/eval/negative_intent_prototypes.json",
        )
        result = detector.detect("今天天气怎么样", tid="t1", t0=0.0)
        # result: ("weather", 0.82, "negative_intent") 或 None

    线程安全:
        - prototype 加载由 threading.Lock 保护
        - detect 中编码 + 相似度计算为只读操作，可并发

    失败降级:
        - 模型不可用 → 返回 None（放行到 RRF）
        - prototype 加载失败 → 返回 None
        - 编码异常 → 返回 None
    """

    def __init__(
        self,
        vector_adapter: Any,
        *,
        prototypes_path: Optional[str] = None,
        threshold: Optional[float] = None,
    ):
        """初始化检测器

        Args:
            vector_adapter: SkillVectorAdapter 实例（提供 encode_query 方法）
            prototypes_path: prototype JSON 路径，None 时用默认路径
            threshold: 相似度阈值，None 时读环境变量 SKILL_NEGATIVE_INTENT_THRESHOLD
                       默认 0.75；显式传入则覆盖环境变量
        """
        self._vector_adapter = vector_adapter
        self._prototypes_path = Path(
            prototypes_path or _DEFAULT_PROTOTYPES_PATH
        )

        # 【变易】阈值：默认从环境变量读取，参数显式传入则覆盖
        if threshold is None:
            self._threshold = _env_float(
                "SKILL_NEGATIVE_INTENT_THRESHOLD", _DEFAULT_THRESHOLD,
            )
        else:
            self._threshold = threshold

        # 懒加载状态
        self._loaded = False
        self._lock = None  # 延迟创建锁，避免 import 时拉起 threading
        # 缓存: prototype 矩阵 (K, dim) + 类别列表
        self._proto_matrix = None  # np.ndarray (K, dim)
        self._categories: List[str] = []
        # 原始样本（供测试与审计）
        self._raw_samples: Dict[str, List[str]] = {}

    def _load_prototypes(self) -> bool:
        """懒加载 prototype 数据并编码为矩阵

        Returns:
            True 加载成功；False 加载失败（文件不存在/编码失败）

        【不易】加载失败不抛异常，返回 False 由 detect 降级
        """
        if self._loaded:
            return self._proto_matrix is not None

        if self._lock is None:
            import threading
            self._lock = threading.Lock()

        with self._lock:
            if self._loaded:
                return self._proto_matrix is not None

            try:
                # 1. 读取 JSON
                if not self._prototypes_path.exists():
                    logger.warning(log_dict({'module_name': 'negative_intent_detector', 'action': 'prototypes.not_found', 'path': str(self._prototypes_path)}))
                    self._loaded = True
                    return False

                with open(self._prototypes_path, "r", encoding="utf-8") as f:
                    data = json.load(f)

                categories_data = data.get("categories", [])
                if not categories_data:
                    logger.warning(log_dict({'module_name': 'negative_intent_detector', 'action': 'prototypes.empty', 'path': str(self._prototypes_path)}))
                    self._loaded = True
                    return False

                # 2. 编码每个样本，按类别取均值
                import numpy as np

                self._categories = []
                self._raw_samples = {}
                proto_vectors = []  # 每类一个均值向量

                for cat in categories_data:
                    cat_name = cat["category"]
                    samples = cat.get("samples", [])
                    if not samples:
                        continue

                    self._categories.append(cat_name)
                    self._raw_samples[cat_name] = samples

                    # 编码该类所有样本
                    sample_vecs = []
                    for s in samples:
                        vec = self._vector_adapter.encode_query(s)
                        if vec is not None:
                            sample_vecs.append(vec)

                    if not sample_vecs:
                        # 该类所有样本编码失败，跳过
                        logger.warning(log_dict({'module_name': 'negative_intent_detector', 'action': 'category.encode_all_failed', 'category': cat_name}))
                        # 回滚已添加的类别
                        self._categories.pop()
                        self._raw_samples.pop(cat_name)
                        continue

                    # 取均值并归一化（均值向量需重新归一化以保持余弦相似度语义）
                    mean_vec = np.mean(sample_vecs, axis=0)
                    norm = np.linalg.norm(mean_vec)
                    if norm > 0:
                        mean_vec = mean_vec / norm
                    proto_vectors.append(mean_vec)

                if not proto_vectors:
                    logger.warning(log_dict({'module_name': 'negative_intent_detector', 'action': 'prototypes.no_valid_vectors'}))
                    self._loaded = True
                    return False

                # 3. 堆叠为矩阵 (K, dim)
                self._proto_matrix = np.stack(proto_vectors, axis=0)

                logger.info(log_dict({'module_name': 'negative_intent_detector', 'action': 'prototypes.loaded', 'category_count': len(self._categories), 'matrix_shape': list(self._proto_matrix.shape), 'threshold': self._threshold}))

                self._loaded = True
                return True

            except Exception as e:  # noqa: BLE001
                logger.warning(log_dict({'module_name': 'negative_intent_detector', 'action': 'prototypes.load_failed', 'error': str(e)[:300]}))
                self._loaded = True
                return False

    def detect(
        self,
        query: str,
        *,
        tid: str,
        t0: float,
    ) -> Optional[Tuple[str, float, str]]:
        """检测 query 是否为非技能意图

        Args:
            query: 用户意图文本
            tid: trace_id（用于可观测性日志）
            t0: 起始时间戳（用于计算 elapsed_ms）

        Returns:
            None: 未命中（放行到 RRF）或检测器降级
            (category, similarity, retrieval_method): 命中，retrieval_method="negative_intent"

        【不易】任何失败都返回 None（放行），不抛异常
        【变易】环境变量开关 SKILL_NEGATIVE_INTENT_ENABLED 控制启用
        【简易】单次 encode + 矩阵点积，O(K*dim) 复杂度
        """
        # 环境变量开关（默认开启）
        enabled = os.environ.get(
            "SKILL_NEGATIVE_INTENT_ENABLED", "true"
        ).lower()
        if enabled in ("false", "0", "off", "no"):
            return None

        if not query:
            return None

        # 懒加载 prototypes
        if not self._loaded:
            if not self._load_prototypes():
                return None  # 加载失败降级

        if self._proto_matrix is None or not self._categories:
            return None

        # 编码 query
        try:
            q_vec = self._vector_adapter.encode_query(query)
            if q_vec is None:
                # 模型不可用，降级
                return None

            import numpy as np

            # 计算相似度（点积，已归一化）
            # q_vec: (dim,), proto_matrix: (K, dim)
            sims = self._proto_matrix @ q_vec  # (K,)

            # 找最大相似度
            max_idx = int(np.argmax(sims))
            max_sim = float(sims[max_idx])
            matched_category = self._categories[max_idx]

            # 阈值判定
            if max_sim < self._threshold:
                return None

            elapsed = (time.time() - t0) * 1000
            logger.info(log_dict({'module_name': 'negative_intent_detector', 'action': 'detect.rejected', 'intent': query[:100], 'category': matched_category, 'similarity': round(max_sim, 4), 'threshold': self._threshold}))

            return (matched_category, max_sim, "negative_intent")

        except Exception as e:  # noqa: BLE001
            logger.warning(log_dict({'module_name': 'negative_intent_detector', 'action': 'detect.exception', 'error': str(e)[:300]}))
            return None

    def health(self) -> Dict[str, Any]:
        """健康检查"""
        return {
            "enabled": os.environ.get(
                "SKILL_NEGATIVE_INTENT_ENABLED", "true"
            ).lower() not in ("false", "0", "off", "no"),
            "threshold": self._threshold,
            "loaded": self._loaded,
            "category_count": len(self._categories),
            "matrix_shape": (
                list(self._proto_matrix.shape)
                if self._proto_matrix is not None else None
            ),
            "prototypes_path": str(self._prototypes_path),
        }
