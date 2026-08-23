"""路由挂载自检：静态确认关键 Blueprint / 网关是否在 app_server 接线生效

背景：app_server 中 health/learning_metrics 用 `app.register_blueprint` 直接注册，
而 modules_bp / log_system_bp 通过封装函数（register_modules_api / register_log_system）
间接注册 —— 仅 grep `register_blueprint` 会误判为"未挂载"。
本脚本同时识别两种注册形态，并对各 blueprint 路由定义做存在性校验。

用法：
  python scripts/check_mounted_routes.py              # 全量自检
  python scripts/check_mounted_routes.py --json       # JSON 输出（CI 友好）
退出码：0 = 全部已挂载；1 = 存在未挂载项
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_SERVER = ROOT / "app_server.py"

# 关键接线目标：bp 名 → 注册形态（app_server 中的调用特征）+ 路由定义文件
TARGETS = {
    "health_bp": {
        "register_forms": [r"register_blueprint\(\s*health_bp\s*\)"],
        "route_file": ROOT / "agent" / "health" / "dashboard.py",
        "route_marker": r"@health_bp\.route",
    },
    "learning_metrics_bp": {
        "register_forms": [r"register_blueprint\(\s*learning_metrics_bp\s*\)"],
        "route_file": ROOT / "agent" / "learning_metrics_api.py",
        "route_marker": r"@learning_metrics_bp\.route",
    },
    "modules_bp": {
        "register_forms": [
            r"register_modules_api\(",           # 封装注册
            r"register_blueprint\(\s*modules_bp\s*\)",  # 直接注册
        ],
        "route_file": ROOT / "agent" / "modules_api.py",
        "route_marker": r"@modules_bp\.route",
    },
    "log_system_bp": {
        "register_forms": [
            r"register_log_system\(",
            r"register_blueprint\(\s*log_system_bp\s*\)",
        ],
        "route_file": ROOT / "agent" / "log_system" / "dashboard.py",
        "route_marker": r"@log_system_bp\.route",
    },
    "api_gateway(开放API)": {
        # app_server 用别名接线：`from agent.api_gateway_flask import register_gateway as reg_gateway`
        "register_forms": [r"(?:register_gateway|reg_gateway)\("],
        "route_file": ROOT / "agent" / "api_gateway_flask.py",
        "route_marker": r"@app\.route\(",  # 网关端点 + /api/docs + /api/open/keys
    },
}


def check(app_server_src: str, spec: dict) -> dict:
    """校验单个目标：注册调用存在 + 路由定义文件存在且含路由装饰器"""
    register_found = any(re.search(p, app_server_src) for p in spec["register_forms"])
    route_file_ok = spec["route_file"].exists()
    route_count = 0
    if route_file_ok:
        src = spec["route_file"].read_text(encoding="utf-8", errors="replace")
        route_count = len(re.findall(spec["route_marker"], src))
    return {
        "registered": register_found,
        "route_file_exists": route_file_ok,
        "route_count": route_count,
    }


def main():
    ap = argparse.ArgumentParser(description="关键 Blueprint/网关路由挂载自检")
    ap.add_argument("--json", action="store_true", help="JSON 输出（CI 友好）")
    args = ap.parse_args()

    src = APP_SERVER.read_text(encoding="utf-8", errors="replace")
    results = {name: check(src, spec) for name, spec in TARGETS.items()}

    ok_all = all(
        r["registered"] and r["route_file_exists"] and r["route_count"] > 0
        for r in results.values()
    )

    if args.json:
        print(json.dumps({"ok": ok_all, "targets": results}, ensure_ascii=False, indent=2))
    else:
        print(f"路由挂载自检（源: {APP_SERVER.name}）\n")
        for name, r in results.items():
            mark = "PASS" if (r["registered"] and r["route_count"] > 0) else "FAIL"
            detail = (
                f"registered={'Y' if r['registered'] else 'N'} "
                f"routes={r['route_count']}"
            )
            print(f"  [{mark}] {name}  {detail}")
        print(f"\n{'全部已挂载' if ok_all else '存在未挂载/无路由项'}："
              f"{sum(1 for r in results.values() if r['registered'] and r['route_count'] > 0)}/{len(results)}")

    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
