"""TASK-S6-01 验证据点：最小可运行工作台（**仅用于本地验收/E2E，不参与生产**）

【为什么需要它】
    `app_server.py` 是完整宿主（扩展/MCP/调度器/子代理全量初始化，本机冷启动
    可达数分钟）。验收需要的是"**工作台页面 + `/api/cp/*` 面板真实可达**"这一条
    端到端链路，故本脚本只注册：
        - 静态资源与工作台模板（`templates/yunshu.html` + `static/`）
        - `/api/cp/*` 治理面板路由（真实后端实现，非桩）
        - 最小页面（工作台 hash 路由仍由前端 React 承担）

【纪律】
    - **不替代 app_server.py**：生产入口不变（`app_server.py` 已显式注册
      `routes_ui_panels`，见其 "治理可观测六面板" 段）；
    - 只读 + 面板路由；不加载扩展/MCP/调度器，故冷启动秒级；
    - 用途写在文件头，避免被误当作第二套服务。

用法::

    python scripts/dev/cp_panel_evidence_server.py --port 5757
"""

from __future__ import annotations

import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)


def create_app():
    from flask import Flask, redirect, render_template, send_from_directory

    app = Flask(
        __name__,
        template_folder=os.path.join(_ROOT, "templates"),
        static_folder=os.path.join(_ROOT, "static"),
    )

    # ── 治理面板路由（真实实现）──
    from agent.server_routes.routes_ui_panels import register_routes as reg_panels
    reg_panels(app, None)
    from agent.server_routes.routes_approval import register_routes as reg_approval
    reg_approval(app, None)

    @app.route("/chat")
    @app.route("/")
    def workbench():
        try:
            return render_template("yunshu.html")
        except Exception as e:  # noqa: BLE001
            return f"<h1>工作台模板不可用</h1><pre>{e}</pre>", 500

    @app.route("/static/<path:subpath>")
    def static_files(subpath: str):
        return send_from_directory(app.static_folder, subpath)

    @app.route("/favicon.ico")
    def favicon():
        return redirect("/static/favicon.svg")

    return app


def main() -> int:
    parser = argparse.ArgumentParser(description="S6-01 验证据点（最小工作台）")
    parser.add_argument("--port", type=int, default=5757)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    app = create_app()
    print(f"[cp-evidence] workbench: http://{args.host}:{args.port}/chat#/workbench")
    print(f"[cp-evidence] panels   : http://{args.host}:{args.port}/api/cp/panels")
    app.run(host=args.host, port=args.port, threaded=True, use_reloader=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
