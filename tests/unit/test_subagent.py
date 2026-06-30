"""Subagent 分身生命周期抽象测试

测试 SubagentContainer、SubagentLifecycleManager、Sandbox
"""
import time

import pytest

from agent.subagent.container import SubagentConfig, SubagentContainer, ExecutionResult
from agent.subagent.lifecycle import SubagentLifecycleManager, SubagentLifecycleError
from agent.subagent.sandbox import Sandbox, PermissionDenied


# ═══════════════════════════════════════════════════════════════════
#  SubagentConfig 配置测试
# ═══════════════════════════════════════════════════════════════════

class TestSubagentConfig:
    """SubagentConfig 数据类测试"""

    def test_default_values(self):
        """配置应有合理的默认值"""
        config = SubagentConfig(name="test", model_id="gpt-4")
        assert config.memory_provider == "holographic"
        assert config.tool_sources == []
        assert config.context_window == 4096
        assert config.permissions == ["read"]
        assert config.tags == []
        assert config.ttl_seconds == 0  # 永久存活

    def test_custom_values(self):
        """应能设置自定义值"""
        config = SubagentConfig(
            name="coder",
            model_id="claude-3-opus",
            memory_provider="mem0",
            tool_sources=["builtin", "mcp:filesystem"],
            context_window=8192,
            permissions=["read", "write", "execute"],
            tags=["code", "helper"],
            ttl_seconds=3600,
        )
        assert config.name == "coder"
        assert config.model_id == "claude-3-opus"
        assert config.memory_provider == "mem0"
        assert "builtin" in config.tool_sources
        assert config.context_window == 8192
        assert "write" in config.permissions
        assert "code" in config.tags
        assert config.ttl_seconds == 3600

    def test_permissions_mutable(self):
        """权限列表应是可变的（每次新建新实例）"""
        c1 = SubagentConfig(name="a", model_id="m1", permissions=["read"])
        c2 = SubagentConfig(name="b", model_id="m2", permissions=["read", "write"])
        assert c1.permissions != c2.permissions
        assert len(c1.permissions) == 1


# ═══════════════════════════════════════════════════════════════════
#  SubagentContainer 容器测试
# ═══════════════════════════════════════════════════════════════════

class TestSubagentContainer:
    """SubagentContainer 容器功能测试"""

    def setup_method(self):
        self.config = SubagentConfig(name="test-agent", model_id="gpt-4")
        self.container = SubagentContainer(self.config)

    def test_create_has_id(self):
        """创建后应有唯一 ID"""
        assert self.container.id.startswith("sa-")
        assert len(self.container.id) == 15  # "sa-" + 12 hex chars

    def test_create_context_empty(self):
        """初始上下文应为空"""
        assert self.container.context == []

    def test_create_not_destroyed(self):
        """初始状态不应是已销毁"""
        assert not self.container.is_destroyed

    def test_execute_returns_result(self):
        """执行任务应返回 ExecutionResult"""
        result = self.container.execute("测试任务")
        assert isinstance(result, ExecutionResult)

    def test_execute_has_output(self):
        """执行结果应包含占位输出"""
        result = self.container.execute("帮我写代码")
        assert "[Subagent:test-agent]" in result.output
        assert "帮我写代码" in result.output

    def test_execute_has_trace_id(self):
        """执行结果应有追踪 ID"""
        result = self.container.execute("test")
        assert len(result.trace_id) == 16

    def test_execute_records_context(self):
        """执行后应将输入输出记录到上下文"""
        self.container.execute("hello")
        assert len(self.container.context) == 2  # user + assistant
        assert self.container.context[0]["role"] == "user"
        assert self.container.context[0]["content"] == "hello"
        assert self.container.context[1]["role"] == "assistant"

    def test_execute_updated_at(self):
        """执行后应更新 updated_at"""
        old = self.container.updated_at
        time.sleep(0.01)
        self.container.execute("test")
        assert self.container.updated_at > old

    def test_execute_destroyed_returns_error(self):
        """已销毁的分身执行应返回错误"""
        self.container._is_destroyed = True
        result = self.container.execute("any task")
        assert result.error is not None
        assert "已销毁" in result.error

    def test_is_expired_default(self):
        """ttl=0 时不应过期"""
        assert not self.container.is_expired

    def test_is_expired_with_ttl(self):
        """ttl 到期后应标记过期"""
        config = SubagentConfig(name="exp", model_id="m1", ttl_seconds=0.001)
        c = SubagentContainer(config)
        time.sleep(0.01)
        assert c.is_expired

    def test_age_seconds_increases(self):
        """存活时间应持续增长"""
        age1 = self.container.age_seconds
        time.sleep(0.01)
        age2 = self.container.age_seconds
        assert age2 > age1

    def test_memory_delta_record(self):
        """应能记录记忆增量"""
        self.container.record_memory_delta("key1", {"data": "value1"})
        assert "key1" in self.container.get_memory_delta()

    def test_memory_delta_clear(self):
        """应能清空记忆增量"""
        self.container.record_memory_delta("k", "v")
        self.container.clear_memory_delta()
        assert self.container.get_memory_delta() == {}

    def test_clear_context(self):
        """清空上下文后应为空"""
        self.container.execute("task1")
        assert len(self.container.context) > 0
        self.container.clear_context()
        assert self.container.context == []

    def test_get_status(self):
        """状态报告应包含所有字段"""
        status = self.container.get_status()
        assert "id" in status
        assert "name" in status
        assert "model_id" in status
        assert "memory_provider" in status
        assert "is_destroyed" in status
        assert status["name"] == "test-agent"

    def test_duration_ms_non_negative(self):
        """执行耗时不应为负数"""
        import time
        result = self.container.execute("test")
        assert result.duration_ms >= 0

    def test_execution_result_timestamp(self):
        """执行结果应有时间戳"""
        result = self.container.execute("test")
        assert result.timestamp != ""


