#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量修复 observability.py 中 trackEvent 函数的保留键冲突 bug

【背景】
    模板生成的 agent/<module>/observability.py 中, trackEvent 函数将
    `**(payload or {})` 直接展开传递给 _emit_structured_log()。当 payload
    含有 action / trace_id / duration_ms / level / module_name 等保留键时,
    会与 _emit_structured_log 的显式参数冲突, 触发:
        TypeError: got multiple values for argument

【修复方案】
    在 trackEvent 函数中, 展开 payload 前过滤掉保留键:
        _RESERVED = {"action", "trace_id", "duration_ms", "level", "module_name"}
        safe_payload = {k: v for k, v in (payload or {}).items() if k not in _RESERVED}
    然后将 `**(payload or {})` 替换为 `**safe_payload`。

【适用范围】
    仅修复模板生成的 agent/<module>/observability.py (trackEvent camelCase)。
    手写版本 (agent/skills_mgmt/observability.py, agent/workflow_learning/
    observability.py) 使用 track_event snake_case 且实现不同, 自动跳过。

【安全机制】
    - 正则锚定 (t0 = time.time() + try: + _emit_structured_log(f"track.{...}"))
      精确定位 trackEvent 函数体, 避免误改其他函数。
    - ast.parse() 语法校验: 修改后必须可解析, 否则回滚不写入。
    - 幂等性: 已修复的文件自动跳过, 可重复执行。
    - 失败隔离: 单个文件异常不影响其他文件处理。
    - 显式跳过名单: skills_mgmt (手写 track_event)。

【可观测性说明】
    本脚本为核心业务逻辑节点输出 JSON 结构化日志, 含 trace_id / module_name
    / action / duration_ms 字段, 遵循项目硬约束。
