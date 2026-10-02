# -*- coding: utf-8 -*-
"""上下文三旋钮「口径对齐 + 真正生效」回归锚（2026-10-02）

背景（线上提问：「上下文最大 Token / 单次发送 / 单次回复的默认值是不是太小？没几下就用完」）:

  实测 provider（``GET https://api.deepseek.com/v1/models``）：``deepseek-flash`` =
  DeepSeek-V4.1-Flash，``context_window=1048576``、``max_output_tokens=393216``。
  而修复前云枢这边是：

    · ``memory.token_limit`` 代码默认 4096、校验器上限 32768（config.yaml 想写 131072 会被打回）；
    · 面板百分比的分母取 ``_cfg`` 里的副本，**与真正组装上下文的 ``_memory_token_limit`` 不是一个数**；
    · ``status_level`` 用「累计压缩次数 ≥5」判 critical ⇒ 占用 22% 也常年报红；
    · 「单次发送 / 单次回复」两个旋钮**全仓没有任何强制点**（只有存取 + 展示），
      真正卡住回复长度的是 orchestrator 里按模型名硬编码的 8192。

本文件锁死修好后的契约：
  1. ``_resolve_max_output_tokens``：配置优先、启发式只当下限（单调不减）、硬上限收敛；
  2. ``_push_runtime_window``：把新窗口推给**正在跑**的编排器（否则滑块只改了个没人读的副本）；
  3. ``/api/context/status``：分母 = 编排窗口 + 披露来源；档位按**占用**判；摘要退化单列；
     取不到分母时如实报 ``None``（不拿硬编码冒充分母）；
  4. ``POST /api/context/config``：值域放宽到模型量级，且返回"是否当场生效"。
"""
from __future__ import annotations

import os
import sys
import types

import pytest

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from agent.orchestrator.orchestrator import Orchestrator  # noqa: E402


def _bare_orchestrator(memory_cfg=None):
    """不跑 ``__init__`` 的编排器壳（只测纯计算方法）"""
    orch = Orchestrator.__new__(Orchestrator)
    orch._config = {"memory": dict(memory_cfg or {})}
    return orch


class TestResolveMaxOutputTokens:
    """单次回复 max_tokens：配置优先 + 启发式兜底 + 硬上限收敛"""

    def test_未配置时与改动前逐字一致(self):
        """回归护栏：没有配置 ⇒ 仍是 8192（小模型档），不许悄悄变短或变长"""
        assert _bare_orchestrator()._resolve_max_output_tokens("deepseek-flash") == 8192
        assert _bare_orchestrator()._resolve_max_output_tokens("deepseek-v4-pro") == 16384

    def test_配置生效(self):
        """这是本次修复的核心：界面上的「单次回复」旋钮**真的**决定 max_tokens"""
        orch = _bare_orchestrator({"per_message_recv_limit": 32768})
        assert orch._resolve_max_output_tokens("deepseek-flash") == 32768

    def test_单调不减_配置缺失不会让回复更短(self):
        """配置为 0 / 缺键 / 垃圾值 ⇒ 回落到该模型档位，而不是更小"""
        for cfg in ({}, {"per_message_recv_limit": 0}, {"per_message_recv_limit": None},
                    {"per_message_recv_limit": "很多"}, {"per_message_recv_limit": -5}):
            assert _bare_orchestrator(cfg)._resolve_max_output_tokens("deepseek-flash") == 8192
        # 配置值小于档位（手滑调小）⇒ 仍取档位：绝不让配置把回复压到比以前更短
        orch = _bare_orchestrator({"per_message_recv_limit": 512})
        assert orch._resolve_max_output_tokens("deepseek-flash") == 8192
        assert orch._resolve_max_output_tokens("deepseek-v4-pro") == 16384

    def test_硬上限收敛_手滑的大数字不会打回400(self, monkeypatch):
        monkeypatch.delenv("CP_CHAT_MAX_OUTPUT_CEILING", raising=False)
        orch = _bare_orchestrator({"per_message_recv_limit": 99999999})
        assert orch._resolve_max_output_tokens("deepseek-flash") == 131072

    def test_硬上限可由环境变量覆盖(self, monkeypatch):
        monkeypatch.setenv("CP_CHAT_MAX_OUTPUT_CEILING", "20000")
        orch = _bare_orchestrator({"per_message_recv_limit": 99999999})
        assert orch._resolve_max_output_tokens("deepseek-flash") == 20000

    def test_荒谬配置_上限低于下限时不得顶成0(self, monkeypatch):
        monkeypatch.setenv("CP_CHAT_MAX_OUTPUT_CEILING", "100")
        orch = _bare_orchestrator({"per_message_recv_limit": 99999999})
        assert orch._resolve_max_output_tokens("deepseek-flash") == 8192

    def test_配置形状异常不抛异常(self):
        orch = Orchestrator.__new__(Orchestrator)
        orch._config = None
        assert orch._resolve_max_output_tokens("deepseek-flash") == 8192
        orch._config = {"memory": "不是映射"}
        assert orch._resolve_max_output_tokens("deepseek-flash") == 8192


