#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/dev/new_session_worktree.py 的 .env 供给用例（S10-06）

背景（Why）:
    worktree 是独立工作目录，主工作区的 .env 不会自动出现。缺 .env 的 worktree 内
    进程没有 LLM 配置，只能走离线响应，极易把"环境没配"误判成"功能不达标"
    （S9-01 实测登记遗留 #6：.worktrees/<id>/.env 为 0 字节，主工作区为 144741 字节）。

契约（被测对象的三条硬约束）:
    A. 符号链接优先，无权限/不支持时回退复制；输出必须说明是链接还是复制，
       且复制模式必须提示"不会自动跟随主工作区更新"。
    B. 已存在的 worktree .env 绝不覆盖。
    C. .env 不得进入 git：写完实测 `git -C <worktree> status --short`，
       一旦 .env 会出现在 status 里（未被 .gitignore 忽略）立即回滚删除。

环境说明（为什么要 monkeypatch symlink）:
    符号链接能力取决于 OS 权限（Windows 需开发者模式或管理员），不能作为用例前提；
    故"链接成功"与"链接失败"两个分支都用 monkeypatch 确定性驱动，另有一条不 mock 的
    环境真实性用例（只断言两条路径都能产出可用 .env，不断言走了哪条）。
"""

from __future__ import annotations

import argparse
import importlib.util
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "dev" / "new_session_worktree.py"


def _load():
    spec = importlib.util.spec_from_file_location("new_session_worktree", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


NSW = _load()

ENV_TEXT = "LLM_API_KEY=sk-test-0123456789\nLLM_BASE_URL=https://example.invalid/v1\n" * 8


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _init_repo(path: Path, gitignore: str = "") -> Path:
    """建一个一次性真实 git 仓库（master 有初始提交），供 worktree 用例使用"""
    path.mkdir(parents=True, exist_ok=True)
    assert _git(path, "init", "-b", "master").returncode == 0
    (path / "README.md").write_text("init\n", encoding="utf-8")
    _git(path, "add", "README.md")
    if gitignore:
        (path / ".gitignore").write_text(gitignore, encoding="utf-8")
        _git(path, "add", ".gitignore")
    r = _git(
        path,
        "-c",
        "user.email=test@example.invalid",
        "-c",
        "user.name=test",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-m",
        "init",
    )
    assert r.returncode == 0, r.stderr
    return path


def _source_env(dir_path: Path) -> Path:
    src = dir_path / ".env"
    src.write_text(ENV_TEXT, encoding="utf-8")
    return src


def _status_env_lines(cwd: Path) -> list[str]:
    """`git status --short` 中指向 cwd 自身 `.env` 的行（路径精确等于 .env）。

    用精确路径而非子串匹配：源 .env 位于仓库根时，从 worktree 视角会以 `../.env`
    出现，那属于主工作区的文件，不是"worktree 的 .env 进了 git"。
    """
    r = _git(cwd, "status", "--short", "--untracked-files=all")
    assert r.returncode == 0, r.stderr
    hits = []
    for line in r.stdout.splitlines():
        path = line[3:].strip().strip('"')
        if path == ".env":
            hits.append(line)
    return hits


def _raise_symlink_denied(self, target, target_is_directory=False):
    """模拟 Windows 无 SeCreateSymbolicLinkPrivilege：WinError 1314"""
    raise OSError(1314, "客户端没有所需的特权。", str(target))


class TestProvideEnvMode:
    """A. 链接优先 / 复制回退"""

    def test_prefers_symlink_and_does_not_copy(self, tmp_path, monkeypatch):
        """链接可用时必须走链接路径，禁止顺手复制（复制会与主工作区脱钩）"""
        src = _source_env(tmp_path)
        wt = tmp_path / "wt"
        wt.mkdir()
        calls: list[tuple[Path, Path]] = []

        def fake_symlink(self, target, target_is_directory=False):
            calls.append((self, Path(target)))
            self.write_text(ENV_TEXT, encoding="utf-8")  # 模拟链接可见的同一内容

        def forbidden_copy(*a, **k):  # pragma: no cover - 触发即失败
            raise AssertionError("链接成功时不应调用 shutil.copy2")

        monkeypatch.setattr(Path, "symlink_to", fake_symlink)
        monkeypatch.setattr(NSW.shutil, "copy2", forbidden_copy)

        res = NSW._provide_env(wt, src)

        assert res["mode"] == "linked"
        assert calls == [(wt / ".env", src)]
        # 字节数按磁盘真实大小比对（Windows 下 write_text 会把 \n 落成 \r\n）
        assert res["bytes"] == src.stat().st_size > 0
        assert (wt / ".env").read_text(encoding="utf-8") == ENV_TEXT

    def test_falls_back_to_copy_when_symlink_denied(self, tmp_path, monkeypatch):
        """无符号链接权限时回退复制，内容与主工作区一致且不是链接"""
        src = _source_env(tmp_path)
        wt = tmp_path / "wt"
        wt.mkdir()
        monkeypatch.setattr(Path, "symlink_to", _raise_symlink_denied)

        res = NSW._provide_env(wt, src)

        target = wt / ".env"
        assert res["mode"] == "copied"
        assert target.is_file() and not target.is_symlink()
        assert target.read_text(encoding="utf-8") == ENV_TEXT
        assert res["bytes"] == src.stat().st_size > 0

    def test_real_environment_yields_usable_env(self, tmp_path):
        """不 mock 的环境真实性：两条路径都必须产出可用 .env（不断言走了哪条）"""
        src = _source_env(tmp_path)
        wt = tmp_path / "wt"
        wt.mkdir()

        res = NSW._provide_env(wt, src)

        assert res["mode"] in ("linked", "copied")
        assert (wt / ".env").read_text(encoding="utf-8") == ENV_TEXT
        assert res["bytes"] > 0

    def test_missing_source_does_not_invent_file(self, tmp_path):
        """主工作区无 .env ⇒ 明确报 missing-source，不凭空造空文件"""
        wt = tmp_path / "wt"
        wt.mkdir()

        res = NSW._provide_env(wt, tmp_path / "absent" / ".env")

        assert res["mode"] == "missing-source"
        assert not (wt / ".env").exists()


class TestNeverOverwrite:
    """B. 不覆盖已存在的 .env"""

    def test_existing_env_untouched(self, tmp_path):
        src = _source_env(tmp_path)
        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / ".env").write_text("PREEXISTING=1\n", encoding="utf-8")

        res = NSW._provide_env(wt, src)

        assert res["mode"] == "skipped-exists"
        assert (wt / ".env").read_text(encoding="utf-8") == "PREEXISTING=1\n"

    def test_existing_empty_env_untouched(self, tmp_path):
        """0 字节残留（S9-01 遗留 #6 的现场）也不能被静默改写，只报告不覆盖"""
        src = _source_env(tmp_path)
        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / ".env").write_bytes(b"")

        res = NSW._provide_env(wt, src)

        assert res["mode"] == "skipped-exists"
        assert res["bytes"] == 0
        assert (wt / ".env").stat().st_size == 0


