"""知识卡片模型与持久化（任务2 · 核心引擎）。

存储布局（AGENTS.md）：
    knowledge/wiki/<type>/<slug>.md   — 成品卡片（YAML frontmatter + 正文）
    knowledge/archives/<slug>.md      — Archive 态卡片（物理归档）
    knowledge/index.md                — 全局索引（全量/增量维护）
    knowledge/log.md                  — 操作日志（顶部追加）

【不易】
- 纯文件系统存储，不依赖 memory / VectorStore（零耦合）。
- 同 slug 创建冲突抛 CardConflictError，绝不静默覆盖。
- 状态迁移唯一事实源为 lifecycle.TRANSITIONS；非法迁移抛 InvalidTransitionError。
- `update` 时以正文双链解析结果同步 `links` 字段（双向链接一致性）。
- 每次写操作登记 log.md（AGENTS.md §4），并增量维护 index.md（AGENTS.md §3）。
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from agent.knowledge.index import update_index_delta
from agent.knowledge.lifecycle import CardStatus, can_transition, validate_transition
from agent.knowledge.light_loader import CardLight, scan_light_cards
from agent.knowledge.links import ARCHIVES_PREFIX, parse_links, rewrite_link_targets
from agent.knowledge.links_index import read_links_index, update_links_delta
from agent.knowledge.logbook import append_log
from agent.knowledge.schema import Card, validate_card

logger = logging.getLogger(__name__)

# wiki 类型目录固定顺序（AGENTS.md §2）
_TYPE_DIRS = ("concepts", "entities", "insights")


class _RWLock:
    """写者优先读写锁（CardStore 用）：多读者并发、写者独占、写者可重入读。

    Why 需要（并发风险排查结论）：create/update/delete 是多步写操作
    （写卡 + 增量 index + 追加 log），写写并发会交错破坏 index/log 一致性；
    读（get/list 读文件）与写并发虽已被 _atomic_write + 容错兜底，仍加
    「读锁 vs 写锁」互斥，让写操作整体串行且不被读打断。

    - 写者等待所有在读读者完成；写者持有期间新读者等待（写者优先，防写饿死）。
    - 持有写锁的同一线程可重入获取读锁（如 delete→_has_incoming_links→list、
      create→冲突检查 get），避免同线程自锁死锁。
    """

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._readers = 0
        self._writer = False
        self._writer_tid: Optional[int] = None

    def acquire_read(self) -> None:
        me = threading.get_ident()
        with self._cond:
            while self._writer and self._writer_tid != me:
                self._cond.wait()
            self._readers += 1

    def release_read(self) -> None:
        with self._cond:
            self._readers -= 1
            if self._readers == 0:
                self._cond.notify_all()

    def acquire_write(self) -> None:
        me = threading.get_ident()
        with self._cond:
            while self._writer or self._readers > 0:
                self._cond.wait()
            self._writer = True
            self._writer_tid = me

    def release_write(self) -> None:
        with self._cond:
            self._writer = False
            self._writer_tid = None
            self._cond.notify_all()

    @contextmanager
    def read(self):
        self.acquire_read()
        try:
            yield
        finally:
            self.release_read()

    @contextmanager
    def write(self):
        self.acquire_write()
        try:
            yield
        finally:
            self.release_write()


class CardConflictError(Exception):
    """同 slug 卡片已存在（create 不覆盖）"""


class CardNotFoundError(Exception):
    """目标卡片不存在"""


class InvalidTransitionError(Exception):
    """非法状态迁移"""


@dataclass
class BatchImportResult:
    """批量导入结果汇总（CLI 打印与测试断言用）。"""

    imported: int = 0
    skipped: int = 0
    failed: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)


_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.DOTALL)


def _card_to_md(card: Card) -> str:
    """Card → frontmatter + 正文 Markdown 文本。

    字段顺序与任务0 schema 定义一致；metadata 为空时不写入 frontmatter。
    """
    data = asdict(card)
    content = data.pop("content", "")
    data.pop("explicit_slug", None)  # 仅内存豁免标记，不写入 frontmatter
    if not data.get("metadata"):
        data.pop("metadata", None)
    frontmatter = yaml.safe_dump(
        data, allow_unicode=True, sort_keys=False, default_flow_style=None
    ).strip()
    return f"---\n{frontmatter}\n---\n\n{content.rstrip()}\n"


def _md_to_card(path: Path, text: str) -> Card:
    """Markdown 文本 → Card（frontmatter 解析 + 正文还原）。"""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise ValueError(f"frontmatter 解析失败: {path}")
    try:
        # 优先 libyaml C 扩展（实测 ~7.6x 提速：1200 卡 718ms→94ms），
        # 无 C 扩展环境回退纯 Python SafeLoader（语义完全一致）。
        _loader = getattr(yaml, "CSafeLoader", None) or yaml.SafeLoader
        data = yaml.load(m.group(1), Loader=_loader) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"frontmatter YAML 解析失败: {path}: {exc}") from exc
    data.pop("content", None)
    fields = Card.__dataclass_fields__
    card = Card(**{k: v for k, v in data.items() if k in fields})
    card.content = m.group(2).strip("\n")
    return card


class CardStore:
    """卡片文件持久化：knowledge/wiki/<type>/<slug>.md（YAML frontmatter + 正文）"""

    def __init__(
        self,
        wiki_root: str | Path = "knowledge/wiki",
        *,
        archives_dir: Optional[str | Path] = None,
        index_path: Optional[str | Path] = None,
        log_path: Optional[str | Path] = None,
        links_index_path: Optional[str | Path] = None,
    ):
        """构造 CardStore。

        默认布局（AGENTS.md）：archives/index/log 均位于 wiki_root 的父目录
        （knowledge/）下；测试或自定义布局可通过关键字参数覆盖。
        """
        self._wiki_root = Path(wiki_root)
        root = self._wiki_root.parent
        self._archives_dir = (
            Path(archives_dir) if archives_dir else root / "archives"
        )
        self._index_path = Path(index_path) if index_path else root / "index.md"
        self._log_path = Path(log_path) if log_path else root / "log.md"
        self._links_index_path = (
            Path(links_index_path) if links_index_path else root / "index_links.md"
        )
        self._rwlock = _RWLock()  # 读写锁：写串行化（index/log 一致性），读并发
        # 内存缓存（list(use_cache=True) 时启用）：指纹 = 文件系统快照
        self._list_cache: Optional[list[Card]] = None
        self._list_fingerprint: Optional[tuple] = None

    # ---------- 内部工具 ----------

    @staticmethod
    def _check_slug(slug: str) -> None:
        """校验 slug 可用作安全文件名（防路径穿越）。"""
        if not isinstance(slug, str) or not slug:
            raise ValueError("slug 不能为空")
        if "/" in slug or "\\" in slug or slug in (".", ".."):
            raise ValueError(f"非法 slug: {slug!r}")

    def _find_path(self, slug: str) -> Optional[Path]:
        """按 slug 定位卡片文件（wiki 类型目录 / archives 目录）。

        支持 `archives/<slug>` 前缀目标（resolve_link 解析归档链接用）。
        """
        if slug.startswith("archives/"):
            rest = slug[len("archives/"):]
            if not rest or "/" in rest or "\\" in rest or rest in (".", ".."):
                return None
            p = self._archives_dir / f"{rest}.md"
            return p if p.exists() else None
        if "/" in slug or "\\" in slug or slug in (".", ".."):
            return None
        for t in _TYPE_DIRS:
            p = self._wiki_root / t / f"{slug}.md"
            if p.exists():
                return p
        return None

    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        """同目录临时文件 + os.replace，保证原子性（防并发半写）。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)

    def _write_card(self, path: Path, card: Card) -> None:
        self._atomic_write(path, _card_to_md(card))

    def _md_to_card(self, path: Path) -> Card:
        return _md_to_card(path, path.read_text(encoding="utf-8"))

    def _exists(self, slug: str) -> bool:
        """slug 对应的卡片文件是否存在（纯文件检查，不解析 YAML）。

        P1-3 lazy 解析：create/import 判重路径只关心「文件已存在」这一结论，
        无需把 frontmatter 完整解析为 Card（解析留给真正需要字段的读路径）。
        【不易】写入保护契约「同 slug 已存在，绝不静默覆盖」以文件存在为界，
        损坏文件同样视为已占用，拒绝 create 覆盖。
        """
        return self._find_path(slug) is not None

    def _validate(self, card: Card) -> None:
        """持久化前校验（schema 契约）；违规抛 ValueError。"""
        errors = validate_card(asdict(card))
        if errors:
            raise ValueError("卡片校验失败: " + "; ".join(errors))

    def _has_incoming_links(self, slug: str) -> bool:
        """是否有其他 wiki 卡片指向该 slug（纯 slug 入链）。

        P0-2 入链索引优化：优先查 index_links.md（O(1) 查表）；
        索引文件缺失/解析失败时回退全库扫描（容错，保证行为不退化）。
        """
        return bool(self._incoming_sources(slug))

    def _incoming_sources(self, slug: str) -> list[str]:
        """返回指向该 slug 的引用方 slug 列表（入链明细，供删除日志排查）。

        索引读取失败/缺失时回退全库扫描，行为与 _has_incoming_links 一致。
        """
        path = self._links_index_path
        if path.exists():
            try:
                return list(read_links_index(path).get(slug, []))
            except (ValueError, OSError) as exc:
                logger.warning("入链索引解析失败，回退全库扫描: %s (%s)", path, exc)
        return [
            card.slug for card in self.list()
            if card.slug != slug and slug in card.links
        ]

    def _sync_links_index_add(self, card: Card) -> None:
        """create/update 后：将卡片 links 的引用关系登记入入链索引。

        容错降级：入链索引是可重建的辅助文件，写失败（OSError/ValueError）
        仅告警不阻断卡片主操作（与 _has_incoming_links 降级风格一致）。
        """
        try:
            for link in card.links:
                if not link.startswith(ARCHIVES_PREFIX):
                    update_links_delta(link, card.slug, self._links_index_path, add=True)
        except (ValueError, OSError) as exc:
            logger.warning(
                "入链索引登记失败（已降级，卡片操作继续）: %s (%s)",
                self._links_index_path, exc,
            )

    def _sync_links_index_remove(self, slug: str, links: list[str]) -> None:
        """delete 后：移除被删卡片 links 产生的引用关系（入链已校验为空）。

        容错降级：同上，索引写失败仅告警不阻断卡片主操作。
        """
        try:
            for link in links:
                if not link.startswith(ARCHIVES_PREFIX):
                    update_links_delta(link, slug, self._links_index_path, add=False)
        except (ValueError, OSError) as exc:
            logger.warning(
                "入链索引移除失败（已降级，卡片操作继续）: %s (%s)",
                self._links_index_path, exc,
            )

    # ---------- CRUD ----------

    def create(self, card: Card) -> Card:
        """创建卡片；同 slug 已存在时抛 CardConflictError（不覆盖）。

        正文双链为链接事实源：links 显式传入时优先保留（兼容既有调用方），
        为空则从正文解析（与 update 语义对齐，保证入链索引完整登记）。
        """
        with self._rwlock.write():  # 写串行化：写卡 + index + log 三步原子可见
            self._check_slug(card.slug)
            self._validate(card)
            logger.debug(
                "create: slug=%s type=%s 校验通过 显式links=%s",
                card.slug, card.type, card.links,
            )
            if self._exists(card.slug):  # 写锁内重入读（同线程放行）；仅文件存在性，免 YAML 解析
                logger.warning("创建冲突: slug=%s 已存在，拒绝覆盖（CardConflictError）", card.slug)
                raise CardConflictError(f"卡片已存在: {card.slug}")
            path = self._wiki_root / card.type / f"{card.slug}.md"
            # 入链追踪：正文双链为链接事实源（显式传入优先，为空则从正文解析）
            links_source = "显式传入" if card.links else "正文双链解析"
            if not card.links:
                card.links = parse_links(card.content)
            logger.debug(
                "create[链接解析]: slug=%s 来源=%s 解析前links=%s 解析后links=%s",
                card.slug, links_source, [], card.links,
            )
            logger.info(
                "创建卡片[入链追踪]: slug=%s type=%s links来源=%s links=%s 正文长度=%d 双链解析=%s",
                card.slug, card.type, links_source, card.links,
                len(card.content), parse_links(card.content),
            )
            self._write_card(path, card)
            update_index_delta(card.slug, card, self._index_path)
            append_log("create", card.slug, f"type={card.type}", log_path=self._log_path)
            self._sync_links_index_add(card)  # 入链索引同步（P0-2）
            self._sync_list_cache(
                added=card,
                added_fp=self._fp_entry(card.type, card.slug, path),
            )  # 内存缓存增量同步：写后查询不再全量重载
            logger.debug(
                "create[入链登记]: slug=%s 每个引用目标逐项登记 add=True index=%s",
                card.slug, self._links_index_path,
            )
            logger.info(
                "创建卡片[入链追踪]: slug=%s 入链索引登记完成 引用目标=%s index=%s",
                card.slug,
                [l for l in card.links if not l.startswith(ARCHIVES_PREFIX)],
                self._links_index_path,
            )
            logger.info("创建卡片: slug=%s type=%s path=%s", card.slug, card.type, path)
            return card

    def get(self, slug: str) -> Optional[Card]:
        """按 slug 读取；不存在返回 None。

        支持 `archives/<slug>` 前缀（解析归档链接）；损坏卡片视为不存在（返回
        None，不抛异常，保证断链容错）。
        """
        with self._rwlock.read():  # 与写锁互斥：读不打断多步写
            path = self._find_path(slug)
            if path is None:
                return None
            try:
                return self._md_to_card(path)
            except (ValueError, TypeError):
                logger.debug(
                    "get: slug=%r 卡片文件解析失败视为不存在 path=%s", slug, path,
                )
                return None

    def update(self, card: Card) -> Card:
        """更新卡片（按 slug 定位，frontmatter + 正文原子写）。

        【不易】以正文双链解析结果同步 `links` 字段（双向链接一致性）。
        若 type 变更，文件迁移到新类型目录（旧文件删除）。
        """
        with self._rwlock.write():  # 写串行化：迁移 + 写卡 + index + log 原子可见
            self._check_slug(card.slug)
            self._validate(card)
            old_path = self._find_path(card.slug)
            if old_path is None:
                raise CardNotFoundError(f"卡片不存在: {card.slug}")
            _t0 = time.perf_counter()
            # 入链索引同步：先移除旧引用（读旧卡 links；损坏卡容错为空）
            try:
                old_links = self._md_to_card(old_path).links
            except (ValueError, TypeError):
                old_links = []
            try:
                for link in old_links:
                    if not link.startswith(ARCHIVES_PREFIX):
                        update_links_delta(link, card.slug, self._links_index_path, add=False)
            except (ValueError, OSError) as exc:
                logger.warning(
                    "入链索引移除旧引用失败（已降级，卡片操作继续）: %s (%s)",
                    self._links_index_path, exc,
                )
            card.links = parse_links(card.content)
            logger.info(
                "更新卡片: slug=%s 正文双链同步 links=%s 耗时=%.2fms",
                card.slug, card.links, (time.perf_counter() - _t0) * 1000,
            )
            new_path = self._wiki_root / card.type / f"{card.slug}.md"
            old_fp = self._fp_entry(old_path.parent.name, card.slug, old_path)  # unlink 前记录旧指纹
            if old_path != new_path:
                logger.info("更新卡片: slug=%s type 变更迁移文件 %s → %s", card.slug, old_path, new_path)
                self._write_card(new_path, card)
                old_path.unlink()
            else:
                self._write_card(old_path, card)
            update_index_delta(card.slug, card, self._index_path)
            append_log("update", card.slug, f"type={card.type}", log_path=self._log_path)
            self._sync_links_index_add(card)  # 入链索引同步：登记新引用（P0-2）
            self._sync_list_cache(
                added=card,
                removed=card.slug,
                added_fp=self._fp_entry(card.type, card.slug, new_path),
                removed_fp=old_fp,
            )  # 内存缓存增量同步：写后查询不再全量重载
            return card

    def delete(self, slug: str) -> bool:
        """删除卡片（校验入链，有入链时拒绝并返回 False）。"""
        with self._rwlock.write():  # 写串行化：入链检查 + 删除 + index + log 原子可见
            self._check_slug(slug)
            # 入链检查：优先索引查表，缺失/失败回退全库扫描
            incoming = self._incoming_sources(slug)
            logger.info(
                "删除卡片[入链追踪]: slug=%s 入链检查 引用方=%s index=%s",
                slug, incoming, self._links_index_path,
            )
            if incoming:  # 写锁内重入读（list）
                logger.warning(
                    "删除被拒[入链追踪]: slug=%s 存在入链 引用方=%s（引用方需先解除引用）",
                    slug, incoming,
                )
                return False
            path = self._find_path(slug)
            if path is None:
                logger.warning("删除未命中: slug=%s 不存在", slug)
                return False
            # 入链索引同步：读被删卡 links（损坏卡容错为空，仍可删除）
            try:
                old_links = self._md_to_card(path).links
            except (ValueError, TypeError):
                logger.debug(
                    "delete: slug=%s 卡片解析失败 links 容错为空 path=%s", slug, path,
                )
                old_links = []
            logger.debug(
                "delete: slug=%s 读被删卡成功 path=%s links=%s",
                slug, path, old_links,
            )
            logger.info(
                "删除卡片[入链追踪]: slug=%s 入链检查通过(无引用方) path=%s 被删卡links=%s",
                slug, path, old_links,
            )
            del_fp = self._fp_entry(path.parent.name, slug, path)  # unlink 前记录指纹
            path.unlink()
            update_index_delta(slug, None, self._index_path)
            append_log("delete", slug, "", log_path=self._log_path)
            self._sync_links_index_remove(slug, old_links)  # 入链索引同步（P0-2）
            logger.debug(
                "delete[入链移除]: slug=%s 每个引用目标逐项移除 add=False index=%s",
                slug, self._links_index_path,
            )
            logger.info(
                "删除卡片[入链追踪]: slug=%s 入链索引移除完成 引用目标=%s index=%s",
                slug,
                [l for l in old_links if not l.startswith(ARCHIVES_PREFIX)],
                self._links_index_path,
            )
            logger.info("删除卡片: slug=%s path=%s", slug, path)
            self._sync_list_cache(
                removed=slug, removed_fp=del_fp,
            )  # 内存缓存增量同步：写后查询不再全量重载
            return True

    def delete_many(self, slugs: list[str]) -> dict[str, bool]:
        """批量删除；返回 {slug: 是否删除成功}（P1-1 批量删除优化）。

        【不易】入链判定语义与单删 delete 一致：待删集合**外**的引用方仍指向
        slug → 拒绝；待删集合内部的互相引用不阻止删除（整批同时消失，
        不产生残留断链）。复杂度：优先一次解析入链索引 O(M)（缺失/损坏时
        回退全库扫描 O(N)）构建入链映射 + O(K) 逐张删除（原逐次 delete
        为 O(K·N)，且索引存在时逐次 delete 每次 O(M) 解析索引）。
        """
        with self._rwlock.write():  # 写串行化：一次扫描 + K 次删除原子可见
            # 1. 构建「被引用 slug → 引用方列表」（archives/ 前缀不列入链）。
            #    优先复用入链索引（读一次 O(M)），缺失/损坏时回退全库扫描
            #    （降级铁律：行为不退化，与 _has_incoming_links 一致）。
            incoming: Optional[dict[str, list[str]]] = None
            if self._links_index_path.exists():
                try:
                    incoming = read_links_index(self._links_index_path)
                except (ValueError, OSError) as exc:
                    logger.warning("入链索引解析失败，回退全库扫描: %s (%s)", self._links_index_path, exc)
            if incoming is None:
                incoming = {}
                for card in self.list():
                    for link in card.links:
                        if not link.startswith(ARCHIVES_PREFIX):
                            incoming.setdefault(link, []).append(card.slug)
            pending = set(slugs)
            result: dict[str, bool] = {}
            for slug in slugs:
                self._check_slug(slug)
                refs = [r for r in incoming.get(slug, []) if r not in pending]
                if refs:
                    logger.warning(
                        "批量删除被拒: slug=%s 存在外部入链=%s", slug, refs,
                    )
                    result[slug] = False
                    continue
                path = self._find_path(slug)
                if path is None:
                    logger.warning("批量删除未命中: slug=%s 不存在", slug)
                    result[slug] = False
                    continue
                try:
                    old_links = self._md_to_card(path).links
                except (ValueError, TypeError):
                    old_links = []
                path.unlink()
                update_index_delta(slug, None, self._index_path)
                append_log("delete", slug, "batch", log_path=self._log_path)
                self._sync_links_index_remove(slug, old_links)  # 入链索引同步（P0-2）
                logger.info("批量删除卡片: slug=%s path=%s", slug, path)
                result[slug] = True
            # 批量删除后一次性失效缓存：逐张增量同步为 O(K·N)，批量场景失效更优
            if any(result.values()):
                self._invalidate_list_cache()
            return result

    def _fingerprint(self) -> tuple:
        """文件系统指纹：{(目录, 文件名, mtime_ns)} 排序元组。

        用于 list(use_cache=True) 的缓存失效判定：文件增删改（mtime 变化）
        都会改变指纹 → 自动重载。指纹仅含文件名与 mtime（不读内容），
        10 万卡量级扫描成本约秒级，远低于全量 YAML 解析（数分钟）。
        目录缺失时跳过（与 list() 跳过不存在目录的语义一致，指纹仍可用）。
        """
        entries = []
        for t in _TYPE_DIRS:
            d = self._wiki_root / t
            if not d.is_dir():
                continue
            with os.scandir(d) as it:
                for e in it:
                    if e.name.endswith(".md"):
                        entries.append((t, e.name, e.stat().st_mtime_ns))
        return tuple(sorted(entries))

    def _list_from_disk(self, parallel: bool = False) -> list[Card]:
        """全量读盘：所有类型目录下的可解析卡片（按 slug 字典序）。

        parallel=True 时用线程池并发读文件（IO 密集，read_text 释放 GIL，
        多线程可显著提速；结果按提交顺序返回，与串行语义/排序完全一致）。
        损坏卡片跳过逻辑与串行路径一致（不阻断全库列举）。
        """
        jobs: list[tuple[str, Path]] = []  # (type_dir, path)，已按目录+文件名排序
        for t in _TYPE_DIRS:
            d = self._wiki_root / t
            if not d.exists():
                continue
            for p in sorted(d.glob("*.md")):
                jobs.append((t, p))
        if not parallel or len(jobs) <= 1:
            cards: list[Card] = []
            for _t, p in jobs:
                try:
                    cards.append(self._md_to_card(p))
                except (ValueError, TypeError):
                    continue  # 跳过损坏卡片，不阻断全库列举
            return cards

        from concurrent.futures import ThreadPoolExecutor

        def _load(item: tuple[str, Path]):
            _t, p = item
            try:
                return (_t, p.name, _md_to_card(p, p.read_text(encoding="utf-8")))
            except (ValueError, TypeError):
                return (_t, p.name, None)

        cards = []
        # ex.map 按提交顺序（jobs 已排序）返回，保证结果排序与串行一致
        with ThreadPoolExecutor(max_workers=min(8, max(1, len(jobs)))) as ex:
            for _t, _name, card in ex.map(_load, jobs):
                if card is not None:
                    cards.append(card)
        return cards

    # ---------- 内存缓存失效/同步 ----------

    @staticmethod
    def _fp_entry(type_dir: str, slug: str, path: Path) -> Optional[tuple]:
        """写操作后的单文件指纹条目 (type_dir, filename, mtime_ns)。

        path 须为 stat 前的文件路径（update/delete 在 unlink 前调用）。
        文件缺失返回 None（调用方忽略；指纹不一致时下次命中会全量比较
        发现 → 自动重载，安全回退）。
        """
        try:
            return (type_dir, f"{slug}.md", path.stat().st_mtime_ns)
        except OSError:
            return None

    def _invalidate_list_cache(self) -> None:
        """写路径缓存失效（批量操作用：delete_many / import_from_dir）。

        置空缓存与指纹，下次 list(use_cache=True) 自动全量重载。
        批量场景逐张增量同步为 O(K·N)，整体失效一次更优。
        """
        self._list_cache = None
        self._list_fingerprint = None

    def _sync_list_cache(
        self,
        *,
        added: Optional[Card] = None,
        removed: Optional[str] = None,
        added_fp: Optional[tuple] = None,
        removed_fp: Optional[tuple] = None,
    ) -> None:
        """写操作后增量同步内存缓存与指纹（缓存已加载时）。

        缓存未加载（_list_cache is None）直接返回：下次 list(use_cache=True)
        自动全量加载，无需同步。

        【不易】安全边界：缓存内容与指纹在同一函数内同步，保证二者一致。
        任一遗漏导致的指纹不一致，都会在下次 list 命中时被全量指纹比较
        发现 → 自动全量重载（只慢不坏，绝不返回陈旧数据）。
        """
        if self._list_cache is None:
            return
        if removed is not None:
            self._list_cache = [
                c for c in self._list_cache if c.slug != removed
            ]
        if added is not None:
            self._list_cache = [
                c for c in self._list_cache if c.slug != added.slug
            ]
            self._list_cache.append(added)
            # 与 _list_from_disk 顺序一致：按类型目录序 + 组内 slug 字典序
            self._list_cache.sort(
                key=lambda c: (_TYPE_DIRS.index(c.type), c.slug)
            )
        if added_fp is not None or removed_fp is not None:
            fp = set(self._list_fingerprint or ())
            if removed_fp is not None:
                fp.discard(removed_fp)
            if added_fp is not None:
                fp.add(added_fp)
            self._list_fingerprint = tuple(sorted(fp))

    def list(
        self,
        status: Optional[str] = None,
        type: Optional[str] = None,
        *,
        use_cache: bool = False,
        parallel: bool = False,
    ) -> list[Card]:
        """列出卡片，可按状态/类型过滤（按 slug 字典序）。

        parallel=True 时全量读盘改用线程池并发（IO 密集提速，默认关闭
        保持既有行为；结果排序/损坏跳过语义与串行完全一致）。
        use_cache=True 时启用内存缓存（性能优化）：
        - 首次调用全量读盘并缓存；指纹（文件名+mtime 快照）未变时直接
          返回缓存副本（跳过 YAML 解析——10 万卡场景耗时从分钟级降到秒级）。
        - 指纹在文件增删改时自动失效并重载，无需手动失效。
        - 【边界】仅依赖 mtime 检测变化：人为还原 mtime 的文件可能漏检
          （极端场景，可接受）；缓存仅在本次进程内有效。
        - 默认 False 保持原语义（每次实时读盘），不改变既有调用行为。
        """
        if use_cache:
            if self._list_cache is not None:
                fp = self._fingerprint()
                if fp == self._list_fingerprint:
                    cards = [
                        c
                        for c in self._list_cache
                        if (status is None or c.status == status)
                        and (type is None or c.type == type)
                    ]
                    logger.info(
                        "list: 缓存命中(use_cache) 指纹未变 返回卡片=%d（过滤 status=%s type=%s）",
                        len(cards), status, type,
                    )
                    return cards
            else:
                # 缓存未加载（首次调用 或 批量写后失效）：跳过指纹扫描直接重载，
                # 加载后重建指纹基线（10 万卡场景省一次全量 scandir+stat）
                fp = None
            with self._rwlock.read():
                cards = self._list_from_disk(parallel=parallel)
            self._list_cache = cards
            if fp is None:
                fp = self._fingerprint()  # 重建指纹基线
            self._list_fingerprint = fp
            if status is not None or type is not None:
                cards = [
                    c
                    for c in cards
                    if (status is None or c.status == status)
                    and (type is None or c.type == type)
                ]
            return cards
        with self._rwlock.read():  # 与写锁互斥：全库列举不被多步写打断
            cards = self._list_from_disk(parallel=parallel)
            if status is not None or type is not None:
                cards = [
                    c
                    for c in cards
                    if (status is None or c.status == status)
                    and (type is None or c.type == type)
                ]
            return cards

    def list_light(self, *, parallel: bool = False) -> list[CardLight]:
        """轻量检测视图（P0 内存优化）：只解析检测六字段，丢弃正文/insight。

        语义与 list() 一致：按类型目录序 + slug 字典序，损坏卡跳过；
        复用独立插件 light_loader（零依赖，可拷贝到其他项目复用）。
        parallel=True 时线程池并发解析（IO 密集提速，结果保序）。
        检测类调用方（lint_all）应优先使用本方法，避免完整 Card 驻留内存。
        """
        with self._rwlock.read():  # 与写锁互斥：全库列举不被多步写打断
            return scan_light_cards(
                self._wiki_root, type_dirs=_TYPE_DIRS, parallel=parallel,
            )

    # ---------- 批量导入 ----------

    def import_from_dir(
        self, src_dir: str | Path, *, force: bool = False
    ) -> BatchImportResult:
        """批量导入目录下全部 `*.md` 卡片文件（YAML frontmatter）。

        【不易】同 slug 冲突默认跳过不覆盖（create 契约）；force=True 时改走
        update（含正文双链同步）。单卡失败仅记录计数，不中断批次。
        文件处理顺序按文件名排序，保证结果可复现。
        """
        src = Path(src_dir)
        if not src.is_dir():
            raise ValueError(f"批量导入目录不存在: {src}")
        _t0 = time.perf_counter()
        result = BatchImportResult()
        for p in sorted(src.glob("*.md")):
            try:
                card = self._md_to_card(p)
                if self._exists(card.slug):  # 判重仅需存在性（免 YAML 解析，P1-3）
                    if not force:
                        result.skipped += 1
                        logger.info("批量导入跳过: %s（同 slug 已存在，force=False）", p.name)
                        continue
                    self.update(card)
                    result.imported += 1
                    continue
                self.create(card)
                result.imported += 1
            except (ValueError, TypeError, CardConflictError) as exc:
                result.failed += 1
                result.failures.append((p.name, str(exc)))
                logger.warning("批量导入失败: %s: %s", p.name, exc)
        if result.imported:
            self._invalidate_list_cache()  # 批量导入后一次性失效缓存（避免逐张 O(K·N) 同步）
        logger.info(
            "批量导入完成: 导入=%d 跳过=%d 失败=%d 耗时=%.2fms",
            result.imported, result.skipped, result.failed,
            (time.perf_counter() - _t0) * 1000,
        )
        return result

    def export_dir(
        self,
        dst_dir: str | Path,
        *,
        status: Optional[str] = None,
        type: Optional[str] = None,
    ) -> int:
        """导出卡片为 frontmatter md 到目录（可再被 import_from_dir 回读）。

        【不易】复用 _card_to_md（单一事实源），文件名 `<slug>.md` 与
        import_from_dir 的 `*.md` 扫描兼容（round-trip 可逆）。
        """
        dst = Path(dst_dir)
        dst.mkdir(parents=True, exist_ok=True)
        n = 0
        for card in self.list(status=status, type=type):
            self._atomic_write(dst / f"{card.slug}.md", _card_to_md(card))
            n += 1
        logger.info("导出卡片: %d 张 → %s", n, dst)
        return n

    # ---------- 生命周期状态机 ----------

    def transition(self, slug: str, to_status: str) -> Card:
        """状态迁移：仅更新 frontmatter 的 status 字段，不移动文件。

        例外：to_status == 'archive' 时物理移入 knowledge/archives/，并重写
        全部指向该卡片的入链引用（更新相关卡片的 links 与正文双链）。
        迁移合法性以 lifecycle.TRANSITIONS 为唯一事实源，非法迁移抛
        InvalidTransitionError。
        """
        _t0 = time.perf_counter()
        self._check_slug(slug)
        path = self._find_path(slug)
        if path is None:
            raise CardNotFoundError(f"卡片不存在: {slug}")
        try:
            card = self._md_to_card(path)
        except (ValueError, TypeError) as exc:
            raise CardNotFoundError(f"卡片读取失败: {slug}") from exc

        try:
            target = CardStatus(to_status)
        except ValueError:
            logger.warning("迁移被拒: slug=%s 非法目标状态 to_status=%r", slug, to_status)
            raise InvalidTransitionError(f"非法目标状态: {to_status!r}") from None
        try:
            current = CardStatus(card.status)
        except ValueError:
            logger.warning("迁移被拒: slug=%s 卡片当前状态非法 status=%r", slug, card.status)
            raise InvalidTransitionError(
                f"卡片当前状态非法: {card.status!r}"
            ) from None

        if not can_transition(current, target):
            reason = validate_transition(current, target) or (
                f"非法迁移: {current.value} → {target.value}"
            )
            logger.warning("迁移被拒: slug=%s %s", slug, reason)
            raise InvalidTransitionError(reason)

        if target is CardStatus.ARCHIVE:
            return self._archive(card, path)
        card.status = target.value
        self._write_card(path, card)
        update_index_delta(card.slug, card, self._index_path)
        append_log(
            "transition", slug, f"{current.value} → {target.value}",
            log_path=self._log_path,
        )
        logger.info(
            "状态迁移: slug=%s %s → %s（文件路径不变）耗时=%.2fms",
            slug, current.value, target.value,
            (time.perf_counter() - _t0) * 1000,
        )
        return card

    def _archive(self, card: Card, path: Path) -> Card:
        """归档：物理移入 archives/ + 重写入链（links + 正文双链）。"""
        _t0 = time.perf_counter()
        old_status = card.status
        card.status = CardStatus.ARCHIVE.value
        self._archives_dir.mkdir(parents=True, exist_ok=True)
        archive_path = self._archives_dir / f"{card.slug}.md"
        self._write_card(archive_path, card)
        path.unlink()
        logger.info("归档卡片: slug=%s %s → archive，移入 %s", card.slug, old_status, archive_path)
        rewritten = self._rewrite_incoming_links(card.slug, card.title)
        logger.info(
            "归档重链: slug=%s 共改写 %d 张引用卡 耗时=%.2fms",
            card.slug, rewritten, (time.perf_counter() - _t0) * 1000,
        )
        update_index_delta(card.slug, None, self._index_path)
        append_log(
            "transition", card.slug, f"{old_status} → archive",
            log_path=self._log_path,
        )
        return card

    def _rewrite_incoming_links(self, slug: str, title: str) -> int:
        """将全部指向 slug 的 wiki 卡片入链改写为 archives/<slug>，返回改写数。

        规则（AGENTS.md §3.1）：links 字段 `slug` → `archives/<slug>`；
        正文 `[[slug]]` → `[[archives/<slug>|title]]`、`[[slug|别名]]` →
        `[[archives/<slug>|别名]]`（保留原别名）。
        """
        rewritten = 0
        new_target = f"archives/{slug}"
        for card in self.list():
            if card.slug == slug or slug not in card.links:
                continue
            logger.info("归档重链: 引用卡 slug=%s 改写 links=%s", card.slug, card.links)
            card.links = [
                new_target if link == slug else link for link in card.links
            ]
            card.content = rewrite_link_targets(
                card.content, slug, new_target, default_alias=title
            )
            self._write_card(
                self._wiki_root / card.type / f"{card.slug}.md", card
            )
            append_log(
                "update", card.slug, f"归档重链: {slug}",
                log_path=self._log_path,
            )
            rewritten += 1
        return rewritten
