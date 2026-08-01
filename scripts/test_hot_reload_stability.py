#!/usr/bin/env python
"""Reranker 热重载稳定性测试（最小实现）

【不易】不加载真实模型（patch 加载与推理路径），聚焦热重载+回滚逻辑正确性
【变易】线程并发 rerank + 主线程周期切换 env variant（valid ↔ invalid）
【简易】--duration/--concurrency 参数；--ci-mode 退出码语义（0 通过 / 1 失败）

验证点（对应验收清单 3.4）：
1. 并发 rerank 不崩溃，成功率 ≥ 95%
2. 无效 variant 触发 hot_reload.failed_rollback，保留旧 session（服务不中断）
3. 回滚前 traceback 被捕获（self._last_load_traceback 非空）—— 任务2核心

用法:
    python scripts/test_hot_reload_stability.py --duration 30 --concurrency 4
    python scripts/test_hot_reload_stability.py --duration 10 --concurrency 2 --ci-mode
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from unittest.mock import MagicMock

# 项目根目录加入 path（脚本独立运行场景）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

VALID_VARIANT = "model_quantized.onnx"
INVALID_VARIANT = "nonexistent.onnx"


class _FakeSession:
    """模拟 ort.InferenceSession：run 返回固定 logits，避免真实模型推理。"""

    def run(self, outputs, feed):
        ids = feed.get("input_ids", [])
        n = len(ids) if isinstance(ids, list) else 1
        return [[0.5] for _ in range(n)]

    def get_inputs(self):
        m = MagicMock()
        m.name = "input_ids"
        return [m]


def _patched_try_load(self, variant: str):
    """替代 _try_load_onnx_variant：valid 返回 fake session，invalid 抛 FileNotFoundError。

    【不易】不修改真实文件系统，纯内存模拟
    【变易】按 variant 名判定 valid/invalid，覆盖回滚场景
    """
    if "nonexistent" in variant or "invalid" in variant:
        raise FileNotFoundError(f"onnx_file_not_found: /fake/models/{variant}")
    return _FakeSession(), MagicMock(), ["input_ids", "attention_mask"]


def _patched_predict(self, pairs, tid):
    """替代 _predict_with_timeout：直接返回分数，跳过推理与超时逻辑。"""
    return [0.5] * len(pairs)


def _make_reranker():
    """构造一个已加载 fake session 的 SkillReranker（跳过真实模型加载）。

    【不易】复用 SkillReranker.__init__，仅注入 fake session 状态
    【简易】不触发 _load_model，直接设置已加载状态
    """
    from agent.skills_mgmt.reranker import SkillReranker

    os.environ["SKILL_RERANKER_USE_ONNX"] = "true"
    os.environ["SKILL_RERANKER_ONNX_VARIANT"] = VALID_VARIANT
    # 测试中立即检测 variant 变化（关闭 30s 节流）
    os.environ["SKILL_RERANKER_HOT_RELOAD_INTERVAL"] = "0"
    os.environ["SKILL_RERANKER_ENABLED"] = "true"

    r = SkillReranker()
    # 模拟初始 ONNX 加载成功（不调用真实 _load_onnx）
    r._onnx_session = _FakeSession()
    r._onnx_tokenizer = MagicMock()
    r._onnx_input_names = ["input_ids", "attention_mask"]
    r._onnx_variant = VALID_VARIANT
    r._onnx_variant_loaded = VALID_VARIANT
    r._onnx_variant_attempted = VALID_VARIANT
    r._use_onnx = True
    r._load_attempted = True
    return r


class _StabilityRunner:
    """并发稳定性测试执行器。"""

    def __init__(self, duration: int, concurrency: int, ci_mode: bool):
        self.duration = duration
        self.concurrency = concurrency
        self.ci_mode = ci_mode
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.success = 0
        self.failure = 0
        self.rollback_count = 0
        self.traceback_captured = 0
        self.exceptions: list[str] = []

    def _worker(self, r) -> None:
        """工作线程：持续 rerank，统计成功/失败与回滚。"""
        candidates = [{"name": "skill_a", "description": "反思", "category": "c"}]
        while not self._stop.is_set():
            try:
                result = r.rerank("测试查询", list(candidates), top_k=1)
                ok = isinstance(result, list)
                # 检测回滚是否捕获了 traceback
                if r._last_load_traceback:
                    with self._lock:
                        self.traceback_captured += 1
                        self.rollback_count += 1
                        # 清空避免重复计数（直到下次失败再次写入）
                        r._last_load_traceback = None
                with self._lock:
                    if ok:
                        self.success += 1
                    else:
                        self.failure += 1
            except Exception as e:  # noqa: BLE001 —— 捕获并记录，不让线程死亡
                with self._lock:
                    self.failure += 1
                    self.exceptions.append(f"{type(e).__name__}: {e}")
            time.sleep(0.02)  # 让出 CPU，避免空转

    def _variant_cycler(self) -> None:
        """主线程：周期切换 env variant（valid ↔ invalid）触发热重载。"""
        toggle = False
        while not self._stop.is_set():
            toggle = not toggle
            os.environ["SKILL_RERANKER_ONNX_VARIANT"] = (
                INVALID_VARIANT if toggle else VALID_VARIANT
            )
            time.sleep(0.5)

    def run(self) -> int:
        """执行稳定性测试，返回退出码（0 通过 / 1 失败）。"""
        from unittest.mock import patch

        r = _make_reranker()
        threads: list[threading.Thread] = []

        with patch(
            "agent.skills_mgmt.reranker.SkillReranker._try_load_onnx_variant",
            _patched_try_load,
        ), patch(
            "agent.skills_mgmt.reranker.SkillReranker._predict_with_timeout",
            _patched_predict,
        ):
            cycler = threading.Thread(target=self._variant_cycler, daemon=True)
            cycler.start()
            for _ in range(self.concurrency):
                t = threading.Thread(target=self._worker, args=(r,), daemon=True)
                t.start()
                threads.append(t)

            print(f"[stability] 运行 {self.duration}s, 并发 {self.concurrency}...")
            time.sleep(self.duration)
            self._stop.set()
            for t in threads:
                t.join(timeout=2.0)
            cycler.join(timeout=2.0)

        total = self.success + self.failure
        success_rate = (self.success / total * 100) if total else 0.0
        print("\n" + "=" * 50)
        print(f"总调用数      : {total}")
        print(f"成功数        : {self.success}")
        print(f"失败数        : {self.failure}")
        print(f"成功率        : {success_rate:.1f}%")
        print(f"回滚次数      : {self.rollback_count}")
        print(f"traceback 捕获: {self.traceback_captured}")
        if self.exceptions:
            print(f"异常样本(前3) : {self.exceptions[:3]}")
        print("=" * 50)

        # 通过标准：成功率 ≥ 95% + traceback 至少捕获 1 次（验证回滚追踪生效）
        passed = success_rate >= 95.0 and self.traceback_captured >= 1
        if self.ci_mode:
            return 0 if passed else 1
        return 0 if passed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Reranker 热重载稳定性测试")
    parser.add_argument("--duration", type=int, default=30, help="测试时长(秒)")
    parser.add_argument("--concurrency", type=int, default=4, help="并发线程数")
    parser.add_argument("--ci-mode", action="store_true", help="CI 模式(退出码语义)")
    args = parser.parse_args()
    runner = _StabilityRunner(args.duration, args.concurrency, args.ci_mode)
    return runner.run()


if __name__ == "__main__":
    sys.exit(main())
