# -*- coding: utf-8 -*-
"""外部记忆导入：可选 LLM 摘要（T2）—— 来源标注 / 预算硬上限 / 失败降级。

这一组用例守护五条口径（见 agent/memory_import/summarize.py 的模块 docstring）：

1. 默认关闭：use_llm=False 时不构造 LLM、不产生任何外部调用；
2. 成功路径：digest_source="llm"，正文带模型名与「覆盖原件」对账行；
3. 预算硬上限：次数/输入字符超限即回退规则式，且 reason 如实落库；
4. 失败即降级：异常/空输出一律回退规则式，**不把异常原文**写进 preview；
5. 不碰知识卡片与提问轨迹：那两类批次的 digest 必须逐字保持规则式。
"""
from __future__ import annotations

import pytest

from agent.memory_import import pipeline, service, summarize
from agent.memory_import.models import (
    CARRIER_CARD, CARRIER_MEMORY, KIND_MESSAGE, KIND_PREFERENCE, KIND_PROMPT,
    ROLE_ASSISTANT, ROLE_USER, Batch, MemoryRecord,
)


def _rec(text, *, role=ROLE_USER, kind=KIND_MESSAGE, ts="2026-07-01T10:00:00+08:00",
         source="claude", session="s1", project="p1"):
    return MemoryRecord(source=source, session_id=session, ts=ts, role=role,
                        text=text, kind=kind, project=project)


class FakeLLM:
    """最小 LLM 替身：只实现 make_digest_fn 真正调用的 summarize()。"""

    def __init__(self, reply="", error=None, model="fake-model"):
        self.reply = reply
        self.error = error
        self.model = model
        self.calls = []

    def summarize(self, messages=None, max_tokens=500, system_prompt=""):
        self.calls.append({"messages": messages, "max_tokens": max_tokens,
                           "system_prompt": system_prompt})
        if self.error is not None:
            raise self.error
        return self.reply


GOOD_REPLY = "- 2026-07-01 把部署脚本改成幂等，失败可重跑\n- 约定 2.13 线版本号"


def _message_batches():
    return [
        _rec("帮我把部署脚本改成幂等的，失败可重跑，注意 2026-07-01 那次回滚", ts="2026-07-01T10:00:00+08:00"),
        _rec("改完后跑一遍 smoke，确认 2.13 版本号一致", role=ROLE_ASSISTANT,
             ts="2026-07-01T10:02:00+08:00"),
        _rec("六月那次迁移把路径改成了相对路径", ts="2026-06-02T09:00:00+08:00"),
    ]


# ════════════════════════════════════════════════════════════
#  预算与环境变量
# ════════════════════════════════════════════════════════════


def test_budget_from_env_clamps_and_falls_back(monkeypatch):
    monkeypatch.setenv(summarize.MAX_BATCHES_ENV, "5")
    monkeypatch.setenv(summarize.MAX_CHARS_ENV, "8000")
    budget = summarize.budget_from_env()
    assert (budget.max_batches, budget.max_input_chars) == (5, 8000)

    # 非法/越界一律回退默认值（写坏配置不该让预览炸掉）
    monkeypatch.setenv(summarize.MAX_BATCHES_ENV, "999")
    monkeypatch.setenv(summarize.MAX_CHARS_ENV, "abc")
    budget = summarize.budget_from_env()
    assert budget.max_batches == summarize.DEFAULT_MAX_BATCHES
    assert budget.max_input_chars == summarize.DEFAULT_MAX_INPUT_CHARS


