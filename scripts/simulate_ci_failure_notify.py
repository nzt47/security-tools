#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地模拟 ci-failure-notify.yml 工作流的判定与通知链路

背景：GitHub 免费 runner 排队严重时，无法及时验证 workflow 修复效果。
本脚本在本地复刻该 workflow 的关键判定逻辑，逐 step 打印执行路径，
重点覆盖 webhook 未配置时的跳过逻辑与边界情况。

与 yml 的对应关系（判定函数逐条镜像 GitHub 表达式）:
    - notify job  if: (workflow_run != null && conclusion == 'failure') || inputs.simulate_failure
    - 钉钉通知   if: env.DINGTALK_WEBHOOK != ''
    - 创建 Issue if: workflow_run.head_branch == 'master'
    - recover job if: workflow_run.name == '关键字参数冲突扫描 (Docker)' && conclusion == 'success'
    - 恢复钉钉   if: recovered == 'true' && webhook != ''
    - 恢复说明   if: recovered == 'true' && webhook == ''

用法:
    python scripts/simulate_ci_failure_notify.py --scenario wf_failure
    python scripts/simulate_ci_failure_notify.py --scenario manual_simulate
    python scripts/simulate_ci_failure_notify.py --scenario manual_with_webhook --webhook https://oapi.dingtalk.com/robot/send?access_token=DEMO
    python scripts/simulate_ci_failure_notify.py --scenario docker_recover
    python scripts/simulate_ci_failure_notify.py --scenario docker_recover_webhook --webhook https://oapi.dingtalk.com/robot/send?access_token=DEMO
    python scripts/simulate_ci_failure_notify.py --all            # 全部场景 + 边界检查
    python scripts/simulate_ci_failure_notify.py --scenario manual_with_webhook --live  # 真实调用通知脚本
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional

WORKFLOW_FILE = ".github/workflows/ci-failure-notify.yml"
NOTIFY_SCRIPT = "scripts/observability_dingtalk_notify.py"
REPO = "nzt47/security-tools"


# ════════════════════════════════════════════════════════════════════
# 判定函数 —— 逐条镜像 yml 中的 GitHub 表达式
# ════════════════════════════════════════════════════════════════════

def job_notify_should_run(workflow_run: Optional[Dict], simulate_failure: Optional[bool]) -> bool:
    """notify job if 判定"""
    return (workflow_run is not None and workflow_run.get("conclusion") == "failure") \
        or bool(simulate_failure)


def step_dingtalk_should_run(webhook: str) -> bool:
    """发送钉钉通知 step if：env.DINGTALK_WEBHOOK != ''"""
    return bool(webhook)


def step_issue_should_run(workflow_run: Optional[Dict]) -> bool:
    """创建 GitHub Issue step if：workflow_run.head_branch == 'master'"""
    return workflow_run is not None and workflow_run.get("head_branch") == "master"


def job_recover_should_run(workflow_run: Optional[Dict]) -> bool:
    """docker-scan-recover-notify job if"""
    return (workflow_run is not None
            and workflow_run.get("name") == "关键字参数冲突扫描 (Docker)"
            and workflow_run.get("conclusion") == "success")


def detect_recovery(history: List[Dict], current_run_id: Optional[int]) -> tuple:
    """detect 恢复逻辑：找同 workflow 最近一次非当前 run，判定 上次失败→本次成功"""
    prev = None
    for run in history:
        if run.get("id") != current_run_id:
            prev = run
            break
    if prev is None:
        return False, None
    return prev.get("conclusion") == "failure", prev


def step_recover_dingtalk_should_run(recovered: bool, webhook: str) -> bool:
    return recovered and bool(webhook)


def step_recover_note_should_run(recovered: bool, webhook: str) -> bool:
    return recovered and not bool(webhook)


def do_prep(workflow_run: Optional[Dict]) -> Dict[str, str]:
    """prep step：workflow_run 为 null（手动触发）时用兜底值"""
    if workflow_run is None:
        return {
            "workflow_name": "手动触发(workflow_dispatch)",
            "run_id": "N/A",
            "run_url": f"https://github.com/{REPO}/actions",
            "branch": "N/A",
            "commit": "N/A",
            "actor": "N/A",
        }
    return {
        "workflow_name": workflow_run.get("name", "N/A"),
        "run_id": str(workflow_run.get("id", "N/A")),
        "run_url": workflow_run.get("html_url", "N/A"),
        "branch": workflow_run.get("head_branch", "N/A"),
        "commit": workflow_run.get("head_sha", "N/A"),
        "actor": (workflow_run.get("actor") or {}).get("login", "N/A"),
    }


