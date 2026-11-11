"""`agent/sensor_config.py` —— watch_dirs 解析（env > Config overrides > config.yaml > 默认）

【为什么必须钉这些判据（2026-11-12 现场）】
`config.py` 的 Config **从不解析 config.yaml**，而 LifecycleManager 用它取
`sensor.watch_dirs` ⇒ 在 config.yaml 里配监听目录是**死键**，FileWatcher 永不启动，
感知侧学习"开了但近乎空转"。本文件把新的三层取值、以及两条安全约束钉死：
① 学习产物目录（data/learning）不得被监听（否则自激）；② 仓库根不得被监听（淹没缓冲区）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent import sensor_config as sc


def _write_yaml(root: Path, body: str) -> None:
    (root / "config.yaml").write_text(body, encoding="utf-8")


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    """干净的仓库根 + 清空 env（否则跑测机器上真设了 SENSOR_WATCH_DIRS 会污染判据）。"""
    monkeypatch.delenv(sc.ENV_WATCH_DIRS, raising=False)
    (tmp_path / "knowledge").mkdir()
    (tmp_path / "data" / "inbox").mkdir(parents=True)
    (tmp_path / "data" / "learning" / "novelty_memory").mkdir(parents=True)
    return tmp_path


def test_env_wins_over_config_yaml(repo, monkeypatch):
    _write_yaml(repo, "sensor:\n  watch_dirs:\n    - ./knowledge\n")
    monkeypatch.setenv(sc.ENV_WATCH_DIRS, "./data/inbox")
    dirs = sc.resolve_watch_dirs({}, repo_root=repo)
    assert dirs == [str((repo / "data/inbox").resolve())]


def test_config_overrides_beat_yaml(repo):
    _write_yaml(repo, "sensor:\n  watch_dirs:\n    - ./knowledge\n")
    dirs = sc.resolve_watch_dirs({"watch_dirs": ["./data/inbox"]}, repo_root=repo)
    assert dirs == [str((repo / "data/inbox").resolve())]


def test_reads_config_yaml_sensor_watch_dirs(repo):
    """★ 本任务的核心：config.yaml 的 sensor.watch_dirs **真的**被读到（以前是死键）"""
    _write_yaml(repo, "sensor:\n  watch_dirs:\n    - ./knowledge\n    - ./data/inbox\n")
    dirs = sc.resolve_watch_dirs({"watch_dirs": None}, repo_root=repo)
    assert dirs == [str((repo / "knowledge").resolve()),
                    str((repo / "data/inbox").resolve())]


def test_yaml_accepts_separated_string(repo):
    """config.yaml 里写成逗号串也要认（运维手写习惯；与 env 同一套分隔符）"""
    _write_yaml(repo, "sensor:\n  watch_dirs: './knowledge; ./data/inbox'\n")
    assert len(sc.resolve_watch_dirs({}, repo_root=repo)) == 2


def test_defaults_when_nothing_configured(repo):
    assert sc.resolve_watch_dirs(None, repo_root=repo) == [
        str((repo / p).resolve()) for p in sc.DEFAULT_WATCH_DIRS
    ]


def test_learning_output_dir_is_dropped(repo, monkeypatch):
    """★ 安全约束：data/learning 及其子目录必须被剔除（监听学习产物 = 自激）"""
    monkeypatch.setenv(
        sc.ENV_WATCH_DIRS,
        ";".join(["./knowledge", "./data/learning", "./data/learning/novelty_memory"]))
    dirs = sc.resolve_watch_dirs({}, repo_root=repo)
    assert dirs == [str((repo / "knowledge").resolve())]


def test_repo_root_itself_is_dropped(repo, monkeypatch):
    monkeypatch.setenv(sc.ENV_WATCH_DIRS, str(repo))
    assert sc.resolve_watch_dirs({}, repo_root=repo) == []


def test_dedupes_relative_and_absolute(repo, monkeypatch):
    monkeypatch.setenv(
        sc.ENV_WATCH_DIRS,
        ";".join(["./knowledge", str(repo / "knowledge"), "./knowledge/"]))
    assert sc.resolve_watch_dirs({}, repo_root=repo) == [str((repo / "knowledge").resolve())]


def test_missing_dir_is_kept_for_watcher_to_warn(repo, monkeypatch):
    """不存在的目录保留（由 FileWatcher 记 warning 跳过）——静默丢弃会让人以为"配了没生效"。"""
    monkeypatch.setenv(sc.ENV_WATCH_DIRS, "./knowledge;./not-there")
    dirs = sc.resolve_watch_dirs({}, repo_root=repo)
    assert str((repo / "not-there").resolve()) in dirs


def test_empty_env_falls_through_to_yaml(repo, monkeypatch):
    _write_yaml(repo, "sensor:\n  watch_dirs:\n    - ./knowledge\n")
    monkeypatch.setenv(sc.ENV_WATCH_DIRS, "  ")  # 空白 = 没配，不是"监听当前目录"
    assert sc.resolve_watch_dirs({}, repo_root=repo) == [str((repo / "knowledge").resolve())]


def test_broken_yaml_falls_back_to_defaults(repo, monkeypatch):
    _write_yaml(repo, "sensor: [unclosed\n")
    assert sc.resolve_watch_dirs({}, repo_root=repo) == [
        str((repo / p).resolve()) for p in sc.DEFAULT_WATCH_DIRS
    ]
