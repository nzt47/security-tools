"""纯 .env 单一数据源架构 - 回归测试

测试覆盖：
1. EnvConfigManager 基础功能（get/set/delete/reload）
2. _key_to_env_var 映射规则
3. _save_secure / _load_secure 端到端
4. update() 各场景（单实例/多实例/webhook/search key）
5. network_config.json 无明文验证
6. get_all / get_raw_config 脱敏
7. 并发写入线程安全
8. 原子写入恢复

运行：python -m pytest tests/unit/test_env_hot_reload.py -v
"""
import os
import json
import threading
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest


# ─────────────────────────────────────────────────
# 测试夹具
# ─────────────────────────────────────────────────

@pytest.fixture
def temp_env_file(tmp_path):
    """临时 .env 文件，测试后自动清理"""
    env_file = tmp_path / ".env"
    env_file.touch()
    return env_file


@pytest.fixture
def env_manager(temp_env_file):
    """独立的 EnvConfigManager 实例（不使用全局单例）"""
    from agent.env_config_manager import EnvConfigManager
    return EnvConfigManager(env_file_path=str(temp_env_file))


@pytest.fixture
def ncm_with_temp_env(tmp_path, monkeypatch):
    """使用临时 .env 和 network_config.json 的 NetworkConfigManager"""
    from agent.env_config_manager import EnvConfigManager
    from agent.network_config import NetworkConfigManager

    # 临时 .env 文件
    env_file = tmp_path / ".env"
    env_file.touch()

    # 临时 network_config.json
    nc_file = tmp_path / "network_config.json"

    # 创建独立的 EnvConfigManager（避免污染全局单例）
    env_mgr = EnvConfigManager(env_file_path=str(env_file))

    # 用 monkeypatch 让 NetworkConfigManager 使用我们的 env_mgr
    with patch('agent.network_config.get_env_config_manager', return_value=env_mgr):
        ncm = NetworkConfigManager(config_file=str(nc_file))
        yield ncm, env_mgr, env_file, nc_file

    # 清理测试期间设置的环境变量
    test_keys = [k for k in os.environ if k.startswith(('LLM_', 'SEARCH_', 'ERROR_REPORTING_'))]
    for k in test_keys:
        os.environ.pop(k, None)


# ─────────────────────────────────────────────────
# 1. EnvConfigManager 基础功能
# ─────────────────────────────────────────────────

class TestEnvConfigManagerBasic:
    """EnvConfigManager 基础 API 测试"""

    def test_set_writes_to_file_and_environ(self, env_manager, temp_env_file):
        """set() 应同时写入 .env 文件和 os.environ"""
        env_manager.set('TEST_KEY_1', 'value_123')

        # os.environ 已同步
        assert os.getenv('TEST_KEY_1') == 'value_123'

        # .env 文件已写入
        content = temp_env_file.read_text(encoding='utf-8')
        assert 'TEST_KEY_1=value_123' in content

    def test_get_reads_from_environ(self, env_manager):
        """get() 从 os.environ 读取"""
        env_manager.set('TEST_KEY_2', 'abc')
        assert env_manager.get('TEST_KEY_2') == 'abc'
        assert env_manager.get('TEST_KEY_2', 'default') == 'abc'

    def test_get_returns_default_when_missing(self, env_manager):
        """get() 未找到时返回 default"""
        assert env_manager.get('NON_EXIST_KEY', 'fallback') == 'fallback'
        assert env_manager.get('NON_EXIST_KEY') is None

    def test_delete_removes_from_file_and_environ(self, env_manager, temp_env_file):
        """delete() 从 .env 和 os.environ 移除"""
        env_manager.set('TEST_KEY_3', 'to_delete')
        assert os.getenv('TEST_KEY_3') == 'to_delete'

        env_manager.delete('TEST_KEY_3')
        assert os.getenv('TEST_KEY_3') is None

        content = temp_env_file.read_text(encoding='utf-8')
        assert 'TEST_KEY_3' not in content

    def test_set_updates_existing_key(self, env_manager, temp_env_file):
        """set() 更新已存在的 KEY（不追加重复行）"""
        env_manager.set('TEST_KEY_4', 'old_value')
        env_manager.set('TEST_KEY_4', 'new_value')

        assert os.getenv('TEST_KEY_4') == 'new_value'
        content = temp_env_file.read_text(encoding='utf-8')
        # 只有一行 TEST_KEY_4
        assert content.count('TEST_KEY_4=') == 1
        assert 'TEST_KEY_4=new_value' in content

    def test_reload_loads_env_file_to_environ(self, env_manager, temp_env_file):
        """reload() 将 .env 文件内容加载到 os.environ"""
        # 外部直接写入 .env 文件（绕过 set）
        temp_env_file.write_text('RELOAD_TEST_KEY=reload_value\n', encoding='utf-8')
        assert os.getenv('RELOAD_TEST_KEY') is None

        env_manager.reload()
        assert os.getenv('RELOAD_TEST_KEY') == 'reload_value'


