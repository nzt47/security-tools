# -*- coding: utf-8 -*-
"""压缩触发点 vs 缓存命中 的量测探针（**只读**，不改任何生产状态）

回答两个问题（Owner 2026-11-08）：
  ① 为什么「上下文窗口还没到 80%」就压缩？实际触发点到底是 80% 阈值、还是消息条数/字符预算？
  ② 缓存命中率的下滑，是「压缩改写历史」造成的，还是「自动选档换模式/换工具集」造成的？

结论与数字见 docs/handoff/rounds/ 与交接文档 §11.65；本文件只负责**可复现地**把数字再算一遍。

用法
----
    python -X utf8 scripts/probe_compress_vs_cache.py            # 全量（含 /api/llm-monitor 实时记录）
    python -X utf8 scripts/probe_compress_vs_cache.py --no-live  # 只算离线部分（不连服务）

口径（与生产代码同源，不另写一份）：
  · 触发判据：memory/memory_manager.py::_should_compress_now
        counted = 最近 200 条记忆里 user/assistant 的 content token（tool 结果单独计量）
        counted >= token_limit × compress_threshold            （默认 0.8）
  · floor 水位：_execute_compression 成功后把 **load_recent_messages(limit=100)** 的 counted 记成
        _compress_floor_tokens；下一次要求 counted >= floor × recompress_growth_ratio(1.10)
  · 界面占用率：plugins/memory.py::api_context_status → 本**会话** messages.jsonl 的 content token 之和 ÷ 编排窗口
  · 缓存命中：agent/llm_monitor.py 的 usage.prompt_cache_hit_tokens / prompt_cache_miss_tokens
"""
from __future__ import annotations

import argparse
import collections
import io
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)

TRIGGER_RATIO = 0.8
GROWTH_RATIO = 1.10
WINDOW_DEFAULT = 131072
TRIGGER_WINDOW = 200      # _should_compress_now: 最近 200 条
FLOOR_WINDOW = 100        # _execute_compression: load_recent_messages(limit=100)


def _counter():
    from memory.token_counter import TokenCounter
    return TokenCounter()


def _counted(counter, items):
    """(正文 token, 工具结果 token) —— 与 _compress_accounting 同口径"""
    body = 0
    tool = 0
    for m in items:
        n = counter.count(m.get("content") or "")
        if str(m.get("role")) == "tool" or m.get("tool_call_id"):
            tool += n
        else:
            body += n
    return body, tool


def _load_jsonl(path):
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


def part_a(counter, memory_path, window):
    """触发池是哪一份数据、当前水位多少"""
    msgs = _load_jsonl(memory_path)
    trigger = int(window * TRIGGER_RATIO)
    print("== A. 压缩触发池 = 全局记忆 %s（跨会话，无 session 字段）" % memory_path)
    print("   条数=%d · counted(all)=%d" % (len(msgs), _counted(counter, msgs)[0]))
    rows = {}
    for w in (20, FLOOR_WINDOW, TRIGGER_WINDOW):
        c, t = _counted(counter, msgs[-w:])
        rows[w] = c
        print("   counted(last%3d) = %7d = 窗口 %.1f%% · 阈值的 %.2f 倍" % (w, c, 100.0 * c / window, c / trigger))
    print("   触发阈值 = %.1f × %d = %d" % (TRIGGER_RATIO, window, trigger))
    for i in range(len(msgs)):
        c, _ = _counted(counter, msgs[max(0, i - TRIGGER_WINDOW + 1):i + 1])
        if c >= trigger:
            print("   首次越阈值：第 %d 条 · %s · counted=%d（%.1f%%）"
                  % (i + 1, msgs[i].get("timestamp"), c, 100.0 * c / window))
            break
    else:
        print("   尚未越阈值")
    return msgs, rows, trigger


