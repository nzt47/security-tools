"""内容检索（grep）与精准编辑（edit）单元测试

覆盖 ``agent/tools/search_tools.py`` 新增的两个工具原语：

- grep：内容命中与行号、include 过滤（含花括号）、忽略目录剪枝、max_results 截断、
        非法正则、大小写开关、上下文行、二进制跳过、路径不存在。
- edit：单次替换、未找到、多处不唯一（含行号）、replace_all、读前置拒绝、
        权限拒绝、内容安全拒绝、CRLF 保真、BOM 保真、文件不存在、同串短路。

测试直接取注册表里的 handler 调用（与模型实际调用路径一致）。
"""
import pytest
from unittest.mock import MagicMock


def _snapshot_registry() -> dict:
    """进程级工具注册表的快照（`agent/tools/__init__.py` 的 `_registry`）"""
    from agent import tools as _tools

    return dict(_tools._registry)


def _restore_registry(saved: dict) -> None:
    """把注册表**逐条**还原成快照，并推进 `_registry_version` 让各级缓存失效

    与 `tests/unit/test_tool_count_consistency.py` 的 `isolated_tool_registry` 同一形状：
    不用 `tools.clear()`（它会连 `_tool_health` 一起清），只换 `_registry` 的内容。
    """
    from agent import tools as _tools

    _tools._registry.clear()
    _tools._registry.update(saved)
    _tools._registry_version += 1


# ════════════════════════════════════════════════════════════
#  fixtures
# ════════════════════════════════════════════════════════════

@pytest.fixture
def registered():
    """把 grep/edit 注册到"放行"的 mock dl 上，产出 (handlers, dl)；用完**整表还原**注册表

    【不易·为什么不是只还原 grep/edit】`search_tools.register_all` 除 grep/edit 外还登记
    `compress` / `decompress` / `diff_files` 三个**真实工具**；旧写法只存还 grep、edit
    ⇒ 那 3 个留在**进程级**注册表（`agent/tools/__init__.py:_registry`）里，污染同进程
    后续测试（实测：本文件与 `tests/unit/test_tool_count_consistency.py` 合跑必红 2 条）。
    """
    from agent import tools as registry
    from agent.tools.search_tools import register_all

    dl = MagicMock()
    dl._permission.check_action.return_value = MagicMock(allowed=True, reason="")
    dl._permission.check_text.return_value = {"level": "safe", "matches": []}

    saved = _snapshot_registry()
    register_all(dl)
    handlers = {name: registry._registry[name]["handler"] for name in ("grep", "edit")}
    try:
        yield handlers, dl
    finally:
        _restore_registry(saved)


@pytest.fixture
def grep_tool(registered):
    """grep handler"""
    return registered[0]["grep"]


@pytest.fixture
def edit_tool(registered):
    """edit handler"""
    return registered[0]["edit"]


def _note(path):
    """登记"已读"，使 edit 的读前置校验可通过"""
    from agent.tools.search_tools import note_file_read
    note_file_read(path)


# ════════════════════════════════════════════════════════════
#  一、grep —— 内容检索
# ════════════════════════════════════════════════════════════

