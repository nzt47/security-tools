"""E4-1 第一期：脚本编排运行时（独立 node 子进程 + JSONL 工具 RPC）。

判据来自 docs/plan/2026-11-27-p4-编排安全设计.md §8（实现完成时逐条验）：
  1) 开关关 ⇒ disabled，**不 spawn、不写 run_dir**；
  2) 白名单：write/shell/govern/编排自身/terminal 一律 denied_tool；
  3) 五道上限：墙钟 / 输出 / 调用次数 / 内存（平台各一条），越界杀树；
  4) 协议：脚本拿不到 require/process；未知工具名回 denied_tool；
  5) 审计 ≥3 条事件（run / tool_call / exit），工具台账**不重复计数**；
  6) env 全部登记 registry（由 test_settings_registry 的零缺口门禁守）；
  7) 前端：本工具不建路由（无路由即无引用问题）。
"""
from __future__ import annotations

import os
import shutil

import pytest

from agent.orchestration import runtime, whitelist


def _node_available() -> bool:
    """node 存在**且**支持 --permission/--allow-net 才算可用

    【为什么不能只看 shutil.which】GitHub Actions 的 runner 为 actions 运行时预装了 node
    （可能是 20.x）—— 它就在 PATH 上，但**没有** --permission/--allow-net。只看 which
    会让运行时用例在 CI 上真的跑起来并失败（本机 Node 25 当然绿）。设计稿的口径是
    "node 是可选运行时，CI 不需要它" ⇒ 探针判不可用就跳过。
    """
    if shutil.which(runtime.node_binary()) is None:
        return False
    try:
        return bool(runtime.probe_node().get("ok"))
    except Exception:  # noqa: BLE001 探针异常按不可用处理（跳过而不是假红）
        return False


NODE_ONLY = pytest.mark.skipif(
    not _node_available(),
    reason="本机 node 缺失或缺少 --permission/--allow-net（CI 不要求装 node）")


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """每个用例：干净探针缓存 + 独立 run 根 + 默认关开关"""
    runtime.reset_probe_cache()
    monkeypatch.setattr(runtime, "RUN_ROOT", tmp_path / "orchestration")
    monkeypatch.setenv(runtime.ENABLED_ENV, "0")
    for key in (runtime.NODE_PATH_ENV, runtime.TIMEOUT_ENV, runtime.MAX_CALLS_ENV,
                runtime.MAX_OUTPUT_ENV, runtime.MAX_MEMORY_ENV, runtime.RESULT_CHARS_ENV):
        monkeypatch.delenv(key, raising=False)
    yield
    runtime.reset_probe_cache()


@pytest.fixture
def registered_read_tool(monkeypatch):
    """注册一个真实只读工具（read_file）供脚本调用"""
    from agent.tools.file_tools_reg import register_all as reg_file

    reg_file(None)
    return "read_file"


# ════════════════════════════════════════════════════════════
#  1. 开关关：disabled，不 spawn、不写 run_dir
# ════════════════════════════════════════════════════════════

class TestDisabledByDefault:
    def test_默认关闭(self):
        assert runtime.enabled() is False

    def test_关的时候不建_run_dir(self, tmp_path):
        root = tmp_path / "orchestration"
        result = runtime.run("return 1;")
        assert result["ok"] is False
        assert result["code"] == "disabled"
        assert not root.exists(), "开关关闭时**不得**写 run_dir（设计判据 §8.1）"

    def test_受限权限预设一律拒绝(self, monkeypatch, tmp_path):
        monkeypatch.setenv(runtime.ENABLED_ENV, "1")
        monkeypatch.setenv("CP_PERMISSION_PRESET", "workspace-write")
        result = runtime.run("return 1;")
        assert result["code"] == "confined"
        assert not (tmp_path / "orchestration").exists()

    def test_script_为空或超长直接拒(self, monkeypatch):
        monkeypatch.setenv(runtime.ENABLED_ENV, "1")
        assert runtime.run("   ")["code"] == "bad_script"
        assert runtime.run("x" * (runtime.MAX_SCRIPT_CHARS + 1))["code"] == "bad_script"


# ════════════════════════════════════════════════════════════
#  2. 白名单：三重交集 + 永远拒绝
# ════════════════════════════════════════════════════════════

