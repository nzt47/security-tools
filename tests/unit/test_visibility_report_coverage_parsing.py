# -*- coding: utf-8 -*-
"""
visibility_report.py 的 coverage.xml 解析逻辑单元测试

【测试目标】
验证 _read_test_coverage() 在以下场景的行为：
1. coverage.xml 有效 line-rate > 0 → 返回真实覆盖率
2. coverage.xml line-rate=0 → 警告日志 + 返回 0.0（不再降级到 pyproject.toml）
3. coverage.xml 缺失 → error 日志 + 返回 0.0（不再降级到 pyproject.toml）
4. coverage.xml 解析失败（非法 XML / 非法 line-rate）→ error 日志 + 返回 0.0
5. 无任何数据源 → error 日志 + 返回 0.0（不静默）

【设计变更说明】
自 2026-06-27 起，_read_test_coverage() 不再读取 pyproject.toml fail_under 作为降级基线。
原因：CI 中 coverage.xml 由 full-project-tests job 通过 artifact 保证就位，
      用配置基线（如 40%）掩盖真实覆盖率缺失会导致指标失真。
      coverage.xml 缺失/无效时直接返回 0.0 并报错，显式暴露问题。

【可观测性约束】
- 边界显性化：所有返回 0.0 的路径必须输出结构化日志，禁止静默返回
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
#  2. coverage.xml line-rate=0 → 警告 + 返回 0.0（不再降级）
# ═══════════════════════════════════════════════════════════════

class TestLineRateZeroReturnsZero:
    """line-rate=0 视为无效报告，返回 0.0 并输出警告（不再降级到 pyproject.toml）"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_zero_line_rate_returns_zero(self, tmp_path):
        """line-rate=0 时应返回 0.0，不再降级到 pyproject.toml fail_under"""
        (tmp_path / "coverage.xml").write_text(_make_coverage_xml(0.0), encoding="utf-8")
        # 即使 pyproject.toml 存在 fail_under=40，也不应降级
        (tmp_path / "pyproject.toml").write_text(
            '[tool.coverage.report]\nfail_under = 40\n', encoding="utf-8"
        )
        collector = _make_collector(tmp_path)
        assert collector._read_test_coverage() == 0.0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_zero_line_rate_emits_warning(self, tmp_path, caplog):
        """line-rate=0 应输出 invalid_xml 警告日志"""
        (tmp_path / "coverage.xml").write_text(_make_coverage_xml(0.0), encoding="utf-8")
        collector = _make_collector(tmp_path)
        with caplog.at_level(logging.WARNING):
            collector._read_test_coverage()
        msgs = [r.getMessage() for r in caplog.records]
        assert any("invalid_xml" in m for m in msgs), f"未找到 invalid_xml 警告: {msgs}"


# ═══════════════════════════════════════════════════════════════
#  3. coverage.xml 缺失 → error + 返回 0.0（不再降级）
# ═══════════════════════════════════════════════════════════════