class TestGrep:

    def test_grep_hits_with_line_numbers(self, grep_tool, tmp_path):
        """命中：返回相对路径、1-based 行号与该行原文"""
        (tmp_path / "a.txt").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
        result = grep_tool(pattern="beta", path=str(tmp_path))
        assert result["ok"] is True
        assert result["count"] == 1
        assert result["truncated"] is False
        match = result["matches"][0]
        assert match["path"] == "a.txt"
        assert match["line"] == 2
        assert match["text"] == "beta"

    def test_grep_hits_all_matching_lines(self, grep_tool, tmp_path):
        """同一文件多行命中 → 逐条返回"""
        (tmp_path / "a.txt").write_text("hit\nmiss\nhit\n", encoding="utf-8")
        result = grep_tool(pattern="hit", path=str(tmp_path))
        assert [m["line"] for m in result["matches"]] == [1, 3]
        assert result["count"] == 2

    def test_grep_include_filter(self, grep_tool, tmp_path):
        """include 过滤：只检索 *.py"""
        (tmp_path / "a.py").write_text("TOKEN\n", encoding="utf-8")
        (tmp_path / "b.md").write_text("TOKEN\n", encoding="utf-8")
        result = grep_tool(pattern="TOKEN", path=str(tmp_path), include="*.py")
        assert result["count"] == 1
        assert result["matches"][0]["path"] == "a.py"

    def test_grep_include_brace_expansion(self, grep_tool, tmp_path):
        """include 支持花括号：*.{md,txt}"""
        (tmp_path / "a.py").write_text("TOKEN\n", encoding="utf-8")
        (tmp_path / "b.md").write_text("TOKEN\n", encoding="utf-8")
        (tmp_path / "c.txt").write_text("TOKEN\n", encoding="utf-8")
        result = grep_tool(pattern="TOKEN", path=str(tmp_path), include="*.{md,txt}")
        assert sorted(m["path"] for m in result["matches"]) == ["b.md", "c.txt"]

    def test_grep_include_matches_nested_files(self, grep_tool, tmp_path):
        """include 按文件名匹配 → 命中子目录中的同名后缀文件"""
        sub = tmp_path / "src" / "deep"
        sub.mkdir(parents=True)
        (sub / "a.py").write_text("TOKEN\n", encoding="utf-8")
        result = grep_tool(pattern="TOKEN", path=str(tmp_path), include="*.py")
        assert result["count"] == 1
        assert result["matches"][0]["path"].replace("\\", "/") == "src/deep/a.py"

    def test_grep_skips_ignored_dirs(self, grep_tool, tmp_path):
        """忽略目录（__pycache__ / data / .git / node_modules）不被检索"""
        for name in ("__pycache__", "data", ".git", "node_modules"):
            d = tmp_path / name
            d.mkdir()
            (d / "ignored.txt").write_text("TOKEN\n", encoding="utf-8")
        src = tmp_path / "src"
        src.mkdir()
        (src / "kept.txt").write_text("TOKEN\n", encoding="utf-8")

        result = grep_tool(pattern="TOKEN", path=str(tmp_path))
        assert result["count"] == 1
        assert result["matches"][0]["path"].replace("\\", "/") == "src/kept.txt"

    def test_grep_max_results_truncates(self, grep_tool, tmp_path):
        """达到 max_results 立即停止并置 truncated=True"""
        (tmp_path / "a.txt").write_text("hit\n" * 10, encoding="utf-8")
        result = grep_tool(pattern="hit", path=str(tmp_path), max_results=3)
        assert result["count"] == 3
        assert result["truncated"] is True

    def test_grep_not_truncated_when_below_limit(self, grep_tool, tmp_path):
        """未达上限时 truncated 为 False"""
        (tmp_path / "a.txt").write_text("hit\nhit\n", encoding="utf-8")
        result = grep_tool(pattern="hit", path=str(tmp_path), max_results=10)
        assert result["count"] == 2
        assert result["truncated"] is False

    def test_grep_invalid_regex_returns_error(self, grep_tool, tmp_path):
        """非法正则 → ok=False，不抛异常"""
        (tmp_path / "a.txt").write_text("x\n", encoding="utf-8")
        result = grep_tool(pattern="([a-z", path=str(tmp_path))
        assert result["ok"] is False
        assert "正则表达式无效" in result["error"]

    def test_grep_case_sensitive_switch(self, grep_tool, tmp_path):
        """大小写敏感开关：默认不敏感，打开后不命中"""
        (tmp_path / "a.txt").write_text("Hello World\n", encoding="utf-8")
        assert grep_tool(pattern="hello", path=str(tmp_path))["count"] == 1
        assert grep_tool(pattern="hello", path=str(tmp_path), case_sensitive=True)["count"] == 0

    def test_grep_context_lines(self, grep_tool, tmp_path):
        """context_lines>0 → 命中条目附带上下文块（含命中行）"""
        (tmp_path / "a.txt").write_text("a\nb\nc\nd\n", encoding="utf-8")
        result = grep_tool(pattern="^c$", path=str(tmp_path), context_lines=1)
        assert result["count"] == 1
        context = result["matches"][0]["context"]
        assert [(c["line"], c["text"]) for c in context] == [(2, "b"), (3, "c"), (4, "d")]

    def test_grep_skips_binary_file(self, grep_tool, tmp_path):
        """含 NUL 字节的二进制文件被跳过"""
        (tmp_path / "bin.dat").write_bytes(b"TOKEN\x00TOKEN")
        (tmp_path / "a.txt").write_text("TOKEN\n", encoding="utf-8")
        result = grep_tool(pattern="TOKEN", path=str(tmp_path))
        assert result["count"] == 1
        assert result["matches"][0]["path"] == "a.txt"

    def test_grep_skips_oversized_file(self, grep_tool, tmp_path):
        """超过 2MB 的文件被跳过（不整体失败）"""
        (tmp_path / "big.txt").write_bytes(b"TOKEN\n" + b"x" * (2 * 1024 * 1024 + 1))
        result = grep_tool(pattern="TOKEN", path=str(tmp_path))
        assert result["ok"] is True
        assert result["count"] == 0

    def test_grep_missing_path_returns_error(self, grep_tool, tmp_path):
        """搜索路径不存在 → ok=False"""
        result = grep_tool(pattern="x", path=str(tmp_path / "nope"))
        assert result["ok"] is False
        assert "搜索路径不存在" in result["error"]

    def test_grep_empty_pattern_returns_error(self, grep_tool, tmp_path):
        """缺 pattern → ok=False"""
        result = grep_tool(pattern="", path=str(tmp_path))
        assert result["ok"] is False
        assert "pattern" in result["error"]

    def test_grep_single_file_path(self, grep_tool, tmp_path):
        """path 指向单个文件时只检索该文件"""
        target = tmp_path / "a.txt"
        target.write_text("TOKEN\n", encoding="utf-8")
        (tmp_path / "b.txt").write_text("TOKEN\n", encoding="utf-8")
        result = grep_tool(pattern="TOKEN", path=str(target))
        assert result["count"] == 1
        assert result["matches"][0]["path"] == "a.txt"

    def test_grep_handles_chinese_content(self, grep_tool, tmp_path):
        """中文内容可检索（二进制判据只看 NUL，不用"非文本占比"）"""
        (tmp_path / "a.md").write_text("云枢是数字生命体\n", encoding="utf-8")
        result = grep_tool(pattern="数字生命", path=str(tmp_path))
        assert result["count"] == 1
        assert result["matches"][0]["text"] == "云枢是数字生命体"


