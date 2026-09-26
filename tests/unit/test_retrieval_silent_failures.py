# -*- coding: utf-8 -*-
"""检索链静默错误修复的单元测试（任务卡 C1）

覆盖已实测的 4 个缺陷（审计 P1/P11 + Q3 W2）：
  1. 超时被 `with ThreadPoolExecutor` 的 __exit__(shutdown(wait=True)) 抵消
     → `test_search_timeout_actually_returns`（慢桩 sleep 5s，断言 3s 内返回且不抛）
  2. 技能链降级静默 return []（超时/异常/后端缺失/索引空/编码失败）
     → 断言每条路径都带 degraded 标记 + 结构化日志 event（不再静默）
  3. 落盘旧库（native_chroma）覆盖不足时的静默降级
     → `test_fallback_store_low_coverage_is_visible`
  4. ensure_indexed 只按 id 集合差 ⇒ 同 id 内容变更永不重编码（Q3 W2）
     → `test_content_change_triggers_reencode`（旧实现下必然失败）
  5. SkillIndexCache 不覆盖主轨 ⇒ 7 个主轨独有技能结构上不可召回（Q3 S2）
     → `TestIndexCacheMainTrack`

【只读约束】测试全部使用 tmp_path 沙箱与假模型：
  - 不加载 sentence-transformers / BGE-m3（4.25 GB）、不访问网络、不写生产索引；
  - 生产 data/skills_repo/.index/cache.json 与 data/skill_vectors/ 不被触碰。
"""
import json
import logging
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from agent.model_router.adapters import OpenAIAdapter
from agent.skills_mgmt.file_store import SkillFileStore
from agent.skills_mgmt.index_cache import SkillIndexCache
from agent.skills_mgmt.vector_adapter import (
    SkillVectorAdapter,
    SkillVectorSearchResult,
    _DEGRADE_FALLBACK_STORE_ENGAGED,
    _DEGRADE_FALLBACK_STORE_LOW_COVERAGE,
    _DEGRADE_INDEX_EMPTY,
    _DEGRADE_NO_BACKEND,
    _DEGRADE_TIMEOUT,
)


# ══════════════════════════════════════════════════════════════
#  测试工具
# ══════════════════════════════════════════════════════════════

class _DictRecordHandler(logging.Handler):
    """收集结构化 dict 日志（本项目统一用 log_dict(dict) 作为 msg）"""

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)

    def events(self):
        """返回所有 dict 形式的日志 payload"""
        return [r.msg for r in self.records if isinstance(r.msg, dict)]

    def actions(self):
        return [p.get("action") for p in self.events()]


# 【复核加固 1】模块级超时：把失败模式从"pytest 全局 120s 挂死"收敛为"明确超时"。
# pytest.ini 的全局 timeout 是 120s；本文件正常总耗时 ~8s（单用例 <2s），60s 有 ~30 倍余量。
pytestmark = pytest.mark.timeout(60)


@pytest.fixture(autouse=True)
def no_heavy_backends(monkeypatch):
    """铁律守卫：本文件的任何用例都**不得**真实初始化重型后端

    - 两个真实初始化入口（BGE-m3 via sentence-transformers 4.25GB / chromadb 原生）
      被替换成"记录并返回 None"的桩；
    - 用例结束后断言它们**从未被调用**；
    - 同时断言 sentence_transformers / chromadb 未在本文件用例执行期间被导入。
    这样"测试意外加载 4.25GB 模型"会**立即失败并指名**，而不是把时间悄悄烧掉。
    """
    from agent.skills_mgmt.vector_adapter import SkillVectorAdapter

    calls = []
    monkeypatch.setattr(
        SkillVectorAdapter, "_try_init_sentence_transformers",
        lambda self: (calls.append("sentence_transformers"), None)[1],
    )
    monkeypatch.setattr(
        SkillVectorAdapter, "_try_init_native_chroma",
        lambda self: (calls.append("native_chroma"), None)[1],
    )
    st_preimported = "sentence_transformers" in sys.modules
    chroma_preimported = "chromadb" in sys.modules
    try:
        yield
    finally:
        assert calls == [], "用例触发了真实后端初始化（必须打桩）: %s" % calls
        if not st_preimported:
            assert "sentence_transformers" not in sys.modules, \
                "本文件用例意外导入了 sentence_transformers（疑似真加载模型）"
        if not chroma_preimported:
            assert "chromadb" not in sys.modules, \
                "本文件用例意外导入了 chromadb"


@pytest.fixture()
def log_capture():
    """把 vector_adapter 的结构化日志抓下来（不改全局 logging 配置之外的状态）"""
    logger = logging.getLogger("agent.skills_mgmt.vector_adapter")
    handler = _DictRecordHandler()
    old_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)


class _FakeModel:
    """假 SentenceTransformer：可注入延迟、记录每次 encode 的文本"""

    def __init__(self, dim=4, delay=0.0):
        self._dim = dim
        self._delay = delay
        self.calls = []  # 每次 encode 的文本列表

    def get_sentence_embedding_dimension(self):
        return self._dim

    def encode(self, texts, normalize_embeddings=True, show_progress_bar=False, **kwargs):
        self.calls.append(list(texts))
        if self._delay:
            time.sleep(self._delay)
        # 确定性向量：文本长度参与取值，便于断言"内容变了向量也变了"
        return np.array([[len(t) % 7 + 1.0] * self._dim for t in texts], dtype=float)


