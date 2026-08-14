#!/usr/bin/env python3
"""自愈恢复动作映射表测试（补 M4）

验收标准 #5：policy.restore_map 可加载且每项 actions 有明确状态（执行/SKIPPED原因）。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.self_healing.policy import (
    RESTORE_MAP,
    ACTION_STATUS,
    DEFAULT_ACTIONS,
    UNIMPLEMENTED_REASON,
    get_actions_for_alert,
    get_domain_for_alert,
    get_domain_actions,
    get_action_status,
)


class TestRestoreMap:
    """restore_map 可加载 + 每个动作有明确状态"""

    def test_restore_map_loadable(self):
        """四个故障域齐全"""
        assert "llm_timeout" in RESTORE_MAP
        assert "tool_failure" in RESTORE_MAP
        assert "memory_failure" in RESTORE_MAP
        assert "decision_loop" in RESTORE_MAP

    def test_each_domain_has_detect_and_actions(self):
        """每项含 detect 条件与 actions 列表"""
        for domain, entry in RESTORE_MAP.items():
            assert "detect" in entry, domain
            assert isinstance(entry["actions"], list) and entry["actions"], domain

    def test_each_action_has_status(self):
        """每项 actions 有明确状态（executed/unimplemented）"""
        for domain, entry in RESTORE_MAP.items():
            for action in entry["actions"]:
                assert get_action_status(action) in ("executed", "unimplemented"), \
                    f"{domain}.{action} 无明确实现状态"

    def test_heal_action_enum_all_covered(self):
        """HealAction 全部枚举值均有状态（未实现动作返回 SKIPPED 而非 FAILED）"""
        from agent.monitoring.self_healer import HealAction
        for member in HealAction:
            assert get_action_status(member.value) in ("executed", "unimplemented"), member.value

    def test_unknown_action_status(self):
        """未注册动作 → unknown"""
        assert get_action_status("no_such_action") == "unknown"


class TestDomainLookup:
    """故障域识别"""

    def test_get_domain_for_alert(self):
        assert get_domain_for_alert("llm_timeout_alert") == "llm_timeout"
        assert get_domain_for_alert("tool_failure_rate") == "tool_failure"
        assert get_domain_for_alert("memory_high") == "memory_failure"
        assert get_domain_for_alert("decision_loop_detected") == "decision_loop"
        assert get_domain_for_alert("unrelated_alert") is None

    def test_get_actions_for_alert(self):
        assert get_actions_for_alert("llm_timeout_alert") == ["retry_limited", "degrade_llm_router"]
        assert get_actions_for_alert("tool_failure_alert") == ["recover_circuit_breaker", "clear_cache"]
        assert get_actions_for_alert("unknown_alert") == DEFAULT_ACTIONS

    def test_get_domain_actions(self):
        assert get_domain_actions("tool_failure") == ["recover_circuit_breaker", "clear_cache"]
        assert get_domain_actions("no_such_domain") == []

    def test_unimplemented_reason_available(self):
        """未实现动作有明确跳过原因"""
        assert UNIMPLEMENTED_REASON == "未实现"
