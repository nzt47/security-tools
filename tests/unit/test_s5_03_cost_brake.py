"""TASK-S5-03 成本刹车与断食 单元测试

覆盖范围（对齐任务书 §四 验收清单）：

- **日级硬熔断**：超 `budget.daily_cents` → 停**非关键** outbound；
  用户显式请求 / 审批中任务**不受影响**；次日 00:00 自动恢复；审计 + 事件留痕。
- **周级断食**：进（日均成本比 > 1.3×，持续判定）→ 降本；出（连续 24h ≤ 1.1×）；
  冷却 12h；三态可观测（状态 / 进入退出时间 / 阈值来源）。
- **θ 分阶段（§6.3）**：配置驱动、无硬编码；W1-W4 不设。
- **断食期 S3 shadow 预算联动**（归零/减半生效）。
- **审批衰减率**（披露不考核）可计算。
- **shadow_overhead_ms 计入口径**（金额部分与机时部分的区分如实标注）。
- **「未超阈值时零影响」**（防误伤）—— 总开关默认关闭时的硬断言。

时间类断言**全部注入时钟**（`clock=`），不依赖真实墙钟；落盘用例一律
显式传 `state_path` / `directory`（S3-02/S3-03 两次运行时区污染教训）。
"""

import json
import os
from datetime import datetime, timedelta

import pytest

from agent.observability import acr
from agent.observability import events as ev
from agent.observability import utc as U
from agent.monitoring import cost_brake as CB

#: 固定基准时刻（带本地时区）——所有用例据此注入时钟，避免跨日/墙钟漂移
BASE = datetime(2026, 9, 14, 10, 0, tzinfo=datetime.now().astimezone().tzinfo)


def at(day_offset: int = 0, hour: int = 10) -> datetime:
    """基准时刻 ± 天、指定小时（带时区）"""
    base = BASE + timedelta(days=day_offset)
    return base.replace(hour=hour, minute=0, second=0, microsecond=0)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """隔离：事件目录 + 锚模型 + 全部 CP_BUDGET_* 环境 + 进程单例"""
    monkeypatch.setenv(ev.ENV_DIR, str(tmp_path / "events"))
    monkeypatch.setenv(U.ENV_ANCHOR_MODEL, "gpt-4")
    for name in (U.ENV_COEFFICIENTS, U.ENV_PRICE_OVERRIDES):
        monkeypatch.delenv(name, raising=False)
    for name in CB.__all__:
        if name.startswith("ENV_"):
            monkeypatch.delenv(getattr(CB, name), raising=False)
    U.reset_config_cache()
    ev.reset_event_stores()
    CB.reset_cost_brake()
    yield
    U.reset_config_cache()
    ev.reset_event_stores()
    CB.reset_cost_brake()


@pytest.fixture
def events_dir(tmp_path):
    return str(tmp_path / "events")


@pytest.fixture
def store(tmp_path):
    """写入端一律用**进程单例**（统一事件出口 §3.6）

    刻意不另建 `EventStore(同一路径)`：`events.py` 的单写者纪律
    （`register_writer`）会对同一路径的第二个 writer 抛
    `SingleWriterViolationError`，而刹车的事件留痕走 `emit()` → 单例。
    用单例既贴合生产路径，也避免测试里出现两个 writer 的假失败。
    """
    return ev.get_event_store()


def make_brake(events_dir, **overrides):
    """按需装配刹车（默认在 **tmp 目录** 内、不持久化，避免运行时区污染）"""
    cfg = CB.load_config(overrides.pop("env", None))
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return CB.CostBrake(config=cfg, events_dir=events_dir, state_path="",
                        persist=False)


def seed_cost(store, *, day, cents_target, hour=10, tag="c"):
    """写入一条成本事件，使当日归一成本 ≈ ``cents_target``（gpt-4 锚价 3/6 cents per 1k）

    ``10×3 + 5×6 = 60`` → 每 10000/5000 token 组合 = 60 cents；按比例拆分多次写入。
    """
    remaining = float(cents_target)
    index = 0
    while remaining > 1e-9:
        chunk = min(60.0, remaining)
        tokens_in = int(round(chunk / 60.0 * 10000))
        tokens_out = int(round(chunk / 60.0 * 5000))
        U.record_cost(model="gpt-4", tokens_in=tokens_in, tokens_out=tokens_out,
                      interaction_id=f"{tag}-{day}-{index}", store=store,
                      ts=f"{day}T{hour:02d}:00:00.000+08:00")
        remaining -= chunk
        index += 1


# ════════════════════════════════════════════════════════════
#  1. 配置：默认关闭 / 非法值回退 / 优先级
# ════════════════════════════════════════════════════════════


