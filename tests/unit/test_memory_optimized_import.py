"""memory_optimized._create_client 导入逻辑回归测试（全 mock，无真实 chromadb）

Why（不易·死锁根因守护）：_create_client 是「子进程探测 → 主进程导入 → Mock 兜底」
三段式，_probe_import 带模块级缓存。本文件将 scripts/demo_memory_optimized_import.py
的 12 条 mock 校验路径转为 pytest 用例，纳入常规回归（tests/unit 自动收集），
防止子进程探测改造被回滚或回归（曾因回滚丢失一次）。

覆盖：
- _create_client 分支 A-E：探测不可用 / import chromadb 失败 / chromadb.config 失败
  / 客户端创建失败 / 全部成功（含 path/settings 关键参数校验）
- _probe_import 边界 ①-④：缓存复用 / rc=0 / rc!=0 / 超时+OSError
- 端到端：30s 子进程超时降级、探测成功全链路保留真实客户端
- 决策日志链（probe_start → probe_ok → ready|chromadb|client_failed|timeout）
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile
import types
from unittest import mock

import pytest

from agent import memory_optimized as mo


# ---------- mock helpers（与 demo 脚本同构，测试自包含不依赖 scripts/） ----------


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
    inst.persist_directory = tempfile.mkdtemp(prefix="mo_test_")
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


@pytest.fixture(autouse=True)
def _reset_probe_cache():
    """每个用例前后重置模块级探测缓存，避免跨用例污染。"""
    mo._CHROMADB_IMPORT_OK = None
    yield
    mo._CHROMADB_IMPORT_OK = None


def _run_real_probe(subprocess_run_impl):
    """真实 _probe_import + 注入 subprocess.run 行为（缓存由 autouse fixture 重置）。"""
    with mock.patch.object(mo.subprocess, "run", side_effect=subprocess_run_impl):
        return mo._probe_import("import chromadb; from chromadb.config import Settings")


# ---------- _create_client 分支 A-E ----------


class TestCreateClientBranches:
    def test_probe_unavailable_degrades(self):
        """A：探测不可用(False) → 直接 MockChromaClient，不进入主进程导入。"""
        inst = _make_instance()
        with mock.patch.object(mo, "_probe_import", return_value=False) as probe:
            inst._create_client()
        probe.assert_called_once_with("import chromadb; from chromadb.config import Settings")
        assert isinstance(inst._client, mo.MockChromaClient)

    def test_import_chromadb_fails_degrades(self):
        """B：探测成功但 `import chromadb` 抛 ImportError → MockChromaClient。"""
        real_import = __import__  # patch 前捕获，避免 fake 内部引用替换后自递归

        def _fake_import(name, *args, **kwargs):
            if name == "chromadb":
                raise ImportError("模拟 chromadb import 不可用")
            return real_import(name, *args, **kwargs)

        inst = _make_instance()
        with mock.patch.object(mo, "_probe_import", return_value=True), mock.patch(
            "builtins.__import__", side_effect=_fake_import
        ):
            inst._create_client()
        assert isinstance(inst._client, mo.MockChromaClient)

    def test_settings_import_fails_degrades(self):
        """C：`import chromadb` 成功但 `chromadb.config` 导入失败 → MockChromaClient。"""
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
        assert isinstance(inst._client, mo.MockChromaClient)

    def test_client_creation_fails_degrades(self):
        """D：探测/import 成功，但 PersistentClient 创建抛异常 → MockChromaClient。"""

        def _boom(**kwargs):
            raise RuntimeError("模拟 PersistentClient 创建失败")

        inst = _make_instance()
        fake_chromadb, fake_config = _make_fake_chromadb(persistent_client=_boom)
        with mock.patch.object(mo, "_probe_import", return_value=True), mock.patch.dict(
            sys.modules, {"chromadb": fake_chromadb, "chromadb.config": fake_config}
        ):
            inst._create_client()
        assert isinstance(inst._client, mo.MockChromaClient)

    def test_full_success_keeps_real_client_with_params(self):
        """E：全部成功 → 保留真实客户端，且 path/settings 关键参数正确。"""
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
        assert inst._client is fake_client
        assert captured.get("path") == inst.persist_directory
        settings = captured.get("settings")
        assert settings is not None
        assert settings.anonymized_telemetry is False
        assert settings.allow_reset is True


# ---------- _probe_import 边界 ①-④ ----------


class TestProbeImportBranches:
    def test_probe_rc0_returns_true_and_caches(self):
        """②：子进程 returncode==0 → True 并写入缓存。"""
        result = _run_real_probe(lambda *a, **k: _FakeProc(0))
        assert result is True
        assert mo._CHROMADB_IMPORT_OK is True

    def test_probe_rc_nonzero_returns_false_and_caches(self):
        """③：子进程 returncode!=0 → False 并写入缓存。"""
        result = _run_real_probe(lambda *a, **k: _FakeProc(1))
        assert result is False
        assert mo._CHROMADB_IMPORT_OK is False

    def test_probe_timeout_expired_returns_false_and_caches(self):
        """④：subprocess.run 抛 TimeoutExpired → False 并写入缓存。"""

        def _raise_timeout(*a, **k):
            raise subprocess.TimeoutExpired(["python", "-c", "x"], timeout=30.0)

        result = _run_real_probe(_raise_timeout)
        assert result is False
        assert mo._CHROMADB_IMPORT_OK is False

    def test_probe_oserror_returns_false_and_caches(self):
        """④变体：subprocess.run 抛 OSError（子进程启动失败）→ False 并写入缓存。"""
        result = _run_real_probe(lambda *a, **k: (_ for _ in ()).throw(OSError("模拟子进程启动失败")))
        assert result is False
        assert mo._CHROMADB_IMPORT_OK is False

    def test_cache_reuse_avoids_second_subprocess(self):
        """①：缓存命中后直接复用，不再启动子进程（subprocess.run 仅调用一次）。"""
        calls = []

        def _impl(*a, **k):
            calls.append(1)
            return _FakeProc(0)

        with mock.patch.object(mo.subprocess, "run", side_effect=_impl):
            assert mo._probe_import("x") is True
            assert mo._probe_import("x") is True
        assert len(calls) == 1


# ---------- 端到端 ----------


class TestEndToEnd:
    def test_probe_timeout_end_to_end_degrades(self):
        """端到端：模拟 30s 子进程超时 → 真实 _probe_import False → 全链路降级 Mock。

        Why 不等真实 30s：_probe_import 内部 subprocess.run(timeout=30s) 超时抛
        TimeoutExpired，与「子进程 import chromadb 卡死 30 秒被 terminate」等价。
        """
        inst = _make_instance()

        def _raise_timeout(*a, **k):
            raise subprocess.TimeoutExpired(
                [sys.executable, "-c", "import chromadb"],
                timeout=mo._CHROMADB_IMPORT_TIMEOUT,
            )

        with mock.patch.object(mo.subprocess, "run", side_effect=_raise_timeout):
            # 不 patch _probe_import：走真实探测 → 内部抛超时 → False
            inst._create_client()
        assert isinstance(inst._client, mo.MockChromaClient)
        assert mo._CHROMADB_IMPORT_OK is False

    def test_probe_ok_end_to_end_keeps_real_client(self):
        """端到端正例：真实探测 rc=0 → 缓存 → 主进程导入 → 创建成功 → 真实客户端。"""
        inst = _make_instance()
        fake_client = object()
        fake_chromadb, fake_config = _make_fake_chromadb(persistent_client=lambda **kw: fake_client)
        with mock.patch.object(
            mo.subprocess, "run", side_effect=lambda *a, **k: _FakeProc(0)
        ), mock.patch.dict(sys.modules, {"chromadb": fake_chromadb, "chromadb.config": fake_config}):
            inst._create_client()
        assert inst._client is fake_client
        assert mo._CHROMADB_IMPORT_OK is True


# ---------- 决策日志链（排查保障） ----------


class TestDecisionLogs:
    def test_success_path_emits_full_decision_chain(self, caplog):
        """成功路径必须打出 probe_start → probe_ok → ready 决策链。"""
        inst = _make_instance()
        fake_client = object()
        fake_chromadb, fake_config = _make_fake_chromadb(persistent_client=lambda **kw: fake_client)
        with caplog.at_level(logging.INFO, logger="agent.memory_optimized"), mock.patch.object(
            mo, "_probe_import", return_value=True
        ), mock.patch.dict(sys.modules, {"chromadb": fake_chromadb, "chromadb.config": fake_config}):
            inst._create_client()
        assert "chromadb.probe_start" in caplog.text
        assert "chromadb.probe_ok" in caplog.text
        assert "chromadb.ready" in caplog.text

    def test_timeout_path_emits_warning(self, caplog):
        """降级路径（探测超时）必须打出 probe_start + timeout warning。"""
        inst = _make_instance()

        def _raise_timeout(*a, **k):
            raise subprocess.TimeoutExpired(["python", "-c", "import chromadb"], timeout=30.0)

        with caplog.at_level(logging.INFO, logger="agent.memory_optimized"), mock.patch.object(
            mo.subprocess, "run", side_effect=_raise_timeout
        ):
            inst._create_client()
        assert "chromadb.probe_start" in caplog.text
        assert "chromadb.timeout" in caplog.text
