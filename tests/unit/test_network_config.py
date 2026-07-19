"""网络配置模块单元测试

测试覆盖：
1. 加密存储逻辑（敏感信息处理）
2. 配置脱敏逻辑
3. 配置导入/导出
4. LLM 实例管理
5. MCP 服务管理
6. 配置验证
7. 超时和重试参数验证
"""

import os
import json
import tempfile
import pytest
from unittest.mock import Mock, MagicMock, patch
from pathlib import Path

# 导入被测试模块
from agent.network_config import (
    NetworkConfigManager,
    _DEFAULT_NETWORK_CONFIG,
    _DEFAULT_LLM_INSTANCE,
    _DEFAULT_MCP_SERVICE,
)


class TestNetworkConfigEncryption:
    """测试 .env 单一数据源存储逻辑（原加密存储测试，适配纯 .env 架构）"""

    # 测试期间需要清理的环境变量
    _TEST_ENV_KEYS = ['LLM_API_KEY', 'ERROR_REPORTING_WEBHOOK_URL',
                      'SEARCH_TAVILY_API_KEY', 'SEARCH_BING_API_KEY',
                      'SEARCH_GOOGLE_API_KEY']

    def setup_method(self):
        """每个测试方法前设置临时配置文件 + 保存环境变量状态"""
        self.temp_file = tempfile.NamedTemporaryFile(
            mode='w', suffix='.json', delete=False
        )
        self.temp_file.close()
        self.config_path = self.temp_file.name
        # 保存环境变量状态，测试后恢复
        self._saved_env = {k: os.environ.get(k) for k in self._TEST_ENV_KEYS}
        for k in self._TEST_ENV_KEYS:
            os.environ.pop(k, None)

    def teardown_method(self):
        """每个测试方法后清理临时文件 + 恢复环境变量"""
        if os.path.exists(self.config_path):
            os.unlink(self.config_path)
        for k, v in self._saved_env.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)

    def test_sensitive_api_key_encrypted_on_save(self):
        """测试 LLM API Key 在保存时写入 .env（纯 .env 架构）"""
        manager = NetworkConfigManager(config_file=self.config_path)

        updates = {
            'llm': {
                'enabled': True,
                'provider': 'openai',
                'api_key': 'sk-real-api-key-12345',
                'model': 'gpt-4',
            }
        }
        manager.update(updates)

        # 验证 API Key 已写入 os.environ（热重载）
        assert os.getenv('LLM_API_KEY') == 'sk-real-api-key-12345'
        # 验证 network_config.json 不含明文
        with open(self.config_path, 'r', encoding='utf-8') as f:
            saved = json.load(f)
        assert saved.get('llm', {}).get('api_key', '') == ''

    def test_masked_api_key_not_saved(self):
        """测试脱敏后的 API Key 不被保存（行为不变）"""
        manager = NetworkConfigManager(config_file=self.config_path)

        updates = {'llm': {'api_key': '***1234'}}  # 脱敏值
        manager.update(updates)

        # 脱敏值不应写入环境变量
        assert os.getenv('LLM_API_KEY') is None

    def test_search_api_key_encrypted(self):
        """测试搜索引擎 API Key 写入 .env"""
        manager = NetworkConfigManager(config_file=self.config_path)

        updates = {
            'search_api_keys': {
                'tavily': 'tvly-real-key-123',
                'bing': 'bing-real-key-456',
                'google': 'google-real-key-789',
            }
        }
        manager.update(updates)

        # 验证各搜索引擎 Key 都写入 os.environ
        assert os.getenv('SEARCH_TAVILY_API_KEY') == 'tvly-real-key-123'
        assert os.getenv('SEARCH_BING_API_KEY') == 'bing-real-key-456'
        assert os.getenv('SEARCH_GOOGLE_API_KEY') == 'google-real-key-789'

    def test_webhook_url_encrypted(self):
        """测试 Webhook URL 写入 .env"""
        manager = NetworkConfigManager(config_file=self.config_path)

        updates = {
            'external_services': {
                'error_reporting': {
                    'webhook_url': 'https://hooks.example.com/real-webhook'
                }
            }
        }
        manager.update(updates)

        assert os.getenv('ERROR_REPORTING_WEBHOOK_URL') == 'https://hooks.example.com/real-webhook'

    def test_get_all_returns_masked_values(self):
        """测试 get_all() 返回脱敏后的配置"""
        os.environ['LLM_API_KEY'] = 'sk-real-key-12345'
        manager = NetworkConfigManager(config_file=self.config_path)

        config = manager.get_all()

        # API Key 应被脱敏为 *** + 后4位
        assert config['llm']['api_key'] == '***2345'

    def test_get_raw_config_returns_unmasked(self):
        """测试 get_raw_config() 返回原始配置"""
        os.environ['LLM_API_KEY'] = 'sk-real-key-12345'
        manager = NetworkConfigManager(config_file=self.config_path)

        config = manager.get_raw_config()

        # API Key 应为原始值
        assert config['llm']['api_key'] == 'sk-real-key-12345'

    def test_no_secure_manager_warning(self):
        """测试无 SecureManager 时敏感信息写入 .env（新架构下 secure_manager 已废弃）"""
        manager = NetworkConfigManager(
            config_file=self.config_path
        )

        updates = {'llm': {'api_key': 'sk-test-key'}}
        manager.update(updates)

        # 新架构下，无论 secure_manager 是否为 None，都写入 .env
        assert os.getenv('LLM_API_KEY') == 'sk-test-key'
        # network_config.json 不含明文
        with open(self.config_path, 'r', encoding='utf-8') as f:
            saved_config = json.load(f)
        assert saved_config.get('llm', {}).get('api_key', '') == ''


