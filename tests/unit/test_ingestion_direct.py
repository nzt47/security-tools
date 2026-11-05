# -*- coding: utf-8 -*-
"""直通摄取（`service.direct` / POST /api/ingestion/direct）：文本 → 草稿。

锁四条：
1. 一次调用就落草稿（不再让人先过 preview + confirm 两道闸）；
2. 仍**只出草稿**：不安装、不注册、不启用，台账记 candidate；
3. 文本默认判 L2 ⇒ **不偷偷代跑 LLM**，如实抛 RewriteRequired；
4. 重写失败（LLM 不可用/超时）同样不写草稿 —— 宁可失败，也不产出"看起来像"的空壳。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent.ingestion import service
from agent.ingestion.models import Conformance, LEVEL_L1, RewriteRequired

TEXT = "把会议记录整理成待办：先读记录，再抽动作项，最后按负责人分组。"


@pytest.fixture(autouse=True)
def _no_audit(monkeypatch):
    monkeypatch.setattr(service, "_audit", lambda payload: None)


class _FakeLLM:
    model = "fake-llm"

    def __init__(self, reply: str = "", *, boom: bool = False) -> None:
        self.reply = reply
        self.boom = boom

    def chat(self, messages, system_prompt="", max_tokens=0, temperature=0.0):
        if self.boom:
            raise TimeoutError("slow")
        return self.reply


def test_一次调用只出草稿并进台账(monkeypatch, tmp_path):
    """L1 来源（测试放行 classify）⇒ 直通直接落草稿 + 台账 candidate，不安装。"""
    monkeypatch.setattr(service, "classify",
                        lambda asset: Conformance(LEVEL_L1, ("测试放行",)))
    captured: dict = {}
    from agent.skills_mgmt.transform import jobs as jobs_mod
    monkeypatch.setattr(jobs_mod, "upsert_job",
                        lambda **kw: captured.update(kw))

    result, plan = service.direct(TEXT, draft_dir=tmp_path / "drafts")

    assert result.draft is True
    assert result.installed is False
    assert result.queued is False
    assert plan.conformance.level == LEVEL_L1
    root = Path(result.draft_dir)
    assert (root / "SKILL.md").is_file()
    assert (root / "manifest.json").is_file()
    assert captured.get("source") == "ingestion"
    assert captured.get("status") == "candidate"
    assert captured.get("form") == "skill"


def test_文本默认判_L2_不代跑_LLM(tmp_path):
    """一段普通文本不该被"直通"悄悄送进 LLM：默认必须抛 RewriteRequired。"""
    with pytest.raises(RewriteRequired) as excinfo:
        service.direct(TEXT, draft_dir=tmp_path / "drafts")
    assert "L2" in str(excinfo.value)
    assert not (tmp_path / "drafts").exists() or not any((tmp_path / "drafts").iterdir())


def test_重写失败不写草稿(tmp_path):
    """LLM 超时/返回垃圾 ⇒ 抛 RewriteRequired，隔离区不能留半成品。"""
    with pytest.raises(RewriteRequired):
        service.direct(TEXT, allow_rewrite=True, llm_client=_FakeLLM(boom=True),
                       draft_dir=tmp_path / "drafts")
    assert not (tmp_path / "drafts").exists() or not any((tmp_path / "drafts").iterdir())


def test_空白文本直接拒绝():
    from agent.ingestion.models import InvalidSource

    with pytest.raises(InvalidSource):
        service.direct("   ")
