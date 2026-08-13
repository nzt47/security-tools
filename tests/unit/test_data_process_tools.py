"""数据处理工具单元测试 — 覆盖 JSONPath 查询、JSON/YAML 转换、JSON 校验与格式检测。

对应 agent/data_process_tools.py：
- _parse_jsonpath / _recursive_descent_search / _walk_jsonpath 内部 JSONPath 引擎
- json_query / json_to_yaml / yaml_to_json / json_validate 公开接口
- _is_xml / _is_yaml / _is_csv / data_format_detect 格式检测

Why mock：yaml 模块在函数内延迟导入，通过 patch("yaml.dump"/"yaml.safe_load")
或临时替换 sys.modules["yaml"] 模拟缺库/异常场景；json 解析异常通过 patch
json.loads 触发，全程不触碰网络。
"""
# pylint: disable=redefined-outer-name,missing-function-docstring,protected-access

import csv
import json as json_mod
import sys
from contextlib import contextmanager

import pytest
from unittest.mock import patch

import yaml

from agent import data_process_tools as dp


@contextmanager
def _hide_yaml():
    """临时将 sys.modules['yaml'] 置 None，模拟 pyyaml 未安装（import 抛 ImportError）"""
    saved = sys.modules.get("yaml")
    sys.modules["yaml"] = None
    try:
        yield
    finally:
        if saved is None:
            sys.modules.pop("yaml", None)
        else:
            sys.modules["yaml"] = saved


class TestParseJsonpath:
    """_parse_jsonpath() JSONPath 表达式解析"""

    def test_root_only(self):
        """单独的 $ 解析为根节点"""
        assert dp._parse_jsonpath("$") == [("root", None)]

    def test_dot_path(self):
        """点记法逐级解析"""
        assert dp._parse_jsonpath("$.a.b") == [
            ("root", None), ("dot", "a"), ("dot", "b"),
        ]

    def test_index(self):
        """数组索引 [n] 解析为 index token"""
        assert dp._parse_jsonpath("$.a[0]") == [
            ("root", None), ("dot", "a"), ("index", 0),
        ]

    def test_wildcard(self):
        """数组通配 [*] 解析为 wildcard token"""
        assert dp._parse_jsonpath("$.a[*]") == [
            ("root", None), ("dot", "a"), ("wildcard", None),
        ]

    def test_recursive_descent(self):
        """递归下降 ..key 解析为 recursive_descent token"""
        assert dp._parse_jsonpath("$..a") == [
            ("root", None), ("recursive_descent", "a"),
        ]

    def test_mixed_path(self):
        """混合语法组合解析"""
        assert dp._parse_jsonpath("$.items[*].name") == [
            ("root", None), ("dot", "items"), ("wildcard", None), ("dot", "name"),
        ]

    def test_multi_index(self):
        """多维数组索引解析"""
        assert dp._parse_jsonpath("$[0][1]") == [
            ("root", None), ("index", 0), ("index", 1),
        ]

    def test_implicit_root(self):
        """$ 后直接跟键名时自动前置 root token"""
        assert dp._parse_jsonpath("$a") == [("root", None), ("dot", "a")]

    def test_not_start_with_dollar(self):
        """不以 $ 开头的表达式抛 ValueError"""
        with pytest.raises(ValueError, match="必须以 \\$ 开头"):
            dp._parse_jsonpath("a.b")

    def test_empty_path(self):
        """空表达式抛 ValueError"""
        with pytest.raises(ValueError, match="必须以 \\$ 开头"):
            dp._parse_jsonpath("")

    def test_dot_missing_key(self):
        """'.' 后缺少键名抛 ValueError"""
        with pytest.raises(ValueError, match=r"JSONPath 中 '\.' 后缺少键名"):
            dp._parse_jsonpath("$.")

    def test_ddot_missing_key(self):
        """'..' 后缺少键名抛 ValueError"""
        with pytest.raises(ValueError, match="'\\.\\.' 后缺少键名"):
            dp._parse_jsonpath("$..")

    def test_invalid_syntax(self):
        """无法匹配的语法抛 ValueError"""
        with pytest.raises(ValueError, match="JSONPath 解析失败"):
            dp._parse_jsonpath("$.a[")


