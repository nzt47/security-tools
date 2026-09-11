"""评测判定器（**机械可验优先**）——TASK-S5-02 / v7.2 §6.5

判定器 = 把"答案里的某个取值"与"用例冻结的期望"做**确定性比较**，返回
``(passed, detail)``。设计纪律（对齐任务书 §二 步骤 1「判定标准机械可验优先」）：

1. **机械判定器不做语义联想**：只做相等/包含/正则/集合/顺序/长度/路径存在/
   代码可执行/断言通过 这类可复现判断。凡是"看起来对"的判断一律不做。
2. **不依赖 LLM 凭证**：`python_probes` 在**受限命名空间**内执行被测代码（只给
   白名单内置函数，禁 `import`/`open`/`eval`），因此判定不触网、不调模型。
3. **代理判定器必须显式降级**：`PROXY_CHECKERS`（词表代理）只允许出现在
   `verdict_kind="proxy"` 的用例上（`cases.validate_case_set` 强制），报告中
   逐条披露"这是代理口径，不是 LLM 裁判"。
4. **相对断言优先**：性能类判定用 `numeric_le` / `numeric_between` 由**用例**给出
   阈值与 clock 口径，判定器自身不硬编码墙钟（CI 插桩抖动不可比）。

判定器清单见 `MECHANICAL_CHECKERS` / `PROXY_CHECKERS`。
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger("agent.eval.checkers")

#: 判定结果 = (是否通过, 说明)
CheckOutcome = Tuple[bool, str]
#: 判定器函数签名
Checker = Callable[[Any, Mapping[str, Any], "CheckContext"], CheckOutcome]

#: 仓库根（`agent/eval/checkers.py` → 上溯三级）
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: `python_probes` 允许使用的内置函数白名单（**故意极小**：判定不执行任意代码）
SAFE_BUILTINS: Dict[str, Any] = {
    "abs": abs, "all": all, "any": any, "bool": bool, "dict": dict,
    "divmod": divmod, "enumerate": enumerate, "float": float, "int": int,
    "isinstance": isinstance, "len": len, "list": list, "max": max, "min": min,
    "print": print, "range": range, "reversed": reversed, "round": round,
    "set": set, "sorted": sorted, "str": str, "sum": sum, "tuple": tuple,
    "type": type, "zip": zip, "ValueError": ValueError, "TypeError": TypeError,
    "KeyError": KeyError, "IndexError": IndexError, "ZeroDivisionError": ZeroDivisionError,
}

#: 逐仓库符号检索的缓存（`symbols_exist` 用；键 = (root, symbol)）
_SYMBOL_CACHE: Dict[Tuple[str, str], bool] = {}
#: 语料缓存（键 = (root, suffixes) → ((路径, 文本), ...)）：一次遍历，之后纯内存检索
_CORPUS_CACHE: Dict[Tuple[str, Tuple[str, ...]], Tuple[Tuple[str, str], ...]] = {}
#: 检索时剪枝的目录（运行时产物 / 缓存 / 依赖 / 工作树副本）
_PRUNED_DIRS = frozenset({
    ".git", "__pycache__", "node_modules", ".mypy_cache", ".pytest_cache",
    "dist", "build", "htmlcov", ".pytest_tmp", ".worktrees", ".venv", "venv",
    "site-packages", "data", "logs", ".ci_logs", "test_reports", "reports",
    ".p6_snapshots", "memory_data", ".idea", ".vscode",
})
#: 语料文件数上限与单文件大小上限（防止异常大树把判定拖垮）
_MAX_CORPUS_FILES = 20000
_MAX_CORPUS_FILE_BYTES = 1 << 20

#: 只扫描这些后缀（符号存在性判定不该扫二进制/构建产物）
_SCAN_SUFFIXES = (".py", ".json", ".yaml", ".yml", ".md", ".toml", ".cfg", ".ini", ".txt")


class CheckerError(ValueError):
    """判定器调用非法（未登记 / 参数缺失 / 取值路径不可解析）"""


@dataclass
class CheckContext:
    """判定上下文（仓库根 + 用例身份；不携带任何运行期句柄）"""

    repo_root: str = REPO_ROOT
    case_id: str = ""
    layer: str = ""
    case: Any = None
    extra: Dict[str, Any] = field(default_factory=dict)


# ════════════════════════════════════════════════════════════
#  取值路径
# ════════════════════════════════════════════════════════════


def resolve_path(answer: Any, path: str) -> Tuple[bool, Any]:
    """解析答案内的取值路径（``a.b[0].c``）

    Returns:
        ``(found, value)``；路径不存在返回 ``(False, None)``（判定器据此给出
        "缺失"而不是静默通过）。
    """
    if not path:
        return True, answer
    current = answer
    for token in _tokenize_path(path):
        if isinstance(token, int):
            if not isinstance(current, (list, tuple)) or token >= len(current) or token < -len(current):
                return False, None
            current = current[token]
            continue
        if isinstance(current, Mapping):
            if token not in current:
                return False, None
            current = current[token]
            continue
        if isinstance(current, (list, tuple)):
            # 支持 `items.name` 形式的投影 → 返回列表
            projected = []
            for item in current:
                if isinstance(item, Mapping) and token in item:
                    projected.append(item[token])
                else:
                    return False, None
            current = projected
            continue
        return False, None
    return True, current


def _tokenize_path(path: str) -> List[Any]:
    out: List[Any] = []
    for part in str(path).split("."):
        if not part:
            continue
        name = part
        while "[" in name and name.endswith("]"):
            name, _, index = name.partition("[")
            index = index[:-1]
            if name:
                out.append(name)
                name = ""
            try:
                out.append(int(index))
            except ValueError as e:
                raise CheckerError(f"非法取值下标: {part!r}") from e
        if name:
            out.append(name)
    return out


def set_path(answer: Any, path: str, value: Any) -> bool:
    """按取值路径写入（不存在则按需创建中间对象）→ 是否成功

    只用于**负样本对照**（`agent.eval.solvers` 的变异解）：正常判定流程只读。
    """
    if not path:
        return False
    tokens = _tokenize_path(path)
    current = answer
    for index, token in enumerate(tokens):
        last = index == len(tokens) - 1
        nxt = tokens[index + 1] if not last else None
        if isinstance(token, int):
            if not isinstance(current, list):
                return False
            while len(current) <= token:      # 允许按需扩展（负样本对照要能"写坏"）
                current.append(None)
            if last:
                current[token] = value
                return True
            if not isinstance(current[token], (dict, list)):
                current[token] = [] if isinstance(nxt, int) else {}
            current = current[token]
            continue
        if not isinstance(current, dict):
            return False
        if last:
            current[token] = value
            return True
        if not isinstance(current.get(token), (dict, list)):
            current[token] = [] if isinstance(nxt, int) else {}
        current = current[token]
    return False


def delete_path(answer: Any, path: str) -> bool:
    """按取值路径删除叶子 → 是否成功（负样本对照用）"""
    if not path:
        return False
    tokens = _tokenize_path(path)
    current = answer
    for index, token in enumerate(tokens):
        last = index == len(tokens) - 1
        if isinstance(token, int):
            if not isinstance(current, list) or token >= len(current):
                return False
            if last:
                current.pop(token)
                return True
        else:
            if not isinstance(current, dict) or token not in current:
                return False
            if last:
                current.pop(token)
                return True
        current = current[token] if not last else current
    return False


# ════════════════════════════════════════════════════════════
#  机械判定器
# ════════════════════════════════════════════════════════════


def check_equals(value: Any, args: Mapping[str, Any], ctx: CheckContext) -> CheckOutcome:
    """严格相等（含类型；`args.value` 为期望值）"""
    expected = args.get("value")
    return _eq(value, expected), f"equals: got {_short(value)!r} want {_short(expected)!r}"


def check_one_of(value: Any, args: Mapping[str, Any], ctx: CheckContext) -> CheckOutcome:
    """取值 ∈ `args.values`（枚举校验；避免"差不多的名字"被判过）"""
    values = _as_list(args.get("values"))
    if not values:
        raise CheckerError("one_of 需要 args.values 非空数组")
    ok = any(_eq(value, v) for v in values)
    return ok, f"one_of: got {_short(value)!r} allowed {_short(values)!r}"


def check_not_one_of(value: Any, args: Mapping[str, Any], ctx: CheckContext) -> CheckOutcome:
    """取值 ∉ `args.values`（负向枚举：用于"不得选破坏性工具"这类断言）"""
    values = _as_list(args.get("values"))
    ok = not any(_eq(value, v) for v in values)
    return ok, f"not_one_of: got {_short(value)!r} forbidden {_short(values)!r}"


def check_contains(value: Any, args: Mapping[str, Any], ctx: CheckContext) -> CheckOutcome:
    """字符串包含 `args.text`（大小写敏感；`ignore_case` 可选）"""
    text = str(args.get("text") or "")
    ok, hay = _text_contains(value, text, bool(args.get("ignore_case")))
    return ok, f"contains: {text!r} {'∈' if ok else '∉'} {_short(hay)}"


def check_contains_all(value: Any, args: Mapping[str, Any], ctx: CheckContext) -> CheckOutcome:
    """字符串包含 `args.texts` **全部**片段"""
    texts = [str(t) for t in _as_list(args.get("texts"))]
    if not texts:
        raise CheckerError("contains_all 需要 args.texts 非空数组")
    missing = [t for t in texts
               if not _text_contains(value, t, bool(args.get("ignore_case")))[0]]
    return not missing, f"contains_all: missing={_short(missing)!r}"


def check_contains_any(value: Any, args: Mapping[str, Any], ctx: CheckContext) -> CheckOutcome:
    """字符串包含 `args.texts` **任一**片段"""
    texts = [str(t) for t in _as_list(args.get("texts"))]
    if not texts:
        raise CheckerError("contains_any 需要 args.texts 非空数组")
    hit = [t for t in texts if _text_contains(value, t, bool(args.get("ignore_case")))[0]]
    return bool(hit), f"contains_any: hit={_short(hit)!r}"


def check_not_contains(value: Any, args: Mapping[str, Any], ctx: CheckContext) -> CheckOutcome:
    """字符串**不得**包含 `args.texts` 任一片段（反例断言）"""
    texts = [str(t) for t in _as_list(args.get("texts"))]
    if not texts:
        raise CheckerError("not_contains 需要 args.texts 非空数组")
    hit = [t for t in texts if _text_contains(value, t, bool(args.get("ignore_case")))[0]]
    return not hit, f"not_contains: violated={_short(hit)!r}"


def check_regex(value: Any, args: Mapping[str, Any], ctx: CheckContext) -> CheckOutcome:
    """正则**全串匹配**（`re.fullmatch`；`search=True` 时改为搜索）"""
    pattern = str(args.get("pattern") or "")
    if not pattern:
        raise CheckerError("regex 需要 args.pattern")
    flags = re.MULTILINE if args.get("multiline") else 0
    try:
        rx = re.compile(pattern, flags)
    except re.error as e:
        raise CheckerError(f"regex 模式非法: {pattern!r} ({e})") from e
    subject = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    matched = bool(rx.search(subject)) if args.get("search") else bool(rx.fullmatch(subject))
    return matched, f"regex: {'match' if matched else 'no-match'} pattern={pattern!r}"


def check_json_subset(value: Any, args: Mapping[str, Any], ctx: CheckContext) -> CheckOutcome:
    """递归子集比较（`args.subset` 的每个叶子都必须相等存在）"""
    subset = args.get("subset")
    missing: List[str] = []
    _subset_walk(value, subset, "$", missing)
    return not missing, f"json_subset: missing={_short(missing)!r}"


def check_list_set_equals(value: Any, args: Mapping[str, Any], ctx: CheckContext) -> CheckOutcome:
    """列表**集合相等**（顺序无关；元素按 JSON 规范化比较）"""
    expected = _as_list(args.get("values"))
    if not isinstance(value, (list, tuple)):
        return False, f"list_set_equals: 取值不是数组而是 {type(value).__name__}"
    got = sorted(json.dumps(v, ensure_ascii=False, sort_keys=True, default=str) for v in value)
    want = sorted(json.dumps(v, ensure_ascii=False, sort_keys=True, default=str) for v in expected)
    return got == want, f"list_set_equals: got={_short(got)!r} want={_short(want)!r}"


def check_list_ordered(value: Any, args: Mapping[str, Any], ctx: CheckContext) -> CheckOutcome:
    """列表**顺序相等**（顺序即语义：如回滚步骤必须倒序）"""
    expected = _as_list(args.get("values"))
    if not isinstance(value, (list, tuple)):
        return False, f"list_ordered: 取值不是数组而是 {type(value).__name__}"
    got = list(value)
    ok = len(got) == len(expected) and all(_eq(g, e) for g, e in zip(got, expected))
    return ok, f"list_ordered: got={_short(got)!r} want={_short(expected)!r}"


def check_reverse_of(value: Any, args: Mapping[str, Any], ctx: CheckContext) -> CheckOutcome:
    """取值必须是 `args.values` 的**逆序**（补偿/回滚顺序断言）"""
    expected = _as_list(args.get("values"))
    if not isinstance(value, (list, tuple)):
        return False, f"reverse_of: 取值不是数组而是 {type(value).__name__}"
    ok = list(value) == list(reversed(expected))
    return ok, f"reverse_of: got={_short(list(value))!r} want_reversed={_short(list(reversed(expected)))!r}"


def check_length_between(value: Any, args: Mapping[str, Any], ctx: CheckContext) -> CheckOutcome:
    """长度区间断言（`args.min` / `args.max`，含端点；缺省侧不设限）"""
    try:
        size = len(value)
    except TypeError:
        return False, f"length_between: 取值无长度 ({type(value).__name__})"
    low = _opt_int(args.get("min"))
    high = _opt_int(args.get("max"))
    ok = (low is None or size >= low) and (high is None or size <= high)
    return ok, f"length_between: len={size} bounds=({low}, {high})"


def check_numeric_between(value: Any, args: Mapping[str, Any], ctx: CheckContext) -> CheckOutcome:
    """数值区间断言（**相对/阈值断言**：阈值由用例给出，判定器不硬编码墙钟）"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False, f"numeric_between: 非数值 {_short(value)!r}"
    low = args.get("min")
    high = args.get("max")
    ok = ((low is None or number >= float(low)) and (high is None or number <= float(high)))
    return ok, f"numeric_between: value={number} bounds=({low}, {high})"