class TestNetworkConfigMasking:
    """测试配置脱敏逻辑（适配纯 .env 架构）"""

    def setup_method(self):
        self.temp_file = tempfile.NamedTemporaryFile(
            mode='w', suffix='.json', delete=False
        )
        self.temp_file.close()
        self.config_path = self.temp_file.name
        self._saved_llm_key = os.environ.get('LLM_API_KEY')
        os.environ.pop('LLM_API_KEY', None)

    def teardown_method(self):
        if os.path.exists(self.config_path):
            os.unlink(self.config_path)
        if self._saved_llm_key is not None:
            os.environ['LLM_API_KEY'] = self._saved_llm_key
        else:
            os.environ.pop('LLM_API_KEY', None)

    def test_short_api_key_masking(self):
        """测试短 API Key 脱敏（少于 4 字符）"""
        os.environ['LLM_API_KEY'] = 'abc'
        manager = NetworkConfigManager(config_file=self.config_path)

        config = manager.get_all()

        # 短 Key 应被完全脱敏
        assert config['llm']['api_key'] == '***'

    def test_empty_api_key_no_masking(self):
        """测试空 API Key 不脱敏"""
        os.environ.pop('LLM_API_KEY', None)  # 确保为空
        manager = NetworkConfigManager(config_file=self.config_path)

        config = manager.get_all()

        # 空 Key 应保持空
        assert config['llm']['api_key'] == ''

    def test_llm_instance_api_key_masking(self):
        """测试 LLM 实例 API Key 脱敏"""
        manager = NetworkConfigManager(config_file=self.config_path)

        # 添加 LLM 实例
        manager.add_llm_instance({
            'name': 'TestInstance',
            'provider': 'openai',
            'api_key': 'sk-instance-key-12345',
            'model': 'gpt-4',
        })

        config = manager.get_all()

        # 实例 API Key 应被脱敏为 *** + 后4位
        instances = config.get('llm_instances', [])
        assert len(instances) > 0
        assert instances[0]['api_key'] == '***2345'


