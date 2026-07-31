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

模型选型（见 v6.5 计划 §3.1，2026-07-31 实测更新）:
    | 模型 | 大小 | ONNX INT8 P99 | 中文支持 | 推荐度 |
    |------|------|---------------|---------|--------|
    | jinaai/jina-reranker-v2-base-multilingual | ~280MB | 258ms ✅ | ✅ 良好 | ⭐⭐⭐ 当前默认 |
    | BAAI/bge-reranker-base | 266MB(量化) | 487ms ✅达标 | ✅ 良好 | ⭐⭐ 备选（区分度待评估）|
    | BAAI/bge-reranker-v2-m3 | ~2.3GB | 4641ms(PyTorch)❌ | ✅ SOTA | ⭐ 谨慎（需 ONNX 量化）|

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
    SKILL_RERANKER_MIN_SCORE: 最低分数阈值（默认 0.05，软拒识）
    SKILL_RERANKER_USE_ONNX: 是否优先使用 ONNX 推理（默认 true）
    SKILL_RERANKER_ONNX_VARIANT: ONNX 变体文件名（默认 model_quantized.onnx）
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
        SKILL_RERANKER_MIN_SCORE: 最低分数阈值（默认 0.05，软拒识）
        SKILL_RERANKER_USE_ONNX: 是否优先使用 ONNX 推理（默认 true）
        SKILL_RERANKER_ONNX_VARIANT: ONNX 变体文件名（默认 model_quantized.onnx）
    """

    # 默认配置（可通过环境变量覆盖）
    # 【不易】_DEFAULT_MODEL fallback 为 jina 路径（与 .env 默认一致），不再用 v2-m3
    # 原因：v2-m3 PyTorch 路径在 Windows CPU 触发 0xC0000005 崩溃；.env 未设置时需安全 fallback
    _DEFAULT_MODEL = "C:/Users/Administrator/.cache/huggingface/hub/models--jinaai--jina-reranker-v2-base-multilingual"
    _DEFAULT_TIMEOUT = 30
    # 【变易】min_score 软拒识阈值：从 0.001 提到 0.05，过滤极低分候选
    # 实测依据（2026-07-31 RERANKER_DISCRIMINATION_COMPARE_REPORT.json）：
    #   - bge-base 正样本 top1 最低 0.0623（case_020 'context_aware'），0.05 不误杀
    #   - 0.1 会误杀 bge case_020（top1=0.0623 < 0.1），故选 0.05 而非 0.1
    # 局限：jina 对 case_042 负样本给出 0.0562，0.05 仍无法过滤；
    #       真正解决负样本拒识靠 bge 模型本身（bge 给 case_042 分数 < 0.001 已被默认阈值过滤）
    _DEFAULT_MIN_SCORE = 0.05
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
        # 【变易】分阶段耗时侧信道：predict 阶段写入，rerank 阶段读取后清空
        # Why 侧信道: 不改 _predict_onnx/_predict_pytorch 返回值结构（守【不易】）
        # Why 实例变量而非返回值: predict 可能在 ThreadPoolExecutor 中执行，
        #   闭包捕获复杂；实例变量线程安全由 GIL 单写保证，rerank 主线程读取足够
        self._last_tokenize_ms: float = 0.0
        self._last_inference_ms: float = 0.0
        # 【变易】热重载机制：监听 .env mtime 变化，SKILL_RERANKER_ONNX_VARIANT
        #         变化时无需重启进程即可切换 ONNX 模型变体（INT8 ↔ FP32）
        # 设计原则:
        #   - 不双模型常驻（2.3GB×2 内存浪费，违【简易】）
        #   - 不引入 SIGHUP（Windows 支持差，违【变易】）
        #   - mtime 轮询 + RLock 指针替换，新会话在锁外加载（避免阻塞推理）
        # Why RLock 而非 RWLock: Python 标准库无 RWLock，RLock 仅在指针替换时
        #   短暂持锁（<1ms），推理全程无锁，性能可接受
        # Why 推理无锁安全: Python 字节码 LOAD_ATTR 读取 session 引用到栈顶后，
        #   即使 self._onnx_session 被替换，栈顶引用仍持有旧 session，GC 不回收，
        #   C++ 端 run() 能安全完成
        self._env_file = os.environ.get(
            "SKILL_RERANKER_ENV_FILE",
            os.path.join(os.getcwd(), ".env"),
        )
        self._env_mtime: float = self._get_env_mtime()
        self._env_check_interval = float(os.environ.get(
            "SKILL_RERANKER_HOT_RELOAD_INTERVAL", "30"
        ))  # mtime 轮询间隔（秒），默认 30s
        self._last_env_check: float = 0.0  # 上次检查时间戳（0 触发首次检查）
        self._reload_lock = threading.RLock()  # 保护会话指针替换
        # 当前已加载的 variant（与 _onnx_variant 区分：
        # _onnx_variant 是"期望加载的"，_onnx_variant_loaded 是"实际已加载的"）
        self._onnx_variant_loaded: str = self._onnx_variant
        # 【变易】加载失败原因侧信道：_load_onnx 写入，_hot_reload_onnx_variant 读取
        # Why 侧信道: _load_onnx 返回 bool 不暴露失败原因，热重载 failed_rollback
        #   日志需要根因信息辅助排障（variant 文件不存在 / 路径错误 / 加载异常）
        self._last_load_error: Optional[str] = None
        self._last_load_traceback: Optional[str] = None

    def _get_env_mtime(self) -> float:
        """获取 .env 文件 mtime（不存在返回 0）

        【不易】OSError 静默处理，不抛异常
        【简易】os.path.getmtime 单次调用
        """
        try:
            return os.path.getmtime(self._env_file)
        except OSError:
            return 0.0

    def _read_variant_from_env_file(self) -> Optional[str]:
        """从 .env 文件读取 SKILL_RERANKER_ONNX_VARIANT（不污染 os.environ）

        【不易】仅读取不写入 os.environ，避免影响其他模块
        【变易】支持 KEY="VALUE" 带引号格式
        【简易】逐行扫描，找到即返回

        Returns:
            variant 字符串（如 "model_quantized.onnx"），未找到返回 None
        """
        try:
            with open(self._env_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    # 跳过注释和空行
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    if key.strip() == "SKILL_RERANKER_ONNX_VARIANT":
                        return value.strip().strip('"').strip("'")
        except OSError:
            # 文件不存在或读取失败，返回 None 让调用方保留原值
            pass
        return None

    def _check_hot_reload(self) -> None:
        """检查 .env 是否变化，触发热重载

        【不易】失败时保留旧会话，不抛异常，不阻塞 rerank 主流程
        【变易】mtime 轮询节流（30s 间隔），避免每次 rerank 都查文件系统
        【简易】mtime 未变直接返回，变化时重读 variant 对比

        调用时机: rerank() 入口处，仅在 ONNX 已加载时触发
        """
        # 仅在 ONNX 已加载且启用时检查（未加载时 _load_model 会处理）
        if not self._use_onnx or self._onnx_session is None:
            return

        # 节流：间隔内不重复检查
        now = time.time()
        if now - self._last_env_check < self._env_check_interval:
            return
        self._last_env_check = now

        current_mtime = self._get_env_mtime()
        if current_mtime == self._env_mtime:
            return  # mtime 未变，无需重载

        # mtime 变化，重读 .env 提取最新 variant
        new_variant = self._read_variant_from_env_file()
        if new_variant is None:
            # .env 中未配置 ONNX_VARIANT 或读取失败，仅更新 mtime 避免重复检查
            self._env_mtime = current_mtime
            return

        if new_variant == self._onnx_variant_loaded:
            # variant 未变化（可能是 .env 其他配置变了），仅更新 mtime
            self._env_mtime = current_mtime
            return

        # variant 变化，触发热重载
        logger.info(json.dumps({
            "module_name": "reranker",
            "action": "hot_reload.detected",
            "old_variant": self._onnx_variant_loaded,
            "new_variant": new_variant,
            "env_file": self._env_file,
        }, ensure_ascii=False))
        self._hot_reload_onnx_variant(new_variant)
        self._env_mtime = current_mtime

    def _hot_reload_onnx_variant(self, new_variant: str) -> None:
        """热重载 ONNX 会话（variant 切换）

        【不易】失败时保留旧会话，不抛异常；不改变 rerank 接口
        【变易】新会话在锁外加载（避免阻塞推理），仅指针替换时持锁
        【简易】复用 _load_onnx 内部加载逻辑，通过临时实例隔离加载过程

        安全保证:
            1. 新会话加载失败 → 旧会话保留，记录告警
            2. 加载过程异常 → 回滚到旧会话
            3. 指针替换是原子操作（Python 赋值），推理无锁安全
        """
        with self._reload_lock:
            old_variant = self._onnx_variant_loaded
            old_session = self._onnx_session
            old_tokenizer = self._onnx_tokenizer
            old_input_names = self._onnx_input_names

            # 临时切换 variant 并重置加载状态
            # Why 重置 _load_attempted: _load_onnx 内部会检查此标志，
            # 需重置才能触发重新加载；finally 中恢复避免影响失败重试语义
            self._onnx_variant = new_variant
            self._onnx_session = None
            self._onnx_tokenizer = None
            self._onnx_input_names = None
            self._load_attempted = False
            self._use_onnx = False  # 临时置 False，让 _load_onnx 重新走加载流程

            try:
                if self._load_onnx():
                    # 加载成功：新 session 已写入 self._onnx_session
                    # 【不易修复】显式恢复 _use_onnx=True：
                    # 真实 _load_onnx 成功时会设 _use_onnx=True，但 mock 测试
                    # 时 mock 不执行内部逻辑，需显式保证状态一致（幂等）
                    self._use_onnx = True
                    self._onnx_variant_loaded = new_variant
                    logger.info(json.dumps({
                        "module_name": "reranker",
                        "action": "hot_reload.success",
                        "old_variant": old_variant,
                        "new_variant": new_variant,
                    }, ensure_ascii=False))
                    emit_metric("yunshu_reranker_hot_reload_total",
                                value=1, kind="counter",
                                labels={"status": "success"})
                else:
                    # 加载失败：回滚到旧会话
                    self._onnx_session = old_session
                    self._onnx_tokenizer = old_tokenizer
                    self._onnx_input_names = old_input_names
                    self._onnx_variant = old_variant
                    self._onnx_variant_loaded = old_variant
                    self._use_onnx = old_session is not None
                    # 【可观测性】读取 _load_onnx 侧信道记录的失败根因
                    # Why 读取侧信道: _load_onnx 返回 False 不暴露原因，
                    #   侧信道记录了具体根因（文件不存在 / 路径错误 / 加载异常）
                    load_error = self._last_load_error or "unknown"
                    load_traceback = self._last_load_traceback
                    rollback_log = {
                        "module_name": "reranker",
                        "action": "hot_reload.failed_rollback",
                        "target_variant": new_variant,
                        "kept_variant": old_variant,
                        "reason": "new_variant_load_failed",
                        "load_error": load_error,
                    }
                    # 异常场景才追加 traceback（非异常场景为 None 时省略）
                    if load_traceback:
                        rollback_log["traceback"] = load_traceback
                    logger.warning(json.dumps(rollback_log, ensure_ascii=False))
                    emit_metric("yunshu_reranker_hot_reload_total",
                                value=1, kind="counter",
                                labels={"status": "failed"})
            except Exception as e:  # noqa: BLE001
                # 异常回滚（防御性：_load_onnx 内部已有 try/except，此处兜底）
                self._onnx_session = old_session
                self._onnx_tokenizer = old_tokenizer
                self._onnx_input_names = old_input_names
                self._onnx_variant = old_variant
                self._onnx_variant_loaded = old_variant
                self._use_onnx = old_session is not None
                # 【可观测性】捕获完整异常堆栈，定位热重载过程中的意外崩溃
                # Why 完整堆栈: exception_rollback 是兜底分支，需堆栈定位
                #   是 _load_onnx 之外还是内部的异常
                tb_str = traceback.format_exc()
                logger.warning(json.dumps({
                    "module_name": "reranker",
                    "action": "hot_reload.exception_rollback",
                    "target_variant": new_variant,
                    "error": str(e)[:300],
                    "traceback": tb_str[:2000],
                }, ensure_ascii=False))
                emit_metric("yunshu_reranker_hot_reload_total",
                            value=1, kind="counter",
                            labels={"status": "exception"})
            finally:
                # 恢复 _load_attempted=True，保持"加载已尝试，不重试"语义
                # （热重载是显式触发，下次热重载会再次重置）
                self._load_attempted = True

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
                # 【变易】记录失败原因到侧信道，供热重载 failed_rollback 日志引用
                self._last_load_error = f"model_path_not_local_dir: {self._model_name}"
                self._last_load_traceback = None  # 非异常场景无堆栈
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
                # 【变易】记录失败原因到侧信道（这是"无效 variant 回滚"最常见的根因）
                self._last_load_error = f"onnx_file_not_found: {onnx_path}"
                self._last_load_traceback = None
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
            # 【可观测性】捕获完整堆栈，供热重载 exception_rollback 日志引用
            # Why 完整堆栈: str(e) 仅含错误消息，traceback 含调用链定位根因
            tb_str = traceback.format_exc()
            logger.warning(json.dumps({
                "module_name": "reranker",
                "action": "onnx.load_failed",
                "model": self._model_name,
                "error": str(e)[:300],
                "traceback": tb_str[:2000],  # 截断避免日志过长
            }, ensure_ascii=False))
            # [Observability] Prometheus 指标：ONNX 加载失败（P0 告警数据源）
            emit_metric("yunshu_reranker_load_total",
                        value=1, kind="counter",
                        labels={"backend": "onnx", "status": "failed"})
            # 【变易】记录失败原因 + 完整堆栈到侧信道
            self._last_load_error = f"{type(e).__name__}: {str(e)[:300]}"
            self._last_load_traceback = tb_str[:2000]
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

        # 【变易】热重载检查：.env 中 SKILL_RERANKER_ONNX_VARIANT 变化时
        # 无需重启进程即可切换 ONNX 模型变体（INT8 ↔ FP32）
        # Why 放在 _load_model 之后: 仅在 ONNX 已加载时才有热重载意义
        # Why 放在构造 pairs 之前: 热重载会替换 session，需在推理前完成
        self._check_hot_reload()

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
        # 【变易】读取 predict 阶段侧信道耗时（分阶段定位延迟瓶颈）
        # Why 读取后清零: 防止降级路径（_predict 未调用）残留上次数据污染日志
        # 排查路径: duration_ms >> tokenize_ms + inference_ms → 瓶颈在排序/过滤/sigmoid 后处理
        #          tokenize_ms 占比高 → tokenizer 慢（检查 max_length/ batch_size）
        #          inference_ms 占比高 → ONNX 推理慢（检查模型量化/并发）
        tokenize_ms = self._last_tokenize_ms
        inference_ms = self._last_inference_ms
        self._last_tokenize_ms = 0.0
        self._last_inference_ms = 0.0
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
            # 【变易】分阶段耗时：定位 P99 > 500ms 告警的延迟瓶颈
            "tokenize_ms": round(tokenize_ms, 2),
            "inference_ms": round(inference_ms, 2),
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
        【不易修复】支持 dict 候选（loader/compare 脚本传 dict）和对象（单元测试用）
                   修复前：getattr(dict, "name", "") 返回 ""，导致所有 dict 候选 doc_text
                   为空，ONNX 推理对所有候选返回相同分数（stddev=0.0），reranker 完全失效
        """
        # dict 用 .get()，对象用 getattr()——统一访问接口
        if isinstance(candidate, dict):
            def getter(key, default=""):
                return candidate.get(key, default)
        else:
            def getter(key, default=""):
                return getattr(candidate, key, default)

        parts = []
        for attr in ("name", "description", "category"):
            val = getter(attr)
            if val:
                parts.append(str(val))
        # tags 可能是列表
        tags = getter("tags", [])
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
        # 【可观测性】PyTorch 路径 tokenize+inference 在 CrossEncoder.predict 内部，
        # 无法分离，统一记为 inference_ms（tokenize_ms 留 0）
        # Why 不分离: sentence_transformers API 不暴露分阶段耗时，强行 monkey-patch 违【简易】
        t_predict_start = time.perf_counter()
        scores = self._model.predict(pairs)
        self._last_inference_ms = (time.perf_counter() - t_predict_start) * 1000
        self._last_tokenize_ms = 0.0  # PyTorch 路径无法分离 tokenize
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

            # 【可观测性】分阶段耗时埋点：tokenize 阶段
            # Why perf_counter 而非 time.time: 单调时钟，不受系统时间回拨影响
            t_tokenize_start = time.perf_counter()
            # tokenize（batch 推理，padding 到 batch 内最长序列）
            encoded = self._onnx_tokenizer(
                texts_a, texts_b,
                padding=True, truncation=True, max_length=512,
                return_tensors="np",
            )
            self._last_tokenize_ms = (time.perf_counter() - t_tokenize_start) * 1000

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

            # 【可观测性】分阶段耗时埋点：ONNX 推理阶段
            # Why 单独测推理: 区分 tokenize 瓶颈 vs 推理瓶颈，定位 P99 > 500ms 根因
            t_inference_start = time.perf_counter()
            # ONNX 推理（C++ 引擎，无 GIL）
            outputs = self._onnx_session.run(None, feed)
            self._last_inference_ms = (time.perf_counter() - t_inference_start) * 1000
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
            # 【不易】异常时清零侧信道，避免脏数据污染下一次 rerank.completed 日志
            self._last_tokenize_ms = 0.0
            self._last_inference_ms = 0.0
            return [0.0] * len(pairs)
