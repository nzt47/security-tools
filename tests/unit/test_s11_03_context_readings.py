# -*- coding: utf-8 -*-
"""TASK-S11-03 回归锚：上下文/读数口径统一（context 块归属 + 告警可见性）

三件被锁死的意图（对应 S10-03 遗留 R3/R5）::

    意图1（R3 会话归属）``/api/chat`` 的 ``context`` 块必须属于**本请求**的
        ``session_id``，而不是 ``_get_current_session_id()`` 的全局会话。
        修复前 `plugins/chat.py:300` 取全局会话 ⇒ A 会话聊天可能报 B 会话的数字。

    意图2（R3 分母）``context.token_limit`` 必须是**真实编排窗口上限**
        （``DigitalLife.context_limit_info()``），并披露来源；
        修复前是 ``_cfg.get("memory","token_limit", default=4096)`` ——
        config.yaml 无该键（`agent/orchestrator/lifecycle_manager.py:283-289`），
        分母恒为硬编码 4096。同一响应里 ``context.percentage``（÷4096）与
        ``metadata.context_notice.pct``（÷131072）互相矛盾。

    意图3（R5 可见性）上下文告警必须**出现在结构化字段里**、且**不在 response 正文里**。
        修复前 ``chat()``（`agent/orchestrator/orchestrator.py:571-581`）只取 text、
        丢弃 metadata，且 `plugins/chat.py` 不转发 metadata
        ⇒ 告警"修好了但没人看得见"。

本文件的两层被测对象：
    A. 插件层（真 Flask test client + 桩 ``app_server`` 模块）——现象的最终出口；
    B. 编排层（``Orchestrator.__new__``）——``context_limit_info`` / 按会话元数据留存。

**关于桩**：本套件不调真实模型、不连服务，``_get_token_counter`` 与 ``_Yunshu``
均为注入桩（``count(s) = len(s)``，1 字符 = 1 token），数字是**桩上的确定性算术**，
不代表真机 token 数。
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import pytest

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from agent.orchestrator.orchestrator import Orchestrator  # noqa: E402
from agent.orchestrator.turn_state import TurnStateStore  # noqa: E402

#: 桩上注入的编排窗口值（**刻意与任何真机值都不同**：既不同于修复前的显示分母 4096，
#: 也不同于真机现值 —— 这样"读到了 131072"就只能来自单一事实源，
#: 不可能是某个被硬编码回去的常量）。
#:
#: 真机现值（口径变更记录）：`config.yaml` 于 Owner 裁定 #1 显式写入
#: `memory.token_limit: 32768`（见 docs/zh/Owner裁定记录_20260913.md §裁定 #1），
#: 故 `context.token_limit` 真机应为 **32768**、来源 `config.yaml:memory.token_limit`。
#: 本套件用桩值 131072 是为验证"读数跟着单一事实源走"，不主张真机数字。
STUB_WINDOW = 131072

#: 修复前的硬编码显示分母（必须**不再**出现为 context.token_limit）
LEGACY_DISPLAY_LIMIT = 4096

#: 全局默认会话：修复前 context 块读的就是它（用于证明"串台"已消失）
GLOBAL_SESSION = "sess_GLOBAL_must_not_leak"

#: 真机 S10-03 的告警文案片段（不得出现在 response 正文里）
NOTICE_TEXT = "当前会话上下文即将耗尽"


# ════════════════════════════════════════════════════════════════════════════
#  插件层：桩 app_server + 真 Flask test client
# ════════════════════════════════════════════════════════════════════════════

class _CounterStub:
    """TokenCounter 桩：``count(s) == len(s)``（1 字符 = 1 token）。"""

    def count(self, text: str) -> int:
        return len(text or "")


class _SessionMgrStub:
    """SessionManager 桩：进程内 list 存储，`limit<=0` 表示不截断。"""

    def __init__(self) -> None:
        self._messages: dict[str, list[dict]] = {}
        self._current = GLOBAL_SESSION

    def seed(self, session_id: str, contents: list[str]) -> None:
        """预置历史消息（用于制造"会串台"的全局会话）。"""
        self._messages.setdefault(session_id, []).extend(
            {"role": "user", "content": c} for c in contents)

    def get_session(self, session_id: str):
        return {"id": session_id} if session_id in self._messages else None

    def create_session(self, session_id: str, title: str = "") -> dict:
        self._messages.setdefault(session_id, [])
        return {"id": session_id, "title": title}

    def get_current_id(self) -> str:
        return self._current

    def add_message(self, session_id: str, role: str, content: str, **kwargs) -> dict:
        self._messages.setdefault(session_id, []).append(
            {"role": role, "content": content})
        return {"role": role, "content": content}

    def get_messages(self, session_id: str, limit: int = 50) -> list[dict]:
        msgs = list(self._messages.get(session_id, []))
        if limit > 0:
            msgs = msgs[-limit:]
        return msgs


class _YunshuStub:
    """DigitalLife 桩。``chat()`` 会像真编排层那样按会话留存本轮 metadata。"""

    def __init__(self) -> None:
        self._metadata_by_session: dict[str, dict] = {}
        self.next_response = "桩回答"
        self.next_metadata: dict = {}
        self._behavior = types.SimpleNamespace(
            profile=types.SimpleNamespace(label="桩模式"))

    # ── 编排层公开读取口（TASK-S11-03 新增/复用）──
    def context_limit_info(self) -> dict:
        return {"limit_tokens": STUB_WINDOW,
                "limit_source": "builtin_default(131072)", "available": True}

    def last_response_metadata(self, session_id=None) -> dict:
        return self._metadata_by_session.get(session_id or "", {})

    def last_turn_state(self, session_id=None) -> dict:
        return {"tool_steps": [], "reasoning": None}

    # ── api_chat 用到的其它入口 ──
    def chat(self, user_input: str, *, session_id=None, session_mgr=None) -> str:
        self._metadata_by_session[session_id or ""] = dict(self.next_metadata)
        return self.next_response

    def get_behavior_mode(self):
        return types.SimpleNamespace(value="normal")

    def get_config(self) -> dict:
        return {"configured": True, "provider": "stub", "api_key_set": True}

    def check_health(self) -> list:
        return [types.SimpleNamespace(to_dict=lambda: {"name": "stub", "ok": True})]

    @staticmethod
    def _is_skill_enabled(name: str) -> bool:
        return True


class _SafetyGuardStub:
    @staticmethod
    def check(text: str) -> dict:
        return {"level": "safe", "matches": [], "safe": True}


class _CfgStub:
    """``config.yaml`` 读取桩：``memory`` 段**没有** ``token_limit`` 键。

    与真机一致（`agent/orchestrator/lifecycle_manager.py:283-289` 实测确认未配），
    故修复前 `_cfg.get("memory","token_limit", default=4096)` 恒得 4096。
    这里如实模拟"键不存在"，好让修复前的失败是**语义失败**（分母错），
    而不是"桩缺属性导致的 ImportError/AttributeError"这种假红。
    """

    def get(self, section, key, default=None):
        return default


@pytest.fixture()
def chat_env(monkeypatch):
    """装配桩 ``app_server`` + 注册 ``plugins.chat`` 蓝图，返回 (client, yunshu, sessions)。"""
    yunshu = _YunshuStub()
    sessions = _SessionMgrStub()
    # 全局会话塞入大量历史：修复前 context 块读的正是它，数字会明显串台
    sessions.seed(GLOBAL_SESSION, ["G" * 5000])

    fake = types.ModuleType("app_server")
    fake._Yunshu = yunshu
    fake._session_mgr = sessions
    fake._get_current_session_id = lambda: GLOBAL_SESSION
    fake._safety_guard = _SafetyGuardStub()
    fake._save_conversation_record = lambda **kwargs: None
    fake._get_token_counter = lambda: _CounterStub()
    fake._cfg = _CfgStub()
    fake.logger = types.SimpleNamespace(
        info=lambda *a, **k: None, warning=lambda *a, **k: None,
        error=lambda *a, **k: None, debug=lambda *a, **k: None)
    fake.PROMETHEUS_AVAILABLE = False
    fake.SECURITY_BLOCKS = None
    fake._CHAT_HISTORY = []
    fake.require_token = lambda f: f
    fake.log_request = lambda *args, **kwargs: (lambda f: f)

    # 【测试隔离】只替换 sys.modules 中的条目，结束自动还原（不污染其它套件）
    monkeypatch.setitem(sys.modules, "app_server", fake)

    from flask import Flask
    from plugins.chat import bp

    app = Flask("s11_03_test")
    app.config.update(TESTING=True)
    app.register_blueprint(bp)
    return app.test_client(), yunshu, sessions


def _post(client, message: str, session_id: str):
    resp = client.post("/api/chat", json={"message": message, "session_id": session_id})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()


# ── 意图1：context 块与请求会话一致 ─────────────────────────────────────────

def test_context_块归属本请求会话_两会话交替可验证(chat_env):
    """A/B/A 交替：每轮 context.session_id 与 session_total_tokens 都属于本请求会话。

    修复前：三处读数全部取 ``_get_current_session_id()``（= 全局会话
    ``sess_GLOBAL_must_not_leak``，预置 5000 字符）⇒ 三轮数字恒为 5000。
    """
    client, yunshu, _sessions = chat_env

    def _turn(message: str, reply: str, session_id: str):
        yunshu.next_response = reply
        yunshu.next_metadata = {}
        return _post(client, message, session_id)

    a1 = _turn("AAA", "aaa", "sess_A")   # A: 3 + 3 = 6
    b1 = _turn("BB", "bb", "sess_B")     # B: 2 + 2 = 4
    a2 = _turn("AAA", "aaa", "sess_A")   # A: 6 + 6 = 12（累计生长）

    # 【串台锚，放在最前】桩上 count(s)=len(s)，故数字可逐步手算复现：
    #   A 首轮 3+3=6，B 2+2=4，A 再一轮 6+6=12
    # 修复前这里恒为全局会话的 5000（断言会以 [5000, 5000, 5000] 失败）
    assert [a1["context"]["session_total_tokens"],
            b1["context"]["session_total_tokens"],
            a2["context"]["session_total_tokens"]] == [6, 4, 12]
    # 并回显本次实际使用的 session_id（让调用方能自查）
    assert [a1["context"]["session_id"], b1["context"]["session_id"],
            a2["context"]["session_id"]] == ["sess_A", "sess_B", "sess_A"]
    # 【防假绿】全局会话（5000 字符）绝不能被读进任何一轮
    for body in (a1, b1, a2):
        assert body["context"]["session_total_tokens"] != 5000
        assert body["context"]["session_id"] != GLOBAL_SESSION
    assert a2["context"]["session_message_count"] == 4


# ── 意图2：分母是真实编排窗口 + 来源披露 ────────────────────────────────────

def test_context分母是真实编排窗口而非硬编码4096(chat_env):
    """``context.token_limit`` 取编排窗口单一事实源，并披露来源。"""
    client, yunshu, _sessions = chat_env
    yunshu.next_response = "ok"
    yunshu.next_metadata = {}
    body = _post(client, "hello", "sess_A")
    ctx = body["context"]

    assert ctx["token_limit"] == STUB_WINDOW
    assert ctx["token_limit"] != LEGACY_DISPLAY_LIMIT
    assert ctx["token_limit_source"] == "builtin_default(131072)"
    # percentage 必须用**被披露的那个分母**，且被明确标注为"累计占比（非窗口占用）"
    assert ctx["percentage"] == pytest.approx(
        ctx["session_total_tokens"] / ctx["token_limit"] * 100, abs=0.05)
    assert ctx["percentage_semantics"] == "session_cumulative_share_of_window"
    assert "非" in ctx["percentage_note"]
    # 每个读数都要能说清来源
    assert ctx["session_total_source"]
    assert ctx["session_message_count"] == len(
        _sessions_messages(chat_env, "sess_A"))


def _sessions_messages(chat_env, session_id: str) -> list:
    return chat_env[2].get_messages(session_id, limit=0)


def test_分母不可得时记None而非0或硬编码(chat_env, monkeypatch):
    """【不易】上限缺位 ⇒ token_limit/percentage 记 ``None``。

    不得回退成 4096（假装可信的假分母），也不得记 0（"0%" 会被读成"空窗口"）。
    """
    client, yunshu, _sessions = chat_env
    monkeypatch.setattr(
        yunshu, "context_limit_info",
        lambda: {"limit_tokens": None, "limit_source": "unavailable",
                 "available": False})
    yunshu.next_response = "ok"
    yunshu.next_metadata = {}
    ctx = _post(client, "hello", "sess_A")["context"]
    assert ctx["token_limit"] is None
    assert ctx["token_limit_source"] == "unavailable"
    assert ctx["percentage"] is None
    # 分子仍照常给（读数缺位只影响分母，不影响能拿到的部分）
    assert ctx["session_total_tokens"] == len("hello") + len("ok")


# ── 意图3：告警在结构化字段里可见、正文里不可见 ─────────────────────────────

def test_告警在结构化字段里可见且不在正文里(chat_env):
    """S10-03 的口径修正必须**真的到达 HTTP 出口**（R5）。"""
    client, yunshu, _sessions = chat_env
    notice = {
        "kind": "system_notice", "level": "critical", "reason": "summary_degraded",
        "pct": 0.8, "used_tokens": 1048, "limit_tokens": STUB_WINDOW,
        "limit_source": "builtin_default(131072)",
        "message": f"{NOTICE_TEXT}（压缩退化）",
    }
    yunshu.next_response = "答案是 5"
    yunshu.next_metadata = {"context_notice": notice}

    body = _post(client, "2 加 3 等于多少？只回答数字", "sess_A")

    # ① 正文干净：告警文案**不得**混进 response
    assert body["response"] == "答案是 5"
    assert NOTICE_TEXT not in body["response"]
    assert "创建新会话" not in body["response"]
    # ② 结构化字段可见：正向断言（移出正文 ≠ 丢掉告警，防假绿）
    assert body["metadata"]["context_notice"]["kind"] == "system_notice"
    assert body["metadata"]["context_notice"]["level"] == "critical"
    assert body["metadata"]["context_notice"]["reason"] == "summary_degraded"
    # ③ 同一响应里 token 读数的**分母一致**（R3 与 S10-03 的口径合流点）
    assert body["context"]["token_limit"] == \
        body["metadata"]["context_notice"]["limit_tokens"] == STUB_WINDOW


def test_告警不跨会话串台(chat_env):
    """A 会话的 critical 告警**不得**出现在 B 会话的响应里。

    告警按会话归属，与 context 块同一纪律（修复前 ``chat()`` 丢弃 metadata，
    若改成读全局 ``_last_context_warning`` 就会把 A 的告警发给 B）。
    """
    client, yunshu, _sessions = chat_env

    yunshu.next_response = "A 的回答"
    yunshu.next_metadata = {"context_notice": {
        "kind": "system_notice", "level": "critical", "reason": "usage_high",
        "limit_tokens": STUB_WINDOW, "message": NOTICE_TEXT}}
    a = _post(client, "AAA", "sess_A")
    assert a["metadata"]["context_notice"]["level"] == "critical"

    yunshu.next_response = "B 的回答"
    yunshu.next_metadata = {}
    b = _post(client, "BB", "sess_B")
    assert b["metadata"] == {}
    assert NOTICE_TEXT not in b["response"]


def test_无告警时metadata为空字典_形状稳定(chat_env):
    """没有系统提示时 ``metadata`` 仍存在且为 ``{}``（形状可预测，前端无需判 None）。"""
    client, yunshu, _sessions = chat_env
    yunshu.next_response = "ok"
    yunshu.next_metadata = {}
    body = _post(client, "hi", "sess_A")
    assert body["metadata"] == {}
    assert "metadata_omitted_keys" not in body


def test_不可序列化元数据被丢弃且显式披露(chat_env):
    """非 JSON 可序列化的元数据键必须被丢弃，**且**在响应里披露其键名。

    Why: 读数通道不得因编排层塞进一个不可序列化对象而让 /api/chat 500；
    但"静默丢弃"会被误读成"编排层没产出该键"。
    """
    client, yunshu, _sessions = chat_env
    yunshu.next_response = "ok"
    yunshu.next_metadata = {"plan_summary": {"goal": "g"},
                            "bad_object": object()}
    body = _post(client, "hi", "sess_A")
    assert body["metadata"] == {"plan_summary": {"goal": "g"}}
    assert body["metadata_omitted_keys"] == ["bad_object"]
    assert "bad_object" not in body["metadata"]


# ════════════════════════════════════════════════════════════════════════════
#  编排层：context_limit_info / 按会话元数据留存
# ════════════════════════════════════════════════════════════════════════════

def _bare_orchestrator(limit=STUB_WINDOW, source="builtin_default(131072)"):
    orch = Orchestrator.__new__(Orchestrator)
    orch._memory_token_limit = limit
    orch._memory_token_limit_source = source
    orch._session_id = "default_session"
    return orch


def test_编排层暴露窗口上限与来源():
    info = _bare_orchestrator().context_limit_info()
    assert info == {"limit_tokens": STUB_WINDOW,
                    "limit_source": "builtin_default(131072)",
                    "available": True}


@pytest.mark.parametrize("bad", [None, 0, -1, "131072", 3.5, True])
def test_编排层上限不可得时诚实记None(bad):
    """缺位 / 非法 / 非正整数一律 ``None`` + ``unavailable``，不伪造分母。"""
    orch = _bare_orchestrator()
    orch._memory_token_limit = bad
    info = orch.context_limit_info()
    assert info["limit_tokens"] is None
    assert info["limit_source"] == "unavailable"
    assert info["available"] is False


def test_编排层元数据按会话留存且不跨轮(monkeypatch):
    """``chat()`` 把本轮 metadata 按会话留存；下一轮先清空，不跨会话、不跨轮。"""
    orch = _bare_orchestrator()
    orch._running = True
    calls: list = []

    def _fake_process(user_input, **kwargs):
        sid = kwargs.get("session_id")
        calls.append(sid)
        return {"success": True, "response": f"reply:{sid}",
                "metadata": {"context_notice": {"level": "critical",
                                                "session": sid}}}

    monkeypatch.setattr(orch, "process", _fake_process)

    assert orch.chat("a", session_id="sess_X") == "reply:sess_X"
    assert orch.chat("b", session_id="sess_Y") == "reply:sess_Y"

    # 各自拿到**自己**那一轮的元数据（不串台）
    assert orch.last_response_metadata("sess_X")["context_notice"]["session"] == "sess_X"
    assert orch.last_response_metadata("sess_Y")["context_notice"]["session"] == "sess_Y"
    # 从未产出元数据的会话 → 空 dict
    assert orch.last_response_metadata("sess_Z") == {}

    # 下一轮：process() 内的 _begin_turn 清空本轮槽位后（此处由 fake 直接返回）
    orch.chat("c", session_id="sess_X")
    # 显式模拟"本轮无元数据"：写入口不接受 or 回退
    orch._set_response_metadata("sess_X", None)
    assert orch.last_response_metadata("sess_X") == {}


def test_元数据槽位不改变last_turn_state对外形状():
    """【不易】``last_turn_state()`` 的返回形状是对外契约，新增 metadata 槽位不得渗入。"""
    store = TurnStateStore()
    store.begin("s")
    store.set("s", tool_steps=[{"tool": "t"}], reasoning="r")
    store.set_metadata("s", {"context_notice": {"level": "critical"}})

    snap = store.snapshot("s")
    assert set(snap.keys()) == {"tool_steps", "reasoning"}
    assert store.previous("s") == {"tool_steps": [], "reasoning": None}
    # metadata 另走独立读取口
    assert store.metadata_snapshot("s") == {"context_notice": {"level": "critical"}}
    # begin() 清空本轮 ⇒ 元数据不跨轮残留
    store.begin("s")
    assert store.metadata_snapshot("s") is None
    assert store.snapshot("s") == {"tool_steps": [], "reasoning": None}


def test_元数据存储按会话有界且写入是浅拷贝():
    store = TurnStateStore(max_sessions=3)
    payload = {"context_notice": {"level": "critical"}}
    for i in range(5):
        store.begin(f"s{i}")
        store.set_metadata(f"s{i}", payload)
    assert len(store) == 3                      # 有界（LRU 淘汰最旧）
    assert store.metadata_snapshot("s0") is None
    assert store.metadata_snapshot("s4") == payload

    # 写入是**浅拷贝**：只隔离顶层键的增删，嵌套对象仍共享引用（如实断言，不夸大）
    #  · 顶层新增 → 不渗入存储（这就是浅拷贝挡住的那部分）
    payload["injected"] = 1
    assert "injected" not in store.metadata_snapshot("s4")
    #  · 嵌套改动 → **会**渗入（浅拷贝挡不住；本用例把该边界显式写下来，
    #    以免后来者误以为这里是深隔离）
    payload["context_notice"]["level"] = "mutated"
    assert store.metadata_snapshot("s4")["context_notice"]["level"] == "mutated"
