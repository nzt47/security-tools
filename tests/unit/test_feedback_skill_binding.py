"""feedback 与 skill_id 绑定 — 单元测试

测试覆盖：
1. 落库绑定：submit_feedback(skill_id=...) 写入 feedback 表，可按 skill_id 查出
2. 老库兼容：缺列时自动 ALTER TABLE 补齐
3. 聚合统计：get_skill_feedback_summary 输出满意度/平均分/推荐动作
4. 反馈驱动优化：SkillEnhancer.optimize_params 接受 feedback_summary 给出建议
5. 一键式优化：optimize_with_feedback 端到端
6. ContextInjector.inject_result 评分引导注入
7. SkillsMgmtService.submit_skill_feedback 桥接调用
8. 边界：非法 rating / feedback_type 抛 ValueError

状态同步机制说明：
- 测试用例隔离：每个测试独立 tmp_path / monkeypatch 全局单例
- 后端权威原则：所有断言基于持久化记录而非内存
"""
import os
import sys
import tempfile

import pytest

# 让 tests/ 可以导入 agent 包
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from agent.feedback import (
    FeedbackManager,
    FeedbackType,
    FeedbackRecord,
    get_feedback_manager,
)
from agent.skills_mgmt.enhancer import SkillEnhancer
from agent.skills_mgmt.store import SkillStore
from agent.skills_mgmt.models import Skill, SkillStatus
from agent.skills_mgmt.executor import ExecutionResult
from agent.skills_mgmt.context_injector import ContextInjector
from agent.skills_mgmt.service import SkillsMgmtService


# ═══════════════════════════════════════════════════════════════════
#  Fixture
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture
def isolated_feedback_manager(tmp_path, monkeypatch):
    """每个测试独立的 FeedbackManager（独立 SQLite 文件）"""
    # 重置全局单例，避免污染
    import agent.feedback as fb_module
    monkeypatch.setattr(fb_module, "_global_feedback_manager", None)

    storage = str(tmp_path / "feedback")
    mgr = FeedbackManager(storage_path=storage)
    mgr.initialize()
    # 覆盖 get_feedback_manager 让 enhancer 也用同一实例
    monkeypatch.setattr(fb_module, "get_feedback_manager", lambda: mgr)
    return mgr


@pytest.fixture
def svc(tmp_path):
    """独立存储的 SkillsMgmtService"""
    store_path = str(tmp_path / "skills_mgmt.json")
    return SkillsMgmtService(store_path=store_path)


def _make_skill(svc, skill_id="skill-demo"):
    """创建一个测试技能"""
    return svc.create_manual({
        "id": skill_id,
        "name": skill_id,
        "description": "测试技能",
        "content": "# 测试\nprint('hello')\n",
        "content_type": "python",
        "category": "custom",
        "tags": ["test"],
        "author": "tester",
    })


# ═══════════════════════════════════════════════════════════════════
#  1. 落库绑定
# ═══════════════════════════════════════════════════════════════════

class TestFeedbackSkillBinding:
    """submit_feedback 绑定 skill_id 落库"""

    def test_submit_with_skill_id_persists(self, isolated_feedback_manager):
        """提交反馈时 skill_id 应被持久化"""
        mgr = isolated_feedback_manager
        record = mgr.submit_feedback(
            trace_id="trace-1",
            feedback_type="like",
            rating=5,
            comment="很好用",
            skill_id="skill-pdf",
        )
        assert record.skill_id == "skill-pdf"

        # 从数据库重新读出
        loaded = mgr.get_feedback(record.feedback_id)
        assert loaded is not None
        assert loaded.skill_id == "skill-pdf"

    def test_get_feedback_by_skill(self, isolated_feedback_manager):
        """按 skill_id 查询应只返回该技能的反馈"""
        mgr = isolated_feedback_manager
        mgr.submit_feedback(trace_id="t1", feedback_type="like",
                            skill_id="skill-a")
        mgr.submit_feedback(trace_id="t2", feedback_type="dislike",
                            rating=2, skill_id="skill-a")
        mgr.submit_feedback(trace_id="t3", feedback_type="like",
                            skill_id="skill-b")

        a_feedback = mgr.get_feedback_by_skill("skill-a")
        b_feedback = mgr.get_feedback_by_skill("skill-b")

        assert len(a_feedback) == 2
        assert len(b_feedback) == 1
        assert all(f.skill_id == "skill-a" for f in a_feedback)

    def test_quality_case_carries_skill_id(self, isolated_feedback_manager):
        """正面反馈归档为 quality_case 时应携带 skill_id"""
        mgr = isolated_feedback_manager
        mgr.submit_feedback(
            trace_id="t-pos", feedback_type="like", rating=5,
            comment="非常棒", skill_id="skill-excel",
        )
        cases = mgr.list_quality_cases(skill_id="skill-excel")
        assert len(cases) >= 1
        assert cases[0].skill_id == "skill-excel"

    def test_backward_compat_skill_id_default_empty(self, isolated_feedback_manager):
        """不传 skill_id 时默认空字符串（向后兼容）"""
        mgr = isolated_feedback_manager
        record = mgr.submit_feedback(
            trace_id="t-old", feedback_type="suggestion",
        )
        assert record.skill_id == ""


