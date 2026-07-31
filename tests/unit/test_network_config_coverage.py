"""NetworkConfig 未覆盖路径单元测试

【不易】覆盖配置加载/保存/更新的异常路径和敏感数据处理
【变易】使用 tempfile 隔离测试环境，不污染真实配置文件
【简易】复用现有 test_network_config.py 的 setup/teardown 模式

覆盖目标：NetworkConfig 覆盖率 70.3% → 80%+
针对未覆盖行号：170, 220, 246, 251-252, 282-294, 386-387, 433-501, 529-550, 560-667, 698-723, 781-881

测试维度：
    1. _load() 配置文件不存在/损坏时的默认配置路径
    2. _ensure_config_structure() 字段补全逻辑
    3. _save() 敏感数据剥离逻辑
    4. update() 完整更新流程（LLM/搜索实例/MCP）
    5. _update_llm_instances() 新增/更新实例
    6. _update_search_instances() 新增/更新实例
    7. import_config() / export_config() 往返测试
    8. reset() 重置逻辑
    9. get_raw_config() vs get_all() 脱敏对比
"""
import os
import json
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from agent.network_config import (
    NetworkConfigManager,
    _DEFAULT_NETWORK_CONFIG,
    _DEFAULT_LLM_INSTANCE,
    _DEFAULT_MCP_SERVICE,
)


class TestLoadEdgeCases:
    """测试 _load() 异常路径（覆盖行 220-241, 234-236）"""

    _TEST_ENV_KEYS = ['LLM_API_KEY', 'ERROR_REPORTING_WEBHOOK_URL']

    def setup_method(self):
        self.temp_file = tempfile.NamedTemporaryFile(
            mode='w', suffix='.json', delete=False
        )
        self.temp_file.close()
        self.config_path = self.temp_file.name
        self._saved_env = {k: os.environ.get(k) for k in self._TEST_ENV_KEYS}
        for k in self._TEST_ENV_KEYS:
            os.environ.pop(k, None)

    def teardown_method(self):
        if os.path.exists(self.config_path):
            os.unlink(self.config_path)
        for k, v in self._saved_env.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)

    def test_load_creates_default_when_file_missing(self):
        """配置文件不存在时应创建默认配置并保存"""
        # 删除临时文件模拟不存在
        os.unlink(self.config_path)
        manager = NetworkConfigManager(config_file=self.config_path)

        config = manager._load()

        # 验证返回默认配置
        assert config['llm']['enabled'] is True
        assert 'llm_instances' in config
        # 验证文件已创建
        assert os.path.exists(self.config_path)

    def test_load_uses_default_on_json_decode_error(self):
        """JSON 解析失败时应使用默认配置（覆盖行 234-236）"""
        with open(self.config_path, 'w', encoding='utf-8') as f:
            f.write("{invalid json content !!!}")

        manager = NetworkConfigManager(config_file=self.config_path)
        config = manager._load()

        # 验证使用默认配置
        assert config['llm']['enabled'] is True
        assert 'llm_instances' in config

    def test_load_uses_default_on_oserror(self):
        """OSError 时应使用默认配置"""
        manager = NetworkConfigManager(config_file=self.config_path)

        # mock open 抛 OSError
        with patch('builtins.open', side_effect=OSError("permission denied")):
            config = manager._load()

        assert config == _DEFAULT_NETWORK_CONFIG or 'llm' in config

    def test_load_caches_result(self):
        """_load 应缓存结果，第二次调用不重新读文件"""
        manager = NetworkConfigManager(config_file=self.config_path)
        config1 = manager._load()
        # 修改文件内容
        with open(self.config_path, 'w', encoding='utf-8') as f:
            json.dump({"llm": {"enabled": False}}, f)
        # 第二次应返回缓存
        config2 = manager._load()
        assert config1 is config2, "应返回缓存对象"


