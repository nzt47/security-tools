"""LLM 监控 · 会话持久化（落盘最近 N 条，重启回填）单元测试

对应需求：**服务关闭后重开，「LLM 通信监控」能看到最近的记录**（不只是最后一条）。

行为契约（2026-11-16 起）：
  - 落盘形状 {"records": [...], "_count": N, "_persisted_at": ...}，滚动保留最近 N 条
    （N = llm_monitor.persist_records，默认 30）；**兼容读取旧版的单条 dict**；
  - 每记录一条通信即（节流）写快照到 data/llm_monitor_last.json；
  - 进程退出（服务关闭）由 atexit → persist_session_last() 兜底写盘；
  - 新实例启动时**回填**快照里的最近 N 条（restored=True）；
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
        # 新形状：滚动快照（records 列表），不再直接把单条字段摊在顶层
        assert payload["_persisted_at"]
        assert payload["_count"] == 1
        assert payload["records"][-1]["response_text"] == "你好呀"

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
        """同一进程内多次记录：节流跳过后仍可由 persist_last 显式补写（且写的是最近 N 条）"""
        monitor = lm.LLMMonitor(max_records=10)
        for i in range(3):
            monitor.record(_interaction(response_text=f"第{i}条"))
        assert monitor.persist_last() is True
        payload = json.loads(persist_file.read_text(encoding="utf-8"))
        assert payload["_count"] == 3
        assert [r["response_text"] for r in payload["records"]] == ["第0条", "第1条", "第2条"]

    def test_无记录时落盘返回False(self, persist_file):
        monitor = lm.LLMMonitor(max_records=10)
        assert monitor.persist_last() is False
        assert not persist_file.exists()


class TestRollingSnapshot:
    def test_重启回填最近N条而不是一条(self, persist_file, monkeypatch):
        """落盘是滚动快照 ⇒ 重启后能看到最近 N 条（此前只落最后一条，重启只剩 1 条）。"""
        monkeypatch.setattr(lm, "PERSIST_RECORDS_DEFAULT", 5)
        m1 = lm.LLMMonitor(max_records=50)
        m1._persist_n = 5
        for i in range(7):
            m1._records.append(_interaction(response_text=f"第{i}条"))
        assert m1.persist_last() is True
        payload = json.loads(persist_file.read_text(encoding="utf-8"))
        assert payload["_count"] == 5
        assert [r["response_text"] for r in payload["records"]] == [f"第{i}条" for i in range(2, 7)]

        m2 = lm.LLMMonitor(max_records=50)
        assert m2.restored_from_disk is True
        records, total = m2.get_records()
        assert total == 5
        # get_records() 按**最新在前**返回（与接口一致），落盘顺序是旧在前
        assert [r["response_text"] for r in records] == [f"第{i}条" for i in range(6, 1, -1)]
        assert all(r["restored"] is True for r in records)

    def test_兼容旧的单条dict快照(self, persist_file):
        """2026-11-16 之前的文件是单条 dict（字段摊在顶层）⇒ 仍应回填 1 条。"""
        legacy = {
            "id": "abc123", "source": "chat", "response_text": "旧格式的一条",
            "timestamp": 1700000000.0, "_persisted_at": "2026-11-15 10:00:00",
        }
        persist_file.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
        monitor = lm.LLMMonitor(max_records=10)
        assert monitor.restored_from_disk is True
        records, total = monitor.get_records()
        assert total == 1
        assert records[0]["response_text"] == "旧格式的一条"
        assert monitor.persisted_info()["count"] == 1


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
        assert payload["records"][-1]["response_text"] == "关闭前最后一条"

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
