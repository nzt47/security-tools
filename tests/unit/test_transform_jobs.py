# -*- coding: utf-8 -*-
"""S1：统一转化作业台账（agent/skills_mgmt/transform/jobs.py）。

锁住三件事：
  1. **幂等**：同一 (source, source_ref) 永远同一条作业（钩子会被重复触发）；
  2. **状态机**：只许前进/旁路终态，非法流转要显式抛错（不静默吞）；
  3. **坏台账不炸**：JSON 坏掉按空台账处理（面板/登记不能因此挂掉）。
"""
from __future__ import annotations

import json

import pytest

from agent.skills_mgmt.transform import jobs


def test_同一来源同一条目幂等(tmp_path):
    a = jobs.upsert_job(source="ingestion", source_ref="ing_1", form="tool",
                        title="demo", root=tmp_path)
    b = jobs.upsert_job(source="ingestion", source_ref="ing_1", form="tool",
                        title="demo2", root=tmp_path)
    assert a["job_id"] == b["job_id"] == "ingestion:ing_1"
    assert b["title"] == "demo2"          # 元数据可补
    assert len(jobs.list_jobs(root=tmp_path)) == 1


def test_状态只能按状态机前进(tmp_path):
    jobs.upsert_job(source="ingestion", source_ref="ing_2", form="tool", root=tmp_path)
    jobs.transition("ingestion:ing_2", "normalized", root=tmp_path)
    jobs.transition("ingestion:ing_2", "registered", root=tmp_path,
                    target={"kind": "tool", "id": "demo_tool"})
    job = jobs.get_job("ingestion:ing_2", root=tmp_path)
    assert job["status"] == "registered"
    assert job["target"] == {"kind": "tool", "id": "demo_tool"}
    assert [h["to"] for h in job["history"]] == ["normalized", "registered"]
    with pytest.raises(jobs.JobError):
        jobs.transition("ingestion:ing_2", "candidate", root=tmp_path)


def test_新作业可以直接落在目标状态_导入老草稿(tmp_path):
    job = jobs.upsert_job(source="ingestion", source_ref="old_draft", form="tool",
                          status="registered", root=tmp_path)
    assert job["status"] == "registered"
    assert job["history"] == []          # 是"创建于"，不是"流转到"


def test_台账损坏时按空台账处理(tmp_path):
    path = jobs.jobs_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert jobs.list_jobs(root=tmp_path) == []
    job = jobs.upsert_job(source="transform", source_ref="tid", form="skill", root=tmp_path)
    assert job["status"] == "candidate"
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["version"] == 1 and len(raw["jobs"]) == 1


def test_登记后清掉隔离副本_raw_md(tmp_path):
    """S5「读完即弃」：作业进入 registered/enabled 才删外来原文副本"""
    tdir = tmp_path / "transforms" / "tid1"
    tdir.mkdir(parents=True)
    (tdir / "raw.md").write_text("外来原文", encoding="utf-8")
    (tdir / "evidence.json").write_text("{}", encoding="utf-8")
    jobs.upsert_job(source="transform", source_ref="tid1", form="skill",
                    artifacts=str(tdir), root=tmp_path)
    jobs.transition("transform:tid1", "normalized", root=tmp_path)
    assert (tdir / "raw.md").exists(), "候选阶段必须保留原文（P5 重写要用）"
    jobs.transition("transform:tid1", "reviewed", root=tmp_path)
    jobs.transition("transform:tid1", "registered", root=tmp_path)
    assert not (tdir / "raw.md").exists(), "登记后应清掉外来原文副本"
    assert (tdir / "evidence.json").exists()


def test_保留期开关打开则不清(tmp_path, monkeypatch):
    monkeypatch.setenv("CP_RETAIN_QUARANTINE_COPY_DAYS", "7")
    tdir = tmp_path / "transforms" / "tid2"
    tdir.mkdir(parents=True)
    (tdir / "raw.md").write_text("外来原文", encoding="utf-8")
    jobs.upsert_job(source="transform", source_ref="tid2", form="skill",
                    artifacts=str(tdir), root=tmp_path)
    jobs.transition("transform:tid2", "normalized", root=tmp_path)
    jobs.transition("transform:tid2", "registered", root=tmp_path)
    assert (tdir / "raw.md").exists(), "隔离期内不得删除"


def test_按来源与状态过滤并按更新时间倒序(tmp_path):
    jobs.upsert_job(source="ingestion", source_ref="a", form="tool", root=tmp_path)
    jobs.upsert_job(source="transform", source_ref="b", form="skill", root=tmp_path)
    jobs.transition("transform:b", "reviewed", root=tmp_path)
    only_ing = jobs.list_jobs(source="ingestion", root=tmp_path)
    assert [j["job_id"] for j in only_ing] == ["ingestion:a"]
    reviewed = jobs.list_jobs(status="reviewed", root=tmp_path)
    assert [j["job_id"] for j in reviewed] == ["transform:b"]
    s = jobs.summary(root=tmp_path)
    assert s["total"] == 2 and s["by_status"]["reviewed"] == 1
    assert jobs.list_jobs(root=tmp_path)[0]["job_id"] == "transform:b"  # 最近更新在前


