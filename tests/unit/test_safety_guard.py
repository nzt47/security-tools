"""
安全守护模块测试
"""

import pytest
import tempfile
import json
import os

from agent.safety_guard import (
    SafetyGuard,
    get_safety_guard,
    register_alert_callback,
)


class TestSafetyGuardInit:
    """测试安全守护器初始化"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_init_default(self):
        """测试默认初始化"""
        guard = SafetyGuard()
        assert guard._keywords is not None
        assert guard._alert_history == []
        assert guard._blocked_count == 0
        assert guard._warned_count == 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_init_with_custom_keywords_path(self):
        """测试自定义关键词路径初始化"""
        with tempfile.TemporaryDirectory() as tmpdir:
            keywords_file = os.path.join(tmpdir, "keywords.json")
            keywords_data = {
                "critical": [{"pattern": "dangerous", "description": "危险操作"}],
                "warning": [{"pattern": "caution", "description": "注意"}]
            }
            with open(keywords_file, "w", encoding="utf-8") as f:
                json.dump(keywords_data, f)
            
            guard = SafetyGuard(keywords_path=keywords_file)
            assert len(guard._keywords.get("critical", [])) == 1
            assert len(guard._keywords.get("warning", [])) == 1

    @pytest.mark.unit
    @pytest.mark.p0
    def test_init_with_missing_keywords_file(self):
        """测试关键词文件不存在时的初始化"""
        guard = SafetyGuard(keywords_path="/nonexistent/path/keywords.json")
        # 应该使用内置规则（空规则）
        assert guard._keywords is not None


class TestSafetyCheck:
    """测试安全检查功能"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_check_empty_text(self):
        """测试空文本检查"""
        guard = SafetyGuard()
        result = guard.check("")
        assert result["safe"] is True
        assert result["level"] == "safe"
        assert result["matches"] == []

    @pytest.mark.unit
    @pytest.mark.p0
    def test_check_none_text(self):
        """测试 None 文本检查"""
        guard = SafetyGuard()
        result = guard.check(None)
        assert result["safe"] is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_check_safe_text(self):
        """测试安全文本检查"""
        guard = SafetyGuard()
        result = guard.check("这是一段普通的文本内容")
        assert result["safe"] is True
        assert result["level"] == "safe"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_check_with_custom_keywords(self):
        """测试自定义关键词检查"""
        with tempfile.TemporaryDirectory() as tmpdir:
            keywords_file = os.path.join(tmpdir, "keywords.json")
            keywords_data = {
                "critical": [{"pattern": "rm -rf", "description": "删除命令", "category": "system"}],
                "warning": [{"pattern": "sudo", "description": "超级用户", "category": "privilege"}]
            }
            with open(keywords_file, "w", encoding="utf-8") as f:
                json.dump(keywords_data, f)
            
            guard = SafetyGuard(keywords_path=keywords_file)
            
            # 测试严重级别匹配
            result = guard.check("rm -rf /home/user")
            assert result["safe"] is False
            assert result["level"] == "critical"
            assert len(result["matches"]) > 0
            
            # 测试警告级别匹配
            result2 = guard.check("sudo apt install")
            assert result2["safe"] is False
            assert result2["level"] == "warning"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_check_case_insensitive(self):
        """测试大小写不敏感"""
        with tempfile.TemporaryDirectory() as tmpdir:
            keywords_file = os.path.join(tmpdir, "keywords.json")
            keywords_data = {
                "critical": [{"pattern": "DELETE", "description": "删除", "category": "db"}],
                "warning": []
            }
            with open(keywords_file, "w", encoding="utf-8") as f:
                json.dump(keywords_data, f)
            
            guard = SafetyGuard(keywords_path=keywords_file)
            
            # 大小写不敏感匹配
            result = guard.check("delete from table")
            assert result["safe"] is False
            
            result2 = guard.check("DELETE FROM TABLE")
            assert result2["safe"] is False


