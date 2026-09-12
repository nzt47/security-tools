"""TASK-S4-03 混沌演练（v7.2 §11.10）—— 可复跑的受控演练脚本

【为什么单独一个脚本而不是塞进 pytest】
    §11.10 的演练项含「kill -9 主进程」这类**真杀进程**的动作。任务书硬约束：
    "混沌演练（kill 进程）会中断本地服务 → 在受控环境执行并记录，**勿在生产/主工作区
    直接 kill**"。故本脚本：
      - 只 kill **自己 spawn 的子进程**（绝不碰运行中的服务/monkeypatch 宿主）；
      - 一切落盘在 `tempfile.mkdtemp()` 下（台账 / 审计库 / 锁文件），退出即弃；
      - 退出前清理子进程与临时目录。

【§11.10 清单 ↔ 本脚本覆盖】
    清单（每季度全项一遍）：
      kill -9 主进程 / 删最新快照 / 断网 10 分钟 / 磁盘写满 /
      向审计链注入一条篡改 / 上游返回畸形 JSON / kill Watchdog 本身（验证互watch）
    本任务（S4-03）覆盖其中与"熔断回滚与 Saga"直接相关的四项：
      D1 kill -9 主进程        → Watchdog 唯一性（P7.2-16）+ 杀伤后锁释放与陈旧判定
      D2 向审计链注入一条篡改   → 链式审计检出（S2-02 设施 + 本任务演练留证）
      D3 删最新快照            → 整包回滚到缺失包时**拒绝 + L4**（P7.2-15）
      D4 Saga 补偿失败         → 升级 L4 + 事故卡（§4.6）
    未覆盖项（断网 / 磁盘写满 / 畸形 JSON / kill Watchdog 本身）属**全季度清单**的其余项，
    不在本任务范围（如实登记于验收报告"未覆盖"节）。

【用法】
    python scripts/chaos_s4_03_drill.py            # 跑全部四项
    python scripts/chaos_s4_03_drill.py --only D1  # 只跑一项
    python scripts/chaos_s4_03_drill.py --json out.json --md out.md
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.self_healing import release_bundle as rb  # noqa: E402
from agent.self_healing import saga as sg  # noqa: E402
from agent.self_healing import watchdog_singleton as ws  # noqa: E402
from agent.self_healing.levels import (  # noqa: E402
    HealLevel,
    list_incidents,
    reset_levels_state,
)

# ────────────────────────────────────────────────────────────
#  演练记录
# ────────────────────────────────────────────────────────────


@dataclass
class DrillRecord:
    """单项演练的实测记录（命令 + 输出 + 恢复验证）"""

    drill_id: str
    title: str
    chaos: str                       # 注入的故障
    steps: List[Dict[str, Any]] = field(default_factory=list)
    expectations: List[Dict[str, Any]] = field(default_factory=list)
    recovery: List[Dict[str, Any]] = field(default_factory=list)
    passed: bool = True

    def step(self, name: str, detail: Any = "") -> None:
        self.steps.append({"ts": _now(), "name": name, "detail": detail})

    def expect(self, claim: str, ok: bool, evidence: Any = "") -> None:
        self.expectations.append({"claim": claim, "ok": bool(ok),
                                  "evidence": evidence})
        if not ok:
            self.passed = False

    def recover(self, name: str, ok: bool, evidence: Any = "") -> None:
        self.recovery.append({"name": name, "ok": bool(ok), "evidence": evidence})
        if not ok:
            self.passed = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "drill_id": self.drill_id, "title": self.title, "chaos": self.chaos,
            "passed": self.passed, "steps": self.steps,
            "expectations": self.expectations, "recovery": self.recovery,
        }


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# ────────────────────────────────────────────────────────────
#  D1：kill -9 主进程 → Watchdog 唯一性 + 杀伤后恢复
# ────────────────────────────────────────────────────────────

#: 子进程脚本：持有 Watchdog 单例锁 → 写 READY 文件 → 长睡（等被 kill -9）
_CHILD_SOURCE = textwrap.dedent(
    """
    import os, sys, time
    sys.path.insert(0, {root!r})
    from agent.self_healing.watchdog_singleton import WatchdogSingleton

    lock_path = sys.argv[1]
    ready_path = sys.argv[2]
    guard = WatchdogSingleton(lock_path=lock_path, role="watchdog").acquire()
    with open(ready_path, "w", encoding="utf-8") as fh:
        fh.write(str(os.getpid()))
    time.sleep(120)   # 等主演练脚本 kill -9
    """
)


def drill_d1_kill_watchdog(tmp: Path) -> DrillRecord:
    """D1：kill -9 Watchdog 持有者 → 唯一性成立 + OS 释放锁 + 陈旧判定 + 可重获"""
    record = DrillRecord(
        drill_id="D1",
        title="kill -9 主进程（Watchdog 持有者）→ 单机唯一性 + 杀伤后恢复",
        chaos="kill -9 持有 watchdog.lock 的子进程（模拟主进程崩溃）",
    )
    root = str(Path(__file__).resolve().parent.parent)
    lock_path = tmp / "d1" / "watchdog.lock"
    ready_path = tmp / "d1" / "ready.txt"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    child = subprocess.Popen(
        [sys.executable, "-c", _CHILD_SOURCE.format(root=root),
         str(lock_path), str(ready_path)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        cwd=root,
    )
    record.step("spawn", {"pid": child.pid, "cmd": "python -c <child watchdog holder>"})

    # 等子进程拿到锁（文件握手；不依赖管道捕获输出——受限环境下管道可能不可用）
    deadline = time.time() + 30
    while time.time() < deadline and not ready_path.exists():
        if child.poll() is not None:
            record.expect("子进程成功持有单例锁", False,
                          f"子进程提前退出 rc={child.returncode}")
            return record
        time.sleep(0.05)
    record.expect("子进程成功持有单例锁", ready_path.exists(),
                  {"lock_path": str(lock_path),
                   "holder": _safe_read_json(lock_path)})

    # ① 唯一性：第二个实例必须被拒（分裂脑防护 P7.2-16）
    try:
        ws.reset_watchdog_singleton()
        ws.WatchdogSingleton(lock_path=str(lock_path), register_singleton=False).acquire()
        record.expect("第二个 Watchdog 实例被拒（分裂脑防护）", False,
                      "第二个实例竟然拿到了锁")
    except ws.SplitBrainError as exc:
        record.expect("第二个 Watchdog 实例被拒（分裂脑防护）", True,
                      {"exception": type(exc).__name__,
                       "holder": exc.holder, "lock_path": exc.lock_path})
    except Exception as exc:  # noqa: BLE001
        record.expect("第二个 Watchdog 实例被拒（分裂脑防护）", False,
                      f"异常类型非预期: {type(exc).__name__}: {exc}")

    # ② 注入故障：kill -9
    record.step("chaos", {"action": "kill -9", "pid": child.pid})
    child.kill()
    child.wait(timeout=30)
    record.step("killed", {"pid": child.pid, "returncode": child.returncode})

    # ③ 恢复验证 1：OS 随进程消亡释放锁 → 新实例可重获
    time.sleep(0.5)
    reacquired = False
    guard = None
    try:
        ws.reset_watchdog_singleton()
        guard = ws.WatchdogSingleton(lock_path=str(lock_path),
                                     register_singleton=False).acquire()
        reacquired = guard.held
    except Exception as exc:  # noqa: BLE001
        record.recover("杀伤后新实例可重获单例锁", False,
                       f"{type(exc).__name__}: {exc}")
    if guard is not None:
        record.recover("杀伤后新实例可重获单例锁", reacquired,
                       {"held": guard.held, "holder": guard.holder().to_dict()})

    # ④ 恢复验证 2：陈旧锁判定（**须在本进程释放后再判**——持有中的锁不是"陈旧"）
    if guard is not None:
        guard.release()
    ws.reset_watchdog_singleton()
    stale_guard = ws.WatchdogSingleton(lock_path=str(lock_path),
                                       register_singleton=False)
    record.recover("杀伤后锁文件被判为陈旧（可安全清理）", stale_guard.is_stale(),
                   {"lockfile_holder": stale_guard.read_holder(),
                    "note": "字节 0 哨兵 + 定长身份槽；OS 锁随进程消亡释放"})

    record.step("cleanup", {"lock_released": True})
    return record


def _safe_read_json(path: Path) -> Dict[str, Any]:
    """读锁文件 JSON（损坏返回 {}；只读，不改）"""
    try:
        raw = Path(path).read_text(encoding="utf-8").strip("\0 \r\n\t")
        return json.loads(raw) if raw else {}
    except Exception:  # noqa: BLE001
        return {}


# ────────────────────────────────────────────────────────────
#  D2：向审计链注入一条篡改
# ────────────────────────────────────────────────────────────


def drill_d2_audit_tamper(tmp: Path) -> DrillRecord:
    """D2：向链式审计注入篡改 → 检出注入点（并分别验证两种注入的影响范围）

    【实现期实测校准（重要，勿照抄模块 docstring 的粗口径）】
        `verify_chain` 有两道独立的哈希：
          - `payload_hash`：**本条**载荷/元数据的绑定 —— 改 `payload` 只打掉这一道；
          - `self_hash`   ：**链级**绑定（前驱取重算值前向传播）—— 改"喂给 self_hash
                            的字段"（如 payload_hash / actor / subject）才会**连带**打掉
                            其后全部记录。
        故本演练分两个变体实测，如实记录两者的**不同影响范围**：
            D2a 篡改 payload            → 期望恰好 1 条异常（注入点）；
            D2b 篡改 payload_hash 字段  → 期望注入点及其后全部异常（哈希前向传播）。
    """
    record = DrillRecord(
        drill_id="D2",
        title="向审计链注入篡改 → 链式校验定位注入点（两种注入的影响范围分别验证）",
        chaos="直接 UPDATE 审计 SQLite（绕过写入 API）：D2a 改 payload；D2b 改 payload_hash",
    )
    db_path = tmp / "d2" / "audit.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    from agent.audit.chain import AuditChain, verify_chain

    chain = AuditChain(db_path=str(db_path))
    n = 8
    for index in range(1, n + 1):
        chain.append(action=f"chaos.drill.entry.{index}", actor="chaos",
                     subject=f"drill:{index}", payload={"index": index, "phase": "before"})
    chain.flush()
    record.step("append", {"entries": n, "db": str(db_path), "table": "audit_chain"})

    before = chain.verify_chain()
    record.expect("注入前链完整（基线可信）", before.ok,
                  {"checked": before.checked, "summary": before.summary()})
    chain.close()

    tamper_seq = 4

    # ── D2a：篡改 payload（只打掉 payload_hash 这道） ──
    conn = sqlite3.connect(str(db_path))
    try:
        cursor = conn.execute(
            "UPDATE audit_chain SET payload = ? WHERE seq = ?",
            (json.dumps({"index": tamper_seq, "phase": "TAMPERED"}, ensure_ascii=False),
             tamper_seq),
        )
        conn.commit()
        record.expect("D2a 篡改确实落到库中（影响行数=1）", cursor.rowcount == 1,
                      {"rowcount": cursor.rowcount, "seq": tamper_seq})
    finally:
        conn.close()
    record.step("chaos-D2a", {"action": "UPDATE payload", "seq": tamper_seq})

    reader = AuditChain.reader(db_path=str(db_path))
    try:
        after_a = verify_chain(reader.entries())
    finally:
        reader.close()
    bad_a = [row.get("seq") for row in (after_a.bad_seqs or [])]
    record.expect("D2a 篡改被检出（链不再完整）", not after_a.ok,
                  {"summary": after_a.summary()})
    record.expect("D2a 首个异常位置 == 注入点", after_a.first_bad_seq == tamper_seq,
                  {"first_bad_seq": after_a.first_bad_seq, "expected": tamper_seq})
    record.expect("D2a 影响范围 = 恰好注入点 1 条（payload 只绑定本条）",
                  bad_a == [tamper_seq],
                  {"bad_seqs": bad_a, "reason": after_a.reason})

    # 恢复：还原 payload → 链重新完整（证明检出归因于该条篡改，非噪声）
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("UPDATE audit_chain SET payload = ? WHERE seq = ?",
                     (json.dumps({"index": tamper_seq, "phase": "before"},
                                 ensure_ascii=False), tamper_seq))
        conn.commit()
    finally:
        conn.close()
    restored = AuditChain.reader(db_path=str(db_path))
    try:
        final_a = verify_chain(restored.entries())
    finally:
        restored.close()
    record.recover("D2a 还原 payload 后链恢复完整", final_a.ok,
                   {"checked": final_a.checked, "summary": final_a.summary()})

    # ── D2b：篡改 payload_hash 字段（喂给 self_hash ⇒ 引发前向传播） ──
    conn = sqlite3.connect(str(db_path))
    try:
        original_hash = conn.execute(
            "SELECT payload_hash FROM audit_chain WHERE seq = ?", (tamper_seq,)
        ).fetchone()[0]
        forged = "0" * len(str(original_hash))
        cursor = conn.execute(
            "UPDATE audit_chain SET payload_hash = ? WHERE seq = ?",
            (forged, tamper_seq))
        conn.commit()
        record.expect("D2b 篡改确实落到库中（影响行数=1）", cursor.rowcount == 1,
                      {"rowcount": cursor.rowcount, "seq": tamper_seq,
                       "field": "payload_hash"})
    finally:
        conn.close()
    record.step("chaos-D2b", {"action": "UPDATE payload_hash", "seq": tamper_seq})

    reader = AuditChain.reader(db_path=str(db_path))
    try:
        after_b = verify_chain(reader.entries())
    finally:
        reader.close()
    bad_b = [row.get("seq") for row in (after_b.bad_seqs or [])]
    record.expect("D2b 首个异常位置 == 注入点", after_b.first_bad_seq == tamper_seq,
                  {"first_bad_seq": after_b.first_bad_seq, "expected": tamper_seq})
    record.expect("D2b 影响范围 = 注入点及其后全部（哈希前向传播）",
                  bad_b and bad_b[0] == tamper_seq
                  and set(bad_b) == set(range(tamper_seq, n + 1)),
                  {"bad_seqs": bad_b, "reason": after_b.reason})

    # 恢复：还原 payload_hash → 链重新完整
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("UPDATE audit_chain SET payload_hash = ? WHERE seq = ?",
                     (original_hash, tamper_seq))
        conn.commit()
    finally:
        conn.close()
    restored = AuditChain.reader(db_path=str(db_path))
    try:
        final_b = verify_chain(restored.entries())
    finally:
        restored.close()
    record.recover("D2b 还原 payload_hash 后链恢复完整", final_b.ok,
                   {"checked": final_b.checked, "summary": final_b.summary()})
    record.step("cleanup", {"db_removed_with_tmpdir": True})
    return record


# ────────────────────────────────────────────────────────────
#  D3：删最新快照 → 整包回滚到缺失包
# ────────────────────────────────────────────────────────────


def _components(tag: str, version: str) -> Dict[str, Any]:
    return {name: (version, f"{rb.HASH_ALGO}:{tag}-{name}") for name in rb.COMPONENT_NAMES}


def drill_d3_missing_snapshot(tmp: Path) -> DrillRecord:
    """D3：删最新整包快照 → 回滚到缺失包被拒 + L4 事故卡；回退到现存包可恢复"""
    record = DrillRecord(
        drill_id="D3",
        title="删最新快照 → 整包回滚被拒并升级 L4；回退到现存包恢复",
        chaos="从整包台账中删除最新 bundle（模拟快照被误删/损坏）",
    )
    work = tmp / "d3"
    work.mkdir(parents=True, exist_ok=True)
    incident_dir = work / "incidents"
    reset_levels_state()

    store = rb.ReleaseStore(path=work / "ledger.json")
    v1 = rb.build_bundle(_components("v1", "1.0.0"), note="基线包")
    v2 = rb.build_bundle(_components("v2", "2.0.0"), note="升级包")
    store.put(v1)
    store.put(v2)
    record.step("snapshot", {"bundles": store.count(),
                             "v1": v1.bundle_id, "v2": v2.bundle_id})

    # 注入故障：删掉最新包（重写台账，去掉 v2）
    ledger = json.loads((work / "ledger.json").read_text(encoding="utf-8"))
    kept = [row for row in ledger["bundles"]
            if row["bundle_hash"] != v2.bundle_hash]
    (work / "ledger.json").write_text(
        json.dumps({"schema": ledger["schema"], "bundles": kept},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    record.step("chaos", {"action": "delete newest bundle",
                          "deleted": v2.bundle_hash})

    reloaded = rb.ReleaseStore(path=work / "ledger.json")
    record.expect("最新包确已消失", reloaded.count() == 1 and reloaded.get(v2.bundle_hash) is None,
                  {"count": reloaded.count()})

    # 期望：回滚到缺失包 → 明确拒绝（不是静默成功）
    rejected = False
    detail: Dict[str, Any] = {}
    try:
        rb.rollback_bundle(v2.bundle_hash, store=reloaded,
                           incidents_dir=str(incident_dir))
        record.expect("回滚到缺失包被拒", False, "回滚竟然成功了")
    except rb.BundleNotFoundError as exc:
        rejected = True
        detail = {"exception": type(exc).__name__, "message": str(exc)}
        record.expect("回滚到缺失包被拒", True, detail)
    except Exception as exc:  # noqa: BLE001
        record.expect("回滚到缺失包被拒", False,
                      f"异常类型非预期: {type(exc).__name__}: {exc}")
    if not rejected:
        return record

    # 期望：部分回滚（只回技能）仍被拒并开 L4 事故卡 —— 与快照缺失相互独立的两道闸门
    partial_incident = ""
    try:
        rb.rollback_bundle(v1.bundle_hash, store=reloaded, components=["skills"],
                           incidents_dir=str(incident_dir))
        record.expect("部分回滚被拒并触发 L4", False, "部分回滚竟然成功了")
    except rb.PartialRollbackError as exc:
        partial_incident = exc.incident_id
        record.expect("部分回滚被拒并触发 L4", bool(exc.incident_id),
                      {"missing": exc.missing, "incident_id": exc.incident_id})
    cards = list_incidents(directory=str(incident_dir))
    record.expect("L4 事故卡已落盘", len(cards) >= 1,
                  {"cards": [c.incident_id for c in cards],
                   "severities": [c.severity.value for c in cards]})

    # 恢复验证：回滚到**现存**的 v1 → 五组件整包计划可生成且可落地（applier 为演练桩）
    applied: Dict[str, Any] = {}

    def _applier(plan: Any) -> None:
        applied["component_count"] = plan.component_count()
        applied["is_full_bundle"] = plan.is_full_bundle()

    plan = rb.rollback_bundle(v1.bundle_hash, store=reloaded,
                              current=v2.component_versions(),
                              applier=_applier, incidents_dir=str(incident_dir))
    record.recover("回退到现存包可恢复（五组件整包计划）",
                   plan.applied and applied.get("is_full_bundle") is True,
                   {"applied": plan.applied, "moves": plan.component_count(),
                    "is_full_bundle": applied.get("is_full_bundle"),
                    "from_bundle": plan.from_bundle_hash[:20]})
    record.step("cleanup", {"incident_dir": str(incident_dir)})
    return record


# ────────────────────────────────────────────────────────────
#  D4：Saga 补偿失败 → 升级 L4
# ────────────────────────────────────────────────────────────


def drill_d4_saga_compensation_failure(tmp: Path) -> DrillRecord:
    """D4：Saga 补偿失败 → 升级 L4（快照恢复路径）+ 事故卡 + journal 留证"""
    record = DrillRecord(
        drill_id="D4",
        title="Saga 补偿失败 → 升级 L4 + 最高告警 + journal 留证",
        chaos="补偿回调抛异常（模拟「外部副作用已发生且无法自动撤销」）",
    )
    work = tmp / "d4"
    work.mkdir(parents=True, exist_ok=True)
    incident_dir = work / "incidents"
    reset_levels_state()
    sg.reset_journal_writers()

    journal = sg.SagaJournal(path=work / "journal.log")
    compensated_calls: List[str] = []

    def _boom() -> Any:
        compensated_calls.append("boom")
        raise RuntimeError("外部副作用已发生，自动补偿不可用")

    saga = sg.Saga(
        journal=journal,
        steps=[
            sg.SagaStep("write_config", compensator=lambda: compensated_calls.append("cfg") or "ok"),
            sg.SagaStep("rotate_key", compensator=_boom),
        ],
        trace_id="chaos-d4", incidents_dir=str(incident_dir),
    )
    saga.prepare({"op": "deploy"}, snapshot={"version": "1.0.0"})
    saga.execute(lambda: {"version": "2.0.0"})
    record.step("executed", {"state": saga.state.value,
                             "three_phase": journal.is_three_phase_complete(saga.saga_id)})

    record.step("chaos", {"action": "abort → compensate（其中一个补偿抛错）"})
    result = saga.abort("演练：执行失败")

    record.expect("补偿失败被标记（不静默）", bool(result.failed),
                  {"failed": result.failed, "compensated": result.compensated})
    record.expect("升级 L4（escalated=True）", result.escalated,
                  {"state": result.state, "incident_id": result.incident_id})
    record.expect("开出事故卡", bool(result.incident_id),
                  {"incident_id": result.incident_id})
    cards = list_incidents(directory=str(incident_dir))
    record.expect("事故卡级别为 L4",
                  bool(cards) and all(c.severity == HealLevel.L4 for c in cards),
                  {"severities": [c.severity.value for c in cards]})
    steps = journal.steps_of(saga.saga_id)
    # 注：abort 路径按 §4.6 设计**不含 confirm**（失败即不再确认），故此处只要求
    # prepare + execute + abort + 补偿 + escalate 齐备；三态完整性由正向路径用例覆盖。
    record.expect("journal 含 prepare/execute/abort/补偿/escalate",
                  {sg.STEP_PREPARE, sg.STEP_EXECUTE, sg.STEP_ABORT}.issubset(set(steps))
                  and any(sg.step_kind(s) == sg.STEP_COMPENSATE for s in steps)
                  and sg.STEP_ESCALATE in steps,
                  {"steps": steps})

    # 幂等性：再次补偿不应重复执行已经成功的补偿
    before_calls = list(compensated_calls)
    replay = saga.compensate()
    record.expect("重复补偿幂等（已成功步骤跳过）",
                  replay.skipped and compensated_calls.count("cfg") == before_calls.count("cfg"),
                  {"skipped": replay.skipped, "failed": replay.failed,
                   "calls": compensated_calls})

    # 恢复验证：journal 可从磁盘重建（重启后可继续处置未完成事务）
    reloaded = sg.SagaJournal(path=work / "journal.log")
    rebuilt = saga.state_from_journal()
    record.recover("journal 可从磁盘重读并重建状态（重启后仍可处置）",
                   rebuilt == sg.SagaState.ESCALATED
                   and sg.STEP_ESCALATE in reloaded.steps_of(saga.saga_id),
                   {"rebuilt_state": rebuilt.value,
                    "journal_entries": len(reloaded.entries(saga.saga_id)),
                    "incomplete_sagas": reloaded.incomplete_sagas()})
    sg.reset_journal_writers()
    return record


# ────────────────────────────────────────────────────────────
#  运行与报告
# ────────────────────────────────────────────────────────────

DRILLS: Dict[str, Callable[[Path], DrillRecord]] = {
    "D1": drill_d1_kill_watchdog,
    "D2": drill_d2_audit_tamper,
    "D3": drill_d3_missing_snapshot,
    "D4": drill_d4_saga_compensation_failure,
}


def render_markdown(records: List[DrillRecord], started: str) -> str:
    """演练记录 Markdown（命令 + 期望 + 输出 + 恢复验证）"""
    passed = sum(1 for r in records if r.passed)
    lines = [
        "# TASK-S4-03 混沌演练实测记录（v7.2 §11.10）",
        "",
        f"- 运行时刻：{started}",
        f"- 结果：**{passed}/{len(records)} 通过**",
        f"- 命令：`python scripts/chaos_s4_03_drill.py`",
        "- 环境：全部落盘于临时目录（`tempfile.mkdtemp()`），退出即弃；"
        "**未触碰主工作区数据与运行中服务**",
        "",
        "## 覆盖对照（§11.10 季度清单）",
        "",
        "| 清单项 | 本任务覆盖 | 说明 |",
        "|---|---|---|",
        "| kill -9 主进程 | ✅ D1 | 杀的是本脚本 spawn 的 Watchdog 持有子进程 |",
        "| 删最新快照 | ✅ D3 | 整包台账删包 → 回滚被拒 + L4 |",
        "| 向审计链注入一条篡改 | ✅ D2 | 真 UPDATE SQLite → 链校验定位注入点 |",
        "| Saga 补偿失败 | ✅ D4 | §4.6 失败路径（本任务自有项） |",
        "| 断网 10 分钟 | ❌ 未覆盖 | 属季度清单其余项，不在本任务范围 |",
        "| 磁盘写满 | ❌ 未覆盖 | 同上 |",
        "| 上游返回畸形 JSON | ❌ 未覆盖 | 同上 |",
        "| kill Watchdog 本身（验证互 watch） | ❌ 未覆盖 | 互 watch 属主进程/Watchdog 双侧实现，"
        "本任务只做单机唯一性（P7.2-16） |",
        "",
        "## 逐项记录",
        "",
    ]
    for record in records:
        lines.append(f"### {record.drill_id} {record.title}")
        lines.append("")
        lines.append(f"- **故障注入**：{record.chaos}")
        lines.append(f"- **结论**：{'✅ 通过' if record.passed else '❌ 未通过'}")
        lines.append("")
        lines.append("| 阶段 | 动作 | 明细 |")
        lines.append("|---|---|---|")
        for step in record.steps:
            detail = json.dumps(step["detail"], ensure_ascii=False) if step["detail"] != "" else ""
            lines.append(f"| {step['ts']} | {step['name']} | `{_clip(detail)}` |")
        lines.append("")
        lines.append("**期望 vs 实测**")
        lines.append("")
        lines.append("| # | 期望 | 结果 | 证据 |")
        lines.append("|---|---|---|---|")
        for index, item in enumerate(record.expectations, 1):
            lines.append(f"| {index} | {item['claim']} | "
                         f"{'✅' if item['ok'] else '❌'} | "
                         f"`{_clip(json.dumps(item['evidence'], ensure_ascii=False, default=str))}` |")
        lines.append("")
        lines.append("**恢复验证**")
        lines.append("")
        lines.append("| # | 恢复项 | 结果 | 证据 |")
        lines.append("|---|---|---|---|")
        for index, item in enumerate(record.recovery, 1):
            lines.append(f"| {index} | {item['name']} | "
                         f"{'✅' if item['ok'] else '❌'} | "
                         f"`{_clip(json.dumps(item['evidence'], ensure_ascii=False, default=str))}` |")
        lines.append("")
    return "\n".join(lines)


def _clip(text: str, limit: int = 400) -> str:
    """裁剪长证据（保留可读性；完整证据见 JSON 输出）"""
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[:limit] + "…"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="TASK-S4-03 混沌演练（§11.10）")
    parser.add_argument("--only", default="", help="只跑指定项（逗号分隔，如 D1,D2）")
    parser.add_argument("--json", default="", help="写出 JSON 记录到该路径")
    parser.add_argument("--md", default="", help="写出 Markdown 记录到该路径")
    args = parser.parse_args(argv)

    wanted = ([x.strip().upper() for x in args.only.split(",") if x.strip()]
              if args.only else list(DRILLS))
    unknown = [x for x in wanted if x not in DRILLS]
    if unknown:
        print(f"未知演练项: {unknown}；可用: {list(DRILLS)}")
        return 2

    started = _now()
    records: List[DrillRecord] = []
    with tempfile.TemporaryDirectory(prefix="s403-chaos-") as raw_tmp:
        tmp = Path(raw_tmp)
        print(f"[chaos] 临时目录（退出即弃）: {tmp}")
        for drill_id in wanted:
            print(f"\n{'=' * 72}\n[chaos] {drill_id} 开始\n{'=' * 72}")
            try:
                record = DRILLS[drill_id](tmp)
            except Exception as exc:  # noqa: BLE001 单项异常不影响其余项
                record = DrillRecord(drill_id=drill_id, title="（执行异常）",
                                     chaos=str(exc))
                record.expect("演练可执行", False,
                              f"{type(exc).__name__}: {exc}")
            records.append(record)
            for item in record.expectations:
                print(f"  [{'PASS' if item['ok'] else 'FAIL'}] {item['claim']}"
                      + (f" — {_clip(json.dumps(item['evidence'], ensure_ascii=False, default=str), 200)}"
                         if item["evidence"] != "" else ""))
            for item in record.recovery:
                print(f"  [恢复{'OK' if item['ok'] else 'FAIL'}] {item['name']}")
            print(f"  => {drill_id} {'通过' if record.passed else '未通过'}")

    payload = {
        "task": "TASK-S4-03",
        "started": started,
        "finished": _now(),
        "spec": "v7.2 §11.10 混沌演练清单",
        "passed": sum(1 for r in records if r.passed),
        "total": len(records),
        "records": [r.to_dict() for r in records],
    }
    if args.json:
        Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
        print(f"\n[chaos] JSON 记录: {args.json}")
    if args.md:
        Path(args.md).write_text(render_markdown(records, started), encoding="utf-8")
        print(f"[chaos] Markdown 记录: {args.md}")

    print(f"\n[chaos] 汇总: {payload['passed']}/{payload['total']} 通过")
    return 0 if payload["passed"] == payload["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
