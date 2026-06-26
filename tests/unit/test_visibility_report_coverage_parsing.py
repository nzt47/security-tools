# -*- coding: utf-8 -*-
"""
visibility_report.py 的 coverage.xml 解析与降级逻辑单元测试

【测试目标】
验证 _read_test_coverage() 在以下场景的行为：
1. coverage.xml 有效 line-rate > 0 → 返回真实覆盖率
2. coverage.xml line-rate=0 → 警告日志 + 降级到 pyproject.toml fail_under
3. coverage.xml 缺失 → error 日志 + 降级到 pyproject.toml fail_under
4. coverage.xml 解析失败（非法 XML / 非法 line-rate）→ error 日志 + 降级
5. pyproject.toml 缺失 fail_under → error 日志 + 返回 0.0
6. 全部缺失 → error 日志 + 返回 0.0（不静默）

【可观测性约束】
- 边界显性化：所有降级路径必须输出结构化日志，禁止静默返回 0
- 异常处理：coverage.xml 解析失败时输出明确错误，便于排查 CI artifact 传递问题
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest

# 将 scripts 目录加入 sys.path 以导入 visibility_report 模块
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from visibility_report import MetricCollector  # noqa: E402


# ═══════════════════════════════════════════════════════════════
#  辅助函数
# ═══════════════════════════════════════════════════════════════

def _make_coverage_xml(line_rate: float) -> str:
    """构造最小化 Cobertura coverage.xml 内容"""
    return (
        '<?xml version="1.0" ?>\n'
        f'<coverage line-rate="{line_rate}" '
        'branch-rate="0" version="6.0" timestamp="1700000000000">\n'
        '  <packages />\n'
        '</coverage>\n'
    )


def _make_pyproject(fail_under=None) -> str:
    """构造包含（或不包含）fail_under 的 pyproject.toml"""
    lines = [
        '[build-system]',
        'requires = ["setuptools>=61.0"]',
        '',
        '[tool.coverage.run]',
        'source = ["agent"]',
        '',
        '[tool.coverage.report]',
        'precision = 2',
        'show_missing = true',
    ]
    if fail_under is not None:
        lines.append(f'fail_under = {fail_under}')
    else:
        lines.append('skip_covered = false')
    lines.append('')
    return "\n".join(lines)


def _make_collector(project_root: Path) -> MetricCollector:
    """构造以 project_root 为根的 MetricCollector"""
    return MetricCollector(project_root, {})


# ═══════════════════════════════════════════════════════════════
#  1. coverage.xml 有效 line-rate
# ═══════════════════════════════════════════════════════════════

class TestValidCoverageXml:
    """coverage.xml 提供有效 line-rate 时应直接返回真实覆盖率"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_line_rate_85_percent(self, tmp_path):
        (tmp_path / "coverage.xml").write_text(_make_coverage_xml(0.85), encoding="utf-8")
        collector = _make_collector(tmp_path)
        assert collector._read_test_coverage() == 85.0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_line_rate_100_percent(self, tmp_path):
        (tmp_path / "coverage.xml").write_text(_make_coverage_xml(1.0), encoding="utf-8")
        collector = _make_collector(tmp_path)
        assert collector._read_test_coverage() == 100.0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_line_rate_low_value(self, tmp_path):
        (tmp_path / "coverage.xml").write_text(_make_coverage_xml(0.123), encoding="utf-8")
        collector = _make_collector(tmp_path)
        assert collector._read_test_coverage() == 12.3

    @pytest.mark.unit
    @pytest.mark.p1
    def test_valid_xml_does_not_emit_error(self, tmp_path, caplog):
        """有效 coverage.xml 不应触发任何 error/warning 日志"""
        (tmp_path / "coverage.xml").write_text(_make_coverage_xml(0.9), encoding="utf-8")
        collector = _make_collector(tmp_path)
        with caplog.at_level(logging.WARNING):
            collector._read_test_coverage()
        # 不应有 read_test_coverage 相关的 warning/error
        relevant = [
            r for r in caplog.records
            if "read_test_coverage" in r.getMessage()
        ]
        assert relevant == []


# ═══════════════════════════════════════════════════════════════
#  2. coverage.xml line-rate=0 → 警告 + 降级
# ═══════════════════════════════════════════════════════════════

