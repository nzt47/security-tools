# -*- coding: utf-8 -*-
"""L8：config.yaml 读取必须**锚定仓库根**，不得依赖进程 CWD（2026-09-25 修复）

【背景】agent/orchestrator/orchestrator.py 的 _load_context_assembler_config 曾用
  `open("config.yaml", ...)`（**相对 CWD**）读配置：进程 CWD 不是仓库根时该层静默失效
  （except 吞掉 FileNotFoundError → 回退 enabled=False），而开关中心按登记表
  learning.context_assembler.enabled 读 config.yaml ⇒ **两者矛盾**。

【本文件钉两件事】
  ① **行为**：CWD 换到别处时，该层仍读到**仓库根那份** config.yaml（同值）；
  ② **结构**：生产代码里不得再出现**相对路径**的 open("config.yaml"（防同类缺陷复发）。
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]

#: 生产根（与 scripts/scan_settings.py::DEFAULT_ROOTS 同口径的目录部分）
_PRODUCTION_ROOTS = (
    "agent", "planning", "memory", "sensor", "core", "utils",
    "cognitive", "cloudshu", "lifetrace", "persona", "plugins", "mcp_services",
)

#: 允许出现相对 open("config.yaml") 的地方（无 —— 只留空元组以便将来显式登记例外）
_ALLOWED_RELATIVE_SITES: tuple = ()


def _config_expectation() -> tuple:
    """从**仓库根**的 config.yaml 读出该层应当得到的 (enabled, token_budget)"""
    raw = yaml.safe_load((REPO_ROOT / "config.yaml").read_text(encoding="utf-8")) or {}
    lc = (raw.get("learning") or {}).get("context_assembler") or {}
    return bool(lc.get("enabled", False)), int(lc.get("token_budget", 3000))


class TestConfigYamlIsAnchoredToRepoRoot:
    """★ L8：CWD 变化不影响 config.yaml 的读取"""

    def test_context_assembler_config_reads_repo_config_from_other_cwd(
            self, tmp_path, monkeypatch):
        """CWD 换到没有 config.yaml 的临时目录后，仍读到**仓库根那份**配置

        （修前：该层直接失效 → enabled 回退 False，与开关中心的显示矛盾。）
        """
        from agent.orchestrator.orchestrator import Orchestrator

        expect_enabled, expect_budget = _config_expectation()
        monkeypatch.delenv("LEARNING_CONTEXT_ASSEMBLER_ENABLED", raising=False)
        monkeypatch.chdir(tmp_path)
        assert not (tmp_path / "config.yaml").exists(), "夹具前提：临时 CWD 里没有 config.yaml"

        cfg = Orchestrator._load_context_assembler_config(object())
        assert cfg["enabled"] is expect_enabled, (
            "CWD 变化后该层读不到仓库根 config.yaml ⇒ L8 回归", cfg)
        assert cfg["token_budget"] == expect_budget

    def test_env_override_still_wins(self, tmp_path, monkeypatch):
        """env 覆盖优先级不变（只改路径解析，不改优先级）"""
        from agent.orchestrator.orchestrator import Orchestrator

        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("LEARNING_CONTEXT_ASSEMBLER_ENABLED", "1")
        cfg = Orchestrator._load_context_assembler_config(object())
        assert cfg["enabled"] is True


class TestNoRelativeConfigYamlOpens:
    """★ 结构守卫：生产代码里不得再出现相对路径的 config.yaml 打开"""

    def test_no_relative_open_of_config_yaml(self):
        """扫生产根，抓 `open("config.yaml"` / `open('config.yaml'` 这类**相对 CWD** 的读取

        判据故意只认「字面量文件名直接给 open」这一种形态：
        传参进来的路径（如 `open(config_path)`）无法机械判定，不在本守卫范围（如实标注）。
        """
        offenders = []
        for root_name in _PRODUCTION_ROOTS:
            root = REPO_ROOT / root_name
            if not root.is_dir():
                continue
            for path in sorted(root.rglob("*.py")):
                rel = path.relative_to(REPO_ROOT).as_posix()
                if rel in _ALLOWED_RELATIVE_SITES:
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
                for idx, line in enumerate(text.splitlines(), start=1):
                    stripped = line.strip()
                    if stripped.startswith("#"):
                        continue
                    if ("open(\"config.yaml\"" in line) or ("open('config.yaml'" in line):
                        offenders.append(f"{rel}:{idx}")
        assert offenders == [], (
            "生产代码里出现相对 CWD 的 config.yaml 读取（L8 同类缺陷）："
            f"{offenders}；请改为仓库根锚定路径（见 tests/unit/test_config_yaml_anchor.py）")