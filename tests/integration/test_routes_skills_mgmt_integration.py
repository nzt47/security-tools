"""routes_skills_mgmt HTTP 端点集成测试

覆盖技能管理系统 v1 API 的核心端点：
1. 健康检查
2. 高级搜索与参数校验
3. AI 辅助创建技能
4. 技能详情获取与异常映射（404/400）
5. 技能更新与删除
6. 技能反馈提交校验
7. 审核阈值获取与更新
"""

import pytest
from unittest.mock import MagicMock

from agent.skills_mgmt.exceptions import SkillMgmtError, SkillNotFoundError

pytestmark = pytest.mark.integration


class TestRoutesSkillsMgmtIntegration:
    """routes_skills_mgmt HTTP 端点集成测试"""

    def test_health_endpoint(self, skills_mgmt_client):
        """测试 1：健康检查端点"""
        client, mock_svc = skills_mgmt_client

        mock_svc.health.return_value = {"ok": True, "storage": "ready"}

        resp = client.get("/api/skills-mgmt/health")

        assert resp.status_code == 200
        body = resp.json
        assert body["ok"] is True
        mock_svc.health.assert_called_once()

    def test_search_with_filters(self, skills_mgmt_client):
        """测试 2：高级搜索与分页"""
        client, mock_svc = skills_mgmt_client

        # 构造 mock 搜索结果
        mock_skill = MagicMock()
        mock_skill.model_dump.return_value = {"skill_id": "s1", "name": "测试技能"}
        mock_result = MagicMock()
        mock_result.items = [mock_skill]
        mock_result.total = 1
        mock_result.page = 1
        mock_result.page_size = 20
        mock_result.elapsed_ms = 5.2
        mock_svc.search.return_value = mock_result

        resp = client.get("/api/skills-mgmt/search?q=测试&page=1&page_size=20")

        assert resp.status_code == 200
        body = resp.json
        assert body["ok"] is True
        assert len(body["items"]) == 1
        assert body["total"] == 1
        assert body["page"] == 1
        mock_svc.search.assert_called_once()

    def test_create_via_ai_validates_inputs(self, skills_mgmt_client):
        """测试 3：AI 辅助创建技能的参数校验"""
        client, mock_svc = skills_mgmt_client

        # 缺少 name → 400
        resp = client.post("/api/skills-mgmt/create/ai", json={"intent": "do something"})
        assert resp.status_code == 400
        assert resp.json["ok"] is False

        # 缺少 intent → 400
        resp = client.post("/api/skills-mgmt/create/ai", json={"name": "测试技能"})
        assert resp.status_code == 400

        # name 为空字符串 → 400
        resp = client.post("/api/skills-mgmt/create/ai",
                           json={"name": "  ", "intent": "do something"})
        assert resp.status_code == 400

        # 正常创建 → 201
        mock_skill = MagicMock()
        mock_skill.model_dump.return_value = {"skill_id": "s1", "name": "测试技能"}
        mock_svc.create_via_ai.return_value = mock_skill

        resp = client.post("/api/skills-mgmt/create/ai",
                           json={"name": "测试技能", "intent": "搜索信息"})
        assert resp.status_code == 201
        body = resp.json
        assert body["ok"] is True
        assert body["skill"]["skill_id"] == "s1"
        mock_svc.create_via_ai.assert_called_once_with(
            name="测试技能", intent="搜索信息",
            category="custom", tags=None,
        )

    def test_get_skill_not_found_returns_404(self, skills_mgmt_client):
        """测试 4：技能不存在返回 404，其他业务异常返回 400"""
        client, mock_svc = skills_mgmt_client

        # SkillNotFoundError → 404
        mock_svc.get.side_effect = SkillNotFoundError("skill_xxx")
        resp = client.get("/api/skills-mgmt/skill_xxx")
        assert resp.status_code == 404
        body = resp.json
        assert body["ok"] is False
        assert body["code"] == "SKILL_NOT_FOUND"

        # 其他 SkillMgmtError → 400
        mock_svc.get.side_effect = SkillMgmtError(
            "参数错误", code="SKILL_VALIDATION_ERROR"
        )
        resp = client.get("/api/skills-mgmt/skill_yyy")
        assert resp.status_code == 400
        assert resp.json["code"] == "SKILL_VALIDATION_ERROR"

    def test_update_and_delete_skill(self, skills_mgmt_client):
        """测试 5：技能更新与删除"""
        client, mock_svc = skills_mgmt_client

        # PATCH 更新
        mock_skill = MagicMock()
        mock_skill.model_dump.return_value = {"skill_id": "s1", "name": "新名称"}
        mock_svc.update.return_value = mock_skill

        resp = client.patch("/api/skills-mgmt/s1", json={"name": "新名称"})
        assert resp.status_code == 200
        body = resp.json
        assert body["ok"] is True
        assert body["skill"]["name"] == "新名称"
        mock_svc.update.assert_called_once_with("s1", {"name": "新名称"})

        # DELETE 删除
        mock_svc.delete.return_value = None
        resp = client.delete("/api/skills-mgmt/s1")
        assert resp.status_code == 200
        assert resp.json["ok"] is True
        mock_svc.delete.assert_called_once_with("s1")

    def test_submit_skill_feedback_validates(self, skills_mgmt_client):
        """测试 6：技能反馈提交校验"""
        client, mock_svc = skills_mgmt_client

        # 缺少 trace_id → 400
        resp = client.post("/api/skills-mgmt/s1/feedback",
                           json={"feedback_type": "like"})
        assert resp.status_code == 400
        assert resp.json["code"] == "VALIDATION_ERROR"

        # 非法 feedback_type → 400
        resp = client.post("/api/skills-mgmt/s1/feedback",
                           json={"trace_id": "t1", "feedback_type": "invalid"})
        assert resp.status_code == 400

        # rating 越界 → 400
        resp = client.post("/api/skills-mgmt/s1/feedback",
                           json={"trace_id": "t1", "feedback_type": "like", "rating": 6})
        assert resp.status_code == 400

        # 正常提交 → 200
        mock_svc.submit_skill_feedback.return_value = {
            "feedback": {"feedback_id": "f1"},
            "summary": {"satisfaction_rate_percent": 100.0},
        }
        resp = client.post("/api/skills-mgmt/s1/feedback",
                           json={
                               "trace_id": "trace_001",
                               "feedback_type": "like",
                               "rating": 5,
                               "comment": "很好用",
                               "category": "quality",
                           })
        assert resp.status_code == 200
        body = resp.json
        assert body["ok"] is True
        assert "feedback" in body
        assert "summary" in body

    def test_review_thresholds_get_and_update(self, skills_mgmt_client):
        """测试 7：审核阈值获取与更新"""
        client, mock_svc = skills_mgmt_client

        # 构造 mock reviewer
        mock_reviewer = MagicMock()
        mock_thresholds = MagicMock()
        mock_thresholds.duplicate_max = 60.0
        mock_thresholds.security_min = 70.0
        mock_thresholds.quality_min = 50.0
        mock_thresholds.overall_min = 60.0
        mock_reviewer.thresholds = mock_thresholds
        mock_reviewer.dup_detector.threshold = 0.6
        mock_svc.reviewer = mock_reviewer

        # GET 获取阈值
        resp = client.get("/api/skills-mgmt/review/thresholds")
        assert resp.status_code == 200
        body = resp.json
        assert body["ok"] is True
        thresholds = body["thresholds"]
        assert thresholds["duplicate_max"] == 60.0
        assert thresholds["security_min"] == 70.0
        assert thresholds["quality_min"] == 50.0
        assert thresholds["overall_min"] == 60.0

        # PUT 更新阈值
        from agent.skills_mgmt.reviewer import ReviewThresholds
        resp = client.put("/api/skills-mgmt/review/thresholds",
                          json={
                              "duplicate_max": 70.0,
                              "security_min": 80.0,
                              "quality_min": 60.0,
                              "overall_min": 70.0,
                          })
        assert resp.status_code == 200
        assert resp.json["ok"] is True
        # 验证 ReviewThresholds 被正确构造
        assert mock_reviewer.thresholds is not None
