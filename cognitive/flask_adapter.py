# cognitive/flask_adapter.py
"""元认知 API 路由：把 PromptInjector 的注入 / 翻译 / 拒答暴露为 Flask 端点。

注：/api/cognitive/prompt 与 /translate 是**调试面**（原文可能含传感器原始文本），
按 config/route_ui_intent.json 标注为「不该进 UI」。
"""
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
        """按当前传感器读数生成完整注入提示词（调试面，非用户 UI）。"""
        text = injector.inject(_get_all_readings())
        return Response(text, mimetype="text/plain; charset=utf-8")

    @app.route("/api/cognitive/translate/<sensor_name>")
    def cognitive_translate(sensor_name):
        """把指定传感器的读数翻译成自然语言；sensor_name 不存在时 404。"""
        for r in _get_all_readings():
            if r.get("sensor_name") == sensor_name:
                return injector.translate(r)
        return {"error": f"sensor '{sensor_name}' not found"}, 404

    @app.route("/api/cognitive/reject")
    def cognitive_reject():
        """判断当前读数下任务是否应被拒绝，返回 {rejected, reason}。"""
        rejected, reason = injector.should_reject_task(_get_all_readings())
        return jsonify({"rejected": rejected, "reason": reason})

    logger.info("已注册元认知 API 路由 (4 个端点)")
