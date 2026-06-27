# -*- coding: utf-8 -*-
"""
test_quality_assess.py 缓存优化单元测试

【测试目标】
验证 TestQualityAssessor 中 assess_boundary_coverage() 与 assess_exception_handling()
方法接受可选 analysis 参数后的缓存共享逻辑，确保 generate_report() 只调用一次
analyze_test_files()，且不传 analysis 时能正确降级到独立扫描。

【覆盖维度】
1. 传入 analysis 参数：缓存命中，不触发 analyze_test_files
2. 不传 analysis 参数：触发独立扫描
3. analysis=None 显式传入：降级到独立扫描
4. 多次调用一致性
5. 空测试目录、文件读取失败、大量文件
6. generate_report 端到端：只扫描一次
7. 性能回归：mock 计数验证

【可观测性约束】
- 边界显性化：测试命名反映业务意图
- 异常处理：所有 mock 隔离文件系统，避免污染真实仓库

【生成日志摘要】
- 生成时间：2026-06-26
- 版本：v1.0.0
- 内容：test_quality_assess.py 缓存共享逻辑单元测试
"""

from __future__ import annotations

import importlib.util
import sys
import threading
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# 加载 scripts/test_quality_assess.py（文件名以 test_ 开头，需用 importlib 显式加载
# 避免被 pytest 误识别为测试模块）
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "test_quality_assess.py"
sys.path.insert(0, str(PROJECT_ROOT))

_spec = importlib.util.spec_from_file_location(
    "test_quality_assess_module", SCRIPT_PATH
)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)

TestQualityAssessor = _module.TestQualityAssessor
QualityLevel = _module.QualityLevel


# ═══════════════════════════════════════════════════════════════
#  1. assess_boundary_coverage 缓存命中
# ═══════════════════════════════════════════════════════════════

class TestBoundaryCoverageCacheHit:
    """assess_boundary_coverage 接收 analysis 参数时的缓存命中行为"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_use_provided_analysis_without_rescan(self, tmp_path):
        """传入 analysis 时应直接使用，不再调用 analyze_test_files"""
        assessor = TestQualityAssessor()
        test_dir = tmp_path / "tests"
        test_dir.mkdir()

        analysis = {
            "test_file_count": 10,
            "total_tests": 50,
            "boundary_coverage_files": 7,
            "exception_coverage_files": 5,
            "boundary_coverage_rate": 0.7,
            "exception_coverage_rate": 0.5,
        }
        with patch.object(
            assessor, "analyze_test_files", return_value=analysis
        ) as mock_analyze:
            dim = assessor.assess_boundary_coverage(test_dir, analysis)
            # 应直接使用传入的 analysis，不调用 analyze_test_files
            assert mock_analyze.call_count == 0

        assert dim.name == "边界条件覆盖"
        assert dim.score == 70.0  # 0.7 * 100

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_return_correct_score_from_analysis(self, tmp_path):
        """应基于 analysis 中的 boundary_coverage_rate 计算分数"""
        assessor = TestQualityAssessor()
        test_dir = tmp_path / "tests"
        test_dir.mkdir()

        # 100% 覆盖
        analysis_full = {
            "test_file_count": 5,
            "total_tests": 10,
            "boundary_coverage_files": 5,
            "exception_coverage_files": 5,
            "boundary_coverage_rate": 1.0,
            "exception_coverage_rate": 1.0,
        }
        dim_full = assessor.assess_boundary_coverage(test_dir, analysis_full)
        assert dim_full.score == 100.0
        assert dim_full.level == QualityLevel.EXCELLENT

        # 0% 覆盖
        analysis_zero = {
            "test_file_count": 5,
            "total_tests": 10,
            "boundary_coverage_files": 0,
            "exception_coverage_files": 0,
            "boundary_coverage_rate": 0.0,
            "exception_coverage_rate": 0.0,
        }
        dim_zero = assessor.assess_boundary_coverage(test_dir, analysis_zero)
        assert dim_zero.score == 0.0
        assert dim_zero.level == QualityLevel.POOR

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_include_correct_details_from_analysis(self, tmp_path):
        """details 应包含 analysis 中的边界覆盖文件数与总文件数"""
        assessor = TestQualityAssessor()
        test_dir = tmp_path / "tests"
        test_dir.mkdir()

        analysis = {
            "test_file_count": 8,
            "total_tests": 20,
            "boundary_coverage_files": 6,
            "exception_coverage_files": 4,
            "boundary_coverage_rate": 0.75,
            "exception_coverage_rate": 0.5,
        }
        dim = assessor.assess_boundary_coverage(test_dir, analysis)
        details_text = " ".join(dim.details)
        assert "6/8" in details_text  # boundary_coverage_files / test_file_count
        assert "75.0%" in details_text  # 覆盖率
        assert "8" in details_text  # 总文件数


# ═══════════════════════════════════════════════════════════════
#  2. assess_boundary_coverage 缓存未命中（降级）
# ═══════════════════════════════════════════════════════════════

class TestBoundaryCoverageCacheMiss:
    """不传 analysis 或传 None 时应降级到独立扫描"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_call_analyze_when_analysis_not_provided(self, tmp_path):
        """不传 analysis 时应调用 analyze_test_files 独立扫描"""
        assessor = TestQualityAssessor()
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        (test_dir / "test_a.py").write_text(
            "def test_a_empty(): pass\n", encoding="utf-8"
        )

        with patch.object(
            assessor,
            "analyze_test_files",
            wraps=assessor.analyze_test_files,
        ) as mock_analyze:
            dim = assessor.assess_boundary_coverage(test_dir)
            # 不传 analysis 应触发独立扫描
            assert mock_analyze.call_count == 1
            mock_analyze.assert_called_once_with(test_dir)

        assert dim.name == "边界条件覆盖"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_call_analyze_when_analysis_explicitly_none(self, tmp_path):
        """显式传 analysis=None 也应触发独立扫描"""
        assessor = TestQualityAssessor()
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        (test_dir / "test_a.py").write_text(
            "def test_a(): pass\n", encoding="utf-8"
        )

        with patch.object(
            assessor,
            "analyze_test_files",
            wraps=assessor.analyze_test_files,
        ) as mock_analyze:
            assessor.assess_boundary_coverage(test_dir, analysis=None)
            assert mock_analyze.call_count == 1


