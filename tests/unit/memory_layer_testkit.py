"""TASK-S5-01 记忆四层单测公共夹具与替身（**测试支撑模块，不被收集为用例**）

为什么单独成模块：四个 `test_memory_*.py` 共用同一套运行时隔离与数据源替身；
集中在 `tests/unit/conftest.py`（共享文件）会波及其他套件，故独立成模块由各测试
文件显式导入夹具名（`pythonpath = tests/unit` 已在 pytest.ini 中配置）。

运行时隔离纪律（批次总表 §三 硬约束 6 / START 坑 1）
----------------------------------------------------
记忆/知识类用例极易污染运行时数据目录。本套件：
- 把 ``MEMORY_LAYERS_ROOT`` / ``MEMORY_SNAPSHOT_ROOT`` / ``MEMORY_IDENTITY_ROOT``
  全部重定向到 ``tmp_path``；
- **默认关闭进程级审计门面**（``audit.enabled = False``），使绝大多数用例零审计写入；
  需要取证链的用例改用 ``bound_audit_chain`` 夹具绑定 tmp 台账；
- 前后 ``reset_audit_chains()``，收尾后台 writer 线程。
"""

import os

import pytest

from agent.audit import facade as facade_mod
from agent.audit.chain import AuditChain, reset_audit_chains


# ════════════════════════════════════════════════════════════
#  运行时隔离
# ════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def memory_runtime(tmp_path, monkeypatch):
    """记忆四层套件的会话隔离：路径重定向 + 审计默认关闭（autouse）"""
    monkeypatch.setenv("MEMORY_LAYERS_ROOT", str(tmp_path / "layers"))
    monkeypatch.setenv("MEMORY_SNAPSHOT_ROOT", str(tmp_path / "snapshots"))
    monkeypatch.setenv("MEMORY_IDENTITY_ROOT", str(tmp_path / "identity"))
    reset_audit_chains()
    previous_enabled = facade_mod.audit.enabled
    facade_mod.audit.enabled = False
    try:
        yield tmp_path
    finally:
        facade_mod.audit.enabled = previous_enabled
        reset_audit_chains()


@pytest.fixture
def bound_audit_chain(tmp_path):
    """把进程级审计门面临时绑定到 tmp 台账（删除权匿名化取证用）"""
    reset_audit_chains()
    chain = AuditChain(
        str(tmp_path / "audit" / "audit_chain.db"),
        roots_path=str(tmp_path / "audit" / "roots.jsonl"),
        signing_key_path=str(tmp_path / "audit" / "roots.key"),
        auto_seal=False,
    )
    previous_chain = facade_mod.audit.bind(chain)
    previous_enabled = facade_mod.audit.enabled
    facade_mod.audit.enabled = True
    facade_mod.audit.reset_counters()
    try:
        yield chain
    finally:
        facade_mod.audit.bind(previous_chain)
        facade_mod.audit.enabled = previous_enabled
        chain.close(timeout=2.0)
        reset_audit_chains()


# ════════════════════════════════════════════════════════════
#  便利构造
# ════════════════════════════════════════════════════════════


def make_store(tmp_path, name="layers", **kwargs):
    """在 tmp 下建一个分层存储（绝不落到项目树 / 家目录）"""
    from agent.memory.layered_store import LayeredMemoryStore

    kwargs.setdefault("audit", False)
    return LayeredMemoryStore(root=str(tmp_path / name), **kwargs)


def tenancy(root, subject="alice", **overrides):
    """按工作区根目录解析租户上下文"""
    from agent.memory.tenancy import resolve_tenancy

    return resolve_tenancy(
        workspace_root=str(root), subject_id=subject,
        use_current_context=False, **overrides)


def make_entry(**overrides):
    """按 §3.11 最小集构造条目（默认 fact 层 / 租户 A / 主体 alice）"""
    from agent.memory.taxonomy import MemoryEntry, new_memory_id, project_scope

    params = {
        "id": new_memory_id(),
        "tenant_id": "ws_tenantA",
        "subject_id": "alice",
        "type": "fact",
        "content_redacted": "项目使用 pytest",
        "content_hash": "",
        "scope": project_scope("ws_tenantA"),
        "confidence": 0.8,
    }
    params.update(overrides)
    return MemoryEntry(**params)


# ════════════════════════════════════════════════════════════
#  数据源替身（触发①②）
# ════════════════════════════════════════════════════════════


class FakeResponse:
    def __init__(self, status):
        self.status = status


