#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
K8s 兼容性校验共享模块（scripts/compat_check.py）单测

【不易】守护不变量：hard error 仅针对"已确认不兼容"（版本过低/API 未注册）；
       未知状态（权限不足/无法获取）必须降级为告警，不得阻断巡检/预热。
【变易】兼容新旧 kubectl 输出（-o json 与文本）、metrics API NotFound/Forbidden 区分。
【简易】用 mock subprocess.run 模拟 kubectl 输出，零集群依赖，CI 可重复运行。

覆盖 8 个边界场景：
  1. 正常兼容（K8s 1.28 + metrics API 可用）→ ok=True，无 errors/warnings
  2. 版本过低 hard error（K8s 1.18 < 1.19）→ ok=False
  3. 版本偏低告警（1.19 ≤ K8s 1.20 < 1.22）→ ok=True，warnings 非空
  4. metrics API NotFound → ok=False（已确认未注册）
  5. metrics API Forbidden → ok=True（权限不足降级告警，不阻断）
  6. kubectl 不可用 → ok=True（无法获取版本降级告警，不阻断）
  7. client/server 版本偏差 > 2 minor → ok=True，warnings 非空
  8. 版本字符串解析 + 旧版 kubectl 文本输出兼容
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

# ── 导入被测模块 ──
# compat_check.py 位于 scripts/ 目录（非 Python 包），需将 scripts 加入 sys.path。
# 顶层 conftest.py 仅加入 PROJECT_ROOT，这里补充 scripts 路径。
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import compat_check  # noqa: E402  (sys.path 注入后导入)


# ════════════════════════════════════════════════════════════════════
#  辅助：构建 mock subprocess.run 的 side_effect
# ════════════════════════════════════════════════════════════════════

def _make_kubectl_mock(
    version_json: str | None = None,
    version_text: str | None = None,
    apiservice_json: str | None = None,
    apiservice_stderr: str | None = None,
    apiservice_rc: int = 0,
    raise_file_not_found: bool = False,
):
    """构建 subprocess.run 的 side_effect，根据 kubectl 子命令返回不同结果。

    参数:
      version_json:   kubectl version -o json 的 stdout（成功）
      version_text:   kubectl version（文本）的 stdout（旧版回退路径）
      apiservice_json: kubectl get apiservice -o json 的 stdout（成功）
      apiservice_stderr: kubectl get apiservice 失败时的 stderr
      apiservice_rc:  kubectl get apiservice 的 returncode
      raise_file_not_found: 模拟 kubectl 未安装（FileNotFoundError）
    """

    def _side_effect(cmd, *args, **kwargs):
        if raise_file_not_found:
            raise FileNotFoundError("kubectl: command not found")

        cmd_str = " ".join(cmd)

        # kubectl version -o json（优先路径）
        if "version" in cmd and "-o" in cmd and "json" in cmd:
            if version_json is not None:
                return CompletedProcess(cmd, 0, stdout=version_json, stderr="")
            # -o json 失败 → 触发回退到文本路径
            return CompletedProcess(cmd, 1, stdout="", stderr="error: json output")

        # kubectl version（文本回退路径）
        if "version" in cmd:
            if version_text is not None:
                return CompletedProcess(cmd, 0, stdout=version_text, stderr="")
            return CompletedProcess(cmd, 1, stdout="", stderr="error")

        # kubectl get apiservice v1beta1.metrics.k8s.io -o json
        if "apiservice" in cmd:
            if apiservice_rc == 0 and apiservice_json is not None:
                return CompletedProcess(cmd, 0, stdout=apiservice_json, stderr="")
            return CompletedProcess(cmd, apiservice_rc, stdout="", stderr=apiservice_stderr or "")

        return CompletedProcess(cmd, 1, stdout="", stderr="unknown command")

    return _side_effect


def _version_json(server: str, client: str | None = None) -> str:
    """生成 kubectl version -o json 的 stdout"""
    return json.dumps({
        "serverVersion": {"gitVersion": server},
        "clientVersion": {"gitVersion": client} if client else {},
    })


