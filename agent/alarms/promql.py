# -*- coding: utf-8 -*-
"""PromQL 子集求值（2026-11-11）：**只为本仓那 16 条规则**服务，不是通用引擎。

【为什么不是"把规则翻成 evaluator 口径"】
手工把 `sum(rate(x{status=~"5.."}[5m])) / clamp_min(sum(rate(x[5m])), 1e-9) > 0.05`
改写成"指标 + 阈值 + 比较符"会**丢掉 rate/clamp_min/分位数**这三样表达力，
而它们正是"5xx 比例"和"P99 延迟"这类告警的全部内容（改完只剩一条永远为真的规则）。
所以这里改为：**直接解析原表达式**，在本地求值；解析不了的构造**如实报 unsupported**，
不降级成"看着像在监控"。

【支持的语法（覆盖 monitoring/alerts.yml 的全部 16 条）】
- 选择器：`name`、`name{a="v",b=~"re",c!="v"}`
- 区间：`[5m]` / `[1h]` / `[30s]`
- 函数：`rate`、`increase`、`resets`、`sum`、`max`、`clamp_min`、`histogram_quantile`
- 聚合后缀：`sum(...) by (le)`（只用于直方图分位数）
- 运算：`+ - * /`、一元负号、括号
- 比较：`== != > < >= <=`（只在顶层出现；返回布尔 + 触发值）

【求值结果三态（比"真/假"多一态，防止把"没数据"当"正常"）】
- `firing` / `ok`：表达式有值并给出结论；
- `no_data`：选择器在本进程注册表里**没有任何序列**（如 `up{job=...}` 由外部 Prometheus 采集，
  或某个计数器从未自增过）—— 这不是"正常"，是"没有测量"；
- `unsupported` / `warming`：构造不支持 / 窗口样本还没攒够（刚重启）。

【纪律】`+Inf` 桶只给"超过最后一个有限上界"，不编具体值；窗口不足返回 None 而不是 0。
"""
from __future__ import annotations

import logging
import math
import re
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from agent.alarms.samples import (
    SampleStore,
    SeriesKey,
    get_sample_store,
    parse_range,
    percentile_from_buckets,
    snapshot_registry,
)

logger = logging.getLogger(__name__)

__version__ = "1.0.0"

STATUS_FIRING = "firing"
STATUS_OK = "ok"
STATUS_NO_DATA = "no_data"
STATUS_UNSUPPORTED = "unsupported"
STATUS_WARMING = "warming"
STATUS_ERROR = "error"

#: 比较运算符
_COMPARATORS = ("==", "!=", ">=", "<=", ">", "<")
#: 已知函数（其余一律 unsupported，而不是"当作恒真"）
_FUNCTIONS = frozenset({"rate", "increase", "resets", "sum", "max",
                        "clamp_min", "histogram_quantile"})

_TOKEN_RE = re.compile(r"""
    \s*(?:
      (?P<number>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)
    | (?P<range>\[[0-9]+(?:ms|s|m|h|d)\])
    | (?P<op>==|!=|>=|<=|=~|!~|[><=+*/(),{}\-])
    | (?P<duration>[0-9]+(?:ms|s|m|h|d))
    | (?P<string>"(?:[^"\\]|\\.)*")
    | (?P<ident>[A-Za-z_:][A-Za-z0-9_:]*)
    )
""", re.VERBOSE)


@dataclass
class Token:
    kind: str
    text: str


def tokenize(expr: str) -> List[Token]:
    tokens: List[Token] = []
    pos = 0
    text = expr.strip()
    while pos < len(text):
        m = _TOKEN_RE.match(text, pos)
        if not m or m.end() == pos:
            raise ValueError("无法识别的字符: " + text[pos:pos + 12])
        pos = m.end()
        kind = m.lastgroup or ""
        tokens.append(Token(kind, m.group(kind)))
    return tokens


#: PromQL（= Go）双引号字符串里**合法**的简单转义字符（其余一律非法，见 unquote）
_SIMPLE_ESCAPES = {
    "a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v",
    "\\": "\\", '"': '"',
}


def _hex_value(body: str, start: int, width: int) -> int:
    chunk = body[start:start + width]
    if len(chunk) != width:
        raise ValueError("转义序列不完整")
    try:
        return int(chunk, 16)
    except ValueError:
        raise ValueError("非法的十六进制转义")