class TestLineRateZeroFallback:
    """line-rate=0 视为无效报告，降级到 pyproject.toml"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_zero_line_rate_falls_back_to_pyproject(self, tmp_path):
        (tmp_path / "coverage.xml").write_text(_make_coverage_xml(0.0), encoding="utf-8")
        (tmp_path / "pyproject.toml").write_text(_make_pyproject(fail_under=40), encoding="utf-8")
        collector = _make_collector(tmp_path)
        assert collector._read_test_coverage() == 40.0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_zero_line_rate_emits_warning(self, tmp_path, caplog):
        (tmp_path / "coverage.xml").write_text(_make_coverage_xml(0.0), encoding="utf-8")
        (tmp_path / "pyproject.toml").write_text(_make_pyproject(fail_under=40), encoding="utf-8")
        collector = _make_collector(tmp_path)
        with caplog.at_level(logging.WARNING):
            collector._read_test_coverage()
        # 应有 invalid_xml 警告
        msgs = [r.getMessage() for r in caplog.records]
        assert any("invalid_xml" in m for m in msgs), f"未找到 invalid_xml 警告: {msgs}"


# ═══════════════════════════════════════════════════════════════
#  3. coverage.xml 缺失 → error + 降级
# ═══════════════════════════════════════════════════════════════

class TestMissingCoverageXml:
    """coverage.xml 不存在时应输出 error 日志并降级"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_missing_xml_falls_back_to_pyproject(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(_make_pyproject(fail_under=55), encoding="utf-8")
        collector = _make_collector(tmp_path)
        assert collector._read_test_coverage() == 55.0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_missing_xml_emits_error_log(self, tmp_path, caplog):
        (tmp_path / "pyproject.toml").write_text(_make_pyproject(fail_under=40), encoding="utf-8")
        collector = _make_collector(tmp_path)
        with caplog.at_level(logging.ERROR):
            collector._read_test_coverage()
        msgs = [r.getMessage() for r in caplog.records]
        assert any("missing_xml" in m for m in msgs), f"未找到 missing_xml 错误: {msgs}"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_missing_xml_error_includes_actionable_hint(self, tmp_path, caplog):
        """error 日志应包含可操作的排查提示（artifact 传递）"""
        (tmp_path / "pyproject.toml").write_text(_make_pyproject(fail_under=40), encoding="utf-8")
        collector = _make_collector(tmp_path)
        with caplog.at_level(logging.ERROR):
            collector._read_test_coverage()
        msgs = [r.getMessage() for r in caplog.records]
        # 日志应提示 artifact 传递问题
        assert any("artifact" in m or "coverage-report" in m for m in msgs), \
            f"错误日志未包含 artifact 排查提示: {msgs}"


# ═══════════════════════════════════════════════════════════════
#  4. coverage.xml 解析失败 → error + 降级
# ═══════════════════════════════════════════════════════════════

class TestMalformedCoverageXml:
    """coverage.xml 解析失败时应输出 error 日志并降级"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_malformed_xml_falls_back(self, tmp_path):
        (tmp_path / "coverage.xml").write_text("not a valid xml <<<<", encoding="utf-8")
        (tmp_path / "pyproject.toml").write_text(_make_pyproject(fail_under=40), encoding="utf-8")
        collector = _make_collector(tmp_path)
        assert collector._read_test_coverage() == 40.0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_malformed_xml_emits_parse_failed_error(self, tmp_path, caplog):
        (tmp_path / "coverage.xml").write_text("not a valid xml <<<<", encoding="utf-8")
        (tmp_path / "pyproject.toml").write_text(_make_pyproject(fail_under=40), encoding="utf-8")
        collector = _make_collector(tmp_path)
        with caplog.at_level(logging.ERROR):
            collector._read_test_coverage()
        msgs = [r.getMessage() for r in caplog.records]
        assert any("parse_failed" in m for m in msgs), f"未找到 parse_failed 错误: {msgs}"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_non_numeric_line_rate_falls_back(self, tmp_path):
        """line-rate 属性为非数字时应降级"""
        content = (
            '<?xml version="1.0" ?>\n'
            '<coverage line-rate="not-a-number">\n'
            '  <packages />\n'
            '</coverage>\n'
        )
        (tmp_path / "coverage.xml").write_text(content, encoding="utf-8")
        (tmp_path / "pyproject.toml").write_text(_make_pyproject(fail_under=42), encoding="utf-8")
        collector = _make_collector(tmp_path)
        assert collector._read_test_coverage() == 42.0


# ═══════════════════════════════════════════════════════════════
#  5. pyproject.toml 降级路径
# ═══════════════════════════════════════════════════════════════

class TestPyprojectFallback:
    """验证 pyproject.toml fail_under 降级路径"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_pyproject_fail_under_int(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(_make_pyproject(fail_under=40), encoding="utf-8")
        collector = _make_collector(tmp_path)
        assert collector._read_test_coverage() == 40.0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_pyproject_fail_under_float(self, tmp_path):
        # fail_under 支持浮点（regex 已扩展为 \d+(?:\.\d+)?）
        (tmp_path / "pyproject.toml").write_text(_make_pyproject(fail_under=55.5), encoding="utf-8")
        collector = _make_collector(tmp_path)
        assert collector._read_test_coverage() == 55.5

    @pytest.mark.unit
    @pytest.mark.p1
    def test_pyproject_fail_under_with_spaces(self, tmp_path):
        """fail_under = 40（含空格）应正确匹配"""
        (tmp_path / "pyproject.toml").write_text(
            '[tool.coverage.report]\nfail_under   =   60\n', encoding="utf-8"
        )
        collector = _make_collector(tmp_path)
        assert collector._read_test_coverage() == 60.0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_pyproject_emits_fallback_warning(self, tmp_path, caplog):
        """降级到 pyproject.toml 时应输出 warning 日志（非真实覆盖率）"""
        (tmp_path / "pyproject.toml").write_text(_make_pyproject(fail_under=40), encoding="utf-8")
        collector = _make_collector(tmp_path)
        with caplog.at_level(logging.WARNING):
            collector._read_test_coverage()
        msgs = [r.getMessage() for r in caplog.records]
        assert any("fallback_pyproject" in m for m in msgs), f"未找到 fallback 警告: {msgs}"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_pyproject_read_failure_emits_error(self, tmp_path, caplog):
        """pyproject.toml 存在但读取失败（如为目录）时应输出 error 日志并返回 0"""
        # 将 pyproject.toml 创建为目录，使 read_text 抛出 OSError（PermissionError）
        (tmp_path / "pyproject.toml").mkdir()
        collector = _make_collector(tmp_path)
        with caplog.at_level(logging.ERROR):
            result = collector._read_test_coverage()
        assert result == 0.0
        msgs = [r.getMessage() for r in caplog.records]
        assert any("pyproject_read_failed" in m for m in msgs), \
            f"未找到 pyproject_read_failed 错误: {msgs}"


