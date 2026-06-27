# -*- coding: utf-8 -*-
"""
visibility_report.py 缓存优化单元测试

【测试目标】
验证 MetricCollector 中新增的 _scan_agent_files() 方法与 _file_content_cache 缓存字段
的正确性与边界条件处理，确保缓存命中、未命中、异常降级等场景行为符合预期。

【覆盖维度】
1. 缓存命中：第二次调用直接返回缓存，不重复扫描
2. 缓存未命中：首次调用扫描 agent/ 下所有 .py 文件
3. agent 目录不存在：返回空字典且缓存仍被填充（空字典）
4. 文件读取失败（OSError/UnicodeDecodeError）：失败文件不入缓存
5. 空目录、大量文件、多次调用一致性
6. 三个共享方法（_calc_structured_log_coverage / _count_health_endpoints /
   _calc_track_coverage）共享同一缓存实例
7. 性能回归：验证缓存生效后 rglob 调用次数减少

【可观测性约束】
- 边界显性化：测试命名反映业务意图
- 异常处理：所有 mock 隔离文件系统与子进程，避免污染真实仓库

【生成日志摘要】
- 生成时间：2026-06-26
- 版本：v1.0.0
- 内容：visibility_report.py 缓存逻辑单元测试
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from unittest.mock import patch, MagicMock, call

import pytest

# 将 scripts 目录加入 sys.path 以导入 visibility_report 模块
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from visibility_report import MetricCollector  # noqa: E402


# ═══════════════════════════════════════════════════════════════
#  1. 缓存初始化与基本命中/未命中
# ═══════════════════════════════════════════════════════════════

class TestCacheInitialization:
    """缓存字段初始化：默认 None，首次调用后填充"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_cache_field_should_be_none_on_init(self, tmp_path):
        """MetricCollector 初始化后 _file_content_cache 应为 None"""
        collector = MetricCollector(tmp_path, {})
        assert collector._file_content_cache is None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_first_call_should_scan_and_populate_cache(self, tmp_path):
        """首次调用应扫描 agent/ 目录并填充缓存"""
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        (agent_dir / "mod1.py").write_text("# mod1\n", encoding="utf-8")
        (agent_dir / "mod2.py").write_text("# mod2\n", encoding="utf-8")

        collector = MetricCollector(tmp_path, {})
        # 首次调用前缓存为 None
        assert collector._file_content_cache is None
        result = collector._scan_agent_files()
        # 首次调用后缓存被填充
        assert collector._file_content_cache is not None
        assert len(result) == 2
        assert collector._file_content_cache is result  # 同一引用

    @pytest.mark.unit
    @pytest.mark.p0
    def test_second_call_should_return_same_cache_without_rescan(self, tmp_path):
        """第二次调用应直接返回缓存，不触发再次扫描"""
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        (agent_dir / "mod.py").write_text("# content\n", encoding="utf-8")

        collector = MetricCollector(tmp_path, {})
        first_result = collector._scan_agent_files()

        # 监控 agent_dir.exists 与 rglob 调用次数
        with patch.object(Path, "rglob", return_value=iter([])) as mock_rglob:
            second_result = collector._scan_agent_files()
            # 缓存命中时不应再调用 rglob
            assert mock_rglob.call_count == 0

        assert second_result is first_result


# ═══════════════════════════════════════════════════════════════
#  2. agent 目录不存在场景
# ═══════════════════════════════════════════════════════════════

