"""TASK-S7-02 「无 push / 无 merge」自证 单元测试（硬验收项）

【为什么这个文件必须存在】
    任务书 §一 边界 ① 是**宪法级**的：绝不自动 push 远端、绝不自动合并。边界不能靠
    注释与承诺，必须有**代码级断言**。本文件用三种彼此独立的手段自证：

      ① **源码扫描**：``agent/repair/`` 全包 + ``scripts/self_repair.py`` 中不得出现
         ``git push`` / ``git merge`` / ``git rebase`` / 远端 PR 创建等调用形态；
      ② **运行时探针**：monkeypatch ``gitio.subprocess`` 与 ``subprocess.run``，
         跑本包全部真实 git 路径，断言**每条**命令都是只读白名单内的子命令；
      ③ **数据结构自证**：``RepairProposal.pushed / merged`` 恒为 False，
         ``BranchResult`` 同样恒 False（要变成 True 就必须先改数据模型）。

    三种手段覆盖不同失效模式：① 防"有人后来加了一行 push"；② 防"通过未扫描到的
    包装间接调用"；③ 防"把 push 的成果记成已 push 来误导人"。

【为什么允许 ``git branch``】
    产出物需要一个本地分支（任务书 §三 步骤 5）。``branch`` 只创建本地引用，
    不切工作区、不接触远端。它被**单独实现**在 ``gitio.create_local_branch()``，
    且本文件断言：除它之外不存在任何写 git 的调用点。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

import pytest

from agent.repair import gitio

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPAIR_PKG = os.path.join(REPO_ROOT, "agent", "repair")
ENTRY_SCRIPT = os.path.join(REPO_ROOT, "scripts", "self_repair.py")

#: 禁止出现在修复包源码里的调用形态（正则；**不含**仅作字符串常量的说明文本）
FORBIDDEN_PATTERNS = (
    (r"""["']push["']""", "git push 子命令字面量"),
    (r"""["']merge["']""", "git merge 子命令字面量"),
    (r"""["']rebase["']""", "git rebase 子命令字面量"),
    (r"""["']reset["']""", "git reset 子命令字面量"),
    (r"""["']checkout["']""", "git checkout 子命令字面量"),
    (r"""["']commit["']""", "git commit 子命令字面量"),
    (r"""["']remote["']""", "git remote 子命令字面量"),
    (r"gh\s+pr\s+create", "远端 PR 创建（gh cli）"),
    (r"create_pull_request", "远端 PR 创建（API）"),
    (r"requests\.(post|put)\s*\(", "对外 HTTP 写请求"),
    (r"urllib\.request\.urlopen", "对外网络请求"),
)


def _source_files():
    """修复包全部 .py 文件 + 入口脚本"""
    files = []
    for name in sorted(os.listdir(REPAIR_PKG)):
        if name.endswith(".py"):
            files.append(os.path.join(REPAIR_PKG, name))
    assert os.path.isfile(ENTRY_SCRIPT), f"入口脚本缺失：{ENTRY_SCRIPT}"
    files.append(ENTRY_SCRIPT)
    return files


def _code_lines(path: str):
    """产出 ``(行号, 代码原文)``，**剔除注释行、docstring 与纯文本块**

    为什么必须剔除：本包的文档大量说明「不做 push」「禁止 merge」，而 gitio 里还有
    一份**声明式**的禁用子命令清单（那是护栏本身，不是调用）。若不剔除，扫描会把
    「写着不许 push」判成「在 push」——这正是"看门狗咬了看门人"的经典误报。
    """
    import ast
    import io
    import tokenize

    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    lines = text.splitlines()
    skip = set()
    # ① docstring 占用的行
    try:
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
                body = getattr(node, "body", [])
                if body and isinstance(body[0], ast.Expr) and \
                        isinstance(body[0].value, ast.Constant) and \
                        isinstance(body[0].value.value, str):
                    first = body[0]
                    for ln in range(first.lineno, (first.end_lineno or first.lineno) + 1):
                        skip.add(ln)
    except SyntaxError:  # pragma: no cover 不应发生
        pass
    # ② 注释行
    try:
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type == tokenize.COMMENT:
                skip.add(token.start[0])
    except tokenize.TokenError:  # pragma: no cover
        pass
    for idx, line in enumerate(lines, start=1):
        if idx in skip:
            continue
        if line.strip().startswith("#"):
            continue
        yield idx, line


def _declarative_block(path: str) -> set:
    """取声明式常量的文本区间（``FORBIDDEN_SUBCOMMANDS``），扫描时排除

    该常量是**护栏白名单的对偶**：它列出"恒被拒绝的子命令"，本身不是调用。
    用 AST 精确定位它的赋值语句（遍历整棵树，不限顶层），避免脆弱的行号硬编码。
    """
    import ast

    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    spans: set = set()
    try:
        tree = ast.parse(text)
    except SyntaxError:  # pragma: no cover
        return spans
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and t.id == "FORBIDDEN_SUBCOMMANDS"
                   for t in targets):
                for ln in range(node.lineno, (node.end_lineno or node.lineno) + 1):
                    spans.add(ln)
    return spans


