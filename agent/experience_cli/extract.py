# -*- coding: utf-8 -*-
r"""learn extract —— DSH 会话 -> 经验样本（方案 P1：extract + distill）。

六条规则（已按 P0 实测修正，见方案「二·补」节）：
  1. 同 turn 同 file_path 只留最后一次 write/edit -> 主样本
  2. tool/result 与 tool/code-dispatch 的 isError / 被覆盖的 edit / 含 revert|rollback -> pitfalls
  3. read 全文、测试长输出 -> 分级截断（edit 全留；write 超限截断）
  4. 技术栈推断：改动文件后缀 + package.json / pyproject.toml / go.mod 内容
  5. 脱敏【分级】：硬阻断命中即拒收；替换集做脱敏替换
  6. diff 去重：sha256(file_path + 规范化 diff 内容)
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

from ._common import (content_text, decompress_lines, desensitize, iter_sessions,
                      result_text, ts_le)

FILE_WRITE_TOOLS = {"write", "write_file"}
FILE_EDIT_TOOLS = {"edit", "multiedit", "str_replace_editor"}
TEST_CMD_RE = re.compile(r"(?i)\b(pytest|vitest|jest|npm\s+test|pnpm\s+test|yarn\s+test|tsc\b|go\s+test|cargo\s+test|ruff|mypy|eslint)\b")
REVERT_RE = re.compile(r"(?i)\b(revert|rollback|撤销|回滚|undo)\b")
TEST_PATH_RE = re.compile(r"(?i)(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]*$|\.(test|spec)\.[jt]sx?$")

TASK_TEXT_LIMIT = 2000     # 方案原为 500；实测 user content p50=740 -> 放宽
_MIN_TASK_CHARS = 12       # 短于此的真实用户消息视为「续接指令」，不覆盖上一个任务
WRITE_KEEP_FULL = 20000
WRITE_HEAD, WRITE_TAIL = 8000, 2000

LANG_BY_EXT = {".py": "python", ".ts": "typescript", ".tsx": "typescript", ".js": "javascript",
               ".jsx": "javascript", ".go": "go", ".rs": "rust", ".java": "java", ".cs": "csharp",
               ".rb": "ruby", ".php": "php", ".md": "markdown", ".yml": "yaml", ".yaml": "yaml",
               ".json": "json", ".toml": "toml", ".sql": "sql"}
FRAMEWORK_HINTS = [("react", ("react", ".tsx", ".jsx")), ("vue", ("vue",)), ("flask", ("flask",)),
                   ("fastapi", ("fastapi",)), ("django", ("django",)), ("electron", ("electron",)),
                   ("vite", ("vite",)), ("pytest", ("pytest",)), ("chromadb", ("chroma",)),
                   ("sentence-transformers", ("sentence_transformers", "sentence-transformers"))]

#: 【必需】只有 source.kind == "user" 才是真实用户消息。实测 2,661 条 user/message 分布：
#: plugin 38.3%（background job 通知）/ user 28.1%（真实）/ subagent-report 10.2% /
#: subagent-settled 10.1% / skill-catalog 9.1%（system-reminder）/ goal 1.6% /
#: coordinator 2.5% / agent-instructions 0.1%。
#: 不做此过滤时 69.6% 的样本 task 会变成系统样板（实测 <system-reminder> 一种占 183 条）。
_REAL_USER_KIND = "user"


def clean_path(path: str, cwd: Optional[str]) -> str:
    """绝对路径 -> 项目相对路径（去 PII 用户名，保留「改了哪个文件」的信息）。"""
    if not path:
        return path
    p = path.replace("/", "\\")
    if cwd:
        c = cwd.replace("/", "\\").rstrip("\\")
        if p.lower() == c.lower():
            return "."
        if p.lower().startswith(c.lower() + "\\"):
            return p[len(c) + 1:]
    return desensitize(p)[0]


def norm_diff(s: str) -> str:
    return "\n".join(ln.rstrip() for ln in (s or "").replace("\r\n", "\n").split("\n")).strip()


def truncate(text: str, limit: int) -> Tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:WRITE_HEAD] + "\n...[中略]...\n" + text[-WRITE_TAIL:], True


def infer_stack(paths: List[str], blobs: List[str]) -> Dict[str, Any]:
    lang: collections.Counter = collections.Counter()
    for p in paths:
        ext = os.path.splitext(p)[1].lower()
        if ext in LANG_BY_EXT:
            lang[LANG_BY_EXT[ext]] += 1
    joined = "\n".join(blobs).lower()
    fw = [name for name, keys in FRAMEWORK_HINTS if any(k in joined for k in keys)]
    return {"lang": (lang.most_common(1)[0][0] if lang else "unknown"),
            "frameworks": sorted(set(fw))[:6], "files_changed": len(set(paths))}


def infer_task_type(text: str, paths: List[str]) -> str:
    """任务分类。注意：不把改动路径整体拼进待判文本 —— 那会把约 68% 的样本误判为 test。"""
    t = (text or "").lower()
    if re.search(r"(?i)(fix|bug|报错|error|traceback|失败|修复|不工作|异常)", t):
        return "bugfix"
    test_paths = sum(1 for p in paths if TEST_PATH_RE.search(p))
    if re.search(r"(?i)(pytest|vitest|jest|单元测试|测试用例|跑测试|回归测试)", t) or \
       (paths and test_paths * 2 > len(paths)):
        return "test"
    if re.search(r"(?i)(refactor|重构|cleanup|清理|整理|拆分|重命名)", t):
        return "refactor"
    if re.search(r"(?i)(config|配置|\.env|yaml|部署脚本|ci\b|workflow)", t):
        return "config"
    if re.search(r"(?i)(infra|k8s|kubernetes|nginx|监控|运维|docker\b|容器)", t):
        return "infra"
    return "feature"


def extract(root: str, snapshot: Optional[str] = None, verbose: bool = False):
    files = iter_sessions(root) if os.path.isdir(root) else [root]
    samples: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    diff_registry: Dict[str, Dict[str, Any]] = {}
    stat: collections.Counter = collections.Counter()

    for fi, path in enumerate(files, 1):
        records: List[Dict[str, Any]] = []
        for ln in decompress_lines(path):
            try:
                o = json.loads(ln)
            except Exception:
                stat["parse_fail"] += 1
                continue
            if ts_le(o.get("time"), snapshot):
                records.append(o)
        if not records:
            continue
        records.sort(key=lambda o: o.get("seq", 0))

        cwd = None
        for o in records:
            if o.get("type") == "session" and o.get("cwd"):
                cwd = str(o["cwd"])
                break

        call_ids, res_ids = set(), set()
        for o in records:
            d = o.get("data") or {}
            if not isinstance(d, dict):
                continue
            if o.get("type") == "tool/call" and d.get("callId"):
                call_ids.add(d["callId"])
            elif o.get("type") == "tool/result":
                s = (d.get("message") or {}).get("source") or {}
                if isinstance(s, dict) and s.get("callId"):
                    res_ids.add(s["callId"])

        turns: Dict[int, Dict[str, Any]] = {}
        cur_task, cur_task_seq = "", 0
        for o in records:
            t, d = o.get("type"), o.get("data") or {}
            if not isinstance(d, dict):
                continue
            if t == "user/message":
                src = d.get("source")
                kind = str((src or {}).get("kind") or "") if isinstance(src, dict) else ""
                if kind != _REAL_USER_KIND:
                    continue
                txt = content_text(d.get("content")).strip()
                if not txt:
                    continue
                if len(txt) >= _MIN_TASK_CHARS or not cur_task:
                    cur_task, cur_task_seq = txt, o.get("seq", 0)
                continue
            tn = d.get("turn")
            if not isinstance(tn, int):
                continue
            slot = turns.setdefault(tn, {"recs": [], "task": cur_task, "task_seq": cur_task_seq})
            slot["recs"].append(o)
            if not slot["task"] and cur_task:
                slot["task"], slot["task_seq"] = cur_task, cur_task_seq

        for tn, slot in sorted(turns.items()):
            recs = slot["recs"]
            seq_from, seq_to = recs[0].get("seq", 0), recs[-1].get("seq", 0)
            changes: List[Dict[str, Any]] = []
            errors: List[Dict[str, Any]] = []
            test_calls: List[Tuple[int, str]] = []
            res_err: Dict[str, bool] = {}
            revert_hits = 0

            for o in recs:
                t, d = o.get("type"), o.get("data") or {}
                if not isinstance(d, dict):
                    continue
                if t in ("tool/call", "tool/code-dispatch"):
                    nm = str(d.get("name") or "").lower()
                    if nm in FILE_WRITE_TOOLS or nm in FILE_EDIT_TOOLS:
                        try:
                            changes.append({"tool": nm, "seq": o.get("seq"),
                                            "args": json.loads(d.get("arguments") or "{}")})
                        except Exception:
                            pass
                    elif nm == "pwsh" and t == "tool/call":
                        try:
                            cmd = json.loads(d.get("arguments") or "{}").get("command", "")
                        except Exception:
                            cmd = ""
                        if TEST_CMD_RE.search(cmd or "") and d.get("callId"):
                            test_calls.append((o.get("seq", 0), d["callId"]))
                        if REVERT_RE.search(cmd or ""):
                            revert_hits += 1
                    if t == "tool/code-dispatch" and d.get("isError"):
                        errors.append({"src": "tool/code-dispatch", "seq": o.get("seq")})
                elif t == "tool/result":
                    m = d.get("message") or {}
                    s = m.get("source") or {}
                    cid = s.get("callId") if isinstance(s, dict) else None
                    if isinstance(m.get("content"), list):
                        for b in m["content"]:
                            if isinstance(b, dict):
                                if cid:
                                    res_err[cid] = bool(b.get("isError"))
                                if b.get("isError"):
                                    errors.append({"src": "tool/result", "seq": o.get("seq")})

            if not changes:
                stat["skip_no_change"] += 1
                continue
            if not (slot["task"] or "").strip():
                stat["skip_no_task"] += 1
                rejected.append({"reason": "no_real_user_task",
                                 "source": {"seq_from": seq_from, "seq_to": seq_to}})
                continue

            last_by_path: Dict[str, Dict[str, Any]] = {}
            superseded = 0
            for c in changes:
                p = c["args"].get("file_path") or c["args"].get("path") or ""
                if not p:
                    continue
                if p in last_by_path:
                    superseded += 1
                    errors.append({"src": "overwritten_edit", "seq": last_by_path[p]["seq"]})
                last_by_path[p] = c

            blocked_by = None
            diffs_out: List[Dict[str, Any]] = []
            repl_total = 0
            for p, c in last_by_path.items():
                a = c["args"]
                sp = clean_path(p, cwd)
                if c["tool"] in FILE_EDIT_TOOLS:
                    old, new = a.get("old_string", ""), a.get("new_string", "")
                    body = "--- a/%s\n+++ b/%s\n-%s\n+%s" % (
                        sp, sp, "\n-".join((old or "").split("\n")), "\n+".join((new or "").split("\n")))
                else:
                    body = a.get("content", "") or ""
                body, blk, nr = desensitize(body)
                repl_total += nr
                if blk:
                    blocked_by = blk
                    break
                body, trunc = truncate(body, WRITE_KEEP_FULL)
                if trunc:
                    stat["truncated_diffs"] += 1
                diffs_out.append({"path": sp, "op": c["tool"], "diff": body,
                                  "bytes": len(body), "truncated": trunc})

            if blocked_by:
                stat["reject_block"] += 1
                rejected.append({"reason": "desensitize_hard_block", "pattern": blocked_by,
                                 "source": {"seq_from": seq_from, "seq_to": seq_to}})
                continue

            uniq: List[Dict[str, Any]] = []
            dup_n = 0
            for d in diffs_out:
                key = hashlib.sha256((d["path"] + "\x00" + norm_diff(d["diff"])).encode("utf-8")).hexdigest()
                rec = diff_registry.get(key)
                if rec is None:
                    diff_registry[key] = {"first_seq": seq_from, "refs": 1}
                    d["dedup_key"] = key[:16]
                    uniq.append(d)
                else:
                    rec["refs"] += 1
                    dup_n += 1
            if not uniq:
                stat["reject_dup"] += 1
                rejected.append({"reason": "duplicate_diffs", "dup_diffs": dup_n,
                                 "source": {"seq_from": seq_from, "seq_to": seq_to}})
                continue

            task_ds, blk2, nr2 = desensitize((slot["task"] or "")[: TASK_TEXT_LIMIT * 3])
            repl_total += nr2
            if blk2:
                stat["reject_block_task"] += 1
                rejected.append({"reason": "desensitize_hard_block_in_task", "pattern": blk2,
                                 "source": {"seq_from": seq_from, "seq_to": seq_to}})
                continue
            task_final = task_ds[:TASK_TEXT_LIMIT]
            paths = [d["path"] for d in uniq]
            verified = "unverified"
            if test_calls:
                last_seq, last_cid = max(test_calls)
                verified = ("unverified" if last_cid not in res_err
                            else ("fail" if res_err[last_cid] else "pass"))

            sid = hashlib.sha256(("%s|%d|%d" % (os.path.basename(os.path.dirname(path)),
                                                seq_from, seq_to)).encode()).hexdigest()[:32]
            samples.append({
                "id": sid,
                "source": {"type": "dsh", "session": os.path.basename(os.path.dirname(path)),
                           "turn": tn, "seq_from": seq_from, "seq_to": seq_to},
                "task": task_final,
                "task_truncated": len(task_ds) > TASK_TEXT_LIMIT,
                "task_type": infer_task_type(task_final, paths),
                "stack": infer_stack(paths, [d["diff"][:4000] for d in uniq]),
                "diffs": uniq, "decision": "",
                "pitfalls": [{"symptom": ("tool error" if e["src"] != "overwritten_edit"
                                          else "edit superseded by later edit"),
                              "cause": "", "fix": "", "verified_by": e["src"], "seq": e.get("seq")}
                             for e in errors[:20]],
                "verified": verified,
                "signal": {"isError": len(errors), "superseded": superseded,
                           "test_cmds": len(test_calls), "revert_hits": revert_hits,
                           "dup_diffs": dup_n, "redactions": repl_total},
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "deprecated_after": None,
            })
            stat["samples"] += 1
        if verbose and fi % 100 == 0:
            print("  ...%d/%d sessions, %d samples" % (fi, len(files), stat["samples"]), file=sys.stderr)

    return samples, rejected, stat, diff_registry


def cmd_extract(args: argparse.Namespace) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    t0 = time.time()
    samples, rejected, stat, reg = extract(args.sessions, args.snapshot_until, args.verbose)
    d = os.path.dirname(os.path.abspath(args.out))
    if d:
        os.makedirs(d, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        for s in samples:
            fh.write(json.dumps(s, ensure_ascii=False) + "\n")
    if args.rejected:
        with open(args.rejected, "w", encoding="utf-8") as fh:
            for r in rejected:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    vd = collections.Counter(s["verified"] for s in samples)
    tt = collections.Counter(s["task_type"] for s in samples)
    print("=" * 60)
    print("extract 完成：%.1fs" % (time.time() - t0))
    print("  样本          : %d" % len(samples))
    print("  唯一 diff     : %d" % len(reg))
    print("  verified 分布 : %s" % dict(vd))
    print("  task_type     : %s" % dict(tt))
    print("  拒绝          : %d -> %s" % (len(rejected), args.rejected or "(未落盘)"))
    print("  统计          : %s" % dict(stat))
    return 0
