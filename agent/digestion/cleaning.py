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

# ════════════════════════════════════════════════════════════
#  结构维度（TASK-S8-05 / D3）
# ════════════════════════════════════════════════════════════
#
# 现症：CJK 按字切分 → 60 条不同的三步任务链得到**同一个** intent_key
# （``产+仅+公+写+出+…``），"同类轨迹"判定近乎失效。修复不推翻 S3-01 的
# 文本归一（它仍是辅助维度，且"顺序无关/取值无关"两条保证由它承担），而是
# 在其前**追加**结构原子：能力集合 + 步数档位。文本可有歧义，结构不会。

#: 结构原子版本（进键前缀：口径变更必须**可辨**，不与旧键静默混用）
STRUCTURAL_KEY_VERSION = "v2"

#: 步数档位边界（同任务重试/合并会让步数抖动 ±1，档位吸收抖动）
STEP_BUCKETS: Tuple[Tuple[int, str], ...] = (
    (0, "0"), (1, "1"), (2, "2"), (5, "3-5"), (10, "6-10"),
)
STEP_BUCKET_OVERFLOW = "11+"
STEP_BUCKET_UNKNOWN = "unknown"


def step_count_bucket(step_count: Any) -> str:
    """步数 → **档位**（0 / 1 / 2 / 3-5 / 6-10 / 11+；非法输入 → ``unknown``）

    用档位而非精确值：同一次任务的重试与重复步骤合并会让步数 ±1 抖动，
    按精确值分键会把同一任务拆成多个键（与"可归并"的初衷相反）。
    """
    try:
        count = int(step_count)
    except (TypeError, ValueError):
        return STEP_BUCKET_UNKNOWN
    if count < 0:
        return STEP_BUCKET_UNKNOWN
    for upper, label in STEP_BUCKETS:
        if count <= upper:
            return label
    return STEP_BUCKET_OVERFLOW


def capability_set_key(capabilities: Iterable[Any]) -> str:
    """能力集合 → 稳定摘要（去空、去重、排序、``+`` 连接；空 → ``none``）

    **只记身份不记顺序**：同一条链的并行/重排不改变集合（与"可归并"一致），
    而"读一步"与"读→执行→写"必然分属不同集合（D3 的核心区分度）。
    """
    values = sorted({str(c).strip() for c in (capabilities or []) if str(c).strip()})
    return "+".join(values) if values else "none"


def steps_capability_set(steps: Iterable[Any], *,
                         drop_noise: bool = True) -> str:
    """步骤序列 → 能力集合摘要（``capability_id`` 优先，缺则用 ``label``）

    与 `capability.canonical` 无关：本函数**不做**任何别名归一（那是 capability
    模块的职责），只把该序列已有的身份如实去重汇总 —— 不猜测、不改写。

    ``drop_noise=True``（默认）先削去**前导探索/重试段**（`strip_noise_prefix`）：
    任务的**形状**不该因为某次执行多探了一步而改变 —— 这正是"同任务成败两条轨迹
    必须同键"的前提（实测：不削前缀时负样本 3 → 0，"失败集配对→分支提取"静默失效）。
    中后段的探索步是真实步骤，**不削**（与 `strip_noise_prefix` 同纪律）。
    """
    rows = list(steps or [])
    if drop_noise:
        rows, _ = _coerce_and_strip(rows)
    out: List[str] = []
    for step in rows:
        if isinstance(step, dict):
            cid = step.get("capability_id") or step.get("label")
        else:
            cid = getattr(step, "capability_id", "") or getattr(step, "label", "")
        text = str(cid or "").strip()
        if text:
            out.append(text)
    return capability_set_key(out)


def task_step_count(steps: Iterable[Any], *, drop_noise: bool = True) -> int:
    """任务**步数**（与 `steps_capability_set` 同一口径：默认削前导噪声段）"""
    rows = list(steps or [])
    if drop_noise:
        rows, _ = _coerce_and_strip(rows)
    return len(rows)


