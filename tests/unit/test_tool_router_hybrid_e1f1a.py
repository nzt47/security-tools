# -*- coding: utf-8 -*-
"""E1-F1-A 卡守卫：①「就绪 = 真的读到过 ready」②缓存优先（含命名空间解析）③孤儿防护

【本文件守护的三条缺陷（都是 E1-F1 定位、E1-F1-A 修复的）】
    1. `_ensure_worker()` 旧实现只看 `self._proc.poll() is None` ⇒ worker 还在加载
       就返回 True ⇒ 请求线程会往一个"还没 ready"的 worker 写 encode，
       读到的第一行是 ready 那一行（误当成 embeddings / 或把真响应留给下一次查询）。
    2. worker 用 `SentenceTransformer(repo_id)` 走**在线**解析 ⇒ 本机两个 HF 端点
       各 21 s 超时、每文件重试 5 次 ⇒ 就绪进入 >300 s 重尾 ⇒ 向量腿恒为 bm25_only。
       修法是缓存优先，但**必须**把裸模型名解析成能命中缓存的 repo id
       （`snapshot_download` 不会替你补 `sentence-transformers/` 命名空间）。
    3. worker 在"父进程消失"时无人回收 ⇒ 每跑一次留一个 450 MB 孤儿
       （E1-F1 实测 2 个）。修法：父进程侧 close()+atexit，子进程侧父进程存活看门狗。

【为什么这些断言值得存在】它们钉的是**可复现的失效型**，不是实现细节：
    · 未 ready ⇒ 必须不许写管道（否则就是静默错位）；
    · 裸名 ⇒ 必须解析到命名空间候选（否则缓存分支是死代码，就是本卡返工的原因）；
    · 全部候选未命中 ⇒ 必须回退在线（否则"换模型"变成"静默关掉向量腿"）。
"""
from __future__ import annotations

import json
import subprocess
import sys
import types

import pytest


# ════════════════════════════════════════════════════════════
#  探针夹具（本文件自持，不复用别处的假对象）
# ════════════════════════════════════════════════════════════

class _EmptyStream:
    """立即 EOF 的假流（模拟"子进程已死/立刻退出"）"""

    def read(self, *a):
        return ""

    def readline(self):
        return ""


class _DeadProc:
    """poll() 非 None（进程已死）+ stdout 立即 EOF —— 用于把握手按在"失败"分支上

    Why 需要它：`_ensure_worker()` 在"进程活着但没 ready"时会先回收再重新 Popen。
    测试必须替换 `mod.subprocess`，否则那次 Popen 会真的拉起一个 worker（实测会加载
    真实模型 17 s）—— 单测不该有这种副作用。
    """

    def __init__(self):
        self.pid = 424243
        self.stdin = self
        self.stdout = _EmptyStream()
        self.stderr = _EmptyStream()
        self.written = []
        self.killed = False

    def write(self, data):
        self.written.append(data)
        return len(data)

    def flush(self):
        pass

    def poll(self):
        return 0

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.killed = True


class _FakeSubprocess:
    """替换模块内 subprocess 名字绑定（不污染全局 subprocess.Popen）"""

    PIPE = "PIPE"
    STDOUT = "STDOUT"
    TimeoutExpired = subprocess.TimeoutExpired

    def __init__(self):
        self.proc = _DeadProc()
        self.popen_calls = 0

    def Popen(self, *a, **k):      # noqa: N802 - 与真实 API 同名
        self.popen_calls += 1
        return self.proc


class _AliveProc:
    """poll() 恒为 None（进程活着）+ 记录写进 stdin 的内容"""

    def __init__(self):
        self.pid = 424242
        self.written = []
        self.stdin = self
        self.stdout = None
        self.stderr = None
        self.killed = False

    def write(self, data):
        self.written.append(data)
        return len(data)

    def flush(self):
        pass

    def poll(self):          # noqa: D102 - 与 subprocess.Popen 同名
        return None

    def wait(self, timeout=None):
        return None

    def kill(self):
        self.killed = True


def _worker_ns():
    """把 worker 脚本当普通模块 exec 出来（`__name__ != "__main__"` ⇒ 不会真的跑起来）"""
    import agent.tool_router_hybrid as mod

    ns: dict = {"__name__": "e1f1a_worker_ns"}
    exec(compile(mod._WORKER_SCRIPT_EMBEDDING, "<embedding_worker_script>", "exec"), ns)
    return ns


