"""Grafana 面板指标名称检查器

扫描所有 dashboard JSON 中的 PromQL 表达式，提取指标名称，
与规则文件中的指标名称对比，找出硬编码的旧版/不匹配指标。

检查项:
  1. dashboard 中引用但规则文件中不存在的指标（可能已废弃）
  2. 规则文件中定义但 dashboard 未使用的指标（监控盲区）
  3. 指标名称命名一致性（旧版命名 vs 新版命名）

运行: python scripts/check_grafana_metric_names.py
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DASHBOARDS_DIR = PROJECT_ROOT / "monitoring" / "grafana" / "dashboards"
# [不易-BUGFIX] 规则文件实际位于 monitoring/prometheus/ 下（alert_rules.yml），
# 原路径 monitoring/alerts.yml 不存在，导致 alert_rules.yml 中的 Yunshu_interaction_*
# 等指标被漏扫。改为扫描 monitoring/prometheus/ 下所有 .yml + rules/*.yml
RULES_DIR = PROJECT_ROOT / "monitoring" / "prometheus" / "rules"
ALERTS_FILE = PROJECT_ROOT / "monitoring" / "prometheus" / "alert_rules.yml"

# 指标名称正则：同时匹配小写 yunshu_ 和大写 Yunshu_ 前缀（prometheus.py 用 namespace="Yunshu"）
# [变易] 注意：无前缀指标（http_requests_total/security_blocks_total/system_cpu_usage_percent 等，
# 定义在 utils/prometheus_monitor.py）无法被此正则匹配，需结合代码层审计。此处仅覆盖规则文件常用前缀。
METRIC_RE = re.compile(r'\b(?:yunshu_|Yunshu_)\w+\b')


def extract_metrics_from_rules(filepath: Path) -> set[str]:
    """从规则 YAML 文件提取指标名称"""
    if not filepath.exists():
        return set()
    content = filepath.read_text(encoding="utf-8")
    # 排除 alert group 名称（name: yunshu_reranker_p0 等不是指标）
    metrics = set()
    for line in content.split("\n"):
        # 跳过 name: 行（alert group 名称）
        if re.match(r'\s*-\s*name:\s', line):
            continue
        # 跳过 alert: 行（alert 名称）
        if re.match(r'\s*-\s*alert:\s', line):
            continue
        for m in METRIC_RE.findall(line):
            metrics.add(m)
    return metrics


def extract_metrics_from_dashboard(filepath: Path) -> dict[str, list[str]]:
    """从 dashboard JSON 提取每个面板的 PromQL 指标

    返回: {metric_name: [panel_titles]}
    """
    if not filepath.exists():
        return {}
    data = json.loads(filepath.read_text(encoding="utf-8"))
    metric_map: dict[str, list[str]] = {}

    def process_panel(panel: dict):
        title = panel.get("title", f"panel-{panel.get('id','?')}")
        for target in panel.get("targets", []):
            expr = target.get("expr", "")
            if not expr:
                continue
            for m in METRIC_RE.findall(expr):
                metric_map.setdefault(m, []).append(title)
        # 递归处理嵌套面板（row panels）
        for sub in panel.get("panels", []):
            process_panel(sub)

    for panel in data.get("panels", []):
        process_panel(panel)

    return metric_map


def main():
    print("=" * 70)
    print("  Grafana Dashboard Metric Name Audit")
    print("=" * 70)
    print()

    # 1. 收集规则文件中的指标名称
    rule_metrics: set[str] = set()
    rule_files = []
    if RULES_DIR.exists():
        rule_files = list(RULES_DIR.glob("*.yml"))
    if ALERTS_FILE.exists():
        rule_files.append(ALERTS_FILE)

    print(f"[Rule Files] {len(rule_files)} files scanned")
    for rf in sorted(rule_files):
        rel = rf.relative_to(PROJECT_ROOT)
        metrics = extract_metrics_from_rules(rf)
        rule_metrics |= metrics
        print(f"  {rel}: {len(metrics)} metrics")

    # 排除 alert group 名称（非指标）
    non_metric_patterns = {"yunshu_reranker_p0", "yunshu_reranker_p1", "yunshu_reranker_p2",
                           "yunshu_v6_query_pattern_p0", "yunshu_v6_query_pattern_p1",
                           "yunshu_v6_query_pattern_p2", "yunshu_v62_negative_intent_alerts"}
    rule_metrics -= non_metric_patterns

    print(f"\n[Rule Metrics] {len(rule_metrics)} unique metric names:")
    for m in sorted(rule_metrics):
        print(f"  {m}")

    # 2. 收集 dashboard 中的指标名称
    print()
    dashboard_files = sorted(DASHBOARDS_DIR.glob("*.json"))
    print(f"[Dashboards] {len(dashboard_files)} files scanned")

    all_dashboard_metrics: dict[str, dict[str, list[str]]] = {}
    dashboard_metric_set: set[str] = set()

    for df in dashboard_files:
        rel = df.relative_to(PROJECT_ROOT)
        metric_map = extract_metrics_from_dashboard(df)
        if metric_map:
            all_dashboard_metrics[rel.name] = metric_map
            dashboard_metric_set |= set(metric_map.keys())
            print(f"  {rel.name}: {len(metric_map)} metrics")

    # 3. 对比分析
    print()
    print("=" * 70)
    print("  Audit Results")
    print("=" * 70)

    # 3.1 dashboard 中有但规则文件中没有的指标（可能已废弃/旧版）
    orphaned = dashboard_metric_set - rule_metrics
    print(f"\n[1] Metrics in Dashboards but NOT in Rule Files ({len(orphaned)}):")
    if orphaned:
        for m in sorted(orphaned):
            locations = []
            for dname, mmap in all_dashboard_metrics.items():
                if m in mmap:
                    locations.append(f"{dname}({', '.join(set(mmap[m]))})")
            print(f"  ⚠ {m}")
            for loc in locations:
                print(f"      → {loc}")
    else:
        print("  ✅ No orphaned metrics")

    # 3.2 规则文件中有但 dashboard 中没有的指标（监控盲区）
    missing = rule_metrics - dashboard_metric_set
    print(f"\n[2] Metrics in Rule Files but NOT in Dashboards ({len(missing)}):")
    if missing:
        for m in sorted(missing):
            print(f"  ℹ {m} (no dashboard panel displays this metric)")
    else:
        print("  ✅ All rule metrics have dashboard panels")

    # 3.3 命名一致性检查（旧版命名模式）
    print(f"\n[3] Naming Convention Check:")
    # 检查是否有旧版命名（如不带 _total 后缀的 counter，或 _ms 后缀的 histogram）
    naming_issues = []
    for m in dashboard_metric_set:
        # 检查 histogram 的 _bucket/_sum/_count 变体是否一致
        if m.endswith("_ms") and not m.endswith("_ms_bucket") and not m.endswith("_ms_sum") and not m.endswith("_ms_count"):
            # 检查是否有对应的 _bucket 变体
            bucket_var = m + "_bucket"
            if bucket_var in rule_metrics and m not in rule_metrics:
                naming_issues.append(f"  ⚠ {m}: histogram base name used but only {bucket_var} exists in rules")

    if naming_issues:
        for issue in naming_issues:
            print(issue)
    else:
        print("  ✅ No naming convention issues")

    # 4. 各 dashboard 详情
    print(f"\n[4] Dashboard Detail:")
    for dname, mmap in sorted(all_dashboard_metrics.items()):
        print(f"\n  {dname} ({len(mmap)} metrics):")
        for m in sorted(mmap.keys()):
            in_rules = "✅" if m in rule_metrics else "⚠️"
            panels = ", ".join(sorted(set(mmap[m])))
            print(f"    {in_rules} {m}  [panels: {panels}]")

    # 5. 汇总
    print()
    print("=" * 70)
    print("  Summary")
    print("=" * 70)
    print(f"  Rule metrics:        {len(rule_metrics)}")
    print(f"  Dashboard metrics:   {len(dashboard_metric_set)}")
    print(f"  Orphaned (old?):     {len(orphaned)}")
    print(f"  Missing (no panel):  {len(missing)}")
    print(f"  Naming issues:       {len(naming_issues)}")

    if len(orphaned) == 0 and len(naming_issues) == 0:
        print()
        print("  ✅ PASS: All dashboard metrics match rule file structure")
        return 0
    else:
        print()
        print("  ⚠ ACTION NEEDED: Review orphaned metrics and naming issues")
        return 1


if __name__ == "__main__":
    sys.exit(main())
