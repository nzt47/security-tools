"""网络配置模块补充测试 - 覆盖未覆盖的代码分支

测试覆盖：
1. LLM 实例更新逻辑
2. MCP 配置更新逻辑
3. apply_to_app 方法
4. 搜索引擎配置更新
5. 配置验证边界条件
"""

import os
import json
import tempfile
import pytest
from unittest.mock import Mock, MagicMock, patch
from pathlib import Path

from agent.network_config import (
    NetworkConfigManager,
    _DEFAULT_NETWORK_CONFIG,
)


class TestLLMInstanceUpdate:
    """测试 LLM 实例更新逻辑"""

    def setup_method(self):
        self.temp_file = tempfile.NamedTemporaryFile(
            mode='w', suffix='.json', delete=False
        )
        self.temp_file.close()
        self.config_path = self.temp_file.name

    def teardown_method(self):
        if os.path.exists(self.config_path):
            os.unlink(self.config_path)

    def test_update_existing_instance(self):
        """测试更新现有 LLM 实例"""
        mock_secure = Mock()
        mock_secure.set_secure_value = Mock()
        mock_secure.get_secure_value = Mock(return_value='')

        manager = NetworkConfigManager(
            config_file=self.config_path,
            secure_manager=mock_secure
        )

        # 添加初始实例
        instance = manager.add_llm_instance({
            'name': 'TestInstance',
            'provider': 'openai',
            'api_endpoint': 'https://api.example.com',
            'timeout': 30,
        })

        instance_id = instance['id']

        # 更新实例（包含 id）
        updates = {
            'llm_instances': [{
                'id': instance_id,
                'name': 'TestInstance',
                'provider': 'deepseek',  # 更改提供商
                'api_endpoint': 'https://api.deepseek.com',
                'timeout': 60,  # 更改超时
            }]
        }
        manager.update(updates)

        # 验证更新
        updated = manager.get_llm_instance(instance_id)
        assert updated['provider'] == 'deepseek'
        assert updated['timeout'] == 60

    def test_update_instance_with_new_api_key(self):
        """测试更新实例时提供新 API Key"""
        mock_secure = Mock()
        mock_secure.set_secure_value = Mock()
        mock_secure.get_secure_value = Mock(return_value='')

        manager = NetworkConfigManager(
            config_file=self.config_path,
            secure_manager=mock_secure
        )

        # 添加实例
        instance = manager.add_llm_instance({
            'name': 'TestInstance',
            'provider': 'openai',
            'api_endpoint': 'https://api.example.com',
        })

        # 更新时提供新 API Key
        updates = {
            'llm_instances': [{
                'id': instance['id'],
                'name': 'TestInstance',
                'provider': 'openai',
                'api_endpoint': 'https://api.example.com',
                'api_key': 'sk-new-api-key-12345',
            }]
        }
        manager.update(updates)

        # 验证加密保存被调用
        mock_secure.set_secure_value.assert_called()

    def test_add_instance_without_id(self):
        """测试添加实例时自动生成 id"""
        mock_secure = Mock()
        mock_secure.set_secure_value = Mock()
        mock_secure.get_secure_value = Mock(return_value='')

        manager = NetworkConfigManager(
            config_file=self.config_path,
            secure_manager=mock_secure
        )

        # 通过 update 添加实例（不带 id）
        updates = {
            'llm_instances': [{
                'name': 'NewInstance',
                'provider': 'openai',
                'api_endpoint': 'https://api.example.com',
            }]
        }
        manager.update(updates)

        instances = manager.get_llm_instances()
        assert len(instances) == 1
        assert instances[0]['name'] == 'NewInstance'
        assert 'id' in instances[0]


