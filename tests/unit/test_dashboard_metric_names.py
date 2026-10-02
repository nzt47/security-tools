# -*- coding: utf-8 -*-
"""看板指标名真实性守卫（§10 B-1 的防复发断言）

【为什么要这个测试】
2026-10-02 的监控清理发现：Grafana 看板里大量面板引用了**本仓根本不存在的指标名** ——
大驼峰 `Yunshu_*` 命名空间（对应那个从未实例化的 exporter）、旧名
（`system_cpu_usage_percent` / `http_requests_total`，真名都带 `yunshu_` 前缀）、
以及只有 pushgateway 链路才会产生的 CI/部署指标。后果是「面板永远空白，却没人知道为什么」，
只能靠人工 grep 事后发现（这次就是）。

本测试把「看板里的每个指标名都必须能追溯到某个产出它的代码 / 记录规则」固化成断言：
新增面板时若写了不存在的名字，CI 直接红，而不是等几个月后有人对着一片空白发呆。

【判据（静态可判，不依赖运行中的服务）】
known = `BUSINESS_METRICS_DEFINITIONS`（业务指标登记表）
      ∪ 生产代码里的 `yunshu_*` / `Yunshu_*` 字符串字面量（prometheus_client 定义与埋点）
      ∪ monitoring 下所有 `record:` 记录规则名（形如 `yunshu:latency_p95:5m`）

【已知豁免（必须显式、必须带说明）】
`_TOLERATED` 里是 10 个 CI/CD 与部署/回滚指标：产出方是 pushgateway 推送链路，本部署没有接入
⇒ 面板恒空。这些面板**保留**，但已就地写入「本部署无数据源」的 description；
接入 pushgateway 后应把名字补进产出侧并清空本集合。
第二个测试断言这些面板确实带着说明，防止「豁免了却没解释」。

【不覆盖（静态判不了）】
「名字存在但没有数据」：例如 `yunshu_safe_file_reader_*` 由 utils/file_reader.py 发射，
而该文件在非测试代码里没有调用方 ⇒ 指标恒为 0、9 条告警恒不触发。这属于**接线**问题，
见 docs/closeout/能力层重构交付报告_20261001.md §10（A 类）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# 看板所在目录：
#   1) monitoring/grafana/dashboards/ —— compose 真正 provisioning 的目录（权威）
#   2) monitoring/grafana_dashboards/ —— 遗留目录（不再被 compose 加载，但仍会被人工导入）
#   3) monitoring/                    —— 早期散落的看板
DASHBOARD_DIRS = (
    ROOT / "monitoring" / "grafana" / "dashboards",
    ROOT / "monitoring" / "grafana_dashboards",
    ROOT / "monitoring",
)

# 生产代码扫描范围：排除测试、文档、脚本与各种归档 / 缓存目录
_EXCLUDED_PARTS = {
    "tests", "docs", "_scratch", "demos", "backup", "backups", ".git", "node_modules",
    "yunshu-ui", "htmlcov", "htmlcov_planning", "coverage_report", "build", "release",
    "releases", ".pytest_tmp", "test_dir", "tmp_backup_test", "restored-frontend",
    "env_archive", "_edge_profile", "__pycache__", ".mypy_cache", ".ruff_cache",
    ".pytest_cache", ".benchmarks", "pytest_chunks", "test_data", "test_reports",
    "_ci_logs", "_t06_logs", "_t08_logs",
}

_METRIC_RE = re.compile(r"\b(?:yunshu_|Yunshu_)\w+")
_LITERAL_RE = re.compile(r"""['"]((?:yunshu|Yunshu)_[A-Za-z0-9_]+)['"]""")
_RECORD_RE = re.compile(r"record:\s*([A-Za-z0-9_:]+)")

#: prometheus_client 为直方图 / 计数器自动派生的后缀
_DERIVED_SUFFIXES = ("_bucket", "_count", "_sum", "_created", "_total")

