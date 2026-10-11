#!/usr/bin/env python3
"""cognitive_loop 共享教训池操作入口 —— 列表 / 人工批准提升 / 擦除（二期）

【它解决什么（不这样会怎样）】
    提升闸门二期把 candidate→shared 设计为「N 个不同 instance_id 的独立确认，或人工批准」。
    自动路径在 `record_lesson` 内完成；**人工批准与擦除此前没有入口** —— 本脚本把
    `cognitive_lessons.promote` / `.erase` / `.recall_shared` 接到命令行，并强制其守卫：
      · 三个动作都**必须显式传 --tenant / --subject**（作用域只能来自这里，防跨主体越权）；
      · promote / erase 只影响当前 (tenant, subject) 内的记录；
      · 目录未配置 ⇒ 拒绝（fail-closed，不假装成功）。

用法：
    python scripts/subagent_cognitive_lessons.py --tenant T --subject S --dir <池目录> list
    python scripts/subagent_cognitive_lessons.py --tenant T --subject S --dir <池目录> \
        promote <lesson_id> --approver ops
    python scripts/subagent_cognitive_lessons.py --tenant T --subject S --dir <池目录> erase <lesson_id>

退出码：0 成功；2 参数/配置错误；3 目标不存在或被守卫拒绝。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:  # 直接 python scripts/... 跑时也能 import agent
    sys.path.insert(0, _REPO_ROOT)


def build_parser():
    parser = argparse.ArgumentParser(description="cognitive_loop 共享教训池操作（二期）")
    parser.add_argument("--tenant", required=True, help="租户 id（作用域，必填）")
    parser.add_argument("--subject", required=True, help="主体 id（作用域，必填）")
    parser.add_argument("--dir", default="",
                        help="共享教训池目录（含 lessons.jsonl）；缺省读 CP_SUBAGENT_COGNITIVE_LESSONS_DIR")
    parser.add_argument("--limit", type=int, default=200, help="list 最多返回条数（默认 200）")
    subs = parser.add_subparsers(dest="cmd", required=True)
    subs.add_parser("list", help="列出当前作用域内可见的教训（只读）")
    p_prom = subs.add_parser("promote", help="人工批准：把一条教训提升为 shared")
    p_prom.add_argument("lesson_id")
    p_prom.add_argument("--approver", default="", help="审批人标识（审计用）")
    p_er = subs.add_parser("erase", help="擦除当前作用域内的一条教训")
    p_er.add_argument("lesson_id")
    p_ex = subs.add_parser("export", help="导出当前作用域的全部教训（JSON；与 memory_ops 同口径）")
    p_ex.add_argument("--out", default="", help="写文件路径（缺省打印到 stdout）")
    p_im = subs.add_parser("import", help="从 JSON 导入教训（域强制为当前作用域）")
    p_im.add_argument("path", help="JSON 文件路径：数组或 {lessons: [...]}")
    return parser


def _environ(args) -> dict:
    d = args.dir or os.environ.get("CP_SUBAGENT_COGNITIVE_LESSONS_DIR", "")
    return {
        "CP_SUBAGENT_COGNITIVE_SHARED_LESSONS": "1",
        "CP_SUBAGENT_COGNITIVE_LESSONS_DIR": d,
    }


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    from agent.subagent import cognitive_lessons as cl

    env = _environ(args)
    if not cl.lessons_dir(env):
        print("共享教训池目录未配置（--dir 或 CP_SUBAGENT_COGNITIVE_LESSONS_DIR）", file=sys.stderr)
        return 2
    with cl.scope_context(args.tenant, args.subject):
        if args.cmd == "list":
            hits = cl.recall_shared("", count=max(1, int(args.limit or 200)),
                                    char_budget=10 ** 9, environ=env)
            print(json.dumps({"count": len(hits), "lessons": hits}, ensure_ascii=False, indent=2))
            return 0
        if args.cmd == "promote":
            ok = cl.promote(args.lesson_id, approver=args.approver, environ=env)
            if ok:
                print("[cognitive-lessons] 已提升 %s" % args.lesson_id)
                return 0
            print("[cognitive-lessons] 未找到可提升的记录: %s" % args.lesson_id, file=sys.stderr)
            return 3
        if args.cmd == "erase":
            n = cl.erase(args.lesson_id, environ=env)
            print("[cognitive-lessons] 已擦除 %d 条" % n)
            return 0 if n else 3
        if args.cmd == "export":
            rows = cl.list_lessons(environ=env)
            text = json.dumps({"count": len(rows), "lessons": rows},
                              ensure_ascii=False, indent=2)
            if args.out:
                with open(args.out, "w", encoding="utf-8") as fh:
                    fh.write(text)
                print("[cognitive-lessons] 已导出 %d 条 → %s" % (len(rows), args.out))
            else:
                print(text)
            return 0
        if args.cmd == "import":
            with open(args.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            rows = data.get("lessons") if isinstance(data, dict) else data
            n = cl.import_lessons(rows, environ=env)
            print("[cognitive-lessons] 已导入 %d 条" % n)
            return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
