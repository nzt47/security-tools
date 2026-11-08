# -*- coding: utf-8 -*-
"""压缩调用能不能复用对话前缀？—— 只读量测探针（默认不出网）

背景（Owner 2026-11-08，交接条 compress-call-prefix-reuse）
------------------------------------------------------------
压缩（摘要）调用与对话（工作台 SSE）调用在构造上不是同一段前缀，实测 hit=0/57,385
⇒ 每次压缩都按全价付一次普通对话的钱。本探针回答「能复用多少」，不改任何生产代码。

三条判据（离线部分给出数字）
----------------------------
1. 现状公共前缀：压缩请求 = [system: 摘要指令] + 最近 100 条（无 tools）；
   对话请求 = [system: 工作台模板] + tools + 按预算保留的消息窗口。
   两者从第 0 个 token 就不同（system 不同；压缩还不带 tools）⇒ 公共前缀 0。
2. 可达前缀：只有同时满足「同一 system + 同一 tools + 同一起点的消息切片」，
   前缀才落在消息段里。切片起点不同（100 条 vs 预算窗口 N 条）⇒ 只剩 system+tools 块；
   让压缩改用对话那一段窗口，前缀才等于整条对话请求。
3. 成本换算：DeepSeek 前缀缓存命中价 = 全价 10%（PROMPT_CACHE_HIT_PRICE_RATIO）。

数字来源（不另写一套口径）
--------------------------
* 消息池：data/messages.jsonl（未分会话的旧池 = session_id 为 null）；
* token：memory.token_counter.TokenCounter（与 _select_within_budget 同一个计数器）；
* 预算：agent.chat_limits.resolve_context_budget / resolve_max_output_tokens
  （与 plugins/chat.py::_workbench_real_stream 同一对函数）；
* system 与 tools：data/llm_monitor_last.json（上一次真实请求落盘记录；
  prompt_cache_hit_tokens 就是「同一 system+tools 块」的实测命中量）；
* 压缩输入切片：memory.memory_manager.COMPRESS_INPUT_WINDOW，触发窗口
  COMPRESS_TRIGGER_WINDOW，指令文本取自 memory.summarizer.Summarizer.STRATEGIES
  与 memory.llm_service.LLMService._do_summarize 的默认系统提示（逐字同源）。

用法
----
    python -X utf8 scripts/probe_compress_prefix_reuse.py                 # 只算，不出网
    python -X utf8 scripts/probe_compress_prefix_reuse.py --session <id>  # 指定会话
    python -X utf8 scripts/probe_compress_prefix_reuse.py --live          # 再打 2 次真实调用验证
    python -X utf8 scripts/probe_compress_prefix_reuse.py --live --with-baseline   # 多验一次现状

--live 会用 .env 里的 LLM 凭据发真实请求（不打印任何凭据），只回报 usage 数字。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)

#: 前缀缓存命中价 / 全价（DeepSeek：命中 0.1×）
PROMPT_CACHE_HIT_PRICE_RATIO = 0.10
#: 编排窗口默认值（拿不到运行时配置时用；与 _resolve_context_window 的兜底同值）
WINDOW_DEFAULT = 131072
#: 触发比例（memory_manager 默认 compress_threshold）
TRIGGER_RATIO = 0.8
#: 每条消息的 chat-template 固定开销（只影响模型换算的小数点后一位）
MSG_OVERHEAD_TOKENS = 3
#: --live 默认的输入预算上限（给输出与模板留足余量，别把窗口顶穿）
LIVE_BUDGET_DEFAULT = 100000
#: --live 单次回复上限（只要 usage，不需要长回答）
LIVE_MAX_TOKENS = 64
#: --live 预热那一次只要几个 token
LIVE_WARM_MAX_TOKENS = 8
#: --ab 的回复上限（与 Summarizer.FIRST_TRY_TOKENS 同量级，便于比较「够不够出摘要」）
AB_MAX_TOKENS = 500
#: 关 thinking（口径 §6：本次只量前缀命中；且落盘历史里的 assistant 消息没有
#: reasoning_content，开着 thinking 回放会被上游 400 拒绝）
LIVE_EXTRA_BODY = {"thinking": {"type": "disabled"}}
#: 上次真实请求的落盘记录
RECORD_PATH = os.path.join(_ROOT, "data", "llm_monitor_last.json")
MESSAGES_PATH = os.path.join(_ROOT, "data", "messages.jsonl")


def _counter():
    from memory.token_counter import TokenCounter
    return TokenCounter()


def _production_constants():
    """(压缩输入窗口, 触发窗口, 来源) —— 优先取生产常量，取不到才回落到同值字面量。"""
    try:
        from memory.memory_manager import (COMPRESS_INPUT_WINDOW,
                                           COMPRESS_TRIGGER_WINDOW)
        return int(COMPRESS_INPUT_WINDOW), int(COMPRESS_TRIGGER_WINDOW), "memory.memory_manager"
    except Exception:  # noqa: BLE001 导入失败不阻断量测（值有注释锚点）
        return 100, 200, "(字面量兜底)"


def _instructions():
    """(压缩请求里实际出现的默认 system 文本, 策略指令) —— 与生产逐字同源。"""
    default_sys = "请将以下对话总结为核心要点，保留关键决策、问题和结论。要求简洁准确。"
    strategy = default_sys
    try:
        from memory.llm_service import LLMService
        import inspect
        src = inspect.getsource(LLMService._do_summarize)
        for line in src.splitlines():
            if "system_prompt = system_prompt or" in line and '"' in line:
                default_sys = line.split('"')[1]
                break
    except Exception:  # noqa: BLE001 取不到就用字面量（与源码同文）
        pass
    try:
        from memory.summarizer import Summarizer
        strategy = Summarizer.STRATEGIES.get("default", strategy)
    except Exception:  # noqa: BLE001
        pass
    return default_sys, strategy


def _load_messages(path):
    rows = []
    if not os.path.exists(path):
        return rows
    for line in io.open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:  # noqa: BLE001 坏行跳过（与生产读侧同策略）
            continue
    return rows


def _pick_pool(rows, session_id):
    """返回 (会话名, 消息列表)。session_id=None 表示未分会话的旧池（session 为 null）。"""
    if session_id:
        picked = [r for r in rows if str(r.get("session_id") or "") == session_id]
        return session_id, [r for r in picked if r.get("content")]
    legacy = [r for r in rows if not r.get("session_id") and r.get("content")]
    return "(legacy·未分会话)", legacy


def _load_record(path):
    if not os.path.exists(path):
        return {}
    try:
        with io.open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 坏记录按缺失处理
        return {}


def _mtok(counter, msgs):
    return sum(counter.count(str(m.get("content") or "")) for m in msgs)


def _keep_within_budget(counter, msgs, budget):
    """与 plugins/chat.py::_select_within_budget 同规则：从后往前装，装不下就停。"""
    if not msgs or budget <= 0:
        return list(msgs), 0
    kept, used = [], 0
    for m in reversed(msgs):
        cost = counter.count(str(m.get("content") or ""))
        if kept and used + cost > budget:
            break
        kept.append(m)
        used += cost
    kept.reverse()
    return kept, used


def _budget_for_chat(overhead_tokens):
    """工作台对话的历史预算（与 _workbench_real_stream 同一口径）。"""
    from agent.chat_limits import resolve_context_budget, resolve_max_output_tokens
    try:
        max_out = int(resolve_max_output_tokens(4096, "deepseek-flash"))
    except Exception:  # noqa: BLE001
        max_out = 8192
    budget = int(resolve_context_budget(
        WINDOW_DEFAULT, overhead_tokens=int(overhead_tokens),
        max_output_tokens=max_out))
    return budget, max_out


def offline(session_id):
    counter = _counter()
    sys_default, sys_strategy = _instructions()
    in_win, trig_win, win_src = _production_constants()
    rows = _load_messages(MESSAGES_PATH)
    name, pool = _pick_pool(rows, session_id)
    if not pool:
        print("!! 消息池为空：%s" % name)
        return {}

    rec = _load_record(RECORD_PATH)
    system_text = str(rec.get("system_prompt") or "")
    tools = rec.get("tools") or []
    measured_block = int(rec.get("prompt_cache_hit_tokens") or 0)

    body_tok = _mtok(counter, pool)
    trig_slice = pool[-trig_win:]
    trig_tok = _mtok(counter, trig_slice)
    comp_slice = pool[-in_win:]
    comp_tok = _mtok(counter, comp_slice)
    instr_tok = counter.count(sys_default) + counter.count(sys_strategy)

    if not measured_block:
        measured_block = counter.count(system_text) + counter.count(
            json.dumps(tools, ensure_ascii=False))
    budget, max_out = _budget_for_chat(measured_block)
    kept, kept_tok = _keep_within_budget(counter, pool, budget)

    comp_now = instr_tok + comp_tok + MSG_OVERHEAD_TOKENS * len(comp_slice)
    chat_now = measured_block + kept_tok + MSG_OVERHEAD_TOKENS * len(kept)

    # 压缩切片与对话窗口的最长公共后缀（用来证明「重叠是后缀、不是前缀」）
    overlap_msg = 0
    for i in range(1, min(len(comp_slice), len(kept)) + 1):
        if comp_slice[-i] != kept[-i]:
            break
        overlap_msg = i

    # D1：同 system+tools，切片仍是最近 in_win 条，指令放末尾
    d1_prompt = measured_block + comp_tok + MSG_OVERHEAD_TOKENS * len(comp_slice) + instr_tok
    d1_hit = measured_block
    # D2：同 system+tools，切片改成对话窗口，指令放末尾
    d2_prompt = measured_block + kept_tok + MSG_OVERHEAD_TOKENS * len(kept) + instr_tok
    d2_hit = measured_block + kept_tok + MSG_OVERHEAD_TOKENS * len(kept)

    def cost(hit, prompt):
        return PROMPT_CACHE_HIT_PRICE_RATIO * hit + max(0, prompt - hit)

    print("== A. 现状：两条请求长什么样（真实数据重建）")
    print("   会话：%s · %d 条 / %d 字符 / %d token（全池）" % (
        name, len(pool), sum(len(str(m.get("content") or "")) for m in pool), body_tok))
    print("   触发窗口（%d 条，%s）：%d token = 窗口 %d 的 %.1f%%（阈值 %d = %.1f%%）" % (
        trig_win, win_src, trig_tok, WINDOW_DEFAULT,
        100.0 * trig_tok / WINDOW_DEFAULT, int(WINDOW_DEFAULT * TRIGGER_RATIO),
        100.0 * TRIGGER_RATIO))
    print("   对话请求：system %d 字符 + tools %d 个 + 窗口 %d 条 ⇒ %d token" % (
        len(system_text), len(tools), len(kept), chat_now))
    print("             （历史预算 %d = 窗口 - (system+tools 块 %d) - 回复上限 %d）" % (
        budget, measured_block, max_out))
    print("   压缩请求（现在）：system 摘要指令 ×2 + 最近 %d 条（无 tools）⇒ %d token" % (
        len(comp_slice), comp_now))
    print("   两个 requests 的第一条 system 内容就已不同，且压缩不带 tools")
    print("   ⇒ 现状公共前缀 = 0 token（与实测 hit=0/57,385 一致）")
    print("   ⇒ 消息段的重叠是后缀关系：压缩的 %d 条全部落在对话窗口之内（后缀 %d 条）" % (
        len(comp_slice), overlap_msg))
    print("      后缀不算前缀：前缀缓存从第 0 个 token 比对，起点不同 ⇒ 之后全 miss。")

    print()
    print("== B. 可达前缀：三种改法")
    print("   D1 同 system+tools · 仍用最近 %d 条 · 指令放末尾" % in_win)
    print("      命中 %d（只有 system+tools 块）· prompt %d ⇒ 命中率 %.1f%%" % (
        d1_hit, d1_prompt, 100.0 * d1_hit / max(1, d1_prompt)))
    print("   D2 同 system+tools · 切片改用对话窗口 %d 条 · 指令放末尾" % len(kept))
    print("      命中 %d（= 整条对话请求）· prompt %d ⇒ 命中率 %.1f%%" % (
        d2_hit, d2_prompt, 100.0 * d2_hit / max(1, d2_prompt)))
    print("   注：对话请求的最后一条消息带「易变上下文」（检索/技能片段拼在问题前），")
    print("       压缩侧若原样用落盘消息，分歧点只在最后一条消息，命中量少那一条的 token。")

    print()
    print("== C. 每次压缩的成本（hit=%g×，miss=1×；单位 = 全价 token 当量）"
          % PROMPT_CACHE_HIT_PRICE_RATIO)
    c_now = cost(0, comp_now)
    c_d1 = cost(d1_hit, d1_prompt)
    c_d2 = cost(d2_hit, d2_prompt)
    print("   现状 %8.0f ｜ D1 %8.0f (%+.0f%%) ｜ D2 %8.0f (%+.0f%%)" % (
        c_now, c_d1, 100.0 * (c_d1 / c_now - 1), c_d2, 100.0 * (c_d2 / c_now - 1)))
    print("   D2 冷缓存（前缀已被淘汰）= 全价 %d ⇒ 比现状贵 %.0f%%" % (
        d2_prompt, 100.0 * (d2_prompt / c_now - 1)))
    print("   ⇒ 只有「压缩紧跟在本轮对话之后、前缀刚被这次请求写进缓存」时 D2 才划算。")

    return {
        "session": name, "messages": len(pool), "body_tokens": body_tok,
        "trigger_tokens": trig_tok, "compression_now": comp_now,
        "chat_now": chat_now, "budget": budget, "kept": len(kept),
        "kept_tokens": kept_tok, "block_tokens": measured_block,
        "d1": {"hit": d1_hit, "prompt": d1_prompt, "cost": round(c_d1)},
        "d2": {"hit": d2_hit, "prompt": d2_prompt, "cost": round(c_d2)},
        "now_cost": round(c_now), "suffix_overlap_msgs": overlap_msg,
        "system_text": system_text, "tools": tools,
    }


def _load_env(path):
    """把 .env 读进 os.environ（不打印任何值）。"""
    if not os.path.exists(path):
        return
    for line in io.open(path, encoding="utf-8"):
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        k = k.strip()
        if not k or k in os.environ:
            continue
        os.environ[k] = v.strip().strip('"').strip("'")


def live(info, with_baseline=False, live_budget=LIVE_BUDGET_DEFAULT):
    """打真实调用验证：预热对话请求 → 同前缀+指令 → 读 usage 的命中 token。"""
    from openai import OpenAI

    _load_env(os.path.join(_ROOT, ".env"))
    key = os.environ.get("LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or ""
    if not key:
        print("!! 未配置 LLM_API_KEY，跳过 --live")
        return
    base = (os.environ.get("LLM_BASE_URL") or "https://api.deepseek.com").rstrip("/")
    model = os.environ.get("LLM_MODEL") or "deepseek-flash"
    counter = _counter()
    rows = _load_messages(MESSAGES_PATH)
    _, pool = _pick_pool(rows, None)
    kept, kept_tok = _keep_within_budget(counter, pool, int(live_budget))
    system_text = info.get("system_text") or ""
    tools = info.get("tools") or []
    sys_default, _ = _instructions()

    client = OpenAI(api_key=key, base_url=base)
    print()
    print("== D. 真实调用验证（model=%s · system %d 字符 · tools %d 个 · 窗口 %d 条 / %d token）"
          % (model, len(system_text), len(tools), len(kept), kept_tok))

    def call(tag, messages, tools_arg, max_tokens):
        try:
            resp = client.chat.completions.create(
                model=model, messages=messages,
                **({"tools": tools_arg} if tools_arg else {}),
                max_tokens=max_tokens, temperature=0,
                extra_body=LIVE_EXTRA_BODY)
        except Exception as e:  # noqa: BLE001 探针要如实报错，但不回显凭据
            print("   %-24s !! %s: %s" % (tag, type(e).__name__, str(e)[:200]))
            return None
        u = getattr(resp, "usage", None)
        hit = int(getattr(u, "prompt_cache_hit_tokens", 0) or 0) if u else 0
        miss = int(getattr(u, "prompt_cache_miss_tokens", 0) or 0) if u else 0
        prompt = int(getattr(u, "prompt_tokens", 0) or 0) if u else 0
        ratio = (100.0 * hit / prompt) if prompt else 0.0
        print("   %-24s prompt=%6d hit=%6d miss=%6d ⇒ %.1f%%" % (tag, prompt, hit, miss, ratio))
        return {"prompt": prompt, "hit": hit, "miss": miss}

    warm_msgs = [{"role": "system", "content": system_text}] + [
        {"role": m.get("role") or "user", "content": str(m.get("content") or "")} for m in kept]
    warm = call("1) 对话请求(建前缀)", warm_msgs, tools, LIVE_WARM_MAX_TOKENS)
    if warm is None:
        return
    same_prefix = list(warm_msgs) + [{
        "role": "user",
        "content": "以上是我们此前的全部对话。请把它总结为核心要点，保留关键决策、问题和结论。",
    }]
    call("2) 压缩·同前缀+指令", same_prefix, tools, LIVE_MAX_TOKENS)
    if with_baseline:
        base_msgs = [{"role": "system", "content": sys_default}] + [
            {"role": m.get("role") or "user", "content": str(m.get("content") or "")}
            for m in pool[-100:]]
        call("3) 压缩·现状(全价)", base_msgs, None, LIVE_MAX_TOKENS)


def e2e(info, live_budget=LIVE_BUDGET_DEFAULT):
    """端到端验证：生产链路（MemoryManager._execute_compression）真的复用前缀。

    步骤：① 用与对话相同的 payload 打一次真实请求（建前缀）；
          ② 把这份 payload 登记进 agent/chat_prefix；
          ③ 打开开关、构造真实 MemoryManager，跑一次 _execute_compression；
          ④ 用**请求侧探针**读这次 summarize 真正拿到的 usage（llm_monitor 的 summarize
             记录拿不到 usage：_do_summarize 返回的是文本不是响应对象，见记录里的 0）。
    """
    import tempfile
    from openai import OpenAI

    _load_env(os.path.join(_ROOT, ".env"))
    key = os.environ.get("LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or ""
    if not key:
        print("!! 未配置 LLM_API_KEY，跳过 --e2e")
        return
    base = (os.environ.get("LLM_BASE_URL") or "https://api.deepseek.com").rstrip("/")
    model = os.environ.get("LLM_MODEL") or "deepseek-flash"
    counter = _counter()
    rows = _load_messages(MESSAGES_PATH)
    _, pool = _pick_pool(rows, None)
    kept, kept_tok = _keep_within_budget(counter, pool, int(live_budget))
    system_text = info.get("system_text") or ""
    tools = info.get("tools") or []
    sid = "probe-e2e"

    from agent import chat_prefix
    from memory.llm_service import LLMService

    client = OpenAI(api_key=key, base_url=base)
    msgs = [{"role": "system", "content": system_text}] + [
        {"role": m.get("role") or "user", "content": str(m.get("content") or "")} for m in kept]
    print()
    print("== F. 端到端（生产链路）：窗口 %d 条 / %d token" % (len(kept), kept_tok))
    warm = client.chat.completions.create(
        model=model, messages=msgs, tools=tools,
        max_tokens=LIVE_WARM_MAX_TOKENS, temperature=0, extra_body=LIVE_EXTRA_BODY)
    u = getattr(warm, "usage", None)
    print("   ① 建前缀 prompt=%s hit=%s" % (
        getattr(u, "prompt_tokens", "?"), getattr(u, "prompt_cache_hit_tokens", "?")))

    chat_prefix.record(sid, system_text, tools, msgs[1:], window_tokens=WINDOW_DEFAULT)
    os.environ[chat_prefix.ENV_FLAG] = "1"

    from memory.memory_manager import MemoryManager
    mgr = MemoryManager(config={
        "data_dir": tempfile.mkdtemp(prefix="probe-e2e-"),
        "token_limit": WINDOW_DEFAULT,
        "compress_threshold": 0.8,
        "llm": {"timeout": 180},
        "async_compress": {"enabled": False},
    })
    # 请求侧探针：包住 LLMService 的 client，把 summarize 的**响应对象**接住（拿真实 usage）
    captured = []

    class _SpyCompletions:
        def __init__(self, inner):
            self._inner = inner

        def create(self, **kwargs):
            resp = self._inner.create(**kwargs)
            captured.append({"kwargs": kwargs, "resp": resp})
            return resp

    class _SpyChat:
        def __init__(self, inner):
            self.completions = _SpyCompletions(inner.completions)

    class _SpyClient:
        def __init__(self, inner):
            self.chat = _SpyChat(inner.chat)

    _orig_get_client = LLMService._get_client
    LLMService._get_client = lambda self: _SpyClient(_orig_get_client(self))

    mgr._message_window.append({"role": "user", "content": "占位消息", "session_id": sid})
    ok = mgr._execute_compression(sid)
    print("   ② _execute_compression 返回 %s" % ok)

    if not captured:
        print("   !! 没接住 summarize 的请求（生产链路没走 LLMService？）")
        return
    sent = captured[-1]
    u = getattr(sent["resp"], "usage", None)
    prompt = int(getattr(u, "prompt_tokens", 0) or 0) if u else 0
    hit = int(getattr(u, "prompt_cache_hit_tokens", 0) or 0) if u else 0
    miss = int(getattr(u, "prompt_cache_miss_tokens", 0) or 0) if u else 0
    kw = sent["kwargs"]
    print("   ③ 生产链路这次 summarize：prompt=%d hit=%d miss=%d ⇒ %.1f%%（tools=%d，system==工作台 %s）"
          % (prompt, hit, miss, (100.0 * hit / prompt) if prompt else 0.0,
             len(kw.get("tools") or []),
             str((kw.get("messages") or [{}])[0].get("content") or "") == system_text))

def ab(info, live_budget=LIVE_BUDGET_DEFAULT, out_path=""):
    """A/B：同一段对话，两种压缩输入各压一次，比较「是否真出摘要」与长度/内容。

    旧路径 = [system 摘要指令][system 策略指令][最近 100 条]（无 tools）
    新路径 = [system 工作台][对话窗口][user 摘要指令]（同 tools）
    """
    from openai import OpenAI

    _load_env(os.path.join(_ROOT, ".env"))
    key = os.environ.get("LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or ""
    if not key:
        print("!! 未配置 LLM_API_KEY，跳过 --ab")
        return
    base = (os.environ.get("LLM_BASE_URL") or "https://api.deepseek.com").rstrip("/")
    model = os.environ.get("LLM_MODEL") or "deepseek-flash"
    counter = _counter()
    rows = _load_messages(MESSAGES_PATH)
    _, pool = _pick_pool(rows, None)
    kept, kept_tok = _keep_within_budget(counter, pool, int(live_budget))
    system_text = info.get("system_text") or ""
    tools = info.get("tools") or []
    sys_default, sys_strategy = _instructions()
    client = OpenAI(api_key=key, base_url=base)

    def _chat(messages, tools_arg):
        resp = client.chat.completions.create(
            model=model, messages=messages,
            **({"tools": tools_arg} if tools_arg else {}),
            max_tokens=AB_MAX_TOKENS, temperature=0,
            extra_body=LIVE_EXTRA_BODY)
        msg = resp.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None) or []
        u = getattr(resp, "usage", None)
        return {
            "text": (msg.content or "").strip(),
            "tool_calls": [getattr(tc.function, "name", "") for tc in tool_calls],
            "prompt": int(getattr(u, "prompt_tokens", 0) or 0) if u else 0,
            "completion": int(getattr(u, "completion_tokens", 0) or 0) if u else 0,
        }

    print()
    print("== E. A/B：同一段对话，两种压缩输入（窗口 %d 条 / %d token；model=%s）"
          % (len(kept), kept_tok, model))
    old_msgs = [{"role": "system", "content": sys_default},
                {"role": "system", "content": sys_strategy}] + [
        {"role": m.get("role") or "user", "content": str(m.get("content") or "")}
        for m in pool[-100:]]
    new_msgs = [{"role": "system", "content": system_text}] + [
        {"role": m.get("role") or "user", "content": str(m.get("content") or "")}
        for m in kept] + [{"role": "user", "content": sys_strategy}]

    results = {}
    for tag, msgs, t in (("old", old_msgs, None), ("d2", new_msgs, tools)):
        try:
            r = _chat(msgs, t)
        except Exception as e:  # noqa: BLE001 探针如实报错，不回显凭据
            print("   %-14s !! %s: %s" % (tag, type(e).__name__, str(e)[:200]))
            continue
        results[tag] = r
        print("   %-14s prompt=%6d completion=%4d tool_calls=%s len=%d"
              % (tag, r["prompt"], r["completion"], r["tool_calls"] or "-", len(r["text"])))
        print("                  head: %s" % r["text"][:120].replace("\n", " "))
    if out_path and results:
        try:
            import io as _io
            with _io.open(out_path, "w", encoding="utf-8") as f:
                json.dump({"window_messages": len(kept), "window_tokens": kept_tok,
                           "results": results}, f, ensure_ascii=False, indent=2)
            print("   已落盘：%s" % out_path)
        except Exception as e:  # noqa: BLE001 落盘失败不影响结论（屏上已有数字）
            print("   !! 落盘失败：%s" % e)


def main():
    ap = argparse.ArgumentParser(description="压缩调用能不能复用对话前缀（只读量测）")
    ap.add_argument("--session", default="", help="会话 id（默认：未分会话的旧池）")
    ap.add_argument("--live", action="store_true", help="再发真实调用验证命中（花钱）")
    ap.add_argument("--with-baseline", action="store_true",
                    help="--live 时多验一次现状（全价 100 条，约 6 万 token）")
    ap.add_argument("--live-budget", type=int, default=LIVE_BUDGET_DEFAULT,
                    help="--live 的消息 token 预算（默认 %d）" % LIVE_BUDGET_DEFAULT)
    ap.add_argument("--ab", action="store_true",
                    help="再跑一次压缩输入 A/B（旧路径 vs D2 各压一次，花钱）")
    ap.add_argument("--ab-out", default="",
                    help="--ab 时把两份输出落盘为 JSON（便于人评/归档）")
    ap.add_argument("--e2e", action="store_true",
                    help="端到端验证生产链路（建前缀 → MemoryManager 压缩 → 读监控命中）")
    args = ap.parse_args()
    info = offline(args.session.strip() or None)
    if args.live and info:
        live(info, with_baseline=args.with_baseline, live_budget=args.live_budget)
    if args.ab and info:
        ab(info, live_budget=args.live_budget, out_path=args.ab_out)
    if args.e2e and info:
        e2e(info, live_budget=args.live_budget)


if __name__ == "__main__":
    main()
