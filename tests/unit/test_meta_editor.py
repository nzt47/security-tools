"""元智能体受控编辑器单元测试（任务 EVO-T5：meta_editor.py）

覆盖验收条件:
    1. 越界编辑被拦截（propose 丢弃白名单外/策略文件路径；merge 写盘前二次校验拦截）；
    2. 含 critical 级安全问题的提案被 Review 直接拒绝，不进入评估；
    3. 提案默认不合并：submit 后仅 pending_review，无显式审批无法 merge；
    4. 合并后可从 git 回滚（读取父提交内容恢复）；
    5. 轮次/预算限制生效（技能数上限 / token 预算熔断 / 连续无提升暂停）；
    6. 谱系完整记录：提案 → 审核 → 评估 → 审批 → 合并/拒绝全链路可追溯。

测试策略（隔离性）:
    - 用 Fake 注入 proposal_generator / reviewer / evaluator，不依赖真实 LLM 与沙盒；
    - archive 用真实 EvolutionArchive（临时 JSONL），git 用真实 GitSync（临时仓库），
      验证谱系落库与 git 合并/回滚的真实契约。
"""
import json
import types
from pathlib import Path

import pytest

from agent.skills_mgmt.edit_policy import (
    EditFile,
    EditPolicy,
    EditPolicyError,
    EditProposal,
    EditStatus,
    EditStatusTransitionError,
    PathNotAllowedError,
)
from agent.skills_mgmt.file_store import SkillFileStore
from agent.skills_mgmt.git_sync import GitSync
from agent.skills_mgmt.lineage import EvolutionArchive, EvolutionRecord
from agent.skills_mgmt.meta_editor import MetaEditError, MetaEditGenerator, MetaEditor

SKILL_ID = "my-skill"
SKILL_B = "skill-b"

OLD_SKILL_MD = """---
id: my-skill
name: My Skill
description: A test skill for meta editor
version: 1.0.0
tags:
  - calc
default_params:
  precision: 2
---

# My Skill

Old body content for testing.
"""

NEW_SKILL_MD = OLD_SKILL_MD.replace("Old body content", "Improved body content")


# ════════════════════════════════════════════════════════════
#  Fakes（隔离真实 LLM / 沙盒 / 审核器）
# ════════════════════════════════════════════════════════════

class FakeGenerator:
    """返回固定提案结果的生成器（记录调用参数）"""

    def __init__(self, result):
        self.result = result
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


class _Status:
    def __init__(self, value):
        self.value = value


class FakeReviewResult:
    """模拟 reviewer.ReviewResult：status 带 .value，findings 为普通对象"""

    def __init__(self, status="passed", findings=None, summary="审核通过",
                 score=80.0):
        self.status = _Status(status)
        self.findings = findings or []
        self.summary = summary
        self.score = score
        self.duplicate_score = 0.0
        self.security_score = 80.0
        self.quality_score = 80.0


def finding(severity, code="SEC_TEST", message="test finding"):
    return types.SimpleNamespace(
        severity=severity, category="security", code=code,
        message=message, location=None)


class FakeReviewer:
    def __init__(self, result):
        self.result = result
        self.subjects = []

    def review(self, skill, others=None):
        self.subjects.append(skill)
        return self.result


class FakeEvalResult:
    def __init__(self, score=0.8, status="completed", cost_tokens=10):
        self.score = score
        self.status = status
        self.success_rate = 0.8
        self.latency_ms = 5.0
        self.satisfaction = 0.7
        self.cost_tokens = cost_tokens
        self.sample_count = 2
        self.evaluator_version = "1.0"

    def to_eval_result_dict(self):
        return {
            "score": self.score,
            "dimensions": {"success_rate": self.success_rate},
            "sample_count": self.sample_count,
            "evaluator_version": self.evaluator_version,
        }


class FakeEvaluator:
    def __init__(self, score=0.8, status="completed", cost_tokens=10):
        self.calls = []
        self.score = score
        self.status = status
        self.cost_tokens = cost_tokens

    def evaluate(self, skill, params=None):
        self.calls.append((skill, params))
        return FakeEvalResult(self.score, self.status, self.cost_tokens)


class FakeLLM:
    def __init__(self, text):
        self.text = text
        self.calls = []

    def chat(self, prompt, **kwargs):
        self.calls.append(prompt)
        return self.text