def check_all_items(value: Any, args: Mapping[str, Any], ctx: CheckContext) -> CheckOutcome:
    """列表**每一项**都满足嵌套判定（`args.check` = 单条判定，作用于元素本身）"""
    nested = args.get("check")
    if not isinstance(nested, Mapping):
        raise CheckerError("all_items 需要 args.check 对象")
    if not isinstance(value, (list, tuple)):
        return False, f"all_items: 取值不是数组而是 {type(value).__name__}"
    if not value:
        return False, "all_items: 空数组（不可判为通过）"
    failed: List[str] = []
    for index, item in enumerate(value):
        outcome = dispatch(str(nested.get("checker") or ""), item,
                           dict(nested.get("args") or {}), ctx)
        if not outcome[0]:
            failed.append(f"[{index}]{nested.get('checker')}: {outcome[1]}")
    return not failed, f"all_items: failed={_short(failed)!r}"


def check_keys_present(value: Any, args: Mapping[str, Any], ctx: CheckContext) -> CheckOutcome:
    """字典包含 `args.keys` 全部键（且值非 None/空串，防"占位即通过"）"""
    keys = [str(k) for k in _as_list(args.get("keys"))]
    if not isinstance(value, Mapping):
        return False, f"keys_present: 取值不是对象而是 {type(value).__name__}"
    missing = [k for k in keys if k not in value or value.get(k) in (None, "", [], {})]
    return not missing, f"keys_present: missing_or_empty={_short(missing)!r}"


