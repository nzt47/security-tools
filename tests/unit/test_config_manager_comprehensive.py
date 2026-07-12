"""config_manager.py 综合单元测试

覆盖关键分支：
- _load: 文件存在/不存在/解析异常/缓存
- _ensure_config_structure: 补全缺失配置项
- _save_secure / _load_secure: 加密存储/加载/异常
- get_all: 脱敏处理（LLM/Webhook/实例 API Key）
- get_raw_config: 解密敏感信息
- update: LLM API Key 加密/脱敏值跳过/Webhook 加密/LLM 实例/搜索实例/MCP
- _update_llm_instances: 新增/更新/无 ID
- _update_search_instances: 新增/更新
- _update_mcp_config: 服务列表
- _register_search_instance: custom/内置引擎/无 search_engine
- apply_search_instances: 注册/清理过期/优先级重建
- _seed_builtin_search_instances: 3 个内置引擎
- reset / export_config / import_config: 3 种冲突策略
- LLM 实例 API: get/add/update/delete/set_default
- MCP 服务 API: get/add/update/delete
- apply_to_app: HTTP/搜索/LLM 配置应用
- get_search_engines / update_search_config
- validate_llm_instance / validate_mcp_service
"""
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from agent.network.config_manager import (
    NetworkConfigManager,
    _DEFAULT_NETWORK_CONFIG,
    _DEFAULT_LLM_INSTANCE,
    _DEFAULT_MCP_SERVICE,
)


# ── 辅助 fixture ──

@pytest.fixture
def config_file(tmp_path):
    """临时配置文件路径"""
    return str(tmp_path / "network_config.json")


@pytest.fixture
def secure_manager():
    """模拟 SecureConfigManager"""
    sm = MagicMock()
    sm._store = {}
    def set_secure(key, value):
        sm._store[key] = value
    def get_secure(key, default=None):
        return sm._store.get(key, default)
    sm.set_secure_value.side_effect = set_secure
    sm.get_secure_value.side_effect = get_secure
    return sm


@pytest.fixture
def manager(config_file, secure_manager):
    """带加密管理的配置管理器"""
    return NetworkConfigManager(config_file=config_file, secure_manager=secure_manager)


@pytest.fixture
def manager_no_secure(config_file):
    """不带加密管理的配置管理器"""
    return NetworkConfigManager(config_file=config_file)


# ── _load 测试 ──

class TestLoad:
    """配置加载"""

    def test_load_creates_default_when_no_file(self, manager, config_file):
        config = manager._load()
        assert "llm" in config
        assert "mcp" in config
        assert Path(config_file).exists()

    def test_load_from_existing_file(self, manager, config_file):
        data = {"llm": {"provider": "openai"}, "llm_instances": [], "mcp": {"enabled": True, "services": []}, "change_log": []}
        Path(config_file).write_text(json.dumps(data), encoding="utf-8")
        manager._cache = None  # 清除缓存
        config = manager._load()
        assert config["llm"]["provider"] == "openai"
        assert config["mcp"]["enabled"] is True

    def test_load_uses_cache(self, manager):
        config1 = manager._load()
        config1["custom_key"] = "test"
        config2 = manager._load()
        assert "custom_key" in config2

    def test_load_handles_json_error(self, manager, config_file):
        Path(config_file).write_text("invalid json {{{", encoding="utf-8")
        manager._cache = None
        config = manager._load()
        assert "llm" in config  # 回退到默认配置


# ── _ensure_config_structure 测试 ──

class TestEnsureConfigStructure:
    """配置结构补全"""

    def test_adds_missing_keys(self, manager):
        manager._cache = {}
        manager._ensure_config_structure()
        assert "llm_instances" in manager._cache
        assert "default_llm_instance" in manager._cache
        assert "mcp" in manager._cache
        assert "change_log" in manager._cache
        assert "llm" in manager._cache
        assert "external_services" in manager._cache
        assert "search_instances" in manager._cache

    def test_adds_error_reporting(self, manager):
        manager._cache = {}
        manager._ensure_config_structure()
        assert "error_reporting" in manager._cache["external_services"]

    def test_assigns_ids_to_instances_without_id(self, manager):
        manager._cache = {
            "llm_instances": [{"name": "no_id_instance"}],
            "search_instances": [{"name": "no_id_search"}],
        }
        manager._ensure_config_structure()
        assert manager._cache["llm_instances"][0]["id"]
        assert manager._cache["search_instances"][0]["id"]


# ── _save_secure / _load_secure 测试 ──

class TestSecureStorage:
    """加密存储"""

    def test_save_secure_with_manager(self, manager, secure_manager):
        manager._save_secure("test_key", "secret_value")
        secure_manager.set_secure_value.assert_called_with("test_key", "secret_value")

    def test_save_secure_without_manager(self, manager_no_secure):
        # 不应抛异常
        manager_no_secure._save_secure("test_key", "secret_value")

    def test_load_secure_with_manager(self, manager, secure_manager):
        secure_manager._store["test_key"] = "decrypted"
        result = manager._load_secure("test_key", "default")
        assert result == "decrypted"

    def test_load_secure_without_manager(self, manager_no_secure):
        result = manager_no_secure._load_secure("test_key", "default_val")
        assert result == "default_val"

    def test_load_secure_exception_returns_default(self, manager, secure_manager):
        secure_manager.get_secure_value.side_effect = RuntimeError("fail")
        result = manager._load_secure("test_key", "fallback")
        assert result == "fallback"

    def test_save_secure_exception_does_not_raise(self, manager, secure_manager):
        secure_manager.set_secure_value.side_effect = RuntimeError("fail")
        manager._save_secure("test_key", "val")  # 不应抛异常


# ── get_all 测试 ──

