"""TASK-04 Step 2 · memory_abstractor 定时沉淀调度测试

覆盖（验收 §6 功能验收第三条 + 测试要求）:
    1. 默认关闭（config learning.precipitate_enabled=false / env 未开）→ status=disabled，不注册任务
    2. 开启（env LEARNING_PRECIPITATE_ENABLED=true）→ 注册"技能沉淀"任务
    3. unschedule 注销任务（幂等）
    4. _scheduled_run：质量门控通过的草稿 → 审计日志 + 沉淀增量 KPI；失败草稿不审计
    5. auto_register 无论传入什么都被强制 False（不变式：不擅自注册）

守【不易】: 全部使用 tmp_path 审计路径 + 假抽象器，绝不触碰真实 data/；任务注册后必须清理。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.skills_mgmt import precipitate
from agent.skills_mgmt.precipitate import (
    PrecipitateScheduler,
    TASK_NAME,
)

TASK_ENABLED_ENV = "LEARNING_PRECIPITATE_ENABLED"
TASK_INTERVAL_ENV = "LEARNING_PRECIPITATE_INTERVAL_HOURS"


class _FakeAbstractor:
    """假抽象器：记录调用参数，返回预置草稿结果。"""

    def __init__(self, results):
        self.results = results
        self.calls = []

    def abstract_new_skills(self, **kwargs):
        self.calls.append(kwargs)
        return self.results


def _passed_draft(skill_id="draft-skill-1", **overrides):
    base = {
        "cluster_id": "cl-1", "cluster_size": 5, "success_rate": 0.85,
        "draft_skill_id": skill_id, "draft_name": "草稿技能",
        "draft_description": "描述", "draft_content_preview": "预览",
        "quality_gate_passed": True, "quality_gate_reasons": [],
        "registered": False, "duplicate_of": None,
    }
    base.update(overrides)
    return base


def _passed_draft_with_body(skill_id="draft-skill-1", **overrides):
    """质量门控通过 + 完整 draft 物化（P0 #3 阶段 0，与 memory_abstractor 产出对齐）。"""
    base = _passed_draft(skill_id=skill_id)
    base["draft"] = {
        "id": skill_id,
        "name": "草稿技能",
        "description": "描述",
        "content": "核心正文内容",
        "content_type": "skill",
        "category": "custom",
        "root_cause": "根因",
        "triggers": ["触发A"],
        "steps": ["步骤1"],
        "if_then_rules": [],
        "anti_patterns": [],
        "default_params": {},
    }
    base.update(overrides)
    return base


def _cleanup_scheduler():
    """清理可能残留的沉淀任务（跨测试安全）。"""
    try:
        PrecipitateScheduler().unschedule()
    except Exception:  # noqa: BLE001 清理失败不阻断
        pass


@pytest.fixture(autouse=True)
def _precipitate_env_cleanup(monkeypatch):
    """每个用例前清掉沉淀开关相关 env，避免外部泄漏影响断言。"""
    monkeypatch.delenv(TASK_ENABLED_ENV, raising=False)
    monkeypatch.delenv(TASK_INTERVAL_ENV, raising=False)
    yield
    _cleanup_scheduler()


# ─── 调度注册 ───


def test_schedule_default_disabled(monkeypatch):
    """未显式开启 → status=disabled，不注册任务。"""
    monkeypatch.setattr(precipitate, "_precipitate_enabled", lambda: False)
    result = PrecipitateScheduler().schedule()

    assert result["status"] == "disabled"
    from agent.task_scheduler import get_scheduler
    names = [t.get("name") for t in get_scheduler().list_tasks()]
    assert TASK_NAME not in names


def test_schedule_enabled_registers_task(monkeypatch):
    """开启 → 注册定时任务（interval_hours 生效）。"""
    monkeypatch.setattr(precipitate, "_precipitate_enabled", lambda: True)
    scheduler = PrecipitateScheduler()
    result = scheduler.schedule(interval_hours=1)

    assert result["status"] == "scheduled"
    assert result["interval_hours"] == 1
    assert result["auto_register"] is False
    from agent.task_scheduler import get_scheduler
    task = next(
        (t for t in get_scheduler().list_tasks() if t.get("name") == TASK_NAME),
        None)
    assert task is not None
    assert task["interval_sec"] == 3600  # 1h * 3600（list_tasks 序列化键为 interval_sec）


def test_unschedule_removes_task(monkeypatch):
    """unschedule 注销任务；重复注销返回 False。"""
    monkeypatch.setattr(precipitate, "_precipitate_enabled", lambda: True)
    scheduler = PrecipitateScheduler()
    scheduler.schedule(interval_hours=1)

    assert scheduler.unschedule() is True
    assert scheduler.unschedule() is False  # 已注销，幂等


def test_auto_register_forced_false(monkeypatch):
    """auto_register=True 被拒绝强制回退 False（不变式）。"""
    monkeypatch.setattr(precipitate, "_precipitate_enabled", lambda: True)
    result = PrecipitateScheduler().schedule(auto_register=True)

    assert result["auto_register"] is False


# ─── 定时执行 ───


