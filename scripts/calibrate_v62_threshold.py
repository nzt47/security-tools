"""v6.2 阈值校准脚本 — 基于 BGE-m3 相似度分布推荐最优阈值

用途:
    1. 加载 40 个正样本黄金集 + 25 个负样本扩展集
    2. 用 BGE-m3 编码所有样本与 prototype 样本
    3. 计算每个样本与所有 prototype 的 max 余弦相似度
    4. 输出正/负样本相似度分布（min/p50/p95/max）
    5. 推荐阈值 = (正样本 min + 负样本 max) / 2（留 5% margin 偏向正样本）
    6. prototype 冲突检查：每个 prototype 与 8 个技能 description 的相似度 < 0.5
    7. 用推荐阈值验证正样本 0 误伤 + 负样本最大化覆盖

设计原则:
    【不易】只读不改：不修改任何 production 代码，仅输出推荐阈值供人工确认
    【不易】失败降级：BGE-m3 不可用时友好退出，不抛异常
    【不易】正样本 0 误伤校验：用推荐阈值验证 40 正样本全部不被命中
    【变易】支持 --output 导出 JSON 报告 / --threshold 手动验证 / --dry-run 仅检查结构
    【简易】单文件可运行，复用 SkillVectorAdapter.encode_query

用法:
    # 完整校准（加载 BGE-m3，耗时较长）
    python scripts/calibrate_v62_threshold.py

    # 仅检查样本集结构（不加载模型，秒级）
    python scripts/calibrate_v62_threshold.py --dry-run

    # 用指定阈值验证（验证已有阈值的覆盖与误伤）
    python scripts/calibrate_v62_threshold.py --threshold 0.72

    # 导出 JSON 报告
    python scripts/calibrate_v62_threshold.py --output scripts/output/v62_calibration.json

输出:
    控制台: 人类可读的分布表 + 推荐阈值 + 冲突警告
    JSON 报告: 完整统计供文档引用

退出码:
    0: 校准成功，正样本 0 误伤
    1: BGE-m3 不可用或样本集缺失
    2: 推荐阈值导致正样本误伤（违【不易】），需调整 prototype 或回退路径 A
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 项目根目录（守【简易】，与 verify_v61 一致）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ════════════════════════════════════════════════════════════
#  样本集路径
# ════════════════════════════════════════════════════════════

_GOLDEN_SET = _PROJECT_ROOT / "tests" / "eval" / "skill_retrieval_golden_set.json"
_NEGATIVE_SET = _PROJECT_ROOT / "tests" / "eval" / "negative_samples_extended.json"
_PROTOTYPES = _PROJECT_ROOT / "tests" / "eval" / "negative_intent_prototypes.json"


# ════════════════════════════════════════════════════════════
#  数据结构
# ════════════════════════════════════════════════════════════

@dataclass
class Sample:
    """单个样本（正样本或负样本）"""
    case_id: str
    query: str
    expected: List[str]      # 正样本为技能 ID 列表，负样本为空
    category: str            # 正样本为技能类别，负样本为 negative_*


@dataclass
class SampleSim:
    """样本的相似度统计"""
    case_id: str
    query: str
    max_sim: float           # 与所有 prototype 的最大相似度
    matched_category: str   # 最大相似度对应的 prototype 类别
    is_positive: bool


@dataclass
class DistributionStats:
    """相似度分布统计"""
    label: str
    count: int
    min_val: float
    p25: float
    p50: float
    p75: float
    p95: float
    max_val: float
    mean_val: float


@dataclass
class CalibrationReport:
    """校准报告"""
    timestamp: str
    embedding_model: str
    total_positives: int
    total_negatives: int
    prototype_categories: List[str] = field(default_factory=list)
    positive_stats: Optional[DistributionStats] = None
    negative_stats: Optional[DistributionStats] = None
    recommended_threshold: float = 0.0
    has_overlap: bool = False     # 正负样本分布是否有重叠
    conflict_skills: List[Dict[str, Any]] = field(default_factory=list)
    verify_result: Dict[str, Any] = field(default_factory=dict)
    sample_sims: List[Dict[str, Any]] = field(default_factory=list)


# ════════════════════════════════════════════════════════════
#  样本加载
# ════════════════════════════════════════════════════════════

def load_positive_samples() -> List[Sample]:
    """加载正样本黄金集（仅 expected_skill_ids 非空）

    【不易】仅返回 expected 非空的正样本（真技能意图），负样本不参与"不误伤"断言
    """
    if not _GOLDEN_SET.exists():
        print(f"❌ 黄金集不存在: {_GOLDEN_SET}", file=sys.stderr)
        return []
    with _GOLDEN_SET.open("r", encoding="utf-8") as f:
        data = json.load(f)
    samples = []
    for c in data["test_cases"]:
        expected = c.get("expected_skill_ids") or []
        if not expected:
            continue  # 跳过黄金集中的负样本（expected=[]）
        samples.append(Sample(
            case_id=c["case_id"],
            query=c["query"],
            expected=expected,
            category=c.get("category", ""),
        ))
    return samples


def load_negative_samples() -> List[Sample]:
    """加载负样本扩展集（全部 25 个）"""
    if not _NEGATIVE_SET.exists():
        print(f"❌ 负样本集不存在: {_NEGATIVE_SET}", file=sys.stderr)
        return []
    with _NEGATIVE_SET.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return [
        Sample(
            case_id=c["case_id"],
            query=c["query"],
            expected=[],  # 负样本 expected 为空
            category=c.get("category", ""),
        )
        for c in data["test_cases"]
    ]


def load_prototypes() -> List[Dict[str, Any]]:
    """加载 prototype JSON"""
    if not _PROTOTYPES.exists():
        print(f"❌ prototype 文件不存在: {_PROTOTYPES}", file=sys.stderr)
        return []
    with _PROTOTYPES.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("categories", [])


# ════════════════════════════════════════════════════════════
#  向量编码与相似度计算
# ════════════════════════════════════════════════════════════

def build_prototype_matrix(
    adapter,
    prototypes: List[Dict[str, Any]],
) -> Tuple[Optional[Any], List[str], Dict[str, List[str]]]:
    """构建 prototype 均值向量矩阵

    复用 NegativeIntentDetector 的均值向量策略，但本脚本独立计算
    以避免 detector 内部缓存导致校准结果不可重复。

    Args:
        adapter: SkillVectorAdapter 实例
        prototypes: [{category, samples}, ...]

    Returns:
        (proto_matrix, categories, raw_samples)
        proto_matrix: np.ndarray (K, dim) 归一化后的均值向量
        categories: ["weather", "programming", ...]
        raw_samples: {"weather": ["今天天气怎么样", ...]}
        失败返回 (None, [], {})
    """
    import numpy as np

    categories: List[str] = []
    raw_samples: Dict[str, List[str]] = {}
    proto_vectors: List[Any] = []

    for cat in prototypes:
        cat_name = cat["category"]
        samples = cat.get("samples", [])
        if not samples:
            continue

        # 编码该类所有样本
        sample_vecs = []
        for s in samples:
            vec = adapter.encode_query(s)
            if vec is not None:
                sample_vecs.append(vec)

        if not sample_vecs:
            print(f"  ⚠️  类别 {cat_name} 所有样本编码失败，跳过")
            continue

        # 取均值并归一化（与 detector._load_prototypes 一致）
        mean_vec = np.mean(sample_vecs, axis=0)
        norm = np.linalg.norm(mean_vec)
        if norm > 0:
            mean_vec = mean_vec / norm

        categories.append(cat_name)
        raw_samples[cat_name] = samples
        proto_vectors.append(mean_vec)

    if not proto_vectors:
        return None, [], {}

    proto_matrix = np.stack(proto_vectors, axis=0)  # (K, dim)
    return proto_matrix, categories, raw_samples


def compute_sample_sims(
    adapter,
    proto_matrix,
    categories: List[str],
    samples: List[Sample],
    is_positive: bool,
    *,
    progress_label: str = "",
) -> List[SampleSim]:
    """计算每个样本与所有 prototype 的最大相似度

    Args:
        adapter: SkillVectorAdapter 实例
        proto_matrix: (K, dim) 归一化后的 prototype 矩阵
        categories: prototype 类别名列表
        samples: 待计算的样本列表
        is_positive: True 为正样本，False 为负样本
        progress_label: 进度提示标签

    Returns:
        List[SampleSim]
    """
    import numpy as np

    results: List[SampleSim] = []
    total = len(samples)

    for i, s in enumerate(samples, 1):
        q_vec = adapter.encode_query(s.query)
        if q_vec is None:
            print(f"  ⚠️  {s.case_id} 编码失败，跳过")
            continue

        # 点积 = 余弦相似度（已归一化）
        sims = proto_matrix @ q_vec  # (K,)
        max_idx = int(np.argmax(sims))
        max_sim = float(sims[max_idx])
        matched_cat = categories[max_idx]

        results.append(SampleSim(
            case_id=s.case_id,
            query=s.query,
            max_sim=max_sim,
            matched_category=matched_cat,
            is_positive=is_positive,
        ))

        if progress_label and i % 10 == 0:
            print(f"    {progress_label}: {i}/{total}")

    return results


# ════════════════════════════════════════════════════════════
#  分布统计
# ════════════════════════════════════════════════════════════

def compute_distribution(
    sims: List[SampleSim], label: str
) -> DistributionStats:
    """计算相似度分布统计"""
    import numpy as np

    values = [s.max_sim for s in sims]
    if not values:
        return DistributionStats(
            label=label, count=0,
            min_val=0, p25=0, p50=0, p75=0, p95=0, max_val=0, mean_val=0,
        )

    arr = np.array(values)
    return DistributionStats(
        label=label,
        count=len(values),
        min_val=float(np.min(arr)),
        p25=float(np.percentile(arr, 25)),
        p50=float(np.percentile(arr, 50)),
        p75=float(np.percentile(arr, 75)),
        p95=float(np.percentile(arr, 95)),
        max_val=float(np.max(arr)),
        mean_val=float(np.mean(arr)),
    )


def print_distribution(stats: DistributionStats) -> None:
    """打印分布统计表"""
    print(f"\n  {stats.label} ({stats.count} 个样本):")
    print(f"    min  = {stats.min_val:.4f}")
    print(f"    p25  = {stats.p25:.4f}")
    print(f"    p50  = {stats.p50:.4f}")
    print(f"    p75  = {stats.p75:.4f}")
    print(f"    p95  = {stats.p95:.4f}")
    print(f"    max  = {stats.max_val:.4f}")
    print(f"    mean = {stats.mean_val:.4f}")


# ════════════════════════════════════════════════════════════
#  阈值推荐
# ════════════════════════════════════════════════════════════

def recommend_threshold(
    pos_stats: DistributionStats,
    neg_stats: DistributionStats,
) -> Tuple[float, bool]:
    """推荐阈值

    策略:
        - 无重叠（正样本 min > 负样本 max）: 阈值 = (正 min + 负 max) / 2
        - 有重叠（正样本 min <= 负样本 max）: 阈值 = (正 min + 负 max) / 2 * 0.95
          偏向正样本侧，留 5% margin 保护正样本

    Returns:
        (recommended_threshold, has_overlap)
    """
    pos_min = pos_stats.min_val
    neg_max = neg_stats.max_val
    has_overlap = pos_min <= neg_max

    if has_overlap:
        # 有重叠：偏向正样本，留 5% margin
        midpoint = (pos_min + neg_max) / 2
        recommended = midpoint * 0.95
        print(f"\n  ⚠️  正负样本分布有重叠（pos_min={pos_min:.4f} <= neg_max={neg_max:.4f}）")
        print(f"      偏向正样本侧，留 5% margin 保护正样本")
    else:
        # 无重叠：取中点
        recommended = (pos_min + neg_max) / 2
        print(f"\n  ✅ 正负样本分布无重叠（pos_min={pos_min:.4f} > neg_max={neg_max:.4f}）")

    print(f"  推荐阈值: {recommended:.4f}")
    return recommended, has_overlap


# ════════════════════════════════════════════════════════════
#  prototype 与技能 description 冲突检查
# ════════════════════════════════════════════════════════════

def check_prototype_skill_conflict(
    adapter,
    proto_matrix,
    proto_categories: List[str],
    conflict_threshold: float = 0.5,
) -> List[Dict[str, Any]]:
    """检查每个 prototype 与所有技能 description 的相似度

    【不易】prototype 与技能 description 相似度必须 < conflict_threshold
           否则会误伤正样本，需要移除该 prototype 样本或调整类别

    Args:
        adapter: SkillVectorAdapter 实例（提供 encode_query）
        proto_matrix: (K, dim) prototype 矩阵
        proto_categories: prototype 类别名
        conflict_threshold: 冲突阈值（默认 0.5）

    Returns:
        冲突列表 [{prototype_category, skill_id, skill_name, similarity}, ...]
    """
    from agent.skills_mgmt.file_store import SkillFileStore

    fs = SkillFileStore()
    meta_index = fs.load_metadata_index()

    print(f"\n  Prototype 与技能 description 冲突检查（阈值 {conflict_threshold}）:")
    print(f"  技能总数: {len(meta_index)}")

    conflicts: List[Dict[str, Any]] = []

    for skill_id, meta in meta_index.items():
        skill_name = meta.get("name", skill_id)
        description = meta.get("description", "")

        # 构建技能向量输入：name + description + tags
        tags = " ".join(meta.get("tags", []) or [])
        skill_text = f"{skill_name} {description} {tags}".strip()
        if not skill_text:
            continue

        skill_vec = adapter.encode_query(skill_text)
        if skill_vec is None:
            continue

        # 计算技能向量与所有 prototype 的相似度
        import numpy as np
        sims = proto_matrix @ skill_vec  # (K,)

        for i, sim in enumerate(sims):
            if sim >= conflict_threshold:
                conflicts.append({
                    "prototype_category": proto_categories[i],
                    "skill_id": skill_id,
                    "skill_name": skill_name,
                    "similarity": float(sim),
                })
                print(f"    ❌ 冲突: prototype[{proto_categories[i]}] "
                      f"<-> skill[{skill_id}] sim={sim:.4f}")

    if not conflicts:
        print(f"  ✅ 无冲突: 所有 prototype 与技能 description 相似度 < {conflict_threshold}")

    return conflicts


# ════════════════════════════════════════════════════════════
#  阈值验证
# ════════════════════════════════════════════════════════════

def verify_with_threshold(
    pos_sims: List[SampleSim],
    neg_sims: List[SampleSim],
    threshold: float,
) -> Dict[str, Any]:
    """用指定阈值验证正样本 0 误伤 + 负样本覆盖率

    Returns:
        {
            "threshold": float,
            "positive_total": int,
            "positive_false_rejected": int,  # 误伤数（必须 0）
            "positive_false_rejected_cases": [...],
            "negative_total": int,
            "negative_correctly_rejected": int,  # 正确拒绝数
            "negative_missed": int,  # 漏判数
            "negative_missed_cases": [...],
            "coverage_rate": float,  # 负样本覆盖率
            "passed": bool,  # 正样本 0 误伤即为 passed
        }
    """
    # 正样本：max_sim >= threshold 即为误伤
    pos_false_rejected = [
        s for s in pos_sims if s.max_sim >= threshold
    ]

    # 负样本：max_sim >= threshold 即为正确拒绝
    neg_correctly_rejected = [
        s for s in neg_sims if s.max_sim >= threshold
    ]
    neg_missed = [s for s in neg_sims if s.max_sim < threshold]

    coverage = (
        len(neg_correctly_rejected) / len(neg_sims)
        if neg_sims else 0.0
    )

    passed = len(pos_false_rejected) == 0

    return {
        "threshold": threshold,
        "positive_total": len(pos_sims),
        "positive_false_rejected": len(pos_false_rejected),
        "positive_false_rejected_cases": [
            {"case_id": s.case_id, "query": s.query,
             "max_sim": s.max_sim, "matched_category": s.matched_category}
            for s in pos_false_rejected
        ],
        "negative_total": len(neg_sims),
        "negative_correctly_rejected": len(neg_correctly_rejected),
        "negative_missed": len(neg_missed),
        "negative_missed_cases": [
            {"case_id": s.case_id, "query": s.query,
             "max_sim": s.max_sim, "matched_category": s.matched_category}
            for s in neg_missed
        ],
        "coverage_rate": round(coverage, 4),
        "passed": passed,
    }


def print_verify_result(result: Dict[str, Any]) -> None:
    """打印阈值验证结果"""
    print(f"\n  阈值验证 (threshold={result['threshold']:.4f}):")
    print(f"    正样本: {result['positive_total']} 总 / "
          f"{result['positive_false_rejected']} 误伤")

    if result["positive_false_rejected"] > 0:
        print(f"    ❌ 正样本误伤（违【不易】）:")
        for c in result["positive_false_rejected_cases"]:
            print(f"      - {c['case_id']} sim={c['max_sim']:.4f} "
                  f"[{c['matched_category']}] {c['query']}")
    else:
        print(f"    ✅ 正样本 0 误伤（守【不易】）")

    print(f"    负样本: {result['negative_total']} 总 / "
          f"{result['negative_correctly_rejected']} 命中 / "
          f"{result['negative_missed']} 漏判")
    print(f"    负样本覆盖率: {result['coverage_rate']:.2%}")

    if result["negative_missed"] > 0:
        print(f"    漏判负样本（仍由 RRF+Reranker 兜底）:")
        for c in result["negative_missed_cases"][:10]:
            print(f"      - {c['case_id']} sim={c['max_sim']:.4f} "
                  f"[{c['matched_category']}] {c['query']}")

    if result["passed"]:
        print(f"  ✅ 验证通过: 正样本 0 误伤")
    else:
        print(f"  ❌ 验证失败: 正样本有误伤，需调整 prototype 或回退路径 A")


# ════════════════════════════════════════════════════════════
#  主流程
# ════════════════════════════════════════════════════════════

def dry_run_check() -> int:
    """--dry-run 模式：仅检查样本集结构（不加载模型）"""
    print("=" * 70)
    print("  v6.2 阈值校准 (dry-run: 仅检查样本集结构)")
    print("=" * 70)

    positives = load_positive_samples()
    negatives = load_negative_samples()
    prototypes = load_prototypes()

    if not positives or not negatives or not prototypes:
        return 1

    print(f"\n  正样本黄金集: {len(positives)} 个")
    print(f"  负样本扩展集: {len(negatives)} 个")
    print(f"  Prototype 类别: {len(prototypes)} 个")

    print(f"\n  Prototype 详情:")
    for cat in prototypes:
        samples = cat.get("samples", [])
        print(f"    {cat['category']:<20} {len(samples)} 样本")

    # 检查负样本类别与 prototype 类别映射
    print(f"\n  负样本类别分布:")
    neg_cats: Dict[str, int] = {}
    for n in negatives:
        neg_cats[n.category] = neg_cats.get(n.category, 0) + 1
    for cat, count in sorted(neg_cats.items()):
        print(f"    {cat:<30} {count}")

    # 检查 prototype 是否覆盖所有负样本类别（去除 negative_ 前缀）
    proto_cats = {c["category"] for c in prototypes}
    neg_cat_roots = {c.replace("negative_", "") for c in neg_cats}
    uncovered = neg_cat_roots - proto_cats
    if uncovered:
        print(f"\n  ⚠️  以下负样本类别无对应 prototype:")
        for c in sorted(uncovered):
            print(f"    - {c}")
    else:
        print(f"\n  ✅ 所有负样本类别均有对应 prototype")

    print(f"\n✅ dry-run 完成: 样本集结构正常")
    return 0


def run_calibration(
    *,
    threshold_override: Optional[float],
    output_path: Optional[Path],
    conflict_threshold: float,
) -> int:
    """运行完整校准流程"""
    print("=" * 70)
    print("  v6.2 阈值校准 (BGE-m3 相似度分布)")
    print(f"  时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    # ── Step 1: 加载样本集 ──
    print(f"\n[1/6] 加载样本集...")
    positives = load_positive_samples()
    negatives = load_negative_samples()
    prototypes = load_prototypes()

    if not positives:
        print(f"❌ 正样本集为空，终止", file=sys.stderr)
        return 1
    if not negatives:
        print(f"❌ 负样本集为空，终止", file=sys.stderr)
        return 1
    if not prototypes:
        print(f"❌ prototype 集为空，终止", file=sys.stderr)
        return 1

    print(f"  正样本: {len(positives)} 个")
    print(f"  负样本: {len(negatives)} 个")
    print(f"  Prototype: {len(prototypes)} 个类别")

    # ── Step 2: 初始化 vector adapter（加载 BGE-m3）──
    print(f"\n[2/6] 初始化 BGE-m3 模型...")
    print(f"  ⏳ 首次加载可能耗时数分钟（已缓存则 < 10s）")

    try:
        from agent.skills_mgmt.file_store import SkillFileStore
        from agent.skills_mgmt.vector_adapter import SkillVectorAdapter

        # 离线模式提示（与 verify_v61 一致的环境变量处理）
        os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
        os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

        adapter = SkillVectorAdapter(file_store=SkillFileStore())
        # 触发 BGE-m3 加载（ensure_indexed 会调用 _ensure_vector_store）
        indexed = adapter.ensure_indexed()
        if indexed == 0:
            print(f"❌ BGE-m3 加载失败或无技能可索引", file=sys.stderr)
            print(f"   请检查：", file=sys.stderr)
            print(f"   1. sentence-transformers 已安装", file=sys.stderr)
            print(f"   2. BAAI/bge-m3 模型已下载或 HF_ENDPOINT 可访问", file=sys.stderr)
            return 1
        print(f"  ✅ BGE-m3 已就绪，已索引 {indexed} 个技能")
    except Exception as e:
        print(f"❌ BGE-m3 初始化失败: {e}", file=sys.stderr)
        return 1

    # ── Step 3: 构建 prototype 矩阵 ──
    print(f"\n[3/6] 构建 prototype 均值向量矩阵...")
    proto_matrix, proto_categories, raw_samples = build_prototype_matrix(
        adapter, prototypes
    )
    if proto_matrix is None:
        print(f"❌ prototype 矩阵构建失败", file=sys.stderr)
        return 1
    print(f"  ✅ 矩阵 shape: {proto_matrix.shape}, 类别: {len(proto_categories)}")

    # ── Step 4: 计算样本相似度 ──
    print(f"\n[4/6] 编码样本并计算相似度...")
    print(f"  编码 {len(positives)} 个正样本...")
    pos_sims = compute_sample_sims(
        adapter, proto_matrix, proto_categories,
        positives, is_positive=True, progress_label="正样本",
    )
    print(f"  ✅ 正样本编码完成: {len(pos_sims)}/{len(positives)}")

    print(f"  编码 {len(negatives)} 个负样本...")
    neg_sims = compute_sample_sims(
        adapter, proto_matrix, proto_categories,
        negatives, is_positive=False, progress_label="负样本",
    )
    print(f"  ✅ 负样本编码完成: {len(neg_sims)}/{len(negatives)}")

    # ── Step 5: 分布统计与阈值推荐 ──
    print(f"\n[5/6] 相似度分布统计:")
    pos_stats = compute_distribution(pos_sims, "正样本相似度分布")
    neg_stats = compute_distribution(neg_sims, "负样本相似度分布")
    print_distribution(pos_stats)
    print_distribution(neg_stats)

    # 阈值推荐（或使用用户指定阈值）
    if threshold_override is not None:
        recommended = threshold_override
        print(f"\n  📍 使用用户指定阈值: {recommended:.4f}")
        has_overlap = pos_stats.min_val <= neg_stats.max_val
    else:
        recommended, has_overlap = recommend_threshold(pos_stats, neg_stats)

    # ── Step 6: prototype 冲突检查 ──
    print(f"\n[6/6] Prototype 与技能冲突检查...")
    conflicts = check_prototype_skill_conflict(
        adapter, proto_matrix, proto_categories,
        conflict_threshold=conflict_threshold,
    )

    # ── 用推荐阈值验证 ──
    print(f"\n{'='*70}")
    print(f"  阈值验证")
    print(f"{'='*70}")
    verify_result = verify_with_threshold(pos_sims, neg_sims, recommended)
    print_verify_result(verify_result)

    # ── 导出 JSON 报告 ──
    report = CalibrationReport(
        timestamp=time.strftime('%Y-%m-%dT%H:%M:%S'),
        embedding_model="BAAI/bge-m3",
        total_positives=len(positives),
        total_negatives=len(negatives),
        prototype_categories=proto_categories,
        positive_stats=pos_stats,
        negative_stats=neg_stats,
        recommended_threshold=recommended,
        has_overlap=has_overlap,
        conflict_skills=conflicts,
        verify_result=verify_result,
        sample_sims=[
            {"case_id": s.case_id, "query": s.query,
             "max_sim": s.max_sim, "matched_category": s.matched_category,
             "is_positive": s.is_positive}
            for s in (pos_sims + neg_sims)
        ],
    )

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as f:
            json.dump(report.__dict__, f, ensure_ascii=False, indent=2,
                      default=lambda o: o.__dict__ if hasattr(o, "__dict__") else str(o))
        print(f"\n  📄 JSON 报告已导出: {output_path}")

    # ── 退出码判定 ──
    print(f"\n{'='*70}")
    if verify_result["passed"]:
        print(f"  ✅ 校准成功")
        print(f"     推荐阈值: {recommended:.4f}")
        print(f"     正样本 0 误伤（守【不易】）")
        print(f"     负样本覆盖率: {verify_result['coverage_rate']:.2%}")
        if conflicts:
            print(f"  ⚠️  有 {len(conflicts)} 个 prototype 冲突需处理")
        print(f"\n  💡 配置方式（写入 .env）:")
        print(f"     SKILL_NEGATIVE_INTENT_THRESHOLD={recommended:.4f}")
        return 0
    else:
        print(f"  ❌ 校准失败: 推荐阈值导致正样本误伤（违【不易】）")
        print(f"     误伤数: {verify_result['positive_false_rejected']}")
        print(f"     建议措施:")
        print(f"     1. 提高 threshold 后重新校准（--threshold 0.80）")
        print(f"     2. 移除冲突的 prototype 样本")
        print(f"     3. 回退到路径 A（扩充正则规则）")
        return 2


# ════════════════════════════════════════════════════════════
#  CLI 入口
# ════════════════════════════════════════════════════════════

def main() -> int:
    parser = argparse.ArgumentParser(
        description="v6.2 阈值校准脚本 — BGE-m3 相似度分布分析"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="仅检查样本集结构，不加载 BGE-m3 模型（秒级）"
    )
    parser.add_argument(
        "--threshold", type=float, default=None,
        help="指定阈值进行验证（不自动推荐）"
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="JSON 报告输出路径（如 scripts/output/v62_calibration.json）"
    )
    parser.add_argument(
        "--conflict-threshold", type=float, default=0.5,
        help="prototype 与技能 description 冲突阈值（默认 0.5）"
    )
    args = parser.parse_args()

    if args.dry_run:
        return dry_run_check()

    output_path = Path(args.output) if args.output else None
    return run_calibration(
        threshold_override=args.threshold,
        output_path=output_path,
        conflict_threshold=args.conflict_threshold,
    )


if __name__ == "__main__":
    sys.exit(main())