# ════════════════════════════════════════════════════════════
#  ① 源码扫描
# ════════════════════════════════════════════════════════════


class TestSourceScan:
    @pytest.mark.parametrize("path", _source_files())
    def test_no_forbidden_call_forms(self, path):
        """**代码**（非注释/非 docstring）中不得出现被禁调用形态"""
        exempt = _declarative_block(path)
        offenders = []
        for line_no, line in _code_lines(path):
            if line_no in exempt:
                continue
            for pattern, label in FORBIDDEN_PATTERNS:
                if re.search(pattern, line):
                    offenders.append(
                        f"{os.path.basename(path)}:{line_no} {label}: {line.strip()}")
        assert not offenders, "修复包内出现禁止的调用形态：\n" + "\n".join(offenders)

    def test_scanner_actually_finds_synthetic_violations(self, tmp_path):
        """**元测试**：扫描器对合成违规必须报警（否则它只是"永远通过"的摆设）"""
        sample = tmp_path / "sample.py"
        sample.write_text(
            'import subprocess\n\n\n'
            'def f():\n'
            '    subprocess.run(["git", "push", "origin", "master"])\n',
            encoding="utf-8")
        hits = []
        for line_no, line in _code_lines(str(sample)):
            for pattern, label in FORBIDDEN_PATTERNS:
                if re.search(pattern, line):
                    hits.append(label)
        assert any("push" in h for h in hits), "扫描器未能识别合成违规"

    def test_scanner_ignores_comments_and_docstrings(self, tmp_path):
        """元测试：说明性文本（注释/docstring）不应误报"""
        sample = tmp_path / "doc.py"
        sample.write_text(
            '"""模块说明：本模块不做 push。"""\n\n'
            '# 也绝不调用 "git merge"\n'
            'def f():\n'
            '    """函数说明：只读，不 push。"""\n'
            '    return 1\n',
            encoding="utf-8")
        hits = []
        for line_no, line in _code_lines(str(sample)):
            for pattern, label in FORBIDDEN_PATTERNS:
                if re.search(pattern, line):
                    hits.append((line_no, label))
        assert hits == [], f"说明性文本被误报：{hits}"

    def test_declarative_denylist_is_exempt_only_when_registered(self, tmp_path):
        """元测试：声明式禁用清单**默认仍被扫描到**；仅当显式登记时才豁免

        这个口径是刻意的：``FORBIDDEN_SUBCOMMANDS = ("push", ...)`` 这类常量若
        在某处出现却**不是**已登记的那一份，多半是新加的路径，应当被看见。
        豁免名单由 ``_declarative_block()`` 用 AST 精确定位，不靠行号硬编码。
        """
        sample = tmp_path / "deny.py"
        sample.write_text(
            'FORBIDDEN_SUBCOMMANDS = ("push", "merge")\n'
            'OTHER = ("push", "merge")\n',
            encoding="utf-8")
        exempt = _declarative_block(str(sample))
        assert 1 in exempt, "已登记的声明式清单应被豁免"
        assert 2 not in exempt, "未登记的同类常量不应被豁免"

    def test_git_io_exposes_exactly_one_write_api(self):
        """``gitio`` 只暴露**一个**写 git 的入口（``create_local_branch``）"""
        with open(os.path.join(REPAIR_PKG, "gitio.py"), "r", encoding="utf-8") as fh:
            text = fh.read()
        # 只读层唯一执行点：run_git_readonly（白名单）
        assert "assert_readonly_subcommand" in text
        # 写操作只有一处 subprocess 调用（create_local_branch 内）
        run_calls = len(re.findall(r"subprocess\.run\(", text))
        assert run_calls == 2, (
            f"gitio 中 subprocess.run 出现 {run_calls} 次——"
            f"应恰好 2 次（只读通道 + 唯一写操作）；新增写路径必须显式登记")
        branch_fn = text.split("def create_local_branch", 1)[1]
        assert "git\", \"branch\"" in branch_fn or '"branch"' in branch_fn

    def test_forbidden_subcommands_listed_for_audit(self):
        """被拒子命令清单存在且包含全部宪法级禁用项（可审计）"""
        for cmd in ("push", "merge", "rebase", "reset", "commit", "remote"):
            assert cmd in gitio.FORBIDDEN_SUBCOMMANDS

    def test_readonly_allowlist_has_no_write_subcommands(self):
        for cmd in ("push", "merge", "rebase", "commit", "add", "apply", "checkout"):
            assert cmd not in gitio.READONLY_SUBCOMMANDS

    def test_entry_script_has_no_network_or_remote_calls(self):
        with open(ENTRY_SCRIPT, "r", encoding="utf-8") as fh:
            text = fh.read()
        assert "push" in text.lower(), "入口脚本应显式声明「未 push」边界"
        assert not re.search(r"subprocess\.run\(\s*\[\s*[\"']git[\"']", text), \
            "入口脚本不得直接执行 git（一切 git 走 gitio）"