# ═══════════════════════════════════════════════════════════════
#  3. assess_exception_handling 缓存命中/未命中
# ═══════════════════════════════════════════════════════════════

class TestExceptionHandlingCache:
    """assess_exception_handling 缓存共享逻辑"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_use_provided_analysis_without_rescan(self, tmp_path):
        """传入 analysis 时应直接使用，不再调用 analyze_test_files"""
        assessor = TestQualityAssessor()
        test_dir = tmp_path / "tests"
        test_dir.mkdir()

        analysis = {
            "test_file_count": 10,
            "total_tests": 50,
            "boundary_coverage_files": 7,
            "exception_coverage_files": 5,
            "boundary_coverage_rate": 0.7,
            "exception_coverage_rate": 0.5,
        }
        with patch.object(
            assessor, "analyze_test_files", return_value=analysis
        ) as mock_analyze:
            dim = assessor.assess_exception_handling(test_dir, analysis)
            assert mock_analyze.call_count == 0

        assert dim.name == "异常处理覆盖"
        assert dim.score == 50.0  # 0.5 * 100

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_call_analyze_when_analysis_not_provided(self, tmp_path):
        """不传 analysis 时应调用 analyze_test_files 独立扫描"""
        assessor = TestQualityAssessor()
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        (test_dir / "test_a.py").write_text(
            "def test_a(): pass\n", encoding="utf-8"
        )

        with patch.object(
            assessor,
            "analyze_test_files",
            wraps=assessor.analyze_test_files,
        ) as mock_analyze:
            assessor.assess_exception_handling(test_dir)
            assert mock_analyze.call_count == 1
            mock_analyze.assert_called_once_with(test_dir)

    @pytest.mark.unit
    @pytest.mark.p1
    def test_exception_handling_should_return_correct_score(self, tmp_path):
        """应基于 exception_coverage_rate 计算分数"""
        assessor = TestQualityAssessor()
        test_dir = tmp_path / "tests"
        test_dir.mkdir()

        # 90% 覆盖
        analysis = {
            "test_file_count": 10,
            "total_tests": 30,
            "boundary_coverage_files": 5,
            "exception_coverage_files": 9,
            "boundary_coverage_rate": 0.5,
            "exception_coverage_rate": 0.9,
        }
        dim = assessor.assess_exception_handling(test_dir, analysis)
        assert dim.score == 90.0
        assert dim.level == QualityLevel.EXCELLENT


# ═══════════════════════════════════════════════════════════════
#  4. 共享 analysis 一致性
# ═══════════════════════════════════════════════════════════════

class TestSharedAnalysisConsistency:
    """两个 assess_* 方法共享同一 analysis 应产出一致结果"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_two_methods_share_same_analysis_should_be_consistent(self, tmp_path):
        """两个方法基于同一 analysis 应产出互不干扰、且与单独调用一致的结果"""
        assessor = TestQualityAssessor()
        test_dir = tmp_path / "tests"
        test_dir.mkdir()

        analysis = {
            "test_file_count": 4,
            "total_tests": 8,
            "boundary_coverage_files": 3,
            "exception_coverage_files": 2,
            "boundary_coverage_rate": 0.75,
            "exception_coverage_rate": 0.5,
        }
        # 调用两次（共享同一 analysis）
        dim_b1 = assessor.assess_boundary_coverage(test_dir, analysis)
        dim_e1 = assessor.assess_exception_handling(test_dir, analysis)
        dim_b2 = assessor.assess_boundary_coverage(test_dir, analysis)
        dim_e2 = assessor.assess_exception_handling(test_dir, analysis)

        # 多次调用结果一致
        assert dim_b1.score == dim_b2.score == 75.0
        assert dim_e1.score == dim_e2.score == 50.0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_shared_analysis_does_not_mutate_input(self, tmp_path):
        """共享 analysis 不应修改传入的字典"""
        assessor = TestQualityAssessor()
        test_dir = tmp_path / "tests"
        test_dir.mkdir()

        analysis = {
            "test_file_count": 4,
            "total_tests": 8,
            "boundary_coverage_files": 3,
            "exception_coverage_files": 2,
            "boundary_coverage_rate": 0.75,
            "exception_coverage_rate": 0.5,
        }
        original = dict(analysis)
        assessor.assess_boundary_coverage(test_dir, analysis)
        assessor.assess_exception_handling(test_dir, analysis)
        assert analysis == original


