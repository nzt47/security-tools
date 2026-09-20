#!/usr/bin/env python3
"""审计链"测试污染"**只读标注**脚本（L1-d）—— 只标注，绝不删除、绝不改写

【它做什么】
    扫描链式审计台账，用一组**保守、可解释**的判据找出"由测试代码写进生产链"的记录，
    输出一份**清单报告**（JSON + 人类可读 Markdown），逐条给出 ``seq`` 与命中理由，
    供后续统计/合规报告**排除**这些行。

【它绝不做什么（三条硬约束）】
    1. **不改台账、不改日根**：库以 ``file:...?mode=ro`` 只读打开，本脚本没有任何
       INSERT/UPDATE/DELETE/PRAGMA 写操作，也不触碰 ``daily_roots.jsonl``；
    2. **不删除任何记录**：这些行**已经是 hash chain 的一部分**（``prev_hash``/
       ``self_hash`` 已把前后串起来）。删掉它们会再造 N 个 seq 空洞 + 断链，
       重演 2026-09-21 的断裂事故（见 `docs/closeout/L1_审计链修复报告_20260921.md`）。
       标注只是"旁挂清单"，链上记录**原样保留**；
    3. **默认 dry-run**：不带 ``--write-report`` 时只在 stdout 打印摘要，不落任何文件；
       即便落盘，输出目录也被强制排除在 ``data/audit/``（生产数据目录）之外。

【判据分层（保守优先：宁漏不误伤）】
    TIER S（强，结构化：只有测试进程会产生）
        S1 ``pytest_tmp_path``    任一列出现 pytest 临时目录（``.pytest_tmp`` /
                                  ``pytest-of-<user>`` / ``pytest-<n>``）。这类路径只在
                                  pytest 运行期存在，且 tmp 目录名还带着**测试函数名**。
        S2 ``known_test_literal`` subject/action 含"**在 `tests/` 下以字面量出现、
                                  且在生产 `agent/` 代码里不出现**"的标识符族
                                  （与既有报告的 TIER A 同口径，可用 ``--verify-literals``
                                  现场核验来源文件与行号）。
    TIER M（中，标识符像测试但可被生产误用，单独列出、不计入保守口径）
        S3 ``test_only_action``   action ∈ 仅测试脚本使用过的动作名集合
        S4 ``test_actor``         actor 形如 ``test*`` / ``tester`` / ``pytest*`` / ``dummy*``
        S5 ``test_workspace``     workspace_id 形如 ``ws_unit`` / ``ws-test`` ...
    TIER C（弱：合成时钟，可能是**合法历史回填**）
        S6 ``synthetic_clock``    ts 落在真实运行窗口之外（默认 2026-09-12..2026-09-21）

【明确**排除**的两条判据（都实测有反例，列在报告里是为了防止别人再踩）】
    X1 ``loose_pytest_mention``  任一列含 "pytest" 字样但无 S1 特征。
       反例：``repair.diagnose`` 的载荷是**生产自愈在跑 pytest** 的命令行 —— 那是
       生产行为，不是污染（实测 120 条）。
    X2 ``test_name_in_subject``  subject 含 "test" 字样但无 S1/S2。
       反例：生产自愈的 subject 就是**被测文件名** ``tests/unit/test_demo_math.py``
       （实测 120 条）。这也是既有报告 TIER C 会高估的原因。

【口径（保守 vs 参考）】
    strict        = S1 ∪ S2                       ← 本脚本推荐的"可对外引用"数字
    strict_medium = strict ∪ S3 ∪ S4 ∪ S5
    with_clock    = strict ∪ S6                   ← 与既有报告"5,130 条"同口径
    full          = strict ∪ M ∪ C

用法：
    python scripts/annotate_audit_pollution.py                     # dry-run（只打印）
    python scripts/annotate_audit_pollution.py --write-report       # 落盘到 _ci_logs/audit_pollution
    python scripts/annotate_audit_pollution.py --out-dir <dir> --write-report
    python scripts/annotate_audit_pollution.py --json               # 摘要以 JSON 打到 stdout

退出码：0=正常；2=用法/IO 错误。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from agent.audit.chain import DEFAULT_DB_PATH  # noqa: E402

EXIT_OK = 0
EXIT_USAGE = 2

#: 真实运行窗口（含端点）。窗口**之外**的 ts 记 TIER C：合成时钟。
#: 依据：链上记录的时间戳集中在这些天；窗口外跨 2025-08 / 2027-10，只可能来自
#: 测试里显式构造的假时钟。**但** `actor='backfill:s1-02'` 可能是合法历史回填，
#: 故 TIER C 只作旁证，不单独作为污染判定。
DEFAULT_WINDOW = ("2026-09-12", "2026-09-21")

#: S1：pytest 运行期临时目录（三种常见形态；只在 pytest 进程内存在）
PYTEST_TMP_RE = re.compile(r"\.pytest_tmp|pytest-of-|/pytest-\d+/|\\\\pytest-\d+\\\\")

#: S2：在 `tests/` 下以字面量出现、生产代码不出现的标识符族。
#: 每项三个字段：
#:   ``literal``   —— 在测试源码里应当能找到的字面量（``--verify-literals`` 自动复核）
#:   ``match``     —— ``subject_contains`` / ``subject_prefix`` / ``action_equals``
#:   ``value``     —— 在**记录字段**里匹配的值（可与 literal 不同：审计 subject 常由
#:                    生产代码拼装，例如测试里是 ``CONCURRENT_KEY_``，落链 subject 变成
#:                    ``env:CONCURRENT_KEY_``）
#:   ``where``     —— 人工核验过的来源（脚本会复核并写进报告）
KNOWN_TEST_LITERALS: Tuple[Dict[str, str], ...] = (
    {"literal": "probe_approval_e2e_tool", "match": "subject_contains",
     "value": "probe_approval_e2e_tool",
     "where": "tests/unit/test_tool_approval_e2e.py"},
    {"literal": "probe_", "match": "subject_prefix", "value": "probe_",
     "where": "tests/unit/test_tool_approval_e2e.py（probe_* 族）",
     "note": "`probe_` 是**子串**，生产代码里也有 `probe_xxx` 之类无关标识符；"
             "本判据用的是 subject **前缀**，故生产命中不影响该判据"},
    {"literal": "SAME_KEY", "match": "subject_contains", "value": "env:SAME_KEY",
     "where": "tests/unit/test_env_hot_reload.py:453（subject 的 env: 前缀由生产代码加）"},
    {"literal": "CONCURRENT_KEY_", "match": "subject_contains",
     "value": "env:CONCURRENT_KEY_",
     "where": "tests/unit/test_env_hot_reload.py:425（同上）"},
    {"literal": "my-skill", "match": "subject_contains", "value": "my-skill",
     "where": "tests/unit/test_agentskills_io_compat.py 等 5 处"},
    {"literal": "skill-p", "match": "subject_contains", "value": "skill-p",
     "where": "tests/unit/test_feedback_skill_binding.py（含 skill-published）"},
    {"literal": "__sample__", "match": "subject_contains", "value": "__sample__",
     "where": "子代理披露的 E9 样本",
     "note": "该字面量在当前代码树里找不到（样本已不入库）：判据沿用既有报告的人工核验，"
             "脚本无法自动复核 —— 命中仅 3 条，可按需剔除该族"},
    {"literal": "__selftest", "match": "subject_prefix", "value": "__selftest",
     "where": "联调用例内自检"},
    {"literal": "global_test_action", "match": "action_equals",
     "value": "global_test_action", "where": "测试占位动作"},
    {"literal": "definitely_not_registered_tool_xyz", "match": "subject_contains",
     "value": "definitely_not_registered_tool_xyz",
     "where": "tests/unit/test_tool_gate.py"},
)

#: S3：仅测试脚本使用过的动作名（生产代码不产生）
TEST_ONLY_ACTIONS = frozenset({
    "global_test_action", "ssrf_probe_test", "mp.write", "normal.write",
    "overflow.write", "queue.full", "stranded.write", "drain.write",
})

#: S4/S5：形如测试的 actor / workspace
TEST_ACTOR_RE = re.compile(r"^(test|tester|pytest|dummy|fake|mock|tmp)", re.IGNORECASE)
TEST_WORKSPACE_RE = re.compile(r"^ws[-_]?(unit|test|tmp|e2e|sample)", re.IGNORECASE)

#: 排除判据（只为"防止再踩"而计数展示）
LOOSE_PYTEST_RE = re.compile(r"pytest", re.IGNORECASE)
LOOSE_TEST_RE = re.compile(r"test[_\-.]", re.IGNORECASE)


def _read_rows(db_path: str) -> List[sqlite3.Row]:
    """**只读**打开台账（``mode=ro``）并全量读出行

    只读是硬约束：本脚本不做任何写库动作，故连 ``PRAGMA journal_mode`` 这类
    可能改写库头的语句都不执行（``mode=ro`` 下写操作本身就会抛错，双保险）。
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return list(conn.execute(
            "SELECT seq, ts, actor, action, subject, source, trace_id,"
            " workspace_id, payload FROM audit_chain ORDER BY seq"))
    finally:
        conn.close()