class _FakeGuardResult:
    def __init__(self, severity, findings=None):
        self.severity = severity
        self.findings = findings or []


class FakeGuard:
    def __init__(self, severity="ok"):
        self.severity = severity
        self.calls = []

    def validate_llm_output(self, text, **kwargs):
        self.calls.append(text)
        return _FakeGuardResult(self.severity)


def happy_result(skill_id=SKILL_ID, *, edit_type="content",
                 new_content=NEW_SKILL_MD):
    """构造生成器返回的合法提案 dict"""
    return {
        "edit_type": edit_type,
        "files": [{
            "file_path": f"data/skills_repo/{skill_id}/skill.md",
            "new_content": new_content,
        }],
        "change_summary": "改进正文",
        "expected_gain": "更清晰",
    }


# ════════════════════════════════════════════════════════════
#  Fixtures 与构造辅助
# ════════════════════════════════════════════════════════════

@pytest.fixture
def skills_root(tmp_path):
    d = tmp_path / "data" / "skills_repo"
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_skill(skills_root, skill_id, content=OLD_SKILL_MD):
    d = skills_root / skill_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "skill.md").write_text(content, encoding="utf-8")
    return d


@pytest.fixture
def deps(tmp_path):
    """编辑器依赖集：临时白名单策略 + 临时文件仓库 + 临时档案库"""
    return {
        "policy": EditPolicy(whitelist_dirs=["data/skills_repo"],
                             project_root=tmp_path),
        "file_store": SkillFileStore(
            repo_path=str(tmp_path / "data" / "skills_repo")),
        "archive": EvolutionArchive(
            active_path=str(tmp_path / "evolution_archive.jsonl"),
            archive_path=str(tmp_path / "evolution_archive_old.jsonl"),
            active_generations=10,
        ),
    }


def make_editor(deps, *, generator=None, reviewer=None, evaluator=None,
                git=None, **kw):
    """构造注入假依赖的 MetaEditor（避免触碰真实仓库/LLM）"""
    return MetaEditor(
        policy=deps["policy"],
        file_store=deps["file_store"],
        archive=deps["archive"],
        evaluator=evaluator,
        reviewer=reviewer,
        git=git,
        proposal_generator=generator,
        **kw)


@pytest.fixture
def git_env(tmp_path, monkeypatch):
    """临时 git 仓库（tmp_path 根），带用户身份避免 commit 失败"""
    monkeypatch.setenv("GIT_AUTHOR_NAME", "TestUser")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "test@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "TestUser")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "test@example.com")
    g = GitSync(tmp_path)
    g.init_repo()
    return g


def run_to_pending(editor, skill_id):
    """完整前置链路：propose → 审核通过 → 评估达标 → submit（pending_review）

    editor 需已注入 FakeGenerator + FakeReviewer(passed) + FakeEvaluator(达标)。
    """
    editor.start_round()
    p = editor.propose(skill_id)
    assert p is not None
    assert p.status == "draft"
    editor.review_proposal(p)
    assert p.status == "draft"          # 审核通过仍为 draft
    editor.evaluate_proposal(p)
    assert p.eval_result is not None
    editor.submit_proposal(p)
    assert p.status == "pending_review"
    return p


def decisions_of(archive, skill_id):
    return [r.decision for r in archive.list_by_object(skill_id)]


# ════════════════════════════════════════════════════════════
#  提案生成器（output_guard / 诚实降级）
# ════════════════════════════════════════════════════════════

class TestMetaEditGenerator:
    def test_no_llm_returns_none(self):
        gen = MetaEditGenerator(llm_client=None)
        assert gen.generate(skill_id="x", current_content="y") is None

    def test_llm_failure_returns_none(self):
        class _Boom:
            def chat(self, prompt, **kw):
                raise RuntimeError("llm down")

        gen = MetaEditGenerator(llm_client=_Boom())
        assert gen.generate(skill_id="x", current_content="y") is None

    def test_guard_critical_discards(self):
        gen = MetaEditGenerator(
            llm_client=FakeLLM('{"files": [{"file_path": "a.md", "new_content": "b"}]}'),
            output_guard=FakeGuard(severity="critical"))
        assert gen.generate(skill_id="x", current_content="y") is None

    def test_invalid_json_discards(self):
        gen = MetaEditGenerator(llm_client=FakeLLM("not json"))
        assert gen.generate(skill_id="x", current_content="y") is None

    def test_missing_files_discards(self):
        gen = MetaEditGenerator(llm_client=FakeLLM('{"edit_type": "content"}'))
        assert gen.generate(skill_id="x", current_content="y") is None

    def test_valid_json_parsed(self):
        llm = FakeLLM(json.dumps(happy_result()))
        gen = MetaEditGenerator(llm_client=llm)
        out = gen.generate(skill_id="x", current_content="y")
        assert out["edit_type"] == "content"
        assert out["files"][0]["file_path"].endswith("skill.md")