# ═══════════════════════════════════════════════════════════════
#  6. 全部缺失 / 无 fail_under → 返回 0.0 且不静默
# ═══════════════════════════════════════════════════════════════

class TestReturnZeroExplicitly:
    """无任何可用数据源时返回 0.0，且必须输出 error 日志（不静默）"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_both_missing_returns_zero(self, tmp_path):
        collector = _make_collector(tmp_path)
        assert collector._read_test_coverage() == 0.0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_both_missing_emits_error(self, tmp_path, caplog):
        collector = _make_collector(tmp_path)
        with caplog.at_level(logging.ERROR):
            collector._read_test_coverage()
        msgs = [r.getMessage() for r in caplog.records]
        # 应同时有 missing_xml 和 pyproject_missing 错误
        assert any("missing_xml" in m for m in msgs), f"未找到 missing_xml: {msgs}"
        assert any("pyproject_missing" in m for m in msgs), f"未找到 pyproject_missing: {msgs}"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_pyproject_without_fail_under_returns_zero(self, tmp_path, caplog):
        """pyproject.toml 存在但无 fail_under → 返回 0.0 + error 日志"""
        (tmp_path / "pyproject.toml").write_text(_make_pyproject(fail_under=None), encoding="utf-8")
        collector = _make_collector(tmp_path)
        with caplog.at_level(logging.ERROR):
            result = collector._read_test_coverage()
        assert result == 0.0
        msgs = [r.getMessage() for r in caplog.records]
        assert any("fail_under_not_found" in m for m in msgs), \
            f"未找到 fail_under_not_found 错误: {msgs}"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_zero_return_is_explicit_not_silent(self, tmp_path, caplog):
        """返回 0.0 时必须有 error 日志，禁止静默返回"""
        collector = _make_collector(tmp_path)
        with caplog.at_level(logging.WARNING):
            collector._read_test_coverage()
        # 至少有一条 error 级别日志
        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert len(error_records) > 0, "返回 0.0 时未输出任何 error 日志（静默返回）"


# ═══════════════════════════════════════════════════════════════
#  7. 结构化日志格式校验（可观测性强制约束）
# ═══════════════════════════════════════════════════════════════

class TestStructuredLogFormat:
    """验证降级日志符合结构化 JSON 规范（含 trace_id/module_name/action）"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_error_log_is_valid_json_with_required_fields(self, tmp_path, caplog):
        """error 日志应为合法 JSON，且包含 trace_id/module_name/action/duration_ms"""
        collector = _make_collector(tmp_path)
        with caplog.at_level(logging.ERROR):
            collector._read_test_coverage()
        # 找到第一条 JSON 格式的 error 日志
        parsed = None
        for record in caplog.records:
            try:
                data = json.loads(record.getMessage())
                if isinstance(data, dict) and "action" in data:
                    parsed = data
                    break
            except (json.JSONDecodeError, TypeError):
                continue
        assert parsed is not None, "未找到结构化 JSON 日志"
        # 校验必需字段
        assert "trace_id" in parsed, "日志缺少 trace_id"
        assert "module_name" in parsed, "日志缺少 module_name"
        assert "action" in parsed, "日志缺少 action"
        assert "duration_ms" in parsed, "日志缺少 duration_ms"
        assert parsed["module_name"] == "visibility_report"
        assert parsed["action"].startswith("read_test_coverage")