class TestNetworkConfigTimeout:
    """测试超时和重试参数验证"""

    def setup_method(self):
        self.temp_file = tempfile.NamedTemporaryFile(
            mode='w', suffix='.json', delete=False
        )
        self.temp_file.close()
        self.config_path = self.temp_file.name

    def teardown_method(self):
        if os.path.exists(self.config_path):
            os.unlink(self.config_path)

    def test_valid_timeout_accepted(self):
        """测试有效超时值被接受"""
        manager = NetworkConfigManager(config_file=self.config_path)

        updates = {
            'network': {'timeout': 60},
            'llm': {'timeout': 120},
        }
        manager.update(updates)

        config = manager.get_all()
        assert config['network']['timeout'] == 60
        assert config['llm']['timeout'] == 120

    def test_llm_instance_timeout_validation(self):
        """测试 LLM 实例超时范围验证"""
        manager = NetworkConfigManager(config_file='dummy.json')

        # 有效超时
        errors = manager.validate_llm_instance({
            'name': 'Test',
            'api_endpoint': 'https://api.example.com',
            'provider': 'openai',
            'timeout': 60,
        })
        assert not any('超时时间' in e for e in errors)

        # 无效超时（超过 300）
        errors = manager.validate_llm_instance({
            'name': 'Test',
            'api_endpoint': 'https://api.example.com',
            'provider': 'openai',
            'timeout': 500,
        })
        assert any('超时时间' in e for e in errors)

        # 无效超时（小于 1）
        errors = manager.validate_llm_instance({
            'name': 'Test',
            'api_endpoint': 'https://api.example.com',
            'provider': 'openai',
            'timeout': 0,
        })
        assert any('超时时间' in e for e in errors)

    def test_mcp_service_timeout_validation(self):
        """测试 MCP 服务超时范围验证"""
        manager = NetworkConfigManager(config_file=self.config_path)

        # 有效超时
        errors = manager.validate_mcp_service({
            'name': 'TestMCP',
            'address': 'localhost',
            'port': 8080,
            'timeout': 30,
        })
        assert '超时时间' not in str(errors)

        # 无效超时
        errors = manager.validate_mcp_service({
            'name': 'TestMCP',
            'address': 'localhost',
            'port': 8080,
            'timeout': 500,
        })
        assert any('超时时间' in e for e in errors)

    def test_max_retries_validation(self):
        """测试最大重试次数验证"""
        manager = NetworkConfigManager(config_file=self.config_path)

        # 有效重试次数
        errors = manager.validate_llm_instance({
            'name': 'Test',
            'api_endpoint': 'https://api.example.com',
            'provider': 'openai',
            'max_retries': 5,
        })
        assert '重试次数' not in str(errors)

        # 无效重试次数（超过 10）
        errors = manager.validate_llm_instance({
            'name': 'Test',
            'api_endpoint': 'https://api.example.com',
            'provider': 'openai',
            'max_retries': 15,
        })
        assert any('重试次数' in e for e in errors)


