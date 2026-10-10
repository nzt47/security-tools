#!/usr/bin/env python3
"""分身记忆主权操作入口 —— 导出 / 擦除 / 迁移（P5 memory_ops）

【它解决什么（不这样会怎样）】
    scoped 档分身的私人记忆域此前**没有任何入口**：既不能"搬走"（导出/迁移），
    也不能"删干净"（擦除）。本脚本把 `ScopedMemoryDomain` 的三种操作接到命令行，
    并强制其守卫：
      · 导出：只读；空域（tenant/workspace/subject 全空）直接拒绝；
      · 擦除：**必须 --confirm**，且**先写快照再删**（快照写失败即中止删除）；
      · 迁移：默认由**目标域**决定域（源 scope 不带），伪造的域标识不生效；
      三者都走同一条审计链（subagent.memory.export / erase / import，只记条数）。

【"按分身"怎么定位】分身的记忆域由 `SubagentConfig.memory_scope`（tenant/workspace/subject）
    标识；本脚本要求显式传入这三要素（与配置里的同一组值），并据此建域。

用法：
    python scripts/subagent_memory_ops.py export --tenant T --workspace W --subject S \
        --root <分片根> --out facts.json
    python scripts/subagent_memory_ops.py import --tenant T --workspace W --subject S \
        --root <分片根> --in facts.json
    python scripts/subagent_memory_ops.py erase  --tenant T --workspace W --subject S \
        --root <分片根> --snapshot erase.json --confirm
    python scripts/subagent_memory_ops.py migrate --tenant T --workspace W --subject S --root R \
        --to-tenant T2 --to-workspace W2 --to-subject S2 --to-root R2

退出码：0 成功；2 参数/读入错误；3 操作被守卫拒绝（含未确认擦除）；4 部分失败。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:  # 直接 `python scripts/...` 跑时也能 import agent
    sys.path.insert(0, _REPO_ROOT)


def _add_scope(parser, prefix="", required=True):
    tag = "to-" if prefix else ""
    parser.add_argument("--%stenant" % tag, dest=prefix + "tenant", default="",
                        required=required, help="租户 id（分身 memory_scope.tenant_id）")
    parser.add_argument("--%sworkspace" % tag, dest=prefix + "workspace", default="",
                        required=required, help="工作区 id（分身 memory_scope.workspace_id）")
    parser.add_argument("--%ssubject" % tag, dest=prefix + "subject", default="",
                        required=required, help="主体 id（分身 memory_scope.subject_id）")
    parser.add_argument("--%sroot" % tag, dest=prefix + "root", default="",
                        help="分片根目录（holographic 用）")
    parser.add_argument("--%sprovider" % tag, dest=prefix + "provider",
                        default="holographic", help="记忆后端（holographic / mem0）")


def build_parser():
    parser = argparse.ArgumentParser(description="分身记忆主权操作（导出/擦除/迁移）")
    subs = parser.add_subparsers(dest="cmd", required=True)

    p_exp = subs.add_parser("export", help="导出为 JSON（只读）")
    _add_scope(p_exp)
    p_exp.add_argument("--out", required=True)
    p_exp.add_argument("--limit", type=int, default=0)

    p_imp = subs.add_parser("import", help="从 JSON 写回（逐条 fail-soft）")
    _add_scope(p_imp)
    p_imp.add_argument("--in", dest="source", required=True)
    p_imp.add_argument("--keep-scope", action="store_true",
                       help="同域回灌时保留原 scope（默认由目标域决定）")

    p_er = subs.add_parser("erase", help="擦除（必须先快照；必须 --confirm）")
    _add_scope(p_er)
    p_er.add_argument("--snapshot", default="")
    p_er.add_argument("--limit", type=int, default=0)
    p_er.add_argument("--confirm", action="store_true",
                      help="确认执行不可逆删除（缺省则拒绝）")

    p_mig = subs.add_parser("migrate", help="导出源域并写回目标域")
    _add_scope(p_mig)
    _add_scope(p_mig, prefix="to_", required=True)
    p_mig.add_argument("--limit", type=int, default=0)
    return parser


def _domain(args, prefix="") -> "object":
    from agent.memory.scoped_store import scoped_domain_from_scope

    scope = {
        "tenant_id": getattr(args, prefix + "tenant"),
        "workspace_id": getattr(args, prefix + "workspace"),
        "subject_id": getattr(args, prefix + "subject"),
    }
    return scoped_domain_from_scope(getattr(args, prefix + "provider"), scope,
                                    root=getattr(args, prefix + "root") or "")


def _limit(args) -> "object":
    value = int(getattr(args, "limit", 0) or 0)
    return value if value > 0 else None


def _write_json(path, payload):
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if args.cmd == "export":
        out = _domain(args).export_entries(limit=_limit(args))
        if not out.ok:
            print("导出失败: %s (%s)" % (out.error_code, out.degraded), file=sys.stderr)
            return 3
        payload = {"schema": "yunshu.memory.export.v1", "provider": args.provider,
                   "count": len(out.entries), "entries": list(out.entries)}
        _write_json(args.out, payload)
        print("[memory-ops] 已导出 %d 条 -> %s" % (len(out.entries), args.out))
        return 0

    if args.cmd == "import":
        try:
            with open(args.source, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError) as exc:
            print("导出文件不可读: %s" % exc, file=sys.stderr)
            return 2
        rows = doc.get("entries") if isinstance(doc, dict) else doc
        if not isinstance(rows, list):
            print("导出文件缺少 entries 列表", file=sys.stderr)
            return 2
        out = _domain(args).import_entries(rows, keep_scope=bool(args.keep_scope))
        print("[memory-ops] imported=%d failed=%d ok=%s"
              % (out.imported, out.failed, out.ok))
        return 0 if out.ok else 4

    if args.cmd == "erase":
        if not args.confirm:
            print("拒绝擦除：未传 --confirm（不可逆操作必须显式确认）", file=sys.stderr)
            return 3
        out = _domain(args).erase_entries(confirm=True, limit=_limit(args),
                                          snapshot_path=args.snapshot)
        print("[memory-ops] scanned=%d deleted=%d snapshot=%s ok=%s"
              % (out.scanned, out.deleted, out.snapshot_path, out.ok))
        if not out.ok:
            print("擦除未完成: %s (%s)" % (out.error_code, out.degraded),
                  file=sys.stderr)
            return 3
        return 0

    if args.cmd == "migrate":
        src = _domain(args)
        exported = src.export_entries(limit=_limit(args))
        if not exported.ok:
            print("源域导出失败: %s (%s)" % (exported.error_code, exported.degraded),
                  file=sys.stderr)
            return 3
        dst = _domain(args, prefix="to_")
        out = dst.import_entries(list(exported.entries))
        print("[memory-ops] migrate imported=%d failed=%d ok=%s"
              % (out.imported, out.failed, out.ok))
        return 0 if out.ok else 4

    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
