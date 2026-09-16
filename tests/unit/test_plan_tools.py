"""计划清单工具（todo_write）单元测试

覆盖 ``agent/tools/plan_tools.py`` 新增的第四个工具原语：

- 注册与 schema 形状（``todos`` 数组 / item 的 required 与 status 枚举三值）；
- 首次写入：``todos`` 与输入一致、``counts`` 正确、``rendered`` 含三类标记；
- **整体替换语义**（第二次只提交 1 项 ⇒ 上一份清单不再存在，而不是被追加）；
- 空数组 = 清空清单；
- 校验失败六种（非数组 / 项非对象 / content 为空 / status 非法 / 超 50 项 /
  content 超 500 字符）：``ok=False`` 且**已存状态未被破坏**；
- >1 个 in_progress 不拒绝但带 ``warning``；
- 按会话隔离（monkeypatch 会话键 accessor 使其可控）；
- 取不到会话键时不报错（回退默认键）；
- 会话条目上限（超 32 个后最早写入的不再保留，容器大小有界）；
- ``rendered`` 超长截断且带标记。

测试直接取注册表里的 handler 调用（与模型实际调用路径一致），并按用例重置模块级状态。
"""
import pytest

from agent.tools import plan_tools


# ════════════════════════════════════════════════════════════
#  fixtures / 工具函数
# ════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def clean_state():
    """每个用例前后都清空模块级清单状态（本模块状态是进程级内存态，必须隔离）"""
    plan_tools._reset_state()
    yield
    plan_tools._reset_state()


@pytest.fixture
def session_key(monkeypatch):
    """把会话键 accessor 换成可控值，返回一个"改键"的函数

    ``_current_session_key`` 正常路径取自既有 Trace 上下文（``TraceContext.current()``
    的 ``subject_id``），单测里没有真实上下文 ⇒ 用 monkeypatch 直接接管，
    既验证隔离语义，也避免测试依赖观测链路的内部状态。
    """
    holder = {"key": "sess-A"}

    def _set(key):
        holder["key"] = key

    monkeypatch.setattr(plan_tools, "_current_session_key", lambda: holder["key"])
    return _set


@pytest.fixture
def todo_tool():
    """把 todo_write 注册到 registry 上，产出 handler；用完还原注册表

    dl 传 None：本工具是纯内存草稿，不取 dl 的任何属性（也正因如此这里不需要 MagicMock）。
    """
    from agent import tools as registry

    saved = registry._registry.get("todo_write")
    plan_tools.register_all(None)
    handler = registry._registry["todo_write"]["handler"]
    try:
        yield handler
    finally:
        if saved is None:
            registry.unregister("todo_write")
        else:
            registry._registry["todo_write"] = saved


def _todos(*pairs):
    """便捷构造清单：``_todos(("做完 A", "completed"), ("做 B", "pending"))``"""
    return [{"content": c, "status": s} for c, s in pairs]


# ════════════════════════════════════════════════════════════
#  一、注册与 schema 形状
# ════════════════════════════════════════════════════════════

class TestRegistration:

    def test_tool_registered(self, todo_tool):
        """todo_write 已注册，且带非空 description"""
        from agent import tools as registry
        entry = registry._registry["todo_write"]
        assert entry["description"]
        assert callable(entry["handler"])

    def test_schema_shape(self, todo_tool):
        """schema：单参数 todos（数组），item 的 required 含 content 与 status"""
        from agent import tools as registry
        schema = registry._registry["todo_write"]["schema"]
        assert schema["type"] == "object"
        assert schema["required"] == ["todos"]
        assert set(schema["properties"]) == {"todos"}

        todos_prop = schema["properties"]["todos"]
        assert todos_prop["type"] == "array"
        item = todos_prop["items"]
        assert item["type"] == "object"
        assert set(item["required"]) == {"content", "status"}
        assert set(item["properties"]) == {"content", "status"}

    def test_status_enum_has_three_values(self, todo_tool):
        """status 枚举三值，且与实现体的合法状态集合逐字一致"""
        from agent import tools as registry
        item = registry._registry["todo_write"]["schema"]["properties"]["todos"]["items"]
        assert item["properties"]["status"]["enum"] == ["pending", "in_progress", "completed"]
        assert tuple(item["properties"]["status"]["enum"]) == plan_tools._VALID_STATUSES