class TestConfigLayersAgree:
    """三旋钮的**四处定义**（pydantic 字段 / Config.DEFAULT / 字典校验器 / 修正器）必须同口径

    为什么单列一类：2026-10-02 实测踩过一次 —— config.yaml 写 `token_limit: 131072`，
    字典校验器判非法并**静默改回 4096**，于是窗口"调大"失败得毫无声息
    （日志只有一条 WARNING，而面板照旧显示 4096）。这类"多层校验各有一套数"的缺陷，
    只有拿真实取值走一遍全部层才能拦住。
    """

    def test_常量是唯一来源(self):
        import config as cfg

        assert cfg.MemoryConfig.model_fields["token_limit"].default == cfg.MEMORY_TOKEN_LIMIT_DEFAULT
        assert cfg.Config.DEFAULT["memory"]["token_limit"] == cfg.MEMORY_TOKEN_LIMIT_DEFAULT
        assert (cfg.Config.DEFAULT["memory"]["per_message_recv_limit"]
                == cfg.PER_MESSAGE_RECV_LIMIT_DEFAULT)
        assert (cfg.Config.DEFAULT["memory"]["per_message_send_limit"]
                == cfg.PER_MESSAGE_SEND_LIMIT_DEFAULT)

    def test_窗口值与默认值能穿过所有校验层(self):
        """走真实校验路径：131072 必须既不被判非法、也不被改回 4096"""
        import config as cfg

        payload = {"memory": {"token_limit": cfg.MEMORY_TOKEN_LIMIT_DEFAULT}}
        errors = cfg.validate_config(payload)
        assert not [e for e in errors if e.get("loc") == "memory.token_limit"], errors
        fixed, fix_errors = cfg.validate_and_fix_config(
            {"memory": {"token_limit": cfg.MEMORY_TOKEN_LIMIT_DEFAULT}})
        assert fixed["memory"]["token_limit"] == cfg.MEMORY_TOKEN_LIMIT_DEFAULT
        assert not [e for e in fix_errors if e.get("loc") == "memory.token_limit"]

    def test_真实_Config_读到的就是文件里的窗口值(self):
        """端到端：`Config()` 的 memory.token_limit 必须等于常量默认（config.yaml 亦为该值）"""
        import config as cfg

        assert cfg.Config().get("memory", "token_limit") == cfg.MEMORY_TOKEN_LIMIT_DEFAULT

    def test_越界值仍会被拦下并修正到默认值(self):
        import config as cfg

        errors = cfg.validate_config({"memory": {"token_limit": 10 ** 9}})
        assert [e for e in errors if e.get("loc") == "memory.token_limit"]
        fixed, _ = cfg.validate_and_fix_config({"memory": {"token_limit": 10 ** 9}})
        assert fixed["memory"]["token_limit"] == cfg.MEMORY_TOKEN_LIMIT_DEFAULT


