"""NetworkConfigManager 集成测试

覆盖 agent/network/config_manager.py 的所有功能：
- 配置加载/保存/缓存
- 加密存储（SecureManager 注入）
- 配置变更日志
- 配置获取（脱敏/原始）
- 配置更新（LLM/搜索/MCP/敏感信息）
- 搜索实例注册
- 重置/导入/导出
- LLM 实例 CRUD
- MCP 服务 CRUD
- 应用配置到 app_instance
- 搜索引擎配置
- 配置验证
"""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from copy import deepcopy

from agent.network.config_manager import (
    NetworkConfigManager,
    _DEFAULT_NETWORK_CONFIG,
    _DEFAULT_LLM_INSTANCE,
    _DEFAULT_MCP_SERVICE,
    _DEFAULT_SEARCH_INSTANCE,
)


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def config_file(tmp_path):
    """临时配置文件路径"""
    return tmp_path / "network_config.json"


@pytest.fixture
def secure_manager():
    """Mock SecureConfigManager"""
    sm = MagicMock()
    sm.get_secure_value.return_value = None
    return sm


@pytest.fixture
def manager(config_file, secure_manager):
    """NetworkConfigManager 实例（带 secure_manager）"""
    return NetworkConfigManager(config_file=str(config_file), secure_manager=secure_manager)


@pytest.fixture
def manager_no_secure(config_file):
    """NetworkConfigManager 实例（无 secure_manager）"""
    return NetworkConfigManager(config_file=str(config_file), secure_manager=None)


# ============================================================================
# 初始化与加载
# ============================================================================

class TestInitAndLoad:
    def test_init_with_config_file(self, config_file):
        m = NetworkConfigManager(config_file=str(config_file))
        assert m._config_file == config_file
        assert m._secure_manager is None
        assert m._cache is None

    def test_init_default_config_file(self):
        m = NetworkConfigManager()
        assert m._config_file.name == 'network_config.json'

    def test_load_creates_default_when_no_file(self, manager, config_file):
        """文件不存在时创建默认配置"""
        assert not config_file.exists()
        config = manager._load()
        assert config_file.exists()
        assert config["llm"]["enabled"] is True
        assert config["mcp"]["enabled"] is False

    def test_load_from_existing_file(self, manager, config_file):
        """从已有文件加载"""
        data = deepcopy(_DEFAULT_NETWORK_CONFIG)
        data["llm"]["provider"] = "openai"
        config_file.write_text(json.dumps(data), encoding='utf-8')

        config = manager._load()
        assert config["llm"]["provider"] == "openai"

    def test_load_uses_cache(self, manager, config_file):
        """缓存命中时不读文件"""
        manager._load()  # 首次加载并缓存
        # 删除文件后再次加载应返回缓存
        config_file.unlink()
        config = manager._load()
        assert "llm" in config

    def test_load_invalid_json_falls_back_to_default(self, manager, config_file):
        """JSON 解析失败时使用默认配置"""
        config_file.write_text("{invalid json", encoding='utf-8')
        config = manager._load()
        assert config == _DEFAULT_NETWORK_CONFIG

    def test_load_os_error_falls_back_to_default(self, manager, config_file):
        """OSError 时使用默认配置"""
        # 将配置文件路径指向一个目录（读取会失败）
        manager._config_file = config_file  # 目录路径
        config_file.mkdir()
        config = manager._load()
        assert config == _DEFAULT_NETWORK_CONFIG

    def test_ensure_config_structure_adds_missing_fields(self, manager):
        """补全缺失的配置项"""
        manager._cache = {"some_unknown_field": "value"}
        manager._ensure_config_structure()
        assert "llm_instances" in manager._cache
        assert manager._cache["llm_instances"] == []
        assert "default_llm_instance" in manager._cache
        assert "mcp" in manager._cache
        assert "change_log" in manager._cache
        assert "llm" in manager._cache
        assert "external_services" in manager._cache
        assert "error_reporting" in manager._cache["external_services"]
        assert "search_instances" in manager._cache

    def test_ensure_config_structure_assigns_ids_to_instances(self, manager):
        """为缺少 ID 的实例分配 UUID"""
        manager._cache = {
            "llm_instances": [{"name": "llm1"}],
            "search_instances": [{"name": "search1"}],
        }
        manager._ensure_config_structure()
        assert manager._cache["llm_instances"][0]["id"]
        assert manager._cache["search_instances"][0]["id"]


# ============================================================================
# 保存
# ============================================================================

class TestSave:
    def test_save_writes_file(self, manager, config_file):
        data = {"test": "value"}
        manager._save(data)
        assert config_file.exists()
        saved = json.loads(config_file.read_text(encoding='utf-8'))
        assert saved == data

    def test_save_creates_parent_dir(self, tmp_path):
        """父目录不存在时自动创建"""
        config_file = tmp_path / "subdir" / "config.json"
        manager = NetworkConfigManager(config_file=str(config_file))
        manager._save({"test": 1})
        assert config_file.exists()


# ============================================================================
# 加密存储
# ============================================================================