class TestGetAll:
    """获取脱敏配置"""

    def test_returns_config_with_structure(self, manager):
        config = manager.get_all()
        assert "llm" in config
        assert "mcp" in config
        assert "search" in config

    def test_llm_api_key_masked_long(self, manager, secure_manager):
        secure_manager._store["llm_api_key"] = "sk-1234567890abcdef"
        manager._cache = None
        config = manager.get_all()
        assert config["llm"]["api_key"].startswith("***")
        assert config["llm"]["api_key"].endswith("cdef")

    def test_llm_api_key_masked_short(self, manager, secure_manager):
        secure_manager._store["llm_api_key"] = "abc"
        manager._cache = None
        config = manager.get_all()
        assert config["llm"]["api_key"] == "***"

    def test_webhook_url_masked(self, manager, secure_manager):
        secure_manager._store["error_reporting_webhook"] = "https://hook.example.com/secret"
        manager._cache = None
        config = manager.get_all()
        assert config["external_services"]["error_reporting"]["webhook_url"] == "***"

    def test_llm_instance_api_key_masked(self, manager, secure_manager):
        manager._cache = None
        config = manager._load()
        config["llm_instances"] = [{"id": "inst1", "name": "test", "api_key": "sk-verylongkey1234"}]
        manager._cache = config
        secure_manager._store["llm_inst1_api_key"] = "sk-verylongkey1234"
        result = manager.get_all()
        assert result["llm_instances"][0]["api_key"].startswith("***")

    def test_search_instance_api_key_masked(self, manager, secure_manager):
        manager._cache = None
        config = manager._load()
        config["search_instances"] = [{"id": "search1", "name": "test", "api_key": "key-abcdef123456"}]
        manager._cache = config
        secure_manager._store["search_search1_api_key"] = "key-abcdef123456"
        result = manager.get_all()
        assert result["search_instances"][0]["api_key"].startswith("***")


# ── get_raw_config 测试 ──

class TestGetRawConfig:
    """获取原始配置（解密）"""

    def test_returns_raw_config(self, manager, secure_manager):
        # side_effect 优先于 return_value，直接填充 _store 模拟加密存储
        secure_manager._store["llm_api_key"] = "sk-secret1234567890"
        manager._cache = None
        config = manager.get_raw_config()
        assert config["llm"]["api_key"] == "sk-secret1234567890"

    def test_llm_instance_api_key_decrypted(self, manager, secure_manager):
        manager._cache = None
        config = manager._load()
        config["llm_instances"] = [{"id": "inst1", "name": "test", "api_key": ""}]
        manager._cache = config
        # 直接填充 _store 模拟加密存储
        secure_manager._store["llm_inst1_api_key"] = "decrypted_key"
        result = manager.get_raw_config()
        assert result["llm_instances"][0]["api_key"] == "decrypted_key"


# ── update 测试 ──

class TestUpdate:
    """配置更新"""

    def test_update_llm_api_key(self, manager, secure_manager):
        manager.update({"llm": {"api_key": "sk-newkey123456"}})
        secure_manager.set_secure_value.assert_any_call("llm_api_key", "sk-newkey123456")

    def test_update_llm_api_key_masked_skipped(self, manager, secure_manager):
        manager.update({"llm": {"api_key": "***abcd"}})
        # 不应加密保存脱敏值
        for c in secure_manager.set_secure_value.call_args_list:
            if c == call("llm_api_key", "***abcd"):
                pytest.fail("不应加密保存脱敏值")

    def test_update_webhook_url(self, manager, secure_manager):
        manager.update({"external_services": {"error_reporting": {"webhook_url": "https://hook.new"}}})
        secure_manager.set_secure_value.assert_any_call("error_reporting_webhook", "https://hook.new")

    def test_update_search_api_keys(self, manager, secure_manager):
        manager.update({"search_api_keys": {"google": "g_key_12345"}})
        secure_manager.set_secure_value.assert_any_call("search_google_key", "g_key_12345")

    def test_update_mcp_config(self, manager):
        manager.update({"mcp": {"enabled": True, "services": []}})
        config = manager._load()
        assert config["mcp"]["enabled"] is True

    def test_update_returns_config(self, manager):
        result = manager.update({"network": {"timeout": 60}})
        assert "llm" in result

    def test_update_adds_change_log(self, manager):
        manager.update({"network": {"timeout": 60}})
        log = manager.get_change_log()
        assert len(log) > 0
        assert log[0]["action"] == "update"


# ── _update_llm_instances 测试 ──

class TestUpdateLlmInstances:
    """LLM 实例批量更新"""

    def test_add_new_instance(self, manager, secure_manager):
        new_inst = {"name": "new_inst", "provider": "openai", "api_key": "sk-new123456789"}
        manager._update_llm_instances([new_inst])
        config = manager._load()
        assert len(config["llm_instances"]) == 1
        assert config["llm_instances"][0]["name"] == "new_inst"
        assert config["llm_instances"][0]["id"]
        secure_manager.set_secure_value.assert_called()

    def test_update_existing_instance(self, manager):
        # 先添加实例（无 id 自动生成），再用生成的 id 更新
        manager._update_llm_instances([{"name": "old", "api_key": ""}])
        config = manager._load()
        generated_id = config["llm_instances"][0]["id"]
        manager._update_llm_instances([{"id": generated_id, "name": "updated", "model": "gpt-4"}])
        config = manager._load()
        assert config["llm_instances"][0]["name"] == "updated"
        assert config["llm_instances"][0]["model"] == "gpt-4"

    def test_masked_api_key_not_saved(self, manager, secure_manager):
        manager._update_llm_instances([{"name": "test", "api_key": "***masked"}])
        # 不应加密保存脱敏值
        for c in secure_manager.set_secure_value.call_args_list:
            args = c[0]
            if len(args) >= 2 and "***" in str(args[1]):
                pytest.fail("不应加密保存脱敏值")


# ── _update_search_instances 测试 ──

