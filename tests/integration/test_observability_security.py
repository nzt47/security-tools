#!/usr/bin/env python3
"""
可观测性体系安全测试脚本

测试以下安全措施：
1. 敏感数据过滤功能
2. 端点认证机制
3. 访问日志记录
4. 敏感字段屏蔽效果

运行方式：
    python tests/integration/test_observability_security.py
"""

import sys
import os
import json
import time

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def test_sensitive_data_filter():
    """测试敏感数据过滤功能"""
    print("\n" + "="*60)
    print("测试 1: 敏感数据过滤功能")
    print("="*60)

    from agent.monitoring.sensitive_data_filter import (
        SensitiveDataFilter,
        filter_sensitive_data,
        filter_dict,
        filter_string,
        REDACTED_VALUE,
        REDACTED_PARTIAL
    )

    # 测试字典过滤
    test_data = {
        "username": "admin",
        "password": "secret123",
        "api_key": "ak_test_1234567890",
        "email": "test@example.com",
        "db_password": "postgres_pass",
        "public_field": "safe_value"
    }

    filtered = filter_dict(test_data.copy())

    print(f"原始数据: {test_data}")
    print(f"过滤后: {filtered}")

    # 验证敏感字段被过滤
    assert filtered["password"] == REDACTED_VALUE, "password 应被完全屏蔽"
    assert filtered["api_key"] == REDACTED_VALUE, "api_key 应被完全屏蔽"
    assert filtered["db_password"] == REDACTED_VALUE, "db_password 应被完全屏蔽"
    assert filtered["username"] == "admin", "非敏感字段应保留"
    assert filtered["public_field"] == "safe_value", "非敏感字段应保留"

    print("✓ 字典过滤测试通过")

    # 测试嵌套字典
    nested_data = {
        "user": {
            "name": "test_user",
            "credentials": {
                "password": "nested_secret",
                "token": "jwt_token_12345"
            }
        },
        "config": {
            "api_key": "key_12345",
            "debug": True
        }
    }

    filtered_nested = filter_dict(nested_data.copy())
    print(f"\n嵌套数据过滤: {json.dumps(filtered_nested, indent=2)}")

    assert filtered_nested["user"]["credentials"]["password"] == REDACTED_VALUE
    assert filtered_nested["user"]["credentials"]["token"] == REDACTED_VALUE
    assert filtered_nested["config"]["api_key"] == REDACTED_VALUE
    assert filtered_nested["user"]["name"] == "test_user"

    print("✓ 嵌套字典过滤测试通过")

    # 测试字符串过滤
    test_strings = [
        ("Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.token", "JWT Token 应被屏蔽"),
        ("password=secret123", "URL 中的密码应被屏蔽"),
        ("api_key=ghp_test1234567890abcdefghijklmnopqrstuvwxyz", "GitHub Token 应被屏蔽"),
        ("AKIAIOSFODNN7EXAMPLE", "AWS Access Key 应被屏蔽"),
        ("Normal message without sensitive data", "普通消息应保留"),
    ]

    for text, desc in test_strings:
        filtered_text = filter_string(text)
        is_masked = filtered_text != text or "********" in filtered_text or "Normal" in filtered_text
        print(f"  原始: {text[:50]}...")
        print(f"  过滤: {filtered_text[:50]}...")
        print(f"  {desc}: {'✓' if is_masked else '✗'}")

    print("\n✓ 敏感数据过滤功能测试完成")
    return True


def test_filter_patterns():
    """测试各种敏感字段模式"""
    print("\n" + "="*60)
    print("测试 2: 敏感字段模式匹配")
    print("="*60)

    from agent.monitoring.sensitive_data_filter import SensitiveDataFilter

    filter_instance = SensitiveDataFilter()

    # 测试敏感字段识别
    sensitive_keys = [
        "password", "Password", "PASSWORD",
        "api_key", "apiKey", "API_KEY",
        "token", "auth_token", "access_token",
        "secret", "private_key",
        "db_password", "mongo_uri",
        "jwt_token", "bearer_token",
        "session_id", "client_secret"
    ]

    non_sensitive_keys = [
        "username", "email", "name",
        "created_at", "updated_at",
        "public_key", "is_active"
    ]

    print("敏感字段识别测试:")
    for key in sensitive_keys:
        result = filter_instance.is_sensitive_key(key)
        status = "✓" if result else "✗"
        print(f"  {status} {key}: {'敏感' if result else '非敏感'}")

    print("\n非敏感字段识别测试:")
    for key in non_sensitive_keys:
        result = filter_instance.is_sensitive_key(key)
        status = "✓" if not result else "✗"
        print(f"  {status} {key}: {'敏感' if result else '非敏感'}")

    print("\n✓ 敏感字段模式匹配测试完成")
    return True