class TestRecursiveDescentSearch:
    """_recursive_descent_search() 递归搜索"""

    def test_find_in_nested_dict(self):
        """在嵌套 dict 中找到所有同名键值"""
        data = {"a": {"b": 1}, "c": {"b": 2}}
        assert dp._recursive_descent_search(data, "b") == [1, 2]

    def test_find_in_list(self):
        """在 list 嵌套中递归查找"""
        data = [{"x": 1}, {"x": 2}, {"y": 3}]
        assert dp._recursive_descent_search(data, "x") == [1, 2]

    def test_not_found(self):
        """键不存在时返回空列表"""
        assert dp._recursive_descent_search({"a": 1}, "b") == []


class TestWalkJsonpath:
    """_walk_jsonpath() token 遍历执行"""

    def test_empty_tokens_returns_data(self):
        """空 token 列表返回原始数据"""
        assert dp._walk_jsonpath({"a": 1}, []) == [{"a": 1}]

    def test_root_token_only(self):
        """仅 root token 返回原始数据"""
        assert dp._walk_jsonpath([1, 2], [("root", None)]) == [[1, 2]]

    def test_dot_walk(self):
        """dot token 逐级访问"""
        data = {"a": {"b": 1}}
        tokens = [("root", None), ("dot", "a"), ("dot", "b")]
        assert dp._walk_jsonpath(data, tokens) == [1]

    def test_index_out_of_range(self):
        """索引越界时返回空列表"""
        tokens = [("root", None), ("index", 5)]
        assert dp._walk_jsonpath([1, 2], tokens) == []

    def test_wildcard_list(self):
        """通配符展开 list 全部元素"""
        tokens = [("root", None), ("wildcard", None)]
        assert dp._walk_jsonpath([[1], [2]], tokens) == [[1], [2]]

    def test_wildcard_dict(self):
        """通配符展开 dict 全部值"""
        tokens = [("root", None), ("wildcard", None)]
        assert dp._walk_jsonpath({"a": 1, "b": 2}, tokens) == [1, 2]

    def test_none_item_skipped(self):
        """当前集合中的 None 项在后续访问中被跳过"""
        data = [{"x": 1}, None, {"x": 3}]
        tokens = [("root", None), ("wildcard", None), ("dot", "x")]
        assert dp._walk_jsonpath(data, tokens) == [1, 3]


class TestJsonQuery:
    """json_query() 公开查询接口"""

    def test_query_string_json(self):
        """从 JSON 字符串查询点路径"""
        result = dp.json_query('{"store":{"book":[{"title":"A"}]}}', "$.store.book[0].title")
        assert result["ok"] is True
        assert result["data"] == ["A"]
        assert result["count"] == 1

    def test_query_python_dict(self):
        """直接传入 dict 查询"""
        result = dp.json_query({"a": {"b": 1}}, "$.a.b")
        assert result["ok"] is True
        assert result["data"] == [1]

    def test_query_root(self):
        """$ 返回整份数据"""
        data = {"a": 1}
        result = dp.json_query(data, "$")
        assert result["ok"] is True
        assert result["data"] == [data]
        assert result["count"] == 1

    def test_query_recursive_descent(self):
        """递归下降查询所有同名键"""
        result = dp.json_query({"a": {"b": 1}, "c": {"b": 2}}, "$..b")
        assert result["ok"] is True
        assert result["data"] == [1, 2]
        assert result["count"] == 2

    def test_query_wildcard(self):
        """通配符 + 属性组合查询"""
        result = dp.json_query({"items": [{"n": 1}, {"n": 2}]}, "$.items[*].n")
        assert result["ok"] is True
        assert result["data"] == [1, 2]

    def test_query_no_match(self):
        """路径匹配不到返回空列表"""
        result = dp.json_query({"a": 1}, "$.zzz")
        assert result["ok"] is True
        assert result["data"] == []
        assert result["count"] == 0

    def test_query_invalid_json(self):
        """字符串不是合法 JSON 时返回解析失败"""
        result = dp.json_query("{not json", "$.a")
        assert result["ok"] is False
        assert "JSON 解析失败" in result["error"]

    def test_query_wrong_type(self):
        """数据不是对象/数组时返回类型错误"""
        result = dp.json_query(42, "$.a")
        assert result["ok"] is False
        assert "数据必须是 JSON 对象或数组" in result["error"]

    def test_query_bad_path(self):
        """JSONPath 语法错误返回语法错误"""
        result = dp.json_query({"a": 1}, "$.")
        assert result["ok"] is False
        assert "JSONPath 语法错误" in result["error"]

    def test_query_generic_exception(self):
        """内部异常被捕获并返回查询失败"""
        with patch("agent.data_process_tools._walk_jsonpath",
                   side_effect=RuntimeError("boom")):
            result = dp.json_query({"a": 1}, "$.a")
        assert result["ok"] is False
        assert "查询失败" in result["error"]