def _write_skill(repo: Path, skill_id: str, name: str, description: str = "", body: str = "正文") -> None:
    """写入标准 skill.md（front matter + body）"""
    d = repo / skill_id
    d.mkdir(parents=True, exist_ok=True)
    content = (
        "---\n"
        f"id: {skill_id}\n"
        f"name: {name}\n"
        f"description: {description}\n"
        "category: test\n"
        "tags:\n"
        "  - test\n"
        "version: 0.1.0\n"
        "enabled: true\n"
        "---\n"
        f"{body}\n"
    )
    (d / "skill.md").write_text(content, encoding="utf-8")


def _make_adapter(repo: Path, fake_model, *, use_st=True):
    """构造一个只连假模型的 adapter（不触发任何真实后端初始化）"""
    fs = SkillFileStore(repo_path=str(repo))
    SkillIndexCache(fs)  # 挂载索引缓存（与生产 SkillsMgmtService.__init__ 同路径）
    adapter = SkillVectorAdapter(file_store=fs, use_sentence_transformers=False,
                                 use_native_chroma=False)
    backend = (fake_model, [], [], [])
    adapter._st_backend = backend if use_st else None
    adapter._vector_store = backend if use_st else adapter._vector_store
    adapter._ensure_vector_store = lambda: backend if use_st else None
    return adapter, fs


# ══════════════════════════════════════════════════════════════
#  1. 超时真正生效（审计 P1 / 对照 reranker.py:741-756）
# ══════════════════════════════════════════════════════════════

class TestSearchTimeoutIsReal:
    def test_search_timeout_actually_returns(self, tmp_path, log_capture):
        """慢桩（sleep 5s 的 encode）⇒ search() 必须在 3s 内返回且不抛异常

        回归守卫：旧实现用 `with ThreadPoolExecutor(...) as ex:`，超时异常穿出 with 时
        __exit__ 执行 shutdown(wait=True) 会**等满**卡住的任务 ⇒ 本用例耗时 ≈5s。
        """
        repo = tmp_path / "skills_repo"
        _write_skill(repo, "alpha", "alpha", "测试技能")
        slow = _FakeModel(delay=5.0)
        adapter, _fs = _make_adapter(repo, slow)
        adapter._st_backend = (slow, ["alpha"], np.ones((1, 4)), [{"skill_id": "alpha", "enabled": True}])
        adapter._vector_store = adapter._st_backend
        adapter._index_built = True  # 跳过懒构建（懒构建在超时包裹之外，见 report 的已知残留）

        t0 = time.time()
        results = adapter.search("帮我反思一下刚才的回答", top_k=3)
        elapsed = time.time() - t0

        assert elapsed < 3.0, f"search() 未按 2s 超时返回，实际 {elapsed:.2f}s"
        assert isinstance(results, list) and results == []
        assert isinstance(results, SkillVectorSearchResult)
        assert results.degraded is True
        assert results.degrade_reason == _DEGRADE_TIMEOUT
        assert results.vector_leg_empty is True
        assert "skill_vector.search.degraded" in log_capture.actions()

    def test_timeout_does_not_raise_and_marks_state(self, tmp_path):
        """超时不抛异常，且状态快照里能读出"为什么空"（上层/运维可见）"""
        repo = tmp_path / "skills_repo"
        _write_skill(repo, "alpha", "alpha", "测试技能")
        slow = _FakeModel(delay=3.0)
        adapter, _fs = _make_adapter(repo, slow)
        adapter._st_backend = (slow, ["alpha"], np.ones((1, 4)), [{"skill_id": "alpha"}])
        adapter._vector_store = adapter._st_backend
        adapter._index_built = True

        results = adapter.search("帮我写一份周报", top_k=3)
        assert results == []
        state = adapter.last_search_state()
        assert state["degraded"] is True
        assert state["reason"] == _DEGRADE_TIMEOUT
        assert state["backend"] == "st_backend"
        assert adapter.degraded is True


# ══════════════════════════════════════════════════════════════
#  2. 不再静默 return []（审计 P11）
# ══════════════════════════════════════════════════════════════

