# -*- coding: utf-8 -*-
"""W2/TASK-03 守卫：embedding worker 崩溃的**可观测性**与**有界退避重启**

【本文件守护的缺陷（TASK-03 目标 3）】
    1. **降级后果不可见**：worker 被 0xC0000005 打死时，原实现只打一行
       `embedding.encode.proc_dead` / `embedding.worker.crash` 说明**原因**，
       没有任何一行说明**后果**（"语义检索已整条失效，进程余生只剩 BM25"）。
       运维必须自己把"一条 warn"推断成"能力已丢失"。
    2. **不可用态终身粘住**：`_init_failed` 一旦置 True，`_ensure_worker()`
       首行守卫就永远返回 False —— 崩掉的 worker 再也不会被拉起。
    3. **无重启 ⇒ 无风暴约束**：既没有重启，也就没有"重启多少次算够"的边界；
       一旦直接改成无脑重试，就会变成崩溃-重启风暴（2026-09-19 实测 9 次重启）。

【不变式（本文件钉死的性质）】
    · 任何一处 `self._init_failed = True` 都必然伴随
      `action=embedding.worker.unusable` + `degrade_to=bm25_only` 的 WARN；
    · 计数取**上升沿**：重复置位只算一次崩溃；
    · 退避窗口内**不得**拉起子进程（风暴的第一道闸）；
    · 重启次数有上限，用尽后明确放弃（风暴的第二道闸）；
    · `worker_health()` 如实反映 mode / 计数 / 退避 / 是否已放弃。

【为什么用假子进程而不是真跑模型】
    真跑要加载 470MB MiniLM，且**恰恰**要走原生栈——本用例要验的是崩溃**之后**
    的行为，不是崩溃本身。假 Popen 让"worker 秒死"可复现、可断言、无副作用。
"""
from __future__ import annotations

import json
import logging
import subprocess
import time

import pytest


# ════════════════════════════════════════════════════════════
#  假管道 / 假子进程（与 test_worker_startup_timeout.py 同型，本文件自持）
# ════════════════════════════════════════════════════════════

class _EmptyStream:
    """立即 EOF 的假流：模拟"子进程已经死了"。"""

    def read(self, *args):
        return ""

    def readline(self):
        return ""


class _Sink:
    def write(self, data):
        return len(data)

    def flush(self):
        pass


class _FakeProc:
    def __init__(self, returncode: int = 0, stdout=None):
        self.stdin = _Sink()
        self.stdout = stdout if stdout is not None else _EmptyStream()
        self.stderr = _EmptyStream()
        self.pid = 424242
        self._rc = returncode
        self.killed = False

    def poll(self):
        return self._rc

    def wait(self, timeout=None):
        return self._rc

    def kill(self):
        self.killed = True


class _FakeSubprocess:
    PIPE = "PIPE"
    STDOUT = "STDOUT"
    TimeoutExpired = subprocess.TimeoutExpired

    def __init__(self, proc):
        self._proc = proc
        self.popen_calls = 0

    def Popen(self, *args, **kwargs):  # noqa: N802 与真实 API 同名
        self.popen_calls += 1
        return self._proc