class TestEnsureConfigStructure:
    """测试 _ensure_config_structure() 字段补全（覆盖行 243-288）"""

    _TEST_ENV_KEYS = ['LLM_API_KEY']

    def setup_method(self):
        self.temp_file = tempfile.NamedTemporaryFile(
            mode='w', suffix='.json', delete=False
        )
        self.temp_file.close()
        self.config_path = self.temp_file.name
        self._saved_env = {k: os.environ.get(k) for k in self._TEST_ENV_KEYS}
        for k in self._TEST_ENV_KEYS:
            os.environ.pop(k, None)

    def teardown_method(self):
        if os.path.exists(self.config_path):
            os.unlink(self.config_path)
        for k, v in self._saved_env.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)

    def test_ensures_llm_instances_exists(self):
        """缺少 llm_instances 时应补全为空列表"""
        with open(self.config_path, 'w', encoding='utf-8') as f:
            json.dump({"llm": {"enabled": True}}, f)

        manager = NetworkConfigManager(config_file=self.config_path)
        config = manager._load()

        assert config['llm_instances'] == []
        assert config['default_llm_instance'] == ''

    def test_ensures_mcp_config_exists(self):
        """缺少 mcp 配置时应补全"""
        with open(self.config_path, 'w', encoding='utf-8') as f:
            json.dump({"llm": {}}, f)

        manager = NetworkConfigManager(config_file=self.config_path)
        config = manager._load()

        assert 'mcp' in config
        assert config['mcp']['enabled'] is False
        assert config['mcp']['services'] == []

    def test_ensures_external_services_exists(self):
        """缺少 external_services 时应补全 error_reporting"""
        with open(self.config_path, 'w', encoding='utf-8') as f:
            json.dump({"llm": {}}, f)

        manager = NetworkConfigManager(config_file=self.config_path)
        config = manager._load()

        assert 'external_services' in config
        assert 'error_reporting' in config['external_services']

    def test_ensures_search_instances_exists(self):
        """缺少 search_instances 时应补全为空列表"""
        with open(self.config_path, 'w', encoding='utf-8') as f:
            json.dump({"llm": {}}, f)

        manager = NetworkConfigManager(config_file=self.config_path)
        config = manager._load()

        assert config['search_instances'] == []

    def test_ensures_llm_instance_has_id(self):
        """LLM 实例缺少 ID 时应自动生成"""
        with open(self.config_path, 'w', encoding='utf-8') as f:
            json.dump({
                "llm": {},
                "llm_instances": [{"name": "test-instance", "provider": "openai"}]
            }, f)

        manager = NetworkConfigManager(config_file=self.config_path)
        config = manager._load()

        assert config['llm_instances'][0]['id'] is not None
        assert len(config['llm_instances'][0]['id']) > 0

    def test_ensures_search_instance_has_id(self):
        """搜索实例缺少 ID 时应自动生成"""
        with open(self.config_path, 'w', encoding='utf-8') as f:
            json.dump({
                "llm": {},
                "search_instances": [{"engine": "tavily"}]
            }, f)

        manager = NetworkConfigManager(config_file=self.config_path)
        config = manager._load()

        assert config['search_instances'][0]['id'] is not None