# ═══════════════════════════════════════════════════════════════
#  5. 空测试目录与异常场景
# ═══════════════════════════════════════════════════════════════

class TestEmptyAndErrorScenarios:
    """空目录、文件读取失败等边界场景"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_empty_test_dir_should_yield_zero_coverage(self, tmp_path):
        """空测试目录应产出 0% 覆盖率"""
        assessor = TestQualityAssessor()
        test_dir = tmp_path / "tests"
        test_dir.mkdir()  # 空目录

        # 不传 analysis，触发独立扫描
        dim_b = assessor.assess_boundary_coverage(test_dir)
        dim_e = assessor.assess_exception_handling(test_dir)
        assert dim_b.score == 0.0
        assert dim_e.score == 0.0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_missing_test_dir_should_not_raise(self, tmp_path):
        """测试目录不存在时不应抛出异常"""
        assessor = TestQualityAssessor()
        test_dir = tmp_path / "nonexistent_tests"
        # 不传 analysis 触发独立扫描；rglob 在不存在的目录上返回空
        dim_b = assessor.assess_boundary_coverage(test_dir)
        assert dim_b.score == 0.0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_file_read_failure_should_be_skipped(self, tmp_path):
        """文件读取失败时应被跳过（analyze_test_files 内部 try/except）"""
        assessor = TestQualityAssessor()
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        good_file = test_dir / "test_good.py"
        good_file.write_text(
            # 注：BOUNDARY_PATTERNS 使用 \b 词边界，下划线是词字符，
            # 因此需用空格分隔的关键词（如 None / empty）
            "def test_case():\n"
            "    # 测试 None 与 empty 输入\n"
            "    pass\n",
            encoding="utf-8",
        )
        bad_file = test_dir / "test_bad.py"
        bad_file.write_text("def test_bad(): pass\n", encoding="utf-8")

        original_open = open

        def mock_open(path, *args, **kwargs):
            if str(path).endswith("test_bad.py"):
                raise OSError("模拟读取失败")
            return original_open(path, *args, **kwargs)

        with patch("builtins.open", mock_open):
            # analyze_test_files 内部 try/except 应吞掉异常
            analysis = assessor.analyze_test_files(test_dir)
            # 修复后（Bug 2）：test_file_count 在 try 块内递增，仅计成功读取的文件
            # 故 bad_file 读取失败不计入，test_file_count=1（只有 good_file）
            assert analysis["test_file_count"] == 1
            # boundary_coverage_files 只统计成功读取且匹配的文件 → 1（good 含 None/empty）
            assert analysis["boundary_coverage_files"] == 1

    @pytest.mark.unit
    @pytest.mark.p1
    def test_many_files_should_be_handled(self, tmp_path):
        """大量测试文件应正常分析"""
        assessor = TestQualityAssessor()
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        # 注：BOUNDARY_PATTERNS 使用 \b 词边界，下划线是词字符，
        # 因此 test_boundary_empty 会被视为一个完整词，无法匹配。
        # 需使用带空格分隔的关键词（如 None / empty / boundary）。
        for i in range(30):
            (test_dir / f"test_{i:03d}.py").write_text(
                "def test_case():\n"
                "    # 测试 boundary 场景：None 与 empty 输入\n"
                "    pass\n",
                encoding="utf-8",
            )

        analysis = assessor.analyze_test_files(test_dir)
        assert analysis["test_file_count"] == 30
        assert analysis["boundary_coverage_files"] == 30


# ═══════════════════════════════════════════════════════════════
#  6. generate_report 端到端：analyze_test_files 只调用一次
# ═══════════════════════════════════════════════════════════════

class TestGenerateReportCacheSharing:
    """generate_report 应只调用一次 analyze_test_files"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_generate_report_should_call_analyze_once(self, tmp_path, monkeypatch):
        """generate_report 应只调用一次 analyze_test_files"""
        # 切换工作目录到 tmp_path，使 Path('tests') 解析到 tmp_path/tests
        monkeypatch.chdir(tmp_path)
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        (test_dir / "test_a.py").write_text(
            "def test_empty(): pass\n", encoding="utf-8"
        )
        # 占位 business_metrics.py 避免 _check_metrics_coverage 失败
        (tmp_path / "agent").mkdir()
        (tmp_path / "agent" / "monitoring").mkdir()
        (tmp_path / "agent" / "monitoring" / "business_metrics.py").write_text(
            "# placeholder\n", encoding="utf-8"
        )

        assessor = TestQualityAssessor()
        with patch.object(
            assessor,
            "analyze_test_files",
            wraps=assessor.analyze_test_files,
        ) as mock_analyze:
            assessor.generate_report(coverage_rate=80.0)

        # 整个 generate_report 流程中 analyze_test_files 只应被调用 1 次
        assert mock_analyze.call_count == 1, (
            f"generate_report 应只调用 1 次 analyze_test_files，"
            f"实际 {mock_analyze.call_count} 次"
        )

    @pytest.mark.unit
    @pytest.mark.p0
    def test_generate_report_should_share_analysis_between_two_methods(
        self, tmp_path, monkeypatch
    ):
        """generate_report 中 assess_boundary_coverage 与 assess_exception_handling
        应共享同一 analysis 实例"""
        monkeypatch.chdir(tmp_path)
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        (test_dir / "test_a.py").write_text(
            "def test_empty(): pass\n", encoding="utf-8"
        )
        (tmp_path / "agent").mkdir()
        (tmp_path / "agent" / "monitoring").mkdir()
        (tmp_path / "agent" / "monitoring" / "business_metrics.py").write_text(
            "# placeholder\n", encoding="utf-8"
        )

        assessor = TestQualityAssessor()
        captured_analyses = []
        original_analyze = assessor.analyze_test_files

        def spy_analyze(test_dir):
            result = original_analyze(test_dir)
            captured_analyses.append(result)
            return result

        with patch.object(assessor, "analyze_test_files", side_effect=spy_analyze):
            assessor.generate_report(coverage_rate=50.0)

        # 只调用一次
        assert len(captured_analyses) == 1
        # 同一 analysis 对象在两个方法间共享
        first_analysis = captured_analyses[0]
        assert first_analysis["test_file_count"] == 1


