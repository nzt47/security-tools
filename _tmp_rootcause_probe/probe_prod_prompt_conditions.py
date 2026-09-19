# -*- coding: utf-8 -*-
"""根因探针 B：把"提示词宣传工具"拆成生产提示词的**各个成分**，逐个定标谁在触发 DSML。

背景
----
`cond4_P1/P2/P3` 已证实触发条件是「提示词向模型描述了工具」+「请求 tools 为空」。
但 P1 用的是**自造**提示词（简单列出 25 个工具名），P3 用的是**极简**提示词。
生产提示词是另一套文本（`agent.system_prompt_manager.get_template()`），
所以必须先回答：**生产提示词的哪一句在制造 DSML？**

对照（user prompt 固定，tools 固定为空）
---------------------------------------
  A 生产提示词原样
  B 生产提示词 · 去掉「执行铁律：…首条回复必须是tool_calls…」
  C 生产提示词 · 把 {tool_status} 清空（只留执行铁律）
  D 生产提示词 · 列出真实工具名（模拟 tools_config 有禁用项时的渲染）
  E 生产提示词原样 + tools 非空（阳性对照，应走结构化 tool_calls）

落盘：_tmp_rootcause_probe/evidence/（临时目录，交付前清理）
"""
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(
    os.path.expanduser("~"), "Desktop", "设计思路", "云枢能力层重构审计与子任务",
    "_baseline", "dsml-evidence"))
os.chdir(ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))

from probe_with_tools import _bootstrap_tools, _client  # noqa: E402

EVID = os.path.join(HERE, "evidence")
os.makedirs(EVID, exist_ok=True)

PROMPT = "列出当前目录下的 Python 文件"

# 「执行铁律」那一行的原文（生产模板里硬编码，与 tools= 是否下发无关）
IRON_RULE = "执行铁律：遇任何实操请求，首条回复必须是tool_calls，严禁先发文字或废话。"


def _strip_iron_rule(text: str) -> str:
    out = []
    for line in text.splitlines():
        if "首条回复必须是" in line and "tool_calls" in line:
            continue
        out.append(line)
    return "\n".join(out)


def _blank_tool_status_section(text: str) -> str:
    """把 {tool_status} 渲染出来的行清掉，保留标题（即"只有执行铁律"的条件）。"""
    out = []
    for line in text.splitlines():
        if line.startswith("【工具】") or line.startswith("【技能】") or line.startswith("💡"):
            continue
        out.append(line)
    return "\n".join(out)


def run(tag, system_prompt, tools, note=""):
    c = _client()
    kwargs = dict(
        model=os.environ.get("LLM_MODEL") or "deepseek-chat",
        messages=[{"role": "system", "content": system_prompt},
                  {"role": "user", "content": PROMPT}],
        max_tokens=2048, temperature=0.3,
    )
    if tools:
        kwargs["tools"] = tools
    t0 = time.time()
    resp = c.chat.completions.create(**kwargs)
    elapsed = round((time.time() - t0) * 1000, 1)
    msg = resp.choices[0].message
    content = msg.content or ""
    tcs = [{"name": tc.function.name, "arguments": tc.function.arguments}
           for tc in (msg.tool_calls or [])] or None
    raw = {
        "tag": tag,
        "note": note,
        "request": {"tools_present": bool(tools), "tools_count": len(tools),
                    "prompt": PROMPT, "system_prompt": system_prompt},
        "elapsed_ms": elapsed,
        "finish_reason": resp.choices[0].finish_reason,
        "message_content": msg.content,
        "message_reasoning_content": getattr(msg, "reasoning_content", None),
        "message_tool_calls": tcs,
        "dsml_hit": ("DSML" in content) or ("\uff5c" in content),
        "tool_calls_len": len(tcs or []),
        "usage": resp.usage.model_dump() if getattr(resp, "usage", None) else None,
    }
    out = os.path.join(EVID, "probeB_%s_%d.json" % (tag, int(time.time())))
    with open(out, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2, default=str)
    print("=== %-28s tools=%-2d %s" % (tag, len(tools), note))
    print("    finish=%-10s dsml=%-5s tool_calls=%d content_len=%d" % (
        raw["finish_reason"], raw["dsml_hit"], raw["tool_calls_len"], len(content)))
    print("    U+FF5C=%d  content[:180]=%s" % (content.count("\uff5c"), repr(content[:180])))
    print("    -> %s" % out)
    return raw


if __name__ == "__main__":
    ok, bad = _bootstrap_tools()
    print("[bootstrap] ok=%d bad=%d" % (len(ok), len(bad)))
    from agent.tools import get_tool_defs, list_tools
    from agent.lines import line_whitelist
    wl, res = line_whitelist(None)
    full = get_tool_defs(whitelist=wl)
    names = [x["function"]["name"] for x in full]
    print("[bootstrap] registry=%d line_wl=%s defs=%d" % (
        len(list_tools()), (len(wl) if wl else None), len(full or [])))

    # ── 真实生产提示词 ──
    from agent.system_prompt_manager import get_template
    from agent.digital_life_persona import DigitalLifePersonaMixin

    class _P(DigitalLifePersonaMixin):
        def __init__(self):
            self._cached_tool_status = None
            self._cached_skill_instructions = None
            self._loaded_skill_ids = []

    status_real = _P()._build_tool_status_text()

    def render(tool_status):
        return get_template().format(
            current_date="2025年1月1日",
            body_status="（省略）",
            mode_name="正常",
            mode_description="正常运转",
            memory_context="（暂无记忆内容）",
            tool_status=tool_status,
            skill_instructions="",
        )

    prod = render(status_real)
    print("\n[prod prompt] len=%d\n----\n%s\n----\n" % (len(prod), prod))

    listed_status = ("【工具】已启用(%d): %s\n【技能】已启用(31): (略)"
                     % (len(names), ", ".join(names)))

    results = []
    results.append(run("A_prod_asis_notools", prod, [], "生产提示词原样 + 无 tools"))
    results.append(run("B_prod_no_ironrule_notools", _strip_iron_rule(prod), [],
                       "去掉执行铁律 + 无 tools"))
    results.append(run("C_prod_no_toolstatus_notools",
                       _blank_tool_status_section(_strip_iron_rule(prod)), [],
                       "只留执行铁律（清空 tool_status）+ 无 tools"))
    results.append(run("D_prod_listed_notools", render(listed_status), [],
                       "tool_status 列出工具名 + 无 tools"))
    results.append(run("E_prod_asis_tools", prod, full,
                       "生产提示词原样 + 有 tools（阳性对照）"))

    print("\n" + "=" * 70)
    print("%-34s %-8s %-8s %s" % ("条件", "tools数", "DSML", "tool_calls"))
    print("=" * 70)
    for r in results:
        print("%-34s %-8d %-8s %d" % (
            r["tag"], r["request"]["tools_count"], r["dsml_hit"], r["tool_calls_len"]))