class TestNetworkConfigImportExport:
    """测试配置导入/导出"""

    def setup_method(self):
        self.temp_file = tempfile.NamedTemporaryFile(
            mode='w', suffix='.json', delete=False
        )
        self.temp_file.close()
        self.config_path = self.temp_file.name
        self._saved_llm_key = os.environ.get('LLM_API_KEY')
        os.environ.pop('LLM_API_KEY', None)

    def teardown_method(self):
        if os.path.exists(self.config_path):
            os.unlink(self.config_path)
        if self._saved_llm_key is not None:
            os.environ['LLM_API_KEY'] = self._saved_llm_key
        else:
            os.environ.pop('LLM_API_KEY', None)

    def test_export_returns_json_string(self):
        """测试导出返回 JSON 字符串"""
        manager = NetworkConfigManager(config_file=self.config_path)

        exported = manager.export_config()

        # 应为有效 JSON
        parsed = json.loads(exported)
        assert 'llm' in parsed
        assert 'network' in parsed

    def test_export_masks_sensitive_data(self):
        """测试导出时脱敏敏感数据（纯 .env 架构：从 os.environ 读取）"""
        os.environ['LLM_API_KEY'] = 'sk-real-key-12345'
        manager = NetworkConfigManager(config_file=self.config_path)

        exported = manager.export_config()
        parsed = json.loads(exported)

        # API Key 应被脱敏为 *** + 后4位
        assert parsed['llm']['api_key'] == '***2345'

    def test_import_valid_json(self):
        """测试导入有效 JSON"""

        manager = NetworkConfigManager(
            config_file=self.config_path
        )

        import_json = json.dumps({
            'llm': {'timeout': 120},
            'network': {'timeout': 60},
        })

        result = manager.import_config(import_json, 'overwrite')

        assert result['llm']['timeout'] == 120
        assert result['network']['timeout'] == 60

    def test_import_invalid_json_raises_error(self):
        """测试导入无效 JSON 抛出错误"""
        manager = NetworkConfigManager(config_file=self.config_path)

        with pytest.raises(ValueError) as exc_info:
            manager.import_config('invalid json string')

        assert '配置格式错误' in str(exc_info.value)

    def test_import_conflict_strategies(self):
        """测试导入冲突处理策略"""

        manager = NetworkConfigManager(
            config_file=self.config_path
        )

        # 初始配置
        manager.update({'llm': {'timeout': 30}})

        # overwrite: 完全覆盖
        manager.import_config(
            json.dumps({'llm': {'timeout': 60}}),
            'overwrite'
        )
        assert manager.get_all()['llm']['timeout'] == 60

        # skip: 跳过已存在
        manager.import_config(
            json.dumps({'llm': {'timeout': 120}}),
            'skip'
        )
        # timeout 应保持原值（因为已存在）
        assert manager.get_all()['llm']['timeout'] == 60

        # merge: 合并配置
        manager.import_config(
            json.dumps({'llm': {'max_retries': 5}}),
            'merge'
        )
        config = manager.get_all()
        assert config['llm']['timeout'] == 60  # 保持原值
        assert config['llm']['max_retries'] == 5  # 新增


class TestLLMConfigIntegration:
    """测试 LLM 配置合并功能（默认配置 + 多实例管理）"""

    def setup_method(self):
        self.temp_file = tempfile.NamedTemporaryFile(
            mode='w', suffix='.json', delete=False
        )
        self.temp_file.close()
        self.config_path = self.temp_file.name

    def teardown_method(self):
        if os.path.exists(self.config_path):
            os.unlink(self.config_path)

    def test_default_llm_instance_config_exists(self):
        """测试默认配置中存在 default_llm_instance 字段"""
        manager = NetworkConfigManager(config_file=self.config_path)
        config = manager.get_all()
        
        assert 'default_llm_instance' in config
        assert config['default_llm_instance'] == ''

    def test_set_default_llm_instance_updates_config(self):
        """测试设置默认实例同时更新 default_llm_instance 字段"""

        manager = NetworkConfigManager(
            config_file=self.config_path
        )

        instance1 = manager.add_llm_instance({
            'name': 'Instance1',
            'provider': 'openai',
            'api_endpoint': 'https://api.example.com',
        })

        manager.set_default_llm_instance(instance1['id'])

        config = manager.get_all()
        assert config['default_llm_instance'] == instance1['id']

    def test_set_default_updates_is_default_flag(self):
        """测试设置默认实例时更新 is_default 标记"""

        manager = NetworkConfigManager(
            config_file=self.config_path
        )

        instance1 = manager.add_llm_instance({
            'name': 'Instance1',
            'provider': 'openai',
            'api_endpoint': 'https://api.example.com',
        })

        instance2 = manager.add_llm_instance({
            'name': 'Instance2',
            'provider': 'deepseek',
            'api_endpoint': 'https://api.deepseek.com',
        })

        manager.set_default_llm_instance(instance2['id'])

        instances = manager.get_llm_instances()
        assert instances[0]['is_default'] == False
        assert instances[1]['is_default'] == True

    def test_default_llm_instance_persists_on_reload(self):
        """测试默认实例配置在重新加载后保持不变"""

        manager = NetworkConfigManager(
            config_file=self.config_path
        )

        instance = manager.add_llm_instance({
            'name': 'PersistentInstance',
            'provider': 'openai',
            'api_endpoint': 'https://api.example.com',
        })

        manager.set_default_llm_instance(instance['id'])

        # 创建新实例模拟重新加载
        manager2 = NetworkConfigManager(
            config_file=self.config_path
        )

        config = manager2.get_all()
        assert config['default_llm_instance'] == instance['id']

    def test_default_llm_instance_empty_when_no_instances(self):
        """测试无实例时 default_llm_instance 为空"""
        manager = NetworkConfigManager(config_file=self.config_path)
        config = manager.get_all()
        
        assert config['default_llm_instance'] == ''

    def test_ensure_config_structure_adds_default_field(self):
        """测试配置结构确保函数添加 default_llm_instance 字段"""
        # 创建一个完整但缺少 default_llm_instance 的配置文件
        complete_config = {
            'llm': {'enabled': True, 'provider': '', 'api_key': '', 'model': '', 'api_endpoint': '', 'timeout': 30, 'max_retries': 3},
            'llm_instances': [],
            'network': {'timeout': 30, 'retry_delay': 1, 'max_retries': 3},
            'external_services': {'error_reporting': {'enabled': False, 'webhook_url': ''}},
            'search_api_keys': {'tavily': '', 'bing': '', 'google': ''},
            'mcp': {'enabled': False, 'services': []},
            'change_log': []
        }
        with open(self.config_path, 'w') as f:
            json.dump(complete_config, f)

        manager = NetworkConfigManager(
            config_file=self.config_path
        )

        config = manager.get_all()
        assert 'default_llm_instance' in config


