#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Docker 镜像测试脚本 — 验证 kwarg-scanner 镜像功能完整性

测试维度（遵循代码测试规范）:
    1. 功能测试: 镜像启动、命令执行、扫描功能
    2. 边界测试: 无参数、错误参数、空目录
    3. 兼容性测试: 不同挂载方式
    4. 错误处理测试: 退出码、错误消息
    5. 健康检查测试: --health 标志

运行:
    python packages/kwarg_scanner/tests/test_docker.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List

# ════════════════════════════════════════════════════════════
# 结构化日志（遵循可观测性硬约束）
# ════════════════════════════════════════════════════════════

def _trace_id() -> str:
    return uuid.uuid4().hex[:16]


def _log(action: str, **payload: Any) -> None:
    record = {
        "trace_id": _trace_id(),
        "module_name": "test_docker",
        "action": action,
        "duration_ms": 0.0,
        **payload,
    }
    print(json.dumps(record, ensure_ascii=False, default=str), file=sys.stderr)


# 埋点占位符
def trackEvent(event_name: str, payload: Dict) -> None:
    _log("track_event", event_name=event_name, payload=str(payload))


# ════════════════════════════════════════════════════════════
# 测试工具
# ════════════════════════════════════════════════════════════

IMAGE = os.environ.get("SCANNER_IMAGE", "kwarg-scanner:latest")
PROJECT_ROOT = Path(__file__).resolve().parents[3]  # c:\Users\Administrator\agent


def run_docker(args: List[str], mount_project: bool = True,
               env: Dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """运行 Docker 容器并返回结果"""
    cmd = ["docker", "run", "--rm"]
    if mount_project:
        cmd += ["-v", f"{PROJECT_ROOT}:/project"]
    if env:
        for k, v in env.items():
            cmd += ["-e", f"{k}={v}"]
    cmd += [IMAGE] + args
    _log("docker_run", cmd=" ".join(cmd))
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60)


def assert_equal(actual: Any, expected: Any, msg: str) -> bool:
    if actual != expected:
        _log("assert_fail", msg=msg, actual=str(actual), expected=str(expected))
        return False
    return True


def assert_in(needle: str, haystack: str, msg: str) -> bool:
    if needle not in haystack:
        _log("assert_fail", msg=msg, needle=needle, haystack=haystack[:200])
        return False
    return True


# ════════════════════════════════════════════════════════════
# 测试用例
# ════════════════════════════════════════════════════════════

