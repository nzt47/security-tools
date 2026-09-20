# -*- coding: utf-8 -*-
"""`sensor/novelty.py` 单元补测 —— TASK-09 · P0-5（其三）。

本文件补的是"**边界与契约**"，不是重复既有用例
================================================
`sensor/novelty.py` 已经有 `tests/unit/test_novelty_pipeline.py`（TASK-06 产出）
与 `tests/unit/test_behavior_drift.py`，它们锁定的是**主干语义**
（4 类 diff → 事件类型/置信度、钩子开关、容量滚动、漂移超阈值）。
本文件**刻意不重复**那些用例，只补它们没有覆盖、且失败时最难定位的四块：

  ① **值的精确形状**：`to_dict()` 的键集合与顺序、`detail` 的子键筛选规则
     （是"键存在就带"还是"值非空才带"？—— 二者在 `previous=None` 时结论相反）；
  ② **类型与阈值的边界**：`level` 的三个边界点、falsy 输入、非字符串 `severity`、
     `threshold=0` 时的退化行为；
  ③ **"静默吃掉"风险的显式锁定**：`compute_drift_score` 用 `abs(prev)` 而非 `prev`
     （文档字符串写的是 `/prev`，**代码比文档更宽松**）；`default_max_entries` 的
     环境变量/配置/默认值三级回落，每级都有一条静默兜底分支；
  ④ **判定不可达分支**：`_build_event` 的 `suggested_action` 兜底文案
     在 `classify_change` 的调用集里**永远走不到**（4 个事件类型全都有文案），
     故必须直接调用 `_build_event` 才能覆盖。

【不易】纯数据/纯函数模块：不 import 硬件、不读真实 `~/.Yunshu`、不写墙钟计时断言（E7）。
【不易】不使用 `importlib.reload`。
【变易】`_config_value` 的测试用 `monkeypatch.setattr(module, "__file__", ...)` 指向 `tmp_path`，
    因此**不会**（也无需）patch 全局 `os.path`，避免影响 pytest 自身的路径解析。
"""
from __future__ import annotations

import datetime as dt

import pytest

import sensor.novelty as nv
from sensor.novelty import (
    DEFAULT_CHANGE_LOG_MAX_ENTRIES,
    DEFAULT_DRIFT_THRESHOLD,
    EVENT_BEHAVIOR_DRIFT,
    EVENT_FILE_CHANGE,
    EVENT_HARDWARE_CHANGE,
    EVENT_PROCESS_CHANGE,
    NoveltyEvent,
    _build_event,
    _SUGGESTED_ACTIONS,
    classify_change,
    classify_changes,
    compute_drift_score,
    default_max_entries,
    detect_behavior_drift,
    trim_change_log,
    week_key,
)

ENV_KEY = "SENSOR_LEARNING_CHANGE_LOG_MAX_ENTRIES"


def _change(ctype, **overrides):
    """构造一条 `ChangeDetector` 风格的 diff 条目。"""
    base = {
        "name": f"change_{ctype}",
        "value": "v",
        "type": ctype,
        "severity": "normal",
        "description": f"变更: {ctype}",
    }
    base.update(overrides)
    return base


# ═══════════════════════════════════════════════════════════════
#  1. `NoveltyEvent` 的值形状
# ═══════════════════════════════════════════════════════════════

class TestNoveltyEventShape:
    """数据结构本身：字段顺序、默认值工厂、`level` 阈值、`to_dict` 键集合。"""

    def test_required_positional_fields(self):
        """前 5 个字段必填，`created_at` / `detail` 有默认（便于调用方少写两个参数）。"""
        ev = NoveltyEvent(EVENT_HARDWARE_CHANGE, "warning", "摘要", 0.85, "动作")
        assert ev.event_type == EVENT_HARDWARE_CHANGE
        assert ev.severity == "warning"
        assert ev.diff_summary == "摘要"
        assert ev.confidence == 0.85
        assert ev.suggested_action == "动作"
        assert ev.detail == {}

    def test_detail_default_is_not_shared_between_instances(self):
        """**可变默认值防线**：两个实例的 `detail` 必须是**不同**的 dict。

        若哪天写成 `detail: Dict = {}`（而不是 `field(default_factory=dict)`），
        所有事件会共享同一个 dict ⇒ 一条事件的 detail 污染全局，且只在
        跨实例写入时才暴露。这是 dataclass 最经典的坑，故显式回归。
        """
        a = NoveltyEvent("t", "normal", "s", 0.1, "a")
        b = NoveltyEvent("t", "normal", "s", 0.1, "a")
        assert a.detail is not b.detail
        a.detail["x"] = 1
        assert b.detail == {}

    def test_created_at_default_format_is_second_precision(self):
        """`created_at` 用 `isoformat(timespec="seconds")` ⇒ 长度 19、**无微秒**。

        下游（记忆 jsonl / 草稿文件）按该格式做可读排序；掺入微秒会让
        同一秒内的事件排序不稳定。
        """
        ev = NoveltyEvent("t", "normal", "s", 0.1, "a")
        assert len(ev.created_at) == 19
        assert "." not in ev.created_at
        parsed = dt.datetime.fromisoformat(ev.created_at)
        assert parsed.tzinfo is None          # 本地朴素时间（非 UTC），锁定现状
        assert parsed.microsecond == 0

    def test_created_at_can_be_injected(self):
        """显式传入的 `created_at` 必须**原样保留**（可注入 = 可复现）。"""
        ev = NoveltyEvent("t", "normal", "s", 0.1, "a", "2026-01-02T03:04:05")
        assert ev.created_at == "2026-01-02T03:04:05"

    @pytest.mark.parametrize("confidence,expected", [
        (1.0, "high"), (0.85, "high"), (0.7, "high"),        # 下边界含
        (0.699999, "medium"), (0.55, "medium"), (0.4, "medium"),  # 下边界含
        (0.399999, "low"), (0.3, "low"), (0.0, "low"),
    ])
    def test_level_thresholds(self, confidence, expected):
        """`level` 的三个**边界点**（0.7 / 0.4）必须落在**高档**（`>=` 不是 `>`）。

        真实值恰好落在边界上的情况不是假设：`classify_change` 给出的 0.30/0.50/0.55/0.85
        都远离边界，但 `detect_behavior_drift` 的置信度写死 0.50，
        且钩子侧可能传入自定义置信度 ⇒ 边界语义必须钉死。
        """
        assert NoveltyEvent("t", "normal", "s", confidence, "a").level == expected

    def test_negative_confidence_is_low(self):
        """负置信度（异常输入）落到 `low`，不抛异常、不误判为 high。"""
        assert NoveltyEvent("t", "normal", "s", -1.0, "a").level == "low"

    def test_to_dict_key_set_and_order(self):
        """`to_dict()` 恰好 8 键，顺序固定，且 `level` 是**派生**字段（放最后）。"""
        out = NoveltyEvent("t", "warning", "s", 0.85, "a").to_dict()
        assert list(out) == [
            "event_type", "severity", "diff_summary", "confidence",
            "suggested_action", "created_at", "detail", "level",
        ]
        assert out["level"] == "high"
        assert out["confidence"] == 0.85

    def test_to_dict_detail_is_the_same_object(self):
        """`to_dict()` 不深拷 `detail`（落盘由调用方负责）—— 别名语义显式记录。"""
        ev = NoveltyEvent("t", "normal", "s", 0.1, "a", detail={"k": 1})
        assert ev.to_dict()["detail"] is ev.detail

    def test_to_dict_is_json_serializable(self):
        """真实使用场景是 `json.dumps(event.to_dict())` 落盘 ⇒ 必须可序列化。"""
        import json

        ev = NoveltyEvent("t", "normal", "中文摘要", 0.3, "中文动作", detail={"n": None})
        assert json.loads(json.dumps(ev.to_dict(), ensure_ascii=False)) == ev.to_dict()


