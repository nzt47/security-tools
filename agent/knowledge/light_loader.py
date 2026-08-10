"""知识库轻量检测视图加载器（P0 内存优化 · 独立可复用插件）。

【背景】审计五类检测（孤儿/断链/index 漂移/过期/未裁决矛盾）只需要每张卡
六个字段：slug / status / type / date / links / contradictions。完整 Card
对象还携带 insight、正文 content 等大字段，全量驻留内存是 5~10 倍浪费
（见 docs/reports/knowledge_audit_architecture_optimization_20260811.md §2）。

【插件性】本模块零依赖（仅标准库 + yaml），不 import CardStore / Card：
- 其他项目只需拷贝本文件，即可获得「轻量 frontmatter 扫描」能力；
- 损坏卡（frontmatter/YAML 解析失败）与目录缺失的跳过语义，
  与 CardStore._list_from_disk 完全一致（只慢不坏，绝不误报）。

【不易】
- 只读扫描，不修改任何文件；
- 字段提取与 Card frontmatter 契约一致（yaml 键名不重命名）；
- 排序与 CardStore.list 一致：按类型目录序 + 组内 slug 字典序。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date as _date_type, datetime as _datetime_type
from pathlib import Path
from typing import Optional, Sequence, Union

import yaml

# 默认 wiki 类型目录（AGENTS.md §2）；CardStore 可传入自定义顺序
DEFAULT_TYPE_DIRS: tuple[str, ...] = ("concepts", "entities", "insights")

# 卡片文件 frontmatter：^---\n(<yaml>)\n---\n?(<正文>)
_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.DOTALL)

# 检测所需字段（五类检测 + 断链日志明细全部覆盖）
_LIGHT_FIELDS = ("slug", "status", "type", "date", "links", "contradictions")


@dataclass
class CardLight:
    """检测视图：仅保留审计五类检测所需字段（P0 内存优化）。

    与完整 Card 的区别：不含 insight / content / source / tags / metadata 等
    检测用不到的字段；`links` 保持列表语义（可含 archives/ 前缀目标）。
    """

    slug: str
    status: str
    type: str
    date: str
    links: list
    contradictions: list


def parse_light(md_text: str) -> CardLight:
    """单文件 frontmatter 文本 → CardLight；解析失败抛 ValueError。

    frontmatter 缺失 / YAML 语法错误均抛 ValueError（调用方按损坏卡跳过）。
    """
    m = _FRONTMATTER_RE.match(md_text)
    if not m:
        raise ValueError("frontmatter 解析失败")
    try:
        # 优先 libyaml C 扩展（实测 ~7.6x 提速），无则回退纯 Python SafeLoader
        loader = getattr(yaml, "CSafeLoader", None) or yaml.SafeLoader
        data = yaml.load(m.group(1), Loader=loader) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"frontmatter YAML 解析失败: {exc}") from exc
    # date 规范化：PyYAML 会把无引号 `2026-08-01` 解析为 date 对象；
    # 检测逻辑（date.fromisoformat）要求字符串，统一转 ISO str（与写卡路径一致）
    raw_date = data.get("date", "")
    if isinstance(raw_date, (_date_type, _datetime_type)):
        raw_date = raw_date.isoformat()
    return CardLight(
        slug=data.get("slug", ""),
        status=data.get("status", ""),
        type=data.get("type", ""),
        date=raw_date,
        links=list(data.get("links") or []),
        contradictions=list(data.get("contradictions") or []),
    )


def scan_light_cards(
    wiki_root: Union[str, Path],
    *,
    type_dirs: Sequence[str] = DEFAULT_TYPE_DIRS,
    parallel: bool = False,
    max_workers: Optional[int] = None,
) -> list[CardLight]:
    """全量扫描 wiki 根下各类型目录的卡片轻量视图。

    - 排序：按 type_dirs 顺序 + 组内 slug 字典序（与 CardStore.list 一致）；
    - 损坏卡（frontmatter/YAML 解析失败）跳过，不阻断全库列举；
    - parallel=True 时线程池并发解析（IO 密集提速，结果按提交顺序保序，
      数千张损坏卡并存时顺序与串行完全一致，见 scripts/dev/stress_light_loader_parallel.py）；
    - max_workers=None（默认）时线程数 = min(8, 卡片数)（既有行为不变）；
      显式指定时按给定值（>=1）。基准验证：10000 卡下 1/2/4/8/16 档
      无显著差异（1.03~1.11x），性能拐点不存在于线程数，详见
      docs/reports/light_loader_serial_parallel_bench_20260811.md。

    用法（独立复用）：
        from light_loader import scan_light_cards
        cards = scan_light_cards("path/to/wiki")
        for c in cards:
            print(c.slug, c.links)
    """
    root = Path(wiki_root)
    jobs: list[Path] = []
    for t in type_dirs:
        d = root / t
        if not d.exists():
            continue
        jobs.extend(sorted(d.glob("*.md")))

    if not parallel or len(jobs) <= 1:
        cards: list[CardLight] = []
        for p in jobs:
            try:
                cards.append(parse_light(p.read_text(encoding="utf-8")))
            except (ValueError, TypeError):
                continue  # 损坏卡片跳过，不阻断全库列举
        return cards

    from concurrent.futures import ThreadPoolExecutor

    def _load(p: Path):
        try:
            return parse_light(p.read_text(encoding="utf-8"))
        except (ValueError, TypeError):
            return None

    workers = max(1, int(max_workers)) if max_workers is not None else min(8, max(1, len(jobs)))
    cards = []
    # ex.map 按提交顺序返回（jobs 已排序），保证结果排序与串行一致
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for card in ex.map(_load, jobs):
            if card is not None:
                cards.append(card)
    return cards
