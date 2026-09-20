"""子进程读取超时回归测试 —— 声明了超时就必须真的生效

【本文件守护的缺陷】
    `agent/tool_router_reranker.py` 的 `_WORKER_STARTUP_TIMEOUT = 60` 曾经**只在注释里**
    被引用:读 ready 信号的代码是裸 `self._proc.stdout.readline()`,旁边的注释却写着
    "用进程退出判断超时,避免 readline 永久阻塞" —— 没有任何东西实现它。
    子进程若卡在模型加载里(既不输出也不退出),父进程就永久阻塞:
    日志停在启动前一行、工具检索链路静默挂死,且没有任何异常可捕获。
    `_predict_scores` 的响应读取、`tool_router_hybrid._encode_via_worker` 的响应读取
    是同一处缺陷的另外两个副本。

【为什么不用真的等 60s】
    把超时值 monkeypatch 成 0.3s,并用"readline 永不返回"的假管道证明调用方**在限时内
    返回失败值**。真实等 60s 既拖慢测试,也证明不了"有界"这件事本身。
    假管道的读线程会一直阻塞在 Event 上 —— 它是 daemon 线程,
    这本身就顺带验证了"遗留读线程不阻止解释器退出"。
"""
from __future__ import annotations

import json
import subprocess
import threading
import time

import pytest

# 永不 set ⇒ 模拟"子进程既不输出也不退出"
_NEVER = threading.Event()


# ════════════════════════════════════════════════════════════
#  假管道 / 假子进程
# ════════════════════════════════════════════════════════════

class _BlockingStream:
    """readline() 永久阻塞的假 stdout"""

    def __init__(self):
        self.readline_calls = 0

    def readline(self):
        self.readline_calls += 1
        _NEVER.wait()
        return ""  # pragma: no cover  —— 永远不会走到


class _ScriptedStream:
    """按脚本逐行返回的假 stdout(用尽后返回 '' 表示 EOF)"""

    def __init__(self, lines):
        self._lines = list(lines)
        self._lock = threading.Lock()

    def readline(self):
        with self._lock:
            return self._lines.pop(0) if self._lines else ""


class _EmptyStream:
    def read(self, *args):
        return ""

    def readline(self):
        return ""


class _Sink:
    """吞掉写入的假 stdin"""

    def __init__(self):
        self.written = []

    def write(self, data):
        self.written.append(data)
        return len(data)

    def flush(self):
        pass


class _FakeProc:
    """最小 Popen 替身:支持 poll/wait/kill/stdin/stdout/stderr"""

    def __init__(self, stdout=None):
        self.stdin = _Sink()
        self.stdout = stdout if stdout is not None else _BlockingStream()
        self.stderr = _EmptyStream()
        self.killed = False
        self.pid = 424242

    def poll(self):
        return None  # 恒为"仍在运行"——正是无限阻塞的前提

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.killed = True


class _FakeSubprocess:
    """替换模块内 subprocess 名字绑定,避免污染全局 subprocess.Popen"""

    PIPE = "PIPE"
    STDOUT = "STDOUT"
    TimeoutExpired = subprocess.TimeoutExpired

    def __init__(self, proc):
        self._proc = proc
        self.popen_calls = 0
        self.last_kwargs = {}

    def Popen(self, *args, **kwargs):  # noqa: N802 与真实 API 同名
        self.popen_calls += 1
        self.last_kwargs = kwargs
        return self._proc


# ════════════════════════════════════════════════════════════
#  一、_readline_with_timeout 本身
# ════════════════════════════════════════════════════════════

class TestReadlineWithTimeout:
    def test_timeout_returns_sentinel_within_bound(self):
        """阻塞的 readline 在限时内返回哨兵,而不是永久挂住"""
        import agent.tool_router_reranker as mod

        t0 = time.monotonic()
        result = mod._readline_with_timeout(_BlockingStream(), 0.3)
        elapsed = time.monotonic() - t0

        assert result is mod._READLINE_TIMED_OUT
        assert elapsed < 5.0, f"应当在限时内返回,实测 {elapsed:.2f}s"

    def test_normal_line_is_returned(self):
        import agent.tool_router_reranker as mod

        assert mod._readline_with_timeout(_ScriptedStream(["hello\n"]), 1.0) == "hello\n"

    def test_eof_is_not_confused_with_timeout(self):
        """EOF(子进程已退出)必须返回空串,不能当成超时 —— 否则诊断信息丢失

        调用方靠 `not line` 区分"子进程死了(读 stderr 诊断)"与"超时(进程还活着)"。
        """
        import agent.tool_router_reranker as mod

        result = mod._readline_with_timeout(_EmptyStream(), 1.0)
        assert result == ""
        assert result is not mod._READLINE_TIMED_OUT

    def test_reader_exception_is_reraised(self):
        """读线程里的异常要回传到调用方(既有 except 分支依赖它)"""
        import agent.tool_router_reranker as mod

        class _Broken:
            def readline(self):
                raise OSError("pipe broken")

        with pytest.raises(OSError):
            mod._readline_with_timeout(_Broken(), 1.0)


