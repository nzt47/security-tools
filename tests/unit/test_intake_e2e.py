# -*- coding: utf-8 -*-
"""入轨一条龙的**端到端**测试：真跑 git，只桩掉 `gh pr create` 这一个外部命令。

为什么要这条：单元测试都是 monkeypatch `_run`（连 git 也桩掉），正好绕过
"git 参数拼错 / 分支没回到 master / 提交里混进别的文件" 这类真问题 ——
§8 陷阱 32 的教训是「解析外部命令输出的单测，至少有一条要跑真命令」。

链路：自动补全 → git add → 干净 worktree 派生 → （可选）刷基线 → 开分支 → commit → push → 建 PR → 回 master。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import importlib

intake = importlib.import_module("scripts.intake_skill")

THIN_SKILL = """---
id: demo-skill
name: 演示技能（自动整理）
description: 这是一个中文描述。
---

# 演示技能

## 何时使用

- 端到端测试用。
"""


def _git(cwd, *args):
    # core.quotepath=false：否则 `git show --name-only` 会把中文路径转义成 "docs/rfc/\\344..."，
    # 与真实路径对不上（本次实测踩过）
    r = subprocess.run(["git", "-c", "core.quotepath=false"] + list(args), cwd=str(cwd),
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert r.returncode == 0, "git %s 失败: %s%s" % (args, r.stdout, r.stderr)
    return r.stdout


class _FakePR:
    returncode = 0
    stdout = "https://example.test/pr/1\n"
    stderr = ""


def _build_repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", str(origin))
    _git(root, "init", "-b", "master")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    _git(root, "config", "commit.gpgsign", "false")
    (root / "data" / "skills_repo").mkdir(parents=True)
    (root / "data" / "capability_manifest.json").write_text(
        '{"entries": [{"kind": "skill", "enabled": true, "location": "local"}]}', encoding="utf-8")
    (root / "docs" / "rfc").mkdir(parents=True)
    (root / "docs" / "rfc" / "云枢能力清单盘点表.md").write_text("# 盘点\n", encoding="utf-8")
    (root / "scripts").mkdir()
    # 派生脚本用桩：只改盘点表一行，模拟"派生后有变更"
    (root / "scripts" / "sync_capability_manifest.py").write_text(
        "import pathlib\n"
        "p = pathlib.Path('docs/rfc/云枢能力清单盘点表.md')\n"
        "p.write_text('# 盘点\\n\\n- 已派生\\n', encoding='utf-8')\n",
        encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "init")
    _git(root, "remote", "add", "origin", str(origin))
    _git(root, "push", "-u", "origin", "master")
    skill_dir = root / "data" / "skills_repo" / "demo-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.md").write_text(THIN_SKILL, encoding="utf-8")
    return root, origin, skill_dir


def test_一条龙真跑git_只桩gh(tmp_path, monkeypatch):
    root, origin, skill_dir = _build_repo(tmp_path)
    monkeypatch.setattr(intake, "ROOT", root)
    monkeypatch.setattr(intake, "SKILLS_REPO", root / "data" / "skills_repo")
    monkeypatch.setattr(intake, "SYNC_SCRIPT_REL", Path("scripts") / "sync_capability_manifest.py")
    monkeypatch.setattr(intake.shutil, "which", lambda name: "/usr/bin/gh")
    calls = []
    monkeypatch.setattr(intake, "_gh_pr_create",
                        lambda gh, base, title, body, r: (calls.append((gh, base, title))) or _FakePR())

    # 1) 入轨（含自动补全）
    rep = intake.intake_one("demo-skill", execute=True, fix_meta=True, fix_baselines=False)
    assert rep["ok"] is True, rep
    assert {"status", "enabled", "version", "category", "author", "tags"} <= set(rep["meta_fixed"])
    text = (skill_dir / "skill.md").read_text(encoding="utf-8")
    assert "自动整理" not in text and "enabled: true" in text and "description_zh" in text
    assert (root / "docs" / "rfc" / "云枢能力清单盘点表.md").read_text(encoding="utf-8").count("已派生") == 1

    # 2) 一条龙
    out = intake.open_pr("demo-skill", root=root)
    assert out["ok"] is True, out
    assert out["pr_url"] == "https://example.test/pr/1"
    assert calls and calls[0][1] == "master" and "demo-skill" in calls[0][2]

    # 3) 分支真的推到了 origin，工作区回到 master 且干净
    remote = _git(root, "ls-remote", "--heads", "origin", out["branch"])
    assert remote.strip(), "分支没推到 origin"
    assert _git(root, "rev-parse", "--abbrev-ref", "HEAD").strip() == "master"
    assert _git(root, "status", "--porcelain", "--untracked-files=no").strip() == ""

    # 4) 提交里只有该技能目录 + 派生物
    files = [f for f in _git(root, "show", "--name-only", "--pretty=format:", out["branch"]).split("\n") if f]
    assert "data/skills_repo/demo-skill/skill.md" in files
    assert all(f.startswith("data/skills_repo/demo-skill/") or f.startswith("data/capability")
               or f.startswith("docs/rfc/") for f in files), files