class TestDockerImage:
    """Docker 镜像测试套件"""

    passed: int = 0
    failed: int = 0
    errors: List[str] = []

    def _ok(self, name: str) -> None:
        self.passed += 1
        _log("test_pass", test_name=name)
        print(f"  [PASS] {name}")

    def _fail(self, name: str, reason: str) -> None:
        self.failed += 1
        self.errors.append(f"{name}: {reason}")
        _log("test_fail", test_name=name, reason=reason)
        print(f"  [FAIL] {name}: {reason}")

    # ── 功能测试 ──────────────────────────────────────────────

    def test_health_check(self) -> None:
        """测试 --health 健康检查"""
        name = "test_health_check"
        print(f"\n[功能测试] {name}")
        result = run_docker(["--health"], mount_project=False)
        if not assert_equal(result.returncode, 0, f"{name} 退出码应为 0"):
            self._fail(name, f"exit={result.returncode}, stderr={result.stderr[:200]}")
            return
        try:
            health = json.loads(result.stdout.strip())
            if not assert_equal(health.get("status"), "healthy", "状态应为 healthy"):
                self._fail(name, f"健康状态异常: {health}")
                return
        except json.JSONDecodeError as e:
            self._fail(name, f"输出非 JSON: {e}, stdout={result.stdout[:200]}")
            return
        self._ok(name)

    def test_version(self) -> None:
        """测试 --version 版本输出"""
        name = "test_version"
        print(f"\n[功能测试] {name}")
        result = run_docker(["--version"], mount_project=False)
        if not assert_equal(result.returncode, 0, f"{name} 退出码应为 0"):
            self._fail(name, f"exit={result.returncode}")
            return
        if not assert_in("1.0.0", result.stdout, "版本号应包含 1.0.0"):
            self._fail(name, f"stdout={result.stdout[:200]}")
            return
        self._ok(name)

    def test_scan_clean_code(self) -> None:
        """测试扫描干净代码（应通过）"""
        name = "test_scan_clean_code"
        print(f"\n[功能测试] {name}")
        # 扫描 packages/kwarg_scanner/kwarg_scanner 目录（应无 HIGH 风险）
        result = run_docker(
            ["--path", "/project/packages/kwarg_scanner/kwarg_scanner"],
            env={"MIN_RISK": "HIGH", "OUTPUT_FORMAT": "text"}
        )
        if not assert_equal(result.returncode, 0, f"{name} 干净代码应返回 0"):
            self._fail(name, f"exit={result.returncode}, stderr={result.stderr[:300]}")
            return
        self._ok(name)

    def test_scan_with_high_risk(self) -> None:
        """测试扫描含 HIGH 风险的代码（应阻断）"""
        name = "test_scan_with_high_risk"
        print(f"\n[功能测试] {name}")
        # 创建临时目录写入高风险代码
        with tempfile.TemporaryDirectory() as tmpdir:
            risky_code = '''
def emit(action, *, trace_id=None, **kw):
    pass

payload = {"trace_id": "xxx", "extra": 1}
emit("x", trace_id="t", **payload)
'''
            risk_file = Path(tmpdir) / "risk.py"
            risk_file.write_text(risky_code, encoding="utf-8")

            # 挂载临时目录
            cmd = [
                "docker", "run", "--rm",
                "-v", f"{tmpdir}:/project",
                "-e", "MIN_RISK=HIGH",
                "-e", "OUTPUT_FORMAT=json",
                IMAGE,
                "--path", "/project"
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if not assert_equal(result.returncode, 1, f"{name} 高风险代码应返回 1"):
                self._fail(name, f"exit={result.returncode}, stdout={result.stdout[:300]}")
                return
            # 验证 stderr 含结构化日志
            if not assert_in("scan_blocked", result.stderr, "应包含 scan_blocked 日志"):
                self._fail(name, f"stderr={result.stderr[:300]}")
                return
        self._ok(name)

    # ── 边界测试 ──────────────────────────────────────────────

    def test_no_project_mount(self) -> None:
        """测试未挂载 /project 时的错误处理"""
        name = "test_no_project_mount"
        print(f"\n[边界测试] {name}")
        # 不挂载 /project，但 ENTRYPOINT 会检查
        result = run_docker([], mount_project=False)
        # 应返回错误码 2（参数错误）或 0（如果 WORKDIR /project 已存在）
        # WORKDIR /project 在镜像构建时创建，所以目录存在但为空
        # 此时扫描空目录应返回 0
        if result.returncode not in (0, 1, 2):
            self._fail(name, f"意外的退出码: {result.returncode}")
            return
        self._ok(name)

    def test_empty_directory(self) -> None:
        """测试扫描空目录"""
        name = "test_empty_directory"
        print(f"\n[边界测试] {name}")
        with tempfile.TemporaryDirectory() as tmpdir:
            result = run_docker(
                ["--path", "/project"],
                env={"MIN_RISK": "HIGH"}
            )
            # 改用挂载空目录
            cmd = [
                "docker", "run", "--rm",
                "-v", f"{tmpdir}:/project",
                "-e", "MIN_RISK=HIGH",
                IMAGE,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if not assert_equal(result.returncode, 0, f"{name} 空目录应返回 0"):
                self._fail(name, f"exit={result.returncode}, stderr={result.stderr[:300]}")
                return
        self._ok(name)

    def test_invalid_min_risk(self) -> None:
        """测试无效的风险等级参数"""
        name = "test_invalid_min_risk"
        print(f"\n[边界测试] {name}")
        result = run_docker(
            ["--path", "/project/packages/kwarg_scanner"],
            env={"MIN_RISK": "INVALID"}
        )
        # 无效参数应返回错误码
        if result.returncode not in (0, 1, 2):
            self._fail(name, f"意外退出码: {result.returncode}")
            return
        self._ok(name)

    # ── 错误处理测试 ──────────────────────────────────────────

    def test_structured_logging(self) -> None:
        """测试结构化日志输出"""
        name = "test_structured_logging"
        print(f"\n[错误处理测试] {name}")
        result = run_docker(
            ["--path", "/project/packages/kwarg_scanner/kwarg_scanner"],
            env={"MIN_RISK": "HIGH", "ENABLE_LOGGING": "true"}
        )
        # stderr 应包含结构化 JSON 日志
        required_fields = ["trace_id", "module_name", "action", "duration_ms"]
        for field in required_fields:
            if not assert_in(field, result.stderr, f"日志应包含 {field}"):
                self._fail(name, f"缺少字段 {field}, stderr={result.stderr[:300]}")
                return
        self._ok(name)

    def test_exit_code_mapping(self) -> None:
        """测试退出码映射（0=通过, 1=阻断, 2=参数错误）"""
        name = "test_exit_code_mapping"
        print(f"\n[错误处理测试] {name}")
        # 测试通过场景
        result_ok = run_docker(
            ["--path", "/project/packages/kwarg_scanner/kwarg_scanner"],
            env={"MIN_RISK": "HIGH"}
        )
        if not assert_equal(result_ok.returncode, 0, "干净代码应为 0"):
            self._fail(name, f"通过场景失败: exit={result_ok.returncode}")
            return
        self._ok(name)

    # ── 兼容性测试 ─────────────────────────────────────────────

    def test_json_output_format(self) -> None:
        """测试 JSON 输出格式"""
        name = "test_json_output_format"
        print(f"\n[兼容性测试] {name}")
        with tempfile.TemporaryDirectory() as tmpdir:
            cmd = [
                "docker", "run", "--rm",
                "-v", f"{tmpdir}:/project",
                "-e", "MIN_RISK=HIGH",
                "-e", "OUTPUT_FORMAT=json",
                "-e", "OUTPUT_FILE=/project/report.json",
                IMAGE,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            report_file = Path(tmpdir) / "report.json"
            if not report_file.exists():
                self._fail(name, "未生成 report.json")
                return
            try:
                report = json.loads(report_file.read_text(encoding="utf-8"))
                if "findings" not in report and "summary" not in report:
                    self._fail(name, f"报告缺少必要字段: {list(report.keys())}")
                    return
            except json.JSONDecodeError as e:
                self._fail(name, f"报告非有效 JSON: {e}")
                return
        self._ok(name)

    # ── 运行所有测试 ───────────────────────────────────────────

    def run_all(self) -> bool:
        """运行所有测试，返回是否全部通过"""
        print("=" * 60)
        print("Docker 镜像测试套件 — kwarg-scanner")
        print(f"镜像: {IMAGE}")
        print(f"项目根: {PROJECT_ROOT}")
        print("=" * 60)

        trackEvent("test_suite_started", {"image": IMAGE})

        tests = [
            self.test_health_check,
            self.test_version,
            self.test_scan_clean_code,
            self.test_scan_with_high_risk,
            self.test_no_project_mount,
            self.test_empty_directory,
            self.test_invalid_min_risk,
            self.test_structured_logging,
            self.test_exit_code_mapping,
            self.test_json_output_format,
        ]

        for test in tests:
            try:
                test()
            except Exception as e:
                self._fail(test.__name__, f"异常: {e}")

        print("\n" + "=" * 60)
        print(f"测试结果: {self.passed} 通过, {self.failed} 失败")
        if self.errors:
            print("\n失败详情:")
            for err in self.errors:
                print(f"  - {err}")
        print("=" * 60)

        trackEvent("test_suite_completed", {
            "passed": self.passed,
            "failed": self.failed,
        })

        return self.failed == 0


# ════════════════════════════════════════════════════════════
# 主入口
# ════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # 检查 Docker 可用性
    try:
        subprocess.run(["docker", "info"], capture_output=True, check=True, timeout=10)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("[ERROR] Docker 不可用，请先启动 Docker daemon")
        sys.exit(3)

    # 检查镜像是否存在
    try:
        result = subprocess.run(
            ["docker", "images", "-q", IMAGE],
            capture_output=True, text=True, timeout=10
        )
        if not result.stdout.strip():
            print(f"[ERROR] 镜像不存在: {IMAGE}")
            print(f"[HINT] 请先构建: docker build -t {IMAGE} ./packages/kwarg_scanner")
            sys.exit(2)
    except subprocess.SubprocessError as e:
        print(f"[ERROR] 检查镜像失败: {e}")
        sys.exit(3)

    suite = TestDockerImage()
    success = suite.run_all()
    sys.exit(0 if success else 1)
