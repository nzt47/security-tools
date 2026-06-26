# -*- coding: utf-8 -*-
"""
3 个核心 API 的契约定义

【API 清单】
1. /api/chat       - 对话接口（POST）
2. /api/health     - 健康检查（GET，返回身体状态读数数组）
3. /api/dashboard  - 仪表盘（GET，/api/dashboard/quality 质量监控数据）

【契约来源】
- agent/server_routes/routes_chat.py
- agent/server_routes/routes_panorama.py
- agent/server_routes/routes_dashboard.py
"""

from __future__ import annotations

# 兼容直接运行与包导入两种方式
try:
    from .contract_framework import Contract, FieldSpec, Interaction
except ImportError:
    from contract_framework import Contract, FieldSpec, Interaction


# ═══════════════════════════════════════════════════════════════
#  /api/chat 契约
# ═══════════════════════════════════════════════════════════════

def build_chat_contract() -> Contract:
    """构建 /api/chat 对话接口契约"""
    return Contract(
        name="chat_api",
        consumer="yunshu_frontend",
        provider="yunshu_backend",
        version="1.0.0",
        description="对话接口契约：处理用户消息并返回响应",
        interactions=[
            # ── 交互 1：正常对话请求 ──
            Interaction(
                description="正常对话请求 - 返回响应文本与模式信息",
                request_method="POST",
                request_path="/api/chat",
                request_body_fields=[
                    FieldSpec(
                        name="message",
                        type="string",
                        required=True,
                        description="用户输入消息",
                        min_length=1,
                        max_length=10000,
                    ),
                    FieldSpec(
                        name="voice",
                        type="boolean",
                        required=False,
                        description="是否启用语音合成",
                    ),
                    FieldSpec(
                        name="session",
                        type="string",
                        required=False,
                        description="会话 ID（可选）",
                    ),
                ],
                response_status=200,
                response_body_fields=[
                    FieldSpec(
                        name="response",
                        type="string",
                        required=True,
                        description="对话响应文本",
                    ),
                    FieldSpec(
                        name="mode",
                        type="string",
                        required=True,
                        description="行为模式",
                        enum=["normal", "focus", "creative", "study", "rest"],
                    ),
                    FieldSpec(
                        name="mode_label",
                        type="string",
                        required=True,
                        description="模式标签",
                    ),
                    FieldSpec(
                        name="logs",
                        type="array",
                        required=True,
                        description="处理日志数组",
                        items=FieldSpec(name="log_entry", type="string", description="日志条目"),
                    ),
                    FieldSpec(
                        name="timing",
                        type="object",
                        required=True,
                        description="耗时统计",
                        properties=[
                            FieldSpec(name="total", type="number", description="总耗时(ms)"),
                            FieldSpec(name="safety_check", type="number", description="安全检查耗时(ms)"),
                            FieldSpec(name="chat_processing", type="number", description="对话处理耗时(ms)"),
                        ],
                    ),
                    FieldSpec(
                        name="health",
                        type="array",
                        required=False,
                        description="身体状态读数",
                        items=FieldSpec(
                            name="reading",
                            type="object",
                            description="身体状态读数",
                            properties=[
                                FieldSpec(name="sensor_name", type="string"),
                                FieldSpec(name="severity", type="string", enum=["normal", "warning", "critical"]),
                            ],
                        ),
                    ),
                    FieldSpec(
                        name="llm_state",
                        type="object",
                        required=False,
                        description="LLM 配置状态",
                        properties=[
                            FieldSpec(name="configured", type="boolean"),
                            FieldSpec(name="provider", type="string"),
                            FieldSpec(name="api_key_set", type="boolean"),
                        ],
                    ),
                ],
                response_example={
                    "response": "您好，我是云枢。",
                    "mode": "normal",
                    "mode_label": "正常模式",
                    "logs": ["[START] 收到对话请求"],
                    "timing": {"total": 123.45, "safety_check": 1.2, "chat_processing": 100.0},
                    "health": [],
                    "llm_state": {"configured": True, "provider": "openai", "api_key_set": True},
                },
            ),
            # ── 交互 2：空消息请求 ──
            Interaction(
                description="空消息请求 - 返回 400 错误",
                request_method="POST",
                request_path="/api/chat",
                request_body_fields=[
                    FieldSpec(name="message", type="string", required=True, description="空消息"),
                ],
                response_status=400,
                response_body_fields=[
                    FieldSpec(name="error", type="string", required=True, description="错误信息"),
                ],
                response_example={"error": "消息不能为空"},
            ),
            # ── 交互 3：安全拦截请求 ──
            Interaction(
                description="安全拦截请求 - 返回 403 阻断",
                request_method="POST",
                request_path="/api/chat",
                request_body_fields=[
                    FieldSpec(
                        name="message",
                        type="string",
                        required=True,
                        description="含危险操作的消息",
                        min_length=1,
                    ),
                ],
                response_status=403,
                response_body_fields=[
                    FieldSpec(name="response", type="string", required=True, description="拦截提示"),
                    FieldSpec(name="blocked", type="boolean", required=True, description="是否阻断"),
                    FieldSpec(name="mode", type="string", required=True, enum=["normal", "focus", "creative", "study", "rest"]),
                    FieldSpec(name="safety", type="object", required=True, description="安全检查结果",
                        properties=[
                            FieldSpec(name="level", type="string", enum=["safe", "warning", "critical"]),
                            FieldSpec(name="safe", type="boolean"),
                        ]),
                ],
                response_example={
                    "response": "⚠️ 安全警告：检测到危险操作！",
                    "blocked": True,
                    "mode": "normal",
                    "safety": {"level": "critical", "safe": False},
                },
            ),
        ],
    )


