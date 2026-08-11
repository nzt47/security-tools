"""routes_health 单元测试

覆盖 agent/server_routes/routes_health.py 的全部路由端点:
  - /api/health/score, /calculate, /trend, /history, /weights (GET/PUT),
    /summary, /quick-check, /export (json/csv)
  - _collect_system_metrics 的默认值/心跳/Prometheus/历史/异常分支

设计原则: AAA; 用真实 Flask test_client 触发路由, mock 计算器与系统指标,
避免依赖 HealthScoreCalculator 的持久化行为。
"""
# pylint: disable=redefined-outer-name,missing-function-docstring,protected-access

from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from agent.server_routes.routes_health import register_routes, _collect_system_metrics


class _Dim:
    """模拟 DimensionScore: 提供 .score 属性(CSV 分支)且可 JSON 序列化"""

    def __init__(self, score):
        self.score = score
        self.weight = 0.1
        self.indicators = {}
        self.issues = []


def _full_dims():
    """模拟 HealthReport 的完整六维度(CSV 导出需要每个维度都有 .score)"""
    return {
        "stability": _Dim(90.0),
        "performance": _Dim(80.0),
        "quality": _Dim(70.0),
        "efficiency": _Dim(60.0),
        "availability": _Dim(50.0),
        "security": _Dim(40.0),
    }


class FakeReport:
    """模拟 HealthReport: 仅暴露路由用到的属性/方法"""

    def __init__(self, score=80.0, level="good", dimensions=None):
        self.overall_score = score
        self.level = level
        self.timestamp = "2026-08-11T12:00:00"
        self.critical_issues = ["issue1"]
        self.recommendations = ["rec1"]
        self.dimensions = dimensions if dimensions is not None else _full_dims()

    def to_dict(self):
        return {
            "overall_score": self.overall_score,
            "level": self.level,
            "timestamp": self.timestamp,
            "dimensions": {
                name: {"score": d.score, "weight": d.weight,
                       "indicators": d.indicators, "issues": d.issues}
                for name, d in self.dimensions.items()
            },
            "critical_issues": self.critical_issues,
            "recommendations": self.recommendations,
        }


class FakeCalculator:
    def __init__(self, reports=None):
        self.reports = reports or [FakeReport()]
        self.weights = {"stability": 0.2}

    def calculate(self, metrics):
        return FakeReport()

    def get_history(self, n=10):
        return self.reports[:n]

    def get_trend(self, n=10):
        return {"trend": "stable", "points": n}


@pytest.fixture
def client():
    app = Flask(__name__)
    register_routes(app, {"state": "ok"})
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def mock_calc():
    calc = FakeCalculator()
    with patch("agent.server_routes.routes_health.get_calculator", return_value=calc), \
         patch("agent.server_routes.routes_health._collect_system_metrics",
               return_value={"error_rate": 0.01}):
        yield calc


# ═══════════════════════════════════════════════════════════════
# /api/health/score
# ═══════════════════════════════════════════════════════════════

class TestHealthScore:
    def test_score_success(self, client, mock_calc):
        resp = client.get("/api/health/score")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["overall_score"] == 80.0
        assert data["level"] == "good"

    def test_score_error(self, client, mock_calc):
        mock_calc.calculate = MagicMock(side_effect=ValueError("bad metrics"))
        resp = client.get("/api/health/score")
        assert resp.status_code == 500
        data = resp.get_json()
        assert data["overall_score"] == 0
        assert data["level"] == "critical"

    def test_score_calculate_post(self, client, mock_calc):
        resp = client.post("/api/health/score/calculate", json={"error_rate": 0.5})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["report"]["overall_score"] == 80.0

    def test_score_calculate_post_error(self, client, mock_calc):
        mock_calc.calculate = MagicMock(side_effect=RuntimeError("boom"))
        resp = client.post("/api/health/score/calculate", json={})
        assert resp.status_code == 500
        assert resp.get_json()["ok"] is False

    def test_score_calculate_empty_body(self, client, mock_calc):
        # 真实行为: 无 JSON body 时 request.get_json() 抛 BadRequest → 500 ok:False
        resp = client.post("/api/health/score/calculate")
        assert resp.status_code == 500
        assert resp.get_json()["ok"] is False


# ═══════════════════════════════════════════════════════════════
# /api/health/trend & history
# ═══════════════════════════════════════════════════════════════

