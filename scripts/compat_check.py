#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
K8s 兼容性校验共享模块 — 供巡检/预热脚本复用

【不易】守护不变量：hard error 仅针对"已确认不兼容"（版本过低/API 未注册）；
       未知状态（权限不足/无法获取）必须降级为告警，不得阻断巡检/预热。
【变易】兼容新旧 kubectl 输出（-o json 与文本）、metrics API NotFound/Forbidden 区分。
【简易】subprocess.run 调用 kubectl，零第三方依赖，纯标准库。

覆盖 8 个边界场景（对应 tests/unit/test_compat_check.py）：
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
import re
import subprocess
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

# ── 版本阈值常量 ──
MIN_K8S_VERSION: Tuple[int, int] = (1, 19)      # 低于此版本 → hard error
WARN_K8S_VERSION: Tuple[int, int] = (1, 22)     # 低于此版本 → 告警
MAX_VERSION_SKEW: int = 2                        # client/server 最大允许 minor 偏差

# metrics API 的 APIService 名称（kubectl get apiservice 查询目标）
_METRICS_APISERVICE = "v1beta1.metrics.k8s.io"

# kubectl 命令模板
_VERSION_JSON_CMD = ["kubectl", "version", "-o", "json"]
_VERSION_TEXT_CMD = ["kubectl", "version"]
_APISERVICE_CMD = ["kubectl", "get", "apiservice", _METRICS_APISERVICE, "-o", "json"]

# subprocess 调用参数（mock 测试通过 patch.object(subprocess, "run") 拦截，
# 只匹配命令内容，capture_output/text 等 kwargs 由 side_effect 忽略）
_RUN_TIMEOUT = 15  # 秒


@dataclass
class CompatibilityCheckResult:
    """K8s 兼容性检查结果

    ok 属性 = errors 为空（【不易】契约：hard error 全部进 errors）
    """

    k8s_server_version: Optional[str] = None
    k8s_client_version: Optional[str] = None
    metrics_api_registered: bool = False
    metrics_api_available: bool = False
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """兼容性是否通过：仅 errors 为空时 ok（【不易】契约不变量）"""
        return len(self.errors) == 0

    def to_dict(self) -> dict:
        """序列化为 dict（报告脚本依赖字段集合，见测试 test_to_dict_contains_all_fields）"""
        return {
            "k8s_server_version": self.k8s_server_version,
            "k8s_client_version": self.k8s_client_version,
            "metrics_api_registered": self.metrics_api_registered,
            "metrics_api_available": self.metrics_api_available,
            "ok": self.ok,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


def _parse_k8s_version(raw: Optional[str]) -> Optional[Tuple[int, int]]:
    """解析 K8s 版本字符串 → (major, minor)

    兼容 "v1.28.3" / "1.27" / "v1.19.0-beta.1" / "v2.0.1" 等格式；
    无法解析（None/空串/纯文本）返回 None。
    """
    if not raw or not isinstance(raw, str):
        return None
    m = re.search(r"v?(\d+)\.(\d+)", raw.strip())
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)))


def _run_kubectl(cmd: List[str]) -> subprocess.CompletedProcess:
    """执行 kubectl 命令（统一超时与编码参数）"""
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=_RUN_TIMEOUT,
    )


def _fetch_versions(result: CompatibilityCheckResult) -> None:
    """获取 server/client 版本：优先 -o json，失败回退文本输出

    【不易】kubectl 不可用（FileNotFoundError）降级为告警，不抛异常。
    """
    # 路径 1：kubectl version -o json（优先）
    try:
        proc = _run_kubectl(_VERSION_JSON_CMD)
        if proc.returncode == 0 and proc.stdout.strip():
            data = json.loads(proc.stdout)
            result.k8s_server_version = (data.get("serverVersion", {}) or {}).get("gitVersion")
            result.k8s_client_version = (data.get("clientVersion", {}) or {}).get("gitVersion")
            if result.k8s_server_version or result.k8s_client_version:
                return
        # -o json 失败/无输出 → 回退到文本路径
    except FileNotFoundError:
        result.warnings.append("无法获取 kubectl 版本：kubectl 未安装或不在 PATH 中")
        return
    except (json.JSONDecodeError, ValueError, OSError) as e:
        result.warnings.append("无法解析 kubectl version -o json 输出，尝试文本路径: %s" % (e,))

    # 路径 2：kubectl version（旧版文本输出回退）
    try:
        proc = _run_kubectl(_VERSION_TEXT_CMD)
        if proc.returncode == 0 and proc.stdout:
            server_m = re.search(r"Server Version:\s*(\S+)", proc.stdout)
            client_m = re.search(r"Client Version:\s*(\S+)", proc.stdout)
            if server_m:
                result.k8s_server_version = server_m.group(1)
            if client_m:
                result.k8s_client_version = client_m.group(1)
        else:
            result.warnings.append("无法获取 kubectl 版本（命令返回非零或输出为空）")
    except FileNotFoundError:
        result.warnings.append("无法获取 kubectl 版本：kubectl 未安装或不在 PATH 中")
    except (ValueError, OSError) as e:
        result.warnings.append("无法获取 kubectl 版本: %s" % (e,))


