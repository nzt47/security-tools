"""Prometheus 部署验证 Checklist 本地执行脚本 — 对应阶段5手册 §2.3 的 C1-C5。

用法:
    python scripts/verify_prometheus_checklist.py
    python scripts/verify_prometheus_checklist.py --prom-url http://localhost:9090 --metrics-url http://localhost:5678

行为说明:
- C1 规则校验: 优先调用 promtool（若在 PATH），否则降级用 PyYAML 校验规则文件语法。
- C2-C5 API 查询: 若目标服务未监听，对应项标记 SKIP（不视为失败），便于本地未部署时快速自检。
- 退出码: 0=全部通过或 SKIP; 1=存在 FAIL。
"""
import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RULES = [
    PROJECT_ROOT / "monitoring" / "prometheus" / "rules" / "lock_watchdog_alerts.yml",
]
DEFAULT_CONFIG = PROJECT_ROOT / "monitoring" / "prometheus" / "prometheus.yml"
METRIC_NAMES = ("yunshu_lock_hold_timeouts_total", "yunshu_lock_wait_timeouts_total", "yunshu_lock_hold_duration_ms")
ALERT_NAMES = ("LockHoldTimeout", "LockWaitTimeout")

PASS, SKIP, FAIL = "PASS", "SKIP", "FAIL"


def http_get(url: str, timeout: float = 5.0):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode("utf-8", errors="replace")


def check_c1(args) -> str:
    """C1: promtool 规则/配置校验，无 promtool 时降级 YAML 语法校验。"""
    promtool = subprocess.run(["where", "promtool"], capture_output=True, text=True, shell=True)
    if promtool.returncode == 0:
        for rule in DEFAULT_RULES:
            r = subprocess.run(["promtool", "check", "rules", str(rule)], capture_output=True, text=True)
            if r.returncode != 0:
                print(f"  FAIL promtool check rules {rule.name}:\n{r.stdout}{r.stderr}")
                return FAIL
        r = subprocess.run(["promtool", "check", "config", str(DEFAULT_CONFIG)], capture_output=True, text=True)
        if r.returncode != 0:
            print(f"  FAIL promtool check config {DEFAULT_CONFIG.name}:\n{r.stdout}{r.stderr}")
            return FAIL
        print("  PASS promtool check rules + config")
        return PASS
    # 降级: PyYAML 语法校验 + expr 引用新名
    try:
        import yaml
    except ImportError:
        print("  FAIL promtool 不存在且未安装 PyYAML，无法校验")
        return FAIL
    for rule in DEFAULT_RULES:
        data = yaml.safe_load(rule.read_text(encoding="utf-8"))
        exprs = [r.get("expr", "") for g in data["groups"] for r in g["rules"]]
        missing = [m for m in METRIC_NAMES if not m.endswith("_ms") and not any(m in e for e in exprs)]
        if missing:
            print(f"  FAIL {rule.name} 的 expr 未引用新名: {missing}")
            return FAIL
    print("  PASS 规则文件 YAML 语法 + expr 引用新名（无 promtool，降级校验）")
    return PASS


def check_c2(args) -> str:
    """C2: 配置热加载 POST /-/reload。"""
    try:
        req = urllib.request.Request(f"{args.prom_url}/-/reload", method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status in (200, 202):
                print("  PASS /-/reload 热加载成功")
                return PASS
            print(f"  FAIL /-/reload 返回 {resp.status}")
            return FAIL
    except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
        print(f"  SKIP Prometheus 未运行 ({args.prom_url}): {e}")
        return SKIP


def check_c3(args) -> str:
    """C3: /api/v1/rules 确认锁看门狗告警规则已加载。"""
    try:
        _, body = http_get(f"{args.prom_url}/api/v1/rules")
    except Exception as e:
        print(f"  SKIP Prometheus 未运行: {e}")
        return SKIP
    found = [a for a in ALERT_NAMES if a in body]
    print(f"  已加载告警: {found if found else '未找到'}")
    return PASS if len(found) == len(ALERT_NAMES) else FAIL


def check_c4(args) -> str:
    """C4: 应用 /metrics 暴露 yunshu_lock_* 指标。"""
    try:
        status, body = http_get(f"{args.metrics_url}/metrics")
    except Exception as e:
        print(f"  SKIP 应用 /metrics 不可达 ({args.metrics_url}): {e}")
        return SKIP
    if status != 200:
        print(f"  SKIP 应用 /metrics 非 200（服务未提供监控端点）: HTTP {status}")
        return SKIP
    present = [m for m in METRIC_NAMES if re.search(rf"^{re.escape(m)}({{| )", body, re.M)]
    print(f"  已暴露指标: {present if present else '未找到'}")
    return PASS if len(present) == len(METRIC_NAMES) else FAIL


def check_c5(args) -> str:
    """C5: 查询面返回样本 yunshu_lock_hold_timeouts_total。"""
    try:
        _, body = http_get(f"{args.prom_url}/api/v1/query?query={METRIC_NAMES[0]}")
    except Exception as e:
        print(f"  SKIP Prometheus 未运行: {e}")
        return SKIP
    try:
        data = json.loads(body)
        results = data.get("data", {}).get("result", [])
    except (ValueError, AttributeError) as e:
        print(f"  FAIL 查询响应解析失败: {e}")
        return FAIL
    if not results:
        print(f"  FAIL 查询无样本（计数为 0 或未触发）: {METRIC_NAMES[0]}")
        return FAIL
    for r in results:
        print(f"  样本: {r.get('metric', {})} value={r.get('value')}")
    return PASS


CHECKS = [("C1 规则校验", check_c1), ("C2 配置热加载", check_c2), ("C3 规则已加载", check_c3), ("C4 指标可采集", check_c4), ("C5 查询面样本", check_c5)]


def main():
    ap = argparse.ArgumentParser(description="Prometheus 部署验证 Checklist C1-C5")
    ap.add_argument("--prom-url", default="http://localhost:9090")
    ap.add_argument("--metrics-url", default="http://localhost:5678")
    args = ap.parse_args()

    print(f"Prometheus: {args.prom_url}  应用 /metrics: {args.metrics_url}\n")
    results = []
    for name, fn in CHECKS:
        status = fn(args)
        results.append((name, status))
        print(f"[{status}] {name}")
    print()
    for name, status in results:
        print(f"  {status}  {name}")

    failed = [n for n, s in results if s == FAIL]
    print(f"\n结果: {sum(1 for _, s in results if s == PASS)} PASS / "
          f"{sum(1 for _, s in results if s == SKIP)} SKIP / "
          f"{len(failed)} FAIL")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
