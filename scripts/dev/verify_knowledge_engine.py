"""任务2 知识卡片引擎本地验证脚本（mock 数据 + 断言）。

用法（Windows PowerShell）：
    $env:PYTHONIOENCODING="utf-8"
    python scripts/dev/verify_knowledge_engine.py

验证项（每项均带断言，全部通过打印 PASS，任一失败退出码非 0）：
  1. parse_links 双链解析（[[目标]] / [[目标|别名]] / archives 前缀 / 去重保序）
  2. update 正文双链同步 links 字段
  3. resolve_link 命中与断链返回 None
  4. find_broken_links 断链检出
  5. find_orphans 孤儿检测（archives 前缀不算入链）
  6. index.md 增量更新（update_index_delta 叠加）与全量重建（rebuild_index）结果一致
  7. delete 入链保护（有入链拒绝 / 无入链成功 + index 移除条目）
  8. transition archive 归档重链（links + 正文无死链 + index 移除条目）
  9. 同 slug 创建冲突抛 CardConflictError
 10. type 变更后 index 条目迁移 section（增量叠加 == 全量重建）
 11. 状态变更（draft→current→archive）+ 双向链接互引重链全流程

说明：全部在临时目录中构造 mock 数据，不污染真实 knowledge/ 目录；
日志级别 INFO，可观察 card/links/index 各模块核心分支的 logger 打印。
"""

from __future__ import annotations

import logging
import sys
import tempfile
from pathlib import Path

# 使脚本可在任意 cwd 下运行：把项目根加入 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.knowledge.card import (  # noqa: E402
    CardConflictError,
    CardStore,
    InvalidTransitionError,
)
from agent.knowledge.index import rebuild_index  # noqa: E402
from agent.knowledge.links import (  # noqa: E402
    find_broken_links,
    find_orphans,
    parse_links,
    resolve_link,
)
from agent.knowledge.schema import Card, slugify  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s [%(name)s] %(message)s",
)

_passed = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    """断言检查：失败立即退出非 0。"""
    global _passed
    if cond:
        _passed += 1
        print(f"  [PASS] {name} {extra}")
    else:
        print(f"  [FAIL] {name} {extra}")
        raise SystemExit(1)


def make_card(
    title: str,
    status: str = "current",
    type: str = "concepts",
    content: str = "",
    links=None,
    insight: str = "一句话核心洞见",
) -> Card:
    card = Card(
        title=title,
        slug=slugify(title),
        status=status,
        type=type,
        source="mock/inbox.md",
        date="2026-08-06",
        tags=[],
        links=links if links is not None else [],
        insight=insight,
    )
    card.content = content
    return card


def _strip_time(text: str) -> str:
    """剔除时间戳行，用于增量/全量 index 内容比对。"""
    return "\n".join(
        line for line in text.splitlines()
        if not line.startswith("> 此文件由 AI 自动维护")
    )