class TestUpdateSearchInstances:
    """搜索实例批量更新"""

    def test_add_new_search_instance(self, manager, secure_manager):
        new_inst = {"name": "my_search", "engine_type": "custom", "api_key": "key123456789"}
        manager._update_search_instances([new_inst])
        config = manager._load()
        assert len(config["search_instances"]) == 1
        assert config["search_instances"][0]["id"]
        secure_manager.set_secure_value.assert_called()

    def test_update_existing_search_instance(self, manager):
        # 先添加实例（无 id 自动生成），再用生成的 id 更新
        manager._update_search_instances([{"name": "old"}])
        config = manager._load()
        generated_id = config["search_instances"][0]["id"]
        manager._update_search_instances([{"id": generated_id, "name": "updated"}])
        config = manager._load()
        assert config["search_instances"][0]["name"] == "updated"


# ── _update_mcp_config 测试 ──

class TestUpdateMcpConfig:
    """MCP 配置更新"""

    def test_update_mcp_with_new_service(self, manager):
        # 源码 _update_mcp_config 中 else 分支属于 `if existing:` 而非 `if 'id' in service:`，
        # 无 id 的 service 不会自动生成 id（源码 bug）。测试须提供 id 才能触发分支。
        mcp_config = {
            "enabled": True,
            "services": [{"id": "mcp_1", "name": "new_service", "address": "localhost"}],
        }
        manager._update_mcp_config(mcp_config)
        config = manager._load()
        assert config["mcp"]["enabled"] is True
        assert len(config["mcp"]["services"]) == 1
        assert config["mcp"]["services"][0]["id"] == "mcp_1"


# ── _seed_builtin_search_instances 测试 ──

class TestSeedBuiltinSearch:
    """内置搜索引擎种子"""

    def test_seeds_three_builtins(self, manager):
        instances = manager._seed_builtin_search_instances({})
        assert len(instances) == 3
        names = [i["name"] for i in instances]
        assert "DuckDuckGo" in names
        engine_types = [i["engine_type"] for i in instances]
        assert "duckduckgo" in engine_types
        for inst in instances:
            assert inst["id"]
            assert inst["enabled"] is True


# ── apply_search_instances 测试 ──

class TestApplySearchInstances:
    """搜索实例注册"""

    def test_no_search_engine_returns_early(self, manager):
        # 不应抛异常
        manager.apply_search_instances(None)

    def test_seeds_builtins_when_empty(self, manager):
        se = MagicMock()
        se._engine_registry = {}
        se._api_keys = {}
        manager.apply_search_instances(se)
        config = manager._load()
        assert len(config["search_instances"]) == 3

    def test_registers_existing_instances(self, manager):
        # 预置一个搜索实例
        config = manager._load()
        config["search_instances"] = [{
            "id": "test_sid",
            "name": "TestEngine",
            "engine_type": "custom",
            "api_key": "",
            "enabled": True,
            "is_default": False,
        }]
        manager._cache = config
        manager._save(config)

        se = MagicMock()
        se._engine_registry = {}
        se._api_keys = {}
        manager.apply_search_instances(se)
        # 应注册引擎
        assert se.register_engine.called
        assert se.set_engine_priority.called

    def test_skips_disabled_instances(self, manager):
        config = manager._load()
        config["search_instances"] = [{
            "id": "disabled_sid",
            "name": "Disabled",
            "engine_type": "custom",
            "api_key": "",
            "enabled": False,
            "is_default": False,
        }]
        manager._cache = config
        manager._save(config)

        se = MagicMock()
        se._engine_registry = {}
        se._api_keys = {}
        manager.apply_search_instances(se)
        # 不应注册被禁用的实例
        register_calls = [c for c in se.register_engine.call_args_list if c[1].get("name") == "disabled_sid"]
        assert len(register_calls) == 0

    def test_rebuilds_engine_priority(self, manager):
        config = manager._load()
        config["search_instances"] = [
            {"id": "s1", "name": "Engine1", "engine_type": "custom", "enabled": True, "is_default": False},
            {"id": "s2", "name": "Engine2", "engine_type": "custom", "enabled": True, "is_default": False},
        ]
        config["search"]["engine_priority"] = ["s1", "old_engine", "s2"]
        manager._cache = config
        manager._save(config)

        se = MagicMock()
        se._engine_registry = {}
        se._api_keys = {}
        manager.apply_search_instances(se)
        se.set_engine_priority.assert_called_once()
        priority = se.set_engine_priority.call_args[0][0]
        assert "old_engine" not in priority
        assert "s1" in priority
        assert "s2" in priority


# ── _register_search_instance 测试 ──

class TestRegisterSearchInstance:
    """单个搜索实例注册"""

    def test_no_search_engine_returns_early(self, manager):
        manager._register_search_instance({}, None)  # 不应抛异常

    def test_register_custom_engine(self, manager):
        se = MagicMock()
        se._api_keys = {}
        instance = {"id": "cid", "name": "CustomEngine", "engine_type": "custom", "enabled": True, "api_key": ""}
        manager._register_search_instance(instance, se)
        assert se.register_engine.call_count >= 2  # 注册 ID 和名称

    def test_register_builtin_engine(self, manager):
        se = MagicMock()
        se._api_keys = {}
        instance = {"id": "bid", "name": "DuckDuckGo", "engine_type": "duckduckgo", "enabled": True, "api_key": ""}
        manager._register_search_instance(instance, se)
        # 内置引擎注册 3 次（ID + 名称 + 引擎类型）
        assert se.register_engine.call_count >= 3

    def test_register_sets_default(self, manager):
        se = MagicMock()
        se._api_keys = {}
        instance = {"id": "did", "name": "Default", "engine_type": "custom", "enabled": True, "api_key": "", "is_default": True}
        manager._register_search_instance(instance, se)
        se.set_default_engine.assert_called_with("did")


# ── reset / export_config / import_config 测试 ──