# ════════════════════════════════════════════════════════════
#  ② 运行时探针：真实路径全部命令都是只读
# ════════════════════════════════════════════════════════════


class RecordingRun:
    """``subprocess.run`` 探针：记录命令并转交真实实现"""

    def __init__(self):
        self.calls = []
        self._real = subprocess.run

    def __call__(self, args, *a, **kw):
        self.calls.append([str(x) for x in args])
        return self._real(args, *a, **kw)

    @property
    def git_subcommands(self):
        """从记录到的命令里提取 git 子命令（跳过 ``-c k=v`` 一类全局选项）"""
        out = []
        for call in self.calls:
            if not call or os.path.basename(call[0]) not in ("git", "git.exe"):
                continue
            rest = call[1:]
            idx = 0
            while idx < len(rest):
                token = rest[idx]
                if token == "-c":
                    idx += 2          # `-c key=value`：值与键都跳过
                    continue
                if token.startswith("-"):
                    idx += 1
                    continue
                break
            out.append(rest[idx] if idx < len(rest) else "")
        return out


@pytest.fixture()
def probe(monkeypatch):
    recorder = RecordingRun()
    monkeypatch.setattr(gitio.subprocess, "run", recorder)
    return recorder


class TestRuntimeProbe:
    def test_readonly_queries_only_use_allowlist(self, probe):
        gitio.repo_head(REPO_ROOT)
        gitio.head_changed_files(REPO_ROOT)
        gitio.recent_commits(REPO_ROOT, limit=2)
        gitio.is_git_repo(REPO_ROOT)
        gitio.branch_exists(REPO_ROOT, "definitely-not-a-branch")
        assert probe.calls, "应至少执行了一次 git 命令"
        for sub in probe.git_subcommands:
            assert sub in gitio.READONLY_SUBCOMMANDS, f"出现非只读子命令：{sub}"

    def test_forbidden_subcommand_is_rejected_before_subprocess(self, probe):
        for cmd in ("push", "merge", "rebase", "reset", "commit", "fetch", "remote"):
            with pytest.raises(gitio.ForbiddenGitOperation):
                gitio.run_git_readonly([cmd], cwd=REPO_ROOT)
        assert probe.calls == [], "被拒命令不得真的执行"

    def test_prefix_arguments_rejected(self, probe):
        """``git -c ... push`` / ``--git-dir=...`` 一类前缀参数一律拒绝"""
        for argv in (["-c", "core.pager=cat", "log"], ["--git-dir=/tmp/x", "log"]):
            with pytest.raises(gitio.ForbiddenGitOperation):
                gitio.run_git_readonly(argv, cwd=REPO_ROOT)
        assert probe.calls == []

    def test_output_flag_rejected(self, probe):
        with pytest.raises(gitio.ForbiddenGitOperation):
            gitio.run_git_readonly(["log", "--output=/tmp/leak"], cwd=REPO_ROOT)

    def test_unknown_subcommand_fails_closed(self, probe):
        with pytest.raises(gitio.ForbiddenGitOperation):
            gitio.run_git_readonly(["frobnicate"], cwd=REPO_ROOT)

    def test_branch_creation_is_local_only(self, probe, tmp_path):
        """唯一写操作：``git branch``（不 checkout、不接触远端）"""
        repo = tmp_path / "repo"
        repo.mkdir()
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        for args in (["init", "-q"], ["config", "user.email", "t@e.com"],
                     ["config", "user.name", "t"]):
            subprocess.run(["git", *args], cwd=str(repo), capture_output=True, env=env)
        (repo / "a.txt").write_text("x\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=str(repo), capture_output=True, env=env)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(repo),
                       capture_output=True, env=env)
        probe.calls.clear()

        result = gitio.create_local_branch(str(repo), "repair/20260912-test")
        assert result.created is True, result.error
        assert result.to_dict()["pushed"] is False
        assert result.to_dict()["merged"] is False
        subs = probe.git_subcommands
        assert "branch" in subs
        for sub in subs:
            assert sub in ("branch", "rev-parse"), f"出现预期外的子命令：{sub}"
        # 工作区分支**未切换**（仍是 master/main）
        head = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=str(repo),
                              capture_output=True, text=True, env=env).stdout.strip()
        assert head in ("master", "main")
        # 该分支确实存在于本地
        assert gitio.branch_exists(str(repo), "repair/20260912-test") is True
        # 且**没有远端**（本流程从不添加远端）
        remotes = subprocess.run(["git", "remote"], cwd=str(repo), capture_output=True,
                                 text=True, env=env).stdout.strip()
        assert remotes == ""

    def test_branch_creation_refuses_existing(self, probe, tmp_path):
        repo = tmp_path / "repo2"
        repo.mkdir()
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        subprocess.run(["git", "init", "-q"], cwd=str(repo), capture_output=True, env=env)
        subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=str(repo),
                       capture_output=True, env=env)
        subprocess.run(["git", "config", "user.name", "t"], cwd=str(repo),
                       capture_output=True, env=env)
        (repo / "a.txt").write_text("x\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=str(repo), capture_output=True, env=env)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(repo),
                       capture_output=True, env=env)
        first = gitio.create_local_branch(str(repo), "repair/x")
        assert first.created is True
        second = gitio.create_local_branch(str(repo), "repair/x")
        assert second.created is False
        assert second.existed is True

    def test_invalid_branch_name_refused(self, tmp_path):
        result = gitio.create_local_branch(str(tmp_path), "bad name with spaces")
        assert result.created is False
        assert "非法分支名" in result.error

    def test_is_readonly_invocation_helper(self):
        assert gitio.is_readonly_invocation(["log", "-n1"]) is True
        assert gitio.is_readonly_invocation(["push", "origin", "master"]) is False
        assert gitio.is_readonly_invocation([]) is False
        assert gitio.is_readonly_invocation(["log", "--output=/tmp/x"]) is False


