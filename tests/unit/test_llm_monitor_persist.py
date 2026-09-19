"""LLM 监控 · 会话持久化（服务关闭保存最后一条通信）单元测试

对应需求：「实现会话数据持久化功能：当服务关闭时自动保存会话的最后一条通信内容」。

行为契约：
  - 每记录一条通信即（节流）写快照到 data/llm_monitor_last.json；
  - 进程退出（服务关闭）由 atexit → persist_session_last() 兜底写盘；
  - 新实例启动时**回填**上次会话快照（restored=True），供「LLM 通信监控」回看；
  - clear() 同时清掉快照（否则重启后旧记录会"复活"）；
  - 未初始化监控器时退出钩子不新建实例（退出路径不得引入副作用）。
"""
from __future__ import annotations

import json

import pytest

import agent.llm_monitor as lm


@pytest.fixture
def persist_file(tmp_path, monkeypatch):
    """把快照文件指向临时目录（不污染仓库 data/）"""
    p = tmp_path / "llm_monitor_last.json"
    monkeypatch.setattr(lm, "PERSIST_FILE", str(p))
    return p


@pytest.fixture(autouse=True)
def _reset_singleton():
    lm.reset_llm_monitor()
    yield
    lm.reset_llm_monitor()


def _interaction(**kw):
    base = dict(source="chat", model="deepseek-chat", provider="deepseek",
                system_prompt="你是云枢。", messages=[{"role": "user", "content": "你好"}],
                response_text="你好呀", request_tokens=120, response_tokens=30)
    base.update(kw)
    return lm.LLMInteraction(**base)


class TestPersistOnRecord:
    def test_每条通信写盘快照(self, persist_file):
        monitor = lm.LLMMonitor(max_records=10)
        assert monitor.persist_last(_interaction()) is True
        payload = json.loads(persist_file.read_text(encoding="utf-8"))
        assert payload["response_text"] == "你好呀"
        assert payload["_persisted_at"]

    def test_record_落盘并可回填(self, persist_file):
        m1 = lm.LLMMonitor(max_records=10)
        m1.record(_interaction(response_text="第一条"))
        assert persist_file.exists()

        # 模拟服务重启：新实例应回填上次会话最后一条通信
        m2 = lm.LLMMonitor(max_records=10)
        assert m2.restored_from_disk is True
        records, total = m2.get_records()
        assert total == 1
        assert records[0]["response_text"] == "第一条"
        assert records[0]["restored"] is True

    def test_节流不丢最后状态_显式落盘补写(self, persist_file):
        """同一进程内多次记录：节流跳过后仍可由 persist_last 显式补写"""
        monitor = lm.LLMMonitor(max_records=10)
        for i in range(3):
            monitor.record(_interaction(response_text=f"第{i}条"))
        assert monitor.persist_last() is True
        payload = json.loads(persist_file.read_text(encoding="utf-8"))
        assert payload["response_text"] == "第2条"

    def test_无记录时落盘返回False(self, persist_file):
        monitor = lm.LLMMonitor(max_records=10)
        assert monitor.persist_last() is False
        assert not persist_file.exists()


class TestClearAndInfo:
    def test_clear_同时清除快照(self, persist_file):
        monitor = lm.LLMMonitor(max_records=10)
        monitor.record(_interaction())
        assert persist_file.exists()
        monitor.clear()
        assert not persist_file.exists()
        assert monitor.record_count == 0

    def test_persisted_info_摘要(self, persist_file):
        monitor = lm.LLMMonitor(max_records=10)
        assert monitor.persisted_info() == {}
        monitor.persist_last(_interaction())
        info = monitor.persisted_info()
        assert info["file"] == "llm_monitor_last.json"
        assert info["persisted_at"]

    def test_快照损坏时静默回退(self, persist_file):
        persist_file.write_text("{ 不是合法 JSON", encoding="utf-8")
        monitor = lm.LLMMonitor(max_records=10)
        assert monitor.restored_from_disk is False
        assert monitor.record_count == 0


class TestExitHook:
    def test_退出钩子保存最后一条(self, persist_file):
        monitor = lm.get_monitor()
        monitor.record(_interaction(response_text="关闭前最后一条"))
        assert lm.persist_session_last() is True
        payload = json.loads(persist_file.read_text(encoding="utf-8"))
        assert payload["response_text"] == "关闭前最后一条"

    def test_未初始化时不新建实例(self, persist_file):
        from agent.utils.singleton_manager import is_initialized
        assert is_initialized("llm_monitor") is False
        assert lm.persist_session_last() is False
        assert is_initialized("llm_monitor") is False
        assert not persist_file.exists()

    def test_退出钩子确已注册到_atexit(self, monkeypatch, persist_file):
        """服务关闭兜底必须是真 atexit 注册（而非只有测试里手动调用的函数）

        做法：把 atexit.register 换成记录器后重载模块（reload 在**同一个模块对象**
        上重跑模块代码），检查注册回调里确实包含 persist_session_last。
        注意：reload 会把模块级 `PERSIST_FILE` 重置回仓库默认路径，故随后必须显式
        改回临时路径 —— 否则后续用例（乃至真实 data/）会被本测试写脏。
        """
        import atexit
        import importlib

        captured = []
        monkeypatch.setattr(atexit, "register", lambda fn, *a, **k: captured.append(fn))
        reloaded = importlib.reload(lm)
        assert reloaded.persist_session_last in captured
        # 还原隔离路径（reload 丢掉了 monkeypatch 对模块常量的替换）
        lm.PERSIST_FILE = str(persist_file)
        assert lm.PERSIST_FILE == str(persist_file)
