#!/usr/bin/env python3
"""工具能力平面回填脚本 —— 为 data/tool_definitions/*.yaml 补齐 plane/effect/risk/tags

【为什么需要】
    云枢的工具"存在什么、属于什么、风险多高、谁能用"此前散落在 13 处互不同步的表里
    （见 docs/工具集评估与重分类报告.md §2）。本脚本把其中**三个治理维度**收敛到
    工具定义本身，使 `data/tool_definitions/*.yaml` 成为唯一权威。

【四平面（可组装的主轴）】
    resident  常驻 —— 每轮必发的高频低 token 工具（原 core 类）
    perceive  感知 —— 只读取信息、不改变世界
    act       行动 —— 会改变世界（写文件/执行/进程/网络副作用）
    govern    治理 —— **会改变云枢自身能力集**（装扩展、生成工具、接 MCP）
                        ⇒ 治理平面本身就是审批边界：凡是 govern 平面的工具，
                          默认需要人工确认（见 effect=extend）
【效果轴（risk 的判据）】
    read     无副作用          → risk low
    write    本地可逆变更      → risk low/medium/high
    execute  任意代码/进程/出网 → risk medium/high/critical
    extend   改变自身能力集     → risk high/critical（必须审批）

【不易】
    - 幂等：已存在的字段**不覆盖**（只补缺失），可反复运行
    - 只做文本插入，不用 yaml.dump 回写 —— 保住 description 的折行与字段顺序
    - 插入位置固定：version 行之后（无 version 则 category 之后）
【变易】
    PLANE_MAP 是数据，可随工具增减调整
【简易】
    python scripts/backfill_tool_planes.py            # 回填
    python scripts/backfill_tool_planes.py --check    # 只检查不写（CI 用）
"""
from __future__ import annotations

import argparse
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFS_DIR = os.path.join(_ROOT, "data", "tool_definitions")

PLANES = ("resident", "perceive", "act", "govern")
EFFECTS = ("read", "write", "execute", "extend")
RISKS = ("low", "medium", "high", "critical")

