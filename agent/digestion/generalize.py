"""参数泛化：具体值 → 占位符（TASK-S3-01 步骤 2/3 的共有底座）

两层泛化，职责不重叠：

1. **形态泛化（shape）** —— `shape_placeholder()` / `normalize_param_value()`：
   把"每次都不一样、且不承载任务语义"的值压成形态占位符
   （``${path}`` / ``${timestamp}`` / ``${uuid}`` / ``${hex_id}`` / ``${url}`` /
   ``${number}``）。这一步在**清洗**阶段执行（任务书步骤 2"归一参数化：路径/时间戳/
   随机值→占位符"），使同一条轨迹变得与具体路径/时刻无关。
2. **具名泛化（named slot）** —— `infer_parameter_slots()` / `apply_slots()`：
   在**挖掘**阶段，对同一骨架位置上"跨轨迹取值有差异"的键提取具名参数槽
   ``${键名}``（任务书步骤 3"具体值→参数占位符、硬编码路径→变量"）；跨轨迹取值
   恒定的键保留字面量（不制造伪参数）。

**占位符语法沿用云枢既有唯一方言**：``${name}``（见
`agent/workflow_learning/learner._templatize_params` 与 `executor._REF_RE`），
本模块**不新造方言**，避免第三套模板语义。

任务书要求的"补齐"点：既有 `learner._templatize_params` 只把含用户输入关键字的字符串
替换为 ``${input}``，且会 lower() 掉原文；本模块补齐"路径/时间戳/随机值/URL/数字"的
**形态识别**与"跨轨迹差异 → 具名槽"的**统计判定**，两者互补而非重复。
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .models import NOISE_UNPARAMETERIZED, ParameterSlot

#: 具名参数槽占位符模板（与 learner/executor 的方言一致）
SLOT_TEMPLATE = "${%s}"

# 形态占位符
PH_PATH = "${path}"
PH_TIMESTAMP = "${timestamp}"
PH_UUID = "${uuid}"
PH_HEX_ID = "${hex_id}"
PH_URL = "${url}"
PH_NUMBER = "${number}"
PH_EMAIL = "${email}"
PH_TEXT = "${text}"

_SHAPE_PLACEHOLDERS = frozenset({
    PH_PATH, PH_TIMESTAMP, PH_UUID, PH_HEX_ID, PH_URL, PH_NUMBER, PH_EMAIL,
    PH_TEXT,
})

#: 已参数化判定：`${name}` 形态
_PLACEHOLDER_RE = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_.]*\}$")

_TS_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?)?$")
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_HEX_ID_RE = re.compile(r"^[0-9a-fA-F]{16,64}$")
_URL_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://\S+$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_NUMBER_RE = re.compile(r"^[+-]?\d+(\.\d+)?$")
#: 绝对路径（Windows 盘符 / UNC / POSIX 根 / 家目录）
_ABS_PATH_RE = re.compile(
    r"^(?:[a-zA-Z]:[\\/]|\\\\[^\\/]+[\\/]|/(?:[^/\s]+/)*[^/\s]*|~[\\/])")
#: 相对路径但含分隔符（如 src/x.py、./a/b）
_REL_PATH_RE = re.compile(r"^\.{0,2}[\\/]?[^\\/\s]+[\\/][^\\/\s]+")
#: 文件名（含扩展名，无分隔符）
_FILENAME_RE = re.compile(r"^[^\\/\s]+\.[A-Za-z0-9]{1,8}$")


def is_placeholder(value: Any) -> bool:
    """是否已是 ``${name}`` 形态的占位符"""
    if not isinstance(value, str):
        return False
    text = value.strip()
    return bool(_PLACEHOLDER_RE.match(text)) or text in _SHAPE_PLACEHOLDERS


def slot_placeholder(name: str) -> str:
    """键名 → 具名槽占位符（``${name}``）"""
    safe = re.sub(r"[^0-9A-Za-z_]+", "_", str(name or "")).strip("_")
    return SLOT_TEMPLATE % (safe or "arg")


def shape_placeholder(value: Any) -> Optional[str]:
    """值的**形态占位符**；无稳定形态（普通短文本）返回 None。

    判定顺序即优先级：时间戳 → UUID → URL → 邮箱 → 绝对路径 → 相对路径 →
    文件名 → 长 hex → 数字 → None。顺序刻意把"更具体"的形态放前面
    （如 ``2026-09-10`` 既是时间戳也匹配数字形态时取时间戳）。
    """
    if not isinstance(value, str):
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, (int, float)):
            return PH_NUMBER
        return None
    text = value.strip()
    if not text:
        return None
    if text in _SHAPE_PLACEHOLDERS:
        return text
    if _TS_RE.match(text):
        return PH_TIMESTAMP
    if _UUID_RE.match(text):
        return PH_UUID
    if _URL_RE.match(text):
        return PH_URL
    if _EMAIL_RE.match(text):
        return PH_EMAIL
    if _ABS_PATH_RE.match(text):
        return PH_PATH
    if _REL_PATH_RE.match(text) or _FILENAME_RE.match(text):
        return PH_PATH
    if _HEX_ID_RE.match(text):
        return PH_HEX_ID
    if _NUMBER_RE.match(text):
        return PH_NUMBER
    return None


def normalize_param_value(value: Any) -> Any:
    """清洗期归一：形态可识别 → 形态占位符；否则原值（递归 list/dict）"""
    like = shape_placeholder(value)
    if like is not None:
        return like
    if isinstance(value, list):
        return [normalize_param_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): normalize_param_value(v) for k, v in value.items()}
    return value


def generalize_step_params(params: Any) -> Dict[str, Any]:
    """一步的参数字典 → 形态归一后的副本（非 dict 输入返回 ``{"value": ...}``）"""
    if not isinstance(params, dict):
        if params is None:
            return {}
        return {"value": normalize_param_value(params)}
    return {str(k): normalize_param_value(v) for k, v in params.items()}


def _canonical(value: Any) -> str:
    """取值 → 稳定比较串（用于"跨轨迹是否同值"判定）"""
    if isinstance(value, (dict, list)):
        try:
            import json
            return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        except Exception:  # noqa: BLE001
            return str(value)
    return str(value)


def infer_parameter_slots(
    steps_per_trajectory: Sequence[Sequence[Any]],
    *,
    labels_per_trajectory: Optional[Sequence[Sequence[str]]] = None,
    max_examples: int = 3,
) -> List[ParameterSlot]:
    """跨同类轨迹推断具名参数槽（"取值有差异"才算参数）。

    Args:
        steps_per_trajectory: 每条轨迹的步骤序列；每步为 ``(label, params)`` 二元组
            或 ``params`` 字典（无 label 时按位置 ``step<seq>`` 归位）。
        labels_per_trajectory: 与上者对齐的标签序列（可选，用于给槽标注所属步骤）。
        max_examples: 每个槽保留的样例值上限（只保留**少量脱敏后**样例，便于人工核对）。

    Returns:
        参数槽列表，按 (步序, 键名) 确定性排序；**跨轨迹恒定值不进槽**（避免伪参数）。
    """
    # key = (position, param_name) -> {canonical_value: 出现次数}
    buckets: Dict[Tuple[int, str], Dict[str, int]] = {}
    raw_examples: Dict[Tuple[int, str], List[Any]] = {}
    label_of: Dict[Tuple[int, str], str] = {}
    order: List[Tuple[int, str]] = []

    for ti, steps in enumerate(steps_per_trajectory):
        labels = None
        if labels_per_trajectory is not None and ti < len(labels_per_trajectory):
            labels = labels_per_trajectory[ti]
        for pos, step in enumerate(steps):
            if isinstance(step, (tuple, list)) and len(step) == 2 \
                    and isinstance(step[1], dict):
                params = step[1]
                label = str(step[0] or "")
            elif isinstance(step, dict):
                params = step
                label = str(labels[pos]) if labels and pos < len(labels) \
                    else f"step{pos + 1}"
            else:
                continue
            for name, value in params.items():
                key = (pos, str(name))
                if key not in buckets:
                    buckets[key] = {}
                    raw_examples[key] = []
                    label_of[key] = label
                    order.append(key)
                canon = _canonical(value)
                buckets[key][canon] = buckets[key].get(canon, 0) + 1
                if len(raw_examples[key]) < max_examples:
                    raw_examples[key].append(value)

    # 同一键名出现在多个位次时按**出现次序**加后缀：首位保持 `${键名}`，
    # 其后为 `${键名_2}`、`${键名_3}`（比按原始位次编号更可读，且不产生跳号）
    occurrences: Dict[str, List[int]] = {}
    for pos, name in order:
        if pos not in occurrences.setdefault(name, []):
            occurrences[name].append(pos)

    slots: List[ParameterSlot] = []
    for key in order:
        pos, name = key
        values = buckets[key]
        if len(values) <= 1:
            # 跨轨迹取值恒定 → 字面量，不制造伪参数
            continue
        ordinal = occurrences[name].index(pos) + 1
        slot_name = name if ordinal == 1 else f"{name}_{ordinal}"
        slots.append(ParameterSlot(
            name=slot_name,
            placeholder=slot_placeholder(slot_name),
            step_label=label_of[key],
            sample_count=sum(values.values()),
            distinct_values=len(values),
            examples=[_safe_example(v) for v in raw_examples[key]][:max_examples],
        ))
    slots.sort(key=lambda s: (s.step_label, s.name))
    return slots


def _safe_example(value: Any) -> str:
    """样例值 → 短字符串（占位符/形态值原样；其余截断，绝不放大原文）"""
    text = value if isinstance(value, str) else _canonical(value)
    return text[:60]


def slot_index(slots: Iterable[ParameterSlot]) -> Dict[str, ParameterSlot]:
    """槽索引：槽名 / 键名 → 槽（供 `apply_slots` 反查）"""
    index: Dict[str, ParameterSlot] = {}
    for s in slots:
        index[s.name] = s
        index[s.placeholder] = s
    return index


def apply_slots(params: Dict[str, Any],
                slots: Sequence[ParameterSlot]) -> Tuple[Dict[str, Any], bool]:
    """把一步的形态归一参数替换为**具名槽**（跨轨迹有差异的键）。

    Returns:
        (替换后的参数, 是否发生了替换) —— 后者供调用方标记
        `NOISE_UNPARAMETERIZED`（"仍有未参数化的字面量"）用。
    """
    by_name: Dict[str, ParameterSlot] = {}
    for s in slots:
        by_name.setdefault(s.name, s)
        by_name.setdefault(s.placeholder, s)
    out: Dict[str, Any] = {}
    changed = False
    for name, value in params.items():
        slot = by_name.get(str(name))
        if slot is not None and (is_placeholder(value)
                                 or shape_placeholder(value) is not None
                                 or not isinstance(value, (dict, list))):
            out[name] = slot.placeholder
            changed = True
        else:
            out[name] = value
    return out, changed


__all__ = [
    "SLOT_TEMPLATE", "PH_PATH", "PH_TIMESTAMP", "PH_UUID", "PH_HEX_ID", "PH_URL",
    "PH_NUMBER", "PH_EMAIL", "PH_TEXT",
    "is_placeholder", "slot_placeholder", "shape_placeholder",
    "normalize_param_value", "generalize_step_params",
    "infer_parameter_slots", "slot_index", "apply_slots",
]