class TestConfig:
    def test_default_is_disabled_zero_impact(self):
        cfg = CB.load_config({})
        assert cfg.enabled is False
        assert cfg.bypassed is True
        assert cfg.daily_cents == 0.0
        assert cfg.day_breaker_configured is False

    def test_invalid_bool_falls_back_to_disabled(self):
        """非法布尔值**不得**被 bool(非空串) 误判为开启（默认不误伤）"""
        assert CB.load_config({CB.ENV_ENABLED: "maybe"}).enabled is False
        assert CB.load_config({CB.ENV_ENABLED: "或许"}).enabled is False

    def test_explicit_true_enables(self):
        assert CB.load_config({CB.ENV_ENABLED: "true"}).enabled is True
        assert CB.load_config({CB.ENV_ENABLED: "1"}).enabled is True

    def test_daily_cents_from_env(self):
        cfg = CB.load_config({CB.ENV_ENABLED: "1", CB.ENV_DAILY_CENTS: "5000"})
        assert cfg.daily_cents == 5000.0
        assert cfg.day_breaker_configured is True

    def test_invalid_daily_cents_falls_back(self):
        cfg = CB.load_config({CB.ENV_ENABLED: "1", CB.ENV_DAILY_CENTS: "abc"})
        assert cfg.daily_cents == 0.0
        cfg2 = CB.load_config({CB.ENV_ENABLED: "1", CB.ENV_DAILY_CENTS: "-5"})
        assert cfg2.daily_cents == 0.0

    def test_invalid_ratios_fall_back_to_spec_defaults(self):
        cfg = CB.load_config({CB.ENV_ENABLED: "1", CB.ENV_FASTING_IN_RATIO: "x",
                              CB.ENV_FASTING_OUT_RATIO: "-1",
                              CB.ENV_FASTING_OUT_HOURS: "0",
                              CB.ENV_COOLDOWN_HOURS: "abc"})
        assert cfg.fasting_in_ratio == CB.DEFAULT_FASTING_IN_RATIO
        assert cfg.fasting_out_ratio == CB.DEFAULT_FASTING_OUT_RATIO
        assert cfg.fasting_out_hours == CB.DEFAULT_FASTING_OUT_HOURS
        assert cfg.cooldown_hours == CB.DEFAULT_COOLDOWN_HOURS

    def test_invalid_consecutive_and_baseline_days_fall_back(self):
        cfg = CB.load_config({CB.ENV_ENABLED: "1", CB.ENV_FASTING_IN_CONSECUTIVE: "0",
                              CB.ENV_BASELINE_DAYS: "-3"})
        assert cfg.fasting_in_consecutive == CB.DEFAULT_FASTING_IN_CONSECUTIVE
        assert cfg.baseline_days == CB.DEFAULT_BASELINE_DAYS

    def test_shadow_factor_out_of_range_falls_back(self):
        cfg = CB.load_config({CB.ENV_ENABLED: "1",
                              CB.ENV_SHADOW_FACTOR_FASTING: "3.0"})
        assert cfg.shadow_factor_fasting == CB.DEFAULT_SHADOW_FACTOR_FASTING
        assert any("越界" in w for w in cfg.warnings)

    def test_shadow_factor_half_is_accepted(self):
        """「减半」也是合法配置（§7 断食期 shadow 预算归零/减半）"""
        cfg = CB.load_config({CB.ENV_ENABLED: "1",
                              CB.ENV_SHADOW_FACTOR_FASTING: "0.5"})
        assert cfg.shadow_factor_fasting == 0.5

    def test_invalid_fasting_signal_falls_back(self):
        cfg = CB.load_config({CB.ENV_ENABLED: "1", CB.ENV_FASTING_SIGNAL: "nope"})
        assert cfg.fasting_signal == CB.SIGNAL_DAILY_COST

    def test_week_high_overrides_fasting_in(self):
        cfg = CB.load_config({CB.ENV_ENABLED: "1", CB.ENV_WEEKLY_UTC_HIGH: "1.8"})
        assert cfg.weekly_utc_high == 1.8

    def test_config_sources_are_recorded(self):
        cfg = CB.load_config({CB.ENV_ENABLED: "1", CB.ENV_DAILY_CENTS: "10"})
        assert cfg.sources[CB.ENV_ENABLED] == "env"
        assert cfg.sources[CB.ENV_FASTING_IN_RATIO] == "default"

    def test_config_yaml_section_is_readable(self):
        """`config.yaml:budget` 段读得到 dict（无段/不可读 → 空 dict，不抛）"""
        assert isinstance(CB._config_yaml_budget(), dict)


# ════════════════════════════════════════════════════════════
#  2. 阶段推断与 θ 分阶段阈值（§6.3）
# ════════════════════════════════════════════════════════════


class TestThetaStages:
    def test_before_start_is_w1_w4(self):
        info = CB.resolve_phase(BASE, phase_start="2026-10-01")
        assert info["phase"] == CB.PHASE_W1_W4

    def test_week_5_and_9_map_to_w5_w9(self):
        start = (BASE.date() - timedelta(days=28)).isoformat()   # 第 5 周首日
        info = CB.resolve_phase(BASE, phase_start=start)
        assert info["phase"] == CB.PHASE_W5_W9
        assert info["week"] == 5
        week9 = BASE + timedelta(days=28)
        assert CB.resolve_phase(week9, phase_start=start)["phase"] == CB.PHASE_W5_W9

    def test_week_10_maps_to_w10_w14(self):
        info = CB.resolve_phase(BASE, phase_start=(BASE.date() - timedelta(days=63)).isoformat())
        assert info["phase"] == CB.PHASE_W10_W14

    def test_week_15_rolls_into_m4_stage(self):
        """W15（≈98 天 ≈ 第 4 月）已进入 M4-M6 段 —— **月优先于周**，无空档"""
        info = CB.resolve_phase(BASE, phase_start=(BASE.date() - timedelta(days=98)).isoformat())
        assert info["week"] == 15
        assert info["month"] == 4
        assert info["phase"] == CB.PHASE_M4_M6

    def test_month_boundary_is_month_based(self):
        """用明确的月份边界验证 M4/M7（避免依赖天数折算）"""
        start = "2026-01-01"
        m4 = datetime(2026, 4, 15, 10, 0, tzinfo=BASE.tzinfo)
        m7 = datetime(2026, 7, 15, 10, 0, tzinfo=BASE.tzinfo)
        assert CB.resolve_phase(m4, phase_start=start)["phase"] == CB.PHASE_M4_M6
        assert CB.resolve_phase(m7, phase_start=start)["phase"] == CB.PHASE_M7_PLUS

    def test_default_phase_when_not_configured(self):
        assert CB.resolve_phase(BASE)["phase"] == CB.PHASE_W1_W4
        assert CB.resolve_phase(BASE)["source"] == "default"

    def test_w1_w4_has_no_theta(self):
        """§6.3：W1-W4 不设 θ"""
        out = CB.theta_limit(BASE, config=CB.load_config({}))
        assert out["limit"] is None
        assert out["phase"] == CB.PHASE_W1_W4

    def test_w5_w9_tightens_within_stage(self):
        cfg = CB.load_config({CB.ENV_PHASE_START: "2026-08-17"})
        early = CB.theta_limit(datetime(2026, 9, 14, tzinfo=BASE.tzinfo), config=cfg)
        late = CB.theta_limit(datetime(2026, 10, 12, tzinfo=BASE.tzinfo), config=cfg)
        assert early["phase"] == CB.PHASE_W5_W9
        assert early["limit"] == pytest.approx(1.5)
        assert late["limit"] < early["limit"]
        assert late["limit"] == pytest.approx(1.3)

    def test_explicit_phase_uses_stage_start(self):
        cfg = CB.load_config({CB.ENV_PHASE: CB.PHASE_M7_PLUS})
        out = CB.theta_limit(BASE, config=cfg)
        assert out["phase"] == CB.PHASE_M7_PLUS
        assert out["limit"] == pytest.approx(0.5)

    def test_m4_m6_table(self):
        cfg = CB.load_config({CB.ENV_PHASE: CB.PHASE_M4_M6})
        assert CB.theta_limit(BASE, config=cfg)["limit"] == pytest.approx(0.8)
        assert CB.theta_limit(BASE, config=cfg)["end"] == pytest.approx(0.6)

    def test_theta_table_override_from_env(self):
        table = json.dumps({CB.PHASE_W5_W9: {"start": 2.0, "end": 1.4}})
        cfg = CB.load_config({CB.ENV_THETA_TABLE: table,
                              CB.ENV_PHASE: CB.PHASE_W5_W9})
        assert cfg.theta_source == "env"
        assert CB.theta_limit(BASE, config=cfg)["limit"] == pytest.approx(2.0)

    def test_theta_table_partial_override_keeps_other_stages(self):
        table = json.dumps({CB.PHASE_M7_PLUS: 0.4})
        cfg = CB.load_config({CB.ENV_THETA_TABLE: table})
        assert cfg.theta_table[CB.PHASE_M7_PLUS] == {"start": 0.4, "end": 0.4}
        assert cfg.theta_table[CB.PHASE_W10_W14] == {"start": 1.0, "end": 1.0}

    def test_theta_table_invalid_json_falls_back(self):
        cfg = CB.load_config({CB.ENV_THETA_TABLE: "{not json}"})
        assert cfg.theta_source == "default"
        assert cfg.theta_table[CB.PHASE_W5_W9] == {"start": 1.5, "end": 1.3}

    def test_theta_table_unknown_phase_ignored(self):
        cfg = CB.load_config({CB.ENV_THETA_TABLE: json.dumps({"w99": 1.0})})
        assert any("未登记阶段" in w for w in cfg.warnings)

    def test_theta_table_negative_ignored(self):
        cfg = CB.load_config({CB.ENV_THETA_TABLE: json.dumps({CB.PHASE_W5_W9: -1.0})})
        assert cfg.theta_table[CB.PHASE_W5_W9] == {"start": 1.5, "end": 1.3}


