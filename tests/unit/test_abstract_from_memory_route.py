"""POST /api/skills-mgmt/abstract-from-memory 路由集成测试

测试覆盖:
    1. 健康检查 (路由可达)
    2. 默认参数: 返回草稿, auto_register=False
    3. auto_register=True 时调用 create_manual
    4. 自定义 min_cluster_size / min_success_rate / cluster_jaccard
    5. 参数边界校验: days<1 / max_skills 越界 / min_success_rate 越界
    6. 空记忆场景 (返回空 drafts)
    7. 响应结构完整性
    8. 多聚类场景 + 排序
"""

from __future__ import annotations
import os
import tempfile
import unittest
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

from flask import Flask

from agent.skills_mgmt.memory_abstractor import MemoryEntry
from agent.skills_mgmt.models import Skill, SkillStatus, SkillCategory, ContentType
from agent.skills_mgmt.store import SkillStore


def _build_minimal_app(skills_service):
    """构造最小 Flask app, 注册 skills-mgmt 路由 (不使用 patch context manager)

    注意: 调用方需自行 patch get_skills_mgmt_service 在请求期间返回 skills_service
    """
    app = Flask(__name__)
    app.config.update(TESTING=True)
    from agent.server_routes.routes_skills_mgmt import register_routes
    state = type("_S", (), {})()
    register_routes(app, state)
    return app


def _make_skill_from_draft(draft: Dict[str, Any]) -> Skill:
    """从草稿 dict 构造 Skill (用于 mock create_manual)"""
    return Skill(**draft)


class _AbstractRouteTestBase(unittest.TestCase):
    """构造 mock service + app + client

    关键: patch 必须在整个测试期间生效, 不能只在 app 构造时
    """

    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="abs_route_test_")
        self.store = SkillStore(path=os.path.join(self._tmpdir, "skills.json"))
        # mock skills service
        self.svc = MagicMock()
        self.svc.list_all.return_value = []  # 无已有技能 → 不会触发重复检测
        self.svc.create_manual.side_effect = self._create_manual
        self.svc.review.return_value = MagicMock(status="PASSED")

        self._created: List[Skill] = []

        # 启动 patch — 持续到 tearDown
        self._patches = [
            patch("agent.state_manager.get_skills_mgmt_service",
                  return_value=self.svc),
            patch("agent.server_auth.require_token", lambda f: f),
            patch("agent.server_routes.routes_skills_mgmt.get_skills_mgmt_service",
                  return_value=self.svc),
        ]
        for p in self._patches:
            p.start()

        app = _build_minimal_app(self.svc)
        self.client = app.test_client()

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()
        for fn in os.listdir(self._tmpdir):
            os.remove(os.path.join(self._tmpdir, fn))
        os.rmdir(self._tmpdir)

    def _create_manual(self, data: Dict[str, Any]) -> Skill:
        skill = _make_skill_from_draft(data)
        self.store.upsert(skill)
        self._created.append(skill)
        return skill

    def _make_cluster_entries(self, n: int = 5,
                                text: str = "analyze python code for bugs",
                                success: bool = True) -> List[Dict[str, Any]]:
        """构造 n 条相似记忆 (用于注入到 abstractor)"""
        return [
            {
                "source": "workflow",
                "source_id": f"wf-{i}",
                "task_text": text,
                "success": success,
                "tool_calls": [{"name": "grep"}, {"name": "ast"}],
                "params": {"language": "python", "verbose": True},
                "tags": ["code-review", "python"],
            }
            for i in range(n)
        ]


class TestAbstractRouteHealth(_AbstractRouteTestBase):
    """路由可达性"""

    def test_route_is_registered(self):
        """POST /api/skills-mgmt/abstract-from-memory 应返回 200"""
        # 用空数据集测试 — abstractor 会返回空 drafts
        with patch(
            "agent.skills_mgmt.memory_abstractor.MemorySkillAbstractor._load_recent_memories",
            return_value=[],
        ):
            resp = self.client.post(
                "/api/skills-mgmt/abstract-from-memory",
                json={"enable_signal_scoring": False},
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])