class TestMCPConfigUpdate:
    """测试 MCP 配置更新逻辑"""

    def setup_method(self):
        self.temp_file = tempfile.NamedTemporaryFile(
            mode='w', suffix='.json', delete=False
        )
        self.temp_file.close()
        self.config_path = self.temp_file.name

    def teardown_method(self):
        if os.path.exists(self.config_path):
            os.unlink(self.config_path)

    def test_update_mcp_config_add_new_service(self):
        """测试更新 MCP 配置的行为"""
        manager = NetworkConfigManager(config_file=self.config_path)

        # 先启用 MCP
        updates = {
            'mcp': {
                'enabled': True,
                'services': [{
                    'name': 'NewMCP',
                    'address': 'localhost',
                    'port': 8080,
                    'protocol': 'http',
                }]
            }
        }
        manager.update(updates)

        # update 直接替换整个 mcp 对象
        mcp_config = manager.get_all().get('mcp', {})
        assert mcp_config['enabled'] is True
        assert len(mcp_config.get('services', [])) == 1
        assert mcp_config['services'][0]['name'] == 'NewMCP'

    def test_add_mcp_service_generates_id(self):
        """测试通过 add_mcp_service 添加服务会自动生成 id"""
        manager = NetworkConfigManager(config_file=self.config_path)

        # 使用 add_mcp_service 添加服务
        service = manager.add_mcp_service({
            'name': 'NewMCP',
            'address': 'localhost',
            'port': 8080,
            'protocol': 'http',
        })

        services = manager.get_mcp_services()
        assert len(services) == 1
        assert services[0]['name'] == 'NewMCP'
        assert 'id' in services[0]
        assert 'created_at' in services[0]

    def test_update_mcp_config_update_existing_service(self):
        """测试更新 MCP 配置更新现有服务"""
        manager = NetworkConfigManager(config_file=self.config_path)

        # 先添加服务
        service = manager.add_mcp_service({
            'name': 'TestMCP',
            'address': 'localhost',
            'port': 8080,
        })

        # 更新配置（包含已有 id）
        updates = {
            'mcp': {
                'enabled': True,
                'services': [{
                    'id': service['id'],
                    'name': 'TestMCP',
                    'address': '127.0.0.1',  # 更改地址
                    'port': 9090,  # 更改端口
                    'protocol': 'https',  # 更改协议
                }]
            }
        }
        manager.update(updates)

        services = manager.get_mcp_services()
        assert len(services) == 1
        assert services[0]['address'] == '127.0.0.1'
        assert services[0]['port'] == 9090
        assert services[0]['protocol'] == 'https'