# ═══════════════════════════════════════════════════════════════
#  /api/health 契约
# ═══════════════════════════════════════════════════════════════

def build_health_contract() -> Contract:
    """构建 /api/health 健康检查契约

    来源：routes_panorama.py 的 api_health() 返回 readings 数组
    """
    return Contract(
        name="health_api",
        consumer="yunshu_frontend",
        provider="yunshu_backend",
        version="1.0.0",
        description="健康检查契约：返回身体状态读数数组",
        interactions=[
            Interaction(
                description="获取身体状态读数 - 返回读数数组",
                request_method="GET",
                request_path="/api/health",
                response_status=200,
                response_body_fields=[
                    FieldSpec(
                        name="_root",
                        type="array",
                        required=True,
                        description="身体状态读数数组（根级数组）",
                        items=FieldSpec(
                            name="reading",
                            type="object",
                            description="单个身体状态读数",
                            properties=[
                                FieldSpec(name="sensor_name", type="string", required=True, description="传感器名称"),
                                FieldSpec(name="description", type="string", required=False, description="读数描述"),
                                FieldSpec(name="severity", type="string", required=True,
                                    enum=["normal", "warning", "critical"], description="严重程度"),
                                FieldSpec(name="value", type="number", required=False, description="读数值"),
                                FieldSpec(name="unit", type="string", required=False, description="单位"),
                                FieldSpec(name="timestamp", type="string", required=False, description="时间戳"),
                            ],
                        ),
                    ),
                ],
                response_example=[
                    {
                        "sensor_name": "heart_rate",
                        "description": "心率",
                        "severity": "normal",
                        "value": 72,
                        "unit": "bpm",
                        "timestamp": "2026-06-26T10:00:00",
                    },
                ],
            ),
        ],
    )


# ═══════════════════════════════════════════════════════════════
#  /api/dashboard 契约
# ═══════════════════════════════════════════════════════════════

def build_dashboard_contract() -> Contract:
    """构建 /api/dashboard 仪表盘契约

    来源：routes_dashboard.py 的 /api/dashboard/quality 端点
    """
    return Contract(
        name="dashboard_api",
        consumer="yunshu_frontend",
        provider="yunshu_backend",
        version="1.0.0",
        description="仪表盘契约：质量监控数据接口",
        interactions=[
            Interaction(
                description="获取质量监控数据 - 返回质量指标",
                request_method="GET",
                request_path="/api/dashboard/quality",
                request_query={
                    "time_range": "today",
                },
                response_status=200,
                response_body_fields=[
                    FieldSpec(name="total_requests", type="integer", required=True, description="总请求数", minimum=0),
                    FieldSpec(name="success_count", type="integer", required=True, description="成功数", minimum=0),
                    FieldSpec(name="error_count", type="integer", required=True, description="错误数", minimum=0),
                    FieldSpec(name="success_rate", type="number", required=True, description="成功率", minimum=0, maximum=100),
                    FieldSpec(name="avg_response_time", type="number", required=False, description="平均响应时间(ms)", minimum=0),
                    FieldSpec(name="p95_response_time", type="number", required=False, description="P95 响应时间(ms)", minimum=0),
                    FieldSpec(name="p99_response_time", type="number", required=False, description="P99 响应时间(ms)", minimum=0),
                    FieldSpec(
                        name="time_range",
                        type="object",
                        required=True,
                        description="时间范围",
                        properties=[
                            FieldSpec(name="start", type="number", description="开始时间戳"),
                            FieldSpec(name="end", type="number", description="结束时间戳"),
                        ],
                    ),
                    FieldSpec(
                        name="error_breakdown",
                        type="object",
                        required=False,
                        description="错误分类统计",
                    ),
                ],
                response_example={
                    "total_requests": 1000,
                    "success_count": 950,
                    "error_count": 50,
                    "success_rate": 95.0,
                    "avg_response_time": 123.45,
                    "p95_response_time": 300.0,
                    "p99_response_time": 500.0,
                    "time_range": {"start": 1719360000.0, "end": 1719446400.0},
                    "error_breakdown": {"timeout": 30, "validation": 20},
                },
            ),
            Interaction(
                description="获取追踪数据列表 - 返回追踪记录",
                request_method="GET",
                request_path="/api/dashboard/traces",
                request_query={"limit": "20"},
                response_status=200,
                response_body_fields=[
                    FieldSpec(name="total", type="integer", required=True, description="追踪总数", minimum=0),
                    FieldSpec(
                        name="traces",
                        type="array",
                        required=True,
                        description="追踪记录数组",
                        items=FieldSpec(
                            name="trace",
                            type="object",
                            description="单个追踪记录",
                            properties=[
                                FieldSpec(name="trace_id", type="string", required=True),
                                FieldSpec(name="service", type="string", required=True),
                                FieldSpec(name="operation", type="string", required=True),
                                FieldSpec(name="duration_ms", type="number", required=True, minimum=0),
                                FieldSpec(name="status", type="string", required=True, enum=["success", "error"]),
                            ],
                        ),
                    ),
                ],
                response_example={
                    "total": 1,
                    "traces": [
                        {
                            "trace_id": "abc123",
                            "service": "chat",
                            "operation": "api.chat",
                            "duration_ms": 123.45,
                            "status": "success",
                        },
                    ],
                },
            ),
        ],
    )


# ═══════════════════════════════════════════════════════════════
#  契约注册表
# ═══════════════════════════════════════════════════════════════

def get_all_contracts() -> list:
    """获取所有契约定义"""
    return [
        build_chat_contract(),
        build_health_contract(),
        build_dashboard_contract(),
    ]
