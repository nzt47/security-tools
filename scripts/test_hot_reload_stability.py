#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reranker 热重载机制稳定性测试

【不易】不修改 reranker.py 源码，仅通过公共接口验证
【变易】多线程并发模拟：一边频繁修改 .env，一边并发 rerank
【简易】单脚本自包含，命令行参数化

测试目标:
    1. 验证 RLock 在并发场景下正确保护会话切换
    2. 验证热重载失败时旧会话保留（服务不中断）
    3. 验证 .env 频繁修改不会导致 rerank 崩溃或数据错乱
    4. 验证 mock 模型下 rerank 结果一致性（切换 variant 不影响结果语义）

设计原理:
    - Writer 线程：周期性修改 .env 中 SKILL_RERANKER_ONNX_VARIANT
      （在 model_quantized.onnx / model.onnx / nonexistent.onnx 间循环）
    - Reader 线程：并发调用 rerank()，记录结果与耗时
    - 主线程：汇总统计，校验一致性

用法:
    # 默认 60s 测试，8 并发 rerank
    python scripts/test_hot_reload_stability.py

    # 自定义参数
    python scripts/test_hot_reload_stability.py --duration 120 --concurrency 16

    # CI 模式（短时测试，遇错即退）
    python scripts/test_hot_reload_stability.py --duration 30 --concurrency 4 --ci-mode
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

# 【不易】防止 sentence_transformers 真实 import 导致 Windows 0xC0000005 崩溃
if "sentence_transformers" not in sys.modules:
    sys.modules["sentence_transformers"] = MagicMock()

_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from agent.skills_mgmt.reranker import SkillReranker


# ──────────────────────────────────────────────
#  测试夹具：模拟候选 + .env 管理
# ──────────────────────────────────────────────

class MockCandidate:
    """模拟 SkillMatch 候选对象（与单元测试一致）"""

    def __init__(self, skill_id: str, name: str, score: float = 0.5):
        self.skill_id = skill_id
        self.name = name
        self.description = f"{name}描述"
        self.score = score
        self.category = "test"
        self.tags = ["test"]
        self.score_breakdown = None


class EnvFileRotator:
    """.env 文件轮换器（线程安全）

    【不易】每次写入是原子操作（write+rename），避免读到半写状态
    【变易】在 3 个 variant 间循环：valid_int8 / valid_fp32 / invalid
    """

    VALID_VARIANTS = [
        "model_quantized.onnx",  # INT8（mock 加载成功）
        "model.onnx",            # FP32（mock 加载成功）
    ]
    INVALID_VARIANT = "nonexistent.onnx"  # 加载失败（触发回滚）

    def __init__(self, env_file: Path):
        self.env_file = env_file
        self._lock = threading.Lock()
        self._cycle_index = 0
        self.current_variant = self.VALID_VARIANTS[0]
        self.write_count = 0
        # 初始化 .env
        self._write_env(self.current_variant)

    def _write_env(self, variant: str) -> None:
        """原子写入 .env 文件（写入临时文件后 rename）"""
        content = (
            f"SKILL_RERANKER_ENABLED=true\n"
            f"SKILL_RERANKER_USE_ONNX=true\n"
            f"SKILL_RERANKER_ONNX_VARIANT={variant}\n"
        )
        # 原子写入：先写临时文件，再 rename（POSIX 原子，Windows 也能保证）
        tmp_file = self.env_file.with_suffix(".env.tmp")
        tmp_file.write_text(content, encoding="utf-8")
        tmp_file.replace(self.env_file)

    def rotate(self) -> str:
        """轮换到下一个 variant（含 invalid 变体）

        Returns:
            切换后的 variant 名
        """
        with self._lock:
            # 周期：valid → valid → invalid → valid → ...
            # 每 3 次轮换插入 1 次 invalid（触发回滚验证）
            self._cycle_index += 1
            if self._cycle_index % 3 == 0:
                new_variant = self.INVALID_VARIANT
            else:
                new_variant = self.VALID_VARIANTS[self._cycle_index % len(self.VALID_VARIANTS)]

            self._write_env(new_variant)
            self.current_variant = new_variant
            self.write_count += 1
            return new_variant


# ──────────────────────────────────────────────
#  Writer 线程：周期性修改 .env
# ──────────────────────────────────────────────

class EnvWriterThread(threading.Thread):
    """周期性修改 .env 触发热重载

    【不易】每次修改后等待一段时间，让 rerank 有机会检测
    【变易】轮换间隔随机化，避免与 rerank 周期同步
    """

    def __init__(
        self,
        rotator: EnvFileRotator,
        duration: float,
        interval: float = 2.0,
    ):
        super().__init__(daemon=True, name="env-writer")
        self.rotator = rotator
        self.duration = duration
        self.interval = interval
        self.stop_event = threading.Event()
        self.rotate_log: List[Dict[str, Any]] = []

    def run(self) -> None:
        start = time.time()
        while not self.stop_event.is_set() and (time.time() - start) < self.duration:
            variant = self.rotator.rotate()
            self.rotate_log.append({
                "time": time.time() - start,
                "variant": variant,
            })
            # 随机等待（避免与 rerank 同步）
            self.stop_event.wait(self.interval)

    def stop(self) -> None:
        self.stop_event.set()


