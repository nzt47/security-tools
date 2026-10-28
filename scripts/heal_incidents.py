#!/usr/bin/env python3
"""自愈事故卡的运维处置：列出 / 归档 / 补齐要素后 resolve（**默认 dry-run**）

【为什么需要它（2026-10-28 实测）】
    「治理面板 · 自愈事故」长期显示 14 起未闭合，全部是出域链路监测（5.7 机制 4）在
    **测试进程**里产生的卡（target_host=api.example.com、trace_ids 空、missing_elements
    五项全缺），而它们按设计**无法 resolve**：IncidentCard.resolve() 要求六要素齐备，
    而 raise_incident() 只填 root_cause/fatal_change/trace_ids。

    ⇒ 正确处置**不是**手工把 status 改成 resolved（那等于给测试产物编造「致命变更/回归用例」，
      污染 MTTD/MTTR，正是六要素机制要防的「不可追溯的 97%」），而是：
      ① 归档（移出 data/healing_incidents/，面板不再计入 open，JSON 原样保留可追溯）；
      ② 或对**真的能补齐**的卡补要素后 resolve。

用法：
    python scripts/heal_incidents.py --list
    python scripts/heal_incidents.py --archive --ids inc-a,inc-b --reason "误报：占位域"        # 预览
    python scripts/heal_incidents.py --archive --ids inc-a,inc-b --reason "误报：占位域" --confirm
    python scripts/heal_incidents.py --archive --filter-host api.example.com --reason "..." --confirm
    python scripts/heal_incidents.py --resolve --ids inc-c --confirm      # 仅对六要素齐备的卡
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import datetime
from typing import Dict, List, Optional, Sequence

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# 【坑】Windows 控制台默认 GBK，卡片里有特殊字符时直接 print 会 UnicodeEncodeError
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

from agent.self_healing.levels import (  # noqa: E402
    STATUS_RESOLVED,
    IncidentCard,
    incidents_dir,
    list_incidents,
    save_incident,
)

ARCHIVE_DIRNAME = "_archive"


def _now_tag() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _select(cards: Sequence[IncidentCard], ids: Sequence[str],
            host: str, status: str) -> List[IncidentCard]:
    """按 id / host / status 过滤（都不给 = 不过滤）"""
    wanted = {str(i).strip() for i in ids if str(i).strip()}
    out: List[IncidentCard] = []
    for c in cards:
        if wanted and c.incident_id not in wanted:
            continue
        if host and str((c.detail or {}).get("target_host") or "") != host:
            continue
        if status and c.status != status:
            continue
        out.append(c)
    return out


def _print_table(cards: Sequence[IncidentCard]) -> None:
    open_cards = [c for c in cards if c.status != STATUS_RESOLVED]
    blocked = [c for c in open_cards if not c.is_resolvable()]
    print("共 %d 张卡；未闭合 %d 张（其中缺要素、按设计不可 resolve：%d 张）"
          % (len(cards), len(open_cards), len(blocked)))
    print("-" * 96)
    for c in sorted(cards, key=lambda x: (x.created_at or "")):
        missing = ",".join(c.missing_elements()) or "-"
        host = str((c.detail or {}).get("target_host") or "-")
        print("%s  %3s  %-8s  %19s  host=%-24s  缺=%s" % (
            c.incident_id, c.severity.value, c.status,
            (c.created_at or "")[:19], host, missing))


def _audit(action: str, subject: str, payload: Dict[str, object]) -> Optional[int]:
    """写一条审计并 flush（一次性进程必须 flush，否则只停在 seqjournal，见交接陷阱 11）"""
    try:
        from agent.audit.facade import audit
        entry = audit.record(action=action, actor="ops", subject=subject,
                             payload=payload, source="system")
        chain = audit.chain
        if chain is not None:
            chain.flush()
        return int(getattr(entry, "seq", 0) or 0)
    except Exception as exc:  # noqa: BLE001 审计失败必须显式告警（状态变更不能留白）
        print("[WARN] 审计写入失败: %s: %s" % (type(exc).__name__, exc))
        return None


def _archive_one(card: IncidentCard, *, directory: Optional[str],
                 reason: str, confirm: bool) -> bool:
    base = incidents_dir(directory)
    src = base / (card.incident_id + ".json")
    dest_dir = base / ARCHIVE_DIRNAME / _now_tag()
    dest = dest_dir / (card.incident_id + ".json")
    print("  -> %s: %s  =>  %s/%s" % (card.incident_id, src.name, dest_dir.name, dest.name))
    print("     reason: %s" % reason)
    if not confirm:
        return True
    if dest.exists():
        print("     [SKIP] 目标已存在，避免覆盖：%s" % dest)
        return False
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="自愈事故卡处置（默认 dry-run）")
    ap.add_argument("--list", action="store_true", help="只列出（默认动作）")
    ap.add_argument("--archive", action="store_true", help="归档（移出事故目录，保留 JSON）")
    ap.add_argument("--resolve", action="store_true", help="resolve（仅六要素齐备才行）")
    ap.add_argument("--ids", default="", help="逗号分隔的 incident id")
    ap.add_argument("--filter-host", default="", help="按 detail.target_host 过滤（批量归档用）")
    ap.add_argument("--status", default="", help="按状态过滤（open / resolved）")
    ap.add_argument("--reason", default="", help="处置理由（archive/resolve 必填，进审计）")
    ap.add_argument("--incidents-dir", default="", help="事故卡目录（默认 env / data/healing_incidents）")
    ap.add_argument("--confirm", action="store_true", help="真正执行（缺省只预览）")
    args = ap.parse_args()

    directory = args.incidents_dir or None
    cards = list_incidents(directory=directory)
    ids = [x for x in str(args.ids or "").split(",") if x.strip()]

    if not (args.archive or args.resolve):
        _print_table(cards)
        return 0

    if not str(args.reason or "").strip():
        print("[FAIL] --archive / --resolve 必须给 --reason（处置理由要进审计）")
        return 2
    if not ids and not args.filter_host:
        print("[FAIL] 必须用 --ids 或 --filter-host 指定目标（不做全局一把梭）")
        return 2

    targets = _select(cards, ids, args.filter_host, args.status)
    unknown = sorted(set(ids) - {c.incident_id for c in cards})
    if unknown:
        print("[WARN] 未找到的 id：" + ",".join(unknown))
    if not targets:
        print("[OK] 没有匹配的事故卡，什么都没做")
        return 0

    mode = "archive" if args.archive else "resolve"
    print("=== %s（%s）===" % ("预览" if not args.confirm else "执行", mode))
    done: List[str] = []
    skipped: List[str] = []
    if args.archive:
        resolved_ones = [c.incident_id for c in targets if c.status == STATUS_RESOLVED]
        if resolved_ones:
            print("[WARN] 已是 resolved、归档无意义：" + ",".join(resolved_ones))
        for c in targets:
            if _archive_one(c, directory=directory, reason=args.reason, confirm=args.confirm):
                done.append(c.incident_id)
            else:
                skipped.append(c.incident_id)
    else:
        for c in targets:
            if not c.is_resolvable():
                print("  [BLOCKED] %s 缺要素：%s（先补齐再 resolve；否则请用 --archive）"
                      % (c.incident_id, ",".join(c.missing_elements())))
                skipped.append(c.incident_id)
                continue
            print("  -> %s: open => resolved" % c.incident_id)
            if args.confirm:
                c.resolve()
                save_incident(c, directory=directory, writer="scripts.heal_incidents")
            done.append(c.incident_id)

    if not args.confirm:
        print("")
        print("[DRY-RUN] 将处理 %d 张（跳过 %d 张）；加 --confirm 才真正执行"
              % (len(done), len(skipped)))
        return 0

    subject = "incidents:" + ",".join(done)[:80]
    seq = _audit("healing.incident." + mode, subject,
                 {"ids": done, "skipped": skipped, "reason": args.reason,
                  "mode": mode, "incidents_dir": str(directory or "")})
    print("")
    print("[OK] %s 完成：%d 张（跳过 %d 张）；审计 seq=%s"
          % (mode, len(done), len(skipped), seq))

    # 自证：重新列一次，确认面板可见集合真的变了（别只信"我调用过 move"）
    after = list_incidents(directory=directory)
    still_open = [c.incident_id for c in after if c.status != STATUS_RESOLVED]
    print("[VERIFY] 目录内剩余 %d 张；未闭合 %d 张%s"
          % (len(after), len(still_open),
             ("（" + ",".join(still_open[:6]) + "）") if still_open else ""))
    if args.archive:
        archived = sorted((incidents_dir(directory) / ARCHIVE_DIRNAME).rglob("inc-*.json"))
        print("[VERIFY] 归档区现有 %d 个 JSON（证据仍在，可追溯）" % len(archived))
    return 0


if __name__ == "__main__":
    sys.exit(main())
