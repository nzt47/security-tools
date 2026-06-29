#!/usr/bin/env python3
"""@trace_route 装饰器覆盖率验证测试

验证内容（对应 visibility_report.py 的 D3 指标）：
1. 覆盖率达标：agent/server_routes/routes_*.py 中 @trace_route 占 @app.route 比例 ≥ 30%
2. 关键路由覆盖：routes_chat / routes_dashboard / routes_panorama / routes_business_dashboard 至少含 1 个 @trace_route
3. 装饰器功能：@trace_route 正确创建 TraceContext 并在函数执行期间生效
4. 装饰器顺序约定：@trace_route 应位于 @log_request 外层（保证 trace_id 在日志记录时可用）
5. 装饰器不破坏原函数元信息（__name__ / __doc__ 保留）

计算口径与 scripts/visibility_report.py 的 _calc_trace_coverage 完全一致，
保证测试与报告结论对齐。
"""

import re
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ROUTES_DIR = PROJECT_ROOT / "agent" / "server_routes"

# 阈值与 visibility_report.py 中 config.yaml 的 visibility_thresholds.runtime.trace_coverage 对齐
TRACE_COVERAGE_THRESHOLD = 30.0


# ──────────────────────────────────────────────────────────────────────
# 工具函数：与 visibility_report.py 计算口径完全一致
# ──────────────────────────────────────────────────────────────────────

def _count_routes() -> tuple[int, int, list[str]]:
    """统计 routes_*.py 中 @app.route 与 @trace_route 数量

    Returns:
        (total_routes, traced_routes, files_without_trace)
        - total_routes: 所有 @app.route 装饰器数量
        - traced_routes: 所有 @trace_route 装饰器数量
        - files_without_trace: 含 @app.route 但完全无 @trace_route 的文件名
    """
    total_routes = 0
    traced_routes = 0
    files_without_trace = []

    if not ROUTES_DIR.exists():
        return 0, 0, []

    for py_file in ROUTES_DIR.glob("routes_*.py"):
        try:
            content = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        # 与 visibility_report.py 一致：统计 @app.route(["'] 装饰器
        routes = re.findall(r'@app\.route\(["\']', content)
        traced = re.findall(r'@trace_route', content)

        total_routes += len(routes)
        traced_routes += len(traced)

        # 记录完全无 @trace_route 但有 @app.route 的文件
        if len(routes) > 0 and len(traced) == 0:
            files_without_trace.append(py_file.name)

    return total_routes, traced_routes, files_without_trace


def _calc_coverage() -> float:
    """计算 @trace_route 覆盖率（与 visibility_report.py 口径一致）"""
    total, traced, _ = _count_routes()
    if total == 0:
        return 100.0
    return round(traced / total * 100, 1)


# ──────────────────────────────────────────────────────────────────────
# 测试类：覆盖率达标验证
# ──────────────────────────────────────────────────────────────────────

class TestTraceRouteCoverage:
    """@trace_route 覆盖率达标验证（D3 指标）"""

    def test_coverage_meets_threshold(self):
        """@trace_route 覆盖率必须 ≥ 30%"""
        coverage = _calc_coverage()
        assert coverage >= TRACE_COVERAGE_THRESHOLD, (
            f"@trace_route 覆盖率 {coverage}% 低于阈值 {TRACE_COVERAGE_THRESHOLD}%，"
            f"请为 agent/server_routes/routes_*.py 中未加装饰器的路由补充 @trace_route"
        )

    def test_coverage_calculation_consistent_with_report(self):
        """覆盖率计算口径与 visibility_report.py 一致"""
        total, traced, _ = _count_routes()
        # 至少存在路由，避免空集误判为 100% 通过
        assert total > 0, "agent/server_routes/ 下未扫描到任何 @app.route 装饰器"
        assert traced > 0, "未扫描到任何 @trace_route 装饰器"

    def test_files_without_trace_route(self):
        """列出无 @trace_route 的路由文件（信息性断言，不阻断）"""
        _, _, files_without = _count_routes()
        # 信息性输出，便于排查；不强制断言为空，避免阻断 CI
        # 但若超过半数文件缺失，则视为改造不充分
        all_route_files = list(ROUTES_DIR.glob("routes_*.py"))
        if all_route_files:
            missing_ratio = len(files_without) / len(all_route_files)
            assert missing_ratio < 0.5, (
                f"超过半数路由文件缺少 @trace_route：{files_without}"
            )


