"""
M3 里程碑验收测试 —— 硬编码边界值基线治理

验证 scripts/check_hardcoded_boundaries.py 扫描器：
  1. 能识别 timeout / retry / capacity 三类硬编码边界值（样例驱动）
  2. 基线文件结构有效（docs/observability/hardcoded_boundary_baseline_report.json）
  3. 样例扫描结果可关联到被扫文件（防误扫/漏扫）

M3 实施后（基线更新至当前扫描结果），CI「硬编码边界值扫描」应转绿。
"""
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import check_hardcoded_boundaries as chb  # noqa: E402

BASELINE_FILE = REPO_ROOT / "docs" / "observability" / "hardcoded_boundary_baseline_report.json"

SAMPLE_PY = """\
import sqlite3
import concurrent.futures

def connect():
    # call_arg：参数名 timeout + 数字字面量
    return sqlite3.connect("app.db", timeout=5.0)

def fetch():
    # call_arg：参数名 max_retries + 数字字面量
    return _http_get(url="http://x", max_retries=3)

def safe_execute(timeout=30.0):  # default_arg：函数签名默认参数
    return None

def worker():
    # call_arg：参数名 max_workers + 数字字面量
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        pass
"""


def _scan(tmp_path) -> dict:
    sample = tmp_path / "sample_module.py"
    sample.write_text(SAMPLE_PY, encoding="utf-8")
    return chb.analyze_directory(str(tmp_path))


def test_scanner_detects_timeout(tmp_path):
    """扫描器必须识别硬编码 timeout（如 sqlite3.connect(timeout=...)）"""
    report = _scan(tmp_path)
    timeouts = [f for f in report["details"] if f["category"] == "timeout" and f["risk"] == "high"]
    assert timeouts, "扫描器未识别硬编码 timeout"


def test_scanner_detects_capacity(tmp_path):
    """扫描器必须识别硬编码容量限制（如 ThreadPoolExecutor(max_workers=...)）"""
    report = _scan(tmp_path)
    caps = [f for f in report["details"] if f["category"] == "capacity" and f["risk"] == "high"]
    assert caps, "扫描器未识别硬编码 capacity"


def test_scanner_detects_retry(tmp_path):
    """扫描器必须识别硬编码重试次数（如 range(3) 循环）"""
    report = _scan(tmp_path)
    retries = [f for f in report["details"] if f["category"] == "retry" and f["risk"] == "high"]
    assert retries, "扫描器未识别硬编码 retry"


def test_baseline_file_valid():
    """基线文件结构有效：high_risk 为 int 且 configured_modules 非空"""
    data = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
    assert isinstance(data.get("high_risk"), int) and data["high_risk"] > 0
    assert isinstance(data.get("configured_modules"), list) and data["configured_modules"]


def test_sample_findings_reference_sample_file(tmp_path):
    """扫描发现必须关联到被扫文件（防止误扫其他目录或漏扫样例）"""
    report = _scan(tmp_path)
    names = {f["file"] for f in report["details"]}
    assert any("sample_module.py" in name for name in names), "扫描结果未关联样例文件"