# ─────────────────────────────────────────────────
# 2. _key_to_env_var 映射规则
# ─────────────────────────────────────────────────

class TestKeyToEnvVarMapping:
    """_key_to_env_var 映射规则测试"""

    @pytest.fixture
    def ncm(self):
        from agent.network_config import NetworkConfigManager
        return NetworkConfigManager()

    @pytest.mark.parametrize("key,expected", [
        # 特殊映射
        ('llm_api_key', 'LLM_API_KEY'),
        ('error_reporting_webhook', 'ERROR_REPORTING_WEBHOOK_URL'),
        # LLM 实例
        ('llm_default_api_key', 'LLM_API_KEY'),
        ('llm_myinst123_api_key', 'LLM_MYINST123_API_KEY'),
        ('llm_test-multi_api_key', 'LLM_TEST-MULTI_API_KEY'),
        # Search 引擎（旧版 _key 后缀）
        ('search_tavily_key', 'SEARCH_TAVILY_API_KEY'),
        ('search_bing_key', 'SEARCH_BING_API_KEY'),
        # Search 实例（新版 _api_key 后缀）
        ('search_abc_api_key', 'SEARCH_ABC_API_KEY'),
        ('search_abc-api_api_key', 'SEARCH_ABC-API_API_KEY'),
        # 其他
        ('custom_key', 'CUSTOM_KEY'),
    ])
    def test_mapping(self, ncm, key, expected):
        assert ncm._key_to_env_var(key) == expected


# ─────────────────────────────────────────────────
# 3. _save_secure / _load_secure 端到端
# ─────────────────────────────────────────────────

class TestSaveLoadSecure:
    """_save_secure / _load_secure 端到端测试"""

    def test_save_secure_writes_env_var(self, ncm_with_temp_env):
        """_save_secure 写入 .env + os.environ"""
        ncm, env_mgr, env_file, nc_file = ncm_with_temp_env

        ncm._save_secure('llm_api_key', 'sk-test-12345')

        assert os.getenv('LLM_API_KEY') == 'sk-test-12345'
        content = env_file.read_text(encoding='utf-8')
        assert 'LLM_API_KEY=sk-test-12345' in content

    def test_load_secure_reads_env_var(self, ncm_with_temp_env):
        """_load_secure 从环境变量读取"""
        ncm, env_mgr, env_file, nc_file = ncm_with_temp_env

        ncm._save_secure('llm_api_key', 'sk-load-test')
        loaded = ncm._load_secure('llm_api_key', default='fallback')
        assert loaded == 'sk-load-test'

    def test_load_secure_returns_default_when_empty(self, ncm_with_temp_env):
        """_load_secure 环境变量为空时返回 default"""
        ncm, env_mgr, env_file, nc_file = ncm_with_temp_env

        loaded = ncm._load_secure('llm_nonexist_key', default='default_val')
        assert loaded == 'default_val'

    def test_load_secure_with_explicit_env_var(self, ncm_with_temp_env):
        """_load_secure 显式指定 env_var 优先于自动推导"""
        ncm, env_mgr, env_file, nc_file = ncm_with_temp_env

        os.environ['CUSTOM_ENV_VAR'] = 'custom_value'
        try:
            loaded = ncm._load_secure('some_key', env_var='CUSTOM_ENV_VAR')
            assert loaded == 'custom_value'
        finally:
            os.environ.pop('CUSTOM_ENV_VAR', None)

    def test_save_secure_multi_instance_key(self, ncm_with_temp_env):
        """_save_secure 多实例 LLM key 映射正确"""
        ncm, env_mgr, env_file, nc_file = ncm_with_temp_env

        ncm._save_secure('llm_inst-abc_api_key', 'sk-inst-abc')
        assert os.getenv('LLM_INST-ABC_API_KEY') == 'sk-inst-abc'

        loaded = ncm._load_secure('llm_inst-abc_api_key')
        assert loaded == 'sk-inst-abc'


