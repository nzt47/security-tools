#!/usr/bin/env python3
"""scripts/config_snapshot.py 单元测试草稿（批次 1 · 门禁类）

覆盖 generate_snapshot() 与 _get_git_sha()：
1. 快照结构：version/generated_at/generated_from/total_paths/config/metadata 键齐全
2. metadata 为每个 validation rule 的 path 建立条目
3. total_paths == OBSERVABILITY_VALIDATION_RULES 长度
4. include_runtime=True → runtime_included 键
5. git SHA 获取：成功 / 命令异常 / 空输出 → 'unknown'
注：agent.monitoring.observability_config 的依赖通过 monkeypatch 隔离，避免真实配置副作用。
"""

import pytest
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import importlib.util

import agent.monitoring.observability_config as oc


def _load():
    spec = importlib.util.spec_from_file_location(
        "config_snapshot",
        Path(__file__).resolve().parents[2] / "scripts" / "config_snapshot.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CS = _load()


class _FakeConfig:
    def get_all(self):
        return {"observability": {"enabled": True}}


def _patch_oc(monkeypatch, rules):
    """用假规则替换 observability_config 依赖"""
    monkeypatch.setattr(oc, "OBSERVABILITY_VALIDATION_RULES", rules)
    monkeypatch.setattr(oc, "get_observability_config", lambda: _FakeConfig())
    monkeypatch.setattr(oc, "reset_observability_config", lambda: None)


_RULES = [
    SimpleNamespace(path="observability.enabled", default=True,
                    description="开关", error_message="缺失"),
    SimpleNamespace(path="observability.endpoint", default="http://x",
                    description="端点", error_message="缺失"),
]


class TestGenerateSnapshot:
    def test_structure_keys(self, monkeypatch):
        _patch_oc(monkeypatch, _RULES)
        monkeypatch.setattr(CS, "_get_git_sha", lambda: "abc1234")
        snap = CS.generate_snapshot()
        for key in ("version", "generated_at", "generated_from", "total_paths",
                    "config", "metadata"):
            assert key in snap
        assert snap["version"] == "1.0"
        assert "abc1234" in snap["generated_from"]

    def test_total_paths_matches_rules(self, monkeypatch):
        _patch_oc(monkeypatch, _RULES)
        monkeypatch.setattr(CS, "_get_git_sha", lambda: "abc1234")
        snap = CS.generate_snapshot()
        assert snap["total_paths"] == 2

    def test_metadata_built_per_rule(self, monkeypatch):
        _patch_oc(monkeypatch, _RULES)
        monkeypatch.setattr(CS, "_get_git_sha", lambda: "abc1234")
        snap = CS.generate_snapshot()
        assert set(snap["metadata"].keys()) == {"observability.enabled",
                                                "observability.endpoint"}
        assert snap["metadata"]["observability.enabled"]["default"] is True

    def test_config_contains_get_all_result(self, monkeypatch):
        _patch_oc(monkeypatch, _RULES)
        monkeypatch.setattr(CS, "_get_git_sha", lambda: "abc1234")
        snap = CS.generate_snapshot()
        assert snap["config"]["observability"]["enabled"] is True

    def test_include_runtime_flag(self, monkeypatch):
        _patch_oc(monkeypatch, _RULES)
        monkeypatch.setattr(CS, "_get_git_sha", lambda: "abc1234")
        assert "runtime_included" in CS.generate_snapshot(include_runtime=True)
        assert "runtime_included" not in CS.generate_snapshot(include_runtime=False)


class TestGetGitSha:
    def test_success(self, monkeypatch):
        result = SimpleNamespace(stdout="abc1234\n", returncode=0)
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: result)
        assert CS._get_git_sha() == "abc1234"

    def test_empty_stdout(self, monkeypatch):
        result = SimpleNamespace(stdout="   \n", returncode=0)
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: result)
        assert CS._get_git_sha() == "unknown"

    def test_exception(self, monkeypatch):
        def boom(*a, **k):
            raise FileNotFoundError("git not found")
        monkeypatch.setattr(subprocess, "run", boom)
        assert CS._get_git_sha() == "unknown"


class TestMain:
    def test_writes_snapshot_to_output(self, tmp_path, monkeypatch):
        """main：生成快照并写入 --output 文件"""
        _patch_oc(monkeypatch, _RULES)
        monkeypatch.setattr(CS, "_get_git_sha", lambda: "abc1234")
        out = tmp_path / "snap.json"
        with patch.object(sys, "argv", [
            "config_snapshot.py", "--output", str(out),
        ]):
            CS.main()
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["version"] == "1.0"
        assert data["total_paths"] == 2

    def test_include_runtime_flag(self, tmp_path, monkeypatch):
        """main：--include-runtime 传递到 generate_snapshot"""
        _patch_oc(monkeypatch, _RULES)
        monkeypatch.setattr(CS, "_get_git_sha", lambda: "abc1234")
        out = tmp_path / "snap.json"
        with patch.object(sys, "argv", [
            "config_snapshot.py", "--output", str(out), "--include-runtime",
        ]):
            CS.main()
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data.get("runtime_included") is True


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