class TestNoSilentEmptyRecall:
    def test_backend_missing_is_marked(self, tmp_path, log_capture):
        """后端不可用 → 空结果 + degraded=no_backend + 结构化 event"""
        repo = tmp_path / "skills_repo"
        _write_skill(repo, "alpha", "alpha", "测试技能")
        adapter, _fs = _make_adapter(repo, _FakeModel(), use_st=False)

        results = adapter.search("帮我反思", top_k=3)
        assert results == []
        assert results.degraded is True
        assert results.degrade_reason == _DEGRADE_NO_BACKEND
        events = [p for p in log_capture.events()
                  if p.get("action") == "skill_vector.search.degraded"]
        assert events and events[0]["reason"] == _DEGRADE_NO_BACKEND
        assert events[0]["degrade_to"] == "tfidf_bm25_fusion"

    def test_empty_index_is_logged(self, tmp_path, log_capture):
        """后端在、但索引为空：以前**连日志都没有**，现在必须显式降级"""
        repo = tmp_path / "skills_repo"
        _write_skill(repo, "alpha", "alpha", "测试技能")
        empty = _FakeModel()
        adapter, _fs = _make_adapter(repo, empty)
        # 有后端但没有任何 doc（模拟"索引没建起来/刚被清空"）
        adapter._st_backend = (empty, [], [], [])
        adapter._vector_store = adapter._st_backend
        adapter._index_built = True

        results = adapter.search("帮我反思", top_k=3)
        assert results == []
        assert results.degrade_reason == _DEGRADE_INDEX_EMPTY
        assert "skill_vector.search.degraded" in log_capture.actions()
        assert empty.calls == []  # 未做无意义的 encode

    def test_negative_query_empty_is_not_degraded(self, tmp_path):
        """有意过滤（负样本启发式）必须与"故障降级"可区分"""
        repo = tmp_path / "skills_repo"
        _write_skill(repo, "alpha", "alpha", "测试技能")
        adapter, _fs = _make_adapter(repo, _FakeModel())
        adapter._index_built = True

        results = adapter.search("12345", top_k=3)
        assert results == []
        assert results.degraded is False
        assert results.vector_leg_empty is True

    def test_health_exposes_degradation_exit(self, tmp_path):
        """health() 必须给出降级原因出口（旧实现只有一个 vector_available 布尔）"""
        repo = tmp_path / "skills_repo"
        _write_skill(repo, "alpha", "alpha", "测试技能")
        adapter, _fs = _make_adapter(repo, _FakeModel(), use_st=False)
        adapter.search("帮我反思", top_k=3)
        health = adapter.health()
        assert health["degraded"] is True
        assert health["degrade_reason"] == _DEGRADE_NO_BACKEND
        assert health["last_search_state"]["reason"] == _DEGRADE_NO_BACKEND
        assert health["degrade_counts"][_DEGRADE_NO_BACKEND] >= 1
        assert "coverage" in health and "fallback_store" in health


# ══════════════════════════════════════════════════════════════
#  3. 落盘旧库（native_chroma）覆盖不足必须可见
# ══════════════════════════════════════════════════════════════

class TestFallbackStoreVisibility:
    def test_fallback_store_low_coverage_is_visible(self, tmp_path, log_capture, monkeypatch):
        """BGE-m3 不可用 → 落到只覆盖 8/30 的旧库时必须 WARN + 置降级标记"""
        repo = tmp_path / "skills_repo"
        for i in range(3):
            _write_skill(repo, f"skill-{i}", f"skill-{i}", "测试技能")
        adapter, _fs = _make_adapter(repo, _FakeModel())
        # 只读探针不回真实磁盘：注入 8/30 的覆盖快照（与审计实测同形）
        monkeypatch.setattr(adapter, "_collect_fallback_store_stats", lambda: {
            "path": str(tmp_path / "native_chroma"), "entries": 8, "source_count": 30,
            "newest_entry": "2026-07-23 17:07:59", "oldest_entry": "2026-07-23 17:07:59",
            "age_days": 64.1, "age_source": "embeddings.created_at",
        })

        adapter._warn_fallback_store(reason="sentence_transformers_init_failed")

        assert adapter.degraded is True
        assert adapter._active_degrade_reason == _DEGRADE_FALLBACK_STORE_LOW_COVERAGE
        events = [p for p in log_capture.events()
                  if p.get("action") == "skill_vector.fallback_store.engaged"]
        assert events, "落到降级旧库时必须留结构化日志"
        payload = events[0]
        assert payload["store_entries"] == 8
        assert payload["source_count"] == 30
        assert payload["store_age_days"] == 64.1
        assert payload["degrade_to"] == "native_chroma_fallback_store"
        health = adapter.health()
        assert health["degrade_reason"] == _DEGRADE_FALLBACK_STORE_LOW_COVERAGE
        assert health["fallback_store"]["entries"] == 8


# ══════════════════════════════════════════════════════════════
#  4. ensure_indexed 改为内容哈希判增量（审计 Q3 W2）
# ══════════════════════════════════════════════════════════════

