"""预检 runner — memory_optimized 导入降级逻辑的 12 条路径检查（单事实源）

Why（不易）：_create_client 是「子进程探测 → 主进程导入 → Mock 兜底」三段式，
_probe_import 带模块级缓存。本模块用 mock 数据逐一验证每条路径与边界，
不改业务代码、不依赖真实 chromadb（全部通过 mock 模拟，包括 30s 超时场景）。

单事实源（简易）：本文件是 demo 脚本逻辑的唯一归属。CLI（python -m agent.preflight）
与 pytest（tests/unit/test_preflight_runner.py）都调用 run_preflight()，
不再维护 demo/pytest 两套断言实现。
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import types
from dataclasses import dataclass, field
from typing import Callable
from unittest import mock

from agent import memory_optimized as mo


# ---------- mock helpers ----------


class _FakeSettings:
    """记录构造参数的假 Settings（校验 anonymized_telemetry / allow_reset）。"""

    def __init__(self, **kw):
        self.anonymized_telemetry = kw.get("anonymized_telemetry")
        self.allow_reset = kw.get("allow_reset")


class _FakeProc:
    """模拟 subprocess.run 的返回对象。"""

    def __init__(self, returncode):
        self.returncode = returncode


def _make_instance():
    """绕过 __init__（含异步初始化/磁盘检查），只构造导入逻辑所需实例。"""
    inst = mo.OptimizedChromaDB.__new__(mo.OptimizedChromaDB)
    inst.persist_directory = tempfile.mkdtemp(prefix="mo_preflight_")
    inst._client = None
    return inst


def _make_fake_chromadb(persistent_client):
    """构造可注入 sys.modules 的假 chromadb 模块（含 chromadb.config.Settings）。"""
    fake_chromadb = types.ModuleType("chromadb")
    fake_config = types.ModuleType("chromadb.config")
    fake_config.Settings = _FakeSettings
    fake_chromadb.config = fake_config
    fake_chromadb.PersistentClient = persistent_client
    return fake_chromadb, fake_config


def _reset_cache():
    """恢复模块级探测缓存（预检结束后避免污染同进程其他代码）。"""
    mo._CHROMADB_IMPORT_OK = None


# ---------- 检查结果 ----------


@dataclass
class CheckResult:
    """单条路径检查结果。"""

    index: int          # 路径编号（1-12，与 CLI/文档一致）
    name: str           # 路径描述
    ok: bool            # 是否通过
    detail: str = ""    # 失败原因（ok=False 时）

    def __str__(self) -> str:
        mark = "✓" if self.ok else "✗"
        suffix = f"  ← {self.detail}" if not self.ok else ""
        return f"[{self.index}] {self.name}  {mark}{suffix}"


# ---------- _create_client 分支（A/B/C/D/E） ----------


def _case_probe_unavailable() -> None:
    """A：子进程探测不可用(False) → 直接 MockChromaClient，不进入主进程导入。"""
    inst = _make_instance()
    with mock.patch.object(mo, "_probe_import", return_value=False) as probe:
        inst._create_client()
    probe.assert_called_once_with("import chromadb; from chromadb.config import Settings")
    assert isinstance(inst._client, mo.MockChromaClient), type(inst._client)


def _case_main_process_import_fails() -> None:
    """B：探测成功但主进程 `import chromadb` 抛 ImportError → MockChromaClient。"""
    real_import = __import__  # 在 patch 前捕获，避免 fake 内部引用被替换后自递归

    def _fake_import(name, *args, **kwargs):
        if name == "chromadb":
            raise ImportError("模拟 chromadb import 不可用")
        return real_import(name, *args, **kwargs)  # pragma: no cover — mock 兜底，仅对非 chromadb 透明回退，patch 期内不会触发

    inst = _make_instance()
    with mock.patch.object(mo, "_probe_import", return_value=True), mock.patch(
        "builtins.__import__", side_effect=_fake_import
    ):
        inst._create_client()
    assert isinstance(inst._client, mo.MockChromaClient), type(inst._client)


def _case_settings_import_fails() -> None:
    """C：`import chromadb` 成功，但 `from chromadb.config import Settings` 失败 → Mock。

    Why 注入假顶层 chromadb：`import chromadb` 命中 sys.modules 快速返回，
    `chromadb.config` 触发 fake_import 抛 ImportError——避免真实导入 + 父包回退递归。
    """
    fake_chromadb = types.ModuleType("chromadb")
    real_import = __import__

    def _fake_import(name, *args, **kwargs):
        if name == "chromadb.config":
            raise ImportError("模拟 chromadb.config 不可用")
        return real_import(name, *args, **kwargs)

    inst = _make_instance()
    with mock.patch.object(mo, "_probe_import", return_value=True), mock.patch(
        "builtins.__import__", side_effect=_fake_import
    ), mock.patch.dict(sys.modules, {"chromadb": fake_chromadb}):
        inst._create_client()
    assert isinstance(inst._client, mo.MockChromaClient), type(inst._client)


def _case_client_creation_fails() -> None:
    """D：探测/import 成功，但 PersistentClient 创建抛异常 → MockChromaClient。"""

    def _boom(**kwargs):
        raise RuntimeError("模拟 PersistentClient 创建失败")

    inst = _make_instance()
    fake_chromadb, fake_config = _make_fake_chromadb(persistent_client=_boom)
    with mock.patch.object(mo, "_probe_import", return_value=True), mock.patch.dict(
        sys.modules, {"chromadb": fake_chromadb, "chromadb.config": fake_config}
    ):
        inst._create_client()
    assert isinstance(inst._client, mo.MockChromaClient), type(inst._client)


def _case_full_success() -> None:
    """E：全部成功 → 保留真实客户端，且校验 PersistentClient 关键参数。"""
    fake_client = object()
    captured = {}

    def _fake_persistent_client(**kwargs):
        captured.update(kwargs)
        return fake_client

    inst = _make_instance()
    fake_chromadb, fake_config = _make_fake_chromadb(persistent_client=_fake_persistent_client)
    with mock.patch.object(mo, "_probe_import", return_value=True), mock.patch.dict(
        sys.modules, {"chromadb": fake_chromadb, "chromadb.config": fake_config}
    ):
        inst._create_client()
    assert inst._client is fake_client, type(inst._client)
    # 关键参数校验：path 必须指向持久化目录；settings 必须禁用遥测、允许 reset
    assert captured.get("path") == inst.persist_directory, captured
    settings = captured.get("settings")
    assert settings is not None, captured
    assert settings.anonymized_telemetry is False, settings.__dict__
    assert settings.allow_reset is True, settings.__dict__


# ---------- _probe_import 真实实现分支（①缓存 ②rc=0 ③rc!=0 ④异常） ----------


def _run_real_probe(subprocess_run_impl):
    """真实 _probe_import + 注入 subprocess.run 行为（每 case 前重置缓存）。"""
    _reset_cache()
    with mock.patch.object(mo.subprocess, "run", side_effect=subprocess_run_impl):
        return mo._probe_import("import chromadb; from chromadb.config import Settings")


def _case_probe_success() -> None:
    """②：子进程 returncode==0 → True 并写入缓存。"""
    result = _run_real_probe(lambda *a, **k: _FakeProc(0))
    assert result is True and mo._CHROMADB_IMPORT_OK is True, mo._CHROMADB_IMPORT_OK


def _case_probe_nonzero() -> None:
    """③：子进程 returncode!=0 → False 并写入缓存。"""
    result = _run_real_probe(lambda *a, **k: _FakeProc(1))
    assert result is False and mo._CHROMADB_IMPORT_OK is False, mo._CHROMADB_IMPORT_OK


def _case_probe_subprocess_exception() -> None:
    """④：subprocess.run 抛 TimeoutExpired → False 并写入缓存。"""

    def _raise_timeout(*a, **k):
        raise subprocess.TimeoutExpired(["python", "-c", "x"], timeout=30.0)

    result = _run_real_probe(_raise_timeout)
    assert result is False and mo._CHROMADB_IMPORT_OK is False, mo._CHROMADB_IMPORT_OK


def _case_cache_reuse() -> None:
    """①：缓存命中后直接复用，不再启动子进程（subprocess.run 仅调用一次）。"""
    _reset_cache()
    calls = []

    def _impl(*a, **k):
        calls.append(1)
        return _FakeProc(0)

    with mock.patch.object(mo.subprocess, "run", side_effect=_impl):
        assert mo._probe_import("x") is True
        assert mo._probe_import("x") is True
    assert len(calls) == 1, f"subprocess.run 被调用 {len(calls)} 次"


# ---------- 端到端 ----------


def _case_probe_timeout_degrade() -> None:
    """端到端：模拟子进程 30s 超时 → 真实 _probe_import False → 全链路降级 Mock。

    Why 不等真实 30s：_probe_import 内部 subprocess.run(timeout=30s) 超时后抛
    TimeoutExpired，与「子进程 import chromadb 卡死 30 秒被 terminate」完全等价。
    """
    _reset_cache()
    inst = _make_instance()

    def _raise_timeout(*a, **k):
        raise subprocess.TimeoutExpired(
            [sys.executable, "-c", "import chromadb"],
            timeout=mo._CHROMADB_IMPORT_TIMEOUT,
        )

    with mock.patch.object(mo.subprocess, "run", side_effect=_raise_timeout):
        # 不 patch _probe_import：走真实探测 → 内部 subprocess.run 抛超时 → False
        inst._create_client()
    assert isinstance(inst._client, mo.MockChromaClient), type(inst._client)
    assert mo._CHROMADB_IMPORT_OK is False


def _case_probe_oserror() -> None:
    """④变体：subprocess.run 抛 OSError（子进程启动失败）→ False 并写入缓存。"""
    result = _run_real_probe(lambda *a, **k: (_ for _ in ()).throw(OSError("模拟子进程启动失败")))
    assert result is False and mo._CHROMADB_IMPORT_OK is False, mo._CHROMADB_IMPORT_OK


def _case_probe_ok_end_to_end() -> None:
    """端到端正例：真实探测 rc=0 → 缓存 → 主进程导入 → 创建成功 → 真实客户端。

    Why 与 case5 的区别：case5 patch 掉 _probe_import 只验证主进程侧；本 case
    不 patch _probe_import，走「子进程探测 → 缓存 → 导入 → 创建」完整链路正例。
    """
    _reset_cache()
    inst = _make_instance()
    fake_client = object()
    fake_chromadb, fake_config = _make_fake_chromadb(persistent_client=lambda **kw: fake_client)
    with mock.patch.object(
        mo.subprocess, "run", side_effect=lambda *a, **k: _FakeProc(0)
    ), mock.patch.dict(sys.modules, {"chromadb": fake_chromadb, "chromadb.config": fake_config}):
        inst._create_client()
    assert inst._client is fake_client, type(inst._client)
    assert mo._CHROMADB_IMPORT_OK is True


# ---------- 顺序执行 ----------

# 路径编号与历史 demo/文档一致（1-12）
CASES: list[tuple[int, str, Callable[[], None]]] = [
    (1, "探测不可用(False) → MockChromaClient", _case_probe_unavailable),
    (2, "探测成功 + `import chromadb` 失败 → MockChromaClient", _case_main_process_import_fails),
    (3, "探测成功 + `chromadb.config` 导入失败 → MockChromaClient", _case_settings_import_fails),
    (4, "探测/import 成功 + 客户端创建失败 → MockChromaClient", _case_client_creation_fails),
    (5, "探测/import/创建全部成功 → 真实客户端，path/settings 参数正确", _case_full_success),
    (6, "探测：子进程 returncode==0 → True（已缓存）", _case_probe_success),
    (7, "探测：子进程 returncode!=0 → False（已缓存）", _case_probe_nonzero),
    (8, "探测：subprocess.run 抛 TimeoutExpired → False（已缓存）", _case_probe_subprocess_exception),
    (11, "探测：subprocess.run 抛 OSError → False（已缓存）", _case_probe_oserror),
    (9, "缓存复用：第二次探测不启动子进程（仅 1 次 subprocess.run）", _case_cache_reuse),
    (10, "子进程探测超时(30s 模拟) → 降级 MockChromaClient", _case_probe_timeout_degrade),
    (12, "端到端：真实探测成功(rc=0) → 导入/创建成功 → 真实客户端", _case_probe_ok_end_to_end),
]


def run_preflight() -> list[CheckResult]:
    """顺序执行全部路径检查，返回结果列表（绝不抛异常，失败转 CheckResult）。"""
    results: list[CheckResult] = []
    _reset_cache()
    try:
        for index, name, fn in CASES:
            try:
                fn()
                results.append(CheckResult(index=index, name=name, ok=True))
            except AssertionError as e:
                results.append(CheckResult(index=index, name=name, ok=False, detail=str(e)))
            except Exception as e:  # 防御：任何意外异常都转失败而非中断
                results.append(
                    CheckResult(index=index, name=name, ok=False, detail=f"{type(e).__name__}: {e}")
                )
    finally:
        _reset_cache()
    return results