class TestHealthTrendHistory:
    def test_trend(self, client, mock_calc):
        resp = client.get("/api/health/trend?hours=48")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["trend"]["trend"] == "stable"
        assert data["data_points"] == 1

    def test_trend_error(self, client, mock_calc):
        mock_calc.get_history = MagicMock(side_effect=ValueError("x"))
        resp = client.get("/api/health/trend")
        assert resp.status_code == 500
        assert "error" in resp.get_json()

    def test_history(self, client, mock_calc):
        mock_calc.reports = [FakeReport(score=70 + i, level="fair") for i in range(5)]
        resp = client.get("/api/health/history?limit=2&offset=1")
        assert resp.status_code == 200
        data = resp.get_json()
        # 路由内部 get_history(n=limit*2)=4, 故 total=4
        assert data["total"] == 4
        assert len(data["history"]) == 2

    def test_history_error(self, client, mock_calc):
        mock_calc.get_history = MagicMock(side_effect=ValueError("x"))
        resp = client.get("/api/health/history")
        assert resp.status_code == 500


# ═══════════════════════════════════════════════════════════════
# /api/health/weights
# ═══════════════════════════════════════════════════════════════

class TestHealthWeights:
    def test_weights_get(self, client, mock_calc):
        resp = client.get("/api/health/weights")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["weights"] == {"stability": 0.2}
        assert "dimensions" in data

    def test_weights_put_success(self, client, mock_calc):
        resp = client.put("/api/health/weights", json={"weights": {"stability": 1.0}})
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True
        assert mock_calc.weights == {"stability": 1.0}

    def test_weights_put_invalid_sum(self, client, mock_calc):
        resp = client.put("/api/health/weights", json={"weights": {"stability": 0.5}})
        assert resp.status_code == 400
        assert resp.get_json()["ok"] is False
        assert "权重总和" in resp.get_json()["error"]


# ═══════════════════════════════════════════════════════════════
# /api/health/summary
# ═══════════════════════════════════════════════════════════════

class TestHealthSummary:
    def test_summary_with_history(self, client, mock_calc):
        resp = client.get("/api/health/summary")
        assert resp.status_code == 200
        assert resp.get_json()["overall_score"] == 80.0

    def test_summary_empty_history(self, client, mock_calc):
        mock_calc.reports = []
        resp = client.get("/api/health/summary")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["overall_score"] == 100
        assert data["level"] == "excellent"
        assert data["dimensions"] == {}

    def test_summary_error(self, client, mock_calc):
        mock_calc.get_history = MagicMock(side_effect=ValueError("x"))
        resp = client.get("/api/health/summary")
        assert resp.status_code == 500


# ═══════════════════════════════════════════════════════════════
# /api/health/quick-check
# ═══════════════════════════════════════════════════════════════

class TestHealthQuickCheck:
    def test_quick_check_normal(self, client):
        fake_psutil = MagicMock()
        fake_psutil.cpu_percent.return_value = 50.0
        fake_psutil.virtual_memory.return_value = MagicMock(percent=50.0, available=8 * 1024**3)
        fake_psutil.disk_usage.return_value = MagicMock(percent=50.0, free=50 * 1024**3)
        with patch.dict("sys.modules", {"psutil": fake_psutil}):
            resp = client.get("/api/health/quick-check")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["score"] == 100
        assert data["level"] == "excellent"
        assert data["issues"] == []

    def test_quick_check_high_load(self, client):
        fake_psutil = MagicMock()
        fake_psutil.cpu_percent.return_value = 95.0
        fake_psutil.virtual_memory.return_value = MagicMock(percent=95.0, available=1 * 1024**3)
        fake_psutil.disk_usage.return_value = MagicMock(percent=95.0, free=5 * 1024**3)
        with patch.dict("sys.modules", {"psutil": fake_psutil}):
            resp = client.get("/api/health/quick-check")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["score"] < 100
        assert len(data["issues"]) >= 3

    def test_quick_check_no_psutil(self, client):
        with patch.dict("sys.modules", {"psutil": None}):
            resp = client.get("/api/health/quick-check")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is False
        assert "psutil" in data["error"]


# ═══════════════════════════════════════════════════════════════
# /api/health/export
# ═══════════════════════════════════════════════════════════════