class TestAgentDirMissing:
    """agent 目录不存在时的边界行为"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_return_empty_dict_when_agent_dir_missing(self, tmp_path):
        """agent/ 不存在时应返回空字典"""
        collector = MetricCollector(tmp_path, {})
        result = collector._scan_agent_files()
        assert result == {}
        # 缓存被填充为空字典（不再是 None），避免后续重复检查
        assert collector._file_content_cache == {}

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_not_raise_when_agent_dir_missing(self, tmp_path):
        """agent/ 不存在时不应抛出异常"""
        collector = MetricCollector(tmp_path, {})
        # 多次调用都应安全
        for _ in range(3):
            assert collector._scan_agent_files() == {}

    @pytest.mark.unit
    @pytest.mark.p1
    def test_cache_should_be_populated_even_when_empty(self, tmp_path):
        """agent/ 不存在时缓存仍应被填充（为空字典），避免重复扫描"""
        collector = MetricCollector(tmp_path, {})
        collector._scan_agent_files()
        # 缓存应被填充为空字典
        assert collector._file_content_cache is not None
        assert collector._file_content_cache == {}


# ═══════════════════════════════════════════════════════════════
#  3. 文件读取失败处理
# ═══════════════════════════════════════════════════════════════

class TestFileReadFailure:
    """文件读取失败时的边界行为：OSError/UnicodeDecodeError 不入缓存"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_skip_files_with_oserror(self, tmp_path):
        """OSError 文件应被跳过，不进入缓存"""
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        good_file = agent_dir / "good.py"
        bad_file = agent_dir / "bad.py"
        good_file.write_text("# good\n", encoding="utf-8")
        bad_file.write_text("# bad\n", encoding="utf-8")

        collector = MetricCollector(tmp_path, {})

        # 模拟 bad_file.read_text 抛出 OSError
        original_read_text = Path.read_text

        def mock_read_text(self, *args, **kwargs):
            if self == bad_file:
                raise OSError("模拟权限拒绝")
            return original_read_text(self, *args, **kwargs)

        with patch.object(Path, "read_text", mock_read_text):
            result = collector._scan_agent_files()

        # bad.py 应被跳过，只剩 good.py
        assert good_file in result
        assert bad_file not in result
        assert len(result) == 1

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_skip_files_with_unicode_decode_error(self, tmp_path):
        """UnicodeDecodeError 文件应被跳过"""
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        good_file = agent_dir / "good.py"
        bad_file = agent_dir / "bad.py"
        good_file.write_text("# good\n", encoding="utf-8")
        bad_file.write_text("# bad\n", encoding="utf-8")

        collector = MetricCollector(tmp_path, {})

        original_read_text = Path.read_text

        def mock_read_text(self, *args, **kwargs):
            if self == bad_file:
                raise UnicodeDecodeError("utf-8", b"\xff\xfe", 0, 1, "模拟非法 UTF-8")
            return original_read_text(self, *args, **kwargs)

        with patch.object(Path, "read_text", mock_read_text):
            result = collector._scan_agent_files()

        assert good_file in result
        assert bad_file not in result

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_continue_scanning_after_failure(self, tmp_path):
        """一个文件读取失败不应中断其他文件的扫描"""
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        # 创建 3 个文件，中间一个失败
        files = []
        for i in range(3):
            f = agent_dir / f"mod{i}.py"
            f.write_text(f"# mod{i}\n", encoding="utf-8")
            files.append(f)

        bad_file = files[1]
        collector = MetricCollector(tmp_path, {})

        original_read_text = Path.read_text

        def mock_read_text(self, *args, **kwargs):
            if self == bad_file:
                raise OSError("模拟失败")
            return original_read_text(self, *args, **kwargs)

        with patch.object(Path, "read_text", mock_read_text):
            result = collector._scan_agent_files()

        # 3 个文件中 1 个失败，应缓存 2 个
        assert len(result) == 2
        assert files[0] in result
        assert files[2] in result
        assert bad_file not in result

    @pytest.mark.unit
    @pytest.mark.p1
    def test_all_files_failure_should_yield_empty_cache(self, tmp_path):
        """所有文件都失败时缓存应为空字典"""
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        f1 = agent_dir / "f1.py"
        f2 = agent_dir / "f2.py"
        f1.write_text("# f1\n", encoding="utf-8")
        f2.write_text("# f2\n", encoding="utf-8")

        collector = MetricCollector(tmp_path, {})

        def mock_read_text(self, *args, **kwargs):
            raise OSError("全部失败")

        with patch.object(Path, "read_text", mock_read_text):
            result = collector._scan_agent_files()

        assert result == {}
        assert collector._file_content_cache == {}


# ═══════════════════════════════════════════════════════════════
#  4. 空目录与大量文件场景
# ═══════════════════════════════════════════════════════════════