# ════════════════════════════════════════════════════════════
#  二、edit —— 精准编辑
# ════════════════════════════════════════════════════════════

class TestEdit:

    def test_edit_single_replace_success(self, edit_tool, tmp_path):
        """单次替换成功：内容更新 + replaced/bytes_written 正确"""
        target = tmp_path / "a.txt"
        target.write_bytes("alpha\nbeta\n".encode("utf-8"))  # 显式 LF：断言字节数不受平台换行翻译影响
        _note(target)
        result = edit_tool(path=str(target), old_string="beta", new_string="gamma")
        assert result["ok"] is True
        assert result["replaced"] == 1
        assert result["bytes_written"] == len("alpha\ngamma\n".encode("utf-8"))
        assert target.read_bytes() == b"alpha\ngamma\n"

    def test_edit_not_found(self, edit_tool, tmp_path):
        """old_string 不存在 → occurrences=0"""
        target = tmp_path / "a.txt"
        target.write_text("alpha\n", encoding="utf-8")
        _note(target)
        result = edit_tool(path=str(target), old_string="zzz", new_string="y")
        assert result["ok"] is False
        assert result["occurrences"] == 0
        assert "未找到待替换内容" in result["error"]
        assert target.read_text(encoding="utf-8") == "alpha\n"

    def test_edit_multiple_matches_rejected_with_lines(self, edit_tool, tmp_path):
        """多处匹配且 replace_all=False → 报错并给出现行号"""
        target = tmp_path / "a.txt"
        target.write_text("x\nx\nx\n", encoding="utf-8")
        _note(target)
        result = edit_tool(path=str(target), old_string="x", new_string="y")
        assert result["ok"] is False
        assert result["occurrences"] == 3
        assert result["lines"] == [1, 2, 3]
        assert "出现 3 次，不唯一" in result["error"]
        assert target.read_text(encoding="utf-8") == "x\nx\nx\n"

    def test_edit_replace_all(self, edit_tool, tmp_path):
        """replace_all=True → 全部替换"""
        target = tmp_path / "a.txt"
        target.write_text("x\nx\nx\n", encoding="utf-8")
        _note(target)
        result = edit_tool(path=str(target), old_string="x", new_string="y", replace_all=True)
        assert result["ok"] is True
        assert result["replaced"] == 3
        assert target.read_text(encoding="utf-8") == "y\ny\ny\n"

    def test_edit_requires_prior_read(self, edit_tool, tmp_path):
        """未 read_file 过 → blocked（读前置校验）"""
        target = tmp_path / "a.txt"
        target.write_text("alpha\n", encoding="utf-8")
        result = edit_tool(path=str(target), old_string="alpha", new_string="beta")
        assert result["ok"] is False
        assert result.get("blocked") is True
        assert "编辑前必须先读取该文件" in result["error"]
        assert target.read_text(encoding="utf-8") == "alpha\n"

    def test_edit_permission_denied(self, registered, tmp_path):
        """权限系统拒绝 → blocked，且不落盘"""
        handlers, dl = registered
        dl._permission.check_action.return_value = MagicMock(allowed=False, reason="测试拒绝")
        target = tmp_path / "a.txt"
        target.write_text("alpha\n", encoding="utf-8")
        _note(target)
        result = handlers["edit"](path=str(target), old_string="alpha", new_string="beta")
        assert result["ok"] is False
        assert result.get("blocked") is True
        assert "权限系统拒绝: 测试拒绝" in result["error"]
        assert target.read_text(encoding="utf-8") == "alpha\n"

    def test_edit_critical_content_blocked(self, registered, tmp_path):
        """内容安全检查为 critical → blocked"""
        handlers, dl = registered
        dl._permission.check_text.return_value = {
            "level": "critical", "matches": [{"description": "疑似密钥外泄"}],
        }
        target = tmp_path / "a.txt"
        target.write_text("alpha\n", encoding="utf-8")
        _note(target)
        result = handlers["edit"](path=str(target), old_string="alpha", new_string="beta")
        assert result["ok"] is False
        assert result.get("blocked") is True
        assert "内容安全检查未通过" in result["error"]
        assert target.read_text(encoding="utf-8") == "alpha\n"

    def test_edit_preserves_crlf(self, edit_tool, tmp_path):
        """CRLF 文件编辑后仍是 CRLF（不得被改成 LF）"""
        target = tmp_path / "a.txt"
        target.write_bytes(b"line1\r\nline2\r\n")
        _note(target)
        result = edit_tool(path=str(target), old_string="line2", new_string="changed")
        assert result["ok"] is True
        raw = target.read_bytes()
        assert raw == b"line1\r\nchanged\r\n"
        assert raw.count(b"\r\n") == 2
        assert raw.replace(b"\r\n", b"").count(b"\n") == 0

    def test_edit_preserves_bom(self, edit_tool, tmp_path):
        """带 BOM 的文件编辑后 BOM 保留（不新增、不删除）"""
        target = tmp_path / "a.txt"
        target.write_bytes(b"\xef\xbb\xbf" + "你好\n".encode("utf-8"))
        _note(target)
        result = edit_tool(path=str(target), old_string="你好", new_string="世界")
        assert result["ok"] is True
        raw = target.read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf")
        assert raw == b"\xef\xbb\xbf" + "世界\n".encode("utf-8")

    def test_edit_without_bom_does_not_add_bom(self, edit_tool, tmp_path):
        """无 BOM 的文件编辑后不会凭空多出 BOM"""
        target = tmp_path / "a.txt"
        target.write_bytes("你好\n".encode("utf-8"))
        _note(target)
        edit_tool(path=str(target), old_string="你好", new_string="世界")
        assert not target.read_bytes().startswith(b"\xef\xbb\xbf")

    def test_edit_missing_file(self, edit_tool, tmp_path):
        """文件不存在 → 明确报错"""
        result = edit_tool(path=str(tmp_path / "nope.txt"), old_string="a", new_string="b")
        assert result["ok"] is False
        assert "文件不存在" in result["error"]

    def test_edit_same_string_short_circuits(self, edit_tool, tmp_path):
        """old_string == new_string → 直接拒绝"""
        target = tmp_path / "a.txt"
        target.write_text("alpha\n", encoding="utf-8")
        _note(target)
        result = edit_tool(path=str(target), old_string="alpha", new_string="alpha")
        assert result["ok"] is False
        assert "无需修改" in result["error"]

    def test_edit_missing_path(self, edit_tool):
        """缺 path → 明确报错"""
        result = edit_tool(path="", old_string="a", new_string="b")
        assert result["ok"] is False
        assert "path" in result["error"]