# ════════════════════════════════════════════════════════════
#  ③ 数据结构自证
# ════════════════════════════════════════════════════════════


class TestDataModelSelfEvidence:
    def test_proposal_defaults_are_not_pushed_or_merged(self):
        from agent.repair.models import RepairProposal
        proposal = RepairProposal(branch="repair/x")
        assert proposal.pushed is False
        assert proposal.merged is False
        assert proposal.to_dict()["pushed"] is False
        assert proposal.to_dict()["merged"] is False

    def test_branch_result_defaults(self):
        result = gitio.BranchResult(name="repair/x")
        assert result.to_dict()["pushed"] is False
        assert result.to_dict()["merged"] is False

    def test_proposal_has_no_remote_fields(self):
        """产物数据模型里**不存在** remote/push/merge 字段（从类型层面杜绝钩子）"""
        from agent.repair.models import RepairProposal
        fields = set(RepairProposal.__dataclass_fields__)
        for forbidden in ("remote", "remote_url", "push_command", "merge_command",
                          "pr_url", "pull_request_url"):
            assert forbidden not in fields

    def test_report_model_has_no_remote_fields(self):
        from agent.repair.models import RepairReport
        fields = set(RepairReport.__dataclass_fields__)
        for forbidden in ("remote", "pushed_to", "merged_at", "pr_url"):
            assert forbidden not in fields

    def test_propose_marks_not_pushed_even_on_success(self, tmp_path, monkeypatch):
        """产出成功后 ``pushed/merged`` 仍为 False，且真实仓库无远端交互"""
        from repair_fixtures import make_patch, make_ticket, make_verification, simple_diff
        from agent.repair import propose as PR

        repo = tmp_path / "repo"
        (repo / "agent").mkdir(parents=True)
        (repo / "agent" / "demo_math.py").write_text("def add(a, b):\n    return a - b\n",
                                                     encoding="utf-8")
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        for args in (["init", "-q"], ["config", "user.email", "t@e.com"],
                     ["config", "user.name", "t"], ["add", "-A"],
                     ["commit", "-q", "-m", "init"]):
            subprocess.run(["git", *args], cwd=str(repo), capture_output=True, env=env)

        called = []
        real_run = gitio.subprocess.run

        def _spy(args, *a, **kw):
            called.append([str(x) for x in args])
            return real_run(args, *a, **kw)

        monkeypatch.setattr(gitio.subprocess, "run", _spy)
        proposal = PR.propose(PR.ProposeRequest(
            repo_root=str(repo), ticket=make_ticket(repo_head=gitio.repo_head(str(repo))),
            patch=make_patch(simple_diff()), verification=make_verification(ok=True),
            run_id="rep-nopush", artifact_dir=str(tmp_path / "artifacts"),
            create_branch=True))
        assert proposal.created_branch is True
        assert proposal.pushed is False
        assert proposal.merged is False
        git_calls = [c for c in called
                     if c and os.path.basename(c[0]) in ("git", "git.exe")]
        for call in git_calls:
            assert "push" not in call
            assert "merge" not in call
            assert "remote" not in call
        remotes = subprocess.run(["git", "remote"], cwd=str(repo), capture_output=True,
                                 text=True, env=env).stdout.strip()
        assert remotes == ""