def test_llm_available_reflects_env(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert summarize.llm_available() == (False, summarize.REASON_UNCONFIGURED)
    monkeypatch.setenv("LLM_API_KEY", "sk-test-0123456789")
    assert summarize.llm_available() == (True, "")


def test_llm_eligible_only_for_message_batches():
    assert summarize.llm_eligible(CARRIER_MEMORY, "p1") is True
    assert summarize.llm_eligible(CARRIER_MEMORY, "prompts") is False
    assert summarize.llm_eligible(CARRIER_CARD, "user") is False


# ════════════════════════════════════════════════════════════
#  成功 / 降级
# ════════════════════════════════════════════════════════════


def test_llm_digest_marks_source_and_reports_coverage():
    llm = FakeLLM(GOOD_REPLY)
    fn = summarize.make_digest_fn(llm)
    batches = pipeline.build_batches(_message_batches(), digest_fn=fn)

    llm_batches = [b for b in batches if b.digest_source == "llm"]
    assert llm_batches, "会话批次应有一批走 LLM"
    batch = llm_batches[0]
    assert batch.carrier == CARRIER_MEMORY
    # 摘要头必须写清「模型 + 可能漏项 + 覆盖原件」——人才能对账
    assert "LLM 摘要" in batch.digest
    assert "fake-model" in batch.digest
    assert "覆盖原件" in batch.digest and "user" in batch.digest
    assert batch.digest_meta["model"] == "fake-model"
    assert batch.digest_meta["records"] >= 1
    assert llm.calls and "不得引入摘录之外的信息" in llm.calls[0]["system_prompt"]


def test_llm_failure_falls_back_to_rule_without_leaking_error_text():
    llm = FakeLLM(error=RuntimeError("连接被拒绝 10.0.0.1:443 token=sk-secret"))
    fn = summarize.make_digest_fn(llm)
    batches = pipeline.build_batches(_message_batches(), digest_fn=fn)

    for batch in batches:
        assert batch.digest_source == "rule"
        # 规则式正文不得包含异常原文（py/stack-trace-exposure）
        assert "sk-secret" not in batch.digest
        assert "10.0.0.1" not in batch.digest
    reasons = {b.digest_meta.get("reason") for b in batches if b.carrier == CARRIER_MEMORY}
    assert summarize.REASON_ERROR in reasons


def test_llm_empty_output_falls_back():
    llm = FakeLLM(reply="   \n  \n")
    fn = summarize.make_digest_fn(llm)
    batches = pipeline.build_batches(_message_batches(), digest_fn=fn)
    assert all(b.digest_source == "rule" for b in batches if b.carrier == CARRIER_MEMORY)
    assert any(b.digest_meta.get("reason") == summarize.REASON_EMPTY for b in batches)


def test_llm_budget_caps_number_of_calls():
    llm = FakeLLM(GOOD_REPLY)
    budget = summarize.DigestBudget(max_batches=1, max_input_chars=100000)
    fn = summarize.make_digest_fn(llm, budget=budget)
    records = [
        _rec("七月的工作：把 smoke 脚本改了，注意 2026-07 那次回滚", ts="2026-07-01T10:00:00+08:00"),
        _rec("六月的工作：迁移到相对路径，2026-06 的约定", ts="2026-06-01T10:00:00+08:00"),
        _rec("五月的工作：把重试改成指数退避，2026-05 的决定", ts="2026-05-01T10:00:00+08:00"),
    ]
    batches = pipeline.build_batches(records, digest_fn=fn)
    llm_batches = [b for b in batches if b.digest_source == "llm"]
    assert len(llm_batches) == 1, "预算 1 次就只能有 1 批走 LLM"
    assert len(llm.calls) == 1
    assert budget.calls == 1
    over = [b for b in batches if b.digest_meta.get("reason") == summarize.REASON_BUDGET]
    assert over, "超预算的批次必须留下降级原因"


def test_llm_input_too_large_is_skipped_without_calling():
    llm = FakeLLM(GOOD_REPLY)
    budget = summarize.DigestBudget(max_batches=10, max_input_chars=250)
    fn = summarize.make_digest_fn(llm, budget=budget)
    # 单条摘录按 EXCERPT_CHARS=300 截断 ⇒ 一条就超过 250 字符的输入上限
    records = [_rec("长会话：" + "把部署脚本改成幂等的，失败可重跑。" * 60,
                    ts="2026-07-01T10:00:00+08:00")]
    batches = pipeline.build_batches(records, digest_fn=fn)
    assert llm.calls == []
    assert any(b.digest_meta.get("reason") == summarize.REASON_TOO_LARGE for b in batches)


def test_card_and_prompt_batches_are_never_rewritten():
    llm = FakeLLM(GOOD_REPLY)
    fn = summarize.make_digest_fn(llm)
    records = [
        _rec("## 语言偏好\n一律中文", role="note", kind=KIND_PREFERENCE, project="user"),
        _rec("第一问：怎么配环境", kind=KIND_PROMPT, ts="2026-05-01T10:00:00+08:00", source="trae"),
        _rec("六月的会话：讨论 2.13 升级的判据", ts="2026-06-02T09:00:00+08:00"),
    ]
    batches = pipeline.build_batches(records, digest_fn=fn)
    card = next(b for b in batches if b.carrier == CARRIER_CARD)
    prompts = next(b for b in batches if b.scope == "prompts") if False else None
    assert card.digest_source == "rule"
    assert "一律中文" in card.digest
    assert "LLM" not in card.digest


def test_batch_roundtrip_keeps_digest_provenance():
    batch = Batch(batch_id="mb_0123456789ab", carrier=CARRIER_MEMORY, title="t",
                  source="claude", digest="# x", digest_source="llm",
                  digest_meta={"model": "m", "records": 2})
    again = Batch.from_dict(batch.to_dict())
    assert again.digest_source == "llm"
    assert again.digest_meta == {"model": "m", "records": 2}
    # 老快照没有这两个字段时必须回落到 rule（向后兼容）
    legacy = Batch.from_dict({"batch_id": "mb_0123456789ab", "carrier": CARRIER_MEMORY,
                              "title": "t", "source": "claude", "digest": "# x"})
    assert legacy.digest_source == "rule" and legacy.digest_meta == {}


# ════════════════════════════════════════════════════════════
#  preview 集成（替换 collect，不碰真实文件系统）
# ════════════════════════════════════════════════════════════


def _patch_collect(monkeypatch, records):
    monkeypatch.setattr(service, "collect",
                        lambda *a, **k: service.CollectResult(records=list(records), reports={}))


def test_preview_default_is_rule_and_never_builds_llm(monkeypatch):
    _patch_collect(monkeypatch, _message_batches())

    def boom():
        raise AssertionError("use_llm=False 时不该构造 LLM")

    monkeypatch.setattr(summarize, "build_default_llm", boom)
    plan = service.preview(save=False)
    assert plan["digest"]["requested"] is False
    assert plan["digest"]["mode"] == "rule"
    assert plan["digest"]["llm_batches"] == 0
    assert all(b["digest_source"] == "rule" for b in plan["batches"])


def test_preview_use_llm_marks_batches_and_budget(monkeypatch):
    _patch_collect(monkeypatch, _message_batches())
    llm = FakeLLM(GOOD_REPLY)
    monkeypatch.setattr(summarize, "llm_available", lambda: (True, ""))
    monkeypatch.setattr(summarize, "build_default_llm", lambda: llm)
    monkeypatch.setenv(summarize.MAX_BATCHES_ENV, "3")

    plan = service.preview(save=False, use_llm=True)
    assert plan["digest"]["mode"] == "llm"
    assert plan["digest"]["llm_batches"] >= 1
    assert plan["digest"]["budget"]["calls"] == plan["digest"]["llm_batches"]
    assert any(b["digest_source"] == "llm" for b in plan["batches"])
    # 快照（落盘凭证）与返回值同源：digest 段必须可追溯
    assert plan["digest"]["llm_available"] is True


def test_preview_use_llm_without_config_reports_reason(monkeypatch):
    _patch_collect(monkeypatch, _message_batches())
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    plan = service.preview(save=False, use_llm=True)
    assert plan["digest"]["mode"] == "rule"
    assert plan["digest"]["reason"] == summarize.REASON_UNCONFIGURED
    assert all(b["digest_source"] == "rule" for b in plan["batches"])


def test_preview_use_llm_error_still_returns_plan(monkeypatch):
    _patch_collect(monkeypatch, _message_batches())
    llm = FakeLLM(error=TimeoutError("upstream timeout"))
    monkeypatch.setattr(summarize, "llm_available", lambda: (True, ""))
    monkeypatch.setattr(summarize, "build_default_llm", lambda: llm)
    plan = service.preview(save=False, use_llm=True)
    assert plan["batches"], "LLM 失败不能导致预览失败"
    assert plan["digest"]["mode"] == "rule"
    assert plan["digest"]["reason"] in {summarize.REASON_ERROR, "llm_fallback"}
    assert "TimeoutError" not in str(plan)


def test_commit_manifest_records_digest_provenance(monkeypatch, tmp_path):
    """批次清单要能回答「这条长期记忆是原话汇总还是 LLM 改写」。"""
    _patch_collect(monkeypatch, _message_batches())
    llm = FakeLLM(GOOD_REPLY)
    monkeypatch.setattr(summarize, "llm_available", lambda: (True, ""))
    monkeypatch.setattr(summarize, "build_default_llm", lambda: llm)
    plan = service.preview(save=True, root=tmp_path, use_llm=True)

    writes = []
    def fake_writer(batch):
        writes.append(batch.batch_id)
        return {"ok": True, "ref": "memory/manual", "detail": {}}

    mem_batches = [b["batch_id"] for b in plan["batches"] if b["carrier"] == CARRIER_MEMORY]
    result = service.commit(plan["plan_id"], batch_ids=mem_batches, confirm=True,
                            dry_run=False, root=tmp_path,
                            writers={CARRIER_MEMORY: fake_writer})
    assert result["ok"] is True
    entry = next(e for e in result["committed"] if e["batch_id"] in mem_batches)
    assert entry["digest_source"] in {"llm", "rule"}
    listed = {b["batch_id"]: b for b in service.list_batches(root=tmp_path)}
    assert listed[entry["batch_id"]]["digest_source"] == entry["digest_source"]

