"""集中式工具闸门（``agent/tool_gate.py``）单元测试

覆盖：
    1. 总开关 ``CP_TOOL_GATE_ENABLED`` 关闭 → 放行（即使命中黑名单）；
    2. ``denied_tools`` 命中 → 拒绝，且返回结构为
       ``{"ok","blocked","error_code","error"}``（``blocked=True`` /
       ``error_code="PERMISSION_DENIED"``）；
    3. ``denied_tools`` 含 ``"*"`` → 拒绝一切；
    4. 未命中黑名单 → 放行；多角色 ``denied_tools`` 并集生效；
    5. 描述符 ``trust.requires_approval=true`` → 拒绝，且工具原名与
       ``cp.<source>.<name>`` 形态**双向**都能命中；
    6. 策略/描述符文件缺失、JSON 损坏、结构异常 → 放行（fail-open）且不抛异常；
    7. 接线：``ToolCallingService._execute_safe`` 在闸门拒绝时**零次**实际执行
       （未进入 3 次重试循环），并原样返回拒绝结果。

纪律：**不触碰真实 ``data/`` 文件**——所有用例都把模块级常量
``POLICY_POLICIES_PATH`` / ``DESCRIPTORS_PATH`` 指向 ``tmp_path`` 下的构造文件。
"""
import json

import pytest
from unittest.mock import MagicMock, patch


# ════════════════════════════════════════════════════════════
#  fixtures
# ════════════════════════════════════════════════════════════

class _GateFiles:
    """把闸门的两个文件路径指向 tmp_path，并提供写入/调用辅助"""

    def __init__(self, module, policy_path, desc_path):
        self.mod = module
        self.policy_path = policy_path
        self.desc_path = desc_path

    def write_policy(self, data):
        self.policy_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def write_policy_raw(self, text):
        self.policy_path.write_text(text, encoding="utf-8")

    def write_descriptors(self, data):
        self.desc_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def write_descriptors_raw(self, text):
        self.desc_path.write_text(text, encoding="utf-8")

    def check(self, func_name, args=None):
        return self.mod.check_tool_call(func_name, args)


@pytest.fixture
def gate(tmp_path, monkeypatch):
    """把两个路径常量指向 tmp_path 的假文件；默认两份文件都是"空规则" """
    import agent.tool_gate as G

    files = _GateFiles(G, tmp_path / "permission_policies.json",
                       tmp_path / "descriptors.json")
    monkeypatch.setattr(G, "POLICY_POLICIES_PATH", str(files.policy_path))
    monkeypatch.setattr(G, "DESCRIPTORS_PATH", str(files.desc_path))
    monkeypatch.delenv(G.GATE_ENABLED_ENV, raising=False)
    G._reset_cache()
    files.write_policy({"version": 1, "default_role": "guest", "roles": {}})
    files.write_descriptors({"schema_version": 1, "descriptors": {}})
    return files


def _descriptor_entry(cid, name, requires_approval):
    """最小描述符条目（只保留闸门读取的字段）"""
    return {
        "meta": {"id": cid},
        "capability": {"name": name},
        "trust": {"requires_approval": requires_approval},
    }


# ════════════════════════════════════════════════════════════
#  一、总开关
# ════════════════════════════════════════════════════════════

class TestGateSwitch:

    @pytest.mark.parametrize("value", ["0", "false", "FALSE", "no", "Off", " off "])
    def test_开关关闭时放行即使命中黑名单(self, gate, monkeypatch, value):
        """总开关关闭 → 无论策略怎么写都放行（含通配符黑名单）"""
        gate.write_policy({"roles": {"admin": {"denied_tools": ["*"]}}})
        monkeypatch.setenv(gate.mod.GATE_ENABLED_ENV, value)
        assert gate.check("write_file") is None

    def test_缺省视为开启(self, gate):
        """未设置环境变量 → 闸门生效（黑名单仍拦截）"""
        gate.write_policy({"roles": {"guest": {"denied_tools": ["write_file"]}}})
        result = gate.check("write_file")
        assert result is not None and result["blocked"] is True

    @pytest.mark.parametrize("value", ["1", "true", "yes", "on", ""])
    def test_开关开启时闸门生效(self, gate, monkeypatch, value):
        gate.write_policy({"roles": {"guest": {"denied_tools": ["write_file"]}}})
        monkeypatch.setenv(gate.mod.GATE_ENABLED_ENV, value)
        assert gate.check("write_file")["blocked"] is True