class TestJsonToYaml:
    """json_to_yaml() JSON → YAML 转换"""

    def test_convert_object(self):
        """对象转换成功且内容为合法 YAML"""
        result = dp.json_to_yaml('{"a": 1, "b": [1, 2]}')
        assert result["ok"] is True
        parsed = yaml.safe_load(result["data"])
        assert parsed == {"a": 1, "b": [1, 2]}

    def test_convert_array(self):
        """数组转换成功"""
        result = dp.json_to_yaml('[1, 2, 3]')
        assert result["ok"] is True
        assert yaml.safe_load(result["data"]) == [1, 2, 3]

    def test_not_string(self):
        """非字符串输入返回类型错误"""
        result = dp.json_to_yaml({"a": 1})
        assert result["ok"] is False
        assert "数据必须是字符串" in result["error"]

    def test_empty(self):
        """空字符串返回数据为空"""
        result = dp.json_to_yaml("   ")
        assert result["ok"] is False
        assert "JSON 数据为空" in result["error"]

    def test_invalid_json(self):
        """非法 JSON 返回解析失败"""
        result = dp.json_to_yaml("{bad")
        assert result["ok"] is False
        assert "JSON 解析失败" in result["error"]

    def test_missing_pyyaml(self):
        """pyyaml 缺失时返回库未安装"""
        with _hide_yaml():
            result = dp.json_to_yaml('{"a": 1}')
        assert result["ok"] is False
        assert "pyyaml 库未安装" in result["error"]

    def test_dump_error(self):
        """yaml.dump 异常返回转换失败"""
        with patch("yaml.dump", side_effect=RuntimeError("boom")):
            result = dp.json_to_yaml('{"a": 1}')
        assert result["ok"] is False
        assert "转换失败" in result["error"]


class TestYamlToJson:
    """yaml_to_json() YAML → JSON 转换"""

    def test_convert_object(self):
        """对象转换成功且内容为合法 JSON"""
        result = dp.yaml_to_json("a: 1\nb:\n  - 1\n  - 2\n")
        assert result["ok"] is True
        assert json_mod.loads(result["data"]) == {"a": 1, "b": [1, 2]}

    def test_convert_scalar(self):
        """简单标量（数字）转换成功"""
        result = dp.yaml_to_json("42")
        assert result["ok"] is True
        assert json_mod.loads(result["data"]) == 42

    def test_not_string(self):
        """非字符串输入返回类型错误"""
        result = dp.yaml_to_json({"a": 1})
        assert result["ok"] is False
        assert "数据必须是字符串" in result["error"]

    def test_empty(self):
        """空字符串返回数据为空"""
        result = dp.yaml_to_json("\n\n")
        assert result["ok"] is False
        assert "YAML 数据为空" in result["error"]

    def test_yaml_error(self):
        """YAML 解析异常返回解析失败"""
        with patch("yaml.safe_load", side_effect=yaml.YAMLError("bad yaml")):
            result = dp.yaml_to_json("a: 1")
        assert result["ok"] is False
        assert "YAML 解析失败" in result["error"]

    def test_none_result(self):
        """解析结果为 None（空文档）返回错误"""
        result = dp.yaml_to_json("~")
        assert result["ok"] is False
        assert "YAML 解析结果为空" in result["error"]

    def test_unsupported_type(self):
        """解析结果为不支持类型（set）返回错误"""
        with patch("yaml.safe_load", return_value={"a", "b"}):
            result = dp.yaml_to_json("a: 1")
        assert result["ok"] is False
        assert "YAML 解析结果类型不支持" in result["error"]

    def test_missing_pyyaml(self):
        """pyyaml 缺失时返回库未安装"""
        with _hide_yaml():
            result = dp.yaml_to_json("a: 1")
        assert result["ok"] is False
        assert "pyyaml 库未安装" in result["error"]

    def test_dumps_error(self):
        """json.dumps 异常返回转换失败"""
        with patch("agent.data_process_tools.json.dumps",
                   side_effect=RuntimeError("boom")):
            result = dp.yaml_to_json("a: 1")
        assert result["ok"] is False
        assert "转换失败" in result["error"]


