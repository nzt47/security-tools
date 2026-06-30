"""kwarg_scanner 包测试 — 验证核心扫描功能"""

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# 确保能导入包
sys.path.insert(0, str(Path(__file__).parent.parent))

from kwarg_scanner import (
    KwargScanner, ScanConfig, RiskLevel,
    ConflictFinding, FuncSignature,
    scan_file, scan_directory,
    format_text_report, format_json_report,
)


# ════════════════════════════════════════════════════════════
#  测试夹具
# ════════════════════════════════════════════════════════════

BAD_CODE = '''\
"""含 HIGH 风险的代码"""


def emit_log(action, *, trace_id=None, duration_ms=0.0, level="info", **payload):
    """模拟 _emit_structured_log"""
    print(f"[{level}] {action}")


def track_event_bad(event_name, payload=None):
    """HIGH 风险: **payload 未过滤保留键"""
    emit_log(
        f"track.{event_name}",
        trace_id="t1",
        duration_ms=0.0,
        level="info",
        **(payload or {}),
    )
'''

GOOD_CODE = '''\
"""安全代码 — 已过滤保留键"""


def emit_log(action, *, trace_id=None, duration_ms=0.0, level="info", **payload):
    print(f"[{level}] {action}")


def track_event_good(event_name, payload=None):
    """SAFE: 使用 safe_payload 过滤"""
    _RESERVED = {"action", "trace_id", "duration_ms", "level"}
    safe_payload = {k: v for k, v in (payload or {}).items() if k not in _RESERVED}
    emit_log(
        f"track.{event_name}",
        trace_id="t2",
        duration_ms=0.0,
        **safe_payload,
    )
'''

FORWARD_CODE = '''\
"""MEDIUM 风险: **(payload or {}) 模式转发到外部函数"""


def call_api(event_name, payload=None):
    """MEDIUM: or_expr 模式 + 2 个显式 kwarg，外部函数签名未知"""
    import logging
    logging.info(
        "event",
        extra={"event": event_name},
        stacklevel=2,
        **(payload or {}),
    )
'''