def _verify_literal_in_tests(literal: str) -> List[str]:
    """在 ``tests/`` 下找该字面量，返回 ``文件:行`` 列表（来源自证）"""
    hits: List[str] = []
    root = os.path.join(_ROOT, "tests")
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                    for lineno, line in enumerate(fh, 1):
                        if literal in line:
                            hits.append(f"{os.path.relpath(path, _ROOT)}:{lineno}")
                            if len(hits) >= 3:
                                return hits
            except OSError:
                continue
    return hits


def _literal_in_production(literal: str) -> List[str]:
    """旁证：该字面量是否出现在生产代码 ``agent/``（出现即说明该判据不够保守）"""
    hits: List[str] = []
    root = os.path.join(_ROOT, "agent")
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                    for lineno, line in enumerate(fh, 1):
                        if literal in line:
                            hits.append(f"{os.path.relpath(path, _ROOT)}:{lineno}")
                            if len(hits) >= 3:
                                return hits
            except OSError:
                continue
    return hits


def _record_reasons(row: sqlite3.Row, window: Tuple[str, str]) -> Tuple[List[str], List[str]]:
    """返回 ``(命中理由, 排除判据理由)``；每条理由自带判据编号，便于人工复核"""
    seq, ts, actor, action, subject, source, trace_id, workspace_id, payload = (
        row["seq"], row["ts"] or "", row["actor"] or "", row["action"] or "",
        row["subject"] or "", row["source"] or "", row["trace_id"] or "",
        row["workspace_id"] or "", row["payload"] or "")
    blob = " ".join([ts, actor, action, subject, source, trace_id, workspace_id, payload])

    reasons: List[str] = []
    excluded: List[str] = []

    if PYTEST_TMP_RE.search(blob.replace("\\\\", "/").replace("\\", "/")):
        m = PYTEST_TMP_RE.search(blob.replace("\\\\", "/").replace("\\", "/"))
        reasons.append(f"S1 pytest_tmp_path: 命中 pytest 临时目录片段 “{m.group(0)}”"
                       if m else "S1 pytest_tmp_path")
    for item in KNOWN_TEST_LITERALS:
        mode, value = item["match"], item["value"]
        hit = ((mode == "subject_contains" and value in subject)
               or (mode == "subject_prefix" and subject.startswith(value))
               or (mode == "action_equals" and action == value))
        if hit:
            reasons.append(f"S2 known_test_literal: {mode} “{value}”"
                           f"（测试源码字面量 “{item['literal']}”；{item['where']}）")
    if action in TEST_ONLY_ACTIONS and not any(r.startswith("S2") for r in reasons):
        reasons.append(f"S3 test_only_action: action==“{action}”（仅测试脚本使用）")
    if TEST_ACTOR_RE.match(actor):
        reasons.append(f"S4 test_actor: actor==“{actor}”")
    if TEST_WORKSPACE_RE.match(workspace_id):
        reasons.append(f"S5 test_workspace: workspace_id==“{workspace_id}”")
    if not (window[0] <= ts[:10] <= window[1]):
        reasons.append(f"S6 synthetic_clock: ts={ts[:10]} 在真实运行窗口 "
                       f"{window[0]}..{window[1]} 之外")

    # ── 排除判据（只记录"我们**没有**用它"，附反例证据）──
    if LOOSE_PYTEST_RE.search(blob) and not any(r.startswith("S1") for r in reasons):
        excluded.append("X1 loose_pytest_mention（该行提到 pytest 但无 pytest 临时目录；"
                        "可能是生产自愈在跑/诊断 pytest）")
    if LOOSE_TEST_RE.search(f"{action} {subject}") and not reasons:
        excluded.append("X2 test_name_in_subject（action/subject 含 test 字样但无结构特征；"
                        "可能是生产记录引用了被测文件名）")
    return reasons, excluded


