"""LLM 禁用时段闸门单元测试（2026-11-20，含第二轮修订）。

覆盖：默认禁用时段（工作日 9-12/14-18，节假日豁免）、自由配置时段、
开关语义、下一禁用/恢复时刻、提前收尾提示、任务顺延、
SDK 层拦截、聊天流在禁用时段的拒绝、定时任务注册。
"""
from __future__ import annotations

import datetime as dt
import json

import pytest

from agent import llm_idle_gate as gate

BJ = gate.TZ


def _at(local_iso: str) -> dt.datetime:
    return dt.datetime.fromisoformat(local_iso).replace(tzinfo=BJ)


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    # 每例独立配置文件：不读/写仓库 data/llm_gate_config.json
    monkeypatch.setenv(gate.ENV_CONFIG_FILE, str(tmp_path / "llm_gate_config.json"))
    monkeypatch.setenv(gate.ENV_ENABLED, "0")
    monkeypatch.setattr(gate, "_last_tick_blocked", None)
    yield


def _write_config(tmp_path, **patch):
    cfg = {
        "enabled": True,
        "windows": [
            {"days": [0, 1, 2, 3, 4], "start": "09:00", "end": "12:00"},
            {"days": [0, 1, 2, 3, 4], "start": "14:00", "end": "18:00"},
        ],
        "skip_holidays": True,
        "lead_seconds": 300,
    }
    cfg.update(patch)
    path = tmp_path / "llm_gate_config.json"
    path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    return path


class TestDefaultWindows:
    def test_工作日高峰被禁用(self, tmp_path):
        _write_config(tmp_path)
        # 2026-11-20 周五
        assert gate.is_blocked(_at("2026-11-20 09:00")) is True
        assert gate.is_blocked(_at("2026-11-20 11:59")) is True
        assert gate.is_blocked(_at("2026-11-20 12:00")) is False   # 左闭右开
        assert gate.is_blocked(_at("2026-11-20 13:59")) is False
        assert gate.is_blocked(_at("2026-11-20 14:00")) is True
        assert gate.is_blocked(_at("2026-11-20 17:59")) is True
        assert gate.is_blocked(_at("2026-11-20 18:00")) is False

    def test_周末与法定节假日放行(self, tmp_path):
        _write_config(tmp_path)
        assert gate.is_blocked(_at("2026-11-21 10:00")) is False   # 周六
        assert gate.is_blocked(_at("2026-11-22 15:00")) is False   # 周日
        # 2026 春节 2/15-2/23；2/16、2/23 都是周一
        assert gate.is_blocked(_at("2026-02-16 10:00")) is False
        assert gate.is_blocked(_at("2026-02-23 10:00")) is False
        assert gate.is_blocked(_at("2026-02-24 09:30")) is True    # 节后工作日

    def test_节假日豁免可以关闭(self, tmp_path):
        _write_config(tmp_path, skip_holidays=False)
        assert gate.is_blocked(_at("2026-02-16 10:00")) is True

    def test_下一禁用与恢复时刻(self, tmp_path):
        _write_config(tmp_path)
        assert gate.next_block_start(_at("2026-11-20 13:00")) == _at("2026-11-20 14:00")
        assert gate.next_block_start(_at("2026-11-20 18:30")) == _at("2026-11-23 09:00")
        assert gate.next_block_start(_at("2026-11-21 10:00")) == _at("2026-11-23 09:00")
        assert gate.next_unblock(_at("2026-11-20 10:30")) == _at("2026-11-20 12:00")
        assert gate.seconds_until_blocked(_at("2026-11-20 13:00")) == 3600


class TestCustomWindows:
    def test_自定义时段立即生效(self, tmp_path):
        _write_config(tmp_path, windows=[
            {"days": [0], "start": "08:00", "end": "09:00"},   # 仅周一
        ])
        assert gate.is_blocked(_at("2026-11-23 08:30")) is True   # 周一
        assert gate.is_blocked(_at("2026-11-24 08:30")) is False  # 周二

    def test_保存非法时段被拒绝(self, tmp_path):
        _write_config(tmp_path)
        with pytest.raises(ValueError):
            gate.save_config({"windows": [{"days": [0], "start": "12:00", "end": "09:00"}]})
        with pytest.raises(ValueError):
            gate.save_config({"windows": [{"days": [], "start": "09:00", "end": "10:00"}]})
        with pytest.raises(ValueError):
            gate.save_config({"lead_seconds": -1})

    def test_保存后落盘并可读回(self, tmp_path):
        _write_config(tmp_path)
        new = gate.save_config({
            "enabled": False,
            "windows": [{"days": [2], "start": "10:30", "end": "11:15"}],
            "lead_seconds": 120,
        })
        assert new["enabled"] is False
        assert new["windows"][0]["start"] == "10:30"
        assert new["lead_seconds"] == 120
        assert gate.get_config()["windows"][0]["days"] == [2]


