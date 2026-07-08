"""OfflineEvolver 批量进化流程演示脚本

构造 3 个 Mock Skill (满足候选条件),运行 evolve_batch 展示:
    1. 候选选择 → 变异体生成 → 评估 → 帕累托筛选 → 提交
    2. 日志埋点输出 (帕累托性能指标)
    3. 进化报告汇总

运行方式:
    python run_evolution_demo.py
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent))

from agent.skills_mgmt.models import Skill, SkillMetrics, SkillCategory, SkillStatus, ContentType
from agent.skills_mgmt.enhancer import VersionBump
from agent.skills_mgmt.offline_evolver import (
    OfflineEvolver, EvolutionStrategy, EvolutionResult, BatchEvolutionReport,
)


# ════════════════════════════════════════════════════════════
#  Mock 数据构造
# ════════════════════════════════════════════════════════════

def _make_skill(skill_id: str, name: str, *,
                usage: int, success_rate: float, avg_latency_ms: float,
                params: Dict, param_stats: Optional[Dict] = None) -> Skill:
    """构造单个 Mock Skill"""
    success_count = int(usage * success_rate)
    return Skill(
        id=skill_id,
        name=name,
        description=f"Mock skill for evolution demo: {name}",
        category=SkillCategory.CUSTOM,
        status=SkillStatus.APPROVED,
        enabled=True,
        version="1.0.0",
        content_type=ContentType.MARKDOWN,
        default_params=params,
        metrics=SkillMetrics(
            usage_count=usage,
            success_count=success_count,
            failure_count=usage - success_count,
            success_rate=success_rate,
            avg_latency_ms=avg_latency_ms,
            p95_latency_ms=avg_latency_ms * 1.5,
            param_stats=param_stats or {},
        ),
    )


def build_mock_skills() -> List[Skill]:
    """构造 3 个 Mock Skill (均满足进化候选条件)"""
    return [
        _make_skill(
            "demo-search-optimize", "搜索优化技能",
            usage=50, success_rate=0.70, avg_latency_ms=3000,
            params={"threshold": 0.5, "max_results": 100, "boost_factor": 1.2},
            param_stats={
                "abc12345": {
                    "params": {"threshold": 0.3, "max_results": 50, "boost_factor": 1.5},
                    "success": 8, "failure": 2, "total_latency_ms": 25000,
                },
                "def67890": {
                    "params": {"threshold": 0.7, "max_results": 200, "boost_factor": 0.8},
                    "success": 3, "failure": 7, "total_latency_ms": 40000,
                },
            },
        ),
        _make_skill(
            "demo-cache-tuner", "缓存调优技能",
            usage=30, success_rate=0.80, avg_latency_ms=2000,
            params={"ttl": 300, "max_size": 1000, "eviction_policy": "lru"},
        ),
        _make_skill(
            "demo-batch-processor", "批处理技能",
            usage=100, success_rate=0.60, avg_latency_ms=5000,
            params={"batch_size": 64, "parallelism": 4, "timeout_sec": 30},
            param_stats={
                "ghi11111": {
                    "params": {"batch_size": 128, "parallelism": 8, "timeout_sec": 60},
                    "success": 15, "failure": 5, "total_latency_ms": 80000,
                },
            },
        ),
    ]


# ════════════════════════════════════════════════════════════
#  Mock Store / Enhancer
# ════════════════════════════════════════════════════════════

class MockStore:
    """模拟 SkillStore — 内存存储"""
    def __init__(self, skills: List[Skill]):
        self._data: Dict[str, Skill] = {s.id: s for s in skills}

    def get(self, skill_id: str) -> Skill:
        if skill_id not in self._data:
            from agent.skills_mgmt.exceptions import SkillNotFoundError
            raise SkillNotFoundError(skill_id)
        return self._data[skill_id]

    def list_all(self) -> List[Skill]:
        return list(self._data.values())

    def upsert(self, skill: Skill) -> None:
        self._data[skill.id] = skill


class MockEnhancer:
    """模拟 SkillEnhancer — 仅实现 bump_version"""
    def __init__(self):
        self._version_counter: Dict[str, int] = {}

    def bump_version(self, skill_id: str, kind: str, *,
                     changelog: str = "",
                     content: Optional[str] = None) -> VersionBump:
        old_version = "1.0.0"
        # 简单递增 patch 版本
        count = self._version_counter.get(skill_id, 0) + 1
        self._version_counter[skill_id] = count
        new_version = f"1.0.{count}"
        return VersionBump(
            old_version=old_version,
            new_version=new_version,
            changelog=changelog,
        )


# ════════════════════════════════════════════════════════════
#  日志配置
# ════════════════════════════════════════════════════════════

def setup_logging():
    """配置日志输出到 stdout,展示 offline_evolver 的结构化日志"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        stream=sys.stdout,
    )
    # 确保 offline_evolver 的日志可见
    logging.getLogger("agent.skills_mgmt.observability").setLevel(logging.INFO)


