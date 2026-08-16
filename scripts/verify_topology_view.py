#!/usr/bin/env python3
"""S3 收尾验证：主控台 HTML 接线 + WindowSensor 开关

1. import app_server 构建真实 app（YUNSHU_DISABLE_WINDOW_SENSOR=1 需在此前设置）
2. test_client 获取 / 页面 HTML，断言拓扑视图接线元素存在
3. 断言 /api/modules/topology 路由已注册
4. 断言 sensor.window_sensor 未被导入（开关生效）

用法: python scripts/verify_topology_view.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["YUNSHU_DISABLE_WINDOW_SENSOR"] = "1"

passed = 0
failed = 0


def check(name: str, cond: bool, detail: str = ""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  [PASS] {name} {detail}")
    else:
        failed += 1
        print(f"  [FAIL] {name} {detail}")


def main() -> int:
    import logging
    logging.disable(logging.INFO)  # 压启动噪音

    import app_server
    app = app_server.app
    client = app.test_client()
    # 主控台 index.html 由 /legacy 渲染（/ 是 health_dashboard，/chat 重定向 /static/chat）
    html = client.get("/legacy").get_data(as_text=True)

    print("== 主控台 HTML 接线 ==")
    check("导航按钮 data-view=topology", 'data-view="topology"' in html)
    check("视图容器 #view-topology", 'id="view-topology"' in html)
    check("topology.css 引入", "css/topology.css" in html)
    check("topology.js 引入", "js/topology.js" in html)
    check("registerView topology", "registerView('topology'" in html)

    print("== 后端路由 ==")
    rules = {r.rule for r in app.url_map.iter_rules()}
    check("/api/modules/topology 已注册", "/api/modules/topology" in rules)
    check("/api/modules/<module_id>/detail 已注册", "/api/modules/<module_id>/detail" in rules)
    check("/api/modules/<module_id>/actions 已注册", "/api/modules/<module_id>/actions" in rules)

    print("== WindowSensor 屏蔽开关 ==")
    ws_loaded = "sensor.window_sensor" in sys.modules
    check("YUNSHU_DISABLE_WINDOW_SENSOR 屏蔽导入", not ws_loaded,
          f"loaded={ws_loaded}")
    check("_window_sensor 为 None", app_server._window_sensor is None)

    print(f"\n结果: {passed} passed / {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
