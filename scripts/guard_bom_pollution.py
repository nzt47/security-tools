#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""guard_bom_pollution.py — 受保护 PS 文件 BOM 污染监控(防并行会话叠加)

背景(2026-08-05): scripts/run_l3_regression_tests.ps1 曾被并行会话/自动提交
脚本写入叠加 BOM(EF BB BF x3), 工作区与 HEAD 内容一致但编码被破坏, 触发
check_ps1_encoding BLOCK。本脚本聚焦「受保护文件清单」: 清单内任一文件出现
叠加 BOM / 关键文件缺 BOM / 非法 UTF-8 → BLOCK 并预警, 供 CI、维护巡检
(maintenance_check M8)与并行会话提交前自检使用。

三义:
- 不易: 判定全部复用 ps_bom_contract(BOM/count_leading_bom/is_utf8/hex_head/
        REQUIRE_BOM_DEFAULT), 不复制实现; 编码契约不变量不因监控脚本而变
- 变易: --watch 可追加受保护文件; --repo-root 支持任意仓库; --json 供 CI 消费
- 简易: 单文件, 输出行与 check_ps1_encoding 一致([BLOCK]), 退出码 0/1

用法:
  python scripts/guard_bom_pollution.py                          # 默认清单
  python scripts/guard_bom_pollution.py --watch scripts/x.ps1    # 追加保护
  python scripts/guard_bom_pollution.py --json                   # stdout 仅 JSON
  python scripts/guard_bom_pollution.py --repo-root <repo>       # 指定仓库
退出码: 0 = 无污染; 1 = 存在叠加 BOM / 关键文件缺 BOM / 非法 UTF-8
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import ps_bom_contract as _contract

# 受保护文件清单(相对仓库根, 单一事实源在 ps_bom_contract):
# 1) 关键契约文件(REQUIRE_BOM_DEFAULT): 缺 BOM 即 BLOCK
# 2) 历史污染点: 曾被并行会话叠加 BOM 的文件, 加入后任何叠加立即预警
WATCH_DEFAULT = list(_contract.REQUIRE_BOM_DEFAULT) + [
    "scripts/run_l3_regression_tests.ps1",
]


def main() -> int:
    ap = argparse.ArgumentParser(description="受保护 PS 文件 BOM 污染监控")
    ap.add_argument("--repo-root", default=".", help="仓库根目录(默认当前目录)")
    ap.add_argument("--watch", action="append", default=[],
                    help="追加受保护文件(相对 --repo-root, 可多次)")
    ap.add_argument("--json", action="store_true", help="stdout 仅输出 JSON")
    ap.add_argument("--quiet", action="store_true",
                    help="仅输出 BLOCK 明细(汇总隐藏, 供巡检/CI 失败诊断)")
    args = ap.parse_args()

    root = Path(args.repo_root).resolve()
    if not root.is_dir():
        print(f"[ERROR] 仓库根目录不存在: {root}", file=sys.stderr)
        return 1

    require_bom = [Path(p) for p in _contract.REQUIRE_BOM_DEFAULT]
    watch = [Path(p) for p in dict.fromkeys([*WATCH_DEFAULT, *args.watch])]
    blocked = []  # (path, reason)

    for rel in watch:
        p = root / rel
        if not p.is_file():
            continue  # 文件暂不存在, 不误报(清单是防御性保护)
        data = p.read_bytes()
        n_bom = _contract.count_leading_bom(data)
        if not _contract.is_utf8(data):
            blocked.append((rel, f"非法 UTF-8 (head: {_contract.hex_head(data)})"))
        elif n_bom > 1:
            blocked.append((rel, f"叠加 BOM x{n_bom} → x1 (head: {_contract.hex_head(data)})"))
        elif n_bom == 0 and rel in require_bom:
            blocked.append((rel, "缺 BOM(关键契约文件)"))

    # BLOCK 明细始终输出(与 check_ps1_encoding 同契约, 失败时可直接定位文件)
    for rel, reason in blocked:
        print(f"[BLOCK] {rel}: {reason}")
    if not args.quiet:
        print("---")
        print(f"受保护文件 {len(watch)} 个 → 污染 {len(blocked)}")

    if args.json:
        print(json.dumps({
            "tool": "guard_bom_pollution",
            "status": "fail" if blocked else "pass",
            "blocked": [{"path": str(p), "reason": r} for p, r in blocked],
            "watch_count": len(watch),
        }, ensure_ascii=False, indent=2))

    return 1 if blocked else 0


if __name__ == "__main__":
    sys.exit(main())
