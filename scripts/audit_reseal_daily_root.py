#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""D5 · 每日 Merkle 根重封与覆盖补齐工具（**默认 dry-run**）

【干什么】
    只针对「每日 Merkle 根文件」（默认 data/audit/daily_roots.jsonl）的两个运维动作：
      1) 补封：某个**有记录**的 UTC 日没有日根 → 追加一条正确的根；
      2) 重封：某个 UTC 日的有效日根与当日实际不符 → 追加一条正确的根取代它。
    每个动作写完后**立即自验**（chain.verify_daily_root）并打印结果；
    任一自验不通过 ⇒ 显式报错并非零退出（退出码 3）。

【唯一副作用与不变量（代码强制，不是口头承诺）】
    a) 只向 <roots> **追加**行：运行前后逐字节断言「旧内容是新内容的**前缀**」，
       违反即中止（退出码 4）—— 删一行、改一个字节都会立刻被抓到；
    b) **不新增/不删除/不改写**审计链台账 audit_chain 的任何一行：
       台账以 role="reader" 打开（框架层 append() 直接抛 ReadOnlyChainError，
       本脚本不具备写台账的能力），且运行前后比对行数 / seq 区间 / 链头 self_hash /
       sha256 文件指纹；
    c) **不生成签名私钥**：私钥文件不存在时默认拒绝 --apply（退出码 2）。
       原因：RootsSigner 在密钥缺失且允许生成时会**现场生成并落盘**，
       那会改变日根的签名身份 —— 运维工具绝不能顺手做这件事。

【为什么默认 dry-run】
    daily_roots.jsonl 是合规证据。写动作必须显式加 --apply 才会发生。

【--chain-from：只给"文件尾部已存在断链记录"的现场用（AUDIT-ROOT-REPAIR 2026-09-26 新增）】
    `chain._append_daily_root()` 固定把新根的 `prev_entry_hash` 取成**文件末行**的
    `entry_hash`（`chain.py:3013-3018`，与《审计日根封印与导入流程约定》§4.2 一致）。
    但当尾部已经被**非本链的记录**污染（实测 2026-09-25 有 3 条竞争记录，其中第 2、3 条
    的 prev 指向同一行 ⇒ `_verify_root_chain` 的**位置式**遍历从第 12 行起永久断裂），
    再"续接末行"只会把新根接到一条**已断链的污染记录**后面，使文件**再也无法修复**：
    位置式校验在断点即返回，追加多少行都改不了断点之前的形状。
    `--chain-from <YYYY-MM-DD | 64位hex>` 把新根的 prev 显式指向**该日有效根的
    `entry_hash`**（= 跳过尾部污染记录、续接**最后一条真实生产根**）。默认 `tail`
    完全保持既有行为。为防"一次追加多行、彼此都用同一个 prev"这种新的断链，本选项
    **只允许单日**（多目标 ⇒ 退出码 2）。

【--all-missing 的作用范围（刻意的窄口径）】
    只补齐"有记录但**没有任何日根**"的 UTC 日，并且跳过 UTC 今日之后的日期
    （本仓实测存在 2027-10-19 / 2027-10-20 这类未来日期记录，成因未查清，
    不擅自为其封根）；要处理它们必须显式加 --include-future。

    它**刻意不**顺手重封"已有日根但验签不过"的日：那种日一律要用 --date 点名处理。
    理由见下一条 —— 批量动作最容易把"发现问题"变成"掩盖问题"。

【已有日根但验签不过：默认拒绝重封"篡改类"失败】
    重封会追加一条正确的根，于是该日在默认读路径上立刻**重新显示为通过**。
    对 root_hash_mismatch 这类"记录与实际不符"的历史缺陷，这正是我们要的修复
    （如生产 2026-09-21）；但对 entry_hash_mismatch / root_chain_broken /
    signature_invalid 这类**篡改类**失败，"重封"等于把篡改证据从默认路径上抹掉。
    故默认拒绝，必须由人显式加 --allow-tamper-reseal 才继续。

