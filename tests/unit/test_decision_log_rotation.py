"""TASK-S8-02 步骤 4 用例：决策日志轮转（rotation）+ 写路径加固

每条用例对应任务书里的一个验收点：

    1. 大小轮转：真的产生分片、活动文件真的缩小；
    2. **统计不变性**：轮转前后 ``read()`` 的记录多重集完全一致，
       ``simulate().to_dict()["totals"]`` 逐字段一致（不变的证明方式见
       ``TestStatisticalInvariance`` 的注释）；
    3. ``read(limit=N)`` 取**最新** N 条（轮转后不再取到最旧分片——修掉的排序缺陷）；
    4. 按日轮转；
    5. 归档而非删除：总行数守恒、任何文件都不被删除；
    6. 默认关闭（保守默认：默认配置下写入远超阈值也不轮转）；
    7. 锁不可用 ⇒ 轮转跳过 + 计数 + ``append()`` 仍成功；
    8. ``os.replace`` 原子路径失败时不留半成品、不丢记录；
    9. 并发追加不被轮转覆盖（注入式复现"快照之后、替换之前"的写入）；
   10. 分片命名与 ``_candidate_files`` 口径一致（点号形态；连字符形态对读取端不可见）；
   11. 不可读分片：告警 + 计数（不再静默丢一整片）；
   12. 时间序：脏 ``ts`` 不被排到最前、单文件乱序按 ``ts`` 排序。

【落盘纪律】所有用例显式传 ``tmp_path`` 下的路径（``isolate_policy`` 只是兜底），
**绝不碰** 仓库默认的 ``data/policies/decisions.jsonl``。
"""
from __future__ import annotations

import json
import logging
import os
from collections import Counter
from datetime import date, datetime, timedelta, timezone

import pytest

from policy_testkit import isolate_policy, make_policy, make_store

from agent.policy.decisions import (
    ROTATION_BELOW_THRESHOLD,
    ROTATION_CONCURRENT_WRITE,
    ROTATION_DISABLED,
    ROTATION_ERROR,
    ROTATION_LOCK_UNAVAILABLE,
    ROTATION_NOTHING_TO_MOVE,
    ROTATION_OK,
    ROTATION_UNSUPPORTED_PATH,
    TRIGGER_DAY,
    TRIGGER_SIZE,
    DecisionLog,
    DecisionRecord,
    chronological,
    shard_affixes,
    shard_day,
    shard_name,
    ts_order_key,
    valid_day,
)
from agent.policy.engine import DecisionObserver, PolicyEngine
from agent.policy.models import (
    EFFECT_DENY,
    PolicyContext,
    PolicyDecision,
)
from agent.policy.simulator import SimulationChange, simulate

pytestmark = [pytest.mark.unit]

TZ = timezone(timedelta(hours=8))
BASE_TS = datetime(2026, 9, 1, 10, 0, 0, tzinfo=TZ)


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    return isolate_policy(tmp_path, monkeypatch)


# ════════════════════════════════════════════════════════════
#  工具箱
# ════════════════════════════════════════════════════════════


def _ctx(**kwargs):
    return PolicyContext.build(capability_id="cp.test.src.act", actor="u1",
                               action="http.post", tenant_id="t-alpha",
                               target={"external": True, "host": "h"},
                               **kwargs)


def _decision(effect=EFFECT_DENY, **kwargs):
    base = dict(effect=effect, policy_id="hist.policy", policy_version="1.0.0",
                reason_code="policy_deny", capability_id="cp.test.src.act",
                action="http.post", actor="u1", tenant_id="t-alpha",
                latency_ms=0.5)
    base.update(kwargs)
    return PolicyDecision(**base)


def _rec(ts, *, effect=EFFECT_DENY, actor="u1"):
    """一条历史决策记录（与 simulator 用例同构：external=True ⇒ 基线 deny 可重放）"""
    ctx = PolicyContext.build(
        capability_id="cp.test.src.act", actor=actor, action="http.post",
        tenant_id="t-alpha",
        capability={"trust": {"data_class": "internal"}},
        target={"external": True, "host": "api.example.com"})
    return DecisionRecord(
        ts=ts, input=ctx.input, effect=effect, policy_id="hist.policy",
        policy_version="1.0.0", reason_code="policy_deny",
        capability_id="cp.test.src.act", action="http.post", actor=actor,
        tenant_id="t-alpha")


def _series(count, *, start=BASE_TS, step_seconds=60):
    """ts 严格递增的记录序列（actor 各不相同 ⇒ 绝不触发去重）"""
    return [_rec((start + timedelta(seconds=step_seconds * index)).isoformat(),
                 actor=f"u{index}") for index in range(count)]