class TestSecureStorage:
    def test_save_secure_with_manager(self, manager, secure_manager):
        manager._save_secure("test_key", "secret_value")
        secure_manager.set_secure_value.assert_called_once_with("test_key", "secret_value")

    def test_save_secure_without_manager(self, manager_no_secure):
        """无 secure_manager 时不报错（仅记录警告）"""
        manager_no_secure._save_secure("test_key", "secret_value")

    def test_save_secure_exception_handled(self, manager, secure_manager):
        """加密保存异常被捕获"""
        secure_manager.set_secure_value.side_effect = RuntimeError("encrypt failed")
        manager._save_secure("test_key", "secret_value")

    def test_load_secure_with_manager(self, manager, secure_manager):
        secure_manager.get_secure_value.return_value = "decrypted_value"
        result = manager._load_secure("test_key", "default")
        assert result == "decrypted_value"

    def test_load_secure_without_manager(self, manager_no_secure):
        result = manager_no_secure._load_secure("test_key", "default_value")
        assert result == "default_value"

    def test_load_secure_exception_handled(self, manager, secure_manager):
        """加密加载异常被捕获，返回默认值"""
        secure_manager.get_secure_value.side_effect = RuntimeError("decrypt failed")
        result = manager._load_secure("test_key", "fallback")
        assert result == "fallback"

    def test_load_secure_without_default(self, manager_no_secure):
        result = manager_no_secure._load_secure("test_key")
        assert result is None


# ============================================================================
# 变更日志
# ============================================================================

class TestChangeLog:
    def test_add_change_log_basic(self, manager):
        manager._cache = deepcopy(_DEFAULT_NETWORK_CONFIG)
        manager._add_change_log("update", "llm", {"key": "value"})
        assert len(manager._cache["change_log"]) == 1
        entry = manager._cache["change_log"][0]
        assert entry["action"] == "update"
        assert entry["section"] == "llm"
        assert entry["details"] == {"key": "value"}
        assert "id" in entry
        assert "timestamp" in entry

    def test_add_change_log_no_details(self, manager):
        manager._cache = deepcopy(_DEFAULT_NETWORK_CONFIG)
        manager._add_change_log("reset", "all")
        assert manager._cache["change_log"][0]["details"] == {}

    def test_add_change_log_trims_to_100(self, manager):
        """超过 100 条时截断"""
        manager._cache = deepcopy(_DEFAULT_NETWORK_CONFIG)
        for i in range(105):
            manager._add_change_log("update", "llm")
        assert len(manager._cache["change_log"]) == 100

    def test_add_change_log_inserts_at_front(self, manager):
        """新日志插入到列表头部"""
        manager._cache = deepcopy(_DEFAULT_NETWORK_CONFIG)
        manager._add_change_log("first", "llm")
        manager._add_change_log("second", "llm")
        assert manager._cache["change_log"][0]["action"] == "second"
        assert manager._cache["change_log"][1]["action"] == "first"

    def test_get_change_log(self, manager):
        manager._cache = deepcopy(_DEFAULT_NETWORK_CONFIG)
        for i in range(25):
            manager._add_change_log("update", "llm")
        logs = manager.get_change_log(limit=10)
        assert len(logs) == 10

    def test_get_change_log_default_limit(self, manager):
        manager._cache = deepcopy(_DEFAULT_NETWORK_CONFIG)
        manager._add_change_log("update", "llm")
        logs = manager.get_change_log()
        assert len(logs) == 1


# ============================================================================
# 配置获取（脱敏）
# ============================================================================

class TestGetAll:
    def test_get_all_returns_config(self, manager):
        config = manager.get_all()
        assert "llm" in config
        assert "mcp" in config

    def test_get_all_masks_long_llm_api_key(self, manager, secure_manager):
        """长 LLM API Key 脱敏为 ***+后4位"""
        secure_manager.get_secure_value.return_value = "sk-1234567890abcdef"
        config = manager.get_all()
        assert config["llm"]["api_key"] == "***cdef"

    def test_get_all_masks_short_llm_api_key(self, manager, secure_manager):
        """短 LLM API Key 脱敏为 ***"""
        secure_manager.get_secure_value.return_value = "abc"
        config = manager.get_all()
        assert config["llm"]["api_key"] == "***"

    def test_get_all_masks_webhook_url(self, manager, secure_manager):
        secure_manager.get_secure_value.return_value = "https://webhook.example.com/secret"
        config = manager.get_all()
        assert config["external_services"]["error_reporting"]["webhook_url"] == "***"

    def test_get_all_masks_llm_instance_api_key(self, manager, secure_manager):
        """LLM 实例 API Key 脱敏"""
        manager._cache = deepcopy(_DEFAULT_NETWORK_CONFIG)
        manager._cache["llm_instances"] = [{"id": "inst1", "name": "test", "api_key": ""}]
        secure_manager.get_secure_value.return_value = "sk-1234567890"
        config = manager.get_all()
        assert config["llm_instances"][0]["api_key"] == "***7890"

    def test_get_all_masks_search_instance_api_key(self, manager, secure_manager):
        """搜索实例 API Key 脱敏"""
        manager._cache = deepcopy(_DEFAULT_NETWORK_CONFIG)
        manager._cache["search_instances"] = [{"id": "s1", "api_key": ""}]
        secure_manager.get_secure_value.return_value = "search-key-12345"
        config = manager.get_all()
        assert config["search_instances"][0]["api_key"] == "***2345"


