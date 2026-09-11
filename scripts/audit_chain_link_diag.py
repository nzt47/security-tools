#!/usr/bin/env python
"""审计链断链形态诊断（**只读**；不写入、不修复、不追溯）

为什么需要它
------------
`scripts/verify_audit_chain.py --stats` 只给出**汇总**结论（ok / TAMPERED + 首个异常位置），
不足以判定"链到底坏在哪、坏成什么样"。本脚本补齐形态学证据，用于：

1. §11.10 篡改演练与月度验签的**定位**环节；
2. 区分三类性质完全不同的成因（对应三种不同处置）：
   - `prev 指向库中不存在的 self_hash`（悬空前驱）⇒ **记录缺失或写者并发交错**，不是改写；
   - `prev 指向更晚的 seq`（回跳）⇒ 写者串扰/乱序落盘；
   - `seq 不连续 / 重复 / self_hash 重复` ⇒ 结构性损坏（插删改）。
3. 为「审计台账保留与冷存」边界草案（`RFC-审计台账保留与冷存_边界草案.md`）提供**可复现**的现状基线。

只读保证
--------
以 `file:<db>?mode=ro` URI 打开 SQLite，且全程只执行 `SELECT` / `PRAGMA`。
不 import `agent.audit`（避免任何副作用：不建库、不建链、不启写者线程、不写链）。

用法
----
    python scripts/audit_chain_link_diag.py                       # 默认库
    python scripts/audit_chain_link_diag.py <db_path>             # 指定库
    python scripts/audit_chain_link_diag.py <db_path> --json      # 机器可读

退出码：0 = 链完整；1 = 检出断链（诊断成功）；2 = 用法/IO 错误。

输出只用 ASCII 标记与 GBK 可编码字符（Windows 默认 GBK 控制台不会因 print 崩）
"""

import json
import os
import sqlite3
import sys
from collections import Counter

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(_PROJECT_ROOT, "data", "audit", "audit_chain.db")


def diagnose(db_path: str) -> dict:
    """只读诊断：返回形态学结论（不抛异常以外的副作用）"""
    report: dict = {"db": db_path, "exists": os.path.exists(db_path)}
    if not report["exists"]:
        report["error"] = "库文件不存在"
        return report
    report["size_bytes"] = os.path.getsize(db_path)

    con = sqlite3.connect("file:%s?mode=ro" % db_path.replace("?", "%3f"), uri=True)
    try:
        con.row_factory = sqlite3.Row
        total, seq_min, seq_max = con.execute(
            "SELECT COUNT(*), MIN(seq), MAX(seq) FROM audit_chain").fetchone()
        report["count"] = total
        report["seq_min"] = seq_min
        report["seq_max"] = seq_max
        report["seq_contiguous"] = (total == (seq_max or 0) - (seq_min or 0) + 1)
        report["seq_duplicates"] = con.execute(
            "SELECT COUNT(*) FROM (SELECT seq FROM audit_chain GROUP BY seq"
            " HAVING COUNT(*) > 1)").fetchone()[0]
        report["distinct_self_hash"] = con.execute(
            "SELECT COUNT(DISTINCT self_hash) FROM audit_chain").fetchone()[0]

        rows = con.execute(
            "SELECT seq, ts, actor, action, source, self_hash, prev_hash"
            " FROM audit_chain ORDER BY seq").fetchall()
        self_seqs = {r["self_hash"]: r["seq"] for r in rows}

        dangling, backjump, others, broken_seqs = [], [], [], []
        for index, row in enumerate(rows):
            if index == 0:
                continue
            if row["prev_hash"] == rows[index - 1]["self_hash"]:
                continue
            broken_seqs.append(row["seq"])
            source_seq = self_seqs.get(row["prev_hash"])
            if source_seq is None:
                dangling.append({"seq": row["seq"], "ts": row["ts"],
                                 "action": row["action"],
                                 "prev_hash": (row["prev_hash"] or "")[:16]})
            elif source_seq > row["seq"]:
                backjump.append({"seq": row["seq"], "prev_points_to_seq": source_seq})
            else:
                others.append({"seq": row["seq"], "prev_points_to_seq": source_seq})

        ranges = []
        if broken_seqs:
            start = last = broken_seqs[0]
            for seq in broken_seqs[1:]:
                if seq - last > 1:
                    ranges.append([start, last])
                    start = seq
                last = seq
            ranges.append([start, last])

        report["broken_links"] = len(broken_seqs)
        report["dangling_prev"] = dangling
        report["backjump_prev"] = backjump
        report["other_mismatch"] = others
        report["broken_seq_ranges"] = ranges
        report["by_source"] = dict(Counter(
            r["source"] for r in con.execute("SELECT source FROM audit_chain")))
        report["by_day"] = dict(sorted(Counter(
            (r["ts"] or "")[:10] for r in con.execute("SELECT ts FROM audit_chain")
        ).items()))
        report["by_action_top"] = dict(Counter(
            r["action"] for r in con.execute("SELECT action FROM audit_chain")
        ).most_common(8))
        report["verdict"] = "intact" if not broken_seqs else "broken"
    finally:
        con.close()
    return report


def main(argv) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    as_json = "--json" in argv[1:]
    db_path = os.path.abspath(args[0]) if args else DEFAULT_DB

    try:
        report = diagnose(db_path)
    except Exception as exc:  # noqa: BLE001 用法/IO 错误
        print("诊断失败: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 2

    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return 0 if report.get("verdict") == "intact" else 1

    if not report.get("exists"):
        print("库文件不存在: %s" % db_path, file=sys.stderr)
        return 2

    print("库: %s (%s bytes)" % (db_path, report["size_bytes"]))
    print("条数=%s seq=%s..%s 连续=%s 重复 seq=%s 不同 self_hash=%s"
          % (report["count"], report["seq_min"], report["seq_max"],
             report["seq_contiguous"], report["seq_duplicates"],
             report["distinct_self_hash"]))
    print("断链 %s 处（悬空前驱 %s / 回跳 %s / 其他 %s）"
          % (report["broken_links"], len(report["dangling_prev"]),
             len(report["backjump_prev"]), len(report["other_mismatch"])))
    if report["dangling_prev"]:
        print("  [!] 悬空前驱 = 前驱记录在**任何**库中都不存在"
              " => 记录缺失或写者并发交错（非改写）")
        for item in report["dangling_prev"][:5]:
            print("    seq=%s ts=%s action=%s prev->%s"
                  % (item["seq"], item["ts"], item["action"], item["prev_hash"]))
    if report["backjump_prev"]:
        print("  [!] 回跳 = 前驱指向更晚的 seq => 写者串扰/乱序落盘")
    if report["broken_seq_ranges"]:
        print("  断链 seq 区间（前 8）: %s" % report["broken_seq_ranges"][:8])
    print("按来源: %s" % report["by_source"])
    print("按日: %s" % report["by_day"])
    print("按动作 top: %s" % report["by_action_top"])
    print("结论: %s" % ("链完整 OK" if report["verdict"] == "intact"
                       else "检出断链 BROKEN（**不要就地改写修复**；见 RFC 边界草案 §三.2）"))
    return 0 if report["verdict"] == "intact" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
