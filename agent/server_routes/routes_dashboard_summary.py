#!/usr/bin/env python3
"""
运营统计仪表盘 API —— Dashboard 页面数据源
------------------------------------------------------
提供前端 Dashboard 页面的业务统计聚合数据：
  - stats：总用户数 / 总订单数 / 转化率 / 活跃用户
  - trend：近 7 天访问趋势
  - roles：用户角色分布

数据源：内置 Mock 数据（与前端 src/pages/Dashboard/index.tsx 的 Mock 保持一致）。
接入真实统计逻辑时，仅需替换 _get_dashboard_summary() 中各字段的数据来源，
接口路径与响应结构保持不变（契约稳定，前端无需改动）。

API 端点清单：
- GET /api/dashboard/summary —— 运营统计总览

响应格式遵循前端 request.ts 拦截器约定：{code: 200, data: {...}, message: "success"}
"""

import logging
from datetime import datetime, timedelta

from flask import jsonify

from agent.logging_utils import log_dict
from agent.server_auth import log_request
from agent.server_routes.tracing_decorator import trace_route

logger = logging.getLogger(__name__)

# 近 7 天访问量（与前端 Mock 一致；日期动态回推，接口随时可演示）
TREND_VISITS = [1860, 2130, 1980, 2650, 2420, 2890, 3120]


def _build_visit_trend():
    """生成近 7 天访问趋势：日期以今天为基准回推（近 7 天语义不变，日期自适应）"""
    today = datetime.now()
    return [
        {"day": (today - timedelta(days=offset)).strftime("%m-%d"), "visits": visits}
        for offset, visits in zip(range(len(TREND_VISITS) - 1, -1, -1), TREND_VISITS)
    ]


def _get_dashboard_summary():
    """聚合运营统计数据（当前为 Mock，接入真实统计时替换各字段来源即可）"""
    return {
        "stats": {
            "totalUsers": 12480,
            "totalOrders": 3926,
            "conversionRate": 3.42,
            "activeUsers": 8153,
        },
        "trend": _build_visit_trend(),
        "roles": [
            {"name": "普通用户", "value": 10640},
            {"name": "编辑", "value": 1560},
            {"name": "管理员", "value": 280},
        ],
    }


def register_routes(app, state):
    """注册运营统计仪表盘路由"""

    @app.route("/api/dashboard/summary", methods=["GET"])
    @trace_route("DashboardSummary")
    @log_request(show_response=False)
    def api_dashboard_summary():
        """
        运营统计总览

        Response:
            {
                "code": 200,
                "data": {
                    "stats": {
                        "totalUsers": int,
                        "totalOrders": int,
                        "conversionRate": float,
                        "activeUsers": int
                    },
                    "trend": [{"day": str, "visits": int}],
                    "roles": [{"name": str, "value": int}]
                },
                "message": "success"
            }
        """
        result = _get_dashboard_summary()
        logger.info(
            log_dict({"module_name": "routes_dashboard_summary", "action": "summary", "status": "success"})
        )
        return jsonify({"code": 200, "data": result, "message": "success"})