class TestNeverEntersGit:
    """C. .env 不得进入 git：status 可见即回滚"""

    def test_ignored_env_is_kept(self, tmp_path):
        """被 .gitignore 忽略（真实仓库形态）⇒ 保留，且 status 不出现 .env"""
        repo = _init_repo(tmp_path / "repo", gitignore=".env\n")
        src = _source_env(repo)
        wt = repo / "wt"
        wt.mkdir()

        res = NSW._provide_env(wt, src)

        assert res["mode"] in ("linked", "copied")
        assert _status_env_lines(wt) == [], "worktree 的 .env 出现在 git status 中"
        # 被检查对象确实在场：文件存在且非空，排除"文件没了所以没报"的假绿灯
        assert (wt / ".env").stat().st_size > 0

    def test_unignored_env_is_rolled_back(self, tmp_path):
        """未被忽略（status 会亮出 .env）⇒ 立即删除，宁缺也不进 git"""
        repo = _init_repo(tmp_path / "repo")
        src = _source_env(repo)
        wt = repo / "wt"
        wt.mkdir()
        # 先自证前置条件成立：未提供前该路径确实未被忽略（防止用例空转）
        assert _git(wt, "check-ignore", "-q", ".env").returncode == 1

        res = NSW._provide_env(wt, src)

        assert res["mode"] == "blocked-untracked"
        assert "status_entry" in res and ".env" in res["status_entry"]
        assert not (wt / ".env").exists()
        assert _status_env_lines(wt) == []