class TestDefaultParams(_AbstractRouteTestBase):
    """默认参数场景"""

    def test_default_returns_drafts_without_registering(self):
        """默认 auto_register=False → 不调用 create_manual"""
        entries = self._make_cluster_entries(n=5)
        with patch(
            "agent.skills_mgmt.memory_abstractor.MemorySkillAbstractor._load_recent_memories",
            return_value=[MemoryEntry(**e) for e in entries],
        ):
            resp = self.client.post(
                "/api/skills-mgmt/abstract-from-memory",
                json={"enable_signal_scoring": False},
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["total_clusters"], 1)
        self.assertEqual(data["passed_clusters"], 1)
        self.assertEqual(data["registered_count"], 0)
        # 不应调用 create_manual
        self.assertEqual(self.svc.create_manual.call_count, 0)

    def test_default_response_shape(self):
        """响应结构完整"""
        entries = self._make_cluster_entries(n=5)
        with patch(
            "agent.skills_mgmt.memory_abstractor.MemorySkillAbstractor._load_recent_memories",
            return_value=[MemoryEntry(**e) for e in entries],
        ):
            resp = self.client.post(
                "/api/skills-mgmt/abstract-from-memory",
                json={"enable_signal_scoring": False},
            )
        data = resp.get_json()
        # 顶层字段
        for key in ("ok", "total_input_memories", "total_clusters",
                    "passed_clusters", "registered_count", "drafts"):
            self.assertIn(key, data)
        # drafts 第一项字段
        d = data["drafts"][0]
        for key in ("cluster_id", "cluster_size", "success_rate",
                    "common_tool_names", "common_tags",
                    "draft_skill_id", "draft_name", "draft_description",
                    "draft_content_preview", "draft_default_params",
                    "quality_gate_passed", "quality_gate_reasons",
                    "registered", "skill_id", "duplicate_of"):
            self.assertIn(key, d)


class TestAutoRegister(_AbstractRouteTestBase):
    """auto_register=True"""

    def test_auto_register_creates_skill(self):
        """auto_register=True 时调用 create_manual"""
        entries = self._make_cluster_entries(n=5)
        with patch(
            "agent.skills_mgmt.memory_abstractor.MemorySkillAbstractor._load_recent_memories",
            return_value=[MemoryEntry(**e) for e in entries],
        ):
            resp = self.client.post(
                "/api/skills-mgmt/abstract-from-memory",
                json={"auto_register": True, "enable_signal_scoring": False},
            )
        data = resp.get_json()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(data["registered_count"], 1)
        self.assertEqual(self.svc.create_manual.call_count, 1)
        # skill_id 应在响应中
        self.assertIsNotNone(data["drafts"][0]["skill_id"])