# ═══════════════════════════════════════════════════════════════
#  7. 性能回归：mock 计数验证
# ═══════════════════════════════════════════════════════════════

class TestCachePerformance:
    """性能回归：缓存生效后调用次数减少"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_calling_both_methods_with_shared_analysis_calls_zero_scans(self, tmp_path):
        """两个方法都传入 analysis 时应零次扫描"""
        assessor = TestQualityAssessor()
        test_dir = tmp_path / "tests"
        test_dir.mkdir()

        analysis = {
            "test_file_count": 10,
            "total_tests": 20,
            "boundary_coverage_files": 8,
            "exception_coverage_files": 6,
            "boundary_coverage_rate": 0.8,
            "exception_coverage_rate": 0.6,
        }
        with patch.object(
            assessor, "analyze_test_files"
        ) as mock_analyze:
            assessor.assess_boundary_coverage(test_dir, analysis)
            assessor.assess_exception_handling(test_dir, analysis)
            # 共享 analysis，零次扫描
            assert mock_analyze.call_count == 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_calling_both_methods_without_analysis_calls_two_scans(self, tmp_path):
        """两个方法都不传 analysis 时应触发 2 次扫描（独立模式）"""
        assessor = TestQualityAssessor()
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        (test_dir / "test_a.py").write_text(
            "def test_empty(): pass\n", encoding="utf-8"
        )

        with patch.object(
            assessor,
            "analyze_test_files",
            wraps=assessor.analyze_test_files,
        ) as mock_analyze:
            assessor.assess_boundary_coverage(test_dir)
            assessor.assess_exception_handling(test_dir)
            # 不共享时各扫描一次
            assert mock_analyze.call_count == 2

    @pytest.mark.unit
    @pytest.mark.p1
    def test_generate_report_calls_one_scan_vs_two_without_sharing(
        self, tmp_path, monkeypatch
    ):
        """generate_report 应只调用 1 次 analyze_test_files，
        相比独立调用两个方法（2 次）减半"""
        monkeypatch.chdir(tmp_path)
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        (test_dir / "test_a.py").write_text(
            "def test_empty(): pass\n", encoding="utf-8"
        )
        (tmp_path / "agent").mkdir()
        (tmp_path / "agent" / "monitoring").mkdir()
        (tmp_path / "agent" / "monitoring" / "business_metrics.py").write_text(
            "# placeholder\n", encoding="utf-8"
        )

        # 场景 1：generate_report（共享）
        assessor1 = TestQualityAssessor()
        with patch.object(
            assessor1,
            "analyze_test_files",
            wraps=assessor1.analyze_test_files,
        ) as mock1:
            assessor1.generate_report(coverage_rate=80.0)
        shared_calls = mock1.call_count

        # 场景 2：独立调用两个方法
        assessor2 = TestQualityAssessor()
        with patch.object(
            assessor2,
            "analyze_test_files",
            wraps=assessor2.analyze_test_files,
        ) as mock2:
            assessor2.assess_boundary_coverage(test_dir)
            assessor2.assess_exception_handling(test_dir)
        independent_calls = mock2.call_count

        # 共享模式应比独立模式少调用一次
        assert shared_calls == 1
        assert independent_calls == 2
        assert shared_calls < independent_calls


# ═══════════════════════════════════════════════════════════════
#  8. 并发安全（基础测试）
# ═══════════════════════════════════════════════════════════════

class TestAnalysisConcurrency:
    """analysis 共享的线程安全性基础测试"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_concurrent_calls_with_shared_analysis_should_be_safe(self, tmp_path):
        """多线程并发使用同一 analysis 应安全（只读共享）"""
        assessor = TestQualityAssessor()
        test_dir = tmp_path / "tests"
        test_dir.mkdir()

        analysis = {
            "test_file_count": 10,
            "total_tests": 30,
            "boundary_coverage_files": 7,
            "exception_coverage_files": 5,
            "boundary_coverage_rate": 0.7,
            "exception_coverage_rate": 0.5,
        }
        results = []
        errors = []
        barrier = threading.Barrier(4)

        def worker(method_name):
            try:
                barrier.wait()
                if method_name == "boundary":
                    dim = assessor.assess_boundary_coverage(test_dir, analysis)
                else:
                    dim = assessor.assess_exception_handling(test_dir, analysis)
                results.append(dim.score)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=worker, args=("boundary",)),
            threading.Thread(target=worker, args=("boundary",)),
            threading.Thread(target=worker, args=("exception",)),
            threading.Thread(target=worker, args=("exception",)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"并发调用失败: {errors}"
        # boundary 两次都应是 70，exception 两次都应是 50
        assert sorted(results) == [50.0, 50.0, 70.0, 70.0]


# ═══════════════════════════════════════════════════════════════
#  9. P0 补充：analyze_test_files 计数一致性 / 非法值 / 多模式匹配
# ═══════════════════════════════════════════════════════════════

class TestAnalyzeTestFilesP0Boundaries:
    """analyze_test_files 的 P0 级边界条件覆盖

    覆盖缺口（来自 test_coverage_gap_analysis.md 2.3 节）：
    - 计数一致性（159行 vs 167行，已修复：test_file_count 移入 try 块内）
    - 非法 boundary_coverage_rate 值（289行）
    - 多模式匹配 break 语义（167-168行）
    """

    @pytest.mark.unit
    @pytest.mark.p0
    def test_analyze_test_files_count_inconsistency_on_read_failure(
        self, tmp_path
    ):
        """P0-6: 文件读取失败时 test_file_count 不应包含失败文件（修复后验证）

        修复前：test_file_count 在 try 块外递增，含失败文件，导致覆盖率被压低
        修复后：test_file_count 在 try 块内递增，仅计成功读取的文件

        构造 2 个文件，1 个读取失败：
        - 修复前：test_file_count=2，boundary_coverage_files<=1 → 覆盖率偏低
        - 修复后：test_file_count=1，boundary_coverage_files<=1 → 覆盖率准确
        """
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        # 文件 1：正常文件，含边界关键词
        (test_dir / "test_normal.py").write_text(
            "def test_with_none():\n"
            "    assert None is None\n",
            encoding="utf-8",
        )
        # 文件 2：将模拟读取失败
        (test_dir / "test_fail.py").write_text(
            "def test_fail():\n    pass\n", encoding="utf-8"
        )

        assessor = TestQualityAssessor()

        # mock open 对 test_fail.py 抛 OSError
        import builtins
        original_open = builtins.open

        def mock_open(file, *args, **kwargs):
            if "test_fail.py" in str(file):
                raise OSError("模拟读取失败")
            return original_open(file, *args, **kwargs)

        with patch("builtins.open", side_effect=mock_open):
            result = assessor.analyze_test_files(test_dir)

        # 修复后：test_file_count 只计成功读取的文件（=1，不含 test_fail.py）
        assert result["test_file_count"] == 1, (
            f"修复后 test_file_count 应为 1（仅成功读取的文件），"
            f"实际 {result['test_file_count']}。"
            f"若为 2 说明修复未生效（test_file_count 仍含失败文件）"
        )
        # boundary_coverage_files 不含失败文件
        assert result["boundary_coverage_files"] <= 1
        # 覆盖率应基于 1 个文件计算，不被人为压低
        assert result["boundary_coverage_rate"] <= 1.0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_assess_boundary_coverage_with_illegal_rate_negative(self, tmp_path):
        """P0-7: 传入 boundary_coverage_rate=-0.5（非法负值），验证处理行为

        当前实现不校验非法值，直接计算 score = -0.5 * 100 = -50.0
        本测试明确记录此行为，便于后续评估是否需要增加校验
        """
        assessor = TestQualityAssessor()
        test_dir = tmp_path / "tests"
        test_dir.mkdir()

        # 构造非法 analysis：boundary_coverage_rate 为负数
        illegal_analysis = {
            "test_file_count": 10,
            "total_tests": 30,
            "boundary_coverage_files": -5,  # 非法负值
            "exception_coverage_files": 5,
            "boundary_coverage_rate": -0.5,  # 非法负值
            "exception_coverage_rate": 0.5,
        }

        dim = assessor.assess_boundary_coverage(test_dir, illegal_analysis)

        # 当前实现不校验，直接计算：score = -0.5 * 100 = -50.0
        # 明确记录此行为：非法值会产生负分，建议后续增加校验
        assert dim.score == -50.0, (
            f"非法 boundary_coverage_rate=-0.5 应产生 score=-50.0，"
            f"实际 {dim.score}。当前实现不校验非法值"
        )

    @pytest.mark.unit
    @pytest.mark.p0
    def test_analyze_test_files_multiple_boundary_patterns_match_once(
        self, tmp_path
    ):
        """P0-8: 文件同时匹配多个 BOUNDARY_PATTERNS，boundary_count 只递增 1 次

        覆盖行 167-168: 多模式匹配时 break 语义，匹配多个也只计一次
        """
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        # 单文件同时匹配 4 个 BOUNDARY_PATTERNS：
        # - None/empty/zero（模式 1）
        # - large/small（模式 2）
        # - invalid/valid（模式 3）
        # - overflow/timeout（模式 4）
        (test_dir / "test_multi_boundary.py").write_text(
            "def test_none_and_empty():\n"
            "    assert None is None\n"
            "    assert [] == []\n"
            "def test_large_small():\n"
            "    assert len([1]*100) > 0\n"
            "def test_invalid_valid():\n"
            "    assert True\n"
            "def test_overflow_timeout():\n"
            "    pass\n",
            encoding="utf-8",
        )

        assessor = TestQualityAssessor()
        result = assessor.analyze_test_files(test_dir)

        # 即使匹配 4 个模式，break 后 boundary_coverage_files 只计 1
        assert result["boundary_coverage_files"] == 1, (
            f"多模式匹配 break 后 boundary_coverage_files 应为 1，"
            f"实际 {result['boundary_coverage_files']}"
        )
        assert result["test_file_count"] == 1
        assert result["boundary_coverage_rate"] == 1.0


# ═══════════════════════════════════════════════════════════════
#  10. P1 补充：空文件/纯注释/失败计数/不一致边界/目录缺失/空 analysis
# ═══════════════════════════════════════════════════════════════

class TestAnalyzeTestFilesEmptyAndCommentsP1:
    """analyze_test_files 空文件与纯注释的 P1 级边界条件覆盖

    覆盖缺口（来自 test_coverage_gap_analysis.md 2.3 节 P1）：
    - 空文件（0 字节）处理
    - 只含注释不含 def test_ 的文件
    """

    @pytest.mark.unit
    @pytest.mark.p1
    def test_analyze_test_files_empty_file_zero_bytes(self, tmp_path):
        """P1-7: 空文件（0 字节）应被正常处理，不计入 total_tests

        场景：test_empty.py 是 0 字节文件，read() 返回空字符串，
        re.findall 在空字符串上返回空列表。

        预期：
        - 不抛异常
        - test_file_count = 1（空文件仍被计入文件数）
        - total_tests = 0（空文件无 def test_）
        - boundary_coverage_files = 0（空文件不匹配任何模式）
        """
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        # 创建 0 字节空文件
        empty_file = test_dir / "test_empty.py"
        empty_file.write_text("", encoding="utf-8")
        # 验证确实是 0 字节
        assert empty_file.stat().st_size == 0

        assessor = TestQualityAssessor()
        result = assessor.analyze_test_files(test_dir)

        # 空文件应被正常处理
        assert result["test_file_count"] == 1, (
            f"空文件应计入 test_file_count，实际 {result['test_file_count']}"
        )
        assert result["total_tests"] == 0, (
            f"空文件无 def test_，total_tests 应为 0，"
            f"实际 {result['total_tests']}"
        )
        assert result["boundary_coverage_files"] == 0, (
            f"空文件不匹配任何 BOUNDARY_PATTERNS，"
            f"boundary_coverage_files 应为 0，"
            f"实际 {result['boundary_coverage_files']}"
        )

    @pytest.mark.unit
    @pytest.mark.p1
    def test_analyze_test_files_comments_only_no_test_functions(self, tmp_path):
        """P1-8: 只含注释不含 def test_ 的文件处理

        场景：test_comments.py 只含注释行，不含任何 def test_ 定义，
        但注释中可能含 boundary 关键词（如 None/empty）。

        预期：
        - test_file_count = 1（文件被读取成功）
        - total_tests = 0（无 def test_ 定义）
        - boundary_coverage_files 取决于注释是否含边界关键词
        """
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        # 只含注释的文件，注释中含边界关键词
        (test_dir / "test_comments.py").write_text(
            "# This file has no test functions\n"
            "# It only contains comments about None and empty\n"
            "# boundary cases are documented here\n",
            encoding="utf-8",
        )

        assessor = TestQualityAssessor()
        result = assessor.analyze_test_files(test_dir)

        # 文件被成功读取
        assert result["test_file_count"] == 1, (
            f"纯注释文件应计入 test_file_count，"
            f"实际 {result['test_file_count']}"
        )
        # 无 def test_ 定义
        assert result["total_tests"] == 0, (
            f"纯注释文件无 def test_，total_tests 应为 0，"
            f"实际 {result['total_tests']}"
        )
        # 注释含 None/empty/boundary 关键词，应匹配 BOUNDARY_PATTERNS
        # 注：BOUNDARY_PATTERNS 对整个 content 匹配，不区分注释与代码
        assert result["boundary_coverage_files"] == 1, (
            f"注释含边界关键词应匹配 BOUNDARY_PATTERNS，"
            f"boundary_coverage_files 应为 1，"
            f"实际 {result['boundary_coverage_files']}"
        )


class TestAnalyzeTestFilesTotalTestsOnFailureP1:
    """文件读取失败时 total_tests 不递增的 P1 级边界条件覆盖"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_analyze_test_files_total_tests_not_incremented_on_failure(
        self, tmp_path
    ):
        """P1-9: 文件读取失败时 total_tests 不应递增（修复后验证）

        场景：构造 2 个文件：
        - test_normal.py：正常文件，含 5 个 def test_ 定义
        - test_fail.py：将模拟读取失败（可能含 def test_，但因读取失败不计入）

        修复后：total_tests += 在 try 块内，失败文件不计入

        预期：
        - total_tests = 5（只计正常文件的 def test_ 数）
        - test_file_count = 1（只计成功读取的文件）
        """
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        # 正常文件：含 5 个 def test_ 定义
        (test_dir / "test_normal.py").write_text(
            "def test_case_1(): pass\n"
            "def test_case_2(): pass\n"
            "def test_case_3(): pass\n"
            "def test_case_4(): pass\n"
            "def test_case_5(): pass\n",
            encoding="utf-8",
        )
        # 失败文件：含 3 个 def test_，但会模拟读取失败
        (test_dir / "test_fail.py").write_text(
            "def test_fail_1(): pass\n"
            "def test_fail_2(): pass\n"
            "def test_fail_3(): pass\n",
            encoding="utf-8",
        )

        assessor = TestQualityAssessor()

        # mock open 对 test_fail.py 抛 OSError
        import builtins
        original_open = builtins.open

        def mock_open(file, *args, **kwargs):
            if "test_fail.py" in str(file):
                raise OSError("模拟读取失败")
            return original_open(file, *args, **kwargs)

        with patch("builtins.open", side_effect=mock_open):
            result = assessor.analyze_test_files(test_dir)

        # 修复后：total_tests 只计成功读取文件的 def test_ 数
        # test_normal.py 有 5 个 def test_，test_fail.py 读取失败不计入
        assert result["total_tests"] == 5, (
            f"修复后 total_tests 应为 5（只计 test_normal.py 的 def test_），"
            f"实际 {result['total_tests']}。"
            f"若为 8 说明失败文件的 def test_ 被错误计入"
        )
        # test_file_count 也只计成功读取的文件
        assert result["test_file_count"] == 1, (
            f"test_file_count 应为 1（只计成功读取的文件），"
            f"实际 {result['test_file_count']}"
        )


class TestAssessBoundaryCoverageInconsistentBoundaryP1:
    """boundary_files > test_file_count 不一致边界的 P1 级覆盖"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_assess_boundary_coverage_boundary_files_greater_than_total(
        self, tmp_path
    ):
        """P1-10: boundary_coverage_files > test_file_count 时不一致边界处理

        场景：构造非法 analysis：
        - test_file_count = 3
        - boundary_coverage_files = 5（大于文件总数，数据不一致）

        当前实现不校验一致性，直接用 boundary_coverage_rate 计算 score。

        预期：
        - 不抛异常
        - score = boundary_coverage_rate * 100
        - 明确记录此不一致行为，便于后续评估是否需要增加校验
        """
        assessor = TestQualityAssessor()
        test_dir = tmp_path / "tests"
        test_dir.mkdir()

        # 构造不一致 analysis：boundary_files > test_file_count
        inconsistent_analysis = {
            "test_file_count": 3,
            "total_tests": 10,
            "boundary_coverage_files": 5,  # 大于 test_file_count（不一致）
            "exception_coverage_files": 2,
            "boundary_coverage_rate": 5 / 3,  # 1.666...（大于 1.0，非法）
            "exception_coverage_rate": 2 / 3,
        }

        dim = assessor.assess_boundary_coverage(test_dir, inconsistent_analysis)

        # 当前实现不校验一致性，直接计算 score
        # score = boundary_coverage_rate * 100 = 5/3 * 100 = 166.666...
        # 注意：assess_boundary_coverage 中 score 未被 round
        expected_score = 5 / 3 * 100
        assert dim.score == expected_score, (
            f"不一致 analysis 应直接计算 score={expected_score}，"
            f"实际 {dim.score}。当前实现不校验 boundary_files > test_file_count"
        )
        # details 应反映不一致的数据
        details_text = " ".join(dim.details)
        assert "5/3" in details_text, (
            f"details 应包含 '5/3'（boundary_files/test_file_count），"
            f"实际 {details_text}"
        )


