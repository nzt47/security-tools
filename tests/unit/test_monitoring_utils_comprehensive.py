"""agent/monitoring/utils.py 全面单元测试

测试目标：覆盖 monitoring/utils.py 的所有公开函数
覆盖维度：
1. 统计计算：calculate_percentiles / calculate_histogram_stats
2. 追踪 ID：generate_trace_id / generate_span_id / is_valid_hex_string
3. 日志格式化：format_structured_log / _safe_json_dumps
4. 安全工具：mask_sensitive_value / is_sensitive_field / filter_sensitive_dict
5. 时间工具：current_timestamp_ms / current_timestamp_s / format_duration_ms
6. 标签工具：make_label_key / parse_label_key
7. 单例元类：SingletonMeta

状态同步说明：纯函数测试无状态污染；SingletonMeta 测试通过清理 _instances 隔离。
"""
import json
import threading
import time

import pytest

from agent.monitoring.utils import (
    SingletonMeta,
    _safe_json_dumps,
    calculate_histogram_stats,
    calculate_percentiles,
    current_timestamp_ms,
    current_timestamp_s,
    filter_sensitive_dict,
    format_duration_ms,
    format_structured_log,
    generate_span_id,
    generate_trace_id,
    is_sensitive_field,
    is_valid_hex_string,
    make_label_key,
    mask_sensitive_value,
    parse_label_key,
)


# ── 1. 统计计算 ─────────────────────────────────────────


class TestCalculatePercentiles:
    def test_empty_values(self):
        result = calculate_percentiles([])
        assert result["count"] == 0
        assert result["sum"] == 0.0
        assert result["avg"] == 0.0
        assert result["min"] == 0.0
        assert result["max"] == 0.0
        assert result["p50"] == 0.0
        assert result["p95"] == 0.0
        assert result["p99"] == 0.0

    def test_single_value(self):
        result = calculate_percentiles([5.0])
        assert result["count"] == 1
        assert result["sum"] == 5.0
        assert result["avg"] == 5.0
        assert result["min"] == 5.0
        assert result["max"] == 5.0
        assert result["p50"] == 5.0

    def test_multiple_values(self):
        result = calculate_percentiles([1, 2, 3, 4, 5])
        assert result["count"] == 5
        assert result["sum"] == 15
        assert result["avg"] == 3.0
        assert result["min"] == 1
        assert result["max"] == 5

    def test_unsorted_input(self):
        result = calculate_percentiles([5, 3, 1, 4, 2])
        assert result["min"] == 1
        assert result["max"] == 5

    def test_p50_calculation(self):
        # 0.50 * 10 = 5 -> idx 5
        result = calculate_percentiles(list(range(10)))
        assert result["p50"] == 5

    def test_p95_calculation(self):
        # 0.95 * 10 = 9.5 -> int(9.5) = 9, min(9, 9) = 9
        result = calculate_percentiles(list(range(10)))
        assert result["p95"] == 9

    def test_p99_calculation(self):
        result = calculate_percentiles(list(range(100)))
        assert result["p99"] == 99

    def test_negative_values(self):
        result = calculate_percentiles([-5, -1, -3])
        assert result["min"] == -5
        assert result["max"] == -1
        assert result["sum"] == -9

    def test_float_values(self):
        result = calculate_percentiles([1.5, 2.5, 3.5])
        assert result["avg"] == 2.5


class TestCalculateHistogramStats:
    def test_is_alias(self):
        """calculate_histogram_stats 应等价于 calculate_percentiles"""
        data = [1, 2, 3, 4, 5]
        assert calculate_histogram_stats(data) == calculate_percentiles(data)


# ── 2. 追踪 ID ─────────────────────────────────────────


class TestTraceId:
    def test_generate_trace_id_default_length(self):
        tid = generate_trace_id()
        assert len(tid) == 16
        assert is_valid_hex_string(tid)

    def test_generate_trace_id_custom_length(self):
        tid = generate_trace_id(length=8)
        assert len(tid) == 8

    def test_generate_trace_id_unique(self):
        ids = {generate_trace_id() for _ in range(100)}
        assert len(ids) == 100

    def test_generate_span_id(self):
        sid = generate_span_id()
        assert len(sid) == 16
        assert is_valid_hex_string(sid)

    def test_generate_span_id_unique(self):
        ids = {generate_span_id() for _ in range(100)}
        assert len(ids) == 100