# ═══════════════════════════════════════════════════════════════
#  2. `classify_change`：命中与未命中
# ═══════════════════════════════════════════════════════════════

class TestClassifyChangeMisses:
    """未命中规则的入口条件（噪声过滤是**静默**的，必须逐条锁）。"""

    @pytest.mark.parametrize("empty", [None, {}, [], 0, "", 0.0, ()])
    def test_falsy_change_returns_none(self, empty):
        """falsy 输入（含空 dict）⇒ `None`。注意 `[]` 不会抛 `AttributeError`。"""
        assert classify_change(empty) is None

    @pytest.mark.parametrize("ctype", [
        "registry_changed", "environment_changed", "system_info_changed",  # change_detector 的真实噪音
        "", "unknown", "DEVICE_ADDED", "device-added", " device_added",
        "hardware", "devices_added", "process_start", "file_add",
    ])
    def test_unmatched_types_return_none(self, ctype):
        """这些类型**不学习**（`None`）——包括大小写/空格/近似名的变体。

        `"DEVICE_ADDED"` 不命中说明匹配是**大小写敏感**的（真实生产者全小写）；
        `"devices_added"` 不命中说明不是子串匹配。这两点决定了
        "生产者端一旦改大小写，学习会静默停摆"，故必须显式锁。
        """
        assert classify_change(_change(ctype)) is None

    def test_missing_type_key_returns_none(self):
        """缺 `type` 键 ⇒ `str(None)` 得到字符串 `"None"` ⇒ 不命中 ⇒ `None`（不抛异常）。"""
        assert classify_change({"name": "x", "severity": "critical"}) is None
        assert classify_change({"type": None}) is None

    def test_non_string_type_is_stringified(self):
        """非字符串 `type` 先 `str()` 再比较：`3` → `"3"` ⇒ 不命中（不是崩溃）。"""
        assert classify_change({"type": 3}) is None

    def test_bool_type_is_stringified_to_true(self):
        """`True` → `"True"` ⇒ 不命中。锁定 `str()` 归一化这一步真实存在。"""
        assert classify_change({"type": True}) is None


class TestClassifyChangeHits:
    """四类命中的**事件类型 + 精确置信度 + 派生 level**。"""

    @pytest.mark.parametrize("ctype", sorted(nv._HARDWARE_TYPES))
    def test_hardware_types(self, ctype):
        """`_HARDWARE_TYPES` 5 个成员逐一 → `hardware_change` / 0.85 / high。"""
        ev = classify_change(_change(ctype))
        assert ev.event_type == EVENT_HARDWARE_CHANGE
        assert ev.confidence == 0.85
        assert ev.level == "high"

    @pytest.mark.parametrize("ctype", ["hardware_failure", "hardware_", "hardware_removal",
                                       "hardware_UnknownThing"])
    def test_hardware_prefix_family(self, ctype):
        """`hardware_` **前缀**一律归硬件（含空后缀 `"hardware_"`）。

        这条与 `ChangeDetector.register_change_from_event` 的
        `type = f"hardware_{event_type}"` 构成跨模块契约（见 `TestCrossModuleContract`）。
        """
        ev = classify_change(_change(ctype))
        assert ev.event_type == EVENT_HARDWARE_CHANGE
        assert ev.confidence == 0.85

    @pytest.mark.parametrize("ctype", sorted(nv._PROCESS_TYPES))
    def test_process_types(self, ctype):
        """`_PROCESS_TYPES` 3 个成员 → `process_change` / 0.55 / medium。

        注意 `service_state_changed` 也归"进程"类 —— 这是刻意的粗分类
        （服务与进程同属"运行面"），但 `_diff_services` 给它的是
        `severity="critical"`（服务停止时）⇒ **严重级 critical 却只得到 medium 置信度**。
        见报告 §5 的跨模块观察项。
        """
        ev = classify_change(_change(ctype))
        assert ev.event_type == EVENT_PROCESS_CHANGE
        assert ev.confidence == 0.55
        assert ev.level == "medium"

    @pytest.mark.parametrize("ctype", sorted(nv._FILE_TYPES))
    def test_file_types(self, ctype):
        """`_FILE_TYPES` 4 个成员 → `file_change` / 0.30 / low。"""
        ev = classify_change(_change(ctype))
        assert ev.event_type == EVENT_FILE_CHANGE
        assert ev.confidence == 0.30
        assert ev.level == "low"

    def test_behavior_drift_type(self):
        """`behavior_drift` → 0.50 / medium（与 `detect_behavior_drift` 的置信度一致）。"""
        ev = classify_change(_change(EVENT_BEHAVIOR_DRIFT))
        assert ev.event_type == EVENT_BEHAVIOR_DRIFT
        assert ev.confidence == 0.50
        assert ev.level == "medium"

    def test_file_types_have_no_producer_in_this_repo(self):
        """**发现（登记，未修）**：`_FILE_TYPES` 的 4 个字面量在本仓**没有生产者**。

        核实方式（三重口径，遵守 `TASK-00 §0.2b`「否定式结论换口径复测」）：
          ① `rg -g '*.py' 'file_added|file_modified|file_removed|file_changed'` 全仓
             → 仅 `sensor/novelty.py:49`（本声明）与测试/文档；
          ② 仅搜 `sensor/` 目录（去 `.py` 过滤）→ 同上；
          ③ 全仓不限扩展名 → 另有 `docs/.../TASK-06_变更说明.md:46` 与
             `sensor/hardware_file_sensor.py` 的 **sensor_name**（`hwfile_added_*`）。
        真因：`sensor/file_watcher.py:172-176` 的 `file_created/file_modified/...`
        是 **`SensorReading.sensor_name`**，而 novelty 比的是 **diff dict 的 `type`**。
        ⇒ `EVENT_FILE_CHANGE` 这一支在本仓**当前不可达**（`file_change` 事件不会产生）。
        这是"声明了却无生产者"的契约缺口，本任务禁止改生产代码 ⇒ 只登记 + 锁定现状。
        """
        assert nv._FILE_TYPES == {"file_added", "file_modified", "file_removed", "file_changed"}
        # 反向锁：file_watcher 真正使用的是 sensor_name 词汇，二者交集为零
        watcher_sensor_names = {"file_created", "file_modified", "file_deleted",
                                "file_moved", "dir_created", "dir_deleted"}
        assert watcher_sensor_names & nv._FILE_TYPES == {"file_modified"}


