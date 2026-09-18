#!/usr/bin/env python3
"""统一可调用性清单同步脚本 —— 派生 data/capability_manifest.json

【这份清单是什么】
    把「工具 / 技能能不能被 LLM 自己发起调用」写成**一份可自动解析的数据**：
    每条能力都带同一组字段（`tool_name` / `tool_type` / `llm_callable` /
    `callable_mode` / `schema_registered` / `host_executor` / `permission_level` /
    `sandbox_allowed` / `reason`）+ 展示标识（✅ 可调用 / ⚠️ 条件可调用 / ❌ 不可调用）。
    字段语义、判定规则、三层归属见 `agent/lines/callability.py` 的模块文档。

【为什么是派生而不是手写】
    手写清单必然与权威漂移。本清单的输入是：
      · data/tool_definitions/*.yaml      工具的声明层（唯一权威）
      · data/skill_callability.yaml       技能的声明层（唯一权威）
      · 注册点静态扫描（AST）              执行器事实（默认口径，确定性）
      · data/permission_policies.json      角色 denied_tools（与 tool_gate 同口径）
      · data/skills.json / skills_mgmt.json / skills_repo/*/skill.md   技能事实
    ⇒ 想改清单，就改上面这些数据文件，然后跑本脚本；`--check` 会拦住"手改了清单"。

【不易】
    - **不使用运行时注册表**（`--runtime` 才启用）：运行时装了什么取决于本进程导入过哪些
      模块，用它生成产物会让清单随进程而变，CI 无法守门。静态扫描是确定性的。
    - `--check` 只比结构不比 `generated_at`（时间戳必然不同）。
    - 校验不过 ⇒ 非零退出码（CI/pre-commit 守门）。
【简易】
    python scripts/sync_capability_manifest.py            # 校验 + 生成
    python scripts/sync_capability_manifest.py --check    # 只校验（CI 用）
    python scripts/sync_capability_manifest.py --runtime   # 把运行时注册表并入执行器事实
    python scripts/sync_capability_manifest.py --summary   # 打印统计表
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# 【坑】Windows 控制台默认 GBK，标识里有 ✅/⚠️/❌ ⇒ 直接 print 会 UnicodeEncodeError
# （实测：清单已正确落盘，脚本却在打印统计表时崩掉，看起来像"生成失败"）。故显式切 UTF-8。
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

from agent.lines.callability import (  # noqa: E402
    MANIFEST_PATH,
    MARK_BLOCKED,
    MARK_CALLABLE,
    MARK_CONDITIONAL,
    build_manifest,
)

_FIELD_SPEC = ("tool_name", "tool_type", "llm_callable", "callable_mode",
               "schema_registered", "host_executor", "permission_level",
               "sandbox_allowed", "reason")


def validate(manifest: dict) -> list[str]:
    """清单自洽性校验（返回问题列表；空 = 通过）

    守的是三条不变量：
      ① "不可调用必须说得出为什么"（❌/⚠️ 的成因不许为空）；
      ② "标识与判定同源"（✅ ⇔ 模型可发起且无附加条件）；
      ③ "✅ 必须有真正的落地条件"（有执行器 + 有参数契约）。
    """
    errs: list[str] = []
    entries = manifest.get("entries") or []
    if not entries:
        errs.append("清单为空（entries 为空）")
    for e in entries:
        name = e.get("tool_name") or "?"
        for f in _FIELD_SPEC:
            if f not in e:
                errs.append(f"{name}: 缺少统一字段 {f!r}")
        callable_ = bool(e.get("llm_callable"))
        reachable = bool(e.get("reachable"))
        mark = e.get("mark")

        # ① 说得出为什么
        if not callable_ and not str(e.get("reason") or "").strip():
            errs.append(f"{name}: llm_callable=false 但 reason 为空（必须说明为什么）")
        if mark == MARK_BLOCKED and not (e.get("blockers") or []):
            errs.append(f"{name}: 标为 ❌ 但 blockers 为空（说不清是哪条硬阻断）")
        if mark == MARK_CONDITIONAL and not (
                (e.get("soft_blockers") or []) or (e.get("conditions") or [])):
            errs.append(f"{name}: 标为 ⚠️ 但既无 soft_blockers 也无 conditions")

        # ② 标识与判定同源
        if e.get("callable_mode") == "manual" and callable_:
            errs.append(f"{name}: callable_mode=manual 却标 llm_callable=true（自相矛盾）")
        if mark not in (MARK_CALLABLE, MARK_CONDITIONAL, MARK_BLOCKED):
            errs.append(f"{name}: 标识非法: {mark!r}")
        if mark == MARK_CALLABLE and (not callable_ or e.get("conditions")):
            errs.append(f"{name}: 标为 ✅ 但模型不可发起或存在附加条件")
        if mark == MARK_BLOCKED and reachable:
            errs.append(f"{name}: 标为 ❌ 却 reachable=true（❌ 只用于不可达）")
        if not reachable and mark != MARK_BLOCKED:
            errs.append(f"{name}: 不可达却不是 ❌")
        if callable_ and not reachable:
            errs.append(f"{name}: llm_callable=true 但 reachable=false")

        # ③ ✅ 的落地条件
        if callable_ and not e.get("host_executor"):
            errs.append(f"{name}: 判为可调用却没有执行器")
        if callable_ and not e.get("schema_registered"):
            errs.append(f"{name}: 判为可调用却没有 JSON Schema")
    return errs


def _strip_time(manifest: dict) -> dict:
    return {k: v for k, v in manifest.items() if k != "generated_at"}


def _diff(old: dict, new: dict) -> list[str]:
    """按能力名对比新旧清单（只报变化的字段，便于人读）"""
    out: list[str] = []
    old_map = {e.get("tool_name"): e for e in (old.get("entries") or [])}
    new_map = {e.get("tool_name"): e for e in (new.get("entries") or [])}
    for name in sorted(set(old_map) | set(new_map)):
        a, b = old_map.get(name), new_map.get(name)
        if a is None:
            out.append(f"+ 新增 {name} [{b.get('mark')}]")
        elif b is None:
            out.append(f"- 消失 {name}")
        else:
            changed = [f for f in _FIELD_SPEC + ("mark",) if a.get(f) != b.get(f)]
            if changed:
                out.append(f"~ {name}: " + "，".join(
                    f"{f}: {a.get(f)!r} → {b.get(f)!r}" for f in changed))
    return out


def _print_summary(manifest: dict) -> None:
    counts = manifest.get("counts") or {}
    print(f"清单条目: {counts.get('total')} "
          f"(✅ {counts.get('callable')} / ⚠️ {counts.get('conditional')} "
          f"/ ❌ {counts.get('blocked')})；其中模型可发起 "
          f"{counts.get('model_callable')} 条")
    print(f"按触发者: {counts.get('by_trigger')}")
    print(f"按类型: {counts.get('by_type')}")
    print(f"按权限等级: {counts.get('by_permission_level')}")
    print("❌ 不可达成因分布:")
    for reason, n in (counts.get("blocked_reasons") or {}).items():
        print(f"   {n:>3}  {reason}")
    print("⚠️ 有条件成因分布:")
    for reason, n in (counts.get("conditional_reasons") or {}).items():
        print(f"   {n:>3}  {reason}")
    print("⚠️ 条件可调用明细（前 20 条）:")
    shown = 0
    for e in manifest.get("entries") or []:
        if e.get("mark") == MARK_CONDITIONAL and shown < 20:
            shown += 1
            why = e.get("conditions") or e.get("soft_blockers") or []
            print(f"   - {e['tool_name']} [{e.get('trigger')}]: {'；'.join(why)}")
    blocked = [e for e in manifest.get("entries") or [] if e.get("mark") == MARK_BLOCKED]
    print("❌ 不可达明细:")
    for e in blocked:
        print(f"   - {e['tool_name']}: {e.get('reason')}")


def _runtime_executor_facts() -> dict:
    """运行时注册表的执行器事实（**在脚本侧**导入 `agent.tools`）

    【为什么在脚本里而不是 agent/lines/callability.py 里导入】
        `agent.tools` 反向依赖 `agent.lines.callability`（隐藏判否工具），若后者也导入
        `agent.tools` 就构成循环依赖，被架构规则 `no_circular_dependency` 判违规
        （实测 architecture-check 红灯）。scripts/ 不在该规则的扫描根（`--root agent`）内，
        故"运行时事实"在这里取、再注入给 `build_manifest(executor_facts=...)`。
    """
    try:
        from agent.tools import registry_facts
        facts = registry_facts() or {}
    except Exception as e:  # noqa: BLE001 注册表不可用不得让清单生成失败
        print(f"[WARN] 运行时注册表不可用，退回静态扫描口径: {e}")
        return {}
    return {str(n): str((info or {}).get("host_executor") or "")
            for n, info in facts.items()}


def main() -> int:
    ap = argparse.ArgumentParser(description="统一可调用性清单同步与校验")
    ap.add_argument("--check", action="store_true", help="只校验不写（CI/pre-commit 用）")
    ap.add_argument("--runtime", action="store_true",
                    help="把运行时注册表并入执行器事实（产物随进程而变，勿用于 CI）")
    ap.add_argument("--summary", action="store_true", help="打印统计表")
    ap.add_argument("--out", default=MANIFEST_PATH, help="清单输出路径")
    args = ap.parse_args()

    manifest = build_manifest(
        executor_facts=_runtime_executor_facts() if args.runtime else None)

    errs = validate(manifest)
    if errs:
        print(f"[FAIL] 清单自洽性校验失败（{len(errs)} 条）：")
        for e in errs[:40]:
            print(f"   - {e}")
        return 1

    old = None
    if os.path.exists(args.out):
        try:
            with open(args.out, "r", encoding="utf-8") as f:
                old = json.load(f)
        except ValueError:
            old = None

    if args.check:
        if old is None:
            print(f"[FAIL] 清单不存在或无法解析: {args.out}\n"
                  f"       运行 python scripts/sync_capability_manifest.py 生成")
            return 1
        changes = _diff(old, manifest)
        if changes:
            print(f"[FAIL] 清单与权威数据不一致（{len(changes)} 处）：")
            for c in changes[:40]:
                print(f"   - {c}")
            print("\n运行 python scripts/sync_capability_manifest.py 重新派生"
                  "（清单是派生物，勿手改）")
            return 1
        if args.summary:
            _print_summary(manifest)
        print(f"[OK] 清单与权威数据一致：{len(manifest['entries'])} 条能力")
        return 0

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    changes = _diff(old, manifest) if old else [f"+ 首次生成 {len(manifest['entries'])} 条"]
    print(f"[OK] 清单已写入 {args.out}（{len(manifest['entries'])} 条能力）")
    for c in changes[:40]:
        print(f"   - {c}")
    if args.summary:
        _print_summary(manifest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