# ════════════════════════════════════════════════════════════
#  3. 断食状态机（§7：进 / 出 / 冷却）
# ════════════════════════════════════════════════════════════


class TestFastingMachine:
    def test_normal_to_fasting_after_consecutive_high(self):
        m = CB.FastingMachine(in_consecutive=2)
        first = m.observe(2.0, at(0, 1))
        assert first["transition"] is False            # 单点尖峰不触发
        second = m.observe(2.0, at(0, 2))
        assert second["transition"] is True
        assert m.state == CB.STATE_FASTING
        assert m.restricted is True
        assert m.snapshot.entered_at                    # 进入时间可观测
        assert m.snapshot.threshold_source

    def test_single_spike_does_not_enter(self):
        m = CB.FastingMachine(in_consecutive=2)
        m.observe(5.0, at(0, 1))
        m.observe(0.5, at(0, 2))
        assert m.state == CB.STATE_NORMAL

    def test_none_ratio_does_not_advance(self):
        m = CB.FastingMachine(in_consecutive=1)
        out = m.observe(None, at(0, 1))
        assert out["transition"] is False
        assert m.state == CB.STATE_NORMAL
        assert "不臆断" in out["reason"]

    def test_nonnumeric_ratio_does_not_raise(self):
        m = CB.FastingMachine(in_consecutive=1)
        out = m.observe("boom", at(0, 1))
        assert out["transition"] is False
        assert m.state == CB.STATE_NORMAL

    def test_no_exit_before_24h(self):
        m = CB.FastingMachine(in_consecutive=1, out_hours=24.0)
        m.observe(2.0, at(0, 1))
        assert m.state == CB.STATE_FASTING
        m.observe(1.0, at(0, 2))                       # 开始低于出阈值计时
        m.observe(1.0, at(0, 12))                      # 仅 10h
        assert m.state == CB.STATE_FASTING
        m.observe(1.0, at(1, 1))                       # 23h
        assert m.state == CB.STATE_FASTING

    def test_exit_after_24h_low(self):
        m = CB.FastingMachine(in_consecutive=1, out_hours=24.0, cooldown_hours=12.0)
        m.observe(2.0, at(0, 1))
        m.observe(1.0, at(0, 2))                       # 计时起点
        out = m.observe(1.0, at(1, 2))                 # 恰好 24h
        assert out["transition"] is True
        assert m.state == CB.STATE_COOLDOWN
        assert m.restricted is False                   # 冷却期**不降本**，只抑制重进
        assert m.snapshot.cooldown_until

    def test_high_ratio_resets_low_timer(self):
        m = CB.FastingMachine(in_consecutive=1, out_hours=24.0)
        m.observe(2.0, at(0, 1))
        m.observe(1.0, at(0, 2))                       # 计时起点
        m.observe(3.0, at(0, 12))                      # 反弹 → 清零
        assert m.snapshot.below_since == ""
        m.observe(1.0, at(1, 12))                      # 重新计时
        assert m.state == CB.STATE_FASTING

    def test_cooldown_suppresses_reentry(self):
        m = CB.FastingMachine(in_consecutive=1, out_hours=0.0, cooldown_hours=12.0)
        m.observe(2.0, at(0, 1))
        m.observe(1.0, at(0, 2))                       # out_hours=0 → 立即退出
        assert m.state == CB.STATE_COOLDOWN
        m.observe(9.9, at(0, 3))                       # 冷却期内高比值**不重进**
        assert m.state == CB.STATE_COOLDOWN
        assert m.restricted is False

    def test_cooldown_expires_to_normal(self):
        m = CB.FastingMachine(in_consecutive=1, out_hours=0.0, cooldown_hours=12.0)
        m.observe(2.0, at(0, 1))
        m.observe(1.0, at(0, 2))                       # 02:00 退出 → 冷却至当日 14:00
        assert m.state == CB.STATE_COOLDOWN
        m.observe(1.0, at(0, 13))
        assert m.state == CB.STATE_COOLDOWN            # 尚未到 12h
        m.observe(1.0, at(0, 15))                      # 12h 后
        assert m.state == CB.STATE_NORMAL

    def test_snapshot_round_trip(self):
        m = CB.FastingMachine(in_consecutive=1)
        m.observe(2.0, at(0, 1))
        payload = json.loads(json.dumps(m.to_dict()))
        fresh = CB.FastingMachine()
        fresh.restore(payload)
        assert fresh.state == CB.STATE_FASTING
        assert fresh.snapshot.entered_at == m.snapshot.entered_at

    def test_restore_tolerates_garbage(self):
        m = CB.FastingMachine()
        m.restore({"state": "bogus", "consecutive_high": "x"})
        assert m.state == CB.STATE_NORMAL
        m.restore(None)


# ════════════════════════════════════════════════════════════
#  4. 日级硬熔断（P7.2-06）
# ════════════════════════════════════════════════════════════