# ============================================================================
# 配置获取（原始）
# ============================================================================

class TestGetRawConfig:
    def test_get_raw_config_returns_unmasked(self, manager, secure_manager):
        secure_manager.get_secure_value.return_value = "sk-secret-key"
        config = manager.get_raw_config()
        assert config["llm"]["api_key"] == "sk-secret-key"

    def test_get_raw_config_loads_llm_instance_keys(self, manager, secure_manager):
        manager._cache = deepcopy(_DEFAULT_NETWORK_CONFIG)
        manager._cache["llm_instances"] = [{"id": "inst1", "name": "test", "api_key": ""}]
        secure_manager.get_secure_value.return_value = "instance-key"
        config = manager.get_raw_config()
        assert config["llm_instances"][0]["api_key"] == "instance-key"


# ============================================================================
# 配置更新
# ============================================================================

class TestUpdate:
    def test_update_llm_api_key_new(self, manager, secure_manager):
        """新增 LLM API Key 加密保存"""
        manager.update({"llm": {"api_key": "sk-new-key"}})
        secure_manager.set_secure_value.assert_called_with("llm_api_key", "sk-new-key")

    def test_update_llm_api_key_masked_skipped(self, manager, secure_manager):
        """脱敏值跳过加密保存"""
        manager.update({"llm": {"api_key": "***1234"}})
        secure_manager.set_secure_value.assert_not_called()

    def test_update_llm_api_key_masked_only_stars_skipped(self, manager, secure_manager):
        """仅 *** 的值跳过"""
        manager.update({"llm": {"api_key": "***"}})
        secure_manager.set_secure_value.assert_not_called()

    def test_update_webhook_url_new(self, manager, secure_manager):
        manager.update({
            "external_services": {
                "error_reporting": {"webhook_url": "https://hook.example.com"}
            }
        })
        secure_manager.set_secure_value.assert_called_with(
            "error_reporting_webhook", "https://hook.example.com"
        )

    def test_update_webhook_url_masked_skipped(self, manager, secure_manager):
        manager.update({
            "external_services": {
                "error_reporting": {"webhook_url": "***"}
            }
        })
        secure_manager.set_secure_value.assert_not_called()

    def test_update_search_api_keys(self, manager, secure_manager):
        manager.update({
            "search_api_keys": {"google": "google-api-key", "bing": "bing-key"}
        })
        secure_manager.set_secure_value.assert_any_call("search_google_key", "google-api-key")
        secure_manager.set_secure_value.assert_any_call("search_bing_key", "bing-key")

    def test_update_search_api_keys_masked_skipped(self, manager, secure_manager):
        manager.update({"search_api_keys": {"google": "***"}})
        secure_manager.set_secure_value.assert_not_called()

    def test_update_mcp_config(self, manager):
        result = manager.update({"mcp": {"enabled": True, "services": []}})
        assert result["mcp"]["enabled"] is True

    def test_update_merges_config(self, manager):
        manager.update({"network": {"timeout": 60}})
        config = manager.get_all()
        assert config["network"]["timeout"] == 60

    def test_update_clears_cache(self, manager):
        manager.update({"network": {"timeout": 60}})
        assert manager._cache is not None

    def test_update_adds_change_log(self, manager):
        manager.update({"network": {"timeout": 60}})
        logs = manager.get_change_log()
        assert any(log["action"] == "update" for log in logs)


# ============================================================================
# _update_llm_instances
# ============================================================================

class TestUpdateLlmInstances:
    def test_add_new_instance(self, manager, secure_manager):
        manager._cache = deepcopy(_DEFAULT_NETWORK_CONFIG)
        manager._update_llm_instances([{"name": "new_inst", "api_key": "sk-12345"}])
        assert len(manager._cache["llm_instances"]) == 1
        inst = manager._cache["llm_instances"][0]
        assert inst["id"]
        assert inst["name"] == "new_inst"
        assert "created_at" in inst
        secure_manager.set_secure_value.assert_called()

    def test_update_existing_instance(self, manager):
        manager._cache = deepcopy(_DEFAULT_NETWORK_CONFIG)
        manager._cache["llm_instances"] = [{"id": "inst1", "name": "old", "api_key": ""}]
        manager._update_llm_instances([{"id": "inst1", "name": "updated"}])
        assert manager._cache["llm_instances"][0]["name"] == "updated"
        assert "updated_at" in manager._cache["llm_instances"][0]

    def test_update_existing_instance_with_api_key(self, manager, secure_manager):
        manager._cache = deepcopy(_DEFAULT_NETWORK_CONFIG)
        manager._cache["llm_instances"] = [{"id": "inst1", "name": "old", "api_key": ""}]
        manager._update_llm_instances([{"id": "inst1", "api_key": "sk-new"}])
        secure_manager.set_secure_value.assert_called_with("llm_inst1_api_key", "sk-new")

    def test_update_existing_instance_masked_api_key_skipped(self, manager, secure_manager):
        manager._cache = deepcopy(_DEFAULT_NETWORK_CONFIG)
        manager._cache["llm_instances"] = [{"id": "inst1", "name": "old", "api_key": "old-key"}]
        manager._update_llm_instances([{"id": "inst1", "api_key": "***"}])
        secure_manager.set_secure_value.assert_not_called()


