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
    - **不并入运行时技能目录/台账**（`data/skills.json`、`data/skills_mgmt.json`，两者被
      .gitignore 忽略）：干净 checkout / CI 里它们不存在 ⇒ 提交前若读了它们，CI 重算必然
      与提交产物不一致（实测 CI 报 23 处差异）。清单口径 = 仓库可复现的能力面。
    - `--check` 只比结构不比 `generated_at`（时间戳必然不同）。
    - 校验不过 ⇒ 非零退出码（CI/pre-commit 守门）。
【简易】
    python scripts/sync_capability_manifest.py            # 校验 + 生成（提交用口径）
    python scripts/sync_capability_manifest.py --check    # 只校验（CI 用）
    python scripts/sync_capability_manifest.py --runtime --summary   # 本地看运行时全貌
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

#: CapabilitySpec 必填字段（TASK-04 完成判据：每条都含 location/kind/owner/version/capability_id）
_SPEC_REQUIRED_FIELDS = ("capability_id", "kind", "location", "owner", "version",
                         "tenant_id", "namespace", "registry_source", "location_source")

#: 人读盘点表的输出路径（与 JSON 由**同一次运行**产出 ⇒ 禁止手工维护两份，E10）
INVENTORY_PATH = os.path.join(_ROOT, "docs", "rfc", "云枢能力清单盘点表.md")