# ════════════════════════════════════════════════════════════
#  验收 1 + 5：propose 护栏（越界 / 轮次 / 预算 / 暂停）
# ════════════════════════════════════════════════════════════

class TestProposeGuards:
    def test_out_of_whitelist_dropped(self, deps, skills_root):
        # 构造尝试写 agent/ 核心目录的提案 → propose 丢弃（越界拦截）
        write_skill(skills_root, SKILL_ID)
        gen = FakeGenerator({
            "edit_type": "content",
            "files": [{"file_path": "agent/system_tools.py",
                       "new_content": "x"}],
            "change_summary": "s", "expected_gain": "g"})
        editor = make_editor(deps, generator=gen)
        assert editor.propose(SKILL_ID) is None

    def test_strategy_file_dropped(self, deps, skills_root):
        # 白名单内策略文件同样拦截（拒绝递归自修改，验收 7 在编辑链路内生效）
        write_skill(skills_root, SKILL_ID)
        gen = FakeGenerator({
            "edit_type": "content",
            "files": [{"file_path": "data/skills_repo/edit_policy.py",
                       "new_content": "x"}],
            "change_summary": "s", "expected_gain": "g"})
        editor = make_editor(deps, generator=gen)
        assert editor.propose(SKILL_ID) is None

    def test_new_file_not_allowed(self, deps, skills_root):
        # 白名单内但不存在的文件 → 丢弃（元智能体不允许凭空造文件）
        write_skill(skills_root, SKILL_ID)
        gen = FakeGenerator({
            "edit_type": "content",
            "files": [{"file_path": "data/skills_repo/ghost/skill.md",
                       "new_content": "x"}],
            "change_summary": "s", "expected_gain": "g"})
        editor = make_editor(deps, generator=gen)
        assert editor.propose(SKILL_ID) is None

    def test_generator_none_returns_none(self, deps, skills_root):
        write_skill(skills_root, SKILL_ID)
        editor = make_editor(deps, generator=FakeGenerator(None))
        assert editor.propose(SKILL_ID) is None

    def test_propose_without_llm_or_generator_returns_none(self, deps, skills_root):
        # 无 LLM 也无生成器 → 诚实降级不产提案
        write_skill(skills_root, SKILL_ID)
        editor = make_editor(deps)
        assert editor.propose(SKILL_ID) is None

    def test_skills_per_round_limit(self, deps, skills_root):
        # 每轮仅允许编辑 N=1 个技能；第 2 个（含重复）不再产生提案
        write_skill(skills_root, SKILL_ID)
        write_skill(skills_root, SKILL_B)
        editor = make_editor(deps, generator=FakeGenerator(happy_result()),
                             max_skills_per_round=1)
        editor.start_round()
        assert editor.propose(SKILL_ID) is not None
        assert editor.propose(SKILL_ID) is None       # 同技能去重
        assert editor.propose(SKILL_B) is None        # 技能数达上限

    def test_start_round_resets_state(self, deps, skills_root):
        write_skill(skills_root, SKILL_ID)
        editor = make_editor(deps, generator=FakeGenerator(happy_result()),
                             max_skills_per_round=1)
        editor.start_round()
        assert editor.propose(SKILL_ID) is not None
        assert editor.propose(SKILL_ID) is None       # 本轮已编辑
        editor.start_round()
        assert editor.propose(SKILL_ID) is not None   # 新轮重置

    def test_token_budget_breaker(self, deps, skills_root):
        # 单轮 token 预算熔断：累计超预算后停止产生新提案
        write_skill(skills_root, SKILL_ID)
        write_skill(skills_root, SKILL_B)
        editor = make_editor(deps, generator=FakeGenerator(happy_result()),
                             max_skills_per_round=2,
                             max_tokens_per_round=150,
                             token_estimator=lambda p: 100)
        editor.start_round()
        assert editor.propose(SKILL_ID) is not None
        assert editor._round_tokens == 100
        assert editor.propose(SKILL_B) is None        # 100+100 > 150 熔断
        assert editor._round_tokens == 100

    def test_stalled_skill_paused(self, deps, skills_root):
        # 连续 K=3 轮无提升 → 暂停该技能进化
        write_skill(skills_root, SKILL_ID)
        for i, s in enumerate([0.6, 0.5, 0.4, 0.4]):
            deps["archive"].append(EvolutionRecord(
                object_type="tool_code", object_id=SKILL_ID,
                decision="pending_review",
                eval_result={"score": s},
                created_at=f"2026-08-14T10:00:0{i}",
            ))
        editor = make_editor(deps, generator=FakeGenerator(happy_result()))
        assert editor.is_stalled(SKILL_ID) is True
        assert editor.propose(SKILL_ID) is None

    def test_not_stalled_with_gain(self, deps, skills_root):
        # 最近一代有提升 → 不暂停
        write_skill(skills_root, SKILL_ID)
        for i, s in enumerate([0.5, 0.4, 0.6]):
            deps["archive"].append(EvolutionRecord(
                object_type="tool_code", object_id=SKILL_ID,
                decision="pending_review",
                eval_result={"score": s},
                created_at=f"2026-08-14T10:00:0{i}",
            ))
        editor = make_editor(deps, generator=FakeGenerator(happy_result()))
        assert editor.is_stalled(SKILL_ID) is False
        assert editor.propose(SKILL_ID) is not None