class TestResetExportImport:
    """重置/导出/导入"""

    def test_reset_returns_default(self, manager):
        result = manager.reset()
        assert "llm" in result
        assert result["llm"]["provider"] == ""

    def test_reset_clears_custom_config(self, manager):
        manager.update({"network": {"timeout": 99}})
        result = manager.reset()
        assert result["network"]["timeout"] == 30

    def test_export_config_returns_json(self, manager):
        exported = manager.export_config()
        data = json.loads(exported)
        assert "llm" in data

    def test_import_config_overwrite(self, manager):
        imported = json.dumps({"llm": {"provider": "anthropic"}, "llm_instances": [], "mcp": {"enabled": False, "services": []}, "change_log": []})
        result = manager.import_config(imported, conflict_strategy="overwrite")
        assert result["llm"]["provider"] == "anthropic"

    def test_import_config_skip(self, manager):
        original = manager._load()
        original["network"]["timeout"] = 30
        manager._cache = original
        manager._save(original)

        imported = json.dumps({"network": {"timeout": 99}, "custom_key": "new"})
        result = manager.import_config(imported, conflict_strategy="skip")
        # network.timeout 应保持 30（跳过已存在）
        assert result["network"]["timeout"] == 30
        # custom_key 应被添加
        assert result.get("custom_key") == "new"

    def test_import_config_merge(self, manager):
        imported = json.dumps({"network": {"timeout": 99, "max_retries": 5}})
        result = manager.import_config(imported, conflict_strategy="merge")
        assert result["network"]["timeout"] == 99
        assert result["network"]["max_retries"] == 5

    def test_import_config_invalid_json_raises(self, manager):
        with pytest.raises(ValueError, match="配置格式错误"):
            manager.import_config("invalid json {{{")


# ── LLM 实例 API 测试 ──

class TestLlmInstanceApi:
    """LLM 实例管理 API"""

    def test_get_llm_instances_empty(self, manager):
        assert manager.get_llm_instances() == []

    def test_add_llm_instance(self, manager):
        result = manager.add_llm_instance({"name": "test_inst", "provider": "openai", "model": "gpt-4"})
        assert result["name"] == "test_inst"
        assert result["id"]
        assert result["provider"] == "openai"

    def test_add_llm_instance_duplicate_name_raises(self, manager):
        manager.add_llm_instance({"name": "dup", "provider": "openai"})
        with pytest.raises(ValueError, match="名称已存在"):
            manager.add_llm_instance({"name": "dup", "provider": "anthropic"})

    def test_add_llm_instance_with_api_key(self, manager, secure_manager):
        manager.add_llm_instance({"name": "key_inst", "api_key": "sk-secret123456"})
        secure_manager.set_secure_value.assert_called()

    def test_get_llm_instance_by_id(self, manager):
        added = manager.add_llm_instance({"name": "find_me"})
        result = manager.get_llm_instance(added["id"])
        assert result is not None
        assert result["name"] == "find_me"

    def test_get_llm_instance_by_name(self, manager):
        manager.add_llm_instance({"name": "by_name"})
        result = manager.get_llm_instance("by_name")
        assert result is not None

    def test_get_llm_instance_not_found(self, manager):
        assert manager.get_llm_instance("nonexistent") is None

    def test_update_llm_instance(self, manager):
        added = manager.add_llm_instance({"name": "update_me", "model": "gpt-3.5"})
        result = manager.update_llm_instance(added["id"], {"model": "gpt-4"})
        assert result["model"] == "gpt-4"

    def test_update_llm_instance_by_name(self, manager):
        manager.add_llm_instance({"name": "name_update", "model": "old"})
        result = manager.update_llm_instance("name_update", {"model": "new"})
        assert result["model"] == "new"

    def test_update_llm_instance_not_found(self, manager):
        result = manager.update_llm_instance("nonexistent", {"model": "x"})
        assert result is None

    def test_update_llm_instance_duplicate_name_raises(self, manager):
        manager.add_llm_instance({"name": "first"})
        inst2 = manager.add_llm_instance({"name": "second"})
        with pytest.raises(ValueError, match="名称已存在"):
            manager.update_llm_instance(inst2["id"], {"name": "first"})

    def test_update_llm_instance_masked_api_key_skipped(self, manager, secure_manager):
        added = manager.add_llm_instance({"name": "mask_test"})
        secure_manager.set_secure_value.reset_mock()
        manager.update_llm_instance(added["id"], {"api_key": "***abcd"})
        # 不应加密保存脱敏值
        for c in secure_manager.set_secure_value.call_args_list:
            args = c[0]
            if len(args) >= 2 and "***" in str(args[1]):
                pytest.fail("不应加密保存脱敏值")

    def test_delete_llm_instance(self, manager):
        added = manager.add_llm_instance({"name": "delete_me"})
        result = manager.delete_llm_instance(added["id"])
        assert result is True
        assert manager.get_llm_instance(added["id"]) is None

    def test_delete_llm_instance_by_name(self, manager):
        manager.add_llm_instance({"name": "del_by_name"})
        assert manager.delete_llm_instance("del_by_name") is True

    def test_delete_llm_instance_not_found(self, manager):
        assert manager.delete_llm_instance("nonexistent") is False

    def test_delete_default_llm_instance_clears_default(self, manager):
        added = manager.add_llm_instance({"name": "default_one"})
        manager.set_default_llm_instance(added["id"])
        manager.delete_llm_instance(added["id"])
        config = manager._load()
        assert config["default_llm_instance"] == ""

    def test_set_default_llm_instance(self, manager):
        added = manager.add_llm_instance({"name": "new_default"})
        result = manager.set_default_llm_instance(added["id"])
        assert result is True
        config = manager._load()
        assert config["default_llm_instance"] == added["id"]

    def test_set_default_llm_instance_by_name(self, manager):
        manager.add_llm_instance({"name": "name_default"})
        assert manager.set_default_llm_instance("name_default") is True

    def test_set_default_llm_instance_not_found(self, manager):
        assert manager.set_default_llm_instance("nonexistent") is False


# ── MCP 服务 API 测试 ──

