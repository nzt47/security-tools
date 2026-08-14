"""技能审核器独立单元测试（reviewer.py 三大子审核器边界）

与 tests/unit/test_skills_mgmt.py 的服务级（svc.review）测试互补，
本文件聚焦组件级边界：
    1. DuplicateDetector — 完全相同(hash 命中)/阈值边界/空 others/跳过自身/空内容
    2. SecurityScanner  — 满分/三档扣分/critical 阻断与解除/危险依赖/评分下限
    3. QualityAssessor — 满配满分/空技能低分/代码类 try+raise/非代码保底/标签与版本边界
    4. SkillReviewer   — 通过/各维度失败决策/critical 直接拒绝/阈值注入决策
"""
import pytest

from agent.skills_mgmt.exceptions import SkillSecurityError
from agent.skills_mgmt.models import (
    ContentType,
    ReviewStatus,
    Skill,
    SkillStatus,
)
from agent.skills_mgmt.reviewer import (
    DuplicateDetector,
    QualityAssessor,
    ReviewThresholds,
    SecurityScanner,
    SkillReviewer,
)


# ════════════════════════════════════════════════════════════
#  构造辅助
# ════════════════════════════════════════════════════════════

# 满配 Python 技能正文：≥100 字符、含 try/raise（质量错误处理两档满分）
_PY_FULL = """def run(data):
    try:
        result = process(data)
        return result
    except Exception as exc:
        raise ValueError(f"processing failed: {exc}")
"""


def make_skill(skill_id: str = "demo-skill", **kw) -> Skill:
    """构造技能：默认一份能通过全部审核的健康样例"""
    defaults = dict(
        id=skill_id,
        name="示例技能",
        description="这是一个用于演示的示例技能，描述长度超过二十个字符以确保文档评分达标。",
        content=_PY_FULL,
        content_type=ContentType.PYTHON,
        tags=["demo", "sample"],
        version="1.0.0",
        author="tester",
        config_schema={"type": "object", "properties": {"mode": {"type": "string"}}},
        dependencies=[],
    )
    defaults.update(kw)
    return Skill(**defaults)


# ════════════════════════════════════════════════════════════
#  1. DuplicateDetector
# ════════════════════════════════════════════════════════════

class TestDuplicateDetector:
    def test_identical_content_scores_100(self):
        s = make_skill(content="alpha beta gamma delta")
        other = make_skill("other-skill", content="alpha beta gamma delta")
        score, dup_with = DuplicateDetector().detect(s, [other])
        assert score == 100.0
        assert dup_with == ["other-skill"]

    def test_threshold_boundary_triggered(self):
        # name 相同、内容 4→3 词：Jaccard 0.8 >= 阈值 0.7 → 判定重复
        s = make_skill(skill_id="dup-a", name="x", description="",
                       content="alpha beta gamma delta")
        other = make_skill("dup-b", name="x", description="",
                           content="alpha beta gamma")
        score, dup_with = DuplicateDetector().detect(s, [other])
        assert score == 80.0
        assert dup_with == ["dup-b"]

    def test_below_threshold_reports_zero(self):
        # 内容 4→2 词：Jaccard 0.6 < 阈值 0.7 → 不判重复
        # 【已知行为】detect 只报告"已判重复"的技能，低于阈值一律返回 0
        s = make_skill(skill_id="dup-a", name="x", description="",
                       content="alpha beta gamma delta")
        other = make_skill("dup-b", name="x", description="",
                           content="alpha beta")
        score, dup_with = DuplicateDetector().detect(s, [other])
        assert score == 0.0
        assert dup_with == []

    def test_empty_others_no_duplicate(self):
        score, dup_with = DuplicateDetector().detect(
            make_skill(content="unique content"), [])
        assert score == 0.0
        assert dup_with == []

    def test_skips_self_id(self):
        # others 中出现同 id 技能（内容不同）→ 必须被跳过，不得误报
        s = make_skill(content="unique body one")
        same_id = make_skill(content="unique body two")
        score, dup_with = DuplicateDetector().detect(s, [same_id])
        assert score == 0.0
        assert dup_with == []

    def test_empty_vs_nonempty_not_duplicate(self):
        # 空内容与有内容 → hash 不同且无 token 交集，不判重复
        s = make_skill("empty-a", content="", name="x", description="")
        other = make_skill("full-b", content="alpha beta gamma delta",
                           name="y", description="")
        score, dup_with = DuplicateDetector().detect(s, [other])
        assert score == 0.0
        assert dup_with == []

    def test_empty_vs_empty_guards_known_behavior(self):
        # 【已知边界守卫】两个空内容技能：sha256("") 恒非空使 hash 分支命中
        # → 当前实现判为 100% 重复。若未来修复"空内容不算重复"，需同步更新此断言。
        s = make_skill("empty-a", content="", name="x", description="")
        other = make_skill("empty-b", content="", name="y", description="")
        score, dup_with = DuplicateDetector().detect(s, [other])
        assert score == 100.0
        assert dup_with == ["empty-b"]