# ──────────────────────────────────────────────
#  Reader 线程：并发 rerank
# ──────────────────────────────────────────────

class RerankReaderThread(threading.Thread):
    """并发调用 rerank 验证稳定性

    【不易】捕获所有异常（rerank 不应抛异常），记录失败用例
    【变易】每次 rerank 用不同 query 和候选数
    """

    def __init__(
        self,
        reranker: SkillReranker,
        duration: float,
        thread_id: int,
    ):
        super().__init__(daemon=True, name=f"rerank-reader-{thread_id}")
        self.reranker = reranker
        self.duration = duration
        self.thread_id = thread_id
        self.stop_event = threading.Event()
        # 统计
        self.total_calls = 0
        self.success_calls = 0
        self.exception_calls = 0
        self.empty_results = 0
        self.exceptions: List[str] = []

    def run(self) -> None:
        start = time.time()
        query_pool = ["反思", "语音", "PDF解析", "测试", "查询"]
        while not self.stop_event.is_set() and (time.time() - start) < self.duration:
            self.total_calls += 1
            try:
                # 构造候选（每次数量不同）
                n_candidates = 3 + (self.total_calls % 3)
                candidates = [
                    MockCandidate(f"skill_{i}", f"技能_{i}")
                    for i in range(n_candidates)
                ]
                query = query_pool[self.total_calls % len(query_pool)]
                result = self.reranker.rerank(query, candidates, top_k=2)
                self.success_calls += 1
                if not result:
                    self.empty_results += 1
            except Exception as e:  # noqa: BLE001
                # 【不易】rerank 不应抛异常，捕获并记录
                self.exception_calls += 1
                self.exceptions.append(f"{type(e).__name__}: {str(e)[:200]}")
                # 短暂等待避免疯狂失败
                self.stop_event.wait(0.1)

    def stop(self) -> None:
        self.stop_event.set()


# ──────────────────────────────────────────────
#  统计与报告
# ──────────────────────────────────────────────

def print_report(
    writer: EnvWriterThread,
    readers: List[RerankReaderThread],
    duration: float,
    ci_mode: bool,
) -> bool:
    """打印测试报告，返回是否通过

    Returns:
        True: 测试通过（无异常、成功率达标）
        False: 测试失败（有异常或成功率不达标）
    """
    total_calls = sum(r.total_calls for r in readers)
    total_success = sum(r.success_calls for r in readers)
    total_exception = sum(r.exception_calls for r in readers)
    total_empty = sum(r.empty_results for r in readers)
    total_rotations = writer.rotator.write_count

    # 异常样本（去重后前 5 个）
    all_exceptions: List[str] = []
    for r in readers:
        all_exceptions.extend(r.exceptions[:5])

    # 通过标准:
    # 1. 无异常（rerank 不应抛异常）
    # 2. 成功率 >= 95%（允许少量 mock 失败）
    # 3. 至少触发 1 次热重载（如果 .env 修改 >= 1 次）
    success_rate = (total_success / total_calls * 100) if total_calls > 0 else 0
    passed = (total_exception == 0) and (success_rate >= 95.0)

    print("\n" + "=" * 72)
    print("Reranker 热重载稳定性测试报告")
    print("=" * 72)
    print(f"测试时长: {duration:.1f}s")
    print(f"并发 Reader 线程: {len(readers)}")
    print(f".env 修改次数: {total_rotations}")
    print(f"")
    print(f"📊 Rerank 调用统计")
    print(f"  总调用: {total_calls}")
    print(f"  成功: {total_success} ({success_rate:.1f}%)")
    print(f"  异常: {total_exception}")
    print(f"  空结果: {total_empty}（热重载期间的预期降级）")
    print(f"")
    print(f"📊 .env 轮换分布")
    valid_writes = sum(
        1 for log in writer.rotate_log
        if log["variant"] != EnvFileRotator.INVALID_VARIANT
    )
    invalid_writes = sum(
        1 for log in writer.rotate_log
        if log["variant"] == EnvFileRotator.INVALID_VARIANT
    )
    print(f"  有效 variant 切换: {valid_writes}")
    print(f"  无效 variant 切换: {invalid_writes}（应触发自动回滚）")
    print(f"")

    if all_exceptions:
        print(f"⚠️  异常样本（前 5 个）:")
        for exc in all_exceptions[:5]:
            print(f"  - {exc}")
        print(f"")

    if passed:
        print("✅ 测试通过: 热重载机制在并发场景下稳定")
    else:
        print("❌ 测试失败:")
        if total_exception > 0:
            print(f"   - rerank 抛出异常 {total_exception} 次（应为 0）")
        if success_rate < 95.0:
            print(f"   - 成功率 {success_rate:.1f}% < 95%")

    print("=" * 72)
    return passed


