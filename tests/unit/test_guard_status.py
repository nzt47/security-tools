"""护栏状态服务测试（任务4 Step 4/6）

覆盖（评估标准锚点）:
    1. G1-G5 聚合结构完整（每项含 enabled/status/最近变更/回滚命令）
    2. 状态与真实配置一致（审计断言）：
       - G5 回归门禁模式 == EVOLUTION_REGRESSION_GATE（默认 warn_only）
       - G4 预算模式 == config.yaml learning.budget.mode（warn_only）
       - G2 红线规则数 == value_guard 内置默认（>0）
       - G1 schema 项数 == 52（真实登记表）
    3. /api/learning/guards 只读端点 200 + G1-G5
    4. GUARD_STATUS_ENABLED=false → 关闭态
    5. 单护栏采集失败 → status=unknown（不抛异常）
"""
import json
import os

import pytest

from agent.learning.guard_status import get_guard_status

GUARDS = ("G1", "G2", "G3", "G4", "G5")


@pytest.fixture()
def status(tmp_path, monkeypatch):
    """G1 使用隔离存储（不触碰仓库 store 状态）；其余护栏读真实配置"""
    from agent.learning.meta_policy import reset_meta_policy_store
    reset_meta_policy_store()                       # 防跨测试单例污染
    monkeypatch.setenv("META_POLICY_STORE_DIR", str(tmp_path / "mp_store"))
    yield get_guard_status()
    reset_meta_policy_store()


class TestGuardStatusStructure:
    def test_all_five_guards_present(self, status):
        assert set(status["guards"].keys()) == set(GUARDS)
        assert status["enabled"] is True

    def test_each_guard_has_required_fields(self, status):
        for gid in GUARDS:
            g = status["guards"][gid]
            assert g["id"] == gid
            assert "enabled" in g
            assert "status" in g
            assert "detail" in g
            assert "rollback_command" in g
            assert "latest_change" in g

    def test_summary_counts(self, status):
        s = status["summary"]
        assert s["total_guards"] == 5
        assert s["ready"] + s["watching"] + s["degraded"] + \
            s["disabled"] + s["unknown"] + s["empty"] == 5


class TestGuardStatusConsistency:
    """状态与真实配置一致（审计断言测试）"""

    def test_g1_meta_policy_matches_store(self, status):
        g1 = status["guards"]["G1"]
        assert g1["status"] == "ready"
        assert g1["schema_entries"] == 52        # 真实登记表
        assert g1["current_version"] == "v1"     # 隔离存储引导版本
        assert "rollback" in g1["rollback_command"]

    def test_g2_value_guard_matches_rules(self, status):
        g2 = status["guards"]["G2"]
        # 内置默认红线规则数（value_guard._default_rules），审计断言
        from agent.skills_mgmt.value_guard import _default_rules
        assert g2["rules_count"] == len(_default_rules())
        assert g2["critical_rules_count"] >= 1
        assert g2["enabled"] is True            # VALUE_GUARD_ENABLED 默认 1

    def test_g3_lineage_reports_records(self, status):
        g3 = status["guards"]["G3"]
        assert "records_count" in g3
        assert g3["records_count"] >= 0
        assert "by_decision" in g3

    def test_g4_budget_mode_matches_config(self, status):
        """G4 预算模式与 config.yaml learning.budget.mode 一致"""
        import yaml
        from pathlib import Path
        cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
        expected_mode = cfg["learning"]["budget"]["mode"]   # warn_only
        g4 = status["guards"]["G4"]
        assert g4["budget_mode"] == expected_mode
        assert g4["status"] == "ready"

    def test_g4_rollback_thresholds_match_defaults(self, status):
        """G4 回滚阈值与 rollback.py 默认一致（env 未覆盖时）"""
        from agent.skills_mgmt.rollback import (
            _env_error_rise_pct, _env_latency_rise_pct, _env_max_daily,
            _env_success_drop_pct, _env_window_min,
        )
        g4 = status["guards"]["G4"]
        if os.getenv("ROLLBACK_MAX_DAILY") is None:
            assert g4["max_daily"] == _env_max_daily() == 2
        if os.getenv("ROLLBACK_SUCCESS_DROP_PCT") is None:
            assert g4["success_drop_pct"] == _env_success_drop_pct() == 20.0
        if os.getenv("ROLLBACK_WINDOW_MIN") is None:
            assert g4["window_min"] == _env_window_min() == 1440
        assert g4["error_rise_pct"] == _env_error_rise_pct()
        assert g4["latency_rise_pct"] == _env_latency_rise_pct()

    def test_g5_regression_gate_matches_config(self, status):
        """G5 回归门禁模式与 EVOLUTION_REGRESSION_GATE（默认 warn_only）一致"""
        from agent.skills_mgmt.offline_evolver import _env_regression_gate_mode
        g5 = status["guards"]["G5"]
        assert g5["gate_mode"] == _env_regression_gate_mode() == "warn_only"
        assert g5["status"] == "watching"       # warn_only → 观察态
        assert g5["degrade_threshold"] == 0.05
        assert g5["sampleset_version"] == "v1"
        assert g5["eval_sample_count"] >= 50    # 评估集 v1 ≥50（任务1 交付）
        assert g5["observation_window_weeks"] == 4   # 触发监控观察窗口

    def test_g5_budget_and_baseline_fields(self, status):
        g5 = status["guards"]["G5"]
        assert g5["budget_tokens"] >= 0
        assert g5["baseline_count"] >= 0
        assert 0.0 <= g5["replay_coverage_threshold"] <= 1.0