# ════════════════════════════════════════════════════════════
#  二、写入成功路径
# ════════════════════════════════════════════════════════════

class TestWrite:

    def test_first_write(self, todo_tool, session_key):
        """首次写入：todos 与输入一致、counts 正确、rendered 含三类标记"""
        payload = _todos(("读完设计稿", "completed"),
                         ("改 orchestrator", "in_progress"),
                         ("跑回归", "pending"))
        result = todo_tool(todos=payload)
        assert result["ok"] is True
        assert result["todos"] == payload
        assert result["counts"] == {"pending": 1, "in_progress": 1, "completed": 1}
        assert result["rendered"] == (
            "- [x] 读完设计稿\n"
            "- [~] 改 orchestrator\n"
            "- [ ] 跑回归"
        )
        assert "- [ ]" in result["rendered"]      # pending
        assert "- [~]" in result["rendered"]      # in_progress
        assert "- [x]" in result["rendered"]      # completed
        assert "warning" not in result

    def test_replacement_semantics(self, todo_tool, session_key):
        """整体替换：第二次只提交 1 项 ⇒ 上一份清单整体消失（不是追加）"""
        first = _todos(("第一步", "completed"), ("第二步", "pending"))
        assert todo_tool(todos=first)["ok"] is True

        second = _todos(("只剩这一步", "in_progress"))
        result = todo_tool(todos=second)
        assert result["ok"] is True
        assert result["todos"] == second
        assert len(result["todos"]) == 1
        contents = [t["content"] for t in result["todos"]]
        for gone in ("第一步", "第二步"):
            assert gone not in contents, f"替换语义失效：{gone} 仍存在于清单中"
        assert result["counts"] == {"pending": 0, "in_progress": 1, "completed": 0}
        assert "- [x]" not in result["rendered"]

    def test_empty_array_clears(self, todo_tool, session_key):
        """空数组 = 清空清单（counts 全 0、rendered 为空串）"""
        assert todo_tool(todos=_todos(("待办", "pending")))["ok"] is True
        result = todo_tool(todos=[])
        assert result["ok"] is True
        assert result["todos"] == []
        assert result["counts"] == {"pending": 0, "in_progress": 0, "completed": 0}
        assert result["rendered"] == ""
        assert "warning" not in result

    def test_multiple_in_progress_warns_but_accepts(self, todo_tool, session_key):
        """同时 >1 项 in_progress：ok=True 且带 warning（不拒绝）"""
        result = todo_tool(todos=_todos(("A", "in_progress"), ("B", "in_progress")))
        assert result["ok"] is True
        assert result["counts"]["in_progress"] == 2
        assert result["warning"] == "同时有 2 项处于 in_progress，建议只保留一项"

    def test_single_in_progress_has_no_warning(self, todo_tool, session_key):
        """恰好 1 项 in_progress 是推荐形态：不带 warning"""
        result = todo_tool(todos=_todos(("A", "in_progress"), ("B", "pending")))
        assert result["ok"] is True
        assert "warning" not in result

    def test_empty_content_is_stripped(self, todo_tool, session_key):
        """content 首尾空白被归一化（存进去的就是去空白后的文本）"""
        result = todo_tool(todos=[{"content": "  带空白的项  ", "status": "pending"}])
        assert result["ok"] is True
        assert result["todos"] == [{"content": "带空白的项", "status": "pending"}]

    def test_extra_keys_dropped(self, todo_tool, session_key):
        """多余键被丢弃：内部状态形状恒为 {content, status}"""
        result = todo_tool(todos=[{"content": "A", "status": "pending", "id": 1, "x": "y"}])
        assert result["ok"] is True
        assert result["todos"] == [{"content": "A", "status": "pending"}]


# ════════════════════════════════════════════════════════════
#  三、校验失败 —— 且不得破坏已存状态
# ════════════════════════════════════════════════════════════

