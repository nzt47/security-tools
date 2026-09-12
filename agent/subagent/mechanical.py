"""委派成功率的**机械信号清单**（TASK-S7-06 步骤 2 / 收官审计 §8.5 残留 R4）

## 背景（T7「成功率判定主体可靠性」）

S3-02 已确立「**机械优先 + LLM 兜底**」三层比对原则（结构 schema 硬性 → 副作用集合
硬性 → LLM-judge 仅软性 + 10% 人工抽检）。但**委派成功率**这一口径此前没有把该原则
**显式化**成可执行的信号清单（审计 T7 残留 R4）：`agent.eval` 的委派回收率只有一条
混合口径，读的人无法判断"这个成功是机械验出来的，还是 LLM 说它成功"。

本模块把清单**固化为代码**（不止是文档），并给出**优先级**与**适用条件**：

| 优先级 | 信号 | 判定物 | 适用条件（不满足 ⇒ 本条**不适用**，降级下一条） |
|---|---|---|---|
| ① | `artifact_structure` | 产物是否**结构性通过** `task_file` 声明的产物格式（schema / 必需字段） | 契约⑤产物格式是**机器可验**形态（dict/JSON schema），且给了产物 |
| ② | `test_transition` | 涉代码委派的**目标用例 fail→pass** | 调用方给出可执行测试证据（`before`/`after` 或失败/通过 id 集） |
| ③ | `side_effects` | **声明副作用 vs 实际副作用**集合一致（复用 S3-02 比对能力） | 给出声明集合 + 实际集合（或 S3-02 比对结果） |
| ④ | `replayability` | 产物**可重放复现**（同输入同结果） | 给出 ≥2 次重放的指纹或 `runs`/`identical` |
| ⑤ | `llm_review` | LLM 复评（**兜底**，必须带 `judge_kind` + 抽样人工校准） | ①-④ 均不适用时 |

## 五条硬纪律

1. **不适用 ≠ 通过**：证据缺失时信号只能"不适用"，由下一条接手；**绝不**在不适用时
   返回 pass（那是把"没验"说成"验过"）。
2. **机械优先**：①-④ 任一条适用即以它为准（`evaluate_mechanical()` 取**首个适用**者），
   LLM 复评**只在全部机械信号都不适用时**才被使用。
3. **`signal_kind` 必标**：结果分 ``mechanical`` / ``llm`` 两类，进 `CloudPivotReview`
   与 `Reflection`，并由 `agent.eval` 指标**分两列披露、严禁混算**。
4. **口径复用不另立**：副作用比对复用 S3-02 的 `SIDE_EFFECT_KINDS` 分类、
   `Observation.side_effect_set()` 形态归一与 `diff_side_effects()` 的集合匹配规则
   （具体值优先、`${path}` 形态容忍），本模块不重写一套匹配语义。
5. **不臆造数字**：每条信号的 ``evidence`` 只承载**调用方给出的实测证据**与判定过程，
   不补默认值、不估分。

**import 纪律**：`agent.digestion.*` 一律在**函数体内**懒加载（本模块被
`agent.subagent.collection` 在收集热路径上导入，不得在导入期拉起消化子系统）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger("agent.subagent.mechanical")

# ════════════════════════════════════════════════════════════
#  清单（唯一事实源：顺序即优先级）
# ════════════════════════════════════════════════════════════

SIGNAL_ARTIFACT_STRUCTURE = "artifact_structure"
SIGNAL_TEST_TRANSITION = "test_transition"
SIGNAL_SIDE_EFFECTS = "side_effects"
SIGNAL_REPLAYABILITY = "replayability"
SIGNAL_LLM_REVIEW = "llm_review"

#: 判定主体（两列口径；**严禁混算**）
KIND_MECHANICAL = "mechanical"
KIND_LLM = "llm"

#: 优先级顺序（高 → 低）；索引即优先级（0 为最高）
SIGNAL_PRIORITY: Tuple[str, ...] = (
    SIGNAL_ARTIFACT_STRUCTURE,
    SIGNAL_TEST_TRANSITION,
    SIGNAL_SIDE_EFFECTS,
    SIGNAL_REPLAYABILITY,
    SIGNAL_LLM_REVIEW,
)

#: 机械可验信号（①-④；优先使用）
MECHANICAL_SIGNALS: Tuple[str, ...] = SIGNAL_PRIORITY[:-1]
#: 兜底信号（⑤；仅在机械信号全不适用时使用）
LLM_SIGNALS: Tuple[str, ...] = (SIGNAL_LLM_REVIEW,)

#: ``judge_kind`` 取值（**与 S3-02 `shadow.JUDGE_KIND_*` 同词表**）
#: 此处复写常量而非导入：本模块被收集热路径导入，不得在导入期拉起消化子系统。
#: 词表一致性由回归测试兜住（`test_s7_06_mechanical_signals`。
JUDGE_KIND_MECHANICAL = "mechanical_signal"
JUDGE_KIND_LLM = "llm_judge"
JUDGE_KIND_LOCAL = "deterministic_local"
JUDGE_KIND_INJECTED = "injected"
JUDGE_KIND_LLM_FALLBACK = "deterministic_local(llm_unavailable)"
#: 机械列允许的 judge_kind（只有这一个）
JUDGE_KINDS_MECHANICAL: Tuple[str, ...] = (JUDGE_KIND_MECHANICAL,)


def kind_of(signal: str) -> str:
    """信号 → 判定主体（``mechanical`` / ``llm``；未知信号按兜底处理并如实标注）"""
    return KIND_MECHANICAL if str(signal or "") in MECHANICAL_SIGNALS else KIND_LLM


def priority_of(signal: str) -> int:
    """信号优先级（0 最高；未知信号 → ``len(SIGNAL_PRIORITY)``，即最低）"""
    try:
        return SIGNAL_PRIORITY.index(str(signal or ""))
    except ValueError:
        return len(SIGNAL_PRIORITY)


@dataclass(frozen=True)
class SignalSpec:
    """清单条目（定义 / 适用条件 / 通过条件 / 数据源 / 披露），供指标字典直接引用"""

    order: int
    signal: str
    kind: str
    name: str
    definition: str
    applies_when: str
    passed_when: str
    source: str
    disclosure: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "order": self.order, "signal": self.signal, "kind": self.kind,
            "name": self.name, "definition": self.definition,
            "applies_when": self.applies_when, "passed_when": self.passed_when,
            "source": self.source, "disclosure": self.disclosure,
        }


SIGNAL_SPECS: Tuple[SignalSpec, ...] = (
    SignalSpec(
        order=1, signal=SIGNAL_ARTIFACT_STRUCTURE, kind=KIND_MECHANICAL,
        name="产物结构",
        definition="产物是否**结构性通过** task_file ⑤产物的格式声明（schema / 必需字段）",
        applies_when=("契约⑤产物格式为**机器可验**形态（dict：``required_fields`` / "
                      "``schema``，或 JSON 对象串）**且**给出产物条目"),
        passed_when=("存在 ≥1 条非空产物，且每条产物都含全部必需字段、字段类型与 "
                     "schema 声明一致"),
        source="agent.subagent.delegation.DelegationContext.artifact_format（八要素⑤）+ "
               "执行结果的 artifacts",
        disclosure=("自然语言写的产物格式**不可机验** ⇒ 本条不适用（不做关键词猜测）；"
                    "适用时 schema 只做字段名/类型校验，**不做值语义判定**"),
    ),
    SignalSpec(
        order=2, signal=SIGNAL_TEST_TRANSITION, kind=KIND_MECHANICAL,
        name="测试/校验（fail→pass）",
        definition="涉代码的委派：目标用例是否从 fail 变为 pass（可执行的直接证据）",
        applies_when=("给出测试证据：``{before, after}`` 或 ``{failing_ids, passed_ids}``"),
        passed_when="``after == pass``（逐号形态要求失败 id 集全部进入通过 id 集）",
        source="调用方/执行器的**真实测试运行**证据（不入库则不适用）",
        disclosure=("``before == pass`` 时如实标注 `strict_fail_to_pass=False`"
                    "（起始即通过 ⇒ 非 fail→pass 转变，证据强度降级但结论仍为机械可验）"),
    ),
    SignalSpec(
        order=3, signal=SIGNAL_SIDE_EFFECTS, kind=KIND_MECHANICAL,
        name="副作用核对",
        definition="**声明副作用 vs 实际副作用**集合是否一致",
        applies_when="给出声明集合 + 实际集合，或 S3-02 比对结果（`LayerResult` 形）",
        passed_when="声明集合逐条在实测集合中命中（**具体值优先、`${path}` 形态容忍**）",
        source="复用 S3-02：`agent.digestion.sandbox`（`SIDE_EFFECT_KINDS` / "
               "`Observation.side_effect_set()` / `diff_side_effects()` 集合匹配规则）",
        disclosure=("声明侧无内容指纹 ⇒ 内容指纹检查**不适用**（不当作通过），"
                    "仅在 evidence 中如实标注；目标集合比对始终执行"),
    ),
    SignalSpec(
        order=4, signal=SIGNAL_REPLAYABILITY, kind=KIND_MECHANICAL,
        name="可回放性",
        definition="产物能否**重放复现**（同一输入同一结果）",
        applies_when=("给出 ≥2 次重放指纹 ``{fingerprints: [...]}``，或 "
                      "``{runs, identical}``"),
        passed_when="≥2 次重放且指纹全同（``identical`` 为真且 ``runs >= 2``）",
        source="S3-02 回放沙箱的重放/`determinism_probe` 结果或等价调用方证据",
        disclosure="单次重放**不构成**可回放性证据（不适用，非通过）",
    ),
    SignalSpec(
        order=5, signal=SIGNAL_LLM_REVIEW, kind=KIND_LLM,
        name="LLM 复评（兜底）",
        definition="①-④ 均不适用时由云枢侧复评器给出的判定（ReflectionEngine / 规则复评）",
        applies_when="机械信号全部不适用（**唯一**触发条件）",
        passed_when="复评器 ``passed`` 为真（口径见 `agent.subagent.collection`）",
        source="`reflection_engine_reviewer` / 注入的复评器（S4-04）",
        disclosure=("**必须**标注 `judge_kind` 并抽样人工校准（沿用 S3-02 M1 口径："
                    "≥0.85 仅软性 + 10% 人工抽检）；LLM 判定**不得**与机械判定混算"),
    ),
)

SIGNAL_BY_NAME: Dict[str, SignalSpec] = {s.signal: s for s in SIGNAL_SPECS}


def mechanical_signal_catalog() -> List[Dict[str, Any]]:
    """清单的字典形式（写进 `agent.eval` 指标字典与面板，**文档与代码同源**）"""
    return [spec.to_dict() for spec in SIGNAL_SPECS]


# ════════════════════════════════════════════════════════════
#  结果模型
# ════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class SignalResult:
    """一条信号的判定结果（``applicable=False`` 表示**不适用**，不是通过也不是失败）"""

    signal: str = ""
    applicable: bool = False
    passed: bool = False
    kind: str = ""
    score: float = 0.0
    reasons: Tuple[str, ...] = ()
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "signal", str(self.signal or ""))
        object.__setattr__(self, "kind", self.kind or kind_of(self.signal))

    @property
    def priority(self) -> int:
        return priority_of(self.signal)

    @property
    def verdict(self) -> str:
        """pass / fail / not_applicable（**三态**，不把不适用混进失败或通过）"""
        if not self.applicable:
            return "not_applicable"
        return "pass" if self.passed else "fail"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "signal": self.signal, "kind": self.kind, "priority": self.priority,
            "applicable": bool(self.applicable), "passed": bool(self.passed),
            "verdict": self.verdict, "score": round(float(self.score), 4),
            "reasons": list(self.reasons), "evidence": dict(self.evidence),
        }


def _not_applicable(signal: str, reason: str, **evidence: Any) -> SignalResult:
    return SignalResult(signal=signal, applicable=False, passed=False,
                        reasons=(reason,), evidence=dict(evidence))


def _result(signal: str, passed: bool, reasons: Sequence[str],
            **evidence: Any) -> SignalResult:
    return SignalResult(signal=signal, applicable=True, passed=bool(passed),
                        score=1.0 if passed else 0.0, reasons=tuple(reasons),
                        evidence=dict(evidence))


# ════════════════════════════════════════════════════════════
#  ① 产物结构
# ════════════════════════════════════════════════════════════

#: schema 声明的类型名 → Python 类型判定（**宽松读法，严格结论**；未知名即不可判定）
_TYPE_NAMES: Dict[str, Tuple[type, ...]] = {
    "str": (str,), "string": (str,), "text": (str,),
    "int": (int,), "integer": (int,),
    "float": (float,), "number": (int, float),
    "bool": (bool,), "boolean": (bool,),
    "list": (list, tuple), "array": (list, tuple),
    "dict": (dict,), "object": (dict,), "map": (dict,),
}


def _as_mapping(value: Any) -> Optional[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        return value
    if isinstance(value, str) and value.strip().startswith("{"):
        try:
            parsed = json.loads(value)
        except ValueError:
            return None
        return parsed if isinstance(parsed, Mapping) else None
    return None


def _required_and_schema(declared: Any) -> Tuple[List[str], Dict[str, str], str]:
    """产物格式声明 → ``(必需字段, 字段类型 schema, 说明)``

    仅接受**机器可验**形态（dict / JSON 对象串）；自然语言声明返回空 ⇒ 本条不适用。
    """
    spec = _as_mapping(declared)
    if spec is None:
        return [], {}, "产物格式声明为自然语言（不可机验）"
    required: List[str] = []
    for key in ("required_fields", "required", "fields"):
        raw = spec.get(key)
        if isinstance(raw, (list, tuple, set, frozenset)):
            required = [str(x) for x in raw if str(x).strip()]
            break
    schema: Dict[str, str] = {}
    raw_schema = spec.get("schema") or spec.get("types")
    if isinstance(raw_schema, Mapping):
        schema = {str(k): str(v).strip().lower() for k, v in raw_schema.items()}
    if not required and not schema:
        return [], {}, ("产物格式声明为 dict 但既无 required_fields/required/fields "
                        "也无 schema/types ⇒ 不可机验")
    return required, schema, "机器可验声明"


def _type_matches(value: Any, declared: str) -> bool:
    """值是否符合 schema 声明的类型名（**宽松读法、严格结论**）

    ``bool`` 与 ``int`` 严格区分（Python 里 ``True`` 是 ``int``，但语义不同）：
    声明 ``int`` 而值是 ``True`` ⇒ **不通过**（那是把布尔当计数，语义错误）。
    """
    expected = _TYPE_NAMES.get(str(declared or "").strip().lower())
    if expected is None:
        return False
    if isinstance(value, bool):
        return bool in expected
    if bool in expected:
        return False
    return isinstance(value, expected)


def artifact_structure_signal(*, artifacts: Any = None,
                              artifact_format: Any = None) -> SignalResult:
    """信号①：产物是否**结构性通过** ``task_file`` ⑤产物格式声明（schema/必需字段）"""
    required, schema, note = _required_and_schema(artifact_format)
    if not required and not schema:
        return _not_applicable(
            SIGNAL_ARTIFACT_STRUCTURE,
            f"{note} ⇒ 本条**不适用**（不做关键词猜测；降级到下一条机械信号）",
            declared_format=str(artifact_format or "")[:200])
    items = [a for a in (artifacts or [])]
    if not items:
        return _result(SIGNAL_ARTIFACT_STRUCTURE, False,
                       ["产物格式已声明为可机验形态，但产物条目为空 ⇒ 结构性不通过"],
                       declared={"required_fields": required, "schema": schema},
                       artifacts=0)
    failures: List[Dict[str, Any]] = []
    for index, item in enumerate(items):
        row = item if isinstance(item, Mapping) else {"value": item}
        missing = [f for f in required if f not in row
                   or row.get(f) in (None, "", [], {})]
        mismatch = [f"{key}: 期望 {kind}，实际 {type(row.get(key)).__name__}"
                    for key, kind in schema.items()
                    if key in row and not _type_matches(row.get(key), kind)]
        if missing or mismatch:
            failures.append({"index": index, "missing": missing,
                             "type_mismatch": mismatch})
    passed = not failures
    reasons = ([f"产物 {len(items)} 条全部满足声明格式"
                f"（必需字段 {len(required)} / 类型约束 {len(schema)}）"] if passed else
               [f"产物结构性不通过：{len(failures)}/{len(items)} 条不合格",
                "；".join(f"#{f['index']} 缺 {f['missing']} 类型不符 {f['type_mismatch']}"
                          for f in failures[:3])])
    return _result(SIGNAL_ARTIFACT_STRUCTURE, passed, reasons,
                   declared={"required_fields": required, "schema": schema},
                   artifacts=len(items), failures=failures[:10])


# ════════════════════════════════════════════════════════════
#  ② 测试 fail→pass
# ════════════════════════════════════════════════════════════


def _norm_status(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in ("pass", "passed", "ok", "success", "green"):
        return "pass"
    if text in ("fail", "failed", "error", "red"):
        return "fail"
    return ""


def test_transition_signal(*, test_evidence: Any = None) -> SignalResult:
    """信号②：涉代码委派的**目标用例 fail→pass**（可执行的直接证据）"""
    data = _as_mapping(test_evidence)
    if data is None:
        return _not_applicable(SIGNAL_TEST_TRANSITION,
                               "未给出可执行测试证据 ⇒ 本条**不适用**")
    before = _norm_status(data.get("before"))
    after = _norm_status(data.get("after"))
    failing = data.get("failing_ids") or data.get("failed_ids")
    passing = data.get("passed_ids") or data.get("passing_ids")
    if before and after:
        strict = before == "fail" and after == "pass"
        reasons = (["目标用例 fail→pass（可执行直接证据）"] if strict else
                   [f"目标用例 before={before} after={after}"
                    + ("（起始即通过 ⇒ 非 fail→pass 转变，证据强度降级）"
                       if before == "pass" else "")])
        return _result(SIGNAL_TEST_TRANSITION, after == "pass", reasons,
                       mode="before_after", before=before, after=after,
                       strict_fail_to_pass=strict,
                       case_id=str(data.get("case_id") or ""))
    if isinstance(failing, (list, tuple, set, frozenset)) \
            and isinstance(passing, (list, tuple, set, frozenset)):
        failing_ids = {str(x) for x in failing if str(x).strip()}
        passed_ids = {str(x) for x in passing if str(x).strip()}
        if not failing_ids:
            return _not_applicable(
                SIGNAL_TEST_TRANSITION,
                "``failing_ids`` 为空 ⇒ 无 fail→pass 转变可判 ⇒ 本条**不适用**")
        remaining = sorted(failing_ids - passed_ids)
        return _result(SIGNAL_TEST_TRANSITION, not remaining,
                       ([f"{len(failing_ids)} 个失败用例全部转为通过"] if not remaining
                        else [f"仍有 {len(remaining)} 个用例未转通过: {remaining[:5]}"]),
                       mode="id_sets", failing_ids=sorted(failing_ids),
                       passed_ids=sorted(passed_ids), still_failing=remaining[:10],
                       strict_fail_to_pass=True)
    return _not_applicable(
        SIGNAL_TEST_TRANSITION,
        "测试证据形态不可识别（需 ``{before, after}`` 或 ``{failing_ids, passed_ids}``）"
        " ⇒ 本条**不适用**")


# ════════════════════════════════════════════════════════════
#  ③ 副作用核对（复用 S3-02 比对能力）
# ════════════════════════════════════════════════════════════


def _side_effect_sets(raw: Any) -> Optional[Dict[str, List[str]]]:
    """副作用集合归一到 ``SIDE_EFFECT_KINDS`` 四类（**只接受映射形态**）

    非映射（如裸列表）**不猜类别** ⇒ 返回 ``None``（调用方判为"不适用"）。
    归一后仍交由 S3-02 `diff_side_effects()` 做匹配（具体值优先、形态容忍），
    本模块不另立匹配语义。
    """
    from agent.digestion.cases import SIDE_EFFECT_KINDS
    if raw is None:
        return {k: [] for k in SIDE_EFFECT_KINDS}
    if not isinstance(raw, Mapping):
        return None
    out: Dict[str, List[str]] = {}
    for kind in SIDE_EFFECT_KINDS:
        values = raw.get(kind) or []
        if isinstance(values, str):
            values = [values]
        elif isinstance(values, (set, frozenset)):
            values = sorted(values, key=str)
        try:
            out[kind] = sorted({str(v) for v in values if str(v).strip()})
        except TypeError:
            return None
    return out


def side_effects_signal(*, declared: Any = None, actual: Any = None,
                        side_effect_diff: Any = None) -> SignalResult:
    """信号③：**声明副作用 vs 实际副作用**集合一致性（复用 S3-02 `diff_side_effects`）

    两种输入形态：

    - ``side_effect_diff``：调用方已用 S3-02 `diff_side_effects()` 对**双跑两臂**算好的
      结果（``LayerResult`` 或 ``to_dict()``）—— 直接采信其 ``passed``（**不重算、不改判**）；
    - ``declared`` + ``actual``：把声明集合放进 S3-02 的**用例契约位**
      （``expected_side_effects``），实测集合放进候选臂 —— 于是复用 S3-02 的
      `_sets_match` 语义（**具体值优先、`${path}` 形态容忍**），本模块不另立匹配规则。

    **诚实标注**：形态二下"上游臂 vs 候选臂"与"内容指纹"两项检查在委派场景**无第二臂**、
    故**不适用**（同一实测集合自比只会恒真）；`evidence` 中逐项标 ``not_applicable``，
    **绝不当作通过**。判定只依赖契约层（声明 vs 实际）。
    """
    if side_effect_diff is not None:
        payload = side_effect_diff
        if hasattr(payload, "to_dict"):
            try:
                payload = payload.to_dict()
            except Exception as e:  # noqa: BLE001
                logger.warning("[Signal] S3-02 比对结果不可读: %s", e)
                return _not_applicable(SIGNAL_SIDE_EFFECTS,
                                       f"S3-02 比对结果不可读 ⇒ 本条不适用（{e}）")
        data = dict(payload) if isinstance(payload, Mapping) else {}
        if "passed" not in data:
            return _not_applicable(SIGNAL_SIDE_EFFECTS,
                                   "S3-02 比对结果缺 ``passed`` ⇒ 本条**不适用**")
        passed = bool(data.get("passed"))
        reasons = list(data.get("reasons") or [])[:5]
        return _result(SIGNAL_SIDE_EFFECTS, passed,
                       reasons or ["采信 S3-02 三层比对之副作用层结果"],
                       mode="replay_sandbox_diff", layer=str(data.get("layer") or ""),
                       arms_check="applicable", content_check="applicable",
                       contract_check="applicable")
    if declared is None or actual is None:
        return _not_applicable(
            SIGNAL_SIDE_EFFECTS,
            "未同时给出声明副作用与实际副作用（或 S3-02 比对结果）⇒ 本条**不适用**")
    try:
        from agent.digestion.sandbox import Observation, diff_side_effects
    except Exception as e:  # noqa: BLE001 复用件不可用 ⇒ 不适用而非失败
        return _not_applicable(SIGNAL_SIDE_EFFECTS,
                               f"S3-02 比对能力不可用（{type(e).__name__}: {e}）"
                               " ⇒ 本条**不适用**（不自行重写一套匹配语义）")
    expect = _side_effect_sets(declared)
    observed = _side_effect_sets(actual)
    if expect is None or observed is None:
        return _not_applicable(
            SIGNAL_SIDE_EFFECTS,
            "副作用集合不是 ``{kind: [值]}`` 映射形态（**不猜类别**）⇒ 本条**不适用**")

    class _Contract:
        """最小契约桩（`diff_side_effects` 只读这两个属性）"""

        side_effects_source = "authored"

        def __init__(self, expected: Dict[str, List[str]]) -> None:
            self.expected_side_effects = expected

    # 候选臂 = 实测；上游臂同为实测（无第二臂）—— 判定实际落在契约层
    arm = Observation(implementation="actual", side_effects=observed,
                      env_snapshot={"digests": []})
    # 契约桩按鸭子类型传入（`diff_side_effects` 只读 `side_effects_source` 与
    # `expected_side_effects` 两个属性）；此处标 Any 以表达"结构性契约"而非完整用例
    contract: Any = _Contract(expect)
    try:
        payload = diff_side_effects(arm, arm, contract).to_dict()
    except Exception as e:  # noqa: BLE001
        return _not_applicable(SIGNAL_SIDE_EFFECTS,
                               f"S3-02 比对执行失败（{type(e).__name__}: {e}）"
                               " ⇒ 本条**不适用**")
    detail = dict(payload.get("detail") or {})
    passed = bool(payload.get("passed"))
    return _result(SIGNAL_SIDE_EFFECTS, passed,
                   list(payload.get("reasons") or [])[:5] or
                   ["声明副作用与实际副作用集合一致（S3-02 契约匹配：具体值优先、"
                    "`${path}` 形态容忍）"],
                   mode="declared_contract",
                   declared=expect, actual=observed,
                   contract_check="applicable",
                   arms_check="not_applicable",
                   content_check="not_applicable",
                   layer=str(payload.get("layer") or ""),
                   arms_note=("委派场景无第二臂：`targets_ok`/`content_ok` 由同一实测集合"
                              "自比恒真 ⇒ **不当作独立证据**，已标 not_applicable"),
                   targets_ok=detail.get("targets_ok"),
                   content_ok=detail.get("content_ok"))


# ════════════════════════════════════════════════════════════
#  ④ 可回放性
# ════════════════════════════════════════════════════════════


def replayability_signal(*, replay_evidence: Any = None) -> SignalResult:
    """信号④：产物**可重放复现**（同一输入同一结果）"""
    data = _as_mapping(replay_evidence)
    if data is None:
        return _not_applicable(SIGNAL_REPLAYABILITY, "未给出重放证据 ⇒ 本条**不适用**")
    fingerprints = data.get("fingerprints")
    if isinstance(fingerprints, (list, tuple)):
        values = [str(f) for f in fingerprints if str(f or "").strip()]
        if len(values) < 2:
            return _not_applicable(
                SIGNAL_REPLAYABILITY,
                f"仅 {len(values)} 次重放 ⇒ 不构成可回放性证据（**不适用**，非通过）")
        identical = len(set(values)) == 1
        return _result(SIGNAL_REPLAYABILITY, identical,
                       ([f"{len(values)} 次重放指纹全同（{values[0]}）"] if identical else
                        [f"{len(values)} 次重放指纹不一致: {sorted(set(values))[:3]}"
                         " ⇒ 不可复现"]),
                       mode="fingerprints", runs=len(values),
                       fingerprints=values[:5], case_id=str(data.get("case_id") or ""))
    runs = data.get("runs")
    identical_flag = data.get("identical")
    if runs is None or identical_flag is None:
        return _not_applicable(
            SIGNAL_REPLAYABILITY,
            "重放证据形态不可识别（需 ``{fingerprints}`` 或 ``{runs, identical}``）"
            " ⇒ 本条**不适用**")
    try:
        count = int(runs)
    except (TypeError, ValueError):
        return _not_applicable(SIGNAL_REPLAYABILITY,
                               f"``runs`` 非法（{runs!r}）⇒ 本条**不适用**")
    if count < 2:
        return _not_applicable(
            SIGNAL_REPLAYABILITY,
            f"重放次数 {count} < 2 ⇒ 不构成可回放性证据（**不适用**，非通过）")
    passed = bool(identical_flag)
    return _result(SIGNAL_REPLAYABILITY, passed,
                   ([f"{count} 次重放结果一致"] if passed else
                    [f"{count} 次重放结果不一致 ⇒ 不可复现"]),
                   mode="runs_identical", runs=count, identical=passed,
                   case_id=str(data.get("case_id") or ""))


# ════════════════════════════════════════════════════════════
#  机械优先：取首个适用信号
# ════════════════════════════════════════════════════════════


def evaluate_mechanical(*, artifacts: Any = None, artifact_format: Any = None,
                        test_evidence: Any = None, declared_side_effects: Any = None,
                        actual_side_effects: Any = None, side_effect_diff: Any = None,
                        replay_evidence: Any = None) -> Optional[SignalResult]:
    """按优先级取**首个适用**的机械信号（①→④）；全不适用返回 ``None``（→ 走 LLM 兜底）

    返回 ``None`` 的语义是「无机械证据可用」，**不是**「机械判定失败」；调用方据此
    才可启用 LLM 复评（`agent.subagent.collection`）。
    """
    candidates = (
        artifact_structure_signal(artifacts=artifacts,
                                  artifact_format=artifact_format),
        test_transition_signal(test_evidence=test_evidence),
        side_effects_signal(declared=declared_side_effects, actual=actual_side_effects,
                            side_effect_diff=side_effect_diff),
        replayability_signal(replay_evidence=replay_evidence),
    )
    for item in candidates:
        if item.applicable:
            return item
    return None


def mechanical_evidence(**kwargs: Any) -> Dict[str, Any]:
    """机械信号的**全量**试算（供报告/面板取证：逐条给 verdict，含 ``not_applicable``）

    与 `evaluate_mechanical()` 的区别：本函数**不早停**，返回四条逐条结果 + 首个适用者，
    便于验收报告展示"为什么这一条没被采用"。
    """
    results = [
        artifact_structure_signal(artifacts=kwargs.get("artifacts"),
                                  artifact_format=kwargs.get("artifact_format")),
        test_transition_signal(test_evidence=kwargs.get("test_evidence")),
        side_effects_signal(declared=kwargs.get("declared_side_effects"),
                            actual=kwargs.get("actual_side_effects"),
                            side_effect_diff=kwargs.get("side_effect_diff")),
        replayability_signal(replay_evidence=kwargs.get("replay_evidence")),
    ]
    chosen = next((r for r in results if r.applicable), None)
    return {
        "signals": [r.to_dict() for r in results],
        "chosen": (chosen.signal if chosen is not None else ""),
        "chosen_kind": (chosen.kind if chosen is not None else ""),
        "mechanical_available": chosen is not None,
        "catalog": mechanical_signal_catalog(),
    }


__all__ = [
    "SIGNAL_ARTIFACT_STRUCTURE", "SIGNAL_TEST_TRANSITION", "SIGNAL_SIDE_EFFECTS",
    "SIGNAL_REPLAYABILITY", "SIGNAL_LLM_REVIEW", "SIGNAL_PRIORITY",
    "MECHANICAL_SIGNALS", "LLM_SIGNALS", "KIND_MECHANICAL", "KIND_LLM",
    "JUDGE_KIND_MECHANICAL", "JUDGE_KIND_LLM", "JUDGE_KIND_LOCAL",
    "JUDGE_KIND_INJECTED", "JUDGE_KIND_LLM_FALLBACK", "JUDGE_KINDS_MECHANICAL",
    "kind_of", "priority_of", "SignalSpec", "SIGNAL_SPECS", "SIGNAL_BY_NAME",
    "mechanical_signal_catalog", "SignalResult",
    "artifact_structure_signal", "test_transition_signal", "side_effects_signal",
    "replayability_signal", "evaluate_mechanical", "mechanical_evidence",
]
