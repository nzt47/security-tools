"""agent.network 包单元测试

覆盖：
- config_validator.py: validate_llm_instance / validate_mcp_service
- observability.py: trackEvent / _emit_structured_log / _trace_id
- config_manager.py: NetworkConfigManager 基础方法（_load/_save/get_all/get_raw_config/update/reset/export/import/LLM 实例 CRUD）

状态同步机制：无异步时序场景，使用 tmp_path 隔离文件系统 IO，Mock 外部依赖。
"""
import json
import os
import tempfile
from pathlib import Path
from unittest import mock

import pytest

# ── config_validator 测试 ──

from agent.network.config_validator import validate_llm_instance, validate_mcp_service


class TestValidateLlmInstance:
    """validate_llm_instance 验证逻辑"""

    def test_valid_instance(self):
        inst = {
            "name": "openai-prod",
            "api_endpoint": "https://api.openai.com/v1",
            "provider": "openai",
        }
        assert validate_llm_instance(inst) == []

    def test_missing_name(self):
        errors = validate_llm_instance({"api_endpoint": "https://x.com", "provider": "x"})
        assert any("服务名称" in e for e in errors)

    def test_missing_api_endpoint(self):
        errors = validate_llm_instance({"name": "n", "provider": "x"})
        assert any("API 端点" in e for e in errors)

    def test_missing_provider(self):
        errors = validate_llm_instance({"name": "n", "api_endpoint": "https://x.com"})
        assert any("提供商" in e for e in errors)

    def test_invalid_url_format(self):
        errors = validate_llm_instance({
            "name": "n", "provider": "x", "api_endpoint": "not-a-url"
        })
        assert any("URL 格式" in e for e in errors)

    def test_url_missing_scheme(self):
        errors = validate_llm_instance({
            "name": "n", "provider": "x", "api_endpoint": "://no-scheme.com"
        })
        assert any("URL 格式" in e for e in errors)

    def test_max_concurrent_requests_not_int(self):
        errors = validate_llm_instance({
            "name": "n", "provider": "x", "api_endpoint": "https://x.com",
            "max_concurrent_requests": "abc"
        })
        assert any("最大并发" in e for e in errors)

    def test_max_concurrent_requests_zero(self):
        errors = validate_llm_instance({
            "name": "n", "provider": "x", "api_endpoint": "https://x.com",
            "max_concurrent_requests": 0
        })
        assert any("最大并发" in e for e in errors)

    def test_timeout_out_of_range(self):
        errors = validate_llm_instance({
            "name": "n", "provider": "x", "api_endpoint": "https://x.com",
            "timeout": 999
        })
        assert any("超时" in e for e in errors)

    def test_timeout_zero(self):
        errors = validate_llm_instance({
            "name": "n", "provider": "x", "api_endpoint": "https://x.com",
            "timeout": 0
        })
        assert any("超时" in e for e in errors)

    def test_max_retries_out_of_range(self):
        errors = validate_llm_instance({
            "name": "n", "provider": "x", "api_endpoint": "https://x.com",
            "max_retries": 99
        })
        assert any("重试" in e for e in errors)

    def test_max_retries_negative(self):
        errors = validate_llm_instance({
            "name": "n", "provider": "x", "api_endpoint": "https://x.com",
            "max_retries": -1
        })
        assert any("重试" in e for e in errors)

    def test_empty_dict(self):
        errors = validate_llm_instance({})
        assert len(errors) >= 3

    def test_all_valid_fields(self):
        inst = {
            "name": "full",
            "api_endpoint": "https://api.test.com/v1",
            "provider": "anthropic",
            "max_concurrent_requests": 10,
            "timeout": 60,
            "max_retries": 3,
        }
        assert validate_llm_instance(inst) == []


