"""功能单位监控路由（/api/system/units）—— 只读快照。

一个请求返回所有已登记单位的 {status, summary, metrics, issues, last_activity}，
供「全景看板 · 工作状况」页直接渲染。探测本身在 unit_monitor 里带 2s 超时与 5s 缓存，
所以页面可以放心轮询。
"""
from __future__ import annotations

import logging

from flask import jsonify, request

from agent.server_auth import require_token
from agent.server_routes.tracing_decorator import trace_route

logger = logging.getLogger(__name__)


def register_routes(app, state=None):
    from agent.monitoring import unit_monitor, unit_probes

    try:
        unit_probes.register_all()
    except Exception as exc:  # noqa: BLE001 登记失败不应拖垮路由注册
        logger.warning("[units] 探测单位登记失败: %s", exc)

    @app.route("/api/system/platform-cards", methods=["GET"])
    # 顺序有意：require_token 放**最外层**，标记才会挂在 Flask 最终持有的视图函数上
    # （trace_route 的 wrapper 不保证把 __dict__ 传出来，实测会丢 REQUIRES_TOKEN_ATTR）。
    @require_token
    @trace_route("SystemUnits")
    def api_system_platform_cards():
        """**平台能力卡**：云枢自身功能（不是 LLM 技能）按同一套卡片形态吐给界面。

        【为什么单独一个端点】技能中心的「能力卡」只覆盖 data/skills_repo 下的 LLM 技能；
        Owner 2026-11-01 追问「能力卡是否包括云枢自身功能」⇒ 答案是**不包含**，但不该让他去
        三个不同叶子拼信息。这里把三类平台能力聚成同一种形状：

          · 功能单位（unit_monitor：服务/审计/调度/记忆/知识库/工具/工作流…）——带 status 与 metrics；
          · 工具（agent.tools.list_tools：模型可调用的 94 个）——带 enabled；
          · 调度任务（task_scheduler：15 个）——带排期与 zero_token 口径。

        边界：**只读**；不查权限日志（那是 /api/tools/config 的事）；与 /api/system/units 同源，
        所以「面板上是黄的，这里也是黄的」，不会两处打架。
        """
        try:
            groups = []
            units = unit_monitor.collect()
            groups.append({
                "kind": "unit",
                "title": "功能单位",
                "desc": "服务进程 / 审计链 / 调度 / 记忆 / 知识库 / 工具 / 工作流等运行状况",
                "count": len(units.get("units") or []),
                "items": [{
                    "id": u.get("id"),
                    "name": u.get("name"),
                    "summary": u.get("summary"),
                    "status": u.get("status"),
                    "family": u.get("family"),
                    "metrics": u.get("metrics") or {},
                    "issues": u.get("issues") or [],
                } for u in (units.get("units") or [])],
            })

            from agent.tools import list_tools, declared_disabled_tool_names
            disabled_tools = declared_disabled_tool_names()
            tool_items = []
            for t in list_tools():
                desc = (t.get("description") or "").strip().replace("\n", " ")
                tool_items.append({
                    "id": t.get("name"),
                    "name": t.get("name"),
                    "summary": desc[:120],
                    "status": "off" if t.get("name") in disabled_tools else "ok",
                })
            groups.append({
                "kind": "tool",
                "title": "工具",
                "desc": "模型可发起的工具调用（停用的仍列出，status=off）",
                "count": len(tool_items),
                "items": tool_items,
            })

            from agent.task_scheduler import get_scheduler
            tasks = get_scheduler().list_tasks()
            groups.append({
                "kind": "task",
                "title": "调度任务",
                "desc": "定时任务（含学习调度器三项，zero_token 表示不花模型预算）",
                "count": len(tasks),
                "items": [{
                    "id": t.get("task_id") or t.get("name"),
                    "name": t.get("name"),
                    "summary": t.get("schedule_text") or "",
                    "status": "ok" if t.get("enabled", True) else "off",
                    "zero_token": t.get("zero_token"),
                } for t in tasks],
            })

            return jsonify({"ok": True, "groups": groups})
        except Exception as exc:  # noqa: BLE001
            logger.warning("[platform-cards] 采集失败: %s", exc)
            return jsonify({"ok": False, "error": str(exc)}), 500

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