def collect(db_path: str, window: Tuple[str, str]) -> Dict[str, Any]:
    rows = _read_rows(db_path)
    records: List[Dict[str, Any]] = []
    tier_counts = {"S1": 0, "S2": 0, "S3": 0, "S4": 0, "S5": 0, "S6": 0}
    excluded_counts = {"X1": 0, "X2": 0}
    sets: Dict[str, set] = {k: set() for k in
                            ("S1", "S2", "S3", "S4", "S5", "S6", "X1", "X2")}
    for row in rows:
        reasons, excluded = _record_reasons(row, window)
        codes = [r.split(" ", 1)[0] for r in reasons]
        for code in codes:
            tier_counts[code] = tier_counts.get(code, 0) + 1
            sets[code].add(int(row["seq"]))
        for text in excluded:
            excluded_counts[text.split(" ", 1)[0]] += 1
            sets[text.split(" ", 1)[0]].add(int(row["seq"]))
        if reasons or excluded:
            records.append({
                "seq": int(row["seq"]), "ts": row["ts"], "actor": row["actor"],
                "action": row["action"], "subject": row["subject"],
                "source": row["source"], "workspace_id": row["workspace_id"],
                "tier": ("S" if any(c in ("S1", "S2") for c in codes) else
                         "M" if any(c in ("S3", "S4", "S5") for c in codes) else
                         "C" if "S6" in codes else "X"),
                "reasons": reasons, "excluded_criteria": excluded,
            })
    strict = sets["S1"] | sets["S2"]
    medium = sets["S3"] | sets["S4"] | sets["S5"]
    clock = sets["S6"]
    return {
        "db_path": os.path.abspath(db_path),
        "db_sha256": _sha256_file(db_path),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window": list(window),
        "total_records": len(rows),
        "criteria_counts": tier_counts,
        "excluded_criteria_counts": excluded_counts,
        "verdicts": {
            "strict": sorted(strict),
            "strict_medium": sorted(strict | medium),
            "with_clock": sorted(strict | clock),
            "full": sorted(strict | medium | clock),
        },
        "records": records,
        "chain_first_seq": int(rows[0]["seq"]) if rows else 0,
        "chain_last_seq": int(rows[-1]["seq"]) if rows else 0,
    }