class TestValidateMcpService:
    """validate_mcp_service 验证逻辑"""

    def test_valid_service(self):
        svc = {"name": "mcp1", "address": "localhost", "port": 8080}
        assert validate_mcp_service(svc) == []

    def test_missing_name(self):
        errors = validate_mcp_service({"address": "x", "port": 8080})
        assert any("名称" in e for e in errors)

    def test_missing_address(self):
        errors = validate_mcp_service({"name": "n", "port": 8080})
        assert any("地址" in e for e in errors)

    def test_missing_port(self):
        errors = validate_mcp_service({"name": "n", "address": "x"})
        assert any("端口" in e for e in errors)

    def test_port_out_of_range(self):
        errors = validate_mcp_service({"name": "n", "address": "x", "port": 70000})
        assert any("端口" in e for e in errors)

    def test_port_zero(self):
        errors = validate_mcp_service({"name": "n", "address": "x", "port": 0})
        assert any("端口" in e for e in errors)

    def test_port_not_int(self):
        errors = validate_mcp_service({"name": "n", "address": "x", "port": "abc"})
        assert any("端口" in e for e in errors)

    def test_invalid_protocol(self):
        errors = validate_mcp_service({
            "name": "n", "address": "x", "port": 8080, "protocol": "ftp"
        })
        assert any("协议" in e for e in errors)

    def test_valid_protocol_http(self):
        svc = {"name": "n", "address": "x", "port": 8080, "protocol": "http"}
        assert validate_mcp_service(svc) == []

    def test_valid_protocol_https(self):
        svc = {"name": "n", "address": "x", "port": 8080, "protocol": "https"}
        assert validate_mcp_service(svc) == []

    def test_invalid_retry_strategy(self):
        errors = validate_mcp_service({
            "name": "n", "address": "x", "port": 8080, "retry_strategy": "invalid"
        })
        assert any("重试策略" in e for e in errors)

    def test_valid_retry_strategies(self):
        for strategy in ["fixed", "exponential", "none"]:
            svc = {"name": "n", "address": "x", "port": 8080, "retry_strategy": strategy}
            assert validate_mcp_service(svc) == []

    def test_timeout_out_of_range(self):
        errors = validate_mcp_service({
            "name": "n", "address": "x", "port": 8080, "timeout": 999
        })
        assert any("超时" in e for e in errors)

    def test_max_retries_out_of_range(self):
        errors = validate_mcp_service({
            "name": "n", "address": "x", "port": 8080, "max_retries": 99
        })
        assert any("重试" in e for e in errors)


# ── observability 测试 ──

from agent.network import observability as net_obs


class TestNetworkObservability:
    """network.observability 埋点模块"""

    def test_trace_id_length(self):
        tid = net_obs._trace_id()
        assert isinstance(tid, str)
        assert len(tid) == 16

    def test_trace_id_unique(self):
        ids = {net_obs._trace_id() for _ in range(100)}
        assert len(ids) == 100

    def test_emit_structured_log_basic(self, caplog):
        with caplog.at_level("INFO", logger="agent.network"):
            net_obs._emit_structured_log("test_action", duration_ms=42.5)
        assert any("test_action" in r.message for r in caplog.records)

    def test_emit_structured_log_with_trace_id(self, caplog):
        with caplog.at_level("INFO", logger="agent.network"):
            net_obs._emit_structured_log("act", trace_id="custom-tid", duration_ms=10)
        assert any("custom-tid" in r.message for r in caplog.records)

    def test_emit_structured_log_level_warning(self, caplog):
        with caplog.at_level("WARNING", logger="agent.network"):
            net_obs._emit_structured_log("warn_act", level="warning")
        assert any("warn_act" in r.message for r in caplog.records)

    def test_emit_structured_log_extra_payload(self, caplog):
        with caplog.at_level("INFO", logger="agent.network"):
            net_obs._emit_structured_log("act", user_id="u123", action_type="click")
        msgs = [r.message for r in caplog.records]
        assert any("u123" in m for m in msgs)
        assert any("click" in m for m in msgs)

    def test_track_event_basic(self, caplog):
        with caplog.at_level("INFO", logger="agent.network"):
            net_obs.trackEvent("config_saved", {"instance_id": "inst1"})
        assert any("track.config_saved" in r.message for r in caplog.records)

    def test_track_event_no_payload(self, caplog):
        with caplog.at_level("INFO", logger="agent.network"):
            net_obs.trackEvent("simple_event")
        assert any("track.simple_event" in r.message for r in caplog.records)

    def test_track_event_reserved_keys_filtered(self, caplog):
        with caplog.at_level("INFO", logger="agent.network"):
            net_obs.trackEvent("evt", {
                "action": "should_be_filtered",
                "trace_id": "should_be_filtered",
                "custom_field": "kept",
            })
        msgs = " ".join(r.message for r in caplog.records)
        assert "kept" in msgs
        assert "should_be_filtered" not in msgs

    def test_track_event_does_not_raise(self):
        """埋点失败不影响主流程"""
        with mock.patch.object(net_obs, "_emit_structured_log", side_effect=Exception("boom")):
            net_obs.trackEvent("fail_test")  # 不应抛异常


# ── config_manager 测试 ──

from agent.network.config_manager import NetworkConfigManager


@pytest.fixture
def temp_config_file(tmp_path):
    """临时配置文件路径"""
    return str(tmp_path / "network_config.json")