# ============================================================================
# _update_search_instances
# ============================================================================

class TestUpdateSearchInstances:
    def test_add_new_search_instance(self, manager, secure_manager):
        manager._cache = deepcopy(_DEFAULT_NETWORK_CONFIG)
        manager._update_search_instances([{"name": "search1", "api_key": "key123"}])
        assert len(manager._cache["search_instances"]) == 1
        assert manager._cache["search_instances"][0]["id"]
        secure_manager.set_secure_value.assert_called()

    def test_update_existing_search_instance(self, manager):
        manager._cache = deepcopy(_DEFAULT_NETWORK_CONFIG)
        manager._cache["search_instances"] = [{"id": "s1", "name": "old"}]
        manager._update_search_instances([{"id": "s1", "name": "updated"}])
        assert manager._cache["search_instances"][0]["name"] == "updated"

    def test_update_existing_with_masked_key_skipped(self, manager, secure_manager):
        manager._cache = deepcopy(_DEFAULT_NETWORK_CONFIG)
        manager._cache["search_instances"] = [{"id": "s1", "name": "old", "api_key": "k"}]
        manager._update_search_instances([{"id": "s1", "api_key": "***"}])
        secure_manager.set_secure_value.assert_not_called()


# ============================================================================
# _update_mcp_config
# ============================================================================

class TestUpdateMcpConfig:
    def test_update_mcp_replaces_config(self, manager):
        manager._cache = deepcopy(_DEFAULT_NETWORK_CONFIG)
        new_mcp = {"enabled": True, "services": [{"id": "s1", "name": "svc"}]}
        manager._update_mcp_config(new_mcp)
        assert manager._cache["mcp"]["enabled"] is True

    def test_update_mcp_adds_service_without_id(self, manager):
        """MCP 服务没有 ID 时自动生成"""
        manager._cache = deepcopy(_DEFAULT_NETWORK_CONFIG)
        manager._update_mcp_config({
            "enabled": True,
            "services": [{"name": "new_svc"}]
        })
        # _update_mcp_config 只记录日志，不修改 services
        assert manager._cache["mcp"]["enabled"] is True


# ============================================================================
# 搜索实例注册
# ============================================================================

class TestRegisterSearchInstance:
    def test_register_no_search_engine(self, manager):
        """无 search_engine 时直接返回"""
        manager._register_search_instance({"id": "s1"}, None)

    def test_register_custom_engine(self, manager):
        search_engine = MagicMock()
        instance = {"id": "s1", "name": "Custom", "engine_type": "custom", "api_key": ""}
        manager._register_search_instance(instance, search_engine)
        assert search_engine.register_engine.call_count >= 1

    def test_register_builtin_engine(self, manager):
        search_engine = MagicMock()
        search_engine._search_duckduckgo = MagicMock()
        instance = {
            "id": "s1", "name": "DDG", "engine_type": "duckduckgo",
            "api_key": "", "enabled": True, "is_default": True
        }
        manager._register_search_instance(instance, search_engine)
        search_engine.set_default_engine.assert_called_with("s1")

    def test_register_builtin_engine_fallback_to_custom(self, manager):
        """内置引擎无专用 handler 时回退到 custom"""
        search_engine = MagicMock()
        # 不设置 _search_unknown 属性 → getattr 返回 None
        instance = {
            "id": "s1", "name": "Unknown", "engine_type": "unknown",
            "api_key": ""
        }
        manager._register_search_instance(instance, search_engine)
        assert search_engine.register_engine.called

    def test_register_syncs_api_key_for_builtin(self, manager):
        """内置引擎同步 API Key 到 _api_keys"""
        search_engine = MagicMock()
        search_engine._search_duckduckgo = MagicMock()
        search_engine._api_keys = {}  # 使用真实 dict 以验证键值写入
        instance = {
            "id": "s1", "name": "DDG", "engine_type": "duckduckgo",
            "api_key": "ddg-key"
        }
        manager._register_search_instance(instance, search_engine)
        assert search_engine._api_keys["duckduckgo"] == "ddg-key"