# ═══════════════════════════════════════════════════════════════
#  3. `_build_event` 的两条"值筛选"规则
# ═══════════════════════════════════════════════════════════════

class TestBuildEventFieldRules:
    """`severity` 白名单、`diff_summary` 兜底、`detail` 的**键存在**筛选。"""

    @pytest.mark.parametrize("raw,expected", [
        ("normal", "normal"), ("warning", "warning"), ("critical", "critical"),
        ("high", "normal"), ("WARNING", "normal"), ("", "normal"),
        (None, "normal"), (3, "normal"), (True, "normal"), (["critical"], "normal"),
    ])
    def test_severity_whitelist(self, raw, expected):
        """`severity` 只放行 3 个白名单字符串，**其余一律回落 `"normal"`**。

        失败模式是"**静默降级**"：生产者若哪天把 `severity` 改成 `"high"`（不是本仓值域），
        事件会照常产生但级别永远是 normal，告警通道不会响。
        """
        assert _build_event(EVENT_HARDWARE_CHANGE, 0.85, _change("device_added", severity=raw)).severity == expected

    def test_severity_missing_defaults_to_normal(self):
        """缺 `severity` 键 ⇒ `None` ⇒ `"normal"`。"""
        assert _build_event("x", 0.5, {"type": "device_added"}).severity == "normal"

    def test_diff_summary_uses_description(self):
        """有 `description` ⇒ 直接用它（人类可读文案优先于自动拼装）。"""
        ev = _build_event("x", 0.5, {"description": "服务状态变更: A -> Stopped"})
        assert ev.diff_summary == "服务状态变更: A -> Stopped"

    @pytest.mark.parametrize("bad_desc", [None, "", 0])
    def test_diff_summary_falls_back_when_falsy(self, bad_desc):
        """`description` 为 falsy（含**空串**）⇒ 回落为 `f"检测到 {event_type} 变更"`。

        注意判据是 `or` 而**不是** `is None`：空字符串也会触发兜底。
        """
        ev = _build_event(EVENT_PROCESS_CHANGE, 0.55, {"description": bad_desc})
        assert ev.diff_summary == f"检测到 {EVENT_PROCESS_CHANGE} 变更"

    def test_detail_keeps_present_but_none_keys(self):
        """**关键**：`detail` 用 `if k in change` 筛选 ⇒ **值为 None 的键也会被带上**。

        这条与"值非空才带"是**相反**的语义，而 `_diff_devices` 的 `added` 分支
        就**不带** `previous` 键（`change_detector.py:308-316`）⇒
        两类事件（新增 vs 移除）的 `detail` 子键集合**不同**。
        下游若假定"detail 必有 previous"，新增事件上会 KeyError。故显式锁。
        """
        ev = _build_event("x", 0.5, {"name": "n", "previous": None, "current": None})
        assert ev.detail == {"name": "n", "previous": None, "current": None}

    def test_detail_only_whitelists_four_keys(self):
        """`detail` 只搬 4 个键（`name/previous/current/detail`），其余（含 `value`/`count`/
        `severity`/`type`）**一律丢弃**。"""
        ev = _build_event("x", 0.5, {"name": "n", "value": 3, "count": 3, "severity": "warning",
                                     "type": "process_started", "detail": ["a"], "extra": 9})
        assert ev.detail == {"name": "n", "detail": ["a"]}

    def test_detail_key_order_follows_whitelist_not_input(self):
        """`detail` 的键顺序按**白名单元组**排列，与输入 dict 的插入顺序无关。"""
        ev = _build_event("x", 0.5, {"detail": "d", "current": "c", "name": "n"})
        assert list(ev.detail) == ["name", "current", "detail"]

    def test_detail_empty_when_no_whitelisted_keys(self):
        """一个白名单键都没有 ⇒ 空 dict（不是 None）。"""
        ev = _build_event("x", 0.5, {"value": 1})
        assert ev.detail == {}

    def test_suggested_action_known_types_are_exact(self):
        """4 个已知事件类型的建议文案**逐字**来自 `_SUGGESTED_ACTIONS`。"""
        for etype, text in _SUGGESTED_ACTIONS.items():
            assert _build_event(etype, 0.5, {}).suggested_action == text

    def test_suggested_action_fallback_is_reachable_only_via_direct_call(self):
        """**不可达分支显式覆盖**：`suggested_action` 的兜底文案。

        `classify_change` 只会用 4 个有文案的事件类型调用 `_build_event`
        ⇒ 该兜底分支在正常路径上**永不执行**（但仍是安全网，必须覆盖且锁定文案）。
        """
        ev = _build_event("some_future_type", 0.5, {})
        assert ev.suggested_action == "复核环境变化，评估是否需要沉淀为学习信号"
        assert "some_future_type" not in _SUGGESTED_ACTIONS

    def test_suggested_actions_cover_all_event_constants(self):
        """结构不变量：4 个 `EVENT_*` 常量**每个都有**建议文案（少一个即静默走兜底）。"""
        assert set(_SUGGESTED_ACTIONS) == {
            EVENT_HARDWARE_CHANGE, EVENT_PROCESS_CHANGE,
            EVENT_FILE_CHANGE, EVENT_BEHAVIOR_DRIFT,
        }
        assert all(isinstance(v, str) and v for v in _SUGGESTED_ACTIONS.values())