def unquote(text: str) -> str:
    """把 PromQL 双引号字符串还原成实际内容（**与 Prometheus/Go 同语义**）。

    【为什么必须有这一层（2026-11-12 现场实证）】monitoring/alerts.yml 的两条延迟规则曾用
    "单反斜杠 + 点" 表示正则里的字面点 —— 在 Go/PromQL 里那是**非法转义**，Prometheus 直接
    拒绝加载**整个规则文件**（现场日志 unknown escape sequence U+002E；CI 的 observability
    e2e 因此红了一天多）。本模块此前用 strip(chr(34)) 不还原转义 ⇒ "本地算得出、真实
    Prometheus 加载不了"，判据与线上不一致。

    现在：合法转义按 Go 规则还原（两个反斜杠 → 一个，正则里的字面点），**非法转义直接
    ValueError** ⇒ 该条如实报 unsupported，而不是"本地能算就算"。
    """
    body = text
    if len(body) >= 2 and body[0] == '"' and body[-1] == '"':
        body = body[1:-1]
    out: List[str] = []
    i = 0
    size = len(body)
    while i < size:
        char = body[i]
        if char != "\\":
            out.append(char)
            i += 1
            continue
        i += 1
        if i >= size:
            raise ValueError("字符串以反斜杠结尾")
        esc = body[i]
        if esc in _SIMPLE_ESCAPES:
            out.append(_SIMPLE_ESCAPES[esc])
            i += 1
            continue
        if esc == "x":
            out.append(chr(_hex_value(body, i + 1, 2)))
            i += 3
            continue
        if esc == "u":
            out.append(chr(_hex_value(body, i + 1, 4)))
            i += 5
            continue
        if esc == "U":
            out.append(chr(_hex_value(body, i + 1, 8)))
            i += 9
            continue
        if esc in "01234567":
            chunk = body[i:i + 3]
            if len(chunk) != 3 or any(item not in "01234567" for item in chunk):
                raise ValueError("非法的八进制转义")
            out.append(chr(int(chunk, 8)))
            i += 3
            continue
        raise ValueError("非法转义: 反斜杠 + " + esc)
    return "".join(out)


@dataclass
class Expr:
    kind: str                     # number | selector | call | binary | unary
    value: object = None
    name: str = ""
    labels: Dict[str, Tuple[str, str]] = field(default_factory=dict)  # 标签 → (op, value)
    range_sec: Optional[float] = None
    args: List["Expr"] = field(default_factory=list)
    group_by: Optional[str] = None
    op: str = ""


class _Parser:
    """递归下降（优先级：比较 < 加减 < 乘除 < 一元 < 原子）"""

    def __init__(self, tokens: Sequence[Token]) -> None:
        self._tokens = list(tokens)
        self._i = 0

    def _peek(self) -> Optional[Token]:
        return self._tokens[self._i] if self._i < len(self._tokens) else None

    def _next(self) -> Token:
        token = self._tokens[self._i]
        self._i += 1
        return token

    def _accept(self, text: str) -> bool:
        token = self._peek()
        if token is not None and token.text == text:
            self._i += 1
            return True
        return False

    def _expect(self, text: str) -> None:
        if not self._accept(text):
            raise ValueError("期望 " + repr(text) + "，实际 " + repr(self._peek()))

    def parse(self) -> Expr:
        expr = self._comparison()
        if self._peek() is not None:
            raise ValueError("表达式未消费完: " + str(self._peek()))
        return expr

    def _comparison(self) -> Expr:
        left = self._additive()
        token = self._peek()
        if token is not None and token.text in _COMPARATORS:
            self._next()
            right = self._additive()
            return Expr("binary", op=token.text, args=[left, right])
        return left

    def _additive(self) -> Expr:
        node = self._multiplicative()
        while True:
            token = self._peek()
            if token is None or token.text not in ("+", "-"):
                return node
            self._next()
            node = Expr("binary", op=token.text, args=[node, self._multiplicative()])

    def _multiplicative(self) -> Expr:
        node = self._unary()
        while True:
            token = self._peek()
            if token is None or token.text not in ("*", "/"):
                return node
            self._next()
            node = Expr("binary", op=token.text, args=[node, self._unary()])

    def _unary(self) -> Expr:
        if self._accept("-"):
            return Expr("unary", op="-", args=[self._unary()])
        return self._primary()

    def _primary(self) -> Expr:
        token = self._peek()
        if token is None:
            raise ValueError("表达式意外结束")
        if token.kind == "number":
            self._next()
            return Expr("number", value=float(token.text))
        if token.text == "(":
            self._next()
            node = self._comparison()
            self._expect(")")
            return node
        if token.kind == "ident":
            self._next()
            if token.text in _FUNCTIONS and self._peek() is not None and self._peek().text == "(":
                return self._call(token.text)
            if token.text not in _FUNCTIONS:
                return self._selector(token.text)
            raise ValueError("函数缺少参数列表: " + token.text)
        raise ValueError("无法解析的记号: " + repr(token))

    def _call(self, name: str) -> Expr:
        self._expect("(")
        args: List[Expr] = []
        if not self._accept(")"):
            while True:
                args.append(self._comparison())
                if self._accept(","):
                    continue
                self._expect(")")
                break
        group_by = None
        if self._peek() is not None and self._peek().kind == "ident" and self._peek().text == "by":
            self._next()
            self._expect("(")
            inner = self._next()
            group_by = inner.text
            self._expect(")")
        return Expr("call", name=name, args=args, group_by=group_by)

    def _selector(self, name: str) -> Expr:
        labels: Dict[str, Tuple[str, str]] = {}
        if self._accept("{"):
            while True:
                label = self._next()
                op = self._next()
                if op.text not in ("=", "=~", "!=", "!~"):
                    raise ValueError("不支持的标签匹配符: " + op.text)
                raw = self._next()
                labels[label.text] = (op.text, unquote(raw.text))
                if self._accept(","):
                    continue
                self._expect("}")
                break
        range_sec = None
        token = self._peek()
        if token is not None and token.kind == "range":
            self._next()
            range_sec = parse_range(token.text[1:-1])
            if range_sec is None:
                raise ValueError("无法解析区间: " + token.text)
        return Expr("selector", name=name, labels=labels, range_sec=range_sec)