def _sha256_file(path: str) -> str:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def _pct(n: int, total: int) -> str:
    return f"{n}（{n / total * 100:.2f}%）" if total else str(n)


def render_markdown(data: Dict[str, Any], literal_evidence: Sequence[Dict[str, Any]]) -> str:
    total = data["total_records"]
    v = data["verdicts"]
    out: List[str] = []
    out.append("# 审计链测试污染**标注**报告（只读/旁挂清单，链上记录未做任何改动）\n")
    out.append(f"- 生成时间：{data['generated_at']}")
    out.append(f"- 台账：`{data['db_path']}`")
    out.append(f"- 台账 sha256（只读校验，未改动）：`{data['db_sha256']}`")
    out.append(f"- 总记录数：{total}（seq {data['chain_first_seq']}..{data['chain_last_seq']}）")
    out.append(f"- 真实运行窗口（TIER C 判据）：{data['window'][0]} .. {data['window'][1]}\n")
    out.append("## 结论（分层口径）\n")
    out.append("| 口径 | 条数 | 说明 |")
    out.append("|---|---|---|")
    out.append(f"| **strict（S1∪S2，推荐对外引用）** | {_pct(len(v['strict']), total)} | "
               f"结构性强证据：pytest 临时目录 + 仅测试源码出现的字面量 |")
    out.append(f"| strict_medium（∪S3∪S4∪S5） | {_pct(len(v['strict_medium']), total)} | "
               f"并入「像测试」的 action/actor/workspace（可被生产误用，需人工确认） |")
    out.append(f"| with_clock（∪S6） | {_pct(len(v['with_clock']), total)} | "
               f"再并入「合成时钟」（可能是合法历史回填）——既有报告的 5,130 口径 |")
    out.append(f"| full（S∪M∪C） | {_pct(len(v['full']), total)} | 上界，**会高估** |\n")
    out.append("## 判据命中计数（可多条同时命中）\n")
    out.append("| 编号 | 判据 | 命中 | 层级 |")
    out.append("|---|---|---|---|")
    labels = {
        "S1": ("pytest 临时目录（`.pytest_tmp` / `pytest-of-` / `pytest-<n>`）", "强"),
        "S2": ("仅测试源码出现的字面量（10 族，逐族可核）", "强"),
        "S3": ("仅测试脚本使用的 action 名", "中"),
        "S4": ("actor 形如 test*/tester/pytest*", "中"),
        "S5": ("workspace_id 形如 ws_unit/ws-test", "中"),
        "S6": ("ts 落在真实运行窗口之外（合成时钟）", "弱"),
    }
    for code, (label, tier) in labels.items():
        out.append(f"| {code} | {label} | {_pct(data['criteria_counts'][code], total)} | {tier} |")
    out.append("")
    out.append("## 明确**排除**的判据（实测有反例，列出以防再踩）\n")
    out.append("| 编号 | 判据 | 命中 | 为什么不采用 |")
    out.append("|---|---|---|---|")
    out.append(f"| X1 | 任一行含 “pytest” 字样、但**无** pytest 临时目录 | "
               f"{data['excluded_criteria_counts']['X1']} | "
               f"反例就在这批命中里：`approval.submit`（seq 19218/19219/19224）是"
               f"**生产环境**里请求审批去执行 `pytest -q tests/unit` —— 提到 pytest "
               f"不等于测试写入 |")
    out.append(f"| X2 | action/subject 含 “test” 字样、但**无**结构特征 | "
               f"{data['excluded_criteria_counts']['X2']} | "
               f"反例：`env:TEST_KEY_*`（action=config.env_set、actor=**AdminWT**，即 UI 管理员"
               f"配置一个名字里带 TEST 的真实环境变量）、`skill:test-skill`，"
               f"以及生产自愈记录里引用被测文件名（`tests/unit/test_demo_math.py`）"
               f"—— 按名字判会误伤生产记录（既有报告 TIER C 的 5,699 条高估正源于此） |")
    out.append("")
    out.append("## S2 字面量来源自证（脚本现场核验）\n")
    out.append("| 字面量 | tests/ 命中（前 3） | agent/ 生产代码命中 |")
    out.append("|---|---|---|")
    for ev in literal_evidence:
        if ev["in_tests"]:
            tests_hits = "、".join(ev["in_tests"])
        else:
            tests_hits = ("（自动核验未命中：" + (ev.get("note") or "判据需人工复核") + "）")
        prod_hits = "、".join(ev["in_production"]) or "无"
        out.append(f"| `{ev['literal']}` | {tests_hits} | {prod_hits} |")
    out.append("")
    out.append("## 误伤风险（诚实列出）\n")
    out.append("1. **S1 的残余风险极低但非零**：pytest 临时目录只在 pytest 进程内存在，"
               "若未来有生产脚本**故意**去读 `.pytest_tmp`（例如「清理测试残留」的工具调用），"
               "该行会被误标。当前实测 663 条命中里，载荷上下文都是 pytest 自己的 db/lock/event "
               "路径与「带测试函数名的 tmp 目录」，未见清理类调用。")
    out.append("2. **S2 依赖字面量集合**：集合外的测试标识符不会被命中（**宁漏不误伤**）。"
               "新增测试若写生产链，需按同一方法补进 `KNOWN_TEST_LITERALS`。"
               "个别字面量脚本无法自动复核（见上表「自动核验未命中」），已逐条标注来源。")
    out.append("3. **S6（合成时钟）会误伤合法回填**：窗口外的 1,902 条中，"
               "`actor='backfill:s1-02'` 属**可能合法的历史回填**，故 S6 单列、"
               "不计入 strict 口径。")
    out.append("4. **S4/S5 可被生产误用**：`tester`/`test_user` 这类 actor 名可能是"
               "生产演示账号；`ws_unit` 可能是真实工作区名。故列为 TIER M、需人工确认。")
    out.append("5. **X2 那 200 条是「两种解释都成立」的灰区**：例如 `env:TEST_KEY_*`"
               "（action=`config.env_set`）—— ①这些名字确实**只在**测试源码里出现"
               "（`tests/unit/test_env_hot_reload.py:81`），像测试写入；"
               "②但 actor 是 `AdminWT`，而仓库文档（`docs/security/…`）表明那是**本机 OS 用户/"
               "后台管理员**，`config.env_set` 是生产 UI 动作。**脚本不替人裁决**，"
               "故不计入 strict 口径；这批是「要不要算污染」的人决策项。")
    out.append("")
    out.append("## 需人工裁决（脚本不替人决定）\n")
    out.append(f"1. 上面 X2 的 {data['excluded_criteria_counts']['X2']} 条"
               f"「名字像测试但身份是生产」的记录算不算污染？建议：按「是否由 pytest 进程产生」"
               f"逐条查 trace/payload，而不是按名字判。")
    out.append(f"2. TIER C 的 {data['criteria_counts']['S6']} 条合成时钟记录里，"
               f"`actor='backfill:s1-02'` 是否为**合法历史回填**（若是，应从污染口径中扣除）。")
    out.append("3. 是否需要把 strict 清单固化成 CI 门禁（例如「新增记录命中 S1/S2 即失败」），"
               "以阻止测试隔离缺陷再次污染生产链。")
    out.append("")
    out.append("## 处置声明（**必须逐字保留**）\n")
    out.append("> 这些记录**继续留在链上**：它们已经是 hash chain 的一部分"
               "（`prev_hash`/`self_hash` 已把前后串起来），删除会在 append-only 链上"
               "再造同等数量的 seq 空洞与断链（重演 2026-09-21 断裂事故），"
               "并且会销毁「测试隔离缺陷」这一事实的证据。")
    out.append("> 本报告是**旁挂清单**：仅供后续统计 / 合规报告**排除**这些行使用；"
               "链上数据、`daily_roots.jsonl` 与 seq 编号一律未改动。")
    return "\n".join(out) + "\n"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="只读标注审计链中的测试污染（默认 dry-run；绝不删除/改写记录）")
    p.add_argument("--db", default="", help="链式审计台账路径（默认 data/audit/audit_chain.db）")
    p.add_argument("--out-dir", default=os.path.join("_ci_logs", "audit_pollution"),
                   help="报告输出目录（**禁止**指向 data/audit；默认 _ci_logs/audit_pollution）")
    p.add_argument("--write-report", action="store_true",
                   help="落盘 JSON+Markdown 报告（默认 dry-run：只打印摘要）")
    p.add_argument("--json", action="store_true", help="摘要以 JSON 输出到 stdout")
    p.add_argument("--window", default=",".join(DEFAULT_WINDOW),
                   help="真实运行窗口 START,END（用于 S6 合成时钟判据）")
    p.add_argument("--no-verify-literals", action="store_true",
                   help="跳过 S2 字面量的 tests/agent 源码自证扫描")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    db_path = args.db or DEFAULT_DB_PATH
    if not os.path.exists(db_path):
        print(f"[错误] 台账不存在: {db_path}")
        return EXIT_USAGE
    window = tuple(x.strip() for x in str(args.window).split(","))  # type: ignore
    if len(window) != 2:
        print("[错误] --window 需要 START,END")
        return EXIT_USAGE

    out_dir = os.path.abspath(args.out_dir)
    prod_dir = os.path.abspath(os.path.join(_ROOT, "data", "audit"))
    if out_dir == prod_dir or out_dir.startswith(prod_dir + os.sep):
        print(f"[错误] 输出目录不得指向生产审计数据目录: {out_dir}")
        return EXIT_USAGE

    data = collect(db_path, window)  # type: ignore[arg-type]

    literal_evidence: List[Dict[str, Any]] = []
    if not args.no_verify_literals:
        for item in KNOWN_TEST_LITERALS:
            literal_evidence.append({
                "literal": item["literal"], "where": item["where"],
                "note": item.get("note", ""),
                "in_tests": _verify_literal_in_tests(item["literal"]),
                "in_production": _literal_in_production(item["literal"]),
            })

    v, total = data["verdicts"], data["total_records"]
    summary = {
        "db_path": data["db_path"],
        "db_sha256": data["db_sha256"],
        "total_records": total,
        "strict": len(v["strict"]),
        "strict_medium": len(v["strict_medium"]),
        "with_clock": len(v["with_clock"]),
        "full": len(v["full"]),
        "criteria_counts": data["criteria_counts"],
        "excluded_criteria_counts": data["excluded_criteria_counts"],
        "mode": "write-report" if args.write_report else "dry-run",
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print("[审计污染标注] 只读扫描完成（未改动台账/日根）")
        print(f"  台账={data['db_path']}  总记录={total}  sha256={data['db_sha256'][:16]}…")
        print(f"  strict(S1∪S2)={_pct(len(v['strict']), total)}  "
              f"strict+medium={_pct(len(v['strict_medium']), total)}  "
              f"with_clock={_pct(len(v['with_clock']), total)}  "
              f"full={_pct(len(v['full']), total)}")
        print(f"  判据命中: " + "  ".join(
            f"{k}={data['criteria_counts'][k]}" for k in ("S1", "S2", "S3", "S4", "S5", "S6")))
        print(f"  排除判据命中(不计入): X1={data['excluded_criteria_counts']['X1']} "
              f"X2={data['excluded_criteria_counts']['X2']}")
        print("  这些记录**继续留在链上**；本清单仅供统计/合规排除使用。")

    if args.write_report:
        os.makedirs(out_dir, exist_ok=True)
        json_path = os.path.join(out_dir, "audit_pollution_report.json")
        md_path = os.path.join(out_dir, "audit_pollution_report.md")
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump({"summary": summary, "criteria": data["criteria_counts"],
                       "excluded_criteria": data["excluded_criteria_counts"],
                       "verdicts": v, "records": data["records"],
                       "literal_evidence": literal_evidence},
                      fh, ensure_ascii=False, indent=1)
        with open(md_path, "w", encoding="utf-8") as fh:
            fh.write(render_markdown(data, literal_evidence))
        print(f"  报告已写入: {json_path}")
        print(f"            {md_path}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