# ════════════════════════════════════════════════════════════
#  一、缓存优先：模型名 → repo id 解析（返工①的核心）
# ════════════════════════════════════════════════════════════

class TestCacheCandidates:
    def test_bare_name_tries_namespaced_first(self):
        """裸模型名必须**先**试 sentence-transformers/<name>，再试裸名。

        Why 这条最重要：生产默认模型名就是裸名（_DEFAULT_MODEL），而
        snapshot_download 不会替你补命名空间 ⇒ 只试裸名 = 缓存分支永远抛
        LocalEntryNotFoundError = 每次都静默回退在线（本卡返工前的实测）。
        """
        ns = _worker_ns()
        got = ns["_cache_candidates"]("paraphrase-multilingual-MiniLM-L12-v2")
        assert [r for r, _ in got] == [
            "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
            "paraphrase-multilingual-MiniLM-L12-v2",
        ]
        assert [p for _, p in got] == ["hit_namespaced", "hit_bare"]

    def test_explicit_repo_id_is_not_rewritten(self):
        """已带 `/` 的名字本身就是 repo id，不得再拼前缀。"""
        ns = _worker_ns()
        got = ns["_cache_candidates"]("BAAI/bge-m3")
        assert got == [("BAAI/bge-m3", "hit_namespaced")]

    def test_empty_name_yields_no_candidate(self):
        ns = _worker_ns()
        assert ns["_cache_candidates"]("") == []


class TestLoadModelFallback:
    """三态可观测 + 回退在线（全部用注入的假库，零网络）"""

    def _fake_libs(self, monkeypatch, *, snap, st_calls, st_raise_for=()):
        fake_hf = types.ModuleType("huggingface_hub")
        fake_hf.snapshot_download = snap
        fake_st = types.ModuleType("sentence_transformers")

        class _FakeST:
            def __init__(self, name_or_path):
                st_calls.append(str(name_or_path))
                if str(name_or_path) in st_raise_for:
                    raise RuntimeError("simulated load failure")

        fake_st.SentenceTransformer = _FakeST
        monkeypatch.setitem(sys.modules, "huggingface_hub", fake_hf)
        monkeypatch.setitem(sys.modules, "sentence_transformers", fake_st)

    def test_cache_hit_namespaced(self, monkeypatch):
        calls: list = []

        def snap(repo_id=None, local_files_only=False, **kw):
            assert repo_id == "sentence-transformers/m1", repo_id
            assert local_files_only is True, "缓存优先必须 local_files_only=True"
            return "/hub/snap-m1"

        self._fake_libs(monkeypatch, snap=snap, st_calls=calls)
        ns = _worker_ns()
        model, source, probe = ns["_load_model"]("m1")
        assert probe == "hit_namespaced"
        assert source == "local_cache:/hub/snap-m1"
        assert calls == ["/hub/snap-m1"], "必须把**快照目录**交给 SentenceTransformer"

    def test_total_miss_falls_back_online(self, monkeypatch):
        """全部候选未命中 ⇒ 必须回退在线（否则缓存优先会变成"静默关掉向量腿"）。"""
        calls: list = []

        def snap(repo_id=None, local_files_only=False, **kw):
            raise RuntimeError("LocalEntryNotFoundError")

        self._fake_libs(monkeypatch, snap=snap, st_calls=calls)
        ns = _worker_ns()
        _, source, probe = ns["_load_model"]("m1")
        assert probe == "miss"
        assert source == "online:m1"
        assert calls == ["m1"], "回退在线时用的必须是**原始模型名**"

    def test_broken_snapshot_falls_back_online_and_is_reported(self, monkeypatch):
        """快照目录在、加载失败 ⇒ probe=load_failed 且**仍然**回退在线。"""
        calls: list = []

        def snap(repo_id=None, local_files_only=False, **kw):
            return "/hub/broken"

        self._fake_libs(monkeypatch, snap=snap, st_calls=calls,
                        st_raise_for=("/hub/broken",))
        ns = _worker_ns()
        _, source, probe = ns["_load_model"]("m1")
        assert probe == "load_failed"
        assert source == "online:m1"
        # 裸名有两个候选（命名空间 + 裸名），假 snapshot_download 对两者都返回同一个
        # 坏目录 ⇒ 两次本地加载都失败，最后**必须**回退在线（用原始模型名）。
        assert calls == ["/hub/broken", "/hub/broken", "m1"]

    def test_second_candidate_is_used_when_namespaced_misses(self, monkeypatch):
        calls: list = []
        seen: list = []

        def snap(repo_id=None, local_files_only=False, **kw):
            seen.append(repo_id)
            if repo_id == "sentence-transformers/m1":
                raise RuntimeError("namespaced miss")
            return "/hub/bare-m1"

        self._fake_libs(monkeypatch, snap=snap, st_calls=calls)
        ns = _worker_ns()
        _, source, probe = ns["_load_model"]("m1")
        assert seen == ["sentence-transformers/m1", "m1"]
        assert probe == "hit_bare"
        assert source == "local_cache:/hub/bare-m1"