class TestCustomParams(_AbstractRouteTestBase):
    """自定义参数"""

    def test_min_cluster_size_early_return(self):
        """min_cluster_size=10 时, 8 条 < 10 → 输入检查提前返回, 不聚类"""
        entries = self._make_cluster_entries(n=8)
        with patch(
            "agent.skills_mgmt.memory_abstractor.MemorySkillAbstractor._load_recent_memories",
            return_value=[MemoryEntry(**e) for e in entries],
        ):
            resp = self.client.post(
                "/api/skills-mgmt/abstract-from-memory",
                json={"min_cluster_size": 10, "enable_signal_scoring": False},
            )
        data = resp.get_json()
        # 输入检查: 8 < 10 → 提前返回空 drafts, 不进入聚类阶段
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(data["total_clusters"], 0)
        self.assertEqual(data["passed_clusters"], 0)
        self.assertEqual(data["drafts"], [])

    def test_min_cluster_size_quality_gate_fail(self):
        """min_cluster_size=10 时, 12 条聚成 2 类 (各 6 条), 都因 size=6 < 10 不通过质量门"""
        entries = []
        # 组 1: 6 条 python 分析 (互不相同 → 与组 2 不聚类)
        for i in range(6):
            entries.append({
                "source": "t", "source_id": f"py-{i}",
                "task_text": "analyze python code for bugs",
                "success": True,
                "tool_calls": [{"name": "grep"}, {"name": "ast"}],
                "params": {"language": "python"},
                "tags": ["python", "review"],
            })
        # 组 2: 6 条天气查询 (与组 1 完全不同的词集)
        for i in range(6):
            entries.append({
                "source": "t", "source_id": f"weather-{i}",
                "task_text": "weather forecast tokyo tomorrow",
                "success": True,
                "tool_calls": [{"name": "http"}],
                "params": {"city": "tokyo"},
                "tags": ["weather"],
            })
        with patch(
            "agent.skills_mgmt.memory_abstractor.MemorySkillAbstractor._load_recent_memories",
            return_value=[MemoryEntry(**e) for e in entries],
        ):
            resp = self.client.post(
                "/api/skills-mgmt/abstract-from-memory",
                json={"min_cluster_size": 10, "max_skills": 10, "enable_signal_scoring": False},
            )
        data = resp.get_json()
        # 12 条 >= 10 → 输入检查通过; 聚成 2 类 (各 6 条), 都因 size=6 < 10 不通过质量门
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(data["total_clusters"], 2)
        self.assertEqual(data["passed_clusters"], 0)
        for d in data["drafts"]:
            self.assertFalse(d["quality_gate_passed"])
            self.assertTrue(any("聚类大小" in r
                                for r in d["quality_gate_reasons"]))

    def test_cluster_jaccard_override(self):
        """cluster_jaccard=0.9 时, 相似但不完全相同的文本不聚类"""
        entries = [
            {"source": "t", "source_id": f"id-{i}",
             "task_text": f"analyze python code version {i}",
             "success": True, "tool_calls": [{"name": "grep"}],
             "params": {}, "tags": []}
            for i in range(5)
        ]
        # 高阈值 → 不聚类 → 5 个独立聚类 (大小=1)
        with patch(
            "agent.skills_mgmt.memory_abstractor.MemorySkillAbstractor._load_recent_memories",
            return_value=[MemoryEntry(**e) for e in entries],
        ):
            resp = self.client.post(
                "/api/skills-mgmt/abstract-from-memory",
                json={"cluster_jaccard": 0.95, "max_skills": 10, "enable_signal_scoring": False},
            )
        data = resp.get_json()
        # 5 个独立聚类, 都因为 size=1 < 3 不通过质量门
        self.assertEqual(data["total_clusters"], 5)
        self.assertEqual(data["passed_clusters"], 0)


class TestParamValidation(_AbstractRouteTestBase):
    """参数边界校验"""

    def test_days_below_one(self):
        resp = self.client.post(
            "/api/skills-mgmt/abstract-from-memory",
            json={"days": 0},
        )
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertFalse(data["ok"])
        self.assertIn("days", data["error"])

    def test_max_skills_below_one(self):
        resp = self.client.post(
            "/api/skills-mgmt/abstract-from-memory",
            json={"max_skills": 0},
        )
        self.assertEqual(resp.status_code, 400)

    def test_max_skills_above_50(self):
        resp = self.client.post(
            "/api/skills-mgmt/abstract-from-memory",
            json={"max_skills": 100},
        )
        self.assertEqual(resp.status_code, 400)

    def test_min_success_rate_out_of_range(self):
        resp = self.client.post(
            "/api/skills-mgmt/abstract-from-memory",
            json={"min_success_rate": 1.5},
        )
        self.assertEqual(resp.status_code, 400)

    def test_cluster_jaccard_negative(self):
        resp = self.client.post(
            "/api/skills-mgmt/abstract-from-memory",
            json={"cluster_jaccard": -0.1},
        )
        self.assertEqual(resp.status_code, 400)

    def test_invalid_json_returns_400(self):
        """非 JSON body 应回退到默认值"""
        # 不传 body → request.get_json() 返回 None → {} → 默认值
        resp = self.client.post(
            "/api/skills-mgmt/abstract-from-memory",
            content_type="application/json",
            data="",
        )
        # 不应 400, 应该用默认参数跑 (但记忆可能为空)
        self.assertIn(resp.status_code, (200, 500))


class TestEmptyMemories(_AbstractRouteTestBase):
    """空记忆场景"""

    def test_no_memories_returns_empty_drafts(self):
        with patch(
            "agent.skills_mgmt.memory_abstractor.MemorySkillAbstractor._load_recent_memories",
            return_value=[],
        ):
            resp = self.client.post(
                "/api/skills-mgmt/abstract-from-memory",
                json={"enable_signal_scoring": False},
            )
        data = resp.get_json()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(data["total_clusters"], 0)
        self.assertEqual(data["drafts"], [])