def _apiservice_available_json() -> str:
    """生成 metrics API 已注册且可用的 json"""
    return json.dumps({
        "status": {
            "conditions": [{"type": "Available", "status": "True"}]
        }
    })


# ════════════════════════════════════════════════════════════════════
#  场景 1: 正常兼容（K8s 1.28 + metrics API 可用）
# ════════════════════════════════════════════════════════════════════

class TestNormalCompatible:
    """场景 1: 版本达标 + API 可用 → ok=True，无 errors/warnings"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_normal_compatible_no_errors_no_warnings(self):
        with patch.object(compat_check.subprocess, "run") as mock_run:
            mock_run.side_effect = _make_kubectl_mock(
                version_json=_version_json("v1.28.3", "v1.28.1"),
                apiservice_json=_apiservice_available_json(),
            )
            result = compat_check.check_k8s_compatibility()

        assert result.ok is True, f"应通过，但 errors={result.errors}"
        assert result.errors == [], f"不应有 errors: {result.errors}"
        assert result.warnings == [], f"不应有 warnings: {result.warnings}"
        assert result.k8s_server_version == "v1.28.3"
        assert result.k8s_client_version == "v1.28.1"
        assert result.metrics_api_registered is True
        assert result.metrics_api_available is True


# ════════════════════════════════════════════════════════════════════
#  场景 2: 版本过低 hard error（K8s 1.18 < 1.19 最低要求）
# ════════════════════════════════════════════════════════════════════

class TestVersionTooLowHardError:
    """场景 2: server 版本 < MIN_K8S_VERSION(1,19) → ok=False（hard error）"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_version_below_min_is_hard_error(self):
        with patch.object(compat_check.subprocess, "run") as mock_run:
            mock_run.side_effect = _make_kubectl_mock(
                version_json=_version_json("v1.18.5", "v1.18.5"),
                apiservice_json=_apiservice_available_json(),
            )
            result = compat_check.check_k8s_compatibility()

        assert result.ok is False, "版本过低应为 hard error"
        assert any("版本过低" in e for e in result.errors), \
            f"errors 应含'版本过低': {result.errors}"
        assert any("1.19" in e for e in result.errors), \
            f"errors 应提示最低版本 1.19: {result.errors}"


# ════════════════════════════════════════════════════════════════════
#  场景 3: 版本偏低告警（1.19 ≤ 1.20 < 1.22）
# ════════════════════════════════════════════════════════════════════

class TestVersionLowWarning:
    """场景 3: 版本达标但 < WARN_K8S_VERSION(1,22) → ok=True，warnings 非空"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_version_between_min_and_warn_is_warning(self):
        with patch.object(compat_check.subprocess, "run") as mock_run:
            mock_run.side_effect = _make_kubectl_mock(
                version_json=_version_json("v1.20.1", "v1.20.1"),
                apiservice_json=_apiservice_available_json(),
            )
            result = compat_check.check_k8s_compatibility()

        assert result.ok is True, "版本达标应通过（仅告警）"
        assert result.errors == []
        assert any("版本偏低" in w for w in result.warnings), \
            f"warnings 应含'版本偏低': {result.warnings}"
        assert any("1.22" in w for w in result.warnings), \
            f"warnings 应建议升级至 1.22: {result.warnings}"


# ════════════════════════════════════════════════════════════════════
#  场景 4: metrics API NotFound → hard error
# ════════════════════════════════════════════════════════════════════

class TestMetricsApiNotFound:
    """场景 4: APIService NotFound → ok=False（已确认未注册）"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_metrics_api_not_found_is_hard_error(self):
        with patch.object(compat_check.subprocess, "run") as mock_run:
            mock_run.side_effect = _make_kubectl_mock(
                version_json=_version_json("v1.28.3", "v1.28.1"),
                apiservice_rc=1,
                apiservice_stderr="Error from server: NotFound: apiservice \"v1beta1.metrics.k8s.io\" not found",
            )
            result = compat_check.check_k8s_compatibility()

        assert result.ok is False, "API 未注册应为 hard error"
        assert any("未注册" in e for e in result.errors), \
            f"errors 应含'未注册': {result.errors}"
        assert result.metrics_api_registered is False


