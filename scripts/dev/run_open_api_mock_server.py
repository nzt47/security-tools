"""本地开放 API Mock 服务：验证 API Key 鉴权链路（无需启动完整 app_server）

复用真实组件（【不易】：认证/scope/限流逻辑零修改）：
  - agent.api_gateway.ApiGateway（认证 X-API-Key/Bearer + scope 校验 + 限流配额）
  - agent.api_gateway_flask.register_gateway（before_request 中间层 + /api/open/* + /api/docs）
注册 T8.4 灰度开放的 8 个只读端点视图（返回简单 JSON）。

Key 管理隔离：ApiKeyManager 落盘路径替换为内存保存（避免写入 agent/data/api_keys.json）。

用法：
  python scripts/dev/run_open_api_mock_server.py --port 5678
配合 open_api_client.py 联调：
  python scripts/examples/open_api_client.py --user-id demo@example.com
  python scripts/examples/open_api_client.py --user-id demo@example.com --role viewer --tenant-id t1
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from flask import Flask, jsonify  # noqa: E402

# 先隔离 Key 存储（模块加载时 patch 实例方法），再导入网关组件
from agent.api_gateway import ApiKeyManager  # noqa: E402


def _memory_save(self) -> None:
    """内存保存（不落盘）：保持单例内可见即可，避免污染生产 api_keys.json"""
    return None


ApiKeyManager._save_keys = _memory_save  # type: ignore[method-assign]
ApiKeyManager._load_keys = _memory_save  # type: ignore[method-assign]

from agent.api_gateway_flask import register_gateway  # noqa: E402
from agent.api_gateway import get_api_gateway  # noqa: E402

# 与 open_api_client.py 的 OPEN_ENDPOINTS 一致（T8.4 灰度开放的 8 个只读端点）
OPEN_ENDPOINTS = [
    "/api/news",
    "/api/audit/logs",
    "/api/schedules",
    "/api/skills",
    "/api/tasks",
    "/api/search-performance/status",
    "/api/search-performance/history",
    "/api/search-performance/summary",
]


def create_app() -> Flask:
    app = Flask(__name__)

    @app.get("/api/health")
    def health():
        return jsonify({"ok": True})

    # 8 个内部只读端点视图（真实 app_server 中由业务模块提供；此处为验证鉴权链路）
    for path in OPEN_ENDPOINTS:
        name = "open_" + path.strip("/").replace("/", "_")

        def view(_p: str = path):
            return jsonify({"ok": True, "endpoint": _p})

        view.__name__ = name
        view.__doc__ = f"Mock 视图: {path}"
        app.add_url_rule(path, endpoint=name, view_func=view, methods=["GET"])

    # 重置网关单例为全新实例（避免复用测试残留状态）
    gw = get_api_gateway()
    gw._endpoints.clear()
    gw._api_key_manager._api_keys.clear()
    register_gateway(app)  # 真实中间层：开放 8 端点 + /api/open/echo + /api/open/keys
    return app


def main():
    ap = argparse.ArgumentParser(description="本地开放 API Mock 服务（Key 内存化）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5678)
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    app = create_app()
    print(f"[mock] 开放 API Mock 服务: http://{args.host}:{args.port}（Key 内存化，不落盘）")
    print(f"[mock] 端点数: {len(OPEN_ENDPOINTS)} + /api/open/echo + /api/open/keys + /api/docs")
    app.run(host=args.host, port=args.port, debug=args.debug, use_reloader=False)


if __name__ == "__main__":
    main()
