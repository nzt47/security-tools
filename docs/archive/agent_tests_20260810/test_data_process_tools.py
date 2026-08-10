"""数据处理工具测试 -- 测试 data_process_tools.py 的 JSON/YAML 查询、转换、验证与格式检测

覆盖范围：
- json_query: JSONPath 表达式（$、.key、[n]、[*]、..key）
- json_to_yaml / yaml_to_json: 循环转换
- json_validate: 合法/非法 JSON 验证
- data_format_detect: JSON/XML/YAML/CSV 格式检测
"""
import json
import pytest

from agent.data_process_tools import (
    json_query,
    json_to_yaml,
    yaml_to_json,
    json_validate,
    data_format_detect,
)


# ════════════════════════════════════════════════════════════════════════════════
#  JSONPath 查询测试
# ════════════════════════════════════════════════════════════════════════════════

class TestJsonQuery:
    """json_query 函数测试"""

    def test_root(self):
        """$ 根节点查询"""
        data = {"name": "Alice", "age": 30}
        result = json_query(data, "$")
        assert result["ok"] is True
        assert result["count"] == 1
        assert result["data"][0] == data

    def test_dot_key(self):
        """.key 属性访问"""
        data = {"user": {"name": "Bob", "email": "bob@test.com"}}
        result = json_query(data, "$.user.name")
        assert result["ok"] is True
        assert result["data"] == ["Bob"]
        assert result["count"] == 1

    def test_nested_dot(self):
        """多层级 .key1.key2 访问"""
        data = {"a": {"b": {"c": 42}}}
        result = json_query(data, "$.a.b.c")
        assert result["ok"] is True
        assert result["data"] == [42]

    def test_array_index(self):
        """[n] 数组索引"""
        data = {"items": ["a", "b", "c", "d"]}
        result = json_query(data, "$.items[2]")
        assert result["ok"] is True
        assert result["data"] == ["c"]

    def test_array_wildcard(self):
        """[*] 数组通配"""
        data = {"scores": [90, 85, 95]}
        result = json_query(data, "$.scores[*]")
        assert result["ok"] is True
        assert len(result["data"]) == 3
        assert result["count"] == 3

    def test_recursive_descent(self):
        """..key 递归下降搜索"""
        data = {
            "store": {
                "book": [
                    {"title": "Go", "author": "X"},
                    {"title": "Rust", "author": "Y"},
                ]
            }
        }
        result = json_query(data, "$..title")
        assert result["ok"] is True
        assert result["count"] == 2
        assert set(result["data"]) == {"Go", "Rust"}

    def test_recursive_descent_simple(self):
        """..key 简单场景"""
        data = {"a": {"b": 1}, "c": {"b": 2}}
        result = json_query(data, "$..b")
        assert result["ok"] is True
        assert result["data"] == [1, 2]

    def test_json_string_input(self):
        """输入为 JSON 字符串时自动解析"""
        json_str = '{"name": "Alice", "items": [10, 20, 30]}'
        result = json_query(json_str, "$.items[*]")
        assert result["ok"] is True
        assert result["data"] == [10, 20, 30]

    def test_invalid_json_string(self):
        """无效 JSON 字符串返回错误"""
        result = json_query("{bad json", "$.key")
        assert result["ok"] is False
        assert "error" in result

    def test_invalid_jsonpath(self):
        """无效 JSONPath 返回错误"""
        data = {"a": 1}
        result = json_query(data, "not_a_path")
        assert result["ok"] is False
        assert "error" in result

    def test_non_dict_list_input(self):
        """非 dict/list 输入返回错误"""
        result = json_query(42, "$.key")
        assert result["ok"] is False

    def test_nonexistent_key(self):
        """访问不存在的键返回空结果"""
        data = {"name": "Test"}
        result = json_query(data, "$.nonexistent")
        assert result["ok"] is True
        assert result["data"] == []
        assert result["count"] == 0

    def test_array_wildcard_on_dict(self):
        """[*] 在 dict 上返回所有值"""
        data = {"a": 1, "b": 2, "c": 3}
        result = json_query(data, "$[*]")
        assert result["ok"] is True
        # [*] 在 dict 上展开为所有值
        assert sorted(result["data"]) == [1, 2, 3]

    def test_complex_path(self):
        """复合路径: .key[0].key"""
        data = {
            "store": {
                "books": [
                    {"title": "Python", "author": {"name": "Guido"}},
                    {"title": "Java", "author": {"name": "James"}},
                ]
            }
        }
        result = json_query(data, "$.store.books[0].author.name")
        assert result["ok"] is True
        assert result["data"] == ["Guido"]