class TestContentHashIncrementalIndexing:
    def test_adapter_lock_is_reentrant(self, tmp_path):
        """self._lock 必须**可重入**：普通 Lock 在同线程嵌套获取时是静默自死锁

        复核背景：C1 首版 ensure_indexed 持普通 Lock 时调用 _remove_skill_vector
        （内部再次 with self._lock）⇒ 线程永不返回、无异常无日志，pytest 只能靠超时
        中断。本用例把该类缺陷钉死：嵌套获取 + 持锁调用内部方法都必须在有界时间内完成。
        """
        repo = tmp_path / "skills_repo"
        _write_skill(repo, "alpha", "alpha", "技能甲")
        adapter, _fs = _make_adapter(repo, _FakeModel())
        adapter.ensure_indexed()

        done = {}

        def _nested():
            with adapter._lock:
                with adapter._lock:            # 普通 Lock 会在此永久阻塞
                    pass
                adapter._remove_skill_vector_locked("alpha")  # 持锁调用内部方法
            done["ok"] = True

        t = threading.Thread(target=_nested, daemon=True)
        t.start()
        t.join(timeout=10)
        assert not t.is_alive(), "self._lock 不可重入（同线程嵌套获取自死锁）"
        assert done.get("ok") is True

    @pytest.mark.timeout(40)
    def test_ensure_indexed_returns_within_bounded_time(self, tmp_path):
        """ensure_indexed 的全部路径（首次全量 / 增量删+加 / force 全量）必须有界返回

        用守护线程 + join(20) 把"挂死"变成"20s 内明确失败"，避免整份用例集被拖死。
        """
        repo = tmp_path / "skills_repo"
        _write_skill(repo, "alpha", "alpha", "技能甲")
        _write_skill(repo, "beta", "beta", "技能乙")
        model = _FakeModel()
        adapter, _fs = _make_adapter(repo, model)

        out = {}

        def _run():
            try:
                out["first"] = adapter.ensure_indexed()             # 首次全量
                _write_skill(repo, "alpha", "alpha", "技能甲-新")     # 同 id 内容变更
                adapter.fs.load_metadata_index(refresh=True)
                out["second"] = adapter.ensure_indexed()            # 增量（DELETE+INSERT）
                out["third"] = adapter.ensure_indexed(force=True)   # force 全量
            except Exception as e:  # noqa: BLE001
                out["error"] = repr(e)

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        t.join(timeout=20)
        assert not t.is_alive(), "ensure_indexed 未在 20s 内返回（疑似锁自旋/自死锁）"
        assert "error" not in out, out.get("error")
        assert out == {"first": 2, "second": 2, "third": 2}

    def test_unchanged_content_is_not_reencoded(self, tmp_path):
        """内容未变 → 第二次 ensure_indexed 不得再编码（幂等）"""
        repo = tmp_path / "skills_repo"
        _write_skill(repo, "alpha", "alpha", "技能甲")
        _write_skill(repo, "beta", "beta", "技能乙")
        model = _FakeModel()
        adapter, _fs = _make_adapter(repo, model)

        assert adapter.ensure_indexed() == 2
        assert len(model.calls) == 1 and len(model.calls[0]) == 2
        assert adapter.ensure_indexed() == 2
        assert len(model.calls) == 1, "内容未变却发生了重编码"

    def test_content_change_triggers_reencode(self, tmp_path, log_capture):
        """**同一 id 内容变更**必须重编码（旧实现按 id 集合差判增量 → 永不重编码）

        这是审计 Q3 W2 的直接回归守卫：把 skill.md 的 description 改掉但 id 不变。
        """
        repo = tmp_path / "skills_repo"
        _write_skill(repo, "alpha", "alpha", "技能甲-旧")
        _write_skill(repo, "beta", "beta", "技能乙")
        model = _FakeModel()
        adapter, _fs = _make_adapter(repo, model)
        assert adapter.ensure_indexed() == 2

        _write_skill(repo, "alpha", "alpha", "技能甲-新描述")  # id 不变、内容变
        adapter.fs.load_metadata_index(refresh=True)  # 模拟 git pull 后的下一次访问
        adapter.ensure_indexed()

        assert len(model.calls) == 2, "同 id 内容变更未触发重编码（Q3 W2 回归）"
        assert len(model.calls[1]) == 1, "只应重编码变更的那一条"
        assert "技能甲-新描述" in model.calls[1][0]
        assert adapter.indexed_count == 2
        # 日志里应能看出走的是内容哈希增量
        building = [p for p in log_capture.events()
                    if p.get("action") == "ensure_indexed.building"]
        assert building and building[-1]["incremental_by"] == "content_hash"
        assert building[-1]["dirty_skill_count"] == 1

    def test_dimension_mismatch_rebuilds_and_reencodes(self, tmp_path):
        """维度不匹配 → 清空重建，且**必须重新编码**（不能被内容哈希误判为"无变化"）"""
        repo = tmp_path / "skills_repo"
        _write_skill(repo, "alpha", "alpha", "技能甲")
        model = _FakeModel(dim=4)
        adapter, _fs = _make_adapter(repo, model)
        adapter.ensure_indexed()
        assert len(model.calls) == 1
        # 模拟模型换代：残留旧维度向量 + 已是"已索引"状态
        adapter._st_backend = (model, ["alpha"], np.ones((1, 3)), [{"skill_id": "alpha"}])
        adapter._vector_store = adapter._st_backend
        adapter._indexed_skill_ids = {"alpha"}   # 哈希仍在 ⇒ 只有维度校验能触发重建

        assert adapter.ensure_indexed() == 1
        assert len(model.calls) == 2, "维度重建后未重新编码（会被内容哈希误判为无变化）"
        assert adapter._st_backend[2].shape == (1, 4)

    def test_deleted_skill_vector_is_purged(self, tmp_path):
        """技能被删除 → 其向量必须从索引里清掉（旧实现会残留并继续被召回）"""
        import shutil
        repo = tmp_path / "skills_repo"
        _write_skill(repo, "alpha", "alpha", "技能甲")
        _write_skill(repo, "beta", "beta", "技能乙")
        model = _FakeModel()
        adapter, _fs = _make_adapter(repo, model)
        adapter.ensure_indexed()
        assert set(adapter._st_backend[1]) == {"alpha", "beta"}

        shutil.rmtree(repo / "beta")
        adapter.fs.load_metadata_index(refresh=True)
        adapter.ensure_indexed()

        assert set(adapter._st_backend[1]) == {"alpha"}
        assert "beta" not in adapter._indexed_content_hash

    def test_threshold_triggers_full_rebuild(self, tmp_path, log_capture):
        """脏条数达到阈值 → 整体重建；未达阈值 → 仍走增量

        阈值口径（实现里唯一判据）：max(_CONTENT_HASH_FULL_REBUILD_MIN_DIRTY=8,
        _CONTENT_HASH_FULL_REBUILD_DIRTY_RATIO × 总数)。20 个技能 ⇒ 阈值 = max(8, 8) = 8。
        """
        repo = tmp_path / "skills_repo"
        for i in range(20):
            _write_skill(repo, f"skill-{i:02d}", f"skill-{i:02d}", f"描述 {i}")
        model = _FakeModel()
        adapter, _fs = _make_adapter(repo, model)
        adapter.ensure_indexed()
        assert len(model.calls[0]) == 20

        # 改 2/20 = 10% < 阈值 8 ⇒ 增量
        for i in range(2):
            _write_skill(repo, f"skill-{i:02d}", f"skill-{i:02d}", f"描述变更 {i}")
        adapter.fs.load_metadata_index(refresh=True)
        adapter.ensure_indexed()
        assert len(model.calls) == 2
        assert len(model.calls[1]) == 2, "未达阈值应只重编码脏项"
        building = [p for p in log_capture.events()
                    if p.get("action") == "ensure_indexed.building"]
        assert building[-1]["full_rebuild"] is False
        assert building[-1]["rebuild_threshold"] == 8

        # 再改 9/20 = 45% ≥ 阈值 8 ⇒ 全量重建
        for i in range(2, 11):
            _write_skill(repo, f"skill-{i:02d}", f"skill-{i:02d}", f"描述再变更 {i}")
        adapter.fs.load_metadata_index(refresh=True)
        adapter.ensure_indexed()
        assert len(model.calls) == 3
        assert len(model.calls[2]) == 20, "达到阈值应整批重编码"
        building = [p for p in log_capture.events()
                    if p.get("action") == "ensure_indexed.building"]
        assert building[-1]["full_rebuild"] is True


