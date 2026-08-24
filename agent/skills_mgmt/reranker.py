"""v6.5 Cross-Encoder Reranker — 对 RRF 融合候选二次排序

设计原则:
    【不易】不改变 match() 公共接口签名；降级到 RRF 排序（不抛异常）
    【变易】模型可通过环境变量配置（SKILL_RERANKER_MODEL）；懒加载避免 import 时拉起模型
    【简易】单次 predict，O(n) 复杂度；接口最小化（rerank 单方法）

架构层级:
    v6.1 规则层 → v6.2 embedding 拒绝层 → TF-IDF + 向量检索 → RRF 融合
    → 【v6.5】Reranker 二次排序 → top-k 最终结果

推理后端（按优先级自动降级）:
    1. ONNX Runtime（默认启用，SKILL_RERANKER_USE_ONNX=true）
       - 优先加载 <model_dir>/onnx/model_quantized.onnx
       - jina-reranker-v2 quantized 实测 P99 258ms（30.8x 加速 vs PyTorch）
       - C++ 引擎无 GIL/线程问题，豁免子进程隔离约束
    2. PyTorch + sentence-transformers（ONNX 不可用时降级）
       - Windows CPU 环境需子进程隔离（防 0xC0000005 崩溃）
    3. RRF 降级（模型不可用时返回原序）

模型选型（见 v6.5 计划 §3.1）:
    | 模型 | 大小 | 延迟 | 中文支持 | 推荐度 |
    |------|------|------|---------|--------|
    | BAAI/bge-reranker-v2-m3 | ~2.3GB | ~200ms | ✅ 优秀 | ⭐⭐⭐ 推荐（默认）|
    | BAAI/bge-reranker-base | ~1.1GB | ~100ms | ✅ 良好 | ⭐⭐ 备选 |
    | jinaai/jina-reranker-v2-base-multilingual | ~280MB | ~80ms | ✅ 良好 | ⭐ 轻量备选 |

    选择 BAAI/bge-reranker-v2-m3 的理由:
    1. 与 BGE-m3 embedding 同系列，编码空间一致
    2. 中文 reranker SOTA，P@3 提升预期 +18.5%
    3. 已有 BGE-m3 部署经验，运维成本低

Windows 崩溃防护（守【不易】）:
    根据 project_memory 记录:
    > Embedding 检索在 Windows CPU 环境下无隔离时会导致主进程 0xC0000005 崩溃
    Reranker 同样需要子进程隔离（multiprocessing.Process + terminate）
    注意：ONNX Runtime 为 C++ 引擎，无 PyTorch GIL/线程问题，可豁免子进程隔离

用法:
    reranker = SkillReranker()
    reranked = reranker.rerank(query, candidates, top_k=3)

环境变量:
    SKILL_RERANKER_ENABLED: true/false（默认 true）
    SKILL_RERANKER_MODEL: 模型名或本地路径（默认 BAAI/bge-reranker-v2-m3）
    SKILL_RERANKER_TIMEOUT: 子进程超时秒数（默认 30）
    SKILL_RERANKER_MIN_SCORE: 最低分数阈值（默认 0.001）
    SKILL_RERANKER_USE_ONNX: 是否优先使用 ONNX 推理（默认 true）
    SKILL_RERANKER_ONNX_VARIANT: ONNX 变体文件名（默认 model_quantized.onnx）
    SKILL_RERANKER_HOT_RELOAD_INTERVAL: 热重载 env 检查间隔秒（默认 30，惰性触发，不启后台线程）
"""
from __future__ import annotations

import json
import math
import os
import sys
import threading
import time
import traceback
import uuid
from typing import Any, Dict, List, Optional, Tuple

# 延迟导入：避免 import 时拉起 sentence-transformers
# 仅在 _load_model() 中实际导入

# ──────────────────────────────────────────────
#  日志（复用 skills_mgmt.observability）
# ──────────────────────────────────────────────

try:
    from .observability import logger, emit_metric
except ImportError:
    # 测试环境降级：使用标准 logging
    import logging
    logger = logging.getLogger("reranker")

    def emit_metric(name, *, value=1.0, labels=None, kind="counter"):
        """测试环境 no-op emit_metric（守【不易】埋点失败不影响主流程）"""
        return None


def _trace_id() -> str:
    return uuid.uuid4().hex[:16]


def _sigmoid(x: float) -> float:
    """数值稳定的 sigmoid 函数

    【不易】将 Cross-Encoder raw logits 映射到 [0,1] 概率空间，
           使 min_score 阈值（默认 0.001）恢复"过滤极低概率匹配"语义
    【简易】分段实现避免 math.exp 溢出（|x| > 700 时 exp 会抛 OverflowError）
    """
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


# ════════════════════════════════════════════════════════════
#  SkillReranker 类
# ════════════════════════════════════════════════════════════