class TestWhitelist:
    def test_静态清单只读且不含永远禁止项(self):
        from agent.lines import load_tool_meta

        meta = load_tool_meta()
        for name in whitelist.STATIC_ALLOWED:
            assert name not in whitelist.NEVER_ALLOWED, f"{name} 同时出现在静态清单与永远禁止项"
            item = meta.get(name)
            assert item is not None, f"{name} 在 YAML 里没有登记"
            assert str(item.effect) == "read", f"{name} 的 effect 不是 read"
            assert str(item.plane) != "govern", f"{name} 属治理平面"

    def test_治理平面与写工具一律出局(self, monkeypatch):
        monkeypatch.setattr(whitelist, "_available_tool_names", lambda: (
            "read_file", "grep", "write_file", "shell_execute", "ext_install",
            "terminal", "run_orchestration_script",
        ))
        result = whitelist.resolve(line="engineering", allow_tools=["write_file"])
        assert result["ok"] is True, result
        assert "read_file" in result["tools"]
        for denied in ("write_file", "shell_execute", "ext_install", "terminal",
                       "run_orchestration_script"):
            assert denied not in result["tools"], f"{denied} 不该进脚本白名单"

    def test_静态清单被误加也拒绝(self, monkeypatch):
        """纵深防御：即使有人把 terminal/编排自身误写进静态清单，也一律出局"""
        monkeypatch.setattr(whitelist, "_available_tool_names", lambda: (
            "read_file", "terminal", "run_orchestration_script",
        ))
        monkeypatch.setattr(
            whitelist, "STATIC_ALLOWED",
            tuple(whitelist.STATIC_ALLOWED) + ("terminal", "run_orchestration_script"),
        )
        result = whitelist.resolve(line="engineering",
                                   allow_tools=["terminal", "run_orchestration_script"])
        assert "read_file" in result["tools"]
        assert result["dropped"].get("terminal") == "never_allowed"
        assert result["dropped"].get("run_orchestration_script") == "never_allowed"

    def test_allow_tools_不能放宽静态清单(self, monkeypatch):
        monkeypatch.setattr(whitelist, "_available_tool_names", lambda: (
            "read_file", "write_file",
        ))
        result = whitelist.resolve(line="engineering", allow_tools=["write_file"])
        assert "write_file" not in result["tools"]

    def test_主线不存在按不可用处理(self):
        result = whitelist.resolve(line="definitely-not-a-line")
        assert result["ok"] is False
        assert result["code"] == "line_unavailable"

    def test_注册表为空时白名单为空(self, monkeypatch):
        monkeypatch.setattr(whitelist, "_available_tool_names", lambda: ())
        result = whitelist.resolve(line="engineering")
        assert result["ok"] is True
        assert result["tools"] == ()

    def test_真实注册表下的白名单全是只读工具(self):
        result = whitelist.resolve(line="engineering")
        assert result["ok"] is True, result
        from agent.lines import load_tool_meta

        meta = load_tool_meta()
        assert isinstance(result["tools"], tuple)
        for name in result["tools"]:
            assert name in whitelist.STATIC_ALLOWED
            assert str(meta[name].effect) == "read"


# ════════════════════════════════════════════════════════════
#  3. 协议与隔离（需要 node）
# ════════════════════════════════════════════════════════════

class TestProtocol:
    @NODE_ONLY
    def test_脚本拿不到_process_与_require(self, monkeypatch, registered_read_tool):
        monkeypatch.setenv(runtime.ENABLED_ENV, "1")
        script = (
            'return {p: typeof process, r: typeof require,'
            ' g: typeof globalThis.process, m: typeof globalThis.require};'
        )
        result = runtime.run(script, timeout_sec=30)
        assert result["ok"] is True, result
        assert result["result"] == {"p": "undefined", "r": "undefined",
                                    "g": "undefined", "m": "undefined"}

    @NODE_ONLY
    def test_只读工具能调通(self, monkeypatch, registered_read_tool):
        monkeypatch.setenv(runtime.ENABLED_ENV, "1")
        script = (
            'const r = await tools.call("read_file", {path: "README.md", range: "1-1"});'
            ' return {ok: r.ok, has: !!(r.value)};'
        )
        result = runtime.run(script, timeout_sec=30)
        assert result["ok"] is True, result
        assert result["result"] == {"ok": True, "has": True}
        assert result["calls"] == 1

    @NODE_ONLY
    def test_未知工具名回_denied_tool(self, monkeypatch, registered_read_tool):
        monkeypatch.setenv(runtime.ENABLED_ENV, "1")
        script = 'const r = await tools.call("no_such_tool", {}); return r;'
        result = runtime.run(script, timeout_sec=30)
        assert result["ok"] is True
        assert result["result"]["code"] == "denied_tool"
        assert result["denied"] == 1

    @NODE_ONLY
    def test_写工具被白名单拒(self, monkeypatch, registered_read_tool):
        monkeypatch.setenv(runtime.ENABLED_ENV, "1")
        script = 'const r = await tools.call("write_file", {path: "x", content: "y"}); return r;'
        result = runtime.run(script, allow_tools=["write_file"], timeout_sec=30)
        assert result["result"]["code"] == "denied_tool"
        assert result["denied"] == 1

    @NODE_ONLY
    def test_spawn_默认关闭(self, monkeypatch, registered_read_tool):
        monkeypatch.setenv(runtime.ENABLED_ENV, "1")
        script = 'const r = await spawn([{goal: "x"}]); return r;'
        result = runtime.run(script, timeout_sec=30)
        assert result["result"]["code"] == "denied_tool"
        assert result["spawned"] == 0


