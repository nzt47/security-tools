"""E0-3 检查点与 workspace 变更快照的契约测试。

全部在临时 git 仓库里跑（不碰真实工作区）：过滤运行期路径、上界与截断、整文件捕获、
只读对比（与当前工作区 / 与另一个检查点）、路径穿越拒绝、非 git 目录 fail-soft、去抖判定。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent.context import checkpoint

_ENVS = (checkpoint.CHECKPOINT_ENABLED_ENV, checkpoint.MAX_FILES_ENV,
         checkpoint.MAX_FILE_BYTES_ENV, checkpoint.DEBOUNCE_ENV)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True)


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    for key in _ENVS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(checkpoint, "checkpoint_dir", lambda: tmp_path / "checkpoints")
    checkpoint.reset_state()
    yield
    checkpoint.reset_state()


@pytest.fixture()
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "ci@example.com")
    _git(root, "config", "user.name", "ci")
    (root / "a.txt").write_text("a1\n", encoding="utf-8")
    (root / "b.txt").write_text("b1\n", encoding="utf-8")
    (root / "keep.txt").write_text("keep\n", encoding="utf-8")
    (root / "data").mkdir()
    (root / "data" / "feedback.db").write_text("x", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root


# ── 开关/上界 ────────────────────────────────────────────────


def test_默认开启():
    assert checkpoint.enabled() is True


@pytest.mark.parametrize("raw", ["0", "false", "no", "off", "disable"])
def test_关闭写法(monkeypatch, raw):
    monkeypatch.setenv(checkpoint.CHECKPOINT_ENABLED_ENV, raw)
    assert checkpoint.enabled() is False


def test_上界解析与钳制(monkeypatch):
    assert checkpoint.max_files() == 50
    assert checkpoint.max_file_bytes() == 1_048_576
    assert checkpoint.debounce_seconds() == 120.0
    monkeypatch.setenv(checkpoint.MAX_FILES_ENV, "0")
    assert checkpoint.max_files() == 1
    monkeypatch.setenv(checkpoint.MAX_FILES_ENV, "9999")
    assert checkpoint.max_files() == 500
    monkeypatch.setenv(checkpoint.MAX_FILE_BYTES_ENV, "1")
    assert checkpoint.max_file_bytes() == 4_096
    monkeypatch.setenv(checkpoint.DEBOUNCE_ENV, "1")
    assert checkpoint.debounce_seconds() == 10.0
    monkeypatch.setenv(checkpoint.DEBOUNCE_ENV, "oops")
    assert checkpoint.debounce_seconds() == 120.0


# ── 快照 ────────────────────────────────────────────────────


def test_非git目录fail_soft(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "x.txt").write_text("x", encoding="utf-8")
    assert checkpoint.snapshot(root=plain) is None


def test_无改动返回None(repo):
    assert checkpoint.snapshot(root=repo) is None


def test_快照捕获改动与整文件(repo):
    (repo / "a.txt").write_text("a2\n", encoding="utf-8")
    (repo / "new.txt").write_text("new\n", encoding="utf-8")
    (repo / "b.txt").unlink()
    manifest = checkpoint.snapshot(root=repo, trigger_tool="pytest")
    assert manifest is not None
    paths = {e["path"]: e for e in manifest["changed"]}
    assert set(paths) == {"a.txt", "new.txt", "b.txt"}
    assert paths["a.txt"]["captured"] is True
    assert paths["a.txt"]["sha256_16"]
    assert checkpoint.read_file(manifest["id"], "a.txt") == "a2\n"
    assert paths["b.txt"]["captured"] is False  # 已删除：无内容可捕获
    target = checkpoint.checkpoint_dir() / manifest["id"]
    assert (target / "manifest.json").is_file()
    assert (target / "status.txt").is_file()
    assert (target / "diff.patch").read_bytes()  # 改动有 diff


def test_过滤运行期路径(repo):
    (repo / "data" / "feedback.db").write_text("changed", encoding="utf-8")
    (repo / "a.txt").write_text("a3\n", encoding="utf-8")
    manifest = checkpoint.snapshot(root=repo)
    assert manifest is not None
    paths = {e["path"] for e in manifest["changed"]}
    assert "data/feedback.db" not in paths
    assert "a.txt" in paths


def test_文件数上界与截断(repo, monkeypatch):
    monkeypatch.setenv(checkpoint.MAX_FILES_ENV, "1")
    for name in ("a.txt", "b.txt", "keep.txt"):
        (repo / name).write_text("changed\n", encoding="utf-8")
    manifest = checkpoint.snapshot(root=repo)
    assert manifest is not None
    assert manifest["truncated"] is True
    assert len(manifest["changed"]) == 1
    assert manifest["changed_total"] == 3


def test_大文件不捕获(repo, monkeypatch):
    monkeypatch.setenv(checkpoint.MAX_FILE_BYTES_ENV, "4096")
    (repo / "a.txt").write_text("x" * 5000, encoding="utf-8")
    manifest = checkpoint.snapshot(root=repo)
    entry = {e["path"]: e for e in manifest["changed"]}["a.txt"]
    assert entry["captured"] is False and entry["size"] == 5000


def test_关闭后不快照(repo, monkeypatch):
    monkeypatch.setenv(checkpoint.CHECKPOINT_ENABLED_ENV, "0")
    (repo / "a.txt").write_text("a4\n", encoding="utf-8")
    assert checkpoint.snapshot(root=repo) is None


# ── 读取/对比 ────────────────────────────────────────────────


def test_读取拒绝清单外与穿越(repo):
    (repo / "a.txt").write_text("a5\n", encoding="utf-8")
    manifest = checkpoint.snapshot(root=repo)
    assert checkpoint.read_file(manifest["id"], "../../etc/passwd") is None
    assert checkpoint.read_file(manifest["id"], "keep.txt") is None   # 未改动 ⇒ 不在清单
    assert checkpoint.read_file("../../x", "a.txt") is None


def test_清单与对比(repo):
    (repo / "a.txt").write_text("a6\n", encoding="utf-8")
    first = checkpoint.snapshot(root=repo)
    assert first is not None
    # 当前工作区：再改一次 ⇒ modified
    (repo / "a.txt").write_text("a7\n", encoding="utf-8")
    cmp_now = checkpoint.compare(first["id"])
    assert cmp_now is not None
    row = {r["path"]: r for r in cmp_now["rows"]}["a.txt"]
    assert row["state"] == "modified"
    # 与另一个检查点比
    second = checkpoint.snapshot(root=repo)
    cmp_two = checkpoint.compare(first["id"], second["id"])
    assert cmp_two["against"] == second["id"]
    assert {r["path"]: r for r in cmp_two["rows"]}["a.txt"]["state"] == "modified"
    listed = checkpoint.list_checkpoints(limit=5)
    assert {row["id"] for row in listed} >= {first["id"], second["id"]}


def test_对比可发现新增与删除(repo):
    (repo / "a.txt").write_text("a8\n", encoding="utf-8")
    first = checkpoint.snapshot(root=repo)
    (repo / "new2.txt").write_text("n\n", encoding="utf-8")
    (repo / "a.txt").unlink()
    second = checkpoint.snapshot(root=repo)
    result = checkpoint.compare(first["id"], second["id"])
    rows = {r["path"]: r["state"] for r in result["rows"]}
    assert rows["new2.txt"] == "added"
    assert rows["a.txt"] == "removed"


# ── 去抖/状态 ───────────────────────────────────────────────


def test_非改写工具不排快照():
    assert checkpoint.maybe_snapshot("read_file") is False
    assert checkpoint.maybe_snapshot("grep") is False


def test_关闭后不排快照(monkeypatch):
    monkeypatch.setenv(checkpoint.CHECKPOINT_ENABLED_ENV, "0")
    assert checkpoint.maybe_snapshot("write_file") is False


def test_status_形状():
    payload = checkpoint.status()
    assert payload["enabled"] is True
    assert payload["max_files"] == 50
    assert "write_file" in payload["mutating_tools"]
    assert "agent/data/" in payload["ignored_prefixes"]
    assert payload["dir"]
