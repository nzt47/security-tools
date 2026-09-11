"""轨迹清洗与同类判定（TASK-S3-01 步骤 2 / v7.2 T3 修正落地）

任务书 §步骤 2 的四条清洗规则，逐条对应本模块的一个函数：

| 规则 | 实现 | 标记 |
|---|---|---|
| 剔除失败重试/探索前缀 | `strip_noise_prefix()` | `NOISE_RETRY_PREFIX` / `NOISE_EXPLORE_PREFIX` |
| 合并同类重复步骤 | `merge_duplicate_steps()` | `NOISE_DUPLICATE_STEP` |
| 失败轨迹保留并标注（负样本） | `classify_outcome()` + `mark_negative()` | `NOISE_NEGATIVE_SAMPLE` |
| 归一参数化（路径/时间戳/随机值→占位符） | `generalize.normalize_param_value()` | — |

**同类轨迹判定键** (`models.SameTaskKey`) 的三元组与归一规则见
`intent_key_for_trace()` / `arg_shape_signature()` / `normalize_intent()` 的文档。

守【不易】：清洗**绝不丢弃**轨迹——最坏情形（前缀规则会把轨迹掏空）下退回原序列并
只打标记，保证"清洗后计数"永远 ≥1，且 `raw_step_count`/`dropped_steps` 留住差异供
验收对账。
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .generalize import generalize_step_params, is_placeholder
from .models import (
    NOISE_DUPLICATE_STEP,
    NOISE_EXPLORE_PREFIX,
    NOISE_NEGATIVE_SAMPLE,
    NOISE_RETRY_PREFIX,
    OUTCOME_FAILURE,
    OUTCOME_SUCCESS,
    SameTaskKey,
    Trajectory,
    TrajectoryStep,
    TraceSet,
)

# ════════════════════════════════════════════════════════════
#  标签与噪声词表
# ════════════════════════════════════════════════════════════

#: 探索/试探性动作（仅在**前导段**出现时才算噪声前缀）
_EXPLORE_TOKENS = (
    "explore", "probe", "inspect", "survey", "peek", "trial", "try_",
    "list_dir", "listdir", "ls_", "scan", "glob", "search", "grep", "find_",
    "debug", "diagnose", "check_env", "env_check", "whoami", "pwd",
    "探索", "探测", "试跑", "排查", "巡检",
)
#: 显式重试动作
_RETRY_TOKENS = ("retry", "retries", "backoff", "reattempt", "重试", "再次",
                 "fallback_retry")
#: 意图文本归一时的停用词（英文整词 + 中文单字虚词）
_STOPWORDS = frozenset({
    "a", "an", "the", "of", "to", "for", "and", "or", "in", "on", "at", "by",
    "with", "please", "help", "me", "my", "i", "you", "it", "this", "that",
    "is", "are", "be", "do", "does", "can", "could", "would", "should",
})
#: 中文单字虚词（中文无空格分词 → 按字切分后剔除虚词；CJK 未做词干化，
#: 这是"确定性优先"的取舍：宁可同义不合并，也不引入不确定的分词依赖）
_CJK_STOPCHARS = frozenset(
    "请帮我你的了和与在对给把是在有能否可以然后并且里中上下个这那都也就很"
    "会要需将从向为以及就想让被把"
)
#: 意图归一里需要抹掉的"具体值形态"（避免同任务不同路径被拆成不同键）
_INTENT_VALUE_RES = (
    re.compile(r"[a-zA-Z]:[\\/][^\s]+"),          # Windows 绝对路径
    re.compile(r"\\\\[^\s]+"),                     # UNC
    re.compile(r"/(?:[^\s/]+/)+[^\s/]*"),          # POSIX 绝对路径
    re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
               r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"),  # UUID
    re.compile(r"\b\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2})?)?\b"),  # 时间戳
    re.compile(r"\b\d+\b"),                        # 数字
)
#: ASCII 词 + 单字 CJK 双路切分（中文不引入分词依赖，按字切分）
_TOKEN_RE = re.compile(r"[0-9A-Za-z]+|[\u4e00-\u9fff]")


def is_explore_label(label: str) -> bool:
    """标签是否属探索/试探性动作"""
    low = str(label or "").strip().lower()
    return bool(low) and any(tok in low for tok in _EXPLORE_TOKENS)


def is_retry_label(label: str) -> bool:
    """标签是否属显式重试动作"""
    low = str(label or "").strip().lower()
    return bool(low) and any(tok in low for tok in _RETRY_TOKENS)


# ════════════════════════════════════════════════════════════
#  同类判定键：intent 归一
# ════════════════════════════════════════════════════════════


def normalize_intent(text: Any) -> str:
    """任务意图文本 → **归一意图键**（确定性、对称、大小写/顺序/取值无关）

    步骤：NFKC 规范化 → 小写 → 抹掉具体值形态（路径/UUID/时间戳/数字）→
    切词（ASCII 整词 + **CJK 按字**）→ 去停用词与无效单字 →
    **去重并排序** → ``+`` 连接。

    两个"无关性"保证（任务书单测所要求）：

    - **顺序无关**：排序后连接 ⇒ 同义不同语序归一到同一键；
    - **取值无关**：路径/时刻/编号先被抹掉 ⇒ 同任务换文件、换时刻仍同键
      （"同任务不同路径的归一并集判定"）。

    中文按**字**切分而不引入分词依赖：宁可同义词不合并（宁可冗余不误合），
    也不让归一结果依赖外部词典版本。
    """
    raw = unicodedata.normalize("NFKC", str(text or "")).strip().lower()
    if not raw:
        return ""
    for pattern in _INTENT_VALUE_RES:
        raw = pattern.sub(" ", raw)
    tokens = _TOKEN_RE.findall(raw)
    kept = set()
    for token in tokens:
        if token in _STOPWORDS:
            continue
        if token in _CJK_STOPCHARS:
            continue
        if len(token) == 1 and token.isascii() and not token.isdigit():
            continue
        kept.add(token)
    return "+".join(sorted(kept))


def arg_shape_signature(args: Any, *, depth: int = 1) -> str:
    """请求参数 → **形态指纹**（只取键结构，不取用户文本值）

    这是默认的意图信号：同一类操作的参数键集合稳定（``read_file`` 恒为
    ``path``），而**取值**（具体文件、时刻、随机 id）不进指纹 —— 既满足
    "同任务不同路径可归并"，也守 S2 的载荷纪律（不放大原文）。
    """
    if args is None:
        return "none"
    if not isinstance(args, dict):
        return type(args).__name__
    if not args:
        return "empty"
    parts: List[str] = []
    for key in sorted(str(k) for k in args):
        parts.append(key)
        if depth > 0:
            value = args.get(key)
            if isinstance(value, dict) and value:
                nested = ",".join(sorted(str(k) for k in value))
                parts[-1] = f"{key}({nested})"
    return "shape:" + "+".join(parts)


def intent_key_for_trace(trace: Any, *, intent: str = "",
                         shape_depth: int = 1) -> str:
    """轨迹 → ``intent_key``（判定键第二元；优先级明确，无歧义）

    解析顺序：

    1. 显式 ``intent`` 实参（调用方给出任务意图文本）→ `normalize_intent()`；
    2. Trace 自身 ``side_effects.notes`` 中的 ``intent:<文本>`` 标注（预留通道）；
    3. 请求参数的**形态指纹** `arg_shape_signature()`（默认口径）；
    4. ``"unknown"``（无任何信号时如实标注，不臆造）。
    """
    if intent:
        key = normalize_intent(intent)
        if key:
            return key
    notes = getattr(getattr(trace, "side_effects", None), "notes", None) or []
    for note in notes:
        text = str(note or "")
        if text.startswith("intent:"):
            key = normalize_intent(text.split(":", 1)[1])
            if key:
                return key
    shape = arg_shape_signature(getattr(getattr(trace, "request", None),
                                        "args_redacted", None), depth=shape_depth)
    return shape or "unknown"


def classify_outcome(status: Any) -> str:
    """Trace 结果状态 → 归一 outcome（非 ``success`` 一律计 ``failure``）"""
    return OUTCOME_SUCCESS if str(status or "") == "success" else OUTCOME_FAILURE


def same_task_key(trace: Any, *, capability_id: str = "", intent: str = "",
                  shape_depth: int = 1) -> SameTaskKey:
    """一条能力级 Trace → 同类判定键三元组"""
    cid = str(capability_id or getattr(trace, "capability_id", "") or "")
    return SameTaskKey(
        capability_id=cid,
        intent_key=intent_key_for_trace(trace, intent=intent,
                                        shape_depth=shape_depth),
        outcome=classify_outcome(getattr(getattr(trace, "response", None),
                                         "status", "")),
    )


# ════════════════════════════════════════════════════════════
#  清洗规则
# ════════════════════════════════════════════════════════════


def _step_from_trace(row: Any, seq: int, *, default_label: str = "") -> TrajectoryStep:
    """能力级 Trace → 轨迹步（参数经形态归一）"""
    from agent.descriptors.bridge import resolve_capability_id  # 局部导入避环

    raw_name = str(getattr(row, "capability_id", "") or "")
    try:
        resolved = resolve_capability_id(raw_name)
        cid = resolved["capability_id"] or raw_name
    except Exception:  # noqa: BLE001  台账不可用 → 保留原文（不丢步）
        cid = raw_name
    label = default_label or _label_of(cid or raw_name)
    side = getattr(row, "side_effects", None)
    timing = getattr(row, "timing", None)
    response = getattr(row, "response", None)
    return TrajectoryStep(
        seq=seq,
        label=label,
        capability_id=cid,
        trace_id=str(getattr(row, "trace_id", "") or ""),
        params=generalize_step_params(
            getattr(getattr(row, "request", None), "args_redacted", None)),
        status=str(getattr(response, "status", "") or ""),
        error_code=str(getattr(response, "error_code", "") or ""),
        duration_ms=float(getattr(timing, "duration_ms", 0.0) or 0.0),
        files_written=[str(p) for p in (getattr(side, "files_written", None) or [])],
        files_deleted=[str(p) for p in (getattr(side, "files_deleted", None) or [])],
        external_calls=[str(p) for p in (getattr(side, "external_calls", None) or [])],
    )


def _label_of(capability_id: str) -> str:
    """capability_id → 归一动作标签（取末段；空则如实标注 unknown）"""
    text = str(capability_id or "").strip()
    if not text:
        return "unknown"
    return text.rsplit(".", 1)[-1] or text


def strip_noise_prefix(steps: List[TrajectoryStep], *,
                       min_keep: int = 2) -> Tuple[List[TrajectoryStep], List[str]]:
    """剔除**前导**失败重试/探索段（只削前缀，不动中后段——中段探索是真实步骤）

    判定：从头部连续命中 (a) 结果为失败/阻断，或 (b) 标签属探索/重试 的步骤。

    **保护**：若剔除后剩余步数 < ``min_keep``，则**不剔除**（原序列返回）并只报
    标记 —— 清洗绝不把轨迹掏空（守【不易】）。
    """
    flags: List[str] = []
    cut = 0
    for step in steps:
        failed = step.status in ("error", "blocked")
        explore = is_explore_label(step.label)
        retry = is_retry_label(step.label)
        if failed or explore or retry:
            if retry:
                if NOISE_RETRY_PREFIX not in flags:
                    flags.append(NOISE_RETRY_PREFIX)
            if explore and not retry:
                if NOISE_EXPLORE_PREFIX not in flags:
                    flags.append(NOISE_EXPLORE_PREFIX)
            cut += 1
            continue
        break
    if cut == 0:
        return steps, flags
    if len(steps) - cut < min_keep:
        # 削完太短 → 保留原序列（宁冗余不误删），标记仍然如实上报
        return steps, flags
    return steps[cut:], flags


def merge_duplicate_steps(steps: List[TrajectoryStep], *,
                          keep_repeat: bool = True) -> Tuple[List[TrajectoryStep], int]:
    """合并**相邻**同类重复步骤（重试/循环产生的重复）

    同标签 + 同参数（含形态归一后）视为重复；合并时保留首次出现的步，
    ``repeat`` 累加（默认保留计数，供"该步执行了几次"这一信息不丢失）。

    Returns:
        (合并后序列, 被合并掉的步数)
    """
    if not steps:
        return steps, 0
    out: List[TrajectoryStep] = []
    merged = 0
    for step in steps:
        if out:
            prev = out[-1]
            if prev.label == step.label and prev.params == step.params:
                prev.repeat = prev.repeat + (step.repeat if keep_repeat else 0)
                merged += 1
                continue
        out.append(step)
    for i, step in enumerate(out):
        step.seq = i + 1
    return out, merged


def mark_negative(traj: Trajectory) -> None:
    """失败轨迹标注为**负样本**（保留，不参与成功骨架，供 S5 评测/negative_intent）"""
    if traj.is_negative:
        traj.flag(NOISE_NEGATIVE_SAMPLE)


def clean_trajectory(steps: List[TrajectoryStep], *,
                     min_keep: int = 2) -> Tuple[List[TrajectoryStep], Dict[str, Any]]:
    """四规则串联清洗一个步骤序列（纯函数，便于单测与复用）

    Returns:
        (清洗后的步骤序列, 清洗统计 {raw, after_prefix, after_merge, dropped,
         merged, flags})
    """
    raw = len(steps)
    flags: List[str] = []
    after_prefix, prefix_flags = strip_noise_prefix(steps, min_keep=min_keep)
    flags.extend(prefix_flags)
    after_merge, merged = merge_duplicate_steps(after_prefix)
    if merged:
        flags.append(NOISE_DUPLICATE_STEP)
    for i, step in enumerate(after_merge):
        step.seq = i + 1
    stats = {
        "raw": raw,
        "after_prefix": len(after_prefix),
        "after_merge": len(after_merge),
        "dropped": raw - len(after_merge),
        "merged": merged,
        "flags": list(flags),
    }
    return after_merge, stats


def trajectory_from_rows(
    rows: Sequence[Any],
    *,
    key: SameTaskKey,
    source_trace_id: str = "",
    task_id: str = "",
    workspace_id: str = "",
    started_at: float = 0.0,
    min_keep: int = 2,
    default_labels: Optional[Sequence[str]] = None,
) -> Trajectory:
    """一组能力级 Trace 行（同一任务，按 ``started_at`` 升序）→ 清洗后的轨迹

    轨迹身份取 ``task_id``（缺则退 `source_trace_id`）；``key`` 由调用方给出
    （观测单元的键），本函数只负责"把行变成清洗后的步序列"。
    """
    ordered = list(rows)
    steps: List[TrajectoryStep] = []
    for i, row in enumerate(ordered):
        label = ""
        if default_labels is not None and i < len(default_labels):
            label = str(default_labels[i] or "")
        steps.append(_step_from_trace(row, i + 1, default_label=label))
    cleaned, stats = clean_trajectory(steps, min_keep=min_keep)
    traj = Trajectory(
        trajectory_id=str(task_id or source_trace_id or "unknown"),
        task_id=str(task_id or ""),
        key=key,
        steps=cleaned,
        source_trace_id=str(source_trace_id or ""),
        raw_step_count=stats["raw"],
        dropped_steps=stats["dropped"],
        merged_steps=stats["merged"],
        is_negative=key.is_negative,
        noise_flags=list(stats["flags"]),
        workspace_id=str(workspace_id or ""),
        started_at=float(started_at or 0.0),
    )
    if traj.is_negative:
        mark_negative(traj)
    return traj


def group_by_same_task(trajectories: Iterable[Trajectory]) -> Dict[str, TraceSet]:
    """轨迹按同类判定键分桶（返回 ``key.as_str() → TraceSet``，确定性顺序）"""
    buckets: Dict[str, TraceSet] = {}
    for traj in sorted(trajectories, key=lambda t: (t.trajectory_id,
                                                   t.source_trace_id)):
        bucket = buckets.get(traj.key.as_str())
        if bucket is None:
            bucket = TraceSet(key=traj.key)
            buckets[traj.key.as_str()] = bucket
        bucket.trajectories.append(traj)
    return buckets


def dedupe_by_task(trajectories: Iterable[Trajectory]) -> List[Trajectory]:
    """同一 ``task_id`` 只保留一条轨迹（多条命中行指向同一任务时去重）

    保留策略：优先保留步数最多的那条（信息量最大），同分按 ``source_trace_id``
    字典序 —— 确定性，与输入顺序无关。
    """
    best: Dict[str, Trajectory] = {}
    for traj in trajectories:
        ident = traj.trajectory_id
        cur = best.get(ident)
        if cur is None:
            best[ident] = traj
            continue
        if (traj.step_count, traj.source_trace_id) > (cur.step_count,
                                                     cur.source_trace_id):
            best[ident] = traj
    return [best[k] for k in sorted(best)]


__all__ = [
    "is_explore_label", "is_retry_label",
    "normalize_intent", "arg_shape_signature", "intent_key_for_trace",
    "classify_outcome", "same_task_key",
    "strip_noise_prefix", "merge_duplicate_steps", "mark_negative",
    "clean_trajectory", "trajectory_from_rows",
    "group_by_same_task", "dedupe_by_task",
]