def _wait_restart(index, timeout: float = 20.0) -> bool:
    """等后台重启线程收尾。

    `_restarting` 在 `Thread.start()` **之前**、持锁置位，故 `_ensure_worker()`
    返回后它必然已是 True；这里只等它回到 False（= 线程确实跑完）。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with index._restart_lock:
            if not index._restarting:
                return True
        time.sleep(0.01)
    return False


# ════════════════════════════════════════════════════════════
#  一、崩溃留痕：原因 + **后果**都要在日志里
# ════════════════════════════════════════════════════════════

class TestCrashIsVisibleWithConsequence:
    def test_access_violation_is_diagnosed_and_declared_bm25_only(self, caplog):
        """0xC0000005 打死 worker ⇒ 既要有崩溃码诊断，也要有 BM25-only 降级声明。"""
        import agent.tool_router_hybrid as mod

        index = mod.EmbeddingIndex()
        index._proc = _FakeProc(returncode=mod._WIN_ACCESS_VIOLATION)

        with caplog.at_level(logging.WARNING):
            assert index._encode_via_worker(["文本"]) is None

        text = caplog.text
        # 1) 原因（原有能力，必须保住）
        assert "embedding.encode.proc_dead" in text, "崩溃原因未留痕"
        assert "0xC0000005" in text, f"崩溃码未被翻译成可读诊断:\n{text}"
        assert str(mod._WIN_ACCESS_VIOLATION) in text, "未记录原始 returncode"
        # 2) 后果（TASK-03 新增，本用例存在的理由）
        assert "embedding.worker.unusable" in text, (
            "worker 不可用态未留痕 —— 从日志看不出语义检索整条失效"
        )
        assert "bm25_only" in text, "未显式声明已降级为 BM25-only"
        # 3) 状态与日志一致
        health = index.worker_health()
        assert health["mode"] == "bm25_only"
        assert health["failure_total"] == 1

    def test_startup_eof_path_is_also_declared_bm25_only(self, caplog):
        """启动期 EOF（另一条致死路径）同样必须走到统一留痕口。"""
        import agent.tool_router_hybrid as mod

        fake = _FakeSubprocess(_FakeProc(returncode=mod._WIN_ACCESS_VIOLATION))
        # 用 monkeypatch 不可用（本文件无 fixture 参数），直接换模块绑定
        original = mod.subprocess
        mod.subprocess = fake
        try:
            index = mod.EmbeddingIndex()
            with caplog.at_level(logging.WARNING):
                assert index._ensure_worker() is False
        finally:
            mod.subprocess = original

        assert index.worker_health()["mode"] == "bm25_only"
        assert "embedding.worker.unusable" in caplog.text
        assert "embedding.worker.crash" in caplog.text, "启动期崩溃原因丢失"


class TestFailureCountingIsRisingEdge:
    def test_repeated_unusable_transitions_count_once(self):
        """12 处置位点共享一个 setter ⇒ 同一次崩溃不得被数成多次。"""
        import agent.tool_router_hybrid as mod

        index = mod.EmbeddingIndex()
        index._init_failed = True
        index._init_failed = True
        index._init_failed = True
        assert index.worker_health()["failure_total"] == 1

    def test_clearing_and_re_failing_counts_twice(self):
        """恢复后再次崩 ⇒ 是**第二次**故障，计数必须跟着涨（否则退避永不推进）。"""
        import agent.tool_router_hybrid as mod

        index = mod.EmbeddingIndex()
        index._init_failed = True
        index._init_failed = False
        index._init_failed = True
        assert index.worker_health()["failure_total"] == 2


# ════════════════════════════════════════════════════════════
#  二、风暴闸门：退避 + 次数上限
# ════════════════════════════════════════════════════════════

class TestRestartStormIsGated:
    def test_no_restart_within_backoff_window(self, monkeypatch):
        """退避窗口内反复调用 ⇒ **一次子进程都不许起**（风暴的第一道闸）。"""
        import agent.tool_router_hybrid as mod

        fake = _FakeSubprocess(_FakeProc(returncode=0))
        monkeypatch.setattr(mod, "subprocess", fake)

        index = mod.EmbeddingIndex()
        index._init_failed = True   # 首次故障 ⇒ 排下一次退避

        for _ in range(5):
            assert index._ensure_worker() is False

        assert fake.popen_calls == 0, "退避窗口内重复拉起子进程 = 崩溃-重启风暴"
        assert index.worker_health()["restart_attempts"] == 0

    def test_backoff_window_is_positive_and_scheduled(self):
        """退避必须是**正**窗口：为 0 就退化成"每请求重试一次"。"""
        import agent.tool_router_hybrid as mod

        index = mod.EmbeddingIndex()
        index._init_failed = True
        remaining = index.worker_health()["next_restart_in_sec"]
        assert remaining is not None
        assert 0.0 < remaining <= mod._WORKER_RESTART_BACKOFF_BASE_SEC

    def test_restart_is_attempted_after_backoff_and_is_bounded(self, monkeypatch):
        """退避到期 ⇒ 后台重试；用尽上限后 ⇒ 明确放弃且不再拉起子进程。"""
        import agent.tool_router_hybrid as mod

        # worker 起不来（秒 EOF）⇒ 每次重启都失败
        fake = _FakeSubprocess(_FakeProc(returncode=0))
        monkeypatch.setattr(mod, "subprocess", fake)

        index = mod.EmbeddingIndex()
        index._init_failed = True

        for expected in range(1, mod._WORKER_MAX_RESTARTS + 1):
            with index._restart_lock:
                index._next_restart_at = 0.0          # 人为跳到期
            assert index._ensure_worker() is False
            assert _wait_restart(index), "后台重启线程未在限时内收尾"
            assert index.worker_health()["restart_attempts"] == expected

        assert fake.popen_calls == mod._WORKER_MAX_RESTARTS, (
            "重启次数与实际 Popen 次数不符（可能有并发重复拉起）"
        )

        health = index.worker_health()
        assert health["retry_exhausted"] is True, "用尽上限后未标记放弃"
        assert health["next_restart_in_sec"] is None

        # 放弃后即便退避时刻已到，也不得再拉起
        before = fake.popen_calls
        for _ in range(3):
            with index._restart_lock:
                index._next_restart_at = 0.0
            assert index._ensure_worker() is False
        time.sleep(0.2)
        assert fake.popen_calls == before, "已达上限仍重启 = 无限风暴"

    def test_only_one_restart_thread_at_a_time(self, monkeypatch):
        """并发请求不得各自拉起一个 worker（双 Popen 会抢同一根 stdout）。"""
        import agent.tool_router_hybrid as mod

        fake = _FakeSubprocess(_FakeProc(returncode=0))
        monkeypatch.setattr(mod, "subprocess", fake)

        index = mod.EmbeddingIndex()
        index._init_failed = True
        with index._restart_lock:
            index._next_restart_at = 0.0

        # 第一次调用排进一次重启；其后调用在 _restarting 为真期间必须只降级
        assert index._ensure_worker() is False
        for _ in range(5):
            assert index._ensure_worker() is False
        # 重启线程已把 _init_failed 清掉了，所以此刻走的是 _restarting 守卫
        if index.worker_health()["restart_attempts"] < mod._WORKER_MAX_RESTARTS:
            assert fake.popen_calls <= mod._WORKER_MAX_RESTARTS
        _wait_restart(index)
        assert fake.popen_calls <= mod._WORKER_MAX_RESTARTS


# ════════════════════════════════════════════════════════════
#  三、只读健康出口（供 /api/health 接线，接线本身归 TASK-04）
# ════════════════════════════════════════════════════════════

class TestWorkerHealthContract:
    def test_healthy_state_reports_hybrid(self):
        import agent.tool_router_hybrid as mod

        health = mod.EmbeddingIndex().worker_health()
        assert health["mode"] == "hybrid"
        assert health["init_failed"] is False
        assert health["failure_total"] == 0
        assert health["max_restart_attempts"] == mod._WORKER_MAX_RESTARTS

    def test_health_matches_the_logged_consequence(self):
        """出口与日志必须说同一件事（否则探针与运维看到两个世界）。"""
        import agent.tool_router_hybrid as mod

        index = mod.EmbeddingIndex()
        index._init_failed = True
        health = index.worker_health()
        assert health["mode"] == "bm25_only"
        assert health["last_failure"]["failure_no"] == 1
        assert health["last_failure"]["next_restart_in_sec"] == pytest.approx(
            mod._WORKER_RESTART_BACKOFF_BASE_SEC
        )

    def test_retriever_exposes_the_same_health(self):
        """上层拿到的句柄是 HybridRetriever，故必须有一层透传。"""
        import agent.tool_router_hybrid as mod

        retriever = mod.HybridRetriever.__new__(mod.HybridRetriever)
        retriever._embedding = mod.EmbeddingIndex()
        retriever._tools_loaded = True
        retriever._embedding._init_failed = True

        health = retriever.embedding_health()
        assert health["mode"] == "bm25_only"
        assert health["retriever_degraded"] is True


# ════════════════════════════════════════════════════════════
#  四、原生栈顺序防线**不得被拆**（TASK-03 目标 2 的反向守卫）
# ════════════════════════════════════════════════════════════

class TestNativeOrderDefenseIntact:
    def test_hybrid_worker_does_not_bypass_the_pinned_order(self):
        """本文件守护的 worker 若自行绕过预导入，pyarrow 的干净窗口就没了。

        判据：`agent/tool_router_hybrid.py` 不得自行 importlib 预加载
        pyarrow/sklearn（顺序只能由 `agent/utils/native_preimport.py` 决定）。
        """
        import agent.tool_router_hybrid as mod
        import pathlib
        import re

        source = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
        offenders = re.findall(
            r"import_module\s*\(\s*['\"](numpy|pyarrow|pandas|sklearn)['\"]", source
        )
        assert offenders == [], (
            f"tool_router_hybrid 自行预导入原生栈 {offenders}："
            "顺序必须唯一由 agent/utils/native_preimport.py 决定"
        )
