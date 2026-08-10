"""构造含矛盾/断链等问题的 mock 知识库，跑完整 audit 流程演示（任务5/7）。

场景覆盖五类检测全部命中（演示健康分扣分推导日志）：
    - 矛盾     : 卡片盒写作法 → 知识降噪设计（status=conflict，AI 只标记不裁决）
    - 断链     : 知识降噪设计 → 幽灵概念（目标卡片不存在）
    - 孤儿     : 第一性原理笔记法（无任何入链）
    - 过期     : 第一性原理笔记法（date 超过 90 天阈值）
    - index 漂移: index.md 含幽灵条目 [[幽灵索引条目]]（与卡片集合 diff）

流程：
    1. 临时知识根构造上述卡片 + index.md（演示结束自动清理）
    2. WorkflowRunner.run_audit() —— 观察五类检测明细 + 健康分扣分推导日志
    3. 调用 CLI `python -m agent.knowledge audit --json <path>` 导出结构化 JSON
    4. 打印 JSON 摘要（健康分 / 扣分明细 / 未裁决矛盾）

预期健康分：100 - 矛盾5 - 断链2 - 孤儿2 - 过期3 - 漂移2 = 86.0

用法（仓库根目录下）：
    $env:PYTHONIOENCODING="utf-8"
    python scripts/dev/demo_audit_mock.py
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

# 使脚本可在任意 cwd 下运行：把仓库根加入 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.knowledge.card import CardStore  # noqa: E402
from agent.knowledge.schema import Card  # noqa: E402
from agent.knowledge.workflow import WorkflowRunner  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s [%(name)s] %(message)s",
)


def _banner(title: str) -> None:
    print("\n" + "═" * 64)
    print(title)
    print("═" * 64)


def _make_card(slug: str, links, contradictions=None, card_date: str | None = None) -> Card:
    """构造 current 态卡片。"""
    return Card(
        title=slug,
        slug=slug,
        status="current",
        type="concepts",
        source="mock/mock.md",
        date=card_date or date.today().isoformat(),
        links=links,
        contradictions=contradictions or [],
        insight=f"{slug} 的一句话核心洞见",
    )


def _build_mock_library(root: Path) -> None:
    """构造五类问题全覆盖的 mock 知识库。"""
    store = CardStore(root / "wiki")
    store.create(_make_card(
        "卡片盒写作法",
        links=["知识降噪设计"],
        contradictions=[{
            "target_slug": "知识降噪设计",
            "status": "conflict",
            "summary": "链接泛滥即噪音，与降噪设计冲突",
        }],
    ))
    # 引用卡片盒写作法（供其入链、非孤儿）且指向不存在卡片（断链）
    store.create(_make_card("知识降噪设计", links=["卡片盒写作法", "幽灵概念"]))
    store.create(_make_card(                                             # 孤儿 + 过期
        "第一性原理笔记法",
        links=[],
        card_date=(date.today() - timedelta(days=120)).isoformat(),
    ))
    # 引擎已生成 index.md（含 3 张卡片条目），末尾补换行后追加幽灵条目 → 漂移
    idx = root / "index.md"
    idx.write_text(idx.read_text(encoding="utf-8").rstrip()
                   + "\n- [[幽灵索引条目]]\n", encoding="utf-8")
    print("  mock 库已构造：矛盾 1 条 / 断链 1 条 / 孤儿 1 张 / 过期 1 张 / 漂移 1 张")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="kb-audit-mock-") as tmp:
        root = Path(tmp)
        print(f"临时知识根: {root}（演示结束自动清理）")

        _banner("[Step1] 构造 mock 知识库（五类问题全覆盖）")
        _build_mock_library(root)

        _banner("[Step2] WorkflowRunner.run_audit（五类检测 + 健康分推导日志）")
        report = WorkflowRunner(knowledge_root=root).run_audit()

        _banner("[Step3] CLI 导出结构化 JSON（python -m agent.knowledge audit --json）")
        out_json = root / "report.json"
        reports_dir = root / "reports"
        proc = subprocess.run(
            [sys.executable, "-m", "agent.knowledge", "audit",
             "--wiki", str(root / "wiki"),
             "--index", str(root / "index.md"),
             "--reports-dir", str(reports_dir),
             "--no-email", "--json", str(out_json)],
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True, text=True, encoding="utf-8",
        )
        print(proc.stdout.strip())
        if proc.returncode != 0:
            print(proc.stderr, file=sys.stderr)
            return 1
        assert out_json.is_file(), "CLI --json 应生成报告文件"

        _banner("[Step4] JSON 摘要（自动化审计输入）")
        payload = json.loads(out_json.read_text(encoding="utf-8"))
        print(f"  健康分: {payload['health_score']} / 100 "
              f"扣分明细={payload['score_breakdown']}")
        print(f"  未裁决矛盾: {payload['unresolved_conflicts']}")
        print(f"  断链: {payload['broken_links']}")
        print(f"  孤儿: {payload['orphans']}")
        print(f"  index 漂移: {payload['index_drift']}")
        print(f"  过期: {payload['stale_cards']}")

        # 断言：健康分推导 = 100 - (5+2+2+3+2) = 86.0，与日志一致
        assert payload["health_score"] == 86.0
        assert payload["score_breakdown"] == {
            "conflicts": 5, "broken_links": 2, "orphans": 2,
            "stale": 3, "index_drift": 2,
        }
        assert payload["ok"] is False

    print("\nmock audit 演示完成 ✓（五类检测 + 健康分推导 + CLI JSON 导出全链路）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