class TestSaveStripsSensitiveData:
    """测试 _save() 敏感数据剥离（覆盖行 290-322）"""

    _TEST_ENV_KEYS = ['LLM_API_KEY', 'ERROR_REPORTING_WEBHOOK_URL',
                      'LLM_TEST123_API_KEY', 'SEARCH_TAVILY_API_KEY']

    def setup_method(self):
        self.temp_file = tempfile.NamedTemporaryFile(
            mode='w', suffix='.json', delete=False
        )
        self.temp_file.close()
        self.config_path = self.temp_file.name
        self._saved_env = {k: os.environ.get(k) for k in self._TEST_ENV_KEYS}
        for k in self._TEST_ENV_KEYS:
            os.environ.pop(k, None)

    def teardown_method(self):
        if os.path.exists(self.config_path):
            os.unlink(self.config_path)
        for k, v in self._saved_env.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)

    def test_save_strips_llm_api_key(self):
        """_save 应从 JSON 文件中移除 llm.api_key"""
        manager = NetworkConfigManager(config_file=self.config_path)
        config = {
            "llm": {"enabled": True, "api_key": "sk-secret-key", "model": "gpt-4"},
            "llm_instances": [],
            "search_instances": [],
        }
        manager._save(config)

        with open(self.config_path, 'r', encoding='utf-8') as f:
            saved = json.load(f)

        assert 'api_key' not in saved['llm'], "api_key 不应保留在 JSON 文件中"

    def test_save_strips_webhook_url(self):
        """_save 应移除 error_reporting.webhook_url"""
        manager = NetworkConfigManager(config_file=self.config_path)
        config = {
            "llm": {},
            "external_services": {
                "error_reporting": {"enabled": True, "webhook_url": "https://hook.example.com"}
            },
            "llm_instances": [],
            "search_instances": [],
        }
        manager._save(config)

        with open(self.config_path, 'r', encoding='utf-8') as f:
            saved = json.load(f)

        assert 'webhook_url' not in saved['external_services']['error_reporting']

    def test_save_strips_llm_instance_api_keys(self):
        """_save 应移除所有 llm_instances 的 api_key"""
        manager = NetworkConfigManager(config_file=self.config_path)
        config = {
            "llm": {},
            "llm_instances": [
                {"id": "inst1", "api_key": "key1"},
                {"id": "inst2", "api_key": "key2"},
            ],
            "search_instances": [],
        }
        manager._save(config)

        with open(self.config_path, 'r', encoding='utf-8') as f:
            saved = json.load(f)

        for inst in saved['llm_instances']:
            assert 'api_key' not in inst, f"实例 {inst.get('id')} 的 api_key 未被移除"

    def test_save_strips_search_instance_api_keys(self):
        """_save 应移除所有 search_instances 的 api_key"""
        manager = NetworkConfigManager(config_file=self.config_path)
        config = {
            "llm": {},
            "llm_instances": [],
            "search_instances": [
                {"id": "s1", "api_key": "search-key-1"},
            ],
        }
        manager._save(config)

        with open(self.config_path, 'r', encoding='utf-8') as f:
            saved = json.load(f)

        for inst in saved['search_instances']:
            assert 'api_key' not in inst

    def test_save_updates_cache(self):
        """_save 应同步更新缓存为已保存的数据（不含 api_key）"""
        manager = NetworkConfigManager(config_file=self.config_path)
        config = {
            "llm": {"api_key": "secret"},
            "llm_instances": [],
            "search_instances": [],
        }
        manager._save(config)

        # 缓存应与文件一致（不含 api_key）
        cached = manager._cache
        assert 'api_key' not in cached.get('llm', {})


