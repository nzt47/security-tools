"""知识卡片 Schema 与校验器（任务0 · 契约层）。

本模块是知识卡片结构的唯一事实源：类型/状态命名必须与
`knowledge/AGENTS.md`、`agent/knowledge/lifecycle.py` 保持一致（单一事实源）。

约束（不易）：
- `validate_card` 绝不抛异常，只返回违规项字符串列表。
- `slugify` 幂等：`slugify(slugify(x)) == slugify(x)`。

来源说明（重建）:
    2026-08-05 依据 .pyc 反汇编提取的结构/常量/逻辑重建，语义与字节码一致。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Optional

# 主题类词表与「记忆/知识/经验」三库同源（叶子模块，无依赖）：
# 卡片这里是**第二个**写侧入口（另两个是向量 VectorStore.add 与经验 samples），
# 三处共用同一份闭集，别在各自门口再定义一次。
from agent.topics import DEFAULT_TOPIC, TOPICS as TOPIC_VOCABULARY

REQUIRED_FIELDS = ["title", "slug", "status", "type", "source", "date"]

VALID_TYPES = {"concepts", "insights", "entities"}
VALID_STATUS = {"current", "archive", "draft", "unknown"}
VALID_CONTRADICTION_STATUS = {"reviewed", "conflict", "resolved"}


@dataclass
class Card:
    """知识卡片结构（frontmatter 字段的唯一事实源）"""

    title: str
    slug: str
    status: str
    type: str
    source: str
    date: str
    tags: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    #: 主题类（业务域）。默认 general —— 旧卡片/未声明一律归它（fail-closed）。
    #: 单选：一条记录只属于一个类，否则「A 检索不到 B 的类」这句验收就没法验证。
    topic: str = DEFAULT_TOPIC
    contradictions: list[dict] = field(default_factory=list)
    insight: str = ""
    scope: str = ""
    content: str = ""
    metadata: dict = field(default_factory=dict)
    # 显式 slug 豁免：slug 被消歧（如 distill 同题笔记 -N 后缀）时置 True，
    # 跳过 validate_card 的「slug == slugify(title)」一致性检查。
    # 仅内存标记，落盘时由 _card_to_md pop，不写入 frontmatter。
    explicit_slug: bool = False


def validate_card(card: dict) -> list[str]:
    """校验卡片 dict，返回违规项字符串列表（绝不抛异常）"""
    errors: list[str] = []
    if not isinstance(card, dict):
        return ["card 必须是 dict"]

    # 1. 必填字段（缺失或空值）
    for name in REQUIRED_FIELDS:
        if card.get(name) in (None, ""):
            errors.append(f"缺少必填字段: {name}")

    # 2. type 合法性
    if "type" in card and card["type"] not in VALID_TYPES:
        errors.append(f"非法 type: {card['type']!r}（允许: "
                      f"{', '.join(sorted(VALID_TYPES))}）")

    # 3. status 合法性
    if "status" in card and card["status"] not in VALID_STATUS:
        errors.append(f"非法 status: {card['status']!r}（允许: "
                      f"{', '.join(sorted(VALID_STATUS))}）")

    # 4. slug 与 slugify(title) 一致性（显式 slug 豁免）
    if not card.get("explicit_slug"):
        title = card.get("title")
        slug = card.get("slug")
        if title and slug and slug != slugify(title):
            errors.append(f"slug 与 slugify(title) 不一致: {slug!r} != "
                          f"{slugify(title)!r}")

    # 5. contradictions 结构校验
    contradictions = card.get("contradictions") or []
    for i, item in enumerate(contradictions):
        if not isinstance(item, dict):
            errors.append(f"contradictions[{i}] 必须是 dict")
            continue
        if not item.get("target_slug"):
            errors.append(f"contradictions[{i}] 缺少 target_slug")
        if item.get("status") not in VALID_CONTRADICTION_STATUS:
            errors.append(f"contradictions[{i}] status 非法: "
                          f"{item.get('status')!r}")

    # 6. 核心洞见必填
    if not card.get("insight"):
        errors.append("缺少一句话核心洞见")

    # 7. topic 合法（空 = 未声明，允许；由 Card 默认值落成 general）
    topic = card.get("topic")
    if topic not in (None, "") and str(topic).strip().lower() not in TOPIC_VOCABULARY:
        errors.append(f"非法 topic: {topic!r}（允许: "
                      f"{', '.join(TOPIC_VOCABULARY)}）")

    return errors


def slugify(title: str) -> str:
    """标题 → 文件名规范（全小写、连字符、去歧义后缀）。

    规则：
    - NFKC 规范化（全角/异体字 → 半角）。
    - 仅保留小写拉丁字母、数字与中日韩文字，其余转为单个连字符。
    - 去除首尾连字符、合并连续连字符。
    - 循环去除尾部「去歧义后缀」（``-<数字>``，如 ``-2``、``-2-3``），
      保证幂等：``slugify(slugify(x)) == slugify(x)``。

    Why 去歧义后缀（不易）：任务2 用 ``-<数字>`` 后缀消解重名 slug；
    本函数必须能"消解后仍幂等"，故循环剥除尾部数字段，且纯函数不依赖
    已有 slug 注册表。
    """
    if not isinstance(title, str) or not title.strip():
        return ""

    s = unicodedata.normalize("NFKC", title).lower()
    s = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", s)
    s = s.strip("-")
    s = re.sub(r"-{2,}", "-", s)

    # 循环去除尾部「去歧义后缀」，保证幂等
    while True:
        stripped = re.sub(r"-\d+$", "", s)
        if stripped == s:
            return s
        s = stripped
