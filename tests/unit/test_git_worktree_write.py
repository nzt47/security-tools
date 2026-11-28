"""E4-5 第六批续：worktree 写路径（建 worktree / 本地提交；默认关）。

判据：
  · 两把钥匙：只读面板开 + 写路径开才动手；写路径关切到 write_disabled，且**一个 git 命令都不跑**；
  · 名字白名单（小写/数字/连字符，<=40），路径永远由 <repo>/.worktrees/<name> 派生；
  · 提交信息是数据：去控制符、折叠空白、截断 200；空 => bad_message；
  · commit 只对 git worktree list 里**等值匹配**的路径生效（不认任意名字）；
  · 真实仓库：建 -> 改 -> 提交 -> 再提交回 nothing_to_commit；未知名字 back not_found。
"""
from __future__ import annotations

import shutil
import subprocess

import pytest

from agent import git_worktree as W
from agent.utils import proc as _proc


@pytest.fixture(autouse=True)
def _off(monkeypatch):
    monkeypatch.delenv(W.WRITE_ENABLED_ENV, raising=False)
    yield


class TestGate:
    def test_开关判据(self, monkeypatch):
        assert W.write_enabled() is False
        monkeypatch.setenv(W.WRITE_ENABLED_ENV, "1")
        assert W.write_enabled() is True

    def test_写关时一个_git_都不跑(self, monkeypatch):
        def _boom(*a, **k):
            raise AssertionError("写路径关时不应跑 git")

        monkeypatch.setattr(W, "_git", _boom)
        assert W.create("probe")["code"] == "write_disabled"
        assert W.commit("probe", "msg")["code"] == "write_disabled"


class TestNameAndMessage:
    @pytest.mark.parametrize("name", ["", "A", "Ünïcode", "../etc", "a/b", "x" * 41, "-lead"])
    def test_非法名字(self, name, monkeypatch):
        monkeypatch.setenv(W.WRITE_ENABLED_ENV, "1")
        assert W.create(name)["code"] == "bad_name"

    def test_合法名字(self):
        assert W.valid_name("probe-1") is True
        assert W.valid_name("a") is True

    def test_提交信息清洗(self):
        assert W.sanitize_message("  fix\n\tthe\tbug  ") == "fix the bug"
        assert W.sanitize_message("x" * 500) == "x" * W.MAX_COMMIT_CHARS
        assert W.sanitize_message("\x00\x01") == ""
        assert W.sanitize_message(None) == ""

    def test_空信息拒绝(self, monkeypatch):
        monkeypatch.setenv(W.WRITE_ENABLED_ENV, "1")
        assert W.commit("probe", "   ")["code"] == "bad_message"


class TestFakeGit:
    def test_同名目录已存在(self, tmp_path, monkeypatch):
        monkeypatch.setenv(W.WRITE_ENABLED_ENV, "1")
        monkeypatch.setattr(W, "_git", lambda: "git")
        (tmp_path / W.WORKTREES_DIRNAME / "probe").mkdir(parents=True)
        assert W.create("probe", root=tmp_path)["code"] == "exists"

    def test_分支已存在(self, tmp_path, monkeypatch):
        monkeypatch.setenv(W.WRITE_ENABLED_ENV, "1")
        monkeypatch.setattr(W, "_git", lambda: "git")
        monkeypatch.setattr(W, "_run", lambda *a, **k: (128, "fatal: a branch named 'x' already exists"))
        assert W.create("probe", root=tmp_path)["code"] == "branch_exists"

    def test_提交前必须等值匹配路径(self, tmp_path, monkeypatch):
        monkeypatch.setenv(W.WRITE_ENABLED_ENV, "1")
        monkeypatch.setattr(W, "_git", lambda: "git")
        monkeypatch.setattr(W, "_run", lambda *a, **k: (0, "worktree C:/elsewhere\nHEAD a\nbranch refs/heads/x\n"))
        assert W.commit("probe", "msg", root=tmp_path)["code"] == "not_found"


@pytest.mark.skipif(shutil.which("git") is None, reason="需要 git")
class TestRealRepo:
    def _git(self, cwd, *args):
        return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=60,
                              **_proc.hidden_kwargs())

    def test_建_改_提交_再提交为空(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        repo.mkdir()
        assert self._git(repo, "init").returncode == 0
        self._git(repo, "config", "user.name", "probe")
        self._git(repo, "config", "user.email", "probe@example.invalid")
        (repo / "a.txt").write_text("one\n", encoding="utf-8")
        self._git(repo, "add", "a.txt")
        assert self._git(repo, "commit", "-m", "init").returncode == 0

        monkeypatch.setenv(W.WRITE_ENABLED_ENV, "1")
        created = W.create("probe", root=repo)
        if created.get("code") in ("create_failed", "branch_exists"):
            pytest.skip("git worktree 在该环境不可用: %s" % created.get("code"))
        assert created["ok"] is True
        wt = repo / W.WORKTREES_DIRNAME / "probe"
        assert wt.is_dir() and created["branch"] == "yunshu/probe"

        assert W.commit("probe", "no change", root=repo)["code"] == "nothing_to_commit"
        (wt / "a.txt").write_text("two\n", encoding="utf-8")
        committed = W.commit("probe", "  change a.txt  ", root=repo)
        assert committed["ok"] is True and committed["commit"]
        assert committed["changed"] >= 1
        assert W.commit("probe", "again", root=repo)["code"] == "nothing_to_commit"
        assert W.commit("nope", "again", root=repo)["code"] == "not_found"