# ════════════════════════════════════════════════════════════
#  2. SecurityScanner
# ════════════════════════════════════════════════════════════

class TestSecurityScanner:
    def test_clean_skill_full_score(self):
        score, findings = SecurityScanner().scan(make_skill())
        assert score == 100.0
        assert findings == []

    def test_warn_secret_deducts_5(self):
        s = make_skill(content='api_key = "abcdefgh123"')
        score, findings = SecurityScanner().scan(s)
        assert score == 95.0
        assert any(f.code == "SEC_HARDCODED_SECRET" for f in findings)

    def test_error_eval_deducts_15(self):
        s = make_skill(content="eval(user_input)")
        score, findings = SecurityScanner().scan(s)
        assert score == 85.0
        assert any(f.code == "SEC_EVAL" for f in findings)

    def test_critical_blocks_by_default(self):
        s = make_skill(content="rm -rf /tmp")
        with pytest.raises(SkillSecurityError):
            SecurityScanner().scan(s)

    def test_critical_not_blocking_when_disabled(self):
        s = make_skill(content="rm -rf /tmp")
        scanner = SecurityScanner(block_on_critical=False)
        score, findings = scanner.scan(s)
        assert score == 70.0
        assert any(f.severity == "critical" for f in findings)

    def test_dangerous_dependency_blocks(self):
        s = make_skill(dependencies=["keylogger-pkg"])
        with pytest.raises(SkillSecurityError):
            SecurityScanner().scan(s)

    def test_score_floor_at_zero(self):
        # 7 个 error = 100 - 105 → 下限 0，不为负
        s = make_skill(content="eval(a) eval(b) eval(c) eval(d) eval(e) eval(f) eval(g)")
        score, _ = SecurityScanner().scan(s)
        assert score == 0.0


# ════════════════════════════════════════════════════════════
#  3. QualityAssessor
# ════════════════════════════════════════════════════════════

class TestQualityAssessor:
    def test_full_quality_score_100(self):
        # 文档30 + schema20 + 代码错误处理20 + 标签10 + 版本作者10 + 无依赖10 = 100
        score, findings = QualityAssessor().assess(make_skill())
        assert score == 100.0
        assert findings == []

    def test_empty_skill_low_score_with_findings(self):
        s = make_skill(skill_id="empty-a", name="x", description="", content="",
                       content_type=ContentType.MARKDOWN, tags=[], version="0.0.0",
                       author="unknown", config_schema={})
        score, findings = QualityAssessor().assess(s)
        assert score == 30.0
        codes = {f.code for f in findings}
        assert {"QUAL_SHORT_DESC", "QUAL_THIN_CONTENT",
                "QUAL_NO_SCHEMA", "QUAL_NO_TAGS"} <= codes

    def test_code_missing_try_raise_get_findings(self):
        # 内容 ≥100 字符但无 try/raise → 错误处理两档扣分
        s = make_skill(content_type=ContentType.PYTHON,
                       content="x = 1\n" * 50)
        score, findings = QualityAssessor().assess(s)
        assert score == 80.0  # 100 - 10(try) - 10(raise)
        codes = {f.code for f in findings}
        assert "QUAL_NO_TRY_CATCH" in codes
        assert "QUAL_NO_RAISE" in codes

    def test_non_code_content_gets_doc_floor(self):
        # 非代码内容错误处理直接给 20 分，不产生 try/raise findings
        s = make_skill(content_type=ContentType.MARKDOWN,
                       content="说明文本。\n" * 20)
        score, findings = QualityAssessor().assess(s)
        assert score == 100.0
        assert not any(f.code in ("QUAL_NO_TRY_CATCH", "QUAL_NO_RAISE")
                       for f in findings)

    def test_tags_boundaries(self):
        base = dict(description="d" * 25, content="c" * 120,
                    content_type=ContentType.MARKDOWN,
                    version="1.0.0", author="a")
        s0 = make_skill(skill_id="tags-0", tags=[], **base)
        s1 = make_skill(skill_id="tags-1", tags=["one"], **base)
        s2 = make_skill(skill_id="tags-2", tags=["one", "two"], **base)
        assert QualityAssessor().assess(s0)[0] == 90.0  # 无标签 -10
        assert QualityAssessor().assess(s1)[0] == 95.0  # 单标签 +5
        assert QualityAssessor().assess(s2)[0] == 100.0  # 双标签 +10

    def test_version_author_boundaries(self):
        base = dict(description="d" * 25, content="c" * 120,
                    content_type=ContentType.MARKDOWN, tags=["a", "b"])
        s0 = make_skill(skill_id="va-0", version="0.0.0", author="unknown", **base)
        s1 = make_skill(skill_id="va-1", version="1.2.3", author="tester", **base)
        assert QualityAssessor().assess(s0)[0] == 90.0  # 版本/作者两档各 0
        assert QualityAssessor().assess(s1)[0] == 100.0  # 两档各 +5


