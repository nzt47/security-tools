"""OPA 子集等效判定器（§5.6 ``match: {<OPA 子集>}``）

【为什么不是真的 OPA】
    P4 架构裁定（TASK-S4-02 §一.3）：单机不强制引入 OPA/WASM，改用**等效声明式
    策略引擎**——但**接口形状必须兼容未来替换 OPA**（``PolicyEngine.check(ctx)
    -> {allow|deny|ask, policy_id}``）。因此本模块把「表达式求值」与「策略装载/
    决策编排」彻底分开：将来换真 OPA 时，只需替换本模块（把 ``match`` 编译成
    预编译 WASM 或在进程内嵌 OPA），``store`` / ``engine`` / 执行点接线一行不动。

【支持子集（本模块的完整语义）】
    判定输入是 §5.6 的 ``input`` 文档（``PolicyContext.input``），叶子用**点分
    字段路径**寻址（``capability.trust.risk_level``）；允许带 ``input.`` 前缀，
    装载期归一化去掉。

    1) 叶子比较（field path comparison）
       ``{"field": "<路径>", "op": "<算子>", "value": <字面量>}``
       ``op`` 缺省为 ``eq``。算子全集：

       | op           | 语义                                   | value 形态        |
       |--------------|----------------------------------------|-------------------|
       | ``eq``       | 相等（int/float 数值等价；``True != 1``）| 标量 / list / dict |
       | ``ne``       | 不等（缺失路径 ⇒ True）                 | 同上              |
       | ``in``       | 集合成员：字段值 ∈ value                | **list**          |
       | ``not_in``   | 集合非成员（缺失路径 ⇒ True）           | **list**          |
       | ``contains`` | 字段（str/list）包含 value              | 标量              |
       | ``startswith`` / ``endswith`` | 字符串前后缀（字段必须为 str）| str       |
       | ``glob``     | fnmatch 通配（字段转 str）              | str               |
       | ``gt``/``gte``/``lt``/``lte`` | 数值/字符串序比较       | 标量              |
       | ``exists``   | 路径存在（value 必须省略）              | —                 |
       | ``not_exists`` | 路径不存在                            | —                 |

    2) 集合成员（set membership）
       即 ``in`` / ``not_in``。**不**支持「集合里套集合」的传递成员判定
       （Rego 里也要显式展开），只做一层 ``value`` 列表包含。

    3) 布尔组合（boolean combination）
       ``{"all": [<expr>, ...]}`` / ``{"any": [...]}``（别名 ``and`` / ``or``）、
       ``{"not": <expr>}``。可任意嵌套。空 ``all`` ⇒ True（恒真），空 ``any`` ⇒ False。

    4) 简写映射（shorthand map）
       一个 dict 里没有 ``all``/``any``/``not`` 保留键时，其余每个键都是**字段
       路径**，值为字面量（等价 ``eq``）或**单算子 dict**（``{"in": [...]}``）。
       一个 dict 内所有简写项按 **AND** 组合；保留键与外层简写项也按 AND 组合：

           {"capability.trust.risk_level": "destructive",
            "target.external": true,
            "all": [{"field": "tenant.id", "op": "in", "value": ["t1", "t2"]}]}

       等价于三者取 AND。该形态是为「策略作者可读」而设——§5.6 的 Rego 例子
       本身就是并列条件（AND），简写映射是它在 JSON 下的直译。

【明确不支持（文档化义务，§二.4）】
    下面这些**不是**「暂未实现」，而是本引擎的**设计边界**。命中任一项时装载期
    直接报错（``validate_match`` 返回可读原因），绝不静默当作 False——静默失真
    会变成「策略以为在拦、实际没拦」的假安全。

    | 不支持                                        | 原因 / 替代做法                        |
    |----------------------------------------------|---------------------------------------|
    | 用户自定义规则/函数（``deny[msg] { ... }``）    | 无 Rego 编译器；改用多条 Policy，按       |
    |                                              | 「首个命中生效」+ ``effective_range`` 分层 |
    | comprehension（``[x | x := ...]``）、``some``/``every`` 迭代 | 需要循环语义；改用 ``in`` + 显式集合 |
    | ``data.*`` 文档查找、``import``                 | 引擎无外部文档空间（无隐式 I/O）           |
    | ``with`` 覆盖、``json.patch``、``sprintf`` 等内建 | 同上，且语义与决策无关                |
    | 算术/字符串插值、数组下标路径（``a.b[0].c``）    | 路径只做 dict 逐段查询；需下标请改用 ``in`` |
    | 正则（``re_match`` / ``matches`` / ``regex``）   | **ReDoS 面**：策略文本可能由 LLM 生成，    |
    |                                              | 不给它一个可写灾难性回溯的口子。用          |
    |                                              | ``startswith``/``endswith``/``contains``/``glob`` |
    | 时间函数（``time.now_ns``）、随机、环境读取      | 破坏决策可重放性（模拟器需要同一输入同一结果）|
    | **任何网络/执行副作用**（``http.send`` 等）      | P7.1-20：本引擎只出决策。装载期 token 拦截  |

【求值纪律】
    - 纯函数：同一 ``input`` + 同一策略必得同一结果（模拟器可重放的前提）。
    - 无 I/O：本模块不 import 任何网络/子进程库（有 AST 用例守着）。
    - 缺失路径**不抛异常**：``eq`` 判 False、``ne``/``not_in``/``not_exists`` 判
      True（Rego 里未定义即不满足，取反亦然）。
    - 不可比（类型不匹配的 ``gt``、非 str 的 ``startswith``）：判 **False** 并
      计入 ``type_errors``，由调用方决定是否告警——**绝不**判 True。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from typing import Any, Dict, List, Optional, Tuple

from agent.policy.models import PolicyValidationError

# ════════════════════════════════════════════════════════════
#  算子表
# ════════════════════════════════════════════════════════════

#: 支持的全部算子（**值域纪律**：不在表内＝不支持，装载期报错）
OPS: Tuple[str, ...] = (
    "eq", "ne", "in", "not_in",
    "contains", "startswith", "endswith", "glob",
    "gt", "gte", "lt", "lte",
    "exists", "not_exists",
)

#: 无需 ``value`` 的算子
_NULLARY_OPS = ("exists", "not_exists")

#: 需要 ``value`` 为 list 的算子
_COLLECTION_OPS = ("in", "not_in")

#: 布尔组合保留键 → 规范名
_COMBINATOR_ALIASES: Dict[str, str] = {
    "all": "all", "and": "all",
    "any": "any", "or": "any",
    "not": "not",
}
_COMBINATORS: Tuple[str, ...] = ("all", "any", "not")

#: 叶子节点的保留键
_LEAF_KEYS: Tuple[str, ...] = ("field", "op", "value")

#: 明确不支持的算子名 → 原因（给出替代做法，便于策略作者自纠）
UNSUPPORTED_OPS: Dict[str, str] = {
    "regex": "不支持正则（ReDoS 面）；用 startswith/endswith/contains/glob",
    "matches": "不支持正则（ReDoS 面）；用 startswith/endswith/contains/glob",
    "re_match": "不支持正则（ReDoS 面）；用 startswith/endswith/contains/glob",
    "match": "不支持正则（ReDoS 面）；用 startswith/endswith/contains/glob",
    "walk": "不支持 walk；用 in + 显式集合",
    "count": "不支持 count；把判定上移到 capability 的聚合字段",
    "some": "不支持 some 迭代；用 in + 显式集合",
    "every": "不支持 every 迭代；用 in + 显式集合",
    "sprintf": "不支持字符串格式化；用 message_template",
    "now": "不支持时间函数（破坏决策可重放性）；用 effective_range",
    "time_now_ns": "不支持时间函数（破坏决策可重放性）；用 effective_range",
}

#: 明确不支持的字段路径前缀
UNSUPPORTED_PATH_PREFIXES: Dict[str, str] = {
    "data": "不支持 data.* 文档查找（引擎无外部文档空间）；用 attributes.*",
    "input.data": "不支持 data.* 文档查找（引擎无外部文档空间）；用 attributes.*",
}

_PATH_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
_INDEX_RE = re.compile(r"\[[^\]]*\]")

#: ``input.`` 前缀（Rego 里字段总写成 ``input.x.y``；装载期归一化去掉）
_INPUT_PREFIX = "input."


class _Missing:
    """缺失哨兵（与 ``None`` 区分：字段存在且值为 null ≠ 字段不存在）"""

    _instance: Optional["_Missing"] = None

    def __new__(cls) -> "_Missing":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return "<MISSING>"

    def __bool__(self) -> bool:
        return False


MISSING = _Missing()


# ════════════════════════════════════════════════════════════
#  路径
# ════════════════════════════════════════════════════════════


def normalize_path(path: Any, *, policy_id: Optional[str] = None) -> str:
    """归一化字段路径：去 ``input.`` 前缀、去首尾空白；非法形态直接报错

    非法＝空串 / 含下标 ``[`` / 含通配 ``*`` / 非点分标识符 / 命中不支持前缀。
    """
    raw = str(path or "").strip()
    if not raw:
        raise PolicyValidationError(["field 路径为空"], policy_id=policy_id,
                                    code="INVALID_MATCH_PATH")
    if raw.startswith(_INPUT_PREFIX):
        raw = raw[len(_INPUT_PREFIX):]
    if _INDEX_RE.search(raw):
        raise PolicyValidationError(
            [f"field 路径 {path!r} 含下标，不支持数组下标（改用 in + 显式集合）"],
            policy_id=policy_id, code="UNSUPPORTED_MATCH_PATH")
    if "*" in raw or "?" in raw:
        raise PolicyValidationError(
            [f"field 路径 {path!r} 含通配符，不支持通配寻址（用 glob 算子比较值）"],
            policy_id=policy_id, code="UNSUPPORTED_MATCH_PATH")
    if not _PATH_RE.match(raw):
        raise PolicyValidationError(
            [f"field 路径 {path!r} 形态非法（应为点分标识符，如 capability.trust.risk_level）"],
            policy_id=policy_id, code="INVALID_MATCH_PATH")
    reason = UNSUPPORTED_PATH_PREFIXES.get(raw) \
        or UNSUPPORTED_PATH_PREFIXES.get(raw.split(".", 1)[0])
    if reason:
        raise PolicyValidationError([f"field 路径 {path!r}: {reason}"],
                                    policy_id=policy_id,
                                    code="UNSUPPORTED_MATCH_PATH")
    return raw


def field_get(payload: Any, path: str) -> Any:
    """按点分路径取值；缺失返回 :data:`MISSING`

    只做 dict 逐段查询（**不含**属性访问、不含下标），因此输入必须是纯 JSON 值
    ——这同时也是「决策输入不可夹带 live 对象」的运行时兜底。
    """
    cur = payload
    for part in str(path or "").split("."):
        if not part:
            continue
        if isinstance(cur, dict):
            if part not in cur:
                return MISSING
            cur = cur[part]
        else:
            return MISSING
    return cur


# ════════════════════════════════════════════════════════════
#  比较语义
# ════════════════════════════════════════════════════════════


def _is_number(value: Any) -> bool:
    """数值（**排除 bool**：Python 里 ``True == 1``，策略语义上二者必须可区分）"""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _equal(left: Any, right: Any) -> bool:
    """等价比较：数值跨 int/float 等价；bool 只与 bool 相等；其余按 ``==``"""
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left == right
    if _is_number(left) and _is_number(right):
        return float(left) == float(right)
    if type(left) is not type(right):
        # 跨类型（str vs number）不等价——显式比较同一叶子时类型不一致属策略错误，
        # 但这里静默判 False 而非抛异常：决策路径必须能容错。
        if isinstance(left, (dict, list)) or isinstance(right, (dict, list)):
            return bool(left == right)
        return False
    return bool(left == right)


def _ordered(left: Any, right: Any) -> Optional[int]:
    """返回 -1/0/1；不可比返回 None（``bool`` 不参与序比较）"""
    if isinstance(left, bool) or isinstance(right, bool):
        return None
    if _is_number(left) and _is_number(right):
        lf, rf = float(left), float(right)
        return (lf > rf) - (lf < rf)
    if isinstance(left, str) and isinstance(right, str):
        return (left > right) - (left < right)
    return None


# ════════════════════════════════════════════════════════════
#  校验（装载期）
# ════════════════════════════════════════════════════════════


def _validate_leaf(node: Dict[str, Any], *, policy_id: Optional[str]) -> List[str]:
    errors: List[str] = []
    unknown = [k for k in node if k not in _LEAF_KEYS]
    if unknown:
        errors.append(f"叶子节点含未知键 {sorted(unknown)}（允许 {list(_LEAF_KEYS)}）")
    try:
        normalize_path(node.get("field"), policy_id=policy_id)
    except PolicyValidationError as exc:
        errors.extend(exc.errors)

    op = str(node.get("op", "eq") or "eq").strip().lower()
    if op in UNSUPPORTED_OPS:
        errors.append(f"算子 {op!r} 不支持：{UNSUPPORTED_OPS[op]}")
    elif op not in OPS:
        errors.append(f"算子 {op!r} 不支持（支持 {list(OPS)}）")

    if op in _NULLARY_OPS:
        if "value" in node:
            errors.append(f"算子 {op!r} 不接受 value（存在性判定只看路径）")
    elif "value" not in node:
        errors.append(f"算子 {op!r} 需要 value")
    elif op in _COLLECTION_OPS and not isinstance(node.get("value"), (list, tuple)):
        errors.append(f"算子 {op!r} 的 value 必须为 list（集合成员判定），"
                      f"got {type(node.get('value')).__name__}")

    for op_name in ("startswith", "endswith", "glob"):
        if op == op_name and not isinstance(node.get("value"), str):
            errors.append(f"算子 {op_name!r} 的 value 必须为 str")
    return errors


def _validate_shorthand_item(path: Any, spec: Any, *, policy_id: Optional[str]) -> List[str]:
    errors: List[str] = []
    try:
        normalize_path(path, policy_id=policy_id)
    except PolicyValidationError as exc:
        errors.extend(exc.errors)
    if isinstance(spec, dict):
        unknown = [k for k in spec if k not in OPS]
        if not unknown and len(spec) == 1:
            op = next(iter(spec))
            errors.extend(_validate_leaf({"field": path, "op": op,
                                          "value": spec[op]}, policy_id=policy_id))
        else:
            bad = [k for k in spec if k in UNSUPPORTED_OPS]
            if bad:
                for name in bad:
                    errors.append(f"算子 {name!r} 不支持：{UNSUPPORTED_OPS[name]}")
            else:
                errors.append(
                    f"简写项 {path!r} 的 dict 必须恰含一个支持算子"
                    f"（{list(OPS)}），got {sorted(spec)}")
    return errors


def validate_match(node: Any, *, policy_id: Optional[str] = None,
                   _depth: int = 0) -> List[str]:
    """静态校验 ``match`` 子树；返回人类可读错误清单（空＝合法）

    只做结构与算子校验，不评估任何输入——用于**装载期**拦截非法/不支持语法。
    """
    errors: List[str] = []
    if _depth > 16:
        return ["match 嵌套过深（>16 层），疑似生成错误"]

    if isinstance(node, bool) or node is None:
        return ["match 不能是布尔/null；空匹配请用 {}（恒真）或显式条件"]

    if isinstance(node, list):
        return [f"match 不能是数组（got 长度 {len(node)}）；组合请用 {{\"all\": [...]}} "
                "或 {\"any\": [...]}"]

    if not isinstance(node, dict):
        return [f"match 节点应为 dict，got {type(node).__name__}"]

    if not node:
        return []  # {} ⇒ 恒真（显式声明的「全命中」，由 store 决定是否允许）

    if "field" in node:
        return _validate_leaf(node, policy_id=policy_id)

    combinator_keys = [k for k in node if k in _COMBINATOR_ALIASES]
    shorthand_keys = [k for k in node
                      if k not in _COMBINATOR_ALIASES and k not in _LEAF_KEYS]

    if not combinator_keys and not shorthand_keys:
        return [f"match 节点无可识别内容 {sorted(node)}；"
                "应为 {field, op, value} / {all|any|not} / 字段路径简写"]

    for key in combinator_keys:
        canonical = _COMBINATOR_ALIASES[key]
        child = node[key]
        if canonical in ("all", "any"):
            if not isinstance(child, list):
                errors.append(f"{key!r} 的值必须为数组，got {type(child).__name__}")
                continue
            for index, item in enumerate(child):
                errors.extend(_validate_match_prefixed(
                    item, f"{key}[{index}]", policy_id=policy_id, depth=_depth + 1))
        else:  # not
            errors.extend(_validate_match_prefixed(
                child, key, policy_id=policy_id, depth=_depth + 1))

    for key in shorthand_keys:
        errors.extend(_validate_shorthand_item(key, node[key], policy_id=policy_id))

    return errors


def _validate_match_prefixed(node: Any, prefix: str, *, policy_id: Optional[str],
                             depth: int) -> List[str]:
    return [f"{prefix}: {err}" for err in
            validate_match(node, policy_id=policy_id, _depth=depth)]


def support_matrix() -> Dict[str, Any]:
    """支持/不支持语法清单（**供文档与门禁自检**，避免文档与实现漂移）

    返回结构直接用于 ``MATCH_SUBSET.md`` 的生成与单测断言：文档里写的算子集合
    必须等于 ``OPS``，不支持清单必须等于 ``UNSUPPORTED_OPS``/``UNSUPPORTED_PATH_PREFIXES``。
    """
    return {
        "supported_ops": list(OPS),
        "supported_combinators": list(_COMBINATORS),
        "supported_aliases": dict(_COMBINATOR_ALIASES),
        "supported_literals": ["null", "bool", "number", "string", "list", "dict"],
        "nullary_ops": list(_NULLARY_OPS),
        "collection_ops": list(_COLLECTION_OPS),
        "supported_shorthand": True,
        "unsupported_ops": dict(UNSUPPORTED_OPS),
        "unsupported_path_prefixes": dict(UNSUPPORTED_PATH_PREFIXES),
        "unsupported_constructs": [
            "用户自定义规则/函数（deny[msg] { ... }）",
            "comprehension / some / every 迭代",
            "data.* 文档查找 / import",
            "with 覆盖 / json.patch / sprintf 等内建",
            "算术与字符串插值",
            "数组下标路径 a.b[0].c",
            "正则（re_match / matches / regex）",
            "时间函数 / 随机 / 环境读取",
            "任何网络或执行副作用（http.send 等，P7.1-20）",
        ],
    }


# ════════════════════════════════════════════════════════════
#  求值
# ════════════════════════════════════════════════════════════


@dataclass
class MatchResult:
    """一次 ``match`` 求值的结果（**判定与诊断都要留痕**）

    Attributes:
        matched: 是否命中。
        leaves: 求值过的叶子数（诊断「策略太宽/太窄」用）。
        type_errors: 不可比/类型不符的叶子清单（判定为 False，但不静默）。
        missing_fields: 求值中缺失的字段路径（去重，诊断键名写错）。
    """

    matched: bool
    leaves: int = 0
    type_errors: List[str] = field(default_factory=list)
    missing_fields: List[str] = field(default_factory=list)

    def __bool__(self) -> bool:  # 便于 `if result:`
        return self.matched


class MatchEvaluator:
    """``match`` 求值器（**无状态、纯函数**；实例只为携带诊断累加器）

    一次 ``evaluate`` 用**一个**实例（内部累加 ``leaves``/``type_errors``/
    ``missing_fields``）；实例不是线程安全的，引擎按调用新建。
    """

    def __init__(self) -> None:
        self.leaves = 0
        self.type_errors: List[str] = []
        self.missing_fields: List[str] = []

    # ── 主入口 ──

    def evaluate(self, node: Any, payload: Any) -> bool:
        """求值；``payload`` 是 ``PolicyContext.input``（纯 JSON dict）"""
        if isinstance(node, dict) and not node:
            return True  # {} ⇒ 恒真
        return bool(self._eval(node, payload))

    def result(self, matched: bool) -> MatchResult:
        seen: List[str] = []
        for name in self.missing_fields:
            if name not in seen:
                seen.append(name)
        return MatchResult(matched=matched, leaves=self.leaves,
                           type_errors=list(self.type_errors),
                           missing_fields=seen)

    # ── 内部 ──

    def _eval(self, node: Any, payload: Any) -> bool:
        if isinstance(node, dict) and not node:
            return True
        if not isinstance(node, dict):
            raise PolicyValidationError(
                [f"match 节点应为 dict，got {type(node).__name__}（装载期应已拦截）"])

        if "field" in node:
            return self._eval_leaf(node, payload)

        combinator_keys = [k for k in node if k in _COMBINATOR_ALIASES]
        shorthand_keys = [k for k in node
                          if k not in _COMBINATOR_ALIASES and k not in _LEAF_KEYS]

        # 简写项与外层保留键按 AND 组合
        for key in shorthand_keys:
            if not self._eval_shorthand(key, node[key], payload):
                return False

        for key in combinator_keys:
            canonical = _COMBINATOR_ALIASES[key]
            child = node[key]
            if canonical == "all":
                if not all(self._eval(item, payload) for item in child):
                    return False
            elif canonical == "any":
                if not any(self._eval(item, payload) for item in child):
                    return False
            else:  # not
                if self._eval(child, payload):
                    return False
        return True

    def _resolve(self, path: str, payload: Any) -> Any:
        value = field_get(payload, path)
        if value is MISSING:
            self.missing_fields.append(path)
        return value

    def _eval_leaf(self, node: Dict[str, Any], payload: Any) -> bool:
        path = normalize_path(node.get("field"))
        op = str(node.get("op", "eq") or "eq").strip().lower()
        self.leaves += 1
        actual = self._resolve(path, payload)
        present = actual is not MISSING
        expected = node.get("value", MISSING)

        if op == "exists":
            return present
        if op == "not_exists":
            return not present

        if op == "eq":
            return present and _equal(actual, expected)
        if op == "ne":
            return (not present) or (not _equal(actual, expected))
        if op == "in":
            if not present:
                return False
            return any(_equal(actual, item) for item in (expected or ()))
        if op == "not_in":
            if not present:
                return True
            return not any(_equal(actual, item) for item in (expected or ()))
        if op == "contains":
            if not present:
                return False
            if isinstance(actual, str):
                return isinstance(expected, str) and expected in actual
            if isinstance(actual, (list, tuple)):
                return any(_equal(item, expected) for item in actual)
            self.type_errors.append(f"{path}: contains 需要 str/list，got "
                                    f"{type(actual).__name__}")
            return False
        if op in ("startswith", "endswith", "glob"):
            if not present:
                return False
            if op == "glob":
                return fnmatchcase(str(actual), str(expected))
            if not isinstance(actual, str):
                self.type_errors.append(f"{path}: {op} 需要 str，got "
                                        f"{type(actual).__name__}")
                return False
            if not isinstance(expected, str):
                self.type_errors.append(f"{path}: {op} 的 value 需要 str")
                return False
            return actual.startswith(expected) if op == "startswith" \
                else actual.endswith(expected)
        if op in ("gt", "gte", "lt", "lte"):
            if not present:
                return False
            order = _ordered(actual, expected)
            if order is None:
                self.type_errors.append(
                    f"{path}: {op} 两端不可比（{type(actual).__name__} vs "
                    f"{type(expected).__name__}）")
                return False
            return {"gt": order > 0, "gte": order >= 0,
                    "lt": order < 0, "lte": order <= 0}[op]

        raise PolicyValidationError([f"算子 {op!r} 不支持（装载期应已拦截）"])

    def _eval_shorthand(self, path: str, spec: Any, payload: Any) -> bool:
        if isinstance(spec, dict):
            if len(spec) != 1:
                raise PolicyValidationError(
                    [f"简写项 {path!r} 的 dict 必须恰含一个算子"])
            op, value = next(iter(spec.items()))
            node: Dict[str, Any] = {"field": path, "op": op}
            if op not in _NULLARY_OPS:
                node["value"] = value
            return self._eval_leaf(node, payload)
        return self._eval_leaf({"field": path, "op": "eq", "value": spec}, payload)


# ════════════════════════════════════════════════════════════
#  便捷门面
# ════════════════════════════════════════════════════════════


def match(policy_match: Any, payload: Any) -> MatchResult:
    """单次求值门面（引擎调用它；模拟器/测试也用它）"""
    evaluator = MatchEvaluator()
    matched = evaluator.evaluate(policy_match, payload)
    return evaluator.result(matched)


def reject_unsupported(node: Any, *, policy_id: Optional[str] = None) -> None:
    """校验并在非法时抛 :class:`PolicyValidationError`（装载期用）"""
    errors = validate_match(node, policy_id=policy_id)
    if errors:
        raise PolicyValidationError(errors, policy_id=policy_id,
                                    code="INVALID_MATCH")


__all__ = [
    "OPS", "UNSUPPORTED_OPS", "UNSUPPORTED_PATH_PREFIXES", "MISSING",
    "MatchResult", "MatchEvaluator",
    "normalize_path", "field_get", "validate_match", "support_matrix",
    "match", "reject_unsupported",
]
