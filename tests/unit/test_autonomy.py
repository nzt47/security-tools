"""TASK-07 自主权分级（L1-L5）测试

覆盖验收：
- L1-L5 分级表文档化且代码映射与表一致（表格驱动单向验证）；
- L1 会话下写工具：聚合视图标注越级（可查询）；既有 PermissionSystem 行为零变化；
- 等级可会话级覆盖；默认等级 L3；ContextVar 栈式恢复。
"""
import os
import sys
import pytest
from unittest.mock import patch, MagicMock

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from agent.autonomy import (
    AutonomyLevel,
    AutonomyPolicy,
    AutonomyContext,
    ToolCategory,
    ConfirmationScope,
    AutonomyVerdict,
    classify_action,
    get_autonomy_level,
    set_autonomy_level,
    set_session_level,
    get_session_level,
    clear_session_level,
    resolve_autonomy_level,
    reset_config_cache,
)


@pytest.fixture(autouse=True)
def _clean_state():
    """每个用例后清理：会话等级注册表 + 配置缓存 + ContextVar 恢复默认"""
    reset_config_cache()
    try:
        yield
    finally:
        set_autonomy_level(AutonomyLevel.L3)
        clear_session_level("sess_test")


def _base_result(allowed=True, requires_confirmation=False):
    """构造 PermissionResult 同构对象（不依赖真实 PermissionSystem，聚焦聚合逻辑）"""
    return MagicMock(allowed=allowed, requires_confirmation=requires_confirmation)


class TestLevelMappingTable:
    """L1-L5 分级表与代码映射一致（表格驱动单向验证）"""

    def test_five_levels_exist(self):
        """5 个等级全部存在且序号正确"""
        table = AutonomyPolicy.table()
        assert list(table.keys()) == ["L1", "L2", "L3", "L4", "L5"]
        assert AutonomyLevel.L1.level == 1
        assert AutonomyLevel.L5.level == 5

    def test_l1_readonly_only(self):
        """L1 只读观察：仅允许只读类别，全量确认 + 审计"""
        policy = AutonomyPolicy.get(AutonomyLevel.L1)
        assert policy.allowed_categories == frozenset({ToolCategory.READONLY})
        assert policy.confirmation_scope == ConfirmationScope.ALL
        assert policy.audit_required is True
        assert "只读" in policy.mechanism  # 映射: 工具白名单=只读集

    def test_l2_low_risk(self):
        """L2 低风险自主：只读 + 低风险，无确认无审计"""
        policy = AutonomyPolicy.get(AutonomyLevel.L2)
        assert ToolCategory.READONLY in policy.allowed_categories
        assert ToolCategory.LOW_RISK in policy.allowed_categories
        assert ToolCategory.MEDIUM_RISK not in policy.allowed_categories
        assert policy.confirmation_scope == ConfirmationScope.NONE

    def test_l3_medium_confirmation(self):
        """L3 中风险需确认（默认）：中风险允许但需确认，映射 DANGEROUS_PATTERNS 确认链"""
        policy = AutonomyPolicy.get(AutonomyLevel.L3)
        assert ToolCategory.MEDIUM_RISK in policy.allowed_categories
        assert policy.confirmation_scope == ConfirmationScope.MEDIUM_AND_ABOVE
        assert "DANGEROUS_PATTERNS" in policy.mechanism or "SENSITIVE_DIRS" in policy.mechanism

    def test_l4_high_risk_expert(self):
        """L4 高风险专家：全能力 + 全审计，高风险需确认"""
        policy = AutonomyPolicy.get(AutonomyLevel.L4)
        assert policy.allowed_categories == frozenset(ToolCategory)
        assert policy.confirmation_scope == ConfirmationScope.HIGH_ONLY
        assert policy.audit_required is True
        assert "SESSION" in policy.mechanism

    def test_l5_full_autonomy(self):
        """L5 完全自主：全能力无确认，映射 GLOBAL 熔断 + rate_limiter"""
        policy = AutonomyPolicy.get(AutonomyLevel.L5)
        assert policy.allowed_categories == frozenset(ToolCategory)
        assert policy.confirmation_scope == ConfirmationScope.NONE
        assert "GLOBAL" in policy.mechanism and "rate_limiter" in policy.mechanism


class TestClassification:
    """工具行为分类"""

    def test_classify_readonly(self):
        assert classify_action("web_search: hello", tool_name="web_search") == ToolCategory.READONLY
        assert classify_action("read_file: /tmp/a.txt") == ToolCategory.READONLY
        assert classify_action("查询用户信息") == ToolCategory.READONLY

    def test_classify_medium(self):
        assert classify_action("write_file: /tmp/a.txt") == ToolCategory.MEDIUM_RISK
        assert classify_action("删除目录 /tmp/x") == ToolCategory.MEDIUM_RISK

    def test_classify_high(self):
        assert classify_action("shell: rm -rf /") == ToolCategory.HIGH_RISK
        assert classify_action("exec: shutdown") == ToolCategory.HIGH_RISK

    def test_classify_default_low_risk(self):
        assert classify_action("普通无副作用操作") == ToolCategory.LOW_RISK