class FakeTenancy:
    """``UnifiedTrace.tenancy`` 替身（触发①作用域过滤用）"""

    def __init__(self, tenant_id="", workspace_id=""):
        self.tenant_id = tenant_id
        self.workspace_id = workspace_id


#: 与各测试文件中的 ROOT_A / ROOT_B 字面量一致（租户 = 其工作区哈希）
ROOT_A = "C:/repos/alpha"
ROOT_B = "C:/repos/beta"


def workspace_hash(workspace_root):
    """工作区根 → workspace-hash（= P7.2-08 的 tenant_id）"""
    from agent.observability.trace_v2 import derive_workspace_id

    return derive_workspace_id(workspace_root)


class FakeTrace:
    """``UnifiedTrace`` 替身；默认归属 ``ROOT_A`` 租户（与既有触发①用例的写入上下文一致）"""

    def __init__(self, capability_id, status="success", tenant_id=None,
                 workspace_id=None):
        resolved = workspace_hash(ROOT_A) if tenant_id is None else tenant_id
        self.capability_id = capability_id
        self.response = FakeResponse(status)
        self.tenancy = FakeTenancy(
            tenant_id=resolved,
            workspace_id=(resolved if workspace_id is None else workspace_id),
        )


class FakeTraceStore:
    """``UnifiedTraceStore`` 替身：只实现遗忘引擎用到的 query 叶子

    与真实 `UnifiedTraceStore.query()` 一致 —— **不提供租户过滤参数**
    （真实的租户隔离由 `TraceQualitySource` 在读取侧完成，见修复 #9）。
    """

    def __init__(self, traces=()):
        self.traces = list(traces)
        self.queries = []
        self.stopped = False

    def query(self, *, capability_id=None, since=None, **_kwargs):
        self.queries.append({"capability_id": capability_id, "since": since})
        return [t for t in self.traces if t.capability_id == capability_id]

    def stop(self, timeout=None):  # noqa: D401 与真实 store 同名
        self.stopped = True
        return True


class FakeQuality:
    def __init__(self, success_rate=0.0, sample_count=0):
        self.success_rate = success_rate
        self.sample_count = sample_count
        self.regression_baseline_id = ""


class FakeEvolution:
    def __init__(self, stage="native"):
        self.stage = stage


class FakeDescriptor:
    def __init__(self, stage="native", success_rate=0.9, sample_count=100):
        self.evolution = FakeEvolution(stage)
        self.quality = FakeQuality(success_rate, sample_count)


class FakeRegistry:
    """``DescriptorRegistry`` 替身（get / resolve_alias）"""

    def __init__(self, descriptors=None, aliases=None):
        self.descriptors = dict(descriptors or {})
        self.aliases = dict(aliases or {})

    def get(self, capability_id):
        return self.descriptors.get(capability_id)

    def resolve_alias(self, capability_id):
        return self.aliases.get(capability_id)


class BrokenRegistry:
    """不可用注册表替身（应"不判定失效"，避免误删）"""

    def get(self, capability_id):
        raise RuntimeError("registry down")

    def resolve_alias(self, capability_id):
        raise RuntimeError("registry down")


def make_quality_source(traces=(), now=1_000_000.0):
    """触发①数据源替身（真实计算路径，仅替换台账）"""
    from agent.memory.forgetting import TraceQualitySource

    fake = FakeTraceStore(traces)
    return TraceQualitySource(trace_store=fake), fake


def make_engine(tmp_path, store=None, *, traces=(), registry=None, clock=None, **kwargs):
    """构造遗忘引擎（全部数据源注入替身，零真实 I/O）"""
    from agent.memory.forgetting import (
        ForgettingEngine,
        MemorySnapshotStore,
        SourceValidityChecker,
    )

    store = store if store is not None else make_store(tmp_path, clock=clock)
    quality_source, fake_traces = make_quality_source(traces)
    engine = ForgettingEngine(
        store,
        snapshots=MemorySnapshotStore(root=str(tmp_path / "snapshots"), clock=clock),
        quality_source=quality_source,
        source_checker=SourceValidityChecker(registry=registry or FakeRegistry()),
        clock=clock,
        **kwargs,
    )
    return engine, fake_traces


class FakeClock:
    """可注入时钟（START 坑 3：TTL/时间类断言不依赖真实时钟边界）"""

    def __init__(self, start: float = 1_700_000_000.0):
        self.value = float(start)

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> float:
        self.value += float(seconds)
        return self.value


def project_root() -> str:
    """仓库根（用于"零运行时目录污染"断言）"""
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