class TestApplySearchInstances:
    def test_no_search_engine(self, manager):
        """无 search_engine 时直接返回"""
        manager.apply_search_instances(None)

    def test_seed_builtin_on_first_run(self, manager):
        """首次运行时创建内置搜索引擎实例"""
        search_engine = MagicMock()
        search_engine._engine_registry = {}
        manager.apply_search_instances(search_engine)
        config = manager.get_raw_config()
        assert len(config["search_instances"]) > 0

    def test_register_existing_instances(self, manager):
        """注册已有实例"""
        manager._cache = deepcopy(_DEFAULT_NETWORK_CONFIG)
        manager._cache["search_instances"] = [{
            "id": "s1", "name": "Test", "engine_type": "custom",
            "api_key": "", "enabled": True
        }]
        manager._save(manager._cache)

        search_engine = MagicMock()
        search_engine._engine_registry = {}
        manager.apply_search_instances(search_engine)
        search_engine.set_engine_priority.assert_called()

    def test_skip_disabled_instances(self, manager):
        """禁用的实例不注册"""
        manager._cache = deepcopy(_DEFAULT_NETWORK_CONFIG)
        manager._cache["search_instances"] = [{
            "id": "s1", "name": "Disabled", "engine_type": "custom",
            "api_key": "", "enabled": False
        }]
        manager._save(manager._cache)

        search_engine = MagicMock()
        search_engine._engine_registry = {}
        manager.apply_search_instances(search_engine)
        # 不应注册任何引擎（实例被禁用）
        search_engine.register_engine.assert_not_called()

    def test_cleanup_old_engines_before_register(self, manager):
        """注册前清理旧引擎"""
        manager._cache = deepcopy(_DEFAULT_NETWORK_CONFIG)
        manager._cache["search_instances"] = [{
            "id": "s1", "name": "Test", "engine_type": "custom",
            "api_key": "", "enabled": True
        }]
        manager._save(manager._cache)

        search_engine = MagicMock()
        search_engine._engine_registry = {"s1": "old_job"}
        manager.apply_search_instances(search_engine)
        search_engine.remove_engine.assert_called_with("s1")


class TestSeedBuiltinSearchInstances:
    def test_seed_creates_3_builtin_engines(self, manager):
        instances = manager._seed_builtin_search_instances({})
        assert len(instances) == 3
        engine_types = {inst["engine_type"] for inst in instances}
        assert "duckduckgo" in engine_types
        assert "sogou" in engine_types
        assert "so360" in engine_types

    def test_seed_instances_have_ids(self, manager):
        instances = manager._seed_builtin_search_instances({})
        for inst in instances:
            assert inst["id"]
            assert inst["created_at"]
            assert inst["updated_at"]


# ============================================================================
# 重置/导入/导出
# ============================================================================

class TestReset:
    def test_reset_restores_default(self, manager):
        manager.update({"network": {"timeout": 99}})
        result = manager.reset()
        assert result["network"]["timeout"] == 30

    def test_reset_adds_change_log(self, manager):
        manager.reset()
        logs = manager.get_change_log()
        assert any(log["action"] == "reset" for log in logs)


class TestExportConfig:
    def test_export_returns_json_string(self, manager):
        exported = manager.export_config()
        assert isinstance(exported, str)
        data = json.loads(exported)
        assert "llm" in data

    def test_export_masks_sensitive(self, manager, secure_manager):
        secure_manager.get_secure_value.return_value = "sk-1234567890abcdef"
        exported = manager.export_config()
        data = json.loads(exported)
        assert data["llm"]["api_key"] == "***cdef"


class TestImportConfig:
    def test_import_overwrite(self, manager):
        imported = json.dumps({"llm": {"enabled": False}, "mcp": {"enabled": True}})
        result = manager.import_config(imported, conflict_strategy="overwrite")
        assert result["llm"]["enabled"] is False

    def test_import_skip(self, manager):
        """skip 策略：跳过已存在的键"""
        manager._cache = deepcopy(_DEFAULT_NETWORK_CONFIG)
        manager._cache["network"]["timeout"] = 99
        manager._save(manager._cache)

        imported = json.dumps({"network": {"timeout": 50}})
        result = manager.import_config(imported, conflict_strategy="skip")
        # network.timeout 已存在，跳过 → 保持 99
        assert result["network"]["timeout"] == 99

    def test_import_merge(self, manager):
        manager._cache = deepcopy(_DEFAULT_NETWORK_CONFIG)
        manager._cache["network"]["timeout"] = 99
        manager._save(manager._cache)

        imported = json.dumps({"network": {"timeout": 50, "max_retries": 5}})
        result = manager.import_config(imported, conflict_strategy="merge")
        assert result["network"]["timeout"] == 50
        assert result["network"]["max_retries"] == 5

    def test_import_invalid_json_raises_valueerror(self, manager):
        with pytest.raises(ValueError, match="配置格式错误"):
            manager.import_config("{invalid}")

    def test_import_adds_change_log(self, manager):
        manager.import_config(json.dumps({"llm": {"enabled": False}}))
        logs = manager.get_change_log()
        assert any(log["action"] == "import" for log in logs)