class TestApplyToApp:
    """测试 apply_to_app 方法"""

    def setup_method(self):
        self.temp_file = tempfile.NamedTemporaryFile(
            mode='w', suffix='.json', delete=False
        )
        self.temp_file.close()
        self.config_path = self.temp_file.name

    def teardown_method(self):
        if os.path.exists(self.config_path):
            os.unlink(self.config_path)

    def test_apply_to_app_with_http_client(self):
        """测试应用到带 HTTP 客户端的应用实例"""
        mock_secure = Mock()
        mock_secure.get_secure_value = Mock(return_value='')

        manager = NetworkConfigManager(
            config_file=self.config_path,
            secure_manager=mock_secure
        )

        # 设置超时
        manager.update({'network': {'timeout': 45}})

        # 创建模拟应用实例
        mock_app = Mock()
        mock_http = Mock()
        mock_http.timeout = 30
        mock_http.max_retries = 3
        mock_app._web_http = mock_http

        # 应用配置
        manager.apply_to_app(mock_app)

        # 验证超时已更新
        assert mock_http.timeout == 45

    def test_apply_to_app_without_http_client(self):
        """测试应用到无 HTTP 客户端的应用实例"""
        mock_secure = Mock()
        mock_secure.get_secure_value = Mock(return_value='')

        manager = NetworkConfigManager(
            config_file=self.config_path,
            secure_manager=mock_secure
        )

        # 创建模拟应用实例（无 _web_http）
        mock_app = Mock(spec=[])  # 空 spec，没有 _web_http

        # 应用配置（不应抛出异常）
        manager.apply_to_app(mock_app)

    def test_apply_to_app_with_search_engine(self):
        """测试应用到带搜索引擎的应用实例"""
        mock_secure = Mock()
        mock_secure.get_secure_value = Mock(return_value='')

        manager = NetworkConfigManager(
            config_file=self.config_path,
            secure_manager=mock_secure
        )

        # 设置搜索配置
        manager.update({
            'search': {
                'default_engine': 'duckduckgo',
                'timeout': 20,
            }
        })

        # 创建模拟应用实例
        mock_app = Mock()
        mock_http = Mock()
        mock_http.timeout = 30
        mock_app._web_http = mock_http

        mock_search = Mock()
        mock_search.update_config = Mock()
        mock_app._web_search = mock_search

        # 应用配置
        manager.apply_to_app(mock_app)

        # 验证搜索引擎配置更新被调用
        mock_search.update_config.assert_called_once()
        call_args = mock_search.update_config.call_args[0][0]
        assert call_args['timeout'] == 20
        assert call_args['default_engine'] == 'duckduckgo'

    def test_apply_to_app_with_llm_configure(self):
        """测试应用到带 LLM 配置的应用实例"""
        mock_secure = Mock()
        mock_secure.get_secure_value = Mock(return_value='sk-test-key-12345')

        manager = NetworkConfigManager(
            config_file=self.config_path,
            secure_manager=mock_secure
        )

        # 设置 LLM 配置
        manager.update({
            'llm': {
                'enabled': True,
                'provider': 'openai',
                'api_key': 'sk-test-key-12345',
                'model': 'gpt-4',
            }
        })

        # 创建模拟应用实例
        mock_app = Mock()
        mock_http = Mock()
        mock_http.timeout = 30
        mock_app._web_http = mock_http
        mock_app.configure_llm = Mock(return_value={'ok': True})

        # 应用配置
        manager.apply_to_app(mock_app)

        # 验证 configure_llm 被调用
        mock_app.configure_llm.assert_called_once_with(
            provider='openai',
            api_key='sk-test-key-12345',
            model='gpt-4'
        )

    def test_apply_to_app_llm_configure_failure(self):
        """测试 LLM 配置失败的处理"""
        mock_secure = Mock()
        mock_secure.get_secure_value = Mock(return_value='sk-test-key-12345')

        manager = NetworkConfigManager(
            config_file=self.config_path,
            secure_manager=mock_secure
        )

        # 设置 LLM 配置
        manager.update({
            'llm': {
                'enabled': True,
                'provider': 'openai',
                'api_key': 'sk-test-key-12345',
                'model': 'gpt-4',
            }
        })

        # 创建模拟应用实例（LLM 配置失败）
        mock_app = Mock()
        mock_http = Mock()
        mock_http.timeout = 30
        mock_app._web_http = mock_http
        mock_app.configure_llm = Mock(return_value={'ok': False, 'error': 'Invalid API Key'})

        # 应用配置（不应抛出异常）
        manager.apply_to_app(mock_app)

    def test_apply_to_app_llm_incomplete(self):
        """测试 LLM 配置不完整时不调用 configure_llm"""
        mock_secure = Mock()
        mock_secure.get_secure_value = Mock(return_value='')

        manager = NetworkConfigManager(
            config_file=self.config_path,
            secure_manager=mock_secure
        )

        # 设置不完整的 LLM 配置
        manager.update({
            'llm': {
                'enabled': True,
                'provider': 'openai',
                'api_key': '',  # 空 API Key
                'model': 'gpt-4',
            }
        })

        # 创建模拟应用实例
        mock_app = Mock()
        mock_http = Mock()
        mock_http.timeout = 30
        mock_app._web_http = mock_http
        mock_app.configure_llm = Mock(return_value={'ok': True})

        # 应用配置
        manager.apply_to_app(mock_app)

        # 验证 configure_llm 未被调用
        mock_app.configure_llm.assert_not_called()