用法::

    python scripts/audit_reseal_daily_root.py                      # dry-run：列出全部缺根日
    python scripts/audit_reseal_daily_root.py --date 2026-09-21    # dry-run：只看某日
    python scripts/audit_reseal_daily_root.py --all-missing        # dry-run：只看缺根日
    python scripts/audit_reseal_daily_root.py --date 2026-09-21 --apply
    python scripts/audit_reseal_daily_root.py --all-missing --apply
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agent.audit.chain import (  # noqa: E402
    DEFAULT_DB_PATH,
    DEFAULT_KEY_PATH,
    DEFAULT_ROOTS_PATH,
    AuditChain,
    RootReseal,
    _normalize_day as _chain_normalize_day,
)

#: 当前有效根重放失败的"篡改类"原因：出现它们说明**链上数据/根文件本身**已被改动，
#: 此时"追加一条新根"会把这条发现从默认读路径（get_daily_root 取最后一条）上抹掉 ——
#: 那是掩盖而不是修复。故默认拒绝，必须由人显式加 --allow-tamper-reseal 才继续。
_TAMPER_REASONS = ("entry_hash_mismatch", "root_chain_broken", "signature_invalid")

_EXIT_OK = 0
_EXIT_USAGE = 2
_EXIT_VERIFY_FAILED = 3
_EXIT_INVARIANT_VIOLATED = 4

#: 禁止出现在本脚本源码里的 SQL 关键字（写台账的语句）。
#: 拆成两段拼接是**必须**的：否则这段自检的源码自身就会命中自己（恒为真）。
_DESTRUCTIVE_SQL = ("del" + "ete ", "upd" + "ate ", "dro" + "p ", "alt" + "er ",
                    "ins" + "ert ")


def _self_check_no_destructive_sql(source_path: Optional[str] = None) -> None:
    """静态自检：源码中不得出现任何写台账的 SQL 语句

    本脚本对台账只有读查询（SELECT / COUNT / GROUP BY）。这条自检把"只读"从注释
    变成可执行的断言：任何人日后往这里加写语句，脚本第一步就拒绝运行。
    `source_path` 只供测试指向**故意注入写语句**的副本，用来证明这条自检真的会红
    （否则它就是一条恒绿的空断言）。
    """
    src = pathlib.Path(source_path or __file__).read_text(encoding="utf-8").lower()
    hits = sorted(kw for kw in _DESTRUCTIVE_SQL if kw in src)
    if hits:
        raise RuntimeError("脚本源码出现写台账的 SQL 关键字: " + ", ".join(hits))


# ── 指纹与只读查询 ──────────────────────────────────────────

def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _file_fingerprint(path: str) -> Dict[str, Any]:
    """文件指纹（不存在 → exists=False 且各字段为空；**不创建**）

    键恒定存在（不因"文件不存在"而缺键），调用方不必到处判空。
    """
    if not os.path.exists(path):
        return {"exists": False, "size": 0, "mtime_ns": 0, "sha256": ""}
    st = os.stat(path)
    return {"exists": True, "size": st.st_size, "mtime_ns": st.st_mtime_ns,
            "sha256": _sha256_file(path)}