# ════════════════════════════════════════════════════════════
#  二、显式黑名单（roles[*].denied_tools 并集）
# ════════════════════════════════════════════════════════════

class TestDeniedTools:

    def test_命中黑名单即拒绝且结构兼容(self, gate):
        """拒绝结果必须与既有工具失败结果兼容，且带 blocked/error_code"""
        gate.write_policy({"roles": {"developer": {"allowed_tools": ["web_search"],
                                                   "denied_tools": ["write_file"]}}})
        result = gate.check("write_file", {"path": "x.txt"})
        assert result is not None
        assert set(result) == {"ok", "blocked", "error_code", "error"}
        assert result["ok"] is False
        assert result["blocked"] is True
        assert result["error_code"] == "PERMISSION_DENIED"
        assert "write_file" in result["error"]            # 含工具名
        assert "denied_tools" in result["error"]          # 含命中的规则来源

    def test_未命中黑名单放行(self, gate):
        gate.write_policy({"roles": {"developer": {"denied_tools": ["system_format"]}}})
        assert gate.check("read_file") is None
        assert gate.check("grep") is None

    def test_通配符拒绝一切(self, gate):
        gate.write_policy({"roles": {"admin": {"denied_tools": ["*"]}}})
        for name in ("read_file", "write_file", "grep", "edit", "shell_execute", "任意工具"):
            result = gate.check(name)
            assert result is not None, name
            assert result["blocked"] is True
            assert result["error_code"] == "PERMISSION_DENIED"
            assert name in result["error"]
        assert "通配符" in gate.check("read_file")["error"]

    def test_多角色_denied_tools_并集生效(self, gate):
        """并集：任一角色拉黑的工具都拦；都没拉黑的工具放行"""
        gate.write_policy({
            "roles": {
                "admin": {"allowed_tools": ["*"], "denied_tools": []},
                "developer": {"denied_tools": ["system_format"]},
                "guest": {"denied_tools": ["shell_execute", " "], "extra": 1},
                "auditor": {"denied_tools": ["write_file"]},
            }
        })
        assert gate.check("system_format")["blocked"] is True
        assert gate.check("shell_execute")["blocked"] is True
        assert gate.check("write_file")["blocked"] is True
        assert gate.check("read_file") is None
        assert gate.check(" ") is None          # 空白条目被忽略，且空名放行

    def test_黑名单写成_canonical_id_也能命中原名(self, gate):
        """策略里写 cp.<source>.<name>，用工具原名调用同样命中（双向等价）"""
        gate.write_policy({"roles": {"r": {"denied_tools": ["cp.builtin.write_file"]}}})
        assert gate.check("write_file")["blocked"] is True
        assert gate.check("cp.builtin.write_file")["blocked"] is True
        assert gate.check("read_file") is None

    def test_大小写不敏感(self, gate):
        gate.write_policy({"roles": {"r": {"denied_tools": ["Write_File"]}}})
        assert gate.check("write_file")["blocked"] is True


# ════════════════════════════════════════════════════════════
#  三、描述符审批要求（trust.requires_approval）
# ════════════════════════════════════════════════════════════