class TestAggregation:
    """聚合视图（不改既有判定语义）"""

    def test_l1_write_tool_escalation_queryable(self):
        """L1 会话下写工具：聚合视图标注越级（可查询）；base 判定原样透传"""
        base = _base_result(allowed=True)
        with AutonomyContext(AutonomyLevel.L1):
            verdict = AutonomyPolicy.aggregate(base, "write_file: /tmp/a.txt")
        assert verdict.within_level is False
        assert any("operation_outside_level" in f for f in verdict.escalation)
        assert verdict.base_allowed is True            # 既有判定零变化
        assert verdict.base_requires_confirmation is False
        assert verdict.confirmation_required is True   # L1 聚合视图：非只读需确认
        # 可查询视图
        view = verdict.to_dict()
        assert view["level"] == "L1"
        assert view["escalation"] and view["within_level"] is False

    def test_l3_read_allowed_within_level(self):
        """L3 下只读操作在等级内，无越级"""
        with AutonomyContext(AutonomyLevel.L3):
            verdict = AutonomyPolicy.aggregate(_base_result(), "查询天气")
        assert verdict.within_level is True
        assert verdict.escalation == []

    def test_l3_medium_operation_requires_confirmation_view(self):
        """L3 下写文件：既有确认链为假时，聚合视图仍标注需确认（视图层叠加）"""
        base = _base_result(allowed=True, requires_confirmation=False)
        with AutonomyContext(AutonomyLevel.L3):
            verdict = AutonomyPolicy.aggregate(base, "write_file: /tmp/a.txt")
        assert verdict.confirmation_required is True
        assert verdict.base_requires_confirmation is False  # 既有语义零变化

    def test_l4_audit_required(self):
        """L4：系统级操作标注审计要求"""
        with AutonomyContext(AutonomyLevel.L4):
            verdict = AutonomyPolicy.aggregate(_base_result(), "shell: reboot")
        assert verdict.audit_required is True
        assert verdict.confirmation_required is True   # HIGH_ONLY 确认范围命中

    def test_l5_no_escalation(self):
        """L5：全能力无越级无确认"""
        with AutonomyContext(AutonomyLevel.L5):
            verdict = AutonomyPolicy.aggregate(_base_result(), "shell: reboot")
        assert verdict.within_level is True
        assert verdict.escalation == []
        assert verdict.confirmation_required is False


class TestContextAndLevelResolution:
    """ContextVar 注入与会话级覆盖"""

    def test_default_level_is_l3(self, monkeypatch):
        """默认等级 L3（无会话覆盖、无环境变量、无配置）"""
        monkeypatch.delenv("AUTONOMY_DEFAULT_LEVEL", raising=False)
        reset_config_cache()
        assert resolve_autonomy_level(None) == AutonomyLevel.L3
        assert get_autonomy_level() == AutonomyLevel.L3

    def test_session_level_override(self):
        """会话级覆盖等级（resolve 优先于默认）"""
        set_session_level("sess_test", AutonomyLevel.L1)
        assert get_session_level("sess_test") == AutonomyLevel.L1
        assert resolve_autonomy_level("sess_test") == AutonomyLevel.L1
        clear_session_level("sess_test")
        assert resolve_autonomy_level("sess_test") == AutonomyLevel.L3

    def test_autonomy_context_restore(self):
        """ContextVar 栈式恢复：退出后回到进入前等级"""
        before = get_autonomy_level()
        with AutonomyContext(AutonomyLevel.L1):
            assert get_autonomy_level() == AutonomyLevel.L1
        assert get_autonomy_level() == before

    def test_autonomy_context_exception_safe(self):
        """with 块内抛异常，__exit__ 仍恢复（异常安全）"""
        before = get_autonomy_level()
        with pytest.raises(RuntimeError):
            with AutonomyContext(AutonomyLevel.L5):
                raise RuntimeError("boom")
        assert get_autonomy_level() == before

    def test_env_override(self, monkeypatch):
        """环境变量 AUTONOMY_DEFAULT_LEVEL 覆盖默认等级"""
        monkeypatch.setenv("AUTONOMY_DEFAULT_LEVEL", "L1")
        reset_config_cache()
        assert resolve_autonomy_level(None) == AutonomyLevel.L1
        monkeypatch.delenv("AUTONOMY_DEFAULT_LEVEL", raising=False)
        reset_config_cache()

    def test_invalid_level_falls_back_l3(self):
        """非法等级回退默认 L3（宽容解析）"""
        assert AutonomyLevel.from_value("L9") == AutonomyLevel.L3
        assert AutonomyLevel.from_value("") == AutonomyLevel.L3
        assert AutonomyLevel.from_value("3") == AutonomyLevel.L3


class TestConfigOverride:
    """per_level_policy 配置覆盖"""

    def test_policy_override_does_not_pollute_table(self):
        """配置覆盖返回新策略，不污染默认表"""
        with patch.object(AutonomyPolicy, "_overrides",
                          {"L3": {"audit_required": True}}), \
             patch.object(AutonomyPolicy, "_overrides_loaded", True):
            overridden = AutonomyPolicy.get(AutonomyLevel.L3)
            assert overridden.audit_required is True
            assert AutonomyPolicy.POLICY_TABLE[AutonomyLevel.L3].audit_required is False