class TestEmptyAndLargeDirectory:
    """空 agent 目录与大量文件场景"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_empty_agent_dir_should_yield_empty_cache(self, tmp_path):
        """agent/ 存在但为空目录时应返回空字典"""
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        collector = MetricCollector(tmp_path, {})
        result = collector._scan_agent_files()
        assert result == {}
        assert collector._file_content_cache == {}

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_scan_nested_subdirectories(self, tmp_path):
        """应递归扫描 agent/ 下所有子目录的 .py 文件"""
        agent_dir = tmp_path / "agent"
        sub1 = agent_dir / "module_a" / "sub"
        sub2 = agent_dir / "module_b"
        sub1.mkdir(parents=True)
        sub2.mkdir(parents=True)

        (sub1 / "a.py").write_text("# a\n", encoding="utf-8")
        (sub1 / "b.py").write_text("# b\n", encoding="utf-8")
        (sub2 / "c.py").write_text("# c\n", encoding="utf-8")
        (agent_dir / "top.py").write_text("# top\n", encoding="utf-8")

        collector = MetricCollector(tmp_path, {})
        result = collector._scan_agent_files()
        # 4 个 .py 文件（含顶层与嵌套）
        assert len(result) == 4

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_handle_many_files(self, tmp_path):
        """大量文件场景应正常缓存"""
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        # 创建 50 个文件
        for i in range(50):
            (agent_dir / f"mod_{i:03d}.py").write_text(
                f"# module {i}\n", encoding="utf-8"
            )

        collector = MetricCollector(tmp_path, {})
        result = collector._scan_agent_files()
        assert len(result) == 50
        # 验证内容正确
        for i in range(50):
            key = agent_dir / f"mod_{i:03d}.py"
            assert key in result
            assert result[key] == f"# module {i}\n"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_only_cache_py_files(self, tmp_path):
        """应只缓存 .py 文件，忽略其他扩展名"""
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        (agent_dir / "code.py").write_text("# py\n", encoding="utf-8")
        (agent_dir / "readme.md").write_text("# md\n", encoding="utf-8")
        (agent_dir / "data.json").write_text("{}", encoding="utf-8")
        (agent_dir / "config.yml").write_text("k: v\n", encoding="utf-8")

        collector = MetricCollector(tmp_path, {})
        result = collector._scan_agent_files()
        assert len(result) == 1
        assert (agent_dir / "code.py") in result


# ═══════════════════════════════════════════════════════════════
#  5. 多次调用一致性
# ═══════════════════════════════════════════════════════════════

class TestMultiCallConsistency:
    """多次调用应返回一致结果"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_multiple_calls_return_same_reference(self, tmp_path):
        """多次调用应返回同一字典引用（缓存生效）"""
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        (agent_dir / "mod.py").write_text("# mod\n", encoding="utf-8")

        collector = MetricCollector(tmp_path, {})
        r1 = collector._scan_agent_files()
        r2 = collector._scan_agent_files()
        r3 = collector._scan_agent_files()

        assert r1 is r2
        assert r2 is r3
        # 修改 r1 应影响 r2（同一对象）
        fake_path = Path("/fake")
        r1[fake_path] = "fake"
        assert fake_path in r2
        assert r2[fake_path] == "fake"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_cache_should_not_reflect_subsequent_filesystem_changes(self, tmp_path):
        """缓存填充后，文件系统变更不应影响缓存内容（快照语义）"""
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        f1 = agent_dir / "f1.py"
        f1.write_text("# v1\n", encoding="utf-8")

        collector = MetricCollector(tmp_path, {})
        result = collector._scan_agent_files()
        assert result[f1] == "# v1\n"

        # 修改文件 + 新增文件
        f1.write_text("# v2\n", encoding="utf-8")
        (agent_dir / "f2.py").write_text("# new\n", encoding="utf-8")

        # 再次调用应返回旧缓存
        result2 = collector._scan_agent_files()
        assert result2 is result
        assert result2[f1] == "# v1\n"  # 仍是旧内容
        assert (agent_dir / "f2.py") not in result2


# ═══════════════════════════════════════════════════════════════
#  6. 三个共享方法的缓存协作
# ═══════════════════════════════════════════════════════════════