#: 工具名 → (plane, effect, risk, tags)
#: tags 是可选的自由标签，供主线按需加权/过滤（不是分类的替代品）
PLANE_MAP: dict[str, tuple[str, str, str, list[str]]] = {
    # ── core_tools.py（9）────────────────────────────────────
    "get_status": ("resident", "read", "low", []),
    "search_memory": ("resident", "read", "low", ["memory"]),
    "remember": ("resident", "write", "low", ["memory"]),
    "get_sensor_summary": ("resident", "read", "low", ["sensor"]),
    "get_persona_info": ("resident", "read", "low", ["persona"]),
    "get_preferences": ("resident", "read", "low", ["persona"]),
    "search_lifetrace": ("perceive", "read", "low", ["memory", "lifetrace"]),
    "trigger_distillation": ("act", "write", "low", ["persona", "evolution"]),

    # ── file_tools_reg.py（8）────────────────────────────────
    "read_file": ("perceive", "read", "low", ["code"]),
    "list_directory": ("perceive", "read", "low", ["code"]),
    "get_file_info": ("perceive", "read", "low", ["code"]),
    "search_files": ("perceive", "read", "low", ["code"]),
    "diff_files": ("perceive", "read", "low", ["code"]),
    "write_file": ("act", "write", "high", ["code"]),
    "compress": ("act", "write", "medium", ["code"]),
    "decompress": ("act", "write", "high", ["code"]),

    # ── web_tools.py（9）────────────────────────────────────
    "web_search": ("perceive", "read", "low", ["web"]),
    "web_get": ("perceive", "read", "medium", ["web"]),
    "web_batch": ("perceive", "read", "medium", ["web"]),
    "get_weather": ("perceive", "read", "low", ["web"]),
    "notify": ("act", "execute", "low", ["channel"]),
    "sqlite_query": ("perceive", "read", "low", ["data"]),
    "run_lint": ("act", "execute", "low", ["code"]),
    "web_extract": ("perceive", "read", "medium", ["web"]),
    "web_post": ("act", "execute", "medium", ["web", "egress"]),
    "web_download": ("act", "write", "medium", ["web"]),

    # ── ext_tools.py（13）────────────────────────────────────
    "ext_list": ("perceive", "read", "low", ["extension"]),
    "ext_discover": ("perceive", "read", "medium", ["extension"]),
    "ext_send_channel": ("act", "execute", "high", ["channel", "egress"]),
    "ext_install": ("govern", "extend", "high", ["extension", "self_modify"]),
    "ext_uninstall": ("govern", "extend", "high", ["extension", "self_modify"]),
    "ext_toggle": ("govern", "extend", "medium", ["extension", "self_modify"]),
    "ext_configure": ("govern", "extend", "medium", ["extension", "self_modify"]),
    "generate_tool": ("govern", "extend", "critical", ["evolution", "self_modify", "codegen"]),
    "scan_mcp": ("govern", "extend", "medium", ["mcp", "self_modify"]),
    "connect_mcp": ("govern", "extend", "high", ["mcp", "self_modify"]),
    "disconnect_mcp": ("govern", "extend", "medium", ["mcp", "self_modify"]),

    # ── pdf_tools.py（4）────────────────────────────────────
    "read_pdf": ("perceive", "read", "low", ["document"]),
    "get_pdf_info": ("perceive", "read", "low", ["document"]),
    "merge_pdf": ("act", "write", "low", ["document"]),
    "split_pdf": ("act", "write", "low", ["document"]),

    # ── system_tools.py（5）────────────────────────────────
    "list_processes": ("perceive", "read", "low", ["process"]),
    "shell_execute": ("act", "execute", "critical", ["shell"]),
    "run_program": ("act", "execute", "high", ["process"]),
    "stop_process": ("act", "execute", "medium", ["process"]),

    # ── code_tools.py（18）─────────────────────────────────
    "code_review": ("perceive", "read", "low", ["code"]),
    "humanize_zh": ("perceive", "read", "low", ["text"]),
    "json_query": ("perceive", "read", "low", ["data"]),
    "data_convert": ("perceive", "read", "low", ["data"]),
    "data_format_detect": ("perceive", "read", "low", ["data"]),
    "list_scheduled_tasks": ("perceive", "read", "low", ["schedule"]),
    "list_async_tasks": ("perceive", "read", "low", ["async"]),
    "get_task_status": ("perceive", "read", "low", ["async"]),
    "get_task_result": ("perceive", "read", "low", ["async"]),
    "arch_diagram": ("act", "write", "low", ["produce"]),
    "schedule_task": ("act", "execute", "high", ["schedule"]),
    "cancel_scheduled_task": ("act", "write", "medium", ["schedule"]),
    "pause_scheduled_task": ("act", "write", "low", ["schedule"]),
    "resume_scheduled_task": ("act", "write", "low", ["schedule"]),
    "submit_task": ("act", "execute", "medium", ["async"]),
    "cancel_task": ("act", "write", "low", ["async"]),

    # ── search_tools.py（2）────────────────────────────────
    "grep": ("perceive", "read", "low", ["code"]),
    "edit": ("act", "write", "high", ["code"]),

    # ── subagent_tools.py（1）──────────────────────────────
    "delegate": ("act", "execute", "medium", ["orchestrate"]),

    # ── fan_out_tools.py（1）───────────────────────────────
    # 一次调用并发起 N 个真实子代理 ⇒ 成本与副作用都按 N 倍放大，故 risk=high
    "fan_out": ("act", "execute", "high", ["orchestrate", "parallel"]),

    # ── plan_tools.py（1）──────────────────────────────────
    "todo_write": ("resident", "write", "low", []),

    # ── knowledge/tools.py（6，此前从未接线）──────────────
    "kb_capture": ("act", "write", "low", ["knowledge", "memory"]),
    "kb_distill": ("act", "write", "medium", ["knowledge", "evolution"]),
    "kb_discuss": ("act", "write", "medium", ["knowledge"]),
    "kb_card": ("act", "write", "low", ["knowledge"]),
    "kb_lint": ("perceive", "read", "low", ["knowledge"]),
    "kb_search": ("perceive", "read", "low", ["knowledge", "memory"]),

    # ── process_distill/tools.py（3，此前无 YAML 定义）─────
    "distill_process_from_knowledge": ("act", "execute", "medium", ["evolution", "knowledge"]),
    "process_distill_run": ("act", "execute", "medium", ["evolution", "knowledge"]),

    # ── extra_tools.py（14，补登记"已实现但从未注册"的能力）──
    # run_sandbox 是**不受限代码执行**（沙盒只是软约束）⇒ critical
    "run_sandbox": ("act", "execute", "critical", ["sandbox", "code"]),
    "get_clipboard": ("perceive", "read", "low", ["clipboard"]),
    "set_clipboard": ("act", "write", "medium", ["clipboard"]),
    # 浏览器会拉起真实进程并出网；截图/OCR 可能含密钥 ⇒ medium
    "browser_navigate": ("act", "execute", "medium", ["web", "browser"]),
    "browser_screenshot": ("perceive", "read", "medium", ["web", "browser"]),
    "browser_close": ("act", "write", "low", ["web", "browser"]),
    "workspace_init": ("act", "write", "low", ["workspace"]),
    "workspace_list": ("perceive", "read", "low", ["workspace"]),
    "workspace_write": ("act", "write", "medium", ["workspace"]),
    "workspace_delete": ("act", "write", "high", ["workspace"]),
    "read_pdf_tables": ("perceive", "read", "low", ["document"]),
    "list_mcp_connections": ("perceive", "read", "low", ["mcp"]),
    "look_at_screen": ("perceive", "read", "medium", ["vision", "privacy"]),
    "weekly_report": ("act", "write", "low", ["document", "report"]),

    # ── git_tools.py（1）────────────────────────────────────
    # 一个工具同时含只读与写动作（写动作另有 confirm=true 闸门）⇒ 按最高风险计
    "git": ("act", "execute", "high", ["code", "git"]),

    # ── test_tools.py（2）──────────────────────────────────
    "run_tests": ("act", "execute", "medium", ["code", "test"]),
    "apply_patch": ("act", "write", "high", ["code", "patch"]),
}

