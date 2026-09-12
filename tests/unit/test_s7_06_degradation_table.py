"""TASK-S7-06 R7：单机降级设施表**机器校验**（文档不能只是"写给人看"）

验收对应（任务书 §四 R7）：
- 表覆盖 **≥7 项**，每项含"要求 / 实现 / 缺口 / 升级路径"；
- 每项**可追溯到代码位置或验收报告**；"已验证/未验证"标注齐全；
- 新文档链接通过本地 docs 链接预检（此处另做一遍**目标存在性**校验，双保险）。

本测试把文档里的**可追溯断言**当数据来读：
`docs/zh/单机降级设施表.md` 的三张表由脚本解析，逐条核对
`路径::符号` 真实存在、状态取值合法、未验证项带批次/残留编号、统计与明细一致。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DOC = REPO_ROOT / "docs" / "zh" / "单机降级设施表.md"

ITEM_HEADING = re.compile(r"^###\s*(\d+)\.\s*(.+?)\s*$", re.MULTILINE)
LINK = re.compile(r"\]\(([^)]+)\)")
STATUS_LABEL = re.compile(r"\*{0,2}(已验证|未验证)\*{0,2}")
PATH_SYMBOL = re.compile(r"`([^`]+?\.py)::([A-Za-z_][\w.]*)`")
BARE_PATH = re.compile(r"`((?:agent|tests|docs)/[\w./\-]+\.\w+)(?:::[^`]*)?`")


@pytest.fixture(scope="module")
def text() -> str:
    assert DOC.exists(), f"R7 交付物缺失: {DOC}"
    return DOC.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def tables(text: str):
    """解析全部 Markdown 表格 → [{"header": [...], "rows": [[...]]}]"""
    out = []
    header = None
    rows = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            if header and rows:
                out.append({"header": header, "rows": rows})
            header, rows = None, []
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if set("".join(cells)) <= set("-: "):
            continue
        if header is None:
            header = cells
        else:
            rows.append(cells)
    if header and rows:
        out.append({"header": header, "rows": rows})
    return out


def _traceability(tables):
    for table in tables:
        if any("断言" in cell for cell in table["header"]):
            return table
    pytest.fail("未找到「可追溯清单」表（表头需含『断言』）")


def _summary(tables):
    for table in tables:
        if any("状态" in cell for cell in table["header"]) \
                and any("条数" in cell for cell in table["header"]):
            return table
    pytest.fail("未找到「统计」表（表头需含『状态』与『条数』）")


# ════════════════════════════════════════════════════════════
#  覆盖度：≥7 项，每项四要素齐全
# ════════════════════════════════════════════════════════════


class TestFacilityCoverage:
    def test_document_exists(self):
        assert DOC.exists()

    def test_at_least_seven_items(self, text):
        items = ITEM_HEADING.findall(text)
        assert len(items) >= 7, f"仅 {len(items)} 项（要求 ≥7）"
        assert [int(n) for n, _ in items] == list(range(1, len(items) + 1))

    def test_required_seven_topics_present(self, text):
        for topic in ("Watchdog", "审计存储", "出域控制", "生成代码执行",
                      "策略引擎", "多租户", "台账保留"):
            assert topic in text, f"缺少设施项：{topic}"

    def test_each_item_has_four_columns(self, tables):
        """每项一张四列对照表：v7.2 要求 / 单机实现 / 边界缺口 / 升级路径"""
        required = ("v7.2 要求", "云枢单机实现", "边界/已知缺口", "升级路径")
        item_tables = [t for t in tables
                       if all(any(need in cell for cell in t["header"])
                              for need in required)]
        assert len(item_tables) >= 7, f"四列对照表仅 {len(item_tables)} 张"

    def test_traceability_claims_carry_status(self, tables):
        table = _traceability(tables)
        assert len(table["rows"]) >= 20, "可追溯断言过少（每项至少 2 条）"
        for row in table["rows"]:
            joined = row[-1]
            assert STATUS_LABEL.search(joined), f"状态标注缺失/非法: {row}"
            if "未验证" in joined:
                assert any(token in joined for token in ("P5", "R5", "生产化", "部署侧",
                                                         "批次", "缺口", "未实现",
                                                         "未容器化", "不可检出", "按需")), (
                    f"未验证项必须给出归属（批次/残留编号）: {row}")


# ════════════════════════════════════════════════════════════
#  可追溯性：路径与符号真实存在
# ════════════════════════════════════════════════════════════


class TestTraceability:
    def test_code_paths_and_symbols_exist(self, tables):
        table = _traceability(tables)
        checked = 0
        for row in table["rows"]:
            cell = row[3]
            for path_text, symbol in PATH_SYMBOL.findall(cell):
                target = REPO_ROOT / path_text
                assert target.exists(), f"引用的模块不存在: {path_text}"
                source = target.read_text(encoding="utf-8")
                leaf = symbol.split(".")[-1]
                assert leaf in source, f"{path_text} 中找不到符号 {leaf}（{row[0]}）"
                checked += 1
        assert checked >= 10, f"仅校验到 {checked} 条 路径::符号 断言"

    def test_evidence_test_files_exist(self, tables):
        """每个"已验证"行都必须能追到跑得过的证据文件（单测或验收报告）"""
        table = _traceability(tables)
        verified_rows = 0
        for row in table["rows"]:
            joined = " ".join(row)
            if "未验证" in joined and "已验证" not in joined:
                continue
            verified_rows += 1
            evidence = [m for m in BARE_PATH.findall(joined) if m.endswith(".py")]
            assert evidence, f"已验证行缺证据文件: {row}"
            for rel in evidence:
                assert (REPO_ROOT / rel).exists(), f"证据文件不存在: {rel}"
        assert verified_rows >= 13, f"已验证行仅 {verified_rows} 条"

    def test_bare_paths_resolve(self, text):
        for rel in set(BARE_PATH.findall(text)):
            assert (REPO_ROOT / rel).exists(), f"引用的路径不存在: {rel}"


# ════════════════════════════════════════════════════════════
#  统计一致 + 链接可解析
# ════════════════════════════════════════════════════════════


class TestConsistency:
    def test_summary_matches_rows(self, tables):
        table = _traceability(tables)
        verified = sum(1 for row in table["rows"]
                       if "未验证" not in row[-1] and "已验证" in row[-1])
        unverified = sum(1 for row in table["rows"] if "未验证" in row[-1])
        summary = _summary(tables)
        claimed = {}
        for row in summary["rows"]:
            label = row[0]
            match = re.match(r"\D*(\d+)", row[1])
            count = int(match.group(1)) if match else 0
            if "已验证" in label and "未验证" not in label:
                claimed["verified"] = count
            elif "未验证" in label:
                claimed["unverified"] = count
            elif "合计" in label:
                claimed["total"] = count
        assert claimed["verified"] == verified
        assert claimed["unverified"] == unverified
        assert claimed["total"] == len(table["rows"]) == verified + unverified

    def test_all_relative_links_resolve(self, text):
        """文档内相对链接目标必须存在（`../` 层级错误是历史红点）"""
        checked = 0
        for raw in LINK.findall(text):
            target = raw.split("#", 1)[0].strip()
            if not target or target.startswith(("http://", "https://", "mailto:")):
                continue
            resolved = (DOC.parent / target).resolve()
            assert resolved.exists(), f"文档链接失效: {raw}"
            checked += 1
        assert checked >= 8, f"仅校验到 {checked} 条相对链接"

    def test_verified_and_unverified_are_both_present(self, text):
        """两种标注都必须出现（只有"已验证"的表格不可信）"""
        labels = STATUS_LABEL.findall(text)
        assert "已验证" in labels and "未验证" in labels

    def test_known_gaps_are_disclosed_not_glossed(self, text):
        """R7 的诚信底线：三个已知缺口必须逐字写明"""
        for phrase in ("删除链尾不可检出", "真实执行型产物未容器化", "现状无 TTL"):
            assert phrase in text, f"已知缺口未如实标注: {phrase}"