class TestDailyBreaker:
    def _brake(self, events_dir, *, daily_cents, **kw):
        return make_brake(events_dir, enabled=True, daily_cents=daily_cents,
                          state_path="", **kw)

    def test_zero_impact_below_budget(self, events_dir, store):
        """未超阈值 → 零影响（防误伤）"""
        seed_cost(store, day=BASE.date().isoformat(), cents_target=30)
        brake = self._brake(events_dir, daily_cents=100)
        status = brake.evaluate(at(0))
        assert status["day_breaker_open"] is False
        assert status["daily_cost_cents"] == pytest.approx(30.0)
        assert brake.allow_outbound(kind="shadow") is True
        assert brake.allow_outbound(kind="digestion") is True
        assert brake.allow_outbound(kind="interactive") is True

    def test_over_budget_opens_and_blocks_non_critical(self, events_dir, store):
        seed_cost(store, day=BASE.date().isoformat(), cents_target=180)
        brake = self._brake(events_dir, daily_cents=100)
        status = brake.evaluate(at(0))
        assert status["day_breaker_open"] is True
        assert status["resume_at"]                    # 恢复时点可观测
        assert brake.allow_outbound(kind="shadow") is False
        assert brake.allow_outbound(kind="digestion") is False
        assert brake.allow_outbound(kind="speculative") is False

    def test_critical_paths_never_blocked(self, events_dir, store):
        """硬约束 1：用户显式请求 / 审批中任务**不得**被熔断"""
        seed_cost(store, day=BASE.date().isoformat(), cents_target=180)
        brake = self._brake(events_dir, daily_cents=100)
        brake.evaluate(at(0))
        assert brake.allow_outbound(kind="interactive") is True
        assert brake.allow_outbound(kind="user_request") is True
        assert brake.allow_outbound(kind="approval_pending") is True
        assert brake.allow_outbound(kind="task_execution") is True
        assert brake.allow_outbound(kind="safety") is True
        assert brake.allow_outbound(critical=True, kind="anything") is True

    def test_default_kind_is_never_blocked(self, events_dir, store):
        """默认调用方（未声明后台 kind）恒放行 —— 默认不误伤"""
        seed_cost(store, day=BASE.date().isoformat(), cents_target=180)
        brake = self._brake(events_dir, daily_cents=100)
        brake.evaluate(at(0))
        assert brake.allow_outbound() is True

    def test_next_day_auto_recovery(self, events_dir, store):
        """次日 00:00 自动恢复（§7）"""
        day1 = BASE.date().isoformat()
        day2 = (BASE + timedelta(days=1)).date().isoformat()
        seed_cost(store, day=day1, cents_target=180)
        brake = self._brake(events_dir, daily_cents=100)
        assert brake.evaluate(at(0))["day_breaker_open"] is True
        assert brake.allow_outbound(kind="shadow", now=at(1, 0)) is True
        status = brake.status()
        assert status["day_breaker_open"] is False
        assert status["day_breaker_opened_day"] == ""
        # 次日无成本 → 当日成本归零
        assert status["day"] == day2
        assert status["daily_cost_cents"] == pytest.approx(0.0)

    def test_same_day_returns_below_budget_releases(self, events_dir, store):
        seed_cost(store, day=BASE.date().isoformat(), cents_target=180)
        brake = self._brake(events_dir, daily_cents=100)
        assert brake.evaluate(at(0))["day_breaker_open"] is True
        # 阈值被调高 → 当日回到阈值内 → 释放（不留"锁死当日"的坑）
        brake.config.daily_cents = 1000
        assert brake.evaluate(at(0, 11))["day_breaker_open"] is False

    def test_budget_removed_releases(self, events_dir, store):
        seed_cost(store, day=BASE.date().isoformat(), cents_target=180)
        brake = self._brake(events_dir, daily_cents=100)
        assert brake.evaluate(at(0))["day_breaker_open"] is True
        brake.config.daily_cents = 0.0
        assert brake.evaluate(at(0, 11))["day_breaker_open"] is False
        assert brake.allow_outbound(kind="shadow") is True

    def test_dry_run_never_blocks(self, events_dir, store):
        """干跑：照常判定与留痕，但**不拦截**（灰度上线用）"""
        seed_cost(store, day=BASE.date().isoformat(), cents_target=180)
        brake = self._brake(events_dir, daily_cents=100, dry_run=True)
        status = brake.evaluate(at(0))
        assert status["day_breaker_open"] is True
        assert brake.allow_outbound(kind="shadow") is True
        assert brake.suppression()["restricted"] is False

    def test_no_budget_configured_never_opens(self, events_dir, store):
        seed_cost(store, day=BASE.date().isoformat(), cents_target=9999)
        brake = self._brake(events_dir, daily_cents=0.0)
        assert brake.evaluate(at(0))["day_breaker_open"] is False
        assert brake.allow_outbound(kind="shadow") is True

    def test_transition_emits_audit_and_events(self, events_dir, store):
        """熔断触发 → 审计 + healing.triggered + metrics.delta"""
        seed_cost(store, day=BASE.date().isoformat(), cents_target=180)
        brake = self._brake(events_dir, daily_cents=100)
        status = brake.evaluate(at(0))
        assert status["audit_seq"] >= 0
        types = [e.type for e in ev.read_events(directory=events_dir)]
        assert ev.EV_HEALING_TRIGGERED in types
        assert ev.EV_METRICS_DELTA in types
        healing = [e for e in ev.read_events(directory=events_dir)
                   if e.type == ev.EV_HEALING_TRIGGERED][0]
        assert healing.payload["type"] == "cost.daily_breaker"
        assert healing.payload["action"] == "stop_non_critical_outbound"

    def test_recovery_emits_resume_event(self, events_dir, store):
        seed_cost(store, day=BASE.date().isoformat(), cents_target=180)
        brake = self._brake(events_dir, daily_cents=100)
        brake.evaluate(at(0))
        brake.evaluate(at(1))
        rows = [e for e in ev.read_events(directory=events_dir)
                if e.type == ev.EV_HEALING_TRIGGERED]
        actions = {e.payload.get("action") for e in rows}
        assert actions == {"stop_non_critical_outbound", "resume"}

    def test_state_persisted_and_restored(self, events_dir, store, tmp_path):
        seed_cost(store, day=BASE.date().isoformat(), cents_target=180)
        path = str(tmp_path / "brake_state.json")
        cfg = CB.load_config({})
        cfg.enabled, cfg.daily_cents = True, 100.0
        first = CB.CostBrake(config=cfg, events_dir=events_dir, state_path=path)
        first.evaluate(at(0))
        assert os.path.exists(path)
        second = CB.CostBrake(config=cfg, events_dir=events_dir, state_path=path)
        assert second.status()["day_breaker_open"] is True

    def test_block_reason_is_explicit(self, events_dir, store):
        seed_cost(store, day=BASE.date().isoformat(), cents_target=180)
        brake = self._brake(events_dir, daily_cents=100)
        brake.evaluate(at(0))
        reason = brake.block_reason(kind="shadow")
        assert "熔断" in reason and "shadow" in reason
        assert brake.block_reason(kind="interactive") == ""


# ════════════════════════════════════════════════════════════
#  5. 周级断食（§7）+ shadow 预算联动
# ════════════════════════════════════════════════════════════


