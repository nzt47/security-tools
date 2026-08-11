"""素材层文件监听单例入口单元测试（任务1 · watcher.py）。

覆盖核心逻辑：
- KnowledgeWatcher 再导出与 ingest 单一事实源一致（【不易】单一事实源）
- start_knowledge_watcher 进程内单例：重复调用不重复启动（契约 Step 3）
- stop 后可重新启动；无活动实例时 stop 为空操作
- 真实 watchdog 集成：文件落入 inbox → 自动登记 meta + log.md
"""
import time

import pytest

from agent.knowledge import ingest as ingest_module
from agent.knowledge import watcher as watcher_module


@pytest.fixture(autouse=True)
def _cleanup_singleton():
    """每个用例前后清理进程内单例，避免监听线程泄漏/跨用例串扰。"""
    watcher_module.stop_knowledge_watcher()
    yield
    watcher_module.stop_knowledge_watcher()


# ════════════════════════════════════════════════════════════
#  再导出：watcher 模块与 ingest 单一事实源一致
# ════════════════════════════════════════════════════════════

def test_watcher_reexports_knowledge_watcher():
    """【不易】watcher.KnowledgeWatcher 必须是 ingest.KnowledgeWatcher 同一类。"""
    assert watcher_module.KnowledgeWatcher is ingest_module.KnowledgeWatcher


def test_watcher_public_api_exists():
    """契约 Step 3 要求的单例入口必须存在且可调用。"""
    assert callable(watcher_module.start_knowledge_watcher)
    assert callable(watcher_module.stop_knowledge_watcher)


# ════════════════════════════════════════════════════════════
#  单例语义（用 Fake 替换监听器，隔离单例逻辑测试）
# ════════════════════════════════════════════════════════════

class _FakeWatcher:
    """记录实例化次数与根目录的假监听器，避免依赖真实 watchdog 时序。"""

    instances: list = []

    def __init__(self, root):
        _FakeWatcher.instances.append(str(root))
        self.root = str(root)
        self._running = False

    @property
    def is_running(self):
        return self._running

    def start(self):
        self._running = True

    def stop(self):
        self._running = False


@pytest.fixture
def fake_watcher(monkeypatch):
    _FakeWatcher.instances = []
    monkeypatch.setattr(watcher_module, "KnowledgeWatcher", _FakeWatcher)
    return _FakeWatcher


def test_start_creates_singleton_with_given_root(fake_watcher, tmp_path):
    """start 后：以给定根目录实例化一次，单例处于运行态。"""
    root = tmp_path / "kb"
    watcher_module.start_knowledge_watcher(root)
    assert fake_watcher.instances == [str(root)]
    assert watcher_module._ACTIVE is not None
    assert watcher_module._ACTIVE.is_running


def test_repeated_start_is_idempotent(fake_watcher, tmp_path):
    """【契约 Step 3】重复调用 start 不重复启动（仅实例化一次）。"""
    watcher_module.start_knowledge_watcher(tmp_path / "kb")
    watcher_module.start_knowledge_watcher(tmp_path / "kb")
    watcher_module.start_knowledge_watcher(tmp_path / "kb")
    assert fake_watcher.instances == [str(tmp_path / "kb")]


def test_stop_releases_singleton_and_restart_works(fake_watcher, tmp_path):
    """stop 释放单例后，可再次 start 创建新实例（新根目录生效）。"""
    watcher_module.start_knowledge_watcher(tmp_path / "kb")
    watcher_module.stop_knowledge_watcher()
    assert watcher_module._ACTIVE is None

    watcher_module.start_knowledge_watcher(tmp_path / "kb2")
    assert fake_watcher.instances == [str(tmp_path / "kb"), str(tmp_path / "kb2")]


def test_stop_without_active_is_noop(tmp_path):
    """无活动实例时 stop 为空操作（不抛异常）。"""
    watcher_module.stop_knowledge_watcher()
    assert watcher_module._ACTIVE is None


def test_start_replaces_stale_non_running_singleton(fake_watcher, tmp_path):
    """单例存在但未运行（如 start 异常残留）时，再次 start 应替换为新实例。

    覆盖 `_ACTIVE is not None and _ACTIVE.is_running` 中
    "存在但 is_running=False" 的短路径分支（语义上与运行中幂等返回区分）。
    """
    watcher_module.start_knowledge_watcher(tmp_path / "kb")
    watcher_module._ACTIVE._running = False  # 模拟"存在但未运行"残留态
    watcher_module.start_knowledge_watcher(tmp_path / "kb")
    assert fake_watcher.instances == [str(tmp_path / "kb"), str(tmp_path / "kb")]
    assert watcher_module._ACTIVE.is_running


# ════════════════════════════════════════════════════════════
#  真实 watchdog 集成（文件落入 inbox → 自动登记）
# ════════════════════════════════════════════════════════════

def test_start_watcher_registers_dropped_inbox_file(tmp_path):
    """端到端：start 后文件落入 inbox → 自动生成 meta 并追加 log.md。"""
    kb = tmp_path / "kb"
    watcher_module.start_knowledge_watcher(kb)
    try:
        assert watcher_module._ACTIVE.is_running
        drop = kb / "inbox" / "auto.md"
        drop.parent.mkdir(parents=True, exist_ok=True)
        drop.write_text("auto registered via watcher", encoding="utf-8")

        meta = kb / "inbox" / "auto.md.meta.json"
        deadline = time.monotonic() + 5.0
        while not meta.is_file() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert meta.is_file(), "watcher 未在超时前登记 inbox 新文件"
        log_text = (kb / "log.md").read_text(encoding="utf-8")
        assert "ingest | auto" in log_text
    finally:
        watcher_module.stop_knowledge_watcher()
    assert watcher_module._ACTIVE is None  # stop 后单例已释放