class _PushTarget:
    """被推送的目标桩：模拟 DigitalLife（``_memory_token_limit`` + ``_memory``）"""

    def __init__(self, with_window=True, with_memory=True):
        if with_window:
            self._memory_token_limit = 32768
            self._memory_token_limit_source = "config.yaml:memory.token_limit"
        if with_memory:
            self._memory = types.SimpleNamespace(_token_limit=32768)


class TestPushRuntimeWindow:
    """窗口值必须推给正在跑的编排器：否则滑块只改了个没人读的副本"""

    def _fake_app_server(self, monkeypatch, target):
        fake = types.ModuleType("app_server")
        fake._Yunshu = target
        monkeypatch.setitem(sys.modules, "app_server", fake)

    def test_推送后组装窗口与压缩阈值同步(self, monkeypatch):
        from plugins.memory import _push_runtime_window

        target = _PushTarget()
        self._fake_app_server(monkeypatch, target)
        assert _push_runtime_window(131072) is True
        assert target._memory_token_limit == 131072
        assert target._memory_token_limit_source == "runtime_override(api)"
        assert target._memory._token_limit == 131072, "压缩触发阈值必须同步，否则两套账"

    def test_编排器不可用时如实返回False(self, monkeypatch):
        from plugins.memory import _push_runtime_window

        self._fake_app_server(monkeypatch, _PushTarget(with_window=False))
        assert _push_runtime_window(131072) is False


class _CounterStub:
    def count(self, text: str) -> int:
        return len(text or "")


class _SessionMgrStub:
    def __init__(self, messages):
        self._messages = list(messages)

    def get_messages(self, session_id, limit=50):
        return list(self._messages) if limit <= 0 else list(self._messages)[-limit:]


class _MemoryStub:
    def __init__(self, rounds=0):
        self._rounds = rounds

    @property
    def compress_rounds(self):
        return self._rounds


class _YunshuStub:
    def __init__(self, window=131072, source="config.yaml:memory.token_limit", rounds=0):
        self._memory_token_limit = window
        self._memory_token_limit_source = source
        self._memory = _MemoryStub(rounds)

    def context_limit_info(self):
        if self._memory_token_limit is None:
            return {"limit_tokens": None, "limit_source": "unavailable", "available": False}
        return {"limit_tokens": self._memory_token_limit,
                "limit_source": self._memory_token_limit_source, "available": True}


class _CfgStub:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def get(self, section, key, default=None):
        if isinstance(section, str) and key is None:  # pragma: no cover 兼容用法
            return self.values.get(section, default)
        return self.values.get(key, default)

    def set(self, value, section, key):
        self.values[key] = value


@pytest.fixture()
def panel_env(monkeypatch):
    """装配桩 app_server + 注册 plugins.memory 蓝图，返回 (client, yunshu, cfg)"""
    from flask import Flask

    from plugins.memory import bp

    yunshu = _YunshuStub()
    sessions = _SessionMgrStub([{"role": "user", "content": "abcd" * 250}])  # 1000 token
    cfg = _CfgStub({"per_message_send_limit": 3456, "per_message_recv_limit": 3968,
                    "compress_threshold": 0.8})

    fake = types.ModuleType("app_server")
    fake._Yunshu = yunshu
    fake._session_mgr = sessions
    fake._get_current_session_id = lambda: "sess-1"
    fake._get_token_counter = lambda: _CounterStub()
    fake._cfg = cfg
    fake.logger = types.SimpleNamespace(
        info=lambda *a, **k: None, warning=lambda *a, **k: None,
        error=lambda *a, **k: None, debug=lambda *a, **k: None)
    fake.require_token = lambda f: f
    fake.log_request = lambda *a, **k: (lambda f: f)
    monkeypatch.setitem(sys.modules, "app_server", fake)

    app = Flask(__name__)
    app.register_blueprint(bp)
    app.config.update(TESTING=True)
    return app.test_client(), yunshu, cfg