class TestNetworkConfigManagerLoad:
    """NetworkConfigManager 加载和初始化"""

    def test_load_creates_default_when_missing(self, temp_config_file):
        mgr = NetworkConfigManager(config_file=temp_config_file)
        config = mgr._load()
        assert "llm_instances" in config
        assert "mcp" in config
        assert os.path.exists(temp_config_file)

    def test_load_from_existing_file(self, temp_config_file):
        data = {"llm_instances": [{"id": "x", "name": "test"}], "mcp": {"enabled": True, "services": []}}
        with open(temp_config_file, "w", encoding="utf-8") as f:
            json.dump(data, f)

        mgr = NetworkConfigManager(config_file=temp_config_file)
        config = mgr._load()
        assert config["llm_instances"][0]["name"] == "test"

    def test_load_caches_result(self, temp_config_file):
        mgr = NetworkConfigManager(config_file=temp_config_file)
        c1 = mgr._load()
        c2 = mgr._load()
        assert c1 is c2  # 同一对象引用（缓存）

    def test_load_invalid_json_falls_back_to_default(self, temp_config_file):
        with open(temp_config_file, "w") as f:
            f.write("{invalid json")

        mgr = NetworkConfigManager(config_file=temp_config_file)
        config = mgr._load()
        assert "llm_instances" in config  # 默认配置

    def test_ensure_config_structure_adds_missing_keys(self, temp_config_file):
        mgr = NetworkConfigManager(config_file=temp_config_file)
        mgr._cache = {}
        mgr._ensure_config_structure()
        assert "llm_instances" in mgr._cache
        assert "default_llm_instance" in mgr._cache
        assert "mcp" in mgr._cache
        assert "change_log" in mgr._cache
        assert "llm" in mgr._cache
        assert "search_instances" in mgr._cache


class TestNetworkConfigManagerSave:
    """NetworkConfigManager 保存"""

    def test_save_writes_file(self, temp_config_file):
        mgr = NetworkConfigManager(config_file=temp_config_file)
        data = {"test": "value"}
        mgr._save(data)
        assert os.path.exists(temp_config_file)
        with open(temp_config_file, "r", encoding="utf-8") as f:
            assert json.load(f) == data

    def test_save_creates_parent_dir(self, tmp_path):
        nested = str(tmp_path / "nested" / "deep" / "config.json")
        mgr = NetworkConfigManager(config_file=nested)
        mgr._save({"x": 1})
        assert os.path.exists(nested)


class TestNetworkConfigManagerAPI:
    """NetworkConfigManager 公共 API"""

    def test_get_all_returns_config(self, temp_config_file):
        mgr = NetworkConfigManager(config_file=temp_config_file)
        config = mgr.get_all()
        assert isinstance(config, dict)
        assert "llm_instances" in config

    def test_get_raw_config(self, temp_config_file):
        mgr = NetworkConfigManager(config_file=temp_config_file)
        raw = mgr.get_raw_config()
        assert isinstance(raw, dict)

    def test_update_modifies_config(self, temp_config_file):
        mgr = NetworkConfigManager(config_file=temp_config_file)
        mgr.update({"llm": {"provider": "test_provider"}})
        config = mgr.get_all()
        assert config["llm"]["provider"] == "test_provider"

    def test_reset_restores_default(self, temp_config_file):
        mgr = NetworkConfigManager(config_file=temp_config_file)
        mgr.update({"llm": {"provider": "modified"}})
        mgr.reset()
        config = mgr.get_all()
        assert config["llm"]["provider"] != "modified"

    def test_export_config(self, temp_config_file):
        mgr = NetworkConfigManager(config_file=temp_config_file)
        exported = mgr.export_config()
        assert isinstance(exported, str)
        parsed = json.loads(exported)
        assert "llm_instances" in parsed

    def test_import_config(self, temp_config_file):
        mgr = NetworkConfigManager(config_file=temp_config_file)
        new_config = {"llm_instances": [{"id": "imp", "name": "imported"}]}
        mgr.import_config(json.dumps(new_config))
        config = mgr.get_all()
        assert config["llm_instances"][0]["name"] == "imported"