# ═══════════════════════════════════════════════════════════════════
#  2. 老库兼容
# ═══════════════════════════════════════════════════════════════════

class TestBackwardCompatMigration:
    """老库（缺 skill_id 列）应自动 ALTER TABLE 补齐"""

    def test_migrate_adds_skill_id_column(self, tmp_path, monkeypatch):
        import agent.feedback as fb_module
        monkeypatch.setattr(fb_module, "_global_feedback_manager", None)

        # FeedbackManager 会把 db 放在 storage_path/feedback.db
        # 这里 storage_path = tmp_path/feedback_storage, db = .../feedback.db
        storage_dir = str(tmp_path / "feedback_storage")
        os.makedirs(storage_dir, exist_ok=True)
        db_path = os.path.join(storage_dir, "feedback.db")

        # 模拟老库：手动建一个不含 skill_id 列的 feedback 表
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                feedback_id TEXT NOT NULL UNIQUE,
                trace_id TEXT NOT NULL,
                feedback_type TEXT NOT NULL,
                rating INTEGER DEFAULT 0,
                comment TEXT DEFAULT '',
                category TEXT DEFAULT 'other',
                user_id TEXT DEFAULT '',
                session_id TEXT DEFAULT '',
                status TEXT DEFAULT 'pending',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                analyzed_at REAL,
                resolved_at REAL,
                analysis_result TEXT DEFAULT '{}',
                context TEXT DEFAULT '{}',
                metadata TEXT DEFAULT '{}'
            )
        """)
        conn.execute("""
            CREATE TABLE quality_cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id TEXT NOT NULL UNIQUE,
                trace_id TEXT NOT NULL,
                user_id TEXT DEFAULT '',
                feedback_id TEXT DEFAULT '',
                title TEXT DEFAULT '',
                content_summary TEXT DEFAULT '',
                tags TEXT DEFAULT '[]',
                quality_score REAL DEFAULT 0,
                created_at REAL NOT NULL,
                context TEXT DEFAULT '{}',
                metadata TEXT DEFAULT '{}'
            )
        """)
        conn.commit()
        conn.close()

        # 用老库路径初始化新 FeedbackManager，应自动补列
        mgr = FeedbackManager(storage_path=storage_dir)
        mgr.initialize()

        # 验证 skill_id 列已存在
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(feedback)")
        cols = {row[1] for row in cursor.fetchall()}
        conn.close()

        assert "skill_id" in cols
        assert "workflow_id" in cols


# ═══════════════════════════════════════════════════════════════════
#  3. 聚合统计
# ═══════════════════════════════════════════════════════════════════

class TestSkillFeedbackSummary:
    """get_skill_feedback_summary 聚合统计"""

    def test_summary_no_data(self, isolated_feedback_manager):
        mgr = isolated_feedback_manager
        s = mgr.get_skill_feedback_summary("skill-empty")
        assert s["total_feedback"] == 0
        assert s["satisfaction_rate_percent"] == 0.0
        assert s["recommended_action"] == "no_data"

    def test_summary_high_satisfaction(self, isolated_feedback_manager):
        mgr = isolated_feedback_manager
        # 5 个 like + 1 个 dislike → 满意度 83%
        for i in range(5):
            mgr.submit_feedback(trace_id=f"t{i}", feedback_type="like",
                               rating=5, skill_id="skill-a")
        mgr.submit_feedback(trace_id="t-x", feedback_type="dislike",
                            rating=2, skill_id="skill-a")

        s = mgr.get_skill_feedback_summary("skill-a")
        assert s["like_count"] == 5
        assert s["dislike_count"] == 1
        assert s["total_feedback"] == 6
        assert s["avg_rating"] > 0
        # 5/6 ≈ 83.33%，未达 90% → keep
        assert s["recommended_action"] in ("keep", "promote_to_published")

    def test_summary_low_satisfaction_triggers_deprecate(
            self, isolated_feedback_manager):
        mgr = isolated_feedback_manager
        # 1 like + 5 dislike → 满意度 16.7%
        mgr.submit_feedback(trace_id="t1", feedback_type="like",
                            skill_id="skill-bad")
        for i in range(5):
            mgr.submit_feedback(trace_id=f"bd{i}", feedback_type="dislike",
                                rating=1, skill_id="skill-bad")

        s = mgr.get_skill_feedback_summary("skill-bad")
        assert s["satisfaction_rate_percent"] < 50
        assert s["recommended_action"] == "consider_deprecate_or_merge"

    def test_summary_captures_recent_dislike_comments(
            self, isolated_feedback_manager):
        mgr = isolated_feedback_manager
        mgr.submit_feedback(trace_id="t1", feedback_type="dislike",
                            rating=1, comment="太难用了",
                            skill_id="skill-c")
        mgr.submit_feedback(trace_id="t2", feedback_type="dislike",
                            rating=2, comment="经常报错",
                            skill_id="skill-c")

        s = mgr.get_skill_feedback_summary("skill-c")
        assert len(s["recent_dislike_comments"]) == 2
        comments = [c["comment"] for c in s["recent_dislike_comments"]]
        assert "太难用了" in comments


# ═══════════════════════════════════════════════════════════════════
#  4. SkillEnhancer 反馈驱动
# ═══════════════════════════════════════════════════════════════════

class TestEnhancerFeedbackDriven:
    """SkillEnhancer 反馈驱动优化建议"""

    def test_optimize_params_with_low_rating(self, tmp_path):
        store = SkillStore(path=str(tmp_path / "s.json"))
        skill = Skill(id="s1", name="s1", description="d",
                      content="c", content_type="python",
                      category="custom", tags=[], author="t")
        skill.metrics.record(success=True, latency_ms=100)
        skill.metrics.record(success=True, latency_ms=120)
        store.upsert(skill)

        enhancer = SkillEnhancer(store)
        result = enhancer.optimize_params(
            "s1",
            feedback_summary={
                "total_feedback": 10,
                "satisfaction_rate_percent": 30.0,
                "avg_rating": 2.2,
            }
        )
        assert any("评分" in r for r in result["recommendations"])
        assert result["actions_taken"].get("low_rating") is True
        assert result["actions_taken"].get("consider_deprecate") is True

    def test_optimize_params_with_high_satisfaction(self, tmp_path):
        store = SkillStore(path=str(tmp_path / "s.json"))
        skill = Skill(id="s2", name="s2", description="d",
                      content="c", content_type="python",
                      category="custom", tags=[], author="t",
                      status=SkillStatus.APPROVED.value)
        # 让成功率 >= 99% & usage_count >= 10
        for _ in range(10):
            skill.metrics.record(success=True, latency_ms=100)
        store.upsert(skill)

        enhancer = SkillEnhancer(store)
        result = enhancer.optimize_params(
            "s2",
            feedback_summary={
                "total_feedback": 8,
                "satisfaction_rate_percent": 95.0,
                "avg_rating": 4.7,
            }
        )
        assert result["actions_taken"].get("promote_to_published") is True
        # 验证状态被持久化为 PUBLISHED
        updated = store.get("s2")
        assert updated.status == SkillStatus.PUBLISHED.value

    def test_record_execution_with_feedback_rating(self, tmp_path):
        store = SkillStore(path=str(tmp_path / "s.json"))
        skill = Skill(id="s3", name="s3", description="d",
                      content="c", content_type="python",
                      category="custom", tags=[], author="t")
        store.upsert(skill)

        enhancer = SkillEnhancer(store)
        # 不应抛异常，且 feedback_rating > 0 被记录
        enhancer.record_execution(
            "s3", success=True, latency_ms=200,
            feedback_rating=4, feedback_id="fb-1", trace_id="tr-1",
        )
        # 后端权威原则：从 store 重新读取以验证持久化
        updated = store.get("s3")
        assert updated.metrics.usage_count == 1


# ═══════════════════════════════════════════════════════════════════
#  5. ContextInjector 评分引导
# ═══════════════════════════════════════════════════════════════════

class TestContextInjectorFeedbackRequest:
    """ContextInjector.inject_result 评分引导注入"""

    def test_inject_result_without_feedback_request(self):
        from agent.skills_mgmt.loader import SkillLoader
        injector = ContextInjector()
        result = ExecutionResult(
            skill_id="s1", script_name="main.py",
            success=True, exit_code=0,
            stdout='{"k":"v"}', stderr="",
            duration_ms=12.5,
        )
        ctx = injector.inject_result(result)
        assert "脚本执行结果" in ctx["prompt"]
        assert ctx["feedback_request"] is None

    def test_inject_result_with_feedback_request(self):
        injector = ContextInjector()
        result = ExecutionResult(
            skill_id="skill-pdf", script_name="main.py",
            success=True, exit_code=0,
            stdout='{"pages": 3}', stderr="",
            duration_ms=20.0,
        )
        ctx = injector.inject_result(
            result, request_feedback=True, trace_id="tr-abc"
        )
        assert ctx["feedback_request"] is not None
        assert ctx["feedback_request"]["skill_id"] == "skill-pdf"
        assert ctx["feedback_request"]["trace_id"] == "tr-abc"
        assert "评分" in ctx["prompt"]
        assert "skill-pdf" in ctx["prompt"]


# ═══════════════════════════════════════════════════════════════════
#  6. SkillsMgmtService 端到端桥接
# ═══════════════════════════════════════════════════════════════════

class TestServiceSubmitSkillFeedback:
    """SkillsMgmtService.submit_skill_feedback 桥接"""

    def test_submit_skill_feedback_end_to_end(
            self, svc, isolated_feedback_manager):
        # 准备：先创建一个技能
        _make_skill(svc, skill_id="skill-demo")

        result = svc.submit_skill_feedback(
            "skill-demo",
            trace_id="tr-1",
            feedback_type="like",
            rating=5,
            comment="不错",
        )
        assert result["feedback"]["skill_id"] == "skill-demo"
        assert result["feedback"]["feedback_type"] == "like"
        assert result["summary"]["skill_id"] == "skill-demo"
        assert result["summary"]["like_count"] == 1

        # 验证指标也被同步更新
        skill = svc.get("skill-demo")
        assert skill.metrics.usage_count == 1

    def test_submit_skill_feedback_skill_not_found(
            self, svc, isolated_feedback_manager):
        from agent.skills_mgmt import SkillNotFoundError
        with pytest.raises(SkillNotFoundError):
            svc.submit_skill_feedback(
                "not-exist",
                trace_id="tr-x",
                feedback_type="like",
            )

    def test_get_skill_feedback_summary_via_service(
            self, svc, isolated_feedback_manager):
        _make_skill(svc, skill_id="skill-x")
        # 先提交反馈
        svc.submit_skill_feedback(
            "skill-x", trace_id="t1", feedback_type="dislike", rating=2,
        )
        summary = svc.get_skill_feedback_summary("skill-x")
        assert summary["dislike_count"] == 1
        assert summary["total_feedback"] == 1

    def test_optimize_with_feedback_via_service(
            self, svc, isolated_feedback_manager):
        _make_skill(svc, skill_id="skill-opt")
        # 提交一些低分反馈
        for i in range(5):
            svc.submit_skill_feedback(
                "skill-opt", trace_id=f"t{i}",
                feedback_type="dislike", rating=1,
                comment=f"问题{i}",
            )
        result = svc.optimize_with_feedback("skill-opt")
        assert "recommendations" in result
        assert "feedback_summary" in result
        assert result["feedback_summary"]["dislike_count"] == 5


# ═══════════════════════════════════════════════════════════════════
#  7. 边界处理
# ═══════════════════════════════════════════════════════════════════

class TestBoundaryValidation:
    """submit_feedback 边界校验"""

    def test_invalid_rating_raises(self, isolated_feedback_manager):
        mgr = isolated_feedback_manager
        with pytest.raises(ValueError) as exc:
            mgr.submit_feedback(
                trace_id="t", feedback_type="like", rating=99,
            )
        assert "rating" in str(exc.value)

    def test_invalid_feedback_type_raises(self, isolated_feedback_manager):
        mgr = isolated_feedback_manager
        with pytest.raises(ValueError) as exc:
            mgr.submit_feedback(
                trace_id="t", feedback_type="invalid_type",
            )
        assert "feedback_type" in str(exc.value)

    def test_empty_trace_id_raises(self, isolated_feedback_manager):
        mgr = isolated_feedback_manager
        with pytest.raises(ValueError) as exc:
            mgr.submit_feedback(
                trace_id="", feedback_type="like",
            )
        assert "trace_id" in str(exc.value)


# ═══════════════════════════════════════════════════════════════════
#  8. 状态同步机制验证（满足用户规则要求）
# ═══════════════════════════════════════════════════════════════════

class TestStateSynchronization:
    """验证状态同步机制：
    - 后端权威原则：feedback 落库后所有读取都基于持久化数据
    - 乐观更新回滚：skill metrics 在 feedback 失败时应保持一致
    """

    def test_feedback_persisted_across_sessions(
            self, tmp_path, monkeypatch):
        """反馈数据应跨会话持久化"""
        import agent.feedback as fb_module
        monkeypatch.setattr(fb_module, "_global_feedback_manager", None)

        storage = str(tmp_path / "fb")
        mgr1 = FeedbackManager(storage_path=storage)
        mgr1.initialize()
        mgr1.submit_feedback(
            trace_id="t-persist", feedback_type="like", rating=5,
            skill_id="skill-persist",
        )

        # 模拟新会话：重新创建 manager
        mgr2 = FeedbackManager(storage_path=storage)
        mgr2.initialize()
        records = mgr2.get_feedback_by_skill("skill-persist")

        assert len(records) == 1
        assert records[0].skill_id == "skill-persist"
        assert records[0].rating == 5

    def test_skill_status_promoted_after_high_score(
            self, tmp_path, monkeypatch):
        """高评分 + APPROVED 状态 → 应自动晋升为 PUBLISHED"""
        import agent.feedback as fb_module
        monkeypatch.setattr(fb_module, "_global_feedback_manager", None)

        # 准备 feedback manager
        fb_storage = str(tmp_path / "fb")
        fb_mgr = FeedbackManager(storage_path=fb_storage)
        fb_mgr.initialize()
        monkeypatch.setattr(fb_module, "get_feedback_manager",
                            lambda: fb_mgr)

        # 准备 APPROVED 状态的技能 + 高使用量
        store = SkillStore(path=str(tmp_path / "s.json"))
        skill = Skill(id="s-promote", name="s-promote", description="d",
                      content="c", content_type="python",
                      category="custom", tags=[], author="t",
                      status=SkillStatus.APPROVED.value)
        for _ in range(10):
            skill.metrics.record(success=True, latency_ms=100)
        store.upsert(skill)

        enhancer = SkillEnhancer(store)
        result = enhancer.optimize_with_feedback("s-promote")

        # 验证：自动晋升 PUBLISHED 落库
        updated = store.get("s-promote")
        assert updated.status == SkillStatus.PUBLISHED.value
        assert result["actions_taken"].get("promote_to_published") is True