# ════════════════════════════════════════════════════════════════════
#  场景 5: metrics API Forbidden → 降级告警（不阻断）
# ════════════════════════════════════════════════════════════════════

class TestMetricsApiForbidden:
    """场景 5: APIService Forbidden → ok=True（权限不足降级告警）

    【不易】in-cluster 限权场景不得误判为"未注册"hard error，
           否则违反"预热/巡检不阻断"不变量。
    """

    @pytest.mark.unit
    @pytest.mark.p0
    def test_metrics_api_forbidden_degrades_to_warning(self):
        with patch.object(compat_check.subprocess, "run") as mock_run:
            mock_run.side_effect = _make_kubectl_mock(
                version_json=_version_json("v1.28.3", "v1.28.1"),
                apiservice_rc=1,
                apiservice_stderr="Error from server: Forbidden: User cannot list apiservices",
            )
            result = compat_check.check_k8s_compatibility()

        assert result.ok is True, "权限不足应降级为告警，不阻断"
        assert result.errors == [], f"不应有 errors: {result.errors}"
        assert any("权限不足" in w for w in result.warnings), \
            f"warnings 应含'权限不足': {result.warnings}"


# ════════════════════════════════════════════════════════════════════
#  场景 6: kubectl 不可用 → 降级告警（不阻断）
# ════════════════════════════════════════════════════════════════════

class TestKubectlUnavailable:
    """场景 6: kubectl 未安装/不可达 → ok=True（无法获取版本降级告警）"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_kubectl_missing_degrades_to_warning(self):
        with patch.object(compat_check.subprocess, "run") as mock_run:
            mock_run.side_effect = _make_kubectl_mock(
                raise_file_not_found=True,
            )
            result = compat_check.check_k8s_compatibility()

        assert result.ok is True, "kubectl 不可用应降级为告警，不阻断"
        assert result.errors == []
        # 版本获取失败 + API 检查失败，均应降级为告警
        assert any("无法获取" in w or "kubectl" in w for w in result.warnings), \
            f"warnings 应含版本获取失败提示: {result.warnings}"
        assert result.k8s_server_version is None
        assert result.k8s_client_version is None


# ════════════════════════════════════════════════════════════════════
#  场景 7: client/server 版本偏差 > 2 minor → 告警
# ════════════════════════════════════════════════════════════════════

class TestVersionSkewWarning:
    """场景 7: client/server 版本偏差 > MAX_VERSION_SKEW(2) → 告警"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_client_server_skew_gt_2_is_warning(self):
        # server=1.28, client=1.25 → skew=3 > 2
        with patch.object(compat_check.subprocess, "run") as mock_run:
            mock_run.side_effect = _make_kubectl_mock(
                version_json=_version_json("v1.28.3", "v1.25.0"),
                apiservice_json=_apiservice_available_json(),
            )
            result = compat_check.check_k8s_compatibility()

        assert result.ok is True, "版本偏差仅为告警，不阻断"
        assert result.errors == []
        assert any("版本偏差" in w for w in result.warnings), \
            f"warnings 应含'版本偏差': {result.warnings}"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_client_server_skexactly_2_no_warning(self):
        """边界: skew == 2（正好等于上限）不应告警"""
        # server=1.28, client=1.26 → skew=2 == MAX_VERSION_SKEW
        with patch.object(compat_check.subprocess, "run") as mock_run:
            mock_run.side_effect = _make_kubectl_mock(
                version_json=_version_json("v1.28.3", "v1.26.0"),
                apiservice_json=_apiservice_available_json(),
            )
            result = compat_check.check_k8s_compatibility()

        assert result.ok is True
        assert not any("版本偏差" in w for w in result.warnings), \
            f"skew==2 不应告警: {result.warnings}"


# ════════════════════════════════════════════════════════════════════
#  场景 8: 版本字符串解析 + 旧版 kubectl 文本输出兼容
# ════════════════════════════════════════════════════════════════════