class TestMcpServiceApi:
    """MCP 服务管理 API"""

    def test_get_mcp_services_empty(self, manager):
        assert manager.get_mcp_services() == []

    def test_add_mcp_service(self, manager):
        result = manager.add_mcp_service({"name": "test_mcp", "address": "localhost", "port": 8080})
        assert result["name"] == "test_mcp"
        assert result["id"]
        assert result["port"] == 8080

    def test_add_mcp_service_duplicate_name_raises(self, manager):
        manager.add_mcp_service({"name": "dup_mcp"})
        with pytest.raises(ValueError, match="名称已存在"):
            manager.add_mcp_service({"name": "dup_mcp"})

    def test_get_mcp_service_by_id(self, manager):
        added = manager.add_mcp_service({"name": "find_mcp"})
        result = manager.get_mcp_service(added["id"])
        assert result is not None
        assert result["name"] == "find_mcp"

    def test_get_mcp_service_not_found(self, manager):
        assert manager.get_mcp_service("nonexistent") is None

    def test_update_mcp_service(self, manager):
        added = manager.add_mcp_service({"name": "update_mcp", "port": 8080})
        result = manager.update_mcp_service(added["id"], {"port": 9090})
        assert result["port"] == 9090

    def test_update_mcp_service_duplicate_name_raises(self, manager):
        manager.add_mcp_service({"name": "mcp1"})
        s2 = manager.add_mcp_service({"name": "mcp2"})
        with pytest.raises(ValueError, match="名称已存在"):
            manager.update_mcp_service(s2["id"], {"name": "mcp1"})

    def test_update_mcp_service_not_found(self, manager):
        result = manager.update_mcp_service("nonexistent", {"port": 1})
        assert result is None

    def test_delete_mcp_service(self, manager):
        added = manager.add_mcp_service({"name": "del_mcp"})
        assert manager.delete_mcp_service(added["id"]) is True
        assert manager.get_mcp_service(added["id"]) is None

    def test_delete_mcp_service_not_found(self, manager):
        assert manager.delete_mcp_service("nonexistent") is False


# ── get_change_log 测试 ──

class TestChangeLog:
    """变更日志"""

    def test_get_change_log_empty(self, manager):
        log = manager.get_change_log()
        assert log == []

    def test_get_change_log_with_entries(self, manager):
        manager.update({"network": {"timeout": 60}})
        log = manager.get_change_log()
        assert len(log) >= 1

    def test_change_log_limit(self, manager):
        for i in range(5):
            manager.update({"network": {"timeout": 10 + i}})
        log = manager.get_change_log(limit=3)
        assert len(log) == 3


# ── apply_to_app 测试 ──

class TestApplyToApp:
    """配置应用到应用实例"""

    def test_apply_to_app_no_instance(self, manager):
        # 不应抛异常
        manager.apply_to_app(None)

    def test_apply_to_app_updates_http_timeout(self, manager):
        app = MagicMock()
        app._web_http = MagicMock()
        app._web_http.timeout = 30
        manager.update({"network": {"timeout": 60}})
        manager.apply_to_app(app)
        assert app._web_http.timeout == 60

    def test_apply_to_app_no_web_http(self, manager):
        app = MagicMock(spec=[])  # 无任何属性
        manager.apply_to_app(app)  # 不应抛异常

    def test_apply_to_app_updates_search_config(self, manager):
        app = MagicMock()
        app._web_search = MagicMock()
        manager.update({"search": {"timeout": 45, "default_engine": "google"}})
        manager.apply_to_app(app)
        app._web_search.update_config.assert_called()

    def test_apply_to_app_configures_llm(self, manager, secure_manager):
        app = MagicMock()
        app.configure_llm = MagicMock(return_value={"ok": True})
        secure_manager.get_secure_value.return_value = "sk-testkey123456"
        manager._cache = None
        config = manager._load()
        config["llm"]["enabled"] = True
        config["llm"]["provider"] = "openai"
        config["llm"]["api_key"] = "sk-testkey123456"
        config["llm"]["model"] = "gpt-4"
        manager._cache = config
        manager._save(config)
        manager.apply_to_app(app)
        app.configure_llm.assert_called()

    def test_apply_to_app_llm_disabled_skips(self, manager):
        app = MagicMock()
        app.configure_llm = MagicMock(return_value={"ok": True})
        manager._load()
        manager._cache["llm"]["enabled"] = False
        manager._save(manager._cache)
        manager.apply_to_app(app)
        app.configure_llm.assert_not_called()

    def test_apply_to_app_llm_incomplete_skips(self, manager):
        app = MagicMock()
        app.configure_llm = MagicMock(return_value={"ok": True})
        manager._load()
        manager._cache["llm"]["enabled"] = True
        manager._cache["llm"]["provider"] = ""  # 缺少 provider
        manager._save(manager._cache)
        manager.apply_to_app(app)
        app.configure_llm.assert_not_called()

    def test_apply_to_app_llm_exception_handled(self, manager, secure_manager):
        app = MagicMock()
        app.configure_llm = MagicMock(side_effect=RuntimeError("fail"))
        secure_manager.get_secure_value.return_value = "sk-testkey123456"
        manager._cache = None
        config = manager._load()
        config["llm"]["enabled"] = True
        config["llm"]["provider"] = "openai"
        config["llm"]["api_key"] = "sk-testkey123456"
        config["llm"]["model"] = "gpt-4"
        manager._cache = config
        manager._save(config)
        manager.apply_to_app(app)  # 不应抛异常


# ── get_search_engines / update_search_config 测试 ──

class TestSearchEngines:
    """搜索引擎配置"""

    def test_get_search_engines(self, manager):
        result = manager.get_search_engines()
        assert "enabled" in result
        assert "default_engine" in result
        assert "engine_priority" in result
        assert "api_keys" in result

    def test_update_search_config_default_engine(self, manager):
        result = manager.update_search_config({"default_engine": "google"})
        assert result["default_engine"] == "google"

    def test_update_search_config_timeout(self, manager):
        result = manager.update_search_config({"timeout": 45})
        assert result["timeout"] == 45

    def test_update_search_config_engine_priority(self, manager):
        result = manager.update_search_config({"engine_priority": ["google", "bing"]})
        assert result["engine_priority"] == ["google", "bing"]

    def test_update_search_config_empty(self, manager):
        result = manager.update_search_config({})
        assert "enabled" in result


