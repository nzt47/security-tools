"""
本地测试脚本：验证日志级别动态配置

[用途] 模拟不同 LOG_LEVEL 环境变量，验证 cicd_metrics_push.py 的日志级别过滤。
[不易] 不依赖真实 pushgateway，通过 mock + StringIO 捕获日志输出。
[运行] python scripts/test_log_level_dynamic.py
[覆盖] 6 个测试用例：
  1. LOG_LEVEL=DEBUG：所有日志输出
  2. LOG_LEVEL=INFO（默认）：INFO 日志输出
  3. LOG_LEVEL=WARNING：INFO 日志不输出
  4. LOG_LEVEL=ERROR：仅 ERROR 日志输出
  5. 无效 LOG_LEVEL=FOO：降级为 INFO
  6. LOG_LEVEL 未设置：默认 INFO
"""

from __future__ import annotations

import logging
import os
import sys
from io import StringIO
from unittest.mock import patch

# 确保能导入 cicd_metrics_push（同目录）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cicd_metrics_push  # noqa: E402


def setup_logger(level_name: str) -> StringIO:
    """配置 logger 到指定级别，返回捕获缓冲。

    [不易] 模拟 cicd_metrics_push.py main() 中的日志级别解析逻辑，
    确保测试与生产代码行为一致。
    """
    log_capture = StringIO()
    handler = logging.StreamHandler(log_capture)
    handler.setLevel(logging.DEBUG)  # handler 捕获所有级别，由 logger.setLevel 过滤

    logger = cicd_metrics_push.logger
    logger.handlers = [handler]

    # 复刻 main() 中的级别解析逻辑
    level_name_upper = level_name.upper()
    level = getattr(logging, level_name_upper, logging.INFO)
    if not isinstance(level, int):
        level = logging.INFO
    logger.setLevel(level)

    return log_capture


def count_lines(output: str, keyword: str) -> int:
    """统计包含关键字的日志行数。"""
    return len([line for line in output.split("\n") if keyword in line])


def test_debug_level():
    """测试 1：LOG_LEVEL=DEBUG，所有日志输出。"""
    print("\n[测试 1] LOG_LEVEL=DEBUG")
    os.environ["LOG_LEVEL"] = "DEBUG"
    log_capture = setup_logger("DEBUG")

    with patch("prometheus_client.pushadd_to_gateway"):
        cicd_metrics_push.push("ci-cd-test")

    output = log_capture.getvalue()
    start = count_lines(output, "推送开始")
    param = count_lines(output, "推送参数")
    success = count_lines(output, "推送成功")

    print(f"  推送开始(INFO): {start}, 推送参数(INFO): {param}, 推送成功(INFO): {success}")
    assert start == 1, "DEBUG 级别应输出推送开始"
    assert param == 1, "DEBUG 级别应输出推送参数"
    assert success == 1, "DEBUG 级别应输出推送成功"
    print("  ✅ 通过：DEBUG 级别输出所有 INFO 日志")


def test_info_level():
    """测试 2：LOG_LEVEL=INFO（默认），INFO 日志输出。"""
    print("\n[测试 2] LOG_LEVEL=INFO（默认）")
    os.environ["LOG_LEVEL"] = "INFO"
    log_capture = setup_logger("INFO")

    with patch("prometheus_client.pushadd_to_gateway"):
        cicd_metrics_push.push("ci-cd-test")

    output = log_capture.getvalue()
    start = count_lines(output, "推送开始")
    success = count_lines(output, "推送成功")

    print(f"  推送开始(INFO): {start}, 推送成功(INFO): {success}")
    assert start == 1, "INFO 级别应输出推送开始"
    assert success == 1, "INFO 级别应输出推送成功"
    print("  ✅ 通过：INFO 级别输出 INFO 日志")


def test_warning_level():
    """测试 3：LOG_LEVEL=WARNING，INFO 日志不输出。"""
    print("\n[测试 3] LOG_LEVEL=WARNING")
    os.environ["LOG_LEVEL"] = "WARNING"
    log_capture = setup_logger("WARNING")

    with patch("prometheus_client.pushadd_to_gateway"):
        cicd_metrics_push.push("ci-cd-test")

    output = log_capture.getvalue()
    start = count_lines(output, "推送开始")
    success = count_lines(output, "推送成功")

    print(f"  推送开始(INFO): {start}, 推送成功(INFO): {success}")
    assert start == 0, "WARNING 级别不应输出 INFO 日志"
    assert success == 0, "WARNING 级别不应输出 INFO 日志"
    print("  ✅ 通过：WARNING 级别过滤 INFO 日志")


def test_error_level():
    """测试 4：LOG_LEVEL=ERROR，仅 ERROR 日志输出。"""
    print("\n[测试 4] LOG_LEVEL=ERROR")
    os.environ["LOG_LEVEL"] = "ERROR"
    log_capture = setup_logger("ERROR")

    # mock pushadd 抛异常，触发 ERROR 日志
    with patch("prometheus_client.pushadd_to_gateway",
               side_effect=ConnectionError("测试错误")):
        cicd_metrics_push.push("ci-cd-test")

    output = log_capture.getvalue()
    start = count_lines(output, "推送开始")
    error = count_lines(output, "推送失败")

    print(f"  推送开始(INFO): {start}, 推送失败(ERROR): {error}")
    assert start == 0, "ERROR 级别不应输出 INFO 日志"
    assert error == 1, "ERROR 级别应输出 ERROR 日志"
    print("  ✅ 通过：ERROR 级别仅输出 ERROR 日志")


def test_invalid_level_fallback():
    """测试 5：无效 LOG_LEVEL=FOO，降级为 INFO。"""
    print("\n[测试 5] LOG_LEVEL=FOO（无效）")
    os.environ["LOG_LEVEL"] = "FOO"
    log_capture = setup_logger("FOO")

    with patch("prometheus_client.pushadd_to_gateway"):
        cicd_metrics_push.push("ci-cd-test")

    output = log_capture.getvalue()
    start = count_lines(output, "推送开始")

    print(f"  推送开始(INFO): {start}")
    assert start == 1, "无效级别应降级为 INFO，输出 INFO 日志"
    print("  ✅ 通过：无效级别降级为 INFO")


def test_no_env_default():
    """测试 6：LOG_LEVEL 未设置，默认 INFO。"""
    print("\n[测试 6] LOG_LEVEL 未设置")
    os.environ.pop("LOG_LEVEL", None)

    # 模拟未设置时的默认行为
    level_name = os.environ.get("LOG_LEVEL", "INFO")
    log_capture = setup_logger(level_name)

    with patch("prometheus_client.pushadd_to_gateway"):
        cicd_metrics_push.push("ci-cd-test")

    output = log_capture.getvalue()
    start = count_lines(output, "推送开始")

    print(f"  推送开始(INFO): {start}")
    assert start == 1, "未设置时应默认 INFO，输出 INFO 日志"
    print("  ✅ 通过：未设置默认 INFO")


def main():
    """运行所有测试。"""
    print("=" * 60)
    print("日志级别动态配置验证测试")
    print("=" * 60)

    tests = [
        test_debug_level,
        test_info_level,
        test_warning_level,
        test_error_level,
        test_invalid_level_fallback,
        test_no_env_default,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"  ❌ 失败: {e}")
            failed += 1

    print("\n" + "=" * 60)
    print(f"测试结果: {passed} 通过, {failed} 失败")
    print("=" * 60)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
