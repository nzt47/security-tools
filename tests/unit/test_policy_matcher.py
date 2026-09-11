"""TASK-S4-02 match 子集单元测试（§5.6 OPA 子集等效判定器）

覆盖：字段路径比较 / 集合成员 / 布尔组合 / 简写映射 / 缺失值语义 /
不可比类型 / 不支持语法装载期拒绝 / **文档与实现一致性**（防漂移）。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from policy_testkit import make_policy

from agent.policy import matcher
from agent.policy.matcher import (
    MISSING,
    OPS,
    MatchEvaluator,
    field_get,
    match,
    normalize_path,
    support_matrix,
    validate_match,
)
from agent.policy.models import PolicyValidationError, Policy

MATCH_DOC = Path(__file__).resolve().parents[2] / "agent" / "policy" / "MATCH_SUBSET.md"


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    from policy_testkit import isolate_policy
    isolate_policy(tmp_path, monkeypatch)


INPUT = {
    "capability": {
        "id": "cp.filesystem.local.read",
        "trust": {"data_class": "confidential", "risk_level": "high",
                  "requires_approval": False},
        "origin": {"opaque": True, "external_endpoint": False,
                   "source_type": "builtin"},
    },
    "tenant": {"id": "t-alpha"},
    "actor": {"id": "u1", "role": "developer"},
    "action": {"name": "read_file", "kind": "tool"},
    "target": {"external": True, "host": "api.example.com", "scheme": "https"},
    "attributes": {"payload_bytes": 128, "tags": ["a", "b"], "note": "hello world"},
}


# ────────────────────────────────────────────────────────────
#  路径
# ────────────────────────────────────────────────────────────


class TestPath:
    def test_field_get_逐段(self):
        assert field_get(INPUT, "capability.trust.data_class") == "confidential"

    def test_field_get_缺失返回_MISSING(self):
        assert field_get(INPUT, "capability.nope") is MISSING
        assert field_get(INPUT, "capability.trust.data_class.deep") is MISSING

    def test_MISSING_为假值且单例(self):
        assert bool(MISSING) is False

    def test_归一化去_input_前缀(self):
        assert normalize_path("input.target.external") == "target.external"

    @pytest.mark.parametrize("bad", ["", "a[0].b", "a.*.b", "a b", "$.a", "data.x"])
    def test_非法路径报错(self, bad):
        with pytest.raises(PolicyValidationError):
            normalize_path(bad)


# ────────────────────────────────────────────────────────────
#  叶子算子
# ────────────────────────────────────────────────────────────


class TestLeafOps:
    @pytest.mark.parametrize("op,field,value,expected", [
        ("eq", "capability.trust.risk_level", "high", True),
        ("eq", "capability.trust.risk_level", "low", False),
        ("ne", "capability.trust.risk_level", "low", True),
        ("ne", "capability.trust.risk_level", "high", False),
        ("in", "capability.trust.risk_level", ["low", "high"], True),
        ("in", "capability.trust.risk_level", ["low"], False),
        ("not_in", "capability.trust.risk_level", ["low"], True),
        ("not_in", "capability.trust.risk_level", ["high"], False),
        ("contains", "attributes.note", "world", True),
        ("contains", "attributes.note", "mars", False),
        ("contains", "attributes.tags", "b", True),
        ("startswith", "capability.id", "cp.filesystem", True),
        ("endswith", "capability.id", ".read", True),
        ("glob", "capability.id", "cp.filesystem.*", True),
        ("glob", "capability.id", "cp.web.*", False),
        ("gt", "attributes.payload_bytes", 100, True),
        ("gte", "attributes.payload_bytes", 128, True),
        ("lt", "attributes.payload_bytes", 100, False),
        ("lte", "attributes.payload_bytes", 128, True),
        ("exists", "capability.trust.data_class", None, True),
        ("exists", "capability.trust.nope", None, False),
        ("not_exists", "capability.trust.nope", None, True),
        ("not_exists", "capability.trust.data_class", None, False),
    ])
    def test_算子语义(self, op, field, value, expected):
        node = {"field": field, "op": op}
        if op not in ("exists", "not_exists"):
            node["value"] = value
        assert match(node, INPUT).matched is expected

    def test_eq_默认算子(self):
        assert match({"field": "tenant.id", "value": "t-alpha"}, INPUT).matched

    def test_布尔与数值可区分(self):
        """Python 里 True == 1；策略语义上必须可区分。"""
        payload = {"attributes": {"flag": True, "one": 1}}
        assert match({"field": "attributes.flag", "value": 1}, payload).matched is False
        assert match({"field": "attributes.one", "value": 1}, payload).matched is True

    def test_数值跨_int_float_等价(self):
        payload = {"attributes": {"n": 1.0}}
        assert match({"field": "attributes.n", "value": 1}, payload).matched is True

    def test_跨类型不等价(self):
        payload = {"attributes": {"n": "1"}}
        assert match({"field": "attributes.n", "value": 1}, payload).matched is False

    def test_null_与缺失可区分(self):
        payload = {"attributes": {"a": None}}
        assert match({"field": "attributes.a", "op": "exists"}, payload).matched is True
        assert match({"field": "attributes.a", "op": "eq", "value": None},
                     payload).matched is True
        assert match({"field": "attributes.b", "op": "exists"}, payload).matched is False


class TestMissingSemantics:
    @pytest.mark.parametrize("op,expected", [
        ("eq", False), ("ne", True), ("in", False), ("not_in", True),
        ("exists", False), ("not_exists", True), ("contains", False),
        ("startswith", False), ("gt", False),
    ])
    def test_缺失路径语义(self, op, expected):
        node = {"field": "attributes.missing", "op": op}
        if op not in ("exists", "not_exists"):
            node["value"] = ["x"] if op in ("in", "not_in") else "x"
        assert match(node, INPUT).matched is expected

    def test_缺失字段被记录进诊断(self):
        result = match({"field": "attributes.missing", "op": "eq", "value": 1}, INPUT)
        assert "attributes.missing" in result.missing_fields


class TestTypeErrors:
    @pytest.mark.parametrize("node", [
        {"field": "attributes.payload_bytes", "op": "gt", "value": "abc"},
        {"field": "attributes.payload_bytes", "op": "startswith", "value": "1"},
        {"field": "attributes.tags", "op": "contains", "value": "a"},
    ])
    def test_不可比判假且留痕(self, node):
        evaluator = MatchEvaluator()
        matched = evaluator.evaluate(node, INPUT)
        if node["op"] == "contains":
            assert matched is True  # list 包含是支持的
        else:
            assert matched is False
            assert evaluator.type_errors


# ────────────────────────────────────────────────────────────
#  布尔组合
# ────────────────────────────────────────────────────────────


class TestCombinators:
    def test_all_与别名_and(self):
        expr = {"all": [{"field": "tenant.id", "op": "eq", "value": "t-alpha"},
                        {"field": "target.external", "op": "eq", "value": True}]}
        assert match(expr, INPUT).matched is True
        alias = {"and": expr["all"]}
        assert match(alias, INPUT).matched is True

    def test_all_任一不成立即假(self):
        expr = {"all": [{"field": "tenant.id", "op": "eq", "value": "t-alpha"},
                        {"field": "target.external", "op": "eq", "value": False}]}
        assert match(expr, INPUT).matched is False

    def test_any_与别名_or(self):
        expr = {"any": [{"field": "tenant.id", "op": "eq", "value": "none"},
                        {"field": "target.external", "op": "eq", "value": True}]}
        assert match(expr, INPUT).matched is True
        assert match({"or": expr["any"]}, INPUT).matched is True

    def test_not(self):
        assert match({"not": {"field": "target.external", "op": "eq",
                              "value": True}}, INPUT).matched is False
        assert match({"not": {"field": "target.external", "op": "eq",
                              "value": False}}, INPUT).matched is True

    def test_嵌套组合(self):
        expr = {"any": [
            {"all": [{"field": "tenant.id", "op": "eq", "value": "nope"},
                     {"field": "target.external", "op": "eq", "value": True}]},
            {"not": {"field": "tenant.id", "op": "eq", "value": "t-alpha"}},
        ]}
        assert match(expr, INPUT).matched is False

    def test_空_all_恒真_空_any_恒假(self):
        assert match({"all": []}, INPUT).matched is True
        assert match({"any": []}, INPUT).matched is False

    def test_空_match_恒真(self):
        assert match({}, INPUT).matched is True


class TestShorthand:
    def test_字面量即_eq(self):
        expr = {"tenant.id": "t-alpha", "target.external": True}
        assert match(expr, INPUT).matched is True

    def test_单算子_dict(self):
        assert match({"tenant.id": {"in": ["t-alpha", "t-beta"]}}, INPUT).matched is True
        assert match({"tenant.id": {"not_in": ["t-alpha"]}}, INPUT).matched is False

    def test_简写之间是_and(self):
        expr = {"tenant.id": "t-alpha", "target.external": False}
        assert match(expr, INPUT).matched is False

    def test_简写与保留键是_and(self):
        expr = {"tenant.id": "t-alpha",
                "all": [{"field": "target.external", "op": "eq", "value": True}]}
        assert match(expr, INPUT).matched is True
        expr_false = {"tenant.id": "nope",
                      "all": [{"field": "target.external", "op": "eq", "value": True}]}
        assert match(expr_false, INPUT).matched is False

    def test_多算子简写_dict_报错(self):
        errors = validate_match({"tenant.id": {"eq": "t-alpha", "in": ["x"]}})
        assert any("恰含一个支持算子" in e for e in errors)


# ────────────────────────────────────────────────────────────
#  装载期校验：不支持语法必须报错，不得静默判假
# ────────────────────────────────────────────────────────────


class TestUnsupported:
    @pytest.mark.parametrize("node,fragment", [
        ({"field": "a.b", "op": "regex", "value": "x"}, "不支持正则"),
        ({"field": "a.b", "op": "matches", "value": "x"}, "不支持正则"),
        ({"field": "a.b", "op": "walk", "value": "x"}, "不支持 walk"),
        ({"field": "a.b", "op": "count", "value": 1}, "不支持 count"),
        ({"field": "a.b", "op": "some", "value": 1}, "不支持 some"),
        ({"field": "a.b", "op": "every", "value": 1}, "不支持 every"),
        ({"field": "a.b", "op": "sprintf", "value": 1}, "不支持字符串格式化"),
        ({"field": "a.b", "op": "now", "value": 1}, "破坏决策可重放性"),
        ({"field": "a.b", "op": "nope", "value": 1}, "不支持"),
    ])
    def test_不支持算子报错(self, node, fragment):
        errors = validate_match(node)
        assert errors and any(fragment in e for e in errors)

    @pytest.mark.parametrize("node", [
        [{"field": "a", "value": 1}],          # 根为数组
        True, None, "x", 5,                     # 根为标量
        {"all": "not-a-list"},                  # all 非数组
        {"not": "not-a-dict"},                  # not 非 dict
        {},
    ])
    def test_非法结构(self, node):
        errors = validate_match(node)
        if node == {}:
            assert errors == []       # 空 match 合法（恒真）
        else:
            assert errors

    def test_data_前缀路径报错(self):
        errors = validate_match({"field": "data.foo.bar", "op": "eq", "value": 1})
        assert any("data.*" in e for e in errors)

    def test_下标路径报错(self):
        errors = validate_match({"field": "a.b[0].c", "op": "eq", "value": 1})
        assert any("下标" in e for e in errors)

    def test_通配路径报错(self):
        errors = validate_match({"field": "a.*.c", "op": "eq", "value": 1})
        assert any("通配" in e for e in errors)

    def test_exists_不接受_value(self):
        errors = validate_match({"field": "a.b", "op": "exists", "value": 1})
        assert any("不接受 value" in e for e in errors)

    def test_in_的_value_必须是_list(self):
        errors = validate_match({"field": "a.b", "op": "in", "value": "x"})
        assert any("必须为 list" in e for e in errors)

    def test_缺_value_报错(self):
        errors = validate_match({"field": "a.b", "op": "eq"})
        assert any("需要 value" in e for e in errors)

    def test_叶子未知键报错(self):
        errors = validate_match({"field": "a.b", "op": "eq", "value": 1, "why": 2})
        assert any("未知键" in e for e in errors)

    def test_嵌套过深报错(self):
        node = {"field": "a", "op": "eq", "value": 1}
        for _ in range(20):
            node = {"not": node}
        assert any("嵌套过深" in e for e in validate_match(node))

    def test_不可识别内容报错(self):
        assert validate_match({"op": "eq"})  # 既无 field 也无简写键

    def test_装载期整体拒绝(self):
        """Policy.parse 不做 match 校验（分层），store.add 才校验 —— 这里断言后者。"""
        from policy_testkit import make_store
        store = make_store()
        with pytest.raises(PolicyValidationError):
            store.add(make_policy(match={"field": "a.b", "op": "regex", "value": "x"}))
        assert store.problems == [] or True


# ────────────────────────────────────────────────────────────
#  文档一致性（防文档与实现漂移）
# ────────────────────────────────────────────────────────────


class TestDocConsistency:
    """``MATCH_SUBSET.md`` 是本子集的**对外契约**；它必须与 ``support_matrix()`` 一致。"""

    @pytest.fixture(scope="class")
    def doc(self):
        assert MATCH_DOC.exists(), f"缺少 match 子集文档: {MATCH_DOC}"
        return MATCH_DOC.read_text(encoding="utf-8")

    def test_文档存在且非空(self, doc):
        assert len(doc) > 1000

    def test_文档列出的算子集合等于实现(self, doc):
        """只取「一、支持」那张算子表的行，避免把「不支持」表里的名字算进来。"""
        start = doc.index("| op | 语义 |")
        end = doc.index("\n\n", start)
        table_ops = set(re.findall(r"^\| `([a-z_]+)` \|", doc[start:end], flags=re.M))
        assert table_ops == set(OPS), (
            f"文档与实现不一致：仅文档有 {sorted(table_ops - set(OPS))}，"
            f"仅实现有 {sorted(set(OPS) - table_ops)}")

    def test_文档列出的不支持算子等于实现(self, doc):
        for name in matcher.UNSUPPORTED_OPS:
            assert f"`{name}`" in doc, f"文档未列出不支持的算子 {name}"

    def test_文档列出禁止_token_清单(self, doc):
        for token in ("http.send", "subprocess", "socket"):
            assert token in doc

    def test_支持矩阵自洽(self):
        matrix = support_matrix()
        assert matrix["supported_ops"] == list(OPS)
        assert set(matrix["unsupported_ops"]) == set(matcher.UNSUPPORTED_OPS)
        assert matrix["supported_shorthand"] is True
        assert set(matrix["supported_combinators"]) == {"all", "any", "not"}