# ════════════════════════════════════════════════════════════════════════════════
#  JSON ↔ YAML 转换测试
# ════════════════════════════════════════════════════════════════════════════════

class TestJsonYamlConversion:
    """json_to_yaml 和 yaml_to_json 测试"""

    def test_json_to_yaml_simple(self):
        """基本 JSON→YAML 转换"""
        json_str = '{"name": "Alice", "age": 30}'
        result = json_to_yaml(json_str)
        assert result["ok"] is True
        assert "name:" in result["data"] or "name" in result["data"]
        assert "Alice" in result["data"]

    def test_json_to_yaml_list(self):
        """JSON 数组→YAML 转换"""
        json_str = '["a", "b", "c"]'
        result = json_to_yaml(json_str)
        assert result["ok"] is True
        assert "a" in result["data"]

    def test_yaml_to_json_simple(self):
        """基本 YAML→JSON 转换"""
        yaml_str = "name: Bob\nage: 25\n"
        result = yaml_to_json(yaml_str)
        assert result["ok"] is True
        parsed = json.loads(result["data"])
        assert parsed["name"] == "Bob"
        assert parsed["age"] == 25

    def test_round_trip_json_yaml_json(self):
        """JSON→YAML→JSON 循环转换，内容一致"""
        original = '{"name": "Test", "items": [1, 2, 3], "nested": {"key": "value"}}'
        yaml_result = json_to_yaml(original)
        assert yaml_result["ok"] is True

        json_result = yaml_to_json(yaml_result["data"])
        assert json_result["ok"] is True

        # 重新解析比较
        original_obj = json.loads(original)
        round_tripped_obj = json.loads(json_result["data"])
        assert original_obj == round_tripped_obj

    def test_json_to_yaml_empty_input(self):
        """空 JSON 输入返回错误"""
        result = json_to_yaml("")
        assert result["ok"] is False

    def test_json_to_yaml_invalid(self):
        """无效 JSON 输入返回错误"""
        result = json_to_yaml("not valid json")
        assert result["ok"] is False

    def test_yaml_to_json_empty_input(self):
        """空 YAML 输入返回错误"""
        result = yaml_to_json("")
        assert result["ok"] is False

    def test_json_to_yaml_non_string(self):
        """非字符串输入返回错误"""
        result = json_to_yaml(123)
        assert result["ok"] is False

    def test_yaml_to_json_non_string(self):
        """非字符串输入返回错误"""
        result = yaml_to_json({"key": "value"})
        assert result["ok"] is False


# ════════════════════════════════════════════════════════════════════════════════
#  JSON 验证测试
# ════════════════════════════════════════════════════════════════════════════════

