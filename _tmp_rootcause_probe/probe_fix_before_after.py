# -*- coding: utf-8 -*-
"""根因探针 C：修复前后对照 —— 同一 prompt、同一探针、同一 tools=空 条件。

对照设计（唯一变量 = 是否经过 `agent.tools_prompt_guard` 对齐）
------------------------------------------------------------------
  修复前  生产提示词原样（含 `【工具】全部已启用（共 N 个）`）  + tools=[]  ⇒ 探针 B-A 已证 DSML
  修复后  同一提示词经 `align_system_prompt_with_tools(..., tools_exposed=False)`
          之后再发给上游                              + tools=[]  ⇒ 期望**无 DSML**
  阳性对照 修复后提示词 + tools=26（真实工具定义）              ⇒ 期望走结构化 tool_calls

为何必须真连上游而不是 mock：
    DSML 是**上游模型的行为**，不是平台代码的行为。mock 只能验证平台侧解析，
    无法证明"修复后上游不再吐标记"。所以本探针直连 `LLM_BASE_URL`。

落盘：_tmp_rootcause_probe/evidence/（临时，交付前清理）
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


def _real_prod_prompt():
    from agent.digital_life_persona import DigitalLifePersonaMixin
    from agent.system_prompt_manager import get_template

    class _P(DigitalLifePersonaMixin):
        def __init__(self):
            self._cached_tool_status = None
            self._cached_skill_instructions = None
            self._loaded_skill_ids = []

    status = _P()._build_tool_status_text()
    return get_template().format(
        current_date="2025年1月1日", body_status="（省略）", mode_name="正常",
        mode_description="正常运转", memory_context="（暂无记忆内容）",
        tool_status=status, skill_instructions="")


def send(tag, system_prompt, tools, note=""):
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
        "tag": tag, "note": note,
        "request": {"tools_present": bool(tools), "tools_count": len(tools),
                    "prompt": PROMPT, "system_prompt": system_prompt},
        "elapsed_ms": elapsed,
        "finish_reason": resp.choices[0].finish_reason,
        "message_content": msg.content,
        "message_reasoning_content": getattr(msg, "reasoning_content", None),
        "message_tool_calls": tcs,
        "dsml_hit": ("DSML" in content) or ("\uff5c" in content),
        "usage": resp.usage.model_dump() if getattr(resp, "usage", None) else None,
    }
    out = os.path.join(EVID, "probeC_%s_%d.json" % (tag, int(time.time())))
    with open(out, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2, default=str)
    print("=== %-30s tools=%-2d %s" % (tag, len(tools), note))
    print("    finish=%-10s DSML=%-5s tool_calls=%d content_len=%d"
          % (raw["finish_reason"], raw["dsml_hit"], len(tcs or []), len(content)))
    print("    content[:200] = %s" % repr(content[:200]))
    print("    -> %s" % out)
    return raw


if __name__ == "__main__":
    ok, bad = _bootstrap_tools()
    from agent.tools import get_tool_defs
    from agent.lines import line_whitelist
    wl, _res = line_whitelist(None)
    full = get_tool_defs(whitelist=wl)
    print("[bootstrap] ok=%d bad=%d defs=%d" % (len(ok), len(bad), len(full or [])))

    raw_prod = _real_prod_prompt()

    # ── 修复前（对照基线，重跑一次以确保与探针 B 同期可比）──
    before = send("C1_BEFORE_raw_notools", raw_prod, [], "修复前：原样生产提示词 + 无 tools")

    # ── 修复后：经生产代码里的同一个守卫函数对齐 ──
    from agent.tools_prompt_guard import align_system_prompt_with_tools, prompt_advertises_tools

    fixed_prompt, _ = align_system_prompt_with_tools(
        raw_prod, False, site="probeC", tools_count=0)
    print("\n[guard] 修复前 prompt 宣传工具 = %s" % prompt_advertises_tools(raw_prod))
    print("[guard] 修复后 prompt 宣传工具 = %s" % prompt_advertises_tools(fixed_prompt))
    print("[guard] 修复后提示词长度 = %d" % len(fixed_prompt))

    after = send("C2_AFTER_aligned_notools", fixed_prompt, [],
                 "修复后：经 tools_prompt_guard 对齐 + 无 tools")
    pos = send("C3_POSITIVE_raw_with_tools", raw_prod, full,
               "阳性对照：**未中和的**生产提示词 + 26 个真实 tools（应走结构化 tool_calls）")

    print("\n" + "=" * 76)
    print("%-32s %-8s %-7s %-10s %s" % ("条件", "tools", "DSML", "tool_calls", "结论"))
    print("=" * 76)
    verdict = []
    for r in (before, after, pos):
        verdict.append((r["tag"], r["dsml_hit"], len(r["message_tool_calls"] or [])))
    for tag, dsml, ntc in verdict:
        print("%-32s %-8s %-7s %-10d" % (
            tag, "", "是" if dsml else "否", ntc))
    print("=" * 76)
    if before["dsml_hit"] and not after["dsml_hit"]:
        print("✅ 对照成立：修复前复现 DSML，修复后同条件不再产生 DSML")
    elif not before["dsml_hit"]:
        print("⚠️ 修复前条件本次**未**复现 DSML（上游随机性）——需重跑以取得对照")
    else:
        print("❌ 修复无效：修复后仍出现 DSML")