def test_未知状态与不存在的作业都要报错(tmp_path):
    with pytest.raises(jobs.JobError):
        jobs.upsert_job(source="x", source_ref="y", form="tool", status="bogus", root=tmp_path)
    with pytest.raises(jobs.JobError):
        jobs.transition("nope:nope", "registered", root=tmp_path)

# ═══════════════════════════════════════════════════════════
#  取消归档（2026-11-08）：archived 不再是"只能彻底删除"的死路
# ═══════════════════════════════════════════════════════════

def test_取消归档退回归档前的状态(tmp_path):
    jobs.upsert_job(source="ingestion", source_ref="ing_u1", form="tool", root=tmp_path)
    jobs.transition("ingestion:ing_u1", "normalized", root=tmp_path)
    jobs.transition("ingestion:ing_u1", "registered", root=tmp_path,
                    target={"kind": "tool", "id": "demo_tool"})
    jobs.archive("ingestion:ing_u1", note="误归档", root=tmp_path)
    assert jobs.get_job("ingestion:ing_u1", root=tmp_path)["status"] == "archived"

    job = jobs.unarchive("ingestion:ing_u1", note="点错了", root=tmp_path)
    assert job["status"] == "registered", "必须退回归档前的状态，而不是一律退回 candidate"
    assert job["target"] == {"kind": "tool", "id": "demo_tool"}, "取消归档不该丢目标"
    history = job["history"]
    assert history[-1]["from"] == "archived" and history[-1]["to"] == "registered"
    assert history[-1]["note"] == "点错了"
    assert "ingestion:ing_u1" in [j["job_id"] for j in jobs.list_jobs(root=tmp_path)],         "取消归档后必须重新出现在（默认隐藏已归档的）队列里"


def test_取消归档可以显式指定目标状态(tmp_path):
    jobs.upsert_job(source="ingestion", source_ref="ing_u2", form="tool", root=tmp_path)
    jobs.archive("ingestion:ing_u2", root=tmp_path)
    job = jobs.unarchive("ingestion:ing_u2", status="candidate", root=tmp_path)
    assert job["status"] == "candidate"


def test_未归档的作业不能取消归档(tmp_path):
    jobs.upsert_job(source="ingestion", source_ref="ing_u3", form="tool", root=tmp_path)
    with pytest.raises(jobs.JobError):
        jobs.unarchive("ingestion:ing_u3", root=tmp_path)


def test_归档前状态未知且未显式指定就报错_不猜(tmp_path):
    path = jobs.jobs_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 老台账：只有状态、没有 history（模拟手改/历史遗留）
    path.write_text(json.dumps({"version": 1, "jobs": [{
        "job_id": "ingestion:ing_u4", "source": "ingestion", "source_ref": "ing_u4",
        "form": "tool", "status": "archived", "history": [], "rev": 1,
        "updated_at": "2026-11-01T00:00:00+00:00",
    }]}, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(jobs.JobError) as ei:
        jobs.unarchive("ingestion:ing_u4", root=tmp_path)
    assert "状态" in str(ei.value)
    # 显式给了合法状态就可以
    assert jobs.unarchive("ingestion:ing_u4", status="reviewed", root=tmp_path)["status"] == "reviewed"


def test_取消归档不能退到_archived_或非法状态(tmp_path):
    jobs.upsert_job(source="ingestion", source_ref="ing_u5", form="tool",
                    status="registered", root=tmp_path)
    jobs.archive("ingestion:ing_u5", root=tmp_path)
    with pytest.raises(jobs.JobError):
        jobs.unarchive("ingestion:ing_u5", status="archived", root=tmp_path)
    with pytest.raises(jobs.JobError):
        jobs.unarchive("ingestion:ing_u5", status="not_a_state", root=tmp_path)


def test_取消归档不删任何文件也不动工件目录(tmp_path):
    tdir = tmp_path / "transforms" / "tid_u6"
    tdir.mkdir(parents=True)
    (tdir / "raw.md").write_text("原文", encoding="utf-8")
    jobs.upsert_job(source="transform", source_ref="tid_u6", form="skill",
                    artifacts=str(tdir), root=tmp_path)
    jobs.transition("transform:tid_u6", "normalized", root=tmp_path)
    jobs.archive("transform:tid_u6", root=tmp_path)
    jobs.unarchive("transform:tid_u6", root=tmp_path)
    assert (tdir / "raw.md").exists(), "取消归档只改状态，不碰工件"