class TestMissingCoverageXml:
    """coverage.xml 不存在时应输出 error 日志并返回 0.0（不再降级到 pyproject.toml）"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_missing_xml_returns_zero(self, tmp_path):
        """coverage.xml 缺失时返回 0.0，不再降级到 pyproject.toml fail_under"""
        # 即使 pyproject.toml 存在 fail_under=55，也不应降级
        (tmp_path / "pyproject.toml").write_text(
            '[tool.coverage.report]\nfail_under = 55\n', encoding="utf-8"
        )
        collector = _make_collector(tmp_path)
        assert collector._read_test_coverage() == 0.0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_missing_xml_emits_error_log(self, tmp_path, caplog):
        """coverage.xml 缺失应输出 missing_xml error 日志"""
        collector = _make_collector(tmp_path)
        with caplog.at_level(logging.ERROR):
            collector._read_test_coverage()
        msgs = [r.getMessage() for r in caplog.records]
        assert any("missing_xml" in m for m in msgs), f"未找到 missing_xml 错误: {msgs}"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_missing_xml_error_includes_actionable_hint(self, tmp_path, caplog):
        """error 日志应包含可操作的排查提示（artifact 传递）"""
        collector = _make_collector(tmp_path)
        with caplog.at_level(logging.ERROR):
            collector._read_test_coverage()
        msgs = [r.getMessage() for r in caplog.records]
        # 日志应提示 artifact 传递问题或本地生成方式
        assert any("artifact" in m or "coverage-report" in m or "full-project-tests" in m
                   for m in msgs), \
            f"错误日志未包含 artifact 排查提示: {msgs}"


# ═══════════════════════════════════════════════════════════════
#  4. coverage.xml 解析失败 → error + 返回 0.0（不再降级）
# ═══════════════════════════════════════════════════════════════

class TestMalformedCoverageXml:
    """coverage.xml 解析失败时应输出 error 日志并返回 0.0（不再降级）"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_malformed_xml_returns_zero(self, tmp_path):
        """非法 XML 解析失败时返回 0.0，不再降级到 pyproject.toml"""
        (tmp_path / "coverage.xml").write_text("not a valid xml <<<<", encoding="utf-8")
        # 即使 pyproject.toml 存在 fail_under=40，也不应降级
        (tmp_path / "pyproject.toml").write_text(
            '[tool.coverage.report]\nfail_under = 40\n', encoding="utf-8"
        )
        collector = _make_collector(tmp_path)
        assert collector._read_test_coverage() == 0.0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_malformed_xml_emits_parse_failed_error(self, tmp_path, caplog):
        """非法 XML 应输出 parse_failed error 日志"""
        (tmp_path / "coverage.xml").write_text("not a valid xml <<<<", encoding="utf-8")
        collector = _make_collector(tmp_path)
        with caplog.at_level(logging.ERROR):
            collector._read_test_coverage()
        msgs = [r.getMessage() for r in caplog.records]
        assert any("parse_failed" in m for m in msgs), f"未找到 parse_failed 错误: {msgs}"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_non_numeric_line_rate_returns_zero(self, tmp_path):
        """line-rate 属性为非数字时应返回 0.0（不再降级）"""
        content = (
            '<?xml version="1.0" ?>\n'
            '<coverage line-rate="not-a-number">\n'
            '  <packages />\n'
            '</coverage>\n'
        )
        (tmp_path / "coverage.xml").write_text(content, encoding="utf-8")
        # 即使 pyproject.toml 存在 fail_under=42，也不应降级
        (tmp_path / "pyproject.toml").write_text(
            '[tool.coverage.report]\nfail_under = 42\n', encoding="utf-8"
        )
        collector = _make_collector(tmp_path)
        assert collector._read_test_coverage() == 0.0


# ═══════════════════════════════════════════════════════════════
#  5. 无任何数据源 → 返回 0.0 且不静默
# ═══════════════════════════════════════════════════════════════

class TestReturnZeroExplicitly:
    """无 coverage.xml 时返回 0.0，且必须输出 error 日志（不静默）

    注意：不再测试 pyproject.toml 降级路径，该路径已移除。
    """

    @pytest.mark.unit
    @pytest.mark.p0
    def test_no_coverage_xml_returns_zero(self, tmp_path):
        """无 coverage.xml 时返回 0.0"""
        collector = _make_collector(tmp_path)
        assert collector._read_test_coverage() == 0.0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_no_coverage_xml_emits_error(self, tmp_path, caplog):
        """无 coverage.xml 时应输出 missing_xml error 日志"""
        collector = _make_collector(tmp_path)
        with caplog.at_level(logging.ERROR):
            collector._read_test_coverage()
        msgs = [r.getMessage() for r in caplog.records]
        assert any("missing_xml" in m for m in msgs), f"未找到 missing_xml: {msgs}"

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
#  6. 结构化日志格式校验（可观测性强制约束）
# ═══════════════════════════════════════════════════════════════

class TestStructuredLogFormat:
    """验证日志符合结构化 JSON 规范（含 trace_id/module_name/action）"""

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
                # log_dict 模式下 record.msg 是 dict；传统模式下 getMessage() 返回 JSON 字符串
                if isinstance(record.msg, dict):
                    data = record.msg
                else:
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