def check_path_exists(value: Any, args: Mapping[str, Any], ctx: CheckContext) -> CheckOutcome:
    """仓库内路径存在（可选 `args.symbol`：该文件内必须出现此符号）

    **为什么这是机械可验的**：答案里给出的文件路径必须在真实仓库中存在，
    且（如声明）确实定义了所声称的符号 —— 幻觉路径/幻觉符号直接被拒。
    """
    rel = str(value or "").strip().replace("\\", "/")
    if not rel:
        return False, "path_exists: 取值为空"
    root = str(args.get("root") or ctx.repo_root)
    target = os.path.join(root, rel)
    if not os.path.isfile(target):
        return False, f"path_exists: {rel} 不存在于 {root}"
    symbol = str(args.get("symbol") or "")
    if symbol:
        try:
            with open(target, "r", encoding="utf-8", errors="ignore") as fh:
                blob = fh.read()
        except OSError as e:
            return False, f"path_exists: 读取 {rel} 失败: {e}"
        if symbol not in blob:
            return False, f"path_exists: {rel} 中未出现符号 {symbol!r}"
    return True, f"path_exists: {rel}{f' (+symbol {symbol})' if symbol else ''}"


def check_paths_exist(value: Any, args: Mapping[str, Any], ctx: CheckContext) -> CheckOutcome:
    """列表内**每个**仓库相对路径都存在（提交信息的文件清单用）"""
    if not isinstance(value, (list, tuple)) or not value:
        return False, "paths_exist: 取值必须是非空数组"
    bad: List[str] = []
    for item in value:
        outcome = check_path_exists(item, args, ctx)
        if not outcome[0]:
            bad.append(str(item))
    return not bad, f"paths_exist: bad={_short(bad)!r}"