def test_scheduled_run_audit_and_kpi_no_persist(tmp_path, monkeypatch):
    """质量门控通过 → 审计日志 + KPI；失败草稿不审计；store 零写入。"""
    fake = _FakeAbstractor([_passed_draft(), _passed_draft(
        skill_id="bad-draft", quality_gate_passed=False)])
    kpi_calls = []
    monkeypatch.setattr(precipitate, "_kpi_record",
                        lambda kind: kpi_calls.append(kind))

    audit = tmp_path / "audit.jsonl"
    scheduler = PrecipitateScheduler(abstractor=fake, audit_path=str(audit))
    scheduler._scheduled_run()

    # 假抽象器收到 auto_register=False（不变式透传）
    assert fake.calls == [{"days": 30, "max_skills": 5, "auto_register": False}]
    # 审计：仅质量门控通过的 1 条
    lines = [json.loads(x) for x in
             audit.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(lines) == 1
    assert lines[0]["event"] == "precipitate_draft"
    assert lines[0]["draft_skill_id"] == "draft-skill-1"
    assert lines[0]["registered"] is False
    # KPI：沉淀增量计数仅对通过的草稿
    assert kpi_calls == ["skill"]


def test_scheduled_run_abstractor_error_swallowed(tmp_path, monkeypatch):
    """抽象器抛异常 → 不抛出（调度线程稳定性），审计为空。"""
    class _Boom:
        def abstract_new_skills(self, **kwargs):
            raise RuntimeError("abstractor boom")

    monkeypatch.setattr(precipitate, "_kpi_record", lambda kind: None)
    scheduler = PrecipitateScheduler(abstractor=_Boom(),
                                     audit_path=str(tmp_path / "audit.jsonl"))
    scheduler._scheduled_run()  # 不抛异常
    assert not (tmp_path / "audit.jsonl").exists()


# ─── P0 #3 阶段 0 · _audit_draft draft_body 物化（2026-08-14）───


def test_audit_draft_writes_full_draft_body(tmp_path):
    """完整 draft → draft_body 反序列化后与 draft 一致（人工确认闭环可重建草稿）。"""
    result = _passed_draft_with_body(skill_id="draft-skill-1")

    audit = tmp_path / "audit.jsonl"
    PrecipitateScheduler(audit_path=str(audit))._audit_draft(result)

    line = json.loads(audit.read_text(encoding="utf-8").splitlines()[0])
    body = json.loads(line["draft_body"])
    assert body["id"] == "draft-skill-1"
    assert body["name"] == "草稿技能"
    assert body["content"] == "核心正文内容"
    assert body["root_cause"] == "根因"
    assert body["triggers"] == ["触发A"]
    # 记录字段完整
    assert line["event"] == "precipitate_draft"
    assert line["draft_skill_id"] == "draft-skill-1"
    assert line["cluster_id"] == "cl-1"
    assert line["cluster_size"] == 5
    assert line["success_rate"] == 0.85
    assert line["registered"] is False


def test_audit_draft_fallback_when_serialization_fails(tmp_path, caplog):
    """draft 含不可序列化对象 → 降级 preview 摘要，记录仍写入（不阻断审计）。

    Why（覆盖率缺口 L239-247）: draft_body 序列化失败是防御性降级路径，
    必须验证降级产物为 draft_content_preview 摘要而非抛异常。
    """
    import logging

    caplog.set_level(logging.DEBUG, logger="agent.skills_mgmt.precipitate")
    result = _passed_draft_with_body(
        skill_id="unserializable",
        draft_name="不可序列化技能",
        draft_description="描述",
        draft_content_preview="预览摘要",
    )
    # object() 无法 JSON 序列化 → json.dumps 抛 TypeError
    result["draft"] = {"content": object(), "name": "不可序列化技能"}

    audit = tmp_path / "audit.jsonl"
    scheduler = PrecipitateScheduler(audit_path=str(audit))
    scheduler._audit_draft(result)  # 不抛异常

    lines = [json.loads(x) for x in
             audit.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(lines) == 1
    body = json.loads(lines[0]["draft_body"])
    # 降级产物 = draft_name/draft_description/draft_content_preview 摘要
    assert body["name"] == "不可序列化技能"
    assert body["description"] == "描述"
    assert body["content"] == "预览摘要"
    # 数据流转调试日志可观测
    assert "序列化失败" in caplog.text
    assert "降级 preview" in caplog.text


def test_audit_draft_without_draft_key(tmp_path):
    """result 无 draft 键（存量调用方）→ draft_body="{}"，正常写入不降级。"""
    audit = tmp_path / "audit.jsonl"
    PrecipitateScheduler(audit_path=str(audit))._audit_draft(
        _passed_draft(skill_id="legacy-no-draft"))

    line = json.loads(audit.read_text(encoding="utf-8").splitlines()[0])
    assert line["draft_skill_id"] == "legacy-no-draft"
    assert line["draft_body"] == "{}"


def test_audit_draft_write_failure_swallowed(tmp_path, caplog, monkeypatch):
    """审计写 OSError → 不抛异常（调度稳定性），warning 留痕。"""
    import builtins

    def _boom(*a, **kw):
        raise OSError("audit disk full")

    monkeypatch.setattr(builtins, "open", _boom)
    scheduler = PrecipitateScheduler(audit_path=str(tmp_path / "audit.jsonl"))
    scheduler._audit_draft(_passed_draft_with_body())  # 不抛异常

    assert "审计日志写入失败" in caplog.text


def test_audit_draft_debug_logs_data_flow(tmp_path, caplog):
    """DEBUG 级数据流转日志可观测：开始 → 序列化成功 → 审计写入成功。"""
    import logging

    caplog.set_level(logging.DEBUG, logger="agent.skills_mgmt.precipitate")
    PrecipitateScheduler(audit_path=str(tmp_path / "audit.jsonl"))._audit_draft(
        _passed_draft_with_body(skill_id="flow"))

    assert "_audit_draft 开始" in caplog.text
    assert "draft_body 序列化成功" in caplog.text
    assert "审计写入成功" in caplog.text