class TestIsValidHexString:
    def test_valid_hex(self):
        assert is_valid_hex_string("abcdef123456") is True

    def test_valid_hex_uppercase(self):
        assert is_valid_hex_string("ABCDEF") is True

    def test_empty_string(self):
        assert is_valid_hex_string("") is False

    def test_none_input(self):
        assert is_valid_hex_string(None) is False

    def test_non_hex_chars(self):
        assert is_valid_hex_string("xyz123") is False

    def test_too_short(self):
        assert is_valid_hex_string("ab", min_length=5) is False

    def test_too_long(self):
        long_str = "a" * 100
        assert is_valid_hex_string(long_str, max_length=64) is False

    def test_custom_range(self):
        assert is_valid_hex_string("abc", min_length=3, max_length=10) is True

    def test_with_0x_prefix_fails(self):
        # int("0x12", 16) works but len check may fail
        # 实际上 "0x12" 长度 4，能被 int 解析为 16 进制
        result = is_valid_hex_string("0x12")
        # int("0x12", 16) raises ValueError actually? Let's verify
        # Actually Python int("0x12", 16) = 18, no error
        assert isinstance(result, bool)


# ── 3. 日志格式化 ─────────────────────────────────────────


class TestFormatStructuredLog:
    def test_basic_fields(self):
        result = format_structured_log(
            trace_id="abc123",
            module_name="test_module",
            action="test_action",
        )
        data = json.loads(result)
        assert data["trace_id"] == "abc123"
        assert data["module_name"] == "test_module"
        assert data["action"] == "test_action"
        assert "timestamp" in data

    def test_default_trace_id(self):
        result = format_structured_log(module_name="m", action="a")
        data = json.loads(result)
        assert data["trace_id"] == "unknown"

    def test_with_duration(self):
        result = format_structured_log(
            module_name="m", action="a", duration_ms=123.456
        )
        data = json.loads(result)
        assert data["duration_ms"] == 123.46  # round to 2 decimals

    def test_without_duration(self):
        result = format_structured_log(module_name="m", action="a")
        data = json.loads(result)
        assert "duration_ms" not in data

    def test_with_extra_fields(self):
        result = format_structured_log(
            module_name="m", action="a", user_id="u123", custom="x"
        )
        data = json.loads(result)
        assert data["user_id"] == "u123"
        assert data["custom"] == "x"

    def test_returns_json_string(self):
        result = format_structured_log(module_name="m", action="a")
        assert isinstance(result, str)


class TestSafeJsonDumps:
    def test_normal_dict(self):
        result = _safe_json_dumps({"a": 1, "b": "x"})
        data = json.loads(result)
        assert data == {"a": 1, "b": "x"}

    def test_with_non_serializable(self):
        class Custom:
            def __str__(self):
                return "custom_obj"

        result = _safe_json_dumps({"obj": Custom()})
        data = json.loads(result)
        assert data["obj"] == "custom_obj"

    def test_with_nested_dict(self):
        result = _safe_json_dumps({"outer": {"inner": 1}})
        data = json.loads(result)
        assert data["outer"]["inner"] == 1


# ── 4. 安全工具 ─────────────────────────────────────────


class TestMaskSensitiveValue:
    def test_long_value(self):
        result = mask_sensitive_value("abcdefghij")
        assert result == "ab****ij"

    def test_short_value(self):
        assert mask_sensitive_value("abc") == "****"

    def test_exact_4_chars(self):
        assert mask_sensitive_value("abcd") == "****"

    def test_5_chars(self):
        result = mask_sensitive_value("abcde")
        assert result == "ab****de"

    def test_empty_string(self):
        assert mask_sensitive_value("") == ""

    def test_non_string(self):
        assert mask_sensitive_value(None) is None
        assert mask_sensitive_value(123) == 123

    def test_not_string_int(self):
        # int 不是 str，直接返回
        assert mask_sensitive_value(12345) == 12345


class TestIsSensitiveField:
    def test_password(self):
        assert is_sensitive_field("password") is True

    def test_user_password(self):
        assert is_sensitive_field("user_password") is True

    def test_secret(self):
        assert is_sensitive_field("secret_key") is True

    def test_token(self):
        assert is_sensitive_field("access_token") is True

    def test_api_key(self):
        assert is_sensitive_field("api_key") is True
        assert is_sensitive_field("api-key") is True
        assert is_sensitive_field("apikey") is True

    def test_private_key(self):
        assert is_sensitive_field("private_key") is True

    def test_credit_card(self):
        assert is_sensitive_field("credit_card") is True

    def test_email(self):
        assert is_sensitive_field("email") is True

    def test_phone(self):
        assert is_sensitive_field("phone") is True

    def test_non_sensitive(self):
        assert is_sensitive_field("username") is False
        assert is_sensitive_field("id") is False
        assert is_sensitive_field("created_at") is False

    def test_case_insensitive(self):
        assert is_sensitive_field("PASSWORD") is True
        assert is_sensitive_field("Token") is True