class TestRequiresApproval:

    def test_描述符要求审批即拒绝(self, gate):
        gate.write_descriptors({"descriptors": {
            "cp.builtin.write_file": _descriptor_entry("cp.builtin.write_file",
                                                       "write_file", True)}})
        result = gate.check("write_file")
        assert result is not None
        assert result["blocked"] is True
        assert result["error_code"] == "PERMISSION_DENIED"
        assert "write_file" in result["error"]                     # 工具名
        assert "requires_approval" in result["error"]              # 规则来源
        assert "cp.builtin.write_file" in result["error"]          # 命中的能力 id
        assert "审批" in result["error"]

    def test_canonical_id_形态与原名双向命中(self, gate):
        """台账 id 为 cp.<source>.<name>：原名调用命中；id 形态调用亦命中"""
        gate.write_descriptors({"descriptors": {
            "cp.mcp.write_file": _descriptor_entry("cp.mcp.write_file",
                                                   "write_file", True)}})
        assert gate.check("write_file")["blocked"] is True
        assert gate.check("cp.mcp.write_file")["blocked"] is True
        assert gate.check("cp.builtin.write_file")["blocked"] is True   # 名字段同源
        assert gate.check("read_file") is None

    def test_描述符列表形态也能命中(self, gate):
        """台账为 list[entry]（id 取自 meta.id）时同样生效"""
        gate.write_descriptors([_descriptor_entry("cp.builtin.shell_execute",
                                                  "shell_execute", True)])
        assert gate.check("shell_execute")["blocked"] is True
        assert gate.check("read_file") is None

    def test_requires_approval_为假时放行(self, gate):
        gate.write_descriptors({"descriptors": {
            "cp.builtin.write_file": _descriptor_entry("cp.builtin.write_file",
                                                       "write_file", False)}})
        assert gate.check("write_file") is None

    def test_字符串_true_也算要求审批(self, gate):
        """宽松判定：数据侧写成 "true" 同样拦截（不认识的值才放行）"""
        gate.write_descriptors({"descriptors": {
            "cp.builtin.write_file": _descriptor_entry("cp.builtin.write_file",
                                                       "write_file", "true")}})
        assert gate.check("write_file")["blocked"] is True

    def test_黑名单与审批两规则互不干扰(self, gate):
        """只有描述符规则命中时，报错来源必须是描述符而非黑名单"""
        gate.write_policy({"roles": {"r": {"denied_tools": ["system_format"]}}})
        gate.write_descriptors({"descriptors": {
            "cp.builtin.write_file": _descriptor_entry("cp.builtin.write_file",
                                                       "write_file", True)}})
        assert "requires_approval" in gate.check("write_file")["error"]
        assert "denied_tools" in gate.check("system_format")["error"]
        assert gate.check("read_file") is None


# ════════════════════════════════════════════════════════════
#  四、fail-open：文件缺失 / JSON 损坏 / 结构异常 / 自身异常
# ════════════════════════════════════════════════════════════