@pytest.fixture
def temp_project():
    """创建临时测试项目目录"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        # 创建目录结构
        (tmpdir / "bad").mkdir()
        (tmpdir / "good").mkdir()
        (tmpdir / "forward").mkdir()

        # 写入测试文件
        (tmpdir / "bad" / "module.py").write_text(BAD_CODE, encoding="utf-8")
        (tmpdir / "good" / "module.py").write_text(GOOD_CODE, encoding="utf-8")
        (tmpdir / "forward" / "module.py").write_text(FORWARD_CODE, encoding="utf-8")

        yield tmpdir


# ════════════════════════════════════════════════════════════
#  测试用例
# ════════════════════════════════════════════════════════════

class TestScanFile:
    """测试单文件扫描"""

    def test_scan_bad_code_finds_high(self, tmp_path):
        """扫描含风险的代码 → 应发现 HIGH"""
        f = tmp_path / "bad.py"
        f.write_text(BAD_CODE, encoding="utf-8")
        findings = scan_file(str(f))
        high = [x for x in findings if x.risk_level == "HIGH"]
        assert len(high) >= 1, f"应发现至少 1 处 HIGH，实际: {findings}"
        assert "trace_id" in high[0].conflicting_params
        assert "duration_ms" in high[0].conflicting_params

    def test_scan_good_code_no_high(self, tmp_path):
        """扫描安全代码 → 不应有 HIGH"""
        f = tmp_path / "good.py"
        f.write_text(GOOD_CODE, encoding="utf-8")
        findings = scan_file(str(f))
        high = [x for x in findings if x.risk_level == "HIGH"]
        assert len(high) == 0, f"安全代码不应有 HIGH，但发现: {high}"

    def test_scan_good_code_has_low(self, tmp_path):
        """安全代码的 safe_payload 应标记为 LOW"""
        f = tmp_path / "good.py"
        f.write_text(GOOD_CODE, encoding="utf-8")
        findings = scan_file(str(f))
        lows = [x for x in findings if x.risk_level == "LOW"]
        assert len(lows) >= 1, "safe_payload 应被标记为 LOW"

    def test_scan_forward_code_has_medium(self, tmp_path):
        """转发代码应有 MEDIUM 风险"""
        f = tmp_path / "forward.py"
        f.write_text(FORWARD_CODE, encoding="utf-8")
        findings = scan_file(str(f))
        mediums = [x for x in findings if x.risk_level == "MEDIUM"]
        assert len(mediums) >= 1, f"转发代码应有 MEDIUM，实际: {findings}"


class TestScanDirectory:
    """测试目录扫描"""

    def test_scan_directory_all_files(self, temp_project):
        """扫描目录 → 应覆盖所有文件"""
        findings = scan_directory(str(temp_project))
        assert len(findings) > 0, "应发现风险"

    def test_scan_directory_high_only(self, temp_project):
        """配置 min_risk=HIGH → 只返回 HIGH"""
        config = ScanConfig(min_risk=RiskLevel.HIGH)
        scanner = KwargScanner(config)
        findings = scanner.scan(str(temp_project))
        for f in findings:
            assert f.risk_level == "HIGH"

    def test_scan_directory_excludes(self, temp_project):
        """排除目录 → 应跳过"""
        config = ScanConfig(exclude_dirs={"bad", "good", "forward"})
        scanner = KwargScanner(config)
        findings = scanner.scan(str(temp_project))
        assert len(findings) == 0, "排除所有子目录后应无发现"

    def test_scan_single_file(self, temp_project):
        """扫描单文件路径"""
        file_path = temp_project / "bad" / "module.py"
        config = ScanConfig(min_risk=RiskLevel.HIGH)
        scanner = KwargScanner(config)
        findings = scanner.scan(str(file_path))
        assert len(findings) >= 1, "单文件扫描应发现 HIGH"


class TestConfig:
    """测试配置"""

    def test_default_config(self):
        """默认配置 → min_risk=LOW"""
        config = ScanConfig()
        assert config.min_risk == RiskLevel.LOW

    def test_custom_config(self):
        """自定义配置"""
        config = ScanConfig(
            min_risk=RiskLevel.HIGH,
            exclude_dirs={"custom"},
            filtered_name_prefixes=("my_",),
        )
        assert config.min_risk == RiskLevel.HIGH
        assert "custom" in config.exclude_dirs
        assert "my_" in config.filtered_name_prefixes


class TestFinding:
    """测试 ConflictFinding 数据类"""

    def test_to_dict(self):
        """to_dict 序列化"""
        f = ConflictFinding(
            file="test.py", lineno=10, col=4,
            func_name="foo", explicit_kwargs=["a", "b"],
            spread_expr="kwargs", risk_level="HIGH",
            reason="test", conflicting_params=["a"],
        )
        d = f.to_dict()
        assert d["file"] == "test.py"
        assert d["risk_level"] == "HIGH"
        assert "a" in d["conflicting_params"]

    def test_risk_level_enum(self):
        """RiskLevel 枚举"""
        assert RiskLevel.from_str("HIGH") == RiskLevel.HIGH
        assert RiskLevel.from_str("MEDIUM") == RiskLevel.MEDIUM
        assert RiskLevel.from_str("LOW") == RiskLevel.LOW
        assert RiskLevel.HIGH > RiskLevel.LOW


class TestReporter:
    """测试报告生成"""

    def test_text_report(self):
        """文本报告格式"""
        findings = [
            ConflictFinding(
                file="a.py", lineno=1, col=0,
                func_name="f", explicit_kwargs=["x"],
                spread_expr="kwargs", risk_level="HIGH",
                reason="test", conflicting_params=["x"],
            ),
        ]
        report = format_text_report(findings)
        assert "HIGH" in report
        assert "a.py" in report
        assert "test" in report

    def test_json_report(self):
        """JSON 报告格式"""
        findings = [
            ConflictFinding(
                file="b.py", lineno=5, col=2,
                func_name="g", explicit_kwargs=["y"],
                spread_expr="payload", risk_level="MEDIUM",
                reason="test medium",
            ),
        ]
        report = format_json_report(findings)
        data = json.loads(report)
        assert data["total"] == 1
        assert data["summary"]["MEDIUM"] == 1
        assert data["findings"][0]["file"] == "b.py"

    def test_empty_report(self):
        """空发现的报告"""
        text_report = format_text_report([])
        assert "总发现数: 0" in text_report
        json_report = format_json_report([])
        data = json.loads(json_report)
        assert data["total"] == 0


class TestFilteredVar:
    """测试已过滤变量识别"""

    def test_safe_prefix_recognized(self, tmp_path):
        """safe_ 前缀变量应被识别为 LOW"""
        code = '''\
def emit(action, *, trace_id=None, **kw):
    pass

def foo(payload):
    safe = {k: v for k, v in payload.items() if k not in {"trace_id"}}
    emit("x", trace_id="t", **safe)
'''
        f = tmp_path / "test.py"
        f.write_text(code, encoding="utf-8")
        findings = scan_file(str(f))
        highs = [x for x in findings if x.risk_level == "HIGH"]
        assert len(highs) == 0, "safe_ 前缀变量不应报 HIGH"

    def test_unfiltered_var_high(self, tmp_path):
        """未过滤变量应报 HIGH"""
        code = '''\
def emit(action, *, trace_id=None, **kw):
    pass

def foo(payload):
    emit("x", trace_id="t", **payload)
'''
        f = tmp_path / "test.py"
        f.write_text(code, encoding="utf-8")
        findings = scan_file(str(f))
        highs = [x for x in findings if x.risk_level == "HIGH"]
        assert len(highs) >= 1, "未过滤的 **payload 应报 HIGH"


class TestCLI:
    """测试 CLI 入口"""

    def test_cli_exit_code_clean(self, temp_project):
        """安全代码 → exit 0"""
        from kwarg_scanner.cli import main
        code = main(["--path", str(temp_project / "good"), "--min-risk", "HIGH"])
        assert code == 0

    def test_cli_exit_code_risky(self, temp_project):
        """含风险代码 → exit 1"""
        from kwarg_scanner.cli import main
        code = main(["--path", str(temp_project / "bad"), "--min-risk", "HIGH"])
        assert code == 1

    def test_cli_json_output(self, temp_project, tmp_path):
        """JSON 输出到文件"""
        from kwarg_scanner.cli import main
        output_file = tmp_path / "report.json"
        code = main([
            "--path", str(temp_project / "bad"),
            "--min-risk", "LOW",
            "--format", "json",
            "--output", str(output_file),
        ])
        assert output_file.exists()
        data = json.loads(output_file.read_text(encoding="utf-8"))
        assert data["total"] > 0