class TestLLMInstanceManagement:
    """测试 LLM 实例管理"""

    def setup_method(self):
        self.temp_file = tempfile.NamedTemporaryFile(
            mode='w', suffix='.json', delete=False
        )
        self.temp_file.close()
        self.config_path = self.temp_file.name

    def teardown_method(self):
        if os.path.exists(self.config_path):
            os.unlink(self.config_path)

    def test_add_llm_instance(self):
        """测试添加 LLM 实例"""

        manager = NetworkConfigManager(
            config_file=self.config_path
        )

        instance = manager.add_llm_instance({
            'name': 'TestInstance',
            'provider': 'openai',
            'api_key': 'sk-test-key',
            'model': 'gpt-4',
            'api_endpoint': 'https://api.openai.com/v1',
        })

        assert instance['name'] == 'TestInstance'
        assert instance['provider'] == 'openai'
        assert 'id' in instance
        assert 'created_at' in instance

    def test_add_instance_with_duplicate_name_fails(self):
        """测试添加重名实例失败"""

        manager = NetworkConfigManager(
            config_file=self.config_path
        )

        manager.add_llm_instance({
            'name': 'Instance1',
            'provider': 'openai',
            'api_endpoint': 'https://api.example.com',
        })

        with pytest.raises(ValueError) as exc_info:
            manager.add_llm_instance({
                'name': 'Instance1',  # 重名
                'provider': 'deepseek',
                'api_endpoint': 'https://api.deepseek.com',
            })

        assert '名称已存在' in str(exc_info.value)

    def test_update_llm_instance(self):
        """测试更新 LLM 实例"""

        manager = NetworkConfigManager(
            config_file=self.config_path
        )

        instance = manager.add_llm_instance({
            'name': 'TestInstance',
            'provider': 'openai',
            'api_endpoint': 'https://api.example.com',
        })

        updated = manager.update_llm_instance(
            instance['id'],
            {'timeout': 120}
        )

        assert updated['timeout'] == 120
        assert 'updated_at' in updated

    def test_delete_llm_instance(self):
        """测试删除 LLM 实例"""

        manager = NetworkConfigManager(
            config_file=self.config_path
        )

        instance = manager.add_llm_instance({
            'name': 'ToDelete',
            'provider': 'openai',
            'api_endpoint': 'https://api.example.com',
        })

        result = manager.delete_llm_instance(instance['id'])
        assert result is True

        # 验证实例已删除
        instances = manager.get_llm_instances()
        assert not any(i['name'] == 'ToDelete' for i in instances)

    def test_set_default_llm_instance(self):
        """测试设置默认 LLM 实例"""

        manager = NetworkConfigManager(
            config_file=self.config_path
        )

        instance1 = manager.add_llm_instance({
            'name': 'Instance1',
            'provider': 'openai',
            'api_endpoint': 'https://api.example.com',
        })

        instance2 = manager.add_llm_instance({
            'name': 'Instance2',
            'provider': 'deepseek',
            'api_endpoint': 'https://api.deepseek.com',
        })

        manager.set_default_llm_instance(instance2['id'])

        instances = manager.get_llm_instances()
        default = next(i for i in instances if i.get('is_default'))
        assert default['name'] == 'Instance2'