# ══════════════════════════════════════════════════════════════
#  5. LLM 客户端显式超时与重试上限（审计 Q8 / P1 第③条）
# ══════════════════════════════════════════════════════════════

class TestLLMClientTimeout:
    def test_client_options_defaults(self):
        """默认必须显式传 timeout / max_retries（旧代码两者都不传 ⇒ 600s × 3）"""
        adapter = OpenAIAdapter("gpt-4", api_key="sk-test")
        options = adapter._client_options()
        assert options["max_retries"] == 1
        timeout = options["timeout"]
        assert getattr(timeout, "connect", None) == 5.0
        assert getattr(timeout, "read", None) == 45.0

    def test_client_receives_options(self):
        """真实构造的 OpenAI 客户端必须带上这两项（不发网络请求）"""
        adapter = OpenAIAdapter("gpt-4", api_key="sk-test")
        client = adapter._get_client()
        assert client is not None
        assert client.max_retries == 1
        assert client.timeout.read == 45.0
        assert client.timeout.connect == 5.0

    def test_explicit_options_override(self):
        """调用方显式指定时以调用方为准（不覆盖调用方语义）"""
        adapter = OpenAIAdapter("gpt-4", api_key="sk-test", timeout=12.5, max_retries=0)
        options = adapter._client_options()
        assert options["timeout"] == 12.5
        assert options["max_retries"] == 0

    def test_request_level_kwargs_still_pass_through(self):
        """请求级 timeout / max_retries 仍按调用方传入值生效（SDK 请求级优先）"""
        captured = {}

        class _Completions:
            @staticmethod
            def create(**kwargs):
                captured.update(kwargs)
                raise RuntimeError("stop-after-capture")

        class _Chat:
            completions = _Completions()

        class _StubClient:
            chat = _Chat()

        adapter = OpenAIAdapter("gpt-4", api_key="sk-test")
        adapter._client = _StubClient()
        out = adapter.generate("hi", timeout=7, max_retries=0)
        assert out["success"] is False  # 桩故意抛错
        assert captured["timeout"] == 7 and captured["max_retries"] == 0
        assert captured["model"] == "gpt-4"


# ══════════════════════════════════════════════════════════════
#  6. SkillIndexCache 覆盖主轨（审计 Q3 S2：7 项结构性缺口）
# ══════════════════════════════════════════════════════════════

