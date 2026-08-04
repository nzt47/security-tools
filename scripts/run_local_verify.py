#!/usr/bin/env python3
"""本地 verify 脚本 — 模拟 Ansible site.yml --tags verify 的验证流程

用途:
    当 ansible-playbook 不可用或测试机不可达时, 在本地（或测试机上手动）执行
    等效的 verify 流程, 诚实报告每项检查的通过/跳过/失败状态。

对应 Ansible site.yml verify tag 的 6 个验证步骤:
    1. import 校验: agent.config_validation 可导入
    2. validate_search_instance: 5 个边界用例
    3. HTTP /metrics 端点可达
    4. config_metrics_exporter 端口 9101 可达
    5. config.yaml 篡改降级验证（verify_config_tamper.py）
    6. systemd 服务状态（仅 Linux 测试机, 本地 Windows 跳过）

运行:
    python scripts/run_local_verify.py
    python scripts/run_local_verify.py --app-host 127.0.0.1 --app-port 5678

【不易】诚实报告: 服务未运行时标记 SKIP, 不假装通过
【变易】参数化主机端口, 支持本地/远程测试机
【简易】单文件, 复用现有验证脚本, 不重复造轮子
"""
from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ════════════════════════════════════════════════════════════
#  验证结果类型
# ════════════════════════════════════════════════════════════
# (status, detail)  status ∈ {PASS, FAIL, SKIP}
VerifyResult = Tuple[str, str]


def _check_import() -> VerifyResult:
    """步骤1: 校验 agent.config_validation 可导入"""
    try:
        from agent.config_validation import (  # noqa: F401
            SEARCH_INSTANCE_VALIDATION_RULES,
            validate_dict_against_rules,
        )
        return ("PASS", "agent.config_validation 导入成功")
    except Exception as e:
        return ("FAIL", f"导入失败: {type(e).__name__}: {e}")


def _check_validate_search_instance() -> VerifyResult:
    """步骤2: validate_search_instance 5 个边界用例"""
    try:
        from agent.server_routes.routes_config import validate_search_instance
    except Exception as e:
        return ("FAIL", f"validate_search_instance 导入失败: {e}")

    cases = [
        ({'name': 'test', 'engine_type': 'tavily', 'timeout': 30}, [], '合法配置'),
        ({'engine_type': 'tavily'}, ['名称不能为空'], '空名称'),
        ({'name': 'test', 'engine_type': 'tavily', 'timeout': 500},
         ['超时必须在 1-300 秒之间'], '超时范围'),
        ({'name': 'test', 'engine_type': 'unknown_engine'},
         ['未知的内置引擎类型'], '未知引擎'),
        ({'name': 'test', 'engine_type': 'custom'},
         ['自定义引擎必须提供 API 端点 URL'], '自定义引擎端点'),
    ]
    failed = []
    for cfg, expect, name in cases:
        errors = validate_search_instance(cfg)
        # 【简易】用子串匹配: errors 中的消息可能含动态后缀(如'未知引擎类型: xxx'),
        # 期望的 expect 是子串, 用 any(e in err) 而非 e in errors(列表元素相等)
        ok = (all(any(e in err for err in errors) for e in expect)
              if expect else (len(errors) == 0))
        if not ok:
            failed.append(f"{name}(期望{expect},实际{errors})")

    if failed:
        return ("FAIL", f"{len(failed)}/5 失败: {'; '.join(failed)}")
    return ("PASS", "validate_search_instance: 5/5 PASS")


def _check_http_metrics(app_host: str, app_port: int) -> VerifyResult:
    """步骤3: HTTP /metrics 端点可达"""
    # 先检测端口是否开放
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(2)
    try:
        sock.connect((app_host, app_port))
    except (socket.error, OSError):
        return ("SKIP", f"app_server 未运行 ({app_host}:{app_port} 不可达)")
    finally:
        sock.close()

    try:
        url = f"http://{app_host}:{app_port}/metrics"
        r = urllib.request.urlopen(url, timeout=5)
        if r.status == 200:
            body = r.read()
            return ("PASS", f"/metrics 返回 200, {len(body)} bytes")
        return ("FAIL", f"/metrics 返回 {r.status}")
    except Exception as e:
        return ("FAIL", f"/metrics 请求失败: {e}")


