"""LLM 禁用时段 API（顶栏开关 + 时段配置，2026-11-20）。

- GET  /api/llm-gate  → 当前配置 + 运行状态（顶栏常驻读数）
- POST /api/llm-gate  → 部分更新配置（enabled / windows / skip_holidays / lead_seconds）

为什么单独一个路由模块：顶栏开关与配置弹窗都要读写同一份配置，
配置的校验/落盘在 agent/llm_idle_gate.py（唯一事实源），本模块只做 HTTP 适配。
"""
from __future__ import annotations

import logging

from flask import jsonify, request

from agent.server_auth import require_token
from agent.server_routes._errors import safe_error as _safe
from agent.server_routes.tracing_decorator import trace_route
from agent import llm_idle_gate as gate

logger = logging.getLogger(__name__)


def register_routes(app, state):
    @app.route("/api/llm-gate", methods=["GET"])
    @trace_route("LLMGate")
    def api_llm_gate_get():
        try:
            return jsonify({
                "ok": True,
                "config": gate.get_config(),
                "state": gate.state(),
            })
        except Exception as e:  # noqa: BLE001
            logger.error("读取 LLM 禁用时段配置失败: %s", e)
            return jsonify({"ok": False, "error": _safe(e, logger)}), 500

    @app.route("/api/llm-gate", methods=["POST"])
    @trace_route("LLMGate")
    @require_token
    def api_llm_gate_set():
        data = request.get_json(silent=True) or {}
        try:
            config = gate.save_config(data)
        except gate.GateConfigError as exc:
            # 校验文案是后端自己给的（不含内部路径/堆栈）：走 .message 属性，
            # 不要用 str() 包异常对象 —— 那是 py/stack-trace-exposure 的典型形态（口径 §3.5）。
            return jsonify({"ok": False, "error": exc.message}), 400
        except ValueError:
            # 不是本模块的校验错误（形状异常）：只说"参数不合法"，原文只进日志
            logger.warning("LLM 禁用时段配置参数异常（非校验错误）", exc_info=True)
            return jsonify({"ok": False, "error": "配置参数不合法（详见服务日志）"}), 400
        except Exception as e:  # noqa: BLE001
            logger.error("保存 LLM 禁用时段配置失败: %s", e, exc_info=True)
            return jsonify({"ok": False, "error": _safe(e, logger)}), 500
        return jsonify({"ok": True, "config": config, "state": gate.state()})
