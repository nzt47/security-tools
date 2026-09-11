"""评测用例的**分层数据契约**（TASK-S5-02 / v7.2 §6.5 评测分层 L0–L3）

本模块只定义"用例长什么样、怎么校验、怎么算哈希"，**不执行任何判定**
（判定器在 `agent.eval.checkers`，执行器在 `agent.eval.runner`）。这样做的原因是
L0 锚的"系统不可写 + 哈希锚定"要求用例集必须能被**逐字节复现**：

1. 用例是**数据**（JSON），不是测试代码 —— 冻结后任何字节变化都能被哈希检出；
2. 用例集的整体哈希（`caseset_sha256`）= 对**规范化后的逐条用例**排序后取哈希，
   与文件缩进/键序无关（避免"格式漂移"被误判为锚变更，也避免"内容漂移"被漏判）；
3. 校验是**纯函数**（`validate_case_set`），不读盘、不写入。

## 用例字段（eval.cases.v1）

======================  ==========================================================
字段                     语义
======================  ==========================================================
``id``                  全局唯一，形如 ``L0-S1-01``（前缀必须与所在层一致）
``layer``               ``L0`` / ``L1`` / ``L2`` / ``L3``
``scenario``            种子场景（见 `SCENARIOS`：S1 修 bug / S2 懂代码库 / S3 提交 /
                        Router 决策 / 审批拦截 / 回滚）
``title``               人类可读标题（报告与清单用）
``input``               喂给被测系统的输入（提示词 / 工件 / 候选集），**纯数据**
``expect``              **判定条目列表**（全部通过才算 pass）；每条含
                        ``checker``（判定器名）+ ``path``（答案内的取值路径）+
                        ``args`` + ``why``（为什么这么判，报告逐条展示）
``verdict_kind``        ``mechanical``（机械可验）/ ``proxy``（代理指标，须披露）/
                        ``unsupported``（本环境无法判定，如实标注，不计入 pass）
``notes``               口径说明（尤其 proxy/unsupported 必须写清回落口径）
======================  ==========================================================

**判定标准纪律**：机械可验优先。`verdict_kind="mechanical"` 的用例，其全部
``expect`` 条目都必须由确定性判定器给出（见 `checkers.MECHANICAL_CHECKERS`）；
一旦引用代理判定器（如无 LLM 的词表代理），`verdict_kind` 必须降级为 ``proxy``
并在报告里显式披露 —— 不允许"靠感觉通过"。
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

#: 用例集 schema 名（与 `agent.observability.events.SCHEMA_NAME` 同风格）
SCHEMA_NAME = "eval.cases.v1"
SCHEMA_VERSION = 1

LAYER_L0 = "L0"
LAYER_L1 = "L1"
LAYER_L2 = "L2"
LAYER_L3 = "L3"
LAYERS: Tuple[str, ...] = (LAYER_L0, LAYER_L1, LAYER_L2, LAYER_L3)

#: 种子场景（L2 Core-50 的"三类种子场景"是前三条：S1 修 bug / S2 懂代码库 / S3 提交）
SCENARIO_S1_FIX_BUG = "S1_fix_bug"
SCENARIO_S2_CODEBASE_QA = "S2_codebase_qa"
SCENARIO_S3_COMMIT = "S3_commit"
SCENARIO_ROUTER = "router_decision"
SCENARIO_APPROVAL = "approval_intercept"
SCENARIO_ROLLBACK = "rollback"

SEED_SCENARIOS: Tuple[str, ...] = (
    SCENARIO_S1_FIX_BUG, SCENARIO_S2_CODEBASE_QA, SCENARIO_S3_COMMIT,
)
SCENARIOS: Tuple[str, ...] = SEED_SCENARIOS + (
    SCENARIO_ROUTER, SCENARIO_APPROVAL, SCENARIO_ROLLBACK,
)

VERDICT_MECHANICAL = "mechanical"
VERDICT_PROXY = "proxy"
VERDICT_UNSUPPORTED = "unsupported"
VERDICT_KINDS: Tuple[str, ...] = (VERDICT_MECHANICAL, VERDICT_PROXY,
                                 VERDICT_UNSUPPORTED)

#: 用例顶层允许的键（未知键即校验失败：防止"悄悄加了不生效的字段"）
CASE_FIELDS: Tuple[str, ...] = (
    "id", "layer", "scenario", "title", "input", "expect", "verdict_kind",
    "notes", "tags",
)
#: 判定条目允许的键
CHECK_FIELDS: Tuple[str, ...] = ("checker", "path", "args", "why")

#: 层规模契约（§6.5）：L0=20 / L1=10 / L2=50 / L3 框架（不设固定条数）
LAYER_SIZES: Dict[str, Optional[int]] = {
    LAYER_L0: 20, LAYER_L1: 10, LAYER_L2: 50, LAYER_L3: None,
}
#: L0 每类场景**至少**条数（§6.5 + 任务书 §二 步骤 1「每类 ≥2 条」）
L0_MIN_PER_SCENARIO = 2
#: L2 三类种子场景**每类至少**条数
L2_MIN_PER_SEED = 2


class CaseError(ValueError):
    """用例层基类异常"""


class CaseSchemaError(CaseError):
    """用例结构非法（缺字段 / 未知字段 / 类型不符）"""


class CaseSetError(CaseError):
    """用例集非法（重复 id / 层规模不符 / 场景覆盖不足）"""


# ════════════════════════════════════════════════════════════
#  哈希 / 规范化（哈希锚定的唯一口径）
# ════════════════════════════════════════════════════════════


def canonical_json(data: Any) -> str:
    """稳定 JSON（sort_keys + 紧凑分隔符）——**哈希口径的唯一实现**"""
    return json.dumps(data, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)


def sha256_text(text: str) -> str:
    """文本 → sha256 十六进制（UTF-8）"""
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def case_digest(case: "EvalCase") -> str:
    """单条用例哈希（只覆盖语义字段，不含 `tags` 之外的装饰信息）"""
    return sha256_text(canonical_json(case.to_dict()))


def caseset_digest(cases: Sequence["EvalCase"]) -> str:
    """用例集整体哈希：逐条哈希**排序后**再哈希（与输入顺序无关）

    逐条哈希再汇总（而不是直接哈希整份文件）有两个好处：
    ① 顺序调整不会改变整体哈希（用例顺序无业务含义）；
    ② manifest 里同时存了每条哈希，可定位到**具体哪条**被改动。
    """
    digests = sorted(case_digest(c) for c in cases)
    return sha256_text("\n".join(digests))


def caseset_manifest_entries(cases: Sequence["EvalCase"]) -> Dict[str, str]:
    """``{case_id: 单条哈希}``（排序，便于人读 diff）"""
    return {c.id: case_digest(c) for c in sorted(cases, key=lambda c: c.id)}


# ════════════════════════════════════════════════════════════
#  用例对象
# ════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class EvalCase:
    """一条评测用例（不可变：冻结语义由数据类承担第一层保护）"""

    id: str
    layer: str
    scenario: str
    title: str = ""
    input: Dict[str, Any] = field(default_factory=dict)
    expect: Tuple[Dict[str, Any], ...] = ()
    verdict_kind: str = VERDICT_MECHANICAL
    notes: str = ""
    tags: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", str(self.id or ""))
        object.__setattr__(self, "layer", str(self.layer or ""))
        object.__setattr__(self, "scenario", str(self.scenario or ""))
        object.__setattr__(self, "title", str(self.title or ""))
        object.__setattr__(self, "input", dict(self.input or {}))
        object.__setattr__(self, "expect", tuple(dict(c) for c in (self.expect or ())))
        object.__setattr__(self, "verdict_kind",
                           str(self.verdict_kind or VERDICT_MECHANICAL))
        object.__setattr__(self, "notes", str(self.notes or ""))
        object.__setattr__(self, "tags", tuple(str(t) for t in (self.tags or ())))

    # ── 视图 ────────────────────────────────────────────────

    @property
    def is_mechanical(self) -> bool:
        return self.verdict_kind == VERDICT_MECHANICAL

    @property
    def checkers(self) -> Tuple[str, ...]:
        return tuple(str(c.get("checker") or "") for c in self.expect)

    @property
    def case_key(self) -> str:
        """答案文件里的键（`id` 去掉层前缀，便于参考解书写）"""
        return self.id

    def summary(self) -> Dict[str, Any]:
        return {
            "id": self.id, "layer": self.layer, "scenario": self.scenario,
            "title": self.title, "verdict_kind": self.verdict_kind,
            "checks": len(self.expect), "checkers": list(self.checkers),
            "tags": list(self.tags), "notes": self.notes,
            "sha256": case_digest(self),
        }

    def to_dict(self) -> Dict[str, Any]:
        """规范化字典（哈希口径 = 本方法的 canonical JSON）"""
        data: Dict[str, Any] = {
            "id": self.id, "layer": self.layer, "scenario": self.scenario,
            "title": self.title, "input": dict(self.input),
            "expect": [dict(c) for c in self.expect],
            "verdict_kind": self.verdict_kind, "notes": self.notes,
        }
        if self.tags:
            data["tags"] = list(self.tags)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvalCase":
        """从字典构造（未知字段 → `CaseSchemaError`，不做静默丢弃）"""
        if not isinstance(data, Mapping):
            raise CaseSchemaError(f"用例必须是对象，得到 {type(data).__name__}")
        unknown = sorted(set(data) - set(CASE_FIELDS))
        if unknown:
            raise CaseSchemaError(f"用例含未知字段: {unknown}（允许 {CASE_FIELDS}）")
        for required in ("id", "layer", "scenario"):
            if not str(data.get(required) or ""):
                raise CaseSchemaError(f"用例缺必填字段: {required}")
        raw_expect = data.get("expect") or []
        if not isinstance(raw_expect, (list, tuple)):
            raise CaseSchemaError("expect 必须是数组")
        checks: List[Dict[str, Any]] = []
        for index, item in enumerate(raw_expect):
            if not isinstance(item, Mapping):
                raise CaseSchemaError(f"expect[{index}] 必须是对象")
            bad = sorted(set(item) - set(CHECK_FIELDS))
            if bad:
                raise CaseSchemaError(
                    f"expect[{index}] 含未知字段: {bad}（允许 {CHECK_FIELDS}）")
            if not str(item.get("checker") or ""):
                raise CaseSchemaError(f"expect[{index}] 缺 checker")
            checks.append(dict(item))
        raw_input = data.get("input")
        if raw_input is None:
            raw_input = {}
        if not isinstance(raw_input, Mapping):
            raise CaseSchemaError(
                f"input 必须是对象，得到 {type(raw_input).__name__}")
        return cls(
            id=str(data.get("id") or ""), layer=str(data.get("layer") or ""),
            scenario=str(data.get("scenario") or ""),
            title=str(data.get("title") or ""), input=dict(raw_input),
            expect=tuple(checks),
            verdict_kind=str(data.get("verdict_kind") or VERDICT_MECHANICAL),
            notes=str(data.get("notes") or ""),
            tags=tuple(str(t) for t in (data.get("tags") or ())),
        )


@dataclass
class EvalCaseSet:
    """一层用例集（含来源路径与哈希；`frozen=True` 表示冻结锚）"""

    layer: str
    cases: Tuple[EvalCase, ...] = ()
    path: str = ""
    schema: str = SCHEMA_NAME
    frozen: bool = False
    meta: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.layer = str(self.layer or "")
        self.cases = tuple(self.cases or ())
        self.schema = str(self.schema or SCHEMA_NAME)
        self.meta = dict(self.meta or {})

    # ── 视图 ────────────────────────────────────────────────

    def __len__(self) -> int:
        return len(self.cases)

    def __iter__(self):
        return iter(self.cases)

    @property
    def caseset_sha256(self) -> str:
        return caseset_digest(self.cases)

    def get(self, case_id: str) -> Optional[EvalCase]:
        for case in self.cases:
            if case.id == case_id:
                return case
        return None

    def by_scenario(self) -> Dict[str, List[EvalCase]]:
        out: Dict[str, List[EvalCase]] = {}
        for case in self.cases:
            out.setdefault(case.scenario, []).append(case)
        return out

    def scenario_counts(self) -> Dict[str, int]:
        return {k: len(v) for k, v in sorted(self.by_scenario().items())}

    def verdict_counts(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for case in self.cases:
            out[case.verdict_kind] = out.get(case.verdict_kind, 0) + 1
        return dict(sorted(out.items()))

    def inventory(self) -> List[Dict[str, Any]]:
        """逐条清单（验收报告"逐条清单"直接引用本方法）"""
        return [c.summary() for c in self.cases]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.schema,
            "layer": self.layer,
            "frozen": bool(self.frozen),
            "count": len(self.cases),
            "caseset_sha256": self.caseset_sha256,
            "scenario_counts": self.scenario_counts(),
            "verdict_counts": self.verdict_counts(),
            "meta": dict(self.meta),
            "cases": [c.to_dict() for c in self.cases],
        }


# ════════════════════════════════════════════════════════════
#  读 / 写
# ════════════════════════════════════════════════════════════


def parse_case_set(data: Mapping[str, Any], *, path: str = "") -> EvalCaseSet:
    """字典 → 用例集（结构校验，不校验规模/覆盖）"""
    if not isinstance(data, Mapping):
        raise CaseSchemaError("用例集根必须是对象")
    schema = str(data.get("schema") or "")
    if schema and schema != SCHEMA_NAME:
        raise CaseSchemaError(f"未知 schema: {schema!r}（期望 {SCHEMA_NAME}）")
    layer = str(data.get("layer") or "")
    if layer and layer not in LAYERS:
        raise CaseSchemaError(f"未知层: {layer!r}（允许 {LAYERS}）")
    raw_cases = data.get("cases")
    if not isinstance(raw_cases, (list, tuple)):
        raise CaseSchemaError("cases 必须是数组")
    cases = tuple(EvalCase.from_dict(c) for c in raw_cases)
    return EvalCaseSet(layer=layer, cases=cases, path=str(path), schema=schema or SCHEMA_NAME,
                       frozen=bool(data.get("frozen")), meta=dict(data.get("meta") or {}))


def load_case_set(path: str, *, validate: bool = True) -> EvalCaseSet:
    """从 JSON 文件读用例集

    Args:
        validate: 为真时执行 `validate_case_set`（规模 + 场景覆盖 + 判定器登记）。
    """
    if not os.path.exists(path):
        raise CaseSetError(f"用例集文件不存在: {path}")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, ValueError) as e:
        raise CaseSchemaError(f"用例集 JSON 非法（{path}）: {e}") from e
    case_set = parse_case_set(data, path=path)
    if validate:
        errors = validate_case_set(case_set, require_layer_size=True)
        if errors:
            raise CaseSetError(f"用例集校验失败（{path}）: " + "; ".join(errors))
    return case_set


def dump_case_set(case_set: EvalCaseSet, path: str, *,
                  indent: int = 2) -> str:
    """写用例集（**仅冻结脚本使用**；锚目录由 `agent.eval.anchor` 守门）

    返回写入的规范文本；写入前先做结构自检（写坏数据比不写更糟）。
    """
    errors = validate_case_set(case_set, require_layer_size=False)
    if errors:
        raise CaseSetError("拒绝写出非法用例集: " + "; ".join(errors))
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    text = json.dumps(case_set.to_dict(), ensure_ascii=False, indent=indent) + "\n"
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return text


# ════════════════════════════════════════════════════════════
#  校验（纯函数：不读盘 / 不写入）
# ════════════════════════════════════════════════════════════


def validate_case_set(case_set: EvalCaseSet, *,
                      require_layer_size: bool = True,
                      known_checkers: Optional[Iterable[str]] = None) -> List[str]:
    """用例集校验 → 错误清单（空清单 = 通过）

    校验项（逐条对应任务书 §二 步骤 1 的硬约束）：
    ① id 唯一且前缀 = 层；② scenario 必须是登记场景；③ verdict_kind 合法；
    ④ 每条 ≥1 个判定条目且判定器已登记（`known_checkers` 缺省取
    `agent.eval.checkers.MECHANICAL_CHECKERS` + `PROXY_CHECKERS`）；
    ⑤ 机械用例不得引用代理判定器；⑥ 层规模契约；⑦ L0 每类场景 ≥2；
    ⑧ L2 覆盖三类种子场景且每类 ≥2。
    """
    errors: List[str] = []
    if case_set.layer and case_set.layer not in LAYERS:
        errors.append(f"未知层: {case_set.layer!r}")
    seen: Dict[str, int] = {}
    for case in case_set.cases:
        if case.id in seen:
            errors.append(f"重复用例 id: {case.id}")
        seen[case.id] = seen.get(case.id, 0) + 1
        if case.layer != case_set.layer:
            errors.append(f"{case.id}: layer={case.layer!r} 与用例集层 {case_set.layer!r} 不一致")
        if case_set.layer and not case.id.startswith(f"{case_set.layer}-"):
            errors.append(f"{case.id}: id 前缀必须为 {case_set.layer}-")
        if case.scenario not in SCENARIOS:
            errors.append(f"{case.id}: 未知场景 {case.scenario!r}（允许 {SCENARIOS}）")
        if case.verdict_kind not in VERDICT_KINDS:
            errors.append(f"{case.id}: 未知 verdict_kind {case.verdict_kind!r}")
        if not case.expect and case.verdict_kind != VERDICT_UNSUPPORTED:
            errors.append(f"{case.id}: expect 为空（至少 1 条判定）")
        if case.verdict_kind in (VERDICT_PROXY, VERDICT_UNSUPPORTED) and not case.notes:
            errors.append(
                f"{case.id}: verdict_kind={case.verdict_kind} 必须在 notes 写清"
                "回落口径与披露内容")
        checker_names = _known_checkers(known_checkers)
        for index, check in enumerate(case.expect):
            name = str(check.get("checker") or "")
            if name not in checker_names:
                errors.append(f"{case.id}.expect[{index}]: 未登记的判定器 {name!r}")
            if case.verdict_kind == VERDICT_MECHANICAL and name in _proxy_checkers():
                errors.append(
                    f"{case.id}.expect[{index}]: 机械用例引用了代理判定器 {name!r}"
                    "（应把 verdict_kind 降级为 proxy 并披露）")

    size = LAYER_SIZES.get(case_set.layer)
    if require_layer_size and size is not None and len(case_set.cases) != size:
        errors.append(f"{case_set.layer} 规模契约: 期望 {size} 条，实际 {len(case_set.cases)} 条")

    counts = case_set.scenario_counts()
    if case_set.layer == LAYER_L0:
        for scenario in SCENARIOS:
            if counts.get(scenario, 0) < L0_MIN_PER_SCENARIO:
                errors.append(
                    f"L0 场景覆盖不足: {scenario} 需 ≥{L0_MIN_PER_SCENARIO} 条，"
                    f"实际 {counts.get(scenario, 0)} 条")
    if case_set.layer == LAYER_L2:
        for scenario in SEED_SCENARIOS:
            if counts.get(scenario, 0) < L2_MIN_PER_SEED:
                errors.append(
                    f"L2 种子场景覆盖不足: {scenario} 需 ≥{L2_MIN_PER_SEED} 条，"
                    f"实际 {counts.get(scenario, 0)} 条")
    return errors


def _known_checkers(known: Optional[Iterable[str]]) -> set:
    if known is not None:
        return set(known)
    import agent.eval.checkers as C
    return set(C.MECHANICAL_CHECKERS) | set(C.PROXY_CHECKERS)


def _proxy_checkers() -> set:
    import agent.eval.checkers as C
    return set(C.PROXY_CHECKERS)


__all__ = [
    "SCHEMA_NAME", "SCHEMA_VERSION", "LAYERS", "LAYER_L0", "LAYER_L1", "LAYER_L2",
    "LAYER_L3", "SCENARIOS", "SEED_SCENARIOS", "SCENARIO_S1_FIX_BUG",
    "SCENARIO_S2_CODEBASE_QA", "SCENARIO_S3_COMMIT", "SCENARIO_ROUTER",
    "SCENARIO_APPROVAL", "SCENARIO_ROLLBACK", "VERDICT_MECHANICAL",
    "VERDICT_PROXY", "VERDICT_UNSUPPORTED", "VERDICT_KINDS", "CASE_FIELDS",
    "CHECK_FIELDS", "LAYER_SIZES", "L0_MIN_PER_SCENARIO", "L2_MIN_PER_SEED",
    "CaseError", "CaseSchemaError", "CaseSetError", "EvalCase", "EvalCaseSet",
    "canonical_json", "sha256_text", "case_digest", "caseset_digest",
    "caseset_manifest_entries", "parse_case_set", "load_case_set", "dump_case_set",
    "validate_case_set",
]
