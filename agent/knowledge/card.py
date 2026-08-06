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
import time
from dataclasses import asdict
from pathlib import Path
from typing import Optional

import yaml

from agent.knowledge.index import update_index_delta
from agent.knowledge.lifecycle import CardStatus, can_transition, validate_transition
from agent.knowledge.links import parse_links, rewrite_link_targets
from agent.knowledge.logbook import append_log
from agent.knowledge.schema import Card, validate_card

logger = logging.getLogger(__name__)

# wiki 类型目录固定顺序（AGENTS.md §2）
_TYPE_DIRS = ("concepts", "entities", "insights")


class CardConflictError(Exception):
    """同 slug 卡片已存在（create 不覆盖）"""


class CardNotFoundError(Exception):
    """目标卡片不存在"""


class InvalidTransitionError(Exception):
    """非法状态迁移"""


_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.DOTALL)


def _card_to_md(card: Card) -> str:
    """Card → frontmatter + 正文 Markdown 文本。

    字段顺序与任务0 schema 定义一致；metadata 为空时不写入 frontmatter。
    """
    data = asdict(card)
    content = data.pop("content", "")
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
        data = yaml.safe_load(m.group(1)) or {}
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

    def _validate(self, card: Card) -> None:
        """持久化前校验（schema 契约）；违规抛 ValueError。"""
        errors = validate_card(asdict(card))
        if errors:
            raise ValueError("卡片校验失败: " + "; ".join(errors))

    def _has_incoming_links(self, slug: str) -> bool:
        """是否有其他 wiki 卡片指向该 slug（纯 slug 入链）。"""
        return any(
            slug in card.links for card in self.list() if card.slug != slug
        )

    # ---------- CRUD ----------

    def create(self, card: Card) -> Card:
        """创建卡片；同 slug 已存在时抛 CardConflictError（不覆盖）。"""
        self._check_slug(card.slug)
        self._validate(card)
        if self.get(card.slug) is not None:
            logger.warning("创建冲突: slug=%s 已存在，拒绝覆盖（CardConflictError）", card.slug)
            raise CardConflictError(f"卡片已存在: {card.slug}")
        path = self._wiki_root / card.type / f"{card.slug}.md"
        self._write_card(path, card)
        update_index_delta(card.slug, card, self._index_path)
        append_log("create", card.slug, f"type={card.type}", log_path=self._log_path)
        logger.info("创建卡片: slug=%s type=%s path=%s", card.slug, card.type, path)
        return card

    def get(self, slug: str) -> Optional[Card]:
        """按 slug 读取；不存在返回 None。

        支持 `archives/<slug>` 前缀（解析归档链接）；损坏卡片视为不存在（返回
        None，不抛异常，保证断链容错）。
        """
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
        self._check_slug(card.slug)
        self._validate(card)
        old_path = self._find_path(card.slug)
        if old_path is None:
            raise CardNotFoundError(f"卡片不存在: {card.slug}")
        _t0 = time.perf_counter()
        card.links = parse_links(card.content)
        logger.info(
            "更新卡片: slug=%s 正文双链同步 links=%s 耗时=%.2fms",
            card.slug, card.links, (time.perf_counter() - _t0) * 1000,
        )
        new_path = self._wiki_root / card.type / f"{card.slug}.md"
        if old_path != new_path:
            logger.info("更新卡片: slug=%s type 变更迁移文件 %s → %s", card.slug, old_path, new_path)
            self._write_card(new_path, card)
            old_path.unlink()
        else:
            self._write_card(old_path, card)
        update_index_delta(card.slug, card, self._index_path)
        append_log("update", card.slug, f"type={card.type}", log_path=self._log_path)
        return card

    def delete(self, slug: str) -> bool:
        """删除卡片（校验入链，有入链时拒绝并返回 False）。"""
        self._check_slug(slug)
        if self._has_incoming_links(slug):
            logger.warning("删除被拒: slug=%s 存在入链（引用方需先解除引用）", slug)
            return False
        path = self._find_path(slug)
        if path is None:
            logger.warning("删除未命中: slug=%s 不存在", slug)
            return False
        path.unlink()
        update_index_delta(slug, None, self._index_path)
        append_log("delete", slug, "", log_path=self._log_path)
        logger.info("删除卡片: slug=%s path=%s", slug, path)
        return True

    def list(
        self, status: Optional[str] = None, type: Optional[str] = None
    ) -> list[Card]:
        """列出卡片，可按状态/类型过滤（按 slug 字典序）。"""
        cards: list[Card] = []
        for t in _TYPE_DIRS:
            if type is not None and t != type:
                continue
            d = self._wiki_root / t
            if not d.exists():
                continue
            for p in sorted(d.glob("*.md")):
                try:
                    card = self._md_to_card(p)
                except (ValueError, TypeError):
                    continue  # 跳过损坏卡片，不阻断全库列举
                if status is not None and card.status != status:
                    continue
                cards.append(card)
        return cards

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