class TestSharedCacheAmongMethods:
    """三个采集方法应共享同一缓存实例"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_three_methods_should_share_same_cache(self, tmp_path):
        """_calc_structured_log_coverage / _count_health_endpoints /
        _calc_track_coverage 应共享同一缓存"""
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        (agent_dir / "mod.py").write_text(
            'logger.info(json.dumps({"trace_id": "x"}))\n'
            '@app.route("/health")\n'
            'BusinessMetricsCollector.track("e")\n',
            encoding="utf-8",
        )

        collector = MetricCollector(tmp_path, {})
        # 调用第一个方法触发缓存填充
        collector._calc_structured_log_coverage()
        cache_after_first = collector._file_content_cache
        assert cache_after_first is not None
        assert len(cache_after_first) == 1

        # 调用第二个方法不应改变缓存对象
        collector._count_health_endpoints()
        assert collector._file_content_cache is cache_after_first

        # 调用第三个方法也不应改变缓存对象
        collector._calc_track_coverage()
        assert collector._file_content_cache is cache_after_first

    @pytest.mark.unit
    @pytest.mark.p0
    def test_methods_should_produce_correct_results_with_shared_cache(self, tmp_path):
        """三个方法基于共享缓存应产出正确结果"""
        agent_dir = tmp_path / "agent"
        # 构造一个含 trace 的子模块
        mod_dir = agent_dir / "module_x"
        mod_dir.mkdir(parents=True)
        (mod_dir / "code.py").write_text(
            'import logging\n'
            'logger = logging.getLogger(__name__)\n'
            'logger.info(json.dumps({"trace_id": "abc"}))\n'  # 结构化日志
            'logger.debug("plain")\n'                          # 非结构化
            '@app.route("/api/health")\n'                      # 健康端点
            'def h(): pass\n'
            'BusinessMetricsCollector.track("event")\n',       # 埋点
            encoding="utf-8",
        )

        collector = MetricCollector(tmp_path, {})
        log_cov = collector._calc_structured_log_coverage()
        # 2 条 logger 调用，1 条含 trace_id/json.dumps → 50.0
        assert log_cov == 50.0
        # 健康端点 1 个
        assert collector._count_health_endpoints() == 1
        # 1 个子模块，含埋点 → 100.0
        assert collector._calc_track_coverage() == 100.0


# ═══════════════════════════════════════════════════════════════
#  7. 性能回归：缓存生效后调用次数减少
# ═══════════════════════════════════════════════════════════════

class TestCachePerformance:
    """性能回归：验证缓存生效后 IO 次数减少"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rglob_should_be_called_once_across_three_methods(self, tmp_path):
        """三个采集方法联合调用时，rglob 只应被调用 1 次（首次填充缓存）"""
        agent_dir = tmp_path / "agent"
        mod_dir = agent_dir / "mod"
        mod_dir.mkdir(parents=True)
        (mod_dir / "code.py").write_text(
            'logger.info(json.dumps({"trace_id": "x"}))\n'
            '@app.route("/health")\n'
            'BusinessMetricsCollector.track("e")\n',
            encoding="utf-8",
        )

        collector = MetricCollector(tmp_path, {})
        # 监控 Path.rglob 调用（autospec=True 保留 self 绑定）
        with patch.object(Path, "rglob", autospec=True) as mock_rglob:
            collector._calc_structured_log_coverage()
            collector._count_health_endpoints()
            collector._calc_track_coverage()

        # rglob 在 _scan_agent_files 中只应被调用一次
        rglob_calls = mock_rglob.call_count
        assert rglob_calls == 1, f"rglob 应只调用 1 次，实际 {rglob_calls} 次"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_read_text_should_be_called_once_per_file(self, tmp_path):
        """每个文件的 read_text 应只被调用 1 次（缓存生效后不再读取）"""
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        (agent_dir / "f1.py").write_text("# f1\n", encoding="utf-8")
        (agent_dir / "f2.py").write_text("# f2\n", encoding="utf-8")

        collector = MetricCollector(tmp_path, {})
        # autospec=True 保留 self 绑定，wraps 需手动绑定
        real_read_text = Path.read_text

        def read_text_spy(self, *args, **kwargs):
            return real_read_text(self, *args, **kwargs)

        with patch.object(Path, "read_text", autospec=True, side_effect=read_text_spy) as mock_read:
            collector._calc_structured_log_coverage()
            collector._count_health_endpoints()
            collector._calc_track_coverage()

        # 2 个文件，缓存生效后只读 2 次（不是 6 次）
        assert mock_read.call_count == 2

    @pytest.mark.unit
    @pytest.mark.p1
    def test_cache_reuse_avoids_repeated_io(self, tmp_path):
        """缓存复用应避免重复 IO（通过 spy 验证）"""
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        (agent_dir / "mod.py").write_text("# mod\n", encoding="utf-8")

        collector = MetricCollector(tmp_path, {})
        # 第一次调用：触发扫描
        collector._scan_agent_files()

        # 再调用 5 次：应不再触发 IO
        with patch.object(Path, "read_text", autospec=True) as mock_read:
            for _ in range(5):
                collector._scan_agent_files()
            assert mock_read.call_count == 0


# ═══════════════════════════════════════════════════════════════
#  8. 并发安全（基础测试）
# ═══════════════════════════════════════════════════════════════

class TestCacheConcurrency:
    """缓存字段的线程安全性基础测试

    注：当前实现未加锁，多次并发首次调用可能触发多次扫描，
    但最终缓存值应一致。此处仅验证最终一致性。
    """

    @pytest.mark.unit
    @pytest.mark.p1
    def test_concurrent_first_calls_should_produce_consistent_cache(self, tmp_path):
        """多线程并发首次调用应最终产生一致的缓存（值相同）"""
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        for i in range(5):
            (agent_dir / f"mod_{i}.py").write_text(
                f"# mod{i}\n", encoding="utf-8"
            )

        collector = MetricCollector(tmp_path, {})
        results = []
        barrier = threading.Barrier(5)

        def worker():
            barrier.wait()  # 同步启动
            r = collector._scan_agent_files()
            results.append(r)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 所有结果应有相同的键集合
        key_sets = [frozenset(r.keys()) for r in results]
        assert all(ks == key_sets[0] for ks in key_sets)
        # 最终缓存应被填充
        assert collector._file_content_cache is not None
        assert len(collector._file_content_cache) == 5