class TestFailOpen:

    def test_策略文件缺失放行(self, gate):
        gate.policy_path.unlink()
        gate.mod._reset_cache()
        assert gate.check("write_file") is None

    def test_描述符文件缺失放行(self, gate):
        gate.desc_path.unlink()
        gate.mod._reset_cache()
        assert gate.check("write_file") is None

    def test_两个文件都缺失且目录也不存在时放行(self, gate, monkeypatch, tmp_path):
        monkeypatch.setattr(gate.mod, "POLICY_POLICIES_PATH",
                            str(tmp_path / "nope" / "a.json"))
        monkeypatch.setattr(gate.mod, "DESCRIPTORS_PATH",
                            str(tmp_path / "nope" / "b.json"))
        gate.mod._reset_cache()
        assert gate.check("write_file") is None

    def test_JSON_损坏放行(self, gate):
        gate.write_policy_raw("{这不是 JSON")
        gate.write_descriptors_raw("[1, 2,")
        gate.mod._reset_cache()
        assert gate.check("write_file") is None

    def test_策略结构异常放行(self, gate):
        for bad in ([1, 2, 3], "text", 42, {"roles": ["not-a-dict"]},
                    {"roles": {"r": "not-a-dict"}}, {"roles": {"r": {"denied_tools": "x"}}},
                    {"roles": {"r": {"denied_tools": [None, 7, {"a": 1}]}}}):
            gate.write_policy(bad)
            gate.mod._reset_cache()
            assert gate.check("write_file") is None, bad

    def test_描述符结构异常放行(self, gate):
        for bad in ("text", 42, {"descriptors": "not-a-dict"},
                    {"descriptors": [None, 7, "x"]},
                    {"descriptors": {"cp.x.y": "not-a-dict"}},
                    {"descriptors": {"cp.x.y": {"trust": "not-a-dict"}}}):
            gate.write_descriptors(bad)
            gate.mod._reset_cache()
            assert gate.check("write_file") is None, bad

    def test_闸门自身异常时放行(self, gate, monkeypatch):
        """双保险：判定内部抛异常也必须放行（本闸门的 bug 不得阻断工具执行）"""
        def _boom(_data):
            raise RuntimeError("builder down")

        monkeypatch.setattr(gate.mod, "_build_denied_union", _boom)
        gate.mod._reset_cache()
        assert gate.check("write_file") is None

    def test_环境变量读取异常时不影响放行(self, gate, monkeypatch):
        """只替换闸门模块内的 ``os`` 名字绑定，不动进程级 ``os.environ``"""
        import os as _os

        class _BadEnviron(dict):
            def get(self, *a, **k):
                raise RuntimeError("env down")

        class _OsShim:
            environ = _BadEnviron()

            def __getattr__(self, name):
                return getattr(_os, name)

        monkeypatch.setattr(gate.mod, "os", _OsShim())
        assert gate.check("read_file") is None

    def test_空工具名放行(self, gate):
        gate.write_policy({"roles": {"r": {"denied_tools": ["*"]}}})
        assert gate.check("") is None
        assert gate.check(None) is None

    def test_不抛异常且只读不写(self, gate):
        """调用前后两份文件字节不变（闸门绝不修改数据文件）"""
        gate.write_policy({"roles": {"r": {"denied_tools": ["write_file"]}}})
        before = (gate.policy_path.read_bytes(), gate.desc_path.read_bytes())
        gate.check("write_file")
        gate.check("read_file")
        after = (gate.policy_path.read_bytes(), gate.desc_path.read_bytes())
        assert before == after

    def test_缓存按文件指纹失效(self, gate):
        """改写策略文件后立即生效（缓存必须按指纹失效）"""
        gate.write_policy({"roles": {"r": {"denied_tools": ["write_file"]}}})
        assert gate.check("write_file")["blocked"] is True
        gate.write_policy({"roles": {"r": {"denied_tools": []}}})
        assert gate.check("write_file") is None


# ════════════════════════════════════════════════════════════
#  五、接线：唯一汇聚点 tools.call()（含 orchestrator 直连路径）
# ════════════════════════════════════════════════════════════

#: 探针工具名（注册进真实注册表，用后精确还原）
PROBE_TOOL = "probe_gate_tool"


def _make_service(monkeypatch):
    """构造 ToolCallingService，并把 trace recorder 关掉（不落任何 trace 库）"""
    from agent.tool_calling import ToolCallingService

    service = ToolCallingService(llm_service=MagicMock(model="test-model"))

    def _no_recorder(*_a, **_k):
        raise RuntimeError("recorder disabled in unit test")

    from agent.observability.tool_trace import ToolTraceRecorder
    monkeypatch.setattr(ToolTraceRecorder, "instance", staticmethod(_no_recorder))
    return service


@pytest.fixture
def probe_tool():
    """注册一个计数探针工具到真实注册表；用完还原注册表与健康表"""
    from agent import tools as registry

    calls = {"n": 0}

    def _handler(**kwargs):
        calls["n"] += 1
        return {"ok": True, "result": "handler-ran", "echo": kwargs}

    saved_entry = registry._registry.get(PROBE_TOOL)
    saved_health = registry._tool_health.get(PROBE_TOOL)
    registry.register(PROBE_TOOL, "闸门接线探针工具", handler=_handler)
    try:
        yield calls
    finally:
        if saved_entry is None:
            registry.unregister(PROBE_TOOL)
        else:
            registry._registry[PROBE_TOOL] = saved_entry
            registry._registry_version += 1
        if saved_health is None:
            registry._tool_health.pop(PROBE_TOOL, None)
        else:
            registry._tool_health[PROBE_TOOL] = saved_health