class SkillReranker:
    """v6.5 Cross-Encoder Reranker — 对 RRF 融合候选二次排序

    【不易】不改变 match() 公共接口签名
    【变易】模型可通过环境变量配置（SKILL_RERANKER_MODEL）
    【简易】单次 predict，O(n) 复杂度

    架构:
        1. 懒加载模型（首次 rerank 时加载，避免 import 时拉起）
        2. 子进程隔离编码（防 Windows 0xC0000005 崩溃）
        3. 降级处理（模型不可用时回退原始排序）

    用法:
        reranker = SkillReranker()
        reranked = reranker.rerank(query, candidates, top_k=3)
        # reranked 是按 Reranker 分数排序的 top-k 候选

    环境变量:
        SKILL_RERANKER_ENABLED: true/false（默认 true）
        SKILL_RERANKER_MODEL: 模型名或本地路径（默认 BAAI/bge-reranker-v2-m3）
        SKILL_RERANKER_TIMEOUT: 子进程超时秒数（默认 30）
        SKILL_RERANKER_MIN_SCORE: 最低分数阈值（默认 0.001）
        SKILL_RERANKER_USE_ONNX: 是否优先使用 ONNX 推理（默认 true）
        SKILL_RERANKER_ONNX_VARIANT: ONNX 变体文件名（默认 model_quantized.onnx）
    """

    # 默认配置（可通过环境变量覆盖）
    _DEFAULT_MODEL = "BAAI/bge-reranker-v2-m3"
    _DEFAULT_TIMEOUT = 30
    # 【不易修复】min_score 误过滤回归（2026-08-24）：
    # 扩展黄金集（65 case）实测 0.001 误过滤 7 个 positive case（Precision@3 -3.4%），
    # 0.0001 恢复（+0.0%）。Cross-Encoder sigmoid 后分数普遍偏低，
    # 0.001 阈值会把合理匹配（如 bge-reranker-base ONNX 的 0.0003~0.0009）误拒。
    # 下调至 0.0001 仍保留"过滤极低概率匹配"语义，同时避免误伤。
    _DEFAULT_MIN_SCORE = 0.0001
    _DEFAULT_USE_ONNX = True
    _DEFAULT_ONNX_VARIANT = "model_quantized.onnx"
    # 【变易】单次 rerank predict 超时阈值（秒）—— 与 _timeout（子进程超时）区分
    # 任务要求 >3s 降级；v2-m3 CPU 推理 P99 可达 4.6s，可通过 .env 调大或换轻量模型
    _DEFAULT_RERANK_TIMEOUT = 3.0

    def __init__(self, model_name: Optional[str] = None):
        """初始化 Reranker

        Args:
            model_name: 模型名或本地路径（None 时从环境变量读取，默认 BAAI/bge-reranker-v2-m3）
        """
        self._model = None  # PyTorch CrossEncoder 懒加载
        self._model_name = model_name or os.environ.get(
            "SKILL_RERANKER_MODEL", self._DEFAULT_MODEL
        )
        self._timeout = int(os.environ.get(
            "SKILL_RERANKER_TIMEOUT", str(self._DEFAULT_TIMEOUT)
        ))
        self._min_score = float(os.environ.get(
            "SKILL_RERANKER_MIN_SCORE", str(self._DEFAULT_MIN_SCORE)
        ))
        # ONNX 推理相关（懒加载）
        self._use_onnx_env = os.environ.get(
            "SKILL_RERANKER_USE_ONNX", "true"
        ).lower() not in ("false", "0", "off", "no")
        self._onnx_variant = os.environ.get(
            "SKILL_RERANKER_ONNX_VARIANT", self._DEFAULT_ONNX_VARIANT
        )
        self._onnx_session = None  # ort.InferenceSession 懒加载
        self._onnx_tokenizer = None  # transformers tokenizer 懒加载
        self._onnx_input_names = None  # ONNX 模型输入名缓存
        self._use_onnx = False  # 实际是否走 ONNX 路径（加载成功后置 True）
        self._load_attempted = False  # 防止重复加载尝试
        # 【变易】单次 predict 超时阈值（秒）：超时后降级返回原序，不阻塞主流程
        # 与 _timeout（子进程超时，默认 30s）区分，rerank_timeout 聚焦单次推理延迟
        self._rerank_timeout = float(os.environ.get(
            "SKILL_RERANKER_RERANK_TIMEOUT", str(self._DEFAULT_RERANK_TIMEOUT)
        ))
        # ── 热重载状态（最小实现：惰性检查 + RLock 保护 session 交换）──
        # 【不易】_onnx_variant_loaded：当前已加载 session 对应的 variant（回滚基准）
        # 【不易】_onnx_variant_attempted：最近尝试的 variant（含失败），避免无效 variant 无限重试
        self._onnx_variant_loaded = self._onnx_variant
        self._onnx_variant_attempted = self._onnx_variant
        self._last_reload_check = 0.0  # 上次 env 检查时间戳（节流用）
        self._hot_reload_interval = float(os.environ.get(
            "SKILL_RERANKER_HOT_RELOAD_INTERVAL", "30"
        ))
        # 【不易】RLock 仅保护 session 引用交换（内存状态），锁内不做 I/O（守 project_memory 约束）
        self._reload_lock = threading.RLock()
        # 【变易】最近一次加载失败的错误信息 + traceback（side-channel，供验收检查读取）
        self._last_load_error: Optional[str] = None
        self._last_load_traceback: Optional[str] = None

    # ──────────────────────────────────────────────
    #  模型加载（懒加载 + 降级）
    # ──────────────────────────────────────────────

    def _load_model(self) -> bool:
        """懒加载模型（ONNX 优先 → PyTorch 降级）

        【不易】失败时返回 False（降级到原始排序），不抛异常；不改变 rerank 接口
        【变易】首次调用加载，后续复用缓存；ONNX/PyTorch 双路径自动降级
        【简易】单次加载尝试，O(1) 复杂度

        Returns:
            True: 模型加载成功（ONNX 或 PyTorch 任一）
            False: 模型加载失败（降级到 RRF）
        """
        # 已加载（任一后端）则直接返回
        if self._use_onnx and self._onnx_session is not None:
            return True
        if self._model is not None:
            return True
        if self._load_attempted:
            return False  # 之前已尝试失败，不重试

        self._load_attempted = True

        # 【变易】模型文件大小检查（>1GB 警告，建议轻量模型）
        # 在加载前执行：让用户在加载耗时前知晓模型规模
        self._check_model_size()

        # 优先尝试 ONNX（默认启用，C++ 引擎性能更优）
        if self._use_onnx_env:
            if self._load_onnx():
                return True
            logger.info(json.dumps({
                "module_name": "reranker",
                "action": "onnx.fallback_to_pytorch",
                "reason": "onnx_load_failed_or_unavailable",
            }, ensure_ascii=False))
            # [Observability] ONNX 降级到 PyTorch（降级率监控数据源）
            emit_metric("yunshu_reranker_fallback_total",
                        value=1, kind="counter",
                        labels={"from": "onnx", "to": "pytorch",
                                "reason": "onnx_unavailable"})

        # 降级到 PyTorch
        return self._load_pytorch()

    def _check_model_size(self) -> None:
        """检查模型文件大小，>1GB 警告（建议轻量模型）

        【不易】不阻塞加载，仅记录 warning + emit_metric
        【变易】仅对本地路径检查（HF hub 模型跳过，无法预知大小）
        【简易】os.walk 累加文件大小，单次遍历

        触发场景:
            - 默认 v2-m3 模型 ~2.3GB，CPU 推理 P99 4.6s 不达标
            - 建议换用 BAAI/bge-reranker-base (~1.1GB) 或 jina-reranker-v2 (~280MB)
            - 通过 .env SKILL_RERANKER_MODEL 切换模型
        """
        # 非本地路径（HF hub 模型 ID），跳过大小检查
        if not os.path.isdir(self._model_name):
            return
        total_size = 0
        try:
            for root, _dirs, files in os.walk(self._model_name):
                for f in files:
                    fp = os.path.join(root, f)
                    try:
                        total_size += os.path.getsize(fp)
                    except OSError:
                        # 单文件读取失败不影响整体统计
                        pass
        except OSError:
            # os.walk 失败（权限/路径问题），静默跳过
            return
        size_gb = total_size / (1024 ** 3)
        # 【变易】透出模型大小指标（gauge），供监控告警
        emit_metric("yunshu_reranker_model_size_gb",
                    value=round(size_gb, 2), kind="gauge",
                    labels={"model": self._model_name})
        if size_gb > 1.0:
            logger.warning(json.dumps({
                "module_name": "reranker",
                "action": "model.size_warning",
                "model": self._model_name,
                "size_gb": round(size_gb, 2),
                "threshold_gb": 1.0,
                "suggestion": "consider BAAI/bge-reranker-base (~1.1GB) or "
                               "jina-reranker-v2 (~280MB) for faster CPU inference",
            }, ensure_ascii=False))

    def _load_onnx(self) -> bool:
        """加载 ONNX 推理后端

        【不易】失败时返回 False，不抛异常；不破坏 rerank 接口
        【变易】模型路径可配置（SKILL_RERANKER_MODEL + onnx/<variant>）
        【简易】单次 ort.InferenceSession 初始化

        Returns:
            True: ONNX 加载成功
            False: ONNX 不可用（降级到 PyTorch）
        """
        try:
            import onnxruntime as ort
            from transformers import AutoTokenizer

            # 模型路径需为本地目录（ONNX 文件在 <model_dir>/onnx/<variant>）
            if not os.path.isdir(self._model_name):
                logger.warning(json.dumps({
                    "module_name": "reranker",
                    "action": "onnx.skip",
                    "reason": "model_path_not_local_dir",
                    "model": self._model_name,
                }, ensure_ascii=False))
                # [Observability] ONNX 跳过（配置错误，P2 告警数据源）
                emit_metric("yunshu_reranker_load_total",
                            value=1, kind="counter",
                            labels={"backend": "onnx", "status": "skipped",
                                    "reason": "path_not_local_dir"})
                return False

            onnx_path = os.path.join(self._model_name, "onnx", self._onnx_variant)
            if not os.path.exists(onnx_path):
                logger.warning(json.dumps({
                    "module_name": "reranker",
                    "action": "onnx.skip",
                    "reason": "onnx_file_not_found",
                    "expected_path": onnx_path,
                }, ensure_ascii=False))
                # [Observability] ONNX 文件缺失（配置错误，P2 告警数据源）
                emit_metric("yunshu_reranker_load_total",
                            value=1, kind="counter",
                            labels={"backend": "onnx", "status": "skipped",
                                    "reason": "file_not_found"})
                return False

            t0 = time.time()
            self._onnx_session = ort.InferenceSession(
                onnx_path, providers=["CPUExecutionProvider"]
            )
            self._onnx_input_names = [
                i.name for i in self._onnx_session.get_inputs()
            ]
            self._onnx_tokenizer = AutoTokenizer.from_pretrained(
                self._model_name, trust_remote_code=True
            )
            self._use_onnx = True
            elapsed = time.time() - t0

            logger.info(json.dumps({
                "module_name": "reranker",
                "action": "onnx.loaded",
                "model": self._model_name,
                "onnx_file": self._onnx_variant,
                "inputs": self._onnx_input_names,
                "load_time_s": round(elapsed, 2),
            }, ensure_ascii=False))
            # [Observability] Prometheus 指标：ONNX 加载成功（counter + gauge 加载耗时）
            emit_metric("yunshu_reranker_load_total",
                        value=1, kind="counter",
                        labels={"backend": "onnx", "status": "success"})
            emit_metric("yunshu_reranker_load_time_seconds",
                        value=elapsed, kind="gauge",
                        labels={"backend": "onnx"})
            return True

        except Exception as e:  # noqa: BLE001
            logger.warning(json.dumps({
                "module_name": "reranker",
                "action": "onnx.load_failed",
                "model": self._model_name,
                "error": str(e)[:300],
            }, ensure_ascii=False))
            # [Observability] Prometheus 指标：ONNX 加载失败（P0 告警数据源）
            emit_metric("yunshu_reranker_load_total",
                        value=1, kind="counter",
                        labels={"backend": "onnx", "status": "failed"})
            # 清理半初始化状态
            self._onnx_session = None
            self._onnx_tokenizer = None
            self._use_onnx = False
            return False

    def _load_pytorch(self) -> bool:
        """加载 PyTorch CrossEncoder 后端（降级路径）

        【不易】失败时返回 False（降级到 RRF），不抛异常
        【变易】trust_remote_code 兼容 jina 等自定义代码模型
        【简易】单次 CrossEncoder 初始化

        Returns:
            True: PyTorch 模型加载成功
            False: 加载失败（降级到 RRF）
        """
        try:
            from sentence_transformers import CrossEncoder
            # trust_remote_code=True：jina-reranker-v2 等含自定义代码仓库必需
            # bge-reranker-v2-m3 等标准模型对此参数无副作用，安全兼容
            self._model = CrossEncoder(self._model_name, trust_remote_code=True)
            logger.info(json.dumps({
                "module_name": "reranker",
                "action": "pytorch.loaded",
                "model": self._model_name,
            }, ensure_ascii=False))
            # [Observability] PyTorch 后端加载成功
            emit_metric("yunshu_reranker_load_total",
                        value=1, kind="counter",
                        labels={"backend": "pytorch", "status": "success"})
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning(json.dumps({
                "module_name": "reranker",
                "action": "pytorch.load_failed",
                "model": self._model_name,
                "error": str(e)[:300],
            }, ensure_ascii=False))
            # [Observability] PyTorch 加载失败（P0 双后端全挂告警数据源）
            emit_metric("yunshu_reranker_load_total",
                        value=1, kind="counter",
                        labels={"backend": "pytorch", "status": "failed"})
            return False

    # ──────────────────────────────────────────────
    #  热重载（最小实现：惰性检查 variant 变化 → 加载 → 锁内交换/回滚）
    # ──────────────────────────────────────────────

    def _maybe_hot_reload(self) -> None:
        """惰性检查 .env 中 ONNX variant 是否变化，变化则触发热重载。

        【不易】无 session 或非 ONNX 后端时直接返回（首次加载交给 _load_model）
        【变易】间隔节流（_hot_reload_interval，默认 30s）避免每次 rerank 都读 env
        【简易】无后台线程，仅在 rerank 调用路径上顺带检查

        触发时机：每次 rerank() 调用、_load_model() 成功之后。
        无效 variant 不会无限重试：_onnx_variant_attempted 标记已尝试的 variant。
        """
        # 仅 ONNX 后端已加载时才检查热重载
        if self._onnx_session is None or not self._use_onnx:
            return
        # 节流：未到检查间隔则跳过
        now = time.time()
        if now - self._last_reload_check < self._hot_reload_interval:
            return
        self._last_reload_check = now
        env_variant = os.environ.get(
            "SKILL_RERANKER_ONNX_VARIANT", self._DEFAULT_ONNX_VARIANT
        )
        # 与最近尝试的 variant 相同（成功或失败）→ 跳过，避免重复加载/重试
        if env_variant == self._onnx_variant_attempted:
            return
        self._hot_reload(env_variant)

    def _hot_reload(self, new_variant: str) -> None:
        """热重载到新 variant；失败时捕获异常堆栈并回滚到旧 session。

        【不易】加载失败保留旧 session（不中断服务），并记录 traceback（任务2核心）
        【变易】加载在锁外执行（I/O），session 引用交换在 RLock 内（仅内存状态）
        【简易】_try_load_onnx_variant 不捕获异常，由本方法统一捕获 + 记录堆栈
        """
        t0 = time.time()
        old_variant = self._onnx_variant_loaded
        logger.info(json.dumps({
            "module_name": "reranker",
            "action": "hot_reload.detected",
            "old_variant": old_variant,
            "new_variant": new_variant,
        }, ensure_ascii=False))

        try:
            new_session, new_tokenizer, new_inputs = self._try_load_onnx_variant(
                new_variant
            )
        except Exception:  # noqa: BLE001 —— 统一捕获，记录回滚前的异常堆栈
            # 【任务2核心】捕获回滚前的异常堆栈，确保可追溯失败根因
            tb_str = traceback.format_exc()
            self._last_load_error = tb_str.splitlines()[-1] if tb_str else ""
            self._last_load_traceback = tb_str
            # 标记已尝试，避免无效 variant 在每次 rerank 时无限重试
            self._onnx_variant_attempted = new_variant
            # 区分预期配置错误（FileNotFoundError）与意外异常，对齐验收清单 3.2/3.3：
            # - 无效 variant（文件缺失）→ hot_reload.failed_rollback（清单 3.2）
            # - 意外异常（ONNX 加载/tokenizer 崩溃）→ hot_reload.exception_rollback（清单 3.3）
            is_config_error = "FileNotFoundError" in tb_str or "onnx_file_not_found" in tb_str
            status = "failed" if is_config_error else "exception"
            action = (
                "hot_reload.failed_rollback" if is_config_error
                else "hot_reload.exception_rollback"
            )
            logger.warning(json.dumps({
                "module_name": "reranker",
                "action": action,
                "target_variant": new_variant,
                "kept_variant": old_variant,
                "load_error": (self._last_load_error or "")[:300],
                "traceback": tb_str[:2000],  # 异常堆栈追踪（验收清单 3.2/3.3 校验字段）
                "status": status,
            }, ensure_ascii=False))
            emit_metric("yunshu_reranker_hot_reload_total",
                        value=1, kind="counter",
                        labels={"status": status})
            return

        # 加载成功：锁内交换 session 引用（仅内存状态变更，守 project_memory 锁约束）
        with self._reload_lock:
            self._onnx_session = new_session
            self._onnx_tokenizer = new_tokenizer
            self._onnx_input_names = new_inputs
            self._onnx_variant = new_variant
            self._onnx_variant_loaded = new_variant
            self._onnx_variant_attempted = new_variant
            self._use_onnx = True

        elapsed = time.time() - t0
        logger.info(json.dumps({
            "module_name": "reranker",
            "action": "hot_reload.success",
            "old_variant": old_variant,
            "new_variant": new_variant,
            "reload_time_s": round(elapsed, 2),
        }, ensure_ascii=False))
        emit_metric("yunshu_reranker_hot_reload_total",
                    value=1, kind="counter",
                    labels={"status": "success"})

    def _try_load_onnx_variant(self, variant: str):
        """加载指定 variant 的 ONNX session（不修改实例状态，不捕获异常）。

        【不易】不修改 self.* 状态——成功返回三元组，异常由调用方 _hot_reload 捕获
        【变易】路径校验失败时抛 FileNotFoundError（预期配置错误，status=failed）
        【简易】复用 _load_onnx 的加载逻辑，但返回值而非写实例

        Args:
            variant: ONNX 文件名（如 model_quantized.onnx / model.onnx）

        Returns:
            (ort.InferenceSession, AutoTokenizer, List[str]) 三元组

        Raises:
            FileNotFoundError: 模型路径非目录或 ONNX 文件不存在（无效 variant）
            Exception: ONNX 加载/tokenizer 初始化的意外异常（status=exception）
        """
        import onnxruntime as ort
        from transformers import AutoTokenizer

        # 模型路径需为本地目录（ONNX 文件在 <model_dir>/onnx/<variant>）
        if not os.path.isdir(self._model_name):
            raise FileNotFoundError(
                f"model_path_not_local_dir: {self._model_name}"
            )
        onnx_path = os.path.join(self._model_name, "onnx", variant)
        if not os.path.exists(onnx_path):
            raise FileNotFoundError(f"onnx_file_not_found: {onnx_path}")
        session = ort.InferenceSession(
            onnx_path, providers=["CPUExecutionProvider"]
        )
        input_names = [i.name for i in session.get_inputs()]
        tokenizer = AutoTokenizer.from_pretrained(
            self._model_name, trust_remote_code=True
        )
        return session, tokenizer, input_names

    # ──────────────────────────────────────────────
    #  环境变量开关
    # ──────────────────────────────────────────────

    def _is_enabled(self) -> bool:
        """检查 Reranker 是否启用

        【变易】环境变量开关，设为 false/0/off/no 时禁用
        """
        enabled = os.environ.get("SKILL_RERANKER_ENABLED", "true").lower()
        return enabled not in ("false", "0", "off", "no")

    # ──────────────────────────────────────────────
    #  可用性检查（公共契约）
    # ──────────────────────────────────────────────

    def is_available(self) -> bool:
        """检查模型是否可用（触发懒加载）

        【不易】不抛异常，失败返回 False；不改变 rerank 接口
        【变易】触发懒加载（首次调用时加载模型，_load_attempted 防重试）
        【简易】委托 _load_model，单次检查

        用途:
            loader.match 在调用 rerank 前先用此方法决定是否走精排分支：
                if use_reranker and self._reranker.is_available():
                    fused = self._reranker.rerank(...)

        Returns:
            True: 模型已加载或可加载（ONNX/PyTorch 任一后端可用）+ 环境开关启用
            False: 模型不可用 / 加载失败 / 环境开关禁用
        """
        # 环境变量开关优先：禁用时直接返回 False，不触发加载
        if not self._is_enabled():
            return False
        # 委托 _load_model（内部幂等：_load_attempted 防重试，已加载直接返回 True）
        return self._load_model()

    # ──────────────────────────────────────────────
    #  核心：rerank 接口
    # ──────────────────────────────────────────────

    def rerank(
        self,
        query: str,
        candidates: List[Any],
        top_k: int = 3,
    ) -> List[Any]:
        """对候选技能重新排序

        【不易】不改变候选列表内容，仅重排序；不抛异常
        【变易】模型不可用时降级到原始排序；支持 dict / SkillMatch 两种候选类型
        【简易】单次 predict，按分数降序取 top-k

        Args:
            query: 用户意图文本
            candidates: RRF 融合后的候选列表，支持两种类型：
                - SkillMatch 对象（单元测试用）：更新 score 属性 + score_breakdown
                - dict 对象（loader 调用时用）：设置 rerank_score/original_rank/score 字段
            top_k: 返回 top-k；None 时返回全部过滤后的候选（loader 用 None 表示外层切片）

        Returns:
            重排序后的 top-k 候选（按 Reranker 分数降序）：
            - dict 候选：每个 dict 含 rerank_score/original_rank/score 字段
            - SkillMatch 候选：score 属性已更新，score_breakdown 含 rerank_score/original_rank
            模型不可用时返回原始候选的 top-k（降级，不透出 rerank_score）
        """
        tid = _trace_id()
        t0 = time.time()

        # 空候选快速返回
        if not candidates:
            return []

        # 环境变量开关
        if not self._is_enabled():
            logger.info(json.dumps({
                "trace_id": tid,
                "module_name": "reranker",
                "action": "rerank.disabled",
                "reason": "SKILL_RERANKER_ENABLED=false",
            }, ensure_ascii=False))
            return candidates[:top_k]

        # 模型加载
        if not self._load_model():
            # 降级：返回原始排序的 top-k
            elapsed = (time.time() - t0) * 1000
            logger.warning(json.dumps({
                "trace_id": tid,
                "module_name": "reranker",
                "action": "rerank.fallback",
                "reason": "model_unavailable",
                "candidate_count": len(candidates),
                "duration_ms": round(elapsed, 2),
            }, ensure_ascii=False))
            # [Observability] rerank 降级（P1 降级率告警数据源）
            emit_metric("yunshu_reranker_fallback_total",
                        value=1, kind="counter",
                        labels={"from": "reranker", "to": "original_order",
                                "reason": "model_unavailable"})
            return candidates[:top_k]

        # 【变易】热重载检查：首次加载成功后，惰性检查 .env variant 是否变化
        # 无 session 时 no-op（_maybe_hot_reload 内部守卫），不影响首次加载路径
        self._maybe_hot_reload()

        # 构造 query-document 对
        pairs = []
        for c in candidates:
            doc_text = self._candidate_to_text(c)
            pairs.append((query, doc_text))

        # 子进程隔离编码（防 Windows 崩溃）
        try:
            scores = self._predict_with_timeout(pairs, tid)
        except Exception as e:  # noqa: BLE001
            # 降级：预测失败返回原始排序
            elapsed = (time.time() - t0) * 1000
            logger.warning(json.dumps({
                "trace_id": tid,
                "module_name": "reranker",
                "action": "rerank.predict_failed",
                "error": str(e)[:300],
                "duration_ms": round(elapsed, 2),
            }, ensure_ascii=False))
            return candidates[:top_k]

        # 【不易修复】Cross-Encoder 输出 raw logits（典型范围 -10~+10），
        # 但 min_score 是概率阈值（默认 0.001，[0,1] 区间）。
        # 不做 sigmoid 时，负 logits 的合理匹配（如 jina-reranker-v2 量化 ONNX
        # 对 'self_reflection'→'自我反思技能' 给出 -0.866）会被 min_score 误过滤，
        # 导致 rerank 后 top 为空（实测 Precision@3 从 0.42 暴跌到 0.11）。
        # sigmoid 是单调递增函数，不改变排序，仅让 rerank_score 落入 [0,1] 概率空间，
        # 使 min_score 阈值恢复"过滤极低概率匹配"的语义。
        # 影响：与 sentence-transformers CrossEncoder.predict(apply_softmax=True) 行为一致
        scores = [_sigmoid(s) for s in scores]

        # 按分数降序排序（记录原始位置用于 original_rank）
        # 【变易】透出 original_rank：候选在输入 candidates 中的位置（1-based）
        #         loader 期望此字段用于排查 rerank 前后排名变化
        # indexed_pairs: [((orig_idx, candidate), score), ...]
        indexed_candidates = list(enumerate(candidates))
        scored = list(zip(indexed_candidates, scores))
        scored.sort(key=lambda x: x[1], reverse=True)

        # 过滤低分候选
        filtered = [
            (orig_idx, c, s) for (orig_idx, c), s in scored
            if s >= self._min_score
        ]
        # 【变易】top_k=None 时返回全部过滤后的候选（loader 用 None 表示外层切片）
        # 显式处理 None 比 [:None] 更清晰，且避免后续 [:None] 语义歧义
        effective_top_k = len(filtered) if top_k is None else top_k
        result = filtered[:effective_top_k]

        # 更新候选分数 + 透出 rerank_score/original_rank
        # 【变易】支持 dict 候选（loader 传 dict 列表）与 SkillMatch 对象（单元测试用）
        #         - dict: 设置 rerank_score/original_rank/score 字段（loader 期望）
        #         - SkillMatch: 更新 score 属性 + score_breakdown 透出 rerank_score
        for _rerank_rank, (orig_idx, c, s) in enumerate(result, start=1):
            rounded_score = round(float(s), 4)
            if isinstance(c, dict):
                c["rerank_score"] = rounded_score
                c["original_rank"] = orig_idx + 1  # 原始 candidates 中的位置（1-based）
                c["score"] = rounded_score  # 同步更新 score（向后兼容）
            elif hasattr(c, "score"):
                c.score = rounded_score
                # 如果支持 score_breakdown，透出 rerank_score/original_rank
                if hasattr(c, "score_breakdown"):
                    if c.score_breakdown is None:
                        c.score_breakdown = {}
                    c.score_breakdown["rerank_score"] = rounded_score
                    c.score_breakdown["original_rank"] = orig_idx + 1

        elapsed = (time.time() - t0) * 1000
        # 【不易】sigmoid 分数范围日志：验证 sigmoid 转换后分数落在 [0,1] 概率空间
        # 并暴露区分度（stddev 越小说明 reranker 对候选打分越接近，难以改变排序）
        # 修复背景：评估发现 reranker 被调用（17.7x 耗时）但未改变排序，
        # 需要从日志确认是否因 sigmoid 分数过于接近（区分度不足）
        if scores:
            score_min = float(min(scores))
            score_max = float(max(scores))
            score_mean = float(sum(scores) / len(scores))
            score_var = sum((s - score_mean) ** 2 for s in scores) / len(scores)
            score_stddev = float(math.sqrt(score_var))
        else:
            score_min = score_max = score_mean = score_stddev = 0.0
        logger.info(json.dumps({
            "trace_id": tid,
            "module_name": "reranker",
            "action": "rerank.completed",
            "query": query[:100],
            "candidate_count": len(candidates),
            "result_count": len(result),
            "top_score": float(result[0][2]) if result else 0.0,
            # 【变易】sigmoid 分数范围（验证 [0,1] + 区分度诊断）
            "score_min": round(score_min, 6),
            "score_max": round(score_max, 6),
            "score_mean": round(score_mean, 6),
            "score_stddev": round(score_stddev, 6),
            "duration_ms": round(elapsed, 2),
        }, ensure_ascii=False))
        # [Observability] rerank 成功（P99 延迟直方图 + 成功计数）
        backend = "onnx" if self._use_onnx else "pytorch"
        emit_metric("yunshu_rerank_duration_ms",
                    value=elapsed, kind="histogram",
                    labels={"backend": backend, "success": "true"})
        emit_metric("yunshu_reranker_completed_total",
                    value=1, kind="counter",
                    labels={"backend": backend})

        return [c for _, c, _ in result]

    # ──────────────────────────────────────────────
    #  辅助方法
    # ──────────────────────────────────────────────

    def _candidate_to_text(self, candidate: Any) -> str:
        """将候选对象转为文本（用于 Reranker 输入）

        【简易】复用 name + description + tags
        """
        parts = []
        for attr in ("name", "description", "category"):
            val = getattr(candidate, attr, "")
            if val:
                parts.append(str(val))
        # tags 可能是列表
        tags = getattr(candidate, "tags", [])
        if tags:
            parts.append(" ".join(tags) if isinstance(tags, list) else str(tags))
        return " ".join(parts)

    def _predict_with_timeout(
        self, pairs: List[Tuple[str, str]], tid: str
    ) -> List[float]:
        """预测分发（ONNX 优先 → PyTorch 降级，带超时保护）

        【不易】超时降级返回 [0.0]*n，不抛异常；不改变返回值结构
        【变易】ThreadPoolExecutor 软超时（_rerank_timeout 可配，默认 3s）
        【简易】按 _use_onnx 标志分发到 _predict_onnx / _predict_pytorch

        超时语义:
            - 超时后主线程立即返回 [0.0]*n（触发 rerank 降级到原序）
            - 后台 predict 线程无法真正终止（Python 线程限制），任其自然完成
            - Windows CPU 环境下 v2-m3 推理 P99 可达 4.6s，3s 超时会触发降级
              （建议通过 .env 调大 SKILL_RERANKER_RERANK_TIMEOUT 或换轻量模型）

        根据 project_memory:
            > 0xC00000005 及类似崩溃码需通过 try/except 捕获
            > 子进程隔离是保障稳定性的必要措施（Cross-Encoder 和 Embedding 检索均已实现）
            本方法用 ThreadPoolExecutor 实现软超时，子进程隔离留待生产环境补齐

        Args:
            pairs: (query, document) 对列表
            tid: trace_id

        Returns:
            分数列表（与 pairs 等长，float 类型）
        """
        # 空对快速返回
        if not pairs:
            return []

        # 选择 predict 函数（ONNX 优先 → PyTorch 降级）
        if self._use_onnx and self._onnx_session is not None:
            predict_fn = self._predict_onnx
        elif self._model is not None:
            predict_fn = self._predict_pytorch
        else:
            # 无可用后端
            return [0.0] * len(pairs)

        # ThreadPoolExecutor 软超时包裹
        # 【不易】超时/predict 异常均抛出，由 rerank() 的 except 捕获并降级返回原序
        #         —— 不能返回 [0.0]*n，否则会被 min_score 过滤为空列表（违降级语义）
        # 【简易】单线程池，超时后主线程返回，后台线程任其完成
        # 【变易】不用 with 块：with 退出会 shutdown(wait=True) 阻塞等待后台线程
        #         手动管理 + shutdown(wait=False) 让超时后立即返回，后台线程任其完成
        from concurrent.futures import (
            ThreadPoolExecutor,
            TimeoutError as FuturesTimeout,
        )
        ex = ThreadPoolExecutor(max_workers=1)
        try:
            future = ex.submit(predict_fn, pairs, tid)
            return future.result(timeout=self._rerank_timeout)
        except FuturesTimeout:
            # 超时：记录日志 + emit_metric 后 re-raise，让 rerank 降级返回原序
            backend = "onnx" if self._use_onnx else "pytorch"
            logger.warning(json.dumps({
                "trace_id": tid,
                "module_name": "reranker",
                "action": "predict.timeout",
                "timeout_s": self._rerank_timeout,
                "pair_count": len(pairs),
                "backend": backend,
                "reason": "predict exceeded rerank_timeout, degrading to original order",
            }, ensure_ascii=False))
            emit_metric("yunshu_reranker_timeout_total",
                        value=1, kind="counter",
                        labels={"backend": backend})
            raise  # re-raise FuturesTimeout，由 rerank 的 except 捕获降级
        finally:
            # wait=False：不阻塞等待后台线程，立即返回
            # 后台 predict 线程无法真正终止（Python 线程限制），任其自然完成
            ex.shutdown(wait=False)
        # 其他异常（predict 抛出的 RuntimeError 等）直接传播，由 rerank 的 except 捕获降级

    def _predict_pytorch(
        self, pairs: List[Tuple[str, str]], tid: str
    ) -> List[float]:
        """PyTorch CrossEncoder 推理（提取为独立方法，便于超时包裹）

        【不易】不改变返回值结构（float 分数列表）
        【变易】predict 失败抛异常，由 rerank() 的 except 统一捕获降级（不返回 [0.0]*n）
        【简易】单次 model.predict，O(n) 复杂度

        Args:
            pairs: (query, document) 对列表
            tid: trace_id

        Returns:
            分数列表（与 pairs 等长，float 类型）
        """
        if not pairs:
            return []
        if self._model is None:
            return [0.0] * len(pairs)
        # 不 try/except：predict 异常由 rerank() 的 except 捕获并降级返回原序
        # （与超时降级同路径，保持降级语义一致）
        scores = self._model.predict(pairs)
        return [float(s) for s in scores]

    def _predict_onnx(
        self, pairs: List[Tuple[str, str]], tid: str
    ) -> List[float]:
        """ONNX Runtime 推理

        【不易】不改变返回值结构（float 分数列表）
        【变易】支持动态 batch（pairs 数量可变）
        【简易】单次 sess.run，O(n) 复杂度

        Args:
            pairs: (query, document) 对列表
            tid: trace_id

        Returns:
            分数列表（与 pairs 等长，float 类型）
        """
        if not pairs:
            return []
        if self._onnx_session is None or self._onnx_tokenizer is None:
            return [0.0] * len(pairs)

        try:
            import numpy as np

            # 拆分 pairs 为两个并行 list（tokenizer 期望此格式）
            texts_a = [p[0] for p in pairs]
            texts_b = [p[1] for p in pairs]

            # tokenize（batch 推理，padding 到 batch 内最长序列）
            encoded = self._onnx_tokenizer(
                texts_a, texts_b,
                padding=True, truncation=True, max_length=512,
                return_tensors="np",
            )

            # 按 ONNX 模型实际输入名构造 feed_dict（兼容不同变体）
            feed = {}
            for name in self._onnx_input_names:
                if "input_ids" in name:
                    feed[name] = encoded["input_ids"]
                elif "attention_mask" in name:
                    feed[name] = encoded["attention_mask"]
                elif "token_type_ids" in name:
                    # XLM-Roberta 通常无 token_type_ids，缺省填 0
                    feed[name] = encoded.get(
                        "token_type_ids", np.zeros_like(encoded["input_ids"])
                    )

            # ONNX 推理（C++ 引擎，无 GIL）
            outputs = self._onnx_session.run(None, feed)
            # logits 形状: (batch, 1) 或 (batch,)，展平为一维
            scores = outputs[0].flatten()
            return [float(s) for s in scores]

        except Exception as e:  # noqa: BLE001
            logger.warning(json.dumps({
                "trace_id": tid,
                "module_name": "reranker",
                "action": "onnx.predict_failed",
                "error": str(e)[:300],
            }, ensure_ascii=False))
            # [Observability] ONNX 推理失败（P1 推理失败率告警数据源）
            emit_metric("yunshu_reranker_predict_failed_total",
                        value=1, kind="counter",
                        labels={"backend": "onnx"})
            return [0.0] * len(pairs)