class TestReportOutput:
    """输出必须说明链接/复制，并点出复制不跟随更新"""

    def test_copied_mode_warns_no_auto_follow(self, tmp_path, monkeypatch, capsys):
        src = _source_env(tmp_path)
        wt = tmp_path / "wt"
        wt.mkdir()
        monkeypatch.setattr(Path, "symlink_to", _raise_symlink_denied)

        NSW._report_env(NSW._provide_env(wt, src))

        out = capsys.readouterr().out
        assert "[env] .env 已提供（复制）" in out
        assert "不会自动跟随主工作区更新" in out
        assert "git status --short 未出现 .env" in out

    def test_linked_mode_states_link(self, tmp_path, monkeypatch, capsys):
        src = _source_env(tmp_path)
        wt = tmp_path / "wt"
        wt.mkdir()

        def fake_symlink(self, target, target_is_directory=False):
            self.write_text(ENV_TEXT, encoding="utf-8")

        monkeypatch.setattr(Path, "symlink_to", fake_symlink)

        NSW._report_env(NSW._provide_env(wt, src))

        out = capsys.readouterr().out
        assert "[env] .env 已提供（符号链接）" in out
        assert "自动跟随主工作区更新" in out

    def test_missing_source_names_offline_risk(self, tmp_path, capsys):
        wt = tmp_path / "wt"
        wt.mkdir()

        NSW._report_env(NSW._provide_env(wt, tmp_path / "absent" / ".env"))

        out = capsys.readouterr().out
        assert "没有 LLM 配置" in out
        assert "离线响应" in out


class TestCreateEndToEnd:
    """端到端：真实 git 仓库里 create ⇒ .env 非空 + 不被 git 跟踪 + 提示开箱即用"""

    def test_create_provisions_env_end_to_end(self, tmp_path, monkeypatch, capsys):
        repo = _init_repo(tmp_path / "repo", gitignore=".env\n")
        main_env = _source_env(repo)
        monkeypatch.setattr(NSW, "REPO_ROOT", repo)
        monkeypatch.setattr(NSW, "WORKTREES_DIR", repo / ".worktrees")

        NSW.cmd_create(argparse.Namespace(id="s1", base="master"))  # 不应 SystemExit

        wt = repo / ".worktrees" / "s1"
        assert wt.is_dir()
        env = wt / ".env"
        assert env.exists(), "worktree 内 .env 缺失"
        assert env.stat().st_size > 0, "worktree 内 .env 为 0 字节"
        assert env.read_text(encoding="utf-8") == main_env.read_text(encoding="utf-8")

        status = _git(wt, "status", "--short")
        assert status.returncode == 0
        assert _status_env_lines(wt) == [], f"git status 出现 .env: {status.stdout!r}"

        out = capsys.readouterr().out
        assert ("（符号链接）" in out) or ("（复制）" in out), "输出未说明链接/复制"
        assert "开箱即用" in out and "不必手工复制" in out

        # 收尾：注销 worktree 并删除分支（不留残留）
        assert _git(repo, "worktree", "remove", str(wt), "--force").returncode == 0
        assert _git(repo, "branch", "-D", "s1/main").returncode == 0
        assert not wt.exists()

    def test_create_exits_nonzero_when_source_env_absent(self, tmp_path, monkeypatch, capsys):
        """主工作区无 .env：worktree 已建但必须非零退出，避免被当成绿灯放行"""
        repo = _init_repo(tmp_path / "repo", gitignore=".env\n")
        monkeypatch.setattr(NSW, "REPO_ROOT", repo)
        monkeypatch.setattr(NSW, "WORKTREES_DIR", repo / ".worktrees")

        with pytest.raises(SystemExit) as exc:
            NSW.cmd_create(argparse.Namespace(id="s2", base="master"))

        assert exc.value.code == 3
        out = capsys.readouterr().out
        assert "没有 LLM 配置" in out
        assert "开箱即用" not in out, "未提供 .env 时不得声称开箱即用"
        wt = repo / ".worktrees" / "s2"
        assert wt.is_dir() and not (wt / ".env").exists()

        assert _git(repo, "worktree", "remove", str(wt), "--force").returncode == 0
        assert _git(repo, "branch", "-D", "s2/main").returncode == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
