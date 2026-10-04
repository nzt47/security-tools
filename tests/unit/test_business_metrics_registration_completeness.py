"""业务指标登记完整性回归测试（防「埋点写了但没人读」再次发生）。

【为什么需要这个测试】
    2026-10-02 排查发现：agent/ 下 83 个 emit_metric("名字", ...) 调用里，只有 11 个名字
    登记在 BUSINESS_METRICS_DEFINITIONS。而 BusinessMetricsCollector.export_prometheus()
    的遍历源**就是那张表** ⇒ 另外 72 个名字虽然埋点写进了内存字典，但
    /api/business/prometheus（Prometheus 的 yunshu-business job 抓的端点）里连指标名
    都没有 ⇒ 所有依赖它们的告警**恒不触发**。这类缺陷在运行期完全静默（不报错、不打
    日志），只能靠静态断言守住。

【判据】
    1. 每个 emit_metric(<字面量名字>, ...) 的名字都必须在登记表里；
    2. 登记的类型（counter/gauge/histogram）必须与该调用点传的 kind 一致 ——
       类型写错的表现同样是「样本行永远为空」（导出器去另一个内部字典取值）。
"""
from __future__ import annotations

import ast
import pathlib
import re
from typing import Dict, List, Optional, Set, Tuple

from agent.monitoring.business_metrics import BUSINESS_METRICS_DEFINITIONS

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_AGENT_ROOT = _REPO_ROOT / "agent"

# 允许豁免的文件（相对 agent/ 的 posix 路径）：文件里的 emit_metric 不写业务收集器。
# 目前为空 —— agent/monitoring/ 下**没有** emit_metric 调用（原生 prometheus_client
# 指标走 _record_skill_match_prometheus 等专用函数），不需要豁免。
_EXEMPT_FILES: Set[str] = set()

_NL = chr(10)


#: 便宜预筛：文件文本里没有这个子串就**不必** ast.parse。
#:
#: 【为什么加它 —— 2026-10-04 实测的超时，不是假想】
#: 本文件的两个用例各自调一次 _scan()，而 _scan() 会对 agent/ 下**每个** .py
#: 做 ast.parse + ast.walk。在 CI 共享 runner 上（同分片 19m16s vs 别的分片 9m32s，
#: 负载明显更重）这两次全树 AST 扫描**双双超时**：
#:     Failed: Timeout (>60.0s) from pytest-timeout   × 2
#: 实测表现与本节开头的成因完全一致（"埋点写了但没人读"是断言失败，
#: 而这里是**超时失败** —— 两者必须分开读，否则会去查一个不存在的登记缺口）。
#:
#: 【这与本仓已有的一次修复同源】scripts/audit/contract_diff.py 的 collect_routes_static()
#: 曾因同样原因（全树扫描 × 5 个用例各调一次）全部超时，当时的修法就是
#: **正则预筛 + 记忆化**（4.35s → 0.67s / 0.00s）。此处照搬同一套修法。
#:
#: 【预筛为什么不会漏】它是**文本子串**匹配，只能"多留"不能"少留"：
#: 任何真的含 emit_metric 调用的文件，其源码文本里必然出现这个子串 ⇒ 一定会被 parse。
#: 换句话说预筛放宽是安全的，而 AST 判定仍然是唯一的事实源（docstring 里的示例
#: 即使被 parse 到，也不会有 ast.Call 节点，故不会被当成调用 —— 本仓已有前车之鉴）。
_HINT = re.compile(r"emit_metric")

#: 记忆化：两个用例共享一次扫描。返回的列表只被**读取**，故可直接共享。
_SCAN_CACHE: Optional[Tuple[List[Tuple[str, str, str]], List[str]]] = None