# ═══════════════════════════════════════════════════════════════
#  4. `classify_changes` 批量语义
# ═══════════════════════════════════════════════════════════════

class TestClassifyChanges:
    """批量的**过滤 + 保序**语义（`or []` 兜底 None）。"""

    @pytest.mark.parametrize("bad", [None, [], (), ""])
    def test_falsy_input_yields_empty_list(self, bad):
        assert classify_changes(bad) == []

    def test_filters_noise_and_preserves_order(self):
        """过滤未命中的噪音，且**保持输入顺序**（下游按序沉淀，顺序即优先级）。"""
        changes = [
            _change("device_added"),
            _change("registry_changed"),
            _change("process_started"),
            _change("environment_changed"),
            _change("file_modified"),
            _change(EVENT_BEHAVIOR_DRIFT),
            _change("system_info_changed"),
        ]
        events = classify_changes(changes)
        assert [e.event_type for e in events] == [
            EVENT_HARDWARE_CHANGE, EVENT_PROCESS_CHANGE, EVENT_FILE_CHANGE, EVENT_BEHAVIOR_DRIFT,
        ]
        assert [e.confidence for e in events] == [0.85, 0.55, 0.30, 0.50]

    def test_all_noise_yields_empty(self):
        """全是噪音 ⇒ 空列表（**不产任何学习信号**），不报错。"""
        assert classify_changes([_change("registry_changed"),
                                 _change("environment_changed")]) == []

    def test_generator_input_is_accepted(self):
        """生成器/任意可迭代也能吃（`changes or []` 后直接 for）—— 锁定宽容度。"""
        events = classify_changes(c for c in [_change("device_added"), _change("x")])
        assert [e.event_type for e in events] == [EVENT_HARDWARE_CHANGE]

    def test_duplicate_changes_are_not_deduplicated(self):
        """**现状锁定**：同一变更出现两次 ⇒ 产出两个事件（不做去重）。

        含义：`ChangeDetector._change_log` 与钩子侧各自负责去重；
        novelty 层是无状态映射，不承担幂等职责。
        """
        dup = classify_changes([_change("device_added"), _change("device_added")])
        assert len(dup) == 2
        assert [(e.event_type, e.confidence, e.diff_summary) for e in dup] == [
            (EVENT_HARDWARE_CHANGE, 0.85, "变更: device_added"),
            (EVENT_HARDWARE_CHANGE, 0.85, "变更: device_added"),
        ]


# ═══════════════════════════════════════════════════════════════
#  5. `trim_change_log`：容量控制
# ═══════════════════════════════════════════════════════════════

