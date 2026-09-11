"""TASK-S5-02 判定器单测（`agent/eval/checkers.py`）

覆盖：取值路径解析/写入、**每一个机械判定器**的正负例、代理判定器的降级语义、
以及"路径不可解析 → 判负（而不是跳过）"这一 fail-closed 纪律。
"""

from __future__ import annotations

import pytest

from agent.eval import checkers as K

# ════════════════════════════════════════════════════════════
#  取值路径
# ════════════════════════════════════════════════════════════


class TestResolvePath:
    def test_nested_and_index(self):
        assert K.resolve_path({"a": {"b": [1, 2]}}, "a.b[1]") == (True, 2)

    def test_empty_path_returns_root(self):
        assert K.resolve_path({"a": 1}, "") == (True, {"a": 1})

    def test_missing_key(self):
        assert K.resolve_path({"a": 1}, "b") == (False, None)

    def test_index_out_of_range(self):
        assert K.resolve_path({"a": [1]}, "a[5]") == (False, None)

    def test_projection_over_list(self):
        found, value = K.resolve_path({"items": [{"n": 1}, {"n": 2}]}, "items.n")
        assert found and value == [1, 2]

    def test_projection_missing_key_fails(self):
        assert K.resolve_path({"items": [{"n": 1}, {"x": 2}]}, "items.n") == (False, None)

    def test_bad_index_token(self):
        with pytest.raises(K.CheckerError):
            K.resolve_path({"a": [1]}, "a[x]")

    def test_set_path_creates_intermediate(self):
        target = {}
        assert K.set_path(target, "a.b[0]", 7)
        assert target == {"a": {"b": [7]}}

    def test_set_path_existing_index(self):
        target = {"a": [1, 2]}
        assert K.set_path(target, "a[1]", 9)
        assert target["a"] == [1, 9]

    def test_set_path_extends_list_and_overwrites_scalar(self):
        """负样本对照要能"写到任何地方"：越界下标按需扩展、标量可被覆盖"""
        target = {"a": [1]}
        assert K.set_path(target, "a[3]", 9)
        assert target["a"][3] == 9
        assert K.set_path(target, "a", "replaced")
        assert target["a"] == "replaced"

    def test_set_path_rejects_non_container_root(self):
        assert K.set_path(5, "a", 1) is False
        assert K.set_path({"a": 1}, "", 1) is False

    def test_delete_path(self):
        target = {"a": {"b": 1, "c": 2}}
        assert K.delete_path(target, "a.b")
        assert target == {"a": {"c": 2}}
        assert K.delete_path(target, "a.zzz") is False


# ════════════════════════════════════════════════════════════
#  机械判定器
# ════════════════════════════════════════════════════════════


class TestEqualityCheckers:
    def test_equals_type_sensitive_for_bool(self):
        assert K.check_equals(True, {"value": True}, K.CheckContext())[0]
        assert not K.check_equals(1, {"value": True}, K.CheckContext())[0]
        assert not K.check_equals(True, {"value": 1}, K.CheckContext())[0]

    def test_equals_numeric_cross_type(self):
        assert K.check_equals(2, {"value": 2.0}, K.CheckContext())[0]

    def test_one_of(self):
        assert K.check_one_of("b", {"values": ["a", "b"]}, K.CheckContext())[0]
        assert not K.check_one_of("c", {"values": ["a", "b"]}, K.CheckContext())[0]

    def test_one_of_requires_values(self):
        with pytest.raises(K.CheckerError):
            K.check_one_of("a", {}, K.CheckContext())

    def test_not_one_of(self):
        assert K.check_not_one_of("c", {"values": ["a"]}, K.CheckContext())[0]
        assert not K.check_not_one_of("a", {"values": ["a"]}, K.CheckContext())[0]