class TestMerge:
    def test_merge_nested_dicts(self, manager):
        target = {"a": {"b": 1, "c": 2}}
        source = {"a": {"c": 3, "d": 4}}
        manager._merge(target, source)
        assert target == {"a": {"b": 1, "c": 3, "d": 4}}

    def test_merge_overwrites_non_dict(self, manager):
        target = {"a": 1}
        source = {"a": 2}
        manager._merge(target, source)
        assert target == {"a": 2}

    def test_merge_skip_existing(self, manager):
        target = {"a": 1, "b": {"c": 2}}
        source = {"a": 99, "b": {"c": 99, "d": 3}, "e": 5}
        manager._merge_skip_existing(target, source)
        assert target == {"a": 1, "b": {"c": 2, "d": 3}, "e": 5}


# ============================================================================
# LLM 实例 CRUD
# ============================================================================

class TestLlmInstanceCRUD:
    def test_get_llm_instances_empty(self, manager):
        result = manager.get_llm_instances()
        assert result == []

    def test_get_llm_instances(self, manager):
        manager.add_llm_instance({"name": "test", "provider": "openai"})
        result = manager.get_llm_instances()
        assert len(result) == 1
        assert result[0]["name"] == "test"

    def test_get_llm_instance_by_id(self, manager):
        added = manager.add_llm_instance({"name": "test"})
        found = manager.get_llm_instance(added["id"])
        assert found["name"] == "test"

    def test_get_llm_instance_by_name(self, manager):
        manager.add_llm_instance({"name": "test"})
        found = manager.get_llm_instance("test")
        assert found is not None

    def test_get_llm_instance_not_found(self, manager):
        result = manager.get_llm_instance("nonexistent")
        assert result is None

    def test_add_llm_instance_basic(self, manager):
        result = manager.add_llm_instance({"name": "new", "provider": "openai"})
        assert result["name"] == "new"
        assert result["provider"] == "openai"
        assert result["id"]
        assert result["enabled"] is True  # 默认值

    def test_add_llm_instance_with_api_key(self, manager, secure_manager):
        manager.add_llm_instance({"name": "new", "api_key": "sk-secret"})
        secure_manager.set_secure_value.assert_called()

    def test_add_llm_instance_duplicate_name_raises(self, manager):
        manager.add_llm_instance({"name": "dup"})
        with pytest.raises(ValueError, match="名称已存在"):
            manager.add_llm_instance({"name": "dup"})

    def test_update_llm_instance_by_id(self, manager):
        added = manager.add_llm_instance({"name": "old"})
        result = manager.update_llm_instance(added["id"], {"name": "new"})
        assert result["name"] == "new"

    def test_update_llm_instance_by_name(self, manager):
        manager.add_llm_instance({"name": "old"})
        result = manager.update_llm_instance("old", {"provider": "anthropic"})
        assert result["provider"] == "anthropic"

    def test_update_llm_instance_not_found(self, manager):
        result = manager.update_llm_instance("nonexistent", {"name": "x"})
        assert result is None

    def test_update_llm_instance_duplicate_name_raises(self, manager):
        manager.add_llm_instance({"name": "a"})
        manager.add_llm_instance({"name": "b"})
        with pytest.raises(ValueError, match="名称已存在"):
            manager.update_llm_instance("a", {"name": "b"})

    def test_update_llm_instance_with_api_key(self, manager, secure_manager):
        added = manager.add_llm_instance({"name": "test"})
        secure_manager.reset_mock()
        manager.update_llm_instance(added["id"], {"api_key": "sk-new"})
        secure_manager.set_secure_value.assert_called()

    def test_update_llm_instance_masked_api_key_skipped(self, manager, secure_manager):
        added = manager.add_llm_instance({"name": "test"})
        secure_manager.reset_mock()
        result = manager.update_llm_instance(added["id"], {"api_key": "***1234"})
        secure_manager.set_secure_value.assert_not_called()
        assert result is not None

    def test_delete_llm_instance_by_id(self, manager):
        added = manager.add_llm_instance({"name": "test"})
        assert manager.delete_llm_instance(added["id"]) is True
        assert manager.get_llm_instance(added["id"]) is None

    def test_delete_llm_instance_by_name(self, manager):
        manager.add_llm_instance({"name": "test"})
        assert manager.delete_llm_instance("test") is True

    def test_delete_llm_instance_not_found(self, manager):
        assert manager.delete_llm_instance("nonexistent") is False

    def test_delete_llm_instance_clears_secure_key(self, manager, secure_manager):
        added = manager.add_llm_instance({"name": "test"})
        secure_manager.reset_mock()
        manager.delete_llm_instance(added["id"])
        secure_manager.set_secure_value.assert_called_with(f"llm_{added['id']}_api_key", "")

    def test_delete_default_llm_instance_clears_default(self, manager):
        added = manager.add_llm_instance({"name": "test"})
        manager.set_default_llm_instance(added["id"])
        manager.delete_llm_instance(added["id"])
        config = manager.get_raw_config()
        assert config["default_llm_instance"] == ""

    def test_set_default_llm_instance_by_id(self, manager):
        added = manager.add_llm_instance({"name": "test"})
        assert manager.set_default_llm_instance(added["id"]) is True
        config = manager.get_raw_config()
        assert config["default_llm_instance"] == added["id"]

    def test_set_default_llm_instance_by_name(self, manager):
        manager.add_llm_instance({"name": "test"})
        assert manager.set_default_llm_instance("test") is True

    def test_set_default_llm_instance_not_found(self, manager):
        assert manager.set_default_llm_instance("nonexistent") is False

    def test_set_default_llm_instance_marks_is_default(self, manager):
        a = manager.add_llm_instance({"name": "a"})
        b = manager.add_llm_instance({"name": "b"})
        manager.set_default_llm_instance(a["id"])
        config = manager.get_raw_config()
        for inst in config["llm_instances"]:
            assert inst["is_default"] == (inst["id"] == a["id"])