def main() -> int:
    global _passed
    with tempfile.TemporaryDirectory(prefix="kb_verify_") as tmp:
        root = Path(tmp)
        store = CardStore(root / "wiki")
        index_path = root / "index.md"
        print(f"=== 临时知识库: {root} ===")

        # ---------- 1. parse_links ----------
        print("\n--- 1. parse_links 双链解析 ---")
        text = "参考 [[提示词工程|工程]] 与 [[第一性原理]]，另见 [[驾驭工程]]。"
        got = parse_links(text)
        check("两种语法（无别名/带别名）提取 slug",
              got == ["提示词工程", "第一性原理", "驾驭工程"], f"got={got}")
        check("去重保序", parse_links("[[A]] [[B]] [[A]] [[B|别名]]") == ["A", "B"])
        check("archives 前缀目标", parse_links("[[archives/旧主题|旧主题]]") == ["archives/旧主题"])
        check("无双链返回空", parse_links("普通文本") == [])

        # ---------- 2. 创建卡片（双链网络/孤儿/断链场景） ----------
        print("\n--- 2. 创建卡片 ---")
        store.create(make_card(
            "驾驭工程",
            content="依赖 [[提示词工程]] 与 [[第一性原理]]",
            links=["提示词工程", "第一性原理"],
        ))
        store.create(make_card(
            "提示词工程", status="draft",
            content="关联 [[第一性原理]]", links=["第一性原理"],
        ))
        store.create(make_card(
            "第一性原理", type="insights",
            content="与 [[驾驭工程]] 互相引用", links=["驾驭工程"],
        ))
        store.create(make_card("复杂系统", type="insights", content="孤立页面（无入链）"))
        store.create(make_card(
            "幽灵引用", content="引用不存在的 [[未创建卡片]]", links=["未创建卡片"],
        ))
        check("5 张卡片全部入库", len(store.list()) == 5)

        # ---------- 3. update 正文双链同步 links ----------
        print("\n--- 3. update 同步 links ---")
        card = store.get("复杂系统")
        card.content = "依赖 [[提示词工程]] 体系"
        store.update(card)
        check("正文双链同步 links", store.get("复杂系统").links == ["提示词工程"],
              f"links={store.get('复杂系统').links}")

        # ---------- 4. resolve_link / find_broken_links ----------
        print("\n--- 4. 断链检测 ---")
        check("resolve_link 命中", resolve_link("驾驭工程", store) is not None)
        check("resolve_link 断链返回 None", resolve_link("未创建卡片", store) is None)
        broken = find_broken_links(store.list(), store)
        check("find_broken_links 检出 1 条断链",
              broken == [{"from_slug": "幽灵引用", "to_slug": "未创建卡片"}], f"got={broken}")

        # ---------- 5. find_orphans ----------
        print("\n--- 5. 孤儿检测 ---")
        orphans = find_orphans(store.list())
        check("孤儿 = 无入链卡片（复杂系统/幽灵引用）",
              set(orphans) == {"复杂系统", "幽灵引用"}, f"got={orphans}")

        # ---------- 6. index 增量 vs 全量重建 ----------
        print("\n--- 6. index.md 增量更新一致性 ---")
        delta_text = _strip_time(index_path.read_text(encoding="utf-8"))
        print("增量叠加后的 index.md：\n" + index_path.read_text(encoding="utf-8"))
        count = rebuild_index(root / "wiki", index_path)
        check("rebuild_index 返回卡片数", count == 5, f"count={count}")
        rebuilt = _strip_time(index_path.read_text(encoding="utf-8"))
        check("增量叠加 == 全量重建（逐字节一致）", rebuilt == delta_text)

        # ---------- 7. delete 入链保护 ----------
        print("\n--- 7. delete 入链保护 ---")
        check("有入链拒绝删除（提示词工程被驾驭工程引用）",
              store.delete("提示词工程") is False)
        check("无入链删除成功（复杂系统）", store.delete("复杂系统") is True)
        check("删除后 index 移除条目",
              "- [[复杂系统]]" not in index_path.read_text(encoding="utf-8"))

        # ---------- 8. transition archive 归档重链 ----------
        print("\n--- 8. archive 归档重链 ---")
        store.transition("驾驭工程", "archive")
        first = store.get("第一性原理")
        check("入链 links 改写为 archives 路径",
              first.links == ["archives/驾驭工程"], f"links={first.links}")
        check("正文双链改写（无别名 → 卡片 title 作别名）",
              "[[archives/驾驭工程|驾驭工程]]" in first.content,
              f"content={first.content!r}")
        check("归档后无死链", find_broken_links([first], store) == [])
        check("归档后 wiki 无该文件",
              not (root / "wiki" / "concepts" / "驾驭工程.md").exists())
        check("归档后 index 移除条目",
              "- [[驾驭工程]]" not in index_path.read_text(encoding="utf-8"))

        # ---------- 9. 同 slug 创建冲突 ----------
        print("\n--- 9. 同 slug 创建冲突 ---")
        try:
            store.create(make_card("第一性原理", type="insights"))
            check("同 slug 冲突抛 CardConflictError", False)
        except CardConflictError:
            check("同 slug 冲突抛 CardConflictError", True)

        # ---------- 最终孤儿复查 ----------
        # 复杂系统（提示词工程唯一引用方）删除后，提示词工程失去全部入链，
        # 孤儿集合 = {幽灵引用（原孤儿）, 提示词工程（新孤儿）}
        final_orphans = find_orphans(store.list())
        check("删除/归档后孤儿复查",
              set(final_orphans) == {"幽灵引用", "提示词工程"}, f"got={final_orphans}")

        # ---------- 10. type 变更后 index 增量 == 全量 ----------
        print("\n--- 10. type 变更 index 一致性 ---")
        card = store.get("幽灵引用")
        card.type = "insights"
        store.update(card)
        delta2 = _strip_time(index_path.read_text(encoding="utf-8"))
        rebuild_index(root / "wiki", index_path)
        rebuilt2 = _strip_time(index_path.read_text(encoding="utf-8"))
        check("type 变更后 增量叠加 == 全量重建（逐字节一致）", rebuilt2 == delta2)
        concepts_part = delta2.split("## 实体 (Entities)")[0]
        check("旧 section 无残留、新 section 含条目",
              "幽灵引用" not in concepts_part
              and "- [[幽灵引用]]" in delta2.split("## 实体 (Entities)")[1],
              "幽灵引用 应迁移到 Insights")

        # ---------- 11. 状态变更 + 双向链接全流程 ----------
        print("\n--- 11. 状态变更 + 双向链接全流程 ---")
        # 双向互引卡片对 A ↔ B（经 update 同步 links）
        store.create(make_card("方法甲", type="insights",
                               content="引用 [[方法乙]]"))
        store.create(make_card("方法乙", type="insights",
                               content="回引 [[方法甲]]"))
        a = store.get("方法甲")
        b = store.get("方法乙")
        store.update(a)
        store.update(b)
        check("双向链接互引解析",
              a.links == ["方法乙"] and b.links == ["方法甲"],
              f"A.links={a.links} B.links={b.links}")
        # 状态链：draft → current（合法迁移，index 状态角标同步）
        store.create(make_card("草稿卡", status="draft"))
        store.transition("草稿卡", "current")
        check("draft → current 合法迁移",
              store.get("草稿卡").status == "current")
        check("迁移后 index 状态角标更新",
              "- [[草稿卡]] `current`" in index_path.read_text(encoding="utf-8"))
        # 非法迁移：draft → archive 直跳被拒（须先经 current）
        store.create(make_card("直跳卡", status="draft"))
        try:
            store.transition("直跳卡", "archive")
            check("draft → archive 直跳被拒（须先经 current）", False)
        except InvalidTransitionError:
            check("draft → archive 直跳被拒（须先经 current）", True)
        # current → archive：双向链接重链 + 无死链 + index 移除条目
        store.transition("方法甲", "archive")
        b = store.get("方法乙")
        check("归档后引用卡重链 links",
              b.links == ["archives/方法甲"], f"links={b.links}")
        check("归档后正文双链重写",
              "[[archives/方法甲|方法甲]]" in b.content)
        check("归档后无断链", find_broken_links([b], store) == [])
        check("归档后 index 移除条目",
              "- [[方法甲]]" not in index_path.read_text(encoding="utf-8"))
        # 完整生命周期单卡：draft → current → archive（终态校验）
        store.create(make_card("生命卡", status="draft", content="完整生命周期演示"))
        store.transition("生命卡", "current")
        store.transition("生命卡", "archive")
        arch = store.get("archives/生命卡")
        check("完整链 draft→current→archive 归档终态",
              arch is not None and arch.status == "archive")
        check("归档后 wiki 无文件 / index 无条目",
              not (root / "wiki" / "concepts" / "生命卡.md").exists()
              and "- [[生命卡]]" not in index_path.read_text(encoding="utf-8"))
        # 终态校验：archive 拒绝一切再迁移（lifecycle.TRANSITIONS 唯一事实源）
        from agent.knowledge.lifecycle import CardStatus, can_transition
        check("archive 终态拒绝任何再迁移",
              all(not can_transition(CardStatus.ARCHIVE, s)
                  for s in (CardStatus.DRAFT, CardStatus.CURRENT,
                            CardStatus.UNKNOWN, CardStatus.ARCHIVE)))

    print(f"\n全部通过：{_passed} 项断言 PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