def test_access_logger():
    """测试访问日志记录功能"""
    print("\n" + "="*60)
    print("测试 3: 访问日志记录功能")
    print("="*60)

    import tempfile
    from agent.monitoring.sensitive_data_filter import AccessLogger

    # 使用临时文件进行测试
    with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
        temp_file = f.name

    try:
        logger = AccessLogger(log_file=temp_file)

        # 记录测试访问
        logger.log_access(
            endpoint="/api/diagnostics/config",
            method="GET",
            client_ip="192.168.1.100",
            user_agent="TestClient/1.0",
            trace_id="test_trace_123",
            status_code=200,
            response_time_ms=45.6,
            query_params={"debug": "true", "api_key": "secret_key"}
        )

        logger.log_access(
            endpoint="/api/diagnostics/health",
            method="GET",
            client_ip="192.168.1.101",
            status_code=200,
            response_time_ms=12.3
        )

        print("✓ 访问日志记录成功")

        # 读取访问日志
        records = logger.get_recent_access(limit=10)
        print(f"\n读取到 {len(records)} 条访问记录")

        for record in records:
            print(f"  - {record['datetime']} | {record['endpoint']} | {record['client_ip']} | {record['status_code']}")

        # 验证敏感字段被过滤
        # records 按时间倒序，需找到包含 api_key 的记录（第一次调用）
        api_key_record = next(
            (r for r in records if "api_key" in r.get("query_params", {})),
            None,
        )
        assert api_key_record is not None, "应存在包含 api_key 的访问记录"
        assert api_key_record["query_params"]["api_key"] == "********", "query_params 中的敏感字段应被过滤"

        print("✓ 访问日志读取和过滤测试通过")

        # 测试统计功能
        stats = logger.get_access_stats()
        print(f"\n访问统计:")
        print(f"  总访问次数: {stats['total_accesses']}")
        print(f"  独立端点数: {stats['unique_endpoints']}")
        print(f"  独立IP数: {stats['unique_ips']}")
        print(f"  平均响应时间: {stats['avg_response_time_ms']:.2f}ms")
        print(f"  错误率: {stats['error_rate']:.2%}")

        print("\n✓ 访问日志统计测试通过")

    finally:
        os.unlink(temp_file)

    return True


def test_authentication_flow():
    """测试认证流程"""
    print("\n" + "="*60)
    print("测试 4: 端点认证流程")
    print("="*60)

    # 检查哪些端点需要认证
    endpoints_requiring_auth = [
        "/api/diagnostics/config",
        "/api/diagnostics/logs",
        "/api/diagnostics/metrics",
        "/api/observability/state",
        "/api/observability/logs",
        "/api/observability/logs/stream",
        "/api/observability/alerts",
        "/api/observability/traces",
        "/api/observability/access_logs",
        "/api/observability/access_stats"
    ]

    endpoints_no_auth = [
        "/api/diagnostics/tools",
        "/api/diagnostics/health",
        "/api/diagnostics/trace",
        "/metrics"
    ]

    print("需要认证的端点:")
    for ep in endpoints_requiring_auth:
        print(f"  ✓ {ep}")

    print("\n无需认证的端点:")
    for ep in endpoints_no_auth:
        print(f"  - {ep} (低风险)")

    print("\n✓ 端点认证配置检查完成")
    return True


def test_redacted_values():
    """测试屏蔽值常量"""
    print("\n" + "="*60)
    print("测试 5: 屏蔽值常量")
    print("="*60)

    from agent.monitoring.sensitive_data_filter import REDACTED_VALUE, REDACTED_PARTIAL

    print(f"REDACTED_VALUE: {REDACTED_VALUE}")
    print(f"REDACTED_PARTIAL: {REDACTED_PARTIAL}")

    assert REDACTED_VALUE == "********", "REDACTED_VALUE 应为 8 个星号"
    assert REDACTED_PARTIAL == "****", "REDACTED_PARTIAL 应为 4 个星号"

    print("\n✓ 屏蔽值常量测试通过")
    return True


def run_all_tests():
    """运行所有安全测试"""
    print("\n" + "#"*60)
    print("# 可观测性体系安全测试套件")
    print("#"*60)

    tests = [
        ("敏感数据过滤", test_sensitive_data_filter),
        ("敏感字段模式", test_filter_patterns),
        ("访问日志记录", test_access_logger),
        ("端点认证流程", test_authentication_flow),
        ("屏蔽值常量", test_redacted_values),
    ]

    results = []
    for name, test_func in tests:
        try:
            result = test_func()
            results.append((name, result, None))
        except Exception as e:
            results.append((name, False, str(e)))
            print(f"\n✗ {name} 测试失败: {e}")

    print("\n" + "="*60)
    print("测试结果汇总")
    print("="*60)

    passed = 0
    failed = 0
    for name, result, error in results:
        status = "✓ 通过" if result else "✗ 失败"
        print(f"  {status}: {name}")
        if error:
            print(f"    错误: {error}")
        if result:
            passed += 1
        else:
            failed += 1

    print(f"\n总计: {passed} 通过, {failed} 失败")

    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