#: 已知豁免：产出方是 pushgateway 推送链路（本部署未接入）的 CI/CD 与部署指标。
#: 对应面板已写入「本部署无数据源」说明；接入链路后应清空本集合。
_TOLERATED = {
    "Yunshu_ci_build_failures_total",
    "Yunshu_ci_pipeline_duration_seconds",
    "Yunshu_ci_pipeline_runs_total",
    "Yunshu_ci_test_coverage_percent",
    "Yunshu_ci_test_failures_total",
    "Yunshu_deployment_duration_seconds",
    "Yunshu_deployment_failures_total",
    "Yunshu_deployment_status",
    "Yunshu_deployment_total",
    "Yunshu_rollback_total",
}


def _known_metric_names() -> set:
    """本仓能产出（或已登记）的指标名集合"""
    known = set()

    from agent.monitoring.business_metrics import BUSINESS_METRICS_DEFINITIONS

    known |= set(BUSINESS_METRICS_DEFINITIONS.keys())

    for path in ROOT.rglob("*.py"):
        if set(path.relative_to(ROOT).parts[:-1]) & _EXCLUDED_PARTS:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:  # pragma: no cover - 权限 / 占用等异常
            continue
        known |= set(_LITERAL_RE.findall(text))

    for path in (ROOT / "monitoring").rglob("*.yml"):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:  # pragma: no cover
            continue
        known |= set(_RECORD_RE.findall(text))

    return known


def _is_known(name: str, known: set) -> bool:
    if name in known:
        return True
    for suffix in _DERIVED_SUFFIXES:
        if name.endswith(suffix) and name[: -len(suffix)] in known:
            return True
    return False


def _iter_panels(node):
    """深度遍历面板树（row 面板里还嵌着子面板）"""
    if isinstance(node, dict):
        if "targets" in node or "panels" in node or "title" in node:
            yield node
        for value in node.values():
            yield from _iter_panels(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_panels(item)


def _dashboard_metric_refs():
    """返回 [(看板相对路径, 面板标题, 指标名, 面板说明)]"""
    refs = []
    for directory in DASHBOARD_DIRS:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            rel = str(path.relative_to(ROOT))
            for panel in _iter_panels(data.get("panels", [])):
                title = str(panel.get("title", "?"))
                desc = str(panel.get("description", ""))
                for target in panel.get("targets") or []:
                    expr = target.get("expr") or ""
                    for name in _METRIC_RE.findall(expr):
                        refs.append((rel, title, name, desc))
    return refs


def test_all_dashboard_metrics_are_producible():
    """看板引用的每个指标名都必须能追溯到产出它的代码 / 记录规则

    失败时的读法：报错里的 `<看板> :: <面板> -> <指标>` 就是「永远空白的面板」。
    修法二选一：① 把查询改成真实指标名；② 确认本部署确实没有产出方时，
    删掉面板，或保留但在面板 description 里写明原因并加入 `_TOLERATED`。
    """
    known = _known_metric_names()
    assert len(known) > 100, "指标名集合异常偏小，判据本身可能已失效"

    unknown = {}
    for rel, title, name, _desc in _dashboard_metric_refs():
        if _is_known(name, known) or name in _TOLERATED:
            continue
        unknown.setdefault((rel, title), set()).add(name)

    if unknown:
        lines = [
            "%s :: %s -> %s" % (rel, title, sorted(names))
            for (rel, title), names in sorted(unknown.items())
        ]
        pytest.fail(
            "以下看板面板引用了本仓不存在的指标名（这些面板会永远空白）：\n  "
            + ("\n  ".join(lines))
            + "\n\n修法：改成真实指标名，或删掉面板，或保留并写明原因后加入 _TOLERATED。"
        )


def test_tolerated_panels_explain_themselves():
    """被豁免的指标所在面板必须写明「无数据源」

    为什么单列一条：豁免表本身会退化成「合法的静默空白」。
    只有把「豁免 = 必须带说明」也断言住，豁免才是有信息量的。
    """
    missing = []
    for rel, title, name, desc in _dashboard_metric_refs():
        if name not in _TOLERATED:
            continue
        if "无数据源" not in desc:
            missing.append("%s :: %s -> %s" % (rel, title, name))

    if missing:
        pytest.fail(
            "以下面板的指标被 _TOLERATED 豁免，但面板没有说明原因"
            "（description 里需含「无数据源」）：\n  " + "\n  ".join(sorted(set(missing)))
        )