class TestSearchEngineConfig:
    """测试搜索引擎配置相关功能"""

    def setup_method(self):
        self.temp_file = tempfile.NamedTemporaryFile(
            mode='w', suffix='.json', delete=False
        )
        self.temp_file.close()
        self.config_path = self.temp_file.name

    def teardown_method(self):
        if os.path.exists(self.config_path):
            os.unlink(self.config_path)

    def test_get_search_engines(self):
        """测试获取搜索引擎配置"""
        mock_secure = Mock()
        mock_secure.get_secure_value = Mock(return_value='')

        manager = NetworkConfigManager(
            config_file=self.config_path,
            secure_manager=mock_secure
        )

        # 设置搜索配置
        manager.update({
            'search': {
                'default_engine': 'tavily',
                'max_results': 15,
                'timeout': 20,
            }
        })

        engines = manager.get_search_engines()

        assert engines['default_engine'] == 'tavily'
        assert engines['max_results'] == 15
        assert engines['timeout'] == 20

    def test_get_search_engines_with_api_keys(self):
        """测试获取搜索引擎配置（包含 API Keys）"""
        mock_secure = Mock()
        mock_secure.get_secure_value = Mock(side_effect=lambda k, d: {
            'search_tavily_key': 'tvly-key',
            'search_bing_key': '',
        }.get(k, d))

        manager = NetworkConfigManager(
            config_file=self.config_path,
            secure_manager=mock_secure
        )

        # 设置 API Keys
        manager.update({
            'search_api_keys': {
                'tavily': 'tvly-key',
                'bing': '',
            }
        })

        engines = manager.get_search_engines()

        assert engines['api_keys']['tavily'] is True
        assert engines['api_keys']['bing'] is False

    def test_update_search_config_all_options(self):
        """测试更新搜索引擎配置（所有选项）"""
        mock_secure = Mock()
        mock_secure.get_secure_value = Mock(return_value='')

        manager = NetworkConfigManager(
            config_file=self.config_path,
            secure_manager=mock_secure
        )

        # 更新所有配置选项
        result = manager.update_search_config({
            'default_engine': 'tavily',
            'max_results': 20,
            'timeout': 30,
            'engine_priority': ['tavily', 'duckduckgo'],
            'engine_enabled': {'tavily': True, 'duckduckgo': False},
        })

        assert result['default_engine'] == 'tavily'
        assert result['max_results'] == 20
        assert result['timeout'] == 30

    def test_update_search_config_api_keys(self):
        """测试更新搜索引擎 API Keys"""
        mock_secure = Mock()
        mock_secure.set_secure_value = Mock()
        mock_secure.get_secure_value = Mock(return_value='')

        manager = NetworkConfigManager(
            config_file=self.config_path,
            secure_manager=mock_secure
        )

        # 更新 API Keys
        manager.update_search_config({
            'api_keys': {
                'tavily': 'tvly-new-key',
                'bing': 'bing-new-key',
            }
        })

        # 验证加密保存被调用
        assert mock_secure.set_secure_value.call_count >= 2

    def test_update_search_config_skip_masked_keys(self):
        """测试更新时跳过脱敏的 API Keys"""
        mock_secure = Mock()
        mock_secure.set_secure_value = Mock()
        mock_secure.get_secure_value = Mock(return_value='')  # 添加 get_secure_value

        manager = NetworkConfigManager(
            config_file=self.config_path,
            secure_manager=mock_secure
        )

        # 更新包含脱敏 Key
        manager.update_search_config({
            'api_keys': {
                'tavily': '***1234',  # 脱敏值
                'bing': 'bing-new-key',
            }
        })

        # 验证只有非脱敏的 key 被保存
        calls = [str(c) for c in mock_secure.set_secure_value.call_args_list]
        assert any('bing-new-key' in c for c in calls)
        assert not any('***1234' in c for c in calls)