# ════════════════════════════════════════════════════════════
#  二、reranker:启动读取有界
# ════════════════════════════════════════════════════════════

class TestRerankerStartupTimeout:
    def test_hung_worker_fails_fast_and_cleans_up(self, monkeypatch):
        """worker 卡在加载(不输出也不退出)⇒ 在超时内返回 False 并回收子进程"""
        import agent.tool_router_reranker as mod

        monkeypatch.setattr(mod, "_WORKER_STARTUP_TIMEOUT", 0.3)
        proc = _FakeProc()
        fake_subprocess = _FakeSubprocess(proc)
        monkeypatch.setattr(mod, "subprocess", fake_subprocess)

        reranker = mod.ToolReranker()
        t0 = time.monotonic()
        ready = reranker._ensure_worker()
        elapsed = time.monotonic() - t0

        assert ready is False
        assert elapsed < 5.0, f"应当在限时内返回,实测 {elapsed:.2f}s"
        assert reranker._init_failed is True
        assert reranker._proc is None, "超时路径必须调用 _cleanup_proc 回收子进程"
        assert fake_subprocess.popen_calls == 1

    def test_rerank_degrades_without_blocking(self, monkeypatch):
        """公开入口 rerank 在 worker 卡死时返回原顺序(rerank_score=0.0),不抛异常"""
        import agent.tool_router_reranker as mod

        monkeypatch.setattr(mod, "_WORKER_STARTUP_TIMEOUT", 0.3)
        monkeypatch.setattr(mod, "subprocess", _FakeSubprocess(_FakeProc()))

        reranker = mod.ToolReranker()
        candidates = [("tool_a", 0.9), ("tool_b", 0.5)]
        t0 = time.monotonic()
        result = reranker.rerank(
            "query", candidates, tool_descriptions={"tool_a": "a", "tool_b": "b"}, top_k=5
        )
        elapsed = time.monotonic() - t0

        assert elapsed < 5.0, f"应当在限时内降级返回,实测 {elapsed:.2f}s"
        assert result == [("tool_a", 0.9, 0.0), ("tool_b", 0.5, 0.0)]

    def test_second_call_does_not_retry_after_timeout(self, monkeypatch):
        """超时后 _init_failed=True ⇒ 后续调用直接降级,不再反复起进程"""
        import agent.tool_router_reranker as mod

        monkeypatch.setattr(mod, "_WORKER_STARTUP_TIMEOUT", 0.3)
        fake_subprocess = _FakeSubprocess(_FakeProc())
        monkeypatch.setattr(mod, "subprocess", fake_subprocess)

        reranker = mod.ToolReranker()
        assert reranker._ensure_worker() is False
        assert reranker._ensure_worker() is False
        assert fake_subprocess.popen_calls == 1

    def test_ready_signal_still_works(self, monkeypatch):
        """正常路径不受影响:拿到 ready 即就绪(超时逻辑不得误伤)"""
        import agent.tool_router_reranker as mod

        ready_line = json.dumps(
            {"type": "ready", "load_time_sec": 1.23, "load_source": "/cache/model"}
        ) + "\n"
        monkeypatch.setattr(mod, "subprocess", _FakeSubprocess(_FakeProc(_ScriptedStream([ready_line]))))

        reranker = mod.ToolReranker()
        assert reranker._ensure_worker() is True
        assert reranker._load_time_sec == 1.23
        assert reranker._load_source == "/cache/model"


# ════════════════════════════════════════════════════════════
#  三、reranker:单次 predict 读取有界
# ════════════════════════════════════════════════════════════