class TestFastingIntegration:
    def _brake(self, events_dir, **kw):
        kw.setdefault("enabled", True)
        kw.setdefault("baseline_cents", 60.0)
        kw.setdefault("fasting_in_consecutive", 1)
        return make_brake(events_dir, state_path="", **kw)

    def test_high_ratio_enters_fasting(self, events_dir, store):
        seed_cost(store, day=BASE.date().isoformat(), cents_target=180)  # ratio 3.0
        brake = self._brake(events_dir)
        status = brake.evaluate(at(0))
        assert status["ratio"] == pytest.approx(3.0)
        assert status["fasting"]["state"] == CB.STATE_FASTING
        assert status["fasting"]["entered_at"]
        assert status["entry_threshold_source"]
        assert brake.suppression()["restricted"] is True

    def test_fasting_suppresses_shadow_and_digestion_kinds(self, events_dir, store):
        seed_cost(store, day=BASE.date().isoformat(), cents_target=180)
        brake = self._brake(events_dir)
        brake.evaluate(at(0))
        assert brake.allow_outbound(kind="shadow") is False
        assert brake.allow_outbound(kind="digestion") is False
        assert brake.allow_outbound(kind="internalize") is False
        # 断食是**降本**不是硬停：用户请求与审批仍放行
        assert brake.allow_outbound(kind="interactive") is True
        assert brake.allow_outbound(kind="approval_pending") is True

    def test_low_ratio_stays_normal(self, events_dir, store):
        seed_cost(store, day=BASE.date().isoformat(), cents_target=60)   # ratio 1.0
        brake = self._brake(events_dir)
        status = brake.evaluate(at(0))
        assert status["fasting"]["state"] == CB.STATE_NORMAL
        assert brake.suppression()["restricted"] is False

    def test_no_baseline_leaves_ratio_none(self, events_dir, store):
        brake = self._brake(events_dir, baseline_cents=0.0)
        status = brake.evaluate(at(0))
        assert status["ratio"] is None
        assert status["fasting"]["state"] == CB.STATE_NORMAL

    def test_shadow_budget_factor_is_zero_during_fasting(self, events_dir, store):
        seed_cost(store, day=BASE.date().isoformat(), cents_target=180)
        brake = self._brake(events_dir)
        brake.evaluate(at(0))
        snap = brake.suppression()
        assert snap["shadow_budget_factor"] == 0.0
        assert "shadow" in snap["blocked_kinds"]

    def test_shadow_budget_factor_half_when_configured(self, events_dir, store):
        seed_cost(store, day=BASE.date().isoformat(), cents_target=180)
        brake = self._brake(events_dir, shadow_factor_fasting=0.5)
        brake.evaluate(at(0))
        assert brake.suppression()["shadow_budget_factor"] == 0.5

    def test_day_breaker_also_suppresses_shadow(self, events_dir, store):
        seed_cost(store, day=BASE.date().isoformat(), cents_target=180)
        brake = self._brake(events_dir, daily_cents=100.0)
        brake.evaluate(at(0))
        assert brake.suppression()["shadow_budget_factor"] == 0.0

    def test_fasting_state_observable_and_audited(self, events_dir, store):
        seed_cost(store, day=BASE.date().isoformat(), cents_target=180)
        brake = self._brake(events_dir)
        status = brake.evaluate(at(0))
        fasting = status["fasting"]
        for field in ("state", "entered_at", "exited_at", "cooldown_until",
                      "threshold_source", "last_ratio", "transitions"):
            assert field in fasting, field
        types = [e.type for e in ev.read_events(directory=events_dir)]
        assert ev.EV_METRICS_DELTA in types

    def test_theta_tightens_fasting_entry(self, events_dir, store):
        """θ 严于 1.3× 时取更严者（配置驱动）"""
        seed_cost(store, day=BASE.date().isoformat(), cents_target=72)   # ratio 1.2
        theta = CB.load_config(
            {CB.ENV_THETA_TABLE: json.dumps({CB.PHASE_W1_W4: 1.1})}).theta_table
        brake = self._brake(events_dir, theta_table=theta,
                            theta_binds_fasting=True, phase=CB.PHASE_W1_W4)
        status = brake.evaluate(at(0))
        assert status["entry_threshold"] == pytest.approx(1.1)
        assert "θ" in status["entry_threshold_source"]
        assert status["fasting"]["state"] == CB.STATE_FASTING

    def test_theta_not_binding_when_disabled(self, events_dir, store):
        seed_cost(store, day=BASE.date().isoformat(), cents_target=72)   # ratio 1.2
        theta = CB.load_config(
            {CB.ENV_THETA_TABLE: json.dumps({CB.PHASE_W1_W4: 1.1})}).theta_table
        brake = self._brake(events_dir, theta_table=theta,
                            theta_binds_fasting=False, phase=CB.PHASE_W1_W4)
        status = brake.evaluate(at(0))
        assert status["entry_threshold"] == pytest.approx(1.3)
        assert status["fasting"]["state"] == CB.STATE_NORMAL

    def test_utc_signal_variant(self, events_dir, store):
        """可切换为 per-task UTC 信号（口径可配置，输出带来源）"""
        day = BASE.date().isoformat()
        seed_cost(store, day=day, cents_target=180)
        acr.record_task_closed(task_id="t1", status=acr.STATUS_CLOSED, intent="fix",
                               store=store, ts=f"{day}T10:00:00+08:00")
        brake = self._brake(events_dir, fasting_signal=CB.SIGNAL_UTC_PER_TASK)
        status = brake.evaluate(at(0))
        assert status["utc_cents_per_task"] == pytest.approx(180.0)

    def test_weekly_context_reported(self, events_dir, store):
        seed_cost(store, day=BASE.date().isoformat(), cents_target=60)
        brake = self._brake(events_dir)
        status = brake.evaluate(at(0))
        assert status["weekly"]["iso_week"]
        assert status["weekly"]["cost_normalized_cents"] == pytest.approx(60.0)

    def test_theta_breach_disclosure(self, events_dir, store):
        day = BASE.date().isoformat()
        seed_cost(store, day=day, cents_target=180)
        acr.record_task_closed(task_id="t1", status=acr.STATUS_CLOSED, intent="fix",
                               store=store, ts=f"{day}T10:00:00+08:00")
        brake = self._brake(events_dir, phase=CB.PHASE_M7_PLUS)
        status = brake.evaluate(at(0))
        assert status["theta"]["limit"] == pytest.approx(0.5)


# ════════════════════════════════════════════════════════════
#  6. 零影响（总开关关闭时的硬断言）
# ════════════════════════════════════════════════════════════