def parse(expr: str) -> Expr:
    """解析（失败抛 ValueError；调用方据此报 unsupported）"""
    return _Parser(tokenize(expr)).parse()


# ════════════════════════════════════════════════════════════
#  求值
# ════════════════════════════════════════════════════════════

class Vector(dict):
    """一组序列的即时值（只由 rate/increase 产生；必须经 sum()/max() 才能变成标量）"""


class EvalError(Exception):
    """求值时的"没有数据/不支持的窗口"（携带可读原因，不进告警 detail 的敏感信息）"""


class NoData(EvalError):
    pass


class Warming(EvalError):
    pass


def _series_match(key: SeriesKey, labels: Dict[str, Tuple[str, str]]) -> bool:
    _, key_labels = key
    actual = dict(key_labels)
    for label, (op, expected) in labels.items():
        value = actual.get(label)
        if op == "=" and value != expected:
            return False
        if op == "!=" and value == expected:
            return False
        if op == "=~" and (value is None or not re.fullmatch(expected, value)):
            return False
        if op == "!~" and value is not None and re.fullmatch(expected, value):
            return False
    return True


def _select(expr: Expr, snapshot: Dict[SeriesKey, float]) -> Dict[SeriesKey, float]:
    """按名字+标签筛选。

    【两个"空"必须区分开（2026-11-11 现场实测踩到）】
    · 指标族**根本不存在** ⇒ 抛 NoData（如 up / yunshu_tool_call_total：本部署没有这些埋点）；
    · 指标族存在、只是**这个标签子集没有样本** ⇒ 返回空 dict（如 `status=~"5.."`：从来没有 5xx）。
      后者不是"没监控"，而是"这一子集的增量为 0" —— 调用方（sum/rate）按 0 处理，
      否则 "`健康时不报" 会被显示成 "无数据"，让人误以为 5xx 比例没在监控。
    """
    out = {}
    family_present = False
    for key, value in snapshot.items():
        if key[0] != expr.name:
            continue
        family_present = True
        if expr.labels and not _series_match(key, expr.labels):
            continue
        out[key] = value
    if not family_present:
        raise NoData("无序列: " + expr.name)
    return out


def _bucket_from_key(key: SeriesKey) -> Optional[float]:
    for label, value in key[1]:
        if label == "le":
            if value in ("+Inf", "Inf", "inf"):
                return math.inf
            try:
                return float(value)
            except ValueError:
                return None
    return None


