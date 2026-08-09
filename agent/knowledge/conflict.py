"""冲突裁决协议：标记矛盾 → 裁决 → 归档（任务5 · 治理层）。

规则（AGENTS.md §6.2 人机边界）：
- AI 只**标记**矛盾（status=conflict）、建议归档，**不自动裁决**；
  裁决由人触发 `resolve_conflict`（本模块不包含任何自动调用路径）。
- `resolve_conflict` 复用任务2 `transition` 完成被否卡片归档（自动重链）。

矛盾条目结构（Card.contradictions，schema 契约）：
    {"target_slug": str, "status": "conflict" | "resolved", "summary": str}
裁决后追加 `decision_slug`（裁决卡片 slug）关联裁决结论。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from agent.knowledge.card import CardStore, InvalidTransitionError
from agent.knowledge.logbook import append_log

logger = logging.getLogger(__name__)

_DEFAULT_STORE: Optional[CardStore] = None


def _default_store() -> CardStore:
    """惰性构造默认 CardStore（生产环境直接调用时使用；测试须显式传入）。"""
    global _DEFAULT_STORE
    if _DEFAULT_STORE is None:
        _DEFAULT_STORE = CardStore("knowledge/wiki")
    return _DEFAULT_STORE


def _store_of(card_store=None) -> CardStore:
    return card_store if card_store is not None else _default_store()


def mark_conflict(
    source_slug: str,
    target_slug: str,
    summary: str,
    *,
    card_store=None,
) -> bool:
    """在 source 卡片的 contradictions 中添加矛盾条目并持久化。

    - 条目结构：{"target_slug", "status": "conflict", "summary"}。
    - 幂等：同 (source, target) 已登记时返回 False，不重复添加。
    - 源卡片不存在返回 False。
    - 仅标记（status=conflict），**不裁决、不归档**（AI 不自动裁决）。
    """
    store = _store_of(card_store)
    card = store.get(source_slug)
    if card is None:
        logger.warning("mark_conflict: 源卡片不存在 slug=%s", source_slug)
        return False
    for item in card.contradictions or []:
        if item.get("target_slug") == target_slug:
            logger.info(
                "mark_conflict: 该矛盾已登记（幂等跳过）source=%s target=%s",
                source_slug, target_slug,
            )
            return False
    card.contradictions.append({
        "target_slug": target_slug,
        "status": "conflict",
        "summary": summary,
    })
    store.update(card)
    append_log(
        "mark_conflict", source_slug, f"target={target_slug}",
        log_path=store._log_path,
    )
    logger.info(
        "mark_conflict: source=%s target=%s summary=%r", source_slug, target_slug, summary,
    )
    return True


def _resolve_side(card: Any, other_slug: str, decision_slug: str, store) -> None:
    """将卡片 contradictions 中指向 other_slug 的条目置为 resolved 并关联裁决卡。"""
    changed = 0
    prev_status = None
    for item in card.contradictions or []:
        if item.get("target_slug") == other_slug:
            prev_status = item.get("status")
            item["status"] = "resolved"
            item["decision_slug"] = decision_slug
            changed += 1
    if changed:
        store.update(card)
        logger.info(
            "resolve_conflict: 卡片 %s 的 %d 条矛盾已置 resolved "
            "（target=%s decision_slug=%s status_before=%r）",
            card.slug, changed, other_slug, decision_slug, prev_status,
        )
    else:
        logger.info(
            "resolve_conflict: 卡片 %s 无指向 %s 的矛盾条目（跳过更新）",
            card.slug, other_slug,
        )


def resolve_conflict(
    source_slug: str,
    target_slug: str,
    decision_slug: str,
    *,
    card_store=None,
) -> bool:
    """裁决矛盾：双方矛盾状态置为 resolved，关联 decision_slug（裁决卡片）。

    被否卡片（非 decision 一方）状态迁移至 archive（复用任务2 `transition`，
    归档时自动重链）；decision_slug 为第三方时双方均归档。

    顺序设计：先更新矛盾状态（归档后 wiki 读不到），再执行归档；归档失败
    （如 draft → archive 非法迁移）记警告不阻断——矛盾已裁决，仍返回 True。
    """
    store = _store_of(card_store)
    source = store.get(source_slug)
    if source is None:
        logger.warning("resolve_conflict: 源卡片不存在 slug=%s", source_slug)
        return False
    entry = next(
        (it for it in source.contradictions or []
         if it.get("target_slug") == target_slug),
        None,
    )
    if entry is None:
        logger.warning(
            "resolve_conflict: 矛盾不存在 source=%s target=%s",
            source_slug, target_slug,
        )
        return False
    logger.info(
        "resolve_conflict: 开始裁决 source=%s target=%s decision=%s summary=%r",
        source_slug, target_slug, decision_slug, entry.get("summary", ""),
    )

    # 1. 双方 contradictions 置 resolved（须在归档前完成）
    _resolve_side(source, target_slug, decision_slug, store)
    logger.info(
        "resolve_conflict: source 侧矛盾已置 resolved（decision_slug=%s）", decision_slug,
    )
    target = store.get(target_slug)
    if target is not None:
        _resolve_side(target, source_slug, decision_slug, store)
        logger.info(
            "resolve_conflict: target 侧矛盾已置 resolved（decision_slug=%s）", decision_slug,
        )

    # 2. 被否卡片（非 decision 一方）迁移至 archive；第三方裁决时双方均归档
    if decision_slug == source_slug:
        denied = [target_slug]
    elif decision_slug == target_slug:
        denied = [source_slug]
    else:
        denied = [source_slug, target_slug]
    logger.info(
        "resolve_conflict: 被否卡片清单=%s（decision_slug=%s）",
        denied, decision_slug,
    )
    archived = 0
    for slug in denied:
        denied_card = store.get(slug)
        if denied_card is None:
            logger.info("resolve_conflict: 被否卡不存在（跳过归档）slug=%s", slug)
            continue  # 已被归档/不存在，无需再迁
        try:
            store.transition(slug, "archive")
            archived += 1
            logger.info(
                "resolve_conflict: 被否卡已归档 slug=%s type=%s "
                "（wiki/%s/%s.md → archives/%s.md）",
                slug, denied_card.type, denied_card.type, slug, slug,
            )
        except InvalidTransitionError as exc:
            logger.warning(
                "resolve_conflict: 被否卡片归档失败（矛盾已裁决）slug=%s type=%s "
                "当前状态=%s: %s",
                slug, denied_card.type, denied_card.status, exc,
            )
    append_log(
        "resolve_conflict", source_slug,
        f"decision={decision_slug}; target={target_slug}; archived={archived}",
        log_path=store._log_path,
    )
    logger.info(
        "resolve_conflict: 完成 source=%s target=%s decision=%s archived=%d 矛盾已裁决",
        source_slug, target_slug, decision_slug, archived,
    )
    return True


def list_unresolved(card_store) -> list[dict]:
    """列出全部未裁决矛盾 [{source_slug, target_slug, summary}]。

    未裁决 = contradictions 中 status != "resolved"（含 conflict / reviewed）。
    """
    result: list[dict] = []
    for card in card_store.list():
        for item in card.contradictions or []:
            if item.get("status") != "resolved":
                result.append({
                    "source_slug": card.slug,
                    "target_slug": item.get("target_slug", ""),
                    "summary": item.get("summary", ""),
                })
    logger.info("list_unresolved: 未裁决矛盾=%d 条 %s", len(result), result)
    return result
