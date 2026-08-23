"""评估集回归基线建立 CLI（复查补充 · P0-2）— 单元测试

覆盖：
  - 无已发布技能时 dry-run 返回 count=0（当前仓库 data/skills_mgmt.json 为空）
  - _load_skills 过滤逻辑（只取 PUBLISHED/APPROVED 且 enabled；--skill 限定）
  - --apply 路径在技能存在时调用 RegressionGate.evaluate(record_baseline=True)
    （用假技能 + 注入式门禁验证调用契约，不执行真实评估）

运行：python -m pytest tests/unit/test_establish_baselines.py -q
"""
import json

import pytest

sys_path = __import__("sys")
sys_path.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent.parent))

import scripts.establish_regression_baselines as bl  # noqa: E402


class _FakeSkill:
    def __init__(self, skill_id, status, enabled=True, name="s", version="1.0.0"):
        self.id = skill_id
        self.name = name
        self.status = status
        self.enabled = enabled
        self.version = version


def test_load_skills_filters_published(monkeypatch):
    class _FakeStore:
        def list_all(self):
            from agent.skills_mgmt.models import SkillStatus
            return [
                _FakeSkill("pub-1", SkillStatus.PUBLISHED),
                _FakeSkill("appr-1", SkillStatus.APPROVED),
                _FakeSkill("draft-1", SkillStatus.DRAFT),
                _FakeSkill("arch-1", SkillStatus.ARCHIVED),
                _FakeSkill("disabled-1", SkillStatus.PUBLISHED, enabled=False),
            ]

    monkeypatch.setattr("agent.skills_mgmt.store.SkillStore", _FakeStore)
    skills = bl._load_skills()
    ids = sorted(s.id for s in skills)
    assert ids == ["appr-1", "pub-1"]  # 排除 DRAFT/ARCHIVED/disabled


def test_load_skills_skill_filter(monkeypatch):
    class _FakeStore:
        def list_all(self):
            from agent.skills_mgmt.models import SkillStatus
            return [_FakeSkill("pub-1", SkillStatus.PUBLISHED),
                    _FakeSkill("pub-2", SkillStatus.PUBLISHED)]

    monkeypatch.setattr("agent.skills_mgmt.store.SkillStore", _FakeStore)
    assert [s.id for s in bl._load_skills(["pub-2"])] == ["pub-2"]


def test_dry_run_empty_repo(monkeypatch):
    """当前仓库无已发布技能 → dry-run count=0（真实场景）"""
    class _FakeStore:
        def list_all(self):
            return []

    monkeypatch.setattr("agent.skills_mgmt.store.SkillStore", _FakeStore)
    report = bl._dry_run_report([])
    assert report["mode"] == "dry_run" and report["count"] == 0


def test_apply_records_baseline(monkeypatch):
    """--apply：对每个候选调用 RegressionGate.evaluate(record_baseline=True)"""
    class _FakeGate:
        def __init__(self):
            self.calls = []

        def _build_evaluator(self, skill):
            return _FakeEvaluator()

        def evaluate(self, skill, *, record_baseline=True, **kw):
            self.calls.append((skill.id, record_baseline))
            return _Result("pass", 0.95, 12, 215)

        def has_baseline(self, skill_id):
            return False

        def baseline_score(self, skill_id):
            return 0.95

    class _FakeEvaluator:
        def resolve_category(self, skill):
            return "search"

    class _Result:
        def __init__(self, status, score, sample_count, used_tokens):
            self.status = status
            self.score = score
            self.sample_count = sample_count
            self.used_tokens = used_tokens
            self.notes = []

    gate = _FakeGate()
    monkeypatch.setattr("agent.skills_mgmt.eval_regression.RegressionGate",
                        lambda: gate)
    candidates = [_FakeSkill("pub-1", "PUBLISHED")]
    report = bl._apply_baselines(candidates, None)
    assert gate.calls == [("pub-1", True)]  # record_baseline=True 契约
    assert report["established"] == 1
    assert report["rows"][0]["status"] == "pass"
    assert report["rows"][0]["baseline"] == 0.95


def test_apply_skill_error_skips_not_abort(monkeypatch):
    class _FakeGate:
        def _build_evaluator(self, skill):
            raise RuntimeError("no evaluator")

        def evaluate(self, skill, **kw):
            raise AssertionError("不应执行评估")

        def has_baseline(self, skill_id):
            return False

        def baseline_score(self, skill_id):
            return None

    monkeypatch.setattr("agent.skills_mgmt.eval_regression.RegressionGate",
                        lambda: _FakeGate())
    report = bl._apply_baselines([_FakeSkill("pub-1", "PUBLISHED")], None)
    assert report["rows"][0]["status"] == "error"
    assert report["established"] == 0
    assert "跳过" in str(report["rows"][0].get("error")) or report["rows"][0].get("error")