def _coerce_and_strip(steps: Sequence[Any]) -> Tuple[List[TrajectoryStep], List[str]]:
    """任意步骤形态 → `TrajectoryStep` 列表后削前导噪声段（纯函数，不改入参）

    dict（未反序列化的用例存储形态）与对象两种形态都支持；缺字段的项按空值处理
    （**不丢步**：宁可形状略保守，也不静默改变步数）。
    """
    coerce: List[TrajectoryStep] = []
    for index, step in enumerate(steps or []):
        if isinstance(step, TrajectoryStep):
            coerce.append(step)
            continue
        if isinstance(step, dict):
            coerce.append(TrajectoryStep(
                seq=int(step.get("seq") or index + 1),
                label=str(step.get("label") or step.get("capability_id") or ""),
                capability_id=str(step.get("capability_id") or ""),
                status=str(step.get("status") or "")))
            continue
        coerce.append(TrajectoryStep(
            seq=index + 1,
            label=str(getattr(step, "label", "")
                      or getattr(step, "capability_id", "") or ""),
            capability_id=str(getattr(step, "capability_id", "") or ""),
            status=str(getattr(step, "status", "") or "")))
    return strip_noise_prefix(coerce)


def structural_atom(*, capability_set: str = "", step_count: Any = None,
                    outcome: str = "") -> str:
    """结构原子（``v2|cap=<集合>|steps=<档位>|out=<结果>``）—— 归组键的结构维度

    结果状态只在给出时入原子（``""`` = 调用方未提供 ⇒ 不主张，不伪报 success）。
    """
    parts = [STRUCTURAL_KEY_VERSION, f"cap={capability_set or 'none'}",
             f"steps:{step_count_bucket(step_count)}"]
    if outcome:
        parts.append(f"out={outcome}")
    return "|".join(parts)


def structural_intent_key(normalized_text: Any, *, capability_set: str = "",
                          step_count: Any = None) -> str:
    """**已归一**的文本意图 + 结构维度 → **v2 归组键**

    形如 ``v2|cap=…|steps:…‖<文本归一>``；分隔符 ``‖``（U+2016）不与
    ``+``/``|`` 冲突，故键可被逆向拆解取证（"这条为什么与那条同键"直接读得出来）。

    Args:
        normalized_text: **已经过 `normalize_intent()` 的**文本维度。本函数**不再**
            归一 —— 形态指纹（``shape:path``）本身是键不是自然语言，二次归一
            会把 ``shape:path`` 拆成 ``encoding+path+shape``（实测缺陷）。调用方
            若持有原始文本，请先调用 `normalize_intent()`。

    ``outcome`` **不入本键**：结果状态是 `models.SameTaskKey` 的**独立第三元**
    （成功与失败分属不同键），重复放进文本键会让"同一任务的成败"在两个地方
    各记一次，反而模糊了"意图相同、结果不同"这一语义。
    """
    return (f"{structural_atom(capability_set=capability_set, step_count=step_count)}"
            f"‖{str(normalized_text or '')}")


def is_structural_key(intent_key: Any) -> bool:
    """该键是否为 v2 结构键（口径变更的**显式可辨**判据）"""
    return str(intent_key or "").startswith(f"{STRUCTURAL_KEY_VERSION}|")


def text_key_of(intent_key: Any) -> str:
    """v2 结构键 → 其中的**文本维度**（非结构键原样返回）

    供"改进前后对比"把同一条记录按 v1（纯文本）与 v2（结构+文本）两种口径
    分别归组 —— 对比必须建立在**同一条记录**上，否则数字不可比。
    """
    text = str(intent_key or "")
    if not is_structural_key(text):
        return text
    _, _, tail = text.partition("‖")
    return tail


#: 一条记录未提供结构维度时使用的占位（**不猜**步数/集合，只标"未知"）
SHAPE_UNKNOWN = "unknown"


def record_shape(*, capability_set: Any = None,
                 step_count_bucket_value: Any = None,
                 step_count: Any = None) -> str:
    """记录 → 结构形状签名（``cap=<集合>|steps:<档位>``；缺项如实标 unknown）"""
    caps = str(capability_set or "").strip() or SHAPE_UNKNOWN
    bucket = (str(step_count_bucket_value).strip()
              if str(step_count_bucket_value or "").strip()
              else (step_count_bucket(step_count) if step_count is not None
                    else SHAPE_UNKNOWN))
    if bucket == STEP_BUCKET_UNKNOWN and step_count is None:
        bucket = SHAPE_UNKNOWN
    return f"cap={caps}|steps:{bucket}"


