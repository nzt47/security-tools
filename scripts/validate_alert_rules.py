"""校验 alert_rules.yml 语法与规则数"""
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: pyyaml 未安装", file=sys.stderr)
    sys.exit(1)

p = Path("monitoring/prometheus/rules/yunshu-v6-query-pattern-alerts.yml")
data = yaml.safe_load(open(p, "r", encoding="utf-8"))

groups = data.get("groups", [])
total = sum(len(g.get("rules", [])) for g in groups)

print(f"YAML 校验通过: {len(groups)} groups, {total} rules")
for g in groups:
    name = g["name"]
    rules = g.get("rules", [])
    print(f"\n  [{name}] {len(rules)} rules:")
    for r in rules:
        alert = r.get("alert", "?")
        severity = r.get("labels", {}).get("severity", "?")
        expr_preview = r.get("expr", "").strip().split("\n")[0][:60]
        print(f"    - {alert:<45} [{severity}] {expr_preview}...")