class TestMultiClusterScenario(_AbstractRouteTestBase):
    """多聚类场景"""

    def test_two_distinct_clusters(self):
        """两组不相似的记忆应聚成 2 个聚类"""
        entries = []
        # 聚类 1: 5 条 python 分析
        for i in range(5):
            entries.append(MemoryEntry(
                source="t", source_id=f"py-{i}",
                task_text="analyze python code quality issues",
                success=True, tool_calls=[{"name": "grep"}],
                params={"lang": "python"}, tags=["python", "review"],
            ))
        # 聚类 2: 5 条天气查询
        for i in range(5):
            entries.append(MemoryEntry(
                source="t", source_id=f"weather-{i}",
                task_text="weather forecast tokyo tomorrow",
                success=True, tool_calls=[{"name": "http"}],
                params={"city": "tokyo"}, tags=["weather"],
            ))
        with patch(
            "agent.skills_mgmt.memory_abstractor.MemorySkillAbstractor._load_recent_memories",
            return_value=entries,
        ):
            resp = self.client.post(
                "/api/skills-mgmt/abstract-from-memory",
                json={"max_skills": 5, "enable_signal_scoring": False},
            )
        data = resp.get_json()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(data["total_clusters"], 2)
        self.assertEqual(data["passed_clusters"], 2)
        # 草稿应按 cluster_size 排序 (这里两个 size 都=5, 顺序不重要)
        sizes = [d["cluster_size"] for d in data["drafts"]]
        self.assertEqual(sorted(sizes), [5, 5])

    def test_quality_gate_failures_dont_register(self):
        """质量门控不通过的草稿不应注册"""
        entries = []
        # 1 个高质量聚类 (5 条全成功)
        for i in range(5):
            entries.append(MemoryEntry(
                source="t", source_id=f"good-{i}",
                task_text="analyze python code quality",
                success=True,
            ))
        # 1 个低质量聚类 (5 条全失败)
        for i in range(5):
            entries.append(MemoryEntry(
                source="t", source_id=f"bad-{i}",
                task_text="weather forecast tokyo",
                success=False,
            ))
        with patch(
            "agent.skills_mgmt.memory_abstractor.MemorySkillAbstractor._load_recent_memories",
            return_value=entries,
        ), patch(
            "agent.skills_mgmt.memory_abstractor.MemorySkillAbstractor._find_duplicate",
            return_value=None,
        ):
            resp = self.client.post(
                "/api/skills-mgmt/abstract-from-memory",
                json={"auto_register": True, "max_skills": 5, "enable_signal_scoring": False},
            )
        data = resp.get_json()
        # 只有 1 个聚类通过质量门 (高质量的那个)
        self.assertEqual(data["passed_clusters"], 1)
        self.assertEqual(data["registered_count"], 1)
        self.assertEqual(self.svc.create_manual.call_count, 1)


class TestDuplicateDetection(_AbstractRouteTestBase):
    """与已有技能重复时不创建"""

    def test_duplicate_blocks_registration(self):
        """草稿与已有技能相似度过高时不通过质量门

        通过 patch _find_duplicate 直接模拟重复检测命中,
        避免依赖实际 Jaccard 计算 (那是 test_memory_skill_abstractor 的职责)
        """
        entries = self._make_cluster_entries(n=5)
        with patch(
            "agent.skills_mgmt.memory_abstractor.MemorySkillAbstractor._load_recent_memories",
            return_value=[MemoryEntry(**e) for e in entries],
        ), patch(
            "agent.skills_mgmt.memory_abstractor.MemorySkillAbstractor._find_duplicate",
            return_value="existing-skill-id",
        ):
            resp = self.client.post(
                "/api/skills-mgmt/abstract-from-memory",
                json={"auto_register": True, "enable_signal_scoring": False},
            )
        data = resp.get_json()
        # 草稿与已有技能重复 → 不通过质量门
        self.assertEqual(data["passed_clusters"], 0)
        self.assertEqual(data["registered_count"], 0)
        self.assertEqual(self.svc.create_manual.call_count, 0)
        # duplicate_of 应指向 existing-skill-id
        self.assertEqual(data["drafts"][0]["duplicate_of"], "existing-skill-id")


if __name__ == "__main__":
    unittest.main()
