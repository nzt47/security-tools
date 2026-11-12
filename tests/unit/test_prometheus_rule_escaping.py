# -*- coding: utf-8 -*-
"""Prometheus 规则文件的**转义**守卫（2026-11-12，CI 红了一天多的真实故障）。

【这条用例防什么（现场实证）】
`monitoring/alerts.yml` 的 P99/P95 两条规则曾写成 `endpoint!~".*|modules\.topology|…"`
（单个反斜杠 + 点）。Go/PromQL 双引号字符串里 `\.` 是**非法转义** ⇒ Prometheus 拒绝
加载**整个规则文件**，容器起不来：

    Error loading rule file patterns from config … /etc/prometheus/alerts.yml: 102:15:
    could not parse expression: 2:66: parse error: unknown escape sequence U+002E '.'

后果有两层，都要钉住：
1. 「可观测性质量保障」工作流的 e2e job 因此红了一天多（失败步骤是"启动 Prometheus 容器"，
   看起来像基础设施问题，其实是**我们自己 YAML 的 bug**）；
2. 那句告警在真实 Prometheus 里**从来没被加载过** —— 而应用内求值器当时用 `strip('"')`
   不还原转义，于是"本地算得出、线上加载不了"，判据与线上不一致。

【判据（三条）】
- 每个 rule_files 条目都能在**仓库里**找到（按 compose 的挂载映射），YAML 能解析；
- 每个表达式的双引号字符串里**没有非法转义**（Go 规则：反斜杠 + a/b/f/n/r/t/v/反斜杠/引号、
  八进制、十六进制与 Unicode 转义）；
- `monitoring/alerts.yml` 的 16 条能被应用内 PromQL 子集**全部解析**，且 P99 的排除集
  在语义上真的排掉 `modules.topology`（否则"改回单反斜杠"会让排除集失效却不报错）。
"""
from __future__ import annotations

from pathlib import Path

import yaml

from agent.alarms import promql, rules as rule_engine

ROOT = Path(__file__).resolve().parents[2]
MONITORING = ROOT / "monitoring"

#: 容器相对路径 → 仓库内真实路径（与 docker-compose.monitoring.yml 的挂载一一对应；
#: CI 的 observability-ci.yml 用同一份映射挂载，三处必须同进同退）
REPO_PATH_MAP = (
    ("rules/", "monitoring/prometheus/rules/"),
    ("alert_rules.yml", "monitoring/prometheus/alert_rules.yml"),
    ("skill_retrieval_alerts.yml", "monitoring/alerts/skill_retrieval_alerts.yml"),
)

#: Go/PromQL 双引号字符串里合法的一次性转义
_SIMPLE_ESCAPES = set('abfnrtv\\"')
#: 合法的一次性转义前缀（后接 2/4/8 位十六进制或 3 位八进制）
_NUMERIC_ESCAPES = set("01234567xuU")


def _rule_files() -> list[str]:
    conf = yaml.safe_load((MONITORING / "prometheus.yml").read_text(encoding="utf-8"))
    return [str(item) for item in (conf.get("rule_files") or [])]


def _repo_path(rel: str) -> Path:
    """容器相对路径 → 仓库路径（先按 monitoring/ 直读，读不到再按 compose 映射）。"""
    direct = MONITORING / rel
    if direct.is_file():
        return direct
    for prefix, target in REPO_PATH_MAP:
        if rel.startswith(prefix):
            return ROOT / (target + rel[len(prefix):])
    return direct


def _exprs(path: Path) -> list[str]:
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out: list[str] = []
    for group in doc.get("groups") or []:
        for rule in group.get("rules") or []:
            expr = rule.get("expr") or rule.get("record")
            if expr:
                out.append(str(expr))
    return out


def illegal_escapes(expr: str) -> list[str]:
    """列出双引号字符串里的非法转义（Go 规则；返回形如 ['\\.'] ）。"""
    bad: list[str] = []
    index = 0
    in_string = False
    size = len(expr)
    while index < size:
        char = expr[index]
        if not in_string:
            if char == '"':
                in_string = True
            index += 1
            continue
        if char == '"':
            in_string = False
            index += 1
            continue
        if char == "\\":
            nxt = expr[index + 1] if index + 1 < size else ""
            if nxt in _SIMPLE_ESCAPES or nxt in _NUMERIC_ESCAPES:
                index += 2
                continue
            bad.append("\\" + nxt)
            index += 2
            continue
        index += 1
    return bad