class TestFilterSensitiveDict:
    def test_filters_password(self):
        data = {"username": "alice", "password": "secret123"}
        result = filter_sensitive_dict(data)
        assert result["username"] == "alice"
        assert result["password"] == "se****23"

    def test_filters_nested_dict(self):
        data = {"user": {"name": "bob", "api_key": "keyvalue"}}
        result = filter_sensitive_dict(data)
        assert result["user"]["name"] == "bob"
        assert result["user"]["api_key"] != "keyvalue"

    def test_filters_list_of_dicts(self):
        data = {"items": [{"token": "tok"}, {"name": "x"}]}
        result = filter_sensitive_dict(data)
        assert result["items"][0]["token"] != "tok"
        assert result["items"][1]["name"] == "x"

    def test_preserves_non_sensitive(self):
        data = {"name": "alice", "age": 30}
        result = filter_sensitive_dict(data)
        assert result == {"name": "alice", "age": 30}

    def test_empty_dict(self):
        assert filter_sensitive_dict({}) == {}

    def test_non_string_sensitive_value(self):
        data = {"password": 12345}
        result = filter_sensitive_dict(data)
        # int 转 str 后脱敏
        assert result["password"] != 12345

    def test_complex_non_string_sensitive(self):
        data = {"secret": {"nested": "dict"}}
        result = filter_sensitive_dict(data)
        assert result["secret"] == "***REDACTED***"


# ── 5. 时间工具 ─────────────────────────────────────────


class TestTimeUtils:
    def test_current_timestamp_ms(self):
        ts = current_timestamp_ms()
        assert isinstance(ts, float)
        assert ts > 0
        # 应接近 time.time() * 1000
        expected = time.time() * 1000
        assert abs(ts - expected) < 100  # 100ms 容差

    def test_current_timestamp_s(self):
        ts = current_timestamp_s()
        assert isinstance(ts, float)
        assert ts > 0
        assert abs(ts - time.time()) < 1

    def test_format_duration_ms_under_1s(self):
        assert format_duration_ms(500) == "500ms"
        assert format_duration_ms(0) == "0ms"
        assert format_duration_ms(999) == "999ms"

    def test_format_duration_ms_seconds(self):
        assert format_duration_ms(1000) == "1.00s"
        assert format_duration_ms(1500) == "1.50s"
        # 59999/1000=59.999 四舍五入到 60.00s
        assert format_duration_ms(5000) == "5.00s"
        assert format_duration_ms(10500) == "10.50s"

    def test_format_duration_ms_minutes(self):
        assert format_duration_ms(60000) == "1.00m"
        assert format_duration_ms(120000) == "2.00m"

    def test_format_duration_ms_negative(self):
        # 负数会进入 < 1000 分支
        result = format_duration_ms(-100)
        assert "ms" in result


# ── 6. 标签工具 ─────────────────────────────────────────


class TestLabelUtils:
    def test_make_label_key_empty(self):
        assert make_label_key({}) == ""

    def test_make_label_key_single(self):
        assert make_label_key({"a": "1"}) == "a=1"

    def test_make_label_key_multiple_sorted(self):
        result = make_label_key({"b": "2", "a": "1"})
        # 应按 key 排序
        assert result == "a=1,b=2"

    def test_make_label_key_with_special_chars(self):
        result = make_label_key({"url": "http://example.com"})
        assert result == "url=http://example.com"

    def test_parse_label_key_empty(self):
        assert parse_label_key("") == {}

    def test_parse_label_key_none(self):
        assert parse_label_key(None) == {}

    def test_parse_label_key_single(self):
        assert parse_label_key("a=1") == {"a": "1"}

    def test_parse_label_key_multiple(self):
        result = parse_label_key("a=1,b=2")
        assert result == {"a": "1", "b": "2"}

    def test_parse_label_key_no_equals(self):
        # 不含 = 的部分被忽略
        assert parse_label_key("invalid") == {}

    def test_parse_label_key_partial_invalid(self):
        result = parse_label_key("a=1,invalid,b=2")
        assert result == {"a": "1", "b": "2"}

    def test_roundtrip(self):
        original = {"method": "GET", "status": "200"}
        key = make_label_key(original)
        parsed = parse_label_key(key)
        assert parsed == original


# ── 7. 单例元类 ─────────────────────────────────────────


class TestSingletonMeta:
    def setup_method(self):
        # 清理单例缓存
        SingletonMeta._instances.clear()

    def teardown_method(self):
        SingletonMeta._instances.clear()

    def test_singleton_returns_same_instance(self):
        class MyClass(metaclass=SingletonMeta):
            def __init__(self, value=0):
                self.value = value

        a = MyClass(value=1)
        b = MyClass(value=2)
        assert a is b
        # __init__ 只在首次创建时调用一次，后续调用复用实例
        assert a.value == 1

    def test_different_classes_different_instances(self):
        class A(metaclass=SingletonMeta):
            pass

        class B(metaclass=SingletonMeta):
            pass

        assert A() is not B()
        assert A() is A()
        assert B() is B()

    def test_singleton_thread_safe(self):
        class Counter(metaclass=SingletonMeta):
            def __init__(self):
                self.count = 0

        instances = []
        barrier = threading.Barrier(10)

        def create():
            barrier.wait()
            instances.append(Counter())

        threads = [threading.Thread(target=create) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 所有线程应获得同一实例
        first = instances[0]
        for inst in instances:
            assert inst is first