class TestIndexCacheMainTrack:
    @staticmethod
    def _sandbox(tmp_path, main_extra=True):
        """造一个 <root>/data 布局的沙箱（主轨与 skills_repo 同处 data/）"""
        data = tmp_path / "data"
        repo = data / "skills_repo"
        repo.mkdir(parents=True, exist_ok=True)
        _write_skill(repo, "file-only", "file-only", "只在文件轨")
        _write_skill(repo, "overlap", "overlap", "文件轨版本描述")
        main = {
            "overlap": {"id": "overlap", "name": "overlap",
                        "description": "主轨版本描述（应被文件轨覆盖）",
                        "enabled": False, "tags": ["a"]},
        }
        if main_extra:
            main["main-only"] = {"id": "main-only", "name": "main-only",
                                 "description": "只在主轨", "enabled": True,
                                 "tags": "x,y", "content": "大字段不应进索引"}
        (data / "skills_mgmt.json").write_text(
            json.dumps(main, ensure_ascii=False), encoding="utf-8")
        return data, repo

    def test_main_track_only_skill_is_indexed(self, tmp_path):
        """主轨独有技能必须进 Layer-1 索引（旧实现只扫 skills_repo → 结构性漏召）"""
        _data, repo = self._sandbox(tmp_path)
        fs = SkillFileStore(repo_path=str(repo))
        cache = SkillIndexCache(fs)
        index = cache.get_all_metadata()

        assert set(index) == {"file-only", "overlap", "main-only"}
        assert index["main-only"]["_track"] == "main"
        assert index["main-only"]["description"] == "只在主轨"
        assert index["main-only"]["tags"] == ["x", "y"]  # 字符串按逗号切分
        # 大字段不进索引（否则 cache.json 会膨胀）
        assert "content" not in index["main-only"]
        # 文件轨优先：重合 id 不得被主轨覆盖（现有 Layer-1 语义零变化）
        assert index["overlap"]["description"] == "文件轨版本描述"
        assert index["overlap"]["enabled"] is True
        assert cache.get_main_track_metadata()["overlap"]["description"].startswith("主轨版本")

    def test_persisted_cache_keeps_partitions_separate(self, tmp_path):
        """持久化必须分区分开：skills 只放文件轨（否则 S1 巡检会把主轨条目误报为多余）"""
        data, repo = self._sandbox(tmp_path)
        fs = SkillFileStore(repo_path=str(repo))
        cache = SkillIndexCache(fs)
        cache.get_all_metadata()
        raw = json.loads((repo / ".index" / "cache.json").read_text(encoding="utf-8"))
        assert set(raw["skills"]) == {"file-only", "overlap"}
        assert set(raw["main_track"]) == {"overlap", "main-only"}
        assert raw["main_track_meta"]["hash"]
        assert raw["cache_version"] == SkillIndexCache.CACHE_VERSION

    def test_restart_loads_main_track_and_refreshes_on_change(self, tmp_path):
        """重启加载 + 主轨变更后自动刷新（mtime/hash 失效判据）"""
        data, repo = self._sandbox(tmp_path)
        fs = SkillFileStore(repo_path=str(repo))
        SkillIndexCache(fs).get_all_metadata()

        fs2 = SkillFileStore(repo_path=str(repo))
        cache2 = SkillIndexCache(fs2)
        cache2.load_on_startup()
        assert "main-only" in cache2.get_all_metadata()

        raw = json.loads((data / "skills_mgmt.json").read_text(encoding="utf-8"))
        raw["added-later"] = {"id": "added-later", "name": "added-later", "enabled": True}
        (data / "skills_mgmt.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        assert "added-later" in cache2.get_all_metadata()

    def test_get_metadata_falls_back_to_main_track(self, tmp_path):
        """单技能查询：文件轨没有 → 回主轨（旧实现直接返回 None）"""
        _data, repo = self._sandbox(tmp_path)
        fs = SkillFileStore(repo_path=str(repo))
        cache = SkillIndexCache(fs)
        assert cache.get_metadata("main-only")["description"] == "只在主轨"
        assert cache.get_metadata("does-not-exist") is None

    def test_unchanged_index_keeps_dict_identity(self, tmp_path):
        """未变化时必须返回同一个 dict（守 loader 倒排索引 id() 契约）"""
        _data, repo = self._sandbox(tmp_path)
        fs = SkillFileStore(repo_path=str(repo))
        cache = SkillIndexCache(fs)
        first = cache.get_all_metadata()
        assert cache.get_all_metadata() is first
        assert cache.get_all_metadata(refresh=True) is not first

    def test_main_track_disabled_by_env(self, tmp_path, monkeypatch):
        """回滚开关：SKILLS_INDEX_MAIN_TRACK=0 ⇒ 退回只服务文件轨"""
        monkeypatch.setenv("SKILLS_INDEX_MAIN_TRACK", "0")
        _data, repo = self._sandbox(tmp_path)
        fs = SkillFileStore(repo_path=str(repo))
        cache = SkillIndexCache(fs)
        assert set(cache.get_all_metadata()) == {"file-only", "overlap"}

# ══════════════════════════════════════════════════════════════
#  7. 【复核要求的语义锁定】主轨技能到底可不可召回（真判据 = 代码路径）
# ══════════════════════════════════════════════════════════════

class TestMainTrackRecallReachability:
    """锁定"主轨独有技能当前**不可召回**"这一事实（复核裁决 §S2 更正）

    背景：verify_index_drift.py 的 S2 曾用「cache.json 的 skills ∪ main_track」当可召回集合，
    那是**磁盘分区**而非**代码路径** ⇒ 主轨条目一被持久化，S2 就假绿（FAIL(7) → PASS(0)），
    而检索行为零变化。本类把"代码路径事实"钉死：
      · 裸 SkillLoader() 默认路径（capregistry/skillsearch.py:53、orchestrator.py:3874）
        **看不到**主轨独有技能；
      · 服务形态（SkillsMgmtService.__init__ 装配 SkillIndexCache）才看得到。
    **一旦有人把主轨真正接进裸路径（改 file_store.py / loader.py），本类会变红** ——
    那时请同步更新 scripts/verify_index_drift.py 的 S2 判据与报告结论，不要只改断言。
    """

    def test_main_only_skill_is_NOT_recallable_on_bare_path(self, tmp_path):
        """当前事实（缺陷）：裸 SkillFileStore 不合并主轨 ⇒ 主轨独有技能不可召回"""
        _data, repo = self._sandbox(tmp_path)
        bare = SkillFileStore(repo_path=str(repo))
        assert bare._index_cache is None, "裸 store 不应自带索引缓存（SkillLoader() 默认形态）"
        bare_ids = set(bare.load_metadata_index(refresh=True))
        assert bare_ids == {"file-only", "overlap"}, (
            "裸路径的可见集合变了 —— 若主轨已被接进该路径，请同步更新 "
            "scripts/verify_index_drift.py 的 S2 判据（pathA）与本报告结论")
        assert "main-only" not in bare_ids

    def test_main_only_skill_is_recallable_on_service_path(self, tmp_path):
        """对照（已接线的那条）：服务形态挂 SkillIndexCache 后主轨独有技能可召回"""
        _data, repo = self._sandbox(tmp_path)
        fs = SkillFileStore(repo_path=str(repo))
        SkillIndexCache(fs)
        assert "main-only" in set(fs.load_metadata_index(refresh=True))

    def test_service_path_matches_but_instruction_is_unavailable(self, tmp_path):
        """实测后果：主轨技能被召回后**取不到正文**（load_instruction 抛 SkillNotFoundError）

        这正是裁决 3 要求写清的"可向量化但不可注入正文"的确切症状：
        不是空正文、也不是不进候选，而是**召回得到、正文取不到**，由调用方各自兜底。
        """
        from agent.skills_mgmt.exceptions import SkillNotFoundError
        from agent.skills_mgmt.loader import SkillLoader

        _data, repo = self._sandbox(tmp_path)
        fs = SkillFileStore(repo_path=str(repo))
        SkillIndexCache(fs)
        loader = SkillLoader(fs)
        matched = {m.skill_id for m in
                   loader.match("只在主轨 描述", top_k=5, use_vector=False, use_bm25=False).matches}
        assert "main-only" in matched, "服务形态下主轨技能应可被召回（否则本用例前提不成立）"
        with pytest.raises(SkillNotFoundError):
            loader.load_instruction("main-only")

    @staticmethod
    def _sandbox(tmp_path):
        """<root>/data 布局：文件轨 2 条（file-only / overlap）+ 主轨 1 条独有（main-only）"""
        data = tmp_path / "data"
        repo = data / "skills_repo"
        repo.mkdir(parents=True, exist_ok=True)
        _write_skill(repo, "file-only", "file-only", "只在文件轨")
        _write_skill(repo, "overlap", "overlap", "文件轨版本描述")
        main = {
            "overlap": {"id": "overlap", "name": "overlap",
                        "description": "主轨版本描述（应被文件轨覆盖）", "enabled": True},
            "main-only": {"id": "main-only", "name": "main-only",
                          "description": "只在主轨 描述", "enabled": True,
                          "content": "主轨正文（当前没有任何 Layer-2 路径读取它）"},
        }
        (data / "skills_mgmt.json").write_text(
            json.dumps(main, ensure_ascii=False), encoding="utf-8")
        return data, repo


# ══════════════════════════════════════════════════════════════
#  8. 【裁决 1】RRF 返回对象透出向量腿降级标记（只加字段，不改检索逻辑）
# ══════════════════════════════════════════════════════════════

class TestRrfVectorLegVisibility:
    """向量腿为空/降级时，MatchResult 必须能告诉上层（不再只报 retrieval_method="rrf"）"""

    @staticmethod
    def _loader_with_degraded_adapter(repo):
        """造一个 RRF 场景：TF-IDF 有候选，向量腿返回"降级空结果"""
        from agent.skills_mgmt.loader import SkillLoader
        from agent.skills_mgmt.vector_adapter import (
            SkillVectorSearchResult,
            _DEGRADE_TIMEOUT,
        )

        fs = SkillFileStore(repo_path=str(repo))
        SkillIndexCache(fs)

        class _DegradedAdapter:
            # 非 None 的 _st_backend ⇒ 绕过 loader 的 BM25-fallback 快速退出
            _st_backend = ("fake-model", [], [], [])
            _native_chroma = None
            _active_degrade_reason = _DEGRADE_TIMEOUT

            def __init__(self):
                self.search_calls = 0

            def search(self, query, **kwargs):
                self.search_calls += 1
                # 真实的降级出口：空结果 + 显式标记（C1 修(3)）
                return SkillVectorSearchResult(
                    [], degraded=True, degrade_reason=_DEGRADE_TIMEOUT,
                    backend="st_backend", coverage={"indexed_count": 0})

            def last_search_state(self):
                return {"degraded": True, "reason": _DEGRADE_TIMEOUT}

        adapter = _DegradedAdapter()
        loader = SkillLoader(fs, vector_adapter=adapter)
        return loader, adapter

    def test_rrf_result_exposes_empty_and_degraded_vector_leg(self, tmp_path):
        repo = tmp_path / "skills_repo"
        _write_skill(repo, "alpha", "alpha", "帮我反思刚才的回答")
        _write_skill(repo, "beta", "beta", "帮我反思刚才的回答")
        loader, adapter = self._loader_with_degraded_adapter(repo)

        result = loader.match("帮我反思刚才的回答", top_k=3,
                              use_vector=True, use_bm25=True, fusion_mode="rrf")

        assert adapter.search_calls >= 1, "前置：向量腿确实被调用过"
        assert result.retrieval_method == "rrf"          # 既有语义不变
        assert result.fallback_used is False             # 既有语义不变
        # 新增字段：上层终于能看见"向量腿是空的且已降级"
        assert result.vector_leg_empty is True
        assert result.vector_leg_degraded is True
        assert result.vector_degrade_reason == "timeout"
        payload = result.to_dict()
        assert payload["vector_leg_empty"] is True
        assert payload["vector_leg_degraded"] is True
        assert payload["vector_degrade_reason"] == "timeout"

    def test_match_result_defaults_keep_old_semantics(self, tmp_path):
        """纯 TF-IDF 路径（未接向量腿）默认值必须是 False/None ⇒ 旧行为零变化"""
        repo = tmp_path / "skills_repo"
        _write_skill(repo, "alpha", "alpha", "帮我反思刚才的回答")
        fs = SkillFileStore(repo_path=str(repo))
        SkillIndexCache(fs)
        from agent.skills_mgmt.loader import SkillLoader
        result = SkillLoader(fs).match("帮我反思刚才的回答", top_k=3)
        assert result.vector_leg_empty is False
        assert result.vector_leg_degraded is False
        assert result.vector_degrade_reason is None
        assert result.to_dict()["vector_leg_degraded"] is False


# ══════════════════════════════════════════════════════════════
#  9. 【裁决 2】降级库覆盖率阈值可配（默认 50%）
# ══════════════════════════════════════════════════════════════

class TestFallbackCoverageThreshold:
    @staticmethod
    def _adapter(repo, entries, source_count):
        adapter, _fs = _make_adapter(repo, _FakeModel())
        adapter._collect_fallback_store_stats = lambda: {
            "path": str(repo.parent / "native_chroma"), "entries": entries,
            "source_count": source_count, "newest_entry": "2026-07-23 17:07:59",
            "oldest_entry": "2026-07-23 17:07:59", "age_days": 64.1,
            "age_source": "embeddings.created_at",
        }
        return adapter

    def test_below_default_threshold_marks_low_coverage(self, tmp_path):
        """8/30 = 26.7% < 50% ⇒ 必须标记 low_coverage 且结果带 degraded"""
        repo = tmp_path / "skills_repo"
        _write_skill(repo, "alpha", "alpha", "测试技能")
        adapter = self._adapter(repo, entries=8, source_count=30)
        adapter._warn_fallback_store(reason="sentence_transformers_init_failed")
        assert adapter._active_degrade_reason == _DEGRADE_FALLBACK_STORE_LOW_COVERAGE
        assert adapter.degraded is True
        stats = adapter.health()["fallback_store"]
        assert stats["min_coverage_threshold"] == 0.5
        assert stats["below_threshold"] is True
        assert stats["coverage_ratio"] == round(8 / 30, 4)

    def test_above_threshold_still_marks_engaged(self, tmp_path):
        """覆盖率达标（但仍是从 BGE-m3 降级来）⇒ 标记 engaged，不算 low_coverage"""
        repo = tmp_path / "skills_repo"
        _write_skill(repo, "alpha", "alpha", "测试技能")
        adapter = self._adapter(repo, entries=4, source_count=5)   # 80% >= 50%
        adapter._warn_fallback_store(reason="sentence_transformers_init_failed")
        assert adapter._active_degrade_reason == _DEGRADE_FALLBACK_STORE_ENGAGED
        assert adapter.degraded is True                            # 降级事实仍然可见
        assert adapter.health()["fallback_store"]["below_threshold"] is False

    def test_threshold_is_configurable_by_env(self, tmp_path, monkeypatch):
        """SKILL_VECTOR_FALLBACK_MIN_COVERAGE 可配（此处抬到 0.95 ⇒ 80% 也判低覆盖）"""
        monkeypatch.setenv("SKILL_VECTOR_FALLBACK_MIN_COVERAGE", "0.95")
        repo = tmp_path / "skills_repo"
        _write_skill(repo, "alpha", "alpha", "测试技能")
        adapter = self._adapter(repo, entries=4, source_count=5)
        adapter._warn_fallback_store(reason="sentence_transformers_init_failed")
        assert adapter._active_degrade_reason == _DEGRADE_FALLBACK_STORE_LOW_COVERAGE
        assert adapter.health()["fallback_store"]["min_coverage_threshold"] == 0.95

    def test_invalid_threshold_falls_back_to_default(self, tmp_path, monkeypatch):
        """非法阈值（非数字）回退默认 0.5，不抛异常"""
        monkeypatch.setenv("SKILL_VECTOR_FALLBACK_MIN_COVERAGE", "not-a-number")
        repo = tmp_path / "skills_repo"
        _write_skill(repo, "alpha", "alpha", "测试技能")
        adapter = self._adapter(repo, entries=1, source_count=10)
        adapter._warn_fallback_store(reason="sentence_transformers_init_failed")
        assert adapter.health()["fallback_store"]["min_coverage_threshold"] == 0.5
        assert adapter._active_degrade_reason == _DEGRADE_FALLBACK_STORE_LOW_COVERAGE
