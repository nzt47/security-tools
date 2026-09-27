# -*- coding: utf-8 -*-
r"""learn probe —— 扫描 DSH 会话库，产出 schema 报告（方案 P0）。

不产出业务数据，只做结构统计：事件类型占比 / top-N 工具 / payload 体量 /
base64 与图片 / 并行调用比例 / 配对完整性 / 失败信号 /（可选）脱敏命中。
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional

from ._common import (B64_RE_PAT, IMG_RE_PAT, NOISE_TYPES, ZSTD_MAGIC,
                      decompress_lines, iter_sessions, normalize_tool, pct,
                      result_text, scan_privacy, stats, ts_le)


def cmd_probe(args: argparse.Namespace) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if not os.path.isdir(args.sessions):
        print("[FATAL] 目录不存在: %s" % args.sessions, file=sys.stderr)
        return 2

    files = iter_sessions(args.sessions)
    if args.limit:
        files = files[: args.limit]

    types = collections.Counter()
    tools = collections.Counter()
    arg_lens: Dict[str, List[int]] = collections.defaultdict(list)
    res_lens: List[int] = []
    n_frames = n_records = n_parse_fail = 0
    n_calls = n_results = n_iserr = 0
    n_matched = n_unmatched_call = n_unmatched_res = 0
    n_reasoning = n_b64 = n_img = 0
    step_groups = parallel_groups = 0
    raw_bytes = 0
    err_files: List[str] = []

    t0 = time.time()
    for idx, path in enumerate(files, 1):
        try:
            with open(path, "rb") as fh:
                head = fh.read()
            raw_bytes += len(head)
            n_frames += len(re.findall(re.escape(ZSTD_MAGIC), head))
        except Exception:
            err_files.append(path)
            continue

        call_ids, res_ids = set(), set()
        step_map: collections.Counter = collections.Counter()
        for line in decompress_lines(path):
            try:
                o = json.loads(line)
            except Exception:
                n_parse_fail += 1
                continue
            if not ts_le(o.get("time"), args.snapshot_until):
                continue
            n_records += 1
            t = o.get("type", "NONE")
            types[t] += 1
            if t == "reasoning-chunks":
                n_reasoning += 1
            d = o.get("data")
            if not isinstance(d, dict):
                continue
            if t == "tool/call":
                n_calls += 1
                tools[normalize_tool(d.get("name"))] += 1
                if d.get("callId"):
                    call_ids.add(d["callId"])
                a = d.get("arguments")
                if isinstance(a, str):
                    arg_lens[normalize_tool(d.get("name"))].append(len(a))
                    if B64_RE_PAT.search(a):
                        n_b64 += 1
                    if IMG_RE_PAT.search(a):
                        n_img += 1
                step_map["%s:%s" % (d.get("turn", 0), d.get("step", 0))] += 1
            elif t == "tool/result":
                m = d.get("message")
                if not isinstance(m, dict):
                    continue
                n_results += 1
                src = m.get("source") or {}
                if isinstance(src, dict) and src.get("callId"):
                    res_ids.add(src["callId"])
                blocks = m.get("content")
                if isinstance(blocks, list):
                    for b in blocks:
                        if not isinstance(b, dict):
                            continue
                        if b.get("isError"):
                            n_iserr += 1
                        from ._common import result_text
                        txt = result_text(b)
                        if txt:
                            res_lens.append(len(txt))
                            if IMG_RE_PAT.search(txt):
                                n_img += 1
                            if B64_RE_PAT.search(txt):
                                n_b64 += 1
        n_matched += len(call_ids & res_ids)
        n_unmatched_call += len(call_ids - res_ids)
        n_unmatched_res += len(res_ids - call_ids)
        for v in step_map.values():
            step_groups += 1
            if v > 1:
                parallel_groups += 1
        if args.verbose and idx % 100 == 0:
            print("  ... %d/%d" % (idx, len(files)), file=sys.stderr)

    noise = sum(types[k] for k in NOISE_TYPES)
    rep: Dict[str, Any] = {
        "meta": {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S %z"),
                 "sessions_root": args.sessions, "files": len(files),
                 "read_errors": len(err_files), "elapsed_sec": round(time.time() - t0, 1)},
        "corpus": {"compressed_mb": round(raw_bytes / 1048576, 1),
                   "zstd_frames": n_frames, "records": n_records,
                   "parse_fail": n_parse_fail},
        "event_types": {"total_distinct": len(types), "counts": types.most_common()},
        "noise": {"noise_records": noise, "noise_share": pct(noise, n_records),
                  "reasoning_share": pct(n_reasoning, n_records)},
        "tools": {"total_distinct": len(tools), "top": tools.most_common(30)},
        "pairing": {"calls": n_calls, "results": n_results, "matched": n_matched,
                    "unmatched_call": n_unmatched_call, "unmatched_result": n_unmatched_res,
                    "match_rate": pct(n_matched, n_calls)},
        "parallel": {"step_groups": step_groups, "parallel_groups": parallel_groups,
                     "parallel_rate": pct(parallel_groups, step_groups)},
        "failure": {"isError": n_iserr},
        "payload_bytes": {k: stats(v) for k, v in sorted(arg_lens.items()) if len(v) >= 20},
        "result_bytes": stats(res_lens),
        "base64_or_image": {"base64_hits": n_b64, "image_hits": n_img},
    }
    if args.privacy:
        rep["privacy"] = scan_privacy(files)

    md = _to_markdown(rep, args.topn)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(md)
        print("[ok] Markdown -> %s" % args.out)
    else:
        print(md)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, ensure_ascii=False, indent=2)
        print("[ok] JSON -> %s" % args.json_out)
    c = rep["corpus"]
    print("[summary] files=%d records=%s parse_fail=%d noise=%s pairing=%s"
          % (len(files), format(c["records"], ","), c["parse_fail"],
             rep["noise"]["noise_share"], rep["pairing"]["match_rate"]), file=sys.stderr)
    return 0


def _to_markdown(r: Dict[str, Any], topn: int) -> str:
    m, c = r["meta"], r["corpus"]
    B = chr(96)
    L: List[str] = []
    A = L.append
    A("# P0 Schema 报告 —— DSH 历史会话库（yunshu learn probe 生成）\n")
    A("- 生成时间：%s" % m["generated_at"])
    A("- 会话根目录：%s%s%s" % (B, m["sessions_root"], B))
    A("- 会话文件：**%d**；读取失败 %d；耗时 %s s\n" % (m["files"], m["read_errors"], m["elapsed_sec"]))
    A("## 一、语料规模\n")
    A("| 项 | 值 |\n|---|---|")
    A("| 压缩体积 | **%s MB** |" % c["compressed_mb"])
    A("| zstd 帧总数 | **%s** |" % format(c["zstd_frames"], ","))
    A("| 记录总数 | **%s** |" % format(c["records"], ","))
    A("| JSON 解析失败 | **%d** |\n" % c["parse_fail"])
    A("## 二、事件类型及占比\n")
    A("| 类型 | 条数 | 占比 |\n|---|---|---|")
    for t, n in r["event_types"]["counts"]:
        A("| %s%s%s | %s | %s |" % (B, t, B, format(n, ","), pct(n, c["records"])))
    A("")
    nz = r["noise"]
    A("**噪声占比 %s**（%s 条）；思考链占 **%s**。\n"
      % (nz["noise_share"], format(nz["noise_records"], ","), nz["reasoning_share"]))
    A("## 三、Top-%d 工具名（共 %d 种）\n" % (topn, r["tools"]["total_distinct"]))
    A("| # | 工具 | 调用数 |\n|---|---|---|")
    for i, (t, n) in enumerate(r["tools"]["top"][:topn], 1):
        A("| %d | %s%s%s | %s |" % (i, B, t, B, format(n, ",")))
    A("")
    A("## 四、Payload 体量\n")
    A("| 工具 | n | min | p50 | p90 | max | mean |\n|---|---|---|---|---|---|---|")
    for k, v in r["payload_bytes"].items():
        if v:
            A("| %s%s%s | %s | %d | %d | %d | %d | %d |"
              % (B, k, B, format(v["n"], ","), v["min"], v["p50"], v["p90"], v["max"], v["mean"]))
    rb = r["result_bytes"]
    if rb:
        A("| %stool/result 文本%s | %s | %d | %d | %d | %d | %d |"
          % (B, B, format(rb["n"], ","), rb["min"], rb["p50"], rb["p90"], rb["max"], rb["mean"]))
    A("")
    A("## 五、base64 / 图片\n")
    b = r["base64_or_image"]
    A("- base64 命中 **%d**；data:image/ 命中 **%d**\n" % (b["base64_hits"], b["image_hits"]))
    p = r["parallel"]
    A("## 六、并行调用比例\n")
    A("- step 分组 %s；含 >1 调用 %s；**比例 %s**\n"
      % (format(p["step_groups"], ","), format(p["parallel_groups"], ","), p["parallel_rate"]))
    q = r["pairing"]
    A("## 七、配对完整性\n")
    A("| 项 | 值 |\n|---|---|")
    A("| tool/call | %s |" % format(q["calls"], ","))
    A("| tool/result | %s |" % format(q["results"], ","))
    A("| 成功配对 | **%s** |" % format(q["matched"], ","))
    A("| 配对率 | **%s** |\n" % q["match_rate"])
    A("配对键：%stool/call.data.callId%s ↔ %stool/result.data.message.source.callId%s\n" % (B, B, B, B))
    A("## 八、失败信号\n")
    A("- content[].isError == true：**%s**\n" % format(r["failure"]["isError"], ","))
    if r.get("privacy"):
        pv = r["privacy"]
        A("## 九、脱敏命中统计（仅计数，无明文）\n")
        A("| 模式 | 命中数 |\n|---|---|")
        for k, v in pv.get("hard_block", []):
            A("| %s%s%s | %s |" % (B, k, B, format(v, ",")))
        A("")
        A("| 替换模式 | 命中数 |\n|---|---|")
        for k, v in pv.get("redact_replace", []):
            A("| %s%s%s | %s |" % (B, k, B, format(v, ",")))
        A("")
    A("---\n")
    A("*本报告由 yunshu learn probe 生成；全程只读，不含会话原文。*")
    return "\n".join(L)