def _item_field(item: Any, name: str, default: Any = None) -> Any:
    """记录字段读取（dict 或对象；两处调用方各用一半，故做薄适配）"""
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def grouping_conflict_rate(items: Iterable[Any], *, key_fn: Any,
                           shape_of: Any = None) -> Dict[str, Any]:
    """**同键冲突率**（D3 的量化口径；改进前后用同一函数、同一批记录对比）

    定义：一条记录若与其同键的**其他记录**存在**不同结构形状**（能力集合或步数档位
    不同），则该记录计入冲突 —— 即"被归到同一组的其实是不同的活"。

    ``ratio`` = 冲突记录数 / 记录总数（0.0 = 每个键内的记录形状完全一致）。

    Args:
        items: 记录序列（`Trajectory`、`SameTaskKey` 或 dict 均可）。
        key_fn: 取值方式 —— 可调用对象，或字段名字符串（``"intent_key"``）。
        shape_of: 形状提取器（缺省按 ``capability_set`` + ``step_count_bucket``
            字段读取；字符串键与 v1 记录会落到 ``unknown``，如实标注而非猜测）。

    Returns:
        ``{"total", "keys", "groups", "conflict_items", "conflict_keys", "ratio",
        "key_dimension", "shape_dimension", "conflicts"}``
    """
    resolved_key = (key_fn if callable(key_fn)
                    else (lambda item: _item_field(item, str(key_fn), "")))
    resolved_shape = shape_of or (
        lambda item: record_shape(
            capability_set=_item_field(item, "capability_set"),
            step_count_bucket_value=_item_field(item, "step_count_bucket")))

    buckets: Dict[str, List[Tuple[int, str]]] = {}
    for index, item in enumerate(items or []):
        buckets.setdefault(str(resolved_key(item) or ""), []).append(
            (index, str(resolved_shape(item) or SHAPE_UNKNOWN)))

    conflict_items = 0
    conflict_keys = 0
    conflicts: List[Dict[str, Any]] = []
    for key in sorted(buckets):
        rows = buckets[key]
        shapes = {shape for _, shape in rows}
        if len(shapes) > 1:
            conflict_keys += 1
            conflict_items += len(rows)
            conflicts.append({"key": key, "size": len(rows),
                              "shapes": sorted(shapes)})
    total = sum(len(v) for v in buckets.values())
    return {
        "total": total,
        "keys": len(buckets),
        "groups": len(buckets),
        "conflict_items": conflict_items,
        "conflict_keys": conflict_keys,
        "ratio": round(conflict_items / total, 4) if total else 0.0,
        "key_dimension": "同键分组",
        "shape_dimension": "能力集合 + 步数档位",
        "conflicts": conflicts,
    }


def capability_declaration_gap(items: Iterable[Any], *,
                               declared_of: Any = None,
                               capabilities_of: Any = None,
                               normalize: Any = None) -> Dict[str, Any]:
    """**归组声明缺口**（D3 实测口径：归组键把"多能力链"归到了单个能力名下）

    定义：一条记录声明的能力（``capability_id``）**不在它自己的能力集合里**，或
    自身能力集合**多于一个**却仍被当作单能力观测单元 —— 二者都说明"这条记录被
    归到了与它形状不符的能力名下"。

    这不是文本键能解决的问题（文本相同与否无关），而是 S8-05 D3 在真实语料上
    暴露的**可测量后果**：S7-05 的 60 条三步链用例全部声明 `cp.builtin.read_file`，
    而其能力集合是 ``read_file + shell_execute + write_file``。

    Args:
        declared_of / capabilities_of: 取值器（缺省读 ``declared``/``capability_id``
            与 ``capability_set`` 字段）。
        normalize: 名称归一器（**可选**；传入 ``cases.normalize_capability_id``
            即可把工具名/别名差异排除在外 —— 否则 ``read_file`` 与
            ``cp.builtin.read_file`` 会被误计为"声明缺口"，把别名问题混进结构问题）。

    Returns:
        ``{"total", "gap_items", "multi_capability_items", "undeclared_items",
        "ratio", "samples"}``
    """
    resolved_declared = declared_of or (
        lambda item: (_item_field(item, "capability_id")
                      or _item_field(item, "declared")
                      or ""))
    resolved_caps = capabilities_of or (
        lambda item: [c for c in str(
            _item_field(item, "capability_set", "") or "").split("+") if c])
    resolve = normalize or (lambda value: str(value or ""))

    total = 0
    gap_items = 0
    multi = 0
    undeclared = 0
    samples: List[Dict[str, Any]] = []
    for item in items or []:
        total += 1
        declared = str(resolve(resolved_declared(item)) or "")
        caps = [str(resolve(c)) for c in (resolved_caps(item) or [])]
        if not caps:
            continue
        if len(caps) > 1:
            multi += 1
        if declared and declared not in caps:
            undeclared += 1
        if len(caps) > 1 or (declared and declared not in caps):
            gap_items += 1
            if len(samples) < 5:
                samples.append({"declared": declared, "capabilities": sorted(caps)})
    return {
        "total": total,
        "gap_items": gap_items,
        "multi_capability_items": multi,
        "undeclared_items": undeclared,
        "ratio": round(gap_items / total, 4) if total else 0.0,
        "samples": samples,
    }


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
                         shape_depth: int = 1, capability_set: str = "",
                         step_count: Any = None, steps: Any = None,
                         structural: bool = True) -> str:
    """轨迹 → ``intent_key``（判定键第二元；优先级明确，无歧义）

    解析顺序：

    1. 显式 ``intent`` 实参（调用方给出任务意图文本）→ `normalize_intent()`；
    2. Trace 自身 ``side_effects.notes`` 中的 ``intent:<文本>`` 标注（预留通道）；
    3. 请求参数的**形态指纹** `arg_shape_signature()`（默认口径）；
    4. ``"unknown"``（无任何信号时如实标注，不臆造）。

    **TASK-S8-05（D3）**：``structural=True``（默认）时，返回 **v2 结构键**
    （`structural_intent_key()`：结构原子 + ``‖`` + 文本归一）。结构维度的来源：

    - ``steps`` 显式给出（调用方持有清洗后的步骤）→ 由步骤推导能力集合与步数；
    - 否则退化到 ``capability_set`` / ``step_count`` 显式实参；
    - 二者都缺 → 只以触发能力（``trace.capability_id``）与 ``"unknown"`` 档位入原子
      （**不臆造**步数与集合）。

    ``structural=False`` 保留 S3-01 的**纯文本**口径（供只关心文本归一的调用方与
    回归对照；判定集/报告等按结构归组的调用方应使用默认值）。
    """
    if intent:
        key = normalize_intent(intent)
        if key:
            text_key = key
        else:
            text_key = ""
    else:
        text_key = ""
        notes = getattr(getattr(trace, "side_effects", None), "notes", None) or []
        for note in notes:
            text = str(note or "")
            if text.startswith("intent:"):
                candidate = normalize_intent(text.split(":", 1)[1])
                if candidate:
                    text_key = candidate
                    break
        if not text_key:
            shape = arg_shape_signature(getattr(getattr(trace, "request", None),
                                                "args_redacted", None),
                                        depth=shape_depth)
            text_key = shape or "unknown"
    if not structural:
        return text_key
    return structural_intent_key(
        text_key,
        capability_set=_resolve_capability_set(trace, steps, capability_set),
        step_count=_resolve_step_count(trace, steps, step_count))


