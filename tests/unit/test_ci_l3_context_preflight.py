"""L3 context 预检脚本自动化测试 — 确保未来 CI 自动拦截 context 不一致

背景（2026-08-16 实证）：L3 镜像 context 与工作区代码漂移（缺
agent/skills_mgmt/lineage.py 等已跟踪模块）导致挂载 conftest import 失败 →
130 项测试全量 ERROR。本测试守护 ci_l3_context_preflight.py 的拦截逻辑，
防止预检脚本被误改后失去 fail-fast 能力。

覆盖：4 项校验的通过/失败路径 + JSON 输出契约 + main() 退出码。
"""
import importlib.util
import json
import sys
from pathlib import Path

PREFLIGHT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "ci_l3_context_preflight.py"


def _load_module():
    """从文件路径加载预检脚本（不入包名空间，避免污染）"""
    spec = importlib.util.spec_from_file_location("ci_l3_context_preflight", PREFLIGHT_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class TestPreflightChecks:
    """四项校验的判定逻辑"""

    def test_clean_repo_all_checks_pass(self):
        """真实仓库（agent/memory/scripts/tests 无修改）四项校验应全过"""
        mod = _load_module()
        assert mod.check_build_files() == []
        assert mod.check_critical_modules() == []
        assert mod.check_git_clean() == []
        assert mod.check_tracked_coverage() == []

    def test_main_exit_zero_on_clean_tree(self):
        """干净树 main() 应返回 0（CI 放行）"""
        mod = _load_module()
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
        rc = mod.main(["--json"])
        out = capsys.readouterr().out
        data = json.loads(out)
        assert rc == 0
        assert all(v["ok"] for v in data.values())