def part_b(counter, sessions_dir, window):
    """界面上的百分比算的是哪一份数据"""
    idx_path = os.path.join(sessions_dir, "sessions.json")
    idx = []
    if os.path.exists(idx_path):
        idx = json.loads(io.open(idx_path, encoding="utf-8").read() or "[]")
    idx.sort(key=lambda s: s.get("updated_at", ""), reverse=True)
    print("\n== B. 界面分子/分母 = 最近一个有消息的**会话**")
    for s in idx[:5]:
        f = os.path.join(sessions_dir, s["id"], "messages.jsonl")
        if not os.path.exists(f):
            continue
        sm = _load_jsonl(f)
        tot = sum(counter.count(x.get("content") or "") for x in sm)
        print("   %s  %d 条 · %d tokens = 窗口 %.1f%%  updated=%s"
              % (s["id"], len(sm), tot, 100.0 * tot / window, s.get("updated_at")))
        return tot
    return 0


def part_c(window, session_tokens):
    """历史预算是否先到（消息条数/字符预算）"""
    from agent.chat_limits import resolve_context_budget, resolve_max_output_tokens
    mout = resolve_max_output_tokens(16384, "deepseek-flash")
    budget = resolve_context_budget(window, overhead_tokens=67 + 3799,
                                    max_output_tokens=mout, margin_tokens=512)
    print("\n== C. 历史预算（token 预算，不是条数）")
    print("   budget = %d - (系统 67 + 工具 3799) - 回复上限 %d - 余量 512 = %d"
          % (window, mout, budget))
    print("   当前历史 %d ⇒ 触发预算截断 = %s" % (session_tokens, session_tokens > budget))


def part_d(blackbox_path):
    """压缩事件的真实频次"""
    rows = []
    for rec in _load_jsonl(blackbox_path):
        if rec.get("event_type") in ("memory_compress", "memory_compress_empty"):
            rows.append((rec.get("timestamp"), rec.get("event_type")))
    rows.sort()
    if not rows:
        print("\n== D. 无压缩事件")
        return rows
    ok = sum(1 for r in rows if r[1] == "memory_compress")
    print("\n== D. 压缩事件 %d 次（成功 %d / 空摘要 %d）  %s → %s"
          % (len(rows), ok, len(rows) - ok, rows[0][0][:10], rows[-1][0][:10]))
    print("   按日：", dict(sorted(collections.Counter(r[0][:10] for r in rows).items())))
    return rows


def part_e(counter, msgs, trigger):
    """floor 与触发窗口不一致 ⇒ 闸门恒开"""
    c200 = _counted(counter, msgs[-TRIGGER_WINDOW:])[0]
    c100 = _counted(counter, msgs[-FLOOR_WINDOW:])[0]
    sim_floor = 0
    attempts = 0
    for i in range(len(msgs)):
        c, _ = _counted(counter, msgs[max(0, i - TRIGGER_WINDOW + 1):i + 1])
        if c < trigger:
            continue
        if sim_floor and c < int(sim_floor * GROWTH_RATIO):
            continue
        sim_floor = _counted(counter, msgs[max(0, i - FLOOR_WINDOW + 1):i + 1])[0]
        attempts += 1
    print("\n== E. floor 水位窗口失配")
    print("   触发看最近 %d 条（counted200=%d），floor 记的是最近 %d 条（counted100=%d）"
          % (TRIGGER_WINDOW, c200, FLOOR_WINDOW, c100))
    print("   闸门判据 counted200 >= floor×1.10 = %d ⇒ 恒成立" % int(c100 * GROWTH_RATIO))
    print("   按现有消息池模拟「每条消息都可触发的新增压缩」= %d 次" % attempts)