# ════════════════════════════════════════════════════════════
#  报告打印
# ════════════════════════════════════════════════════════════

def print_report(report: BatchEvolutionReport) -> None:
    """格式化打印进化报告"""
    print("\n" + "=" * 70)
    print("  批量进化报告 (BatchEvolutionReport)")
    print("=" * 70)
    print(f"  开始时间:  {report.started_at}")
    print(f"  结束时间:  {report.finished_at}")
    print(f"  技能总数:  {report.total_skills}")
    print(f"  已进化:    {report.evolved_count}")
    print(f"  已跳过:    {report.skipped_count}")
    print(f"  失败:      {report.failed_count}")
    print(f"  平均提升:  {report.avg_improvement}")
    print("-" * 70)

    for i, r in enumerate(report.results, 1):
        if r.committed:
            status = "✓ COMMITTED"
        elif r.skipped:
            status = "⊘ SKIPPED"
        elif r.error:
            status = "✗ FAILED"
        else:
            status = "⊘ BELOW_THRESHOLD"
        print(f"  [{i}] {r.skill_id}")
        print(f"      状态:     {status}")
        if r.strategy:
            print(f"      策略:     {r.strategy.value}")
        if r.old_version:
            print(f"      版本:     {r.old_version} → {r.new_version or '(未提交)'}")
        print(f"      提升:     {r.improvement:+.4f}")
        if r.error:
            print(f"      说明:     {r.error}")
        print()

    print("=" * 70)


# ════════════════════════════════════════════════════════════
#  主流程
# ════════════════════════════════════════════════════════════

def main():
    setup_logging()

    print("=" * 70)
    print("  OfflineEvolver 批量进化流程演示")
    print("=" * 70)
    print()

    # 1. 构造 Mock 数据
    skills = build_mock_skills()
    print(f"[1] 构造 Mock 技能: {len(skills)} 个")
    for s in skills:
        print(f"    - {s.id}: usage={s.metrics.usage_count}, "
              f"success_rate={s.metrics.success_rate:.2f}, "
              f"latency={s.metrics.avg_latency_ms:.0f}ms, "
              f"params={list(s.default_params.keys())}")
    print()

    # 2. 实例化 OfflineEvolver
    store = MockStore(skills)
    enhancer = MockEnhancer()
    evolver = OfflineEvolver(
        store, enhancer,
        min_usage=10,
        target_success_rate=0.95,
        max_variants_per_skill=5,
        improvement_threshold=0.01,  # 降低阈值以便演示提交
        random_seed=42,  # 可复现
    )
    print(f"[2] 实例化 OfflineEvolver (seed=42, threshold=0.01)")
    print(f"    min_usage={evolver.min_usage}, target_rate={evolver.target_success_rate}")
    print()

    # 3. 运行批量进化 (2 轮)
    print("[3] 运行 evolve_batch (max_rounds=2)...")
    print("-" * 70)
    report = evolver.evolve_batch(max_rounds=2)
    print("-" * 70)

    # 4. 打印报告
    print_report(report)

    # 5. 单独演示 evolve 命令 (指定策略)
    print("\n[5] 单技能进化演示 (指定 FINE_TUNE 策略)...")
    print("-" * 70)
    result = evolver.evolve_once(
        "demo-search-optimize",
        strategies=[EvolutionStrategy.FINE_TUNE, EvolutionStrategy.FINE_TUNE],
    )
    print(f"    结果: skipped={result.skipped}, committed={result.committed}, "
          f"improvement={result.improvement:+.4f}")
    print()

    print("=" * 70)
    print("  演示完成 — 查看上方日志中的 pareto_filter.detail 了解帕累托筛选性能")
    print("=" * 70)


if __name__ == "__main__":
    main()