class TestUpdateFlow:
    """测试 update() 完整更新流程（覆盖行 476-542）"""

    _TEST_ENV_KEYS = ['LLM_API_KEY', 'ERROR_REPORTING_WEBHOOK_URL',
                      'LLM_INST001_API_KEY', 'SEARCH_TAVILY_API_KEY']

    def setup_method(self):
        self.temp_file = tempfile.NamedTemporaryFile(
            mode='w', suffix='.json', delete=False
        )
        self.temp_file.close()
        self.config_path = self.temp_file.name
        self._saved_env = {k: os.environ.get(k) for k in self._TEST_ENV_KEYS}
        for k in self._TEST_ENV_KEYS:
            os.environ.pop(k, None)

    def teardown_method(self):
        if os.path.exists(self.config_path):
            os.unlink(self.config_path)
        for k, v in self._saved_env.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)

    def test_update_llm_api_key_saved_to_env(self):
        """LLM API Key 应写入环境变量而非 JSON 文件"""
        manager = NetworkConfigManager(config_file=self.config_path)

        manager.update({
            'llm': {
                'enabled': True,
                'provider': 'openai',
                'api_key': 'sk-test-update-key',
                'model': 'gpt-4',
            }
        })

        # API Key 应在环境变量中
        assert os.getenv('LLM_API_KEY') == 'sk-test-update-key'
        # JSON 文件中不应有 api_key
        with open(self.config_path, 'r', encoding='utf-8') as f:
            saved = json.load(f)
        assert 'api_key' not in saved.get('llm', {})

    def test_update_skips_masked_api_key(self):
        """脱敏值（***开头的 api_key）应跳过更新"""
        manager = NetworkConfigManager(config_file=self.config_path)

        # 先设置一个真实 key
        manager.update({'llm': {'api_key': 'sk-real-key'}})
        assert os.getenv('LLM_API_KEY') == 'sk-real-key'

        # 传入脱敏值，不应覆盖
        manager.update({'llm': {'api_key': '***masked-key'}})
        assert os.getenv('LLM_API_KEY') == 'sk-real-key', "脱敏值不应覆盖真实 key"

    def test_update_webhook_url_saved_to_env(self):
        """Webhook URL 应写入环境变量"""
        manager = NetworkConfigManager(config_file=self.config_path)

        manager.update({
            'external_services': {
                'error_reporting': {
                    'enabled': True,
                    'webhook_url': 'https://hooks.slack.com/test',
                }
            }
        })

        assert os.getenv('ERROR_REPORTING_WEBHOOK_URL') == 'https://hooks.slack.com/test'

    def test_update_adds_new_llm_instance(self):
        """更新应能新增 LLM 实例（覆盖 _update_llm_instances 行 567-579）"""
        manager = NetworkConfigManager(config_file=self.config_path)

        new_instance = {
            'id': 'inst001',
            'name': 'test-instance',
            'provider': 'openai',
            'api_key': 'sk-instance-key',
            'model': 'gpt-4',
        }

        manager.update({'llm_instances': [new_instance]})

        config = manager._load()
        assert len(config['llm_instances']) == 1
        assert config['llm_instances'][0]['id'] == 'inst001'
        # api_key 应写入环境变量
        assert os.getenv('LLM_INST001_API_KEY') == 'sk-instance-key'
        # JSON 文件中不应有 api_key
        assert 'api_key' not in config['llm_instances'][0]

    def test_update_existing_llm_instance(self):
        """更新应能修改已有 LLM 实例"""
        manager = NetworkConfigManager(config_file=self.config_path)

        # 先添加
        manager.update({'llm_instances': [{
            'id': 'inst001', 'name': 'old-name', 'provider': 'openai',
            'api_key': 'sk-old-key', 'model': 'gpt-3.5',
        }]})

        # 再更新
        manager.update({'llm_instances': [{
            'id': 'inst001', 'name': 'new-name', 'model': 'gpt-4',
        }]})

        config = manager._load()
        inst = config['llm_instances'][0]
        assert inst['name'] == 'new-name'
        assert inst['model'] == 'gpt-4'

    def test_update_search_api_keys_dict(self):
        """旧版 search_api_keys 字典格式应兼容处理（覆盖行 518-524）"""
        manager = NetworkConfigManager(config_file=self.config_path)

        manager.update({
            'search_api_keys': {
                'tavily': 'tvly-test-key',
                'bing': 'bing-test-key',
            }
        })

        assert os.getenv('SEARCH_TAVILY_API_KEY') == 'tvly-test-key'
        assert os.getenv('SEARCH_BING_API_KEY') == 'bing-test-key'