# ─────────────────────────────────────────────────
# 4. update() 各场景
# ─────────────────────────────────────────────────

class TestUpdateScenarios:
    """update() 方法各场景测试"""

    def test_update_single_llm_api_key(self, ncm_with_temp_env):
        """场景 1: 单实例 LLM api_key"""
        ncm, env_mgr, env_file, nc_file = ncm_with_temp_env

        ncm.update({'llm': {'api_key': 'sk-single-test'}})

        # .env 已写入
        content = env_file.read_text(encoding='utf-8')
        assert 'LLM_API_KEY=sk-single-test' in content
        # os.environ 同步
        assert os.getenv('LLM_API_KEY') == 'sk-single-test'
        # network_config.json 不含明文
        nc_content = nc_file.read_text(encoding='utf-8')
        assert 'sk-single-test' not in nc_content

    def test_update_llm_instance_new_id(self, ncm_with_temp_env):
        """场景 2: 多实例 LLM api_key（新 ID，upsert）"""
        ncm, env_mgr, env_file, nc_file = ncm_with_temp_env

        test_inst = [{
            'id': 'test-multi-new',
            'name': 'TestMulti',
            'provider': 'openai',
            'api_key': 'sk-multi-new',
            'api_endpoint': 'https://api.test.com',
            'enabled': True,
        }]
        ncm.update({'llm_instances': test_inst})

        # .env 已写入
        content = env_file.read_text(encoding='utf-8')
        assert 'LLM_TEST-MULTI-NEW_API_KEY=sk-multi-new' in content
        # os.environ 同步
        assert os.getenv('LLM_TEST-MULTI-NEW_API_KEY') == 'sk-multi-new'
        # network_config.json 不含明文
        nc_content = nc_file.read_text(encoding='utf-8')
        assert 'sk-multi-new' not in nc_content

    def test_update_llm_instance_existing_id(self, ncm_with_temp_env):
        """场景 3: 更新已存在的多实例"""
        ncm, env_mgr, env_file, nc_file = ncm_with_temp_env

        # 先新增
        ncm.update({'llm_instances': [{
            'id': 'test-update', 'name': 'Orig', 'provider': 'openai',
            'api_key': 'sk-orig', 'api_endpoint': 'https://api.test.com', 'enabled': True,
        }]})
        assert os.getenv('LLM_TEST-UPDATE_API_KEY') == 'sk-orig'

        # 再更新
        ncm.update({'llm_instances': [{
            'id': 'test-update', 'name': 'Updated', 'api_key': 'sk-updated',
        }]})
        assert os.getenv('LLM_TEST-UPDATE_API_KEY') == 'sk-updated'

    def test_update_webhook_url(self, ncm_with_temp_env):
        """场景 4: webhook url"""
        ncm, env_mgr, env_file, nc_file = ncm_with_temp_env

        ncm.update({'external_services': {'error_reporting': {'webhook_url': 'https://hooks.test.com/abc'}}})

        content = env_file.read_text(encoding='utf-8')
        assert 'ERROR_REPORTING_WEBHOOK_URL=https://hooks.test.com/abc' in content
        assert os.getenv('ERROR_REPORTING_WEBHOOK_URL') == 'https://hooks.test.com/abc'
        # network_config.json 不含明文
        nc_content = nc_file.read_text(encoding='utf-8')
        assert 'https://hooks.test.com/abc' not in nc_content

    def test_update_search_api_keys_legacy_dict(self, ncm_with_temp_env):
        """场景 5: search engine api_key（旧版字典）"""
        ncm, env_mgr, env_file, nc_file = ncm_with_temp_env

        ncm.update({'search_api_keys': {'tavily': 'tvly-test-123'}})

        content = env_file.read_text(encoding='utf-8')
        assert 'SEARCH_TAVILY_API_KEY=tvly-test-123' in content
        assert os.getenv('SEARCH_TAVILY_API_KEY') == 'tvly-test-123'

    def test_update_skips_masked_api_key(self, ncm_with_temp_env):
        """场景 6: 脱敏值（***xxxx）不覆盖真实 key"""
        ncm, env_mgr, env_file, nc_file = ncm_with_temp_env

        # 先写入真实值
        ncm.update({'llm': {'api_key': 'sk-real-value'}})
        assert os.getenv('LLM_API_KEY') == 'sk-real-value'

        # 再提交脱敏值（不应覆盖）
        ncm.update({'llm': {'api_key': '***lue'}})
        assert os.getenv('LLM_API_KEY') == 'sk-real-value'  # 仍是原值