class TestTextCheckers:
    def test_contains_and_ignore_case(self):
        assert K.check_contains("Hello", {"text": "ell"}, K.CheckContext())[0]
        assert K.check_contains("Hello", {"text": "hello", "ignore_case": True},
                                K.CheckContext())[0]

    def test_contains_all_reports_missing(self):
        passed, detail = K.check_contains_all("abc", {"texts": ["a", "z"]}, K.CheckContext())
        assert not passed and "z" in detail

    def test_contains_any(self):
        assert K.check_contains_any("abc", {"texts": ["z", "b"]}, K.CheckContext())[0]
        assert not K.check_contains_any("abc", {"texts": ["z"]}, K.CheckContext())[0]

    def test_not_contains(self):
        assert K.check_not_contains("safe", {"texts": ["--force"]}, K.CheckContext())[0]
        assert not K.check_not_contains("run --force", {"texts": ["--force"]},
                                        K.CheckContext())[0]

    def test_contains_on_non_string_uses_json(self):
        assert K.check_contains({"k": "v"}, {"text": '"k"'}, K.CheckContext())[0]

    def test_regex_fullmatch_vs_search(self):
        assert K.check_regex("fix(a): b", {"pattern": "fix.*"}, K.CheckContext())[0]
        assert not K.check_regex("x fix(a): b", {"pattern": "fix.*"}, K.CheckContext())[0]
        assert K.check_regex("x fix(a): b", {"pattern": "fix.*", "search": True},
                             K.CheckContext())[0]

    def test_regex_bad_pattern(self):
        with pytest.raises(K.CheckerError):
            K.check_regex("a", {"pattern": "("}, K.CheckContext())


class TestStructuredCheckers:
    def test_json_subset_ok(self):
        passed, _ = K.check_json_subset({"a": {"b": 1, "c": 2}},
                                        {"subset": {"a": {"b": 1}}}, K.CheckContext())
        assert passed

    def test_json_subset_missing_leaf(self):
        passed, detail = K.check_json_subset({"a": {"b": 1}},
                                             {"subset": {"a": {"c": 2}}}, K.CheckContext())
        assert not passed and "c" in detail

    def test_json_subset_list_length_must_match(self):
        assert not K.check_json_subset([1, 2], {"subset": [1]}, K.CheckContext())[0]

    def test_list_set_equals_ignores_order(self):
        assert K.check_list_set_equals(["b", "a"], {"values": ["a", "b"]},
                                       K.CheckContext())[0]
        assert not K.check_list_set_equals(["a"], {"values": ["a", "b"]},
                                           K.CheckContext())[0]

    def test_list_ordered_is_order_sensitive(self):
        assert K.check_list_ordered(["a", "b"], {"values": ["a", "b"]}, K.CheckContext())[0]
        assert not K.check_list_ordered(["b", "a"], {"values": ["a", "b"]},
                                        K.CheckContext())[0]

    def test_reverse_of(self):
        assert K.check_reverse_of(["c", "b", "a"], {"values": ["a", "b", "c"]},
                                  K.CheckContext())[0]
        assert not K.check_reverse_of(["a", "b", "c"], {"values": ["a", "b", "c"]},
                                      K.CheckContext())[0]

    def test_reverse_of_rejects_non_list(self):
        assert not K.check_reverse_of("abc", {"values": ["a"]}, K.CheckContext())[0]

    def test_length_between(self):
        assert K.check_length_between([1, 2], {"min": 1, "max": 3}, K.CheckContext())[0]
        assert not K.check_length_between([], {"min": 1}, K.CheckContext())[0]
        assert not K.check_length_between(5, {"min": 1}, K.CheckContext())[0]

    def test_numeric_between(self):
        assert K.check_numeric_between(2, {"min": 2, "max": 2}, K.CheckContext())[0]
        assert not K.check_numeric_between(3, {"max": 2}, K.CheckContext())[0]
        assert not K.check_numeric_between("x", {"min": 0}, K.CheckContext())[0]

    def test_all_items(self):
        assert K.check_all_items(["a", "b"], {"check": {"checker": "nonempty"}},
                                 K.CheckContext())[0]
        assert not K.check_all_items(["a", ""], {"check": {"checker": "nonempty"}},
                                     K.CheckContext())[0]
        assert not K.check_all_items([], {"check": {"checker": "nonempty"}},
                                     K.CheckContext())[0]

    def test_all_items_requires_nested_check(self):
        with pytest.raises(K.CheckerError):
            K.check_all_items(["a"], {}, K.CheckContext())

    def test_keys_present_rejects_empty_values(self):
        assert K.check_keys_present({"a": 1}, {"keys": ["a"]}, K.CheckContext())[0]
        assert not K.check_keys_present({"a": ""}, {"keys": ["a"]}, K.CheckContext())[0]
        assert not K.check_keys_present({}, {"keys": ["a"]}, K.CheckContext())[0]

    def test_nonempty(self):
        assert K.check_nonempty("x", {}, K.CheckContext())[0]
        assert not K.check_nonempty("", {}, K.CheckContext())[0]
        assert not K.check_nonempty([], {}, K.CheckContext())[0]


