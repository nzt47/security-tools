"""TASK-S5-03 成本刹车/断食**演练**（真实触发 → 恢复，全部输出可复现）

演练不是单测的复制：它按「运维真实会走的路径」跑一遍，并把每一步的**实测输出**
打出来，供验收报告直接引用。

两条硬纪律体现在演练方式上：

1. **走进程单例**（`CP_BUDGET_*` 环境变量 + `get_cost_brake()`）——这才是生产路径。
   S3-03 的 shadow 预算、消化调度查询的正是单例/持久化状态；
   用注入实例演练会**假绿**（联动点看不到状态），故演练刻意不这么做。
2. **全部落在临时目录**：事件流与状态文件都不写仓库 `data/`，演练结束即清理。

场景：

  A. 日级硬熔断：当日成本超预算 → OPEN → 停非关键 outbound
     → 关键路径（用户显式请求 / 审批中任务）**不受影响** → 次日 00:00 自动恢复
  B. 周级断食：日均成本比 > 1.3×（持续判定）→ 降本模式
     → 影子任务预算归零 + 消化调度抑制（S3-03 联动实测）
     → 连续 24h ≤ 1.1× → 退出 → 冷却 12h → 恢复常态
  C. θ 分阶段：同一时刻按阶段给出 UTC 上限（含阶段内收紧）
  D. 审批衰减率：自动消化占比 + 疲劳分桶 + 样本不足如实标注（披露不考核）
  E. 口径审计：shadow_overhead_ms 是否进入 UTC 口径（金额与机时分开标注）
  F. 零影响：总开关关闭时对全局行为**零影响**（防误伤自查）

用法::

    python scripts/demo_s5_03_cost_brake.py
    python scripts/demo_s5_03_cost_brake.py --keep      # 保留临时目录以便查验
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

TZ = datetime.now().astimezone().tzinfo
#: 演练基准时刻（固定，保证两次运行输出逐字一致）
BASE = datetime(2026, 9, 14, 10, 0, tzinfo=TZ)

PASS = "OK"
FAIL = "NG"
_checks: list = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    _checks.append((label, bool(ok)))
    print(f"  [{PASS if ok else FAIL}] {label}" + (f" — {detail}" if detail else ""))
    return bool(ok)


def rule(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def at(days: int = 0, hour: int = 10) -> datetime:
    return (BASE + timedelta(days=days)).replace(hour=hour, minute=0, second=0,
                                                 microsecond=0)


def configure_brake(mapping: dict) -> None:
    """按生产路径配置：写 `CP_BUDGET_*` 环境变量并重装进程单例"""
    from agent.monitoring import cost_brake as CB
    for key, value in mapping.items():
        os.environ[str(key)] = str(value)
    CB.reset_cost_brake()
    CB.get_cost_brake(reload=True)


def clear_brake_env() -> None:
    """清掉本次演练写过的 `CP_BUDGET_*`（不影响 PATH 等系统变量）"""
    from agent.monitoring import cost_brake as CB
    for name in CB.__all__:
        if name.startswith("ENV_"):
            os.environ.pop(getattr(CB, name), None)


def seed_cost(store, day: str, cents: float, tag: str) -> None:
    """写入成本事件使当日归一成本 ≈ ``cents``（gpt-4 锚价 3/6 cents per 1k）"""
    from agent.observability import utc as U
    remaining, index = float(cents), 0
    while remaining > 1e-9:
        chunk = min(60.0, remaining)
        tokens_in = int(round(chunk / 60.0 * 10000))
        tokens_out = int(round(chunk / 60.0 * 5000))
        U.record_cost(model="gpt-4", tokens_in=tokens_in, tokens_out=tokens_out,
                      interaction_id=f"{tag}-{day}-{index}", store=store,
                      ts=f"{day}T10:00:00.000+08:00")
        remaining -= chunk
        index += 1


def scenario_a(events_dir: str, store) -> None:
    from agent.monitoring import cost_brake as CB
    from agent.observability import events as ev

    rule("A. 日级硬熔断（P7.2-06）：超预算 → 停非关键 → 次日自动恢复")
    day1 = BASE.date().isoformat()
    seed_cost(store, day1, 180.0, "drillA")

    # A1：先以宽松预算判定 → 未超阈值时零影响
    configure_brake({CB.ENV_ENABLED: "true", CB.ENV_DAILY_CENTS: "1000",
                       CB.ENV_STATE_PATH: os.path.join(
                           os.path.dirname(events_dir), "brake_state.json"),
                       CB.ENV_EVAL_MAX_AGE: "3600"})
    under = CB.get_cost_brake().evaluate(at(0))
    check("A1 未超阈值时熔断不触发（零影响）", under["day_breaker_open"] is False,
          f"当日成本={under['daily_cost_cents']} ≤ 预算={under['daily_budget_cents']}")

    # A2：收紧预算 → 熔断
    configure_brake({CB.ENV_DAILY_CENTS: "100"})
    brake = CB.get_cost_brake()
    status = brake.evaluate(at(0))
    print(json.dumps({k: status[k] for k in (
        "day_breaker_open", "daily_cost_cents", "daily_budget_cents",
        "resume_at", "reason", "audit_seq")}, ensure_ascii=False, indent=2))
    check("A2 超预算 → 熔断 OPEN", status["day_breaker_open"] is True)
    check("A2b 恢复时点可观测", bool(status["resume_at"]),
          f"resume_at={status['resume_at']}")

    blocked = [k for k in CB.BACKGROUND_KINDS
               if not brake.allow_outbound(kind=k, now=at(0))]
    allowed = [k for k in CB.CRITICAL_KINDS
               if brake.allow_outbound(kind=k, now=at(0))]
    print(f"  被拦 kind：{blocked}")
    print(f"  放行 kind：{allowed}")
    check("A3 非关键 outbound 全部被停", set(blocked) == set(CB.BACKGROUND_KINDS))
    check("A4 关键路径（用户请求 / 审批中任务）**不受影响**",
          set(allowed) == set(CB.CRITICAL_KINDS))
    check("A5 默认调用方（未声明后台 kind）恒放行（默认不误伤）",
          brake.allow_outbound(now=at(0)) is True)
    print(f"  拦截原因：{brake.block_reason(kind='shadow', now=at(0))}")

    rows = ev.read_events(directory=events_dir)
    types = [e.type for e in rows]
    check("A6 熔断写入事件流（healing.triggered + metrics.delta）",
          ev.EV_HEALING_TRIGGERED in types and ev.EV_METRICS_DELTA in types,
          f"事件类型计数={ {t: types.count(t) for t in sorted(set(types))} }")
    heal = [e for e in rows if e.type == ev.EV_HEALING_TRIGGERED]
    if heal:
        print("  审计/事件载荷："
              + json.dumps(heal[-1].payload, ensure_ascii=False)[:230])

    recovered = brake.evaluate(at(1, 0))
    check("A7 次日 00:00 自动恢复", recovered["day_breaker_open"] is False,
          f"day={recovered['day']} 当日成本={recovered['daily_cost_cents']}")
    check("A7b 恢复后非关键 outbound 放行",
          brake.allow_outbound(kind="shadow", now=at(1, 0)) is True)
    actions = {e.payload.get("action") for e in ev.read_events(directory=events_dir)
               if e.type == ev.EV_HEALING_TRIGGERED}
    check("A8 触发与恢复各有独立事件留痕",
          actions == {"stop_non_critical_outbound", "resume"},
          f"actions={sorted(a for a in actions if a)}")


def scenario_b(events_dir: str, store) -> None:
    from agent.digestion import shadow as SH
    from agent.monitoring import cost_brake as CB

    rule("B. 周级断食（§7）：进 >1.3× → 降本 → 连续 24h ≤1.1× → 冷却 12h")
    # 走单例：这正是 S3-03 shadow / 消化调度查询的那条路径
    configure_brake({CB.ENV_ENABLED: "true", CB.ENV_DAILY_CENTS: "100000",
                       CB.ENV_BASELINE_CENTS: "60",
                       CB.ENV_FASTING_IN_CONSECUTIVE: "1",
                       CB.ENV_EVAL_MAX_AGE: "3600"})
    brake = CB.get_cost_brake()
    st = brake.evaluate(at(0))
    print(json.dumps({"ratio": st["ratio"],
                      "entry_threshold": st["entry_threshold"],
                      "entry_threshold_source": st["entry_threshold_source"],
                      "fasting": {k: st["fasting"][k] for k in (
                          "state", "entered_at", "exited_at", "cooldown_until",
                          "threshold_source", "transitions", "last_reason")}},
                     ensure_ascii=False, indent=2))
    check("B1 日均成本比 > 1.3× → 进入断食降本模式",
          st["fasting"]["state"] == CB.STATE_FASTING,
          f"ratio={st['ratio']} / threshold={st['entry_threshold']}")
    check("B2 状态可观测（状态/进入时间/阈值来源）",
          bool(st["fasting"]["entered_at"]) and bool(st["fasting"]["threshold_source"]))

    snap = brake.suppression()
    print("  降本联动视图：" + json.dumps(snap, ensure_ascii=False))
    check("B3 断食期 shadow 预算系数 = 0（归零）",
          snap["shadow_budget_factor"] == 0.0)

    budget_now = SH.daily_budget(1000.0)
    check("B4 S3-03 shadow 每日预算**实际归零**（穿透低流量保底）",
          budget_now == 0, f"断食期 daily_budget(1000)={budget_now}（常态为 50）")
    low = SH.daily_budget(3.0)
    check("B4b 低流量保底也被穿透（否则影子任务会溜回来）", low == 0,
          f"daily_budget(3)={low}")

    restricted, why = CB.digestion_restricted()
    check("B5 消化/内化/重探调度被抑制", restricted is True, why)

    check("B6 断食是降本不是硬停：用户请求与审批仍放行",
          brake.allow_outbound(kind="interactive", now=at(0)) is True
          and brake.allow_outbound(kind="approval_pending", now=at(0)) is True
          and brake.allow_outbound(kind="shadow", now=at(0)) is False)

    machine = brake.machine
    machine.observe(1.0, at(0, 11))
    mid = machine.observe(1.0, at(0, 23))
    check("B7 未满 24h 不退出", machine.state == CB.STATE_FASTING, mid["reason"])
    out = machine.observe(1.0, at(1, 11))
    check("B8 连续 24h ≤ 1.1× → 退出断食进入冷却",
          out["transition"] and machine.state == CB.STATE_COOLDOWN, out["reason"])
    check("B9 冷却期不降本（限制已解除）", machine.restricted is False)

    machine.observe(9.9, at(1, 13))
    check("B10 冷却期内高比值**不重进**（防抖动）",
          machine.state == CB.STATE_COOLDOWN)
    machine.observe(1.0, at(1, 23))
    check("B11 冷却 12h 后恢复常态", machine.state == CB.STATE_NORMAL,
          machine.snapshot.last_reason)

    after = SH.daily_budget(1000.0)
    check("B12 恢复常态后 shadow 预算回到常态（联动可逆）", after == 50,
          f"daily_budget(1000)={after}")


def scenario_c() -> None:
    from agent.monitoring import cost_brake as CB

    rule("C. θ 分阶段阈值（§6.3）：配置驱动，阶段内收紧")
    cfg_w = CB.load_config({CB.ENV_PHASE_START: "2026-08-17"})
    rows_theta = []
    for label, moment in (("W1-W4（起点前）", datetime(2026, 8, 10, tzinfo=TZ)),
                          ("W5 首周", datetime(2026, 9, 14, tzinfo=TZ)),
                          ("W9 末周", datetime(2026, 10, 12, tzinfo=TZ)),
                          ("M4", datetime(2026, 12, 15, tzinfo=TZ)),
                          ("M7+", datetime(2027, 3, 15, tzinfo=TZ))):
        info = CB.theta_limit(moment, config=cfg_w)
        rows_theta.append((label, info["phase"], info["limit"]))
        print(f"  {label:<16} phase={info['phase']:<9} θ={info['limit']} "
              f"（表来源={info['table_source']}）")
    check("C1 W1-W4 不设 θ",
          [r[2] for r in rows_theta if r[1] == CB.PHASE_W1_W4] == [None])
    check("C2 W5-W9 阶段内由 1.5× 收紧到 1.3×",
          rows_theta[1][2] == 1.5 and rows_theta[2][2] == 1.3)
    check("C3 表可由配置覆盖（无硬编码）",
          CB.load_config({CB.ENV_THETA_TABLE: json.dumps({"m7_plus": 0.4})})
          .theta_table[CB.PHASE_M7_PLUS] == {"start": 0.4, "end": 0.4})
    check("C4 表来源可追溯", cfg_w.theta_source == "default")


def scenario_d(events_dir: str, store) -> None:
    from agent.monitoring import cost_brake as CB
    from agent.observability import acr
    from agent.observability import events as ev

    rule("D. 审批衰减率（§6.7）：策略自动消化占比（披露不考核）")
    day1 = BASE.date().isoformat()
    day2 = (BASE + timedelta(days=1)).date().isoformat()
    ts1 = f"{day1}T09:00:00.000+08:00"
    ts2 = f"{day2}T09:00:00.000+08:00"
    for i in range(16):
        acr.record_approval(kind="auto_pass", record_id=f"drill-a{i}",
                            state="approved", actor=ev.ACTOR_AUTO,
                            count_intervention=False, store=store, ts=ts1)
    for i in range(4):
        acr.record_approval(kind="approve", record_id=f"drill-h{i}",
                            state="approved", actor=ev.ACTOR_HUMAN,
                            latency_ms=1500.0 + i * 100, store=store, ts=ts1)
    # 次日只来 3 条 → 演示"样本不足只披露不下结论"
    for i in range(3):
        acr.record_approval(kind="approve", record_id=f"drill-t{i}",
                            state="approved", actor=ev.ACTOR_HUMAN,
                            latency_ms=900.0, store=store, ts=ts2)

    decay = CB.approval_decay_rate(day=day1, directory=events_dir)
    print(json.dumps({k: decay[k] for k in (
        "auto_disposed", "human_disposed", "total_disposed", "decay_rate",
        "target", "meets_target", "insufficient_sample", "fatigue_buckets",
        "latency_median_ms", "disclosure_only")}, ensure_ascii=False, indent=2))
    check("D1 自动消化占比可计算（16/20 = 0.8 ≥ 目标 0.7）",
          decay["decay_rate"] == 0.8 and decay["meets_target"] is True)
    check("D2 样本充足时不误报样本不足", decay["insufficient_sample"] is False)

    thin = CB.approval_decay_rate(day=day2, directory=events_dir)
    print(f"  次日（{day2}）：total={thin['total_disposed']} "
          f"rate={thin['decay_rate']} insufficient_sample={thin['insufficient_sample']}")
    check("D3 样本不足（3 < 20）时如实标注，不据此下结论",
          thin["insufficient_sample"] is True and thin["total_disposed"] == 3)
    check("D4 只披露不考核（disclosure_only=True）", decay["disclosure_only"] is True)


def scenario_e(events_dir: str) -> None:
    from agent.monitoring import cost_brake as CB
    from agent.observability import events as ev
    from agent.observability import utc as U

    rule("E. 口径审计：shadow_overhead_ms 是否进入 UTC 口径")
    day1 = BASE.date().isoformat()
    U.record_cost(model="gpt-4", tokens_in=1000, tokens_out=0,
                  shadow_overhead_ms=2000.0, shadow_overhead_cents=3.0,
                  interaction_id="drill-shadow", store=ev.get_event_store(),
                  ts=f"{day1}T08:00:00.000+08:00")
    unpriced = CB.shadow_overhead_audit(day=day1, directory=events_dir,
                                        config=CB.load_config({}))
    print("  未配置机时费率：" + json.dumps(unpriced, ensure_ascii=False, indent=2))
    check("E1 shadow_overhead_ms **已纳入 UTC 聚合口径**",
          unpriced["shadow_overhead_ms"] >= 2000.0 and unpriced["in_utc_scope"])
    check("E2 金额与机时**分开标注**，未配置费率不臆造金额",
          unpriced["shadow_overhead_cents_direct"] >= 3.0
          and unpriced["priced"] is False)
    priced = CB.shadow_overhead_audit(
        day=day1, directory=events_dir,
        config=CB.load_config({CB.ENV_SHADOW_MS_CENTS_PER_S: "10"}))
    check("E3 配置费率后机时折价计入并**参与熔断判定**",
          priced["priced"] is True and priced["included_cents"] > 20.0,
          f"折价={priced['shadow_overhead_cents_from_ms']} cents")

    view = CB.write_cost_daily(str(Path(events_dir).parent / "cost_daily.json"),
                               day=day1, now=at(0), directory=events_dir,
                               config=CB.load_config({}))
    check("E4 归一化日成本视图落盘（data/cost_daily.json 的内容）",
          view["cost_schema_version"] == CB.COST_SCHEMA_VERSION
          and view["source_of_truth"] == "events",
          f"schema={view['cost_schema_version']} "
          f"calibration={view['calibration_version']}")


def scenario_f(events_dir: str) -> None:
    from agent.monitoring import cost_brake as CB

    rule("F. 零影响自查：总开关关闭时对全局行为零影响")
    for name in (CB.ENV_ENABLED, CB.ENV_DAILY_CENTS, CB.ENV_BASELINE_CENTS,
                 CB.ENV_FASTING_IN_CONSECUTIVE):
        os.environ.pop(name, None)
    os.environ[CB.ENV_ENABLED] = "false"
    os.environ[CB.ENV_DAILY_CENTS] = "1"
    CB.reset_cost_brake()
    brake = CB.get_cost_brake(reload=True)
    st_off = brake.evaluate(at(0))
    kinds_ok = all(brake.allow_outbound(kind=k, now=at(0))
                   for k in list(CB.BACKGROUND_KINDS) + list(CB.CRITICAL_KINDS))
    check("F1 总开关关闭 → 判定为 disabled，不拦截任何 kind", kinds_ok,
          f"reason={st_off['reason']}")
    snap = brake.suppression()
    check("F2 降本视图中性（系数 1.0、无受限 kind）",
          snap["shadow_budget_factor"] == 1.0 and snap["blocked_kinds"] == [])
    from agent.digestion import shadow as SH
    check("F3 shadow 预算逐字回到常态", SH.daily_budget(1000.0) == 50
          and SH.daily_budget(3.0) == 1)


def main() -> int:
    parser = argparse.ArgumentParser(description="S5-03 成本刹车与断食演练")
    parser.add_argument("--keep", action="store_true", help="保留临时工作目录")
    args = parser.parse_args()

    # Windows 控制台默认 GBK：统一切到 UTF-8，避免中文/符号输出炸掉演练
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001 老解释器/被重定向 → 忽略
        pass

    from agent.observability import events as ev
    from agent.observability import utc as U
    from agent.monitoring import cost_brake as CB

    workdir = Path(tempfile.mkdtemp(prefix="s5_03_drill_"))
    events_dir = str(workdir / "events")
    clear_brake_env()
    os.environ.update({
        "CP_EVENTS_ENABLED": "1",
        ev.ENV_DIR: events_dir,
        U.ENV_ANCHOR_MODEL: "gpt-4",
    })
    U.reset_config_cache()
    ev.reset_event_stores()
    CB.reset_cost_brake()
    store = ev.get_event_store()

    print(f"演练工作目录：{workdir}")
    print(f"事件流目录　：{events_dir}")
    print(f"基准时刻　　：{BASE.isoformat()}")

    try:
        scenario_a(events_dir, store)
        scenario_b(events_dir, store)
        scenario_c()
        scenario_d(events_dir, store)
        scenario_e(events_dir)
        scenario_f(events_dir)
    finally:
        rule("演练结论")
        passed = sum(1 for _label, ok in _checks if ok)
        total = len(_checks)
        print(f"  检查项：{passed}/{total} 通过")
        for label, ok in _checks:
            if not ok:
                print(f"  [{FAIL}] {label}")
        print(f"  事件流留痕：{len(ev.read_events(directory=events_dir))} 条"
              f"（位于演练临时目录，未污染仓库 data/）")
        if args.keep:
            print(f"  --keep：临时目录已保留 → {workdir}")
        else:
            shutil.rmtree(workdir, ignore_errors=True)
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())