# ════════════════════════════════════════════════════════════
#  验收 2：Reviewer 三重审核（critical 直接拒绝，不进入评估）
# ════════════════════════════════════════════════════════════

class TestReviewRejection:
    def test_critical_finding_rejected_before_eval(self, deps, skills_root):
        write_skill(skills_root, SKILL_ID)
        reviewer = FakeReviewer(FakeReviewResult(
            status="passed",
            findings=[finding("critical", message="危险命令")],
            summary="含 critical 安全问题"))
        evaluator = FakeEvaluator()
        editor = make_editor(deps, generator=FakeGenerator(happy_result()),
                             reviewer=reviewer, evaluator=evaluator)
        editor.start_round()
        p = editor.propose(SKILL_ID)
        editor.review_proposal(p)
        assert p.status == "rejected"
        assert p.review["findings"][0]["severity"] == "critical"
        assert evaluator.calls == []                  # 不进入评估
        with pytest.raises(EditStatusTransitionError):
            editor.evaluate_proposal(p)               # rejected 不可再评估
        assert decisions_of(deps["archive"], SKILL_ID) == ["rejected"]
        recs = deps["archive"].list_by_object(SKILL_ID)
        assert "proposal_id" in recs[0].params
        assert recs[0].params["proposal_id"] == p.proposal_id

    def test_failed_status_rejected(self, deps, skills_root):
        write_skill(skills_root, SKILL_ID)
        reviewer = FakeReviewer(FakeReviewResult(
            status="failed",
            findings=[finding("error")],
            summary="质量分过低"))
        evaluator = FakeEvaluator()
        editor = make_editor(deps, generator=FakeGenerator(happy_result()),
                             reviewer=reviewer, evaluator=evaluator)
        editor.start_round()
        p = editor.propose(SKILL_ID)
        editor.review_proposal(p)
        assert p.status == "rejected"
        assert evaluator.calls == []

    def test_passed_keeps_draft(self, deps, skills_root):
        write_skill(skills_root, SKILL_ID)
        editor = make_editor(deps, generator=FakeGenerator(happy_result()),
                             reviewer=FakeReviewer(FakeReviewResult(status="passed")))
        editor.start_round()
        p = editor.propose(SKILL_ID)
        editor.review_proposal(p)
        assert p.status == "draft"
        assert p.review["status"] == "passed"

    def test_review_requires_draft(self, deps):
        p = EditProposal(object_id=SKILL_ID,
                         files=[EditFile("data/skills_repo/a/skill.md", "a", "b")])
        p.submit()
        editor = make_editor(deps)
        with pytest.raises(EditStatusTransitionError):
            editor.review_proposal(p)