class TestRepoGroundedCheckers:
    def test_path_exists_real_file_with_symbol(self, tmp_path):
        target = tmp_path / "m.py"
        target.write_text("class Marker:\n    pass\n", encoding="utf-8")
        ctx = K.CheckContext(repo_root=str(tmp_path))
        assert K.check_path_exists("m.py", {"symbol": "class Marker"}, ctx)[0]
        assert not K.check_path_exists("m.py", {"symbol": "class Nope"}, ctx)[0]
        assert not K.check_path_exists("missing.py", {}, ctx)[0]
        assert not K.check_path_exists("", {}, ctx)[0]

    def test_paths_exist(self, tmp_path):
        (tmp_path / "a.py").write_text("x", encoding="utf-8")
        ctx = K.CheckContext(repo_root=str(tmp_path))
        assert K.check_paths_exist(["a.py"], {}, ctx)[0]
        passed, detail = K.check_paths_exist(["a.py", "b.py"], {}, ctx)
        assert not passed and "b.py" in detail
        assert not K.check_paths_exist([], {}, ctx)[0]

    def test_symbols_exist_detects_hallucination(self, tmp_path):
        (tmp_path / "a.py").write_text("def real_symbol():\n    pass\n", encoding="utf-8")
        K.reset_symbol_cache()
        ctx = K.CheckContext(repo_root=str(tmp_path))
        assert K.check_symbols_exist(["real_symbol"], {}, ctx)[0]
        passed, detail = K.check_symbols_exist(["real_symbol", "ghost_symbol"], {}, ctx)
        assert not passed and "ghost_symbol" in detail
        assert not K.check_symbols_exist([], {}, ctx)[0]

    def test_symbols_exist_from_text(self, tmp_path):
        (tmp_path / "a.py").write_text("def alpha():\n    pass\n", encoding="utf-8")
        K.reset_symbol_cache()
        ctx = K.CheckContext(repo_root=str(tmp_path))
        assert K.check_symbols_exist("见 def alpha 定义", {}, ctx)[0]


class TestCommitMessageChecker:
    def _ok(self, message: str, args: dict = None):
        return K.check_commit_message(message, args or {
            "types": ["fix"], "scopes": ["acr"], "body_requires": ["Refs:"]},
            K.CheckContext())

    def test_valid_message(self):
        assert self._ok("fix(acr): 修正权重\n\nRefs: TASK-S2-03")[0]

    @pytest.mark.parametrize("message", [
        "", "x", "Fix(acr): 大写类型", "fix: 缺范围", "fix(acr): 结尾句号.\nRefs: a",
        "chore(acr): 类型不符\nRefs: a", "fix(other): 范围不符\nRefs: a",
        "fix(acr): 缺尾注",
    ])
    def test_invalid_messages(self, message):
        assert not self._ok(message)[0]

    def test_subject_length_limit(self):
        long_subject = "fix(acr): " + "字" * 80
        assert not self._ok(long_subject)[0]

    def test_default_types_when_unspecified(self):
        assert K.check_commit_message("docs(x): 文档", {}, K.CheckContext())[0]


