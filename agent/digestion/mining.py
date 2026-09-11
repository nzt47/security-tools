"""模式挖掘：LCS 步骤骨架 + 决策树分支条件（TASK-S3-01 步骤 3 / v7.2 §4.5 双路挖掘）

**第一路 — LCS（步骤序列最长公共子序列）**

- `pairwise_lcs()`：两条序列的**精确** LCS（标准 DP，确定性）；
- `medoid_index()` / `consensus_backbone()`：先在同类轨迹中取"与其余轨迹 LCS 相似度
  之和最大"的一条作**中心轨迹**（medoid；同分按序列内容字典序，与输入顺序无关），
  再按"标签在同类轨迹中的**出现支撑率**"筛出骨架步骤并保持中心轨迹的相对顺序。

  为什么用"中心轨迹 + 支撑率"而不是"多序列 LCS"：多序列 LCS 是 NP-hard，任何
  近似实现都会让"骨架"随输入顺序漂移，违反本任务的**确定性**要求。中心轨迹法
  的结果只依赖轨迹集合（集合语义），可复现、可解释，且支撑率本身就是要写进
  验收报告的证据。

**第二路 — 决策树（分支条件提取）**

`extract_branches()` 在**同一 capability + 同一意图**的成功/失败两组轨迹上比较：
成功 vs 失败的分化特征（某可选步骤的有无、步数档位）才是可执行的分支条件。
树由 `build_decision_tree()`（Gini 不纯度，深度/叶子样本数受限，特征名确定性
排序）构建，只有**叶子纯净**且样本量达标的路径才转为 `BranchCondition`
—— 不确定的分支宁可不出（宁可冗余不可误合）。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .generalize import normalize_param_value
from .models import (
    BranchCondition,
    OUTCOME_FAILURE,
    OUTCOME_SUCCESS,
    PatternStep,
    TREE_MAX_DEPTH,
    TREE_MIN_SAMPLES,
    MIN_STEP_SUPPORT,
)


# ════════════════════════════════════════════════════════════
#  第一路：LCS
# ════════════════════════════════════════════════════════════


def pairwise_lcs(a: Sequence[str], b: Sequence[str]) -> List[str]:
    """两条序列的精确最长公共子序列（经典 DP，O(n·m)，返回序列本身）"""
    n, m = len(a), len(b)
    if n == 0 or m == 0:
        return []
    # dp[i][j] = a[i:] 与 b[j:] 的 LCS 长度（从后往前填，便于还原时字典序更稳）
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            if a[i] == b[j]:
                dp[i][j] = dp[i + 1][j + 1] + 1
            else:
                dp[i][j] = max(dp[i + 1][j], dp[i][j + 1])
    out: List[str] = []
    i = j = 0
    while i < n and j < m:
        if a[i] == b[j]:
            out.append(a[i])
            i += 1
            j += 1
        elif dp[i + 1][j] >= dp[i][j + 1]:
            i += 1
        else:
            j += 1
    return out


def lcs_similarity(a: Sequence[str], b: Sequence[str]) -> float:
    """LCS 相似度（Dice 式：``2·|LCS| / (|a|+|b|)``），两空序列视为 1.0"""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return 2.0 * len(pairwise_lcs(a, b)) / (len(a) + len(b))


def medoid_index(sequences: Sequence[Sequence[str]]) -> int:
    """中心轨迹下标（与其余序列 LCS 相似度之和最大；同分按内容字典序，确定性）"""
    if not sequences:
        raise ValueError("medoid_index: 序列集合为空")
    best_idx = 0
    best_key: Optional[Tuple[float, Tuple[str, ...]]] = None
    for i, seq in enumerate(sequences):
        score = sum(lcs_similarity(seq, other) for j, other in enumerate(sequences)
                    if j != i)
        key = (round(score, 9), tuple(seq))
        if best_key is None or key > best_key:
            best_key = key
            best_idx = i
    return best_idx


def label_support(sequences: Sequence[Sequence[str]]) -> Dict[str, float]:
    """每个标签的**出现支撑率** = 含该标签的轨迹数 / 轨迹总数"""
    total = len(sequences)
    if total == 0:
        return {}
    hits: Dict[str, int] = {}
    for seq in sequences:
        for label in set(seq):
            hits[label] = hits.get(label, 0) + 1
    return {label: round(count / total, 4) for label, count in hits.items()}


def is_subsequence(pattern: Sequence[str], seq: Sequence[str]) -> bool:
    """`pattern` 是否为 `seq` 的（不必连续的）子序列"""
    it = iter(seq)
    return all(any(item == token for item in it) for token in pattern)


def consensus_backbone(
    sequences: Sequence[Sequence[str]],
    *,
    min_support: float = MIN_STEP_SUPPORT,
) -> Tuple[List[Tuple[str, float, int]], int]:
    """同类轨迹 → 步骤骨架（顺序取中心轨迹，支撑率过滤）

    Returns:
        ([(标签, 支撑率, 出现次数), ...], 中心轨迹下标)
    """
    seqs = [list(s) for s in sequences]
    if not seqs:
        return [], -1
    medoid = medoid_index(seqs)
    support = label_support(seqs)
    counts: Dict[str, int] = {}
    for seq in seqs:
        for label in set(seq):
            counts[label] = counts.get(label, 0) + 1
    backbone: List[Tuple[str, float, int]] = []
    seen = set()
    for label in seqs[medoid]:
        if label in seen:
            continue
        ratio = support.get(label, 0.0)
        if ratio < min_support:
            continue
        seen.add(label)
        backbone.append((label, ratio, counts.get(label, 0)))
    return backbone, medoid


def backbone_coverage(backbone: Sequence[str],
                      sequences: Sequence[Sequence[str]]) -> float:
    """骨架对同类轨迹的覆盖率（真子序列判定的比例，非"标签出现"近似）"""
    if not sequences:
        return 0.0
    hit = sum(1 for seq in sequences if is_subsequence(backbone, seq))
    return round(hit / len(sequences), 4)


def optional_labels(backbone: Sequence[str],
                    sequences: Sequence[Sequence[str]],
                    *,
                    min_support: float = 0.1) -> List[str]:
    """非骨架但在部分轨迹中出现的标签（决策树的可选特征；确定性排序）"""
    support = label_support(sequences)
    return sorted(label for label, ratio in support.items()
                  if label not in set(backbone) and ratio >= min_support)


# ════════════════════════════════════════════════════════════
#  第二路：决策树
# ════════════════════════════════════════════════════════════


class DecisionNode:
    """极简决策树节点（bool 特征用于 `has:<label>`；数值特征用于 `steps`）

    ``decision`` = "leaf" | "bool" | "number"
    """

    __slots__ = ("decision", "feature", "threshold", "left", "right", "label",
                 "samples", "positives")

    def __init__(self, *, decision: str, feature: str = "", threshold: float = 0.0,
                 left: Optional["DecisionNode"] = None,
                 right: Optional["DecisionNode"] = None,
                 label: str = "", samples: int = 0, positives: int = 0) -> None:
        self.decision = decision
        self.feature = feature
        self.threshold = threshold
        self.left = left
        self.right = right
        self.label = label
        self.samples = samples
        self.positives = positives

    def as_dict(self) -> Dict[str, Any]:
        base: Dict[str, Any] = {"decision": self.decision, "samples": self.samples,
                                "positives": self.positives}
        if self.decision == "leaf":
            base["label"] = self.label
        else:
            base.update({
                "feature": self.feature,
                "threshold": self.threshold,
                "left": self.left.as_dict() if self.left else None,
                "right": self.right.as_dict() if self.right else None,
            })
        return base


def _gini(rows: Sequence[Mapping[str, Any]]) -> float:
    """不纯度（以 ``__label`` 是否 success 计）"""
    if not rows:
        return 0.0
    pos = sum(1 for r in rows if r.get("__label") == OUTCOME_SUCCESS)
    p = pos / len(rows)
    return 1.0 - p * p - (1 - p) ** 2


def _majority(rows: Sequence[Mapping[str, Any]]) -> str:
    """叶子标签：多数票；平票时取**字典序在前**者（确定性，不偏向任何结果）"""
    pos = sum(1 for r in rows if r.get("__label") == OUTCOME_SUCCESS)
    neg = len(rows) - pos
    if pos > neg:
        return OUTCOME_SUCCESS
    if neg > pos:
        return OUTCOME_FAILURE
    return sorted({str(r.get("__label")) for r in rows})[0]


def build_decision_tree(
    rows: Sequence[Mapping[str, Any]],
    *,
    features: Sequence[str],
    max_depth: int = TREE_MAX_DEPTH,
    min_samples: int = TREE_MIN_SAMPLES,
    depth: int = 0,
) -> DecisionNode:
    """在样本上构建**确定性**决策树（bool 特征 + 数值特征 ``steps``）

    - 分裂准则：Gini 不纯度下降最大；同分按特征名字典序（确定性，与输入顺序无关）；
    - 停止条件：深度达上限 / 样本数不足 / 已是纯叶 / 无有效分裂。
    """
    node = DecisionNode(decision="leaf", label=_majority(rows),
                        samples=len(rows),
                        positives=sum(1 for r in rows
                                      if r.get("__label") == OUTCOME_SUCCESS))
    if depth >= max_depth or len(rows) < min_samples:
        return node
    base = _gini(rows)
    if base <= 0.0:
        return node

    best: Optional[Tuple[float, str, str, float]] = None
    for name in sorted(features):
        values = {r.get(name, 0.0) for r in rows}
        if name == "steps" or len(values) > 2:
            nums = sorted({float(r.get(name, 0.0)) for r in rows})
            if len(nums) < 2:
                continue
            candidates = [(name, "number", nums[len(nums) // 2])]
        elif len(values) >= 2:
            candidates = [(name, "bool", 0.5)]
        else:
            continue
        for feat, kind, threshold in candidates:
            if kind == "bool":
                left = [r for r in rows if float(r.get(feat, 0.0)) > threshold]
                right = [r for r in rows if float(r.get(feat, 0.0)) <= threshold]
            else:
                left = [r for r in rows if float(r.get(feat, 0.0)) < threshold]
                right = [r for r in rows if float(r.get(feat, 0.0)) >= threshold]
            if not left or not right:
                continue
            weighted = (len(left) * _gini(left) + len(right) * _gini(right)) / len(rows)
            gain = base - weighted
            if gain <= 0:
                continue
            key = (-round(gain, 9), feat, kind, threshold)
            if best is None or key < best:
                best = key
    if best is None:
        return node

    _, feat, kind, threshold = best
    if kind == "bool":
        left = [r for r in rows if float(r.get(feat, 0.0)) > threshold]
        right = [r for r in rows if float(r.get(feat, 0.0)) <= threshold]
    else:
        left = [r for r in rows if float(r.get(feat, 0.0)) < threshold]
        right = [r for r in rows if float(r.get(feat, 0.0)) >= threshold]
    return DecisionNode(
        decision=kind, feature=feat, threshold=threshold,
        left=build_decision_tree(left, features=features, max_depth=max_depth,
                                 min_samples=min_samples, depth=depth + 1),
        right=build_decision_tree(right, features=features, max_depth=max_depth,
                                  min_samples=min_samples, depth=depth + 1),
        samples=len(rows),
        positives=sum(1 for r in rows if r.get("__label") == OUTCOME_SUCCESS))


def _condition_text(kind: str, feature: str, threshold: float,
                    positive_branch: bool) -> str:
    """节点 → 人类可读条件（与 `build_decision_tree` 的分裂方向**逐字对应**）

    - 布尔分裂（``has:<label>``）：左支 = 特征 > 0.5 = **出现**；右支 = **缺失**
    - 数值分裂（``steps``）：左支 = ``< threshold``；右支 = ``>= threshold``
    """
    if feature.startswith("has:"):
        label = feature[len("has:"):]
        return f"步骤 `{label}` {'出现' if positive_branch else '缺失'}"
    op = "<" if positive_branch else ">="
    return f"步数 {op} {int(threshold)}"


def _walk(node: DecisionNode, path: List[str], at_step: Dict[str, int],
          total: int, out: List[BranchCondition]) -> None:
    """遍历树，收集**纯叶**路径分支条件"""
    if node.decision == "leaf":
        if not path:
            return
        outcome = node.label
        support = (node.positives if outcome == OUTCOME_SUCCESS
                   else node.samples - node.positives)
        if support < TREE_MIN_SAMPLES:
            return
        # 位次：路径任一元素提到的骨架标签中，取最靠前的位置
        at = -1
        for label, idx in at_step.items():
            if any(f"`{label}`" in part for part in path):
                at = idx if at < 0 else min(at, idx)
        out.append(BranchCondition(
            at_step=at,
            condition=" 且 ".join(path),
            support=support,
            total=total,
            outcome=outcome,
            advice=("满足该条件时保持骨架步骤并按成功路径执行"
                    if outcome == OUTCOME_SUCCESS
                    else "满足该条件时历史多为失败 —— 生成草稿需附失败规避说明"
                         "（不得据此自动发布）"),
        ))
        return
    if node.left is not None:
        _walk(node.left, path + [_condition_text(node.decision, node.feature,
                                                 node.threshold, True)],
              at_step, total, out)
    if node.right is not None:
        _walk(node.right, path + [_condition_text(node.decision, node.feature,
                                                  node.threshold, False)],
              at_step, total, out)


def extract_branches(
    success_sequences: Sequence[Sequence[str]],
    failure_sequences: Sequence[Sequence[str]],
    backbone: Sequence[str],
    *,
    gap: float = 0.3,
) -> List[BranchCondition]:
    """分支条件提取（成功组 vs 失败组）

    两个互补来源，合并后按 (位次, 条件) 确定性排序：

    1. **单特征分化**：某可选步骤在成功组的出现率 − 在失败组的出现率 ≥ ``gap``
       （或反向 ≤ ``-gap``）→ 一条分支条件。无失败样本时退化为"该步骤在成功组
       高频出现"的**建议**（``outcome=success``，但只有在出现率 ≥0.8 且两组都有
       样本可比较时才标注为分化，避免把"样本里都有"当成分化证据）。
    2. **决策树纯叶路径**：在成功/失败合并样本上建树，只有纯净叶（且样本量达标）
       的路径转为条件 —— 多特征组合条件。
    """
    successes = [list(s) for s in success_sequences]
    failures = [list(s) for s in failure_sequences]
    total = len(successes) + len(failures)
    at_step: Dict[str, int] = {label: i + 1 for i, label in enumerate(backbone)}
    conditions: List[BranchCondition] = []

    # 候选特征标签：**骨架步骤（成功侧支撑高、失败侧可能缺失）+ 骨架外可选步骤**
    # 二者都必须参与分化判定 —— 只取"骨架外"会漏掉最典型的失败信号
    # （"骨干步骤缺失 ⇒ 任务失败"），那是实现期实测到的真实盲点。
    all_seqs = successes + failures
    candidates_all = sorted(
        set(backbone) | set(optional_labels(backbone, all_seqs, min_support=0.1)))
    discriminative = [label for label in candidates_all
                      if 0 < sum(1 for s in all_seqs if label in s) < len(all_seqs)]

    # ── 来源 1：单特征分化 ──
    if successes and failures:
        for label in discriminative:
            p_ok = sum(1 for s in successes if label in s) / len(successes)
            p_ng = sum(1 for s in failures if label in s) / len(failures)
            delta = p_ok - p_ng
            if abs(delta) < gap:
                continue
            present = delta > 0
            conditions.append(BranchCondition(
                at_step=at_step.get(label, -1),
                condition=f"步骤 `{label}` {'出现' if present else '缺失'}",
                support=(sum(1 for s in successes if label in s) if present
                         else sum(1 for s in failures if label not in s)),
                total=total,
                outcome=OUTCOME_SUCCESS if present else OUTCOME_FAILURE,
                advice=(f"成功组出现率 {p_ok:.0%} vs 失败组 {p_ng:.0%}"
                        f"（Δ={delta:+.0%}）"),
            ))

    # ── 来源 2：决策树纯叶路径 ──
    if successes and failures and discriminative:
        features = [f"has:{label}" for label in discriminative] + ["steps"]
        rows: List[Dict[str, Any]] = []
        for seq in successes:
            row: Dict[str, Any] = {"__label": OUTCOME_SUCCESS,
                                   "steps": float(len(seq))}
            for label in discriminative:
                row[f"has:{label}"] = 1.0 if label in seq else 0.0
            rows.append(row)
        for seq in failures:
            fail_row: Dict[str, Any] = {"__label": OUTCOME_FAILURE,
                                       "steps": float(len(seq))}
            for label in discriminative:
                fail_row[f"has:{label}"] = 1.0 if label in seq else 0.0
            rows.append(fail_row)
        tree = build_decision_tree(rows, features=features)
        walked: List[BranchCondition] = []
        _walk(tree, [], at_step, total, walked)
        conditions.extend(walked)

    deduped: Dict[str, BranchCondition] = {}
    for cond in conditions:
        if cond.condition not in deduped:
            deduped[cond.condition] = cond
    return sorted(deduped.values(), key=lambda c: (c.at_step, c.condition))


# ════════════════════════════════════════════════════════════
#  骨架 → PatternStep / 副作用画像
# ════════════════════════════════════════════════════════════


def backbone_to_steps(
    backbone: Sequence[Tuple[str, float, int]],
    *,
    capability_of: Optional[Dict[str, str]] = None,
    condition_of: Optional[Dict[str, str]] = None,
    optional: Sequence[str] = (),
) -> List[PatternStep]:
    """骨架元组 → `PatternStep` 列表（``optional`` 内的标签标记为可选步骤）"""
    caps = capability_of or {}
    conds = condition_of or {}
    optional_set = set(optional)
    steps: List[PatternStep] = []
    for i, (label, ratio, count) in enumerate(backbone):
        steps.append(PatternStep(
            seq=i + 1,
            label=label,
            support=ratio,
            optional=label in optional_set,
            capability_id=caps.get(label, ""),
            condition=conds.get(label, ""),
            samples=count,
        ))
    return steps


def side_effect_profile(trajectories: Sequence[Any]) -> Dict[str, Any]:
    """副作用画像（mirrored 的验收物之一）：写/删文件与外部调用的**形态画像**

    只输出**形态归一后**的路径/端点（``${path}`` 等），不输出任何具体文件路径或
    端点原文 —— 与统一 Trace 的载荷纪律一致（只放标识/计数，不放原文）。
    """
    written: Dict[str, int] = {}
    deleted: Dict[str, int] = {}
    external: Dict[str, int] = {}
    per_traj_writes: List[int] = []
    for traj in trajectories:
        writes = 0
        for step in getattr(traj, "steps", []):
            for path in getattr(step, "files_written", []) or []:
                key = str(normalize_param_value(path))
                written[key] = written.get(key, 0) + 1
                writes += 1
            for path in getattr(step, "files_deleted", []) or []:
                key = str(normalize_param_value(path))
                deleted[key] = deleted.get(key, 0) + 1
            for call in getattr(step, "external_calls", []) or []:
                key = str(normalize_param_value(call))
                external[key] = external.get(key, 0) + 1
        per_traj_writes.append(writes)
    return {
        "files_written_shape": sorted(written),
        "files_written_count": sum(written.values()),
        "files_deleted_shape": sorted(deleted),
        "files_deleted_count": sum(deleted.values()),
        "external_calls_shape": sorted(external),
        "external_calls_count": sum(external.values()),
        "writes_per_trajectory_avg": (
            round(sum(per_traj_writes) / len(per_traj_writes), 4)
            if per_traj_writes else 0.0),
        "destructive": bool(deleted),
        "external_endpoint": bool(external),
        "trajectory_count": len(trajectories),
        "undo_hint": ("含删除类副作用：回滚需依赖备份/快照（S1-02 governance.undo_hint）"
                      if deleted else
                      "仅写类副作用：回滚可按 files_written 清单逆序恢复或忽略"),
    }


__all__ = [
    "pairwise_lcs", "lcs_similarity", "medoid_index", "label_support",
    "is_subsequence", "consensus_backbone", "backbone_coverage",
    "optional_labels",
    "DecisionNode", "build_decision_tree", "extract_branches",
    "backbone_to_steps", "side_effect_profile",
]