# ════════════════════════════════════════════════════════════
#  验收 3：提交待审批（无自动合并）+ 提交前置校验
# ════════════════════════════════════════════════════════════

class TestSubmitNoAutoMerge:
    def test_submit_requires_review(self, deps):
        p = EditProposal(object_id=SKILL_ID,
                         files=[EditFile("data/skills_repo/a/skill.md", "a", "b")])
        editor = make_editor(deps)
        with pytest.raises(EditPolicyError):
            editor.submit_proposal(p)

    def test_submit_requires_eval(self, deps):
        p = EditProposal(object_id=SKILL_ID,
                         files=[EditFile("data/skills_repo/a/skill.md", "a", "b")])
        p.review = {"status": "passed", "findings": []}
        editor = make_editor(deps)
        with pytest.raises(EditPolicyError):
            editor.submit_proposal(p)

    def test_submit_low_score_rejected(self, deps):
        p = EditProposal(object_id=SKILL_ID,
                         files=[EditFile("data/skills_repo/a/skill.md", "a", "b")])
        p.review = {"status": "passed", "findings": []}
        p.eval_result = {"score": 0.1, "status": "completed"}
        editor = make_editor(deps)
        with pytest.raises(EditPolicyError):
            editor.submit_proposal(p)

    def test_submit_sets_pending_review_only(self, deps, skills_root):
        # 提交后仅 pending_review + 谱系落库，绝不自动合并（验收 3）
        write_skill(skills_root, SKILL_ID)
        editor = make_editor(deps, generator=FakeGenerator(happy_result()),
                             reviewer=FakeReviewer(FakeReviewResult(status="passed")),
                             evaluator=FakeEvaluator(score=0.8))
        p = run_to_pending(editor, SKILL_ID)
        assert p.status == "pending_review"
        assert p.lineage_record_id
        assert decisions_of(deps["archive"], SKILL_ID) == ["pending_review"]
        # 无显式审批 → 合并被状态机拒绝
        with pytest.raises(EditStatusTransitionError):
            editor.merge_proposal(p)

    def test_human_reject_path(self, deps, skills_root):
        write_skill(skills_root, SKILL_ID)
        editor = make_editor(deps, generator=FakeGenerator(happy_result()),
                             reviewer=FakeReviewer(FakeReviewResult(status="passed")),
                             evaluator=FakeEvaluator(score=0.8))
        p = run_to_pending(editor, SKILL_ID)
        editor.reject_proposal(p, reason="不需要", actor="tester")
        assert p.status == "rejected"
        recs = deps["archive"].list_by_object(SKILL_ID)
        assert [r.decision for r in recs] == ["pending_review", "rejected"]
        assert recs[1].parent_record_id == recs[0].record_id

    def test_approve_requires_pending_review(self, deps):
        p = EditProposal(object_id=SKILL_ID,
                         files=[EditFile("data/skills_repo/a/skill.md", "a", "b")])
        editor = make_editor(deps)
        with pytest.raises(EditStatusTransitionError):
            editor.approve_proposal(p)                # draft 不可审批


# ════════════════════════════════════════════════════════════
#  验收 1 + 4 + 6：合并写盘 / 二次越界拦截 / git 回滚 / 谱系全链路
# ════════════════════════════════════════════════════════════

