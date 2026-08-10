"""知识库最小闭环端到端演示（任务7 · Step 6）。

用法（仓库根目录下）：
    $env:PYTHONIOENCODING="utf-8"
    python scripts/knowledge_demo.py

演示 capture→distill→discuss→card→audit 五步闭环：
    素材   : data/demo_knowledge/ 下 3 篇示例文章（原样只读，入库为复制）
    LLM    : 内置 DemoLLM（按系统提示词返回预置 JSON），离线可跑通闭环
    知识根 : 临时目录（运行结束自动清理），不污染真实 knowledge/
    产物   : 《知识库健康报告》写入 data/demo_knowledge/知识库健康报告.md

人机边界演示（AGENTS.md §6）：
    - 产卡结果状态恒为 draft，须人工 transition → current 才生效（AI 不自动升级）。
    - 讨论发现的矛盾只标记 [冲突: <slug>]（contradictions=conflict），不自动裁决。
"""

from __future__ import annotations

import json
import logging
import re
import sys
import tempfile
from pathlib import Path

# 使脚本可在任意 cwd 下运行：把仓库根加入 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.knowledge.card import CardStore  # noqa: E402
from agent.knowledge.distill import approve_note  # noqa: E402
from agent.knowledge.lint import report_to_json  # noqa: E402
from agent.knowledge.workflow import WorkflowRunner  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s [%(name)s] %(message)s",
)

# 演示素材（文件名 → 来源类型；按名字显式引用，避免误收同目录健康报告）
MATERIALS: list[tuple[str, str]] = [
    ("第一性原理笔记法.md", "article"),
    ("卡片盒写作法.md", "article"),
    ("知识降噪设计.md", "article"),
]

# 讨论对象：只对「卡片盒写作法」发起深度讨论，其余两条走「笔记直产卡」路径
DISCUSS_NOTE = "卡片盒写作法"
DISCUSS_QUESTION = "卡片盒的链接越多越好吗？链接本身会不会变成噪音？"

HEALTH_REPORT_NAME = "知识库健康报告.md"


class DemoLLM:
    """演示用 LLM：按系统提示词路由返回预置响应（离线可复现，duck-typing）。

    契约（与 agent/knowledge/prompts.py 对齐）：
        chat(messages, system_prompt=...) -> str
    """

    model = "demo-llm"

    def __init__(self, distill: dict, discussion: str, extract: dict):
        self._distill = distill      # 标题 → 提炼 JSON
        self._discussion = discussion  # 讨论记录正文
        self._extract = extract      # 讨论 → 字段提炼 JSON

    def chat(self, messages, system_prompt: str = "") -> str:
        content = messages[0]["content"] if messages else ""
        if "知识提炼者" in system_prompt:
            m = re.search(r"素材标题:\s*([^\n]+)", content)
            title = m.group(1).strip() if m else ""
            return json.dumps(self._distill[title], ensure_ascii=False)
        if "知识讨论者" in system_prompt:
            return self._discussion
        if "知识卡片编辑" in system_prompt:
            return json.dumps(self._extract, ensure_ascii=False)
        raise RuntimeError(f"未知系统提示词: {system_prompt!r}")


def _distill_data() -> dict:
    """3 篇素材的预置提炼 JSON（suggested_links 互相引用，保证闭环无孤儿）。"""
    return {
        "第一性原理笔记法": {
            "core_points": [
                "结论必须能追溯推导过程，否则只是记忆而非知识",
                "区分事实、假设、结论三层，标注在笔记里",
                "结论与事实矛盾时以事实为准重新推导",
            ],
            "knowledge_points": ["第一性原理=回到事实与假设起点", "假设审计机制"],
            "inspirations": ["给每条笔记加推导链字段，防止搬运结论"],
            "counter_examples": ["直接收藏干货=搬运结论，检索只会命中噪音"],
            "suggested_links": ["卡片盒写作法", "知识降噪设计"],
            "one_line_insight": "记笔记要回到事实与假设重新推导，而非搬运结论。",
            "confidence": 0.9,
        },
        "卡片盒写作法": {
            "core_points": [
                "原子笔记：一张卡片只讲一个观点，可独立理解",
                "链接优先：用双链组织关系，关系即知识",
                "自下而上：不先定大纲，让主题从链接簇中涌现",
            ],
            "knowledge_points": ["卢曼 Zettelkasten", "链接密度与写作质量的关系"],
            "inspirations": ["每天写 3 张卡片而不是等灵感"],
            "counter_examples": ["只存不链的收藏夹=没有卡片盒"],
            "suggested_links": ["第一性原理笔记法", "知识降噪设计"],
            "one_line_insight": "卡片盒写作法以原子笔记与语义链接为前提，自下而上组织写作。",
            "confidence": 0.88,
        },
        "知识降噪设计": {
            "core_points": [
                "知识系统的价值是信噪比而非存量",
                "降级铁律：LLM 不可用时降级为骨架记录而非中断",
                "幂等：同一素材重复入库只产生一条记录",
            ],
            "knowledge_points": ["多级过滤（提炼→讨论→审核）", "中间层与知识层分离"],
            "inspirations": ["每季度审计一次知识库健康度"],
            "counter_examples": ["全量同步剪藏=第二个互联网，检索不到任何东西"],
            "suggested_links": ["卡片盒写作法"],
            "one_line_insight": "知识系统靠多级降噪过滤，才能把输入噪音变为可复用知识。",
            "confidence": 0.85,
        },
    }


