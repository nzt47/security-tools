# -*- coding: utf-8 -*-
"""审计可信度巡检 — D3 四道门（只读，退出码 0=PASS / 1=FAIL）

【为什么需要它】主报告 §1.5 的 **S1**（确认门豁免名单正在放行 L2 工具）与 **S7**
（审计库含测试垃圾）都是"**已发生、且可复发**"的治理缺陷；§3 风险预案表的
「**确认门豁免漂移**」一行要求的预案正是「豁免变更强制入链 + **每日巡检**」。

- A2 已把 `data/ui_settings.json` 覆盖层里的 `CP_TOOL_CONFIRM_LEVEL_EXEMPT`
  清空，并加了「变更入链」护栏（`action=tool.confirm.exempt_changed`，
  `agent/tool_gate.py::_record_exempt_change_if_needed`）；
- **但没有任何周期性检查**去发现「它又被设回来了」或「名单里被塞进 L2/L3 工具」。
  本次审计之所以要等人工审计才发现 `fan_out`（L2 / risk=high / effect=execute）
  被豁免，根因就是**无巡检**。

四道门（任一 FAIL ⇒ 退出码 1；WARN 不改变退出码）：

  G1 豁免漂移   读覆盖层 `data/ui_settings.json` 的 `CP_TOOL_CONFIRM_LEVEL_EXEMPT`，
                解析工具名 → 用 `agent.lines.load_tool_meta`（**唯一真相源**，与
                闸门/能力清单同源）逐个查确认级：
                  含 **L2 / L3** ⇒ **FAIL**（列出 哪个工具 / 什么级别 / 为什么危险）
                  仅含 L0 / L1 ⇒ WARN（存在豁免但不阻塞）
                  空 / 缺失     ⇒ PASS
                （名单项解析不出工具时记 WARN：它对闸门无实际效果，但意图需核实）
  G2 链完整性   调既有 `AuditChain.reader(...).verify_chain()`（**只读**）全链重算；
                断链 / 读不到台账 ⇒ FAIL。
  G3 日根覆盖   报告「有记录的 UTC 日」与「有 Merkle 日根的日」的**差集**；
                缺失 ⇒ **WARN**（已知历史事实，不是新损坏）；
                并对**最近 N 个（默认 2）有根的日**逐个 `verify_daily_root`，
                失败 ⇒ **FAIL**。
  G4 口径卫生   按可配置模式统计链上的**非业务 action**（测试/探针遗留；
                链只追加 ⇒ 历史无法删，只能被定期 report 出来）；
                占比超阈值 ⇒ WARN。

【G3 的 FAIL 口径为什么不止"最近 N 天"】除窗口内的日，历史日根也全部重放：
  · 任一日的失败原因属**篡改类**（`entry_hash_mismatch` / `root_chain_broken` /
    `signature_invalid`）⇒ 一律 FAIL——那是**此刻**的完整性问题，
    不是"封印后被回填"的历史数据形态；
  · 非篡改类（`root_hash_mismatch` / `leaf_count_mismatch`）且落在窗口外时，
    允许至多 `--max-known-bad-roots`（默认 1）天：本仓当前恰好有 1 天
    （2026-09-21）属此形态，**修它要重算日根（高风险，超出本卡范围）**。
    用**数量**而非"日期白名单"作界，是为了不制造第二份会腐烂的豁免清单：
    一旦**新增**一天失败，计数即越界 ⇒ FAIL。要零容忍用
    `--max-known-bad-roots 0`（当前仓库会红，直到 09-21 重新封存）。

【只读保证（本卡铁律：不改 data/、不写审计链、不删任何行）】
  · 只构造 `AuditChain.reader(...)`（`role='reader'`：不占单写者登记、不启
    writer 线程、不写预留日志、不取跨进程锁），并显式 `signing_enabled=False`
    ⇒ **不会生成签名私钥文件**；
  · 台账**不存在时直接报 FAIL 且不创建**（`AuditChain._init_db` 在 reader 角色下
    仍会建库文件，故这层守卫只能由脚本自己做）；
  · 覆盖层只经 `OverrideStore.get()` 读；G3/G4 的 SQL 走 `mode=ro` 只读连接；
  · 不 import `agent.audit.facade`、不调用 `append`/`record`、不写任何文件。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from agent.audit.chain import (  # noqa: E402
    DEFAULT_ROOTS_PATH,
    AuditChain,
)

#: 豁免名单所在的开关名（与 `agent/tool_gate.py::CONFIRM_LEVEL_EXEMPT_ENV`、
#: `agent/tool_exemptions.py::EXEMPT_SETTING_KEY` **同一个字符串**；三处对拍由
#: `tests/unit/test_tool_exemptions.py` 锁死，此处不新造第四份口径）
EXEMPT_SETTING_KEY = "CP_TOOL_CONFIRM_LEVEL_EXEMPT"

#: 覆盖层默认路径（相对仓库根；与 `agent/settings/overrides.py` 同源常量）
DEFAULT_SETTINGS_REL = os.path.join("data", "ui_settings.json")

#: 需要 FAIL 的确认级（L2=逐次确认、L3=默认禁止；被豁免 = 这一级的要求被取消）
DANGEROUS_LEVELS = ("L2", "L3")

#: 非业务 action 的默认匹配模式（Q7 §1.3 实测的那批：global_test_action / x.y /
#: ssrf_probe_test）。刻意写成**可配置**：口径变了改参数，不改代码。
DEFAULT_JUNK_PATTERN = r"(?i)(?:^|[._-])(?:test|tests|probe)(?:[._-]|$)|^x\.y$|global_test"

#: 篡改类失败原因（与"封印后被回填"的数据形态区分开；见模块 docstring）
TAMPER_REASONS = frozenset({
    "entry_hash_mismatch", "root_chain_broken", "signature_invalid",
    "seal_metadata_mismatch", "root_not_found",
})

DEFAULT_DAILY_ROOT_WINDOW = 2
DEFAULT_MAX_KNOWN_BAD_ROOTS = 1
DEFAULT_JUNK_RATIO_THRESHOLD = 0.001      # 千分之一
DEFAULT_JUNK_SAMPLES = 8


# ============================================================
#  报告收集（0=PASS / 1=FAIL；与 scripts/verify_index_drift.py 同形）
# ============================================================

class Report:
    """收集 NOTE / WARN / FAIL 并决定退出码"""

    def __init__(self) -> None:
        self.fails: List[str] = []
        self.warns: List[str] = []
        self.notes: List[str] = []
        self.gates: Dict[str, str] = {}

    def fail(self, msg: str) -> None:
        self.fails.append(msg)

    def warn(self, msg: str) -> None:
        self.warns.append(msg)

    def note(self, msg: str) -> None:
        self.notes.append(msg)

    def gate(self, name: str, verdict: str) -> None:
        self.gates[name] = verdict

    def emit(self, *, as_json: bool = False, root: str = "") -> int:
        print("")
        print("=== 结论 ===")
        for name in sorted(self.gates):
            print("  %-4s %s" % (name, self.gates[name]))
        for line in self.warns:
            print("WARN: " + line)
        for line in self.fails:
            print("FAIL: " + line)
        code = 1 if self.fails else 0
        print("%s（FAIL=%d WARN=%d）" % ("PASS" if code == 0 else "FAIL",
                                         len(self.fails), len(self.warns)))
        if as_json:
            print(json.dumps({"root": root, "exit_code": code,
                              "gates": self.gates, "fails": self.fails,
                              "warns": self.warns, "notes": self.notes},
                             ensure_ascii=False))
        return code


def _print(text: str = "") -> None:
    print(text)


# ============================================================
#  G1 —— 豁免漂移
# ============================================================

def parse_names(raw: Any) -> List[str]:
    """逗号分隔名单 → 去重后的工具名（保持书写顺序；空 ⇒ []）

    口径与 `agent/tool_exemptions.py::exempt_names` 逐字一致（去空白、忽略空段、
    保序去重）——本函数只是那份口径的**只读副本**，不引入新的解析规则。
    """
    out: List[str] = []
    for part in str(raw or "").split(","):
        text = part.strip()
        if text and text not in out:
            out.append(text)
    return out


def read_override_record(settings_path: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """读覆盖层里 `CP_TOOL_CONFIRM_LEVEL_EXEMPT` 那一条 ⇒ (记录 | None, 读取途径)

    优先用 `agent.settings.overrides.OverrideStore`（覆盖层的**唯一**读写入口，
    自带 mtime 缓存与格式裁定）；它不可用时退回直接解析该 JSON 的 `overrides` 段
    —— 两条路径都只读，且读的是**同一个文件、同一个键**。
    """
    note = ""
    try:
        from agent.settings.overrides import OverrideStore  # noqa: PLC0415
        rec = OverrideStore(settings_path).get(EXEMPT_SETTING_KEY)
        if rec is None:
            return None, "OverrideStore（agent.settings.overrides）"
        return ({"raw": "" if rec.value is None else str(rec.value),
                 "actor": str(rec.actor or ""), "risk": str(rec.risk or ""),
                 "updated_at": str(rec.updated_at or ""),
                 "previous": rec.previous if rec.previous is not None else ""},
                "OverrideStore（agent.settings.overrides）")
    except Exception as e:  # noqa: BLE001 开关层不可用 ⇒ 退回直读（不静默跳过检查）
        note = "直读 JSON（OverrideStore 不可用: %s: %s）；" % (type(e).__name__, e)
    try:
        with open(settings_path, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except FileNotFoundError:
        return None, note + "覆盖层文件不存在"
    except Exception as e:  # noqa: BLE001 读不到 ⇒ 调用方按"未知"处置
        return None, note + "覆盖层不可读: %s: %s" % (type(e).__name__, e)
    item = (doc.get("overrides") or {}).get(EXEMPT_SETTING_KEY) if isinstance(doc, dict) else None
    if not isinstance(item, dict):
        return None, note
    return ({"raw": "" if item.get("value") is None else str(item.get("value")),
             "actor": str(item.get("actor") or ""), "risk": str(item.get("risk") or ""),
             "updated_at": str(item.get("updated_at") or ""),
             "previous": item.get("previous") if item.get("previous") is not None else ""},
            note)


def _load_meta() -> Dict[str, Any]:
    """工具元数据（唯一真相源：`agent.lines.load_tool_meta`）

    与 `agent/tool_gate.py` / `agent/tool_exemptions.py` 读的是**同一个入口**
    ⇒ 巡检报出的确认级与闸门执行时判定的级别同源，不会出现第二套分级。
    """
    from agent.lines import load_tool_meta  # noqa: PLC0415 惰性：要读 91 个 YAML
    return dict(load_tool_meta() or {})


def _name_candidates(name: str) -> List[str]:
    """一个名单项的全部候选工具名（原名 / 小写 / 末段）

    与闸门 `agent/tool_gate.py::_query_keys` 的宽容口径对齐：名单里写
    `delegate` 还是 `cp.builtin.delegate` 都指同一个工具，巡检必须同样认得，
    否则会出现"闸门豁免了、巡检说查不到"的假绿。
    """
    raw = str(name or "").strip()
    if not raw:
        return []
    keys = [raw, raw.lower()]
    if "." in raw:
        keys.append(raw.rsplit(".", 1)[1].strip().lower())
    out: List[str] = []
    for k in keys:
        if k and k not in out:
            out.append(k)
    return out


def resolve_tool(name: str, metas: Dict[str, Any]) -> Optional[Any]:
    """名单项 → `ToolMeta`（解析不出 ⇒ None）"""
    lowered = {str(k).lower(): v for k, v in metas.items()}
    for key in _name_candidates(name):
        if key in metas:
            return metas[key]
        if key in lowered:
            return lowered[key]
    return None


def check_exempt_drift(rep: Report, *, settings_path: str,
                       tool_meta: Optional[Dict[str, Any]] = None,
                       effective: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """G1：豁免名单里有没有 L2/L3 工具

    Args:
        settings_path: 覆盖层文件（`data/ui_settings.json`）。
        tool_meta:     注入用（默认 `load_tool_meta()`）；测试传假表。
        effective:     注入用（默认 `agent.tool_exemptions.effective_value()`，
                       即开关中心解析出的**生效值**）；测试传空字典以免依赖环境。

    Returns:
        `{"override_raw", "override_meta", "effective_raw", "effective_source",
         "names": [...], "verdict"}`
    """
    _print("[G1] 确认门豁免漂移：覆盖层 %s 的 %s" % (settings_path, EXEMPT_SETTING_KEY))
    rec, how = read_override_record(settings_path)
    override_raw = "" if rec is None else str(rec.get("raw") or "")
    _print("     覆盖层读取途径: %s" % how)
    if rec is None:
        _print("     覆盖层: 键不存在（A2 清空后的预期状态）")
    else:
        _print("     覆盖层: value=%r actor=%s updated_at=%s risk=%s previous=%r"
               % (override_raw, rec.get("actor"), rec.get("updated_at"),
                  rec.get("risk"), rec.get("previous")))

    if effective is None:
        try:
            from agent.tool_exemptions import effective_value  # noqa: PLC0415
            effective = dict(effective_value() or {})
        except Exception as e:  # noqa: BLE001 解析器不可用 ⇒ 退回 env（如实标注）
            effective = {"raw": str(os.environ.get(EXEMPT_SETTING_KEY, "") or ""),
                         "source": "env(直读)",
                         "note": "开关解析器不可用: %s: %s" % (type(e).__name__, e)}
    eff_raw = str(effective.get("raw") or "")
    eff_source = str(effective.get("source") or "")
    _print("     生效值（开关中心解析）: %r 来源=%s%s"
           % (eff_raw, eff_source,
              ("｜" + str(effective["note"])) if effective.get("note") else ""))

    # 两个来源都要看：覆盖层是"谁改的、什么时候改的"的证据，**生效值才是闸门真读的**。
    names_override = parse_names(override_raw)
    names_effective = parse_names(eff_raw)
    ordered: List[str] = []
    for n in names_override + names_effective:
        if n not in ordered:
            ordered.append(n)

    result: Dict[str, Any] = {
        "override_raw": override_raw, "override_meta": rec or {},
        "effective_raw": eff_raw, "effective_source": eff_source,
        "names": [], "verdict": "PASS",
    }
    if not ordered:
        _print("     名单为空 ⇒ 无豁免（G1 PASS）")
        rep.gate("G1", "PASS 豁免名单为空/缺失")
        return result

    metas = tool_meta if tool_meta is not None else _load_meta()
    danger: List[str] = []
    warn_items: List[str] = []
    for name in ordered:
        meta = resolve_tool(name, metas)
        sources = []
        if name in names_override:
            sources.append("覆盖层")
        if name in names_effective:
            sources.append("生效值")
        if meta is None:
            _print("     · %-24s 级别=??（未登记：工具清单里没有这个名字/能力 id）" % name)
            warn_items.append("%s（名单里但工具清单中不存在 ⇒ 对闸门无实际效果，需核实意图）" % name)
            result["names"].append({"name": name, "level": "", "tool": None,
                                    "sources": sources})
            continue
        level = str(getattr(meta, "effective_confirm_level", "") or "")
        plane = str(getattr(meta, "plane", "") or "")
        effect = str(getattr(meta, "effect", "") or "")
        risk = str(getattr(meta, "risk", "") or "")
        semantics = str(getattr(meta, "confirm_level_semantics", "") or "")
        _print("     · %-24s 级别=%-3s 平面=%-8s 作用=%-8s 风险=%-8s 来源=%s"
               % (name, level or "??", plane, effect, risk, "+".join(sources) or "-"))
        _print("       语义: %s" % semantics)
        result["names"].append({"name": name, "level": level,
                                "tool": str(getattr(meta, "name", name)),
                                "plane": plane, "effect": effect, "risk": risk,
                                "sources": sources})
        if level in DANGEROUS_LEVELS:
            why = ("L2 = 逐次确认（每次都要人点，且单次有效）被整条取消 ⇒ 该工具不再进"
                   "审批收件箱，模型可**自动路由执行**"
                   if level == "L2" else
                   "L3 = 默认禁止（仅显式预授权可执行）被整条取消 ⇒ 本应只有 SA+scope "
                   "预授权才能跑的能力，变成常规放行")
            if effect in ("execute", "write", "extend"):
                why += "；且 effect=%s（会改变世界，不只是读）" % effect
            if risk in ("high", "critical"):
                why += "；risk=%s" % risk
            danger.append("工具 %s 级别=%s（plane=%s effect=%s risk=%s）来源=%s ⇒ %s"
                          % (name, level, plane, effect, risk,
                             "+".join(sources) or "-", why))
        else:
            warn_items.append("%s 级别=%s（plane=%s effect=%s risk=%s）被豁免；"
                              "L0/L1 不阻塞，但它说明有人显式改过确认门，需确认是否仍需要"
                              % (name, level or "??", plane, effect, risk))

    if danger:
        result["verdict"] = "FAIL"
        _print("     危险豁免 %d 项 ⇒ G1 FAIL" % len(danger))
        for d in danger:
            rep.fail("G1 豁免名单含 L2/L3 工具：%s" % d)
    else:
        result["verdict"] = "WARN"
        _print("     仅 L0/L1 或未登记项 ⇒ G1 WARN（存在豁免但不阻塞）")
        for w in warn_items:
            rep.warn("G1 %s" % w)
    rep.gate("G1", "%s 豁免名单=%s" % (result["verdict"], ",".join(ordered)))
    return result


# ============================================================
#  G2 —— 链完整性
# ============================================================

def open_reader(db_path: str, roots_path: str) -> AuditChain:
    """打开**只读**审计链实例（台账不存在的守卫在调用方，本函数不创建文件）

    `signing_enabled=False` 是硬要求：`RootsSigner` 在 enabled 时若密钥文件
    不存在会**现场生成并落盘**（`chain.py:722-731`）—— 巡检绝不能写盘。
    """
    chain = AuditChain.reader(db_path, roots_path=roots_path, signing_enabled=False)
    if chain.role != "reader":                      # 防守：只读是硬约束
        raise RuntimeError("巡检实例必须是 reader 角色，得到 %r" % chain.role)
    return chain


def check_chain_integrity(rep: Report, *, db_path: str,
                          chain: Optional[AuditChain] = None) -> Dict[str, Any]:
    """G2：`verify_chain()` 全链重算（只读），断链 ⇒ FAIL"""
    _print("[G2] 审计链完整性（verify_chain 全链重算）: %s" % db_path)
    if chain is None:
        if not os.path.exists(db_path):
            _print("     台账不存在 ⇒ 无法校验（本脚本**不创建**台账）")
            rep.fail("G2 审计台账不存在：%s（服务从未启动过才属预期）" % db_path)
            rep.gate("G2", "FAIL 台账缺失")
            return {"ok": False, "reason": "db_missing", "checked": 0}
        chain = open_reader(db_path, DEFAULT_ROOTS_PATH)
    head = chain.chain_head()
    _print("     条数=%s seq=%s..%s 链头=%s… 末条时间=%s"
           % (head.get("count"), head.get("first_seq"), head.get("last_seq"),
              str(head.get("head_self_hash") or "")[:16], head.get("last_ts")))
    t0 = time.time()
    v = chain.verify_chain()
    _print("     重算 %s 条，耗时 %.2fs ⇒ ok=%s reason=%s %s"
           % (v.checked, time.time() - t0, v.ok, v.reason or "ok", v.detail))
    result = {"ok": bool(v.ok), "reason": str(v.reason), "checked": int(v.checked),
              "detail": str(v.detail), "head": head,
              "degraded": bool(head.get("degraded"))}
    if not v.ok:
        rep.fail("G2 审计链断链/被篡改：reason=%s detail=%s（首个篡改点 seq=%s）"
                 % (v.reason, v.detail, getattr(v, "first_bad_seq", None)))
        rep.gate("G2", "FAIL %s" % v.reason)
    else:
        rep.gate("G2", "PASS 全链 %s 条重算一致" % v.checked)
        if head.get("degraded"):
            rep.warn("G2 台账处于降级状态（degraded=True）：读到的可能不是全部记录")
    return result


# ============================================================
#  G3 —— 日根覆盖
# ============================================================

def _ro_connect(db_path: str) -> sqlite3.Connection:
    """`mode=ro` 只读连接（巡检不写台账，连 -wal/-shm 都不该由本脚本创建）"""
    uri = "file:%s?mode=ro" % db_path.replace(os.sep, "/")
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    return con


def _day_counts(db_path: str) -> Dict[str, int]:
    """各 UTC 日的记录条数（**只读**连接；口径 == `chain.day_of_ts`）

    `day_of_ts` 的定义就是 `str(ts)[:10]`（`chain.py:205-207`），故
    `substr(ts,1,10)` 与逐条 `day_of_ts` 恒等——这里用 SQL 聚合只是省掉把
    7 万行物化成 `AuditEntry` 的开销，不是换了口径。
    """
    con = _ro_connect(db_path)
    try:
        rows = con.execute("SELECT substr(ts,1,10) AS d, COUNT(*) AS n "
                           "FROM audit_chain GROUP BY d").fetchall()
        return {str(r["d"]): int(r["n"]) for r in rows}
    finally:
        con.close()


def check_daily_roots(rep: Report, *, chain: AuditChain, db_path: str,
                      window: int = DEFAULT_DAILY_ROOT_WINDOW,
                      max_known_bad: int = DEFAULT_MAX_KNOWN_BAD_ROOTS,
                      verify_all: bool = True) -> Dict[str, Any]:
    """G3：日根覆盖差集（WARN）+ 最近 N 个有根日重放（FAIL）"""
    _print("[G3] 每日 Merkle 根覆盖与重放校验")
    roots_all = chain.read_daily_roots()
    by_day: Dict[str, Any] = {}
    dup_days: List[str] = []
    for r in roots_all:
        if r.date in by_day:
            dup_days.append(r.date)      # 同日多条根：历史重复封存（实测 09-14 有 2 条）
        by_day[r.date] = r
    rooted = sorted(by_day)
    day_counts = _day_counts(db_path)
    recorded_days = sorted(day_counts)

    missing = [d for d in recorded_days if d not in by_day]
    orphan = [d for d in rooted if d not in day_counts]
    _print("     有记录的 UTC 日=%d 有日根的日=%d 日根记录=%d"
           % (len(recorded_days), len(rooted), len(roots_all)))
    _print("     有记录但无日根（差集 %d 天）: %s"
           % (len(missing), ", ".join("%s(%d)" % (d, day_counts[d]) for d in missing) or "无"))
    if orphan:
        _print("     有日根但无记录: %s" % ", ".join(orphan))
    if dup_days:
        _print("     同日多条根（后一条生效）: %s" % ", ".join(sorted(set(dup_days))))

    window_days = rooted[-max(1, int(window)):] if rooted else []
    _print("     重放窗口（最近 %d 个有根日）: %s"
           % (len(window_days), ", ".join(window_days) or "无"))
    checks: List[Dict[str, Any]] = []
    bad_window: List[Dict[str, Any]] = []
    bad_tamper: List[Dict[str, Any]] = []
    bad_history: List[Dict[str, Any]] = []
    for day in (rooted if verify_all else window_days):
        t0 = time.time()
        rv = chain.verify_daily_root(day)
        rec = by_day[day]
        item = {"date": day, "ok": bool(rv.ok), "reason": str(rv.reason),
                "detail": str(rv.detail), "entries": int(rv.entries_verified),
                "leaf_count": int(getattr(rec, "leaf_count", -1)),
                "in_window": day in window_days,
                "seconds": round(time.time() - t0, 3),
                "signature_ok": bool(rv.signature_ok),
                "chains_ok": bool(getattr(rv, "chains_ok", True))}
        checks.append(item)
        if not rv.ok:
            if item["in_window"]:
                bad_window.append(item)
            elif item["reason"] in TAMPER_REASONS:
                bad_tamper.append(item)
            else:
                bad_history.append(item)
    for item in checks:
        _print("     %s %s 叶子=%s（根记录 %s） 签名=%s 根链=%s %.2fs %s"
               % ("OK  " if item["ok"] else "BAD ", item["date"], item["entries"],
                  item["leaf_count"], item["signature_ok"], item["chains_ok"],
                  item["seconds"],
                  "" if item["ok"] else "reason=%s %s" % (item["reason"], item["detail"])))

    for item in bad_window:
        rep.fail("G3 最近有根日 %s 重放失败（reason=%s）：%s —— 封印路径正在产出错误日根"
                 % (item["date"], item["reason"], item["detail"]))
    for item in bad_tamper:
        rep.fail("G3 日根 %s 的失败原因属**篡改类**（reason=%s）：%s —— 与「封印后被"
                 "回填」的历史数据形态不同，需立即排查"
                 % (item["date"], item["reason"], item["detail"]))
    if len(bad_history) > max(0, int(max_known_bad)):
        for item in bad_history:
            rep.fail("G3 窗口外日根 %s 重放失败（reason=%s）：%s —— 已知历史缺陷上限 %d 天，"
                     "实际 %d 天 ⇒ 出现**新增**失败日"
                     % (item["date"], item["reason"], item["detail"],
                        max_known_bad, len(bad_history)))
    elif bad_history:
        for item in bad_history:
            rep.warn("G3 日根 %s 重放失败（reason=%s，叶子 %s ≠ 根记录 %s）：%s —— 属**已知"
                     "历史缺陷**（封印后又被回填；重算日根风险高，超出本卡范围，"
                     "处置见 D3 报告）；窗口外失败日 %d/%d 未越界"
                     % (item["date"], item["reason"], item["entries"],
                        item["leaf_count"], item["detail"],
                        len(bad_history), max_known_bad))
    if missing:
        rep.warn("G3 有记录的日缺 Merkle 日根 %d 天（已知历史事实，不阻塞）：%s"
                 % (len(missing), ", ".join("%s(%d)" % (d, day_counts[d]) for d in missing)))
    if not rooted:
        rep.warn("G3 一个日根都没有：每日封存从未运行过（封存链本身未验证）")

    failed = len(bad_window) + len(bad_tamper) + len(bad_history)
    verdict = "FAIL" if (bad_window or bad_tamper
                         or len(bad_history) > max(0, int(max_known_bad))) else "PASS"
    rep.gate("G3", "%s 日根 %d 天（通过 %d），缺根 %d 天，窗口失败 %d，历史失败 %d"
             % (verdict, len(rooted), len(rooted) - failed, len(missing),
                len(bad_window), len(bad_tamper) + len(bad_history)))
    return {"rooted_days": rooted, "recorded_days": recorded_days,
            "missing_days": missing, "orphan_days": orphan,
            "duplicate_root_days": sorted(set(dup_days)),
            "window": window_days, "checks": checks,
            "bad_window": bad_window, "bad_tamper": bad_tamper,
            "bad_history": bad_history, "verdict": verdict}


# ============================================================
#  G4 —— 统计口径卫生
# ============================================================

def _action_counts(db_path: str) -> Dict[str, int]:
    """链上各 action 的条数（**只读**连接，SQL 聚合）"""
    con = _ro_connect(db_path)
    try:
        rows = con.execute("SELECT action, COUNT(*) AS n FROM audit_chain "
                           "GROUP BY action").fetchall()
        return {str(r["action"]): int(r["n"]) for r in rows}
    finally:
        con.close()


def check_action_hygiene(rep: Report, *, db_path: str,
                         pattern: str = DEFAULT_JUNK_PATTERN,
                         ratio_threshold: float = DEFAULT_JUNK_RATIO_THRESHOLD,
                         samples: int = DEFAULT_JUNK_SAMPLES) -> Dict[str, Any]:
    """G4：非业务 action 的数量/占比/样例（超阈值 ⇒ WARN；**不 FAIL**）

    为什么只 WARN：链是**只追加**的，历史污染无法删除（Q7 §6 已定性为"永久化"）
    ⇒ 把历史遗留做成红灯，等于制造一个**恒红**的门（恒红的门等于没有门）。
    本门的价值在**增量**：占比越界说明有新的测试/探针在往生产链上写。
    """
    _print("[G4] 统计口径卫生（非业务 action）")
    try:
        rx = re.compile(pattern)
    except re.error as e:
        rep.fail("G4 非业务 action 模式非法（--junk-pattern）：%s" % e)
        rep.gate("G4", "FAIL 模式非法")
        return {"error": str(e)}
    counts = _action_counts(db_path)
    total = sum(counts.values())
    junk = {a: n for a, n in counts.items() if rx.search(a)}
    junk_total = sum(junk.values())
    ratio = (float(junk_total) / total) if total else 0.0
    _print("     模式=%s" % pattern)
    _print("     总记录=%d 不同 action=%d 非业务记录=%d 占比=%.4f%%（阈值 %.4f%%）"
           % (total, len(counts), junk_total, ratio * 100.0, ratio_threshold * 100.0))
    for a, n in sorted(junk.items(), key=lambda kv: -kv[1])[:max(1, int(samples))]:
        _print("     · %-32s %d 条（链只追加：这些记录**无法删除**）" % (a, n))
    if junk_total == 0:
        _print("     无非业务 action")
    result = {"total": total, "distinct_actions": len(counts),
              "junk_total": junk_total, "junk": junk, "ratio": ratio,
              "threshold": float(ratio_threshold)}
    if total == 0:
        rep.warn("G4 链上没有记录：口径统计无意义（空链）")
        rep.gate("G4", "WARN 空链")
        return result
    if ratio > float(ratio_threshold):
        rep.warn("G4 非业务 action 占比 %.4f%% 超阈值 %.4f%%（%d/%d 条）：链上新增了测试/"
                 "探针遗留，建议在写入口加 action 白名单（历史污染不可删）"
                 % (ratio * 100.0, float(ratio_threshold) * 100.0, junk_total, total))
        rep.gate("G4", "WARN 非业务=%d(%.4f%%)" % (junk_total, ratio * 100.0))
    else:
        rep.gate("G4", "PASS 非业务=%d(%.4f%%)" % (junk_total, ratio * 100.0))
    return result


# ============================================================
#  入口
# ============================================================

def find_root(explicit: Optional[str] = None) -> str:
    """定位仓库根（显式 --root > cwd > 脚本上级目录）"""
    candidates: List[str] = []
    if explicit:
        candidates.append(os.path.abspath(explicit))
    candidates.append(os.getcwd())
    candidates.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for cand in candidates:
        if (os.path.isdir(os.path.join(cand, "data", "tool_definitions"))
                and os.path.isdir(os.path.join(cand, "agent", "audit"))):
            return cand
    raise SystemExit("找不到仓库根：需含 data/tool_definitions 与 agent/audit（可用 --root 指定）")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="audit_governance_check.py",
        description="审计可信度巡检（D3 四道门：豁免漂移/链完整性/日根覆盖/口径卫生；只读）")
    p.add_argument("--root", default=None, help="仓库根（默认 cwd / 脚本上级目录）")
    p.add_argument("--db", default="", help="审计台账（默认 data/audit/audit_chain.db）")
    p.add_argument("--roots", default="", help="每日根文件（默认 data/audit/daily_roots.jsonl）")
    p.add_argument("--settings", default="", help="开关覆盖层（默认 data/ui_settings.json）")
    p.add_argument("--daily-root-window", type=int, default=DEFAULT_DAILY_ROOT_WINDOW,
                   help="重放窗口：最近 N 个有根的日（默认 2）；窗口内失败 ⇒ FAIL")
    p.add_argument("--max-known-bad-roots", type=int, default=DEFAULT_MAX_KNOWN_BAD_ROOTS,
                   help="窗口外允许的历史失败日数上限（默认 1；0 = 零容忍）")
    p.add_argument("--no-verify-all-roots", action="store_true",
                   help="只重放窗口内的日根（默认全部重放）")
    p.add_argument("--junk-pattern", default=DEFAULT_JUNK_PATTERN,
                   help="非业务 action 的匹配模式（默认 test/probe/x.y/global_test）")
    p.add_argument("--junk-ratio-threshold", type=float, default=DEFAULT_JUNK_RATIO_THRESHOLD,
                   help="非业务 action 占比阈值（默认 0.001 = 千分之一）")
    p.add_argument("--only", default="", help="只跑指定门（如 G1,G3；默认四门全跑）")
    p.add_argument("--json", action="store_true", help="额外输出一行 JSON 汇总（供 CI 消费）")
    return p


def _stat_probe(paths: Sequence[str]) -> Dict[str, str]:
    """(size, mtime_ns) 指纹；不存在记 "absent"（只读自检用，不改任何文件）"""
    out: Dict[str, str] = {}
    for p in paths:
        try:
            st = os.stat(p)
            out[p] = "%d/%d" % (st.st_size, st.st_mtime_ns)
        except OSError:
            out[p] = "absent"
    return out


def run(args: argparse.Namespace) -> int:
    root = find_root(args.root)
    db_path = args.db or os.path.join(root, "data", "audit", "audit_chain.db")
    roots_path = args.roots or os.path.join(root, "data", "audit", "daily_roots.jsonl")
    settings_path = args.settings or os.path.join(root, DEFAULT_SETTINGS_REL)
    only = {s.strip().upper() for s in str(args.only or "").split(",") if s.strip()}

    rep = Report()
    _print("仓库根: %s" % root)
    _print("台账: %s" % db_path)
    _print("日根: %s" % roots_path)
    _print("覆盖层: %s" % settings_path)
    _print("本进程 pid=%d（只读巡检：不写任何文件、不写审计链）" % os.getpid())

    probe_before = _stat_probe([db_path, roots_path, settings_path])

    chain: Optional[AuditChain] = None
    try:
        if "G1" in only or not only:
            check_exempt_drift(rep, settings_path=settings_path)

        if ("G2" in only or "G3" in only or not only) and os.path.exists(db_path):
            chain = open_reader(db_path, roots_path)
        if "G2" in only or not only:
            check_chain_integrity(rep, db_path=db_path, chain=chain)
        if "G3" in only or not only:
            if chain is None:
                _print("[G3] 跳过：台账不可用（见 G2）")
                rep.fail("G3 无法校验：审计台账不可用")
                rep.gate("G3", "FAIL 台账不可用")
            else:
                check_daily_roots(rep, chain=chain, db_path=db_path,
                                  window=args.daily_root_window,
                                  max_known_bad=args.max_known_bad_roots,
                                  verify_all=not args.no_verify_all_roots)
        if "G4" in only or not only:
            if chain is None and not os.path.exists(db_path):
                _print("[G4] 跳过：台账不存在")
                rep.gate("G4", "SKIP 台账不存在")
            else:
                check_action_hygiene(rep, db_path=db_path,
                                     pattern=args.junk_pattern,
                                     ratio_threshold=args.junk_ratio_threshold)
    finally:
        if chain is not None:
            try:
                chain.close(timeout=2.0)
            except Exception:  # noqa: BLE001 关闭失败不影响结论
                pass

    probe_after = _stat_probe([db_path, roots_path, settings_path])
    _print("")
    _print("[只读自检] 运行前后文件状态（size/mtime_ns）：")
    for path in probe_before:
        b, a = probe_before[path], probe_after[path]
        _print("     %-70s %s → %s  %s" % (path, b, a, "未变化" if b == a else "**已变化**"))
        if b != a:
            rep.note("只读自检：%s 在本脚本运行期间发生变化（本脚本只读；该变化来自"
                     "**其它写者**——本机审计链存在并发写入）" % path)

    return rep.emit(as_json=args.json, root=root)


def main(argv: Optional[List[str]] = None) -> int:
    try:                                  # 中文输出在 cp936 管道下不因编码抛错
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 老平台/被重定向时忽略
        pass
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except KeyboardInterrupt:  # pragma: no cover
        return 1
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001 CLI 兜底：巡检自身异常按 FAIL（不静默放过）
        print("FAIL: 巡检自身异常 %s: %s" % (type(e).__name__, e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
