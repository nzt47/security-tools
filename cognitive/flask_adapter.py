# cognitive/flask_adapter.py
import logging

logger = logging.getLogger(__name__)


def register_prompt_routes(app, injector, sensor_cache: dict):
    """为 Flask app 注册元认知 API 路由。

    Args:
        app: Flask 应用实例
        injector: PromptInjector 实例
        sensor_cache: 阶段一的 _CACHE 字典，包含 "readings" 键
    """
    from flask import jsonify, Response

    def _get_all_readings():
        """从缓存中展平所有传感器读数"""
        readings = sensor_cache.get("readings", {})
        if isinstance(readings, dict):
            flat = []
            for group in readings.values():
                if isinstance(group, list):
                    flat.extend(group)
            return flat
        return list(readings) if isinstance(readings, list) else []

    @app.route("/api/cognitive/status")
    def cognitive_status():
        text = injector.get_summary(_get_all_readings())
        return Response(text, mimetype="text/plain; charset=utf-8")

    @app.route("/api/cognitive/prompt")
    def cognitive_prompt():
        text = injector.inject(_get_all_readings())
        return Response(text, mimetype="text/plain; charset=utf-8")

    @app.route("/api/cognitive/translate/<sensor_name>")
    def cognitive_translate(sensor_name):
        for r in _get_all_readings():
            if r.get("sensor_name") == sensor_name:
                return injector.translate(r)
        return {"error": f"sensor '{sensor_name}' not found"}, 404

    @app.route("/api/cognitive/reject")
    def cognitive_reject():
        rejected, reason = injector.should_reject_task(_get_all_readings())
        return jsonify({"rejected": rejected, "reason": reason})

    logger.info("已注册元认知 API 路由 (4 个端点)")