class TestJsonValidate:
    """json_validate() JSON 校验"""

    def test_validate_object(self):
        """对象类型校验"""
        result = dp.json_validate('{"a": 1}')
        assert result["ok"] is True
        assert result["valid"] is True
        assert result["parsed_type"] == "object"
        assert result["keys_count"] == 1

    def test_validate_array(self):
        """数组类型校验"""
        result = dp.json_validate("[1, 2]")
        assert result["ok"] is True
        assert result["parsed_type"] == "array"
        assert result["keys_count"] == 2

    def test_validate_string(self):
        """字符串字面量校验"""
        result = dp.json_validate('"hi"')
        assert result["ok"] is True
        assert result["parsed_type"] == "string"

    def test_validate_boolean(self):
        """布尔类型校验（bool 在 int 之前判定）"""
        result = dp.json_validate("true")
        assert result["ok"] is True
        assert result["parsed_type"] == "boolean"

    def test_validate_number(self):
        """数字类型校验"""
        result = dp.json_validate("3.14")
        assert result["ok"] is True
        assert result["parsed_type"] == "number"

    def test_validate_null(self):
        """null 校验"""
        result = dp.json_validate("null")
        assert result["ok"] is True
        assert result["parsed_type"] == "null"

    def test_validate_not_string(self):
        """非字符串输入返回错误"""
        result = dp.json_validate(42)
        assert result["ok"] is False
        assert "数据必须是字符串" in result["error"]

    def test_validate_empty(self):
        """空字符串判定为无效"""
        result = dp.json_validate("  ")
        assert result["ok"] is True
        assert result["valid"] is False
        assert "数据为空字符串" in result["error"]

    def test_validate_invalid(self):
        """非法 JSON 判定为无效并返回错误"""
        result = dp.json_validate("{oops")
        assert result["ok"] is True
        assert result["valid"] is False
        assert "JSON 格式无效" in result["error"]

    def test_validate_generic_exception(self):
        """json.loads 其他异常返回验证失败"""
        with patch("agent.data_process_tools.json.loads",
                   side_effect=RuntimeError("boom")):
            result = dp.json_validate('{"a": 1}')
        assert result["ok"] is False
        assert "验证失败" in result["error"]


class TestIsXml:
    """_is_xml() XML 置信度检测"""

    def test_empty(self):
        """空字符串置信度为 0"""
        assert dp._is_xml("") == 0.0

    def test_xml_declaration(self):
        """XML 声明获得高置信度"""
        assert dp._is_xml("<?xml version='1.0'?>") == 0.95

    def test_root_element(self):
        """根元素配对模式获得中高置信度"""
        assert dp._is_xml("<root><child>1</child></root>") == 0.85

    def test_plain_text(self):
        """纯文本置信度为 0"""
        assert dp._is_xml("just plain text") == 0.0


class TestIsYaml:
    """_is_yaml() YAML 置信度检测"""

    def test_empty(self):
        """空字符串返回 (0.0, None)"""
        assert dp._is_yaml("") == (0.0, None)

    def test_dict(self):
        """结构化 dict 获高置信度并返回解析对象"""
        conf, obj = dp._is_yaml("a: 1\nb: 2\n")
        assert conf == 0.90
        assert obj == {"a": 1, "b": 2}

    def test_number(self):
        """非结构化标量（数字）置信度 0.60"""
        conf, obj = dp._is_yaml("42")
        assert conf == 0.60
        assert obj == 42

    def test_long_string(self):
        """长字符串很可能是普通文本，置信度 0.40"""
        conf, obj = dp._is_yaml("x" * 30)
        assert conf == 0.40
        assert isinstance(obj, str)

    def test_short_string(self):
        """短字符串可能是 YAML 简单值，置信度 0.70"""
        conf, obj = dp._is_yaml("hello")
        assert conf == 0.70
        assert obj == "hello"

    def test_none_doc(self):
        """空 YAML 文档（~）置信度 0.10"""
        conf, obj = dp._is_yaml("~")
        assert conf == 0.10
        assert obj is None

    def test_safe_load_error(self):
        """safe_load 抛异常时返回 (0.0, None)"""
        with patch("yaml.safe_load", side_effect=RuntimeError("boom")):
            conf, obj = dp._is_yaml("a: 1")
        assert (conf, obj) == (0.0, None)


