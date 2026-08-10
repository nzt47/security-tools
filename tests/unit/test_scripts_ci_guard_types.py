#!/usr/bin/env python3
"""scripts/ci_guard_types.py 单元测试草稿（批次 1 · 门禁类）

【目标】100% 覆盖 validate_report() 的结构/枚举/一致性校验分支：
1. report 非 dict → 单错误
2. tool 标识不匹配
3. timestamp 非字符串 / 非法 ISO
4. steps 缺失 / 空 / 元素非 dict / 缺必需字段 / exit_code 非 int / 未知 step
5. overall 缺失 / status 非法 / exit_code 非 int / status 与 exit_code 不一致
6. overall.exit_code 与 guard_verify 步骤不一致
7. 合法报告 → 空列表（通过）
"""

import pytest
import copy
from pathlib import Path
import importlib.util


def _load():
    spec = importlib.util.spec_from_file_location(
        "ci_guard_types",
        Path(__file__).resolve().parents[2] / "scripts" / "ci_guard_types.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CG = _load()

# 合法报告样例（与 run_ci_guard.py --json 输出契约一致）
VALID_REPORT = {
    "tool": "run_ci_guard",
    "timestamp": "2026-08-09T12:00:00Z",
    "steps": [
        {"step": "detect", "status": "ok", "exit_code": 0, "details": {}},
        {"step": "rollback_sim", "status": "ok", "exit_code": 0, "details": {}},
        {"step": "guard_verify", "status": "ok", "exit_code": 0, "details": {}},
    ],
    "overall": {"status": "pass", "exit_code": 0},
}


def _valid():
    """深拷贝合法报告：避免测试间共享 steps/overall 引用导致污染"""
    return copy.deepcopy(VALID_REPORT)


class TestReportType:
    def test_non_dict_returns_error(self):
        for bad in ([], "str", 42, None):
            errs = CG.validate_report(bad)
            assert len(errs) == 1
            assert "必须是 dict" in errs[0]

    def test_tool_mismatch(self):
        report = _valid()
        report["tool"] = "other"
        errs = CG.validate_report(report)
        assert any("tool" in e and "应为" in e for e in errs)


class TestTimestamp:
    def test_non_string(self):
        report = _valid()
        report["timestamp"] = 12345
        errs = CG.validate_report(report)
        assert any("timestamp" in e and "必须是字符串" in e for e in errs)

    def test_invalid_iso(self):
        report = _valid()
        report["timestamp"] = "not-a-date"
        errs = CG.validate_report(report)
        assert any("timestamp" in e and "不是合法 ISO" in e for e in errs)

    def test_valid_iso_ok(self):
        assert CG.validate_report(_valid()) == []


class TestSteps:
    def test_missing_steps(self):
        report = _valid()
        report.pop("steps")
        errs = CG.validate_report(report)
        assert any("steps" in e and "非空列表" in e for e in errs)

    def test_empty_steps(self):
        report = _valid()
        report["steps"] = []
        errs = CG.validate_report(report)
        assert any("steps" in e and "非空列表" in e for e in errs)

    def test_step_not_dict(self):
        """steps 内非 dict 元素应被记录错误而非抛异常（2026-08-10 修复：L96 过滤非 dict）"""
        report = _valid()
        report["steps"] = ["not-dict"]
        errs = CG.validate_report(report)
        assert any("steps[0]" in e and "必须是 dict" in e for e in errs)

    def test_step_missing_required_keys(self):
        report = _valid()
        report["steps"] = [{"step": "detect"}]
        errs = CG.validate_report(report)
        missing = [e for e in errs if "缺少字段" in e]
        assert any("status" in e for e in missing)
        assert any("exit_code" in e for e in missing)
        assert any("details" in e for e in missing)

    def test_step_exit_code_not_int(self):
        report = _valid()
        report["steps"] = [
            {"step": "detect", "status": "ok", "exit_code": "0", "details": {}},
        ]
        errs = CG.validate_report(report)
        assert any("exit_code" in e and "必须是整数" in e for e in errs)

    def test_step_unknown_name(self):
        report = _valid()
        report["steps"] = [
            {"step": "hack", "status": "ok", "exit_code": 0, "details": {}},
        ]
        errs = CG.validate_report(report)
        assert any("未知步骤" in e for e in errs)


class TestOverall:
    def test_missing_overall(self):
        report = _valid()
        report.pop("overall")
        errs = CG.validate_report(report)
        assert any("overall" in e and "必须是 dict" in e for e in errs)

    def test_status_not_allowed(self):
        report = _valid()
        report["overall"] = {"status": "maybe", "exit_code": 0}
        errs = CG.validate_report(report)
        assert any("overall.status" in e and "未知状态" in e for e in errs)

    def test_exit_code_not_int(self):
        report = _valid()
        report["overall"] = {"status": "pass", "exit_code": "0"}
        errs = CG.validate_report(report)
        assert any("overall.exit_code" in e and "必须是整数" in e for e in errs)

    def test_status_exit_code_inconsistent(self):
        report = _valid()
        report["overall"] = {"status": "pass", "exit_code": 1}
        errs = CG.validate_report(report)
        assert any("status/exit_code 不一致" in e for e in errs)

    def test_fail_with_zero_exit_code_inconsistent(self):
        report = _valid()
        report["overall"] = {"status": "fail", "exit_code": 0}
        errs = CG.validate_report(report)
        assert any("status/exit_code 不一致" in e for e in errs)


class TestGuardVerifyConsistency:
    def test_overall_mismatch_guard_verify(self):
        """overall.exit_code 与最后一个 guard_verify 步骤不一致"""
        report = _valid()
        report["steps"][-1]["exit_code"] = 1
        errs = CG.validate_report(report)
        assert any("与 guard_verify 步骤不一致" in e for e in errs)

    def test_no_guard_verify_no_check(self):
        """无 guard_verify 步骤时不触发一致性校验"""
        report = _valid()
        report["steps"] = [
            {"step": "detect", "status": "ok", "exit_code": 0, "details": {}},
        ]
        errs = CG.validate_report(report)
        assert not any("guard_verify" in e for e in errs)


class TestValidReport:
    def test_valid_returns_empty(self):
        assert CG.validate_report(_valid()) == []


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
