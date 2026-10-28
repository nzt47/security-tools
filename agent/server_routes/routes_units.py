"""功能单位监控路由（/api/system/units）—— 只读快照。

一个请求返回所有已登记单位的 {status, summary, metrics, issues, last_activity}，
供「全景看板 · 工作状况」页直接渲染。探测本身在 unit_monitor 里带 2s 超时与 5s 缓存，
所以页面可以放心轮询。
"""
from __future__ import annotations

import logging

from flask import jsonify, request

from agent.server_routes.tracing_decorator import trace_route

logger = logging.getLogger(__name__)


def register_routes(app, state=None):
    from agent.monitoring import unit_monitor, unit_probes

    try:
        unit_probes.register_all()
    except Exception as exc:  # noqa: BLE001 登记失败不应拖垮路由注册
        logger.warning("[units] 探测单位登记失败: %s", exc)

    @app.route("/api/system/units", methods=["GET"])
    @trace_route("SystemUnits")
    # 只读快照，与 /api/heartbeat、/api/skills-mgmt 列表同级：不加令牌，
    # 否则「工作状况」面板在未配置 UI 令牌时会整块 401（用户看不到任何单位）。
    def api_system_units():
        """功能单位工作状况快照

        Query:
            refresh=1     绕过 5s 缓存重新探测
            family=a,b    只看指定家族（runtime/skills/tools/workflows/scheduler/memory/ingestion）
        """
        try:
            refresh = str(request.args.get("refresh", "")).lower() in ("1", "true", "yes")
            raw = (request.args.get("family") or "").strip()
            families = [f.strip() for f in raw.split(",") if f.strip()] or None
            payload = unit_monitor.collect(refresh=refresh, families=families)
            return jsonify({
                "ok": True,
                **payload,
                "health_score": unit_monitor.health_score(payload["units"]),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning("[units] 采集失败: %s", exc)
            return jsonify({"ok": False, "error": str(exc)}), 500

    @app.route("/api/system/units/families", methods=["GET"])
    @trace_route("SystemUnits")
    def api_system_units_families():
        """已登记的家族清单（供前端筛选器，避免前端硬编码）。"""
        try:
            units = unit_monitor.registered()
            fams = sorted({u.family for u in units})
            return jsonify({"ok": True, "families": fams})
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(exc)}), 500