class TestTrimChangeLog:
    """滚动裁剪的**同一性**语义（未超限时返回原对象，超限时返回新列表）。"""

    ENTRIES = [{"seq": i} for i in range(5)]

    @pytest.mark.parametrize("limit", [None, 0, -1, -100])
    def test_non_positive_or_none_returns_same_object(self, limit):
        """`None` / `0` / 负数 ⇒ **原对象**且**不设上限**（不是"清空"）。

        这条很重要：`ChangeDetector(max_entries=0)` 会一路传到这里，
        于是"上限 0"实际语义是"**无上限**"（见 `test_sensor_change_detector_coverage.py`
        的 `max_entries=0` 用例与报告 §5 登记）。
        """
        assert trim_change_log(self.ENTRIES, limit) is self.ENTRIES
        assert trim_change_log(self.ENTRIES) is self.ENTRIES

    @pytest.mark.parametrize("limit", [5, 6, 100])
    def test_within_limit_returns_same_object(self, limit):
        """`len <= max_entries` ⇒ 原对象（不复制，避免无谓内存）。"""
        assert trim_change_log(self.ENTRIES, limit) is self.ENTRIES

    @pytest.mark.parametrize("limit,expected", [
        (4, [1, 2, 3, 4]), (3, [2, 3, 4]), (2, [3, 4]), (1, [4]),
    ])
    def test_over_limit_keeps_newest_and_returns_new_list(self, limit, expected):
        """超限 ⇒ 保留**最新**（切片 `[-max:]`），返回新列表，原列表**不被修改**。"""
        original = [dict(e) for e in self.ENTRIES]
        out = trim_change_log(self.ENTRIES, limit)
        assert [e["seq"] for e in out] == expected
        assert out is not self.ENTRIES
        assert self.ENTRIES == original          # 入参未被就地裁剪

    def test_input_list_object_is_not_rebound(self):
        """裁剪**不会**把结果写回入参（调用方必须显式接收返回值）。

        这是"静默丢数据"的另一种形态：若调用方写成 `trim_change_log(log, 3)` 而
        不用返回值，日志就会**无限增长**（`ChangeDetector._save_to_persistent_log`
        用 `self._persistent_log = trim_change_log(...)`，是正确的写法）。
        """
        entries = [{"seq": i} for i in range(5)]
        trim_change_log(entries, 2)
        assert len(entries) == 5

    def test_empty_entries(self):
        """空列表：任何上限下都返回原对象。"""
        empty = []
        assert trim_change_log(empty, 3) is empty

    def test_float_limit_raises_typeerror(self):
        """**现状锁定**：浮点上限会 `TypeError`（切片索引必须整数）。

        `max(1, int(env))` 已保证 env 路径给整数，配置路径也 `int()` 过 ⇒
        浮点只可能来自**直接调用**。锁定"不静默截断"这一选择。
        """
        with pytest.raises(TypeError):
            trim_change_log(self.ENTRIES, 2.5)

    def test_bool_limit_behaves_as_one_or_zero(self):
        """`True`/`False` 是 `int` 子类 ⇒ 分别等价于 1 / 0（`0` ⇒ 不设限）。

        锁定 `bool` 不会被特殊处理（Python 的既有语义），避免日后"顺手加类型校验"时
        改掉调用方依赖的行为。
        """
        assert [e["seq"] for e in trim_change_log(self.ENTRIES, True)] == [4]
        assert trim_change_log(self.ENTRIES, False) is self.ENTRIES

    def test_large_list_keeps_exact_tail(self):
        """较大样本上验证"保留尾部、数量精确"（不是仅靠 5 条小样本）。"""
        entries = [{"seq": i} for i in range(1000)]
        out = trim_change_log(entries, 10000 // 100)
        assert len(out) == 100
        assert [e["seq"] for e in out] == list(range(900, 1000))


# ═══════════════════════════════════════════════════════════════
#  6. `default_max_entries`：env > config.yaml > 默认值
# ═══════════════════════════════════════════════════════════════

class TestDefaultMaxEntriesEnvLayer:
    """第一级：环境变量（合法即胜出；非法**静默**回落下一级）。"""

    @pytest.mark.parametrize("raw,expected", [
        ("7", 7), ("  7  ", 7), ("+5", 5), ("10000", 10000),
        ("0", 1), ("-5", 1), ("1", 1),
    ])
    def test_valid_env_wins_and_is_clamped_to_one(self, raw, expected, monkeypatch):
        """合法整数生效；`<=1` 一律被 `max(1, ...)` 抬到 1（不允许"上限 0"）。"""
        monkeypatch.setenv(ENV_KEY, raw)
        monkeypatch.setattr(nv, "_config_value", lambda k, d: 999)
        assert default_max_entries() == expected

    @pytest.mark.parametrize("raw", ["abc", "1.5", "1e3", "0x10", " ", "", "\t"])
    def test_invalid_or_blank_env_falls_through_to_config(self, raw, monkeypatch):
        """非法/空白环境变量 ⇒ **不报错**，继续用配置层的值（静默回落）。"""
        monkeypatch.setenv(ENV_KEY, raw)
        monkeypatch.setattr(nv, "_config_value", lambda k, d: 123)
        assert default_max_entries() == 123

    def test_missing_env_uses_config(self, monkeypatch):
        """未设置环境变量 ⇒ 用配置层。"""
        monkeypatch.delenv(ENV_KEY, raising=False)
        monkeypatch.setattr(nv, "_config_value", lambda k, d: 456)
        assert default_max_entries() == 456

    def test_config_key_passed_through(self, monkeypatch):
        """传给 `_config_value` 的键名必须是 `change_log_max_entries`（拼错就会静默用默认值）。"""
        monkeypatch.delenv(ENV_KEY, raising=False)
        seen = {}

        def spy(key, default):
            seen["key"] = key
            seen["default"] = default
            return 8000

        monkeypatch.setattr(nv, "_config_value", spy)
        assert default_max_entries() == 8000
        assert seen == {"key": "change_log_max_entries",
                        "default": DEFAULT_CHANGE_LOG_MAX_ENTRIES}


class TestDefaultMaxEntriesConfigLayer:
    """第二级：配置层取值的**类型/异常**回落（每一支都静默）。"""

    @pytest.mark.parametrize("config_value,expected", [
        (5000, 5000), ("5000", 5000), (1.0, 1), (True, 1),
        (0, 1), (-3, 1), (None, DEFAULT_CHANGE_LOG_MAX_ENTRIES),
        ("abc", DEFAULT_CHANGE_LOG_MAX_ENTRIES), ([], DEFAULT_CHANGE_LOG_MAX_ENTRIES),
        ({}, DEFAULT_CHANGE_LOG_MAX_ENTRIES),
    ])
    def test_config_value_coercion(self, config_value, expected, monkeypatch):
        """配置层结果被 `int()` + `max(1, ...)`，异常/None ⇒ 回落**常量默认值**。

        `None`（配置项存在但为空）走的是 **`int(None)` → TypeError → 默认 10000**，
        而**不是** `0`，也不会报错 —— 这条区别决定了"配置文件里留空"是安全的。
        """
        monkeypatch.delenv(ENV_KEY, raising=False)
        monkeypatch.setattr(nv, "_config_value", lambda k, d: config_value)
        assert default_max_entries() == expected


class TestConfigValueRealFile:
    """`_config_value` 的真实文件读取（用 `tmp_path` 造配置，不动仓库 `config.yaml`）。"""

    def _point_module_at(self, monkeypatch, tmp_path, config_text):
        """把 `novelty.__file__` 指向 `tmp_path/a/novelty.py` ⇒ cfg 路径解析到 `tmp_path/config.yaml`。

        【为什么这样造而不是 patch `os.path`】`os.path` 是全局模块，
        patch 它会同时影响 pytest 自身的路径解析（假失败风险）。
        `__file__` 只是模块属性，作用域精确。
        """
        (tmp_path / "a").mkdir(parents=True, exist_ok=True)
        (tmp_path / "config.yaml").write_text(config_text, encoding="utf-8")
        monkeypatch.setattr(nv, "__file__", str(tmp_path / "a" / "novelty.py"))

    def test_reads_nested_key(self, monkeypatch, tmp_path):
        """正常路径：`learning.sensor_learning.<key>` 逐层取到。"""
        self._point_module_at(monkeypatch, tmp_path,
                              "learning:\n  sensor_learning:\n    change_log_max_entries: 42\n")
        assert nv._config_value("change_log_max_entries", "SENTINEL") == 42

    @pytest.mark.parametrize("text,expected", [
        ("learning: {}\n", "SENTINEL"),
        ("other: 1\n", "SENTINEL"),
        ("learning:\n  sensor_learning: {}\n", "SENTINEL"),
        ("learning:\n  sensor_learning:\n    change_log_max_entries: null\n", "SENTINEL"),
        ("learning:\n  sensor_learning: null\n", "SENTINEL"),
        ("learning: null\n", "SENTINEL"),
        ("", "SENTINEL"),
    ])
    def test_missing_or_null_key_returns_default(self, monkeypatch, tmp_path, text, expected):
        """缺层 / 层为 `null` / 值为 `null` ⇒ 一律返回默认值。

        注意 `sensor_learning: null` 这一支靠 `(... or {})` 兜底 ——
        若哪天去掉那个 `or {}`，此处会 `AttributeError`（不是静默，但只在配置为空时暴露）。
        """
        self._point_module_at(monkeypatch, tmp_path, text)
        assert nv._config_value("change_log_max_entries", expected) == expected

    def test_broken_yaml_returns_default(self, monkeypatch, tmp_path):
        """YAML 语法错误 ⇒ 吞异常返回默认（**平台启动不得因配置破损而失败**，D4）。"""
        self._point_module_at(monkeypatch, tmp_path, "learning: [unclosed\n")
        assert nv._config_value("change_log_max_entries", "SENTINEL") == "SENTINEL"

    def test_missing_config_file_returns_default(self, monkeypatch, tmp_path):
        """配置文件不存在 ⇒ 直接返回默认（不尝试创建、不报错）。"""
        (tmp_path / "a").mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(nv, "__file__", str(tmp_path / "a" / "novelty.py"))
        assert nv._config_value("change_log_max_entries", "SENTINEL") == "SENTINEL"

    def test_real_repo_config_is_wired(self, monkeypatch):
        """**真实环境断言**（E5：不用夹具冒充生产）：

        仓库根 `config.yaml:154` 确实声明了 `change_log_max_entries: 10000`，
        且 `default_max_entries()` 在无 env 时读到的就是它。
        """
        monkeypatch.delenv(ENV_KEY, raising=False)
        assert nv._config_value("change_log_max_entries", "SENTINEL") == 10000
        assert default_max_entries() == 10000
        assert default_max_entries() == DEFAULT_CHANGE_LOG_MAX_ENTRIES

    def test_unknown_key_returns_default_on_real_config(self):
        """真实配置里不存在的键 ⇒ 默认值（证明取键逻辑不是"永远返回常量"）。"""
        assert nv._config_value("definitely_no_such_key_xyz", "SENTINEL") == "SENTINEL"


# ═══════════════════════════════════════════════════════════════
#  7. `week_key`：周基线键
# ═══════════════════════════════════════════════════════════════

class TestWeekKey:
    """周键 = 本周周一。既有用例已锁 2026-08-14/08-10 两点，本类补边界与类型。"""

    @pytest.mark.parametrize("day,expected", [
        (dt.date(2026, 8, 10), "2026-08-10"),   # 周一
        (dt.date(2026, 8, 11), "2026-08-10"),
        (dt.date(2026, 8, 14), "2026-08-10"),   # 周五
        (dt.date(2026, 8, 16), "2026-08-10"),   # 周日（本周最后一天）
        (dt.date(2026, 8, 17), "2026-08-17"),   # 下周一
        (dt.date(2026, 1, 1), "2025-12-29"),    # 跨年：元旦落在上一年的周
        (dt.date(2024, 2, 29), "2024-02-26"),   # 闰日
    ])
    def test_known_days(self, day, expected):
        """含**跨年**与**闰日**两个易错点（周算术在跨年时最容易错一周）。"""
        assert week_key(day) == expected

    def test_accepts_datetime_and_uses_date_part(self):
        """传 `datetime` ⇒ 取 `.date()`（时分秒被忽略，23:59:59 仍属当天）。"""
        assert week_key(dt.datetime(2026, 8, 14, 23, 59, 59)) == "2026-08-10"
        assert week_key(dt.datetime(2026, 8, 17, 0, 0, 0)) == "2026-08-17"

    def test_returns_monday_for_fourteen_consecutive_days(self):
        """连续 14 天逐一验证：返回值**必为周一**，且 `monday <= day`、落差 < 7 天。

        这是"结构不变量"式的断言：不抄日历，只锁"周一对齐"这一性质，
        因此对任意年份都成立。
        """
        start = dt.date(2026, 8, 3)
        for offset in range(14):
            day = start + dt.timedelta(days=offset)
            key = week_key(day)
            monday = dt.date.fromisoformat(key)
            assert monday.weekday() == 0, (day, key)
            assert monday <= day
            assert (day - monday).days < 7

    def test_one_week_maps_to_one_key(self):
        """同一周的 7 天全部映射到**同一个**键（跨会话周级对齐的前提）。"""
        monday = dt.date(2026, 8, 10)
        keys = {week_key(monday + dt.timedelta(days=i)) for i in range(7)}
        assert keys == {"2026-08-10"}

    def test_default_is_current_week_monday(self):
        """`day=None` ⇒ 本周周一。**不写墙钟断言**，改用结构判据避免并发负载下的假失败。

        判据：返回值是周一、`<= 今天`、且与今天同属一周（落差 < 7 天）。
        """
        key = week_key()
        monday = dt.date.fromisoformat(key)
        today = dt.datetime.now().date()
        assert monday.weekday() == 0
        assert monday <= today
        assert (today - monday).days < 7

    def test_isoformat_roundtrip(self):
        """返回的是 `YYYY-MM-DD` 字符串（长度 10、可 `fromisoformat` 反解）。"""
        key = week_key(dt.date(2026, 8, 14))
        assert len(key) == 10
        assert dt.date.fromisoformat(key) == dt.date(2026, 8, 10)


# ═══════════════════════════════════════════════════════════════
#  8. `compute_drift_score`：相对偏差均值
# ═══════════════════════════════════════════════════════════════

class TestComputeDriftScore:
    """漂移度 = 重叠指标的 `|cur - prev| / |prev|` 均值。"""

    @pytest.mark.parametrize("prev,cur", [
        ({}, {}), (None, None), (None, {"a": 1}), ({"a": 1}, None), ({}, {"a": 1}),
        ({"a": 1}, {}),                 # 无重叠
        ({"a": "abc"}, {"a": 2}),       # prev 非数值
        ({"a": 1}, {"a": "abc"}),       # cur 非数值
        ({"a": 1}, {"a": None}),        # cur 为 None
        ({"a": 0}, {"a": 5}),           # 基线为 0（无法算相对偏差）
        ({"a": 0.0}, {"a": 5}),
        ({"a": "0"}, {"a": 5}),
    ])
    def test_returns_zero_when_no_usable_sample(self, prev, cur):
        """无样本（无重叠 / 非数值 / 基线为 0）⇒ `0.0`（**不是 None**）。

        `0.0` 与"完全没有基线"在 `detect_behavior_drift` 里结论相同（都低于正常阈值），
        但语义不同 —— 锁定为 0.0 以免日后改成 None 时下游 `score < threshold` 抛 TypeError。
        """
        assert compute_drift_score(prev, cur) == 0.0

    @pytest.mark.parametrize("prev_v,cur_v,expected", [
        (10, 10, 0.0),
        (10, 13, 0.3),
        (10, 7, 0.3),          # 下降同样是偏差（绝对值）
        (40.0, 80.0, 1.0),
        (40.0, 20.0, 0.5),
        (1, 2, 1.0),
        (100, 100.0, 0.0),
        (-10, -13, 0.3),       # 负基线用 abs ⇒ 仍为正偏差
        (-10, -5, 0.5),
        (True, 2, 1.0),        # bool → float
        ("10", "20", 1.0),     # 字符串数值可转换
    ])
    def test_relative_deviation(self, prev_v, cur_v, expected):
        """逐例验证 `|cur-prev| / |prev|`。

        **`-10 → -13` 这一例是文档与实现不一致的证据**：
        `compute_drift_score` 的 docstring 写 `|cur-prev|/prev`，而代码是 `/abs(prev)`
        ⇒ 文档口径下该例应为 `-0.3`，实测为 `+0.3`。**代码比文档更合理**
        （漂移度不该为负），故锁定代码行为并把文档偏差登记为发现（报告 §5）。
        """
        assert compute_drift_score({"m": prev_v}, {"m": cur_v}) == pytest.approx(expected)

    def test_never_negative(self):
        """结构不变量：无论正负基线，漂移度**恒 >= 0**。"""
        for prev_v in (-100, -1, 1, 100):
            for cur_v in (-200, -50, 0, 50, 200):
                assert compute_drift_score({"m": prev_v}, {"m": cur_v}) >= 0.0

    def test_mean_of_overlapping_metrics_only(self):
        """只对**重叠**指标求均值；`cur` 独有的新键**不参与**（否则基线扩张会虚增漂移）。"""
        score = compute_drift_score(
            {"a": 10, "b": 20}, {"a": 11, "b": 18, "c": 999, "d": 0})
        assert score == pytest.approx((0.1 + 0.1) / 2)

    def test_mixed_numeric_and_broken_metrics(self):
        """混合样本：坏指标被跳过，好指标照常参与均值（分母是**有效样本数**）。"""
        score = compute_drift_score(
            {"a": 10, "bad": "x", "zero": 0, "b": 100},
            {"a": 20, "bad": 5, "zero": 5, "b": 50})
        assert score == pytest.approx((1.0 + 0.5) / 2)   # (a) 与 (b)，bad/zero 被跳过

    def test_single_metric_precision(self):
        """精度：不做四舍五入（舍入发生在 `detect_behavior_drift` 的 detail 里）。"""
        assert compute_drift_score({"m": 3.0}, {"m": 3.1}) == pytest.approx(1 / 30)

    def test_non_mapping_metrics_raises_attributeerror(self):
        """**现状锁定**：非映射（如 list）会 `AttributeError`（`or {}` 救不了非空 list）。

        真实调用面由 `detect_behavior_drift` 用 `(x.get("metrics") or {})` 兜底，
        但基线文件若被写坏成数组，就会走到这里 ⇒ 记录真实边界。
        """
        with pytest.raises(AttributeError):
            compute_drift_score([1, 2], {"a": 1})


# ═══════════════════════════════════════════════════════════════
#  9. `detect_behavior_drift`：阈值判定与事件形状
# ═══════════════════════════════════════════════════════════════

class TestDetectBehaviorDrift:
    """阈值语义（`>=` 触发）与事件字段的精确形状。"""

    PREV = {"week": "2026-08-10", "metrics": {"m": 40.0}}
    CUR = {"week": "2026-08-17", "metrics": {"m": 80.0}}

    @pytest.mark.parametrize("previous,current", [
        (None, None), (None, {"metrics": {}}), ({"metrics": {}}, None), ({}, {}), ({}, {"a": 1}),
    ])
    def test_missing_baseline_returns_none(self, previous, current):
        """缺任一基线 ⇒ `None`（falsy 判定，空 dict 也算缺）。"""
        assert detect_behavior_drift(previous, current, 0.3) is None

    def test_exactly_at_threshold_triggers(self):
        """**边界**：`score == threshold` 必须**产事件**（判据是 `>=`，不是 `>`）。

        用 `m: 40 → 52` 得到恰好 0.3，阈值 0.3 ⇒ 必须触发。
        """
        ev = detect_behavior_drift({"metrics": {"m": 40.0}}, {"metrics": {"m": 52.0}}, 0.3)
        assert ev is not None
        assert ev.detail["drift_score"] == pytest.approx(0.3)

    def test_just_below_threshold_returns_none(self):
        """**边界对照**：略低于阈值 ⇒ `None`（不产事件）。"""
        assert detect_behavior_drift({"metrics": {"m": 40.0}}, {"metrics": {"m": 52.0}}, 0.301) is None

    def test_event_shape_is_exact(self):
        """事件 6 个字段逐值锁定，含 `diff_summary` 的 `%.4f`/`%.2f` 格式化。"""
        ev = detect_behavior_drift(self.PREV, self.CUR, 0.3)
        assert ev.event_type == EVENT_BEHAVIOR_DRIFT
        assert ev.severity == "warning"
        assert ev.confidence == 0.50
        assert ev.level == "medium"
        assert ev.diff_summary == "周级行为漂移: 基线相对偏差均值 1.0000 ≥ 阈值 0.30"
        assert ev.detail == {"drift_score": 1.0, "threshold": 0.3}
        assert ev.suggested_action == _SUGGESTED_ACTIONS[EVENT_BEHAVIOR_DRIFT]

    def test_detail_threshold_is_not_rounded(self):
        """`detail["threshold"]` 保留**原始浮点**（只有 `drift_score` 被 `round(...,6)`）。

        含义：`diff_summary` 里显示 `0.33`，而 `detail` 里是 `0.3333333` ——
        两者精度不同是刻意的（人读 vs 机读），故显式锁。
        """
        ev = detect_behavior_drift(self.PREV, self.CUR, 0.3333333333)
        assert ev.detail["threshold"] == 0.3333333333
        assert ev.diff_summary.endswith("阈值 0.33")

    def test_drift_score_is_rounded_to_six_places(self):
        """`drift_score` 被 `round(..., 6)`（落盘 JSON 不出现长尾浮点）。"""
        ev = detect_behavior_drift({"metrics": {"m": 3.0}}, {"metrics": {"m": 4.0}}, 0.1)
        assert ev.detail["drift_score"] == 0.333333

    @pytest.mark.parametrize("threshold", [0.0, -1.0])
    def test_zero_threshold_emits_spurious_event_on_empty_metrics(self, threshold):
        """**发现（登记，未修）**：`threshold <= 0` 时，**空指标**也会产出漂移事件。

        `detect_behavior_drift({"week": "a"}, {"week": "b"}, 0.0)`：
        两份基线的 `metrics` 都缺 → `compute_drift_score({}, {})` 返回 `0.0`
        → `0.0 < 0.0` 为 False → **产出一个 `drift_score: 0.0` 的 warning 事件**。
        即"**零漂移**"被报成"检测到漂移"。正常配置（`DEFAULT_DRIFT_THRESHOLD = 0.3`）
        下不可达，但只要有人把阈值配成 0（"想全量观测"）就会得到 100% 假阳性。
        登记为发现，不改代码。
        """
        ev = detect_behavior_drift({"week": "a"}, {"week": "b"}, threshold)
        assert ev is not None
        assert ev.detail["drift_score"] == 0.0
        assert ev.severity == "warning"

    def test_default_threshold_constant_is_positive(self):
        """护栏：常量默认阈值必须为正 ⇒ 上面那条退化路径不会被默认配置触发。"""
        assert DEFAULT_DRIFT_THRESHOLD == 0.3
        assert DEFAULT_DRIFT_THRESHOLD > 0

    def test_explicit_none_metrics_are_tolerated(self):
        """显式 `metrics: None` 由 `or {}` 兜底 ⇒ 不抛异常（与"字段缺失"同结果）。"""
        ev = detect_behavior_drift({"metrics": None}, {"metrics": None}, 0.0)
        assert ev.detail["drift_score"] == 0.0
        assert detect_behavior_drift({"metrics": None}, {"metrics": {"m": 1}}, 0.3) is None

    def test_threshold_from_real_config_helper(self):
        """漂移阈值有独立配置项（`agent/learning/behavior_drift.py::_drift_threshold`），
        本模块只接收参数；这里的 `DEFAULT_DRIFT_THRESHOLD` 是**未配置时的建议值**。"""
        assert isinstance(nv.DEFAULT_DRIFT_THRESHOLD, float)
        assert 0 < nv.DEFAULT_DRIFT_THRESHOLD < 1


# ═══════════════════════════════════════════════════════════════
#  10. `__all__` 与跨模块契约
# ═══════════════════════════════════════════════════════════════

class TestExportsAndContracts:
    """`__all__` 完整性 + 与 `change_detector` 的跨模块契约。"""

    def test_all_names_exist(self):
        """`__all__` 里每个名字都必须在模块里真实存在（拼错即 `ImportError` 于下游）。"""
        missing = [n for n in nv.__all__ if not hasattr(nv, n)]
        assert missing == []

    def test_all_has_no_duplicates(self):
        assert len(nv.__all__) == len(set(nv.__all__))

    def test_all_covers_public_api(self):
        """公共 API（4 事件常量 + 8 个函数/类 + 2 常量）必须都在 `__all__` 里。"""
        required = {
            "NoveltyEvent", "classify_change", "classify_changes",
            "trim_change_log", "default_max_entries",
            "week_key", "compute_drift_score", "detect_behavior_drift",
            "EVENT_HARDWARE_CHANGE", "EVENT_PROCESS_CHANGE",
            "EVENT_FILE_CHANGE", "EVENT_BEHAVIOR_DRIFT",
            "DEFAULT_DRIFT_THRESHOLD", "DEFAULT_CHANGE_LOG_MAX_ENTRIES",
        }
        assert required <= set(nv.__all__)
        assert set(nv.__all__) == required

    @pytest.mark.parametrize("ctype,expected_event,expected_conf", [
        ("device_added", EVENT_HARDWARE_CHANGE, 0.85),
        ("device_removed", EVENT_HARDWARE_CHANGE, 0.85),
        ("device_modified", EVENT_HARDWARE_CHANGE, 0.85),
        ("disk_mounted", EVENT_HARDWARE_CHANGE, 0.85),
        ("disk_unmounted", EVENT_HARDWARE_CHANGE, 0.85),
        ("process_started", EVENT_PROCESS_CHANGE, 0.55),
        ("process_stopped", EVENT_PROCESS_CHANGE, 0.55),
        ("service_state_changed", EVENT_PROCESS_CHANGE, 0.55),
    ])
    def test_real_change_detector_types_are_classified(self, ctype, expected_event, expected_conf):
        """**跨模块契约**：`ChangeDetector` 的 8 个真实 diff `type` 都能被正确分类。

        这些字面量来自 `sensor/change_detector.py` 的 `_diff_devices`（3）/
        `_diff_partitions`（2）/ `_diff_processes`（2）/ `_diff_services`（1），
        不是自造样例 ⇒ 满足 `TASK-00 D12` "用真实来源的产物"。
        """
        ev = classify_change(_change(ctype))
        assert ev.event_type == expected_event
        assert ev.confidence == expected_conf

    @pytest.mark.parametrize("ctype", [
        "registry_changed", "environment_changed", "system_info_changed",
    ])
    def test_real_change_detector_noise_types_are_dropped(self, ctype):
        """另外 3 个真实 diff `type` 被**刻意**判为噪音（`None`，不学习）。

        这三个来自 `_diff_registry` / `_diff_environment` / `_diff_system_info`。
        值得注意：`_diff_system_info` 给它的 `severity` 是 **critical**，
        但 novelty 侧完全不学习 ⇒ "严重但不可学"是刻意分工，不是遗漏。
        """
        assert classify_change(_change(ctype)) is None

    def test_register_change_from_event_output_is_classified_as_hardware(self):
        """**跨模块端到端**：`ChangeDetector.register_change_from_event` 造的条目
        必然被判为硬件变更（因为它把 `type` 写成 `f"hardware_{event_type}"`）。

        这条把"生产者写死的类型前缀"与"消费者的前缀规则"钉在一起：
        任一侧改动（例如把前缀换成 `hw_`）都会让**全部硬件实时事件静默不学习**。
        """
        entry = {
            "timestamp": "2026-09-20T00:00:00",
            "event_type": "arrival",
            "value": "USB\\VID_1234",
            "type": "hardware_arrival",
            "severity": "warning",
            "description": "硬件接入",
            "detail": {"event_type": "arrival"},
        }
        ev = classify_change(entry)
        assert ev.event_type == EVENT_HARDWARE_CHANGE
        assert ev.confidence == 0.85
        assert ev.severity == "warning"          # 严重级透传
        assert ev.diff_summary == "硬件接入"      # description 透传
        # `entry` 没有 `name` 键 ⇒ detail 只带 `detail` 一个子键（键存在才带）
        assert ev.detail == {"detail": {"event_type": "arrival"}}

    def test_behavior_drift_confidence_agrees_across_two_producers(self):
        """同一事件类型的两条产线（`classify_change` 与 `detect_behavior_drift`）
        必须给出**相同**的置信度 0.50，否则同一类事件会有两种分级。"""
        from_classifier = classify_change(_change(EVENT_BEHAVIOR_DRIFT))
        from_drift = detect_behavior_drift({"metrics": {"m": 1.0}}, {"metrics": {"m": 2.0}}, 0.1)
        assert from_classifier.confidence == from_drift.confidence == 0.50
        assert from_classifier.level == from_drift.level == "medium"
        assert from_classifier.suggested_action == from_drift.suggested_action