class TestNetworkConfigManagerLLMInstances:
    """NetworkConfigManager LLM 实例 CRUD"""

    @pytest.fixture
    def mgr(self, temp_config_file):
        return NetworkConfigManager(config_file=temp_config_file)

    def test_get_llm_instances_empty(self, mgr):
        instances = mgr.get_llm_instances()
        assert isinstance(instances, list)

    def test_add_llm_instance(self, mgr):
        result = mgr.add_llm_instance({
            "name": "test-llm",
            "provider": "openai",
            "api_endpoint": "https://api.openai.com/v1",
            "api_key": "sk-test",
            "model": "gpt-4",
        })
        assert isinstance(result, dict)
        assert result["name"] == "test-llm"
        assert "id" in result
        instances = mgr.get_llm_instances()
        assert any(i["name"] == "test-llm" for i in instances)

    def test_add_llm_instance_uses_default_for_missing_name(self, mgr):
        result = mgr.add_llm_instance({"provider": "x"})
        assert isinstance(result, dict)
        assert result["provider"] == "x"
        assert result["name"] == ""

    def test_add_llm_instance_duplicate_name(self, mgr):
        mgr.add_llm_instance({
            "name": "dup",
            "provider": "openai",
            "api_endpoint": "https://api.openai.com/v1",
        })
        with pytest.raises(ValueError):
            mgr.add_llm_instance({
                "name": "dup",
                "provider": "openai",
                "api_endpoint": "https://api.openai.com/v1",
            })

    def test_get_llm_instance_by_id(self, mgr):
        add_result = mgr.add_llm_instance({
            "name": "find-me",
            "provider": "openai",
            "api_endpoint": "https://api.openai.com/v1",
            "api_key": "sk-test",
            "model": "gpt-4",
        })
        inst_id = add_result.get("id", "")
        inst = mgr.get_llm_instance(inst_id)
        assert inst is not None
        assert inst["name"] == "find-me"

    def test_get_llm_instance_not_found(self, mgr):
        assert mgr.get_llm_instance("nonexistent") is None

    def test_delete_llm_instance(self, mgr):
        add_result = mgr.add_llm_instance({
            "name": "to-delete",
            "provider": "openai",
            "api_endpoint": "https://api.openai.com/v1",
            "api_key": "sk-test",
            "model": "gpt-4",
        })
        inst_id = add_result.get("id", "")
        del_result = mgr.delete_llm_instance(inst_id)
        assert del_result is True
        assert mgr.get_llm_instance(inst_id) is None

    def test_delete_llm_instance_not_found(self, mgr):
        assert mgr.delete_llm_instance("nonexistent") is False

    def test_set_default_llm_instance(self, mgr):
        add_result = mgr.add_llm_instance({
            "name": "default-test",
            "provider": "openai",
            "api_endpoint": "https://api.openai.com/v1",
            "api_key": "sk-test",
            "model": "gpt-4",
        })
        inst_id = add_result.get("id", "")
        result = mgr.set_default_llm_instance(inst_id)
        assert result is True
        config = mgr.get_all()
        assert config["default_llm_instance"] == inst_id

    def test_set_default_llm_instance_not_found(self, mgr):
        assert mgr.set_default_llm_instance("nonexistent") is False


class TestNetworkConfigManagerMCP:
    """NetworkConfigManager MCP 服务 CRUD"""

    @pytest.fixture
    def mgr(self, temp_config_file):
        return NetworkConfigManager(config_file=temp_config_file)

    def test_get_mcp_services(self, mgr):
        services = mgr.get_mcp_services()
        assert isinstance(services, list)

    def test_add_mcp_service(self, mgr):
        result = mgr.add_mcp_service({
            "name": "test-mcp",
            "address": "localhost",
            "port": 8080,
            "protocol": "http",
        })
        assert isinstance(result, dict)
        assert result["name"] == "test-mcp"
        assert "id" in result

    def test_add_mcp_service_duplicate_name(self, mgr):
        mgr.add_mcp_service({
            "name": "dup-mcp",
            "address": "localhost",
            "port": 8080,
        })
        with pytest.raises(ValueError):
            mgr.add_mcp_service({
                "name": "dup-mcp",
                "address": "localhost",
                "port": 9090,
            })

    def test_delete_mcp_service(self, mgr):
        add_result = mgr.add_mcp_service({
            "name": "del-mcp",
            "address": "localhost",
            "port": 9090,
        })
        svc_id = add_result.get("id", "")
        assert svc_id  # 确保获取到 ID
        del_result = mgr.delete_mcp_service(svc_id)
        assert del_result is True

    def test_delete_mcp_service_not_found(self, mgr):
        assert mgr.delete_mcp_service("nonexistent") is False


class TestNetworkConfigManagerChangeLog:
    """NetworkConfigManager 变更日志"""

    def test_get_change_log(self, temp_config_file):
        mgr = NetworkConfigManager(config_file=temp_config_file)
        log = mgr.get_change_log()
        assert isinstance(log, list)