class TestGateSwitch:
    def test_开关关闭不拦截(self, tmp_path):
        _write_config(tmp_path, enabled=False)
        assert gate.is_blocked(_at("2026-11-20 10:00")) is False
        gate.guard("test", moment=_at("2026-11-20 10:00"))  # 不抛
        assert gate.state(_at("2026-11-20 10:00"))["enabled"] is False

    def test_默认开关取环境变量(self, tmp_path, monkeypatch):
        monkeypatch.setenv(gate.ENV_ENABLED, "1")
        assert gate.get_config()["enabled"] is True
        assert gate.is_blocked(_at("2026-11-20 10:00")) is True

    def test_禁用时段拦截并带状态(self, tmp_path):
        _write_config(tmp_path)
        st = gate.state(_at("2026-11-20 10:30"))
        assert st["blocked"] is True
        assert st["window"]["start"] == "09:00"
        assert "禁用时段" in st["message"]
        assert "12:00" in st["message"]
        with pytest.raises(gate.LLMIdleBlocked) as ei:
            gate.guard("unit-test", moment=_at("2026-11-20 10:30"))
        assert ei.value.code == "LLM_IDLE_BLOCKED"
        assert ei.value.state["source"] == "unit-test"

    def test_空闲时段放行(self, tmp_path):
        _write_config(tmp_path)
        gate.guard("test", moment=_at("2026-11-20 13:00"))
        assert gate.blocked_state(_at("2026-11-20 13:00")) is None


class TestDeadline:
    def test_临近禁用时段提示收尾(self, tmp_path):
        _write_config(tmp_path, lead_seconds=300)
        note = gate.planning_note(_at("2026-11-20 13:58"))
        assert note["wrapup"] is True
        assert "仅剩" in note["text"] and "收尾" in note["text"]
        assert gate.wrapup_directive(_at("2026-11-20 13:58"))

    def test_时间充裕只给预算提示(self, tmp_path):
        _write_config(tmp_path, lead_seconds=300)
        note = gate.planning_note(_at("2026-11-20 13:30"))
        assert note["wrapup"] is False
        assert "时间预算" in note["text"]

    def test_任务顺延判定(self, tmp_path):
        _write_config(tmp_path, lead_seconds=300)
        assert gate.should_defer_task(_at("2026-11-20 13:58")) is True   # 仅剩 2 分钟
        assert gate.should_defer_task(_at("2026-11-20 13:00")) is False  # 剩 60 分钟
        assert gate.should_defer_task(_at("2026-11-20 10:30")) is True   # 已在禁用时段


class TestSdkPatch:
    def test_安装后在禁用时段拦截真实_sdk_调用(self, tmp_path, monkeypatch):
        from openai.resources.chat.completions import Completions

        _write_config(tmp_path)
        gate.uninstall()
        try:
            monkeypatch.setattr(gate, "_now", lambda: _at("2026-11-20 10:30"))
            assert gate.install() >= 2
            assert gate.installed() is True
            assert gate.install() >= 2      # 幂等

            import openai
            client = openai.OpenAI(api_key="sk-test", base_url="http://127.0.0.1:1/v1")
            with pytest.raises(gate.LLMIdleBlocked):
                client.chat.completions.create(model="test", messages=[])

            assert Completions.create is not None
        finally:
            gate.uninstall()
        assert gate.installed() is False

    def test_空闲时段不拦截_sdk(self, tmp_path, monkeypatch):
        _write_config(tmp_path)
        monkeypatch.setattr(gate, "_now", lambda: _at("2026-11-20 13:00"))
        gate.uninstall()
        try:
            gate.install()
            # 空闲时段放行：这里不应抛 LLMIdleBlocked（会尝试网络，故只断言 guard）
            gate.guard("unit-test", moment=_at("2026-11-20 13:00"))
        finally:
            gate.uninstall()