# ═══════════════════════════════════════════════════════════════
#  9. P0 补充：_calc_track_coverage / _calc_structured_log_coverage / _count_health_endpoints 边界
# ═══════════════════════════════════════════════════════════════

class TestCalcTrackCoverageP0Boundaries:
    """_calc_track_coverage 的 P0 级边界条件覆盖

    覆盖缺口（来自 test_coverage_gap_analysis.md 1.3 节）：
    - 下划线目录跳过（721行）
    - total_modules==0 返回 100.0（736-737行）
    - break 跳过子目录其他文件（733-734行）
    """

    @pytest.mark.unit
    @pytest.mark.p0
    def test_calc_track_coverage_skip_underscore_dirs(self, tmp_path):
        """P0-1: _calc_track_coverage 应跳过 _ 开头的子目录，不计入 total_modules

        覆盖行 731: sub_dir.name.startswith("_") 跳过逻辑
        """
        # 构造 agent/ 目录：_internal（应跳过）+ real（应计入）
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        (agent_dir / "_internal").mkdir()
        # _internal 下文件含埋点，但因目录被跳过不应被统计
        (agent_dir / "_internal" / "mod.py").write_text(
            "trackEvent('x', {})", encoding="utf-8"
        )
        (agent_dir / "real").mkdir()
        # real 下文件不含埋点
        (agent_dir / "real" / "mod.py").write_text(
            "print('no tracking')", encoding="utf-8"
        )

        collector = MetricCollector(tmp_path, {})
        coverage = collector._calc_track_coverage()

        # total_modules 应为 1（只计 real，跳过 _internal）
        # tracked_modules 应为 0（real 无埋点）
        # coverage = 0/1 * 100 = 0.0
        assert coverage == 0.0, (
            f"应跳过 _internal 目录，coverage 应为 0.0，实际 {coverage}"
        )

    @pytest.mark.unit
    @pytest.mark.p0
    def test_calc_track_coverage_total_modules_zero_returns_100(self, tmp_path):
        """P0-2: agent 目录下所有子目录都以 _ 开头时，total_modules=0 应返回 100.0

        覆盖行 751-759: total_modules == 0 返回 100.0 的除零保护
        """
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        # 所有子目录都以 _ 开头
        (agent_dir / "__pycache__").mkdir()
        (agent_dir / "_internal").mkdir()
        (agent_dir / "_private").mkdir()

        collector = MetricCollector(tmp_path, {})
        coverage = collector._calc_track_coverage()

        # total_modules=0，应返回 100.0，不抛除零异常
        assert coverage == 100.0, (
            f"total_modules=0 时应返回 100.0，实际 {coverage}"
        )

    @pytest.mark.unit
    @pytest.mark.p0
    def test_calc_track_coverage_multi_file_subdir_break(self, tmp_path):
        """P0-4: 某子目录下多个 .py 文件含埋点，break 后 tracked_modules 只递增 1 次

        覆盖行 744-746: 找到第一个含埋点文件后 break，跳过该子目录其他文件
        """
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        # mod1 子目录下 2 个文件都含埋点
        (agent_dir / "mod1").mkdir()
        (agent_dir / "mod1" / "a.py").write_text(
            "trackEvent('a', {})", encoding="utf-8"
        )
        (agent_dir / "mod1" / "b.py").write_text(
            "trackEvent('b', {})", encoding="utf-8"
        )
        # mod2 子目录下 2 个文件都含埋点
        (agent_dir / "mod2").mkdir()
        (agent_dir / "mod2" / "a.py").write_text(
            "BusinessMetricsCollector()", encoding="utf-8"
        )
        (agent_dir / "mod2" / "b.py").write_text(
            "track('c')", encoding="utf-8"
        )

        collector = MetricCollector(tmp_path, {})
        coverage = collector._calc_track_coverage()

        # break 生效：每个子目录只计 1 次，tracked_modules=2，total_modules=2
        # coverage = 2/2 * 100 = 100.0
        # 若 break 不生效：tracked_modules=4，coverage=200.0（异常）
        assert coverage == 100.0, (
            f"break 生效时 tracked_modules=2, coverage 应为 100.0，实际 {coverage}。"
            f"若为 200.0 说明 break 未生效"
        )