class TestAlertRecording:
    """测试告警记录"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_alert_history_limit(self):
        """测试告警历史限制"""
        with tempfile.TemporaryDirectory() as tmpdir:
            keywords_file = os.path.join(tmpdir, "keywords.json")
            keywords_data = {
                "critical": [{"pattern": "danger", "description": "危险", "category": "test"}],
                "warning": []
            }
            with open(keywords_file, "w", encoding="utf-8") as f:
                json.dump(keywords_data, f)
            
            guard = SafetyGuard(keywords_path=keywords_file)
            guard._max_alerts = 10
            
            # 生成超过限制的告警
            for i in range(15):
                guard.check(f"danger text {i}")
            
            # 历史应该被限制
            assert len(guard._alert_history) <= 10

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_alerts(self):
        """测试获取告警记录"""
        guard = SafetyGuard()
        
        # 无告警时
        alerts = guard.get_alerts()
        assert alerts == []
        
        # 添加一些告警
        guard._alert_history = [
            {"timestamp": "2024-01-01", "level": "warning"},
            {"timestamp": "2024-01-02", "level": "critical"},
        ]
        
        alerts = guard.get_alerts(limit=1)
        assert len(alerts) == 1


class TestStatistics:
    """测试统计功能"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_stats_initial(self):
        """测试初始统计"""
        guard = SafetyGuard()
        stats = guard.get_stats()
        
        assert stats["blocked_count"] == 0
        assert stats["warned_count"] == 0
        assert stats["total_alerts"] == 0
        assert "keywords_loaded" in stats

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_stats_after_checks(self):
        """测试检查后的统计"""
        with tempfile.TemporaryDirectory() as tmpdir:
            keywords_file = os.path.join(tmpdir, "keywords.json")
            keywords_data = {
                "critical": [{"pattern": "critical_word", "description": "严重", "category": "test"}],
                "warning": [{"pattern": "warning_word", "description": "警告", "category": "test"}]
            }
            with open(keywords_file, "w", encoding="utf-8") as f:
                json.dump(keywords_data, f)
            
            guard = SafetyGuard(keywords_path=keywords_file)
            
            # 执行一些检查
            guard.check("critical_word here")
            guard.check("warning_word there")
            guard.check("safe text")
            
            stats = guard.get_stats()
            assert stats["blocked_count"] == 1
            assert stats["warned_count"] == 1


class TestDynamicKeywords:
    """测试动态添加关键词"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_add_critical_keyword(self):
        """测试添加严重级别关键词"""
        guard = SafetyGuard()
        
        guard.add_keyword(
            pattern="new_critical",
            description="新增严重关键词",
            level="critical",
            category="custom"
        )
        
        # 检查是否生效
        result = guard.check("new_critical detected")
        assert result["safe"] is False
        assert result["level"] == "critical"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_add_warning_keyword(self):
        """测试添加警告级别关键词"""
        guard = SafetyGuard()
        
        guard.add_keyword(
            pattern="new_warning",
            description="新增警告关键词",
            level="warning",
            category="custom"
        )
        
        result = guard.check("new_warning detected")
        assert result["safe"] is False
        assert result["level"] == "warning"


class TestReload:
    """测试重新加载"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_reload_keywords(self):
        """测试重新加载关键词"""
        with tempfile.TemporaryDirectory() as tmpdir:
            keywords_file = os.path.join(tmpdir, "keywords.json")
            
            # 初始关键词
            keywords_data = {
                "critical": [{"pattern": "initial", "description": "初始", "category": "test"}],
                "warning": []
            }
            with open(keywords_file, "w", encoding="utf-8") as f:
                json.dump(keywords_data, f)
            
            guard = SafetyGuard(keywords_path=keywords_file)
            assert guard.check("initial")["safe"] is False
            
            # 更新关键词文件
            keywords_data["critical"].append(
                {"pattern": "updated", "description": "更新", "category": "test"}
            )
            with open(keywords_file, "w", encoding="utf-8") as f:
                json.dump(keywords_data, f)
            
            # 重新加载
            guard.reload()
            
            # 新关键词应该生效
            assert guard.check("updated")["safe"] is False


class TestGlobalInstance:
    """测试全局实例"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_safety_guard(self):
        """测试获取全局实例"""
        guard = get_safety_guard()
        assert guard is not None
        assert isinstance(guard, SafetyGuard)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_register_alert_callback(self):
        """测试注册告警回调"""
        callback_called = []
        
        def test_callback(alert):
            callback_called.append(alert)
        
        register_alert_callback(test_callback)
        
        # 回调应该被注册
        from agent.safety_guard import _alert_callbacks
        assert test_callback in _alert_callbacks