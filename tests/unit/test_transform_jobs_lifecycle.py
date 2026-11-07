"""转化队列作业台账：**归档 → 彻底删除** 的两步语义（2026-11-07）。

【锁死的口径】
  1. 归档 = 从队列**消失**、台账留证据。所以过滤必须在读取侧做：
     `list_jobs()` 默认 `include_archived=False` —— 老实现不按状态过滤，
     "归档"了照样出现在队列里（等于没归档）。
  2. 彻底删除只允许**已归档**的作业：未归档就删等于绕过证据链。
  3. 删除范围 = 台账行 + 工件目录；工件目录只清"仓库内 / 系统临时目录"这两类根，
     其它路径（如用户主目录的历史路径）只列不删。
"""

from __future__ import annotations

import json

import pytest

from agent.skills_mgmt.transform import jobs as J

pytestmark = [pytest.mark.unit, pytest.mark.p3]


def _seed(root, *, status="candidate", artifacts="", jid=None):
    job = J.upsert_job(source="ingestion", source_ref=jid or "ing_seed", form="skill",
                       title="演示作业", status=status, artifacts=artifacts, root=root)
    return job["job_id"]


def test_archive_hides_from_queue_but_keeps_evidence(tmp_path):
    jid = _seed(tmp_path, jid="ing_a")
    assert [j["job_id"] for j in J.list_jobs(root=tmp_path)] == [jid]

    job = J.archive(jid, note="演示数据，不再跟进", root=tmp_path)
    assert job["status"] == "archived"
    # 默认视图：消失
    assert J.list_jobs(root=tmp_path) == []
    # 历史视图：还在，且 history 记了这次流转
    hist = J.list_jobs(root=tmp_path, include_archived=True)
    assert [j["job_id"] for j in hist] == [jid]
    assert any(h.get("to") == "archived" for h in hist[0]["history"])
    # 摘要里 archived / visible 分开报（页面头部据此显示"已归档 N"）
    s = J.summary(root=tmp_path)
    assert s["total"] == 1 and s["archived"] == 1 and s["visible"] == 0


def test_archive_from_enabled_is_allowed(tmp_path):
    jid = _seed(tmp_path, status="enabled", jid="ing_b")
    assert J.archive(jid, root=tmp_path)["status"] == "archived"


def test_remove_job_requires_archived_first(tmp_path):
    jid = _seed(tmp_path, jid="ing_c")
    with pytest.raises(J.JobError) as exc:
        J.remove_job(jid, root=tmp_path)
    assert "已归档" in str(exc.value)
    # 台账没被动过
    assert len(J.list_jobs(root=tmp_path, include_archived=True)) == 1


def test_remove_job_deletes_ledger_row(tmp_path):
    jid = _seed(tmp_path, jid="ing_d")
    J.archive(jid, root=tmp_path)
    removed = J.remove_job(jid, root=tmp_path)
    assert removed["job_id"] == jid
    assert J.list_jobs(root=tmp_path, include_archived=True) == []
    raw = json.loads((tmp_path / "data" / "transforms" / "jobs.json").read_text(encoding="utf-8"))
    assert raw["jobs"] == []


def test_artifact_targets_marks_allowed_and_purges(tmp_path):
    art = tmp_path / "drafts" / "ing_x"
    art.mkdir(parents=True)
    (art / "card.md").write_text("x", encoding="utf-8")
    jid = _seed(tmp_path, jid="ing_e", artifacts=str(art))
    job = J.get_job(jid, root=tmp_path)
    targets = J.artifact_targets(job, allowed_roots=[tmp_path])
    assert len(targets) == 1
    assert targets[0]["exists"] is True
    assert targets[0]["allowed"] is True
    assert "card.md" in targets[0]["files"]

    purged = J.purge_artifacts(job, allowed_roots=[tmp_path])
    assert purged["deleted"] and not purged["skipped"]
    assert not art.exists()


def test_artifact_outside_allowed_roots_is_listed_but_not_deleted(tmp_path):
    # 用户主目录下的历史路径：既不在仓库内、也不在系统临时目录 ⇒ 只列不删
    from pathlib import Path
    outside = str(Path.home() / "yunshu-nonexistent-artifact")
    jid = _seed(tmp_path, jid="ing_f", artifacts=outside)
    job = J.get_job(jid, root=tmp_path)
    targets = J.artifact_targets(job)
    assert targets[0]["allowed"] is False
    # 不存在 ⇒ 既不删也不"跳过"（跳过只用于"存在但不在受控根"的目录）
    assert targets[0]["exists"] is False
    purged = J.purge_artifacts(job)
    assert purged == {"deleted": [], "skipped": []}