class TestValidation:

    @pytest.mark.parametrize("bad, expect_in_error", [
        ("不是数组", "todos 必须是数组"),
        ({"content": "A", "status": "pending"}, "todos 必须是数组"),
        ([{"content": "A"}], "status 非法"),          # 缺 status ⇒ 按非法状态拒绝
        ([{"status": "pending"}], "缺少非空 content"),
        ([{"content": "   ", "status": "pending"}], "缺少非空 content"),
        ([{"content": 123, "status": "pending"}], "缺少非空 content"),
        ([{"content": "A", "status": "doing"}], "status 非法"),
        ([{"content": "A", "status": "DONE"}], "status 非法"),
        (["纯字符串项"], "不是对象"),
        ([{"content": "A", "status": "pending"}] * 51, "清单最多 50 项"),
        ([{"content": "长" * 501, "status": "pending"}], "超过 500 字符"),
    ])
    def test_invalid_input_rejected(self, todo_tool, session_key, bad, expect_in_error):
        """各类非法入参 → ok=False，error 指向具体原因（不抛异常）"""
        result = todo_tool(todos=bad)
        assert result["ok"] is False
        assert expect_in_error in result["error"]
        assert "todos" not in result and "counts" not in result

    def test_invalid_input_keeps_existing_state(self, todo_tool, session_key):
        """**先校验后写入**：非法请求后，上一份合法清单原封不动"""
        good = _todos(("保留我", "in_progress"), ("也保留我", "pending"))
        assert todo_tool(todos=good)["ok"] is True
        assert plan_tools._STATE["sess-A"] == good

        for bad in ("不是数组",
                    [{"content": "", "status": "pending"}],
                    [{"content": "A", "status": "nope"}],
                    [{"content": "A", "status": "pending"}] * 51,
                    [{"content": "长" * 501, "status": "pending"}]):
            assert todo_tool(todos=bad)["ok"] is False
            assert plan_tools._STATE["sess-A"] == good, f"非法请求破坏了已存清单: {bad!r}"

    def test_boundary_values_accepted(self, todo_tool, session_key):
        """边界值合法：恰好 50 项、content 恰好 500 字符"""
        result = todo_tool(todos=_todos(*[(f"项{i}", "pending") for i in range(50)]))
        assert result["ok"] is True
        assert len(result["todos"]) == 50

        result = todo_tool(todos=[{"content": "长" * 500, "status": "pending"}])
        assert result["ok"] is True
        assert len(result["todos"][0]["content"]) == 500


# ════════════════════════════════════════════════════════════
#  四、会话隔离、回退与有界性
# ════════════════════════════════════════════════════════════