class TestIsCsv:
    """_is_csv() CSV 置信度检测"""

    def test_empty(self):
        """空字符串置信度为 0"""
        assert dp._is_csv("") == 0.0

    def test_no_separator(self):
        """无逗号且无换行时置信度为 0"""
        assert dp._is_csv("single line text") == 0.0

    def test_sniff_ok(self):
        """Sniffer 识别成功且有多行数据时置信度 0.85"""
        assert dp._is_csv("a,b\nc,d") == 0.85

    def test_sniff_ok_no_rows(self):
        """Sniffer 识别成功但解析出 0 行时置信度 0.50"""

        class _EmptySniffer:
            def sniff(self, sample, delimiters=None):
                return "excel"

        with patch("agent.data_process_tools.csv.Sniffer", _EmptySniffer), \
             patch("agent.data_process_tools.csv.reader", return_value=iter([])):
            assert dp._is_csv("a,b\nc,d") == 0.50

    def test_sniff_fail_fallback(self):
        """Sniffer 失败时回退到逗号一致性检查"""

        class _BadSniffer:
            def sniff(self, sample, delimiters=None):
                raise csv.Error("no dialect")

        with patch("agent.data_process_tools.csv.Sniffer", _BadSniffer):
            assert dp._is_csv("a,b\nc,d") == 0.60

    def test_sniff_other_exception(self):
        """Sniffer 抛出非 csv.Error 异常时置信度为 0"""

        class _RaisingSniffer:
            def sniff(self, sample, delimiters=None):
                raise RuntimeError("boom")

        with patch("agent.data_process_tools.csv.Sniffer", _RaisingSniffer):
            assert dp._is_csv("a\nb") == 0.0

    def test_fallback_inconsistent(self):
        """回退检查中各行逗号数差异过大时置信度为 0"""

        class _BadSniffer:
            def sniff(self, sample, delimiters=None):
                raise csv.Error("no dialect")

        with patch("agent.data_process_tools.csv.Sniffer", _BadSniffer):
            assert dp._is_csv("a,b,c\nx,y\nz") == 0.0


class TestDataFormatDetect:
    """data_format_detect() 格式自动检测"""

    def test_detect_json_object(self):
        """JSON 对象识别为 json"""
        result = dp.data_format_detect('{"a": 1}')
        assert result["ok"] is True
        assert result["format"] == "json"
        assert result["confidence"] == 0.95

    def test_detect_json_array(self):
        """JSON 数组识别为 json"""
        result = dp.data_format_detect("[1, 2, 3]")
        assert result["format"] == "json"

    def test_detect_json_string_literal(self):
        """较短 JSON 字符串字面量识别为 json（与 yaml 平分时优先 json）"""
        result = dp.data_format_detect('"abc"')
        assert result["format"] == "json"
        assert result["confidence"] == 0.7

    def test_detect_xml(self):
        """XML 文档识别为 xml"""
        result = dp.data_format_detect("<root><child>1</child></root>")
        assert result["format"] == "xml"
        assert result["confidence"] == 0.85

    def test_detect_yaml(self):
        """YAML 映射识别为 yaml"""
        result = dp.data_format_detect("a: 1\nb: 2\n")
        assert result["format"] == "yaml"
        assert result["confidence"] == 0.9

    def test_detect_yaml_number(self):
        """纯数字输入被识别为 yaml（yaml 0.60 > json 0.50）"""
        result = dp.data_format_detect("42")
        assert result["format"] == "yaml"
        assert result["confidence"] == 0.6

    def test_detect_csv(self):
        """CSV 表格识别为 csv"""
        result = dp.data_format_detect("a,b\nc,d")
        assert result["format"] == "csv"
        assert result["confidence"] == 0.85

    def test_detect_unknown(self):
        """无法识别的文本返回 unknown 及全部分数"""
        result = dp.data_format_detect("a: b: c")
        assert result["ok"] is True
        assert result["format"] == "unknown"
        assert result["confidence"] == 0.0
        assert result["scores"]["json"] == 0.0

    def test_detect_empty(self):
        """空字符串返回 unknown（数据为空）"""
        result = dp.data_format_detect("   ")
        assert result["ok"] is True
        assert result["format"] == "unknown"
        assert "数据为空" in result["details"]

    def test_detect_not_string(self):
        """非字符串输入返回错误"""
        result = dp.data_format_detect(123)
        assert result["ok"] is False
        assert "数据必须是字符串" in result["error"]

    def test_detect_generic_exception(self):
        """内部检测异常被捕获并返回格式检测失败"""
        with patch("agent.data_process_tools._is_xml",
                   side_effect=RuntimeError("boom")):
            result = dp.data_format_detect("some data")
        assert result["ok"] is False
        assert "格式检测失败" in result["error"]