class TestToolsCallIsEnforcementPoint:
    """完整性用例：绕过 ``_execute_safe`` 的直连路径同样受闸门约束

    ``agent/orchestrator/orchestrator.py:3519`` / ``:3557`` 直接调
    ``_tools.call(_fn_name, **_fn_args)``，**不经过** ``_execute_safe``；强制点
    因此必须落在唯一汇聚点 ``agent/tools/__init__.py::call()`` 上。
    """

    def test_直连_tools_call_被闸门拒绝(self, gate):
        """orchestrator 直连形态（位置参数工具名）"""
        from agent import tools as registry

        gate.write_policy({"roles": {"guest": {"denied_tools": ["read_file"]}}})
        result = registry.call("read_file", path="x.txt")

        assert result["ok"] is False
        assert result["blocked"] is True
        assert result["error_code"] == "PERMISSION_DENIED"
        assert "read_file" in result["error"]

    def test_直连_tools_call_被闸门拒绝_关键字名形态(self, gate):
        """``call(**params_with_name)`` 形态同样被拦"""
        from agent import tools as registry

        gate.write_policy({"roles": {"r": {"denied_tools": ["write_file"]}}})
        result = registry.call(name="write_file", path="x.txt", content="c")

        assert result["blocked"] is True
        assert result["error_code"] == "PERMISSION_DENIED"

    def test_拒绝发生在注册表查找与自动发现之前(self, gate, monkeypatch):
        """未注册工具命中闸门时返回拒绝 dict，而不是抛 ToolError"""
        from agent import tools as registry

        discovery = MagicMock()
        monkeypatch.setattr(registry, "_discovery_service", discovery)
        gate.write_policy({"roles": {"r": {"denied_tools": ["never_registered_tool_x"]}}})
        assert "never_registered_tool_x" not in registry._registry

        result = registry.call("never_registered_tool_x")

        assert result["blocked"] is True
        assert result["error_code"] == "PERMISSION_DENIED"
        discovery.on_tool_not_found.assert_not_called()

    def test_描述符审批也在直连路径生效(self, gate, probe_tool):
        from agent import tools as registry

        cid = "cp.builtin." + PROBE_TOOL
        gate.write_descriptors({"descriptors": {
            cid: _descriptor_entry(cid, PROBE_TOOL, True)}})

        result = registry.call(PROBE_TOOL, a=1)

        assert result["blocked"] is True
        assert result["error_code"] == "PERMISSION_DENIED"
        assert probe_tool["n"] == 0          # handler 未被调用

    def test_闸门排在限流之前(self, gate, monkeypatch, probe_tool):
        """拒绝要尽早：即使限流器已超限，闸门拒绝也先返回且不触碰限流器"""
        from agent import tools as registry

        limiter = MagicMock()
        limiter.check.return_value = False   # 若走到限流，会返回"频率过高"
        monkeypatch.setattr(registry, "_rate_limiter", limiter)
        gate.write_policy({"roles": {"r": {"denied_tools": [PROBE_TOOL]}}})

        result = registry.call(PROBE_TOOL)

        assert result["error_code"] == "PERMISSION_DENIED"
        limiter.check.assert_not_called()
        assert probe_tool["n"] == 0

    def test_放行时_call_行为不变(self, gate, monkeypatch, probe_tool):
        """零行为回归：未被拦时限流、健康追踪与返回值与接线前一致"""
        from agent import tools as registry

        limiter = MagicMock()
        limiter.check.return_value = True
        monkeypatch.setattr(registry, "_rate_limiter", limiter)

        result = registry.call(PROBE_TOOL, a=1)

        assert result["ok"] is True and result["result"] == "handler-ran"
        assert result["echo"] == {"a": 1}
        assert probe_tool["n"] == 1
        limiter.check.assert_called_once_with(PROBE_TOOL)
        assert registry._tool_health[PROBE_TOOL]["last_ok"] is True

    def test_未知工具的_ToolError_语义不变(self, gate):
        from agent import tools as registry

        with pytest.raises(registry.ToolError):
            registry.call("definitely_not_registered_tool_xyz")

    def test_缺失工具名的_ToolError_语义不变(self, gate):
        from agent import tools as registry

        with pytest.raises(registry.ToolError):
            registry.call()

    def test_开关关闭时直连路径也放行(self, gate, monkeypatch, probe_tool):
        from agent import tools as registry

        gate.write_policy({"roles": {"r": {"denied_tools": [PROBE_TOOL]}}})
        monkeypatch.setenv(gate.mod.GATE_ENABLED_ENV, "off")

        result = registry.call(PROBE_TOOL)

        assert result["ok"] is True
        assert probe_tool["n"] == 1

    def test_闸门模块导入失败时_call_照常执行(self, probe_tool):
        """闸门不可用（导入异常）→ 放行；sys.modules 里的 None 会让 import 抛 ImportError"""
        import sys
        from agent import tools as registry

        with patch.dict(sys.modules, {"agent.tool_gate": None}):
            result = registry.call(PROBE_TOOL)

        assert result["ok"] is True
        assert probe_tool["n"] == 1


