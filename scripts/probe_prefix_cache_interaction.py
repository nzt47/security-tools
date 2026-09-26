# -*- coding: utf-8 -*-
"""F3 探针：用**真实服务**的数据裁决「工具宣告行（tool_status）vs DeepSeek 前缀缓存」。

背景（docs/audit_skill_governance/FINDINGS_DURING_IMPL.md · F3）
------------------------------------------------------------------
agent/system_prompt_config.py:435-439 把 tool_status 刻意放在**稳定节**
（身份 → 原则 → 技能指令 → **工具状态** → 身体状态/模式/日期 → 记忆线索）。
B1 把该行的内容从「注册表全量」改成「**本轮真正下发的 tool_defs**」，
于是「某条链路的当轮下发集若逐请求变化 ⇒ 稳定节里这一行也逐请求变化 ⇒
其后的全部内容无法命中前缀缓存」成为一个待裁决的风险。

F3 的三条待验证动作：(a) 主线路径该行是否恒定；(b) 工作台 SSE 路径该行是否变化
（变了则量化偏移与「其后还有多少」）；(c) 用真实 usage.prompt_cache_hit_tokens
对比「该行恒定」与「该行变化」两种情形。

本探针怎么拿到「实际发给模型的那个 system prompt」
--------------------------------------------------
**不靠猜、不靠复算**：agent/llm_monitor.py 的 LLMInteraction.system_prompt
就是 messages[0]["content"]（_wrap_get_client_for_tool_calling 里取自真请求 kwargs），
而 /api/llm-monitor/records 返回 asdict(record)。
⇒ 探针只要发**真实 HTTP 请求**，再读该端点，就拿到**真请求里的真提示词**。
（附带拿到 tools 数组长度、usage 真值、时间戳 —— 缓存命中率与间隔一并实测。）

本文件只读 + 发请求，**不改任何生产代码**。

用法
----
    # 服务已在 5678 起好后：
    python -X utf8 scripts/probe_prefix_cache_interaction.py --interval 6

    # 只跑某一组（ident=相同请求 / vary=同意图不同表述 / workbench=SSE 工作台）
    python -X utf8 scripts/probe_prefix_cache_interaction.py --cases ident,vary,workbench
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.error
import urllib.request

#: 工具宣告行的判定标记（与 agent/tools_prompt_guard.TOOL_ADVERT_MARKERS 同源）。
#: 【不易】这里**硬编码同一子串**是刻意的：探针认的是"模型真看到的那一行"，
#:  不能 import 生产模块 —— 否则生产把渲染口径改坏时，探针会跟着一起改坏
#:  （"用被测物自证被测物"）。子串若在未来漂移，探针会直接报"无【工具】行"。
ADVERT_MARKER = "【工具】"

#: (a) 的三次**完全相同**的请求。
#: 【为什么带"不要调用任何工具"】本卡预算 6~12 次真实出网调用；一次 /api/chat 若触发
#:   工具循环会变成 4 次调用（实测：列目录问题 = 4 条 usage 记录）。该指令不影响
#:   "系统提示词渲染"这个被测对象（宣告行在模型看到它**之前**就已渲染进提示词），
#:   只是把每组请求收敛到 1 次调用。
IDENT_QUESTION = "不要调用任何工具，请用一句话说明前缀缓存（prefix cache）的工作原理"

#: (b) 工作台 SSE 组的三条请求：**卡点原文给的例子**（同意图、不同表述）
WB_QUESTIONS = ["列出当前目录", "看看有哪些文件", "当前文件夹里有什么"]

#: (c) 主线组的「同意图不同表述」三条请求（与 IDENT 同题目、同长度量级，
#: 但与 IDENT 用词不同 —— 这样两组的总 prompt 长度可比，符合"控制变量"要求）
VARY_QUESTIONS = [
    "不要调用任何工具，请一句话说明前缀缓存（prefix cache）为什么能省钱",
    "不要调用任何工具，一句话讲讲前缀缓存（prefix cache）省钱的道理",
    "不要调用任何工具，前缀缓存（prefix cache）为什么省钱，一句话说清",
]


def _post_json(url, payload, timeout=300):
    """POST JSON，返回 (status, text)。HTTP 错误也返回响应体（不抛）。"""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def _get_text(url, timeout=60):
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def wait_ready(base_url, timeout_s=180.0):
    """等 /api/health 可用，返回 (ok, waited_s, last_detail)"""
    t0 = time.time()
    last = ""
    while time.time() - t0 < timeout_s:
        try:
            st, _body = _get_text(base_url + "/api/health", timeout=5)
            last = "http=%d" % st
            if st == 200:
                return True, time.time() - t0, last
        except Exception as e:  # noqa: BLE001 未就绪时连接被拒是正常的
            last = "%s: %s" % (type(e).__name__, e)
        time.sleep(2.0)
    return False, time.time() - t0, last


def metrics_read(base_url):
    """读 /metrics 里本卡关心的几行（原始行原样带回，不做二次换算）"""
    st, body = _get_text(base_url + "/metrics", timeout=30)
    if st != 200:
        return {"status": st, "lines": {}}
    out = {}
    for ln in body.splitlines():
        s = ln.strip()
        if s.startswith("cache_hit_ratio"):
            out["cache_hit_ratio"] = s
        elif s.startswith('llm_tokens_total{kind="cached"}'):
            out["llm_tokens_total_cached"] = s
        elif s.startswith('llm_tokens_total{kind="prompt"}'):
            out["llm_tokens_total_prompt"] = s
    return {"status": st, "lines": out}


def fetch_records(base_url, limit=200):
    st, body = _get_text(base_url + "/api/llm-monitor/records?limit=%d" % limit, timeout=60)
    if st != 200:
        raise RuntimeError("records 端点返回 %d: %s" % (st, body[:300]))
    return json.loads(body).get("records", [])


def _tokenizer():
    """cl100k_base（与 agent/tools_prompt_guard.TOOL_DEFS_TOKEN_ENCODING 同口径）；
    不可用返回 None（**绝不退化成字符数÷3**）。"""
    try:
        import tiktoken
        return tiktoken.get_encoding("cl100k_base")
    except Exception:
        return None


def advert_location(system_prompt, enc=None):
    """定位宣告行：是否存在、行文本、字符偏移、其后还有多少字符 / token。

    offset 口径 = **该行第一个字符**在 system_prompt 里的 0 基字符下标
    （该行之前所有行长度 + 换行符）。前缀缓存按「最长公共前缀」命中，
    该行一变，命中最多只能保到该行**之前** —— 这正是要量化的损失面。
    """
    sp = system_prompt or ""
    lines = sp.split("\n")
    idx = None
    for i, ln in enumerate(lines):
        if ADVERT_MARKER in ln:
            idx = i
            break
    info = {
        "present": idx is not None,
        "sys_chars": len(sp),
        "sys_tokens": (len(enc.encode(sp)) if enc is not None else None),
        "line": "",
        "line_index": None,
        "offset": None,
        "chars_from_line": None,
        "chars_after_line": None,
        "tokens_from_line": None,
    }
    if idx is None:
        return info
    offset = sum(len(l) + 1 for l in lines[:idx])
    line = lines[idx]
    info.update({
        "line": line,
        "line_index": idx,
        "offset": offset,
        "chars_from_line": len(sp) - offset,
        "chars_after_line": len(sp) - (offset + len(line)),
        "tokens_from_line": (len(enc.encode(sp[offset:])) if enc is not None else None),
    })
    return info


def _h16(s):
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()[:16].upper()


def analyze_record(r, enc=None):
    """把一条监控记录压成本卡需要的字段（只留宣告行，不打印整段提示词）"""
    sp = r.get("system_prompt") or ""
    loc = advert_location(sp, enc)
    msgs = r.get("messages") or []
    last_user = ""
    for m in reversed(msgs):
        if isinstance(m, dict) and m.get("role") == "user":
            last_user = str(m.get("content") or "")[:60]
            break
    hit = int(r.get("prompt_cache_hit_tokens") or 0)
    miss = int(r.get("prompt_cache_miss_tokens") or 0)
    return {
        "id": r.get("id"),
        "ts": float(r.get("timestamp") or 0.0),
        "ts_str": r.get("timestamp_full") or "",
        "session_id": r.get("session_id") or "",
        "source": r.get("source") or "",
        "model": r.get("model") or "",
        "last_user": last_user,
        "n_tools": len(r.get("tools") or []),
        "usage_available": bool(r.get("usage_available")),
        "prompt_tokens": int(r.get("usage_prompt_tokens") or 0),
        "hit": hit,
        "miss": miss,
        "ratio": (hit / (hit + miss)) if (hit + miss) > 0 else None,
        "cache_reported": bool(r.get("cache_reported")),
        "sys_len": len(sp),
        "sys_sha16": _h16(sp),
        "advert": loc,
    }


def fmt_row(i, a, prev_ts):
    loc = a["advert"]
    gap = (a["ts"] - prev_ts) if prev_ts else None
    return (
        "  [%d] ts=%s src=%-12s sess=%-18s tools=%-3d prompt=%6d hit=%6d miss=%6d "
        "ratio=%s gap=%s\n"
        "      sys_chars=%d sys_tokens=%s advert_present=%s advert_off=%s "
        "chars_from_line=%s tokens_from_line=%s sys_sha16=%s\n"
        "      advert_line=%s"
        % (
            i, a["ts_str"], a["source"], a["session_id"], a["n_tools"],
            a["prompt_tokens"], a["hit"], a["miss"],
            ("%.2f%%" % (a["ratio"] * 100)) if a["ratio"] is not None else "n/a",
            ("%.1fs" % gap) if gap is not None else "n/a",
            loc["sys_chars"], loc["sys_tokens"], loc["present"], loc["offset"],
            loc["chars_from_line"], loc["tokens_from_line"], a["sys_sha16"],
            (loc["line"][:200] if loc["present"] else "<无工具宣告行>"),
        )
    )


def summarize(rows):
    hit = sum(a["hit"] for a in rows)
    miss = sum(a["miss"] for a in rows)
    prompts = [a["prompt_tokens"] for a in rows if a["prompt_tokens"]]
    gaps = []
    for i in range(1, len(rows)):
        if rows[i]["ts"] and rows[i - 1]["ts"]:
            gaps.append(rows[i]["ts"] - rows[i - 1]["ts"])
    lines = set(a["advert"]["line"] for a in rows if a["advert"]["present"])
    return {
        "n": len(rows),
        "sum_hit": hit,
        "sum_miss": miss,
        "ratio_sum": (hit / (hit + miss)) if (hit + miss) > 0 else None,
        "ratio_mean": (sum(a["ratio"] for a in rows if a["ratio"] is not None)
                       / max(1, len([a for a in rows if a["ratio"] is not None]))),
        "prompt_min": min(prompts) if prompts else None,
        "prompt_max": max(prompts) if prompts else None,
        "prompt_mean": (sum(prompts) / len(prompts)) if prompts else None,
        "gap_min": min(gaps) if gaps else None,
        "gap_max": max(gaps) if gaps else None,
        "gap_mean": (sum(gaps) / len(gaps)) if gaps else None,
        "distinct_advert_lines": sorted(lines),
        "distinct_tool_counts": sorted(set(a["n_tools"] for a in rows)),
        "distinct_sys_sha16": sorted(set(a["sys_sha16"] for a in rows)),
    }


def assign_records_to_requests(recs, timeline, pad_before=1.0, pad_after=2.0):
    """把监控记录按**请求时间线**归组（纯函数，离线可测）。

    Args:
        recs: 监控记录列表（每条含 timestamp 秒）。
        timeline: [(序号, 问题, 请求开始秒, 请求结束秒), ...]。
        pad_before/pad_after: 请求窗口的松弛量（秒）。记录的 timestamp 是
            **LLM 调用完成**时刻，必然落在 [start, end] 内，松弛只为兜住时钟抖动。

    Returns:
        (groups, unassigned)：groups 是 [(timeline项, [记录...]), ...]（保持 timeline 顺序，
        组内按时间升序）；unassigned 是不落在任何请求窗口内的记录。

    【不易】为什么必须按**请求时间线**归组，而不是按"组的开始时间 + 固定窗口"：
        本卡实测踩过这个坑 —— 组 A 的第 3 次请求的调用记录落在下一组的窗口里
        （原始输出里 ident=2 条 / vary=4 条，正确口径是 3 / 3），
        会让"该行是否逐字相同"的判据用错样本。窗口是请求级的，不是组级的。
    """
    groups = []
    used = set()
    for item in timeline:
        lo = float(item[2]) - float(pad_before)
        hi = float(item[3]) + float(pad_after)
        hit = []
        for k, r in enumerate(recs):
            ts = float(r.get("timestamp") or 0.0)
            if lo <= ts <= hi:
                hit.append(r)
                used.add(k)
        hit.sort(key=lambda r: float(r.get("timestamp") or 0.0))
        groups.append((item, hit))
    unassigned = [r for k, r in enumerate(recs) if k not in used]
    return groups, unassigned


def run_case_main(base_url, session, questions, interval, label, out):
    """主线路径（POST /api/chat）：逐条发请求（组内固定间隔）"""
    out.append("")
    out.append("=" * 100)
    out.append("== 组 %s：%d 次请求，session=%s，请求间隔目标=%.1fs"
               % (label, len(questions), session, interval))
    out.append("=" * 100)
    t0 = time.time()
    timeline = []
    for i, q in enumerate(questions):
        if i:
            time.sleep(interval)
        req_ts = time.time()
        st, body = _post_json(base_url + "/api/chat",
                              {"message": q, "session": session}, timeout=600)
        req_done = time.time()
        timeline.append((i + 1, q, req_ts, req_done))
        try:
            obj = json.loads(body)
            resp_txt = str(obj.get("response") or "")[:80].replace("\n", " ")
        except Exception:
            resp_txt = body[:80].replace("\n", " ")
        out.append("  -> #%d http=%d 耗时=%.1fs q=%r resp=%s"
                   % (i + 1, st, req_done - req_ts, q, resp_txt))
    return t0, timeline


def run_case_workbench(base_url, session, questions, interval, out):
    """工作台 SSE 路径（POST /api/chat/stream）：同意图、不同表述"""
    out.append("")
    out.append("=" * 100)
    out.append("== 组 W（工作台 SSE /api/chat/stream）：%d 次请求，session=%s，间隔目标=%.1fs"
               % (len(questions), session, interval))
    out.append("=" * 100)
    t0 = time.time()
    timeline = []
    for i, q in enumerate(questions):
        if i:
            time.sleep(interval)
        req_ts = time.time()
        st, body = _post_json(base_url + "/api/chat/stream",
                              {"message": q, "session_id": session}, timeout=600)
        req_done = time.time()
        timeline.append((i + 1, q, req_ts, req_done))
        n_chunk = body.count('"type": "chunk"') + body.count('"type":"chunk"')
        out.append("  -> #%d http=%d 耗时=%.1fs q=%r sse_chunks=%d sse_bytes=%d"
                   % (i + 1, st, req_done - req_ts, q, n_chunk, len(body)))
    return t0, timeline


def main(argv=None):
    ap = argparse.ArgumentParser(description="F3 探针：宣告行 vs 前缀缓存（真实服务）")
    ap.add_argument("--base-url", default="http://127.0.0.1:5678")
    ap.add_argument("--interval", type=float, default=6.0,
                    help="同一组内两次请求的目标间隔（秒）—— 命中率强依赖间隔，必须作为对照变量")
    ap.add_argument("--cases", default="ident,vary,workbench")
    ap.add_argument("--tag", default=time.strftime("%H%M%S"))
    ap.add_argument("--ready-timeout", type=float, default=180.0)
    ap.add_argument("--json-out", default="")
    args = ap.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    enc = _tokenizer()

    out = []
    out.append("### F3 探针原始输出 ###")
    out.append("started_at=%s base_url=%s interval=%.1fs cases=%s tag=%s"
               % (time.strftime("%Y-%m-%d %H:%M:%S"), args.base_url, args.interval,
                  args.cases, args.tag))
    out.append("tokenizer=%s"
               % ("tiktoken/cl100k_base" if enc is not None else "不可用（token 列将为 None）"))

    ok, waited, detail = wait_ready(args.base_url, args.ready_timeout)
    out.append("wait_ready ok=%s waited=%.1fs detail=%s" % (ok, waited, detail))
    if not ok:
        out.append("服务未就绪，探针中止。")
        print("\n".join(out))
        return 2

    m_before = metrics_read(args.base_url)
    out.append("")
    out.append("--- /metrics 快照（探针开始前）---")
    out.append(json.dumps(m_before, ensure_ascii=False, indent=2))

    cases = [c.strip() for c in args.cases.split(",") if c.strip()]
    markers = {}
    if "ident" in cases:
        s = "f3-ident-%s" % args.tag
        t, tl = run_case_main(args.base_url, s, [IDENT_QUESTION] * 3, args.interval,
                              "A 相同请求(主线)", out)
        markers["ident"] = (s, t, tl)
    if "vary" in cases:
        s = "f3-vary-%s" % args.tag
        t, tl = run_case_main(args.base_url, s, list(VARY_QUESTIONS), args.interval,
                              "B 同意图不同表述(主线)", out)
        markers["vary"] = (s, t, tl)
    if "workbench" in cases:
        s = "f3-wb-%s" % args.tag
        t, tl = run_case_workbench(args.base_url, s, list(WB_QUESTIONS), args.interval, out)
        markers["workbench"] = (s, t, tl)

    m_after = metrics_read(args.base_url)
    out.append("")
    out.append("--- /metrics 快照（探针结束后）---")
    out.append(json.dumps(m_after, ensure_ascii=False, indent=2))

    recs = fetch_records(args.base_url, limit=200)
    out.append("")
    out.append("monitor records 总数=%d（各组按**请求时间线**归组，见下）" % len(recs))

    result = {"cases": {}, "metrics_before": m_before, "metrics_after": m_after, "raw_out": out}
    # 【口径】按**请求时间线**归组（不按 session_id：实测本部署 /api/chat 的监控记录
    #   session_id 恒为 ""，按 session 过滤会得到空集 = 假"无记录"；
    #   也不按"组的开始时间+固定窗口"：那会把组的边界算错，见 assign_records_to_requests 注释）。
    order = [n for n in ("ident", "vary", "workbench") if n in markers]
    for name in order:
        sess, t0, tl = markers[name]
        grouped, unassigned = assign_records_to_requests(recs, tl)
        sel = [r for _item, part in grouped for r in part]
        sel.sort(key=lambda r: float(r.get("timestamp") or 0))
        rows = [analyze_record(r, enc) for r in sel]
        out.append("")
        out.append("-" * 100)
        out.append("组 %s（session=%s）：命中 %d 条监控记录（按请求时间线归组；未归属 %d 条）"
                   % (name, sess, len(rows), len(unassigned)))
        out.append("  请求时间线: " + "; ".join(
            "#%d [%.1f..%.1f] %r -> %d 条调用"
            % (it[0], it[2], it[3], it[1], len(part)) for it, part in grouped))
        out.append("-" * 100)
        prev = None
        for i, a in enumerate(rows, 1):
            out.append(fmt_row(i, a, prev))
            prev = a["ts"] or None
        sm = summarize(rows)
        out.append("  小结 %s: %s" % (name, json.dumps(sm, ensure_ascii=False)))
        if sm["distinct_advert_lines"]:
            out.append("  宣告行去重后 %d 种；逐字相同=%s"
                       % (len(sm["distinct_advert_lines"]),
                          "是" if len(sm["distinct_advert_lines"]) == 1 else "否"))
        else:
            out.append("  该组记录里不存在工具宣告行（advert_present 全为 False）")
        result["cases"][name] = {"session": sess, "summary": sm, "rows": rows}

    out.append("")
    out.append("=" * 100)
    out.append("== 对照表（间隔 / 总 prompt 长度必须与命中率并列报告）")
    out.append("=" * 100)
    out.append("%-10s %-4s %-20s %-11s %-16s %-14s %s"
               % ("组", "n", "Sigma hit/(hit+miss)", "prompt均值", "间隔 min/mean/max",
                  "advert行种数", "tools数种数"))
    for name in order:
        sm = result["cases"][name]["summary"]
        out.append("%-10s %-4d %-20s %-11s %-16s %-14d %s"
                   % (name, sm["n"],
                      ("%.2f%%" % (sm["ratio_sum"] * 100)) if sm["ratio_sum"] is not None else "n/a",
                      ("%.0f" % sm["prompt_mean"]) if sm["prompt_mean"] else "n/a",
                      "%s/%s/%s" % (("%.1f" % sm["gap_min"]) if sm["gap_min"] else "-",
                                    ("%.1f" % sm["gap_mean"]) if sm["gap_mean"] else "-",
                                    ("%.1f" % sm["gap_max"]) if sm["gap_max"] else "-"),
                      len(sm["distinct_advert_lines"]),
                      sm["distinct_tool_counts"]))
    out.append("finished_at=%s" % time.strftime("%Y-%m-%d %H:%M:%S"))

    text = "\n".join(out)
    print(text)
    if args.json_out:
        try:
            with open(args.json_out, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            print("[json] %s" % args.json_out)
        except Exception as e:  # noqa: BLE001 落盘失败不影响主输出
            print("[json] 落盘失败: %s" % e)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