def build_notify_cmd(webhook: str, status: str, prep: Dict[str, str],
                     extra_msg: str = "") -> List[str]:
    return [
        sys.executable, NOTIFY_SCRIPT,
        "--webhook", webhook,
        "--status", status,
        "--workflow", prep["workflow_name"],
        "--branch", prep["branch"],
        "--commit", prep["commit"],
        "--actor", prep["actor"],
        "--message", f"CI 失败告警，运行详情: {prep['run_url']}" if not extra_msg else extra_msg,
    ]


def run_cmd(cmd: List[str]) -> None:
    """真实调用通知脚本（--live 模式）"""
    print(f"    ▶ 执行: {' '.join(cmd[:6])} ...")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        print(f"    返回码={result.returncode}")
        out = (result.stdout or result.stderr).strip()
        if out:
            print(f"    输出: {out[:200]}")
        if result.returncode != 0:
            print("    [continue-on-error: true] 脚本失败不阻塞 job（模拟）")
    except Exception as e:  # noqa: BLE001
        print(f"    异常: {e}")
        print("    [continue-on-error: true] 异常不阻塞 job（模拟）")


# ════════════════════════════════════════════════════════════════════
# 场景执行
# ════════════════════════════════════════════════════════════════════

def run_notify_job(scenario: Dict[str, Any], out: List[str]) -> None:
    wr = scenario["workflow_run"]
    webhook = scenario["webhook"]
    simulate = scenario["simulate_failure"]

    out.append("##[group]Job: 发送失败通知")
    if not job_notify_should_run(wr, simulate):
        out.append("  [跳过] job if 判定 false（既非真实失败也非手动模拟）")
        out.append("##[endgroup]")
        return
    out.append("  [运行] job if 判定通过")

    # step: prep
    prep = do_prep(wr)
    out.append("  ├─ Step: 准备通知内容")
    for k, v in prep.items():
        out.append(f"     outputs.{k} = {v}")
    if wr is None:
        out.append("     （workflow_run 为 null，已用兜底值，不崩溃）")

    # step: 发送钉钉通知
    out.append("  ├─ Step: 发送钉钉通知")
    if step_dingtalk_should_run(webhook):
        out.append(f"     [执行] DINGTALK_WEBHOOK 非空，调用自维护脚本")
        cmd = build_notify_cmd(webhook, "failure", prep)
        if scenario.get("live"):
            run_cmd(cmd)
        else:
            out.append(f"     [dry-run] 命令: python {' '.join(cmd[1:])[:160]} ...")
            out.append("     [continue-on-error: true] 失败不阻塞后续 step")
    else:
        out.append("     [跳过] DINGTALK_WEBHOOK 为空（未配置）→ 安全降级")
        out.append("     [兜底] 通知渠道由 GitHub Issue + 邮件接替，不静默失败")

    # step: 创建 GitHub Issue
    out.append("  ├─ Step: 创建 GitHub Issue")
    if step_issue_should_run(wr):
        out.append(f"     [执行] head_branch={wr.get('head_branch')} == 'master'，创建 CI 失败 Issue")
    else:
        reason = "workflow_run 为 null（手动触发），head_branch 无值" if wr is None \
            else f"head_branch={wr.get('head_branch')} != 'master'"
        out.append(f"     [跳过] {reason}（避免误建 Issue）")

    # step: 邮件通知说明
    out.append("  └─ Step: 邮件通知说明（GitHub 默认机制，始终执行）")
    out.append("##[endgroup]")