class TestGuardStatusResilience:
    def test_disabled_returns_closed_state(self, monkeypatch):
        monkeypatch.setenv("GUARD_STATUS_ENABLED", "false")
        st = get_guard_status()
        assert st["enabled"] is False
        assert st["guards"] == {}

    def test_builder_failure_degrades_to_unknown(self, monkeypatch):
        """单项采集失败 → status=unknown（绝不抛异常）"""
        import agent.learning.guard_status as gs
        from agent.learning.meta_policy import reset_meta_policy_store
        reset_meta_policy_store()
        monkeypatch.setattr(gs, "_GUARD_BUILDERS",
                            (("G1", gs._g1_meta_policy),
                             ("G2", gs._g2_value_guard),
                             ("G3", lambda: {"error": "boom"}),
                             ("G4", gs._g4_rollback_budget),
                             ("G5", gs._g5_regression_observe)))
        st = get_guard_status()
        assert st["guards"]["G3"]["status"] == "unknown"
        assert "boom" in st["guards"]["G3"]["detail"]
        reset_meta_policy_store()


class TestGuardsEndpoint:
    def test_endpoint_returns_200_with_g1_g5(self):
        from flask import Flask
        from agent.learning_metrics_api import learning_metrics_bp
        app = Flask(__name__)
        app.register_blueprint(learning_metrics_bp)
        client = app.test_client()
        resp = client.get("/api/learning/guards")
        assert resp.status_code == 200
        data = resp.get_json()
        assert set(data["guards"].keys()) == set(GUARDS)
        assert data["enabled"] is True

    def test_endpoint_readonly_no_side_effects(self, tmp_path):
        """只读端点不产生任何写操作（请求前后 store 目录一致）"""
        import os
        from flask import Flask
        from agent.learning_metrics_api import learning_metrics_bp
        from agent.learning.meta_policy import MetaPolicyStore, reset_meta_policy_store
        reset_meta_policy_store()
        os.environ["META_POLICY_STORE_DIR"] = str(tmp_path / "ep_store")
        try:
            MetaPolicyStore(str(tmp_path / "ep_store"))  # 预热：一次性引导 v1 快照
            app = Flask(__name__)
            app.register_blueprint(learning_metrics_bp)
            client = app.test_client()
            before = sorted(p.name for p in (tmp_path / "ep_store").rglob("*"))
            resp = client.get("/api/learning/guards")
            assert resp.status_code == 200
            after = sorted(p.name for p in (tmp_path / "ep_store").rglob("*"))
            assert before == after   # 只读：请求不产生任何新增文件
        finally:
            os.environ.pop("META_POLICY_STORE_DIR", None)
            reset_meta_policy_store()