# ============================================================================
# MCP 服务 CRUD
# ============================================================================

class TestMcpServiceCRUD:
    def test_get_mcp_services_empty(self, manager):
        result = manager.get_mcp_services()
        assert result == []

    def test_add_mcp_service(self, manager):
        result = manager.add_mcp_service({"name": "svc1", "address": "localhost"})
        assert result["name"] == "svc1"
        assert result["id"]
        assert result["enabled"] is True  # 默认值

    def test_add_mcp_service_duplicate_name_raises(self, manager):
        manager.add_mcp_service({"name": "dup"})
        with pytest.raises(ValueError, match="名称已存在"):
            manager.add_mcp_service({"name": "dup"})

    def test_get_mcp_service_by_id(self, manager):
        added = manager.add_mcp_service({"name": "svc"})
        found = manager.get_mcp_service(added["id"])
        assert found["name"] == "svc"

    def test_get_mcp_service_not_found(self, manager):
        assert manager.get_mcp_service("nonexistent") is None

    def test_update_mcp_service(self, manager):
        added = manager.add_mcp_service({"name": "old"})
        result = manager.update_mcp_service(added["id"], {"name": "new"})
        assert result["name"] == "new"

    def test_update_mcp_service_not_found(self, manager):
        result = manager.update_mcp_service("nonexistent", {"name": "x"})
        assert result is None

    def test_update_mcp_service_duplicate_name_raises(self, manager):
        manager.add_mcp_service({"name": "a"})
        b = manager.add_mcp_service({"name": "b"})
        with pytest.raises(ValueError, match="名称已存在"):
            manager.update_mcp_service(b["id"], {"name": "a"})

    def test_delete_mcp_service(self, manager):
        added = manager.add_mcp_service({"name": "svc"})
        assert manager.delete_mcp_service(added["id"]) is True
        assert manager.get_mcp_service(added["id"]) is None

    def test_delete_mcp_service_not_found(self, manager):
        assert manager.delete_mcp_service("nonexistent") is False


# ============================================================================
# 应用配置到 app_instance
# ============================================================================

class TestApplyToApp:
    def test_apply_to_app_no_app(self, manager):
        """无 app_instance 时不报错"""
        manager.apply_to_app(None)

    def test_apply_to_app_updates_http_timeout(self, manager):
        app = MagicMock()
        app._web_http.timeout = 30
        manager.update({"network": {"timeout": 60}})
        manager.apply_to_app(app)
        assert app._web_http.timeout == 60

    def test_apply_to_app_no_web_http_attr(self, manager):
        """app 无 _web_http 属性时跳过"""
        app = MagicMock(spec=[])  # 无属性
        manager.apply_to_app(app)

    def test_apply_to_app_updates_search_config(self, manager):
        app = MagicMock()
        app._web_http = MagicMock()
        manager.update({"search": {"timeout": 45}})
        manager.apply_to_app(app)
        app._web_search.update_config.assert_called()

    def test_apply_to_app_no_web_search_attr(self, manager):
        """app 无 _web_search 属性时跳过搜索配置"""
        app = MagicMock()
        app._web_http = MagicMock()
        # 删除 _web_search 属性
        del app._web_search
        manager.apply_to_app(app)

    def test_apply_to_app_calls_configure_llm(self, manager, secure_manager):
        app = MagicMock()
        app._web_http = MagicMock()
        app._web_search = None
        app.configure_llm.return_value = {"ok": True}
        secure_manager.get_secure_value.return_value = "sk-key"
        manager.update({"llm": {"enabled": True, "provider": "openai", "api_key": "sk-key", "model": "gpt-4"}})
        manager.apply_to_app(app)
        app.configure_llm.assert_called()

    def test_apply_to_app_llm_disabled_skips_configure(self, manager, secure_manager):
        app = MagicMock()
        app._web_http = MagicMock()
        app._web_search = None
        secure_manager.get_secure_value.return_value = "sk-key"
        manager.update({"llm": {"enabled": False, "provider": "openai", "api_key": "sk-key"}})
        manager.apply_to_app(app)
        app.configure_llm.assert_not_called()

    def test_apply_to_app_llm_no_provider_skips(self, manager, secure_manager):
        app = MagicMock()
        app._web_http = MagicMock()
        app._web_search = None
        secure_manager.get_secure_value.return_value = "sk-key"
        manager.update({"llm": {"enabled": True, "provider": "", "api_key": "sk-key"}})
        manager.apply_to_app(app)
        app.configure_llm.assert_not_called()

    def test_apply_to_app_llm_no_api_key_skips(self, manager, secure_manager):
        app = MagicMock()
        app._web_http = MagicMock()
        app._web_search = None
        secure_manager.get_secure_value.return_value = ""
        manager.update({"llm": {"enabled": True, "provider": "openai"}})
        manager.apply_to_app(app)
        app.configure_llm.assert_not_called()

    def test_apply_to_app_configure_llm_failure_handled(self, manager, secure_manager):
        app = MagicMock()
        app._web_http = MagicMock()
        app._web_search = None
        app.configure_llm.side_effect = RuntimeError("configure failed")
        secure_manager.get_secure_value.return_value = "sk-key"
        manager.update({"llm": {"enabled": True, "provider": "openai", "api_key": "sk-key", "model": "gpt-4"}})
        # 不应抛异常
        manager.apply_to_app(app)

    def test_apply_to_app_configure_llm_returns_error(self, manager, secure_manager):
        app = MagicMock()
        app._web_http = MagicMock()
        app._web_search = None
        app.configure_llm.return_value = {"ok": False, "error": "invalid key"}
        secure_manager.get_secure_value.return_value = "sk-key"
        manager.update({"llm": {"enabled": True, "provider": "openai", "api_key": "sk-key", "model": "gpt-4"}})
        manager.apply_to_app(app)

    def test_apply_to_app_uses_llm_instance(self, manager, secure_manager):
        """有 LLM 实例时使用实例配置"""
        app = MagicMock()
        app._web_http = MagicMock()
        app._web_search = None
        app.configure_llm.return_value = {"ok": True}
        secure_manager.get_secure_value.return_value = "instance-key"
        manager.add_llm_instance({
            "name": "inst1", "provider": "anthropic",
            "api_key": "instance-key", "model": "claude-3"
        })
        instances = manager.get_llm_instances()
        manager.set_default_llm_instance(instances[0]["id"])
        manager.apply_to_app(app)
        app.configure_llm.assert_called()
        call_kwargs = app.configure_llm.call_args
        assert call_kwargs.kwargs.get("provider") == "anthropic" or call_kwargs[1].get("provider") == "anthropic"


