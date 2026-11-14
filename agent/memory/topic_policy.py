# -*- coding: utf-8 -*-
"""按类装载的**读侧策略**：这次检索是谁在做 ⇒ 它能读哪些类。

来源优先级（从高到低，只有这三档，别处不要再各读一次配置）：
1. **显式声明的执行上下文** —— 分身委派的 `memory_scope.topics`（ContextVar，进入委派时设置）；
2. **当前生效的主线档案** —— `LineProfile.topics`（主智能体的默认装载）；
3. 都没有 ⇒ **只有 general**（fail-closed；旧配置/取不到档案时只少读，不多读）。

【为什么必须有这一层】三库的读路径有 5 处（`/api/vector/search`、`search_memory` 工具、
`kb_search` 工具、`/api/knowledge/query`、经验注入）。让每处自己读配置 ⇒ 五处五套语义，
漏一处就是越权；而且"未声明"这一档的语义会各处不同。这里只解析一次。

【审计】声明动作（不是每次读）写一条 `memory.topics.declared`：谁（actor）、以什么身份
（subject）、装载了哪些类、来源是 manifest 还是主线。审计只带**标识**，不带任何记忆正文。
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Iterator, Mapping, Optional, Tuple

from agent.topics import (
    DEFAULT_TOPIC,
    declared_topics,
    normalize_topics,
    topics_context,
)

logger = logging.getLogger(__name__)

__all__ = [
    "AUDIT_TOPICS_DECLARED",
    "audit_declared",
    "topics_for_line",
    "topics_for_manifest",
    "effective_topics",
    "supports_topics",
    "search_with_topics",
    "write_topic",
    "enter_manifest",
    "audit_line_declared",
]

#: 审计事件名（与 subagent.memory.scoped.* 同族，便于一处检索）
AUDIT_TOPICS_DECLARED = "memory.topics.declared"

#: 允许进审计 payload 的附加键（**白名单**，不是黑名单）。
#: 理由：审计链 append-only，一旦落进记忆正文就撤不回来；只放已知的标识键，
#: 调用方误传 content 之类也会被挡下（用例 test_audit_declared_never_carries_content 锁着）。
_AUDIT_EXTRA_KEYS = ("line", "mode", "provider", "delegation_id")


def _split_multi(value: Any):
    """把"多选"归一成列表：支持 list/tuple/set 与逗号分隔串（界面两种都可能发）。"""
    if value is None:
        return None
    if isinstance(value, str):
        return [part for part in (p.strip() for p in value.split(",")) if part]
    if isinstance(value, (list, tuple, set, frozenset)):
        return list(value)
    return None


def topics_for_line(profile: Any) -> Tuple[str, ...]:
    """主线档案 ⇒ 它能读的类（认不到 topics 键 ⇒ 只有 general）"""
    if profile is None:
        return (DEFAULT_TOPIC,)
    return normalize_topics(_split_multi(getattr(profile, "topics", None)))


def _field(config: Any, name: str, default: Any = None) -> Any:
    """从配置对象或 Mapping 读字段（与 memory_broker._config_field 同口径）"""
    if isinstance(config, Mapping):
        return config.get(name, default)
    return getattr(config, name, default)


def topics_for_manifest(config: Any) -> Tuple[str, ...]:
    """分身 manifest ⇒ 它能读的类。

    两级来源，**顶层 `memory_topics` 优先**：
    1. `memory_topics`（与记忆档位无关；none 档也能声明）；
    2. `memory_scope.topics`（C2 的旧形态，保留兼容 —— 存量配置与已导出的 bundle 里是这个）。
    都没有 ⇒ 只读 general（fail-closed）。
    """
    try:
        top = _split_multi(_field(config, "memory_topics", None))
        if top:
            return normalize_topics(top)
        from agent.subagent.memory_broker import resolve_config_memory

        plan = resolve_config_memory(config)
        if not plan.ok:
            logger.warning("[topics] manifest 解析失败（%s）⇒ 只读 %s",
                           plan.error, DEFAULT_TOPIC)
            return (DEFAULT_TOPIC,)
        scope = plan.scope if isinstance(plan.scope, Mapping) else {}
        return normalize_topics(_split_multi(scope.get("topics")))
    except Exception as exc:  # noqa: BLE001 判定自身失败 ⇒ 从严
        logger.warning("[topics] manifest topics 解析异常（%s）⇒ 只读 %s",
                       type(exc).__name__, DEFAULT_TOPIC)
        return (DEFAULT_TOPIC,)


def _active_line_topics() -> Tuple[str, ...]:
    """当前生效主线档案的 topics（读不到 ⇒ general）。

    【为什么不缓存】档案是 YAML 文件（十几行），一次 load 约 0.3ms，相对向量编码/BM25
    可忽略；而缓存会让"改了主线装载哪些类"存在生效延迟 —— 那是要人猜的行为。
    """
    try:
        from agent.lines.registry import LineRegistry

        profile = LineRegistry().effective(None)
        return topics_for_line(profile)
    except Exception as exc:  # noqa: BLE001 读不到就从严，不阻断检索
        logger.warning("[topics] 主线档案不可用（%s）⇒ 只读 %s",
                       type(exc).__name__, DEFAULT_TOPIC)
        return (DEFAULT_TOPIC,)


def effective_topics() -> Tuple[str, ...]:
    """本次检索生效的白名单（声明 > 主线 > general）。**永不抛**：检索不能被策略拖挂。"""
    declared = declared_topics()
    if declared is not None:
        return declared
    try:
        return _active_line_topics()
    except Exception as exc:  # noqa: BLE001 兜底（_active_line_topics 内部已兜一层）
        logger.warning("[topics] 生效白名单解析失败（%s）⇒ 只读 %s",
                       type(exc).__name__, DEFAULT_TOPIC)
        return (DEFAULT_TOPIC,)


def supports_topics(store: Any) -> bool:
    """该检索对象是否接受 `topics=` 形参（鸭子类型兼容用）。

    【为什么要判】VectorStore 是唯一实现，但测试与插件里有"只有
    search(query, top_k)"的老式桩。直接传 topics 会 TypeError；而用
    try/except TypeError 兜底会把**真实现内部的 TypeError 一起吞掉** ——
    那会让过滤失效变成静默行为。故按签名判，判不出就当"不支持"（退回旧调用）。
    """
    fn = getattr(store, "search", None)
    if fn is None:
        return False
    try:
        import inspect

        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    if "topics" in params:
        return True
    return any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


def search_with_topics(store: Any, query: str, *, top_k: int, topics: Any = None):
    """统一的向量检索入口：白名单取自策略（或显式传入），返回 (结果, 生效白名单)。

    所有读路径都走这里，避免"某处忘了传 topics"（忘了就退回全库 = 越权）。
    """
    resolved = effective_topics() if topics is None else normalize_topics(topics)
    if supports_topics(store):
        return store.search(query, top_k=top_k, topics=resolved), resolved
    logger.warning("[topics] 检索对象不支持 topics 形参（%s）⇒ 本次不过滤，"
                   "路径未接线请补", type(store).__name__)
    return store.search(query, top_k), resolved


def write_topic() -> str:
    """写入侧**归因**打标：取写入者声明的**首要类**（顺序第一个非 general），没有就 general。

    【语义是"谁产生的"，不是"内容属于哪类"】按内容猜类就是编 —— 一条关于 OOM 的反思
    属于运维还是工程？没人能替它决定。能确定的是"这条记忆是工程线写的"。所以：
    · 主线/分身声明了非 general 类 ⇒ 用它声明顺序里的第一个（声明顺序即优先级）；
    · 没声明 ⇒ general（未分类，仍然 fail-closed：只有 general 白名单的智能体能读到它）。

    【为什么按声明顺序而不是按"线 id 映射"】映射表是第二份词表，迟早漂移；
    声明顺序是配置者自己写的意图，读起来也直观（engineering 线第一个非 general 是 engineering）。
    """
    for topic in effective_topics():
        if topic != DEFAULT_TOPIC:
            return topic
    return DEFAULT_TOPIC


def audit_declared(topics: Tuple[str, ...], *, actor: str = "", subject: str = "",
                   source: str = "", extra: Optional[Mapping[str, Any]] = None) -> bool:
    """记一条"谁装载了哪些类"（best-effort：审计失败不阻断检索）"""
    payload = {
        "topics": list(topics),
        "source": str(source or "unknown"),
    }
    if extra:
        for key in _AUDIT_EXTRA_KEYS:
            if key not in extra:
                continue
            value = extra.get(key)
            if isinstance(value, (str, int, float, bool)) or value is None:
                payload[key] = value
    try:
        from agent.audit.facade import record

        record(AUDIT_TOPICS_DECLARED, actor=(actor or None),
               subject=str(subject or ""), payload=payload)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[topics] 审计写入失败（不影响检索）: %s", type(exc).__name__)
        return False


@contextmanager
def enter_manifest(config: Any, *, audit: Any = None, actor: str = "",
                   subject: str = "") -> Iterator[Tuple[str, ...]]:
    """进入一次委派执行：把 manifest 声明的类写进上下文（并发安全）。

    【审计只在"非默认"时写】与 scoped 审计同款口径（`subagent.memory.scoped.enabled`
    也只在显式开启时写）：默认档每次委派都写一条，等于把审计链变成噪声源，
    而噪声会让人不再看告警。所以：声明了 general 以外的类才留痕。
    """
    resolved = topics_for_manifest(config)
    with topics_context(resolved) as active:
        if active != (DEFAULT_TOPIC,):
            audit_declared(active, actor=actor, subject=subject, source="manifest")
        yield active


def audit_line_declared(profile: Any, *, actor: str = "", line_id: str = "") -> bool:
    """主线档案保存后记一条"这条智能体装载了哪些类"（**配置变更**时才写，不是每次读）"""
    topics = topics_for_line(profile)
    if topics == (DEFAULT_TOPIC,):
        return False
    return audit_declared(topics, actor=actor,
                          subject="agent_line:%s" % (line_id or getattr(profile, "id", "")),
                          source="line.save",
                          extra={"line": line_id or getattr(profile, "id", "")})