class TestMCPServiceManagement:
    """测试 MCP 服务管理"""

    def setup_method(self):
        self.temp_file = tempfile.NamedTemporaryFile(
            mode='w', suffix='.json', delete=False
        )
        self.temp_file.close()
        self.config_path = self.temp_file.name

    def teardown_method(self):
        if os.path.exists(self.config_path):
            os.unlink(self.config_path)

    def test_add_mcp_service(self):
        """测试添加 MCP 服务"""

        manager = NetworkConfigManager(
            config_file=self.config_path
        )

        service = manager.add_mcp_service({
            'name': 'TestMCP',
            'address': 'localhost',
            'port': 8080,
            'protocol': 'http',
        })

        assert service['name'] == 'TestMCP'
        assert service['port'] == 8080
        assert 'id' in service

    def test_add_mcp_with_duplicate_name_fails(self):
        """测试添加重名 MCP 服务失败"""

        manager = NetworkConfigManager(
            config_file=self.config_path
        )

        manager.add_mcp_service({
            'name': 'MCP1',
            'address': 'localhost',
            'port': 8080,
        })

        with pytest.raises(ValueError):
            manager.add_mcp_service({
                'name': 'MCP1',
                'address': '127.0.0.1',
                'port': 9090,
            })

    def test_update_mcp_service(self):
        """测试更新 MCP 服务"""

        manager = NetworkConfigManager(
            config_file=self.config_path
        )

        service = manager.add_mcp_service({
            'name': 'TestMCP',
            'address': 'localhost',
            'port': 8080,
        })

        updated = manager.update_mcp_service(
            service['id'],
            {'timeout': 60}
        )

        assert updated['timeout'] == 60

    def test_delete_mcp_service(self):
        """测试删除 MCP 服务"""

        manager = NetworkConfigManager(
            config_file=self.config_path
        )

        service = manager.add_mcp_service({
            'name': 'ToDelete',
            'address': 'localhost',
            'port': 8080,
        })

        result = manager.delete_mcp_service(service['id'])
        assert result is True