# ════════════════════════════════════════════════════════════
#  三、读前置登记（note_file_read / has_been_read）与接线
# ════════════════════════════════════════════════════════════

class TestReadRegistry:

    def test_note_and_has_been_read(self, tmp_path):
        """note_file_read / has_been_read 对相对与绝对写法等价"""
        from agent.tools.search_tools import has_been_read, note_file_read
        target = tmp_path / "a.txt"
        target.write_text("alpha\n", encoding="utf-8")
        assert has_been_read(target) is False
        note_file_read(target)
        assert has_been_read(target) is True
        assert has_been_read(str(target)) is True

    def test_unrelated_file_not_marked_read(self, tmp_path):
        """登记只对目标文件生效，不波及其它文件"""
        from agent.tools.search_tools import has_been_read, note_file_read
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_text("a\n", encoding="utf-8")
        b.write_text("b\n", encoding="utf-8")
        note_file_read(a)
        assert has_been_read(a) is True
        assert has_been_read(b) is False

    def test_read_file_registers_read_state(self, tmp_path):
        """接线回归：read_file 读成功后登记已读，edit 的读前置校验随即放行

        【不易·为什么用整表快照】`file_tools_reg.register_all` 一次登记**五个**文件工具
        （read_file / write_file / list_directory / get_file_info / search_files）；
        旧写法只还原 read_file ⇒ 另外 4 个真实工具留在进程级注册表里。
        """
        from agent import tools as registry
        from agent.tools.file_tools_reg import register_all as register_file_tools
        from agent.tools.search_tools import has_been_read

        dl = MagicMock()
        dl._permission.check_action.return_value = MagicMock(allowed=True, reason="")
        dl._permission.check_text.return_value = {"level": "safe", "matches": []}

        target = tmp_path / "a.txt"
        target.write_text("alpha\n", encoding="utf-8")
        saved = _snapshot_registry()
        register_file_tools(dl)
        try:
            result = registry._registry["read_file"]["handler"](path=str(target))
            assert result["ok"] is True
            assert has_been_read(target) is True
        finally:
            _restore_registry(saved)


# ════════════════════════════════════════════════════════════
#  四、注册元数据（名称 / schema）
# ════════════════════════════════════════════════════════════

class TestRegistration:

    def test_both_tools_registered_with_schema(self, registered):
        """grep / edit 均已注册且带 OpenAI 风格 schema"""
        from agent import tools as registry
        for name, required in (("grep", ["pattern"]), ("edit", ["path", "old_string", "new_string"])):
            entry = registry._registry[name]
            assert entry["schema"]["type"] == "object"
            assert entry["schema"]["required"] == required
            assert entry["description"]

    def test_grep_schema_optional_params(self, registered):
        """grep 的可选参数与默认值声明齐全"""
        from agent import tools as registry
        props = registry._registry["grep"]["schema"]["properties"]
        assert set(props) == {"pattern", "path", "include", "max_results", "case_sensitive", "context_lines"}
        assert props["max_results"]["type"] == "integer"
        assert props["case_sensitive"]["type"] == "boolean"