# ════════════════════════════════════════════════════════════
#  4. SkillReviewer 门面
# ════════════════════════════════════════════════════════════

class TestSkillReviewer:
    def test_passed_healthy_skill(self):
        skill = make_skill()
        result = SkillReviewer().review(skill)
        assert result.status == ReviewStatus.PASSED
        assert result.score == 100.0
        assert skill.status == SkillStatus.APPROVED

    def test_failed_on_duplicate(self):
        skill = make_skill()
        others = [make_skill("twin-skill", content=skill.content)]
        result = SkillReviewer().review(skill, others=others)
        assert result.status == ReviewStatus.FAILED
        assert any(f.code == "DUPLICATE_HIGH" for f in result.findings)
        assert skill.status == SkillStatus.REJECTED

    def test_failed_on_low_security(self):
        # 3 个 eval(error 级) → sec=55 < 70
        skill = make_skill(content="eval(a) eval(b) eval(c)")
        result = SkillReviewer().review(skill)
        assert result.status == ReviewStatus.FAILED
        assert result.security_score == 55.0
        assert skill.status == SkillStatus.REJECTED

    def test_failed_on_low_quality(self):
        skill = make_skill(skill_id="low-quality", name="x", description="",
                           content="", content_type=ContentType.MARKDOWN,
                           tags=[], version="0.0.0", author="unknown",
                           config_schema={})
        result = SkillReviewer().review(skill)
        assert result.status == ReviewStatus.FAILED
        assert result.quality_score == 30.0
        assert skill.status == SkillStatus.REJECTED

    def test_critical_security_returns_failed_and_rejected(self):
        skill = make_skill(content="rm -rf /")
        result = SkillReviewer().review(skill)
        assert result.status == ReviewStatus.FAILED
        assert result.score == 0.0
        assert result.findings[0].code == "SEC_BLOCKED"
        assert skill.status == SkillStatus.REJECTED

    def test_relaxed_thresholds_pass_even_poor_skill(self):
        # 四维阈值全部放宽 → 无 critical 时任何技能通过（验证决策只依赖阈值）
        skill = make_skill(skill_id="poor-a", name="x", description="",
                           content="", content_type=ContentType.MARKDOWN,
                           tags=[], version="0.0.0", author="unknown",
                           config_schema={})
        reviewer = SkillReviewer(thresholds=ReviewThresholds(
            duplicate_max=200.0, security_min=0.0,
            quality_min=0.0, overall_min=0.0))
        result = reviewer.review(skill)
        assert result.status == ReviewStatus.PASSED
        assert skill.status == SkillStatus.APPROVED

    def test_tight_security_threshold_fails_full_score(self):
        # security_min=101 > 满分 100 → 即使安全满分也判定失败
        reviewer = SkillReviewer(thresholds=ReviewThresholds(
            security_min=101.0))
        result = reviewer.review(make_skill())
        assert result.status == ReviewStatus.FAILED