def _check_metrics_exporter(exporter_port: int) -> VerifyResult:
    """步骤4: config_metrics_exporter 端口 9101 可达"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(2)
    try:
        sock.connect(("127.0.0.1", exporter_port))
        return ("PASS", f"metrics exporter 端口 {exporter_port} 可达")
    except (socket.error, OSError):
        return ("SKIP", f"config_metrics_exporter 未运行 (端口 {exporter_port} 不可达)")
    finally:
        sock.close()


def _check_config_tamper() -> VerifyResult:
    """步骤5: config.yaml 篡改降级验证"""
    script = Path(__file__).resolve().parent / "verify_config_tamper.py"
    if not script.exists():
        return ("FAIL", f"verify_config_tamper.py 不存在: {script}")

    env = {
        **dict(__import__("os").environ),
        "PYTHONIOENCODING": "utf-8",
        "SKILLS_OFFLINE": "1",
    }
    try:
        result = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True, text=True, timeout=60, env=env,
            cwd=str(Path(__file__).resolve().parent.parent),
        )
    except subprocess.TimeoutExpired:
        return ("FAIL", "verify_config_tamper.py 执行超时（60s）")

    # 解析通过率（最后一行 "通过: X/6"）
    output = result.stdout
    pass_line = [l for l in output.splitlines() if "通过:" in l]
    if pass_line:
        try:
            ratio_str = pass_line[-1].split("通过:")[1].strip()
            passed, total = ratio_str.split("/")
            passed, total = int(passed), int(total)
            if passed == total:
                return ("PASS", f"篡改降级验证: {passed}/{total} 全部通过")
            return ("FAIL", f"篡改降级验证: {passed}/{total} 部分失败")
        except (ValueError, IndexError):
            pass

    return ("FAIL", f"verify_config_tamper.py 输出无法解析, exit={result.returncode}")


def _check_systemd() -> VerifyResult:
    """步骤6: systemd 服务状态（仅 Linux）"""
    import platform
    if platform.system() == "Windows":
        return ("SKIP", "Windows 无 systemd, 需在 Linux 测试机执行")

    try:
        for svc in ("yunshu-app", "yunshu-config-metrics"):
            r = subprocess.run(
                ["systemctl", "is-active", svc],
                capture_output=True, text=True, timeout=5,
            )
            state = r.stdout.strip()
            if state != "active":
                return ("FAIL", f"{svc} 状态={state} (期望 active)")
        return ("PASS", "yunshu-app + yunshu-config-metrics 均 active")
    except FileNotFoundError:
        return ("SKIP", "systemctl 不可用")
    except Exception as e:
        return ("FAIL", f"systemctl 检查失败: {e}")


# ════════════════════════════════════════════════════════════
#  主流程
# ════════════════════════════════════════════════════════════
def run_verify(app_host: str, app_port: int, exporter_port: int) -> int:
    print("=" * 80)
    print("本地 verify — 模拟 Ansible site.yml --tags verify")
    print("=" * 80)
    print(f"目标: {app_host}:{app_port} (app) | 127.0.0.1:{exporter_port} (metrics)")
    print()

    checks: List[Tuple[str, VerifyResult]] = [
        ("1. import 校验 (agent.config_validation)", _check_import()),
        ("2. validate_search_instance 5 用例", _check_validate_search_instance()),
        ("3. HTTP /metrics 端点", _check_http_metrics(app_host, app_port)),
        ("4. config_metrics_exporter 端口", _check_metrics_exporter(exporter_port)),
        ("5. config.yaml 篡改降级验证", _check_config_tamper()),
        ("6. systemd 服务状态", _check_systemd()),
    ]

    counts = {"PASS": 0, "FAIL": 0, "SKIP": 0}
    for name, (status, detail) in checks:
        icon = {"PASS": "✅", "FAIL": "❌", "SKIP": "⏭️"}[status]
        print(f"  {icon} [{status}] {name}")
        print(f"        {detail}")
        counts[status] += 1

    print()
    print("-" * 80)
    print(f"汇总: ✅ PASS={counts['PASS']}  ❌ FAIL={counts['FAIL']}  "
          f"⏭️ SKIP={counts['SKIP']}")

    if counts["FAIL"] > 0:
        print("\n【结论】有验证项失败, 需排查")
        return 1
    elif counts["PASS"] >= 2:
        print("\n【结论】核心验证通过 (SKIP 项需启动对应服务后重测)")
        return 0
    else:
        print("\n【结论】通过项不足, 需检查环境")
        return 1


def main():
    parser = argparse.ArgumentParser(
        description="本地 verify 脚本 — 模拟 Ansible verify tag"
    )
    parser.add_argument(
        "--app-host", default="127.0.0.1",
        help="app_server 主机（默认 127.0.0.1）",
    )
    parser.add_argument(
        "--app-port", type=int, default=5678,
        help="app_server 端口（默认 5678）",
    )
    parser.add_argument(
        "--exporter-port", type=int, default=9101,
        help="config_metrics_exporter 端口（默认 9101）",
    )
    args = parser.parse_args()

    sys.exit(run_verify(
        app_host=args.app_host,
        app_port=args.app_port,
        exporter_port=args.exporter_port,
    ))


if __name__ == "__main__":
    main()