"""
from __future__ import annotations

import ast
import json
import re
import sys
import time
import traceback
import uuid
from pathlib import Path
from typing import Dict, List, Tuple

# ============================================================
# 配置
# ============================================================
PROJECT_ROOT = Path(__file__).resolve().parent.parent
AGENT_DIR = PROJECT_ROOT / "agent"

# 显式跳过名单: 手写版本 (track_event snake_case, 实现不同, 无此 bug)
# - skills_mgmt: 任务明确要求跳过
# - workflow_learning: 同样为手写 track_event, 不匹配模板模式会自动跳过,
#   此处显式声明以便报告中清晰区分。
SKIP_MODULES = {"skills_mgmt", "workflow_learning"}

# 保留键: 与 _emit_structured_log 显式参数冲突的键
# _emit_structured_log(action, *, trace_id, duration_ms, level, **payload)
# 显式参数: action, trace_id, duration_ms, level
# record 中固定键: module_name (也会冲突, 故一并过滤)
# 注意: 此处仅做文档说明, 实际过滤逻辑内联在 INSERTION_LINES 中,
# 以确保插入到各文件 trackEvent 函数内的代码与 _emit_structured_log
# 签名保持一致。

# 锚点正则: 精确匹配 trackEvent 函数体内的
#   `    t0 = time.time()\n    try:\n        _emit_structured_log(\n            f"track.{event_name}",`
# 使用该锚点确保只在 trackEvent 内插入过滤逻辑, 不影响其他函数。
ANCHOR_PATTERN = re.compile(
    r'(    t0 = time\.time\(\)\n)'
    r'(    try:\n        _emit_structured_log\(\n            f"track\.\{event_name\}",)',
    re.MULTILINE,
)

# bug 模式 -> 修复模式 (仅替换首个出现, 即 trackEvent 内的)
BUG_PATTERN = "**(payload or {})"
FIX_PATTERN = "**safe_payload"

# 待插入的过滤逻辑 (函数级缩进 4 空格)
INSERTION_LINES = (
    '    _RESERVED = {"action", "trace_id", "duration_ms", "level", "module_name"}\n'
    '    safe_payload = {k: v for k, v in (payload or {}).items() if k not in _RESERVED}\n'
)


# ============================================================
# 结构化日志 (遵循项目硬约束: JSON / trace_id / module_name / action / duration_ms)
# ============================================================
def _emit_log(action: str, duration_ms: float, **extra) -> None:
    """输出 JSON 结构化日志到 stdout"""
    record = {
        "trace_id": str(uuid.uuid4()),
        "module_name": "fix_track_event_reserved_keys",
        "action": action,
        "duration_ms": round(duration_ms, 2),
        "ts": time.time(),
        **{k: v for k, v in extra.items()
           if k not in {"action", "trace_id", "duration_ms", "module_name"}},
    }
    print(json.dumps(record, ensure_ascii=False, default=str))


# ============================================================
# 核心修复逻辑
# ============================================================
def fix_content(content: str) -> Tuple[str, str]:
    """修复单个文件内容

    返回: (新内容, 状态说明)
        状态说明取值:
          - "fixed"              : 成功修复
          - "skip:no_trackEvent" : 无 trackEvent 函数 (手写版本)
          - "skip:already_fixed" : 已修复过 (幂等)
          - "skip:no_bug_pattern": 无 bug 模式 (异常情况)
          - "skip:no_anchor"     : 锚点未匹配 (文件结构异常, 需人工核实)
    """
    # 1. 模板特征检查: 必须含 trackEvent 函数定义
    if "def trackEvent(" not in content:
        return content, "skip:no_trackEvent"

    # 2. 幂等性检查: 已修复则跳过
    #    (safe_payload 已存在, 且 bug 模式已消失)
    if "safe_payload" in content and BUG_PATTERN not in content:
        return content, "skip:already_fixed"

    # 3. bug 模式检查
    if BUG_PATTERN not in content:
        return content, "skip:no_bug_pattern"

    # 4. 锚点匹配: 在 t0 = time.time() 与 try: 之间插入过滤逻辑
    def _insert(match: re.Match) -> str:
        before = match.group(1)   # '    t0 = time.time()\n'
        try_block = match.group(2)  # '    try:\n        _emit_structured_log(...'
        return before + INSERTION_LINES + try_block

    new_content, n_anchor = ANCHOR_PATTERN.subn(_insert, content, count=1)
    if n_anchor == 0:
        return content, "skip:no_anchor"

    # 5. 替换 bug 模式 -> 修复模式 (count=1, 仅 trackEvent 内首个)
    new_content = new_content.replace(BUG_PATTERN, FIX_PATTERN, 1)

    return new_content, "fixed"


def validate_syntax(content: str, file_path: Path) -> None:
    """用 ast.parse() 校验语法, 失败则抛出 SyntaxError"""
    try:
        ast.parse(content, filename=str(file_path))
    except SyntaxError as e:
        raise SyntaxError(f"语法校验失败: {e}") from e


# ============================================================
# 主流程
# ============================================================
def main() -> int:
    t_start = time.time()
    _emit_log("script.start", 0.0, stage="init")

    # 1. 收集所有 agent/**/observability.py
    candidates: List[Path] = sorted(AGENT_DIR.rglob("observability.py"))
    _emit_log(
        "discover.files",
        0.0,
        total=len(candidates),
        skip_modules=sorted(SKIP_MODULES),
    )

    fixed_files: List[str] = []
    skipped: List[Tuple[str, str]] = []   # (文件, 原因)
    errors: List[Tuple[str, str]] = []    # (文件, 错误信息)

    for path in candidates:
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        module_name = path.parent.name

        # 2. 显式跳过手写版本
        if module_name in SKIP_MODULES:
            skipped.append((rel, f"skip:explicit (module={module_name}, 手写版本)"))
            continue

        t_file = time.time()
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            err = f"{type(e).__name__}: {e}"
            errors.append((rel, err))
            _emit_log("file.read.error", 0.0, file=rel, error=err)
            continue

        # 3. 应用修复
        new_content, status = fix_content(content)
        duration = (time.time() - t_file) * 1000

        if status.startswith("skip"):
            skipped.append((rel, status))
            _emit_log("file.skip", duration, file=rel, reason=status)
            continue

        if status != "fixed":
            # 未预期状态, 记为错误以防遗漏
            errors.append((rel, f"unexpected status: {status}"))
            _emit_log("file.unexpected", duration, file=rel, status=status)
            continue

        # 4. 语法校验 (ast.parse) — 失败则回滚, 不写入
        try:
            validate_syntax(new_content, path)
        except SyntaxError as e:
            err = f"ast.parse 失败: {e}"
            errors.append((rel, err))
            _emit_log("file.validate.error", duration, file=rel, error=err)
            # 回滚: 不写入, 保留原文件
            continue

        # 5. 写回磁盘
        try:
            path.write_text(new_content, encoding="utf-8")
        except OSError as e:
            err = f"写入失败: {type(e).__name__}: {e}"
            errors.append((rel, err))
            _emit_log("file.write.error", duration, file=rel, error=err)
            continue

        fixed_files.append(rel)
        _emit_log("file.fixed", duration, file=rel, module=module_name)

    # 6. 汇总报告
    total_duration = (time.time() - t_start) * 1000
    _emit_log(
        "script.done",
        total_duration,
        total_candidates=len(candidates),
        fixed=len(fixed_files),
        skipped=len(skipped),
        errors=len(errors),
    )

    print("\n" + "=" * 70)
    print("修复报告")
    print("=" * 70)
    print(f"扫描文件总数: {len(candidates)}")
    print(f"成功修复:     {len(fixed_files)}")
    print(f"跳过:         {len(skipped)}")
    print(f"错误:         {len(errors)}")

    if fixed_files:
        print("\n--- 已修复文件 ---")
        for f in fixed_files:
            print(f"  [FIXED]  {f}")

    if skipped:
        print("\n--- 跳过文件 ---")
        for f, reason in skipped:
            print(f"  [SKIP]   {f}  ({reason})")

    if errors:
        print("\n--- 错误文件 ---")
        for f, err in errors:
            print(f"  [ERROR]  {f}  ({err})")

    print("=" * 70)
    if errors:
        print(f"完成 (含 {len(errors)} 个错误, 详见上方)")
        return 1
    print("完成 (全部成功)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        _emit_log("script.interrupted", 0.0)
        print("\n已中断")
        sys.exit(130)
    except Exception as e:
        # 顶层兜底: 任何未捕获异常都打印结构化日志 + traceback
        _emit_log(
            "script.fatal",
            0.0,
            error=f"{type(e).__name__}: {e}",
            traceback=traceback.format_exc(),
        )
        print(f"\n致命错误: {type(e).__name__}: {e}")
        traceback.print_exc()
        sys.exit(1)
