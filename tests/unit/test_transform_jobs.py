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