def _resolve_capability_set(trace: Any, steps: Any, given: str) -> str:
    """能力集合：显式 step 序列 > 显式实参 > Trace 自带 steps > 触发能力"""
    if steps is not None:
        return steps_capability_set(steps)
    if given:
        return str(given)
    trace_steps = getattr(trace, "steps", None)
    if trace_steps:
        return steps_capability_set(trace_steps)
    cid = str(getattr(trace, "capability_id", "") or "")
    return capability_set_key([cid]) if cid else "none"


def _resolve_step_count(trace: Any, steps: Any, given: Any) -> Any:
    """步数：显式 step 序列 > 显式实参 > Trace 自带 steps（缺则 ``None`` ⇒ unknown）

    刻意**不**从 ``trajectory.step_count`` 之类派生属性兜底：那是"清洗后的步数"，
    与"任务形状"不是一回事（前导噪声步会被削掉），用它会让同一任务在成败两次执行上
    得到不同档位。宁可如实标 ``unknown``，也不拿一个口径不同的数当形状。
    """
    if steps is not None:
        return task_step_count(steps)
    if given is not None:
        return given
    trace_steps = getattr(trace, "steps", None)
    if trace_steps:
        return task_step_count(trace_steps)
    return None


def restructure_task_key(key: SameTaskKey, steps: Any = None, *,
                         row: Any = None,
                         capability_set: str = "", step_count: Any = None
                         ) -> SameTaskKey:
    """给既有判定键补/换**结构维度**（前三元组逐字不动）

    文本维度沿用 ``key.intent_key`` 中已有的文本（`text_key_of`），**不重新解读意图**
    —— 键的文本维度属于调用方已确定的语义，本函数只补结构，不越权改语义。

    结构维度来源（优先级）：``steps``（由它推导）> 显式 ``capability_set`` /
    ``step_count``。**两条路径都不提供** ⇒ 结构维度落空（``caps=""``、步数 unknown），
    形如 ``v2|cap=|steps:unknown‖<文本>`` —— 如实标注"未提供"，不臆造。
    """
    if steps is not None:
        caps = steps_capability_set(steps)
        count: Any = task_step_count(steps)
    else:
        caps = str(capability_set or "")
        count = step_count
    return SameTaskKey(
        capability_id=key.capability_id,
        intent_key=structural_intent_key(text_key_of(key.intent_key),
                                         capability_set=caps, step_count=count),
        outcome=key.outcome,
        step_count_bucket=("" if count is None else step_count_bucket(count)),
        capability_set=caps,
    )