class TestExecuteSafeRetrySemantics:
    """``_execute_safe`` 对"拒绝"只尝试一次（拒绝不会被重试 3 次）

    既有控制流只在**异常**时重试——``for attempt in range(3)`` 里的
    ``return result`` 是无条件的；闸门拒绝是**返回 dict**而非抛异常，因此天然
    只走一次。本用例把这条语义钉死：core 调 1 次、闸门调 1 次、handler 0 次。
    """

    def test_拒绝只尝试一次_闸门只被调用一次(self, gate, monkeypatch, probe_tool):
        import agent.tool_gate as G

        gate.write_policy({"roles": {"r": {"denied_tools": [PROBE_TOOL]}}})
        service = _make_service(monkeypatch)

        counts = {"core": 0, "gate": 0}
        real_core = service._execute_safe_core
        monkeypatch.setattr(
            service, "_execute_safe_core",
            lambda fn, a: (counts.__setitem__("core", counts["core"] + 1),
                           real_core(fn, a))[1])
        real_check = G.check_tool_call
        monkeypatch.setattr(
            G, "check_tool_call",
            lambda *a, **k: (counts.__setitem__("gate", counts["gate"] + 1),
                             real_check(*a, **k))[1])

        result = service._execute_safe(PROBE_TOOL, {})

        assert counts == {"core": 1, "gate": 1}      # 不是 3 次
        assert probe_tool["n"] == 0                  # handler 从未执行
        assert result["ok"] is False and result["blocked"] is True
        assert result["error_code"] == "PERMISSION_DENIED"
        assert result == real_check(PROBE_TOOL, {})  # 原样返回

    def test_经_execute_safe_放行时正常执行一次(self, gate, monkeypatch, probe_tool):
        service = _make_service(monkeypatch)

        result = service._execute_safe(PROBE_TOOL, {"x": 1})

        assert result["ok"] is True
        assert probe_tool["n"] == 1

    def test_经_execute_safe_通配符拒绝时一次也不执行(self, gate, monkeypatch, probe_tool):
        gate.write_policy({"roles": {"admin": {"denied_tools": ["*"]}}})
        service = _make_service(monkeypatch)

        result = service._execute_safe(PROBE_TOOL, {})

        assert probe_tool["n"] == 0
        assert result["blocked"] is True

    def test_经_execute_safe_描述符审批拒绝(self, gate, monkeypatch, probe_tool):
        cid = "cp.builtin." + PROBE_TOOL
        gate.write_descriptors({"descriptors": {
            cid: _descriptor_entry(cid, PROBE_TOOL, True)}})
        service = _make_service(monkeypatch)

        result = service._execute_safe(PROBE_TOOL, {})

        assert probe_tool["n"] == 0
        assert result["error_code"] == "PERMISSION_DENIED"
