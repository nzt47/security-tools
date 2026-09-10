#!/usr/bin/env python3
"""链式审计验签 CLI（CloudPivot v7.2 §3.5；混沌演练 §11.10「向审计链注入一条篡改」）

功能：
    1. 全链（或指定区间）重算 self_hash / payload_hash / prev_hash，报告 OK 或
       **首个篡改 seq**（并列出篡改点之后的全部失败位置——哈希前向传播所致）；
    2. 每日 Merkle 根重放校验（根哈希重算 + ed25519 签名 + 外层根链连续性）；
    3. 台账摘要（条数 / 来源分布 / 链头 / 校验结论 / 降级与签名状态）。

用法：
    python scripts/verify_audit_chain.py                       # 校验默认台账
    python scripts/verify_audit_chain.py --db <path>           # 指定台账
    python scripts/verify_audit_chain.py --roots-check all     # 全量每日根重放
    python scripts/verify_audit_chain.py --stats --json        # 摘要（JSON）
    python scripts/verify_audit_chain.py --seq 100             # 从锚点 100 起校验

退出码：0=链完整；1=检出篡改/不一致；2=用法或 IO 错误（供混沌演练与 CI 判定）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from agent.audit.chain import (  # noqa: E402
    DEFAULT_DB_PATH,
    DEFAULT_ROOTS_PATH,
    AuditChain,
    AuditChainError,
)

EXIT_OK = 0
EXIT_TAMPERED = 1
EXIT_USAGE = 2


def _print(text: str = "") -> None:
    print(text)


def _report_chain(verification, *, as_json: bool, show_bad: int = 20) -> None:
    if as_json:
        return
    _print(f"[链式校验] {verification.summary()}")
    if not verification.ok and verification.bad_seqs:
        _print(f"  注入点 seq={verification.first_bad_seq}"
               f"（{verification.reason}）——后续全部失败明细（前 {show_bad} 条）：")
        for item in verification.bad_seqs[:show_bad]:
            _print(f"    - seq={item['seq']:<6} {item['reason']:<22} {item['detail']}")
        if len(verification.bad_seqs) > show_bad:
            _print(f"    … 其余 {len(verification.bad_seqs) - show_bad} 条同类失败已省略")


def run(args: argparse.Namespace) -> int:
    db_path = args.db or DEFAULT_DB_PATH
    roots_path = args.roots or DEFAULT_ROOTS_PATH
    if not os.path.exists(db_path) and not args.allow_missing:
        _print(f"[错误] 台账不存在: {db_path}")
        _print("       （若尚未产生审计记录，属预期；用 --allow-missing 视为空链）")
        return EXIT_USAGE

    try:
        chain = AuditChain.reader(db_path, roots_path=roots_path,
                                  signing_enabled=False)
    except (AuditChainError, OSError) as e:
        _print(f"[错误] 打开台账失败: {e}")
        return EXIT_USAGE

    verification = chain.verify_chain(start_seq=args.seq, end_seq=args.end_seq)
    root_reports = []
    if args.roots_check:
        if args.roots_check == "all":
            root_reports = chain.verify_daily_roots_all()
        else:
            root_reports = [chain.verify_daily_root(args.roots_check)]

    payload = {
        "db_path": db_path,
        "roots_path": roots_path,
        "chain": verification.to_dict(),
        "daily_roots": [r.to_dict() for r in root_reports],
    }
    if args.stats:
        payload["stats"] = chain.stats(verify=False)

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _report_chain(verification, as_json=False)
        for rep in root_reports:
            _print(f"[每日根] {rep.summary()}")
        if args.stats:
            st = payload["stats"]
            schemes = sorted({r.get("signature_scheme", "") for r in payload["daily_roots"]
                              if r.get("signature_scheme")}) or [st["signing_scheme"]]
            _print(f"[台账] 条数={st['total']} seq={st['first_seq']}..{st['last_seq']} "
                   f"来源={st['by_source']} 每日根签名={'/'.join(schemes)}")
            _print(f"       链头 self_hash={st['head_self_hash'][:32]}… "
                   f"每日根={st['daily_roots']} 降级={st['degraded']}")

    ok = verification.ok and all(r.ok for r in root_reports)
    if not ok:
        return EXIT_TAMPERED
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="verify_audit_chain.py",
        description="链式审计验签（§3.5）：全链重算比对 + 每日 Merkle 根重放")
    p.add_argument("--db", default="", help="链式审计台账路径（默认 data/audit/audit_chain.db）")
    p.add_argument("--roots", default="", help="每日根文件路径（默认 data/audit/daily_roots.jsonl）")
    p.add_argument("--seq", type=int, default=None, help="从锚点 seq 起校验（局部锚点重算）")
    p.add_argument("--end-seq", type=int, default=None, help="校验到 seq 止（含）")
    p.add_argument("--roots-check", nargs="?", const="all", default="",
                   help="校验每日根：'all' 或 'YYYY-MM-DD'（缺省不校验）")
    p.add_argument("--stats", action="store_true", help="附带台账摘要")
    p.add_argument("--json", action="store_true", help="JSON 输出（供 CI/演练脚本消费）")
    p.add_argument("--allow-missing", action="store_true",
                   help="台账缺失时按空链处理（返回 0）")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except KeyboardInterrupt:  # pragma: no cover
        return EXIT_USAGE
    except Exception as e:  # noqa: BLE001 CLI 兜底：异常视为用法/IO 错误
        _print(f"[错误] {type(e).__name__}: {e}")
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