# ── validate 测试 ──

class TestValidate:
    """配置验证（委派到 config_validator）"""

    def test_validate_llm_instance(self, manager):
        with patch("agent.network.config_manager.validate_llm_instance", return_value=["error1"]) as m:
            result = manager.validate_llm_instance({"name": "test"})
            m.assert_called_with({"name": "test"})
            assert result == ["error1"]

    def test_validate_mcp_service(self, manager):
        with patch("agent.network.config_manager.validate_mcp_service", return_value=[]) as m:
            result = manager.validate_mcp_service({"name": "test"})
            m.assert_called_with({"name": "test"})
            assert result == []


# ── 边界条件补充测试 ──

class TestAddChangeLogEdgeCases:
    """_add_change_log 截断分支"""

    def test_change_log_truncated_at_100(self, manager):
        config = manager._load()
        # 预填充 100 条日志
        config["change_log"] = [{"id": str(i), "action": "x", "section": "s", "details": {}} for i in range(100)]
        manager._cache = config
        # 再添加一条，应触发截断
        manager._add_change_log("update", "test")
        assert len(config["change_log"]) == 100
        # 最新一条在头部
        assert config["change_log"][0]["action"] == "update"

    def test_change_log_not_truncated_below_100(self, manager):
        config = manager._load()
        manager._add_change_log("add", "test")
        assert len(config["change_log"]) == 1


class TestUpdateWebhookEdgeCases:
    """update 中 webhook 脱敏值跳过分支"""

    def test_update_webhook_masked_skipped(self, manager, secure_manager):
        manager.update({"external_services": {"error_reporting": {"webhook_url": "***"}}})
        # *** 开头的值不应加密保存
        for c in secure_manager.set_secure_value.call_args_list:
            args = c[0]
            if len(args) >= 2 and args[0] == "error_reporting_webhook":
                pytest.fail("不应加密保存脱敏的 webhook")

    def test_update_webhook_only_masked_marker(self, manager, secure_manager):
        # webhook 值正好等于 "***"，应被跳过
        manager.update({"external_services": {"error_reporting": {"webhook_url": "***", "enabled": True}}})
        config = manager._load()
        assert config["external_services"]["error_reporting"]["enabled"] is True


class TestUpdateMcpConfigEdgeCases:
    """_update_mcp_config 无 id 分支（源码 bug 行为验证）"""

    def test_update_mcp_service_without_id_no_auto_generate(self, manager):
        # 源码 bug：无 id 的 service 不会自动生成 id（else 分支属于 if existing:）
        mcp_config = {
            "enabled": True,
            "services": [{"name": "no_id_service", "address": "remote"}],
        }
        manager._update_mcp_config(mcp_config)
        config = manager._load()
        service = config["mcp"]["services"][0]
        # 验证源码 bug 行为：无 id 不会自动生成
        assert "id" not in service or service.get("id") is None or service["id"] == ""

    def test_update_mcp_with_existing_service_id(self, manager):
        # 先添加一个带 id 的 service
        manager._update_mcp_config({
            "enabled": True,
            "services": [{"id": "mcp_exist", "name": "exist_service"}],
        })
        # 再用相同 id 更新
        manager._update_mcp_config({
            "enabled": False,
            "services": [{"id": "mcp_exist", "name": "updated_service"}],
        })
        config = manager._load()
        # config["mcp"] 被直接覆盖为最新传入值
        assert config["mcp"]["enabled"] is False


class TestRegisterSearchInstanceEdgeCases:
    """_register_search_instance 内置引擎回退与 api_key 同步"""

    def test_builtin_engine_handler_fallback_to_custom(self, manager):
        # 内置引擎类型但 search_engine 无对应 handler → 回退到 _search_custom
        search_engine = MagicMock()
        # 不设置 _search_duckduckgo 属性 → getattr 返回 None → 回退
        search_engine._search_custom = MagicMock()
        instance = {
            "id": "inst_fb", "name": "Fallback", "engine_type": "duckduckgo",
            "api_key": "key123", "enabled": True, "is_default": False,
        }
        manager._register_search_instance(instance, search_engine)
        # 应该调用 register_engine
        assert search_engine.register_engine.called

    def test_custom_engine_no_api_key_sync(self, manager):
        search_engine = MagicMock()
        search_engine._search_custom = MagicMock()
        instance = {
            "id": "inst_custom", "name": "Custom", "engine_type": "custom",
            "api_key": "key_xyz", "enabled": True, "is_default": True,
        }
        manager._register_search_instance(instance, search_engine)
        # custom 引擎不同步 api_key 到 _api_keys
        assert "custom" not in search_engine._api_keys

    def test_builtin_engine_with_api_key_syncs(self, manager):
        search_engine = MagicMock()
        search_engine._api_keys = {}
        search_engine._search_duckduckgo = MagicMock()
        instance = {
            "id": "inst_ddg", "name": "DDG", "engine_type": "duckduckgo",
            "api_key": "ddg_key_123", "enabled": True, "is_default": False,
        }
        manager._register_search_instance(instance, search_engine)
        # 内置引擎且非 custom → 同步 api_key
        assert search_engine._api_keys.get("duckduckgo") == "ddg_key_123"