def _discussion_text() -> str:
    """「卡片盒写作法」的预置讨论记录（含 [冲突] 标记，演示只标记不裁决）。"""
    return (
        "Q: 卡片盒的链接越多越好吗？链接本身会不会变成噪音？\n"
        "A: 不会自动变好。链接的价值前提是「原子笔记」——一张卡片一个观点，"
        "链接才有语义；链接泛滥的本质是笔记不原子，这正好呼应《知识降噪设计》"
        "的过滤思想。\n"
        "Q: 自下而上不先定大纲，会不会导致写不出文章？\n"
        "A: 适用边界在于卡片数量与链接密度：只有链接簇涌现出主题时才动笔。\n"
        "[冲突: 知识降噪设计]\n"
        "结论摘要：卡片盒写作法的成立前提是「原子笔记+有语义的链接」；"
        "当链接只增不减时它反而制造噪音，与降噪设计冲突。"
    )


def _extract_data() -> dict:
    """讨论 → 卡片字段提炼 JSON。"""
    return {
        "one_line_insight": "卡片盒写作法以原子笔记与语义链接为前提，链接泛滥即噪音。",
        "scope": "适用于以写作为目的的笔记场景，且需要持续的链接维护。",
        "links": ["第一性原理笔记法", "知识降噪设计"],
        "conflicts": ["知识降噪设计"],
    }


def _banner(title: str) -> None:
    print("\n" + "═" * 64)
    print(title)
    print("═" * 64)


def _health_report(root: Path, report: dict) -> str:
    """按审计结果 + 卡片清单生成《知识库健康报告》Markdown。"""
    store = CardStore(root / "wiki")
    cards = store.list()
    rows = [
        f"| {c.slug} | {c.status} | {c.type} | {c.insight} |"
        for c in cards
    ]
    status = "健康 ✓" if report["ok"] else "需治理 ✗"
    return (
        "# 知识库健康报告\n\n"
        f"- 审计时间：{report['audited_at']}\n"
        f"- 卡片总数：{len(cards)}（知识根：{root}，演示结束已清理）\n"
        f"- 健康分：{report['health_score']} / 100\n"
        f"- 结论：{status}\n\n"
        "## 卡片清单\n\n"
        "| 卡片 | 状态 | 类型 | 一句话洞见 |\n"
        "|---|---|---|---|\n"
        + "\n".join(rows)
        + "\n\n"
        "## 未裁决矛盾（AI 只标记不裁决，待人工 resolve_conflict）\n\n"
        + _format_conflicts(report.get("unresolved_conflicts") or [])
        + "\n"
        "## 断链检测\n\n"
        + ("无 ✓" if not report["broken_links"] else "\n".join(
            f"- {b['from_slug']} → {b['to_slug']}" for b in report["broken_links"]))
        + "\n\n"
        "## 孤儿卡片\n\n"
        + ("无 ✓" if not report["orphans"] else "\n".join(
            f"- {slug}" for slug in report["orphans"]))
        + "\n"
    )


