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

# ============================================================================
# 【2026-10-03 · 死代码收口】**本模块当前无生产调用方**（决策：保留，不删）。
#
# ① 现状：loader 侧的懒加载钩子（_get_negative_intent_detector / _match_intent_by_embedding）
#    是随 commit 1159d88f 被**主动删掉**的（不是漏删）；A-2 又删掉了它支撑的 5 条 v6.2 意图告警
#    ⇒ 本仓只剩"非生产"引用：
#      - tests/unit/test_negative_intent.py（单测，随本文件一起保留）
#      - scripts/verify_skill_retrieval_metrics.py:123-142、scripts/calibrate_v62_threshold.py
#      - agent/settings/registry.py:1537-1543 仍登记 SKILL_NEGATIVE_INTENT_ENABLED / _THRESHOLD
#        并把 owner 指到本文件（**登记口径，不代表有调用方**）
#      - agent/skills_mgmt/vector_adapter.py:1229 的注释、docs/refactor_archive/ 的归档件
#
# ② 它的两个指标仍"活着但恒为 0"：yunshu_negative_intent_detector_failed_total /
#    yunshu_negative_intent_duration_ms 已在 agent/monitoring/business_metrics.py:612/623 登记、
#    并出现在 /metrics —— 但没有生产调用方 ⇒ 永远为 0。
#    （与 A-1 的 SafeFileReader 指标同病；规则侧的说明见
#     monitoring/prometheus/rules/yunshu-v6-query-pattern-alerts.yml:107）
#
# ③ 为什么保留：检测器本身完整可复用（BGE-m3 prototype 余弦 + 阈值校准脚本 + 原型数据齐备），
#    它缺的只是"被接回检索链路"这一根线；那是一次有意的重构回退，要重做基线，属独立立项。
#
# ④ 恢复路径（A-2 恢复清单的第①步，原文见
#    docs/closeout/监控死规则与陈旧看板清理_20261002.md §3 表 A-2）：
#    docs/refactor_archive/loader_v6_query_patterns_1159d88f-prior.py —— 该归档件保留了
#    commit 1159d88f **之前**的 loader，含 _get_negative_intent_detector 懒加载与它的调用点。
#    按它把钩子接回 agent/skills_mgmt/loader.py，并**同时**补上 SKILL_NEGATIVE_INTENT_ENABLED 的读取；
#    之后才值得 git show <A-2 删除提交>^:<规则文件> 取回那 5 条告警。
#    注意：loader.py 现已是 6 旋钮版本（见 docs/closeout/能力层重构交付报告_20261001.md D-1），
#    接回前请先重做检索基线（接回会改变检索链路的结果质量）。
# ============================================================================
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from agent.logging_utils import log_dict

# [埋点] 复用 skills_mgmt 统一指标门面（内部走 get_business_metrics_collector() 全局单例，
# 与 app_server 的 /api/business/prometheus 端点共享同一实例）。
# 【失败隔离】observability.emit_metric 内部已 try/except；即使导入失败也只降级为 no-op，
# 绝不能让"埋点"把检测器（乃至检索主流程）带崩。
try:
    from .observability import emit_metric
except Exception:  # noqa: BLE001  独立脚本/异常环境下退化为 no-op
    def emit_metric(name, *, value=1.0, labels=None, kind="counter"):  # type: ignore[misc]
        return None

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