class TestConfigValidation:
    """测试配置验证"""

    def test_validate_llm_instance_url(self):
        """测试 LLM 实例 URL 格式验证"""
        manager = NetworkConfigManager(config_file='dummy.json')

        # 有效 URL
        errors = manager.validate_llm_instance({
            'name': 'Test',
            'api_endpoint': 'https://api.openai.com/v1',
            'provider': 'openai',
        })
        assert 'URL 格式无效' not in str(errors)

        # 无效 URL（无协议）
        errors = manager.validate_llm_instance({
            'name': 'Test',
            'api_endpoint': 'api.openai.com',
            'provider': 'openai',
        })
        assert any('URL 格式无效' in e for e in errors)

        # 无效 URL（无主机）
        errors = manager.validate_llm_instance({
            'name': 'Test',
            'api_endpoint': 'https://',
            'provider': 'openai',
        })
        assert any('URL 格式无效' in e for e in errors)

    def test_validate_mcp_port_range(self):
        """测试 MCP 端口范围验证"""
        manager = NetworkConfigManager(config_file='dummy.json')

        # 有效端口
        errors = manager.validate_mcp_service({
            'name': 'Test',
            'address': 'localhost',
            'port': 8080,
        })
        assert '端口' not in str(errors)

        # 无效端口（超过 65535）
        errors = manager.validate_mcp_service({
            'name': 'Test',
            'address': 'localhost',
            'port': 70000,
        })
        assert any('端口' in e for e in errors)

        # 无效端口（小于 1）
        errors = manager.validate_mcp_service({
            'name': 'Test',
            'address': 'localhost',
            'port': 0,
        })
        assert any('端口' in e for e in errors)

    def test_validate_mcp_protocol(self):
        """测试 MCP 协议类型验证"""
        manager = NetworkConfigManager(config_file='dummy.json')

        # 有效协议
        errors = manager.validate_mcp_service({
            'name': 'Test',
            'address': 'localhost',
            'port': 8080,
            'protocol': 'http',
        })
        assert '协议类型' not in str(errors)

        # 无效协议
        errors = manager.validate_mcp_service({
            'name': 'Test',
            'address': 'localhost',
            'port': 8080,
            'protocol': 'ftp',
        })
        assert any('协议类型' in e for e in errors)

    def test_validate_mcp_retry_strategy(self):
        """测试 MCP 重试策略验证"""
        manager = NetworkConfigManager(config_file='dummy.json')

        # 有效策略
        for strategy in ['fixed', 'exponential', 'none']:
            errors = manager.validate_mcp_service({
                'name': 'Test',
                'address': 'localhost',
                'port': 8080,
                'retry_strategy': strategy,
            })
            assert '重试策略' not in str(errors)

        # 无效策略
        errors = manager.validate_mcp_service({
            'name': 'Test',
            'address': 'localhost',
            'port': 8080,
            'retry_strategy': 'invalid',
        })
        assert any('重试策略' in e for e in errors)


class TestConfigReset:
    """测试配置重置"""

    def setup_method(self):
        self.temp_file = tempfile.NamedTemporaryFile(
            mode='w', suffix='.json', delete=False
        )
        self.temp_file.close()
        self.config_path = self.temp_file.name

    def teardown_method(self):
        if os.path.exists(self.config_path):
            os.unlink(self.config_path)

    def test_reset_returns_default_config(self):
        """测试重置返回默认配置"""

        manager = NetworkConfigManager(
            config_file=self.config_path
        )

        # 先修改配置
        manager.update({'llm': {'timeout': 120}})

        # 重置
        result = manager.reset()

        # 应为默认值
        assert result['llm']['timeout'] == 30
        assert result['network']['timeout'] == 30

    def test_reset_clears_llm_instances(self):
        """测试重置清除 LLM 实例"""
        manager = NetworkConfigManager(config_file=self.config_path)

        manager.add_llm_instance({
            'name': 'Test',
            'provider': 'openai',
            'api_endpoint': 'https://api.example.com',
        })

        manager.reset()

        instances = manager.get_llm_instances()
        assert len(instances) == 0


class TestChangeLog:
    """测试配置变更日志"""

    def setup_method(self):
        self.temp_file = tempfile.NamedTemporaryFile(
            mode='w', suffix='.json', delete=False
        )
        self.temp_file.close()
        self.config_path = self.temp_file.name

    def teardown_method(self):
        if os.path.exists(self.config_path):
            os.unlink(self.config_path)

    def test_update_adds_change_log(self):
        """测试更新添加变更日志"""

        manager = NetworkConfigManager(
            config_file=self.config_path
        )

        manager.update({'llm': {'timeout': 60}})

        logs = manager.get_change_log()
        assert len(logs) > 0
        assert logs[0]['action'] == 'update'

    def test_change_log_limit(self):
        """测试变更日志限制（最多 100 条）"""
        manager = NetworkConfigManager(config_file=self.config_path)

        # 添加超过 100 条日志
        for i in range(150):
            manager.update({'llm': {'timeout': 30 + i}})

        logs = manager.get_change_log(limit=200)
        assert len(logs) <= 100


# 运行测试
if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])