class TestZeroImpactWhenDisabled:
    def test_evaluate_is_disabled(self, events_dir, store):
        seed_cost(store, day=BASE.date().isoformat(), cents_target=99999)
        brake = make_brake(events_dir, enabled=False, daily_cents=1.0,
                           state_path="")
        status = brake.evaluate(at(0))
        assert status["enabled"] is False
        assert status["day_breaker_open"] is False
        assert "默认关闭" in status["reason"]

    def test_nothing_blocked(self, events_dir, store):
        seed_cost(store, day=BASE.date().isoformat(), cents_target=99999)
        brake = make_brake(events_dir, enabled=False, daily_cents=1.0,
                           state_path="")
        for kind in list(CB.BACKGROUND_KINDS) + ["interactive"]:
            assert brake.allow_outbound(kind=kind) is True

    def test_suppression_is_neutral(self, events_dir):
        brake = make_brake(events_dir, enabled=False, state_path="")
        snap = brake.suppression()
        assert snap["restricted"] is False
        assert snap["shadow_budget_factor"] == 1.0
        assert snap["blocked_kinds"] == []

    def test_no_events_emitted(self, events_dir, store):
        seed_cost(store, day=BASE.date().isoformat(), cents_target=99999)
        before = len(ev.read_events(directory=events_dir))
        brake = make_brake(events_dir, enabled=False, daily_cents=1.0,
                           state_path="")
        brake.evaluate(at(0))
        assert len(ev.read_events(directory=events_dir)) == before

    def test_no_state_file_written(self, events_dir, tmp_path):
        path = str(tmp_path / "should_not_exist.json")
        cfg = CB.load_config({})
        cfg.enabled, cfg.daily_cents, cfg.state_path = False, 0.0, path
        brake = CB.CostBrake(config=cfg, events_dir=events_dir, state_path=path)
        brake.evaluate(at(0))
        assert not os.path.exists(path)

    def test_module_level_helpers_are_neutral(self, events_dir, monkeypatch):
        monkeypatch.setenv(ev.ENV_DIR, events_dir)
        CB.reset_cost_brake()
        assert CB.allow_outbound(kind="shadow") is True
        assert CB.shadow_budget_factor() == 1.0
        assert CB.digestion_restricted() == (False, "")


# ════════════════════════════════════════════════════════════
#  7. 门面协作：异常 fail-open / 守卫 / 单例
# ════════════════════════════════════════════════════════════


class TestFacadeBehaviour:
    def test_evaluate_failure_is_fail_open(self, events_dir, monkeypatch):
        """判定异常 → 不拦截（新增机制失败不得阻断主流程）"""
        brake = make_brake(events_dir, enabled=True, daily_cents=1.0, state_path="")

        def boom(*_a, **_kw):
            raise RuntimeError("disk on fire")

        monkeypatch.setattr(CB._utc, "utc_daily", boom)
        assert brake.allow_outbound(kind="shadow", now=at(0)) is True
        assert "fail-open" in brake.status()["reason"]

    def test_outbound_guard_raises_when_blocked(self, events_dir, store):
        seed_cost(store, day=BASE.date().isoformat(), cents_target=180)
        brake = make_brake(events_dir, enabled=True, daily_cents=100.0,
                           state_path="")
        brake.evaluate(at(0))
        with pytest.raises(CB.OutboundBlockedError) as info:
            with CB.outbound_guard(kind="shadow", brake=brake, now=at(0)):
                pass
        assert "熔断" in str(info.value)

    def test_outbound_guard_passes_critical(self, events_dir, store):
        seed_cost(store, day=BASE.date().isoformat(), cents_target=180)
        brake = make_brake(events_dir, enabled=True, daily_cents=100.0,
                           state_path="")
        brake.evaluate(at(0))
        with CB.outbound_guard(kind="interactive", brake=brake, now=at(0)):
            reached = True
        assert reached

    def test_singleton_and_reset(self, monkeypatch, tmp_path):
        monkeypatch.setenv(ev.ENV_DIR, str(tmp_path / "events"))
        monkeypatch.setenv(CB.ENV_STATE_PATH, "")
        CB.reset_cost_brake()
        first = CB.get_cost_brake()
        assert CB.get_cost_brake() is first
        reloaded = CB.get_cost_brake(reload=True)
        assert reloaded is not first
        CB.reset_cost_brake()

    def test_reset_clears_state(self, events_dir, store, tmp_path):
        seed_cost(store, day=BASE.date().isoformat(), cents_target=180)
        path = str(tmp_path / "state.json")
        cfg = CB.load_config({})
        cfg.enabled, cfg.daily_cents = True, 100.0
        brake = CB.CostBrake(config=cfg, events_dir=events_dir, state_path=path)
        brake.evaluate(at(0))
        assert brake.status()["day_breaker_open"] is True
        brake.reset()
        assert brake.status()["day_breaker_open"] is False
        assert not os.path.exists(path)


# ════════════════════════════════════════════════════════════
#  8. 口径：shadow 开销 / 审批衰减率
# ════════════════════════════════════════════════════════════


class TestCalibrationAndShadowOverhead:
    def test_shadow_ms_recorded_in_utc_scope(self, events_dir, store):
        day = BASE.date().isoformat()
        U.record_cost(model="gpt-4", tokens_in=1000, tokens_out=0,
                      shadow_overhead_ms=500.0, shadow_overhead_cents=2.0,
                      interaction_id="s1", store=store, ts=f"{day}T10:00:00+08:00")
        audit = CB.shadow_overhead_audit(day=day, directory=events_dir)
        assert audit["shadow_overhead_ms"] == pytest.approx(500.0)
        assert audit["shadow_overhead_cents_direct"] == pytest.approx(2.0)
        assert audit["in_utc_scope"] is True

    def test_shadow_ms_not_priced_by_default(self, events_dir, store):
        """未配置机时费率 → 如实标注「已计量、未计价」，**不臆造金额**"""
        day = BASE.date().isoformat()
        U.record_cost(model="gpt-4", tokens_in=1000, shadow_overhead_ms=5000.0,
                      interaction_id="s2", store=store, ts=f"{day}T10:00:00+08:00")
        audit = CB.shadow_overhead_audit(day=day, directory=events_dir)
        assert audit["priced"] is False
        assert audit["shadow_overhead_cents_from_ms"] == 0.0
        assert "未折价" in audit["note"]

    def test_shadow_ms_priced_when_rate_configured(self, events_dir, store):
        day = BASE.date().isoformat()
        U.record_cost(model="gpt-4", tokens_in=1000, shadow_overhead_ms=2000.0,
                      interaction_id="s3", store=store, ts=f"{day}T10:00:00+08:00")
        cfg = CB.load_config({CB.ENV_SHADOW_MS_CENTS_PER_S: "10"})
        audit = CB.shadow_overhead_audit(day=day, directory=events_dir, config=cfg)
        assert audit["priced"] is True
        assert audit["shadow_overhead_cents_from_ms"] == pytest.approx(20.0)

    def test_shadow_ms_cents_enters_breaker_cost(self, events_dir, store):
        """shadow 机时折价后**参与熔断判定**（不漏项）"""
        day = BASE.date().isoformat()
        U.record_cost(model="gpt-4", tokens_in=10000, tokens_out=5000,
                      shadow_overhead_ms=100000.0, interaction_id="s4",
                      store=store, ts=f"{day}T10:00:00+08:00")
        brake = make_brake(events_dir, enabled=True, daily_cents=100.0,
                           shadow_ms_cents_per_s=10.0, state_path="")
        status = brake.evaluate(at(0))
        # 60（token）+ 1000×10/1000... 100000ms=100s ×10 cents/s = 1000 cents
        assert status["daily_cost_cents"] == pytest.approx(60.0 + 1000.0)
        assert status["day_breaker_open"] is True

    def test_calibration_version_is_labelled_everywhere(self, events_dir, store):
        """裁定 C：口径版本与「沿用锚价系数、待 L2 校准」标注必须出现在输出里"""
        brake = make_brake(events_dir, state_path="")
        status = brake.evaluate(at(0))
        assert status["calibration_version"] == CB.CALIBRATION_VERSION
        assert status["cost_schema_version"] == CB.COST_SCHEMA_VERSION
        assert "价格锚定系数" in status["calibration_note"]
        assert status["source_of_truth"] == "events"
        assert "L2 Core-50" in CB.CALIBRATION_TRIGGER