class TestPanelReadings:
    """面板读数：分母必须是真实窗口，档位必须按占用判"""

    def test_分母取编排窗口而非配置副本(self, panel_env):
        client, yunshu, cfg = panel_env
        cfg.set(19968, "memory", "token_limit")  # UI 存值：**不得**再当分母
        body = client.get("/api/context/status").get_json()
        assert body["token_limit"] == 131072
        assert body["token_limit_source"] == "config.yaml:memory.token_limit"
        assert body["configured_token_limit"] == 19968, "UI 存值仍要如实回显（便于对照）"
        assert body["percentage"] == round(1000 / 131072 * 100, 1)

    def test_摘要退化不再把档位顶成critical(self, panel_env):
        """线上症状：占用 22% 却常年报红（因为累计压缩 23 次）"""
        client, yunshu, _ = panel_env
        yunshu._memory = _MemoryStub(rounds=23)
        body = client.get("/api/context/status").get_json()
        assert body["percentage"] < 60
        assert body["status_level"] == "ok", "档位只跟占用走"
        assert body["compress_degraded"] is True
        assert "summary_degraded" in body["status_reasons"]
        assert "usage_high" not in body["status_reasons"]

    def test_占用高才报warning或critical(self, panel_env, monkeypatch):
        client, _, _ = panel_env
        import plugins.memory as mem

        monkeypatch.setattr(mem, "_context_limit_info",
                            lambda _y: {"limit_tokens": 1000, "limit_source": "stub"})
        body = client.get("/api/context/status").get_json()
        assert body["percentage"] == 100.0
        assert body["status_level"] == "critical"
        assert "usage_critical" in body["status_reasons"]

    def test_取不到分母时如实报None(self, panel_env):
        client, yunshu, _ = panel_env
        yunshu._memory_token_limit = None
        body = client.get("/api/context/status").get_json()
        assert body["token_limit"] is None
        assert body["token_limit_source"] == "unavailable"
        assert body["percentage"] is None, "分母未知 ⇒ 百分比必须是 None，不能是 0 或假数"
        assert body["status_level"] == "unknown"

    def test_发送侧语义声明为只告警(self, panel_env):
        client, _, _ = panel_env
        body = client.get("/api/context/status").get_json()
        assert body["send_limit_semantics"] == "warn_only"


class TestConfigPost:
    """POST /api/context/config：值域放宽 + 当场生效要如实回报"""

    def test_窗口值域放宽到模型量级且当场推送(self, panel_env, monkeypatch):
        import plugins.memory as mem

        client, yunshu, cfg = panel_env
        pushed = {}
        monkeypatch.setattr(mem, "_push_runtime_window",
                            lambda v: pushed.setdefault("v", v) is None or True)
        body = client.post("/api/context/config", json={"token_limit": 131072}).get_json()
        assert body["ok"] is True and "token_limit" in body["changed"]
        assert body["runtime_applied"] is True
        assert pushed["v"] == 131072
        assert body["token_limit"] == 131072

    def test_窗口值越界收敛(self, panel_env, monkeypatch):
        import plugins.memory as mem

        client, _, cfg = panel_env
        monkeypatch.setattr(mem, "_push_runtime_window", lambda v: True)
        client.post("/api/context/config", json={"token_limit": 99999999})
        assert cfg.values["token_limit"] == 1048576, "上限 = 模型窗口量级"
        client.post("/api/context/config", json={"token_limit": 1})
        assert cfg.values["token_limit"] == 512

    def test_两个单条上限的值域(self, panel_env, monkeypatch):
        import plugins.memory as mem

        client, _, cfg = panel_env
        monkeypatch.setattr(mem, "_push_runtime_window", lambda v: True)
        client.post("/api/context/config", json={
            "per_message_send_limit": 999999, "per_message_recv_limit": 999999})
        assert cfg.values["per_message_send_limit"] == 131072
        assert cfg.values["per_message_recv_limit"] == 393216, "不得低于模型 max_output 量级"

    def test_推送失败时如实回报未生效(self, panel_env, monkeypatch):
        import plugins.memory as mem

        client, _, _ = panel_env
        monkeypatch.setattr(mem, "_push_runtime_window", lambda v: False)
        body = client.post("/api/context/config", json={"token_limit": 65536}).get_json()
        assert body["runtime_applied"] is False