# ════════════════════════════════════════════════════════════
#  判据 1：rule_files 全部可解析
# ════════════════════════════════════════════════════════════

def test_every_rule_file_exists_and_parses():
    files = _rule_files()
    assert files, "monitoring/prometheus.yml 应列出 rule_files"
    missing = [rel for rel in files if not _repo_path(rel).is_file()]
    assert missing == [], "rule_files 列了容器内不存在的文件 ⇒ Prometheus 启动失败: %s" % missing
    for rel in files:
        doc = yaml.safe_load(_repo_path(rel).read_text(encoding="utf-8"))
        assert isinstance(doc, dict), rel
        assert doc.get("groups"), "%s 没有 groups（Prometheus 会报错）" % rel


# ════════════════════════════════════════════════════════════
#  判据 2：没有非法转义（本次故障的根因）
# ════════════════════════════════════════════════════════════

def test_no_illegal_promql_escapes_in_any_rule_file():
    offenders = []
    for rel in _rule_files():
        for expr in _exprs(_repo_path(rel)):
            bad = illegal_escapes(expr)
            if bad:
                offenders.append((rel, bad, expr.strip().splitlines()[0][:80]))
    assert offenders == [], (
        "PromQL 双引号字符串里有非法转义（Prometheus 会拒载整个文件；"
        "正则里的字面点要写两个反斜杠 + 点）: %s" % offenders)


# ════════════════════════════════════════════════════════════
#  判据 3：应用内求值器与线上同语义
# ════════════════════════════════════════════════════════════

def test_alerts_yml_expressions_all_parse_with_local_subset():
    """16 条规则必须都能被应用内 PromQL 子集解析（解析不了 = 求值器与规则漂移）。"""
    exprs = _exprs(MONITORING / "alerts.yml")
    assert len(exprs) == 16, "规则条数变了：%d（改条数请同步本断言与文档）" % len(exprs)
    failures = []
    for expr in exprs:
        try:
            promql.parse(expr)
        except Exception as exc:  # noqa: BLE001 解析失败要逐条列出来
            failures.append((type(exc).__name__, str(exc)[:80], expr[:60]))
    assert failures == [], failures


def test_p99_exclusion_set_really_excludes_scan_endpoints():
    """P99/P95 的排除集在**语义**上必须排掉 modules.topology 一类扫描端点。

    【为什么断言语义而不是字符串】"改回单反斜杠"会让 `\.` 变成非法转义（由前一条用例拦），
    而"把两个反斜杠写成四个"之类的手滑不会报错、只会让排除集失效（P99 又去算流式连接时长）。
    """
    rule = next(r for r in rule_engine.load_rules() if r["alert"] == "YunshuHighLatencyP99")
    parsed = promql.parse(rule["expr"])
    selector = _first_selector(parsed)
    assert selector is not None, "P99 表达式里应有 histogram bucket 选择器"
    labels = selector.labels
    excluded = ("modules.topology", "system_tools.api_process_list", "health.probe_trend",
                "api_skills_mgmt_assess_stream")
    for endpoint in excluded:
        assert not promql._series_match((selector.name, (("endpoint", endpoint),)), labels), endpoint
    # 普通端点必须留下（否则"排除集写宽了"会把全部流量排除掉，P99 永远无数据）
    for endpoint in ("api_chat", "api_health_probe", "api_skills_mgmt_list"):
        assert promql._series_match((selector.name, (("endpoint", endpoint),)), labels), endpoint


def _first_selector(node):
    """深度优先找第一个 selector（P99 是 histogram_quantile(sum(rate(selector[5m])))）。"""
    if getattr(node, "kind", "") == "selector":
        return node
    for arg in (getattr(node, "args", None) or []):
        found = _first_selector(arg)
        if found is not None:
            return found
    inner = getattr(node, "value", None)
    if hasattr(inner, "kind"):
        return _first_selector(inner)
    return None