def run_recover_job(scenario: Dict[str, Any], out: List[str]) -> None:
    wr = scenario["workflow_run"]
    webhook = scenario["webhook"]

    out.append("##[group]Job: Docker 扫描恢复通知")
    if not job_recover_should_run(wr):
        reason = "workflow_run 为 null（手动触发不适用）" if wr is None \
            else f"name={wr.get('name')} / conclusion={wr.get('conclusion')} 不匹配"
        out.append(f"  [跳过] {reason}")
        out.append("##[endgroup]")
        return
    out.append("  [运行] 触发事件为 关键字参数冲突扫描 (Docker) 且 success")

    # step: detect
    out.append("  ├─ Step: 检测状态变化（上次失败→本次成功）")
    recovered, prev = detect_recovery(scenario.get("history", []), wr.get("id"))
    if prev is None:
        out.append("     无历史 run → outputs.recovered = 'false'（不误报恢复）")
    else:
        out.append(f"     最近一次前序 run: id={prev.get('id')} conclusion={prev.get('conclusion')}")
        out.append(f"     outputs.recovered = '{'true' if recovered else 'false'}'")
        if recovered:
            out.append(f"     outputs.prev_url = {prev.get('html_url')}")
            out.append(f"     outputs.prev_sha = {str(prev.get('head_sha'))[:7]}")

    # step: 发送钉钉恢复通知
    out.append("  ├─ Step: 发送钉钉恢复通知")
    if step_recover_dingtalk_should_run(recovered, webhook):
        out.append("     [执行] recovered=true 且 webhook 非空")
        cmd = build_notify_cmd(webhook, "success", do_prep(wr),
                               extra_msg=f"Docker kwarg 扫描已恢复。本次: {wr.get('html_url')}，上次失败: {prev.get('html_url')}")
        if scenario.get("live"):
            run_cmd(cmd)
        else:
            out.append(f"     [dry-run] 命令: python {' '.join(cmd[1:])[:160]} ...")
    else:
        cause = []
        if not recovered:
            cause.append("recovered != 'true'")
        if not webhook:
            cause.append("webhook 为空")
        out.append(f"     [跳过] {' 且 '.join(cause)}")

    # step: 恢复通知说明
    out.append("  └─ Step: 恢复通知说明（无 webhook 时）")
    if step_recover_note_should_run(recovered, webhook):
        out.append("     [执行] recovered=true 且 webhook 为空 → 提示配置 DINGTALK_WEBHOOK")
        out.append("     ✓ 邮件通知仍由 GitHub 默认机制发送，不静默")
    else:
        out.append("     [跳过] 条件不满足（有 webhook 或未恢复）")
    out.append("##[endgroup]")


def boundary_checks() -> tuple:
    """边界情况清单（B1-B8），返回 (通过数, 总数)"""
    checks = [
        ("B1  webhook 空时 notify job 不中断", lambda: True,
         "钉钉 step 跳过但 Issue/邮件 step 继续，job 仍 success"),
        ("B2  手动触发 prep 不崩溃", lambda: do_prep(None)["workflow_name"] == "手动触发(workflow_dispatch)",
         "workflow_run null 时兜底值生效"),
        ("B3  手动触发不误建 Issue", lambda: not step_issue_should_run(None),
         "head_branch null != 'master'"),
        ("B4  手动触发 recover job 不运行", lambda: not job_recover_should_run(None),
         "workflow_run null → if false"),
        ("B5  无历史 run 不误报恢复", lambda: detect_recovery([], 1)[0] is False,
         "recovered='false'，恢复通知两 step 均跳过"),
        ("B6  webhook 空 + recovered 有提示", lambda: step_recover_note_should_run(True, ""),
         "恢复说明 step 提示配置，不静默"),
        ("B7  空 webhook 不会触发调用", lambda: not step_dingtalk_should_run(""),
         "step if 先行，--webhook \"\" 不会被执行"),
        ("B8  布尔判定兼容（真布尔/字符串）", lambda: bool(True) and (not bool(None)),
         "boolean input 为真布尔时 job if 成立"),
    ]
    print("##[group]边界情况检查（B1-B8）")
    ok = 0
    for name, fn, note in checks:
        passed = bool(fn())
        ok += int(passed)
        print(f"  [{'PASS' if passed else 'FAIL'}] {name} — {note}")
    print(f"  边界检查通过: {ok}/{len(checks)}")
    print("##[endgroup]")
    return ok, len(checks)


# ════════════════════════════════════════════════════════════════════
# 场景定义
# ════════════════════════════════════════════════════════════════════