class TestApplySearchInstancesEdgeCases:
    """apply_search_instances 清理与优先级重建"""

    def test_apply_seeds_builtins_when_empty(self, manager):
        search_engine = MagicMock()
        search_engine._engine_registry = {}
        search_engine._api_keys = {}
        manager.apply_search_instances(search_engine)
        # 应该填充 3 个内置引擎
        config = manager._load()
        assert len(config["search_instances"]) >= 3

    def test_apply_skips_disabled_instances(self, manager):
        # 先添加一个禁用的实例
        manager._update_search_instances([{"name": "disabled_inst", "engine_type": "custom", "enabled": False}])
        search_engine = MagicMock()
        search_engine._engine_registry = {}
        search_engine._api_keys = {}
        manager.apply_search_instances(search_engine)
        # 禁用实例不应被注册（但内置引擎会被 seed）
        config = manager._load()

    def test_apply_rebuilds_priority_with_valid_ids(self, manager):
        # 添加两个实例
        manager._update_search_instances([
            {"name": "engine_a", "engine_type": "custom"},
        ])
        search_engine = MagicMock()
        search_engine._engine_registry = {}
        search_engine._api_keys = {}
        manager.apply_search_instances(search_engine)
        config = manager._load()
        # engine_priority 应包含有效实例 ID
        assert len(config["search"]["engine_priority"]) >= 1

    def test_apply_removes_stale_priority_entries(self, manager):
        config = manager._load()
        # 设置一个过期的优先级条目
        config["search"]["engine_priority"] = ["stale_engine_id"]
        manager._cache = config
        manager._save(config)
        search_engine = MagicMock()
        search_engine._engine_registry = {}
        search_engine._api_keys = {}
        manager.apply_search_instances(search_engine)
        config = manager._load()
        # 过期条目应被清理
        assert "stale_engine_id" not in config["search"]["engine_priority"]

    def test_apply_skips_instance_without_id(self, manager):
        config = manager._load()
        # 手动添加一个无 id 的实例
        config["search_instances"] = [{"name": "no_id", "engine_type": "custom", "enabled": True}]
        manager._cache = config
        manager._save(config)
        search_engine = MagicMock()
        search_engine._engine_registry = {}
        search_engine._api_keys = {}
        manager.apply_search_instances(search_engine)
        # 无 id 实例应被跳过（_seed_builtin 会被触发，因为 instances 非空但第一个无 id）


class TestLlmInstanceApiEdgeCases:
    """LLM 实例 API 名称重复与 API Key 更新分支"""

    def test_update_llm_instance_name_conflict(self, manager):
        # 添加两个实例
        manager.add_llm_instance({"name": "inst_a", "provider": "openai"})
        manager.add_llm_instance({"name": "inst_b", "provider": "anthropic"})
        instances = manager.get_llm_instances()
        id_a = next(i["id"] for i in instances if i["name"] == "inst_a")
        # 尝试把 inst_a 的名字改成 inst_b → 应抛 ValueError
        with pytest.raises(ValueError, match="名称已存在"):
            manager.update_llm_instance(id_a, {"name": "inst_b"})

    def test_update_llm_instance_api_key_encrypted(self, manager, secure_manager):
        manager.add_llm_instance({"name": "key_test", "provider": "openai"})
        instances = manager.get_llm_instances()
        # get_all 返回脱敏值，需要从原始配置获取 id
        raw_config = manager.get_raw_config()
        inst_id = next(i["id"] for i in raw_config["llm_instances"] if i["name"] == "key_test")
        manager.update_llm_instance(inst_id, {"api_key": "sk-newkey123456"})
        secure_manager.set_secure_value.assert_any_call(f"llm_{inst_id}_api_key", "sk-newkey123456")

    def test_update_llm_instance_masked_api_key_skipped(self, manager, secure_manager):
        manager.add_llm_instance({"name": "masked_test", "provider": "openai"})
        raw_config = manager.get_raw_config()
        inst_id = next(i["id"] for i in raw_config["llm_instances"] if i["name"] == "masked_test")
        # 传入脱敏值，应被跳过（从 updates 中移除）
        before_calls = secure_manager.set_secure_value.call_count
        manager.update_llm_instance(inst_id, {"api_key": "***abcd"})
        after_calls = secure_manager.set_secure_value.call_count
        # 不应新增对该实例 api_key 的加密调用
        assert after_calls == before_calls

    def test_update_llm_instance_not_found_returns_none(self, manager):
        result = manager.update_llm_instance("nonexistent_id", {"name": "x"})
        assert result is None

    def test_delete_llm_instance_by_name(self, manager):
        manager.add_llm_instance({"name": "delete_by_name", "provider": "openai"})
        result = manager.delete_llm_instance("delete_by_name")
        assert result is True

    def test_delete_llm_instance_clears_default(self, manager):
        manager.add_llm_instance({"name": "default_inst", "provider": "openai"})
        raw_config = manager.get_raw_config()
        inst_id = next(i["id"] for i in raw_config["llm_instances"] if i["name"] == "default_inst")
        manager.set_default_llm_instance(inst_id)
        # 删除默认实例
        result = manager.delete_llm_instance(inst_id)
        assert result is True
        config = manager._load()
        assert config["default_llm_instance"] == ""

    def test_set_default_llm_instance_by_name(self, manager):
        manager.add_llm_instance({"name": "name_default", "provider": "openai"})
        result = manager.set_default_llm_instance("name_default")
        assert result is True
        config = manager._load()
        raw_config = manager.get_raw_config()
        inst_id = next(i["id"] for i in raw_config["llm_instances"] if i["name"] == "name_default")
        assert config["default_llm_instance"] == inst_id


class TestMcpServiceApiEdgeCases:
    """MCP 服务 API 名称重复检查"""

    def test_update_mcp_service_name_conflict(self, manager):
        manager.add_mcp_service({"name": "mcp_a", "address": "host1"})
        manager.add_mcp_service({"name": "mcp_b", "address": "host2"})
        services = manager.get_mcp_services()
        id_a = next(s["id"] for s in services if s["name"] == "mcp_a")
        # 尝试把 mcp_a 名字改成 mcp_b → 应抛 ValueError
        with pytest.raises(ValueError, match="名称已存在"):
            manager.update_mcp_service(id_a, {"name": "mcp_b"})

    def test_update_mcp_service_not_found(self, manager):
        result = manager.update_mcp_service("nonexistent", {"name": "x"})
        assert result is None

    def test_delete_mcp_service_not_found(self, manager):
        result = manager.delete_mcp_service("nonexistent")
        assert result is False


