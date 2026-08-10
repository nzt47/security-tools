"""CI 必挂隐患修复回归测试（2026-08-05 复盘）

覆盖本次所有变更的自动化回归：
1. safe_git_revert.stdout 纯净化修复 —— dry-run 日志必须走 stderr，不污染 stdout
2. ci_guard_types 契约校验重建 —— validate_report 对 run_ci_guard 报告结构校验
3. run_ci_guard 全流程 JSON 输出 —— --json 必须可被 json.loads 解析且 exit_code 语义正确
4. 改名引用一致性 —— simulate_ci_pipeline 原版恢复 + simulate_ci_guard_pipeline 新名
5. scan_missing_deps 巡检 —— 未入库依赖/.pyc 缓存陷阱检测
6. publish_fix_to_docs —— commit hash + 修复点索引生成与去重

Why:
- 本次修复的三类隐患(未入库依赖/.pyc 缓存陷阱/stdout 污染)均为"本地假绿, CI 必挂",
  若再次回归, 本地测试必须第一时间捕获。
- 参见 docs/observability/ci_hidden_failure_fix_report_20260805.md

运行:
    python -m pytest tests/unit/test_ci_guard_fix_regression.py -q
"""

import io
import json
import os
import subprocess
import sys
import time
from contextlib import redirect_stdout

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPTS_DIR = os.path.join(PROJECT_ROOT, "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

PY = sys.executable


def _run_ci_cmd(args, timeout=300):
    """运行 CI 脚本并埋点诊断（仅失败时输出，走 stderr 由 pytest 捕获）。

    Why: 本文件 4 个测试在完整套件下偶发 `returncode=1 且 stderr 为空`
    （疑似资源竞争导致子进程被杀/崩溃，单独运行均通过）。埋点输出：
      - returncode 十六进制：区分「正常业务失败」(1/2) 与「进程被外部
        杀死/崩溃」(如 0xC0000005 access violation / 0xC0000409)，定位是否被杀
      - 耗时：判断是否接近 timeout 上限
      - stdout/stderr 尾部：判断脚本自身报错内容
    """
    t0 = time.monotonic()
    p = subprocess.run(
        [PY] + args, cwd=PROJECT_ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout)
    dt = time.monotonic() - t0
    if p.returncode != 0:
        print(f"[diag-ci] {os.path.basename(args[0])} returncode={p.returncode} "
              f"(0x{p.returncode & 0xFFFFFFFF:08X}) elapsed={dt:.2f}s "
              f"stdout_tail={p.stdout[-500:]!r} stderr_tail={p.stderr[-500:]!r}",
              file=sys.stderr, flush=True)
    return p


# ═══════════════════════════════════════════════════════════
# 1. safe_git_revert: stdout 纯净(核心修复, 防止 CI json.load 必挂)
# ═══════════════════════════════════════════════════════════

class TestSafeGitRevertStdoutPure:
    def test_dry_run_stdout纯净(self):
        """dry-run 调用时 stdout 必须无任何输出(日志走 stderr)"""
        from safe_git_revert import safe_revert

        buf = io.StringIO()
        with redirect_stdout(buf):
            result = safe_revert("HEAD", dry_run=True)
        assert buf.getvalue() == "", f"stdout 被污染: {buf.getvalue()!r}"

    def test_dry_run_返回结构(self):
        from safe_git_revert import safe_revert

        result = safe_revert("HEAD", dry_run=True)
        assert "affected_files" in result
        assert "exit_code" in result
        assert result["exit_code"] == 0

    def test_stdout纯净_不执行任何git修改(self):
        """dry_run 语义: 不应执行 revert, 工作区保持不变"""
        from safe_git_revert import safe_revert

        status_before = subprocess.run(
            ["git", "status", "--porcelain"], cwd=PROJECT_ROOT,
            capture_output=True, text=True, encoding="utf-8").stdout
        safe_revert("HEAD", dry_run=True)
        status_after = subprocess.run(
            ["git", "status", "--porcelain"], cwd=PROJECT_ROOT,
            capture_output=True, text=True, encoding="utf-8").stdout
        assert status_before == status_after


# ═══════════════════════════════════════════════════════════
# 2. ci_guard_types: 契约校验(重建模块回归)
# ═══════════════════════════════════════════════════════════

class TestCiGuardTypesContract:
    def test_合法报告通过(self):
        from ci_guard_types import validate_report

        report = {
            "tool": "run_ci_guard",
            "timestamp": "2026-08-05T10:00:00+00:00",
            "steps": [
                {"step": "detect", "status": "no_changes", "exit_code": 0,
                 "details": {"branch": "master", "base": "origin/main"}},
                {"step": "rollback_sim", "status": "ok", "exit_code": 0,
                 "details": {"message": "无需回滚"}},
                {"step": "guard_verify", "status": "allowed",
                 "exit_code": 0,
                 "details": {"checks": [], "blocked_reasons": []}},
            ],
            "overall": {"status": "pass", "exit_code": 0},
        }
        assert validate_report(report) == []

    def test_tool标识错误(self):
        from ci_guard_types import validate_report

        report = {"tool": "wrong", "timestamp": "2026-08-05T00:00:00+00:00",
                  "steps": [], "overall": {"status": "pass", "exit_code": 0}}
        errs = validate_report(report)
        assert any("tool" in e for e in errs)

    def test_steps为空报错(self):
        from ci_guard_types import validate_report

        report = {"tool": "run_ci_guard",
                  "timestamp": "2026-08-05T00:00:00+00:00",
                  "steps": [], "overall": {"status": "pass", "exit_code": 0}}
        errs = validate_report(report)
        assert any("steps" in e for e in errs)

    def test_overall状态与exit_code不一致(self):
        from ci_guard_types import validate_report

        report = {"tool": "run_ci_guard",
                  "timestamp": "2026-08-05T00:00:00+00:00",
                  "steps": [
                      {"step": "guard_verify", "status": "blocked",
                       "exit_code": 1,
                       "details": {"checks": [], "blocked_reasons": ["x"]}}],
                  "overall": {"status": "pass", "exit_code": 0}}
        errs = validate_report(report)
        assert any("overall" in e for e in errs)

    def test_未知步骤名报错(self):
        from ci_guard_types import validate_report

        report = {"tool": "run_ci_guard",
                  "timestamp": "2026-08-05T00:00:00+00:00",
                  "steps": [{"step": "unknown_step", "status": "ok",
                             "exit_code": 0, "details": {}}],
                  "overall": {"status": "pass", "exit_code": 0}}
        errs = validate_report(report)
        assert any("step" in e for e in errs)


# ═══════════════════════════════════════════════════════════
# 3. run_ci_guard: 全流程 JSON 输出(CI 消费契约)
# ═══════════════════════════════════════════════════════════

class TestRunCiGuardJson:
    def test_json输出可解析且overall一致(self):
        """--json 输出必须能被 json.loads 直接解析(本次修复核心)"""
        p = _run_ci_cmd([os.path.join(SCRIPTS_DIR, "run_ci_guard.py"), "--json"])
        assert p.returncode == 0, f"run_ci_guard 失败: {p.stderr}"
        report = json.loads(p.stdout)
        assert report["tool"] == "run_ci_guard"
        assert report["overall"]["status"] in ("pass", "fail")
        assert report["overall"]["exit_code"] == 0

    def test_stdout纯净为JSON(self):
        """stdout 首字符必须是 { (dry-run 日志不得混入)"""
        p = _run_ci_cmd([os.path.join(SCRIPTS_DIR, "run_ci_guard.py"),
                         "--json", "--skip-detect"])
        assert p.returncode == 0
        assert p.stdout.lstrip().startswith("{"), \
            f"stdout 被污染, 首字符={p.stdout[:30]!r}"
        json.loads(p.stdout)  # 必须可解析

    def test_validate分支契约校验通过(self):
        """--validate 依赖重建的 ci_guard_types, 必须通过"""
        p = _run_ci_cmd([os.path.join(SCRIPTS_DIR, "run_ci_guard.py"),
                         "--validate", "--skip-detect", "--json"])
        assert p.returncode == 0, f"validate 分支失败: {p.stderr}"
        json.loads(p.stdout)

    def test_force_fail注入失败语义(self):
        """--force-fail 注入守卫失败 → exit 1, overall=fail"""
        p = subprocess.run(
            [PY, os.path.join(SCRIPTS_DIR, "run_ci_guard.py"),
             "--force-fail", "--skip-detect", "--json"],
            cwd=PROJECT_ROOT, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=300)
        assert p.returncode == 1
        report = json.loads(p.stdout)
        assert report["overall"]["status"] == "fail"
        assert report["overall"]["exit_code"] == 1


# ═══════════════════════════════════════════════════════════
# 4. 改名引用一致性(simulate_ci_pipeline 原版 / guard_pipeline 新名)
# ═══════════════════════════════════════════════════════════

class TestRenameConsistency:
    def test_两个脚本都存在且独立(self):
        assert os.path.exists(os.path.join(SCRIPTS_DIR, "simulate_ci_pipeline.py")), \
            "原版 simulate_ci_pipeline.py 必须恢复存在"
        assert os.path.exists(
            os.path.join(SCRIPTS_DIR, "simulate_ci_guard_pipeline.py")), \
            "新脚本 simulate_ci_guard_pipeline.py 必须存在"
        # 内容不同(原版是 CI/CD 触发模拟器, 新脚本是 CI 流水线模拟)
        orig = open(os.path.join(SCRIPTS_DIR, "simulate_ci_pipeline.py"),
                    encoding="utf-8").read()
        new = open(os.path.join(SCRIPTS_DIR, "simulate_ci_guard_pipeline.py"),
                   encoding="utf-8").read()
        assert orig != new

    def test_guard_pipeline_json可运行(self):
        p = _run_ci_cmd([os.path.join(SCRIPTS_DIR, "simulate_ci_guard_pipeline.py"),
                         "--json"], timeout=600)
        assert p.returncode == 0, f"模拟失败: {p.stderr}"
        report = json.loads(p.stdout)
        assert report["tool"] == "simulate_ci_guard_pipeline"
        assert report["overall"]["status"] in ("pass", "fail")


# ═══════════════════════════════════════════════════════════
# 5. scan_missing_deps: 未入库依赖/.pyc 陷阱巡检
# ═══════════════════════════════════════════════════════════

class TestScanMissingDeps:
    def test_扫描返回结构化结果(self):
        import scan_missing_deps

        result = scan_missing_deps.scan(PROJECT_ROOT)
        assert result["tool"] == "scan_missing_deps"
        assert "workflow_refs_missing" in result
        assert "lost" in result
        assert "timestamp" in result

    def test_json输出可解析(self):
        p = subprocess.run(
            [PY, os.path.join(SCRIPTS_DIR, "scan_missing_deps.py"),
             "--json"],
            cwd=PROJECT_ROOT, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=120)
        assert p.returncode == 0
        json.loads(p.stdout)


# ═══════════════════════════════════════════════════════════
# 6. publish_fix_to_docs: commit hash + 修复点索引生成
# ═══════════════════════════════════════════════════════════

class TestPublishFixToDocs:
    def test_索引文件生成(self, tmp_path):
        import publish_fix_to_docs

        commits = [{"sha": "e859f22", "subject": "fix(ci): test",
                    "date": "2026-08-05"}]
        content = publish_fix_to_docs._render_index(commits)
        assert "e859f22" in content
        assert content.startswith("# CI 修复记录索引")

    def test_索引去重(self, tmp_path):
        import publish_fix_to_docs

        entries = [
            {"sha": "abc1234", "subject": "a", "date": "2026-08-05"},
            {"sha": "abc1234", "subject": "a-dup", "date": "2026-08-05"},
            {"sha": "def5678", "subject": "b", "date": "2026-08-05"},
        ]
        content = publish_fix_to_docs._render_index(entries)
        assert content.count("abc1234") == 1, "重复 commit 应被去重"

    def test_无变更时不重复推送(self, tmp_path):
        """已含全部 commit → 第二次 dry-run 不再新增内容(幂等)"""
        index = str(tmp_path / "index.md")
        cmd = [PY, os.path.join(SCRIPTS_DIR, "publish_fix_to_docs.py"),
               "--count", "1", "--index", index]
        r1 = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True,
                            text=True, encoding="utf-8", errors="replace",
                            timeout=60)
        assert r1.returncode == 0, r1.stderr
        size1 = os.path.getsize(index)
        # 第二次运行: 索引已含该 commit → 不重复追加, 文件大小不变
        r2 = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True,
                            text=True, encoding="utf-8", errors="replace",
                            timeout=60)
        assert r2.returncode == 0, r2.stderr
        size2 = os.path.getsize(index)
        assert size1 == size2, "重复推送导致索引增长"