class TestVersionParsing:
    """场景 8: _parse_k8s_version 多格式解析 + 旧版 kubectl 文本回退路径"""

    @pytest.mark.unit
    @pytest.mark.p0
    @pytest.mark.parametrize("raw,expected", [
        ("v1.28.3", (1, 28)),
        ("1.27", (1, 27)),
        ("v1.19.0-beta.1", (1, 19)),
        ("v2.0.1", (2, 0)),
    ])
    def test_parse_valid_versions(self, raw, expected):
        assert compat_check._parse_k8s_version(raw) == expected

    @pytest.mark.unit
    @pytest.mark.p0
    @pytest.mark.parametrize("raw", [None, "", "   ", "invalid", "abc"])
    def test_parse_invalid_versions_returns_none(self, raw):
        assert compat_check._parse_k8s_version(raw) is None

    @pytest.mark.unit
    @pytest.mark.p1
    def test_legacy_kubectl_text_output_fallback(self):
        """旧版 kubectl（-o json 不可用）回退到文本解析路径"""
        legacy_text = (
            "Client Version: v1.28.1\n"
            "Server Version: v1.28.3\n"
        )
        with patch.object(compat_check.subprocess, "run") as mock_run:
            mock_run.side_effect = _make_kubectl_mock(
                version_json=None,        # -o json 失败 → 触发回退
                version_text=legacy_text,  # 文本路径成功
                apiservice_json=_apiservice_available_json(),
            )
            result = compat_check.check_k8s_compatibility()

        assert result.ok is True, "旧版文本解析应正常工作"
        assert result.k8s_server_version == "v1.28.3"
        assert result.k8s_client_version == "v1.28.1"
        assert result.errors == []


# ════════════════════════════════════════════════════════════════════
#  补充: 数据结构不变量守护
# ════════════════════════════════════════════════════════════════════

class TestResultDataclass:
    """守护 CompatibilityCheckResult 的契约不变量"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_ok_property_reflects_errors(self):
        """ok 属性必须等价于 errors 为空"""
        from compat_check import CompatibilityCheckResult

        r1 = CompatibilityCheckResult(
            k8s_server_version="v1.28.3", k8s_client_version="v1.28.1",
            metrics_api_registered=True, metrics_api_available=True,
        )
        assert r1.ok is True

        r2 = CompatibilityCheckResult(
            k8s_server_version=None, k8s_client_version=None,
            metrics_api_registered=False, metrics_api_available=False,
            errors=["some error"],
        )
        assert r2.ok is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_to_dict_contains_all_fields(self):
        """to_dict 必须包含所有契约字段（被报告脚本依赖）"""
        from compat_check import CompatibilityCheckResult

        r = CompatibilityCheckResult(
            k8s_server_version="v1.28.3",
            k8s_client_version="v1.28.1",
            metrics_api_registered=True,
            metrics_api_available=True,
            errors=[],
            warnings=["w1"],
        )
        d = r.to_dict()
        required = {
            "k8s_server_version", "k8s_client_version",
            "metrics_api_registered", "metrics_api_available",
            "ok", "errors", "warnings",
        }
        assert required.issubset(d.keys()), f"缺失字段: {required - d.keys()}"
        assert d["ok"] is True
        assert d["warnings"] == ["w1"]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_print_compat_result_does_not_raise(self):
        """print_compat_result 在各种状态下不应抛异常"""
        from compat_check import CompatibilityCheckResult

        # 正常
        compat_check.print_compat_result(CompatibilityCheckResult(
            k8s_server_version="v1.28.3", k8s_client_version="v1.28.1",
            metrics_api_registered=True, metrics_api_available=True,
        ))
        # 有告警
        compat_check.print_compat_result(CompatibilityCheckResult(
            k8s_server_version=None, k8s_client_version=None,
            metrics_api_registered=False, metrics_api_available=False,
            warnings=["w1", "w2"],
        ))
        # 有错误
        compat_check.print_compat_result(CompatibilityCheckResult(
            k8s_server_version="v1.18.0", k8s_client_version="v1.18.0",
            metrics_api_registered=False, metrics_api_available=False,
            errors=["e1"],
        ))