class TestRerankerPredictTimeout:
    def test_hung_predict_returns_none_within_bound(self, monkeypatch):
        """worker 已就绪但推理卡死 ⇒ 返回 None(降级),不永久阻塞"""
        import agent.tool_router_reranker as mod

        monkeypatch.setattr(mod, "_PREDICT_READ_TIMEOUT", 0.3)
        reranker = mod.ToolReranker()
        reranker._proc = _FakeProc()  # 假装 worker 已启动

        t0 = time.monotonic()
        scores = reranker._predict_scores([("q", "d")])
        elapsed = time.monotonic() - t0

        assert scores is None
        assert elapsed < 5.0, f"应当在限时内返回,实测 {elapsed:.2f}s"
        assert reranker._init_failed is True
        assert reranker._proc is None

    def test_scores_still_parsed(self, monkeypatch):
        """正常响应仍能被解析(超时逻辑不得改变返回契约)"""
        import agent.tool_router_reranker as mod

        resp = json.dumps({"type": "scores", "scores": [0.9, 0.1]}) + "\n"
        reranker = mod.ToolReranker()
        reranker._proc = _FakeProc(_ScriptedStream([resp]))

        assert reranker._predict_scores([("q", "d1"), ("q", "d2")]) == [0.9, 0.1]

    def test_exited_worker_still_reports_eof(self, monkeypatch):
        """子进程已退出(stdout 立即 EOF)⇒ 仍走原来的 no_response 分支"""
        import agent.tool_router_reranker as mod

        reranker = mod.ToolReranker()
        reranker._proc = _FakeProc(_EmptyStream())

        assert reranker._predict_scores([("q", "d")]) is None
        assert reranker._init_failed is True


# ════════════════════════════════════════════════════════════
#  四、hybrid:encode 读取有界(同一缺陷的第三处)
# ════════════════════════════════════════════════════════════

class TestHybridEncodeTimeout:
    def test_hung_encode_returns_none_within_bound(self, monkeypatch):
        import agent.tool_router_hybrid as mod

        monkeypatch.setattr(mod, "_WORKER_ENCODE_TIMEOUT", 0.3)
        index = mod.EmbeddingIndex()
        index._proc = _FakeProc()

        t0 = time.monotonic()
        vectors = index._encode_via_worker(["文本"])
        elapsed = time.monotonic() - t0

        assert vectors is None
        assert elapsed < 5.0, f"应当在限时内返回,实测 {elapsed:.2f}s"
        assert index._init_failed is True
        assert index._proc is None

    def test_embeddings_response_still_parsed(self, monkeypatch):
        import agent.tool_router_hybrid as mod

        resp = json.dumps({"type": "embeddings", "vectors": [[0.1, 0.2]]}) + "\n"
        index = mod.EmbeddingIndex()
        index._proc = _FakeProc(_ScriptedStream([resp]))

        assert index._encode_via_worker(["文本"]) == [[0.1, 0.2]]


# ════════════════════════════════════════════════════════════
#  五、常量与源码守卫(防"注释声明了、代码没实现"重现)
# ════════════════════════════════════════════════════════════

class TestTimeoutDeclarationsAreReal:
    def test_startup_timeout_is_the_documented_60s(self):
        import agent.tool_router_reranker as mod

        assert mod._WORKER_STARTUP_TIMEOUT == 60

    def test_predict_timeout_is_shorter_than_startup_timeout(self):
        """模型已加载 ⇒ 单次 predict 的上限必须**明显短于**启动上限,且仍有余量

        实测:单次 rerank P99 4.6s、首次(含预热)7s
        (docs/perf/既有性能数据盘点.md)⇒ 下限取 10s,避免把长尾误判成死锁。
        """
        import agent.tool_router_reranker as mod

        assert 10.0 <= mod._PREDICT_READ_TIMEOUT < mod._WORKER_STARTUP_TIMEOUT

    def test_hybrid_startup_timeout_has_single_source(self):
        """EmbeddingIndex._WORKER_STARTUP_TIMEOUT 必须与模块级常量同源

        历史上它是独立的 60,而读取点用的是模块级 30 ⇒ 声明与行为不一致。
        """
        import agent.tool_router_hybrid as mod

        assert mod.EmbeddingIndex._WORKER_STARTUP_TIMEOUT == mod._WORKER_READY_TIMEOUT
        assert 0 < mod._WORKER_ENCODE_TIMEOUT <= mod._WORKER_READY_TIMEOUT

    @pytest.mark.parametrize(
        "module_name",
        ["agent.tool_router_reranker", "agent.tool_router_hybrid"],
    )
    def test_no_bare_stdout_readline_left(self, module_name):
        """源码守卫:两个模块都不得再有裸 `stdout.readline()`

        这是本次缺陷的**字面形态**("注释里有超时、代码里没有"),故用最直接的方式钉死:
        所有 stdout 读取都必须经过 _readline_with_timeout。
        (`stream.readline()` 出现在超时 helper 内部,不带 `.stdout`,不会误报。)
        """
        import importlib
        import pathlib
        import re

        module = importlib.import_module(module_name)
        source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
        offenders = re.findall(r"\S*\s*\.stdout\s*\.\s*readline\s*\(", source)
        assert offenders == [], f"{module_name} 仍有裸 stdout.readline(): {offenders}"
