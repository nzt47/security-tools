"""防复发守卫：禁止在「读真实时钟且无时钟注入」的今日边界测试里用硬编码绝对日期

（TASK-S11-06「修日期定时炸弹」的守卫部分；2026-09-14 建立）

════════════════════════════════════════════════════════════════════════
一、为什么必须有这条（实测缺陷，不是假想）
════════════════════════════════════════════════════════════════════════
``tests/unit/test_decision_log_rotation.py::test_s801_warm_archive_shards_stay_visible``
曾把"今天"**硬编码**成 2026-09-13（写入 09-10 / 09-11 / 09-13 三条，断言
``archived == 2``）。后果：**编写当天通过，次日起必然失败** ——
2026-09-14 实测 ``assert result["archived"] == 2`` 失败（实际 3）。
这类"定时炸弹"会训练人忽视红灯，而"守卫必须常绿"是当前纪律
（红着的守卫会掩盖新违规）。该条已由相对日期（``date.today()``）修复。

════════════════════════════════════════════════════════════════════════
二、为什么「动态多日期冻结」那条守卫**不足以**防复发（实测证据）
════════════════════════════════════════════════════════════════════════
同文件已有一条 ``test_warm_archive_is_date_independent``（伪造 today 到 +0/1/7/30/400
天）。**实测（2026-09-14）它抓不住本类缺陷**：它把"伪造的今天"与"记录日期"
**一起平移**（记录日期由 ``fake_today`` 推导）⇒ 两侧同向移动，``archived == 2``
**恒成立**（自证式断言）。

探针实测（探针复刻原写法，硬编码日期 = 真实今天）::

    shift=+0（今天=2026-09-14，即"编写当天"）: 6 passed   ← 炸弹绿着，守卫也绿
    shift=+1（今天=2026-09-15）              : 1 failed, 5 passed
                                              ↑炸弹现形            ↑守卫仍全过

⇒ 那条守卫只在**次日**才由炸弹自己暴露；它无法在"编写当天"发现炸弹。
本文件因此改用**静态规则**：只有静态检查能在"写下的那一刻"发现
"用绝对日期表达今天"。

════════════════════════════════════════════════════════════════════════
三、规则（窄口径，宁窄勿宽 —— 宽了会误报，误报的守卫活不下来）
════════════════════════════════════════════════════════════════════════
在一个测试函数里，若调用了 :data:`CLOCK_BOUNDARY_APIS` 中的 API
（= **内部读真实时钟**且**签名没有可注入时钟**），则该函数内**不得**出现
硬编码 ISO 日期字面量 ``20YY-MM-DD``；确实只需"惰性固定时间戳"的，
在 :data:`ALLOWLIST` 登记并写明理由。

★ **刻意不登记**的 API（否则会产生误报，把守卫逼成噪声）：
  · ``retention.scan`` 的 ``file_day`` / ``scan_all`` / ``file_time_range``
    —— 实测**根本不读时钟**（纯读文件/文件名/mtime）；
  · ``retention.scan.cold_files`` / ``warm_plan``、``cleanup._idle_days``
    —— **接受 ``now=`` 注入**，测试传固定值即完全确定，天然免疫；
  · ``DecisionLog._split_lines`` / ``_has_old_day_records`` 等私有实现
    —— 测试不应直接调用（登记了只会误伤）。

════════════════════════════════════════════════════════════════════════
四、守卫自身的两条"防假绿"自检（守卫也会烂）
════════════════════════════════════════════════════════════════════════
1. :func:`test_detector_catches_the_original_bomb_shape`
   —— 拿**原炸弹源码**喂给检测器，必须命中。
   （这不是形式主义：本次实现期就踩过一次 —— 文件级粗筛误用了带 ``^`` 的
   ``search``，导致**所有文件被静默跳过、命中数为 0**，最后是这个正向对照
   把它抓出来的。没有自检的检测器会安静地退化成空转。）
2. :func:`test_registered_apis_still_read_the_clock`
   —— 登记的 API 若被改名/重构掉，"规则"会变成永不命中的空转；
   故断言每个登记项**至今仍在读时钟**（或经由登记项间接读）。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Dict, List, Set, Tuple

import pytest

# ════════════════════════════════════════════════════════════════════
#  登记表：内部读真实时钟、且**无时钟注入**的今日边界 API
#  （每条必须能在源码里指到行；由 test_registered_apis_still_read_the_clock 守住不腐烂）
# ════════════════════════════════════════════════════════════════════

CLOCK_BOUNDARY_APIS: Dict[str, Tuple[str, str]] = {
    # API 名: (承载模块路径, 依据说明)
    "archive_daily_file": (
        "agent/skills_mgmt/log_archiver.py",
        "L394 `today = date.today().isoformat()`；签名仅 (path)，无可注入时钟",
    ),
    "scan_unused": (
        "agent/skills_mgmt/cleanup.py",
        "L324 `now = _now()`，`_now()` = `datetime.now()`；无 now 参数",
    ),
    "cleanup_unused": (
        "agent/skills_mgmt/cleanup.py",
        "L350 内部转调 scan_unused（同样无时钟注入）",
    ),
    "_has_old_day_records": (
        "agent/policy/decisions.py",
        "L1020 `today = date.today().isoformat()`；无时钟注入",
    ),
    "rotate": (
        "agent/policy/decisions.py",
        "L1076 DecisionLog.rotate → _has_old_day_records / _split_lines（内部 date.today()）",
    ),
}

#: 读时钟的源码特征（用于"登记表未腐烂"自检）
_CLOCK_MARKERS = ("today(", "now(", "utcnow(", "time.time(", "self._clock")

#: 已裁定为**惰性固定时间戳**（非炸弹）的例外：(相对路径, 测试函数名) → 理由
#: 逐条必须写明"为什么它不会随日期变化"，否则不许登记。
ALLOWLIST: Dict[Tuple[str, str], str] = {
    ("tests/unit/test_events_write_hardening.py",
     "test_archive_conserves_records_and_keeps_shard_naming"):
        "日期取 2020-01-01/02（**恒早于任何真实今天**）⇒ 永远落在「历史日」一侧，"
        "归档结果与今天是哪天无关（单调安全）；断言的是分片命名与记录守恒，"
        "不是「今天 vs 历史」的边界。",
    ("tests/unit/test_events_write_hardening.py",
     "test_archive_rewrite_failure_loses_no_record"):
        "同上：2020-01-01 恒为历史日，用例只验证写分片失败时活动文件不被截断。",
    ("tests/unit/test_events_write_hardening.py",
     "test_archive_skips_round_when_lock_unavailable"):
        "同上：2020-01-01 恒为历史日，用例只验证拿不到锁时整轮跳过。",
}

#: 决策日志轮转家族：**实测**只以 `rotate(force=True[, trigger=...])` 轮转
#: —— `force=True` 跳过按日阈值判定（见 decisions.py:1076 docstring），
#: 硬编码日期只是"记录载荷"，且日期恒在过去（单调安全）；
#: 断言对象是并发/句柄语义，不是「今天 vs 历史」边界。
#: 证据：逐函数提取 rotate 实参 —— L686 / L708 / L906 / L1053 / L1087 / L1113 全为 force=True。
_ROTATE_CONCURRENCY_REASON = (
    "实测该函数只以 rotate(force=True…) 轮转（跳过按日阈值判定）；"
    "硬编码日期仅作记录载荷且恒在过去（单调安全），"
    "断言的是并发/句柄语义而非「今天 vs 历史」边界。证据行：%s")

for _fn, _line in (
    ("test_快照之后的并发追加被并入而非覆盖", "L686"),
    ("test_分片写入期间的并发追加_放弃替换而不覆盖", "L708"),
    ("test_并发显式轮转与_append_不丢记录", "L906"),
    ("test_轮转关闭时手动_force_轮转后_append_仍可被读到", "L1053"),
    ("test_轮转开启时_手动_force_轮转后_append_仍可被读到", "L1087"),
    ("test_代际变化导致句柄重开", "L1113"),
):
    ALLOWLIST[("tests/unit/test_decision_log_rotation.py", _fn)] = (
        _ROTATE_CONCURRENCY_REASON % _line)

#: 上游粗筛（**不能带 ^**：search 会锚在全文开头，会把所有文件静默跳过）
_ISO_ANY = re.compile(r"20\d\d-\d\d-\d\d")
#: 逐字面量判定（match ⇒ 锚在该字面量开头）
_ISO_LITERAL = re.compile(r"^20\d\d-\d\d-\d\d")

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TESTS_ROOT = _REPO_ROOT / "tests"
_THIS_FILE = Path(__file__).resolve()


# ════════════════════════════════════════════════════════════════════
#  检测器
# ════════════════════════════════════════════════════════════════════


def _iso_literals_in(node: ast.AST) -> List[str]:
    """函数体内出现的硬编码 ISO 日期字面量"""
    found: List[str] = []
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            if _ISO_LITERAL.match(n.value):
                found.append(n.value)
    return found


def _boundary_calls_in(node: ast.AST) -> List[str]:
    """函数体内调用的今日边界 API 名（含 obj.method 形态）"""
    found: List[str] = []
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            name = getattr(n.func, "id", None) or getattr(n.func, "attr", None)
            if name in CLOCK_BOUNDARY_APIS:
                found.append(name)
    return found


def detect(source: str) -> List[Tuple[str, Tuple[str, ...], Tuple[str, ...]]]:
    """扫描一段源码，返回 [(函数名, 边界API, 硬编码日期), ...]"""
    tree = ast.parse(source)
    hits = []
    for n in ast.walk(tree):
        if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        apis = _boundary_calls_in(n)
        if not apis:
            continue
        dates = _iso_literals_in(n)
        if dates:
            hits.append((n.name, tuple(sorted(set(apis))), tuple(sorted(set(dates)))))
    return hits


def scan_tests() -> List[Tuple[str, str, Tuple[str, ...], Tuple[str, ...]]]:
    """扫描 tests/** 下全部 .py，返回 [(相对路径, 函数, API, 日期), ...]"""
    found = []
    for p in sorted(_TESTS_ROOT.rglob("*.py")):
        if p.resolve() == _THIS_FILE:      # 本文件内含"炸弹样例"字符串，排除自身
            continue
        try:
            src = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if not _ISO_ANY.search(src):       # 粗筛（快速跳过绝大多数文件）
            continue
        try:
            hits = detect(src)
        except SyntaxError:
            continue
        rel = p.relative_to(_REPO_ROOT).as_posix()
        for fn, apis, dates in hits:
            found.append((rel, fn, apis, dates))
    return found


# ════════════════════════════════════════════════════════════════════
#  守卫
# ════════════════════════════════════════════════════════════════════


def test_no_hardcoded_absolute_dates_in_clock_boundary_tests():
    """**主守卫**：读真实时钟的今日边界测试里，不得用硬编码绝对日期表达"今天"

    修法（不要放宽断言）：把日期改为相对真实今天推导（并与被测实现的**时区口径一致**
    —— ``archive_daily_file`` 用 ``date.today()`` 即**本地时区**）；或按 §三 选择
    给被测 API 增加**可选**时钟注入参数。**禁止**只把硬编码日期往后挪一天。
    """
    unexpected = [
        (rel, fn, apis, dates)
        for rel, fn, apis, dates in scan_tests()
        if (rel, fn) not in ALLOWLIST
    ]
    if unexpected:
        lines = [
            f"  · {rel}::{fn}  边界API={list(apis)}  硬编码日期={list(dates)[:3]}"
            for rel, fn, apis, dates in unexpected
        ]
        pytest.fail(
            "发现「用硬编码绝对日期表达今天」的写法（日期定时炸弹复发风险）：\n"
            + "\n".join(lines)
            + "\n\n该写法今天通过、明天必然失败（实测见本文件 docstring）。"
            "\n请改为相对 `date.today()` 推导（注意与实现**同一时区口径**），"
            "或给被测 API 增加可选时钟注入参数；"
            "若确认只是惰性固定时间戳，请登记到本文件 ALLOWLIST 并写明理由。"
        )


def test_detector_catches_the_original_bomb_shape():
    """**防假绿自检**：检测器必须能命中原炸弹（否则守卫会安静地退化成空转）

    样例取自 ``a4bedd0a`` 的 ``test_s801_warm_archive_shards_stay_visible`` 原写法。
    """
    sample = (
        "def test_s801_warm_archive_shards_stay_visible(tmp_path):\n"
        "    from agent.skills_mgmt.log_archiver import archive_daily_file\n"
        "    lines = [\n"
        "        _raw_line('2026-09-10T10:00:00+00:00', 'deny', 'd10'),\n"
        "        _raw_line('2026-09-11T10:00:00+00:00', 'allow', 'd11'),\n"
        "        _raw_line('2026-09-13T10:00:00+00:00', 'ask', 'd13'),\n"
        "    ]\n"
        "    result = archive_daily_file(tmp_path / 'decisions.jsonl')\n"
        "    assert result['archived'] == 2\n"
    )
    hits = detect(sample)
    assert hits, "检测器未能命中原炸弹写法 —— 守卫已退化为空转（假绿）"
    assert hits[0][0] == "test_s801_warm_archive_shards_stay_visible"
    assert "archive_daily_file" in hits[0][1]
    assert "2026-09-13T10:00:00+00:00" in hits[0][2]


def test_detector_ignores_inert_fixture_dates():
    """**反向对照**：纯"惰性固定时间戳"（不调边界 API）不得被误报

    误报会让守卫被绕过或关掉，等于没有守卫。
    """
    inert = (
        "def test_sorting_by_ts(tmp_path):\n"
        "    recs = [DecisionRecord(ts='2026-09-02T10:00:00+08:00'),\n"
        "            DecisionRecord(ts='2026-09-01T10:00:00+08:00')]\n"
        "    assert [r.ts for r in sort_records(recs)] == ['2026-09-01T10:00:00+08:00',\n"
        "                                                  '2026-09-02T10:00:00+08:00']\n"
    )
    assert detect(inert) == [], "惰性固定时间戳被误报 ⇒ 守卫会产生噪声"


def _reads_clock_transitively(src: str, tree: ast.AST, entry: str,
                              max_depth: int = 4) -> bool:
    """`entry` 是否**经由调用链**读到真实时钟（同模块内 BFS，深度上限 max_depth）

    【为什么必须沿调用链】`DecisionLog.rotate` 自己**不**读时钟，它经
    `_configured_rule` → `_has_old_day_records`（`date.today()`）才碰到"今天"。
    只查一跳会把这条登记项误报成"已腐烂"，从而逼着人删掉真实覆盖。
    """
    funcs = {n.name: n for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    seen: Set[str] = set()
    stack: List[Tuple[str, int]] = [(entry, 0)]
    while stack:
        name, depth = stack.pop()
        if name in seen or depth > max_depth:
            continue
        seen.add(name)
        fn = funcs.get(name)
        if fn is None:
            continue
        if any(m in (ast.get_source_segment(src, fn) or "") for m in _CLOCK_MARKERS):
            return True
        for c in ast.walk(fn):
            if isinstance(c, ast.Call):
                callee = getattr(c.func, "id", None) or getattr(c.func, "attr", None)
                if callee:
                    stack.append((callee, depth + 1))
    return False


def test_registered_apis_still_read_the_clock():
    """**防腐烂自检**：登记的 API 若被改名/改成可注入，"规则"会变成永不命中的空转

    断言每条登记项在源码里**确实仍读时钟**（沿调用链，见
    :func:`_reads_clock_transitively`）。
    """
    stale: List[str] = []
    for api, (mod_rel, reason) in CLOCK_BOUNDARY_APIS.items():
        mod = _REPO_ROOT / mod_rel
        assert mod.exists(), f"登记表指向的模块不存在：{mod_rel}（{api}）"
        src = mod.read_text(encoding="utf-8", errors="ignore")
        try:
            tree = ast.parse(src)
        except SyntaxError:  # pragma: no cover
            stale.append(f"{api}: {mod_rel} 无法解析")
            continue
        names = {n.name for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        if api not in names:
            stale.append(f"{api}: 在 {mod_rel} 中已不存在（改名/删除？）依据={reason}")
            continue
        if not _reads_clock_transitively(src, tree, api):
            stale.append(
                f"{api}: {mod_rel} 的调用链上已**读不到时钟** —— "
                f"该登记项已失效（依据={reason}），请重新核定登记表")
    if stale:
        pytest.fail("今日边界登记表已腐烂（守卫会退化成空转）：\n  · " + "\n  · ".join(stale))


def test_scan_covers_a_meaningful_number_of_iso_files():
    """**防自欺自检**：粗筛必须真的扫到"含硬编码日期"的文件（防止筛选条件写错导致 0 命中）

    实现期实测教训：粗筛正则曾误用 ``^`` 锚定，导致**所有文件被静默跳过**、
    命中数恒为 0 —— 一个"永远绿"的假守卫。故此处对扫描面本身下界断言。
    """
    iso_files: Set[str] = set()
    for p in sorted(_TESTS_ROOT.rglob("*.py")):
        if p.resolve() == _THIS_FILE:
            continue
        try:
            src = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if _ISO_ANY.search(src):
            iso_files.add(p.relative_to(_REPO_ROOT).as_posix())
    assert len(iso_files) >= 100, (
        f"粗筛只命中 {len(iso_files)} 个含硬编码日期的测试文件，明显偏少 ⇒ "
        f"筛选条件可能写错（历史实测约 190 个）")


# ════════════════════════════════════════════════════════════════════
#  可复算入口：python tests/unit/test_date_bomb_guard.py --scan
#  （量化清单的扫描命令；无需 pytest，直接打印命中与放行）
# ════════════════════════════════════════════════════════════════════

if __name__ == "__main__":  # pragma: no cover
    import sys as _sys

    if "--scan" not in _sys.argv:
        print("用法: python tests/unit/test_date_bomb_guard.py --scan")
        _sys.exit(2)
    _iso_files = [p for p in _TESTS_ROOT.rglob("*.py")
                  if p.resolve() != _THIS_FILE
                  and _ISO_ANY.search(p.read_text(encoding="utf-8", errors="ignore"))]
    _hits = scan_tests()
    _allowed = [h for h in _hits if (h[0], h[1]) in ALLOWLIST]
    _unexpected = [h for h in _hits if (h[0], h[1]) not in ALLOWLIST]
    print(f"扫描 tests/** 下 .py 文件总数        : "
          f"{len(list(_TESTS_ROOT.rglob('*.py')))}")
    print(f"含硬编码 ISO 日期字面量的文件         : {len(_iso_files)}")
    print(f"「调用今日边界 API 且含硬编码日期」命中: {len(_hits)}")
    print(f"  ├─ 已裁定为惰性（ALLOWLIST）        : {len(_allowed)}")
    print(f"  └─ 未裁定（守卫会失败）             : {len(_unexpected)}")
    for rel, fn, apis, dates in _unexpected:
        print(f"      · {rel}::{fn} api={list(apis)} dates={list(dates)[:3]}")
    for rel, fn, apis, dates in _allowed:
        print(f"      √ {rel}::{fn} api={list(apis)} dates={list(dates)[:3]}")
    _sys.exit(1 if _unexpected else 0)