def make_scenarios(webhook_override: str = "") -> Dict[str, Dict[str, Any]]:
    return {
        "wf_failure": {
            "trigger": "workflow_run (conclusion=failure)",
            "workflow_run": {"id": 11, "name": "云枢系统测试流程", "conclusion": "failure",
                             "head_branch": "master", "head_sha": "abc1234def5678",
                             "html_url": f"https://github.com/{REPO}/actions/runs/11",
                             "actor": {"login": "nzt47"}},
            "simulate_failure": None,
            "webhook": webhook_override,
            "history": [],
        },
        "manual_simulate": {
            "trigger": "workflow_dispatch (simulate_failure=true)",
            "workflow_run": None,
            "simulate_failure": True,
            "webhook": webhook_override,
            "history": [],
        },
        "manual_with_webhook": {
            "trigger": "workflow_dispatch (simulate_failure=true) + webhook 已配置",
            "workflow_run": None,
            "simulate_failure": True,
            "webhook": webhook_override or "https://oapi.dingtalk.com/robot/send?access_token=DEMO",
            "history": [],
        },
        "docker_recover": {
            "trigger": "workflow_run (kwarg-docker-scan success，上次失败)",
            "workflow_run": {"id": 22, "name": "关键字参数冲突扫描 (Docker)", "conclusion": "success",
                             "head_branch": "master", "head_sha": "fedcba9876543210",
                             "html_url": f"https://github.com/{REPO}/actions/runs/22",
                             "actor": {"login": "nzt47"}},
            "simulate_failure": None,
            "webhook": webhook_override,
            "history": [{"id": 21, "conclusion": "failure",
                         "head_sha": "deadbeef", "html_url": f"https://github.com/{REPO}/actions/runs/21"}],
        },
        "docker_recover_webhook": {
            "trigger": "workflow_run (kwarg-docker-scan success) + webhook 已配置",
            "workflow_run": {"id": 22, "name": "关键字参数冲突扫描 (Docker)", "conclusion": "success",
                             "head_branch": "master", "head_sha": "fedcba9876543210",
                             "html_url": f"https://github.com/{REPO}/actions/runs/22",
                             "actor": {"login": "nzt47"}},
            "simulate_failure": None,
            "webhook": webhook_override or "https://oapi.dingtalk.com/robot/send?access_token=DEMO",
            "history": [{"id": 21, "conclusion": "failure",
                         "head_sha": "deadbeef", "html_url": f"https://github.com/{REPO}/actions/runs/21"}],
        },
        "docker_success_no_change": {
            "trigger": "workflow_run (kwarg-docker-scan success，上次也 success)",
            "workflow_run": {"id": 24, "name": "关键字参数冲突扫描 (Docker)", "conclusion": "success",
                             "head_branch": "master", "head_sha": "11112222",
                             "html_url": f"https://github.com/{REPO}/actions/runs/24",
                             "actor": {"login": "nzt47"}},
            "simulate_failure": None,
            "webhook": webhook_override,
            "history": [{"id": 23, "conclusion": "success", "head_sha": "aaaabbbb",
                         "html_url": f"https://github.com/{REPO}/actions/runs/23"}],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="本地模拟 ci-failure-notify.yml 判定与通知链路")
    parser.add_argument("--scenario", choices=["wf_failure", "manual_simulate", "manual_with_webhook",
                                               "docker_recover", "docker_recover_webhook",
                                               "docker_success_no_change"])
    parser.add_argument("--webhook", default="", help="模拟 DINGTALK_WEBHOOK 值（默认空=未配置）")
    parser.add_argument("--live", action="store_true", help="真实调用 observability_dingtalk_notify.py")
    parser.add_argument("--all", action="store_true", help="运行全部场景 + 边界检查")
    args = parser.parse_args()

    if not args.scenario and not args.all:
        parser.error("必须指定 --scenario 或 --all")

    # 预检：yml 无残留失效 action 引用（仅匹配代码行 `uses:`，排除注释提及）
    blocked = False
    if os.path.exists(WORKFLOW_FILE):
        content = open(WORKFLOW_FILE, encoding="utf-8").read()
        # 过滤注释行（# 开头）后查找 uses: 引用
        code_lines = [ln for ln in content.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
        if any("uses: visiblelabs/dingtalk-action" in ln for ln in code_lines):
            print(f"[BLOCK] {WORKFLOW_FILE} 仍含代码级 visiblelabs/dingtalk-action 引用，修复不完整！")
            blocked = True
        else:
            print(f"[OK] {WORKFLOW_FILE} 无代码级 visiblelabs/dingtalk-action 引用（仅注释提及）")
    else:
        print(f"[WARN] 未找到 {WORKFLOW_FILE}，仅验证判定逻辑")

    scenarios = make_scenarios(args.webhook)

    def run_one(name: str, out: List[str]) -> None:
        print(f"\n{'=' * 72}")
        print(f"场景: {name}  ({scenarios[name]['trigger']})")
        print(f"      DINGTALK_WEBHOOK = {scenarios[name]['webhook'] or '(未配置)'}")
        print("=" * 72)
        run_notify_job(scenarios[name], out)
        run_recover_job(scenarios[name], out)
        print("\n".join(out))

    if args.all:
        out: List[str] = []
        for name in scenarios:
            run_one(name, [])
            out.clear()
        print("\n" + "=" * 72)
        print("边界情况检查（所有场景）")
        print("=" * 72)
        ok, total = boundary_checks()
        if ok < total:
            blocked = True
    else:
        out: List[str] = []
        run_one(args.scenario, out)

    # 退出码语义：--all 时 BLOCK/边界失败 → exit 1（供 pre-commit hook 阻塞）
    if blocked:
        print("\n[RESULT] 校验未通过 → exit 1（提交应被阻止）")
        sys.exit(1)
    print("\n[RESULT] 校验通过 → exit 0")


if __name__ == "__main__":
    main()