class TestApprovalDecay:
    def _seed_approvals(self, store, *, auto, human, day=None):
        day = day or BASE.date().isoformat()
        ts = f"{day}T10:00:00.000+08:00"
        for index in range(auto):
            acr.record_approval(kind="auto_pass", record_id=f"a{index}",
                                state="approved", actor=ev.ACTOR_AUTO,
                                count_intervention=False, store=store, ts=ts)
        for index in range(human):
            acr.record_approval(kind="approve", record_id=f"h{index}",
                                state="approved", actor=ev.ACTOR_HUMAN,
                                latency_ms=1200.0, store=store, ts=ts)

    def test_decay_rate_computed(self, events_dir, store):
        self._seed_approvals(store, auto=7, human=3)
        out = CB.approval_decay_rate(days=30, directory=events_dir)
        assert out["total_disposed"] == 10
        assert out["auto_disposed"] == 7
        assert out["decay_rate"] == pytest.approx(0.7)
        assert out["meets_target"] is True
        assert out["target"] == CB.APPROVAL_DECAY_TARGET
        assert out["disclosure_only"] is True

    def test_below_target_disclosed_not_enforced(self, events_dir, store):
        self._seed_approvals(store, auto=1, human=9)
        out = CB.approval_decay_rate(days=30, directory=events_dir)
        assert out["meets_target"] is False
        assert out["insufficient_sample"] is True     # 10 < 20：只披露不下结论

    def test_empty_window_is_honest(self, events_dir):
        out = CB.approval_decay_rate(days=30, directory=events_dir)
        assert out["decay_rate"] is None
        assert out["meets_target"] is None
        assert out["total_disposed"] == 0

    def test_fatigue_buckets_and_latency(self, events_dir, store):
        self._seed_approvals(store, auto=0, human=3)
        out = CB.approval_decay_rate(day=BASE.date().isoformat(),
                                     directory=events_dir)
        assert out["total_disposed"] == 3
        assert out["latency_median_ms"] == pytest.approx(1200.0)
        assert sum(out["fatigue_buckets"].values()) == 3

    def test_replay_not_double_counted(self, events_dir, store):
        self._seed_approvals(store, auto=2, human=2)
        self._seed_approvals(store, auto=2, human=2)
        out = CB.approval_decay_rate(days=30, directory=events_dir)
        assert out["total_disposed"] == 4


# ════════════════════════════════════════════════════════════
#  9. S3 shadow 预算联动（断食期归零/减半**实际生效**）
# ════════════════════════════════════════════════════════════


class TestShadowBudgetLinkage:
    """验收项「断食期 S3 shadow 预算联动（归零/减半生效）」"""

    def test_default_factor_is_no_impact(self):
        """成本刹车未开启 → shadow 预算逐字不变（回归保护）"""
        from agent.digestion import shadow as SH
        assert SH.daily_budget(1000) == 50
        assert SH.daily_budget(200) == 30
        assert SH.daily_budget(3) == 1
        assert SH.daily_budget(0) == 0

    def test_factor_zero_truly_zeroes(self):
        """归零必须**穿透低流量保底**（否则保底会把影子任务放回来）"""
        from agent.digestion import shadow as SH
        assert SH.daily_budget(3, factor=0.0) == 0
        assert SH.daily_budget(1000, factor=0.0) == 0

    def test_factor_half_halves(self):
        from agent.digestion import shadow as SH
        assert SH.daily_budget(1000, factor=0.5) == 25
        assert SH.daily_budget(200, factor=0.5) == 15
        # 系数 >0 时不因取整把影子任务关死
        assert SH.daily_budget(3, factor=0.5) == 1

    def test_factor_still_respects_cap(self):
        from agent.digestion import shadow as SH
        assert SH.daily_budget(1000, factor=0.5) <= SH.SHADOW_BUDGET_CAP

    def test_invalid_factor_is_no_impact(self):
        from agent.digestion import shadow as SH
        assert SH.daily_budget(1000, factor="boom") == 50
        assert SH.daily_budget(1000, factor=3.0) == 50

    def test_brake_drives_shadow_factor_end_to_end(self, events_dir, store,
                                                   monkeypatch):
        """端到端：断食生效 → `daily_budget()` 自动归零"""
        from agent.digestion import shadow as SH
        monkeypatch.setenv(ev.ENV_DIR, events_dir)
        monkeypatch.setenv(CB.ENV_ENABLED, "true")
        monkeypatch.setenv(CB.ENV_BASELINE_CENTS, "60")
        monkeypatch.setenv(CB.ENV_FASTING_IN_CONSECUTIVE, "1")
        monkeypatch.setenv(CB.ENV_STATE_PATH, "")
        CB.reset_cost_brake()
        seed_cost(store, day=BASE.date().isoformat(), cents_target=180)
        assert CB.get_cost_brake().evaluate(at(0))["fasting"]["state"] \
            == CB.STATE_FASTING
        assert SH.daily_budget(1000) == 0
        assert CB.shadow_budget_factor() == 0.0

    def test_shadow_runner_plan_records_factor(self, tmp_path):
        """ShadowPlan 记录降本系数 —— 「为什么今天预算是 0」必须可解释"""
        from agent.digestion import shadow as SH
        ledger = SH.ShadowLedger(path=str(tmp_path / "shadow_ledger.jsonl"))
        runner = SH.ShadowRunner(ledger=ledger, env={CB.ENV_ENABLED: "true"})
        plan = runner.plan("cap.demo", sample_ids=[f"s{i}" for i in range(50)],
                           daily_avg=100.0)
        assert plan.cost_factor == 1.0                     # env 未开刹车 → 无影响
        assert plan.to_dict()["cost_factor"] == 1.0
        assert "cost_factor" in plan.to_dict()["formula"]


