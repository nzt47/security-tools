"""五层探针归一化回归测试：数据缺失时必须返回 null 而非假满分

覆盖 assessor.assess_with_probes 的【不易】契约：
- available=False 的层不参与加权，其余层按权重重新归一化
- 全部不可用 → overall=None（禁假满分）
- 部分不可用 → 归一化后 overall 与手算一致（±1e-9）
- assess() 显式 None/空 dict → overall=None（历史无参调用仍返回默认 1.0）
"""
import pytest

from agent.health.assessor import DEFAULT_WEIGHTS, HealthAssessor


def _probe(score=None, available=True, detail="test"):
    """构造最小探针对象（鸭子类型：score/available/detail）"""
    class _P:
        def __init__(self):
            self.score = score
            self.available = available
            self.detail = detail
    return _P()


class TestL1L5ProbeMissing:
    """L1-L5 各层单独缺失时的归一化"""

    def setup_method(self):
        self.assessor = HealthAssessor()

    def test_all_layers_available(self):
        probes = {
            "l1_process": _probe(0.9),
            "l2_dependency": _probe(1.0),
            "l3_llm_tool": _probe(1.0),
            "l4_business": _probe(0.2),
            "l5_semantic": _probe(0.75),
        }
        score = self.assessor.assess_with_probes(probes)
        expected = sum(DEFAULT_WEIGHTS[k] * v.score for k, v in probes.items())
        assert score.overall == pytest.approx(expected, abs=1e-9)
        assert score.issues == []

    def test_l5_missing_only(self):
        """仅 L5 缺失：L1-L4 按原权重归一化，issues 注明 l5_semantic 无数据"""
        probes = {
            "l1_process": _probe(0.9),
            "l2_dependency": _probe(1.0),
            "l3_llm_tool": _probe(1.0),
            "l4_business": _probe(0.2),
            "l5_semantic": _probe(available=False),
        }
        score = self.assessor.assess_with_probes(probes)
        avail_w = sum(DEFAULT_WEIGHTS[k] for k in ("l1_process", "l2_dependency",
                                                   "l3_llm_tool", "l4_business"))
        expected = (0.25 * 0.9 + 0.20 * 1.0 + 0.25 * 1.0 + 0.20 * 0.2) / avail_w
        assert score.overall == pytest.approx(expected, abs=1e-9)
        assert "l5_semantic 无数据" in score.issues

    def test_l3_and_l5_missing(self):
        """L3+L5 缺失：L1/L2/L4 归一化"""
        probes = {
            "l1_process": _probe(0.9),
            "l2_dependency": _probe(1.0),
            "l3_llm_tool": _probe(available=False),
            "l4_business": _probe(0.2),
            "l5_semantic": _probe(available=False),
        }
        score = self.assessor.assess_with_probes(probes)
        avail_w = DEFAULT_WEIGHTS["l1_process"] + DEFAULT_WEIGHTS["l2_dependency"] \
            + DEFAULT_WEIGHTS["l4_business"]
        expected = (0.25 * 0.9 + 0.20 * 1.0 + 0.20 * 0.2) / avail_w
        assert score.overall == pytest.approx(expected, abs=1e-9)
        assert "l3_llm_tool 无数据" in score.issues
        assert "l5_semantic 无数据" in score.issues

    def test_l1_l2_l3_l4_l5_all_missing(self):
        """全部不可用 → overall=None（禁假满分）"""
        probes = {k: _probe(available=False) for k in DEFAULT_WEIGHTS}
        score = self.assessor.assess_with_probes(probes)
        assert score.overall is None
        assert "无数据" in score.issues

    def test_empty_probes(self):
        """空 dict → overall=None"""
        score = self.assessor.assess_with_probes({})
        assert score.overall is None
        assert "无数据" in score.issues

    def test_none_probes(self):
        """None → overall=None"""
        score = self.assessor.assess_with_probes(None)
        assert score.overall is None

    def test_l5_missing_keeps_full_precision(self):
        """缺失层不参与后，overall 保留完整精度（不 round）"""
        probes = {
            "l1_process": _probe(0.9),
            "l2_dependency": _probe(1.0),
            "l3_llm_tool": _probe(1.0),
            "l4_business": _probe(0.2),
            "l5_semantic": _probe(available=False),
        }
        score = self.assessor.assess_with_probes(probes)
        # L1-L4 可用（0.9/1.0/1.0/0.2），权重 0.25/0.20/0.25/0.20
        # (0.25*0.9+0.20*1.0+0.25*1.0+0.20*0.2)/0.90 = 0.794444... 应保留完整小数位
        assert score.overall == pytest.approx(0.7944444444444445, abs=1e-9)
        assert score.overall != round(score.overall, 4)


class TestAssessNoDataContract:
    """assess() 无数据契约：显式 None/空 dict → null；无参调用 → 默认 1.0"""

    def setup_method(self):
        self.assessor = HealthAssessor()

    def test_no_arg_returns_default_full_score(self):
        """无参调用（历史接口兼容）→ 默认健康分 1.0"""
        score = self.assessor.assess()
        assert score.overall == 1.0
        assert score.issues == []

    def test_explicit_none_returns_null(self):
        score = self.assessor.assess(None)
        assert score.overall is None
        assert "无数据" in score.issues

    def test_empty_dict_returns_null(self):
        score = self.assessor.assess({})
        assert score.overall is None
        assert "无数据" in score.issues


class TestProbeStructuredLog:
    """验收标准 7：探针结构化日志（module_name=health_probes 的完成/失败记录）"""

    def test_probe_completed_log(self, caplog):
        """可用探针 → probe.<layer>.completed 结构化日志"""
        import json
        import logging

        from agent.health.probes import ProbeResult, _log_probe

        with caplog.at_level(logging.INFO, logger="agent.health.probes"):
            _log_probe(ProbeResult("l1_process", 0.9, "mem=50% cpu=10%"))
        payload = json.loads(caplog.records[-1].message)
        assert payload["module_name"] == "health_probes"
        assert payload["action"] == "probe.l1_process.completed"
        assert payload["score"] == 0.9
        assert payload["available"] is True

    def test_probe_failed_log(self, caplog):
        """无数据探针 → probe.<layer>.failed 结构化日志（warning 级别）"""
        import json
        import logging

        from agent.health.probes import ProbeResult, _log_probe

        with caplog.at_level(logging.WARNING, logger="agent.health.probes"):
            _log_probe(ProbeResult("l5_semantic", None, "近 7 天无用户反馈", available=False))
        payload = json.loads(caplog.records[-1].message)
        assert payload["module_name"] == "health_probes"
        assert payload["action"] == "probe.l5_semantic.failed"
        assert payload["score"] is None
        assert payload["available"] is False

    def test_run_all_probes_logs_each_layer(self, caplog):
        """run_all_probes 为每层输出一条结构化日志"""
        import json
        import logging

        from agent.health.probes import run_all_probes

        with caplog.at_level(logging.INFO, logger="agent.health.probes"):
            results = run_all_probes()
        assert len(results) == 5
        probe_logs = [
            json.loads(r.message) for r in caplog.records
            if r.name == "agent.health.probes" and r.message
        ]
        assert len(probe_logs) == 5
        for rec in probe_logs:
            assert rec["module_name"] == "health_probes"
            assert rec["action"].startswith("probe.")