class Evaluator:
    """按需访问采样环；一次求值只读当前环（不做写入）。"""

    def __init__(self, store: Optional[SampleStore] = None) -> None:
        self._store = store or get_sample_store()

    # ── 主入口 ──────────────────────────────────────────────
    def evaluate(self, expr: str) -> Tuple[Optional[float], str]:
        """返回 (触发值, 状态)。`firing`/`ok` 时值非 None；其余状态值为 None。

        - `firing`：表达式为真（比较成立）
        - `ok`：表达式有值但比较不成立
        """
        try:
            node = parse(expr)
        except ValueError as exc:
            return None, STATUS_UNSUPPORTED + ":" + str(exc)[:80]
        snapshot = self._store.latest() or snapshot_registry()
        try:
            if node.kind == "binary" and node.op in _COMPARATORS:
                # 顶层比较：左值就是"被观测到的量"（告警里要报它），布尔值决定状态
                left = float(self._eval(node.args[0], snapshot))
                right = float(self._eval(node.args[1], snapshot))
                truth = bool(self._binary(node.op, left, right))
                return left, (STATUS_FIRING if truth else STATUS_OK)
            value = self._eval(node, snapshot)
        except NoData as exc:
            return None, STATUS_NO_DATA + ":" + str(exc)[:80]
        except Warming as exc:
            return None, STATUS_WARMING + ":" + str(exc)[:80]
        except EvalError as exc:
            return None, STATUS_UNSUPPORTED + ":" + str(exc)[:80]
        except Exception as exc:  # noqa: BLE001 求值崩溃不冒充"正常"
            logger.warning("规则求值异常（%s）", type(exc).__name__)
            return None, STATUS_ERROR
        if value is None or isinstance(value, bool):
            return None, STATUS_NO_DATA + ":表达式无值"
        return float(value), STATUS_OK

    # ── 递归求值（标量 / 向量二选一，向量只在函数内部出现） ──
    def _eval(self, node: Expr, snapshot: Dict[SeriesKey, float]):
        if node.kind == "number":
            return float(node.value)
        if node.kind == "selector":
            series = _select(node, snapshot)
            if not series:
                raise NoData("无匹配序列: " + node.name + str(sorted(node.labels)))
            if node.range_sec is not None:
                # 区间选择器只能在函数里用；裸用当作 unsupported
                raise EvalError("区间选择器需在 rate/increase/resets 内使用")
            values = list(series.values())
            return max(values) if len(values) > 1 else values[0]
        if node.kind == "unary":
            return -float(self._eval(node.args[0], snapshot))
        if node.kind == "binary":
            left = self._eval(node.args[0], snapshot)
            right = self._eval(node.args[1], snapshot)
            return self._binary(node.op, left, right)
        if node.kind == "call":
            result = self._call(node, snapshot)
            return result
        raise EvalError("未知节点: " + node.kind)

    @staticmethod
    def _binary(op: str, left, right):
        if isinstance(left, Vector) or isinstance(right, Vector):
            raise EvalError("向量不能直接参与算术/比较，请先用 sum()/max() 聚合")
        if op in _COMPARATORS:
            if isinstance(left, bool) or isinstance(right, bool):
                raise EvalError("比较运算符两侧不能再是比较结果")
            compare = {"==": left == right, "!=": left != right, ">": left > right,
                       "<": left < right, ">=": left >= right, "<=": left <= right}[op]
            return bool(compare)
        left = float(left)
        right = float(right)
        if op == "+":
            return left + right
        if op == "-":
            return left - right
        if op == "*":
            return left * right
        if right == 0:
            raise EvalError("除零")
        return left / right

    def _call(self, node: Expr, snapshot: Dict[SeriesKey, float]):
        name = node.name
        if name in ("sum", "max"):
            if not node.args:
                raise EvalError(name + " 缺少参数")
            inner = node.args[0]
            if node.group_by is not None:
                return self._group_by(inner, snapshot, node.group_by)
            value = self._eval(inner, snapshot)
            if isinstance(value, Vector):
                if not value:
                    # 空向量 = "指标族存在、该子集无样本" ⇒ 增量为 0（不是"无数据"）
                    return 0.0
                values = list(value.values())
                return sum(values) if name == "sum" else max(values)
            return value
        if name in ("rate", "increase", "resets"):
            if len(node.args) != 1 or node.args[0].kind != "selector":
                raise EvalError(name + " 的参数必须是区间选择器")
            selector = node.args[0]
            window = selector.range_sec
            if window is None:
                raise EvalError(name + " 需要 [窗口]")
            series = _select(selector, snapshot)
            if not series:
                if name == "resets":
                    return 0.0
                return Vector()      # 空子集 ⇒ 增量为 0（sum 之后是 0.0）
            if name == "resets":
                # 本仓规则里 resets 只用于 up{job=...}（单序列）⇒ 直接给标量
                total = 0
                seen = False
                for key in series:
                    value = self._store.resets(key, window)
                    if value is None:
                        continue
                    seen = True
                    total += int(value)
                if not seen:
                    raise Warming("窗口样本不足（%s）" % selector.name)
                return float(total)
            vector = Vector()
            missing = 0
            for key in series:
                value = (self._store.rate(key, window) if name == "rate"
                         else self._store.increase(key, window))
                if value is None:
                    missing += 1
                    continue
                vector[key] = float(value)
            if not vector:
                raise Warming("窗口样本不足（%s）" % selector.name)
            return vector
        if name == "clamp_min":
            if len(node.args) != 2:
                raise EvalError("clamp_min 需要两个参数")
            value = float(self._eval(node.args[0], snapshot))
            floor = float(self._eval(node.args[1], snapshot))
            return max(value, floor)
        if name == "histogram_quantile":
            if len(node.args) != 2:
                raise EvalError("histogram_quantile 需要两个参数")
            quantile = float(self._eval(node.args[0], snapshot))
            buckets = self._buckets_from(node.args[1], snapshot)
            value = percentile_from_buckets(buckets, quantile)
            if value is None:
                raise NoData("直方图无样本")
            return value
        raise EvalError("不支持的函数: " + name)

    def _buckets_from(self, node: Expr, snapshot: Dict[SeriesKey, float]):
        """`sum(rate(x_bucket[5m])) by (le)` → [(le, 计数)]"""
        if node.kind != "call" or node.name != "sum":
            raise EvalError("histogram_quantile 的第二个参数只支持 sum(...) by (le)")
        inner = node.args[0] if node.args else None
        if inner is None or inner.kind != "call" or inner.name != "rate":
            raise EvalError("histogram_quantile 内层只支持 rate(...)")
        selector = inner.args[0]
        window = selector.range_sec
        if window is None:
            raise EvalError("rate 需要 [窗口]")
        series = _select(selector, snapshot)
        if not series:
            raise NoData("无匹配序列: " + selector.name)
        edges = self._store.window_edges(window)
        if edges is None:
            raise Warming("窗口样本不足（直方图）")
        (_, old), (_, new) = edges
        buckets = {}
        for key, after in new.items():
            if key[0] != selector.name or not _series_match(key, selector.labels):
                continue
            bound = _bucket_from_key(key)
            if bound is None:
                continue
            before = old.get(key, 0.0)
            delta = after - before
            if delta < 0:
                delta = after
            buckets[bound] = buckets.get(bound, 0.0) + delta
        if not buckets:
            raise NoData("直方图桶为空")
        return sorted(buckets.items())

    def _group_by(self, inner: Expr, snapshot: Dict[SeriesKey, float], label: str):
        if inner.kind != "call" or inner.name != "rate" or inner.args[0].kind != "selector":
            raise EvalError("by(...) 只支持 rate(selector)")
        selector = inner.args[0]
        window = selector.range_sec
        if window is None:
            raise EvalError("rate 需要 [窗口]")
        series = _select(selector, snapshot)
        if not series:
            raise NoData("无序列: " + selector.name)
        edges = self._store.window_edges(window)
        if edges is None:
            raise Warming("窗口样本不足（分组速率）")
        (_, old), (_, new) = edges
        grouped: Dict[str, float] = {}
        for key, after in new.items():
            if key[0] != selector.name or not _series_match(key, selector.labels):
                continue
            actual = dict(key[1])
            if label not in actual:
                continue
            before = old.get(key, 0.0)
            delta = after - before
            if delta < 0:
                delta = after
            grouped[actual[label]] = grouped.get(actual[label], 0.0) + delta / float(window)
        if not grouped:
            raise NoData("分组后无序列")
        return max(grouped.values())


def new_evaluator(store: Optional[SampleStore] = None) -> Evaluator:
    return Evaluator(store)


#: 兼容单元测试的便捷入口：求一条表达式并返回 (status, value, reason)
def evaluate_expression(expr: str, store: Optional[SampleStore] = None) -> Dict[str, object]:
    evaluator = Evaluator(store)
    value, status = evaluator.evaluate(expr)
    if ":" in status:
        status, reason = status.split(":", 1)
    else:
        reason = ""
    return {"status": status, "value": value, "reason": reason}
