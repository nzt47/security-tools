"""TASK-06 新颖性感知学习管线 — 分类/钩子/容量/降级测试

覆盖（任务书 §6 功能验收 + 测试要求）:
    1. 分类: 4 类 diff 样例 → 事件类型/置信度/分级正确；未知类型不学习
    2. 钩子: 开关关 → 零写入；开关开 + 低置信 → 记忆出现该事件；
       高置信 → 草稿文件出现（仅 DRAFT，不注册技能）
    3. 容量: change_log 超上限正确滚动；兼容旧文件格式
    4. 降级: 记忆写入抛错时感知主链路（ChangeDetector.collect）正常

守【不易】: 全部使用 tmp_path 隔离持久化日志，绝不触碰真实 ~/.Yunshu。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sensor.change_detector import ChangeDetector
from sensor.novelty import (
    NoveltyEvent,
    classify_change,
    classify_changes,
    trim_change_log,
)


def _change(change_type: str, **overrides):
    """构造一条 ChangeDetector diff 条目。"""
    base = {
        "name": f"change_{change_type}",
        "value": "v",
        "type": change_type,
        "severity": "normal",
        "description": f"变更: {change_type}",
    }
    base.update(overrides)
    return base


def _snapshot(device_name: str = "a"):
    """构造一份简化快照（仅含设备字段，便于触发 diff）。"""
    return {
        "devices": {device_name: {"name": device_name, "status": "OK", "class": "USB"}},
        "disk_partitions": {},
        "processes": [],
        "services": [],
        "system_info": {"hostname": "h"},
        "registry": {},
        "environment": {},
        "hash": "h",
    }


def _make_change_detector(tmp_path, **kwargs):
    """构造 ChangeDetector（持久化日志隔离到 tmp_path）。"""
    return ChangeDetector(persistent_log_dir=str(tmp_path / "changes"), **kwargs)


# ═══════════════════════════════════════════════════════════════
#  1. 分类器
# ═══════════════════════════════════════════════════════════════

def test_classify_hardware_change_high_confidence():
    """硬件变更 → hardware_change，高置信(0.85)，level=high。"""
    ev = classify_change(_change("device_added"))
    assert ev is not None
    assert ev.event_type == "hardware_change"
    assert ev.confidence == 0.85
    assert ev.level == "high"


def test_classify_process_change_medium_confidence():
    """进程新增/移除 → process_change，中置信(0.55)，level=medium。"""
    for t in ("process_started", "process_stopped"):
        ev = classify_change(_change(t))
        assert ev is not None
        assert ev.event_type == "process_change"
        assert ev.confidence == 0.55
        assert ev.level == "medium"


def test_classify_file_change_low_confidence():
    """文件批量变更 → file_change，低置信(0.3)，level=low。"""
    ev = classify_change(_change("file_modified"))
    assert ev is not None
    assert ev.event_type == "file_change"
    assert ev.confidence == 0.30
    assert ev.level == "low"


def test_classify_behavior_drift_medium_confidence():
    """行为漂移 → behavior_drift，中置信(0.5)。"""
    ev = classify_change(_change("behavior_drift"))
    assert ev is not None
    assert ev.event_type == "behavior_drift"
    assert ev.confidence == 0.50
    assert ev.level == "medium"


def test_classify_unknown_type_skipped():
    """未命中规则（注册表/环境变量等噪音）→ 不学习。"""
    assert classify_change(_change("registry_changed")) is None
    events = classify_changes([_change("device_added"), _change("registry_changed")])
    assert [e.event_type for e in events] == ["hardware_change"]


def test_novelty_event_level_boundaries():
    """置信度分级边界: 0.7→high；0.4→medium；<0.4→low。"""
    assert NoveltyEvent("t", "normal", "s", 0.7, "a", "t").level == "high"
    assert NoveltyEvent("t", "normal", "s", 0.4, "a", "t").level == "medium"
    assert NoveltyEvent("t", "normal", "s", 0.39, "a", "t").level == "low"


# ═══════════════════════════════════════════════════════════════
#  3. change_log.json 容量控制
# ═══════════════════════════════════════════════════════════════

def test_trim_change_log_rolls_oldest():
    """超上限 → 滚动删除最旧，保持顺序。"""
    entries = [{"seq": i} for i in range(5)]
    trimmed = trim_change_log(entries, max_entries=3)
    assert [e["seq"] for e in trimmed] == [2, 3, 4]


def test_trim_change_log_within_limit_or_no_limit():
    """未超上限 / max_entries<=0 → 原样返回。"""
    entries = [{"seq": 0}, {"seq": 1}]
    assert trim_change_log(entries, max_entries=5) is entries
    assert trim_change_log(entries, max_entries=0) is entries
    assert trim_change_log(entries, max_entries=None) is entries


def test_change_detector_capacity_rolls(tmp_path):
    """ChangeDetector 持久化日志超 max_entries → 磁盘与内存同步滚动。"""
    cd = _make_change_detector(tmp_path, max_entries=3)
    for i in range(5):
        cd._save_to_persistent_log({"seq": i})
    assert [e["seq"] for e in cd._persistent_log] == [2, 3, 4]
    data = json.loads(
        (Path(tmp_path) / "changes" / "change_log.json").read_text(encoding="utf-8"))
    assert [e["seq"] for e in data] == [2, 3, 4]


def test_change_detector_load_compat(tmp_path):
    """兼容旧文件: 纯数组 / dict-with-entries / 损坏文件。"""
    changes_dir = tmp_path / "changes"
    changes_dir.mkdir(parents=True)
    path = changes_dir / "change_log.json"
    # 旧格式: 纯数组（超上限滚动）
    path.write_text(json.dumps([{"seq": 0}, {"seq": 1}, {"seq": 2}]), encoding="utf-8")
    cd = ChangeDetector(persistent_log_dir=str(changes_dir), max_entries=2)
    assert [e["seq"] for e in cd._persistent_log] == [1, 2]
    # dict-with-entries 兼容
    path.write_text(json.dumps({"entries": [{"seq": 7}]}), encoding="utf-8")
    cd2 = ChangeDetector(persistent_log_dir=str(changes_dir))
    assert [e["seq"] for e in cd2._persistent_log] == [7]
    # 损坏文件回退空
    path.write_text("{broken", encoding="utf-8")
    cd3 = ChangeDetector(persistent_log_dir=str(changes_dir))
    assert cd3._persistent_log == []


# ═══════════════════════════════════════════════════════════════
#  2. 钩子（观察模式 / 分级沉淀）
# ═══════════════════════════════════════════════════════════════

def test_learning_hook_disabled_zero_side_effects(tmp_path, monkeypatch):
    """开关关（观察模式）→ 零学习副作用：无记忆/草稿/审计文件。"""
    import agent.learning.novelty_hooks as nh

    monkeypatch.setattr(nh, "_sensor_learning_enabled", lambda: False)
    hook = nh.make_learning_hook(memory_dir=str(tmp_path / "mem"),
                                 draft_dir=str(tmp_path / "drafts"),
                                 audit_path=str(tmp_path / "audit.jsonl"))
    hook([_change("device_added"), _change("file_modified")])
    assert not (tmp_path / "mem").exists()
    assert not (tmp_path / "drafts").exists()
    assert not (tmp_path / "audit.jsonl").exists()


def test_learning_hook_low_confidence_writes_memory(tmp_path, monkeypatch):
    """开关开 + 低置信事件 → 记忆出现该事件（不产草稿）。"""
    import agent.learning.novelty_hooks as nh

    monkeypatch.setattr(nh, "_sensor_learning_enabled", lambda: True)
    hook = nh.make_learning_hook(memory_dir=str(tmp_path / "mem"),
                                 draft_dir=str(tmp_path / "drafts"))
    hook([_change("file_modified")])
    mem_file = tmp_path / "mem" / "novelty_memory.jsonl"
    assert mem_file.exists()
    rec = json.loads(mem_file.read_text(encoding="utf-8").strip().splitlines()[0])
    assert rec["event"] == "novelty_event"
    assert rec["event_type"] == "file_change"
    assert rec["level"] == "low"
    assert not (tmp_path / "drafts").exists()  # 低置信不产草稿


def test_learning_hook_high_confidence_writes_draft(tmp_path, monkeypatch):
    """开关开 + 高置信事件 → 草稿文件出现（仅 DRAFT，不注册技能）+ 审计。"""
    import agent.learning.novelty_hooks as nh

    monkeypatch.setattr(nh, "_sensor_learning_enabled", lambda: True)
    hook = nh.make_learning_hook(memory_dir=str(tmp_path / "mem"),
                                 draft_dir=str(tmp_path / "drafts"),
                                 audit_path=str(tmp_path / "audit.jsonl"))
    hook([_change("device_added")])  # 硬件变更 → 高置信
    drafts = list((tmp_path / "drafts").glob("*.json"))
    assert len(drafts) == 1
    draft = json.loads(drafts[0].read_text(encoding="utf-8"))
    assert draft["draft_status"] == "DRAFT"  # 绝不注册技能（仅草稿）
    assert draft["event_type"] == "hardware_change"
    assert (tmp_path / "audit.jsonl").exists()  # 高置信审计留痕
    assert not (tmp_path / "mem").exists()  # 高置信不写记忆


def test_learning_hook_memory_failure_isolated(tmp_path, monkeypatch):
    """记忆写入抛错 → 钩子与感知主链路均不受影响（降级）。"""
    import agent.learning.novelty_hooks as nh

    monkeypatch.setattr(nh, "_sensor_learning_enabled", lambda: True)
    # memory_dir 指向"文件下的子路径" → mkdir 必失败 → _memory_record 兜底
    blocking = tmp_path / "blocker"
    blocking.write_text("x", encoding="utf-8")
    hook = nh.make_learning_hook(memory_dir=str(blocking / "sub"),
                                 draft_dir=str(tmp_path / "drafts"))
    hook([_change("file_modified")])  # 不抛异常

    # 感知采集主链路（ChangeDetector.collect）在钩子异常时零影响
    cd = _make_change_detector(tmp_path, learning_hook=hook)
    base = _snapshot("a")
    cd._baseline = base
    cd._last_check = base
    monkeypatch.setattr(cd, "_capture_snapshot", lambda: _snapshot("b"))
    results = cd.collect()
    assert results  # SensorReading 正常返回


# ═══════════════════════════════════════════════════════════════
#  2b. ChangeDetector 出口钩子（collect 旁路）
# ═══════════════════════════════════════════════════════════════

def test_change_detector_collect_invokes_hook(tmp_path, monkeypatch):
    """collect() 出口旁路触发钩子，diff 结果与 SensorReading 正常。"""
    hook_calls = []
    cd = _make_change_detector(tmp_path)
    cd.set_learning_hook(hook_calls.append)
    base = _snapshot("a")
    cd._baseline = base
    cd._last_check = base
    monkeypatch.setattr(cd, "_capture_snapshot", lambda: _snapshot("b"))
    results = cd.collect()
    assert results  # 主链路结果正常
    assert len(hook_calls) == 1
    assert any(c["type"] == "device_added" for c in hook_calls[0])


def test_change_detector_hook_exception_safe(tmp_path, monkeypatch):
    """钩子抛异常 → collect() 主链路零影响（兜底）。"""
    def boom(changes):
        raise RuntimeError("hook boom")

    cd = _make_change_detector(tmp_path, learning_hook=boom)
    base = _snapshot("a")
    cd._baseline = base
    cd._last_check = base
    monkeypatch.setattr(cd, "_capture_snapshot", lambda: _snapshot("b"))
    results = cd.collect()
    assert results  # 不抛异常，正常返回