class TestDigestionSuppression:
    """消化/内化/重探的降本联动（未开启时不得干预）"""

    def test_restricted_false_when_brake_off(self, events_dir, monkeypatch):
        monkeypatch.setenv(ev.ENV_DIR, events_dir)
        CB.reset_cost_brake()
        assert CB.digestion_restricted() == (False, "")

    def test_internalize_tick_helper_is_neutral_by_default(self, events_dir,
                                                          monkeypatch):
        monkeypatch.setenv(ev.ENV_DIR, events_dir)
        CB.reset_cost_brake()
        from agent.digestion.internalize import cost_policy_restricted
        assert cost_policy_restricted() == (False, "")

    def test_restricted_true_during_fasting(self, events_dir, store, monkeypatch):
        monkeypatch.setenv(ev.ENV_DIR, events_dir)
        monkeypatch.setenv(CB.ENV_ENABLED, "true")
        monkeypatch.setenv(CB.ENV_BASELINE_CENTS, "60")
        monkeypatch.setenv(CB.ENV_FASTING_IN_CONSECUTIVE, "1")
        monkeypatch.setenv(CB.ENV_STATE_PATH, "")
        CB.reset_cost_brake()
        seed_cost(store, day=BASE.date().isoformat(), cents_target=180)
        CB.get_cost_brake().evaluate(at(0))
        restricted, why = CB.digestion_restricted()
        assert restricted is True
        assert "断食" in why

    def test_digestion_restricted_never_raises(self, monkeypatch):
        """新增机制故障不得阻断消化（查询失败即"不抑制"）"""
        def boom(*_a, **_kw):
            raise RuntimeError("nope")

        monkeypatch.setattr(CB, "get_cost_brake", boom)
        assert CB.digestion_restricted() == (False, "")
        assert CB.shadow_budget_factor() == 1.0


# ════════════════════════════════════════════════════════════
#  10. 归一化日成本视图（data/cost_daily.json）
# ════════════════════════════════════════════════════════════


class TestCostDailyView:
    def test_view_reuses_utc_daily_fields(self, events_dir, store):
        day = BASE.date().isoformat()
        seed_cost(store, day=day, cents_target=60)
        view = CB.cost_daily_view(day=day, now=at(0), directory=events_dir,
                                  config=CB.load_config({}))
        assert view["cost_normalized_cents"] == pytest.approx(60.0)
        assert view["date"] == day
        assert view["llm_calls"] == 1
        assert "utc.utc_daily" in view["source"]
        assert view["source_of_truth"] == "events"

    def test_view_flags_over_budget(self, events_dir, store):
        day = BASE.date().isoformat()
        seed_cost(store, day=day, cents_target=180)
        cfg = CB.load_config({})
        cfg.daily_cents = 100.0
        view = CB.cost_daily_view(day=day, now=at(0), directory=events_dir,
                                  config=cfg)
        assert view["over_budget"] is True
        assert view["daily_budget_cents"] == pytest.approx(100.0)

    def test_view_carries_calibration_labels(self, events_dir, store):
        view = CB.cost_daily_view(day=BASE.date().isoformat(), now=at(0),
                                  directory=events_dir, config=CB.load_config({}))
        assert view["calibration_version"] == CB.CALIBRATION_VERSION
        assert "价格锚定系数" in view["calibration_note"]
        assert view["theta"]["phase"]

    def test_write_cost_daily_to_explicit_path(self, events_dir, tmp_path, store):
        target = str(tmp_path / "cost_daily.json")
        view = CB.write_cost_daily(target, day=BASE.date().isoformat(), now=at(0),
                                   directory=events_dir, config=CB.load_config({}))
        assert os.path.exists(target)
        with open(target, encoding="utf-8") as fh:
            assert json.load(fh)["date"] == view["date"]

    def test_write_cost_daily_bad_path_is_best_effort(self, events_dir, tmp_path):
        """落盘失败不得抛（best-effort）"""
        blocked = tmp_path / "afile"
        blocked.write_text("x", encoding="utf-8")
        view = CB.write_cost_daily(str(blocked / "nested" / "cost_daily.json"),
                                   day=BASE.date().isoformat(), now=at(0),
                                   directory=events_dir)
        assert view["date"] == BASE.date().isoformat()


# ════════════════════════════════════════════════════════════
#  11. 双成本轨收敛（裁定 D）的 utc 侧标注
# ════════════════════════════════════════════════════════════


class TestLegacyTrackConvergence:
    def test_utc_daily_carries_calibration_block(self, events_dir, store):
        seed_cost(store, day=BASE.date().isoformat(), cents_target=6)
        row = U.utc_daily(BASE.date().isoformat(), directory=events_dir)
        cal = row["calibration"]
        assert cal["source_of_truth"] == "events"
        assert cal["calibration_version"] == U.CALIBRATION_VERSION
        assert cal["calibrated"] is False
        assert "L2 Core-50" in cal["calibration_trigger"]

    def test_reconcile_cost_log_is_reconciliation_only(self, events_dir, store):
        out = U.reconcile_cost_log(directory=events_dir)
        assert out["role"] == "reconciliation_only"
        assert out["authoritative_source"] == "events"
        assert "停写" in out["note"] or "不存在" in out["note"]

    def test_legacy_track_status_reports_stop_write(self):
        from agent.model_router import cost_tracker as ct
        status = ct.legacy_track_status()
        assert status["write_enabled"] is False
        assert status["source_of_truth"] == "events"
        assert status["rollback_env"] == ct.ENV_LEGACY_WRITE

    def test_snapshot_thresholds_point_at_implementation(self, events_dir, store):
        snap = U.utc_snapshot(days=2, directory=events_dir)
        assert "cost_brake" in snap["thresholds"]["owner"]
        assert snap["calibration"]["source_of_truth"] == "events"

    def test_coefficient_table_carries_calibration(self):
        table = U.coefficient_table(["gpt-4"])
        assert table["calibration"]["calibration_version"] == U.CALIBRATION_VERSION