# ─────────────────────────────────────────────────
# 5. network_config.json 无明文验证
# ─────────────────────────────────────────────────

class TestNoPlaintextInJson:
    """验证 network_config.json 永远不含明文敏感数据"""

    def test_json_no_llm_api_key_after_update(self, ncm_with_temp_env):
        ncm, env_mgr, env_file, nc_file = ncm_with_temp_env

        ncm.update({'llm': {'api_key': 'sk-secret-12345'}})

        nc_json = json.loads(nc_file.read_text(encoding='utf-8'))
        # llm.api_key 字段应不存在或为空
        llm_key = nc_json.get('llm', {}).get('api_key', '')
        assert llm_key == '' or llm_key is None

    def test_json_no_webhook_after_update(self, ncm_with_temp_env):
        ncm, env_mgr, env_file, nc_file = ncm_with_temp_env

        ncm.update({'external_services': {'error_reporting': {'webhook_url': 'https://secret.test/webhook'}}})

        nc_json = json.loads(nc_file.read_text(encoding='utf-8'))
        webhook = nc_json.get('external_services', {}).get('error_reporting', {}).get('webhook_url', '')
        assert webhook == '' or webhook is None

    def test_json_no_llm_instance_api_key(self, ncm_with_temp_env):
        ncm, env_mgr, env_file, nc_file = ncm_with_temp_env

        ncm.update({'llm_instances': [{
            'id': 'leak-test', 'name': 'LeakTest', 'provider': 'openai',
            'api_key': 'sk-leak-test', 'api_endpoint': 'https://api.test.com', 'enabled': True,
        }]})

        nc_json = json.loads(nc_file.read_text(encoding='utf-8'))
        for inst in nc_json.get('llm_instances', []):
            assert inst.get('api_key', '') == '' or inst.get('api_key') is None

    def test_json_no_search_instance_api_key(self, ncm_with_temp_env):
        ncm, env_mgr, env_file, nc_file = ncm_with_temp_env

        ncm.update({'search_instances': [{
            'id': 'search-leak', 'name': 'SearchLeak', 'engine_type': 'custom',
            'api_endpoint': 'https://api.test.com', 'api_key': 'sk-search-leak',
        }]})

        nc_json = json.loads(nc_file.read_text(encoding='utf-8'))
        for inst in nc_json.get('search_instances', []):
            assert inst.get('api_key', '') == '' or inst.get('api_key') is None


# ─────────────────────────────────────────────────
# 6. 脱敏 vs 真实值
# ─────────────────────────────────────────────────

