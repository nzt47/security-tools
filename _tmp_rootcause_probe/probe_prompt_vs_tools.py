# -*- coding: utf-8 -*-
"""根因探针 A：打印生产链路里"系统提示词里的工具宣传"与"请求里的 tools"两侧的真实取值。

为什么写这个：
    三条件对照实验（cond4_P1/P2/P3）已证实触发条件是
    「提示词向模型描述了工具」+「请求 tools 为空」。
    本探针把这条不变量搬到**生产链路**上逐条取证：
      ① 真实系统提示词（get_template + _build_tool_status_text）里到底有没有工具宣传文本；
      ② 各个 tools= 构造点在同一时刻是否会退化为空。

只读，不联网，不改任何状态。
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

print("=" * 78)
print("[1] 真实系统提示词模板（agent.system_prompt_manager.get_template）")
print("=" * 78)
from agent.system_prompt_manager import get_template  # noqa: E402

tpl = get_template()
ADVERT_MARKERS = [
    "首条回复必须是tool_calls",
    "已启用/禁用的工具和技能",
    "tool_status",
]
for m in ADVERT_MARKERS:
    print("  模板含 %-28s : %s" % (m, m in tpl))

print()
print("=" * 78)
print("[2] {tool_status} 的真实渲染值（DigitalLifePersonaMixin._build_tool_status_text）")
print("=" * 78)
try:
    from agent.digital_life_persona import DigitalLifePersonaMixin

    class _P(DigitalLifePersonaMixin):
        def __init__(self):
            self._cached_tool_status = None
            self._cached_skill_instructions = None
            self._loaded_skill_ids = []

    mix = _P()
    status_text = mix._build_tool_status_text()
    print("  渲染值 (%d 字符):" % len(status_text))
    for line in status_text.splitlines():
        print("    | " + line[:400])
except Exception as e:  # noqa: BLE001
    status_text = ""
    print("  [FAIL] %s: %s" % (type(e).__name__, e))

print()
print("=" * 78)
print("[3] 渲染后的工具名是否出现在提示词里（= P1 条件的生产判定）")
print("=" * 78)
tool_names = []
try:
    from agent.tools import get_tool_defs, list_tools

    tool_names = [t["name"] for t in list_tools()]
    defs = get_tool_defs()
    print("  注册表工具数 list_tools() = %d ; get_tool_defs() = %d" % (len(tool_names), len(defs)))
except Exception as e:  # noqa: BLE001
    print("  [FAIL] 工具注册表不可用: %s: %s" % (type(e).__name__, e))

listed = [n for n in tool_names if n and n in status_text]
print("  {tool_status} 里出现的工具名数量 = %d" % len(listed))
print("  样例: %s" % (listed[:12],))

print()
print("=" * 78)
print("[4] 各 tools= 构造点的行为（静态取证，见文件:行号）")
print("=" * 78)
findings = [
    ("agent/tool_calling.py:317", "need_tools = tool_defs if round_idx < self._max_rounds else None",
     "最后一轮**主动不传 tools**"),
    ("agent/orchestrator/orchestrator.py:3446-3447", "if not allow_tools: _tool_defs = []",
     "allow_tools=False 时**清空 tools**（工作流层已执行过工具）"),
    ("agent/orchestrator/orchestrator.py:3519-3520", "_kwargs.pop(\"tools\", None)",
     "最后一轮**弹出 tools**"),
    ("plugins/chat.py:1194", "tools=tool_defs if round_idx == 0 else None",
     "工作台工具循环**仅首轮传 tools**"),
    ("plugins/chat.py:1140-1141", "except: 工具定义加载失败（无工具可用）",
     "加载失败 ⇒ tool_defs 保持 None ⇒ 全程无 tools"),
]
for loc, code, note in findings:
    print("  %-46s %-42s %s" % (loc, code, note))

print()
print("=" * 78)
print("[5] 结论")
print("=" * 78)
print("  提示词侧是否宣传了工具: %s" % ("是" if any(m in tpl for m in ADVERT_MARKERS[:2]) else "否"))
print("  提示词中显式列出的工具名数量: %d" % len(listed))