def _auth_token():
    env = os.path.join(_ROOT, ".env")
    if not os.path.exists(env):
        return ""
    for line in io.open(env, encoding="utf-8"):
        if line.startswith("FLASK_API_TOKEN="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def part_f(base_url):
    """逐条读 LLM 交互记录：命中率 + 前缀/工具集是否变化"""
    import urllib.request

    def _get(url):
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + _auth_token()})
        with urllib.request.urlopen(req, timeout=90) as resp:
            return json.loads(resp.read().decode("utf-8"))

    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
    except Exception:  # noqa: BLE001 无编码器时 token 列留空
        enc = None
    recs = _get(base_url + "/api/llm-monitor/records?limit=500").get("records", [])
    recs.sort(key=lambda r: r.get("timestamp") or 0)
    print("\n== F. LLM 交互记录 %d 条（环形缓冲 + 重启回填的最后一条）" % len(recs))
    tot_hit = tot_miss = 0
    zero = []
    for r in recs:
        hit = int(r.get("prompt_cache_hit_tokens") or 0)
        miss = int(r.get("prompt_cache_miss_tokens") or 0)
        sp = r.get("system_prompt") or ""
        tools = r.get("tools") or []
        ttok = len(enc.encode(json.dumps(tools, ensure_ascii=False))) if (enc and tools) else 0
        tot_hit += hit
        tot_miss += miss
        print("   %s src=%-12s prompt=%6d hit=%6d miss=%6d 命中率=%5.1f%%  sp=%3d字  tools=%2d/%5d tok"
              % (time.strftime("%H:%M:%S", time.localtime(r.get("timestamp") or 0)),
                 r.get("source"), r.get("usage_prompt_tokens") or 0, hit, miss,
                 (100.0 * hit / (hit + miss)) if (hit + miss) else 0.0,
                 len(sp), len(tools), ttok))
        if hit + miss and hit == 0:
            zero.append((r, miss))
    if tot_hit + tot_miss:
        print("   合计命中率 %.1f%%（hit=%d / miss=%d）"
              % (100.0 * tot_hit / (tot_hit + tot_miss), tot_hit, tot_miss))
    zm = sum(m for _, m in zero)
    if tot_miss:
        print("   零命中 %d 次，贡献 miss %d（占全部 miss 的 %.1f%%）"
              % (len(zero), zm, 100.0 * zm / tot_miss))
    for r, m in zero:
        print("      零命中 %s src=%s tools=%d sp=%d字 miss=%d"
              % (time.strftime("%H:%M:%S", time.localtime(r.get("timestamp") or 0)),
                 r.get("source"), len(r.get("tools") or []), len(r.get("system_prompt") or ""), m))
    return recs


def part_g(recs):
    """前缀可复用上限：检索档把「会话摘要+最近消息」塞进 system prompt"""
    if not recs:
        return
    cand = [r for r in recs if len(r.get("system_prompt") or "") > 100]
    if not cand:
        return
    r = cand[0]
    sp = r["system_prompt"]
    tag = "[工作记忆"
    if tag not in sp:
        return
    head = sp[:sp.index(tag)]
    prompt = r.get("usage_prompt_tokens") or 0
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        n = len(enc.encode(head))
    except Exception:  # noqa: BLE001
        n = 0
    print("\n== G. 检索/完整·流式档的前缀可复用上限")
    print("   system prompt 里 [工作记忆] 块起点 = 第 %d 字，块前只有 %d token" % (len(head), n))
    print("   该次 prompt=%d token ⇒ 块一变，可复用 ≤ %.2f%%" % (prompt, (100.0 * n / prompt) if prompt else 0))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-live", action="store_true", help="不连服务（只算离线部分）")
    ap.add_argument("--base-url", default="http://127.0.0.1:5678")
    ap.add_argument("--window", type=int, default=WINDOW_DEFAULT)
    args = ap.parse_args()

    counter = _counter()
    memory_path = os.path.join(_ROOT, "data", "messages.jsonl")
    msgs, _rows, trigger = part_a(counter, memory_path, args.window)
    session_tokens = part_b(counter, os.path.join(_ROOT, "data", "sessions"), args.window)
    part_c(args.window, session_tokens)
    part_d(os.path.join(_ROOT, "data", "blackbox", "blackbox_001.jsonl"))
    part_e(counter, msgs, trigger)
    if not args.no_live:
        recs = part_f(args.base_url)
        part_g(recs)


if __name__ == "__main__":
    main()