# ═══════════════════════════════════════════════════════════════════
#  SubagentLifecycleManager 生命周期测试
# ═══════════════════════════════════════════════════════════════════

class TestSubagentLifecycle:
    """SubagentLifecycleManager 生命周期管理"""

    def setup_method(self):
        self.mgr = SubagentLifecycleManager(max_subagents=10)

    def _make_config(self, name: str, **kw):
        return SubagentConfig(name=name, model_id="gpt-4", **kw)

    def test_create_and_destroy(self):
        """创建后应能销毁"""
        agent = self.mgr.create(self._make_config("test-agent"))
        assert agent is not None
        assert self.mgr.count() == 1

        report = self.mgr.destroy(agent)
        assert report["name"] == "test-agent"
        assert self.mgr.count() == 0

    def test_double_create_fails(self):
        """同名分身重复创建应失败"""
        self.mgr.create(self._make_config("unique"))
        with pytest.raises(SubagentLifecycleError, match="名称已存在"):
            self.mgr.create(self._make_config("unique"))

    def test_list_empty_initially(self):
        """初始时分身列表应为空"""
        assert self.mgr.list() == []
        assert self.mgr.count() == 0

    def test_list_after_create(self):
        """创建后列表应包含该分身"""
        agent = self.mgr.create(self._make_config("agent1"))
        agents = self.mgr.list()
        assert len(agents) == 1
        assert agents[0].config.name == "agent1"

    def test_create_multiple_agents(self):
        """应能创建多个分身"""
        for i in range(5):
            self.mgr.create(self._make_config(f"agent_{i}"))
        assert self.mgr.count() == 5

    def test_max_subagents_limit(self):
        """超过最大数量应拒绝"""
        mgr = SubagentLifecycleManager(max_subagents=2)
        mgr.create(self._make_config("a1"))
        mgr.create(self._make_config("a2"))
        with pytest.raises(SubagentLifecycleError, match="上限"):
            mgr.create(self._make_config("a3"))

    def test_get_by_name(self):
        """应按名称获取"""
        self.mgr.create(self._make_config("finder"))
        agent = self.mgr.get("finder")
        assert agent is not None
        assert agent.config.name == "finder"

    def test_get_nonexistent(self):
        """获取不存在的分身应返回 None"""
        assert self.mgr.get("ghost") is None

    def test_get_by_id(self):
        """应按 ID 获取"""
        agent = self.mgr.create(self._make_config("id-test"))
        found = self.mgr.get_by_id(agent.id)
        assert found is not None
        assert found.id == agent.id

    def test_get_by_id_nonexistent(self):
        """按不存在 ID 查询应返回 None"""
        assert self.mgr.get_by_id("nonexistent") is None

    def test_destroy_nonexistent_does_nothing(self):
        """销毁未管理的分身不应影响状态"""
        # 创建一个不在管理器中的分身
        config = self._make_config("orphan")
        orphan = SubagentContainer(config)
        # 直接销毁它应在管理器中无效果
        assert self.mgr.count() == 0

    def test_gc_no_expired(self):
        """无过期分身时 GC 应返回 0"""
        self.mgr.create(self._make_config("perm1"))
        self.mgr.create(self._make_config("perm2"))
        assert self.mgr.gc() == 0

    def test_gc_cleans_expired(self):
        """GC 应清理过期的分身"""
        # 创建永久的
        self.mgr.create(self._make_config("perm"))
        # 创建短 TTL 的
        self.mgr.create(self._make_config("short", ttl_seconds=0.001))
        time.sleep(0.01)
        assert self.mgr.gc() >= 1
        assert self.mgr.count() == 1

    def test_hot_reload_config(self):
        """热更新应替换配置"""
        agent = self.mgr.create(SubagentConfig(name="hot-reload", model_id="gpt-4"))
        new_config = SubagentConfig(name="hot-reload", model_id="claude-4", memory_provider="mem0")
        self.mgr.hot_reload(agent, new_config)
        assert agent.config.model_id == "claude-4"
        assert agent.config.memory_provider == "mem0"

    def test_hot_reload_rename(self):
        """热更新应支持改名"""
        agent = self.mgr.create(self._make_config("old-name"))
        new_config = self._make_config("new-name")
        self.mgr.hot_reload(agent, new_config)
        assert agent.config.name == "new-name"
        assert self.mgr.get("old-name") is None
        assert self.mgr.get("new-name") is not None

    def test_hot_reload_rename_conflict(self):
        """热更新改名冲突应拒绝"""
        self.mgr.create(self._make_config("existing"))
        agent2 = self.mgr.create(self._make_config("to-rename"))
        new_config = self._make_config("existing")  # 与 existing 冲突
        with pytest.raises(SubagentLifecycleError, match="名称已存在"):
            self.mgr.hot_reload(agent2, new_config)

    def test_list_by_tag(self):
        """应按标签过滤"""
        self.mgr.create(self._make_config("code1", tags=["code"]))
        self.mgr.create(self._make_config("chat1", tags=["chat"]))
        self.mgr.create(self._make_config("code2", tags=["code", "review"]))
        assert len(self.mgr.list_by_tag("code")) == 2
        assert len(self.mgr.list_by_tag("chat")) == 1
        assert len(self.mgr.list_by_tag("review")) == 1

    def test_list_by_permission(self):
        """应按权限过滤"""
        self.mgr.create(self._make_config("reader", permissions=["read"]))
        self.mgr.create(self._make_config("writer", permissions=["read", "write"]))
        assert len(self.mgr.list_by_permission("write")) == 1
        assert len(self.mgr.list_by_permission("read")) == 2

    def test_get_stats(self):
        """统计数据应正确"""
        self.mgr.create(self._make_config("stat1"))
        self.mgr.create(self._make_config("stat2"))
        stats = self.mgr.get_stats()
        assert stats["active_count"] == 2
        assert stats["max_subagents"] == 10
        assert stats["total_created"] == 2
        assert stats["total_destroyed"] == 0
        assert len(stats["subagents"]) == 2

    def test_stats_after_destroy(self):
        """销毁后统计数据应更新"""
        a = self.mgr.create(self._make_config("to-destroy"))
        self.mgr.destroy(a)
        stats = self.mgr.get_stats()
        assert stats["active_count"] == 0
        assert stats["total_created"] == 1
        assert stats["total_destroyed"] == 1