def _format_conflicts(conflicts: list[dict]) -> str:
    """未裁决矛盾渲染：source → target + 状态 + 处置建议（无则占位）。"""
    if not conflicts:
        return "无 ✓\n"
    lines = [
        f"- {u['source_slug']} → {u['target_slug']}"
        f"（status={u.get('status', 'conflict')}"
        f"{'，' + u['summary'] if u.get('summary') else ''}，"
        f"须人工调用 resolve_conflict 裁决）"
        for u in conflicts
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    demo_dir = repo_root / "data" / "demo_knowledge"
    missing = [name for name, _ in MATERIALS if not (demo_dir / name).is_file()]
    if missing:
        print(f"缺少演示素材: {missing}", file=sys.stderr)
        return 1

    llm = DemoLLM(_distill_data(), _discussion_text(), _extract_data())

    # 临时知识根：闭环产物不污染真实 knowledge/（含 log.md / index.md 全程同步）
    with tempfile.TemporaryDirectory(prefix="kb-demo-") as tmp:
        root = Path(tmp)
        runner = WorkflowRunner(knowledge_root=root, llm=llm)
        print(f"演示知识根（临时）: {root}")

        # ── Step1 收集 ─────────────────────────────────────────
        _banner("[Step1] capture 低摩擦收集（data/demo_knowledge/ → inbox/）")
        slugs: dict[str, str] = {}
        for name, stype in MATERIALS:
            slug = runner.run_ingest(demo_dir / name, dest_layer="inbox",
                                     source_type=stype)
            slugs[name] = slug
            print(f"  入库 slug={slug} source_type={stype}（素材原样只读）")

        # ── Step2 提炼 ─────────────────────────────────────────
        _banner("[Step2] distill AI 辅助清洗（inbox/ → processed/）")
        for name, _ in MATERIALS:
            note = runner.run_distill(root / "inbox" / name)
            print(f"  提炼 slug={note.slug} distilled={note.distilled} "
                  f"model={note.llm_model or 'none'} insight={note.one_line_insight!r}")

        # ── Step3 深度讨论 ─────────────────────────────────────
        _banner("[Step3] discuss 深度讨论（人工提问 → AI 追问校准）")
        disc_path = runner.run_discuss(DISCUSS_NOTE, DISCUSS_QUESTION)
        print(f"  讨论记录: {disc_path}")
        print(f"  问题: {DISCUSS_QUESTION}")
        print(f"  冲突标记: [冲突: 知识降噪设计]（AI 只标记矛盾，不自动裁决）")

        # ── Step4 产卡与聚合 ───────────────────────────────────
        _banner("[Step4] card 产卡聚合（人工确认后 AI 产卡，状态 draft）")
        # 人工确认（人机边界：AI 不替人判断，approve 是人的动作）
        for name, _ in MATERIALS:
            approved = approve_note(slugs[name], knowledge_root=root)
            print(f"  （人工）确认笔记 {slugs[name]} → approved={approved}")

        card_slugs: list[str] = []
        # 路径A：讨论 → 卡片（insight/scope/links 来自讨论提炼，source_card 指向讨论）
        a = runner.card_from_discussion(disc_path, card_type="concepts")
        print(f"  讨论产卡: {a}（source_card=processed/卡片盒写作法.discussion.md）")
        card_slugs.append(a)
        # 路径B：笔记直产卡（其余两篇）
        for name, _ in MATERIALS:
            if name.startswith(DISCUSS_NOTE):
                continue
            b = runner.run_card(slugs[name], card_type="concepts")
            print(f"  笔记产卡: {b}")
            card_slugs.append(b)

        # 人工确认卡片（draft → current；AI 不自动升级状态）
        store = CardStore(root / "wiki")
        _banner("（人机边界）人工确认卡片 → draft → current")
        for slug in card_slugs:
            card = store.transition(slug, "current")
            print(f"  transition {slug}: {card.status}（contradictions={card.contradictions}）")

        # ── Step5 审计与健康 ───────────────────────────────────
        _banner("[Step5] audit 审计与健康")
        report = runner.run_audit()
        print(f"  卡片总数={report['total_cards']} "
              f"断链={len(report['broken_links'])} "
              f"孤儿={report['orphans']} 结论={'健康 ✓' if report['ok'] else '需治理 ✗'}")

        # 同步佐证：log.md / index.md 由引擎自动维护
        log_lines = len((root / "log.md").read_text(encoding="utf-8").strip().splitlines())
        index_lines = len((root / "index.md").read_text(encoding="utf-8").strip().splitlines())
        print(f"  log.md 已同步（{log_lines} 行）、index.md 已同步（{index_lines} 行）")

        # ── 生成《知识库健康报告》（Markdown + JSON） ──────────
        report_text = _health_report(root, report)
        demo_dir.mkdir(parents=True, exist_ok=True)
        report_path = demo_dir / HEALTH_REPORT_NAME
        report_path.write_text(report_text, encoding="utf-8")
        _banner("产物：《知识库健康报告》")
        print(f"已写入: {report_path}")
        json_path = demo_dir / "知识库健康报告.json"
        json_path.write_text(
            json.dumps(report_to_json(report, store), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"已写入: {json_path}（结构化 JSON，供自动化审计）")

    print("\n演示完成：capture→distill→discuss→card→audit 最小闭环跑通 ✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