class TestHealthExport:
    def test_export_json(self, client, mock_calc):
        mock_calc.reports = [FakeReport(), FakeReport(score=60, level="poor")]
        resp = client.get("/api/health/export")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["count"] == 2
        assert len(data["history"]) == 2

    def test_export_csv(self, client, mock_calc):
        mock_calc.reports = [FakeReport()]
        resp = client.get("/api/health/export?format=csv")
        assert resp.status_code == 200
        assert resp.content_type.startswith("text/csv")
        assert "Content-Disposition" in resp.headers
        body = resp.data.decode("utf-8")
        assert "时间" in body  # 表头
        assert "80.0" in body

    def test_export_csv_with_dimensions(self, client, mock_calc):
        dims = {
            "stability": MagicMock(score=90.0),
            "performance": MagicMock(score=80.0),
            "quality": MagicMock(score=70.0),
            "efficiency": MagicMock(score=60.0),
            "availability": MagicMock(score=50.0),
            "security": MagicMock(score=40.0),
        }
        mock_calc.reports = [FakeReport(dimensions=dims)]
        resp = client.get("/api/health/export?format=csv")
        assert resp.status_code == 200
        body = resp.data.decode("utf-8")
        assert "90.0" in body and "40.0" in body

    def test_export_error(self, client, mock_calc):
        mock_calc.get_history = MagicMock(side_effect=ValueError("x"))
        resp = client.get("/api/health/export")
        assert resp.status_code == 500


# ═══════════════════════════════════════════════════════════════
# _collect_system_metrics
# ═══════════════════════════════════════════════════════════════

class TestCollectSystemMetrics:
    @pytest.fixture
    def isolated(self):
        """隔离真实 scheduler/prometheus/history, 使默认指标不被环境数据污染"""
        with patch.dict("sys.modules", {"agent.task_scheduler": None,
                                        "agent.prometheus_exporter": None}), \
             patch("agent.server_routes.routes_health.get_calculator",
                   return_value=FakeCalculator(reports=[])):
            yield

    def test_defaults(self, isolated):
        metrics = _collect_system_metrics({})
        assert metrics["error_rate"] == 0.01
        assert metrics["cpu_usage"] == 0.5
        assert metrics["security_alerts"] == 0

    def test_heartbeat_data(self):
        scheduler = MagicMock()
        scheduler.get_heartbeat_status.return_value = {
            "latest": {"checks": {"system": {"cpu": 60, "memory": 70, "disk": 80}}},
        }
        with patch.dict("sys.modules", {"agent.task_scheduler": MagicMock(get_scheduler=lambda: scheduler)}), \
             patch("agent.server_routes.routes_health.get_calculator",
                   return_value=FakeCalculator(reports=[])):
            metrics = _collect_system_metrics({})
        assert metrics["cpu_usage"] == 0.6
        assert metrics["memory_usage"] == 0.7

    def test_heartbeat_import_error(self):
        with patch.dict("sys.modules", {"agent.task_scheduler": None}), \
             patch("agent.server_routes.routes_health.get_calculator",
                   return_value=FakeCalculator(reports=[])):
            metrics = _collect_system_metrics({})
        assert metrics["cpu_usage"] == 0.5  # 保持默认

    def test_prometheus_metrics(self):
        prom = MagicMock()
        prom.get_prometheus_metrics.return_value = {
            "latency_p99": 2.5, "latency_p95": 1.5, "error_rate": 0.2, "request_count": 500,
        }
        with patch.dict("sys.modules", {"agent.prometheus_exporter": prom}), \
             patch("agent.server_routes.routes_health.get_calculator",
                   return_value=FakeCalculator(reports=[])):
            metrics = _collect_system_metrics({})
        assert metrics["p99_latency"] == 2.5
        assert metrics["error_rate"] == 0.2
        assert metrics["total_requests"] == 500

    def test_history_reference(self):
        dims = {"stability": MagicMock(indicators={"error_rate": 0.05})}
        with patch("agent.server_routes.routes_health.get_calculator",
                   return_value=FakeCalculator(reports=[FakeReport(dimensions=dims)])):
            metrics = _collect_system_metrics({})
        # error_rate 参考历史值(0.01*1.1=0.011 < 0.05 → 取 0.011)
        assert metrics["error_rate"] == pytest.approx(0.011, abs=1e-9)

    def test_history_reference_no_dims(self):
        with patch("agent.server_routes.routes_health.get_calculator",
                   return_value=FakeCalculator(reports=[FakeReport(dimensions={})])):
            metrics = _collect_system_metrics({})
        assert metrics["error_rate"] == 0.01