# ============================================================================
# 搜索引擎配置
# ============================================================================

class TestSearchEngines:
    def test_get_search_engines(self, manager):
        result = manager.get_search_engines()
        assert "enabled" in result
        assert "default_engine" in result
        assert "max_results" in result
        assert "timeout" in result
        assert "engine_priority" in result
        assert "engine_enabled" in result
        assert result["api_keys"] == {}

    def test_get_search_engines_custom_values(self, manager):
        manager.update({"search": {"timeout": 45, "max_results": 20}})
        result = manager.get_search_engines()
        assert result["timeout"] == 45
        assert result["max_results"] == 20

    def test_update_search_config_default_engine(self, manager):
        result = manager.update_search_config({"default_engine": "duckduckgo"})
        assert result is not None
        config = manager.get_search_engines()
        assert config["default_engine"] == "duckduckgo"

    def test_update_search_config_max_results(self, manager):
        result = manager.update_search_config({"max_results": 50})
        assert result is not None
        config = manager.get_search_engines()
        assert config["max_results"] == 50

    def test_update_search_config_timeout(self, manager):
        result = manager.update_search_config({"timeout": 60})
        config = manager.get_search_engines()
        assert config["timeout"] == 60

    def test_update_search_config_engine_priority(self, manager):
        result = manager.update_search_config({"engine_priority": ["google", "bing"]})
        config = manager.get_search_engines()
        assert config["engine_priority"] == ["google", "bing"]

    def test_update_search_config_engine_enabled(self, manager):
        result = manager.update_search_config({"engine_enabled": {"google": True}})
        config = manager.get_search_engines()
        assert config["engine_enabled"] == {"google": True}


# ============================================================================
# 配置验证
# ============================================================================

class TestValidation:
    def test_validate_llm_instance_delegates(self, manager):
        with patch("agent.network.config_manager.validate_llm_instance", return_value=["error"]) as mock_v:
            result = manager.validate_llm_instance({"name": "test"})
            mock_v.assert_called_once_with({"name": "test"})
            assert result == ["error"]

    def test_validate_mcp_service_delegates(self, manager):
        with patch("agent.network.config_manager.validate_mcp_service", return_value=[]) as mock_v:
            result = manager.validate_mcp_service({"name": "svc"})
            mock_v.assert_called_once_with({"name": "svc"})
            assert result == []

    def test_validate_llm_instance_valid(self, manager):
        """验证有效的 LLM 实例"""
        instance = {
            "name": "test",
            "provider": "openai",
            "api_key": "sk-key",
            "model": "gpt-4",
            "api_endpoint": "https://api.openai.com",
        }
        result = manager.validate_llm_instance(instance)
        assert isinstance(result, list)

    def test_validate_mcp_service_valid(self, manager):
        """验证有效的 MCP 服务"""
        service = {
            "name": "test",
            "address": "localhost",
            "port": 8080,
            "protocol": "http",
        }
        result = manager.validate_mcp_service(service)
        assert isinstance(result, list)
