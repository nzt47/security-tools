"""重新生成 demo 技能谱系数据（data/evolution_archive.jsonl 默认路径）

背景:
    run_evolution_demo.py 与 offline_evolver.py 被并行会话反复覆盖回旧版
    （MockEnhancer 丢失 set_lineage_hook / 无条件调用），demo 不可依赖。
    本脚本自包含（内置 MockEnhancer），直接调用 OfflineEvolver 生成
    demo-cache-tuner / demo-search-optimize 的谱系记录。

运行方式:
    python scripts/generate_lineage_demo_data.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 使用默认档案路径（data/evolution_archive.jsonl），不隔离
from agent.skills_mgmt.enhancer import VersionBump
from agent.skills_mgmt.lineage import get_default_archive
from agent.skills_mgmt.models import (
    ContentType, Skill, SkillCategory, SkillMetrics, SkillStatus,
)
from agent.skills_mgmt.offline_evolver import OfflineEvolver


class _MockStore:
    def __init__(self, skills):
        self._data = {s.id: s for s in skills}

    def get(self, skill_id):
        if skill_id not in self._data:
            from agent.skills_mgmt.exceptions import SkillNotFoundError
            raise SkillNotFoundError(skill_id)
        return self._data[skill_id]

    def list_all(self):
        return list(self._data.values())

    def upsert(self, skill):
        self._data[skill.id] = skill


class _MockEnhancer:
    """模拟 SkillEnhancer：set_lineage_hook + bump_version 提交后触发谱系钩子"""

    def __init__(self):
        self._version_counter: dict = {}
        self._lineage_hook = None

    def set_lineage_hook(self, hook) -> None:
        self._lineage_hook = hook

    def bump_version(self, skill_id: str, kind: str, *,
                     changelog: str = "",
                     content=None,
                     eval_result=None) -> VersionBump:
        old_version = "1.0.0"
        count = self._version_counter.get(skill_id, 0) + 1
        self._version_counter[skill_id] = count
        new_version = f"1.0.{count}"
        hook = self._lineage_hook
        if hook is not None:
            try:
                hook({
                    "skill_id": skill_id, "kind": kind,
                    "old_version": old_version, "new_version": new_version,
                    "changelog": changelog, "eval_result": eval_result,
                })
            except Exception as e:  # noqa: BLE001 谱系失败不阻断版本升级
                print(f"  [warn] 谱系钩子失败: {e}")
        return VersionBump(old_version=old_version, new_version=new_version,
                           changelog=changelog)


def _make_skill(skill_id: str, name: str, *, usage: int, success_rate: float,
                avg_latency_ms: float, params: dict) -> Skill:
    return Skill(
        id=skill_id, name=name, description=f"Mock: {name}",
        category=SkillCategory.CUSTOM, status=SkillStatus.APPROVED,
        enabled=True, version="1.0.0", content_type=ContentType.MARKDOWN,
        default_params=params,
        metrics=SkillMetrics(
            usage_count=usage, success_count=int(usage * success_rate),
            failure_count=usage - int(usage * success_rate),
            success_rate=success_rate, avg_latency_ms=avg_latency_ms,
            p95_latency_ms=avg_latency_ms * 1.5, param_stats={},
        ),
    )


def main() -> None:
    skills = [
        _make_skill("demo-cache-tuner", "缓存调优", usage=30, success_rate=0.8,
                    avg_latency_ms=2000, params={"ttl": 300, "max_size": 1000}),
        _make_skill("demo-search-optimize", "搜索优化", usage=60, success_rate=0.7,
                    avg_latency_ms=1500, params={"top_k": 10, "threshold": 0.6}),
    ]
    evolver = OfflineEvolver(
        _MockStore(skills), _MockEnhancer(),
        min_usage=10, target_success_rate=0.95,
        max_variants_per_skill=5, improvement_threshold=0.01, random_seed=7,
    )
    archive = get_default_archive()
    for skill in skills:
        for _round in range(3):
            evolver.evolve_once(skill.id, trigger="manual")
    # 直接读档案文件统计（EvolutionArchive 无 list_all 接口）
    import json
    from collections import Counter
    counts = Counter()
    decisions = Counter()
    paths = [os.environ.get("EVOLUTION_ARCHIVE_PATH",
                            "data/evolution_archive.jsonl")]
    old = os.environ.get("EVOLUTION_ARCHIVE_OLD_PATH",
                         "data/evolution_archive_old.jsonl")
    if os.path.exists(old):
        paths.append(old)
    for p in paths:
        if not os.path.exists(p):
            continue
        for line in open(p, encoding="utf-8"):
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("object_id") in ("demo-cache-tuner",
                                      "demo-search-optimize"):
                counts[r["object_id"]] += 1
                decisions[r.get("decision")] += 1
    print(f"生成完成: 共 {sum(counts.values())} 条 demo 谱系记录（追加至默认档案）")
    print("技能分布:", dict(counts))
    print("决策分布:", dict(decisions))


if __name__ == "__main__":
    main()