class TestConfigValidationBoundary:
    """测试配置验证边界条件"""

    def setup_method(self):
        self.temp_file = tempfile.NamedTemporaryFile(
            mode='w', suffix='.json', delete=False
        )
        self.temp_file.close()
        self.config_path = self.temp_file.name

    def teardown_method(self):
        if os.path.exists(self.config_path):
            os.unlink(self.config_path)

    def test_validate_llm_instance_max_concurrent(self):
        """测试 LLM 实例最大并发请求数验证"""
        manager = NetworkConfigManager(config_file=self.config_path)

        # 无效：小于 1
        errors = manager.validate_llm_instance({
            'name': 'Test',
            'api_endpoint': 'https://api.example.com',
            'provider': 'openai',
            'max_concurrent_requests': 0,
        })
        assert any('最大并发请求数' in e for e in errors)

        # 无效：非整数
        errors = manager.validate_llm_instance({
            'name': 'Test',
            'api_endpoint': 'https://api.example.com',
            'provider': 'openai',
            'max_concurrent_requests': 3.5,
        })
        assert any('最大并发请求数' in e for e in errors)

        # 有效
        errors = manager.validate_llm_instance({
            'name': 'Test',
            'api_endpoint': 'https://api.example.com',
            'provider': 'openai',
            'max_concurrent_requests': 5,
        })
        assert not any('最大并发请求数' in e for e in errors)

    def test_validate_llm_instance_max_retries(self):
        """测试 LLM 实例最大重试次数验证"""
        manager = NetworkConfigManager(config_file=self.config_path)

        # 无效：小于 0
        errors = manager.validate_llm_instance({
            'name': 'Test',
            'api_endpoint': 'https://api.example.com',
            'provider': 'openai',
            'max_retries': -1,
        })
        assert any('最大重试次数' in e for e in errors)

        # 无效：超过 10
        errors = manager.validate_llm_instance({
            'name': 'Test',
            'api_endpoint': 'https://api.example.com',
            'provider': 'openai',
            'max_retries': 15,
        })
        assert any('最大重试次数' in e for e in errors)

        # 有效边界值
        errors = manager.validate_llm_instance({
            'name': 'Test',
            'api_endpoint': 'https://api.example.com',
            'provider': 'openai',
            'max_retries': 0,
        })
        assert not any('最大重试次数' in e for e in errors)

        errors = manager.validate_llm_instance({
            'name': 'Test',
            'api_endpoint': 'https://api.example.com',
            'provider': 'openai',
            'max_retries': 10,
        })
        assert not any('最大重试次数' in e for e in errors)

    def test_validate_mcp_service_missing_fields(self):
        """测试 MCP 服务必填字段验证"""
        manager = NetworkConfigManager(config_file=self.config_path)

        # 缺少名称
        errors = manager.validate_mcp_service({
            'address': 'localhost',
            'port': 8080,
        })
        assert any('服务名称' in e for e in errors)

        # 缺少地址
        errors = manager.validate_mcp_service({
            'name': 'TestMCP',
            'port': 8080,
        })
        assert any('MCP 服务地址' in e for e in errors)

        # 缺少端口
        errors = manager.validate_mcp_service({
            'name': 'TestMCP',
            'address': 'localhost',
        })
        assert any('通信端口' in e for e in errors)

    def test_validate_mcp_service_invalid_protocol(self):
        """测试 MCP 服务协议验证"""
        manager = NetworkConfigManager(config_file=self.config_path)

        # 无效协议
        errors = manager.validate_mcp_service({
            'name': 'TestMCP',
            'address': 'localhost',
            'port': 8080,
            'protocol': 'ftp',
        })
        assert any('协议类型' in e for e in errors)

    def test_validate_mcp_service_invalid_retry_strategy(self):
        """测试 MCP 服务重试策略验证"""
        manager = NetworkConfigManager(config_file=self.config_path)

        # 无效策略
        errors = manager.validate_mcp_service({
            'name': 'TestMCP',
            'address': 'localhost',
            'port': 8080,
            'retry_strategy': 'linear',
        })
        assert any('重试策略' in e for e in errors)

    def test_validate_mcp_service_port_boundary(self):
        """测试 MCP 服务端口边界值"""
        manager = NetworkConfigManager(config_file=self.config_path)

        # 最小有效端口
        errors = manager.validate_mcp_service({
            'name': 'TestMCP',
            'address': 'localhost',
            'port': 1,
        })
        assert not any('端口' in e for e in errors)

        # 最大有效端口
        errors = manager.validate_mcp_service({
            'name': 'TestMCP',
            'address': 'localhost',
            'port': 65535,
        })
        assert not any('端口' in e for e in errors)