# ════════════════════════════════════════════════════════════
#  4. 五道上限
# ════════════════════════════════════════════════════════════

class TestCaps:
    @NODE_ONLY
    def test_墙钟超时杀树(self, monkeypatch, registered_read_tool):
        monkeypatch.setenv(runtime.ENABLED_ENV, "1")
        result = runtime.run("while (true) {}", timeout_sec=2)
        assert result["ok"] is False
        assert result["code"] == "timeout"
        assert result["duration_ms"] < 30000

    @NODE_ONLY
    def test_调用次数上限(self, monkeypatch, registered_read_tool):
        monkeypatch.setenv(runtime.ENABLED_ENV, "1")
        script = (
            'for (;;) { await tools.call("read_file", {path: "README.md", range: "1-1"}); }'
        )
        result = runtime.run(script, timeout_sec=30, max_calls=2)
        assert result["code"] == "cap_calls"
        assert result["calls"] == 3, "第 3 次调用必须被拒并如实计数"

    @NODE_ONLY
    def test_输出上限(self, monkeypatch, registered_read_tool):
        monkeypatch.setenv(runtime.ENABLED_ENV, "1")
        monkeypatch.setenv(runtime.MAX_OUTPUT_ENV, "4096")
        result = runtime.run('for (;;) { console.log("x".repeat(500)); }', timeout_sec=30)
        assert result["code"] == "cap_output"

    @NODE_ONLY
    def test_内存上限(self, monkeypatch, registered_read_tool):
        monkeypatch.setenv(runtime.ENABLED_ENV, "1")
        monkeypatch.setenv(runtime.MAX_MEMORY_ENV, "64")
        script = "const a=[]; for (;;) { a.push(new Array(100000).fill(1)); }"
        result = runtime.run(script, timeout_sec=90)
        # 两种可接受结局：node 自己 OOM（cap_memory）或墙钟兜住（timeout）
        assert result["code"] in ("cap_memory", "timeout"), result

    def test_上限从_env_读且夹回区间(self, monkeypatch):
        monkeypatch.setenv(runtime.TIMEOUT_ENV, "99999")
        monkeypatch.setenv(runtime.MAX_CALLS_ENV, "-5")
        monkeypatch.setenv(runtime.MAX_OUTPUT_ENV, "1")
        monkeypatch.setenv(runtime.MAX_MEMORY_ENV, "0")
        caps = runtime.limits()
        assert caps["timeout_sec"] == runtime.MAX_TIMEOUT_SEC
        assert caps["max_calls"] == 1
        assert caps["max_output_bytes"] == 4096
        assert caps["max_memory_mb"] == 64

    def test_node_缺失回_no_node(self, monkeypatch):
        monkeypatch.setenv(runtime.ENABLED_ENV, "1")
        monkeypatch.setenv(runtime.NODE_PATH_ENV, "definitely-not-a-node-binary-xyz")
        result = runtime.run("return 1;")
        assert result["code"] == "no_node"


# ════════════════════════════════════════════════════════════
#  5. 审计三件套 + 工具台账不重复计数
# ════════════════════════════════════════════════════════════

class TestAudit:
    @NODE_ONLY
    def test_一次_run_至少三条审计(self, monkeypatch, registered_read_tool):
        import agent.audit.facade as facade

        events = []
        monkeypatch.setattr(facade, "record",
                            lambda action, *a, **kw: events.append(action))
        monkeypatch.setenv(runtime.ENABLED_ENV, "1")
        script = 'const r = await tools.call("read_file", {path: "README.md", range: "1-1"}); return !!r.ok;'
        result = runtime.run(script, timeout_sec=30)
        assert result["ok"] is True
        assert "orchestration_run" in events
        assert "orchestration_tool_call" in events
        assert "orchestration_exit" in events
        assert len(events) >= 3

    @NODE_ONLY
    def test_工具台账不在编排层重复记(self, monkeypatch, registered_read_tool):
        import agent.skills_mgmt.usage_log as usage_log

        calls = []
        monkeypatch.setattr(usage_log, "record_tool",
                            lambda *a, **kw: calls.append((a, kw)))
        monkeypatch.setenv(runtime.ENABLED_ENV, "1")
        script = 'const r = await tools.call("read_file", {path: "README.md", range: "1-1"}); return !!r.ok;'
        result = runtime.run(script, timeout_sec=30)
        assert result["ok"] is True
        assert calls == [], "工具台账只在真实执行点记一次；编排层不得再记（设计判据 §8.5）"


