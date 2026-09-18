"""语义层配置热更 API（`/api/orchestrator/semantic-config`）

## 为什么单独成一个模块（2026-09-18）

这两个 handler 原先住在 `agent/server_routes/routes_config.py` 里，而那个模块
**从未在 `app_server.py` 里注册**（本仓路由注册的真实位置只有 `app_server.py`；
`server_routes/__init__.py::register_all_routes` 是无调用方的死代码）⇒ 端点线上 404。
而 `agent/orchestrator/orchestrator.py` 明确在**读**这一层覆盖
（`Orchestrator._SEM_API_OVERRIDE` 为最高优先级），写入侧却不存在
⇒ "顶层配置永远读不到 API 覆盖值"，热更能力形同虚设。

为什么不直接把 `routes_config` 整模块注册：它声明 29 条路径，其中 **27 条已由其他模块
提供**，整模块注册会与之**同路径重复**（Flask 同路径多规则，行为不可预期）。故只把缺失的
这两条摘出来单独接线，`routes_config.py` 保持未注册状态（已在
`tests/unit/test_server_routes_registration_inventory.py` 的清单里登记原因）。

行为与迁出前**逐字一致**：含参数白名单、类型/范围校验、`_SEM_API_OVERRIDE` 更新 +
缓存清除、SQLite 持久化（重启后恢复）与 Prometheus 埋点。
"""
from __future__ import annotations

import logging
import time

from flask import jsonify, request

from agent.logging_utils import log_dict
from agent.server_auth import log_request, require_token
from agent.server_routes.tracing_decorator import trace_route

logger = logging.getLogger(__name__)


def register_routes(app, state=None):
    """注册语义层配置热更路由

    ``state`` 未使用（仅保持与既有路由模块相同的签名，便于 `app_server.py` 统一接线）。
    """

    # ═══════════════════════════════════════════════════

    @app.route("/api/orchestrator/semantic-config", methods=["GET"])
    @trace_route("Config")
    @require_token
    @log_request(show_response=False)
    def api_semantic_config_get():
        """查询语义层当前生效配置（含 env/config.yaml/API override 合并结果）"""
        try:
            from agent.orchestrator.orchestrator import Orchestrator
            config = Orchestrator._load_semantic_layer_config()
            return jsonify({
                "ok": True,
                "config": config,
                "api_override": Orchestrator._SEM_API_OVERRIDE,
            })
        except Exception as e:
            logger.error("[语义层配置] 查询失败: %s", e, exc_info=True)
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/orchestrator/semantic-config", methods=["POST"])
    @trace_route("Config")
    @require_token
    @log_request()
    def api_semantic_config_update():
        """热更语义层配置（即时生效，无需重启）

        请求体: {"min_score": 0.5, "enabled": true, ...}
        【不易】仅接受 _SEM_DEFAULTS 中已知的键，类型+范围严格校验
        【变易】更新 _SEM_API_OVERRIDE + 清除缓存，下次请求即生效
        """
        try:
            from agent.orchestrator.orchestrator import Orchestrator
            _t0 = time.perf_counter()  # Prometheus 指标: 生效延迟
            data = request.get_json() or {}

            # 参数校验：类型映射 + 范围约束
            _type_map = {
                "enabled": bool,
                "min_score": (int, float),
                "top_k": int,
                "use_vector": bool,
                "use_bm25": bool,
                "use_reranker": bool,
                "fusion_mode": str,
            }
            _range_checks = {
                "min_score": lambda v: 0.0 <= float(v) <= 1.0 or "min_score 超出范围 [0.0, 1.0]",
                "top_k": lambda v: 1 <= int(v) <= 20 or "top_k 超出范围 [1, 20]",
                "fusion_mode": lambda v: v in ("rrf", "weighted", "none") or "fusion_mode 必须为 rrf/weighted/none",
            }

            overrides = {}
            for key, expected_type in _type_map.items():
                if key in data and data[key] is not None:
                    # 类型检查
                    if not isinstance(data[key], expected_type):
                        return jsonify({"ok": False, "error": "参数 %s 类型错误，期望 %s，实际 %s" % (key, expected_type, type(data[key]).__name__)}), 400
                    # 范围检查
                    if key in _range_checks:
                        check_result = _range_checks[key](data[key])
                        if check_result is not True:
                            return jsonify({"ok": False, "error": str(check_result)}), 400
                    overrides[key] = data[key]

            if not overrides:
                return jsonify({"ok": False, "error": "未提供有效配置参数（支持: min_score, enabled, top_k, use_vector, use_bm25, use_reranker, fusion_mode）"}), 400

            # 应用覆盖（更新 _SEM_API_OVERRIDE + 清除缓存）
            old_override = dict(Orchestrator._SEM_API_OVERRIDE) if Orchestrator._SEM_API_OVERRIDE else {}
            if Orchestrator._SEM_API_OVERRIDE is None:
                Orchestrator._SEM_API_OVERRIDE = {}
            old_values = {k: Orchestrator._SEM_API_OVERRIDE.get(k, Orchestrator._SEM_DEFAULTS.get(k)) for k in overrides}
            Orchestrator._SEM_API_OVERRIDE.update(overrides)
            Orchestrator._clear_semantic_config_cache()

            # 【变易】持久化到 SQLite（重启后恢复，失败不影响内存热更）
            Orchestrator._save_semantic_override_to_db(overrides)

            # 验证生效
            new_config = Orchestrator._load_semantic_layer_config()

            logger.info(log_dict({'module_name': 'routes_config', 'action': 'orchestrator.semantic.config.hot_reload', 'msg': '[语义层] 配置热更成功: overrides=%s old_values=%s new_min_score=%s' % (overrides, old_values, new_config.get("min_score"))}))

            # 【变易】Prometheus 指标埋点: 变更频率 + 生效延迟
            _elapsed_ms = (time.perf_counter() - _t0) * 1000
            try:
                from agent.monitoring.metrics import get_metrics_collector
                _collector = get_metrics_collector()
                _collector.increment_counter("count.orchestrator.semantic.config.update.total")
                _collector.record_latency("orchestrator.semantic.config.update.latency_ms", _elapsed_ms)
            except Exception:
                pass  # 指标采集失败不影响主链路

            return jsonify({
                "ok": True,
                "message": "配置已即时生效",
                "overrides": overrides,
                "old_values": old_values,
                "current_config": new_config,
            })
        except Exception as e:
            logger.error("[语义层配置] 热更失败: %s", e, exc_info=True)
            # 【变易】Prometheus 指标埋点: 失败次数
            try:
                from agent.monitoring.metrics import get_metrics_collector
                get_metrics_collector().increment_counter("count.orchestrator.semantic.config.update.failure")
            except Exception:
                pass  # 指标采集失败不影响主链路
            return jsonify({"ok": False, "error": str(e)}), 500