def _ro_connection(db_path: str) -> sqlite3.Connection:
    """台账**只读**连接（SQLite URI mode=ro；绝不创建文件）"""
    uri = pathlib.Path(db_path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _chain_fingerprint(db_path: str) -> Dict[str, Any]:
    """台账结构指纹：行数 / seq 区间 / 链头 self_hash（只读连接，不改库）"""
    out: Dict[str, Any] = {"rows": 0, "min_seq": 0, "max_seq": 0, "head": ""}
    conn = _ro_connection(db_path)
    try:
        row = conn.execute("SELECT COUNT(*) AS n, MIN(seq) AS lo, MAX(seq) AS hi "
                           "FROM audit_chain").fetchone()
        out["rows"] = int(row["n"] or 0)
        out["min_seq"] = int(row["lo"] or 0)
        out["max_seq"] = int(row["hi"] or 0)
        head = conn.execute("SELECT self_hash FROM audit_chain "
                            "ORDER BY seq DESC LIMIT 1").fetchone()
        out["head"] = str(head["self_hash"]) if head is not None else ""
    finally:
        conn.close()
    return out


def _utc_day_counts(db_path: str) -> Dict[str, int]:
    """各 UTC 日的记录数（substr(ts,1,10) 分组；与台账同一只读连接）"""
    conn = _ro_connection(db_path)
    try:
        rows = conn.execute("SELECT substr(ts,1,10) AS d, COUNT(*) AS n "
                            "FROM audit_chain GROUP BY d ORDER BY d").fetchall()
        return {str(r["d"]): int(r["n"]) for r in rows}
    finally:
        conn.close()


def _norm_day(value: Any) -> str:
    """归一为 YYYY-MM-DD（UTC）—— 直接复用 chain 的实现，保证与验签侧**同一口径**"""
    return _chain_normalize_day(value)


#: `--chain-from` 的显式哈希形态（sha256 十六进制）
_HEX64_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _resolve_chain_from(value: Any, chain: AuditChain) -> Optional[str]:
    """把 `--chain-from` 解析为 `prev_entry_hash`（**只读**；`None` = 交给 chain 既有语义）

    - `tail`（默认）：返回 None ⇒ 行为与改动前**逐字相同**；
    - 64 位十六进制：直接采用（取证/复算场景）；
    - `YYYY-MM-DD`：取该 UTC 日**有效根**（`resolve_daily_root`，默认=最后一条）的
      `entry_hash` —— 即"续接最后一条真实根"，跳过尾部的污染记录。
      该日没有任何日根 ⇒ 抛 ValueError（**绝不硬编一个假的 prev**）。
    """
    raw = str(value if value is not None else "tail").strip()
    if raw == "" or raw.lower() == "tail":
        return None
    if _HEX64_RE.match(raw):
        return raw.lower()
    day = _norm_day(raw)
    rec = chain.resolve_daily_root(day)
    if rec is None:
        raise ValueError("--chain-from %s：该 UTC 日在日根文件里**没有任何**记录，"
                         "拒绝凭空指定 prev（请显式给 64 位 entry_hash）" % raw)
    if not rec.entry_hash:
        raise ValueError("--chain-from %s：该日的有效根记录没有 entry_hash" % raw)
    return str(rec.entry_hash)


def _read_bytes(path: str) -> bytes:
    """读文件字节；**不存在 → b""**（绝不创建）

    日根文件在"从未封过任何根"的部署里并不存在，而重封恰恰要处理这种状态；
    把它当成错误会让首封无法进行。
    """
    if not os.path.exists(path):
        return b""
    with open(path, "rb") as fh:
        return fh.read()


def _roots_line_count(path: str) -> int:
    if not os.path.exists(path):
        return 0
    with open(path, "r", encoding="utf-8") as fh:
        return sum(1 for line in fh if line.strip())


# ── 计划 ────────────────────────────────────────────────────

class RefusedByPolicy(Exception):
    """策略性拒绝：继续执行会掩盖已有发现（不是用法错误，但同样要求人工介入）"""


class Target(object):
    """一个待处理（或待跳过）的 UTC 日"""

    def __init__(self, day: str, count: int, effective: Any, verification: Any,
                 expected: Any, action: str) -> None:
        self.day = day
        self.count = count
        self.effective = effective
        self.verification = verification
        self.expected = expected        # DailyRoot（write=False 的预演结果；跳过时不计算）
        self.action = action            # 补封 / 重封 / 跳过

    @property
    def will_write(self) -> bool:
        return self.action in ("补封", "重封")


def _days_to_consider(args: argparse.Namespace, day_counts: Dict[str, int],
                      chain: AuditChain) -> Tuple[List[str], List[str]]:
    """确定候选日期集合：--date 优先；否则（--all-missing）取"有记录但没有日根"的日"""
    if args.date:
        days = [_norm_day(d) for d in args.date]
        unknown = [d for d in days if d not in day_counts]
        if unknown:
            raise ValueError("以下日期在台账里没有任何记录: " + ", ".join(unknown))
        return sorted(set(days)), []
    missing = [d for d in sorted(day_counts)
               if not chain.daily_root_records(d)]
    return missing, []


def _build_targets(args: argparse.Namespace, chain: AuditChain,
                   days: List[str], day_counts: Dict[str, int],
                   today: str) -> Tuple[List[Target], List[Tuple[str, str]]]:
    targets: List[Target] = []
    skipped: List[Tuple[str, str]] = []
    excludes = {_norm_day(d) for d in (args.exclude or [])}
    for day in days:
        if day in excludes:
            skipped.append((day, "显式 --exclude"))
            continue
        if day > today and not args.include_future:
            skipped.append((day, "未来日期（UTC 今日 " + today
                            + "）；需 --include-future 才处理"))
            continue
        recs = chain.daily_root_records(day)
        effective = recs[-1][1] if recs else None
        verification = chain.verify_daily_root(day) if effective is not None else None
        if effective is not None and verification is not None and verification.ok:
            targets.append(Target(day, day_counts[day], effective, verification,
                                  None, "跳过"))
            continue
        if (effective is not None and verification is not None
                and not verification.ok
                and verification.reason in _TAMPER_REASONS
                and not args.allow_tamper_reseal):
            raise RefusedByPolicy(
                "%s 的当前有效根重放失败，原因 %s 属**篡改类**（链上记录或根文件被改）：%s"
                % (day, verification.reason, verification.detail or ""))
        expected = chain.daily_merkle_root(day, write=False, sign=False)
        action = "补封" if effective is None else "重封"
        targets.append(Target(day, day_counts[day], effective, verification,
                              expected, action))
    return targets, skipped


def _print_plan(args: argparse.Namespace, targets: List[Target],
                skipped: List[Tuple[str, str]], chain: AuditChain,
                roots_path: str, day_counts: Dict[str, int]) -> None:
    print()
    print("── 计划（台账 %d 个 UTC 日；日根文件 %d 行）" % (len(day_counts),
                                                       _roots_line_count(roots_path)))
    if not targets:
        print("   没有需要处理的日期。")
    for i, t in enumerate(targets, 1):
        if t.action == "跳过":
            print("   [%d] %s  %s  —— 当前有效根已通过验签（幂等，不写）"
                  % (i, t.day, t.action))
            continue
        e = t.expected
        print("   [%d] %s  %s  当日记录=%d" % (i, t.day, t.action, t.count))
        if t.effective is None:
            print("        当前有效根: 无（该日从未封根）")
        else:
            v = t.verification
            print("        当前有效根: root=%s leaf_count=%d seq %d..%d "
                  "scheme=%s" % (t.effective.root_hash[:16] + "...",
                                 t.effective.leaf_count, t.effective.first_seq,
                                 t.effective.last_seq, t.effective.signature_scheme))
            print("        当前验签  : ok=%s reason=%s detail=%s"
                  % (v.ok, v.reason or "-", v.detail or "-"))
        print("        将追加    : root=%s leaf_count=%d seq %d..%d"
              % (e.root_hash[:16] + "...", e.leaf_count, e.first_seq, e.last_seq))
        print("        签名      : %s" % ("ed25519（私钥就绪）" if args.apply
                                          else "ed25519（--apply 时用私钥签名）"))
    for day, why in skipped:
        print("   [-] %s  跳过：%s" % (day, why))


# ── 主流程 ──────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="audit_reseal_daily_root.py",
        description="D5 每日 Merkle 根重封与覆盖补齐（默认 dry-run；只追加根记录）",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--date", action="append", default=[], metavar="YYYY-MM-DD",
                   help="只处理该 UTC 日（可重复）")
    p.add_argument("--all-missing", action="store_true",
                   help="补齐所有「有记录但没有日根」的 UTC 日")
    p.add_argument("--apply", action="store_true",
                   help="真正写入（缺省为 dry-run：只打印将要做什么）")
    p.add_argument("--exclude", action="append", default=[], metavar="YYYY-MM-DD",
                   help="排除某日（可重复）")
    p.add_argument("--include-future", action="store_true",
                   help="允许处理 UTC 今日之后的日期（默认跳过并只报告）")
    p.add_argument("--allow-degraded-signing", action="store_true",
                   help="私钥缺失时允许走 sha256 自签降级路径（默认拒绝 --apply）")
    p.add_argument("--allow-tamper-reseal", action="store_true",
                   help="允许重封篡改类失败（entry_hash_mismatch / root_chain_broken / "
                        "signature_invalid）的日期；默认拒绝，避免把篡改发现抹掉")
    p.add_argument("--chain-from", default="tail",
                   metavar="tail|YYYY-MM-DD|<64hex>",
                   help="新根的 prev_entry_hash 取值：tail（默认=文件末行 entry_hash，"
                        "既有语义不变）；YYYY-MM-DD（=该日有效根的 entry_hash，"
                        "用于跳过尾部已断链的污染记录）；或显式 64 位十六进制。"
                        "非 tail 时只允许单日，避免一次追加多行互相断链")
    p.add_argument("--db", default="", help="审计台账路径")
    p.add_argument("--roots", default="", help="日根文件路径")
    p.add_argument("--key-path", default="", help="ed25519 私钥路径")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001 控制台不支持则忽略
        pass

    try:
        _self_check_no_destructive_sql()
    except RuntimeError as exc:
        print("[不变量自检] 失败：%s" % exc)
        return _EXIT_INVARIANT_VIOLATED

    db_path = os.path.abspath(args.db or DEFAULT_DB_PATH)
    roots_path = os.path.abspath(args.roots or DEFAULT_ROOTS_PATH)
    key_path = os.path.abspath(args.key_path or DEFAULT_KEY_PATH)
    today = datetime.now(timezone.utc).date().isoformat()

    print("=" * 78)
    print("D5 每日 Merkle 根重封与覆盖补齐   pid=%d   模式=%s"
          % (os.getpid(), "APPLY（真写日根）" if args.apply
             else "DRY-RUN（不写任何东西）"))
    print("=" * 78)
    print("  台账   : %s" % db_path)
    print("  日根   : %s" % roots_path)
    print("  私钥   : %s" % key_path)
    print("  UTC今日: %s" % today)
    print("  不变量 : 只追加日根行；台账零写入；不生成私钥")

    if not (args.date or args.all_missing):
        print()
        print("[用法] 必须显式指定 --date 或 --all-missing（默认不猜目标）")
        return _EXIT_USAGE
    if args.apply and not (args.date or args.all_missing):
        print()
        print("[用法] --apply 必须配合 --date / --all-missing")
        return _EXIT_USAGE
    if not os.path.exists(db_path):
        print()
        print("[前置] 台账不存在：%s" % db_path)
        print("       拒绝执行（且**不创建**该文件）")
        return _EXIT_USAGE

    key_exists = os.path.exists(key_path)
    if args.apply and not key_exists and not args.allow_degraded_signing:
        print()
        print("[前置] ed25519 私钥不存在：%s" % key_path)
        print("       继续 --apply 会让签名器**现场生成新私钥**（改变日根签名身份），")
        print("       故拒绝；如确认要走 sha256 自签降级路径，显式加 --allow-degraded-signing。")
        return _EXIT_USAGE

    # 台账一律以 reader 打开：不具备写台账能力（append() 抛 ReadOnlyChainError）
    chain = AuditChain.reader(db_path, roots_path=roots_path,
                              signing_key_path=key_path,
                              signing_enabled=bool(args.apply and key_exists),
                              daily_root_protect=True)
    try:
        try:
            prev_override = _resolve_chain_from(args.chain_from, chain)
        except ValueError as exc:
            print()
            print("[用法] %s" % exc)
            return _EXIT_USAGE
        day_counts = _utc_day_counts(db_path)
        # 口径交叉校验：SQL 分组计数 vs chain.count(day=)（后者与验签侧同一 WHERE 口径）
        for day in sorted(day_counts):
            n = chain.count(day=day)
            if n != day_counts[day]:
                print()
                print("[口径] 分叉：%s SQL 计数=%d 而 chain.count(day=)=%d ⇒ 拒绝继续"
                      % (day, day_counts[day], n))
                return _EXIT_USAGE
        print("  口径   : SQL 日分组计数 与 chain.count(day=) 逐日一致（%d 天）"
              % len(day_counts))

        try:
            days, _ = _days_to_consider(args, day_counts, chain)
            targets, skipped = _build_targets(args, chain, days, day_counts, today)
        except ValueError as exc:
            print()
            print("[用法] %s" % exc)
            print("       本工具只处理**台账里有记录**的 UTC 日（不给没有记录的日期凭空封根）")
            return _EXIT_USAGE
        except RefusedByPolicy as exc:
            print()
            print("[策略拒绝] %s" % exc)
            print("       追加一条新根只会让该日**重新显示为通过**，掩盖已有发现；")
            print("       请先查清改动来源。确认要覆盖该发现时，显式加 --allow-tamper-reseal。")
            return _EXIT_USAGE
        _print_plan(args, targets, skipped, chain, roots_path, day_counts)
        if prev_override is not None:
            print("   chain-from: %s ⇒ 本条新根的 prev_entry_hash=%s（显式续接，"
                  "刻意跳过尾部记录）" % (args.chain_from, prev_override[:16] + "…"))

        if not args.apply:
            print()
            print("[DRY-RUN] 以上为将要执行的动作；未写入任何文件。")
            print("          真要写入请加 --apply。")
            return _EXIT_OK

        todo = [t for t in targets if t.will_write]
        if prev_override is not None and len(todo) > 1:
            print()
            print("[用法] --chain-from 非 tail 时只允许**一个**待写日期（实际 %d 个）："
                  "多行共用同一个 prev 会当场制造新的断链" % len(todo))
            return _EXIT_USAGE
        if prev_override is not None:
            #: 唯一的进程内遮蔽：`_append_daily_root` 通过实例属性调用它 ⇒
            #: 新根的 prev_entry_hash 取显式值，其余（canonical json / fsync /
            #: 只读保护 / entry_hash 重算）全部仍走 chain 的既有实现。
            chain._last_root_entry_hash = lambda: prev_override
        if not todo:
            print()
            print("[APPLY] 没有需要写入的日期（全部已通过验签）—— 幂等，未写入任何文件。")
            return _EXIT_OK

        # ── 写前快照（不变量基线）──
        fp_db0 = _file_fingerprint(db_path)
        fp_roots0 = _file_fingerprint(roots_path)
        cf0 = _chain_fingerprint(db_path)
        roots_bytes0 = _read_bytes(roots_path)
        lines0 = _roots_line_count(roots_path)
        print()
        print("── 写入前快照")
        print("   台账 行数=%d seq %d..%d 链头=%s sha256=%s mtime_ns=%s"
              % (cf0["rows"], cf0["min_seq"], cf0["max_seq"], cf0["head"][:16],
                 fp_db0["sha256"][:32], fp_db0["mtime_ns"]))
        print("   日根 行数=%d sha256=%s mtime_ns=%s%s"
              % (lines0, (fp_roots0["sha256"][:32] or "-"),
                 fp_roots0["mtime_ns"] or "-",
                 "" if fp_roots0["exists"] else "（文件此前不存在）"))

        # ── 执行（唯一写动作：chain.reseal_daily_root → 只追加一条日根行）──
        print()
        print("── 执行（每个日期：追加一条根 → 立即 verify_daily_root 自验）")
        results: List[Tuple[Target, RootReseal]] = []
        for i, t in enumerate(todo, 1):
            res = chain.reseal_daily_root(t.day, verify=True)
            results.append((t, res))
            v = res.verification
            print("   [%d/%d] %s %s applied=%s reason=%s"
                  % (i, len(todo), t.day, t.action, res.applied, res.reason or "-"))
            if res.applied and res.current is not None:
                print("         追加 : root=%s leaf_count=%d seq %d..%d scheme=%s "
                      "degraded=%s"
                      % (res.current.root_hash, res.current.leaf_count,
                         res.current.first_seq, res.current.last_seq,
                         res.current.signature_scheme, res.current.degraded))
            if v is not None:
                print("         自验 : ok=%s reason=%s 叶子=%d（根记录 %d） seal=%d/%d "
                      "sig=%s root_chain=%s"
                      % (v.ok, v.reason or "-", v.entries_verified,
                         res.current.leaf_count if res.current is not None else -1,
                         v.seal_index, v.seal_total, v.signature_ok, v.chains_ok))
                if not v.ok:
                    print("         自验失败 detail: %s" % (v.detail or ""))
                _rc = (getattr(v, "recomputed_root", "") == getattr(v, "recorded_root", ""))
                print("         自验明细: 根哈希重算一致=%s 签名=%s 外层根链=%s 叶子=%d/%d"
                      % (_rc, v.signature_ok, v.chains_ok, v.entries_verified,
                         res.current.leaf_count if res.current is not None else -1))
                if (not v.ok and _rc and v.signature_ok and not v.chains_ok
                        and res.current is not None
                        and v.entries_verified == res.current.leaf_count):
                    print("         ⇒ 该日根**本体已正确**（重算一致、叶子数一致、签名有效）；"
                          "失败只来自**外层根链**——文件尾部存在"
                          "\"prev_entry_hash 与前一条 entry_hash 不一致\"的既有记录，"
                          "追加无法修复断点之前的形状（见 AUDITROOT.md §4/§7）")

        # ── 不变量断言（写后）──
        fp_db1 = _file_fingerprint(db_path)
        fp_roots1 = _file_fingerprint(roots_path)
        cf1 = _chain_fingerprint(db_path)
        roots_bytes1 = _read_bytes(roots_path)
        lines1 = _roots_line_count(roots_path)

        print()
        print("── 不变量核对（写后）")
        ok_prefix = roots_bytes1.startswith(roots_bytes0)
        ok_grow = lines1 >= lines0
        print("   日根 只追加（旧内容是新内容的前缀）=%s  行数 %d -> %d %s"
              % (ok_prefix, lines0, lines1,
                 "（+%d）" % (lines1 - lines0) if lines1 >= lines0 else ""))
        same_chain = (cf0["rows"] == cf1["rows"] and cf0["min_seq"] == cf1["min_seq"]
                      and cf0["max_seq"] == cf1["max_seq"] and cf0["head"] == cf1["head"])
        print("   台账 行数 %d -> %d   seq %d..%d -> %d..%d   链头相同=%s"
              % (cf0["rows"], cf1["rows"], cf0["min_seq"], cf0["max_seq"],
                 cf1["min_seq"], cf1["max_seq"], cf0["head"] == cf1["head"]))
        print("   台账 无新增/无删除=%s" % same_chain)
        print("   台账 sha256 %s -> %s  未变=%s"
              % (fp_db0["sha256"][:32], fp_db1["sha256"][:32],
                 fp_db0["sha256"] == fp_db1["sha256"]))
        if fp_db0["sha256"] != fp_db1["sha256"] and same_chain:
            print("        注：文件指纹变化但结构未变 —— 可能有**其它进程**在写台账"
                  "（本脚本 role=reader，不具备写台账能力）。请以结构指纹为准并复核。")

        # 新增行原文（证据）
        print()
        print("── 本次新增的日根行（原始 JSON，逐行）")
        new_text = roots_bytes1[len(roots_bytes0):].decode("utf-8", errors="replace")
        for line in new_text.splitlines():
            if line.strip():
                print("   " + line)

        print()
        failed = [(t.day, r.verification) for t, r in results
                  if r.verification is None or not r.verification.ok]
        if not ok_prefix or not ok_grow or not same_chain:
            print("[结果] 不变量被破坏 ⇒ 退出码 %d" % _EXIT_INVARIANT_VIOLATED)
            return _EXIT_INVARIANT_VIOLATED
        if failed:
            for day, v in failed:
                print("[结果] %s 重封后自验**未通过**：%s %s"
                      % (day, getattr(v, "reason", "no-verification"),
                         getattr(v, "detail", "")))
            print("[结果] 存在自验失败的日期 ⇒ 退出码 %d" % _EXIT_VERIFY_FAILED)
            return _EXIT_VERIFY_FAILED
        print("[结果] 全部 %d 个日期重封后 verify_daily_root 通过；"
              "日根文件只追加、台账零写入 ⇒ 退出码 0" % len(todo))
        return _EXIT_OK
    finally:
        try:
            chain.close(timeout=5.0)
        except Exception:  # noqa: BLE001 收尾失败不影响结论
            pass


if __name__ == "__main__":
    raise SystemExit(main())
