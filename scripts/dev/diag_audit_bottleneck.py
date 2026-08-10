"""诊断知识库审计瓶颈：list() 读盘 vs 五类检测各阶段耗时（1200 卡）。

用法（仓库根下）：
    $env:PYTHONIOENCODING="utf-8"
    python scripts/dev/diag_audit_bottleneck.py

结论导向：确认耗时大头是 card_store.list() 串行读盘（文件 IO），
还是五类检测（纯内存）——决定并发改造的方向。
"""
from __future__ import annotations

import logging
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.knowledge.card import CardStore  # noqa: E402
from agent.knowledge.index import read_index_slugs  # noqa: E402
from agent.knowledge.links import find_broken_links, find_orphans  # noqa: E402
from agent.knowledge.schema import Card  # noqa: E402

N = 1200
logging.disable()  # 静音 lint 明细日志，只保留本脚本 print


def _build_library(store: CardStore, n: int) -> None:
    today = date.today().isoformat()
    for i in range(n):
        links = [f"card-{i - 1}x"] if i > 0 else []
        links.append(f"ghost-{i}")
        store.create(Card(
            title=f"card-{i}x", slug=f"card-{i}x", status="current",
            type="concepts", source="perf/mock.md", date=today,
            tags=[], links=links, contradictions=[], insight="perf",
        ))


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="kb-diag-") as tmp:
        store = CardStore(Path(tmp) / "wiki")
        _build_library(store, N)

        t = time.perf_counter()
        cards = list(store.list())
        t_list = (time.perf_counter() - t) * 1000
        print(f"list() 读盘(YAML解析): {t_list:8.2f}ms  (占比基准)")

        t = time.perf_counter()
        find_orphans(cards)
        t_o = (time.perf_counter() - t) * 1000

        t = time.perf_counter()
        find_broken_links(cards, store)
        t_b = (time.perf_counter() - t) * 1000

        t = time.perf_counter()
        read_index_slugs(Path(tmp) / "index.md")
        t_i = (time.perf_counter() - t) * 1000

        t = time.perf_counter()
        stale = [c for c in cards if c.status == "current"]
        t_s = (time.perf_counter() - t) * 1000

        total = t_list + t_o + t_b + t_i + t_s
        print(f"find_orphans:              {t_o:8.2f}ms")
        print(f"find_broken_links:         {t_b:8.2f}ms")
        print(f"read_index_slugs:          {t_i:8.2f}ms")
        print(f"stale/conflicts 扫描:      {t_s:8.2f}ms")
        print(f"合计:                      {total:8.2f}ms")
        print(f"list() 占比:               {t_list / total * 100:5.1f}%")

        # 二次 list()（use_cache 场景对照）
        t = time.perf_counter()
        store.list(use_cache=True)
        t_cold = (time.perf_counter() - t) * 1000
        t = time.perf_counter()
        store.list(use_cache=True)
        t_warm = (time.perf_counter() - t) * 1000
        print(f"list(use_cache=True) 冷:   {t_cold:8.2f}ms")
        print(f"list(use_cache=True) 热:   {t_warm:8.2f}ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