class _EmptyTrace:
    """空轨迹占位（仅用于"只给步骤、不给 Trace 对象"的调用路径）"""

    capability_id = ""
    request = None
    side_effects = None
    steps: Sequence[Any] = ()

    def __repr__(self) -> str:  # pragma: no cover - 调试友好
        return "<empty trace>"


_EMPTY_TRACE = _EmptyTrace()


def classify_outcome(status: Any) -> str:
    """Trace 结果状态 → 归一 outcome（非 ``success`` 一律计 ``failure``）"""
    return OUTCOME_SUCCESS if str(status or "") == "success" else OUTCOME_FAILURE


def same_task_key(trace: Any, *, capability_id: str = "", intent: str = "",
                  shape_depth: int = 1, steps: Any = None) -> SameTaskKey:
    """一条能力级 Trace → 同类判定键（三元组 + S8-05 的两个**结构维度**）

    ``steps`` 给定时（清洗后的步骤序列），结构维度由步骤推导 —— 这是 S3-01
    ``trajectory_from_rows()`` 的调用口径：键必须在**清洗后**的步骤上计算，
    否则重试前缀会让步数抖动并拆键。
    """
    cid = str(capability_id or getattr(trace, "capability_id", "") or "")
    capability_set = _resolve_capability_set(trace, steps, "")
    step_count = _resolve_step_count(trace, steps, None)
    return SameTaskKey(
        capability_id=cid,
        intent_key=intent_key_for_trace(trace, intent=intent,
                                        shape_depth=shape_depth,
                                        capability_set=capability_set,
                                        step_count=step_count),
        outcome=classify_outcome(getattr(getattr(trace, "response", None),
                                         "status", "")),
        step_count_bucket=step_count_bucket(step_count),
        capability_set=capability_set,
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
    restructure_key: bool = True,
) -> Trajectory:
    """一组能力级 Trace 行（同一任务，按 ``started_at`` 升序）→ 清洗后的轨迹

    轨迹身份取 ``task_id``（缺则退 `source_trace_id`）；``key`` 由调用方给出
    （观测单元的键），本函数只负责"把行变成清洗后的步序列"。

    **TASK-S8-05（D3）**：``restructure_key=True``（默认）时，把调用方给的键补上两个
    结构维度（能力集合与步数档位）。结构维度取自**清洗前**的原始步骤序列，理由：

    - 清洗规则（`strip_noise_prefix`）会削掉前导失败/探索步，**失败任务因此比成功任务
      少一步** —— 若按键结构取自清洗后序列，同一任务的成败两条轨迹会落入**不同键**，
      "失败集配对 → 分支提取"整条链路静默失效（实测：负样本 3 → 0）；
    - 任务**形状**是任务的属性，不因某次执行的前导噪声而改变；清洗影响的步数已由
      ``raw_step_count`` / ``dropped_steps`` 如实留痕，不需要再挤进归组键。

    判定键的前三元组（能力/意图/结果）**保持调用方给定值不变**（清洗不改任务身份）。
    """
    ordered = list(rows)
    steps: List[TrajectoryStep] = []
    for i, row in enumerate(ordered):
        label = ""
        if default_labels is not None and i < len(default_labels):
            label = str(default_labels[i] or "")
        steps.append(_step_from_trace(row, i + 1, default_label=label))
    cleaned, stats = clean_trajectory(steps, min_keep=min_keep)
    if restructure_key and key is not None:
        key = restructure_task_key(key, steps)
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
    # S8-05（D3）结构维度
    "STRUCTURAL_KEY_VERSION", "STEP_BUCKETS", "STEP_BUCKET_OVERFLOW",
    "STEP_BUCKET_UNKNOWN", "SHAPE_UNKNOWN",
    "step_count_bucket", "capability_set_key", "steps_capability_set",
    "task_step_count",
    "structural_atom", "structural_intent_key", "is_structural_key",
    "text_key_of", "record_shape", "grouping_conflict_rate",
    "capability_declaration_gap", "restructure_task_key",
    "strip_noise_prefix", "merge_duplicate_steps", "mark_negative",
    "clean_trajectory", "trajectory_from_rows",
    "group_by_same_task", "dedupe_by_task",
]
