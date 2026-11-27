"""E4-5 第三批：git worktree 只读投影（解析 / 上限 / 真实仓库 / 失败码）。"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from agent import git_worktree as W
from agent.utils import proc as _proc

PORCELAIN = """worktree C:/repo
HEAD aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
branch refs/heads/master

worktree C:/repo/wt-1
HEAD bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
detached
locked

worktree "C:/repo/with space"
HEAD cccccccccccccccccccccccccccccccccccccccc
branch refs/heads/feature/x
"""


class TestParse:
    def test_三条记录与字段(self):
        items = W.parse_porcelain(PORCELAIN)
        assert [i["name"] for i in items] == ["repo", "wt-1", "with space"]
        assert items[0]["branch"] == "master"
        assert items[1]["detached"] is True and items[1]["locked"] is True
        assert items[2]["branch"] == "feature/x"

    def test_空输出(self):
        assert W.parse_porcelain("") == []


class TestCollectWithFakeGit:
    def test_开关判据(self, monkeypatch):
        monkeypatch.delenv(W.ENABLED_ENV, raising=False)
        assert W.enabled() is False
        monkeypatch.setenv(W.ENABLED_ENV, "1")
        assert W.enabled() is True

    def test_没有_git_回_no_git(self, monkeypatch):
        monkeypatch.setattr(W, "_git", lambda: None)
        doc = W.collect()
        assert doc["ok"] is False and doc["code"] == "no_git"

    def test_不是仓库回_not_a_repo(self, monkeypatch):
        monkeypatch.setattr(W, "_git", lambda: "git")
        monkeypatch.setattr(W, "_run", lambda *a, **k: (1, ""))
        doc = W.collect()
        assert doc["ok"] is False and doc["code"] == "not_a_repo"

    def test_计数与_diff_stat(self, monkeypatch):
        def _fake(exe, args):
            if "worktree" in args:
                return 0, PORCELAIN.split("\n\n")[0] + "\n"
            if "status" in args:
                return 0, " M a.py\nM  b.py\nD  c.py\n"
            if "diff" in args:
                return 0, " a.py | 2 +-\n 1 file changed, 1 insertion(+), 1 deletion(-)"
            return 0, ""

        monkeypatch.setattr(W, "_git", lambda: "git")
        monkeypatch.setattr(W, "_run", _fake)
        doc = W.collect(root=Path("/repo"))
        assert doc["ok"] is True and len(doc["worktrees"]) == 1
        row = doc["worktrees"][0]
        assert row["dirty"] is True
        assert row["counts"]["M"] == 2  # " M" 与 "M " 各计一次
        assert row["counts"]["D"] == 1
        assert "1 file changed" in row["diff_stat"]

    def test_diff_超长截断(self, monkeypatch):
        def _fake(exe, args):
            if "worktree" in args:
                return 0, PORCELAIN.split("\n\n")[0] + "\n"
            if "status" in args:
                return 0, ""
            return 0, "x" * (W.MAX_DIFF_CHARS + 100)

        monkeypatch.setattr(W, "_git", lambda: "git")
        monkeypatch.setattr(W, "_run", _fake)
        row = W.collect(root=Path("/repo"))["worktrees"][0]
        assert len(row["diff_stat"]) == W.MAX_DIFF_CHARS
        assert row["diff_truncated"] is True


@pytest.mark.skipif(shutil.which("git") is None, reason="需要 git")
class TestRealRepo:
    def _git(self, cwd, *args):
        return subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=60, **_proc.hidden_kwargs())

    def test_真实仓库两个_worktree(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        assert self._git(repo, "init").returncode == 0
        self._git(repo, "config", "user.name", "probe")
        self._git(repo, "config", "user.email", "probe@example.invalid")
        (repo / "a.txt").write_text("one\n", encoding="utf-8")
        assert self._git(repo, "add", "a.txt").returncode == 0
        assert self._git(repo, "commit", "-m", "init").returncode == 0
        wt = tmp_path / "wt-1"
        added = self._git(repo, "worktree", "add", str(wt), "-b", "wt-branch")
        if added.returncode != 0:
            pytest.skip("git worktree 在该环境不可用")
        doc = W.collect(root=repo)
        assert doc["ok"] is True
        names = {row["name"] for row in doc["worktrees"]}
        assert names == {"repo", "wt-1"}
        main = [r for r in doc["worktrees"] if r["name"] == "repo"][0]
        assert main["dirty"] is False
        (wt / "a.txt").write_text("two\n", encoding="utf-8")
        again = {r["name"]: r for r in W.collect(root=repo)["worktrees"]}
        assert again["wt-1"]["dirty"] is True
        assert again["wt-1"]["counts"]["M"] >= 1
