#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""审计链保留/轮转策略（D1）——**默认只读、默认不删任何东西**

═══ 为什么不能"到期就 DELETE 旧行" ═══
审计链是**合规证据**：每条记录的 `self_hash = H(seq|ts|actor|action|subject|payload_hash|prev_hash)`，
`prev_hash` 指向链上前一条。删掉链头的一段（seq 1..K）不会让剩下的行"自洽失败"，
但会让**整链校验的锚点失效**：`AuditChain.verify_chain()` 在 `start_seq=None` 时
一律用 `GENESIS_PREV_HASH`（64 个 0）当锚（`agent/audit/chain.py` 的 verify_chain），
而删头之后的第一条保留记录其 `prev_hash` 不等于创世哈希
⇒ `verify_chain()` 必然报"首个篡改位置"。
更糟的是**从中间按 ts 删一个日块**会在链里留下空洞（seq 不连续），
这是任何验链器都无法解释的状态。这两点是本脚本全部安全设计的来源。

═══ 本脚本的策略：归档优先 + 前缀截断留锚 ═══
1. **归档（archive，只读导出，永不破坏链）**
   把某个 UTC 日的全部记录原样导出成 `<archive-dir>/audit_<YYYYMMDD>.jsonl.gz`
   （逐行 JSON，字段与 DB 行一一对应），并写一条**锚点**到
   `<archive-dir>/anchors.jsonl`。锚点自带：该日的 `first_seq/last_seq`、
   `first_prev_hash`、`last_self_hash`、归档文件 sha256、行数，
   以及指向前一条锚点的 `prev_anchor_hash`（锚点文件自身也是哈希链，
   可检出"事后删掉/改写某天锚点"）。
   归档对**任何**一天都安全：它只读库、只写冷文件。**强烈建议只做这一步**
   （冷数据搬走 + 证据锚点留下），热库大小问题交给"下一条"。
2. **删除（prune，可选，默认关闭）**
   只有 `--apply --prune --confirm-chain-break delete-audit-rows` 三件套齐全才执行，
   且只允许删**链头前缀**（seq 从 1 开始连续的那一段）。任何"日块位于链中段"的情况
   一律拒绝（否则留空洞）。删除前先逐条校验归档完整性。删除后**必须重新锚定**校验：
   `chain.verify_chain(start_seq=<第一条保留记录>, anchor_prev_hash=<锚点里的 last_self_hash>)`
   ——脚本会把这条命令按实际参数打印出来。
   不删才是默认；"删"是为了回收磁盘，不是保留策略的必要条件。

═══ 用法 ═══
    python scripts/audit_retention.py                        # 干跑：只打印计划，一个字节都不写
    python scripts/audit_retention.py --json                 # 同上，机器可读
    python scripts/audit_retention.py --apply --archive      # 真正写冷归档 + 锚点（仍不删行）
    python scripts/audit_retention.py --verify               # 校验锚点链与归档/活库的边界连续性
    python scripts/audit_retention.py --apply --archive --prune \
        --confirm-chain-break delete-audit-rows              # 归档 + 删除前缀（危险，需逐项确认）

═══ 安全边界 ═══
- 干跑模式**只以 `file:...?mode=ro` 打开数据库**：任何写操作都会直接抛
  `attempt to write a readonly database`，不是"靠自觉不写"。
- 脚本**不新增/不修改链上的任何行**（不做"删除 + 补一条墓碑记录"这种操作：
  追加必须走 `AuditChain.append` 的单写者 + 预留日志路径，脚本直连 SQL 会破坏该纪律）。
- 删除动作本身的留痕由 `anchors.jsonl` 承担；建议同时在服务端用审计门面记一条
  `audit.record("retention.audit_chain.prune", ...)`（脚本会在计划里给出建议调用）。
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone

#: 锚点链的创世前驱（与 chain.GENESIS_PREV_HASH 同形，但此处独立常量，避免依赖 agent 包）
GENESIS_ANCHOR = "0" * 64
ARCHIVE_PREFIX = "audit_"
ANCHORS_NAME = "anchors.jsonl"
#: 删除确认口令（必须逐字给出，防止"手滑 --apply 就删库"）
PRUNE_CONFIRM = "delete-audit-rows"
DEFAULT_DB = os.path.join("data", "audit", "audit_chain.db")
DEFAULT_ARCHIVE_DIR = os.path.join("data", "audit", "archive")


# ════════════════════════════════════════════════════════════
#  基础工具
# ════════════════════════════════════════════════════════════

def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _canonical(obj) -> str:
    """锚点的规范化 JSON（排序键、紧凑分隔符；与链上 payload 规范同风格）"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _anchor_hash(rec: dict) -> str:
    """锚点哈希 = sha256(规范化 JSON，**不含 anchor_hash 字段**）"""
    body = {k: v for k, v in rec.items() if k != "anchor_hash"}
    return _sha256_bytes(_canonical(body).encode("utf-8"))


def _utc_today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _shift_day(day: str, delta_days: int) -> str:
    d = date.fromisoformat(day[:10])
    return (d + timedelta(days=delta_days)).isoformat()


def connect_ro(path: str) -> sqlite3.Connection:
    """**只读**连接（file:...?mode=ro）：写操作会被 SQLite 直接拒绝"""
    uri = "file:///" + os.path.abspath(path).replace("\\", "/") + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def connect_rw(path: str) -> sqlite3.Connection:
    """读写连接（**仅 --apply --prune 使用**）；调用方负责 BEGIN IMMEDIATE 抢写锁"""
    conn = sqlite3.connect(os.path.abspath(path), timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


# ════════════════════════════════════════════════════════════
#  清点：按 UTC 日的块状视图
# ════════════════════════════════════════════════════════════

def read_day_blocks(conn: sqlite3.Connection) -> list:
    """每个 UTC 日一块：行数 / seq 区间 / ts 区间 / payload 字节 / 首尾哈希

    `substr(ts,1,10)` 与 `chain.day_of_ts()`（取 ts 前 10 字符）口径一致。
    **按 first_seq 排序**而不是按日排序：链的物理顺序是 seq，而这批数据里
    ts 并不单调（含 2025/2027 的历史与未来时间戳），"链头前缀"只能按 seq 判定。
    """
    rows = conn.execute(
        "SELECT substr(ts,1,10) AS day, COUNT(*) AS rows, MIN(seq) AS first_seq, "
        "       MAX(seq) AS last_seq, MIN(ts) AS first_ts, MAX(ts) AS last_ts, "
        "       SUM(LENGTH(payload)) AS payload_bytes "
        "FROM audit_chain GROUP BY day ORDER BY first_seq").fetchall()
    blocks = []
    for r in rows:
        edges = conn.execute(
            "SELECT seq, self_hash, prev_hash FROM audit_chain WHERE seq IN (?, ?)",
            (int(r["first_seq"]), int(r["last_seq"]))).fetchall()
        by_seq = {int(e["seq"]): e for e in edges}
        first = by_seq.get(int(r["first_seq"]))
        last = by_seq.get(int(r["last_seq"]))
        blocks.append({
            "day": str(r["day"]), "rows": int(r["rows"]),
            "first_seq": int(r["first_seq"]), "last_seq": int(r["last_seq"]),
            "first_ts": str(r["first_ts"]), "last_ts": str(r["last_ts"]),
            "payload_bytes": int(r["payload_bytes"] or 0),
            "first_prev_hash": str(first["prev_hash"]) if first else "",
            "last_self_hash": str(last["self_hash"]) if last else "",
        })
    return blocks


def estimate_row_bytes(conn: sqlite3.Connection) -> float:
    """单行均摊磁盘占用（**估算**）：页面总数 × 页大小 ÷ 行数

    口径说明：这是"全库均摊"，含 5 个索引。删除行**不会**自动归还磁盘
    （需要 VACUUM，而 VACUUM 会重写整个库、代价与风险都很大），
    故这里的 reclaim 只是"逻辑可回收量"的估算，不是"删完立刻少这么多"。
    """
    page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
    page_count = int(conn.execute("PRAGMA page_count").fetchone()[0])
    total = int(conn.execute("SELECT COUNT(*) FROM audit_chain").fetchone()[0])
    if total <= 0:
        return 0.0
    return (page_size * page_count) / total


# ════════════════════════════════════════════════════════════
#  计划：哪些日可归档、哪些日可删（前缀规则）
# ════════════════════════════════════════════════════════════

def build_plan(conn: sqlite3.Connection, *, archive_dir: str, cutoff_day: str,
               ref_day: str, keep_days: int) -> dict:
    blocks = read_day_blocks(conn)
    row_bytes = estimate_row_bytes(conn)
    total_rows = int(conn.execute("SELECT COUNT(*) FROM audit_chain").fetchone()[0])
    head_day = blocks[-1]["day"] if blocks else ""
    archived = {a["day"]: a for a in load_anchors(archive_dir)}

    # 可删上界 K 取两个约束的更小者：① 窗口（该 seq 之下全是旧日）② 链头日保护
    k_window = _prefix_limit(conn, cutoff_day)
    k_head = _day_head_guard(conn, head_day) if head_day else 0
    k = max(min(k_window, k_head), 0)

    prefix_days = [d["day"] for d in blocks if int(d["first_seq"]) <= k]
    unarchived_in_prefix = [d for d in prefix_days if d not in archived]

    plan_days = []
    for blk in blocks:
        first, last = int(blk["first_seq"]), int(blk["last_seq"])
        if k > 0 and first <= k:
            prune = "yes(全部)" if last <= k else "yes(部分)"
            reason = ""
        else:
            prune = "no"
            if blk["day"] >= cutoff_day:
                reason = "该日仍在保留窗口内（{} ≥ 截止日 {}）".format(
                    blk["day"], cutoff_day)
            elif blk["day"] == head_day:
                reason = "链头日不可删（删掉热库就没有最新证据）"
            else:
                reason = ("位于链中段（seq {} > 可删上界 {}）：删除会在链中留空洞"
                          .format(first, k))
        rows_in_prefix = max(0, min(last, k) - first + 1) if k >= first else 0
        plan_days.append({
            **blk,
            "archive": "yes" if blk["day"] not in archived else "skip(已归档)",
            "prune": prune,
            "rows_in_prefix": rows_in_prefix,
            "prune_blocked_reason": reason,
            "est_reclaim_bytes": int(round(rows_in_prefix * row_bytes)),
        })

    return {
        "db": conn.execute("PRAGMA database_list").fetchone()[2],
        "ref_day": ref_day,
        "keep_days": keep_days,
        "cutoff_day": cutoff_day,
        "total_rows": total_rows,
        "row_bytes_estimate": round(row_bytes, 1),
        "head_day": head_day,
        "prune_upper_seq": k,
        "prune_upper_seq_by_window": k_window,
        "prune_upper_seq_by_head_guard": k_head,
        "prune_days": prefix_days,
        "prune_blocked_days": unarchived_in_prefix,
        "days": plan_days,
        "archive_candidates": [d["day"] for d in plan_days if d["archive"] == "yes"],
        "prune_candidates": [d["day"] for d in plan_days if d["prune"] != "no"],
        "est_reclaim_bytes": sum(d["est_reclaim_bytes"] for d in plan_days),
    }


def _prefix_limit(conn: sqlite3.Connection, cutoff_day: str) -> int:
    """可删 **seq 上界** K：``seq ≤ K`` 的每一行都属于"早于截止日"的日块

    【为什么必须按**行**判定，而不是按"日块"判定（本仓实测踩到的坑）】
    日块与 seq **不同序**：2027-10-19 的块是 seq 928..2719，与 2026-09-14 的块
    （915..2782）交错。若按"日块"删除（`DELETE WHERE seq BETWEEN first AND last`），
    删 2026-09-14 时会连带删掉落在该 seq 区间里的**别的日**的行——静默删错了证据。
    正确口径：可删集合只能是 **seq 从 1 开始的一段前缀**，
    且这段前缀里的每一行都必须来自"早于截止日"的日（否则那段前缀里混着
    保留窗口内的证据）。

    K = （第一个"属于保留窗口内的行"的 seq）- 1；全部都在窗口内时 K=0。
    """
    row = conn.execute(
        "SELECT MIN(seq) AS s FROM audit_chain WHERE substr(ts,1,10) >= ?",
        (cutoff_day,)).fetchone()
    first_in_window = row["s"] if row is not None else None
    if first_in_window is None:
        top = conn.execute("SELECT MAX(seq) AS s FROM audit_chain").fetchone()
        return int((top["s"] if top is not None else 0) or 0)
    return int(first_in_window) - 1


def _day_head_guard(conn: sqlite3.Connection, head_day: str) -> int:
    """链头日保护：可删上界还要再压到"链头日最早一行的 seq - 1"

    否则"所有记录都很旧"的场景下 K 会等于 MAX(seq)，一次删空整条链——
    那是把合规证据连根拔掉，不是保留策略。
    """
    row = conn.execute(
        "SELECT MIN(seq) AS s FROM audit_chain WHERE substr(ts,1,10) = ?",
        (head_day,)).fetchone()
    if row is None or row["s"] is None:
        return 0
    return int(row["s"]) - 1
# ════════════════════════════════════════════════════════════
#  归档 + 锚点
# ════════════════════════════════════════════════════════════

def load_anchors(archive_dir: str) -> list:
    path = os.path.join(archive_dir, ANCHORS_NAME)
    if not os.path.exists(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _archive_name(day: str) -> str:
    return "{}{}{}".format(ARCHIVE_PREFIX, day.replace("-", ""), ".jsonl.gz")


def export_day(conn: sqlite3.Connection, blk: dict, archive_dir: str) -> dict:
    """导出一个 UTC 日的全部记录（**只读**），返回锚点记录（已含 anchor_hash）"""
    rows = conn.execute(
        "SELECT * FROM audit_chain WHERE ts LIKE ? ORDER BY seq ASC",
        (blk["day"] + "%",)).fetchall()
    os.makedirs(archive_dir, exist_ok=True)
    name = _archive_name(blk["day"])
    path = os.path.join(archive_dir, name)
    # mtime=0：让归档文件可复现（同样的输入 ⇒ 同样的字节与 sha256）
    with gzip.GzipFile(path, "wb", mtime=0) as gz:
        for r in rows:
            gz.write((json.dumps({k: r[k] for k in r.keys()}, ensure_ascii=False,
                                 sort_keys=True) + "\n").encode("utf-8"))
    digest = _sha256_file(path)
    anchors = load_anchors(archive_dir)
    prev_anchor = anchors[-1]["anchor_hash"] if anchors else GENESIS_ANCHOR
    rec = {
        "day": blk["day"], "rows": len(rows),
        "first_seq": int(rows[0]["seq"]) if rows else 0,
        "last_seq": int(rows[-1]["seq"]) if rows else 0,
        "first_ts": str(rows[0]["ts"]) if rows else "",
        "last_ts": str(rows[-1]["ts"]) if rows else "",
        "first_prev_hash": str(rows[0]["prev_hash"]) if rows else "",
        "last_self_hash": str(rows[-1]["self_hash"]) if rows else "",
        "archive": name, "archive_sha256": digest,
        "archive_bytes": os.path.getsize(path),
        "prev_anchor_hash": prev_anchor,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    rec["anchor_hash"] = _anchor_hash(rec)
    return rec


def append_anchor(archive_dir: str, rec: dict) -> None:
    os.makedirs(archive_dir, exist_ok=True)
    with open(os.path.join(archive_dir, ANCHORS_NAME), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")


# ════════════════════════════════════════════════════════════
#  执行：归档 / 删除 / 校验
# ════════════════════════════════════════════════════════════

def do_archive(conn: sqlite3.Connection, plan: dict, archive_dir: str) -> dict:
    done, skipped = [], []
    for blk in plan["days"]:
        if blk["archive"] != "yes":
            if blk["archive"].startswith("skip"):
                skipped.append(blk["day"])
            continue
        rec = export_day(conn, blk, archive_dir)
        append_anchor(archive_dir, rec)
        done.append({"day": rec["day"], "rows": rec["rows"],
                     "archive": rec["archive"], "sha256": rec["archive_sha256"][:16]})
    return {"archived": done, "skipped_already_archived": skipped}


def do_prune(db_path: str, plan: dict, archive_dir: str) -> dict:
    """删除 **seq ≤ K 的链头前缀**（前缀内每一行都必须已归档且校验通过）

    单事务 + `BEGIN IMMEDIATE`（拿不到写锁 ⇒ 说明服务在写 ⇒ 直接放弃）。
    删除的是**连续的 seq 前缀**，因此剩下的链从 K+1 起仍然自洽，只是不再以创世哈希
    为锚——脚本会打印重新锚定所需的 (start_seq, anchor_prev_hash)。
    """
    k = int(plan.get("prune_upper_seq") or 0)
    blocked = plan.get("prune_blocked_days") or []
    if k <= 0:
        return {"deleted_rows": 0, "prune_upper_seq": 0,
                "note": "可删前缀为空（没有整段早于截止日的 seq 前缀）"}
    if blocked:
        raise SystemExit("拒绝删除：前缀内存在**未归档**的日 {}（先 --apply --archive）"
                         .format(blocked))
    anchors = {a["day"]: a for a in load_anchors(archive_dir)}
    days_in_prefix = [d["day"] for d in plan["days"] if int(d["first_seq"]) <= k]
    for day in days_in_prefix:
        rec = anchors.get(day)
        if rec is None:
            raise SystemExit("拒绝删除：{} 无锚点".format(day))
        if rec["archive_sha256"] != _sha256_file(
                os.path.join(archive_dir, rec["archive"])):
            raise SystemExit("拒绝删除：{} 的归档文件 sha256 与锚点不符".format(day))

    anchor_self_hash = ""
    conn = connect_rw(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT self_hash FROM audit_chain WHERE seq = ?",
                           (k,)).fetchone()
        anchor_self_hash = str(row["self_hash"]) if row is not None else ""
        cur = conn.execute("DELETE FROM audit_chain WHERE seq <= ?", (k,))
        deleted_rows = int(cur.rowcount or 0)
        head = conn.execute("SELECT MIN(seq) AS s FROM audit_chain").fetchone()
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:  # noqa: BLE001
            pass
        raise
    finally:
        conn.close()
    return {"deleted_rows": deleted_rows, "prune_upper_seq": k,
            "deleted_days": days_in_prefix,
            "first_retained_seq": int((head["s"] if head is not None else 0) or 0),
            "anchor_prev_hash": anchor_self_hash,
            "chain_anchor": [{"last_seq": k, "last_self_hash": anchor_self_hash}]}
def do_verify(conn: sqlite3.Connection, archive_dir: str) -> dict:
    """校验：锚点链自洽 + 归档完整性 + **归档子集的链接连续性** + 活库边界

    【为什么不能按"块首尾相接"校验（本仓实测）】日块与 seq **不同序**：
    2027-10-19 的块是 seq 928..2719，与 2026-09-14 的块（915..2782）交错。
    每个归档文件因此只是全链的一个**子集**，"上一块的 last_self_hash == 下一块的
    first_prev_hash"对交错日根本不成立。正确口径是先在内存里按 seq 建索引，
    再对**相邻且都在归档里**的 (seq, seq+1) 校验 prev_hash 链接：

    ① 锚点自洽：prev_anchor_hash 串成链、anchor_hash 重算一致；
    ② 归档文件与锚点一致：sha256、行数、seq 区间；同一 seq 只允许出现在一个归档里；
    ③ 链接：对每个 (s, s+1) 都在索引里的对，要求 prev_hash(s+1) == self_hash(s)；
    ④ 活库边界：归档最大 seq 的**下一条活库记录**，其 prev_hash 必须等于归档那条的
       self_hash；
    ⑤ 覆盖：归档区间内仍只在活库里的 seq 作为**信息**报出（不是问题——本策略允许
       只归档部分日；删行才要求前缀完整）。
    """
    anchors = load_anchors(archive_dir)
    problems = []
    prev = GENESIS_ANCHOR
    index = {}          # seq -> (self_hash, prev_hash, day)
    duplicates = []
    for i, a in enumerate(anchors):
        if a.get("prev_anchor_hash") != prev:
            problems.append("锚点 {} 的 prev_anchor_hash 与前一条 anchor_hash 不符".format(i + 1))
        if a.get("anchor_hash") != _anchor_hash(a):
            problems.append("锚点 {}（{}）的 anchor_hash 重算不符".format(i + 1, a.get("day")))
        path = os.path.join(archive_dir, str(a.get("archive") or ""))
        if not os.path.exists(path):
            problems.append("归档文件缺失：{}".format(a.get("archive")))
        elif _sha256_file(path) != a.get("archive_sha256"):
            problems.append("归档文件 sha256 不符：{}".format(a.get("archive")))
        else:
            seqs, rows_n = [], 0
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    row = json.loads(line)
                    rows_n += 1
                    seq = int(row["seq"])
                    seqs.append(seq)
                    if seq in index:
                        duplicates.append({"seq": seq, "day": a.get("day")})
                    index[seq] = (str(row["self_hash"]), str(row["prev_hash"]),
                                  a.get("day"))
            if rows_n != int(a.get("rows") or 0):
                problems.append("归档 {} 行数 {} != 锚点 {}".format(
                    a.get("day"), rows_n, a.get("rows")))
            if seqs and (seqs[0] != a.get("first_seq") or seqs[-1] != a.get("last_seq")):
                problems.append("归档 {} 的 seq 区间与锚点不符".format(a.get("day")))
        prev = a.get("anchor_hash", prev)
    if duplicates:
        problems.append("同一 seq 出现在多个归档里（{} 处，例如 {}）".format(
            len(duplicates), duplicates[:3]))

    ordered_seqs = sorted(index)
    link_checked, link_bad = 0, []
    for s in ordered_seqs:
        nxt = index.get(s + 1)
        if nxt is None:
            continue
        link_checked += 1
        if nxt[1] != index[s][0]:
            link_bad.append({"seq": s})
    if link_bad:
        problems.append("归档内相邻 (seq, seq+1) 的 prev_hash 链接断裂 {} 处：{}".format(
            len(link_bad), [x["seq"] for x in link_bad[:5]]))

    live_boundary = None
    if ordered_seqs:
        tail = ordered_seqs[-1]
        nxt = conn.execute("SELECT seq, prev_hash FROM audit_chain WHERE seq > ? "
                           "ORDER BY seq ASC LIMIT 1", (tail,)).fetchone()
        if nxt is not None:
            live_boundary = (str(nxt["prev_hash"]) == index[tail][0])
            if not live_boundary:
                problems.append("归档末端 seq={} 与活库 seq={} 的 prev_hash 不连续".format(
                    tail, nxt["seq"]))
    missing = ([s for s in range(ordered_seqs[0], ordered_seqs[-1] + 1) if s not in index]
               if ordered_seqs else [])
    return {"anchors": len(anchors), "archived_rows": len(index),
            "archived_seq_range": [ordered_seqs[0], ordered_seqs[-1]] if ordered_seqs else [],
            "link_pairs_checked": link_checked, "link_broken": len(link_bad),
            "live_boundary_linked": live_boundary,
            "still_live_only_rows": len(missing),
            "problems": problems, "ok": not problems}


# ════════════════════════════════════════════════════════════
#  CLI
# ════════════════════════════════════════════════════════════

def _print_plan(plan: dict, *, apply_archive: bool, apply_prune: bool) -> None:
    tag = "【DRY-RUN】" if not (apply_archive or apply_prune) else "【APPLY】"
    print("=" * 96)
    print("{} 审计链保留/轮转计划（默认 dry-run：不写文件、不删行）".format(tag))
    print("=" * 96)
    print("库            : {}".format(plan["db"]))
    print("参考日(ref)   : {}（--ref-day；默认 UTC 今天）".format(plan["ref_day"]))
    print("保留窗口      : 最近 {} 天 ⇒ 截止日 {}（早于该日的整天为归档候选）".format(
        plan["keep_days"], plan["cutoff_day"]))
    print("链总行数      : {}".format(plan["total_rows"]))
    print("单行均摊(估算): {} B（页面总数×页大小÷行数，含索引）".format(
        plan["row_bytes_estimate"]))
    print("链头日        : {}".format(plan["head_day"]))
    print("可删 seq 上界 : {}（窗口约束 {} ／ 链头日保护 {} -> 取小）".format(
        plan["prune_upper_seq"], plan["prune_upper_seq_by_window"],
        plan["prune_upper_seq_by_head_guard"]))
    print("-" * 96)
    print("{:<12} {:>8} {:>22} {:>9} {:>11} {:>10}  {}".format(
        "UTC 日", "行数", "seq 区间", "归档", "可删", "前缀内行数", "不可删原因"))
    for d in plan["days"]:
        print("{:<12} {:>8} {:>22} {:>9} {:>11} {:>10}  {}".format(
            d["day"], d["rows"], "{}..{}".format(d["first_seq"], d["last_seq"]),
            d["archive"], d["prune"], d["rows_in_prefix"], d["prune_blocked_reason"]))
    print("-" * 96)
    print("归档候选 {} 个日：{}".format(
        len(plan["archive_candidates"]), ", ".join(plan["archive_candidates"]) or "（无）"))
    print("删除候选 {} 个日：{}".format(
        len(plan["prune_candidates"]), ", ".join(plan["prune_candidates"]) or "（无）"))
    print("逻辑可回收(估算): {:.2f} MB（删除行不会自动归还磁盘，需 VACUUM；本脚本不做）".format(
        plan["est_reclaim_bytes"] / 1024.0 / 1024.0))
    if plan["prune_blocked_days"]:
        print("⛔ 删除被阻断：可删前缀内这些日**还没有归档** -> {}".format(
            ", ".join(plan["prune_blocked_days"])))
    if not (apply_archive or apply_prune):
        print()
        print("本次**没有**写任何文件、**没有**删除任何行（连接以 mode=ro 打开）。")
        print("要真正归档：--apply --archive（仍不删行）")
        print("要真正删除：--apply --archive --prune --confirm-chain-break {}".format(
            PRUNE_CONFIRM))
        print("删除会破坏 verify_chain() 的创世锚点；脚本会在删除后打印重新锚定的命令。")
    print("=" * 96)


def _print_reanchor(plan: dict, prune_result: dict) -> None:
    bounds = prune_result.get("chain_anchor") or []
    if not bounds:
        return
    last = bounds[-1]
    print()
    print("【删除后必须重新锚定】链头前缀已被移除，verify_chain() 的默认创世锚点不再适用：")
    first_kept = int(prune_result.get("first_retained_seq") or (last["last_seq"] + 1))
    print("  已删除 seq ≤ {}（共 {} 行）".format(last["last_seq"], prune_result.get("deleted_rows"))) 
    print("  第一条保留 seq = {}".format(first_kept))
    print("  锚点前驱哈希   = {}".format(last["last_self_hash"]))
    print("  校验命令（局部锚点重算，逐条比对两级哈希）：")
    print("    from agent.audit.chain import get_audit_chain")
    print("    chain = get_audit_chain()")
    print("    v = chain.verify_chain(start_seq={}, anchor_prev_hash={!r})".format(
        first_kept, last["last_self_hash"]))
    print("    print(v.ok, v.checked, v.reason)")
    print("  留痕建议（服务端执行，避免脚本绕过单写者纪律）：")
    print("    from agent.audit.facade import audit")
    print("    audit.record('retention.audit_chain.prune', actor='ops', "
          "payload={...}, status='ok')")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="审计链保留/轮转（默认只读 dry-run；需 --apply 才写文件/删行）")
    ap.add_argument("--db", default=DEFAULT_DB, help="审计库路径")
    ap.add_argument("--archive-dir", default=DEFAULT_ARCHIVE_DIR, help="冷归档目录")
    ap.add_argument("--keep-days", type=int, default=90, help="热库保留最近 N 天（默认 90）")
    ap.add_argument("--before", default="", help="显式截止日 YYYY-MM-DD（覆盖 --keep-days）")
    ap.add_argument("--ref-day", default="", help="计算截止日的参考日（默认 UTC 今天）")
    ap.add_argument("--apply", action="store_true", help="真正执行（默认什么都不写）")
    ap.add_argument("--archive", action="store_true", help="执行冷归档（需 --apply）")
    ap.add_argument("--prune", action="store_true", help="删除已归档的链头前缀（需 --apply）")
    ap.add_argument("--confirm-chain-break", default="",
                    help="删除确认口令：{}".format(PRUNE_CONFIRM))
    ap.add_argument("--verify", action="store_true", help="校验锚点链与边界连续性")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出计划/结果")
    args = ap.parse_args(argv)

    if not os.path.exists(args.db):
        print("审计库不存在：{}".format(args.db), file=sys.stderr)
        return 2

    ref_day = args.ref_day or _utc_today()
    cutoff_day = args.before or _shift_day(ref_day, -max(int(args.keep_days), 1))

    conn = connect_ro(args.db)                     # 全程只读连接（除非 --prune）
    try:
        plan = build_plan(conn, archive_dir=args.archive_dir, cutoff_day=cutoff_day,
                          ref_day=ref_day, keep_days=int(args.keep_days))
        if args.verify:
            result = do_verify(conn, args.archive_dir)
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=1))
            else:
                print("锚点条数      : {}".format(result["anchors"]))
                print("归档行数      : {}（seq 区间 {}）".format(
                    result["archived_rows"], result["archived_seq_range"]))
                print("相邻链接对    : {} 对，断裂 {} 处".format(
                    result["link_pairs_checked"], result["link_broken"]))
                print("活库边界连续  : {}".format(result["live_boundary_linked"]))
                print("归档区间内仍只在活库: {} 行（信息项，非问题）".format(
                    result["still_live_only_rows"]))
                print("结论          : {}".format("OK" if result["ok"] else "发现问题"))
                for p in result["problems"]:
                    print("  - {}".format(p))
            return 0 if result["ok"] else 1

        if not args.json:
            _print_plan(plan, apply_archive=bool(args.apply and args.archive),
                        apply_prune=bool(args.apply and args.prune))

        out = {"plan": plan, "applied": False}
        if args.apply and args.archive:
            res = do_archive(conn, plan, args.archive_dir)
            out["archive_result"] = res
            out["applied"] = True
            if not args.json:
                print("已归档 {} 个日（冷文件 + 哈希链锚点；**没有删除任何行**）：".format(
                    len(res["archived"])))
                for a in res["archived"]:
                    print("  {day}  {rows} 行  {archive}  sha256={sha256}…".format(**a))
                if res["skipped_already_archived"]:
                    print("跳过（已归档）：{}".format(
                        ", ".join(res["skipped_already_archived"])))
        if args.apply and args.prune:
            if args.confirm_chain_break != PRUNE_CONFIRM:
                print("拒绝删除：必须显式给出 --confirm-chain-break {}（链是合规证据）".format(
                    PRUNE_CONFIRM), file=sys.stderr)
                return 2
            # 归档可能刚在本次调用里完成 ⇒ **重算计划**再判定可删前缀
            # （否则 prune 永远看到一个"还没归档"的旧计划而拒绝执行）
            fresh = build_plan(conn, archive_dir=args.archive_dir, cutoff_day=cutoff_day,
                               ref_day=ref_day, keep_days=int(args.keep_days))
            out["prune_plan"] = {"prune_upper_seq": fresh["prune_upper_seq"],
                                 "prune_blocked_days": fresh["prune_blocked_days"]}
            out["prune_result"] = do_prune(args.db, fresh, args.archive_dir)
            out["applied"] = True
            if not args.json:
                print("已删除 {} 行（{} 个日块）".format(
                    out["prune_result"]["deleted_rows"],
                    len(out["prune_result"]["deleted_days"])))
                _print_reanchor(plan, out["prune_result"])
        if args.json:
            print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