def _check_metrics_api(result: CompatibilityCheckResult) -> None:
    """检查 metrics API 是否注册且可用

    【不易】NotFound（已确认未注册）→ hard error；
           Forbidden（权限不足，in-cluster 限权常见）→ 降级告警，不阻断。
    """
    try:
        proc = _run_kubectl(_APISERVICE_CMD)
        if proc.returncode == 0:
            result.metrics_api_registered = True
            try:
                data = json.loads(proc.stdout)
                conditions = (data.get("status", {}) or {}).get("conditions", [])
                result.metrics_api_available = any(
                    c.get("type") == "Available" and str(c.get("status")).lower() == "true"
                    for c in conditions
                )
            except (json.JSONDecodeError, ValueError, AttributeError):
                result.metrics_api_available = False
        else:
            stderr = (proc.stderr or "").lower()
            if "notfound" in stderr:
                # 【不易】已确认未注册 → hard error（阻断）
                result.metrics_api_registered = False
                result.errors.append(
                    "metrics API 未注册: %s 不存在（APIService 未部署）" % (_METRICS_APISERVICE,)
                )
            elif "forbidden" in stderr:
                # 【变易】权限不足 → 降级告警（in-cluster 限权不得误判为未注册）
                result.warnings.append("权限不足：无法查询 metrics API（APIService 状态未知，跳过）")
            else:
                result.warnings.append(
                    "无法查询 metrics API（returncode=%s）：%s"
                    % (proc.returncode, (proc.stderr or "").strip()[:120])
                )
    except FileNotFoundError:
        result.warnings.append("kubectl 不可用，无法检查 metrics API")
    except (ValueError, OSError) as e:
        result.warnings.append("检查 metrics API 失败: %s" % (e,))


def _judge_versions(result: CompatibilityCheckResult) -> None:
    """版本阈值判定：过低 hard error / 偏低告警 / 偏差告警"""
    server_ver = _parse_k8s_version(result.k8s_server_version)
    client_ver = _parse_k8s_version(result.k8s_client_version)

    if server_ver is not None:
        if server_ver < MIN_K8S_VERSION:
            # 【不易】版本过低（已确认不兼容）→ hard error
            result.errors.append(
                "K8s 版本过低: %s（最低要求 1.19）" % (result.k8s_server_version,)
            )
        elif server_ver < WARN_K8S_VERSION:
            result.warnings.append(
                "K8s 版本偏低: %s（建议升级至 1.22）" % (result.k8s_server_version,)
            )

    # client/server 版本偏差（> MAX_VERSION_SKEW minor → 告警）
    if server_ver is not None and client_ver is not None:
        server_num = server_ver[0] * 100 + server_ver[1]
        client_num = client_ver[0] * 100 + client_ver[1]
        if abs(server_num - client_num) > MAX_VERSION_SKEW:
            result.warnings.append(
                "client/server 版本偏差过大: server=%s client=%s（允许偏差 %d minor）"
                % (result.k8s_server_version, result.k8s_client_version, MAX_VERSION_SKEW)
            )


def check_k8s_compatibility() -> CompatibilityCheckResult:
    """执行 K8s 兼容性检查（巡检/预热脚本入口）"""
    result = CompatibilityCheckResult()
    _fetch_versions(result)
    _check_metrics_api(result)
    _judge_versions(result)
    return result


def print_compat_result(result: CompatibilityCheckResult) -> None:
    """人类可读输出（任何状态下不抛异常，见 test_print_compat_result_does_not_raise）"""
    print("K8s 兼容性检查: %s" % ("通过" if result.ok else "失败"))
    print("  server=%s client=%s metrics_api_registered=%s metrics_api_available=%s" % (
        result.k8s_server_version, result.k8s_client_version,
        result.metrics_api_registered, result.metrics_api_available,
    ))
    for e in result.errors:
        print("  [ERROR] %s" % (e,))
    for w in result.warnings:
        print("  [WARN] %s" % (w,))


if __name__ == "__main__":
    print_compat_result(check_k8s_compatibility())