class TestHttpApi:
    def test_get_与_post_配置(self, tmp_path):
        from flask import Flask

        from agent.server_routes.routes_llm_gate import register_routes

        app = Flask("llm_gate_api")
        register_routes(app, lambda: None)
        client = app.test_client()

        got = client.get("/api/llm-gate")
        assert got.status_code == 200
        body = got.get_json()
        assert body["ok"] is True
        assert "config" in body and "state" in body
        # 只读不落盘：GET 不应在仓库 data/ 下生成配置
        assert not (tmp_path / "llm_gate_config.json").exists()

        saved = client.post("/api/llm-gate", json={
            "enabled": True,
            "windows": [{"days": [0], "start": "09:00", "end": "10:30"}],
        })
        assert saved.status_code == 200
        assert saved.get_json()["config"]["windows"][0]["end"] == "10:30"
        assert (tmp_path / "llm_gate_config.json").exists()

        bad = client.post("/api/llm-gate", json={
            "windows": [{"days": [0], "start": "10:00", "end": "09:00"}],
        })
        assert bad.status_code == 400
        assert "结束时间" in bad.get_json()["error"]


class TestChatRoute:
    def test_流式对话在禁用时段开流前被拒(self, tmp_path, monkeypatch):
        import sys
        import types

        from flask import Flask

        import plugins.chat as chat

        _write_config(tmp_path)
        monkeypatch.setattr(gate, "_now", lambda: _at("2026-11-20 10:30"))

        class _Sessions:
            def get_session(self, sid):
                return {"id": sid}

            def set_current(self, sid):
                return None

        fake = types.ModuleType("app_server")
        fake.logger = types.SimpleNamespace(
            info=lambda *a, **k: None, warning=lambda *a, **k: None,
            error=lambda *a, **k: None, debug=lambda *a, **k: None)
        fake._session_mgr = _Sessions()
        fake._get_current_session_id = lambda: "s-blocked"
        monkeypatch.setitem(sys.modules, "app_server", fake)

        app = Flask("llm_gate_test")
        app.register_blueprint(chat.bp)
        resp = app.test_client().post(
            "/api/chat/stream", json={"message": "你好", "session_id": "s-blocked"})
        body = resp.get_data(as_text=True)

        assert resp.status_code == 200
        assert "LLM 禁用时段" in body
        assert '"type": "done"' in body
        assert '"type": "chunk"' not in body


class TestScheduledTask:
    def test_工厂注册禁用时段任务(self):
        import agent.task_scheduler as ts

        ts.reset_scheduler()
        try:
            sched = ts.get_scheduler()
            hit = [t for t in sched.tasks if t.get("name") == "LLM 禁用时段"]
            assert hit, [t.get("name") for t in sched.tasks]
            task = hit[0]
            assert task["interval"] == 600
            assert task["zero_token"] is True
            assert task["owner"] == "agent/llm_idle_gate.py"
            assert "禁用时段" in task["description"]
        finally:
            ts.reset_scheduler()

    def test_可能调_llm_的任务在临近禁用时顺延(self, tmp_path, monkeypatch):
        import agent.task_scheduler as ts

        _write_config(tmp_path, lead_seconds=300)
        monkeypatch.setattr(gate, "_now", lambda: _at("2026-11-20 13:58"))
        monkeypatch.setattr(ts, "TASK_HISTORY_FILE", tmp_path / "task_history.jsonl")
        sched = ts.TaskScheduler()
        calls = []
        sched.add_interval_task(
            name="会调 LLM 的任务", func=lambda: calls.append(1),
            interval_seconds=60, zero_token=False,
        )
        task = sched.tasks[0]
        result = sched.run_task(task)
        assert result["status"] == "skipped"
        assert "顺延" in result["error"]
        assert calls == []

    def test_零_token_任务不受禁用时段影响(self, tmp_path, monkeypatch):
        import agent.task_scheduler as ts

        _write_config(tmp_path, lead_seconds=300)
        monkeypatch.setattr(gate, "_now", lambda: _at("2026-11-20 10:30"))
        monkeypatch.setattr(ts, "TASK_HISTORY_FILE", tmp_path / "task_history.jsonl")
        sched = ts.TaskScheduler()
        calls = []
        sched.add_interval_task(
            name="零 token 任务", func=lambda: calls.append(1),
            interval_seconds=60, zero_token=True,
        )
        result = sched.run_task(sched.tasks[0])
        assert result["status"] == "success"
        assert calls == [1]