class TestPythonProbesChecker:
    def test_pass(self):
        code = "def add(a, b):\n    return a + b\n"
        passed, _ = K.check_python_probes(code, {"probes": [
            {"call": "add", "args": [1, 2], "expected": 3}]}, K.CheckContext())
        assert passed

    def test_failure_reports_probe(self):
        code = "def add(a, b):\n    return a - b\n"
        passed, detail = K.check_python_probes(code, {"probes": [
            {"call": "add", "args": [1, 2], "expected": 3}]}, K.CheckContext())
        assert not passed and "add" in detail

    def test_raises_probe(self):
        code = "def boom(x):\n    raise ValueError('nope')\n"
        assert K.check_python_probes(code, {"probes": [
            {"call": "boom", "args": [1], "raises": "ValueError"}]}, K.CheckContext())[0]
        assert not K.check_python_probes(code, {"probes": [
            {"call": "boom", "args": [1], "raises": "KeyError"}]}, K.CheckContext())[0]
        code2 = "def ok(x):\n    return x\n"
        assert not K.check_python_probes(code2, {"probes": [
            {"call": "ok", "args": [1], "raises": "ValueError"}]}, K.CheckContext())[0]

    def test_syntax_error_fails(self):
        assert not K.check_python_probes("def broken(", {"probes": [
            {"call": "broken", "args": []}]}, K.CheckContext())[0]

    def test_empty_code_fails(self):
        assert not K.check_python_probes("", {"probes": [{"call": "x", "args": []}]},
                                         K.CheckContext())[0]

    def test_undefined_callable_fails(self):
        assert not K.check_python_probes("y = 1\n", {"probes": [
            {"call": "nope", "args": []}]}, K.CheckContext())[0]

    def test_probes_required(self):
        with pytest.raises(K.CheckerError):
            K.check_python_probes("x = 1\n", {}, K.CheckContext())

    def test_import_is_not_available(self):
        """受限命名空间：`import` 不可用（判定不执行任意代码）"""
        assert not K.check_python_probes("import os\n", {"probes": [
            {"call": "os", "args": []}]}, K.CheckContext())[0]

    def test_mapping_artifact_with_code_key(self):
        passed, _ = K.check_python_probes({"code": "def f():\n    return 1\n"},
                                          {"probes": [{"call": "f", "args": [],
                                                       "expected": 1}]}, K.CheckContext())
        assert passed


class TestProxyChecker:
    def test_rubric_hits(self):
        passed, detail = K.check_rubric_keywords(
            "提到 锚 与 哈希", {"groups": [["锚"], ["哈希"], ["不存在"]]}, K.CheckContext())
        assert not passed and "2/3" in detail

    def test_rubric_min_groups(self):
        assert K.check_rubric_keywords("提到 锚", {"groups": [["锚"], ["x"]],
                                                 "min_groups": 1}, K.CheckContext())[0]

    def test_rubric_requires_groups(self):
        with pytest.raises(K.CheckerError):
            K.check_rubric_keywords("x", {}, K.CheckContext())

    def test_registry_classification(self):
        assert K.is_mechanical("equals") and not K.is_proxy("equals")
        assert K.is_proxy("rubric_keywords") and not K.is_mechanical("rubric_keywords")
        assert "rubric_keywords" not in K.MECHANICAL_CHECKERS


# ════════════════════════════════════════════════════════════
#  派发与 fail-closed
# ════════════════════════════════════════════════════════════


class TestDispatchAndRunCheck:
    def test_dispatch_unknown_raises(self):
        with pytest.raises(K.CheckerError):
            K.dispatch("nope", 1, {}, K.CheckContext())

    def test_run_check_missing_path_fails_closed(self):
        result = K.run_check({"a": 1}, {"checker": "equals", "path": "b",
                                        "args": {"value": 1}, "why": "w"})
        assert result["passed"] is False
        assert "取值路径不可解析" in result["detail"]

    def test_run_check_marks_mechanical(self):
        result = K.run_check({"a": 1}, {"checker": "equals", "path": "a",
                                        "args": {"value": 1}})
        assert result["passed"] and result["mechanical"]

    def test_run_check_checker_error_fails_closed(self):
        result = K.run_check({"a": 1}, {"checker": "one_of", "path": "a", "args": {}})
        assert not result["passed"] and "判定器错误" in result["detail"]

    def test_run_check_unknown_checker_fails_closed(self):
        result = K.run_check({"a": 1}, {"checker": "nope", "path": "a"})
        assert not result["passed"] and "未登记的判定器" in result["detail"]

    def test_registry_is_nonempty_and_sorted_names(self):
        assert len(K.MECHANICAL_CHECKERS) >= 20
        assert all(isinstance(name, str) for name in K.ALL_CHECKERS)
        assert set(K.ALL_CHECKERS) == set(K.MECHANICAL_CHECKERS) | set(K.PROXY_CHECKERS)