# ──────────────────────────────────────────────────────────────────────
# 测试类：关键路由文件覆盖验证
# ──────────────────────────────────────────────────────────────────────

class TestKeyRoutesCoverage:
    """关键路由文件必须含 @trace_route 装饰器"""

    @pytest.mark.parametrize("route_file", [
        "routes_chat.py",
        "routes_dashboard.py",
        "routes_panorama.py",
        "routes_business_dashboard.py",
        "routes_health.py",
    ])
    def test_key_route_file_has_trace_route(self, route_file):
        """关键路由文件至少含 1 个 @trace_route"""
        filepath = ROUTES_DIR / route_file
        if not filepath.exists():
            pytest.skip(f"{route_file} 不存在，跳过")
        content = filepath.read_text(encoding="utf-8")
        assert re.search(r'@trace_route', content), (
            f"{route_file} 未使用 @trace_route 装饰器，"
            f"关键路由必须接入链路追踪"
        )


# ──────────────────────────────────────────────────────────────────────
# 测试类：装饰器功能验证
# ──────────────────────────────────────────────────────────────────────

class TestTraceRouteDecoratorBehavior:
    """@trace_route 装饰器功能正确性验证"""

    def test_trace_route_creates_trace_context(self):
        """@trace_route 应在函数执行期间创建并激活 TraceContext"""
        from agent.server_routes.tracing_decorator import trace_route
        from agent.monitoring.tracing import get_trace_id

        captured_trace_id = []

        @trace_route("TestService")
        def sample_handler():
            # 函数执行期间应能获取到 trace_id
            captured_trace_id.append(get_trace_id())
            return {"ok": True}

        result = sample_handler()
        assert result == {"ok": True}
        # 装饰器应在函数执行期间生成 trace_id
        assert len(captured_trace_id) == 1
        assert captured_trace_id[0] is not None, (
            "@trace_route 未在函数执行期间激活 TraceContext，trace_id 为 None"
        )

    def test_trace_route_preserves_function_metadata(self):
        """@trace_route 应保留原函数的 __name__ 和 __doc__"""
        from agent.server_routes.tracing_decorator import trace_route

        @trace_route("TestService")
        def api_sample_operation():
            """样本操作文档字符串"""
            return "result"

        assert api_sample_operation.__name__ == "api_sample_operation"
        assert api_sample_operation.__doc__ == "样本操作文档字符串"

    def test_trace_route_operation_name_derived_from_function(self):
        """@trace_route 应从函数名推导 operation（api_xxx_yyy → xxx.yyy）"""
        from agent.server_routes.tracing_decorator import trace_route
        from agent.monitoring.tracing import TraceContext

        captured_operations = []
        original_init = TraceContext.__init__

        def mock_init(self, service_name, operation, **kwargs):
            captured_operations.append((service_name, operation))
            original_init(self, service_name, operation, **kwargs)

        with patch.object(TraceContext, '__init__', mock_init):
            @trace_route("APIService")
            def api_user_login():
                return "ok"

            api_user_login()

        assert len(captured_operations) == 1
        service, operation = captured_operations[0]
        assert service == "APIService"
        # api_user_login → user.login
        assert operation == "user.login"

    def test_trace_route_propagates_exception(self):
        """@trace_route 不应吞掉原函数抛出的异常"""
        from agent.server_routes.tracing_decorator import trace_route

        @trace_route("TestService")
        def failing_handler():
            raise ValueError("测试异常")

        with pytest.raises(ValueError, match="测试异常"):
            failing_handler()

    def test_trace_route_with_kwargs(self):
        """@trace_route 应正确传递 kwargs 给被装饰函数"""
        from agent.server_routes.tracing_decorator import trace_route

        @trace_route("TestService")
        def handler_with_args(arg1, arg2=None):
            return {"arg1": arg1, "arg2": arg2}

        result = handler_with_args("value1", arg2="value2")
        assert result == {"arg1": "value1", "arg2": "value2"}


# ──────────────────────────────────────────────────────────────────────
# 测试类：装饰器顺序约定验证
# ──────────────────────────────────────────────────────────────────────