class TestSessionIsolation:

    def test_two_sessions_do_not_see_each_other(self, session_key):
        """两个会话键各写一份：互相看不到对方的清单"""
        session_key("sess-A")
        plan_tools.write_todos(_todos(("A 的活", "in_progress")))
        session_key("sess-B")
        plan_tools.write_todos(_todos(("B 的活", "pending"), ("B 的另一件", "completed")))

        # B 重写自己的清单：不受 A 影响
        result_b = plan_tools.write_todos(_todos(("B 的活", "completed")))
        assert [t["content"] for t in result_b["todos"]] == ["B 的活"]

        # 切回 A：A 的清单仍在（未被 B 的写入抹掉，也不是 B 的内容）
        session_key("sess-A")
        result_a = plan_tools.write_todos([{"content": "A 的活", "status": "in_progress"}])
        assert result_a["todos"] == [{"content": "A 的活", "status": "in_progress"}]
        assert plan_tools._STATE["sess-A"] == [{"content": "A 的活", "status": "in_progress"}]
        assert plan_tools._STATE["sess-B"] == [{"content": "B 的活", "status": "completed"}]

    def test_explicit_session_key_argument(self):
        """显式 session_key 优先于 accessor（内部/测试直调路径）"""
        plan_tools.write_todos(_todos(("X", "pending")), session_key="explicit-X")
        assert plan_tools._STATE["explicit-X"] == [{"content": "X", "status": "pending"}]
        assert plan_tools._STATE.get("default") is None

    def test_missing_session_key_falls_back_to_default(self, monkeypatch):
        """取不到会话键（accessor 抛异常）⇒ 退回默认键，**绝不报错**"""
        def _boom():
            raise RuntimeError("no trace context")

        monkeypatch.setattr(plan_tools, "_current_session_key", _boom)
        result = plan_tools.write_todos(_todos(("无会话上下文也要能写", "pending")))
        assert result["ok"] is True
        assert plan_tools._STATE[plan_tools._DEFAULT_SESSION_KEY] == [
            {"content": "无会话上下文也要能写", "status": "pending"}]

    def test_blank_session_key_falls_back_to_default(self, session_key):
        """accessor 返回空串/纯空白 ⇒ 同样归一化到默认键"""
        session_key("   ")
        result = plan_tools.write_todos(_todos(("空白键", "pending")))
        assert result["ok"] is True
        assert list(plan_tools._STATE) == [plan_tools._DEFAULT_SESSION_KEY]

    def test_no_trace_context_returns_default_key(self):
        """真实 accessor：无 Trace 上下文时返回默认键（且绝不抛异常）"""
        from agent.observability.trace_v2 import TraceContext
        key = plan_tools._current_session_key()
        assert isinstance(key, str) and key
        if TraceContext.current() is None:   # 全量套件随机序下可能有外部上下文残留
            assert key == plan_tools._DEFAULT_SESSION_KEY

    def test_session_container_is_bounded(self):
        """会话数上限：写满 32 个会话后容器大小有界，最早写入的被淘汰"""
        assert plan_tools._MAX_SESSIONS == 32
        for i in range(40):
            plan_tools.write_todos(_todos((f"会话{i}的项", "pending")), session_key=f"s{i}")

        assert len(plan_tools._STATE) <= plan_tools._MAX_SESSIONS
        assert len(plan_tools._STATE) == plan_tools._MAX_SESSIONS
        # 最早写入的 8 个会话已被淘汰（FIFO），最近的仍在
        for i in range(8):
            assert f"s{i}" not in plan_tools._STATE, f"s{i} 应已被淘汰"
        for i in range(8, 40):
            assert f"s{i}" in plan_tools._STATE, f"s{i} 不应被淘汰"

    def test_rewrite_does_not_refresh_eviction_order(self):
        """重写已存在的会话不改变其位次（淘汰按"最早写入"，不是 LRU）"""
        for i in range(plan_tools._MAX_SESSIONS):
            plan_tools.write_todos(_todos((f"K{i}", "pending")), session_key=f"k{i}")
        plan_tools.write_todos(_todos(("K0 重写", "completed")), session_key="k0")
        plan_tools.write_todos(_todos(("新会话", "pending")), session_key="brand-new")

        assert "k0" not in plan_tools._STATE      # 位次未刷新 ⇒ 仍是最早，被淘汰
        assert "brand-new" in plan_tools._STATE
        assert len(plan_tools._STATE) == plan_tools._MAX_SESSIONS


# ════════════════════════════════════════════════════════════
#  五、rendered 截断
# ════════════════════════════════════════════════════════════

class TestRendered:

    def test_rendered_truncated_with_marker(self, todo_tool, session_key):
        """清单过长 ⇒ rendered 截断到上限并带标记（todos 字段仍是完整内容）"""
        payload = _todos(*[(f"项{i}" + "长" * 400, "pending") for i in range(50)])
        result = todo_tool(todos=payload)
        assert result["ok"] is True

        rendered = result["rendered"]
        assert len(rendered) < sum(len(t["content"]) for t in payload)
        assert "已截断至 4000 字符" in rendered
        assert rendered.startswith("- [ ] 项0")
        assert len(result["todos"]) == 50           # 完整内容不受 rendered 截断影响

    def test_short_rendered_not_truncated(self, todo_tool, session_key):
        """未超上限时不带截断标记"""
        result = todo_tool(todos=_todos(("短项", "pending")))
        assert "已截断" not in result["rendered"]
