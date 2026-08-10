"""run_ci_guard 输出契约校验 —— --validate 分支运行时依赖

Why:
- ci-guard-runner workflow 用 `run_ci_guard.py --json` 的输出被下游 json.load 消费,
  字段结构(契约)一旦破坏, 解析与展示逻辑将静默错乱。
- validate_report() 在输出前校验契约, 失败返回错误列表, 不抛异常。
- 本模块曾被误删(仅 .pyc 幸存, 从未入库), 2026-08-05 按 run_ci_guard.py
  实际输出结构重建。参见 docs/observability/ci_hidden_failure_fix_report_20260805.md

用法:
    from ci_guard_types import validate_report
    errs = validate_report(report)
    if errs: ...  # 非空即契约违规
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

# 契约常量(与 run_ci_guard.py 保持一致, 变更须同步)
_TOOL = "run_ci_guard"
_ALLOWED_STEPS = {"detect", "rollback_sim", "guard_verify"}
_ALLOWED_OVERALL_STATUS = {"pass", "fail"}
_REQUIRED_STEP_KEYS = ("step", "status", "exit_code", "details")


def _err(field: str, msg: str) -> str:
    return f"[契约校验] {field}: {msg}"


def validate_report(report: Any) -> list[str]:
    """校验 run_ci_guard 输出报告结构, 返回违规描述列表(空 = 通过)

    只做结构/类型/枚举校验, 不做业务语义判断(是否阻止合并由 exit_code 决定)。
    """
    errs: list[str] = []

    if not isinstance(report, dict):
        return [_err("report", "必须是 dict")]

    # tool 标识
    if report.get("tool") != _TOOL:
        errs.append(_err("tool", f"应为 {_TOOL!r}, 实际 {report.get('tool')!r}"))

    # timestamp 可解析
    ts = report.get("timestamp")
    if not isinstance(ts, str):
        errs.append(_err("timestamp", "必须是字符串"))
    else:
        try:
            datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            errs.append(_err("timestamp", f"不是合法 ISO 时间: {ts!r}"))

    # steps 结构
    steps = report.get("steps")
    if not isinstance(steps, list) or not steps:
        errs.append(_err("steps", "必须是非空列表"))
    else:
        for i, s in enumerate(steps):
            if not isinstance(s, dict):
                errs.append(_err(f"steps[{i}]", "必须是 dict"))
                continue
            for k in _REQUIRED_STEP_KEYS:
                if k not in s:
                    errs.append(_err(f"steps[{i}]", f"缺少字段 {k!r}"))
            if not isinstance(s.get("exit_code"), int):
                errs.append(_err(f"steps[{i}].exit_code", "必须是整数"))
            if s.get("step") not in _ALLOWED_STEPS:
                errs.append(_err(
                    f"steps[{i}].step",
                    f"未知步骤 {s.get('step')!r}, 允许 {sorted(_ALLOWED_STEPS)}"))

    # overall 结构
    overall = report.get("overall")
    if not isinstance(overall, dict):
        errs.append(_err("overall", "必须是 dict"))
    else:
        st = overall.get("status")
        code = overall.get("exit_code")
        if st not in _ALLOWED_OVERALL_STATUS:
            errs.append(_err(
                "overall.status",
                f"未知状态 {st!r}, 允许 {sorted(_ALLOWED_OVERALL_STATUS)}"))
        if not isinstance(code, int):
            errs.append(_err("overall.exit_code", "必须是整数"))
        elif (st == "pass" and code != 0) or (st == "fail" and code == 0):
            errs.append(_err(
                "overall",
                f"status/exit_code 不一致: status={st!r} exit_code={code!r}"))

    # overall.exit_code 与 guard_verify 步骤一致
    # 注意：steps 元素可能为非 dict（前面已记录错误），此处须过滤，否则 AttributeError
    if isinstance(steps, list) and steps:
        guard = [s for s in steps if isinstance(s, dict) and s.get("step") == "guard_verify"]
        if guard and isinstance(overall, dict):
            gcode = guard[-1].get("exit_code")
            if overall.get("exit_code") != gcode:
                errs.append(_err(
                    "overall.exit_code",
                    f"与 guard_verify 步骤不一致: overall={overall.get('exit_code')!r} "
                    f"guard_verify={gcode!r}"))

    return errs


if __name__ == "__main__":
    import json
    import sys

    # 支持管道: echo '{"..."}' | python ci_guard_types.py
    data = json.load(sys.stdin)
    issues = validate_report(data)
    if issues:
        for e in issues:
            print(e, file=sys.stderr)
        sys.exit(1)
    print("契约校验通过", file=sys.stderr)