class TestImportExportConfig:
    """测试 import_config() / export_config() 往返（覆盖行 936-978）"""

    _TEST_ENV_KEYS = ['LLM_API_KEY']

    def setup_method(self):
        self.temp_file = tempfile.NamedTemporaryFile(
            mode='w', suffix='.json', delete=False
        )
        self.temp_file.close()
        self.config_path = self.temp_file.name
        self._saved_env = {k: os.environ.get(k) for k in self._TEST_ENV_KEYS}
        for k in self._TEST_ENV_KEYS:
            os.environ.pop(k, None)

    def teardown_method(self):
        if os.path.exists(self.config_path):
            os.unlink(self.config_path)
        for k, v in self._saved_env.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)

    def test_export_returns_json_string(self):
        """export_config 应返回 JSON 字符串"""
        manager = NetworkConfigManager(config_file=self.config_path)
        exported = manager.export_config()

        assert isinstance(exported, str)
        parsed = json.loads(exported)
        assert 'llm' in parsed

    def test_import_export_roundtrip(self):
        """导出后导入应保持配置一致"""
        manager = NetworkConfigManager(config_file=self.config_path)

        # 修改配置
        manager.update({
            'llm': {'enabled': True, 'provider': 'openai', 'model': 'gpt-4'}
        })

        # 导出
        exported = manager.export_config()

        # 重置后导入
        manager2 = NetworkConfigManager(config_file=self.config_path + ".2")
        try:
            manager2.import_config(exported)
            config = manager2._load()

            assert config['llm']['provider'] == 'openai'
            assert config['llm']['model'] == 'gpt-4'
        finally:
            if os.path.exists(self.config_path + ".2"):
                os.unlink(self.config_path + ".2")

    def test_export_strips_sensitive_data(self):
        """导出时应脱敏敏感数据"""
        manager = NetworkConfigManager(config_file=self.config_path)
        manager.update({'llm': {'api_key': 'sk-secret'}})

        exported = manager.export_config()
        parsed = json.loads(exported)

        # 导出的 JSON 中 api_key 应被脱敏或移除
        llm_api_key = parsed.get('llm', {}).get('api_key', '')
        assert llm_api_key != 'sk-secret', "导出不应包含明文 api_key"


class TestResetConfig:
    """测试 reset() 重置逻辑（覆盖行 928-934）"""

    _TEST_ENV_KEYS = ['LLM_API_KEY']

    def setup_method(self):
        self.temp_file = tempfile.NamedTemporaryFile(
            mode='w', suffix='.json', delete=False
        )
        self.temp_file.close()
        self.config_path = self.temp_file.name
        self._saved_env = {k: os.environ.get(k) for k in self._TEST_ENV_KEYS}
        for k in self._TEST_ENV_KEYS:
            os.environ.pop(k, None)

    def teardown_method(self):
        if os.path.exists(self.config_path):
            os.unlink(self.config_path)
        for k, v in self._saved_env.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)

    def test_reset_clears_config(self):
        """reset 应将配置重置为默认状态"""
        manager = NetworkConfigManager(config_file=self.config_path)

        # 先添加一些配置
        manager.update({
            'llm': {'provider': 'openai', 'model': 'gpt-4'},
            'llm_instances': [{'id': 'x', 'name': 'test'}],
        })

        # 重置
        manager.reset()
        config = manager._load()

        # 应恢复默认值
        assert config['llm_instances'] == []
        assert config['default_llm_instance'] == ''


class TestGetRawConfig:
    """测试 get_raw_config() vs get_all() 脱敏对比（覆盖行 433-475）"""

    _TEST_ENV_KEYS = ['LLM_API_KEY']

    def setup_method(self):
        self.temp_file = tempfile.NamedTemporaryFile(
            mode='w', suffix='.json', delete=False
        )
        self.temp_file.close()
        self.config_path = self.temp_file.name
        self._saved_env = {k: os.environ.get(k) for k in self._TEST_ENV_KEYS}
        for k in self._TEST_ENV_KEYS:
            os.environ.pop(k, None)

    def teardown_method(self):
        if os.path.exists(self.config_path):
            os.unlink(self.config_path)
        for k, v in self._saved_env.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)

    def test_get_all_masks_api_key(self):
        """get_all 应脱敏 api_key"""
        manager = NetworkConfigManager(config_file=self.config_path)
        manager.update({'llm': {'api_key': 'sk-1234567890abcdef'}})

        config = manager.get_all()
        api_key = config['llm'].get('api_key', '')

        # 应为脱敏值或空
        assert api_key != 'sk-1234567890abcdef', "get_all 不应返回明文 api_key"