# ════════════════════════════════════════════════════════════
#  二、「就绪 = 真的读到过 ready」
# ════════════════════════════════════════════════════════════

class TestReadyMeansReady:
    def test_live_proc_without_ready_is_not_ready(self, monkeypatch):
        """进程活着但**从没 ready** ⇒ 不许当成"就绪"，而且**不许往它写管道**。

        这是本卡最核心的不变量：旧实现此处返回 True（只看 poll()），于是调用方会往
        一个还没 ready 的 worker 写 encode，读到的第一行是 ready 那一行。
        新实现的做法是：先回收这个来路不明的进程，再重新走一次握手；
        本次调用在没有真 ready 之前一律返回 False。
        """
        import agent.tool_router_hybrid as mod

        fake = _FakeSubprocess()
        monkeypatch.setattr(mod, "subprocess", fake)

        idx = mod.EmbeddingIndex()
        proc = _AliveProc()
        idx._proc = proc
        assert idx._worker_ready.is_set() is False
        assert idx._ensure_worker() is False, "没读到 ready 就不许说就绪"
        # 只允许写"优雅退出"（= 回收这个来路不明的进程），**绝不许**写 encode
        # （写 encode 就等于把 ready 行当成 embeddings 读回来 —— 本卡要根除的错位）。
        assert not any('"encode"' in w for w in proc.written), proc.written
        assert all('"exit"' in w for w in proc.written), proc.written
        # 回收方式 = 先请求优雅退出（写 exit）；_AliveProc.wait() 不抛 ⇒ 不必走到 kill。
        assert idx._proc is not proc, "来路不明的活进程必须先被回收（不能再当它是当前 worker）"
        assert fake.popen_calls == 1, "回收之后才允许重新拉起（且只拉一次）"

    def test_ready_flag_makes_it_true(self):
        import agent.tool_router_hybrid as mod

        idx = mod.EmbeddingIndex()
        idx._proc = _AliveProc()
        idx._worker_ready.set()
        assert idx._ensure_worker() is True

    def test_search_refuses_unready_worker(self, monkeypatch):
        """请求路径（search）在未就绪时必须返回空**且不写管道**。

        Why 关键：错位的入口就是这里 —— 只要此处肯写 encode，worker 还没 ready 时
        那行 ready 就会被当成 embeddings 读走。故断言"零字节写入"。
        """
        np = pytest.importorskip("numpy")
        import agent.tool_router_hybrid as mod

        fake = _FakeSubprocess()
        monkeypatch.setattr(mod, "subprocess", fake)

        idx = mod.EmbeddingIndex()
        proc = _AliveProc()
        idx._proc = proc
        idx._embeddings = np.zeros((2, 4), dtype=np.float32)
        idx._doc_ids = ["a", "b"]
        assert idx.search("q", top_k=2) == []
        assert not any('"encode"' in w for w in proc.written), proc.written
        del np

    def test_init_failed_clears_ready(self):
        import agent.tool_router_hybrid as mod

        idx = mod.EmbeddingIndex()
        idx._proc = _AliveProc()
        idx._worker_ready.set()
        idx._init_failed = True
        assert idx._worker_ready.is_set() is False
        assert idx.worker_health()["worker_ready"] is False

    def test_cleanup_proc_clears_ready(self):
        import agent.tool_router_hybrid as mod

        idx = mod.EmbeddingIndex()
        idx._proc = _AliveProc()
        idx._worker_ready.set()
        idx._cleanup_proc()
        assert idx._worker_ready.is_set() is False

    def test_health_mode_never_claims_hybrid_while_unavailable(self):
        """mode 与 available 必须同源（不存在"报 hybrid 却供不了数"的组合态）。"""
        import agent.tool_router_hybrid as mod

        idx = mod.EmbeddingIndex()
        idx._proc = _AliveProc()
        h = idx.worker_health()
        assert h["available"] is False
        assert h["mode"] == "bm25_only"