def _scan() -> Tuple[List[Tuple[str, str, str]], List[str]]:
    """扫描 agent/ 下全部 emit_metric 调用，返回 ([(名字, 类型, 位置)], [动态名字位置])

    结果为**记忆化**的：同一进程内重复调用几乎零成本（见 _HINT 的说明）。
    """
    global _SCAN_CACHE
    if _SCAN_CACHE is not None:
        return _SCAN_CACHE

    found: List[Tuple[str, str, str]] = []
    dynamic: List[str] = []
    for path in sorted(_AGENT_ROOT.rglob("*.py")):
        rel = path.relative_to(_AGENT_ROOT).as_posix()
        if rel in _EXEMPT_FILES or rel == "monitoring/business_metrics.py":
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not _HINT.search(text):  # 便宜预筛，见上方说明
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:  # 语法错误由别的门禁负责
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if not (isinstance(fn, ast.Name) and fn.id == "emit_metric"):
                continue
            site = "%s:%d" % (rel, node.lineno)
            if not node.args:
                dynamic.append(site)
                continue
            first = node.args[0]
            if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
                dynamic.append(site)
                continue
            name = first.value
            kind = "counter"  # emit_metric 的形参默认值
            for kw in node.keywords:
                if kw.arg == "kind":
                    if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                        kind = kw.value.value
                    else:
                        kind = "<dynamic>"  # 类型不可静态判定，跳过类型断言
            found.append((name, kind, site))
    _SCAN_CACHE = (found, dynamic)
    return _SCAN_CACHE


class TestEmitMetricRegistrationCompleteness:
    """agent/ 下每个 emit_metric 名字都必须已登记，且类型一致"""

    def test_no_unregistered_metric_names(self):
        found, _dynamic = _scan()
        missing: Dict[str, List[str]] = {}
        for name, _kind, site in found:
            if name not in BUSINESS_METRICS_DEFINITIONS:
                missing.setdefault(name, []).append(site)
        assert not missing, (
            "以下 emit_metric 指标名没有登记进 BUSINESS_METRICS_DEFINITIONS，埋点会写进"
            "内存字典但 /api/business/prometheus 里看不到（依赖它的告警恒不触发）："
            + _NL + _NL.join("  %s  <= %s" % (n, ", ".join(s)) for n, s in sorted(missing.items()))
        )

    def test_扫描非空且量级正常(self):
        """防"空集假通过"：预筛若把文件漏掉，扫描结果会退化成空表，
        而上面两条断言在空表下**都会通过**（"没有未登记的" = 真空）。
        故单列一条把"扫到的东西够不够"钉住。

        【判据为什么不写死具体数字】埋点数会随代码演进变化（本文件开头记录的是 83）。
        这里只要求同一量级（>50），既挡得住"预筛漏成空集"，也不会因为新增/删除埋点
        而假红 —— 本会话刚因为"把会变的数值写成契约"红过三条 CI 作业。
        """
        found, _dynamic = _scan()
        assert len(found) > 50, (
            "扫描到的 emit_metric 调用点只有 " + str(len(found)) + " 个，量级不对 —— "
            "预筛（_HINT）可能把文件漏掉了，此时上面两条断言会**空集假通过**"
        )

    def test_记忆化不改变结果(self):
        """记忆化只允许省时间，不允许改结果。"""
        first = _scan()
        second = _scan()
        assert first == second
        assert first is second, "第二次调用没有走缓存 —— 记忆化失效（超时会回来）"

    def test_registered_type_matches_emit_kind(self):
        found, _dynamic = _scan()
        bad: List[str] = []
        for name, kind, site in found:
            defn = BUSINESS_METRICS_DEFINITIONS.get(name)
            if defn is None or kind == "<dynamic>":
                continue
            if defn.metric_type != kind:
                bad.append("%s: 调用点 kind=%s 但登记 metric_type=%s (%s)"
                           % (name, kind, defn.metric_type, site))
        assert not bad, (
            "emit_metric 的 kind 与登记的 metric_type 不一致 —— 导出器会去另一个内部"
            "字典取值，样本行将永远为空：" + _NL + _NL.join("  " + b for b in sorted(bad))
        )

    def test_reranker_alerts_metrics_are_registered(self):
        """reranker-alerts.yml 直接引用的指标名必须在登记表里（否则该文件是整份死规则）"""
        required = [
            "yunshu_rerank_duration_ms",
            "yunshu_reranker_load_total",
            "yunshu_reranker_load_time_seconds",
            "yunshu_reranker_fallback_total",
            "yunshu_reranker_completed_total",
            "yunshu_reranker_predict_failed_total",
        ]
        missing = [n for n in required if n not in BUSINESS_METRICS_DEFINITIONS]
        assert not missing, "reranker 告警依赖的指标未登记: %s" % missing
