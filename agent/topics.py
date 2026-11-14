# -*- coding: utf-8 -*-
"""记忆 / 知识 / 经验的主题类（topic）——词表与读侧白名单的唯一事实源。

【为什么要有这一维】三库此前都**没有业务类**：
    · 向量记忆 metadata.type     = conversation / reflection / user_memory / document  → 「形态」
    · 知识卡片 type              = concepts / insights / entities                     → 「形态」
    · 经验 task_type             = feature / bugfix / refactor / config / test / infra → 「形态」
    · agent/memory/taxonomy.py 的 MemoryType = working/fact/preference/strategy        → 「私人域分层」
    · agent/retention/manifest.py 的 class_id                                          → 「数据留存类」
四者都不是「这条知识属于哪个业务领域」。本模块只定义这一维，且**只定义一次**
（三库同名同值，否则「某个智能体装载了哪些类」这个问题永远答不清）。

【口径（写死，改动要同批改用例）】
1. **闭集 5 值**，默认 `general`。旧数据与未声明一律 general（fail-closed）。
2. 每条记录**一个** topic（单选）。多标签会让「一条记录同时属于两类」的可见性
   变成矩阵问题，而验收只要求「A 检索不到 B 的类」这一句能说清。
3. 词表外的值：**写侧报错**（不静默改成 general —— 那等于替使用者表态，
   写进去之后没人知道它原本想写什么）；**读侧白名单丢弃并告警**
   （配置 typo 只应更严，不应放宽）。
4. ContextVar 在子线程 / 子进程**不会自动继承**（与
   agent/capregistry/identity.py 的既有结论同源）⇒ 取不到上下文就走
   general（fail-closed 方向：丢上下文只会**少读**，不会多读）。

【为什么放在 agent/ 根上而不是某个域里】三个域（向量记忆 memory/vector_store、
知识 agent/knowledge、经验 agent/skills_mgmt）都要用它：放进任一域都会让另外两个
反向依赖那个域，且那两个域的 __init__ 都不轻。本模块只 import 标准库，是纯叶子。
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_TOPIC",
    "TOPICS",
    "TopicError",
    "normalize_topic",
    "normalize_topics",
    "is_topic_allowed",
    "topic_of_metadata",
    "declares_topic",
    "current_topics",
    "declared_topics",
    "topics_context",
    "filter_by_topic",
]

#: 兜底类：旧数据、未声明、取不到上下文，全部归它（fail-closed 的落点）
DEFAULT_TOPIC = "general"

#: 闭集（顺序 = 展示顺序；扩这一类必须同批改本元组 + 迁移报告 + 用例）
TOPICS: Tuple[str, ...] = (
    "general",      # 兜底：未分类 / 跨域 / 旧数据
    "engineering",  # 代码、构建、测试、发布、仓库
    "knowledge",    # 知识沉淀、蒸馏、卡片、流程固化
    "ops",          # 运维、调度、部署、监控、告警
    "product",      # 需求、方案、产品决策
)

#: 单条 topic 允许的最大长度（超长直接判非法，别让它混进 metadata 撑爆存储）
TOPIC_MAX_LEN = 32


class TopicError(ValueError):
    """topic 非法（写侧据此 400 / 抛错，不静默纠正）"""


def normalize_topic(value: Any, *, strict: bool = True) -> str:
    """归一化单个 topic。

    Args:
        value: 待归一化的值（None / 空串 ⇒ DEFAULT_TOPIC）。
        strict: True（写侧）词表外**抛 TopicError**；False（读侧）词表外
            回落 DEFAULT_TOPIC 并告警。

    Raises:
        TopicError: strict=True 且值不在树表内 / 形态非法。
    """
    if value is None:
        return DEFAULT_TOPIC
    if not isinstance(value, str):
        if strict:
            raise TopicError("topic 必须是字符串，收到 %r" % (type(value).__name__,))
        logger.warning("[topics] 非字符串 topic 回落 %s: %r", DEFAULT_TOPIC, type(value).__name__)
        return DEFAULT_TOPIC
    raw = value.strip().lower()
    if not raw:
        return DEFAULT_TOPIC
    if len(raw) > TOPIC_MAX_LEN:
        if strict:
            raise TopicError("topic 长度超过 %d: %d" % (TOPIC_MAX_LEN, len(raw)))
        logger.warning("[topics] 超长 topic 回落 %s（长度 %d）", DEFAULT_TOPIC, len(raw))
        return DEFAULT_TOPIC
    if raw in TOPICS:
        return raw
    if strict:
        raise TopicError(
            "未知 topic=%r（合法值：%s）" % (value, ", ".join(TOPICS)))
    logger.warning("[topics] 未知 topic=%r 回落 %s（读侧白名单不信配置 typo）", value, DEFAULT_TOPIC)
    return DEFAULT_TOPIC


def normalize_topics(values: Any) -> Tuple[str, ...]:
    """归一化读侧白名单（保序去重；**空 ⇒ 只有 general**）。

    这是所有「按类装载」判定的入口：调用方**不得**自己拼白名单，
    否则「未声明」这一档的语义会各处不同。

    词表外的值被丢弃（并告警）而不是让整条白名单失效 —— 但丢弃的后果是
    「更少可见」，不是「更多可见」，符合 fail-closed。
    """
    if values is None:
        return (DEFAULT_TOPIC,)
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, Iterable):
        logger.warning("[topics] 白名单形态非法（%s）⇒ 只给 %s", type(values).__name__, DEFAULT_TOPIC)
        return (DEFAULT_TOPIC,)
    out: List[str] = []
    for raw in values:
        # 【关键：未知值**丢弃**，不能回落 general】回落等于把 typo 变成"额外放行
        # 了 general 这一类" —— 白名单只会因为写错而**变宽**，那正是 fail-open。
        if not isinstance(raw, str):
            logger.warning("[topics] 白名单含非字符串项（%s）已丢弃", type(raw).__name__)
            continue
        item = raw.strip().lower()
        if not item:
            continue
        if item not in TOPICS:
            logger.warning("[topics] 白名单含未知 topic=%r 已丢弃（合法值：%s）",
                           raw, ", ".join(TOPICS))
            continue
        if item not in out:
            out.append(item)
    if not out:
        return (DEFAULT_TOPIC,)
    return tuple(out)


def is_topic_allowed(topic: Any, whitelist: Sequence[str]) -> bool:
    """单条记录的 topic 是否在白名单内（记录侧缺省 = general）"""
    return normalize_topic(topic, strict=False) in set(whitelist)


def topic_of_metadata(metadata: Any) -> str:
    """从 metadata 取 topic（**宽容**：缺省/非法一律 general，绝不抛）"""
    if not isinstance(metadata, Mapping):
        return DEFAULT_TOPIC
    return normalize_topic(metadata.get("topic"), strict=False)


def declares_topic(metadata: Any) -> bool:
    """该记录是否**显式声明**过 topic（报表/迁移用：区分"真的是 general"与"没写"）"""
    return isinstance(metadata, Mapping) and bool(str(metadata.get("topic") or "").strip())


def filter_by_topic(
    rows: Iterable[Any],
    whitelist: Sequence[str],
    *,
    getter: Optional[Callable[[Any], Any]] = None,
) -> Tuple[List[Any], int]:
    """按 topic 过滤一组条目，返回 (保留, 丢弃数)。

    Args:
        rows: 待过滤条目（任意对象）。
        whitelist: 归一化后的白名单（调用方应先经 normalize_topics）。
        getter: 从条目取 metadata / topic 的函数；缺省按
            `row.metadata` → `row.get("metadata")` → `row.get("topic")` 三种形态猜。

    缺省 getter 的三种形态覆盖了三库真实数据：向量 `MemoryItem.metadata`、
    知识 `Card`（自行传 getter）、经验 dict meta。
    """
    allowed = set(whitelist or (DEFAULT_TOPIC,))
    kept: List[Any] = []
    dropped = 0
    for row in rows or ():
        if getter is not None:
            topic = normalize_topic(getter(row), strict=False)
        else:
            topic = topic_of_metadata(_default_metadata(row))
        if topic in allowed:
            kept.append(row)
        else:
            dropped += 1
    return kept, dropped


def _default_metadata(row: Any) -> Any:
    meta = getattr(row, "metadata", None)
    if isinstance(meta, Mapping):
        return meta
    if isinstance(row, Mapping):
        meta = row.get("metadata")
        if isinstance(meta, Mapping):
            return meta
        return row  # 经验侧：meta 本身就是 dict（含 _topic / topic）
    return None


# ── 执行上下文（谁在检索） ───────────────────────────────────
#
# 读侧白名单有两个来源：
#   ① 主智能体 = 当前生效的主线档案（LineProfile.topics）；
#   ② 分身     = 委派 manifest 的 memory_scope.topics。
# 两者都在"开始执行"时写进 ContextVar，检索点只问"现在是谁在检索"，
# 不各自去读配置 —— 只在**一处**解析，才不会出现两套语义。

_declared_var: ContextVar[Optional[Tuple[str, ...]]] = ContextVar(
    "yunshu_memory_topics", default=None)


def declared_topics() -> Optional[Tuple[str, ...]]:
    """当前执行上下文**显式**声明的白名单；None = 未声明（≠ 空）"""
    return _declared_var.get()


def current_topics() -> Tuple[str, ...]:
    """当前执行上下文生效的白名单（未声明 ⇒ 只有 general，fail-closed）"""
    declared = _declared_var.get()
    if declared is None:
        return (DEFAULT_TOPIC,)
    return declared


@contextmanager
def topics_context(topics: Any) -> Iterator[Tuple[str, ...]]:
    """在 with 块内声明白名单（并发安全：每线程/每任务各自一份副本）"""
    resolved = normalize_topics(topics)
    token = _declared_var.set(resolved)
    try:
        yield resolved
    finally:
        _declared_var.reset(token)