def _write_raw(path, records):
    """直接按落盘格式写 JSONL（绕开 append：本轮用例不测 append 时更快更可控）"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(r.to_json_line() + "\n" for r in records),
                    encoding="utf-8")
    return path


def _multiset(records):
    """记录多重集（用落盘行做身份：字段完全一致才算同一条）"""
    return Counter(record.to_json_line() for record in records)


def _read(path, **kwargs):
    return DecisionLog(str(path), enabled=False).read(**kwargs)


def _raw_line_count(directory, stem="decisions"):
    """活动文件 + 全部分片的**非空行**总数（归档而非删除的守恒量）"""
    total = 0
    for name in sorted(os.listdir(directory)):
        if name != f"{stem}.jsonl" and not (name.startswith(stem + ".")
                                            and name.endswith(".jsonl")):
            continue
        full = directory / name
        if not full.is_file():
            continue
        total += len([line for line in full.read_text(encoding="utf-8").splitlines()
                      if line.strip()])
    return total


def _decision_files(directory):
    """目录下的决策日志相关文件（含锁；不含 ``isolate_policy`` 造的其它目录）"""
    return sorted(name for name in os.listdir(directory)
                  if name.startswith("decisions"))


def _engine(policies=()):
    return PolicyEngine(make_store(list(policies)), cache_size=0,
                        decision_log=False,
                        observer=DecisionObserver(enabled=False), inbox=False)


def _totals(path):
    """统计口径的**权威路径**：simulate → DecisionLog.read → totals"""
    baseline = _engine([make_policy(
        id="hist.policy", effect="deny",
        match={"field": "target.external", "op": "eq", "value": True})])
    candidate = make_policy(id="hist.policy", version="2.0.0", effect="allow",
                            match={"field": "target.external", "op": "eq",
                                   "value": True})
    report = simulate(candidate, engine=baseline, log_path=str(path), since_days=0)
    return report.to_dict()["totals"]


# ════════════════════════════════════════════════════════════
#  0. 纯函数口径（分片命名 / 时间序）
# ════════════════════════════════════════════════════════════


class TestShardNaming:
    def test_分片名是点号形态(self):
        assert shard_name("data/policies/decisions.jsonl", "2026-09-11") == \
            os.path.join("data/policies", "decisions.20260911.jsonl")
        assert shard_name("data/policies/decisions.jsonl", "") == \
            os.path.join("data/policies", "decisions.undated.jsonl")

    def test_无扩展名路径拒绝轮转(self):
        """分片名对读取端不可见 ⇒ 宁可拒绝（否则就是把记录搬进读不到的文件）"""
        assert shard_affixes("data/policies/decisions") is None
        assert shard_name("data/policies/decisions", "2026-09-11") == ""

    def test_分片名与_candidate_files_口径一致(self, tmp_path):
        """★ 口径守卫：轮转产出的名字必须能被既有读取端枚举到

        连字符形态（``log_archiver.archive_daily_file`` 的写法）刻意作为**反例**
        钉在这里：它不满足 ``name.startswith(stem + ".")``，对 ``read()`` 完全不可见。
        """
        path = tmp_path / "decisions.jsonl"
        _write_raw(path, _series(2))
        shard = tmp_path / "decisions.20260911.jsonl"
        assert shard_name(str(path), "2026-09-11") == str(shard)
        shard.write_text(_rec("2026-09-11T10:00:00+08:00").to_json_line() + "\n",
                         encoding="utf-8")
        candidates = DecisionLog._candidate_files(str(path))  # noqa: SLF001
        assert str(shard) in candidates                # 点号形态：读得到
        hyphen = tmp_path / "decisions-20260911.jsonl"
        hyphen.write_text(_rec("2026-09-11T11:00:00+08:00").to_json_line() + "\n",
                          encoding="utf-8")
        assert str(hyphen) not in candidates           # 连字符形态：读不到（陷阱）
        assert len(_read(path)) == 3                   # 活动 2 条 + 点号分片 1 条

    def test_分片名反解日期(self):
        assert shard_day("decisions.20260911.jsonl") == "2026-09-11"
        assert shard_day("decisions.undated.jsonl") == ""
        assert shard_day("decisions.jsonl") == ""

    def test_日历日校验(self):
        assert valid_day("2026-09-11") is True
        assert valid_day("2026-02-30") is False
        assert valid_day("2026-9-11") is False
        assert valid_day("") is False


class TestChronological:
    def test_单文件乱序按_ts_排序(self):
        records = [DecisionRecord(ts="2026-09-02T10:00:00+08:00"),
                   DecisionRecord(ts="2026-09-01T10:00:00+08:00")]
        assert [r.ts for r in chronological(records)] == [
            "2026-09-01T10:00:00+08:00", "2026-09-02T10:00:00+08:00"]

    def test_脏_ts_保持原位不被排到最前(self):
        """朴素 ``sort(key=ts)`` 会把脏记录（key 为空）排到最前——这里必须原地不动"""
        records = [DecisionRecord(ts=""),
                   DecisionRecord(ts="2026-09-02T10:00:00+08:00"),
                   DecisionRecord(ts="2026-09-01T10:00:00+08:00")]
        ordered = chronological(records)
        assert ordered[0].ts == ""
        assert [r.ts for r in ordered[1:]] == ["2026-09-01T10:00:00+08:00",
                                               "2026-09-02T10:00:00+08:00"]

    def test_同_ts_稳定保序(self):
        records = [DecisionRecord(ts="2026-09-01T10:00:00+08:00", actor=name)
                   for name in ("a", "b", "c")]
        assert [r.actor for r in chronological(records)] == ["a", "b", "c"]

    def test_naive_与_aware_混排不抛(self):
        records = [DecisionRecord(ts="2026-09-02T10:00:00+08:00"),
                   DecisionRecord(ts="2026-09-01T00:00:00")]
        assert ts_order_key("bad-ts") is None
        assert len(chronological(records)) == 2


# ════════════════════════════════════════════════════════════
#  1/5. 大小轮转 + 归档而非删除
# ════════════════════════════════════════════════════════════


class TestSizeRotation:
    def test_按大小轮转_产生分片且活动文件缩小(self, tmp_path):
        path = _write_raw(tmp_path / "decisions.jsonl", _series(40))
        size_before = path.stat().st_size
        log = DecisionLog(str(path), max_bytes=max(size_before // 4, 1),
                          keep_bytes=1024, rotate_lock_timeout=0.0,
                          append_lock_timeout=0.0)
        summary = log.rotate(force=True, trigger=TRIGGER_SIZE)
        assert summary["rotated"] is True, summary
        assert summary["reason"] == ROTATION_OK
        assert summary["trigger"] == TRIGGER_SIZE
        assert summary["records_moved"] > 0
        assert summary["records_kept"] >= 1
        assert summary["bytes_reclaimed"] == (size_before - path.stat().st_size)
        assert path.stat().st_size < size_before
        shards = log.rotated_shards()
        assert shards, "轮转必须真的在磁盘上留下分片"
        for item in shards:
            assert item["name"].startswith("decisions.")
            assert item["name"].endswith(".jsonl")
            assert item["bytes"] > 0
        assert set(summary["shards"]) == {item["path"] for item in shards}
        assert log.stats["rotation_count"] == 1
        assert log.stats["rotation_records"] == summary["records_moved"]
        assert log.stats["rotation_last"]["reason"] == ROTATION_OK
        # 活动文件仍是**完整合法**的 JSONL（原子替换不留半行）
        for line in path.read_text(encoding="utf-8").splitlines():
            assert json.loads(line)["schema"] == "policy.decision.v1"

    def test_归档而非删除_行数守恒(self, tmp_path):
        records = _series(40)
        path = _write_raw(tmp_path / "decisions.jsonl", records)
        lines_before = _raw_line_count(tmp_path)
        multiset_before = _multiset(_read(path, dedupe=False))
        assert lines_before == 40 and len(multiset_before) == 40
        log = DecisionLog(str(path), max_bytes=1, keep_bytes=1,
                          rotate_lock_timeout=0.0)
        for _ in range(3):
            log.rotate(force=True, trigger=TRIGGER_SIZE)
        assert _raw_line_count(tmp_path) == lines_before      # 一条都没删
        assert _multiset(_read(path, dedupe=False)) == multiset_before
        assert path.exists()                                  # 活动文件始终在

    def test_轮转至少保留最后一条(self, tmp_path):
        """活动文件被清空会让下一次 append 立刻再顶穿阈值 ⇒ 抖动"""
        path = _write_raw(tmp_path / "decisions.jsonl", _series(10))
        log = DecisionLog(str(path), max_bytes=1, keep_bytes=0,
                          rotate_lock_timeout=0.0)
        summary = log.rotate(force=True, trigger=TRIGGER_SIZE)
        assert summary["records_kept"] == 1
        assert len(path.read_text(encoding="utf-8").splitlines()) == 1

    def test_append_自动触发大小轮转(self, tmp_path):
        path = tmp_path / "decisions.jsonl"
        log = DecisionLog(str(path), max_bytes=2048, keep_bytes=512,
                          rotate_lock_timeout=0.0, append_lock_timeout=0.0)
        blob = "x" * 400
        for index in range(20):
            ts = (BASE_TS + timedelta(seconds=index)).isoformat()
            assert log.append(_ctx(), _decision(), extra={"blob": blob},
                              ts=ts) is True
        assert log.stats["rotation_count"] >= 1, log.stats
        assert log.rotated_shards()
        assert path.stat().st_size < 2048
        assert len(_read(path)) == 20          # 20 条一条不少（含已归档的）


# ════════════════════════════════════════════════════════════
#  2. 统计不变性（本任务的核心验收）
# ════════════════════════════════════════════════════════════


class TestStatisticalInvariance:
    """不变性的证明方式

    「不变」= 两个**可观测量**在轮转前后逐字段相同：
      (a) ``read(dedupe=False)`` 的记录**多重集**（比长度更强：长度相同也可能是
          不同的记录集合，故用 ``Counter`` 比对每条落盘行）；
      (b) ``simulate().to_dict()["totals"]``——这是统计口径的权威路径
          （simulator → DecisionLog.read → SimulationReport.to_dict）。
    ``dedupe=False`` 是**刻意**的：默认去重会把"重复归档"这种缺陷掩盖掉，
    本用例要证明的是"轮转自己**没有**制造重复、也没有丢记录"。
    """

    def test_记录多重集不变(self, tmp_path):
        records = _series(36)
        path = _write_raw(tmp_path / "decisions.jsonl", records)
        before = _read(path, dedupe=False)
        log = DecisionLog(str(path), max_bytes=1, keep_bytes=4096,
                          rotate_lock_timeout=0.0)
        summary = log.rotate(force=True, trigger=TRIGGER_SIZE)
        assert summary["rotated"] is True and summary["records_moved"] > 0
        after = _read(path, dedupe=False)
        assert len(after) == len(before) == 36
        assert _multiset(after) == _multiset(before)

    def test_simulate_totals_不变(self, tmp_path):
        path = _write_raw(tmp_path / "decisions.jsonl", _series(12))
        totals_before = _totals(path)
        assert totals_before["total"] == 12
        assert totals_before["deny_to_allow"] == 12        # 有非平凡统计量可比
        log = DecisionLog(str(path), max_bytes=1, keep_bytes=2048,
                          rotate_lock_timeout=0.0)
        assert log.rotate(force=True, trigger=TRIGGER_SIZE)["rotated"] is True
        totals_after = _totals(path)
        assert totals_after == totals_before               # 逐字段
        assert len(totals_before) > 5                      # 防止"空报告也相等"的假绿

    def test_按日轮转后_totals_也不变(self, tmp_path):
        today = date.today()
        records = [_rec((today - timedelta(days=offset)).isoformat()
                        + "T10:00:00+08:00", actor=f"u{offset}")
                   for offset in (1, 2, 3)]
        path = _write_raw(tmp_path / "decisions.jsonl", records)
        totals_before = _totals(path)
        log = DecisionLog(str(path), rotate_daily=True, daily_check_seconds=0.0,
                          rotate_lock_timeout=0.0)
        assert log.rotate(force=True, trigger=TRIGGER_DAY)["records_moved"] == 3
        assert _totals(path) == totals_before


# ════════════════════════════════════════════════════════════
#  3. read(limit=N) 取最新 N 条（修掉的排序缺陷）
# ════════════════════════════════════════════════════════════


class TestReadOrderAfterRotation:
    def test_limit_取最新而非最旧分片(self, tmp_path):
        records = _series(30)
        path = _write_raw(tmp_path / "decisions.jsonl", records)
        log = DecisionLog(str(path), max_bytes=1, keep_bytes=1024,
                          rotate_lock_timeout=0.0)
        assert log.rotate(force=True, trigger=TRIGGER_SIZE)["rotated"] is True
        assert log.rotated_shards(), "本用例前提：确实产生了分片"
        reader = DecisionLog(str(path), enabled=False)
        expected = [r.ts for r in records[-2:]]
        assert [r.ts for r in reader.read(limit=2)] == expected
        assert [r.ts for r in reader.read(limit=5)] == \
            [r.ts for r in records[-5:]]
        assert reader.read(limit=0) == []
        # 全部读回时也是时间序（分片在前、活动文件在后的**文件顺序**已被排序纠正）
        all_records = reader.read()
        assert [r.ts for r in all_records] == sorted(r.ts for r in records)
        # 反证：不排序就会取到**最旧分片**的尾部（这就是修掉的 bug）
        naive = list(reader.iter_records(dedupe=False))[-2:]
        assert [r.ts for r in naive] != expected

    def test_since_窗口与时间序共存(self, tmp_path):
        records = _series(10)
        path = _write_raw(tmp_path / "decisions.jsonl", records)
        log = DecisionLog(str(path), max_bytes=1, keep_bytes=512,
                          rotate_lock_timeout=0.0)
        log.rotate(force=True, trigger=TRIGGER_SIZE)
        reader = DecisionLog(str(path), enabled=False)
        since = records[7].ts
        window = reader.read(since=since)
        assert [r.ts for r in window] == [r.ts for r in records[7:]]
        assert [r.ts for r in reader.read(since=since, limit=2)] == \
            [r.ts for r in records[8:]]


# ════════════════════════════════════════════════════════════
#  4. 按日轮转
# ════════════════════════════════════════════════════════════


class TestDailyRotation:
    def test_按日轮转_旧日进入当日分片(self, tmp_path):
        today = date.today().isoformat()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        path = _write_raw(tmp_path / "decisions.jsonl", [
            _rec(f"{yesterday}T10:00:00+08:00", actor="old"),
            _rec(f"{today}T10:00:00+08:00", actor="new"),
        ])
        log = DecisionLog(str(path), rotate_daily=True, daily_check_seconds=0.0,
                          rotate_lock_timeout=0.0, append_lock_timeout=0.0)
        summary = log.rotate(force=True, trigger=TRIGGER_DAY)
        assert summary["rotated"] is True
        assert summary["records_moved"] == 1 and summary["records_kept"] == 1
        shard = tmp_path / f"decisions.{yesterday.replace('-', '')}.jsonl"
        assert shard.exists()
        assert _rec(f"{yesterday}T10:00:00+08:00", actor="old").to_json_line() in \
            shard.read_text(encoding="utf-8")
        assert log.rotated_shards()[0]["day"] == yesterday
        assert len(_read(path)) == 2               # 读端仍看得到全部历史

    def test_maybe_rotate_走按日路径且再跑无事可做(self, tmp_path):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        path = _write_raw(tmp_path / "decisions.jsonl", [
            _rec(f"{yesterday}T10:00:00+08:00"),
            _rec(f"{date.today().isoformat()}T10:00:00+08:00"),
        ])
        log = DecisionLog(str(path), rotate_daily=True, daily_check_seconds=0.0,
                          rotate_lock_timeout=0.0)
        assert log.maybe_rotate()["reason"] == ROTATION_OK
        assert log.maybe_rotate()["reason"] == ROTATION_NOTHING_TO_MOVE

    def test_无法定日的记录留在活动文件(self, tmp_path):
        """脏 ts 不该被塞进某个"日期分片"里冒充历史"""
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        path = _write_raw(tmp_path / "decisions.jsonl", [
            _rec(f"{yesterday}T10:00:00+08:00"),
            DecisionRecord(ts="", effect=EFFECT_DENY, actor="dirty"),
        ])
        log = DecisionLog(str(path), rotate_daily=True, daily_check_seconds=0.0,
                          rotate_lock_timeout=0.0)
        summary = log.rotate(force=True, trigger=TRIGGER_DAY)
        assert summary["records_moved"] == 1 and summary["records_kept"] == 1
        assert not (tmp_path / "decisions.undated.jsonl").exists()


# ════════════════════════════════════════════════════════════
#  6. 默认关闭（保守默认）
# ════════════════════════════════════════════════════════════


class TestDefaultsOff:
    def test_默认配置不轮转_写再大也不动(self, tmp_path):
        path = tmp_path / "decisions.jsonl"
        log = DecisionLog(str(path))
        assert log.rotation_enabled is False
        assert log.stats["rotation_max_bytes"] == 0
        assert log.stats["rotation_daily"] is False
        blob = "x" * 900
        for index in range(80):
            ctx = PolicyContext.build(capability_id="cp.test.src.act",
                                      actor=f"u{index}", action="http.post",
                                      target={"external": True})
            ts = (BASE_TS + timedelta(seconds=index)).isoformat()
            assert log.append(ctx, _decision(), extra={"blob": blob},
                              ts=ts) is True
        assert path.stat().st_size > 64 * 1024        # 已远超任何合理阈值
        assert log.rotated_shards() == []
        assert log.stats["rotation_count"] == 0
        assert log.maybe_rotate()["reason"] == ROTATION_DISABLED
        # 默认口径下连锁文件都不该产生（append 走句柄快路径、不取跨进程锁）
        assert _decision_files(tmp_path) == ["decisions.jsonl"]
        assert log.stats["lock_appends_effective"] is False
        assert not (tmp_path / "decisions.jsonl.lock").exists()

    def test_环境变量驱动配置(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CP_POLICY_DECISION_LOG_MAX_BYTES", "4096")
        monkeypatch.setenv("CP_POLICY_DECISION_LOG_ROTATE_DAILY", "1")
        monkeypatch.setenv("CP_POLICY_DECISION_LOG_KEEP_BYTES", "512")
        monkeypatch.setenv("CP_POLICY_DECISION_LOG_LOCK_APPENDS", "0")
        monkeypatch.setenv("CP_POLICY_DECISION_LOG_DAILY_CHECK_SECONDS", "0")
        log = DecisionLog(str(tmp_path / "decisions.jsonl"))
        assert log.rotation_enabled is True
        assert log.rotation_config["max_bytes"] == 4096
        assert log.rotation_config["keep_bytes"] == 512
        assert log.rotation_config["rotate_daily"] is True
        assert log.rotation_config["lock_appends"] is False      # 显式 0 = 强制不取
        assert log.stats["rotation_keep_bytes"] == 512

    def test_取锁策略_auto_随轮转开关(self, tmp_path, monkeypatch):
        """**auto**：轮转关闭时不取锁（单进程下它没有互斥对象），开启/强制时取"""
        off = DecisionLog(str(tmp_path / "off.jsonl"))
        assert off.rotation_enabled is False
        assert off.lock_appends_effective is False
        assert off.append(_ctx(), _decision()) is True
        assert not (tmp_path / "off.jsonl.lock").exists()

        on = DecisionLog(str(tmp_path / "on.jsonl"), max_bytes=1 << 20)
        assert on.lock_appends_effective is True
        assert on.append(_ctx(), _decision()) is True
        assert (tmp_path / "on.jsonl.lock").exists()              # 轮转开启 ⇒ 取锁

        monkeypatch.setenv("CP_POLICY_DECISION_LOG_LOCK_APPENDS", "1")
        forced = DecisionLog(str(tmp_path / "forced.jsonl"))
        assert forced.rotation_enabled is False
        assert forced.lock_appends_effective is True              # 显式强制协作加锁
        assert forced.append(_ctx(), _decision()) is True
        assert (tmp_path / "forced.jsonl.lock").exists()

        monkeypatch.setenv("CP_POLICY_DECISION_LOG_LOCK_APPENDS", "0")
        never = DecisionLog(str(tmp_path / "never.jsonl"), max_bytes=1 << 20)
        assert never.rotation_enabled is True
        assert never.lock_appends_effective is False

    def test_默认保留量是阈值的一半(self, tmp_path):
        log = DecisionLog(str(tmp_path / "decisions.jsonl"), max_bytes=8192)
        assert log.effective_keep_bytes == 4096
        log2 = DecisionLog(str(tmp_path / "d2.jsonl"), max_bytes=8192,
                           keep_bytes=256)
        assert log2.effective_keep_bytes == 256

    def test_未配置规则时_force_收缩到最后一条(self, tmp_path):
        """**force 是运维显式动作**：默认配置下也该做确定的事（而不是静默无事发生）"""
        path = _write_raw(tmp_path / "decisions.jsonl", _series(4))
        log = DecisionLog(str(path), rotate_lock_timeout=0.0)
        assert log.rotation_enabled is False        # 自动路径仍然关闭（保守默认）
        summary = log.rotate(force=True)
        assert summary["rotated"] is True
        assert summary["records_moved"] == 3 and summary["records_kept"] == 1
        assert len(_read(path)) == 4                # 一条不丢
        assert log.rotate(force=True)["reason"] == ROTATION_NOTHING_TO_MOVE

    def test_strict_模式不影响轮转(self, tmp_path):
        """strict 的语义是"写入失败要显式失败"，不该让运维问题炸掉决策路径"""
        path = _write_raw(tmp_path / "decisions.jsonl", _series(6))
        log = DecisionLog(str(path), strict=True, max_bytes=1,
                          rotate_lock_timeout=0.0)
        assert log.rotate(force=True, trigger=TRIGGER_SIZE)["rotated"] is True


# ════════════════════════════════════════════════════════════
#  7. 锁不可用 ⇒ 跳过 + 计数 + append 仍成功
# ════════════════════════════════════════════════════════════


class TestLockUnavailable:
    def test_锁被占用时_轮转跳过且_append_仍成功(self, tmp_path):
        from agent.utils.cross_process_lock import (
            CrossProcessLock,
            lock_path_for,
            lock_metrics,
            reset_lock_metrics,
        )

        path = tmp_path / "decisions.jsonl"
        log = DecisionLog(str(path), max_bytes=1, keep_bytes=1,
                          rotate_lock_timeout=0.0, append_lock_timeout=0.0)
        holder = CrossProcessLock(lock_path_for(str(path)), name="test-holder")
        reset_lock_metrics()
        assert holder.try_lock() is True
        try:
            # ① append 拿不到锁：**降级写入**（记录不可丢），并计数
            assert log.append(_ctx(), _decision()) is True
            assert log.stats["degraded_appends"] == 1
            # ② append 内部触发的 maybe_rotate 因锁不可用而跳过并计数
            assert log.stats["rotation_skipped_locked"] >= 1
            # ③ 显式轮转同样跳过
            summary = log.rotate(force=True, trigger=TRIGGER_SIZE)
        finally:
            holder.release()
        assert summary["rotated"] is False
        assert summary["reason"] == ROTATION_LOCK_UNAVAILABLE
        assert summary["records_moved"] == 0
        assert log.stats["rotation_count"] == 0
        assert log.rotated_shards() == []              # 一个分片都没产生
        assert len(_read(path)) == 1                   # 记录确实落下去了
        assert lock_metrics()["conflicts"] >= 1        # 走了统一锁原语的计数与留痕
        assert log.stats["rotation_last"]["reason"] == ROTATION_LOCK_UNAVAILABLE

    def test_锁释放后_轮转恢复(self, tmp_path):
        from agent.utils.cross_process_lock import (
            CrossProcessLock,
            lock_path_for,
        )

        path = _write_raw(tmp_path / "decisions.jsonl", _series(6))
        log = DecisionLog(str(path), max_bytes=1, keep_bytes=1,
                          rotate_lock_timeout=0.0)
        holder = CrossProcessLock(lock_path_for(str(path)), name="test-holder")
        assert holder.try_lock() is True
        assert log.rotate(force=True, trigger=TRIGGER_SIZE)["reason"] == \
            ROTATION_LOCK_UNAVAILABLE
        holder.release()
        assert log.rotate(force=True, trigger=TRIGGER_SIZE)["rotated"] is True


# ════════════════════════════════════════════════════════════
#  8/9. 原子性 + 并发写入不被覆盖
# ════════════════════════════════════════════════════════════


class TestAtomicityAndConcurrency:
    def test_replace_失败时不丢记录也不留半成品(self, tmp_path, monkeypatch):
        import agent.policy.decisions as decisions_mod

        records = _series(12)
        path = _write_raw(tmp_path / "decisions.jsonl", records)
        before_bytes = path.read_bytes()
        multiset_before = _multiset(_read(path, dedupe=False))
        log = DecisionLog(str(path), max_bytes=1, keep_bytes=256,
                          rotate_lock_timeout=0.0)

        def boom(source, target):
            raise OSError("simulated crash inside os.replace")

        monkeypatch.setattr(decisions_mod.os, "replace", boom)
        summary = log.rotate(force=True, trigger=TRIGGER_SIZE)
        assert summary["rotated"] is False
        assert summary["reason"] == ROTATION_ERROR
        assert "simulated crash" in summary["error"]
        assert log.stats["rotation_failures"] == 1
        assert path.read_bytes() == before_bytes          # 活动文件一个字节没变
        assert list(tmp_path.glob("*.tmp")) == []         # 不留临时文件
        # 先归档、后替换 ⇒ 崩溃只可能"多算"（可去重），不可能"少算"
        assert _multiset(_read(path, dedupe=True)) == multiset_before
        assert len(_read(path, dedupe=False)) > len(multiset_before)

    def test_成功路径不留临时文件(self, tmp_path):
        path = _write_raw(tmp_path / "decisions.jsonl", _series(12))
        log = DecisionLog(str(path), max_bytes=1, keep_bytes=256,
                          rotate_lock_timeout=0.0)
        assert log.rotate(force=True, trigger=TRIGGER_SIZE)["rotated"] is True
        assert list(tmp_path.glob("*.tmp")) == []
        assert _decision_files(tmp_path) == ["decisions.20260901.jsonl",
                                             "decisions.jsonl",
                                             "decisions.jsonl.lock"]

    def test_快照之后的并发追加被并入而非覆盖(self, tmp_path, monkeypatch):
        """注入式复现"另一个进程在快照之后、替换之前 append 了一条" """
        path = _write_raw(tmp_path / "decisions.jsonl", _series(12))
        late = _rec("2026-09-09T09:00:00+08:00", actor="late-writer")
        late_line = late.to_json_line() + "\n"
        real_write_temp = DecisionLog._write_temp  # noqa: SLF001

        def write_temp_then_append(self, target, lines):
            temp = real_write_temp(self, target, lines)
            with open(target, "a", encoding="utf-8") as handle:
                handle.write(late_line)            # 模拟并发（降级）写入
            return temp

        monkeypatch.setattr(DecisionLog, "_write_temp", write_temp_then_append)
        log = DecisionLog(str(path), max_bytes=1, keep_bytes=256,
                          rotate_lock_timeout=0.0)
        summary = log.rotate(force=True, trigger=TRIGGER_SIZE)
        assert summary["rotated"] is True
        assert late.ts in [r.ts for r in _read(path)]     # 并发写入活下来了
        assert _multiset(_read(path, dedupe=False)) == _multiset(
            _series(12) + [late])

    def test_分片写入期间的并发追加_放弃替换而不覆盖(self, tmp_path, monkeypatch):
        """放弃替换 ⇒ 活动文件原样（一条不丢），代价只是分片里多一份（读端去重）"""
        path = _write_raw(tmp_path / "decisions.jsonl", _series(12))
        late = _rec("2026-09-09T09:00:00+08:00", actor="late-writer")
        raw_before = path.read_bytes()
        real_archive = DecisionLog._archive_shards  # noqa: SLF001

        def archive_then_append(self, target, moved):
            result = real_archive(self, target, moved)
            with open(target, "a", encoding="utf-8") as handle:
                handle.write(late.to_json_line() + "\n")
            return result

        monkeypatch.setattr(DecisionLog, "_archive_shards", archive_then_append)
        log = DecisionLog(str(path), max_bytes=1, keep_bytes=256,
                          rotate_lock_timeout=0.0)
        summary = log.rotate(force=True, trigger=TRIGGER_SIZE)
        assert summary["reason"] == ROTATION_CONCURRENT_WRITE
        assert log.stats["rotation_skipped_concurrent"] == 1
        assert log.stats["rotation_count"] == 0
        # 活动文件**逐字节**保持原样（只多了注入的那条并发追加）
        assert path.read_bytes().startswith(raw_before)
        appended = path.read_bytes()[len(raw_before):].decode("utf-8")
        assert appended.strip() == late.to_json_line()
        # 活动文件未被替换 ⇒ 并发写入仍在；分片已归档 ⇒ 读端去重后不重复计数
        assert late.ts in [r.ts for r in _read(path)]
        assert _multiset(_read(path, dedupe=True)) == _multiset(
            _series(12) + [late])

    def test_活动文件被并发轮转替换时放弃(self, tmp_path, monkeypatch):
        path = _write_raw(tmp_path / "decisions.jsonl", _series(12))
        real_write_temp = DecisionLog._write_temp  # noqa: SLF001

        def write_temp_then_shrink(self, target, lines):
            temp = real_write_temp(self, target, lines)
            with open(target, "w", encoding="utf-8") as handle:
                handle.write("")                    # 模拟别的轮转者已截断
            return temp

        monkeypatch.setattr(DecisionLog, "_write_temp", write_temp_then_shrink)
        log = DecisionLog(str(path), max_bytes=1, keep_bytes=256,
                          rotate_lock_timeout=0.0)
        summary = log.rotate(force=True, trigger=TRIGGER_SIZE)
        assert summary["reason"] == ROTATION_CONCURRENT_WRITE
        assert "缩小" in summary["error"]
        assert list(tmp_path.glob("*.tmp")) == []


# ════════════════════════════════════════════════════════════
#  11. 不可读分片：告警 + 计数（不再静默丢一整片）
# ════════════════════════════════════════════════════════════


class TestUnreadableShards:
    def test_整片不可读时告警且计数(self, tmp_path, caplog):
        path = _write_raw(tmp_path / "decisions.jsonl", _series(2))
        # 用一个**目录**冒充分片：open() 必然 OSError（跨平台可复现，
        # 且不依赖 chmod 在 Windows 上的无效行为）
        fake = tmp_path / "decisions.20260101.jsonl"
        fake.mkdir()
        reader = DecisionLog(str(path), enabled=False)
        with caplog.at_level(logging.WARNING, logger="agent.policy.decisions"):
            records = reader.read()
        assert len(records) == 2                     # 能读的照读
        assert reader.stats["unreadable_shards"] == 1
        messages = [record.getMessage() for record in caplog.records]
        assert any("整片记录将缺失" in message for message in messages), messages
        assert any(str(fake) in message for message in messages)


# ════════════════════════════════════════════════════════════
#  12. 文档与实现一致（B 项：replay_drift 不在记录上）
# ════════════════════════════════════════════════════════════


class TestDocstringContract:
    def test_重放漂移由模拟器承载而非记录字段(self):
        assert not hasattr(DecisionRecord(ts="t"), "replay_drift")
        assert "replay_drift" not in DecisionRecord(ts="t").to_json_line()
        change = SimulationChange(kind="other", old_effect="allow",
                                  new_effect="deny", drift=True)
        assert change.to_dict()["drift"] is True

    def test_轮转摘要字段齐全(self, tmp_path):
        path = _write_raw(tmp_path / "decisions.jsonl", _series(4))
        log = DecisionLog(str(path), max_bytes=1, keep_bytes=1,
                          rotate_lock_timeout=0.0)
        summary = log.rotate(force=True, trigger=TRIGGER_SIZE)
        for key in ("rotated", "reason", "trigger", "records_moved",
                    "records_kept", "shards", "shards_created",
                    "bytes_reclaimed", "bytes_archived", "active_bytes_before",
                    "active_bytes_replaced", "active_bytes_after", "error",
                    "path"):
            assert key in summary, key
        # 无并发时：活动文件缩减量 == 归档字节量（有并发并入时前者更小）
        assert summary["bytes_archived"] == summary["bytes_reclaimed"] > 0
        assert summary["active_bytes_before"] > summary["active_bytes_after"]


class TestRotationLogging:
    def test_轮转留结构化日志(self, tmp_path, caplog):
        path = _write_raw(tmp_path / "decisions.jsonl", _series(8))
        log = DecisionLog(str(path), max_bytes=1, keep_bytes=1,
                          rotate_lock_timeout=0.0)
        with caplog.at_level(logging.INFO, logger="agent.policy.decisions"):
            log.rotate(force=True, trigger=TRIGGER_SIZE)
        payloads = []
        for record in caplog.records:
            message = record.getMessage()
            if message.startswith("决策日志轮转 "):
                payloads.append(json.loads(message.split(" ", 1)[1]))
        assert payloads, [r.getMessage() for r in caplog.records]
        assert payloads[-1]["reason"] == ROTATION_OK
        assert payloads[-1]["records_moved"] > 0

    def test_跳过也有日志(self, tmp_path, caplog):
        path = _write_raw(tmp_path / "decisions.jsonl", _series(2))
        log = DecisionLog(str(path))
        with caplog.at_level(logging.DEBUG, logger="agent.policy.decisions"):
            log.rotate(force=False)          # 未配置任何规则 ⇒ 不轮转
        bodies = [record.getMessage() for record in caplog.records
                  if "决策日志轮转" in record.getMessage()]
        assert bodies, [r.getMessage() for r in caplog.records]
        assert ROTATION_BELOW_THRESHOLD in bodies[-1]
        assert log.rotation_enabled is False


class TestBelowThreshold:
    def test_未达阈值不轮转(self, tmp_path):
        path = _write_raw(tmp_path / "decisions.jsonl", _series(4))
        log = DecisionLog(str(path), max_bytes=1 << 20,
                          rotate_lock_timeout=0.0)
        assert log.maybe_rotate()["reason"] == ROTATION_BELOW_THRESHOLD
        assert log.rotate()["reason"] == ROTATION_BELOW_THRESHOLD
        assert log.stats["rotation_count"] == 0

    def test_活动文件不存在时安全(self, tmp_path):
        log = DecisionLog(str(tmp_path / "missing.jsonl"), max_bytes=1,
                          rotate_lock_timeout=0.0)
        summary = log.rotate(force=True, trigger=TRIGGER_SIZE)
        assert summary["rotated"] is False
        assert summary["reason"] in ("no_active_file", "nothing_to_move")

    def test_目录当路径时拒绝轮转(self, tmp_path):
        log = DecisionLog(str(tmp_path), max_bytes=1, rotate_lock_timeout=0.0)
        assert log.rotate(force=True, trigger=TRIGGER_SIZE)["reason"] == \
            ROTATION_UNSUPPORTED_PATH

    def test_关闭后不轮转(self, tmp_path):
        path = _write_raw(tmp_path / "decisions.jsonl", _series(4))
        log = DecisionLog(str(path), max_bytes=1, rotate_lock_timeout=0.0)
        log.close()
        summary = log.rotate(force=True, trigger=TRIGGER_SIZE)
        assert summary["reason"] in ("closed", "disabled")

    def test_只读实例不轮转(self, tmp_path):
        path = _write_raw(tmp_path / "decisions.jsonl", _series(4))
        log = DecisionLog(str(path), enabled=False, max_bytes=1)
        assert log.rotate(force=True, trigger=TRIGGER_SIZE)["reason"] == \
            ROTATION_DISABLED
        assert log.rotated_shards() == []


class TestWritePathThreadSafety:
    """轮转会在替换点关闭长期句柄 ⇒ 它与 append 必须**进程内互斥**

    否则另一个线程可能正通过那个句柄写入，句柄在它脚下被关掉 ⇒ 该条 append
    抛 ``ValueError: I/O operation on closed file`` ⇒ 丢一条决策记录。
    """

    def test_轮转与_append_进程内互斥(self, tmp_path, monkeypatch):
        """确定性探针：轮转体执行期间，另一个线程必须拿不到进程内锁"""
        import threading

        path = _write_raw(tmp_path / "decisions.jsonl", _series(8))
        log = DecisionLog(str(path), rotate_lock_timeout=0.0)
        real = DecisionLog._rotate_locked              # noqa: SLF001
        probes = []

        def probing_rotate(self, *, trigger):
            got_holder = []

            def probe():
                got = self._lock.acquire(timeout=0.05)   # noqa: SLF001
                got_holder.append(got)
                if got:
                    self._lock.release()                 # noqa: SLF001

            thread = threading.Thread(target=probe)
            thread.start()
            thread.join()
            probes.append(got_holder)
            return real(self, trigger=trigger)

        monkeypatch.setattr(DecisionLog, "_rotate_locked", probing_rotate)
        assert log.rotate(force=True)["rotated"] is True
        assert probes == [[False]], "轮转体没有持有进程内锁（append 可能写到被关掉的句柄）"

    def test_并发显式轮转与_append_不丢记录(self, tmp_path):
        """真实双线程：轮转关闭（长期句柄）时手动轮转与 append 交替跑，一条不丢"""
        import threading

        path = _write_raw(tmp_path / "decisions.jsonl", _series(20))
        log = DecisionLog(str(path), rotate_lock_timeout=0.5,
                          append_lock_timeout=0.5)
        assert log.rotation_enabled is False            # OFF ⇒ 长期句柄快路径
        assert log.append(_ctx(), _decision(),
                          ts="2026-09-02T00:00:00+08:00") is True
        results = []
        errors = []

        def rotator():
            try:
                for _ in range(3):
                    log.rotate(force=True)
            except Exception as exc:                    # noqa: BLE001
                errors.append(exc)

        def appender():
            for index in range(30):
                ts = (datetime(2026, 9, 11, 10, 0, tzinfo=TZ)
                      + timedelta(seconds=index)).isoformat()
                results.append(log.append(_ctx(), _decision(), ts=ts))

        threads = [threading.Thread(target=rotator),
                   threading.Thread(target=appender)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert errors == []
        assert all(results), "轮转关闭期间仍有 append 失败（句柄被并发关闭？）"
        assert len(results) == 30
        assert len(_read(path)) == 20 + 1 + 30          # 一条不丢


class TestIsolationGuards:
    """守卫「隔离本身」：宿主机设了轮转开关时，用例必须仍走默认（保守）口径

    实测口径：``CP_POLICY_DECISION_LOG_MAX_BYTES=1024 CP_POLICY_DECISION_LOG_ROTATE_DAILY=1``
    下跑本文件，``test_默认配置不轮转_写再大也不动`` 仍须通过——若不通过，
    说明 ``policy_testkit.ENV_KEYS`` 漏了新变量（本用例就是那道红灯）。
    """

    def test_轮转环境变量被清理且落盘指向_tmp(self, tmp_path):
        for name in ("CP_POLICY_DECISION_LOG_MAX_BYTES",
                     "CP_POLICY_DECISION_LOG_ROTATE_DAILY",
                     "CP_POLICY_DECISION_LOG_KEEP_BYTES",
                     "CP_POLICY_DECISION_LOG_LOCK_APPENDS",
                     "CP_POLICY_DECISION_LOG_DAILY_CHECK_SECONDS"):
            assert name not in os.environ, f"{name} 未被 policy_testkit 隔离"
        value = os.environ.get("CP_POLICY_DECISION_LOG", "")
        assert value and str(tmp_path) in value, f"落盘未指向 tmp：{value!r}"
        # 默认口径：两种轮转都关（保守默认）
        assert DecisionLog(str(tmp_path / "d.jsonl")).rotation_enabled is False


class TestPublicApiStability:
    """既有公共 API 只许**增量**（签名不变），本用例把它钉住"""

    def test_既有公共签名不变(self, tmp_path):
        import inspect

        log = DecisionLog(str(tmp_path / "decisions.jsonl"))
        assert list(inspect.signature(DecisionLog.append).parameters) == \
            ["self", "ctx", "decision", "fingerprint", "revision", "ts", "extra"]
        assert list(inspect.signature(DecisionLog.read).parameters) == \
            ["self", "since", "until", "since_days", "limit", "path", "dedupe"]
        assert list(inspect.signature(DecisionLog.iter_records).parameters) == \
            ["self", "since", "until", "since_days", "path", "dedupe"]
        for name in ("flush", "close", "stats", "path", "enabled", "rotated_shards",
                     "maybe_rotate", "rotate"):
            assert hasattr(log, name), name
        assert log.flush() is True
        assert log.path.endswith("decisions.jsonl")
        assert log.enabled is True
        assert log.lock_path.endswith("decisions.jsonl.lock")


# ════════════════════════════════════════════════════════════
#  13. 写路径：轮转关闭走长期句柄快路径，且句柄在替换点必定作废
# ════════════════════════════════════════════════════════════


class TestWritePathFastPath:
    """「轮转关闭 ⇒ 复用长期句柄」与「替换点 ⇒ 句柄必作废」是一对约束

    前者是**性能验收**（单进程写入 p50/p99 不退化），后者是**安全兜底**：
    只要有任何一条路径可能执行 ``os.replace``（包括运维手调的
    ``rotate(force=True)``，默认配置下也允许），长期句柄就必须在那一刻关闭，
    否则 Windows 上替换失败、POSIX 上记录被静默写进被淘汰的 inode。
    """

    def test_轮转关闭时复用长期句柄而非每条开_关(self, tmp_path, monkeypatch):
        """以 ``builtins.open`` 的**实际调用次数**为准（不是看配置，而是看行为）"""
        import builtins

        path = tmp_path / "decisions.jsonl"
        log = DecisionLog(str(path))
        assert log.rotation_enabled is False
        real_open = builtins.open
        opened = []

        def counting_open(file, *args, **kwargs):
            if str(file) == str(path):
                opened.append(str(file))
            return real_open(file, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", counting_open)
        first = None
        for index in range(20):
            assert log.append(_ctx(), _decision(),
                              ts=(BASE_TS + timedelta(seconds=index)).isoformat())
            if first is None:
                first = log._handle                      # noqa: SLF001
        assert len(opened) == 1, f"长期句柄应只开一次，实际 {len(opened)} 次"
        assert log._handle is first                      # noqa: SLF001 句柄身份稳定
        assert log.stats["handle_reuse"] == 20            # 20 条全走长期句柄快路径
        assert log.stats["handle_open"] is True
        assert len(_read(path)) == 20

    def test_轮转开启时不留长期句柄(self, tmp_path):
        """轮转开启 ⇒ 必须每次开-关（否则 os.replace 在 Windows 上必然失败）"""
        path = tmp_path / "decisions.jsonl"
        log = DecisionLog(str(path), max_bytes=1 << 20, rotate_lock_timeout=0.0)
        assert log.rotation_enabled is True
        for index in range(5):
            assert log.append(_ctx(), _decision(),
                              ts=(BASE_TS + timedelta(seconds=index)).isoformat())
        assert log._handle is None                       # noqa: SLF001
        assert log.stats["handle_reuse"] == 0
        assert log.stats["handle_open"] is False

    def test_轮转开启时_append_触发轮转后仍能读到全部(self, tmp_path):
        """开启轮转 ⇒ 慢路径；轮转（含替换）与 append 交错也不丢"""
        path = tmp_path / "decisions.jsonl"
        log = DecisionLog(str(path), max_bytes=1500, keep_bytes=400,
                          rotate_lock_timeout=0.0, append_lock_timeout=0.0)
        for index in range(12):
            assert log.append(_ctx(), _decision(), extra={"blob": "x" * 200},
                              ts=(BASE_TS + timedelta(seconds=index)).isoformat())
        assert log.stats["rotation_count"] >= 1
        assert log.rotated_shards()
        assert len(_read(path)) == 12                    # 已归档 + 活动文件都在
        assert log.stats["rotation_generation"] == log.stats["rotation_count"]

    def test_轮转关闭时手动_force_轮转后_append_仍可被读到(self, tmp_path):
        """★ 孤儿 inode 回归用例（默认配置 + 运维手调 rotate(force=True)）

        轮转关闭 ⇒ 快路径持有长期句柄；此时运维**仍可**显式 ``rotate(force=True)``
        让它执行 ``os.replace``。若忘了在替换点关闭句柄：
          - Windows：``os.replace`` 直接失败（WinError 5）⇒ ``rotated`` 不为 True；
          - POSIX：替换成功但句柄指向被淘汰的 inode ⇒ 之后的 append 落进
            ``_candidate_files()`` 读不到的文件 ⇒ 下面的 6 条"新记录"会消失。
        两种平台都会让本用例红。
        """
        path = _write_raw(tmp_path / "decisions.jsonl", _series(6))
        log = DecisionLog(str(path), rotate_lock_timeout=0.0)
        assert log.append(_ctx(), _decision(),
                          ts="2026-09-01T23:00:00+08:00") is True   # 建立长期句柄
        assert log._handle is not None                   # noqa: SLF001
        summary = log.rotate(force=True)
        assert summary["rotated"] is True, summary       # Windows 上忘了关句柄会失败
        assert log.stats["rotation_generation"] == 1
        assert log._handle is None                       # noqa: SLF001 句柄已在替换点作废
        # 轮转后的新写入必须落在 read() 看得见的地方
        for index in range(6):
            ts = (datetime(2026, 9, 10, 10, 0, tzinfo=TZ)
                  + timedelta(seconds=index)).isoformat()
            assert log.append(_ctx(), _decision(effect="allow", actor=f"new{index}"),
                              ts=ts) is True
        records = _read(path)
        new_ts = [record.ts for record in records if record.ts.startswith("2026-09-10")]
        assert len(new_ts) == 6, f"轮转后的写入读不到 ⇒ 句柄指向了孤儿 inode：{new_ts}"
        assert len(records) == 13                        # 6 旧 + 1 中间 + 6 新
        assert log._handle is not None                   # noqa: SLF001 之后重新持有句柄
        assert log.stats["handle_reopens"] == 0          # 重开是"因作废"，不是"因复核"
        # 新写入必须落在**活动文件**（磁盘上那个真文件）里，而不是某个孤儿 inode
        raw = path.read_text(encoding="utf-8")
        assert "2026-09-10T10:00:05+08:00" in raw

    def test_轮转开启时_手动_force_轮转后_append_仍可被读到(self, tmp_path):
        """同上，但配置开启（走开-关慢路径）——两条路径都要过这道回归

        阈值取 1 MiB（远大于本用例数据量）⇒ 那次 append 不会自动触发轮转，
        于是这次的 ``rotate(force=True)`` 是真的在执行一次替换。
        """
        path = _write_raw(tmp_path / "decisions.jsonl", _series(6))
        log = DecisionLog(str(path), max_bytes=1 << 20, keep_bytes=1,
                          rotate_lock_timeout=0.0, append_lock_timeout=0.0)
        assert log.rotation_enabled is True
        assert log.append(_ctx(), _decision(),
                          ts="2026-09-01T23:00:00+08:00") is True
        assert log._handle is None                       # noqa: SLF001 慢路径不留句柄
        assert log.stats["rotation_count"] == 0          # 未达阈值，确实还没轮转过
        summary = log.rotate(force=True, trigger=TRIGGER_SIZE)
        assert summary["rotated"] is True, summary
        assert log.stats["rotation_generation"] == 1
        for index in range(6):
            ts = (datetime(2026, 9, 10, 10, 0, tzinfo=TZ)
                  + timedelta(seconds=index)).isoformat()
            assert log.append(_ctx(), _decision(effect="allow", actor=f"new{index}"),
                              ts=ts) is True
        records = _read(path)
        assert len([r for r in records if r.ts.startswith("2026-09-10")]) == 6
        assert len(records) == 13
        assert "2026-09-10T10:00:05+08:00" in path.read_text(encoding="utf-8")

    def test_代际变化导致句柄重开(self, tmp_path):
        """代际闸**独立于**"替换点关句柄"：即便句柄没在替换时被作废，快路径也要重开

        做法：真实跑一次成功轮转（代际 0→1，句柄在替换点被关闭），然后把那个**已被
        关闭的旧句柄**塞回实例并把它标成旧代际——等价于"替换发生了、但句柄忘了作废"。
        快路径命中句柄时发现代际不符 ⇒ 关闭重开 ⇒ append 仍成功。
        （若代际闸失效，往已关闭的句柄写入会抛 ValueError，append 返回 False。）
        """
        path = _write_raw(tmp_path / "decisions.jsonl", _series(6))
        log = DecisionLog(str(path), rotate_lock_timeout=0.0)
        assert log.append(_ctx(), _decision(),
                          ts="2026-09-01T23:00:00+08:00") is True
        stale = log._handle                              # noqa: SLF001
        assert log.rotate(force=True)["rotated"] is True
        assert log.stats["rotation_generation"] == 1
        # 注入"替换时忘了作废句柄"的错误状态（旧句柄已关闭 + 旧代际）
        log._handle = stale                              # noqa: SLF001
        log._handle_generation = 0                       # noqa: SLF001
        assert log.append(_ctx(), _decision(),
                          ts="2026-09-10T10:00:00+08:00") is True
        assert log._handle is not stale                  # noqa: SLF001 代际闸已重开
        assert "2026-09-10T10:00:00+08:00" in path.read_text(encoding="utf-8")

    def test_句柄身份复核_指向别的文件时重开(self, tmp_path, monkeypatch):
        """跨进程替换（本进程配置看不见）由周期性身份复核兜住

        本进程不可能替换自己打开着的文件（Windows 直接拒绝、POSIX 才是孤儿 inode
        的来源），所以要模拟"路径上已经不是那个文件"：把实例指向另一个文件，
        等价于句柄的 inode 与路径上的文件不再一致。
        """
        import agent.policy.decisions as decisions_mod

        path = tmp_path / "decisions.jsonl"
        other = tmp_path / "other.jsonl"
        log = DecisionLog(str(path))
        assert log.append(_ctx(), _decision()) is True
        stale = log._handle                              # noqa: SLF001
        monkeypatch.setattr(decisions_mod, "HANDLE_RECHECK_EVERY", 1)
        monkeypatch.setattr(log, "_path", str(other))
        assert log.append(_ctx(), _decision()) is True
        assert log._handle is not stale                  # noqa: SLF001 已重开
        assert log.stats["handle_reopens"] == 1
        assert other.exists() and other.stat().st_size > 0
        # 复核只在"拿不到 inode"时才放弃判定；本机（Windows）st_ino 可用
        assert os.stat(path).st_ino and os.stat(other).st_ino

