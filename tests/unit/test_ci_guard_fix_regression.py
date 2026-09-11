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
import re
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
# 4b. 预提交钩子 CI_GUARD 段契约（2026-09-11 S3-01 收尾补齐）
#
# 背景：钩子 CI_GUARD 段长期引用 simulate_ci_guard_failure.py（git 历史中从未存在），
# 且钩子设计为"脚本缺失时静默跳过" ⇒ 该门禁长期静默放过，给人"已受保护"的错觉。
# 只改引用名也不行：本脚本原先不接受 --assert-allowed，会以 exit 2（unrecognized
# arguments）**阻断仓库全部提交**。故两步缺一不可，且必须被回归锁定。
# ═══════════════════════════════════════════════════════════

_GUARD = os.path.join(SCRIPTS_DIR, "simulate_ci_guard_pipeline.py")

#: 钩子模板（源 + 包内镜像副本，二者必须同源）
_HOOK_TEMPLATES = [
    os.path.join(PROJECT_ROOT, "scripts", "dev", "hook_fail_safe.psm1"),
    os.path.join(PROJECT_ROOT, "packages", "tlm-hook-failsafe",
                 "tlm-hook-failsafe.psm1"),
]


class TestPreCommitCiGuardContract:
    def test_脚本接受assert_allowed且判定通过(self):
        """钩子传入的标志必须被接受，且判定链通过时 exit 0（允许提交）"""
        p = _run_ci_cmd([_GUARD, "--assert-allowed"], timeout=600)
        assert p.returncode == 0, (
            "CI_GUARD 判定链未通过或被参数解析阻断: "
            f"exit={p.returncode} stderr={p.stderr[-500:]}")
        assert "unrecognized arguments" not in (p.stderr or ""), \
            "--assert-allowed 必须是已声明参数，否则钩子会以 exit 2 阻断全部提交"
        assert "允许提交" in p.stdout

    def test_判定失败时返回非0并列出被阻止项(self):
        """**负向路径**：守卫失败必须阻止提交（否则比"静默跳过"更糟）"""
        import importlib.util

        spec = importlib.util.spec_from_file_location("_guard_pipeline", _GUARD)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        fake = {
            "tool": "simulate_ci_guard_pipeline",
            "workflows": [
                {"workflow": "ci-guard-runner", "exit_code": 1, "overall": None},
                {"workflow": "reranker-timeout-guard", "steps": [
                    {"step": "verify 6 场景", "exit_code": 0},
                    {"step": "pytest 9 用例", "exit_code": 2}]},
            ],
            "overall": {"status": "fail", "exit_code": 1},
        }
        blocked = mod._blocked_summary(fake)
        assert any("ci-guard-runner" in b for b in blocked)
        assert any("pytest 9 用例" in b for b in blocked)

        # 端到端：monkeypatch 后 main 必须返回非 0
        orig = mod.simulate
        mod.simulate = lambda: fake
        try:
            sys.argv = ["simulate_ci_guard_pipeline.py", "--assert-allowed"]
            rc = mod.main()
        finally:
            mod.simulate = orig
            sys.argv = sys.argv[:1]
        assert rc == 1, "判定失败时必须以非 0 退出以阻止提交"

    def test_钩子模板已指向真实存在的脚本(self):
        """钩子 `CI_GUARD=` **赋值行**必须指向真实存在的脚本（漂移即红）

        注：只断言赋值行，不断言全文 —— 模板注释里**如实记载**旧名
        `simulate_ci_guard_failure.py` 是必要的历史说明，不属于"仍在使用旧名"。
        """
        for tpl in _HOOK_TEMPLATES:
            assert os.path.exists(tpl), f"钩子模板缺失: {tpl}"
            text = open(tpl, encoding="utf-8").read()
            m = re.search(r'CI_GUARD\s*=\s*"[^"]*/([^"/]+)"', text)
            assert m, f"{os.path.basename(tpl)} 未找到 CI_GUARD 赋值行"
            assert m.group(1) == "simulate_ci_guard_pipeline.py", (
                f"{os.path.basename(tpl)} 的 CI_GUARD 指向 {m.group(1)}"
                "（应为 simulate_ci_guard_pipeline.py，否则门禁静默跳过）")
            # 旧名不得出现在任何可执行行（非注释行）
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("#") or stripped.startswith("'"):
                    continue
                assert "simulate_ci_guard_failure" not in stripped, \
                    f"{os.path.basename(tpl)} 可执行行仍引用旧名: {stripped}"

    def test_钩子模板保留不变量所需模式(self):
        """`verify_core_invariants` H1/H2 要求 CI_GUARD= + --assert-allowed + SKIP_CI_GUARD"""
        for tpl in _HOOK_TEMPLATES:
            text = open(tpl, encoding="utf-8").read()
            assert re.search(r"CI_GUARD\s*=", text)
            assert "--assert-allowed" in text
            assert "SKIP_CI_GUARD" in text

    def test_两个钩子模板同源(self):
        """源模板与包内镜像副本必须逐字一致（避免只改一处）"""
        a = open(_HOOK_TEMPLATES[0], encoding="utf-8").read()
        b = open(_HOOK_TEMPLATES[1], encoding="utf-8").read()
        assert a == b, "scripts/dev/hook_fail_safe.psm1 与包内镜像副本已不同源"


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