# ============================================================================
# 以下为任务3新增：MCP 凭证 / proxy_url / monitoring.endpoint 脱敏回归测试
# ============================================================================


class TestMcpCredentialsMasking:
    """测试 MCP 服务凭证脱敏（P0 高风险修复验证）

    【不易】MCP api_key/token 必须写入 .env，不持久化 JSON
    【变易】get_all 脱敏显示，get_raw_config 返回原始值
    """

    _TEST_ENV_KEYS = ['MCP_SVC001_API_KEY', 'MCP_SVC001_TOKEN']

    def setup_method(self):
        self.temp_file = tempfile.NamedTemporaryFile(
            mode='w', suffix='.json', delete=False
        )
        self.temp_file.close()
        self.config_path = self.temp_file.name
        self._saved_env = {k: os.environ.get(k) for k in self._TEST_ENV_KEYS}
        for k in self._TEST_ENV_KEYS:
            os.environ.pop(k, None)

    def teardown_method(self):
        if os.path.exists(self.config_path):
            os.unlink(self.config_path)
        for k, v in self._saved_env.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)

    def test_mcp_api_key_saved_to_env_not_json(self):
        """MCP 服务 api_key 应写入 .env，不持久化 JSON"""
        manager = NetworkConfigManager(config_file=self.config_path)
        manager.update({
            'mcp': {
                'enabled': True,
                'services': [{
                    'id': 'svc001',
                    'name': 'test-service',
                    'api_key': 'sk-mcp-secret-key-12345',
                    'token': 'tok-mcp-secret-67890',
                }]
            }
        })

        # api_key 应写入环境变量
        assert os.getenv('MCP_SVC001_API_KEY') == 'sk-mcp-secret-key-12345'
        assert os.getenv('MCP_SVC001_TOKEN') == 'tok-mcp-secret-67890'

        # JSON 文件中不应有明文 api_key/token
        with open(self.config_path, 'r', encoding='utf-8') as f:
            saved = json.load(f)
        services = saved.get('mcp', {}).get('services', [])
        assert len(services) == 1
        assert 'api_key' not in services[0], "api_key 不应保留在 JSON"
        assert 'token' not in services[0], "token 不应保留在 JSON"

    def test_mcp_api_key_masked_in_get_all(self):
        """get_all 中 MCP api_key 应脱敏为 ***+后4位"""
        manager = NetworkConfigManager(config_file=self.config_path)
        manager.update({
            'mcp': {
                'enabled': True,
                'services': [{
                    'id': 'svc001',
                    'name': 'test-service',
                    'api_key': 'sk-mcp-secret-key-12345',
                }]
            }
        })

        config = manager.get_all()
        services = config.get('mcp', {}).get('services', [])
        assert len(services) == 1
        # 应脱敏为 *** + 后4位
        assert services[0]['api_key'] == '***2345', f"实际: {services[0]['api_key']}"

    def test_mcp_token_stripped_on_save(self):
        """_save 应剥离 mcp.services[*].token"""
        manager = NetworkConfigManager(config_file=self.config_path)
        config = {
            'mcp': {
                'services': [{
                    'id': 'svc001',
                    'token': 'secret-token-value',
                }]
            }
        }
        manager._save(config)

        with open(self.config_path, 'r', encoding='utf-8') as f:
            saved = json.load(f)
        services = saved.get('mcp', {}).get('services', [])
        assert 'token' not in services[0], "token 不应保留在 JSON"