def check_symbols_exist(value: Any, args: Mapping[str, Any], ctx: CheckContext) -> CheckOutcome:
    """答案中提到的符号**必须真实存在于仓库**（反幻觉断言）

    `value` 为符号名数组，或含符号的文本（文本模式下只抽取**类标识符**词元，
    中文词与短词元不参与判定，避免把自然语言当符号名）；`args.root` 限定扫描根，
    `args.suffixes` 限定后缀。实现为逐文件子串检索（带进程内缓存）。
    """
    if isinstance(value, (list, tuple)):
        symbols = [str(s) for s in value if str(s).strip()]
    else:
        symbols = sorted(set(re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", str(value or ""))))
    if not symbols:
        return False, "symbols_exist: 无符号可查（空取值）"
    root = str(args.get("root") or ctx.repo_root)
    suffixes = tuple(args.get("suffixes") or _SCAN_SUFFIXES)
    missing = [s for s in symbols if not _symbol_exists(root, s, suffixes)]
    return not missing, f"symbols_exist: hallucinated={_short(missing)!r}"


def check_commit_message(value: Any, args: Mapping[str, Any], ctx: CheckContext) -> CheckOutcome:
    """提交信息结构断言（Conventional Commits 子集；**结构可机械校验**）

    校验：`type(scope): subject` 形态、type ∈ `args.types`、scope ∈ `args.scopes`
    （给定时）、subject ≤ `args.subject_max`（缺省 72）、subject 不以句号结尾、
    正文含 `args.body_requires` 片段。
    """
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    lines = text.strip().splitlines()
    if not lines:
        return False, "commit_message: 空提交信息"
    subject = lines[0].strip()
    types = [str(t) for t in _as_list(args.get("types"))] or [
        "feat", "fix", "docs", "test", "refactor", "chore", "perf", "build", "ci", "style", "revert"]
    scopes = [str(s) for s in _as_list(args.get("scopes"))]
    max_len = int(args.get("subject_max") or 72)
    problems: List[str] = []
    matched = re.fullmatch(r"([a-z]+)(?:\(([^)]*)\))?(!)?: (.+)", subject)
    if not matched:
        problems.append("subject 不符合 type(scope): subject 形态")
    else:
        mtype, mscope, _bang, mtext = matched.group(1), matched.group(2) or "", matched.group(3), matched.group(4)
        if mtype not in types:
            problems.append(f"type={mtype!r} 不在 {types}")
        if scopes and mscope not in scopes:
            problems.append(f"scope={mscope!r} 不在 {scopes}")
        if not scopes and not mscope:
            problems.append("缺少 scope")
        if mtext.rstrip().endswith("."):
            problems.append("subject 以句号结尾")
        if not mtext.strip():
            problems.append("subject 为空")
    if len(subject) > max_len:
        problems.append(f"subject 长度 {len(subject)} > {max_len}")
    body = "\n".join(lines[1:])
    for required in _as_list(args.get("body_requires")):
        if str(required) not in body:
            problems.append(f"正文缺少 {required!r}")
    return not problems, f"commit_message: problems={_short(problems)!r}"


def check_nonempty(value: Any, args: Mapping[str, Any], ctx: CheckContext) -> CheckOutcome:
    """取值非空（字符串/数组/对象）"""
    empty = value is None or value == "" or value == [] or value == {}
    return not empty, f"nonempty: got {_short(value)!r}"


def check_python_probes(value: Any, args: Mapping[str, Any], ctx: CheckContext) -> CheckOutcome:
    """在**受限命名空间**执行被测代码并跑断言探针（S1 修 bug 的机械判定）

    `value` 为代码文本，或 ``{"code": "...", "entry": "..."}`` 对象。
    每条探针形如 ``{"call": "func", "args": [...], "expected": ...}``
    或 ``{"call": "func", "args": [...], "raises": "ValueError"}``。

    **口径**：只执行被测代码本身；命名空间只给白名单内置函数，故
    `import`/`open`/`eval`/`__import__` 均不可用（越界即判定失败并如实说明）。
    """
    code = value.get("code") if isinstance(value, Mapping) else value
    if not isinstance(code, str) or not code.strip():
        return False, "python_probes: 无代码可执行"
    probes = _as_list(args.get("probes"))
    if not probes:
        raise CheckerError("python_probes 需要 args.probes 非空数组")
    namespace: Dict[str, Any] = {"__builtins__": dict(SAFE_BUILTINS), "__name__": "eval_probe"}
    try:
        compiled = compile(code, "<eval-case>", "exec")
        exec(compiled, namespace)  # noqa: S102 —— 受限命名空间，白名单内置
    except Exception as e:  # noqa: BLE001 被测代码自身报错 → 判定失败（如实披露）
        return False, f"python_probes: 代码加载失败 {type(e).__name__}: {e}"
    failures: List[str] = []
    for index, probe in enumerate(probes):
        if not isinstance(probe, Mapping):
            failures.append(f"[{index}] 探针非法")
            continue
        name = str(probe.get("call") or "")
        target = namespace.get(name)
        if not callable(target):
            failures.append(f"[{index}] 未定义可调用名 {name!r}")
            continue
        call_args = list(probe.get("args") or [])
        expects_raise = str(probe.get("raises") or "")
        try:
            result = target(*call_args)
        except Exception as e:  # noqa: BLE001
            if expects_raise and type(e).__name__ == expects_raise:
                continue
            failures.append(f"[{index}] {name}{tuple(call_args)} 抛 {type(e).__name__}: {e}")
            continue
        if expects_raise:
            failures.append(f"[{index}] {name} 未抛 {expects_raise}（返回 {_short(result)!r}）")
            continue
        if not _eq(result, probe.get("expected")):
            failures.append(
                f"[{index}] {name}{tuple(call_args)} → {_short(result)!r} "
                f"≠ {_short(probe.get('expected'))!r}")
    return not failures, f"python_probes: failed={_short(failures)!r}"


# ════════════════════════════════════════════════════════════
#  代理判定器（必须显式降级并披露）
# ════════════════════════════════════════════════════════════


def check_rubric_keywords(value: Any, args: Mapping[str, Any], ctx: CheckContext) -> CheckOutcome:
    """**词表代理**判定（非 LLM 裁判）：`args.groups` 每组命中任一关键词即得 1 分

    ⚠ 这是**代理指标**：只用于"解释是否覆盖了必要要点"这类无法机械判定的场合，
    因此引用它的用例必须 `verdict_kind="proxy"`，报告中显式披露
    "代理口径 ≠ 语义正确"。命中组数 ≥ `args.min_groups`（缺省 = 全部组）即通过。
    """
    groups = _as_list(args.get("groups"))
    if not groups:
        raise CheckerError("rubric_keywords 需要 args.groups 非空数组")
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    low = text.lower()
    hits = [any(str(kw).lower() in low for kw in group) for group in groups]
    need = int(args.get("min_groups") or len(groups))
    return sum(1 for h in hits if h) >= need, (
        f"rubric_keywords(proxy): hit_groups={sum(1 for h in hits if h)}/{len(groups)} need={need}")


# ════════════════════════════════════════════════════════════
#  注册表与派发
# ════════════════════════════════════════════════════════════

MECHANICAL_CHECKERS: Dict[str, Checker] = {
    "equals": check_equals,
    "one_of": check_one_of,
    "not_one_of": check_not_one_of,
    "contains": check_contains,
    "contains_all": check_contains_all,
    "contains_any": check_contains_any,
    "not_contains": check_not_contains,
    "regex": check_regex,
    "json_subset": check_json_subset,
    "list_set_equals": check_list_set_equals,
    "list_ordered": check_list_ordered,
    "reverse_of": check_reverse_of,
    "length_between": check_length_between,
    "numeric_between": check_numeric_between,
    "all_items": check_all_items,
    "keys_present": check_keys_present,
    "path_exists": check_path_exists,
    "paths_exist": check_paths_exist,
    "symbols_exist": check_symbols_exist,
    "commit_message": check_commit_message,
    "nonempty": check_nonempty,
    "python_probes": check_python_probes,
}

PROXY_CHECKERS: Dict[str, Checker] = {
    "rubric_keywords": check_rubric_keywords,
}

ALL_CHECKERS: Dict[str, Checker] = {**MECHANICAL_CHECKERS, **PROXY_CHECKERS}


def is_mechanical(name: str) -> bool:
    """判定器是否属于机械可验集合"""
    return str(name) in MECHANICAL_CHECKERS


def is_proxy(name: str) -> bool:
    """判定器是否属于代理口径集合"""
    return str(name) in PROXY_CHECKERS


def dispatch(name: str, value: Any, args: Mapping[str, Any],
             ctx: Optional[CheckContext] = None) -> CheckOutcome:
    """按名字派发判定器（未登记 → `CheckerError`，**不静默通过**）"""
    checker = ALL_CHECKERS.get(str(name))
    if checker is None:
        raise CheckerError(f"未登记的判定器: {name!r}（已登记 {sorted(ALL_CHECKERS)}）")
    return checker(value, dict(args or {}), ctx or CheckContext())


def run_check(answer: Any, check: Mapping[str, Any],
              ctx: Optional[CheckContext] = None) -> Dict[str, Any]:
    """执行单条判定条目 → 结构化结果（供报告逐条展示）

    ``check`` = ``{"checker", "path", "args", "why"}``；取值路径解析失败 →
    ``passed=False``（"答案里没有这个字段"就是不合格，而不是跳过）。
    """
    name = str(check.get("checker") or "")
    path = str(check.get("path") or "")
    args = dict(check.get("args") or {})
    why = str(check.get("why") or "")
    context = ctx or CheckContext()
    found, value = resolve_path(answer, path)
    if not found:
        return {"checker": name, "path": path, "why": why, "passed": False,
                "detail": f"取值路径不可解析: {path!r}（答案缺少该字段）",
                "mechanical": is_mechanical(name)}
    try:
        passed, detail = dispatch(name, value, args, context)
    except CheckerError as e:
        return {"checker": name, "path": path, "why": why, "passed": False,
                "detail": f"判定器错误: {e}", "mechanical": is_mechanical(name)}
    return {"checker": name, "path": path, "why": why, "passed": bool(passed),
            "detail": detail, "mechanical": is_mechanical(name)}


# ════════════════════════════════════════════════════════════
#  内部工具
# ════════════════════════════════════════════════════════════


def _eq(a: Any, b: Any) -> bool:
    """相等比较：bool/int 不互认（`True == 1` 在评测里是错误通过）"""
    if isinstance(a, bool) != isinstance(b, bool):
        return False
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return float(a) == float(b)
    return bool(a == b)


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _opt_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _text_contains(value: Any, text: str, ignore_case: bool) -> Tuple[bool, str]:
    hay = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    if ignore_case:
        return text.lower() in hay.lower(), hay
    return text in hay, hay


def _subset_walk(got: Any, want: Any, path: str, missing: List[str]) -> None:
    if isinstance(want, Mapping):
        if not isinstance(got, Mapping):
            missing.append(f"{path}: 期望对象，得到 {type(got).__name__}")
            return
        for key, sub in want.items():
            if key not in got:
                missing.append(f"{path}.{key}: 缺失")
                continue
            _subset_walk(got[key], sub, f"{path}.{key}", missing)
        return
    if isinstance(want, (list, tuple)):
        if not isinstance(got, (list, tuple)) or len(got) != len(want):
            missing.append(f"{path}: 数组长度不一致（got={len(got) if isinstance(got, (list, tuple)) else '-'}，want={len(want)}）")
            return
        for index, sub in enumerate(want):
            _subset_walk(got[index], sub, f"{path}[{index}]", missing)
        return
    if not _eq(got, want):
        missing.append(f"{path}: got={_short(got)!r} want={_short(want)!r}")


def _symbol_exists(root: str, symbol: str, suffixes: Sequence[str]) -> bool:
    """符号是否出现在 `root` 下（**语料缓存**：一次剪枝遍历，之后纯内存检索）

    性能与稳定性（本任务实测教训）：全量套件并发运行时，逐符号重新 `os.walk`
    会把每次判定都变成一次全树遍历，叠加测试运行期目录后足以把单条用例推到超时。
    因此这里：① 遍历时**剪枝**运行时/产物/缓存目录；② 把命中的文件内容读成
    语料并缓存（按 ``(root, suffixes)`` 维度），后续符号检索只在内存里做子串匹配；
    ③ 设文件数与单文件大小上限，避免异常大树拖垮判定。
    """
    resolved_root = os.path.abspath(root)
    corpus_key = (resolved_root, tuple(suffixes))
    corpus = _CORPUS_CACHE.get(corpus_key)
    if corpus is None:
        corpus = _load_corpus(resolved_root, suffixes)
        _CORPUS_CACHE[corpus_key] = corpus
    found = any(symbol in text for _, text in corpus)
    _SYMBOL_CACHE[(resolved_root, symbol)] = found
    return found


def _load_corpus(root: str, suffixes: Sequence[str]) -> Tuple[Tuple[str, str], ...]:
    """建立检索语料（剪枝 + 限额；只读文本）"""
    if not root or not os.path.isdir(root):
        return ()
    suffix_tuple = tuple(suffixes)
    corpus: List[Tuple[str, str]] = []
    visited = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _PRUNED_DIRS]
        for filename in filenames:
            if not filename.endswith(suffix_tuple):
                continue
            visited += 1
            if visited > _MAX_CORPUS_FILES:
                logger.warning("符号检索语料达到文件上限 %d（root=%s），停止收集",
                               _MAX_CORPUS_FILES, root)
                return tuple(corpus)
            path = os.path.join(dirpath, filename)
            try:
                if os.path.getsize(path) > _MAX_CORPUS_FILE_BYTES:
                    continue
                with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                    corpus.append((path, fh.read()))
            except OSError:
                continue
    return tuple(corpus)


def _short(value: Any, limit: int = 160) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + "…"


def reset_symbol_cache() -> None:
    """清空符号/语料缓存（测试与配置变更后用）"""
    _SYMBOL_CACHE.clear()
    _CORPUS_CACHE.clear()


__all__ = [
    "REPO_ROOT", "SAFE_BUILTINS", "CheckOutcome", "Checker", "CheckContext",
    "CheckerError", "MECHANICAL_CHECKERS", "PROXY_CHECKERS", "ALL_CHECKERS",
    "resolve_path", "set_path", "delete_path", "is_mechanical", "is_proxy",
    "dispatch", "run_check", "reset_symbol_cache",
    "check_equals", "check_one_of", "check_not_one_of", "check_contains",
    "check_contains_all", "check_contains_any", "check_not_contains", "check_regex",
    "check_json_subset", "check_list_set_equals", "check_list_ordered",
    "check_reverse_of", "check_length_between", "check_numeric_between",
    "check_all_items", "check_keys_present", "check_path_exists", "check_paths_exist",
    "check_symbols_exist", "check_commit_message", "check_nonempty",
    "check_python_probes", "check_rubric_keywords",
]