class TestMaskingBehavior:
    """get_all() 脱敏 vs get_raw_config() 真实值"""

    def test_get_all_masks_llm_api_key(self, ncm_with_temp_env):
        ncm, env_mgr, env_file, nc_file = ncm_with_temp_env

        ncm.update({'llm': {'api_key': 'sk-1234567890abcdef'}})

        all_config = ncm.get_all()
        assert all_config['llm']['api_key'].startswith('***')
        assert 'sk-1234567890abcdef' not in all_config['llm']['api_key']

    def test_get_raw_config_returns_real_llm_api_key(self, ncm_with_temp_env):
        ncm, env_mgr, env_file, nc_file = ncm_with_temp_env

        ncm.update({'llm': {'api_key': 'sk-real-12345'}})

        raw = ncm.get_raw_config()
        assert raw['llm']['api_key'] == 'sk-real-12345'

    def test_get_all_masks_webhook(self, ncm_with_temp_env):
        ncm, env_mgr, env_file, nc_file = ncm_with_temp_env

        ncm.update({'external_services': {'error_reporting': {'webhook_url': 'https://hooks.real/secret'}}})

        all_config = ncm.get_all()
        assert all_config['external_services']['error_reporting']['webhook_url'] == '***'

    def test_get_raw_config_returns_real_webhook(self, ncm_with_temp_env):
        ncm, env_mgr, env_file, nc_file = ncm_with_temp_env

        ncm.update({'external_services': {'error_reporting': {'webhook_url': 'https://hooks.real/secret'}}})

        raw = ncm.get_raw_config()
        assert raw['external_services']['error_reporting']['webhook_url'] == 'https://hooks.real/secret'


# ─────────────────────────────────────────────────
# 7. 并发写入线程安全
# ─────────────────────────────────────────────────

class TestThreadSafety:
    """并发写入线程安全测试"""

    def test_concurrent_set_no_corruption(self, env_manager, temp_env_file):
        """多线程并发 set 不同 key，文件不损坏"""
        threads = []
        errors = []

        def worker(i):
            try:
                for j in range(20):
                    env_manager.set(f'CONCURRENT_KEY_{i}', f'value_{i}_{j}')
            except Exception as e:
                errors.append(e)

        for i in range(10):
            t = threading.Thread(target=worker, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # 无异常
        assert len(errors) == 0, f"并发写入出错: {errors}"

        # 文件可正常解析（不损坏）
        content = temp_env_file.read_text(encoding='utf-8')
        lines = [l for l in content.splitlines() if l.startswith('CONCURRENT_KEY_')]
        # 每个 key 只有一行（最终值）
        unique_keys = set(l.split('=')[0] for l in lines)
        assert len(unique_keys) == 10

    def test_concurrent_same_key_last_write_wins(self, env_manager, temp_env_file):
        """多线程并发 set 同一 key，最终值为最后一次写入之一"""
        threads = []
        final_values = []

        def worker(val):
            env_manager.set('SAME_KEY', val)

        for i in range(20):
            val = f'value_{i}'
            final_values.append(val)
            t = threading.Thread(target=worker, args=(val,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        final = os.getenv('SAME_KEY')
        assert final in final_values, f"最终值 {final} 不在可能值范围内"


# ─────────────────────────────────────────────────
# 8. 原子写入恢复
# ─────────────────────────────────────────────────

class TestAtomicWrite:
    """原子写入恢复测试"""

    def test_atomic_write_no_tmp_file_left(self, env_manager, temp_env_file):
        """set() 后无临时文件残留"""
        env_manager.set('ATOMIC_TEST', 'value')

        # 检查临时目录无残留 .env_*.tmp 文件
        parent = temp_env_file.parent
        tmp_files = list(parent.glob('.env_*.tmp'))
        assert len(tmp_files) == 0, f"发现临时文件残留: {tmp_files}"

    def test_atomic_write_preserves_other_keys(self, env_manager, temp_env_file):
        """更新一个 key 不影响其他 key"""
        env_manager.set('KEEP_KEY_1', 'val1')
        env_manager.set('KEEP_KEY_2', 'val2')
        env_manager.set('KEEP_KEY_3', 'val3')

        # 更新 KEY_2
        env_manager.set('KEEP_KEY_2', 'val2_updated')

        # 其他 key 仍存在
        assert os.getenv('KEEP_KEY_1') == 'val1'
        assert os.getenv('KEEP_KEY_3') == 'val3'
        assert os.getenv('KEEP_KEY_2') == 'val2_updated'

    def test_set_value_with_equals_sign(self, env_manager, temp_env_file):
        """值中包含 = 号也能正确解析"""
        env_manager.set('EQUALS_TEST', 'key=value=with=equals')

        assert os.getenv('EQUALS_TEST') == 'key=value=with=equals'

        # reload 后仍正确
        os.environ.pop('EQUALS_TEST')
        env_manager.reload()
        assert os.getenv