def _record_detector_failed(reason: str) -> None:
    """[埋点] 记录检测器降级/失败（yunshu_negative_intent_detector_failed_total）

    【为什么统一收口到一个函数】失败点有 5 处（prototype 文件缺失/为空/无有效向量/
    加载异常/某类别全编码失败），散落各处的重复埋点最容易漏；收口后 reason 取值
    有限且可枚举，Prometheus 侧不会出现标签爆炸。
    【单位】counter 按"次"计，value=1。
    【失败隔离】emit_metric 内部 try/except，本函数不再二次抛错。
    """
    emit_metric("yunshu_negative_intent_detector_failed_total", value=1,
                kind="counter", labels={"reason": reason, "success": "false"})


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
                    _record_detector_failed("prototypes_not_found")
                    self._loaded = True
                    return False

                with open(self._prototypes_path, "r", encoding="utf-8") as f:
                    data = json.load(f)

                categories_data = data.get("categories", [])
                if not categories_data:
                    logger.warning(log_dict({'module_name': 'negative_intent_detector', 'action': 'prototypes.empty', 'path': str(self._prototypes_path)}))
                    _record_detector_failed("prototypes_empty")
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
                        _record_detector_failed("category_encode_all_failed")
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
                    _record_detector_failed("prototypes_no_valid_vectors")
                    self._loaded = True
                    return False

                # 3. 堆叠为矩阵 (K, dim)
                self._proto_matrix = np.stack(proto_vectors, axis=0)

                logger.info(log_dict({'module_name': 'negative_intent_detector', 'action': 'prototypes.loaded', 'category_count': len(self._categories), 'matrix_shape': list(self._proto_matrix.shape), 'threshold': self._threshold}))

                self._loaded = True
                return True

            except Exception as e:  # noqa: BLE001
                logger.warning(log_dict({'module_name': 'negative_intent_detector', 'action': 'prototypes.load_failed', 'error': str(e)[:300]}))
                _record_detector_failed("prototypes_load_failed")
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

        # [埋点] detect 耗时起点（毫秒口径 —— 指标名以 _ms 结尾，见改造规范）。
        # 起点放在"开关/空串检查"之后：那两条是零成本的快速返回，计入会稀释分布。
        _t_detect = time.perf_counter()

        def _finish(result_label: str):
            """[埋点] 收敛所有 return 点的耗时记录：先记 histogram 再原样返回 None。

            【为什么用闭包而不是 try/finally】detect 的返回值有 None 与三元组两种，
            try/finally 无法区分"放行/拒绝/降级"，而这三者的耗时分布正是运维要区分的
            （违规长尾往往只在某一条路径上）。闭包只改 return 语句，不改控制流。
            """
            try:
                # success 标签如实区分"正常判定"与"降级/异常"——否则一行
                # result="degraded_*" 的慢样本会被 success="true"（emit_metric 的
                # 默认补标签）误导成"健康路径上的长尾"。
                _ok = "false" if result_label.startswith(("degraded", "error")) else "true"
                emit_metric("yunshu_negative_intent_duration_ms",
                            value=(time.perf_counter() - _t_detect) * 1000.0,
                            kind="histogram",
                            labels={"result": result_label, "success": _ok})
            except Exception:  # noqa: BLE001  埋点失败隔离
                pass
            return None

        # 懒加载 prototypes
        if not self._loaded:
            if not self._load_prototypes():
                return _finish("degraded_prototypes")  # 加载失败降级

        if self._proto_matrix is None or not self._categories:
            return _finish("degraded_no_prototypes")

        # 编码 query
        try:
            q_vec = self._vector_adapter.encode_query(query)
            if q_vec is None:
                # 模型不可用，降级
                _record_detector_failed("encode_query_unavailable")
                return _finish("degraded_encode")

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
                return _finish("passed")

            elapsed = (time.time() - t0) * 1000
            logger.info(log_dict({'module_name': 'negative_intent_detector', 'action': 'detect.rejected', 'intent': query[:100], 'category': matched_category, 'similarity': round(max_sim, 4), 'threshold': self._threshold}))

            _finish("rejected")
            return (matched_category, max_sim, "negative_intent")

        except Exception as e:  # noqa: BLE001
            logger.warning(log_dict({'module_name': 'negative_intent_detector', 'action': 'detect.exception', 'error': str(e)[:300]}))
            _record_detector_failed("detect_exception")
            return _finish("error")

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
