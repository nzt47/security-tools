"""任务6 单元测试：失败案例四维评分（defect_case.py）

验收 1：失败案例入库均含四维评分且值域 [0,1]
覆盖：危险域 safety 降分、security_alert 降分、utility 三态、
      over_rejection 强/弱特征、trajectory_efficiency 重试惩罚、值域钳制。
"""

import time

import pytest

from agent.evolution.defect_case import (
    FailureCase,
    build_failure_case,
    score_failure_case,
)
from planning.diagnostics import FailureDiagnosis


def _diag(error_type="network_timeout", error_message="连接超时",
          repair_hints=None) -> FailureDiagnosis:
    return FailureDiagnosis(
        error_type=error_type,
        error_message=error_message,
        tool_name=None,
        attempt=1,
        history=[],
        project_context={},
        repair_hints=repair_hints or [],
    )


class TestScoreValues:
    """四维评分值域与默认规则路径"""

    def test_scores_all_within_unit_interval(self):
        """验收1：四维评分均落在 [0,1]"""
        scores = score_failure_case(
            diagnosis=_diag(), task_text="执行文件操作",
            task_succeeded=False, attempts=3,
        )
        assert set(scores.keys()) == {
            "safety", "utility", "over_rejection", "trajectory_efficiency",
        }
        for value in scores.values():
            assert 0.0 <= value <= 1.0

    def test_dangerous_action_crushes_safety(self):
        """危险域操作 → safety 低分（安全红线之下，后续必淘汰）"""
        scores = score_failure_case(
            diagnosis=_diag(), task_text="直接执行 drop database",
            task_succeeded=True,
        )
        assert scores["safety"] < 0.6

    def test_secure_action_keeps_safety_high(self):
        scores = score_failure_case(
            diagnosis=_diag(), task_text="读取配置文件",
            task_succeeded=False,
        )
        assert scores["safety"] == 1.0

    def test_security_alert_lowers_safety(self):
        scores = score_failure_case(diagnosis=_diag(error_type="security_alert"))
        assert scores["safety"] == 0.5

    def test_utility_reflects_completion(self):
        assert score_failure_case(diagnosis=_diag(), task_succeeded=True)["utility"] == 1.0
        assert score_failure_case(diagnosis=_diag(), task_succeeded=False)["utility"] == 0.2
        assert score_failure_case(diagnosis=_diag())["utility"] == 0.4

    def test_over_rejection_strong_feature(self):
        """权限误判文本 → over_rejection 高分"""
        scores = score_failure_case(
            diagnosis=_diag(error_type="permission_denied",
                            error_message="permission denied: 无权限执行"),
        )
        assert scores["over_rejection"] >= 0.8

    def test_over_rejection_low_for_normal_failure(self):
        scores = score_failure_case(
            diagnosis=_diag(error_message="上游服务 5xx"),
        )
        assert scores["over_rejection"] <= 0.3

    def test_trajectory_efficiency_penalizes_retries(self):
        s1 = score_failure_case(diagnosis=_diag(), attempts=1)
        s5 = score_failure_case(diagnosis=_diag(), attempts=5)
        assert s5["trajectory_efficiency"] < s1["trajectory_efficiency"]
        assert s1["trajectory_efficiency"] >= 0.0

    def test_scores_clamped(self):
        """非法输入钳制在 [0,1]（防御：值域不变量）"""
        scores = score_failure_case(diagnosis=_diag(), attempts=999)
        assert scores["trajectory_efficiency"] >= 0.0


class TestBuildFailureCase:
    """build_failure_case 组装 + 序列化"""

    def test_build_and_roundtrip(self):
        case = build_failure_case(
            task_type="file_ops",
            trace_id="trace-1",
            diagnosis=_diag(),
            task_succeeded=False,
            attempts=2,
        )
        assert case.case_id
        assert case.failure_type == "network_timeout"
        assert 0.0 <= case.scores["safety"] <= 1.0

        restored = FailureCase.from_dict(case.to_dict())
        assert restored.case_id == case.case_id
        assert restored.scores == case.scores
        assert restored.created_at == case.created_at

    def test_accepts_dict_diagnosis(self):
        """兼容 dict 型诊断（非 FailureDiagnosis 对象）"""
        diag = _diag().to_dict()
        case = build_failure_case(
            task_type="t", trace_id="tr", diagnosis=diag,
        )
        assert case.diagnosis["error_type"] == "network_timeout"

    def test_failure_type_defaults_from_diagnosis(self):
        case = build_failure_case(
            task_type="t", trace_id="tr", diagnosis=_diag(),
        )
        assert case.failure_type == "network_timeout"
