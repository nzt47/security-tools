"""知识库完整闭环工作流验证脚本（任务7 · Step 5 补充验证）。

用法（Windows PowerShell，仓库根目录下）：
    $env:PYTHONIOENCODING="utf-8"
    python scripts/dev/verify_knowledge_workflow.py

用 mock 数据在**临时 knowledge 根**跑通「capture→distill→discuss→card→audit」
完整闭环，并对每个核心分支逐项断言（任一 FAIL 立即 exit 1）：
  日志级别 INFO，可同时观察 workflow/discuss/distill/card/tools 各模块
  关键分支的 logger 打印（含耗时统计与降级原因），便于排查报错。

  A. 端到端闭环 happy path（内置 MockLLM，离线可跑通）
     capture×3 → distill×3（distilled=True）→ 人工 approve×3
     → discuss 1 篇（含 [冲突] 标记）→ card_from_discussion（insight/scope/source_card）
     → 其余 2 篇笔记直产卡 → 人工 transition → current
     → audit 断链 0 / 孤儿 0；log.md / index.md 全程同步；检索命中
  B. 降级铁律
     llm=None → 骨架笔记（distilled=False, reason=offline）→ 骨架笔记拒绝产卡
     骨架讨论（distilled=False）→ 骨架讨论拒绝产卡
     FailLLM（抛异常）→ 提炼/讨论均降级骨架（reason=error）
  C. 敏感素材护栏
     含 PII 素材 meta 标记 sensitive=true → 提炼跳过（reason=sensitive），
     敏感正文不进入 processed/，且拒绝产卡
  D. 人机边界
     未确认（未 approve）笔记拒绝产卡；冲突只标记不自动裁决
  E. 工具入口（kb_* 离线降级）
     register_knowledge_tools 注册 6 个工具；kb_capture/distill/card/lint/search
     返回 dict；注销幂等
  F. LLM 完全超时极端场景（TimeoutLLM）
     模拟客户端超时抛 TimeoutError → 提炼/讨论降级骨架，全流程不抛未捕获异常

--pre-commit 提交前静默模式 / --traceback 失败堆栈模式（与 verify_knowledge_cli.py 一致）。
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import re
import sys
import tempfile
import time
import traceback
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

# 使脚本可在任意 cwd 下运行：把仓库根加入 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.knowledge.card import CardStore  # noqa: E402
from agent.knowledge.discuss import load_discussion  # noqa: E402
from agent.knowledge.distill import approve_note  # noqa: E402
from agent.knowledge.ingest import ingest_file  # noqa: E402
from agent.knowledge.search import KnowledgeSearch  # noqa: E402
from agent.knowledge.workflow import WorkflowRunner  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s [%(name)s] %(message)s",
)

_passed = 0
_QUIET = False
_TRACEBACK = False

# ════════════════════════════════════════════════════════════
#  mock LLM（duck-typing：只需 chat(messages, system_prompt=...) -> str）
# ════════════════════════════════════════════════════════════


class MockLLM:
    """预置响应 mock：按系统提示词路由（知识提炼者/讨论者/卡片编辑）。"""

    model = "mock-llm"

    def __init__(self, distill: dict, discussion: str, extract: dict):
        self._distill = distill
        self._discussion = discussion
        self._extract = extract

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


class FailLLM:
    """总是抛异常的 LLM：验证「LLM 异常 → 降级骨架」分支。"""

    model = "fail-llm"

    def chat(self, messages, system_prompt: str = "") -> str:
        raise RuntimeError("mock LLM 服务不可用（模拟超时）")


class TimeoutLLM:
    """完全超时极端场景：模拟 LLM 长时间挂起后由客户端中断抛 TimeoutError。

    真实 LLM 客户端在自身请求超时后抛 TimeoutError（如 requests/httpx timeout）；
    降级铁律要求这种情形同样降级为骨架，绝不向外抛异常。
    """

    model = "timeout-llm"

    def chat(self, messages, system_prompt: str = "") -> str:
        time.sleep(0.05)  # 模拟挂起等待（有界，避免验证脚本阻塞）
        raise TimeoutError("LLM 请求完全超时（模拟 60s 无响应，客户端中断）")


def _distill_data() -> dict:
    """3 篇素材的预置提炼 JSON（suggested_links 互相引用 → 闭环无孤儿）。"""
    return {
        "第一性原理笔记法": {
            "core_points": ["结论必须能追溯推导过程", "区分事实/假设/结论三层"],
            "knowledge_points": ["第一性原理=回到事实与假设起点"],
            "inspirations": ["给每条笔记加推导链字段"],
            "counter_examples": ["直接收藏干货=搬运结论，检索只会命中噪音"],
            "suggested_links": ["卡片盒写作法", "知识降噪设计"],
            "one_line_insight": "记笔记要回到事实与假设重新推导，而非搬运结论。",
            "confidence": 0.9,
        },
        "卡片盒写作法": {
            "core_points": ["原子笔记：一张卡片只讲一个观点", "链接优先：用双链组织关系"],
            "knowledge_points": ["卢曼 Zettelkasten", "链接密度与写作质量的关系"],
            "inspirations": ["每天写 3 张卡片而不是等灵感"],
            "counter_examples": ["只存不链的收藏夹=没有卡片盒"],
            "suggested_links": ["第一性原理笔记法", "知识降噪设计"],
            "one_line_insight": "卡片盒写作法以原子笔记与语义链接为前提，自下而上组织写作。",
            "confidence": 0.88,
        },
        "知识降噪设计": {
            "core_points": ["知识系统的价值是信噪比而非存量", "降级铁律：不可用时降级而非中断"],
            "knowledge_points": ["多级过滤（提炼→讨论→审核）", "中间层与知识层分离"],
            "inspirations": ["每季度审计一次知识库健康度"],
            "counter_examples": ["全量同步剪藏=第二个互联网"],
            "suggested_links": ["卡片盒写作法"],
            "one_line_insight": "知识系统靠多级降噪过滤，才能把输入噪音变为可复用知识。",
            "confidence": 0.85,
        },
    }


def _discussion_text() -> str:
    """「卡片盒写作法」预置讨论记录（含 [冲突] 标记，演示只标记不裁决）。"""
    return (
        "Q: 卡片盒的链接越多越好吗？链接本身会不会变成噪音？\n"
        "A: 不会自动变好。链接的价值前提是「原子笔记」，链接泛滥的本质是笔记"
        "不原子，这正好呼应《知识降噪设计》的过滤思想。\n"
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


# ════════════════════════════════════════════════════════════
#  工具函数
# ════════════════════════════════════════════════════════════


def _p(msg: str = "") -> None:
    if not _QUIET:
        print(msg)


def _check(name: str, cond: bool, extra: str = "") -> None:
    """断言检查：失败立即 exit 1（FAIL 恒打印到 stderr）。"""
    global _passed
    if cond:
        _passed += 1
        _p(f"  [PASS] {name} {extra}")
    else:
        print(f"  [FAIL] {name} {extra}", file=sys.stderr)
        if _TRACEBACK:
            traceback.print_stack(file=sys.stderr)
        raise SystemExit(1)


def _raises(exc_type, fn) -> bool:
    """fn() 是否抛出指定异常类型（其他异常/不抛均视为 False）。"""
    try:
        fn()
    except exc_type:
        return True
    except Exception:
        return False
    return False


def _make_materials(src: Path) -> list[str]:
    """构造 3 篇 mock 素材（返回标题列表；文件名 = 标题）。"""
    bodies = {
        "第一性原理笔记法": (
            "记笔记最常犯的错误是把别人的结论当成自己的知识。第一性原理笔记法"
            "要求回到事实与假设的起点重新推导，而不是搬运结论。\n"
        ),
        "卡片盒写作法": (
            "写作不是从空白页开始，而是从卡片盒里已有卡片的重组开始。"
            "原子笔记 + 双链组织，让主题从链接簇中涌现。\n"
        ),
        "知识降噪设计": (
            "知识系统的价值是信噪比而非存量。经过提炼、讨论、审核的多级过滤，"
            "输入噪音才能变成可复用知识。\n"
        ),
    }
    for title, body in bodies.items():
        (src / f"{title}.md").write_text(body, encoding="utf-8")
    return list(bodies)


# ════════════════════════════════════════════════════════════
#  各场景
# ════════════════════════════════════════════════════════════


def scenario_a(tmp: Path, src: Path) -> None:
    """A. 端到端闭环 happy path（MockLLM）。"""
    root = tmp / "kb-a"
    runner = WorkflowRunner(knowledge_root=root,
                            llm=MockLLM(_distill_data(), _discussion_text(),
                                        _extract_data()))
    materials = _make_materials(src)

    _p("\n== A. 端到端闭环 happy path（capture→distill→discuss→card→audit）==")

    _p("\n-- A1 capture 收集入库 --")
    slugs = {}
    for name in materials:
        slugs[name] = runner.run_ingest(src / f"{name}.md", source_type="article")
    _check("3 篇素材全部入库", len(slugs) == 3, f"slugs={sorted(slugs)}")

    _p("\n-- A2 distill 提炼（mock LLM）--")
    notes = {}
    for name in materials:
        notes[name] = runner.run_distill(root / "inbox" / f"{name}.md")
    _check("全部提炼成功 distilled=True",
           all(n.distilled for n in notes.values()))
    _check("全部含一句话洞见", all(n.one_line_insight for n in notes.values()))

    _p("\n-- A3 discuss 深度讨论（含冲突标记）--")
    disc_path = runner.run_discuss("卡片盒写作法", "卡片盒的链接越多越好吗？")
    _check("讨论记录文件已生成", Path(disc_path).is_file(), f"path={disc_path}")
    disc = load_discussion(disc_path)
    _check("讨论记录 distilled=True", disc.distilled)
    _check("讨论发现冲突标记 [冲突: 知识降噪设计]",
           "知识降噪设计" in disc.conflicts, f"conflicts={disc.conflicts}")

    _p("\n-- A4 approve + card 产卡（draft）--")
    for name in materials:
        _check(f"人工 approve 笔记 {name}",
               approve_note(slugs[name], knowledge_root=root))
    card_a = runner.card_from_discussion(disc_path)
    _check("讨论产卡 slug=卡片盒写作法", card_a == "卡片盒写作法")
    store = CardStore(root / "wiki")
    ca = store.get("卡片盒写作法")
    _check("讨论产卡含 insight + scope + source_card 指向讨论",
           ca is not None and ca.insight and ca.scope
           and ca.metadata.get("source_card", "")
           .endswith("processed/卡片盒写作法.discussion.md"),
           f"source_card={ca.metadata.get('source_card') if ca else None}")
    _check("冲突只标记不裁决（contradictions=conflict）",
           ca is not None and ca.contradictions
           == [{"target_slug": "知识降噪设计", "status": "conflict"}],
           f"contradictions={ca.contradictions if ca else None}")
    for name in ("第一性原理笔记法", "知识降噪设计"):
        slug = runner.run_card(slugs[name])
        _check(f"笔记直产卡 {name}", slug == name)
    _check("全部产卡状态为 draft（待人工确认）",
           all(c.status == "draft" for c in store.list()),
           f"statuses={[c.status for c in store.list()]}")

    _p("\n-- A5 人工确认 transition → current --")
    for c in store.list():
        store.transition(c.slug, "current")
    _check("人工确认后全部转 current",
           all(c.status == "current" for c in store.list()))

    _p("\n-- A6 audit 健康审计 --")
    report = runner.run_audit()
    _check("audit: 3 卡 / 0 断链 / 0 孤儿",
           report["total_cards"] == 3
           and not report["broken_links"] and not report["orphans"],
           f"report={report}")

    _p("\n-- A7 log/index 同步 + 检索 --")
    log_text = (root / "log.md").read_text(encoding="utf-8")
    index_text = (root / "index.md").read_text(encoding="utf-8")
    _check("log.md 含 create/transition 记录",
           "create" in log_text and "transition" in log_text)
    _check("index.md 索引全部 3 张卡",
           all(name in index_text for name in materials))
    hits = KnowledgeSearch(CardStore(root / "wiki")).search("卡片盒", top_k=3)
    _check("知识检索命中（卡片盒）", len(hits) >= 1, f"hits={[h.slug for h in hits]}")


def scenario_b(tmp: Path, src: Path) -> None:
    """B. 降级铁律（离线 / LLM 异常 → 骨架产物，不抛异常）。"""
    _p("\n== B. 降级铁律（离线骨架 + 异常降级）==")

    _p("\n-- B1 离线（llm=None）提炼 --")
    root_b = tmp / "kb-b"
    runner_b = WorkflowRunner(knowledge_root=root_b, llm=None)
    runner_b.run_ingest(src / "知识降噪设计.md", source_type="article")
    note_b = runner_b.run_distill(root_b / "inbox" / "知识降噪设计.md")
    _check("离线 → 骨架笔记 distilled=False", note_b.distilled is False)
    _check("离线 reason=offline", note_b.reason == "offline", f"reason={note_b.reason}")
    approve_note(note_b.slug, knowledge_root=root_b)
    _check("骨架笔记即使 approve 也拒绝产卡",
           _raises(ValueError, lambda: runner_b.run_card(note_b.slug)))

    _p("\n-- B2 离线（llm=None）讨论 --")
    disc_b = runner_b.run_discuss(note_b.slug, "测试问题")
    d_b = load_discussion(disc_b)
    _check("离线讨论 → 骨架讨论 distilled=False", d_b.distilled is False)
    _check("骨架讨论拒绝产卡（缺 insight 校验失败）",
           _raises(ValueError, lambda: runner_b.card_from_discussion(disc_b)))

    _p("\n-- B3 LLM 异常（FailLLM）降级 --")
    root_f = tmp / "kb-f"
    runner_f = WorkflowRunner(knowledge_root=root_f, llm=FailLLM())
    runner_f.run_ingest(src / "卡片盒写作法.md", source_type="article")
    note_f = runner_f.run_distill(root_f / "inbox" / "卡片盒写作法.md")
    _check("LLM 异常 → 提炼降级骨架 reason=error",
           note_f.distilled is False and note_f.reason == "error",
           f"reason={note_f.reason}")
    disc_f = runner_f.run_discuss(note_f.slug, "测试问题")
    d_f = load_discussion(disc_f)
    _check("LLM 异常 → 讨论降级骨架 reason=error",
           d_f.distilled is False and d_f.reason == "error",
           f"reason={d_f.reason}")


def scenario_c(tmp: Path, src: Path) -> None:
    """C. 敏感素材护栏（PII 只标记，不进 processed，不产卡）。"""
    _p("\n== C. 敏感素材护栏 ==")
    root_c = tmp / "kb-c"
    runner_c = WorkflowRunner(knowledge_root=root_c,
                              llm=MockLLM(_distill_data(), _discussion_text(),
                                          _extract_data()))
    sens_src = src / "敏感素材.md"
    sens_src.write_text(
        "这是一段含联系方式的内容，请勿外传。\n"
        "邮箱: test@example.com 电话: 13800138000\n",
        encoding="utf-8",
    )
    res = ingest_file(str(sens_src), dest_layer="inbox",
                      knowledge_root=str(root_c))
    _check("敏感素材 meta 标记 sensitive=True", res.sensitive is True,
           f"patterns={res.sensitive_patterns}")
    note_c = runner_c.run_distill(root_c / "inbox" / "敏感素材.md")
    _check("敏感素材跳过提炼（不调用 LLM）reason=sensitive",
           note_c.distilled is False and note_c.reason == "sensitive",
           f"reason={note_c.reason}")
    body = (root_c / "processed" / f"{note_c.slug}.md").read_text(encoding="utf-8")
    _check("敏感正文不进入 processed/", "13800138000" not in body)
    _check("敏感笔记拒绝产卡",
           _raises(ValueError, lambda: runner_c.run_card(note_c.slug)))


def scenario_f(tmp: Path, src: Path) -> None:
    """F. LLM 完全超时极端场景（TimeoutLLM）：降级流程不抛异常。"""
    _p("\n== F. LLM 完全超时极端场景（TimeoutLLM）==")
    root_f = tmp / "kb-timeout"
    runner_f = WorkflowRunner(knowledge_root=root_f, llm=TimeoutLLM())
    try:
        runner_f.run_ingest(src / "卡片盒写作法.md", source_type="article")
        note = runner_f.run_distill(root_f / "inbox" / "卡片盒写作法.md")
        _check("超时 → 提炼降级骨架（reason=error），未抛异常",
               note.distilled is False and note.reason == "error",
               f"reason={note.reason}")
        disc = runner_f.run_discuss(note.slug, "超时场景测试问题")
        d = load_discussion(disc)
        _check("超时 → 讨论降级骨架，未抛异常",
               d.distilled is False and d.reason == "error", f"reason={d.reason}")
        approve_note(note.slug, knowledge_root=root_f)
        _check("超时骨架笔记产卡被拒（ValueError 可预期，非未捕获异常）",
               _raises(ValueError, lambda: runner_f.run_card(note.slug)))
    except Exception as exc:
        _check("整个流程未抛出未捕获异常", False, f"意外异常: {exc!r}")


def scenario_d(tmp: Path, src: Path) -> None:
    """D. 人机边界：未确认笔记拒绝产卡。"""
    _p("\n== D. 人机边界：未确认（未 approve）拒绝产卡 ==")
    root_d = tmp / "kb-d"
    runner_d = WorkflowRunner(knowledge_root=root_d,
                              llm=MockLLM(_distill_data(), _discussion_text(),
                                          _extract_data()))
    runner_d.run_ingest(src / "第一性原理笔记法.md", source_type="article")
    note_d = runner_d.run_distill(root_d / "inbox" / "第一性原理笔记法.md")
    _check("已提炼但状态未确认（draft）",
           note_d.distilled and note_d.status == "draft",
           f"status={note_d.status}")
    _check("未确认笔记拒绝产卡",
           _raises(ValueError, lambda: runner_d.run_card(note_d.slug)))


def scenario_e(tmp: Path) -> None:
    """E. 工具入口（kb_* 离线降级可返回 dict）。"""
    _p("\n== E. 工具入口（kb_* 离线降级）==")
    import agent.knowledge.tools as ktools

    root_e = tmp / "kb-e"
    os.environ["KNOWLEDGE_ROOT"] = str(root_e)
    ktools._runner = None  # 重置惰性单例，让工具落到临时根
    _check("注册 6 个知识工具", ktools.register_knowledge_tools() == 6)

    r = ktools.kb_capture(text="一篇新的待处理想法：AI 助手如何辅助写作。\n",
                          source_type="thought")
    _check("kb_capture(text) 入库成功", r.get("ok") is True and bool(r.get("slug")),
           f"slug={r.get('slug')}")
    src_path = str(root_e / "inbox" / f"{r['slug']}.md")
    r = ktools.kb_distill(source_path=src_path)
    _check("kb_distill 离线降级（ok=True, distilled=False）",
           r.get("ok") is True and r.get("distilled") is False,
           f"reason={r.get('reason')}")
    r = ktools.kb_card(note_slug=r["slug"])
    _check("kb_card 骨架笔记拒绝（ok=False）", r.get("ok") is False)
    r = ktools.kb_lint()
    _check("kb_lint 健康巡检返回报告", r.get("ok") is True
           and "total_cards" in r.get("report", {}))
    r = ktools.kb_search(query="不存在的关键词xyz")
    _check("kb_search 空结果不报错", r.get("ok") is True and r.get("hits") == [])
    _check("注销 6 个知识工具", ktools.unregister_knowledge_tools() == 6)


# ════════════════════════════════════════════════════════════
#  主入口
# ════════════════════════════════════════════════════════════


def run() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="verify-kb-workflow-"))
    src = tmp / "src"
    src.mkdir()
    _p(f"临时知识根: {tmp}（验证结束请手工清理）")

    scenario_a(tmp, src)
    scenario_b(tmp, src)
    scenario_c(tmp, src)
    scenario_d(tmp, src)
    scenario_e(tmp)
    scenario_f(tmp, src)

    _p(f"\n=== 全部通过：{_passed} 项断言 PASS ===")
    return 0


def main(argv: list[str] | None = None) -> int:
    global _QUIET, _TRACEBACK
    parser = argparse.ArgumentParser(
        prog="python scripts/dev/verify_knowledge_workflow.py",
        description="知识库完整闭环工作流 mock 验证（capture→distill→discuss→card→audit）",
    )
    parser.add_argument(
        "--pre-commit",
        action="store_true",
        help="提交前静默模式：抑制常规输出，失败仅打印 FAIL 到 stderr 并 exit 1",
    )
    parser.add_argument(
        "--traceback",
        action="store_true",
        help="失败时打印完整调用堆栈到 stderr（快速定位断言行）",
    )
    args = parser.parse_args(argv)
    if args.pre_commit:
        _QUIET = True
        logging.getLogger().setLevel(logging.WARNING)
    if args.traceback:
        _TRACEBACK = True
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
