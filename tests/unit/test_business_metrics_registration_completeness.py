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
from typing import Dict, List, Set, Tuple

from agent.monitoring.business_metrics import BUSINESS_METRICS_DEFINITIONS

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_AGENT_ROOT = _REPO_ROOT / "agent"

# 允许豁免的文件（相对 agent/ 的 posix 路径）：文件里的 emit_metric 不写业务收集器。
# 目前为空 —— agent/monitoring/ 下**没有** emit_metric 调用（原生 prometheus_client
# 指标走 _record_skill_match_prometheus 等专用函数），不需要豁免。
_EXEMPT_FILES: Set[str] = set()

_NL = chr(10)


def _scan() -> Tuple[List[Tuple[str, str, str]], List[str]]:
    """扫描 agent/ 下全部 emit_metric 调用，返回 ([(名字, 类型, 位置)], [动态名字位置])"""
    found: List[Tuple[str, str, str]] = []
    dynamic: List[str] = []
    for path in sorted(_AGENT_ROOT.rglob("*.py")):
        rel = path.relative_to(_AGENT_ROOT).as_posix()
        if rel in _EXEMPT_FILES or rel == "monitoring/business_metrics.py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
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
    return found, dynamic


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
