"""被测解算器（solver）：把"用例"变成"答案工件"（TASK-S5-02）

评测执行器（`agent.eval.runner`）不关心答案从哪来，只关心两件事：
**① 答案工件是纯 JSON；② 解算过程可复现**。本模块提供四种解算器：

======================  ============================================================
解算器                   用途
======================  ============================================================
``reference``          读锚内参考解 → **判定器自检**（管道通不通）。**不代表模型能力**
``static``             由外部给定 ``{case_id: 答案}`` → 回归/快照对比
``mutant``             **参数感知**的确定性破坏 → 证明每条判定条目**有区分度**（负样本）
``null``               永远返回 ``None`` → 表达"本环境无法评测"（**如实标注**，不计通过）
======================  ============================================================

## 变异解为什么是"参数感知"的

只把参考解随机改一改，无法证明"每条判定条目都在干活"（把判定器写成"永远 True"
也可能躲过随机变异）。因此 `break_answer()` **读取判定条目自己的参数**，构造一个
**保证违反该条目**的取值：

* ``equals`` → 期望值加扰（bool 取反 / 数值 +1 / 字符串追加哨兵）
* ``contains_all`` → 从原文里**删掉**一个必需片段；``not_contains`` → **追加**一个禁止片段
* ``list_ordered`` / ``list_set_equals`` / ``reverse_of`` → 长度或顺序改坏
* ``numeric_between`` / ``length_between`` → 推到区间外
* ``path_exists`` / ``symbols_exist`` → 换成**不存在**的路径/符号（反幻觉路径的负例）
* ``commit_message`` → 换成不合规的提交信息
* ``python_probes`` → 清空代码（不可执行）

于是"**任一**判定条目被破坏 → 该用例必须判 fail"成为可逐条断言的不变量
（`test_eval_runner.py` 对全部用例 × 全部判定条目做负样本对照）。

**为什么需要 null**：本环境可能没有真实 LLM 凭证（任务书 §八 上游已知坑 #4），
此时真实解算器无法产出答案 —— 那就如实返回"未评测"，绝不用参考解冒充模型成绩。
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import agent.eval.checkers as K
from agent.eval.cases import EvalCase

#: 解算器签名：用例 → 答案工件（`None` 表示"无法评测"，`{}` 表示"空答案"=不合格）
Solver = Callable[[EvalCase], Optional[Dict[str, Any]]]

SOLVER_REFERENCE = "reference"
SOLVER_STATIC = "static"
SOLVER_MUTANT = "mutant"
SOLVER_NULL = "null"

#: 变异模式：破坏第一条判定 / 破坏全部判定
MODE_FIRST_CHECK = "first_check"
MODE_ALL_CHECKS = "all_checks"
MODES: Tuple[str, ...] = (MODE_FIRST_CHECK, MODE_ALL_CHECKS)

#: 哨兵（保证"不可能碰巧命中"的取值）
MUTANT_MARK = "__eval_mutant__"
MISSING_PATH = "definitely/missing/__eval_mutant__.py"
#: 反幻觉负例符号：**内嵌 NUL** —— 文本文件里不可能出现该字节序列，因此
#: "仓库内是否存在该符号"必然为假（纯字符串会被本模块自身源码命中）
MISSING_SYMBOL = "NoSuchSymbol\x00__eval_mutant__"


def _stable_index(text: str, modulo: int) -> int:
    if modulo <= 0:
        return 0
    digest = hashlib.sha256(str(text).encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % modulo


# ════════════════════════════════════════════════════════════
#  基础解算器
# ════════════════════════════════════════════════════════════


def reference_solver(answers: Mapping[str, Mapping[str, Any]],
                     *, name: str = "reference") -> Tuple[Solver, str]:
    """参考解解算器：``answers`` 缺该用例时返回 `None`（未评测，不冒充通过）"""
    table = {str(k): dict(v) for k, v in (answers or {}).items()}

    def solve(case: EvalCase) -> Optional[Dict[str, Any]]:
        answer = table.get(case.id)
        return copy.deepcopy(dict(answer)) if answer is not None else None

    return solve, name


def static_solver(answers: Mapping[str, Mapping[str, Any]],
                  *, name: str = "static") -> Tuple[Solver, str]:
    """静态答案解算器（外部给定答案 → 回归/快照对比；缺失 → 未评测）"""
    return reference_solver(answers, name=name)


def null_solver(*, name: str = "null") -> Tuple[Solver, str]:
    """空解算器：永远"未评测"（无凭证/未接线时的诚实回落口径）"""

    def solve(case: EvalCase) -> Optional[Dict[str, Any]]:
        return None

    return solve, name


def mutant_solver(answers: Mapping[str, Mapping[str, Any]], *,
                  mode: str = MODE_ALL_CHECKS,
                  name: str = "mutant") -> Tuple[Solver, str]:
    """变异解算器：对参考解做**参数感知破坏**（每条判定条目都必须能判负）

    Args:
        mode: ``all_checks``（缺省，破坏全部判定条目）/ ``first_check``（只破坏第一条）。
    """
    table = {str(k): dict(v) for k, v in (answers or {}).items()}
    if mode not in MODES:
        raise ValueError(f"未知变异模式: {mode!r}（允许 {MODES}）")

    def solve(case: EvalCase) -> Optional[Dict[str, Any]]:
        base = table.get(case.id)
        if base is None:
            return None
        broken, applied = break_answer(base, case, mode=mode)
        return broken if applied else {}

    return solve, name


# ════════════════════════════════════════════════════════════
#  参数感知的破坏
# ════════════════════════════════════════════════════════════


def break_answer(answer: Mapping[str, Any], case: EvalCase, *,
                 mode: str = MODE_ALL_CHECKS) -> Tuple[Dict[str, Any], bool]:
    """按判定条目破坏答案 → ``(新答案, 是否命中至少一处)``

    ``mode="all_checks"`` 破坏所有判定条目；``"first_check"`` 只破坏第一条。
    返回的新答案保证**至少一条**判定条目失败（命中失败返回 ``{}``，同样必失败）。
    """
    broken = copy.deepcopy(dict(answer))
    checks = list(case.expect)
    if not checks:
        return {}, True
    targets = checks if mode == MODE_ALL_CHECKS else checks[:1]
    applied = False
    for check in targets:
        path = str(check.get("path") or "")
        if not path:
            continue
        found, value = K.resolve_path(broken, path)
        replacement = break_value(value if found else None, check)
        if K.set_path(broken, path, replacement):
            applied = True
    if not applied:
        return {}, True
    return broken, True


def break_value(value: Any, check: Mapping[str, Any]) -> Any:
    """构造一个**保证违反** ``check`` 的取值（读取该条目自身的参数）"""
    name = str(check.get("checker") or "")
    args = dict(check.get("args") or {})
    if name == "equals":
        return _different(args.get("value"))
    if name == "one_of":
        return MUTANT_MARK
    if name == "not_one_of":
        values = args.get("values") or [MUTANT_MARK]
        return values[0]
    if name in ("contains", "contains_all"):
        texts = args.get("texts") or ([args.get("text")] if args.get("text") else [MUTANT_MARK])
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
        # **删除该片段的全部出现**：只删第一处时，短片段（如「锚」）可能在别处再次出现，
        # 判定仍会通过 —— 那样就测不出判定器的区分度（L2 实测踩到 4 条幸存）
        hit = False
        for needle in texts:
            token = str(needle or "")
            if token and token in text:
                text = text.replace(token, "")
                hit = True
        return text if hit else MUTANT_MARK
    if name == "contains_any":
        return MUTANT_MARK
    if name == "not_contains":
        texts = args.get("texts") or [MUTANT_MARK]
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
        return f"{text} {texts[0]}"
    if name in ("regex", "rubric_keywords"):
        return MUTANT_MARK
    if name == "json_subset":
        subset = args.get("subset")
        if isinstance(subset, Mapping) and subset:
            key = sorted(subset)[0]
            if isinstance(value, Mapping) and key in value:
                clone = dict(value)
                clone.pop(key)
                return clone
            return {}
        return {}
    if name in ("list_ordered", "list_set_equals"):
        return [MUTANT_MARK]
    if name == "reverse_of":
        values = list(args.get("values") or [])
        return values + [MUTANT_MARK]
    if name == "length_between":
        low = int(args.get("min") or 0)
        high = args.get("max")
        if low > 0:
            return ["x"] * max(0, low - 1)
        return ["x"] * (int(high) + 1 if high is not None else 1)
    if name == "numeric_between":
        lower = args.get("min")
        upper = args.get("max")
        if lower is not None:
            return float(lower) - 1.0
        return float(upper) + 1.0 if upper is not None else 0.0
    if name == "all_items":
        return [""]
    if name == "keys_present":
        return {}
    if name == "path_exists":
        return MISSING_PATH
    if name == "paths_exist":
        return [MISSING_PATH]
    if name == "symbols_exist":
        return [MISSING_SYMBOL]
    if name == "commit_message":
        return "x"
    if name == "nonempty":
        return "" if not isinstance(value, (list, dict)) else type(value)()
    if name == "python_probes":
        return {} if isinstance(value, Mapping) else ""
    return MUTANT_MARK


def _different(want: Any) -> Any:
    """返回一个**必然不等于** ``want`` 的值（类型敏感：bool 不与 int 互认）"""
    if isinstance(want, bool):
        return not want
    if isinstance(want, (int, float)):
        return want + 1
    if isinstance(want, str):
        return want + MUTANT_MARK
    if isinstance(want, list):
        return list(want) + [MUTANT_MARK]
    if isinstance(want, Mapping):
        return {**dict(want), MUTANT_MARK: True}
    if want is None:
        return MUTANT_MARK
    return MUTANT_MARK


def broken_answer_for_check(answer: Mapping[str, Any], case: EvalCase,
                            index: int) -> Dict[str, Any]:
    """只破坏第 ``index`` 条判定条目（逐条区分度对照用）"""
    broken = copy.deepcopy(dict(answer))
    checks = list(case.expect)
    if index < 0 or index >= len(checks):
        return {}
    check = checks[index]
    path = str(check.get("path") or "")
    if not path:
        return {}
    found, value = K.resolve_path(broken, path)
    if not K.set_path(broken, path, break_value(value if found else None, check)):
        return {}
    return broken


def solver_from_spec(spec: Any, *, answers: Optional[Mapping[str, Mapping[str, Any]]] = None,
                     path: str = "") -> Tuple[Solver, str]:
    """按名字/可调用对象构造解算器

    Args:
        spec: ``"reference"`` / ``"static"`` / ``"mutant"`` / ``"mutant:first_check"`` /
            ``"null"`` / ``"file:<path>"``，或任意 ``callable(case) -> dict|None``。
        answers: `reference`/`static`/`mutant` 需要的答案表。
        path: 已废弃的显式路径参数（保留兼容；推荐 ``file:<path>`` 形式）。
    """
    if callable(spec):
        return spec, getattr(spec, "__name__", "callable")
    name = str(spec or SOLVER_NULL).strip()
    if name.startswith("file:"):
        target = name[5:] or path
        with open(target, "r", encoding="utf-8") as fh:
            loaded = json.load(fh)
        raw_table: Any = loaded
        if isinstance(loaded, Mapping) and isinstance(loaded.get("answers"), Mapping):
            raw_table = loaded.get("answers")
        if not isinstance(raw_table, Mapping):
            raise ValueError(f"答案文件结构非法（{target}）：期望 objects 映射或含 answers 的对象")
        table: Dict[str, Mapping[str, Any]] = {
            str(k): v for k, v in raw_table.items() if isinstance(v, Mapping)}
        return static_solver(table, name=f"file:{target}")
    if name in ("", SOLVER_NULL, "none", "unsupported"):
        return null_solver(name=SOLVER_NULL)
    if name == SOLVER_REFERENCE:
        return reference_solver(answers or {}, name=SOLVER_REFERENCE)
    if name == SOLVER_STATIC:
        return static_solver(answers or {}, name=SOLVER_STATIC)
    if name == SOLVER_MUTANT:
        return mutant_solver(answers or {}, mode=MODE_ALL_CHECKS, name=SOLVER_MUTANT)
    if name.startswith(f"{SOLVER_MUTANT}:"):
        mode = name.split(":", 1)[1].strip() or MODE_ALL_CHECKS
        return mutant_solver(answers or {}, mode=mode, name=name)
    raise ValueError(
        f"未知解算器: {spec!r}（允许 reference/static/mutant[:mode]/null/file:<path>/callable）")


__all__ = [
    "Solver", "SOLVER_REFERENCE", "SOLVER_STATIC", "SOLVER_MUTANT", "SOLVER_NULL",
    "MODE_FIRST_CHECK", "MODE_ALL_CHECKS", "MODES", "MUTANT_MARK", "MISSING_PATH",
    "MISSING_SYMBOL", "reference_solver", "static_solver", "null_solver",
    "mutant_solver", "break_answer", "break_value", "broken_answer_for_check",
    "solver_from_spec",
]