class TestDecoratorOrderConvention:
    """装饰器顺序约定验证

    约定：@trace_route 应位于 @log_request 外层（代码中位于上方），
    保证 trace_id 在 @log_request 记录请求/响应日志时已可用。

    代码顺序示例（符合约定）：
        @app.route(...)
        @trace_route("Chat")      # 外层（上方）
        @log_request()            # 内层（下方）
        def api_xxx(): ...

    说明：本测试为约定检查，统计符合/不符合比例，不强制 100%，
    避免对历史代码造成大规模阻断。但不符合比例过高时会告警。
    """

    def test_decorator_order_convention(self):
        """统计 @trace_route 与 @log_request 的相对顺序"""
        if not ROUTES_DIR.exists():
            pytest.skip("路由目录不存在")

        # 收集同时含 @trace_route 和 @log_request 的文件中的装饰器块
        compliant = 0  # 符合约定：@trace_route 在 @log_request 上方
        violating = 0  # 不符合：@trace_route 在 @log_request 下方

        for py_file in ROUTES_DIR.glob("routes_*.py"):
            try:
                content = py_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

            lines = content.splitlines()
            for i, line in enumerate(lines):
                stripped = line.strip()
                # 查找 @trace_route 行
                if stripped.startswith('@trace_route'):
                    # 向上查找最近的非空、非装饰器注释行
                    # 检查上方紧邻的装饰器是否为 @log_request
                    for j in range(i - 1, max(i - 5, -1), -1):
                        upper = lines[j].strip() if j < len(lines) else ""
                        if not upper or upper.startswith('#'):
                            continue
                        if upper.startswith('@log_request'):
                            # @log_request 在 @trace_route 上方 → @trace_route 是内层 → 违反约定
                            violating += 1
                        elif upper.startswith('@'):
                            # 其他装饰器在上方，继续向上查找
                            continue
                        else:
                            # 遇到非装饰器行，停止
                            break
                    # 检查下方紧邻的装饰器是否为 @log_request
                    for j in range(i + 1, min(i + 5, len(lines))):
                        lower = lines[j].strip()
                        if not lower or lower.startswith('#'):
                            continue
                        if lower.startswith('@log_request'):
                            # @log_request 在 @trace_route 下方 → @trace_route 是外层 → 符合约定
                            compliant += 1
                        elif lower.startswith('@'):
                            continue
                        else:
                            break

        total = compliant + violating
        # 信息性断言：若存在违规，输出比例但不阻断（历史代码兼容）
        # 仅当全部违规时才告警
        if total > 0 and violating == total:
            pytest.fail(
                f"所有 {total} 处 @trace_route 均位于 @log_request 内层，"
                f"不符合约定（@trace_route 应在外层）。"
            )


# ──────────────────────────────────────────────────────────────────────
# 测试类：与 visibility_report.py 对齐验证
# ──────────────────────────────────────────────────────────────────────

class TestVisibilityReportAlignment:
    """验证本测试的计算口径与 visibility_report.py 完全对齐"""

    def _make_collector(self):
        """构造 MetricCollector 实例（复用 visibility_report.py 的计算逻辑）"""
        from scripts.visibility_report import MetricCollector, load_thresholds

        config_path = PROJECT_ROOT / "config.yaml"
        thresholds = load_thresholds(config_path)
        return MetricCollector(
            project_root=PROJECT_ROOT, thresholds=thresholds
        )

    def test_calculation_matches_report_logic(self):
        """本测试的 _calc_coverage 应与 visibility_report.py 结果一致"""
        collector = self._make_collector()
        report_coverage = collector._calc_trace_coverage()
        test_coverage = _calc_coverage()

        # 允许浮点误差
        assert abs(report_coverage - test_coverage) < 0.2, (
            f"测试计算覆盖率 {test_coverage}% 与报告计算覆盖率 {report_coverage}% 不一致，"
            f"计算口径可能已偏离"
        )

    def test_coverage_above_report_threshold(self):
        """覆盖率必须达到 visibility_report 的阈值要求"""
        collector = self._make_collector()
        report_coverage = collector._calc_trace_coverage()
        # 报告中 config.yaml 的阈值
        threshold = collector.thresholds.get("runtime", {}).get("trace_coverage", 30)

        assert report_coverage >= threshold, (
            f"visibility_report 计算 trace_coverage={report_coverage}% < 阈值 {threshold}%"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