class TestConfigMerge:
    """测试配置合并逻辑"""

    def setup_method(self):
        self.temp_file = tempfile.NamedTemporaryFile(
            mode='w', suffix='.json', delete=False
        )
        self.temp_file.close()
        self.config_path = self.temp_file.name

    def teardown_method(self):
        if os.path.exists(self.config_path):
            os.unlink(self.config_path)

    def test_import_skip_existing(self):
        """测试导入时跳过已存在的配置"""
        mock_secure = Mock()
        mock_secure.get_secure_value = Mock(return_value='')

        manager = NetworkConfigManager(
            config_file=self.config_path,
            secure_manager=mock_secure
        )

        # 设置初始配置（覆盖默认值）
        manager.update({'network': {'timeout': 30}})

        # 使用 overwrite 策略导入（完全覆盖）
        manager.import_config(
            json.dumps({'network': {'timeout': 60, 'max_retries': 5}}),
            'overwrite'
        )

        config = manager.get_all()
        # overwrite 策略完全替换
        assert config['network']['timeout'] == 60
        assert config['network']['max_retries'] == 5

    def test_import_merge_strategy(self):
        """测试导入合并策略"""
        mock_secure = Mock()
        mock_secure.get_secure_value = Mock(return_value='')

        manager = NetworkConfigManager(
            config_file=self.config_path,
            secure_manager=mock_secure
        )

        # 设置初始配置
        manager.update({
            'llm': {'timeout': 30, 'max_retries': 3},
            'network': {'timeout': 20},
        })

        # 使用 merge 策略导入
        manager.import_config(
            json.dumps({
                'llm': {'timeout': 60},  # 覆盖
                'search': {'timeout': 15},  # 新增
            }),
            'merge'
        )

        config = manager.get_all()
        assert config['llm']['timeout'] == 60  # 覆盖
        assert config['llm']['max_retries'] == 3  # 保持
        assert config['search']['timeout'] == 15  # 新增


class TestRawConfigAccess:
    """测试原始配置访问"""

    def setup_method(self):
        self.temp_file = tempfile.NamedTemporaryFile(
            mode='w', suffix='.json', delete=False
        )
        self.temp_file.close()
        self.config_path = self.temp_file.name

    def teardown_method(self):
        if os.path.exists(self.config_path):
            os.unlink(self.config_path)

    def test_get_raw_config_with_search_keys(self):
        """测试获取包含搜索 API Keys 的原始配置"""
        mock_secure = Mock()
        mock_secure.get_secure_value = Mock(side_effect=lambda k, d: {
            'search_tavily_key': 'tvly-real-key',
            'search_bing_key': 'bing-real-key',
        }.get(k, d))

        manager = NetworkConfigManager(
            config_file=self.config_path,
            secure_manager=mock_secure
        )

        config = manager.get_raw_config()

        # 原始配置应包含真实的 API Keys
        assert config['search_api_keys']['tavily'] == 'tvly-real-key'
        assert config['search_api_keys']['bing'] == 'bing-real-key'


# 运行测试
if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])