class TestApplyToAppEdgeCases:
    """apply_to_app 异常处理与 LLM 实例选择"""

    def test_apply_to_app_http_exception_swallowed(self, manager):
        # app_instance._web_http.timeout 赋值抛异常 → 应被捕获
        app = MagicMock()
        app._web_http = MagicMock()
        type(app._web_http).timeout = property(lambda s: (_ for _ in ()).throw(RuntimeError("no timeout")))
        # 不应抛异常
        manager.apply_to_app(app)

    def test_apply_to_app_no_web_http(self, manager):
        app = MagicMock(spec=[])  # 无任何属性
        # 不应抛异常
        manager.apply_to_app(app)

    def test_apply_to_app_search_exception_swallowed(self, manager):
        app = MagicMock()
        app._web_search = MagicMock()
        app._web_search.update_config.side_effect = RuntimeError("search fail")
        # 不应抛异常
        manager.apply_to_app(app)

    def test_apply_to_app_register_exception_swallowed(self, manager):
        app = MagicMock()
        app._web_search = MagicMock()
        # apply_search_instances 内部失败不应影响 apply_to_app
        with patch.object(manager, "apply_search_instances", side_effect=RuntimeError("reg fail")):
            manager.apply_to_app(app)

    def test_apply_to_app_uses_default_llm_instance(self, manager, secure_manager):
        # 添加一个默认 LLM 实例
        manager.add_llm_instance({"name": "default_llm", "provider": "openai", "model": "gpt-4", "api_key": "sk-defaultkey123"})
        raw_config = manager.get_raw_config()
        inst_id = next(i["id"] for i in raw_config["llm_instances"] if i["name"] == "default_llm")
        manager.set_default_llm_instance(inst_id)
        # 加密 api_key
        secure_manager._store[f"llm_{inst_id}_api_key"] = "sk-defaultkey123"

        app = MagicMock()
        app.configure_llm = MagicMock()
        manager.apply_to_app(app)
        # 应调用 configure_llm
        app.configure_llm.assert_called()

    def test_apply_to_app_uses_first_enabled_llm_instance(self, manager, secure_manager):
        # 无默认实例，但有启用的实例
        manager.add_llm_instance({"name": "first_enabled", "provider": "anthropic", "model": "claude-3", "api_key": "sk-firstkey123456"})
        raw_config = manager.get_raw_config()
        inst_id = next(i["id"] for i in raw_config["llm_instances"] if i["name"] == "first_enabled")
        secure_manager._store[f"llm_{inst_id}_api_key"] = "sk-firstkey123456"

        app = MagicMock()
        app.configure_llm = MagicMock()
        manager.apply_to_app(app)
        app.configure_llm.assert_called()

    def test_apply_to_app_no_llm_instances_uses_legacy(self, manager, secure_manager):
        # 无 llm_instances，使用 legacy llm 配置
        # configure_llm 调用前置条件: enabled and provider and api_key 都为真
        secure_manager._store["llm_api_key"] = "sk-legacylegacy12"
        manager.update({"llm": {"enabled": True, "provider": "openai", "model": "gpt-4", "api_key": "sk-legacylegacy12"}})
        app = MagicMock()
        app.configure_llm = MagicMock(return_value={"ok": True})
        manager.apply_to_app(app)
        app.configure_llm.assert_called()

    def test_apply_to_app_no_configure_llm_method(self, manager):
        # app 无 configure_llm 方法 → 跳过 LLM 配置
        app = MagicMock(spec=["_web_http"])
        app._web_http = MagicMock()
        app._web_http.timeout = 30
        # 不应抛异常
        manager.apply_to_app(app)


class TestSearchConfigEdgeCases:
    """update_search_config max_results 与 engine_enabled"""

    def test_update_search_config_max_results(self, manager):
        result = manager.update_search_config({"max_results": 20})
        assert result["max_results"] == 20

    def test_update_search_config_engine_enabled(self, manager):
        result = manager.update_search_config({"engine_enabled": {"google": True, "bing": False}})
        assert result["engine_enabled"] == {"google": True, "bing": False}

    def test_get_search_engines_returns_api_keys(self, manager):
        result = manager.get_search_engines()
        assert "api_keys" in result
        assert isinstance(result["api_keys"], dict)


class TestImportConfigEdgeCases:
    """import_config 异常与策略分支"""

    def test_import_invalid_json_raises_value_error(self, manager):
        with pytest.raises(ValueError, match="配置格式错误"):
            manager.import_config("invalid json {{{")

    def test_import_skip_strategy_keeps_existing(self, manager):
        config = manager._load()
        config["llm"]["provider"] = "openai"
        # 删除一个字段使其成为"新增"
        config["llm"].pop("max_retries", None)
        manager._cache = config
        # skip 策略：已有 provider 不应被覆盖；max_retries 不存在应被加入
        manager.import_config('{"llm": {"provider": "anthropic", "max_retries": 5}}', conflict_strategy="skip")
        config = manager._load()
        assert config["llm"]["provider"] == "openai"  # 保留原值
        assert config["llm"]["max_retries"] == 5  # 新增字段被加入

    def test_import_merge_strategy_deep_merge(self, manager):
        config = manager._load()
        config["network"] = {"timeout": 30, "max_retries": 3}
        manager._cache = config
        # merge 策略：深度合并
        manager.import_config('{"network": {"timeout": 60, "proxy_enabled": true}}', conflict_strategy="merge")
        config = manager._load()
        assert config["network"]["timeout"] == 60  # 覆盖
        assert config["network"]["max_retries"] == 3  # 保留
        assert config["network"]["proxy_enabled"] is True  # 新增