#: 需要 `internal: true` 的工具：保留注册（供内部按名调用），但不进模型可见集。
#: 为什么不能直接注销：`distill_process_async` 以 tool_name 提交异步任务，
#: 而 `agent.tools.call()` 要求名字**在 _registry 中**，注销会打断该链路。
INTERNAL_TOOLS = ("process_distill_run",)

_ANCHOR_RE = re.compile(r"^(version:\s*\S+)\s*$", re.MULTILINE)
_CATEGORY_RE = re.compile(r"^(category:\s*\S+)\s*$", re.MULTILINE)


def _render_block(plane: str, effect: str, risk: str, tags: list[str]) -> str:
    lines = [f"plane: {plane}", f"effect: {effect}", f"risk: {risk}"]
    if tags:
        lines.append("tags: [" + ", ".join(tags) + "]")
    return "\n".join(lines)


def _insert_after_anchor(text: str, block: str) -> str:
    """在 version（优先）或 category 行之后插入字段块"""
    m = _ANCHOR_RE.search(text) or _CATEGORY_RE.search(text)
    if not m:
        raise ValueError("找不到 version:/category: 锚点")
    end = m.end()
    return text[:end] + "\n" + block + text[end:]


def _has_field(text: str, field: str) -> bool:
    return re.search(rf"^{field}:\s*\S", text, re.MULTILINE) is not None


def main() -> int:
    ap = argparse.ArgumentParser(description="回填工具能力平面字段")
    ap.add_argument("--check", action="store_true", help="只检查缺失，不写文件")
    args = ap.parse_args()

    yamls = sorted(f for f in os.listdir(_DEFS_DIR) if f.endswith(".yaml"))
    known = {os.path.splitext(f)[0] for f in yamls}

    # 完整性：PLANE_MAP 覆盖全部 YAML；且不含未知工具
    unmapped = sorted(known - set(PLANE_MAP))
    extra = sorted(set(PLANE_MAP) - known)
    if unmapped:
        print(f"[FAIL] 这些 YAML 没有平面映射（请补 PLANE_MAP）: {unmapped}")
        return 2
    if extra:
        print(f"[WARN] PLANE_MAP 里有非 YAML 工具（忽略）: {extra}")

    changed, skipped, missing = [], [], []
    for fname in yamls:
        name = os.path.splitext(fname)[0]
        plane, effect, risk, tags = PLANE_MAP[name]
        # 自校验：plane/effect/risk 取值合法
        assert plane in PLANES, f"{name}: 非法 plane {plane}"
        assert effect in EFFECTS, f"{name}: 非法 effect {effect}"
        assert risk in RISKS, f"{name}: 非法 risk {risk}"

        path = os.path.join(_DEFS_DIR, fname)
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()

        need = [fld for fld in ("plane", "effect", "risk") if not _has_field(text, fld)]
        if not need:
            skipped.append(name)
            continue
        if args.check:
            missing.append((name, need))
            continue

        text = _insert_after_anchor(text, _render_block(plane, effect, risk, tags))
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        changed.append(name)

    if args.check:
        if missing:
            print(f"[FAIL] {len(missing)} 个 YAML 缺字段：")
            for n, flds in missing:
                print(f"   - {n}: 缺 {flds}")
            print("\n运行 python scripts/backfill_tool_planes.py 补齐")
            return 1
        print(f"[OK] 全部 {len(yamls)} 个 YAML 均已具备 plane/effect/risk")
        return 0

    print(f"[OK] 回填完成：新增 {len(changed)} 个，已有字段跳过 {len(skipped)} 个")

    # 平面分布统计
    from collections import Counter
    pc = Counter(v[0] for v in PLANE_MAP.values())
    ec = Counter(v[1] for v in PLANE_MAP.values())
    rc = Counter(v[2] for v in PLANE_MAP.values())
    print(f"     平面分布: {dict(pc)}")
    print(f"     效果分布: {dict(ec)}")
    print(f"     风险分布: {dict(rc)}")
    print(f"     合计 {sum(pc.values())} 个工具")
    return 0


if __name__ == "__main__":
    sys.exit(main())
