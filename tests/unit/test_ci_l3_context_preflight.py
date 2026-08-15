"""L3 context 预检脚本自动化测试 — 确保未来 CI 自动拦截 context 不一致

背景（2026-08-16 实证）：L3 镜像 context 与工作区代码漂移（缺
agent/skills_mgmt/lineage.py 等已跟踪模块）导致挂载 conftest import 失败 →
130 项测试全量 ERROR。本测试守护 ci_l3_context_preflight.py 的拦截逻辑，
防止预检脚本被误改后失去 fail-fast 能力。

覆盖：4 项校验的通过/失败路径 + JSON 输出契约 + main() 退出码。
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

PREFLIGHT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "ci_l3_context_preflight.py"
PROJECT_ROOT = PREFLIGHT_PATH.parent.parent


class TestCiIntegration:
    """CI 流水线接入守护 — 确保未来 CI 自动拦截 context 不一致"""

    def test_l3_workflow_keeps_preflight_step(self):
        """l3-docker-tests.yml 必须保留预检步骤（防 CI 拦截被误删）"""
        workflow = PROJECT_ROOT / ".github" / "workflows" / "l3-docker-tests.yml"
        assert workflow.exists(), "CI workflow 文件缺失"
        content = workflow.read_text(encoding="utf-8")
        assert "ci_l3_context_preflight.py" in content, "预检脚本未接入 CI"
        assert "--json" in content, "预检应使用 --json 输出（CI 友好）"

    def test_preflight_step_placed_before_build(self):
        """预检步骤必须位于构建之前（fail fast 语义）"""
        workflow = PROJECT_ROOT / ".github" / "workflows" / "l3-docker-tests.yml"
        lines = workflow.read_text(encoding="utf-8").splitlines()
        preflight_idx = next(
            (i for i, l in enumerate(lines)
             if "ci_l3_context_preflight.py" in l), -1)
        build_idx = next(
            (i for i, l in enumerate(lines)
             if "build-push-action" in l or "docker compose build" in l), -1)
        assert preflight_idx != -1, "未找到预检步骤"
        assert build_idx != -1, "未找到构建步骤"
        assert preflight_idx < build_idx, "预检必须在构建之前执行"


def _load_module():
    """从文件路径加载预检脚本（不入包名空间，避免污染）"""
    spec = importlib.util.spec_from_file_location("ci_l3_context_preflight", PREFLIGHT_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class TestPreflightChecks:
    """四项校验的判定逻辑"""

    def test_required_files_present_in_repo(self):
        """仓库内构建文件与关键模块应存在（纯文件存在性，不依赖 git 状态）"""
        mod = _load_module()
        assert mod.check_build_files() == []
        assert mod.check_critical_modules() == []

    def test_main_exit_zero_on_clean_inputs(self, monkeypatch):
        """四项校验均通过（模拟干净输入）→ main()=0（CI 放行）"""
        mod = _load_module()
        for fn in ("check_build_files", "check_critical_modules",
                   "check_git_clean", "check_tracked_coverage"):
            monkeypatch.setattr(mod, fn, lambda: [])
        assert mod.main([]) == 0

    def test_missing_critical_module_detected(self, monkeypatch):
        """关键模块缺失（context 漂移场景）→ 返回缺失列表 + main()=1"""
        mod = _load_module()
        fake_missing = ["agent/skills_mgmt/definitely_missing.py"]
        monkeypatch.setattr(mod, "CRITICAL_MODULES", fake_missing)
        assert mod.check_critical_modules() == fake_missing
        assert mod.main([]) == 1

    def test_missing_build_file_detected(self, monkeypatch):
        """构建链路文件缺失 → main()=1"""
        mod = _load_module()
        monkeypatch.setattr(mod, "CRITICAL_BUILD_FILES",
                            ["Dockerfile.linux-test.definitely_missing"])
        assert mod.main([]) == 1

    def test_git_dirty_detected(self, monkeypatch):
        """context 目录有未提交修改（镜像快照不一致）→ main()=1"""
        mod = _load_module()
        monkeypatch.setattr(mod, "check_git_clean", lambda: [" M agent/foo.py"])
        assert mod.main([]) == 1

    def test_tracked_file_missing_on_disk(self, monkeypatch):
        """已跟踪文件磁盘缺失（context 打包遗漏）→ main()=1"""
        mod = _load_module()
        monkeypatch.setattr(mod, "check_tracked_coverage",
                            lambda: ["agent/skills_mgmt/lineage.py"])
        assert mod.main([]) == 1


class TestPreflightJsonContract:
    """--json 输出契约（CI 解析依赖）"""

    def test_json_output_valid_and_marks_failure(self, monkeypatch, capsys):
        """失败场景 --json 输出可解析，失败项 ok=false 且携带 issues"""
        mod = _load_module()
        monkeypatch.setattr(mod, "check_git_clean", lambda: [" M agent/foo.py"])
        rc = mod.main(["--json"])
        out = capsys.readouterr().out
        data = json.loads(out)
        assert rc == 1
        assert set(data.keys()) == {"build_files", "critical_modules",
                                    "git_clean", "tracked_coverage"}
        assert data["git_clean"]["ok"] is False
        assert data["git_clean"]["issues"] == [" M agent/foo.py"]

    def test_json_output_all_ok_on_clean(self, monkeypatch, capsys):
        """通过场景 --json 输出 4 项全 ok=true"""
        mod = _load_module()
        for fn in ("check_build_files", "check_critical_modules",
                   "check_git_clean", "check_tracked_coverage"):
            monkeypatch.setattr(mod, fn, lambda: [])
        rc = mod.main(["--json"])
        out = capsys.readouterr().out
        data = json.loads(out)
        assert rc == 0
        assert all(v["ok"] for v in data.values())


class TestPreflightEdgeCases:
    """context 漂移边界场景（解析细节 / 模式分支 / 环境异常）"""

    def test_git_clean_parses_untracked_as_clean(self, monkeypatch):
        """git status 的 ??（未跟踪）不判脏；M/D/A 判脏（镜像快照一致性语义）"""
        mod = _load_module()

        class FakeProc:
            returncode = 0
            stdout = "?? agent/untracked.py\n M agent/modified.py\n?? memory/new.py"
            stderr = ""

        monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: FakeProc())
        dirty = mod.check_git_clean()
        assert dirty == [" M agent/modified.py"]

    def test_git_clean_only_mode_skips_file_checks(self, monkeypatch):
        """--git-clean-only 仅执行 git 一致性校验，跳过文件存在性"""
        mod = _load_module()
        monkeypatch.setattr(mod, "check_git_clean", lambda: [])
        assert mod.main(["--git-clean-only"]) == 0

    def test_subprocess_error_treated_as_failure(self, monkeypatch, capsys):
        """git 不可用（RuntimeError）→ main()=1 且结果标记失败（不崩溃）"""
        mod = _load_module()

        def boom(*a, **k):
            raise RuntimeError("git not available")

        monkeypatch.setattr(mod.subprocess, "run", boom)
        rc = mod.main([])
        assert rc == 1


class TestSimulatedCiFailure:
    """端到端模拟 CI 流水线：context 漂移 → 构建中断 → 修复 → 放行"""

    def _make_mini_repo(self, tmp_path: Path, with_lineage: bool) -> None:
        """在 tmp_path 构造迷你 git 仓库（含构建文件；可选缺 lineage.py 模拟漂移）"""
        # 构建链路文件（预检 build_files 校验需要）
        (tmp_path / "Dockerfile.linux-test").write_text("", encoding="utf-8")
        (tmp_path / "docker-compose.linux-test.yml").write_text("", encoding="utf-8")
        scripts = tmp_path / "scripts"
        scripts.mkdir()
        (scripts / "predownload_models.py").write_text("", encoding="utf-8")
        (scripts / "run_l3_regression_tests.ps1").write_text("", encoding="utf-8")
        # 关键模块（conftest 引用链；with_lineage=False 即模拟 context 漂移）
        skills = tmp_path / "agent" / "skills_mgmt"
        skills.mkdir(parents=True)
        for name in ("models.py", "meta_editor.py", "service.py"):
            (skills / name).write_text("", encoding="utf-8")
        if with_lineage:
            (skills / "lineage.py").write_text("", encoding="utf-8")
        vs = tmp_path / "memory" / "vector_store"
        vs.mkdir(parents=True)
        (vs / "vector_store.py").write_text("", encoding="utf-8")
        (vs / "sqlite_vec_backend.py").write_text("", encoding="utf-8")
        # 初始化 git 仓库并提交基线（保证 git_clean 校验通过）
        env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path,
                       env={**__import__("os").environ, **env}, check=True)

    def test_context_drift_blocks_build_then_fix_releases(
            self, tmp_path, monkeypatch):
        """缺 lineage.py（镜像快照漂移）→ rc=1（构建中断）；补全提交 → rc=0（放行）"""
        self._make_mini_repo(tmp_path, with_lineage=False)
        monkeypatch.setenv("PREFLIGHT_ROOT", str(tmp_path))

        drifted = _load_module()
        assert drifted.main([]) == 1  # CI 应在此中断（fail fast）

        # 修复：补齐缺失模块并提交
        (tmp_path / "agent" / "skills_mgmt" / "lineage.py").write_text(
            "", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "fix"], cwd=tmp_path,
                       env={**__import__("os").environ,
                            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"},
                       check=True)

        fixed = _load_module()
        assert fixed.main([]) == 0  # 修复后放行

    def test_ci_command_contract_exit_code(self, tmp_path, monkeypatch):
        """CI 命令契约：--json 失败时退出码 1（workflow '|| exit 1' 依赖）"""
        self._make_mini_repo(tmp_path, with_lineage=False)
        monkeypatch.setenv("PREFLIGHT_ROOT", str(tmp_path))
        proc = subprocess.run(
            [sys.executable, str(PREFLIGHT_PATH), "--json"],
            cwd=PROJECT_ROOT, capture_output=True, text=True)
        assert proc.returncode == 1  # 漂移 → 非零退出，CI 中断
        import json as _json
        _json.loads(proc.stdout)  # stdout 必须仍是合法 JSON