# ════════════════════════════════════════════════════════════
#  6. 工具面（注册与 YAML 一致）
# ════════════════════════════════════════════════════════════

class TestToolSurface:
    def test_工具已注册且_schema_有必填_script(self):
        from agent import tools as _tools
        from agent.tools.orchestration_tools import register_all

        register_all(None)
        schema = _tools.get_tool_schema("run_orchestration_script")
        assert schema is not None
        assert schema.get("required") == ["script"]
        assert set(schema["properties"]) >= {"script", "line", "allow_tools",
                                             "allow_spawn", "timeout_seconds", "max_calls"}

    def test_allow_tools_支持字符串写法(self):
        from agent.tools.orchestration_tools import _normalize_tools

        assert _normalize_tools("read_file, grep，docx_read") == [
            "read_file", "grep", "docx_read"]
        assert _normalize_tools(None) == []


# ════════════════════════════════════════════════════════════
#  7. 跨轮次显式状态（E4-2③）
# ════════════════════════════════════════════════════════════

class TestCrossRunState:
    """state 只经 state.get/set/clear 显式读写；不隐式继承、按会话隔离"""

    @NODE_ONLY
    def test_下一次_run_显式读到上一次写的状态(self, monkeypatch):
        monkeypatch.setenv(runtime.ENABLED_ENV, "1")
        first = runtime.run(
            'await state.set({step: 1}); return await state.get();',
            timeout_sec=30, session_id="sess-a")
        assert first["ok"] is True, first
        assert first["result"] == {"ok": True, "value": {"step": 1}}
        assert first["state_ops"] == 2

        second = runtime.run('return await state.get();',
                             timeout_sec=30, session_id="sess-a")
        assert second["ok"] is True, second
        assert second["result"] == {"ok": True, "value": {"step": 1}}

    @NODE_ONLY
    def test_不调用_get_就看不到状态(self, monkeypatch):
        monkeypatch.setenv(runtime.ENABLED_ENV, "1")
        runtime.run('await state.set({secret: "x"}); return 1;',
                    timeout_sec=30, session_id="sess-a")
        # 脚本上下文里没有"上次状态"的快照变量：只能通过显式 state.get() 拿到
        result = runtime.run(
            'return {has: typeof state, injected: typeof globalThis.__state};',
            timeout_sec=30, session_id="sess-a")
        assert result["result"] == {"has": "object", "injected": "undefined"}

    @NODE_ONLY
    def test_会话之间互不可见(self, monkeypatch):
        monkeypatch.setenv(runtime.ENABLED_ENV, "1")
        runtime.run('await state.set("A"); return 1;', timeout_sec=30, session_id="sess-a")
        other = runtime.run('return await state.get();', timeout_sec=30, session_id="sess-b")
        assert other["result"] == {"ok": True, "value": None}

    def test_没有会话绑定回_unavailable(self, monkeypatch):
        monkeypatch.setenv(runtime.ENABLED_ENV, "1")
        # 开关开了但没绑会话：不 spawn 也没关系——这里只验 state 模块的失败码
        from agent.orchestration import state

        result = state.apply("get", None, session_id="", root=runtime.RUN_ROOT)
        assert result["ok"] is False and result["code"] == "state_unavailable"

    @NODE_ONLY
    def test_脚本内超限回_state_too_large(self, monkeypatch):
        monkeypatch.setenv(runtime.ENABLED_ENV, "1")
        result = runtime.run(
            'const r = await state.set("x".repeat(70000)); return r;',
            timeout_sec=60, session_id="sess-a")
        assert result["ok"] is True, result
        assert result["result"]["code"] == "state_too_large"

    def test_回收旧run不删状态目录(self, monkeypatch):
        monkeypatch.setenv(runtime.ENABLED_ENV, "1")
        (runtime.RUN_ROOT / "state").mkdir(parents=True)
        (runtime.RUN_ROOT / "run-old").mkdir()
        runtime._prune_runs(keep=0)
        assert (runtime.RUN_ROOT / "state").is_dir()
        assert not (runtime.RUN_ROOT / "run-old").exists()