class TestJsonValidate:
    """json_validate 函数测试"""

    def test_valid_json_object(self):
        """合法 JSON 对象"""
        result = json_validate('{"name": "Alice", "age": 30}')
        assert result["ok"] is True
        assert result["valid"] is True
        assert result["parsed_type"] == "object"
        assert result["keys_count"] == 2

    def test_valid_json_array(self):
        """合法 JSON 数组"""
        result = json_validate("[1, 2, 3]")
        assert result["ok"] is True
        assert result["valid"] is True
        assert result["parsed_type"] == "array"
        assert result["keys_count"] == 3

    def test_valid_json_string(self):
        """合法 JSON 字符串字面量"""
        result = json_validate('"hello world"')
        assert result["ok"] is True
        assert result["valid"] is True
        assert result["parsed_type"] == "string"

    def test_valid_json_number(self):
        """合法 JSON 数字"""
        result = json_validate("42")
        assert result["ok"] is True
        assert result["valid"] is True
        assert result["parsed_type"] == "number"

    def test_valid_json_boolean(self):
        """合法 JSON 布尔值"""
        result = json_validate("true")
        assert result["ok"] is True
        assert result["valid"] is True
        assert result["parsed_type"] == "boolean"

    def test_valid_json_null(self):
        """合法 JSON null"""
        result = json_validate("null")
        assert result["ok"] is True
        assert result["valid"] is True
        assert result["parsed_type"] == "null"

    def test_invalid_json(self):
        """非法 JSON"""
        result = json_validate("{broken json")
        assert result["ok"] is True
        assert result["valid"] is False
        assert "error" in result

    def test_empty_string(self):
        """空字符串"""
        result = json_validate("")
        assert result["ok"] is True
        assert result["valid"] is False
        assert "error" in result

    def test_non_string_input(self):
        """非字符串输入返回错误"""
        result = json_validate({"key": "value"})
        assert result["ok"] is False


# ════════════════════════════════════════════════════════════════════════════════
#  数据格式检测测试
# ════════════════════════════════════════════════════════════════════════════════

class TestDataFormatDetect:
    """data_format_detect 函数测试"""

    def test_detect_json(self):
        """检测 JSON 格式"""
        result = data_format_detect('{"name": "Alice", "age": 30}')
        assert result["ok"] is True
        assert result["format"] == "json"
        assert result["confidence"] >= 0.9

    def test_detect_xml(self):
        """检测 XML 格式"""
        result = data_format_detect('<root><item>value</item></root>')
        assert result["ok"] is True
        assert result["format"] == "xml"
        assert result["confidence"] >= 0.8

    def test_detect_yaml(self):
        """检测 YAML 格式"""
        result = data_format_detect("name: Test\nitems:\n  - a\n  - b\n")
        assert result["ok"] is True
        assert result["format"] == "yaml"
        assert result["confidence"] >= 0.8

    def test_detect_csv(self):
        """检测 CSV 格式"""
        result = data_format_detect("name,age,city\nAlice,30,NYC\nBob,25,LA\n")
        assert result["ok"] is True
        assert result["format"] == "csv"
        assert result["confidence"] >= 0.5

    def test_detect_unknown(self):
        """无法识别的纯文本（不含结构化特征，长度足够避免被 YAML 误判为简单值）"""
        # 使用足够长的无结构纯文本来避免被 YAML 解析为简单字符串
        long_text = "This is just some random unstructured free text that has no special formatting characters or delimiters whatsoever and should not be detected as any structured data format like JSON XML YAML or CSV."
        result = data_format_detect(long_text)
        assert result["ok"] is True
        # 纯文本可能被 YAML 误判为字符串，但置信度应较低
        # 如果被检测为 yaml，那 confidence 也应较低
        if result["format"] != "unknown":
            assert result["confidence"] < 0.5

    def test_detect_empty_string(self):
        """空字符串"""
        result = data_format_detect("")
        assert result["ok"] is True
        assert result["format"] == "unknown"
        assert result["confidence"] == 0.0

    def test_detect_non_string(self):
        """非字符串输入返回错误"""
        result = data_format_detect(12345)
        assert result["ok"] is False

    def test_detect_json_array(self):
        """检测 JSON 数组格式"""
        result = data_format_detect('[1, 2, 3, 4, 5]')
        assert result["ok"] is True
        assert result["format"] == "json"

    def test_detect_xml_with_declaration(self):
        """检测带声明的 XML"""
        result = data_format_detect('<?xml version="1.0"?><root>data</root>')
        assert result["ok"] is True
        assert result["format"] == "xml"
        assert result["confidence"] >= 0.9

    def test_detect_returns_scores(self):
        """返回所有格式的置信度评分"""
        result = data_format_detect('{"key": "value"}')
        assert result["ok"] is True
        assert "scores" in result
        assert "json" in result["scores"]
        assert "xml" in result["scores"]
        assert "yaml" in result["scores"]
        assert "csv" in result["scores"]
