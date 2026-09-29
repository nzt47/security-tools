# -*- coding: utf-8 -*-
"""ISO-RUNTIME 隔离的**非空转自证**（2026-09-28，P2-5 / P2-8）

【守什么】测试不得在**检出目录**里创建/追加运行期台账：
  · `agent/skills_mgmt/store.py` 的默认台账（首次读即落盘，干净检出上写出 2 字节空对象）；
  · `agent/workflow_learning/repository.py` 的默认仓库（同族）；
  · `agent/knowledge/audit_entry.py` 的审计台账（实测本机跑一遍子集就 +2200 B）。

【为什么要有这个文件】夹具是 autouse 且带 try/except 吞异常 —— 那种写法**坏掉时不会红**：
实测本文件写出来后立刻抓到第一版 `_redirect_default_when_absent` 用了一个**未导入的 `Path`**，
NameError 被 `except Exception` 吞掉 ⇒ 夹具静默空转。所以隔离必须配**能红的**自证。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tests.unit.conftest import _redirect_default_when_absent  # type: ignore

ROOT = Path(__file__).resolve().parents[2]
REPO_LEDGERS = (
    ROOT / "data" / "skills_mgmt.json",
    ROOT / "data" / "learned_workflows.json",
    ROOT / "data" / "audit" / "knowledge_audit.jsonl",
)


def _fingerprint(p: Path):
    if not p.is_file():
        return None
    return (p.stat().st_size, hashlib.sha256(p.read_bytes()).hexdigest())


class _FakeModule:
    """只为测助手函数的两个分支，不碰真实模块对象"""


def test_不存在时会重定向(tmp_path, monkeypatch):
    mod = _FakeModule()
    mod.PATH = str(tmp_path / "nope" / "ledger.json")   # 干净检出形态：不存在
    target = tmp_path / "redirected.json"
    assert _redirect_default_when_absent(mod, "PATH", target, monkeypatch) is True
    assert mod.PATH == target, "不存在时必须真的被重定向（否则夹具是空转的）"


def test_存在时不重定向(tmp_path, monkeypatch):
    real = tmp_path / "exists.json"
    real.write_text("{}", encoding="utf-8")
    mod = _FakeModule()
    mod.PATH = str(real)
    assert _redirect_default_when_absent(mod, "PATH", tmp_path / "x.json",
                                         monkeypatch) is False
    assert mod.PATH == str(real), "真实台账存在时不得改动读语义"


def test_属性不存在时不炸(tmp_path, monkeypatch):
    """结构变化（常量改名/删除）⇒ 返回 False 而不是抛异常（夹具是 autouse）"""
    assert _redirect_default_when_absent(_FakeModule(), "MISSING", tmp_path / "x",
                                         monkeypatch) is False


def test_knowledge审计落点被重定向到tmp(monkeypatch):
    """P2-8：审计台账落点必须**不在**仓库 data/audit/ 下（实测它确实在被追加）"""
    from agent.knowledge.audit_entry import audit_log_path
    p = Path(audit_log_path())
    assert not str(p).startswith(str(ROOT / "data" / "audit")), (
        f"knowledge 审计仍落在检出目录：{p}")


def test_干净检出形态下首次读不会写进检出目录(tmp_path, monkeypatch):
    """模拟"文件不存在"：生产读路径第一次 `list_all()` 会落盘 —— 落点必须是那个 tmp，
    且**仓库的台账逐字节不变**（这才是 P2-5 要的那个不变量）。"""
    from agent.skills_mgmt import store as store_mod

    before = [_fingerprint(p) for p in REPO_LEDGERS]
    clean = tmp_path / "clean" / "skills_mgmt.json"
    monkeypatch.setattr(store_mod, "_DEFAULT_STORE_PATH", clean, raising=False)

    st = store_mod.SkillStore()          # 无参 ⇒ 取默认落点（= 刚指向的干净路径）
    assert st.list_all() == [], "干净台账应当读到空"

    assert clean.is_file(), "首次读应当在**被指定的落点**创建台账（证明这条生产行为存在）"
    assert clean.read_bytes() == b"{}", "空台账的形态就是 2 字节的 {}"
    assert [_fingerprint(p) for p in REPO_LEDGERS] == before, (
        "检出目录里的运行期台账被改动了")


def test_真实台账存在时读取语义不变(monkeypatch):
    """边界如实：真实台账存在 ⇒ 不做隔离，默认落点仍是仓库里那一份（本地覆盖面不丢）"""
    from agent.skills_mgmt import store as store_mod
    real = Path(str(store_mod._DEFAULT_STORE_PATH))
    if not real.is_file():
        pytest.skip("本机没有真实台账（干净检出形态）⇒ 隔离生效那条由上一个用例覆盖")
    assert real == ROOT / "data" / "skills_mgmt.json"