# ═══════════════════════════════════════════════════════════════════
#  Sandbox 沙箱测试
# ═══════════════════════════════════════════════════════════════════

class TestSubagentSandbox:
    """Sandbox 权限沙箱测试"""

    def test_read_permission_allowed(self):
        """read 权限应允许读取操作"""
        s = Sandbox(allowed_permissions={"read"})
        assert s.check_permission("read") is True

    def test_write_permission_denied(self):
        """无 write 权限时写入应拒绝"""
        s = Sandbox(allowed_permissions={"read"})
        with pytest.raises(PermissionDenied):
            s.check_permission("write")

    def test_default_permission(self):
        """默认只允许 read"""
        s = Sandbox()
        assert s.check_permission("read") is True
        with pytest.raises(PermissionDenied):
            s.check_permission("system")

    def test_permission_hierarchy_system(self):
        """system 权限应隐含所有下级权限"""
        s = Sandbox(allowed_permissions={"system"})
        assert s.check_permission("read") is True
        assert s.check_permission("write") is True
        assert s.check_permission("execute") is True
        assert s.check_permission("network") is True

    def test_permission_hierarchy_write(self):
        """write 权限应隐含 read"""
        s = Sandbox(allowed_permissions={"write"})
        assert s.check_permission("read") is True
        with pytest.raises(PermissionDenied):
            s.check_permission("execute")

    def test_permission_hierarchy_execute(self):
        """execute 权限应隐含 read"""
        s = Sandbox(allowed_permissions={"execute"})
        assert s.check_permission("read") is True
        with pytest.raises(PermissionDenied):
            s.check_permission("write")

    def test_permission_hierarchy_network(self):
        """network 权限应隐含 read"""
        s = Sandbox(allowed_permissions={"network"})
        assert s.check_permission("read") is True
        with pytest.raises(PermissionDenied):
            s.check_permission("write")

    def test_check_path_no_restriction(self):
        """无路径限制时应放行"""
        s = Sandbox(allowed_permissions={"read"})
        assert s.check_path("/any/path") is True

    def test_check_path_allowed(self):
        """路径在允许范围内应放行"""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            s = Sandbox(allowed_permissions={"read"}, allowed_paths=[td])
            assert s.check_path(td) is True

    def test_check_path_denied(self):
        """路径不在允许范围内应拒绝"""
        s = Sandbox(allowed_permissions={"read"}, allowed_paths=["/allowed"])
        with pytest.raises(PermissionDenied):
            s.check_path("/forbidden")

    def test_check_execute_default_pass(self):
        """check_execute 默认应放行"""
        s = Sandbox()
        assert s.check_execute("any task") is True

    def test_check_tool_call_read(self):
        """read 权限应允许读取工具"""
        s = Sandbox(allowed_permissions={"read"})
        assert s.check_tool_call("file_read", {}) is True
        assert s.check_tool_call("get_info", {}) is True
        assert s.check_tool_call("list_files", {}) is True

    def test_check_tool_call_write(self):
        """write 权限应允许写入工具"""
        s = Sandbox(allowed_permissions={"write"})
        assert s.check_tool_call("file_write", {}) is True
        assert s.check_tool_call("save_data", {}) is True
        assert s.check_tool_call("delete_file", {}) is True

    def test_check_tool_call_execute(self):
        """execute 权限应允许执行工具"""
        s = Sandbox(allowed_permissions={"execute"})
        assert s.check_tool_call("bash_run", {}) is True
        assert s.check_tool_call("execute_shell", {}) is True

    def test_check_tool_call_network(self):
        """network 权限应允许网络工具"""
        s = Sandbox(allowed_permissions={"network"})
        assert s.check_tool_call("http_fetch", {}) is True
        assert s.check_tool_call("web_search", {}) is True
        assert s.check_tool_call("download_file", {}) is True

    def test_check_tool_call_system(self):
        """system 权限应允许系统工具"""
        s = Sandbox(allowed_permissions={"system"})
        assert s.check_tool_call("system_config", {}) is True
        assert s.check_tool_call("admin_panel", {}) is True

    def test_check_tool_call_denied(self):
        """无权限时工具调用应拒绝"""
        s = Sandbox(allowed_permissions={"read"})
        with pytest.raises(PermissionDenied):
            s.check_tool_call("execute_shell", {})

    def test_check_tool_call_default_read(self):
        """默认工具应需要 read 权限"""
        s = Sandbox(allowed_permissions={"read"})
        assert s.check_tool_call("unknown_tool", {}) is True

    def test_docker_not_available(self):
        """Docker 沙箱应返回 None（尚未实现）"""
        s = Sandbox()
        assert s.get_docker_sandbox() is None

    def test_wasm_not_available(self):
        """WASM 沙箱应返回 None（尚未实现）"""
        s = Sandbox()
        assert s.get_wasm_sandbox() is None

    def test_get_status(self):
        """沙箱状态应包含权限和路径信息"""
        s = Sandbox(allowed_permissions={"read", "write"}, allowed_paths=["/data"])
        status = s.get_status()
        assert "read" in status["allowed_permissions"]
        assert "write" in status["allowed_permissions"]
        assert "/data" in status["allowed_paths"]
        assert status["docker_available"] is False

    def test_permission_denied_exception(self):
        """PermissionDenied 应有正确的消息"""
        e = PermissionDenied("write", "write_file")
        assert "write" in str(e)
        assert "write_file" in str(e)

    def test_permission_denied_without_operation(self):
        """PermissionDenied 可无操作描述"""
        e = PermissionDenied("execute")
        assert str(e) == "权限拒绝: execute"