class TestGenerateReportMissingAndEmptyTestsP1:
    """generate_report 在 tests 目录缺失/空 analysis 时的 P1 级边界覆盖"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_generate_report_tests_dir_not_exists(self, tmp_path, monkeypatch):
        """P1-11: tests 目录不存在时 generate_report 应优雅降级

        场景：工作目录下无 tests 目录，generate_report 调用
        analyze_test_files(Path('tests'))，rglob 在不存在目录上返回空。

        预期：
        - 不抛异常
        - 生成报告包含 test_file_count=0 的维度
        - 边界/异常覆盖率均为 0
        """
        # 切换到无 tests 目录的临时目录
        monkeypatch.chdir(tmp_path)
        # 确认 tests 目录不存在
        assert not (tmp_path / "tests").exists()
        # 占位 business_metrics.py 避免 _check_metrics_coverage 失败
        (tmp_path / "agent").mkdir()
        (tmp_path / "agent" / "monitoring").mkdir()
        (tmp_path / "agent" / "monitoring" / "business_metrics.py").write_text(
            "# placeholder\n", encoding="utf-8"
        )

        assessor = TestQualityAssessor()
        # 不应抛异常
        report = assessor.generate_report(coverage_rate=0.0)

        # 报告应正常生成
        assert "summary" in report
        assert "dimensions" in report
        # 边界/异常覆盖率维度应为 0（无测试文件）
        boundary_dim = next(
            d for d in report["dimensions"] if d["name"] == "边界条件覆盖"
        )
        assert boundary_dim["score"] == 0.0, (
            f"tests 目录不存在时边界覆盖率应为 0.0，"
            f"实际 {boundary_dim['score']}"
        )
        # details 应反映 0 文件
        details_text = " ".join(boundary_dim["details"])
        assert "0/0" in details_text or "0" in details_text

    @pytest.mark.unit
    @pytest.mark.p1
    def test_generate_report_empty_tests_analysis(self, tmp_path, monkeypatch):
        """P1-12: tests_analysis 为空（test_file_count=0）时的边界处理

        场景：tests 目录存在但为空，analyze_test_files 返回
        test_file_count=0 的 analysis，generate_report 应处理除零情况。

        预期：
        - 不抛除零异常
        - boundary_coverage_rate=0 时 score=0.0
        - 分析结果中 0/0 由 `if test_file_count > 0 else 0` 保护
        """
        monkeypatch.chdir(tmp_path)
        test_dir = tmp_path / "tests"
        test_dir.mkdir()  # 空目录
        (tmp_path / "agent").mkdir()
        (tmp_path / "agent" / "monitoring").mkdir()
        (tmp_path / "agent" / "monitoring" / "business_metrics.py").write_text(
            "# placeholder\n", encoding="utf-8"
        )

        assessor = TestQualityAssessor()
        # 先验证 analyze_test_files 对空目录的处理
        analysis = assessor.analyze_test_files(test_dir)
        assert analysis["test_file_count"] == 0
        assert analysis["boundary_coverage_rate"] == 0  # 除零保护
        assert analysis["exception_coverage_rate"] == 0

        # generate_report 应正常处理空 analysis
        report = assessor.generate_report(coverage_rate=0.0)

        # 边界覆盖率维度应为 0.0
        boundary_dim = next(
            d for d in report["dimensions"] if d["name"] == "边界条件覆盖"
        )
        assert boundary_dim["score"] == 0.0
        # 注意：asdict() 保留 Enum 对象，不转换为 .value
        # 因此 boundary_dim["level"] 是 QualityLevel.POOR 而非 "poor"
        assert boundary_dim["level"] == QualityLevel.POOR

        # 异常处理覆盖率维度也应为 0.0
        exception_dim = next(
            d for d in report["dimensions"] if d["name"] == "异常处理覆盖"
        )
        assert exception_dim["score"] == 0.0
        assert exception_dim["level"] == QualityLevel.POOR
