# cognitive/test_cognitive/test_flask_adapter.py
import pytest
from cognitive.prompt_injector import PromptInjector
from cognitive.flask_adapter import register_prompt_routes


@pytest.fixture
def app():
    """创建测试用 Flask app"""
    try:
        from flask import Flask
    except ImportError:
        pytest.skip("Flask 未安装，跳过测试")
    app = Flask(__name__)
    app.config["TESTING"] = True
    return app


@pytest.fixture
def injector():
    return PromptInjector()


@pytest.fixture
def sensor_cache():
    return {
        "readings": {
            "cpu": [
                {"sensor_name": "cpu_temperature", "value": 50.0, "unit": "°C",
                 "description": "CPU 温度", "severity": "normal"},
            ],
            "battery": [
                {"sensor_name": "battery_percentage", "value": 50.0, "unit": "%",
                 "description": "电池电量", "severity": "normal"},
            ],
        }
    }


class TestFlaskAdapter:
    def test_register_routes_adds_endpoints(self, app, injector, sensor_cache):
        """注册路由后应添加相关端点"""
        register_prompt_routes(app, injector, sensor_cache)
        rules = [rule.rule for rule in app.url_map.iter_rules()]
        assert "/api/cognitive/status" in rules
        assert "/api/cognitive/prompt" in rules
        assert "/api/cognitive/translate/<sensor_name>" in rules
        assert "/api/cognitive/reject" in rules

    def test_status_endpoint_returns_text(self, app, injector, sensor_cache):
        """GET /api/cognitive/status 应返回文本"""
        register_prompt_routes(app, injector, sensor_cache)
        client = app.test_client()
        resp = client.get("/api/cognitive/status")
        assert resp.status_code == 200
        assert len(resp.data) > 0

    def test_prompt_endpoint_returns_text(self, app, injector, sensor_cache):
        """GET /api/cognitive/prompt 应返回文本"""
        register_prompt_routes(app, injector, sensor_cache)
        client = app.test_client()
        resp = client.get("/api/cognitive/prompt")
        assert resp.status_code == 200
        assert "云枢" in resp.get_data(as_text=True)

    def test_translate_endpoint_known_sensor(self, app, injector, sensor_cache):
        """GET /api/cognitive/translate/cpu_temperature 应返回描述"""
        register_prompt_routes(app, injector, sensor_cache)
        client = app.test_client()
        resp = client.get("/api/cognitive/translate/cpu_temperature")
        assert resp.status_code == 200
        assert len(resp.data) > 0

    def test_translate_endpoint_unknown_sensor(self, app, injector, sensor_cache):
        """GET /api/cognitive/translate/nonexistent 应返回 404"""
        register_prompt_routes(app, injector, sensor_cache)
        client = app.test_client()
        resp = client.get("/api/cognitive/translate/nonexistent")
        assert resp.status_code == 404

    def test_reject_endpoint_returns_json(self, app, injector, sensor_cache):
        """GET /api/cognitive/reject 应返回 JSON"""
        register_prompt_routes(app, injector, sensor_cache)
        client = app.test_client()
        resp = client.get("/api/cognitive/reject")
        assert resp.status_code == 200
        assert resp.is_json
        data = resp.get_json()
        assert "rejected" in data
        assert "reason" in data