class TestMergeAndRollback:
    def test_merge_applies_and_rollback_restores(self, deps, skills_root, git_env):
        write_skill(skills_root, SKILL_ID, OLD_SKILL_MD)
        rel = f"data/skills_repo/{SKILL_ID}/skill.md"
        git_env.add([rel])
        git_env.commit("initial")                     # 旧内容入库，回滚有父提交可读
        editor = make_editor(deps, generator=FakeGenerator(happy_result()),
                             reviewer=FakeReviewer(FakeReviewResult(status="passed")),
                             evaluator=FakeEvaluator(score=0.8),
                             git=git_env)
        p = run_to_pending(editor, SKILL_ID)
        editor.approve_proposal(p)
        assert p.status == "approved"
        assert p.is_mergeable is True

        editor.merge_proposal(p)
        assert p.status == "merged"
        assert p.merge_commit_sha
        md = skills_root / SKILL_ID / "skill.md"
        assert md.read_text(encoding="utf-8") == NEW_SKILL_MD

        editor.rollback(p)
        assert p.status == "archived"
        assert md.read_text(encoding="utf-8") == OLD_SKILL_MD

        # 验收 6：谱系全链路可追溯（pending_review → committed → rolled_back）
        recs = deps["archive"].list_by_object(SKILL_ID)
        assert decisions_of(deps["archive"], SKILL_ID) == [
            "pending_review", "committed", "rolled_back"]
        assert recs[0].params["proposal_id"] == p.proposal_id
        assert recs[1].parent_record_id == recs[0].record_id
        assert recs[2].parent_record_id == recs[1].record_id
        # 谱系快照留痕：change_summary / 合并 SHA / 回滚原因均可回溯
        assert recs[1].change_summary == p.change_summary
        assert p.merge_commit_sha in recs[1].decision_reason
        assert "git 回滚" in recs[2].decision_reason

    def test_rollback_restores_git_parent_not_proposal_snapshot(
            self, deps, skills_root, git_env):
        # 真实场景：回滚恢复源必须是 git 父提交，而不是提案内存中的 old_content 快照。
        # 篡改快照后回滚仍恢复 OLD，即可证明恢复自 git（验收 4 语义）
        write_skill(skills_root, SKILL_ID, OLD_SKILL_MD)
        rel = f"data/skills_repo/{SKILL_ID}/skill.md"
        git_env.add([rel])
        git_env.commit("initial")
        editor = make_editor(deps, generator=FakeGenerator(happy_result()),
                             reviewer=FakeReviewer(FakeReviewResult(status="passed")),
                             evaluator=FakeEvaluator(score=0.8),
                             git=git_env)
        p = run_to_pending(editor, SKILL_ID)
        editor.approve_proposal(p)
        editor.merge_proposal(p)
        p.files[0].old_content = "FABRICATED-SNAPSHOT"   # 提案快照被篡改
        editor.rollback(p)
        md = skills_root / SKILL_ID / "skill.md"
        assert md.read_text(encoding="utf-8") == OLD_SKILL_MD  # 恢复自 git 父提交

    def test_rollback_after_intervening_commit_uses_merge_parent(
            self, deps, skills_root, git_env):
        # 真实场景：merge 后 HEAD 已有其他提交，回滚仍按 merge_sha^ 恢复，
        # 而非 HEAD^（撤销最后一次提交）—— 证明回滚语义锚定在提案合并点
        write_skill(skills_root, SKILL_ID, OLD_SKILL_MD)
        rel = f"data/skills_repo/{SKILL_ID}/skill.md"
        git_env.add([rel])
        git_env.commit("initial")
        editor = make_editor(deps, generator=FakeGenerator(happy_result()),
                             reviewer=FakeReviewer(FakeReviewResult(status="passed")),
                             evaluator=FakeEvaluator(score=0.8),
                             git=git_env)
        p = run_to_pending(editor, SKILL_ID)
        editor.approve_proposal(p)
        editor.merge_proposal(p)
        merge_sha = p.merge_commit_sha
        assert merge_sha

        # 无关提交插在 merge 与 rollback 之间（模拟后续进化持续进行）
        extra_rel = f"data/skills_repo/extra.txt"
        (skills_root / "extra.txt").write_text("keep-me", encoding="utf-8")
        git_env.add([extra_rel])
        git_env.commit("unrelated change after merge")
        assert git_env.log(limit=1)[0].sha != merge_sha   # HEAD 已偏离合并点

        editor.rollback(p)
        md = skills_root / SKILL_ID / "skill.md"
        assert md.read_text(encoding="utf-8") == OLD_SKILL_MD   # merge_sha^ 内容
        assert (skills_root / "extra.txt").read_text(encoding="utf-8") == "keep-me"
        assert git_env.log(limit=1)[0].message == f"rollback {p.proposal_id}"
        assert decisions_of(deps["archive"], SKILL_ID) == [
            "pending_review", "committed", "rolled_back"]

    def test_merge_blocks_out_of_whitelist_at_write_time(self, deps, git_env):
        # 直接构造 approved 提案携带越界路径 → merge 写盘前二次校验拦截（验收 1）
        p = EditProposal(
            object_id=SKILL_ID,
            files=[EditFile("agent/system_tools.py", "old", "new")],
            status=EditStatus.APPROVED)
        editor = make_editor(deps, git=git_env)
        with pytest.raises(PathNotAllowedError):
            editor.merge_proposal(p)

    def test_merge_blocks_strategy_file_at_write_time(self, deps, git_env):
        # 白名单内策略文件在写盘前同样被二次校验拦截（拒绝递归自修改）
        p = EditProposal(
            object_id=SKILL_ID,
            files=[EditFile("data/skills_repo/meta_editor.py", "old", "new")],
            status=EditStatus.APPROVED)
        editor = make_editor(deps, git=git_env)
        with pytest.raises(PathNotAllowedError):
            editor.merge_proposal(p)

    def test_merge_requires_approval(self, deps, skills_root):
        write_skill(skills_root, SKILL_ID)
        editor = make_editor(deps, generator=FakeGenerator(happy_result()),
                             reviewer=FakeReviewer(FakeReviewResult(status="passed")),
                             evaluator=FakeEvaluator(score=0.8))
        p = run_to_pending(editor, SKILL_ID)
        with pytest.raises(EditStatusTransitionError):
            editor.merge_proposal(p)                  # pending_review 无自动合并

    def test_rollback_requires_merged(self, deps, skills_root):
        write_skill(skills_root, SKILL_ID)
        editor = make_editor(deps, generator=FakeGenerator(happy_result()),
                             reviewer=FakeReviewer(FakeReviewResult(status="passed")),
                             evaluator=FakeEvaluator(score=0.8))
        p = run_to_pending(editor, SKILL_ID)
        with pytest.raises(EditStatusTransitionError):
            editor.rollback(p)                        # 未合并不可回滚

    def test_rollback_requires_commit_sha(self, deps, git_env):
        p = EditProposal(object_id=SKILL_ID,
                         files=[EditFile("data/skills_repo/a/skill.md", "a", "b")],
                         status=EditStatus.MERGED)
        editor = make_editor(deps, git=git_env)
        with pytest.raises(MetaEditError):
            editor.rollback(p)                        # 缺 merge_commit_sha