class TestCalcStructuredLogCoverageP0Boundaries:
    """_calc_structured_log_coverage 的 P0 级边界条件覆盖"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_calc_structured_log_coverage_no_logs_returns_100(self, tmp_path):
        """P0-3: agent 文件无任何 logger 调用时，total_logs=0 应返回 100.0

        覆盖行 239-250: total_logs == 0 返回 100.0 的除零保护
        """
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        # 文件不含任何 logger 调用
        (agent_dir / "mod.py").write_text(
            "print('hello')\n"
            "x = 1 + 2\n"
            "def foo():\n"
            "    return x\n",
            encoding="utf-8",
        )

        collector = MetricCollector(tmp_path, {})
        coverage = collector._calc_structured_log_coverage()

        # total_logs=0，应返回 100.0，不抛除零异常
        assert coverage == 100.0, (
            f"total_logs=0 时应返回 100.0，实际 {coverage}"
        )


class TestCountHealthEndpointsP0Boundaries:
    """_count_health_endpoints 的 P0 级边界条件覆盖（补充第 5 个 P0）"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_count_health_endpoints_multiple_in_same_file(self, tmp_path):
        """P0-5: 单文件含多个 /health 端点，验证正则全局匹配全部计数

        覆盖行 329: re.findall 全局匹配多个端点的行为
        """
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        # 单文件含 3 个不同形式的健康端点
        (agent_dir / "server.py").write_text(
            '@app.route("/health")\n'
            'def health():\n'
            '    return "ok"\n'
            '\n'
            '@app.route("/api/health")\n'
            'def api_health():\n'
            '    return "ok"\n'
            '\n'
            '@app.route("/status")\n'
            'def status():\n'
            '    return "ok"\n',
            encoding="utf-8",
        )

        collector = MetricCollector(tmp_path, {})
        count = collector._count_health_endpoints()

        # 应匹配 3 个端点：/health, /api/health, /status
        assert count == 3, (
            f"单文件含 3 个健康端点应计数为 3，实际 {count}"
        )


# ═══════════════════════════════════════════════════════════════
#  10. P1 补充：缓存重置/非目录/跨行匹配/iterdir/relative_to/性能
# ═══════════════════════════════════════════════════════════════

class TestCacheResetAndRescanP1:
    """缓存重置与重新扫描的 P1 级边界条件覆盖

    覆盖缺口（来自 test_coverage_gap_analysis.md 1.3 节 P1）：
    - 缓存被显式重置为 None 后能否重新扫描
    """

    @pytest.mark.unit
    @pytest.mark.p1
    def test_cache_reset_to_none_then_rescan(self, tmp_path):
        """P1-1: 缓存被显式重置为 None 后应能重新扫描

        场景：首次扫描后，外部代码将 _file_content_cache 重置为 None
        （如刷新场景），再次调用应重新扫描并填充新内容。

        预期：
        - 重置后缓存为 None
        - 再次调用 _scan_agent_files 应触发 rglob
        - 缓存被重新填充，内容反映最新文件系统状态
        """
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        (agent_dir / "v1.py").write_text("# v1\n", encoding="utf-8")

        collector = MetricCollector(tmp_path, {})
        first_result = collector._scan_agent_files()
        assert len(first_result) == 1
        assert collector._file_content_cache is not None

        # 模拟缓存失效场景：新增文件 + 重置缓存
        (agent_dir / "v2.py").write_text("# v2\n", encoding="utf-8")
        collector._file_content_cache = None  # 显式重置

        # 监控 rglob 调用，但执行真实逻辑（用 spy 而非空 mock）
        real_rglob = Path.rglob

        def rglob_spy(self, pattern):
            return real_rglob(self, pattern)

        with patch.object(
            Path, "rglob", autospec=True, side_effect=rglob_spy
        ) as mock_rglob:
            second_result = collector._scan_agent_files()
            # 重置后应再次调用 rglob
            assert mock_rglob.call_count == 1, (
                f"缓存重置为 None 后应重新调用 rglob，"
                f"实际调用 {mock_rglob.call_count} 次"
            )

        # 新缓存应包含新增的 v2.py
        assert len(second_result) == 2
        assert (agent_dir / "v1.py") in second_result
        assert (agent_dir / "v2.py") in second_result