# ════════════════════════════════════════════════════════════
#  三、孤儿防护：看门狗的位置与形态
# ════════════════════════════════════════════════════════════

class TestOrphanGuardShape:
    def test_guard_starts_before_import_and_load(self):
        """看门狗必须**在导入与加载之前**启动（顺序是实测结论，不是风格偏好）。

        Why：worker 的死亡信号只有"父进程没了"。原实现把它交给 stdin 的 EOF，
        而 stdin 的读取在加载之后 ⇒ 加载期间父进程死掉就没人管（孤儿）。
        本卡实测（X6）：Windows 进程句柄等待与 torch 导入**不冲突**（导入耗时与
        无看门狗对照一致）；而"后台线程读 stdin"与 torch 导入会**死锁**（X3/W1）。
        故正确解是：看门狗最先起，且**任何线程都不碰 stdin**。
        """
        import agent.tool_router_hybrid as mod

        src = mod._WORKER_SCRIPT_EMBEDDING
        i_main = src.index("def main():")   # 锚在 main 内，_load_model 的定义在 main 之前
        i_guard = src.index("_start_orphan_guard()", i_main)
        i_import = src.index("from sentence_transformers import SentenceTransformer", i_main)
        i_load = src.index("_load_model(model_name)", i_main)
        assert i_guard < i_import < i_load, (i_guard, i_import, i_load)

    def test_no_background_thread_reads_stdin(self):
        """守卫形态：**不得**再出现"后台线程读 stdin"（实测会卡死解释器）。"""
        import agent.tool_router_hybrid as mod

        src = mod._WORKER_SCRIPT_EMBEDDING
        assert "_REQ_Q" not in src
        assert "for _line in sys.stdin" not in src
        assert "for line in sys.stdin" in src, "协议行仍由主线程读"

    def test_parent_side_close_and_atexit_exist(self):
        """父进程侧必须有**代码里的**清理路径（close + atexit 钩子）。"""
        import agent.tool_router_hybrid as mod

        idx = mod.EmbeddingIndex()
        proc = _AliveProc()
        idx._proc = proc
        idx.close()
        assert proc.killed is True or proc.written, "close() 必须真的回收子进程"
        assert idx._proc is None
        assert callable(mod._shutdown_all_workers)
        assert mod.EmbeddingIndex.close.__doc__


# ════════════════════════════════════════════════════════════
#  四、可配置就绪超时（纵深防御）
# ════════════════════════════════════════════════════════════

class TestReadyTimeoutConfig:
    def test_code_default_is_at_least_120(self):
        import agent.tool_router_hybrid as mod

        assert mod._WORKER_READY_TIMEOUT_DEFAULT >= 120.0
        # 类属性别名必须与模块级常量同源（既有守卫也钉这一条）
        assert mod.EmbeddingIndex._WORKER_STARTUP_TIMEOUT == mod._WORKER_READY_TIMEOUT
        assert 0 < mod._WORKER_ENCODE_TIMEOUT <= mod._WORKER_READY_TIMEOUT

    def test_env_overrides(self, monkeypatch):
        import agent.tool_router_hybrid as mod

        monkeypatch.setenv("AGENT_HYBRID_WORKER_READY_TIMEOUT", "240")
        assert mod._resolve_worker_ready_timeout_from_env() == 240.0

    @pytest.mark.parametrize("bad", ["abc", "", "0", "-5"])
    def test_invalid_env_falls_back_to_default(self, monkeypatch, bad):
        import agent.tool_router_hybrid as mod

        monkeypatch.setenv("AGENT_HYBRID_WORKER_READY_TIMEOUT", bad)
        assert (mod._resolve_worker_ready_timeout_from_env()
                == mod._WORKER_READY_TIMEOUT_DEFAULT)
