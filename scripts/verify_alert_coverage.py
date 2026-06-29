"""告警规则覆盖度验证脚本

检查 alerts.yml 中基于 yunshu_resource_usage 的告警是否覆盖
所有 4 种资源类型（memory/thread/file_handle/db_connection）的
warning + critical 双级阈值。
"""
import re
import sys
from pathlib import Path

import yaml

EXPECTED_RESOURCES = ["memory", "thread", "file_handle", "db_connection"]
EXPECTED_LEVELS = ["warning", "critical"]


def main():
    alerts_path = Path("monitoring/alerts.yml")
    if not alerts_path.exists():
        print(f"[ERROR] {alerts_path} 不存在", file=sys.stderr)
        return 1

    rules = yaml.safe_load(alerts_path.read_text(encoding="utf-8"))
    resource_rules = []
    for group in rules.get("groups", []):
        if group.get("name") != "yunshu_resources":
            continue
        for rule in group.get("rules", []):
            if "alert" not in rule:
                continue
            expr = rule.get("expr", "")
            if "yunshu_resource_usage" in expr:
                resource_rules.append(rule)

    print(f"=== 告警覆盖度验证: {alerts_path} ===")
    print(f"基于 yunshu_resource_usage 的告警总数: {len(resource_rules)}\n")

    # 解析每条告警覆盖的资源类型与级别
    coverage = {}  # resource_type -> set(severities)
    print("告警清单:")
    for rule in resource_rules:
        alert = rule["alert"]
        expr = rule["expr"]
        severity = rule.get("labels", {}).get("severity", "?")
        # 提取 resource_type 标签
        m = re.search(r'resource_type="([^"]+)"', expr)
        rtype = m.group(1) if m else "unknown"
        coverage.setdefault(rtype, set()).add(severity)
        print(f"  {alert:40s} type={rtype:15s} severity={severity}")

    print(f"\n=== 覆盖度矩阵 ===")
    all_ok = True
    for rtype in EXPECTED_RESOURCES:
        levels = coverage.get(rtype, set())
        for level in EXPECTED_LEVELS:
            status = "OK" if level in levels else "MISSING"
            if level not in levels:
                all_ok = False
            print(f"  {rtype:15s} {level:10s}: {status}")

    print()
    if all_ok:
        print(f"[PASS] 所有 4 种资源类型均已覆盖 warning + critical 双级告警")
        return 0
    else:
        print(f"[FAIL] 存在缺失的告警级别，请补全", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
