# -*- coding: utf-8 -*-
"""定时任务的「说明」列（2026-11-13）。

Owner 要求：给定时任务加说明栏，讲清每条任务是做什么的。

【钉住的口径】
1. 注册时显式传 `description=` 优先；
2. 内置任务（20 多个模块各自注册）回落 `DEFAULT_TASK_DESCRIPTIONS`（按名字）；
3. 两者都没有 ⇒ 空串（UI 显示「—」），**不编**；
4. `list_tasks()` 是 /api/scheduler/overview 的唯一来源，说明必须随任务一起出去。
"""
from __future__ import annotations

from agent.task_scheduler import DEFAULT_TASK_DESCRIPTIONS, TaskScheduler


def test_内置任务按名字回落说明():
    sched = TaskScheduler()
    sched.add_cron_task(name="生成周报", func=lambda: None, hour=0, minute=0)
    sched.add_interval_task(name="维护·空闲补跑", func=lambda: None, interval_seconds=600)

    rows = {t["name"]: t for t in sched.list_tasks()}

    assert rows["生成周报"]["description"] == DEFAULT_TASK_DESCRIPTIONS["生成周报"]
    assert rows["维护·空闲补跑"]["description"] == DEFAULT_TASK_DESCRIPTIONS["维护·空闲补跑"]


def test_注册时显式说明优先():
    sched = TaskScheduler()
    sched.add_cron_task(name="生成周报", func=lambda: None, hour=0, minute=0,
                        description="我自己写的说明")
    rows = {t["name"]: t for t in sched.list_tasks()}
    assert rows["生成周报"]["description"] == "我自己写的说明"


def test_未知任务说明为空不编():
    sched = TaskScheduler()
    sched.add_interval_task(name="某个动态任务-xyz", func=lambda: None, interval_seconds=60)
    rows = {t["name"]: t for t in sched.list_tasks()}
    assert rows["某个动态任务-xyz"]["description"] == ""


def test_说明表覆盖全部内置任务且非空():
    """26 条内置任务都应有"做什么"的说明（新增任务时同批补表或显式传参）。"""
    builtin = [
        "生成周报", "清理旧日志", "维护·记忆库审查", "维护·知识库巡检", "维护·路由覆盖自检",
        "维护·能力清单自检", "维护·技能自动分类", "学习·记忆抽象草稿", "维护·审计全链核验",
        "维护·长期记忆归档", "维护·工作流模板补种", "维护·健康快照", "维护·记忆整理",
        "维护·空闲补跑", "系统心跳", "反馈建议执行", "周期进化", "生命周期检查",
        "行为漂移检测", "感知周期采集", "CI 状态同步", "覆盖缺口自检",
        "日志异常基线采样", "技能冲突自动处置", "SLO 指标周报", "数据保留策略归档",
    ]
    for name in builtin:
        text = DEFAULT_TASK_DESCRIPTIONS.get(name, "")
        assert text, name
        assert len(text) >= 8, "说明要能说清做什么（太短的等于没写）: " + name

def test_overview接口透传说明(monkeypatch):
    """白名单漏字段 = 前端永远拿不到（2026-11-13 第一次上线就是这么丢的）。"""
    from flask import Flask

    import plugins.mcp_scheduler as m

    class _FakeScheduler:
        def list_tasks(self):
            return [{
                "name": "生成周报", "task_id": "t1", "type": "python_func",
                "enabled": True, "cron": {"hour": 0, "minute": 0}, "last_run": None,
                "schedule_text": "每周一 00:00", "zero_token": True,
                "zero_token_note": "", "zero_token_conditional": False, "owner": "x",
                "description": "汇总本周运行数据（任务/告警/健康/技能使用）",
            }]

    monkeypatch.setattr("agent.task_scheduler.get_scheduler", lambda: _FakeScheduler())
    app = Flask(__name__)
    app.register_blueprint(m.bp)
    resp = app.test_client().get("/api/scheduler/overview")

    body = resp.get_json()
    payload = body.get("data") or body
    assert resp.status_code == 200
    assert payload["tasks"][0]["description"] == "汇总本周运行数据（任务/告警/健康/技能使用）"