class TestAgentDirIsFileNotDirectoryP1:
    """agent_dir 是文件而非目录时的 P1 级边界条件覆盖

    覆盖缺口：agent_dir.exists() 为 True 但 rglob 在文件上抛异常
    """

    @pytest.mark.unit
    @pytest.mark.p1
    def test_agent_dir_is_file_not_directory(self, tmp_path):
        """P1-2: agent_dir 是文件而非目录时的 rglob 行为

        场景：tmp_path/agent 是一个文件（非目录），
        agent_dir.exists() 返回 True，但 rglob 在文件上行为平台相关：
        - POSIX 上通常抛 ValueError/NotADirectoryError
        - Windows 上返回空迭代器（不抛异常）

        预期（兼容两种平台行为）：
        - 要么抛出 ValueError/NotADirectoryError/OSError
        - 要么返回空字典（Windows 行为）
        - 缓存应被填充（None 或空字典），避免后续重复触发
        """
        # 创建 agent 作为文件而非目录
        agent_file = tmp_path / "agent"
        agent_file.write_text("I am a file, not a directory\n", encoding="utf-8")

        collector = MetricCollector(tmp_path, {})

        # agent_dir.exists() 为 True（文件也存在）
        agent_dir = tmp_path / "agent"
        assert agent_dir.exists()

        # 兼容两种平台行为：抛异常 或 返回空字典
        try:
            result = collector._scan_agent_files()
            # Windows 行为：rglob 在文件上返回空迭代器，结果为空字典
            assert result == {}, (
                f"agent 是文件时，Windows 上应返回空字典，实际 {result}"
            )
            # 缓存应被填充为空字典，避免后续重复扫描
            assert collector._file_content_cache == {}
        except (ValueError, NotADirectoryError, OSError):
            # POSIX 行为：rglob 在文件上抛异常
            # 缓存可能未填充（异常在填充前抛出），这是可接受的
            pass


class TestStructuredLogCoverageMultilineP1:
    """结构化日志覆盖率跨行匹配的 P1 级边界条件覆盖

    覆盖缺口：re.DOTALL 模式跨行匹配 trace_id
    """

    @pytest.mark.unit
    @pytest.mark.p1
    def test_structured_log_coverage_multiline_trace_id(self, tmp_path):
        """P1-3: re.DOTALL 模式跨行匹配 trace_id

        场景：logger.info 调用跨多行，trace_id 出现在第二行，
        re.DOTALL 模式应能让 . 匹配换行符，正确识别为结构化日志。

        预期：
        - 跨行 logger 调用含 trace_id 应被识别为结构化日志
        - 覆盖率应反映跨行匹配的正确性
        """
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        # 构造跨多行的 logger.info 调用，trace_id 在第二行
        (agent_dir / "mod.py").write_text(
            'logger.info(\n'
            '    json.dumps({"trace_id": "abc123", "msg": "cross-line"})\n'
            ')\n'
            'logger.debug("plain single line")\n',
            encoding="utf-8",
        )

        collector = MetricCollector(tmp_path, {})
        coverage = collector._calc_structured_log_coverage()

        # 2 条 logger 调用：1 条跨行含 trace_id（结构化），1 条普通（非结构化）
        # re.DOTALL 应让跨行调用被正确匹配为结构化
        # 覆盖率 = 1/2 * 100 = 50.0
        assert coverage == 50.0, (
            f"re.DOTALL 应跨行匹配 trace_id，覆盖率应为 50.0，"
            f"实际 {coverage}。若为 0.0 说明 DOTALL 未生效"
        )


class TestCalcTrackCoverageIterdirFilesP1:
    """_calc_track_coverage 中 iterdir 返回文件的 P1 级边界条件覆盖

    覆盖缺口：iterdir 返回非目录文件时的处理
    """

    @pytest.mark.unit
    @pytest.mark.p1
    def test_calc_track_coverage_iterdir_returns_files(self, tmp_path):
        """P1-4: iterdir 返回非目录文件时应被跳过，不计入 total_modules

        场景：agent/ 下既有文件（如 top.py）又有子目录，
        iterdir 会返回文件和目录，文件应被 is_dir() 检查跳过。

        预期：
        - 顶层文件 top.py 不计入 total_modules
        - 只有子目录计入 total_modules
        - 覆盖行 737: if not sub_dir.is_dir() 跳过逻辑
        """
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        # 顶层文件（应被跳过）
        (agent_dir / "top_level_file.py").write_text(
            "trackEvent('x', {})", encoding="utf-8"
        )
        # 子目录（应被计入）
        (agent_dir / "module_a").mkdir()
        (agent_dir / "module_a" / "mod.py").write_text(
            "print('no tracking')", encoding="utf-8"
        )

        collector = MetricCollector(tmp_path, {})
        coverage = collector._calc_track_coverage()

        # total_modules 应为 1（只有 module_a，top_level_file.py 被跳过）
        # tracked_modules 为 0（module_a 无埋点）
        # coverage = 0/1 * 100 = 0.0
        assert coverage == 0.0, (
            f"顶层文件应被 is_dir() 跳过，total_modules=1，"
            f"coverage 应为 0.0，实际 {coverage}"
        )