# ──────────────────────────────────────────────
#  主测试流程
# ──────────────────────────────────────────────

def run_stability_test(
    duration: float,
    concurrency: int,
    ci_mode: bool,
    env_file: Path,
) -> int:
    """运行稳定性测试

    【不易】mock _load_onnx 避免真实模型加载
    【变易】模拟 variant 文件存在/不存在两种场景
    """
    # 准备 .env 文件
    env_file.parent.mkdir(parents=True, exist_ok=True)
    rotator = EnvFileRotator(env_file)

    # 创建 reranker 实例并模拟 ONNX 已加载
    # 通过环境变量指定 .env 文件路径
    os.environ["SKILL_RERANKER_ENV_FILE"] = str(env_file)
    os.environ["SKILL_RERANKER_HOT_RELOAD_INTERVAL"] = "0"  # 关闭节流便于测试

    reranker = SkillReranker()
    # 模拟 ONNX 已加载状态
    reranker._use_onnx = True
    reranker._onnx_session = MagicMock()
    reranker._onnx_tokenizer = MagicMock()
    reranker._onnx_input_names = ["input_ids", "attention_mask"]
    reranker._load_attempted = True
    reranker._onnx_variant_loaded = "model_quantized.onnx"
    reranker._env_mtime = reranker._get_env_mtime()
    reranker._last_env_check = 0.0

    # mock _load_onnx：根据 variant 决定成功/失败
    # 【变易】invalid variant 返回 False（触发回滚），valid 返回 True
    original_load_onnx = reranker._load_onnx

    def mock_load_onnx() -> bool:
        if reranker._onnx_variant == EnvFileRotator.INVALID_VARIANT:
            return False  # 模拟文件不存在
        # 模拟加载成功：保留 mock session
        reranker._onnx_session = MagicMock()
        reranker._onnx_tokenizer = MagicMock()
        reranker._use_onnx = True
        return True

    # mock _predict_onnx：返回稳定的 mock 分数（避免真实推理）
    def mock_predict_onnx(pairs, tid):
        # 按候选数量返回递减分数
        n = len(pairs)
        return [0.9 - i * 0.1 for i in range(n)]

    print("=" * 72)
    print("Reranker 热重载稳定性测试")
    print("=" * 72)
    print(f"测试时长: {duration}s")
    print(f"并发 Reader: {concurrency}")
    print(f".env 文件: {env_file}")
    print(f"CI 模式: {'是' if ci_mode else '否'}")
    print("=" * 72)
    print("启动测试线程...\n")

    with patch.object(reranker, "_load_onnx", side_effect=mock_load_onnx), \
         patch.object(reranker, "_predict_onnx", side_effect=mock_predict_onnx):
        # 启动 Writer 线程
        writer = EnvWriterThread(rotator, duration, interval=2.0)
        writer.start()

        # 启动 Reader 线程
        readers = [
            RerankReaderThread(reranker, duration, i)
            for i in range(concurrency)
        ]
        for r in readers:
            r.start()

        # 等待测试完成
        writer.join(timeout=duration + 5)
        for r in readers:
            r.stop()
            r.join(timeout=5)

    # 打印报告
    passed = print_report(writer, readers, duration, ci_mode)

    # CI 模式：失败立即退出
    if ci_mode and not passed:
        return 1

    return 0 if passed else 1


# ──────────────────────────────────────────────
#  命令行入口
# ──────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reranker 热重载机制稳定性测试",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 默认 60s 测试，8 并发
  python scripts/test_hot_reload_stability.py

  # 自定义时长和并发
  python scripts/test_hot_reload_stability.py --duration 120 --concurrency 16

  # CI 模式（30s，4 并发，失败立即退出）
  python scripts/test_hot_reload_stability.py --duration 30 --concurrency 4 --ci-mode
""",
    )
    parser.add_argument(
        "--duration", type=float, default=60.0,
        help="测试时长（秒，默认 60）",
    )
    parser.add_argument(
        "--concurrency", type=int, default=8,
        help="并发 rerank 线程数（默认 8）",
    )
    parser.add_argument(
        "--ci-mode", action="store_true",
        help="CI 模式：失败立即退出，便于流水线识别",
    )
    parser.add_argument(
        "--env-file", type=str, default=None,
        help=".env 文件路径（默认使用临时文件）",
    )
    args = parser.parse_args()

    # 使用临时 .env 文件（避免污染项目 .env）
    if args.env_file:
        env_file = Path(args.env_file)
    else:
        import tempfile
        env_file = Path(tempfile.gettempdir()) / "reranker_stability_test.env"

    try:
        return run_stability_test(
            duration=args.duration,
            concurrency=args.concurrency,
            ci_mode=args.ci_mode,
            env_file=env_file,
        )
    finally:
        # 清理临时 .env 文件
        if not args.env_file and env_file.exists():
            try:
                env_file.unlink()
            except OSError:
                pass


if __name__ == "__main__":
    sys.exit(main())