class TestProxyUrlMasking:
    """测试 proxy_url 输出脱敏（P2 修复验证）

    【不易】proxy_url 需完整值供代理连接，仅在 get_all 输出时脱敏
    【变易】get_all 剥离 user:pass@，get_raw_config 保留原始值
    """

    def setup_method(self):
        self.temp_file = tempfile.NamedTemporaryFile(
            mode='w', suffix='.json', delete=False
        )
        self.temp_file.close()
        self.config_path = self.temp_file.name

    def teardown_method(self):
        if os.path.exists(self.config_path):
            os.unlink(self.config_path)

    def test_proxy_url_auth_stripped_in_get_all(self):
        """get_all 中 proxy_url 应剥离 user:pass@"""
        manager = NetworkConfigManager(config_file=self.config_path)
        manager.update({
            'network': {'proxy_enabled': True, 'proxy_url': 'http://user:pass@proxy.example.com:8080'}
        })

        config = manager.get_all()
        proxy_url = config['network']['proxy_url']

        # 应剥离 user:pass@，保留 host:port
        assert 'user:pass@' not in proxy_url, f"不应包含认证信息: {proxy_url}"
        assert 'proxy.example.com' in proxy_url, f"应保留 host: {proxy_url}"
        assert '8080' in proxy_url, f"应保留 port: {proxy_url}"

    def test_proxy_url_raw_preserved(self):
        """get_raw_config 中 proxy_url 应保持原始值（供代理模块使用）"""
        manager = NetworkConfigManager(config_file=self.config_path)
        manager.update({
            'network': {'proxy_enabled': True, 'proxy_url': 'http://user:pass@proxy.example.com:8080'}
        })

        config = manager.get_raw_config()
        proxy_url = config['network']['proxy_url']

        # get_raw_config 应返回原始完整值
        assert proxy_url == 'http://user:pass@proxy.example.com:8080', (
            f"get_raw_config 应保留原始值: {proxy_url}"
        )


class TestMonitoringEndpointMasking:
    """测试 monitoring.endpoint 输出脱敏（P2 修复验证）

    【不易】endpoint 需完整值供监控上报，仅在 get_all 输出时脱敏
    【变易】get_all 剥离认证信息，get_raw_config 保留原始值
    """

    def setup_method(self):
        self.temp_file = tempfile.NamedTemporaryFile(
            mode='w', suffix='.json', delete=False
        )
        self.temp_file.close()
        self.config_path = self.temp_file.name

    def teardown_method(self):
        if os.path.exists(self.config_path):
            os.unlink(self.config_path)

    def test_monitoring_endpoint_auth_stripped_in_get_all(self):
        """get_all 中 monitoring.endpoint 应剥离认证信息"""
        manager = NetworkConfigManager(config_file=self.config_path)
        manager.update({
            'external_services': {
                'monitoring': {
                    'enabled': True,
                    'endpoint': 'http://admin:secret@monitor.example.com:9090'
                }
            }
        })

        config = manager.get_all()
        endpoint = config['external_services']['monitoring']['endpoint']

        # 应剥离 user:pass@
        assert 'admin:secret@' not in endpoint, f"不应包含认证信息: {endpoint}"
        assert 'monitor.example.com' in endpoint, f"应保留 host: {endpoint}"

    def test_monitoring_endpoint_raw_preserved(self):
        """get_raw_config 中 monitoring.endpoint 应保持原始值"""
        manager = NetworkConfigManager(config_file=self.config_path)
        manager.update({
            'external_services': {
                'monitoring': {
                    'enabled': True,
                    'endpoint': 'http://admin:secret@monitor.example.com:9090'
                }
            }
        })

        config = manager.get_raw_config()
        endpoint = config['external_services']['monitoring']['endpoint']

        # get_raw_config 应返回原始完整值
        assert endpoint == 'http://admin:secret@monitor.example.com:9090', (
            f"get_raw_config 应保留原始值: {endpoint}"
        )