class TestCalcTrackCoverageRelativeToValueErrorP1:
    """_calc_track_coverage 中 relative_to 抛 ValueError 的 P1 级边界条件覆盖

    覆盖缺口：py_file.relative_to 抛 ValueError 时跳过
    """

    @pytest.mark.unit
    @pytest.mark.p1
    def test_calc_track_coverage_relative_to_value_error(self, tmp_path):
        """P1-5: py_file.relative_to 抛 ValueError 时应跳过该文件

        场景：file_cache 中包含不属于当前 sub_dir 的文件，
        py_file.relative_to(sub_dir) 抛 ValueError，应 continue 跳过。

        预期：
        - 顶层文件 agent/top.py 不属于 agent/module_a，relative_to 抛 ValueError
        - top.py 不影响 module_a 的埋点统计
        - 覆盖行 745-747: try/except ValueError 跳过逻辑
        """
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        # 顶层文件（不属于任何子目录，relative_to 会抛 ValueError）
        (agent_dir / "top.py").write_text(
            "trackEvent('top', {})", encoding="utf-8"
        )
        # 子目录 module_a 下的文件（不含埋点）
        (agent_dir / "module_a").mkdir()
        (agent_dir / "module_a" / "mod.py").write_text(
            "print('no tracking')", encoding="utf-8"
        )

        collector = MetricCollector(tmp_path, {})
        coverage = collector._calc_track_coverage()

        # 遍历 module_a 时：
        # - top.py.relative_to(module_a) 抛 ValueError，跳过（不含埋点，不影响）
        # - module_a/mod.py.relative_to(module_a) 成功，检查内容（无埋点）
        # total_modules=1（module_a），tracked_modules=0
        # coverage = 0/1 * 100 = 0.0
        # top.py 的埋点不被计入（因为不属于任何子目录）
        assert coverage == 0.0, (
            f"relative_to ValueError 应跳过 top.py，"
            f"module_a 无埋点，coverage 应为 0.0，实际 {coverage}"
        )


class TestLargeAgentDirPerformanceP1:
    """50+ 文件性能验证的 P1 级边界条件覆盖"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_large_agent_dir_50_plus_files_performance(self, tmp_path):
        """P1-6: 50+ 文件场景下三个采集方法的性能验证

        场景：agent/ 下有 60 个 .py 文件，验证缓存生效后
        三个采集方法（_calc_structured_log_coverage /
        _count_health_endpoints / _calc_track_coverage）联合调用
        在合理时间内完成（< 5 秒）。

        预期：
        - 60 个文件全部被缓存
        - 三个方法联合调用耗时 < 5 秒
        - rglob 只调用 1 次（缓存生效）
        """
        import time

        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        # 创建 60 个文件，每个含结构化日志、健康端点、埋点
        for i in range(60):
            (agent_dir / f"mod_{i:03d}.py").write_text(
                f'logger.info(json.dumps({{"trace_id": "{i}", "action": "test"}}))\n'
                f'@app.route("/health")\n'
                f'def h_{i}(): return "ok"\n'
                f'trackEvent("event_{i}", {{}})\n',
                encoding="utf-8",
            )

        collector = MetricCollector(tmp_path, {})

        # 监控 rglob 调用次数，但执行真实逻辑（用 spy 而非空 mock）
        # autospec=True 的空 mock 会返回 MagicMock 不可迭代，导致扫描结果为空
        # side_effect=rglob_spy 让 mock 执行真实 rglob 逻辑，仅监控调用次数
        real_rglob = Path.rglob

        def rglob_spy(self, pattern):
            return real_rglob(self, pattern)

        with patch.object(
            Path, "rglob", autospec=True, side_effect=rglob_spy
        ) as mock_rglob:
            t0 = time.perf_counter()
            log_cov = collector._calc_structured_log_coverage()
            health_count = collector._count_health_endpoints()
            track_cov = collector._calc_track_coverage()
            elapsed = time.perf_counter() - t0
            rglob_calls = mock_rglob.call_count

        # 性能断言：< 5 秒
        assert elapsed < 5.0, (
            f"60 个文件三个方法联合调用应 < 5 秒，实际 {elapsed:.3f} 秒"
        )
        # 缓存生效：rglob 只调用 1 次
        assert rglob_calls == 1, (
            f"60 个文件缓存生效后 rglob 应只调用 1 次，"
            f"实际 {rglob_calls} 次"
        )
        # 结果正确性验证
        assert log_cov == 100.0  # 所有 logger 调用都含 trace_id
        assert health_count == 60  # 60 个健康端点
        # track_cov: agent/ 下无子目录，total_modules=0，返回 100.0
        assert track_cov == 100.0