def validate(manifest: dict) -> list[str]:
    """清单自洽性校验（返回问题列表；空 = 通过）

    守的是这些不变量：
      ① "不可调用必须说得出为什么"（❌/⚠️ 的成因不许为空）；
      ② "标识与判定同源"（✅ ⇔ 模型可发起且无附加条件）；
      ③ "✅ 必须有真正的落地条件"（有执行器 + 有参数契约）；
      ④ **TASK-04 新增**：CapabilitySpec 必填字段齐全、取值在域内；
      ⑤ **TASK-04 新增**：`location` 声明与事实判定一致（不一致 ⇒ 非零退出）；
      ⑥ **TASK-04 新增**：假能力（名义 > 实际）不得出现在"可用能力"集合里。
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
        # ④ CapabilitySpec 必填字段
        for f in _SPEC_REQUIRED_FIELDS:
            if not str(e.get(f) or "").strip():
                errs.append(f"{name}: 缺少 CapabilitySpec 必填字段 {f!r}")
        if e.get("location") not in ("local", "remote"):
            errs.append(f"{name}: location 取值非法: {e.get('location')!r}")
        if e.get("kind") not in ("tool", "skill"):
            errs.append(f"{name}: kind 取值非法: {e.get('kind')!r}")
        # ⑤ location 声明 vs 事实判定
        if e.get("location_declared") and not e.get("location_consistent", True):
            errs.append(
                f"{name}: **location 声明与事实判定不一致**"
                f"（声明 {e.get('location_declared')!r}，事实判定不符）—— "
                f"改 data/tool_definitions/{name}.yaml 的 location 或修判定器，勿手改清单")
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

    # ⑥ 假能力不得出现在"可用能力"集合里（E5）
    available = set(manifest.get("available_names") or [])
    for fake in manifest.get("non_capabilities") or []:
        nm = str(fake.get("name") or "")
        if fake.get("verdict") == "downgraded":
            continue    # 降级类（schedule_task）允许仍在清单里，但不得是 ✅
        if nm in available:
            errs.append(f"{nm}: 已知非能力（{fake.get('verdict')}）却出现在 ✅ 可用能力集合里")
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
            changed = [f for f in _FIELD_SPEC + _SPEC_REQUIRED_FIELDS + ("mark",)
                       if a.get(f) != b.get(f)]
            if changed:
                out.append(f"~ {name}: " + "，".join(
                    f"{f}: {a.get(f)!r} → {b.get(f)!r}" for f in changed))
    # 【TASK-04】顶层新增节（假能力 / 无效开关 / 同名冲突 / 主线可见集 …）也要比对，
    # 否则它们的漂移不会让 --check 失败。
    for key in ("non_capabilities", "config_switches", "skill_entity_constraints",
                "same_name_conflicts", "registry_variants", "main_line",
                "runtime_only_entities", "counts", "vocabulary"):
        if old.get(key) != new.get(key):
            out.append(f"~ 顶层节 {key} 有变化")
    return out


def _print_summary(manifest: dict) -> None:
    counts = manifest.get("counts") or {}
    print(f"清单条目: {counts.get('total')} "
          f"(✅ {counts.get('callable')} / ⚠️ {counts.get('conditional')} "
          f"/ ❌ {counts.get('blocked')})；其中模型可发起 "
          f"{counts.get('model_callable')} 条")
    print(f"口径: {manifest.get('scope')}")
    only = manifest.get("runtime_only_declarations") or []
    if only:
        print(f"仅在运行时存在、不在清单口径内的技能（{len(only)}）: {only}")
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


def render_inventory_markdown(manifest: dict) -> str:
    """把清单渲染成**人读的《云枢能力清单盘点表》**（Markdown）

    【为什么由同一个脚本渲染（E10）】Markdown 与 JSON 若各写一份，必然漂移。
    本函数只读 `build_manifest()` 的产物，**不引入任何独立数据源**；
    `--check` 会同时校验两份产物，保证"脚本可复跑、输出 diff 为空"。
    【不易·确定性】除 `generated_at` 一行外，输出**完全由数据决定**（排序固定），
    否则每次重跑都产生无意义 diff，"diff 为空"这条判据就失效了。
    """
    counts = manifest.get("counts") or {}
    line = manifest.get("main_line") or {}
    out: list[str] = []
    add = out.append

    add("# 云枢能力清单盘点表（CapabilitySpec 定稿）")
    add("")
    add("> **本文件是生成物，勿手改**：由 `scripts/sync_capability_manifest.py` 从")
    add("> `data/tool_definitions/*.yaml` + `data/skill_callability.yaml` + 注册点静态扫描")
    add("> **同一次运行**产出（与 `data/capability_manifest.json` 同源）。")
    add("> 改数据 → 重跑脚本 → 两份产物一起更新；`--check` 会拦住手改。")
    add("")
    add(f"- 生成时间：{manifest.get('generated_at')}")
    add(f"- 清单口径：{manifest.get('scope')}")
    add(f"- 总条目：**{counts.get('total')}**"
        f"（✅ {counts.get('callable')} / ⚠️ {counts.get('conditional')}"
        f" / ❌ {counts.get('blocked')}；模型可发起 {counts.get('model_callable')}）")
    add("")
    bl = counts.get("by_location") or {}
    add("## 1. `location` 分布（TASK-04 核心新增维度）")
    add("")
    add(f"- `local` = **{bl.get('local', 0)}**；`remote` = **{bl.get('remote', 0)}**")
    add(f"- 判定来源分布：`{json.dumps(counts.get('by_location_source') or {}, ensure_ascii=False)}`")
    add(f"- **事实判定层来源**（回填后 `location_source` 会全是 `declaration`，故这列才是真正的判据）："
        f"`{json.dumps(counts.get('by_location_derived_source') or {}, ensure_ascii=False)}`")
    add(f"- 判定置信度：`{json.dumps(counts.get('location_confidence') or {}, ensure_ascii=False)}`")
    add("")
    add(f"- **判定规则**：{manifest.get('location_rule')}")
    add("- **未使用保守默认兜底**：`location_source` 里不含 `default` 即为零兜底；")
    add("  若出现 `default`，说明该条 `host_executor` 为空（本表会在备注列写明）。")
    add("")
    add("## 2. 能力归属与形态")
    add("")
    add(f"- `kind`：`{json.dumps(counts.get('by_kind') or {}, ensure_ascii=False)}`")
    add(f"- `owner`：`{json.dumps(counts.get('by_owner') or {}, ensure_ascii=False)}`")
    add(f"- `registry_source`：`{json.dumps(counts.get('by_registry_source') or {}, ensure_ascii=False)}`")
    add("")
    add("## 3. 当前主线下的可见集（E12）")
    add("")
    if line.get("line_id"):
        add(f"- 激活主线：**`{line.get('line_id')}`**（`max_tools={line.get('max_tools')}`）")
        add(f"- 实际装配可见集（{len(line.get('visible') or [])} 条）："
            f"`{', '.join(line.get('visible') or [])}`")
        add(f"- `muted`（**YAML 里有、但本主线不发给模型**）："
            f"`{', '.join(line.get('muted') or [])}`")
        add(f"- `denied_by_effect`：`{', '.join(line.get('denied_by_effect') or [])}`")
        add(f"- `needs_approval`：`{', '.join(line.get('needs_approval') or [])}`")
        add("")
        add("> ⚠️ **不得因为某个工具在 YAML 里存在就认为模型能用它** ——")
        add("> 上面的 `muted` 是主线装配的实跑结果，`web_search` 在其中。")
    else:
        add(f"- 未能计算：{line.get('note')}")
    add("")
    add("## 4. 三条注册点 + 日志反推（v1.4 §3.3）")
    add("")
    add("| 注册点 | 事实 |")
    add("|---|---|")
    add("| ① 代码内硬编码注册 | 装饰器 `@_tools.register(...)` 共 17 个模块 + "
        "`agent/process_distill/tools.py`；**表驱动**注册 6 个 `kb_*`（`agent/knowledge/tools.py:297-298` "
        "遍历 `_TOOL_DEFS`）—— 已被本脚本的 AST 扫描识别（见第 6 节的 `kb_*` 行） |")
    add("| ② 配置文件开关 | **三处无效**：见第 7 节（`tools_config.json` 恒不生效、"
        "`system_prompt_config.json` 的 `tool_definitions` 从不被读、`config.yaml:175` 引用 3 个不存在的工具） |")
    add("| ③ 运行时动态注入 | `register_dynamic`（MCP / 插件 / LLM 自生成）；"
        "`lifecycle_manager.py:991`、`discovery_service.py:130`、`persistence.py`（`data/dynamic_tools.json` 当前为空） |")
    add("| ④ 日志反推隐藏调用方 | 本仓库日志样本极短（`server_health.log` / `backend_run.log` 各约 13–14 分钟，"
        "见 TASK-00 §0.2b）⇒ **不足以反推调用方**；已知的隐藏调用方以静态事实给出："
        "`agent/scheduling.py` 的 cron 循环（调用 `_execute_task`，而它是 `pass`）、"
        "`agent/knowledge/tools.py` 的表驱动注册、`plugins/chat.py:1105,1131` 的**第二个独立 LLM 工具循环** |")
    add("")
    add("## 5. 同名冲突逐组定案（E11）")
    add("")
    add("| 名字 | 定义 A（global） | 定义 B（planning） | 定案 |")
    add("|---|---|---|---|")
    for g in manifest.get("same_name_conflicts") or []:
        defs = {d.get("registry_source"): d for d in g.get("definitions") or []}
        a, b = defs.get("global") or {}, defs.get("planning") or {}
        add(f"| `{g.get('name')}` | {a.get('declared_in') or '—'} | "
            f"{b.get('declared_in') or '—'} | {g.get('resolved')} |")
    add("")
    rv = manifest.get("registry_variants") or []
    add(f"**第二套并行注册表**（`planning.ToolRegistry`，共 {len(rv)} 条，"
        f"均带 `registry_source=planning`）：`{', '.join(v['tool_name'] for v in rv)}`")
    add("")
    add("## 6. 能力明细")
    add("")
    add("> 性能列（`avg_ms` / `p99_ms` / `dpm`）**一律留空并标注「未采集」**：")
    add("> 仓库现有实测只有一次 HTTP 压测（p50=15.84s），且进程内计时冒充压测的产物")
    add("> 已确认不可引用（TASK-00 §0.2b）⇒ **严禁估算填充**。采集方案与 TASK-08 对接。")
    add(">")
    add("> `is_stateful` 按 `effect` **派生**（`write`/`execute`/`extend` ⇒ 是）：")
    add("> 仓库的 YAML 没有状态性声明，故这是口径近似而非实测，需 TASK-05 补声明。")
    add("> `auth` 由 `permission_level` + `needs_approval` 派生（同样是既有事实，不是估算）。")
    add("> `used_by_skills` **未能确认**：仓库里没有『技能引用工具』的声明面（技能只绑主线）。")
    add("")
    add("| tenant_id | name | kind/location | owner | version | capability_id | call_path | "
        "is_stateful | avg_ms | p99_ms | dpm | has_schema | auth | used_by_skills | "
        "callable_by | llm_visible | llm_invokable | 主线可见性 | 实体受版本控制 | "
        "location 判定依据 | status | 备注 |")
    add("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for e in manifest.get("entries") or []:
        name = e.get("tool_name")
        llm_visible = "否" if (e.get("internal") or not e.get("reachable")) else "是"
        ev = (e.get("location_evidence") or [""])[0]
        notes: list[str] = []
        if e.get("location_source") == "default":
            notes.append("location 用了保守默认（无执行器）")
        if e.get("location_confidence") == "low":
            notes.append(f"location 置信度低：{len(e.get('location_unresolved') or [])} 个调用点静态不可解析")
        if e.get("soft_codes") and "hollow_executor" in (e.get("soft_codes") or []):
            notes.append("**假能力：执行体是空实现**")
        if e.get("entity_versioned") is False:
            notes.append("实体不在版本控制内（不可复现）")
        if e.get("aliases"):
            notes.append(f"别名 {e.get('aliases')}")
        if e.get("location_ffi"):
            notes.append(f"同进程 FFI（高危副作用，需安全审查）：{e.get('location_ffi')}")
        for blocker in e.get("blockers") or []:
            notes.append(blocker)
        stateful = "是" if e.get("effect") in ("write", "execute", "extend") else "否"
        auth = f"{e.get('permission_level')}" + (" + 需审批" if e.get("needs_approval") else "")
        add("| " + " | ".join([
            str(e.get("tenant_id")),
            f"`{name}`",
            f"{e.get('kind')} / {e.get('location')}",
            str(e.get("owner")),
            str(e.get("version")),
            f"`{e.get('capability_id')}`",
            f"`{e.get('host_executor') or '—'}`",
            stateful,
            "未采集", "未采集", "未采集",
            "是" if e.get("schema_registered") else "否",
            auth,
            "未能确认",
            str(e.get("trigger")),
            llm_visible,
            "是" if e.get("llm_callable") else "否",
            str(e.get("main_line_status")),
            "是" if e.get("entity_versioned") else "**否**",
            ev,
            str(e.get("mark")),
            "；".join(notes) or "—",
        ]) + " |")
    add("")
    add("## 7. 无效配置开关（E13）")
    add("")
    add("| 配置 | 键 | 状态 | 为什么 |")
    add("|---|---|---|---|")
    for c in manifest.get("config_switches") or []:
        add(f"| `{c.get('path')}` | `{c.get('key')}` | **{c.get('status')}** | {c.get('why')} |")
    add("")
    add("## 8. 假能力处置清单（E5；8 个案例逐条定案）")
    add("")
    add("| 名义能力 | 类型 | 定案 | 证据 | 处置 |")
    add("|---|---|---|---|---|")
    for f in manifest.get("non_capabilities") or []:
        add(f"| `{f.get('name')}` | {f.get('kind')} | **{f.get('verdict')}** | "
            f"{f.get('evidence')} | {f.get('disposition')} |")
    add("")
    add(f"> {manifest.get('non_capability_note')}")
    add("")
    add("## 9. 技能侧实施约束（E14）")
    add("")
    add("| 约束 | 值 | 说明 |")
    add("|---|---|---|")
    for c in manifest.get("skill_entity_constraints") or []:
        add(f"| `{c.get('key')}` | `{c.get('value')}` | {c.get('why')} |")
    add("")
    roe = manifest.get("runtime_only_entities") or []
    add(f"**只在运行时存在、实体不受版本控制**的技能（{len(roe)} 条，"
        f"均为 `entity_versioned = false`）："
        f"`{', '.join(x['name'] for x in roe)}`")
    add("")
    add("> 本表 §6 里 23 条技能条目的 `实体受版本控制` 为「是」—— 它们的")
    add("> `data/skills_repo/<id>/skill.md` 确实入库；但**运行时技能目录/台账**")
    add("> （`data/skills.json`、`data/skills_mgmt.json`）被 `.gitignore` 忽略，")
    add("> 故这两份产物**不可复现**，其上方的 7 条技能实体**不可从版本控制重建**。")
    add("")
    add("## 10. 性能列采集方案（留空原因）")
    add("")
    add(f"{manifest.get('performance_note')}")
    add("")
    add("- 采集方案（与 TASK-08 对接）：① 在 `agent/tools/__init__.py::call()` 的既有")
    add("  `_update_health` 埋点旁汇总 `count`/`sum(duration)`（**已有基线字段**，只差落盘）；")
    add("  ② p99 需**环形缓冲**而非均值（现有埋点只有 `last_duration`）；")
    add("  ③ `dpm`（每分钟调用数）由同一埋点按分钟桶聚合；")
    add("  ④ **必须标注口径**：进程内计时不可当压测（TASK-00 §0.2b）。")
    add("")
    return "\n".join(out) + "\n"


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
                    help="并入运行时事实（注册表执行器 + 运行时技能目录/台账）；"
                         "产物随进程与运行时状态而变，**勿用于提交**（CI 口径见 --check）")
    ap.add_argument("--summary", action="store_true", help="打印统计表")
    ap.add_argument("--out", default=MANIFEST_PATH, help="清单输出路径")
    args = ap.parse_args()

    manifest = build_manifest(
        executor_facts=_runtime_executor_facts() if args.runtime else None,
        include_runtime_catalog=args.runtime)

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
        # 【TASK-04 / E10】人读盘点表与 JSON 同源：`--check` 必须一起校验，
        # 否则"同源"只是一句自述（手改了 Markdown 也发现不了）。
        if os.path.exists(INVENTORY_PATH):
            with open(INVENTORY_PATH, "r", encoding="utf-8") as f:
                md_old = f.read()
            md_new = render_inventory_markdown(manifest)
            md_old_norm = _strip_generated_at(md_old)
            if md_old_norm != _strip_generated_at(md_new):
                print(f"[FAIL] 盘点表与清单不同源: {INVENTORY_PATH}\n"
                      f"       运行 python scripts/sync_capability_manifest.py 重新派生")
                return 1
        else:
            print(f"[FAIL] 盘点表不存在: {INVENTORY_PATH}\n"
                  f"       运行 python scripts/sync_capability_manifest.py 生成")
            return 1
        if args.summary:
            _print_summary(manifest)
        print(f"[OK] 清单与权威数据一致：{len(manifest['entries'])} 条能力"
              f"（location: local {manifest['counts']['by_location']['local']}"
              f" / remote {manifest['counts']['by_location']['remote']}）")
        return 0

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    # 人读盘点表：与 JSON 同一次运行产出（E10）
    os.makedirs(os.path.dirname(INVENTORY_PATH), exist_ok=True)
    with open(INVENTORY_PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write(render_inventory_markdown(manifest))
    print(f"[OK] 盘点表已写入 {INVENTORY_PATH}")
    changes = _diff(old, manifest) if old else [f"+ 首次生成 {len(manifest['entries'])} 条"]
    print(f"[OK] 清单已写入 {args.out}（{len(manifest['entries'])} 条能力）")
    for c in changes[:40]:
        print(f"   - {c}")
    if args.summary:
        _print_summary(manifest)
    return 0


def _strip_generated_at(text: str) -> str:
    """去掉含 `generated_at` 的那一行再做比较（时间戳必然不同，不该判为漂移）"""
    return "\n".join(l for l in text.splitlines() if "生成时间" not in l)


if __name__ == "__main__":
    sys.exit(main())