# ════════════════════════════════════════════════════════════
#  编辑类型映射 + PARAMS 评估参数透传
# ════════════════════════════════════════════════════════════

class TestParamsAndTypeMapping:
    def test_documentation_edit_maps_tool_doc(self, deps, skills_root):
        write_skill(skills_root, SKILL_ID)
        editor = make_editor(deps, generator=FakeGenerator(
            happy_result(edit_type="documentation")))
        editor.start_round()
        p = editor.propose(SKILL_ID)
        assert p is not None
        assert p.object_type == "tool_doc"
        assert p.edit_type == "documentation"

    def test_content_edit_maps_tool_code(self, deps, skills_root):
        write_skill(skills_root, SKILL_ID)
        editor = make_editor(deps, generator=FakeGenerator(happy_result()))
        editor.start_round()
        p = editor.propose(SKILL_ID)
        assert p.object_type == "tool_code"

    def test_params_edit_passes_new_params_to_evaluator(self, deps, skills_root):
        # PARAMS 编辑：评估器拿到编辑后 front matter 的 default_params 真实执行
        write_skill(skills_root, SKILL_ID)
        new_md = OLD_SKILL_MD.replace("  precision: 2", "  precision: 4")
        editor = make_editor(deps, generator=FakeGenerator(
            happy_result(edit_type="params", new_content=new_md)),
            reviewer=FakeReviewer(FakeReviewResult(status="passed")),
            evaluator=FakeEvaluator(score=0.8))
        editor.start_round()
        p = editor.propose(SKILL_ID)
        assert p.edit_type == "params"
        editor.review_proposal(p)
        editor.evaluate_proposal(p)
        _candidate, params = editor._evaluator.calls[0]
        assert params["precision"] == 4

    def test_parent_record_id_links_to_latest(self, deps, skills_root):
        # 提案父代 = 该技能最新谱系记录（验收 6 谱系链不断）
        write_skill(skills_root, SKILL_ID)
        deps["archive"].append(EvolutionRecord(
            object_type="tool_code", object_id=SKILL_ID,
            decision="pending_review", eval_result={"score": 0.5},
            created_at="2026-08-14T09:00:00"))
        editor = make_editor(deps, generator=FakeGenerator(happy_result()))
        editor.start_round()
        p = editor.propose(SKILL_ID)
        recs = deps["archive"].list_by_object(SKILL_ID)
        assert p.parent_record_id == recs[-1].record_id
