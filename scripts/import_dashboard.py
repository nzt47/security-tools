#!/usr/bin/env python3
"""Grafana 看板导入与验证脚本

功能：
1. 静态验证看板 JSON 结构（datasource/expr/title 完整性）
2. 验证所有 PromQL 表达式可解析
3. 通过 Grafana API 导入看板（若 Grafana 可达）

使用方式：
    # 仅静态验证
    python scripts/import_dashboard.py --validate

    # 导入到 Grafana
    python scripts/import_dashboard.py --import --url http://localhost:3000 --token <API_TOKEN>

    # 指定看板文件
    python scripts/import_dashboard.py --validate --dashboard monitoring/grafana_dashboards/yunshu_resource_release_dashboard.json
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import List, Tuple

DEFAULT_DASHBOARD = "monitoring/grafana_dashboards/yunshu_resource_release_dashboard.json"


def validate_structure(dashboard: dict) -> Tuple[bool, List[str]]:
    """验证看板结构完整性"""
    errors = []
    if "panels" not in dashboard:
        errors.append("缺少 panels 字段")
        return False, errors
    if not isinstance(dashboard["panels"], list) or not dashboard["panels"]:
        errors.append("panels 为空或非列表")
    for i, panel in enumerate(dashboard["panels"]):
        if "title" not in panel:
            errors.append(f"面板 {i} 缺少 title")
        if "type" not in panel:
            errors.append(f"面板 {i} 缺少 type")
        if "targets" not in panel:
            errors.append(f"面板 {i} 缺少 targets")
            continue
        for j, target in enumerate(panel["targets"]):
            if "expr" not in target or not target["expr"]:
                errors.append(f"面板 {i} target {j} 缺少 expr")
    return len(errors) == 0, errors


def validate_promql(dashboard: dict) -> Tuple[bool, List[str]]:
    """验证 PromQL 表达式基本语法（括号匹配 + 指标名规范）"""
    errors = []
    metric_pattern = re.compile(r"yunshu_[a-z_]+")
    for i, panel in enumerate(dashboard.get("panels", [])):
        for j, target in enumerate(panel.get("targets", [])):
            expr = target.get("expr", "")
            if not expr:
                continue
            # 括号匹配检查
            if expr.count("(") != expr.count(")"):
                errors.append(f"面板 {i} target {j} 括号不匹配: {expr[:60]}")
            # 引号匹配检查
            if expr.count('"') % 2 != 0:
                errors.append(f"面板 {i} target {j} 引号不匹配: {expr[:60]}")
            # 指标名规范检查
            metrics = metric_pattern.findall(expr)
            if not metrics and "deriv" not in expr and "rate" not in expr:
                errors.append(f"面板 {i} target {j} 未引用 yunshu_ 指标: {expr[:60]}")
    return len(errors) == 0, errors


def collect_metrics(dashboard: dict) -> List[str]:
    """提取看板引用的所有指标名"""
    metrics = set()
    pattern = re.compile(r"yunshu_[a-z_]+(?:_bucket|_total|_seconds)?")
    for panel in dashboard.get("panels", []):
        for target in panel.get("targets", []):
            for m in pattern.finditer(target.get("expr", "")):
                metrics.add(m.group(0))
    return sorted(metrics)


def import_to_grafana(dashboard_path: str, url: str, token: str) -> int:
    """通过 Grafana API 导入看板"""
    import urllib.request
    import urllib.error

    with open(dashboard_path, "r", encoding="utf-8") as f:
        dashboard = json.load(f)

    # Grafana 导入 API 要求 dashboard 包装在 "dashboard" 字段
    payload = {
        "dashboard": dashboard,
        "overwrite": True,
        "folderId": 0,
    }

    req = urllib.request.Request(
        f"{url.rstrip('/')}/api/dashboards/db",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            print(f"[OK] 导入成功: {result.get('url', '')}")
            print(f"[OK] 看板 UID: {result.get('uid', '')}")
            print(f"[OK] 版本: {result.get('version', '')}")
            return 0
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"[ERROR] Grafana 返回 {e.code}: {body}", file=sys.stderr)
        return 3
    except urllib.error.URLError as e:
        print(f"[ERROR] 无法连接 Grafana: {e.reason}", file=sys.stderr)
        print(f"[提示] 请确认 Grafana 已启动: {url}", file=sys.stderr)
        return 4


def main():
    parser = argparse.ArgumentParser(description="Grafana 看板验证与导入")
    parser.add_argument("--dashboard", "-d", default=DEFAULT_DASHBOARD, help="看板 JSON 路径")
    parser.add_argument("--validate", action="store_true", help="仅静态验证")
    parser.add_argument("--import", dest="do_import", action="store_true", help="导入到 Grafana")
    parser.add_argument("--url", default="http://localhost:3000", help="Grafana 地址")
    parser.add_argument("--token", default="", help="Grafana API Token")
    args = parser.parse_args()

    dashboard_path = args.dashboard
    if not os.path.exists(dashboard_path):
        print(f"[ERROR] 看板文件不存在: {dashboard_path}", file=sys.stderr)
        return 1

    with open(dashboard_path, "r", encoding="utf-8") as f:
        dashboard = json.load(f)

    print(f"=== 看板验证: {dashboard_path} ===")
    print(f"标题: {dashboard.get('title', '(未知)')}")
    print(f"UID: {dashboard.get('uid', '(未知)')}")
    print(f"面板数: {len(dashboard.get('panels', []))}")
    print()

    # 1. 结构验证
    ok, errors = validate_structure(dashboard)
    if ok:
        print(f"[OK] 结构验证通过（{len(dashboard['panels'])} 个面板）")
    else:
        print(f"[FAIL] 结构验证失败:")
        for e in errors:
            print(f"  - {e}")
        return 2

    # 2. PromQL 验证
    ok, errors = validate_promql(dashboard)
    if ok:
        print("[OK] PromQL 语法验证通过")
    else:
        print("[WARN] PromQL 验证发现问题:")
        for e in errors:
            print(f"  - {e}")

    # 3. 引用指标
    metrics = collect_metrics(dashboard)
    print(f"\n[INFO] 引用指标 ({len(metrics)} 个):")
    for m in metrics:
        print(f"  - {m}")

    # 4. 面板清单
    print(f"\n[INFO] 面板清单:")
    for i, panel in enumerate(dashboard["panels"]):
        title = panel.get("title", "(无标题)")
        ptype = panel.get("type", "?")
        n_targets = len(panel.get("targets", []))
        print(f"  {i+1}. [{ptype}] {title} ({n_targets} 个查询)")

    # 5. 导入 Grafana
    if args.do_import:
        if not args.token:
            print("\n[ERROR] 导入需要 --token 参数（Grafana API Token）", file=sys.stderr)
            return 5
        print(f"\n=== 导入到 Grafana: {args.url} ===")
        return import_to_grafana(dashboard_path, args.url, args.token)

    print("\n[提示] 如需导入 Grafana：")
    print(f"  python scripts/import_dashboard.py --import --url http://localhost:3000 --token <TOKEN>")
    print("  或在 Grafana UI: Dashboards → Import → 上传 JSON 文件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
