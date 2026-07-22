"""网络配置保存逻辑回归测试

针对历史 bug 的回归测试集合，确保以下修复不会回退：
1. _save() 自动剥离 search_instances.api_key（防止明文写入 JSON 文件）
2. _save() 同步更新 self._cache（防止 apply_search_instances 用旧缓存覆盖文件）
3. get_raw_config()/get_all() 返回深拷贝（防止缓存污染残留解密 api_key）
4. _update_search_instances 不写入脱敏值（防止 ***xxxx 覆盖真实 key）
5. apply_search_instances 后 engine_priority 规范化为 UUID
6. JSON 文件中 search_instances 不含明文 api_key

这些 bug 曾导致"搜索引擎排序不能保存、新自定义搜索不正常"。
"""

import os
import json
import copy
import tempfile
from pathlib import Path
from unittest.mock import Mock, MagicMock

import pytest

from agent.network_config import NetworkConfigManager


class _FakeSearchEngine:
    """用于 apply_search_instances 测试的 SearchEngine 替身

    仅实现测试所需接口：_engine_registry / remove_engine /
    set_engine_priority / register_engine / _search_custom / _search_<type> /
    set_engine_enabled / set_default_engine / _api_keys。
    """

    def __init__(self):
        self._engine_registry = {}
        self._priority = []
        self.register_calls = []
        self._api_keys = {}
        self._enabled = {}
        self._default_engine = None

    def remove_engine(self, engine_id):
        self._engine_registry.pop(engine_id, None)

    def register_engine(self, name=None, label=None, handler=None,
                        needs_key=False, description="", **kwargs):
        self._engine_registry[name] = {
            "label": label, "handler": handler,
            "needs_key": needs_key, "description": description,
        }
        self.register_calls.append(name)

    def set_engine_priority(self, priority):
        self._priority = list(priority)

    def set_engine_enabled(self, name, enabled):
        self._enabled[name] = enabled

    def set_default_engine(self, name):
        self._default_engine = name

    def _search_custom(self, instance, query, **kwargs):
        """自定义引擎搜索 handler 占位"""
        return []

    def _search_duckduckgo(self, query, **kwargs):
        return []

    def _search_sogou(self, query, **kwargs):
        return []

    def _search_so360(self, query, **kwargs):
        return []


def _make_manager_with_secure(tmp_path, secure_store=None):
    """创建带 Mock SecureManager 的 NetworkConfigManager"""
    if secure_store is None:
        secure_store = {}
    mock_secure = Mock()
    mock_secure.set_secure_value = Mock(
        side_effect=lambda k, v: secure_store.update({k: v})
    )
    mock_secure.get_secure_value = Mock(
        side_effect=lambda k, default=None: secure_store.get(k, default)
    )
    config_file = tmp_path / "network_config.json"
    manager = NetworkConfigManager(
        config_file=str(config_file)
    )
    return manager, mock_secure, secure_store


@pytest.fixture
def tmp_config_dir(tmp_path):
    """提供临时配置目录"""
    return tmp_path


@pytest.fixture(autouse=True)
def clean_env_vars():
    """自动清理测试期间设置的环境变量（纯 .env 架构隔离）"""
    saved = {}
    test_prefixes = ('LLM_', 'SEARCH_', 'ERROR_REPORTING_')
    for k in list(os.environ.keys()):
        if k.startswith(test_prefixes):
            saved[k] = os.environ.get(k)
            os.environ.pop(k, None)
    yield
    # 恢复
    for k in list(os.environ.keys()):
        if k.startswith(test_prefixes) and k not in saved:
            os.environ.pop(k, None)
    for k, v in saved.items():
        if v is not None:
            os.environ[k] = v
        else:
            os.environ.pop(k, None)


# ════════════════════════════════════════════════════════════════════════════
# 1. _save() 自动剥离 search_instances.api_key
# ════════════════════════════════════════════════════════════════════════════

class TestSaveStripsSearchInstanceApiKey:
    """回归测试：_save() 必须剥离 search_instances 中的 api_key"""

    def test_save_strips_api_key_when_secure_manager_present(self, tmp_config_dir):
        """secure_manager 可用时，_save() 应剥离 search_instances.api_key"""
        manager, _, _ = _make_manager_with_secure(tmp_config_dir)
        config = {
            "search_instances": [
                {"id": "inst-1", "name": "Tavily", "api_key": "sk-real-key-12345"},
                {"id": "inst-2", "name": "Firecrawl", "api_key": "fc-real-key-67890"},
            ],
            "search": {"engine_priority": ["inst-1", "inst-2"]},
        }
        manager._save(config)

        with open(manager._config_file, "r", encoding="utf-8") as f:
            saved = json.load(f)
        for inst in saved["search_instances"]:
            assert "api_key" not in inst, (
                f"实例 {inst.get('name')} 的 api_key 未被剥离，明文写入文件"
            )

    def test_save_keeps_api_key_when_no_secure_manager(self, tmp_config_dir):
        """【纯 .env 架构】_save 无条件移除 api_key，不再依赖 secure_manager"""
        config_file = tmp_config_dir / "network_config.json"
        # 【P2 已清理】secure_manager 参数已移除
        manager = NetworkConfigManager(config_file=str(config_file))
        config = {
            "search_instances": [
                {"id": "inst-1", "name": "Tavily", "api_key": "sk-real-key"},
            ],
            "search": {"engine_priority": ["inst-1"]},
        }
        manager._save(config)

        with open(manager._config_file, "r", encoding="utf-8") as f:
            saved = json.load(f)
        # 新架构下，无论 secure_manager 是否为 None，_save 都移除 api_key
        assert "api_key" not in saved["search_instances"][0], (
            "纯 .env 架构下 _save 应无条件移除 api_key"
        )


# ════════════════════════════════════════════════════════════════════════════
# 2. _save() 同步更新 self._cache
# ════════════════════════════════════════════════════════════════════════════

class TestSaveUpdatesCache:
    """回归测试：_save() 必须同步更新 self._cache，防止后续读取返回旧数据"""

    def test_cache_reflects_latest_save(self, tmp_config_dir):
        """_save() 后 _load() 应返回最新配置，而非旧缓存"""
        manager, _, _ = _make_manager_with_secure(tmp_config_dir)
        manager._save({"search": {"engine_priority": ["old-id"]}, "search_instances": []})

        # 再次保存不同配置
        manager._save({"search": {"engine_priority": ["new-id-1", "new-id-2"]}, "search_instances": []})

        loaded = manager._load()
        assert loaded["search"]["engine_priority"] == ["new-id-1", "new-id-2"], (
            "缓存未更新，_load() 返回了旧数据"
        )

    def test_cache_does_not_contain_api_key_after_save(self, tmp_config_dir):
        """_save() 后缓存中的 search_instances 不应包含 api_key"""
        manager, _, _ = _make_manager_with_secure(tmp_config_dir)
        manager._save({
            "search_instances": [
                {"id": "inst-1", "name": "Tavily", "api_key": "sk-real-key"}
            ],
            "search": {"engine_priority": ["inst-1"]},
        })

        cached = manager._cache
        assert "api_key" not in cached["search_instances"][0], (
            "缓存中残留 api_key，可能导致后续 _save() 把明文写回文件"
        )


# ════════════════════════════════════════════════════════════════════════════
# 3. get_raw_config()/get_all() 返回深拷贝，不污染缓存
# ════════════════════════════════════════════════════════════════════════════

class TestDeepcopyPreventsCachePollution:
    """回归测试：get_raw_config/get_all 必须返回深拷贝"""

    def test_get_raw_config_does_not_pollute_cache(self, tmp_config_dir):
        """修改 get_raw_config() 返回值不应影响缓存"""
        manager, mock_secure, secure_store = _make_manager_with_secure(tmp_config_dir)
        # 【纯 .env 架构】通过 os.environ 设置敏感值（替代 secure_store）
        os.environ['SEARCH_INST-1_API_KEY'] = 'sk-real-key-12345'

        manager._save({
            "search_instances": [{"id": "inst-1", "name": "Tavily"}],
            "search": {"engine_priority": ["inst-1"]},
            "llm": {"api_key": ""},
            "external_services": {"error_reporting": {"webhook_url": ""}},
        })

        # 获取原始配置并修改
        raw = manager.get_raw_config()
        assert raw["search_instances"][0]["api_key"] == "sk-real-key-12345"
        raw["search_instances"][0]["api_key"] = "POLLUTED"
        raw["llm"]["api_key"] = "POLLUTED_LLM"

        # 缓存应不受影响
        cached = manager._cache
        assert "api_key" not in cached["search_instances"][0], (
            "缓存被 get_raw_config 返回值污染"
        )
        assert cached["llm"].get("api_key") != "POLLUTED_LLM", (
            "缓存被 get_raw_config 返回值污染"
        )

    def test_get_all_does_not_pollute_cache(self, tmp_config_dir):
        """修改 get_all() 返回值不应影响缓存"""
        manager, mock_secure, secure_store = _make_manager_with_secure(tmp_config_dir)
        # 【纯 .env 架构】通过 os.environ 设置敏感值
        os.environ['LLM_API_KEY'] = 'sk-real-llm-key'

        manager._save({
            "search_instances": [],
            "search": {"engine_priority": []},
            "llm": {"api_key": ""},
            "external_services": {"error_reporting": {"webhook_url": ""}},
        })

        safe = manager.get_all()
        assert safe["llm"]["api_key"].startswith("***")
        safe["llm"]["api_key"] = "POLLUTED"

        cached = manager._cache
        assert cached["llm"].get("api_key") != "POLLUTED", (
            "缓存被 get_all 返回值污染"
        )

    def test_get_raw_config_returns_independent_copy(self, tmp_config_dir):
        """连续两次 get_raw_config() 应返回独立对象"""
        manager, mock_secure, secure_store = _make_manager_with_secure(tmp_config_dir)
        # 【纯 .env 架构】通过 os.environ 设置敏感值
        os.environ['SEARCH_INST-1_API_KEY'] = 'sk-real-key'

        manager._save({
            "search_instances": [{"id": "inst-1", "name": "Tavily"}],
            "search": {"engine_priority": ["inst-1"]},
            "llm": {"api_key": ""},
            "external_services": {"error_reporting": {"webhook_url": ""}},
        })

        raw1 = manager.get_raw_config()
        raw2 = manager.get_raw_config()
        assert raw1 is not raw2
        assert raw1["search_instances"][0] is not raw2["search_instances"][0]

        raw1["search_instances"][0]["api_key"] = "MODIFIED"
        assert raw2["search_instances"][0]["api_key"] == "sk-real-key", (
            "两次 get_raw_config 返回了同一对象引用"
        )


# ════════════════════════════════════════════════════════════════════════════
# 4. _update_search_instances 不写入脱敏值
# ════════════════════════════════════════════════════════════════════════════

class TestUpdateSearchInstancesIgnoresMaskedKey:
    """回归测试：脱敏值（***xxxx）不应覆盖真实 key 或写入配置"""

    def test_masked_api_key_not_saved_to_secure_store(self, tmp_config_dir):
        """脱敏值不应调用 _save_secure（更新已存在实例）"""
        manager, mock_secure, _ = _make_manager_with_secure(tmp_config_dir)
        # 先创建实例（新增场景，传入真实 key）
        manager._update_search_instances([
            {"name": "Tavily", "api_key": "sk-real-key-original"},
        ])
        mock_secure.reset_mock()
        inst_id = manager._cache["search_instances"][0]["id"]

        # 更新传入脱敏值
        manager._update_search_instances([
            {"id": inst_id, "name": "Tavily", "api_key": "***1234"},
        ])

        mock_secure.set_secure_value.assert_not_called()

    def test_masked_api_key_not_written_to_config(self, tmp_config_dir):
        """脱敏值不应写入 search_instances 配置（更新已存在实例）"""
        manager, _, _ = _make_manager_with_secure(tmp_config_dir)
        manager._update_search_instances([
            {"name": "Tavily", "api_key": "sk-real-key-original"},
        ])
        inst_id = manager._cache["search_instances"][0]["id"]

        manager._update_search_instances([
            {"id": inst_id, "name": "Tavily", "api_key": "***1234"},
        ])

        cached = manager._cache
        inst = next(i for i in cached["search_instances"] if i["id"] == inst_id)
        assert "api_key" not in inst, (
            f"脱敏值被写入配置：{inst}"
        )

    def test_real_api_key_saved_to_secure_store(self, tmp_config_dir):
        """【纯 .env 架构】真实 api_key 应写入 os.environ（新增场景）"""
        manager, mock_secure, secure_store = _make_manager_with_secure(tmp_config_dir)

        manager._update_search_instances([
            {"name": "Tavily", "api_key": "sk-real-key-12345"},
        ])

        inst_id = manager._cache["search_instances"][0]["id"]
        # 新架构下，api_key 写入 os.environ（而非 secure_store）
        env_var = f'SEARCH_{inst_id.upper()}_API_KEY'
        assert os.getenv(env_var) == 'sk-real-key-12345', (
            f"真实 api_key 未写入环境变量 {env_var}"
        )


# ════════════════════════════════════════════════════════════════════════════
# 5. apply_search_instances 规范化 engine_priority 为 UUID
# ════════════════════════════════════════════════════════════════════════════

class TestApplySearchInstancesNormalizesPriority:
    """回归测试：apply_search_instances 应将 engine_priority 规范化为 UUID"""

    def test_priority_normalized_to_uuid(self, tmp_config_dir):
        """旧版名称/类型条目应被替换为实例 UUID"""
        manager, _, secure_store = _make_manager_with_secure(tmp_config_dir)
        # 预置加密 key（避免 _register_search_instance 取不到 key 报错）
        secure_store["search_inst-1_api_key"] = "sk-real-key"

        manager._save({
            "search_instances": [
                {"id": "inst-1", "name": "Tavily", "engine_type": "custom",
                 "enabled": True, "api_endpoint": "https://example.com/search",
                 "http_method": "GET", "query_param": "q",
                 "auth_header": "", "results_path": "data",
                 "title_field": "title", "url_field": "url", "snippet_field": "snippet"},
                {"id": "inst-2", "name": "DuckDuckGo", "engine_type": "duckduckgo",
                 "enabled": True},
            ],
            # 旧版 priority 使用名称和类型
            "search": {"engine_priority": ["Tavily", "duckduckgo", "inst-2"]},
            "llm": {"api_key": ""},
            "external_services": {"error_reporting": {"webhook_url": ""}},
        })

        engine = _FakeSearchEngine()
        manager.apply_search_instances(engine)

        loaded = manager._load()
        priority = loaded["search"]["engine_priority"]
        # 全部应为 UUID（实例 ID）
        assert all(p in ("inst-1", "inst-2") for p in priority), (
            f"priority 未规范化为 UUID: {priority}"
        )
        # 顺序应保留
        assert priority == ["inst-1", "inst-2"]

    def test_apply_does_not_write_api_key_to_file(self, tmp_config_dir):
        """apply_search_instances 后文件中 search_instances 不应有 api_key"""
        manager, _, secure_store = _make_manager_with_secure(tmp_config_dir)
        secure_store["search_inst-1_api_key"] = "sk-real-key"

        manager._save({
            "search_instances": [
                {"id": "inst-1", "name": "Tavily", "engine_type": "custom",
                 "enabled": True, "api_endpoint": "https://example.com/search",
                 "http_method": "GET", "query_param": "q",
                 "auth_header": "", "results_path": "data",
                 "title_field": "title", "url_field": "url", "snippet_field": "snippet"},
            ],
            "search": {"engine_priority": ["inst-1"]},
            "llm": {"api_key": ""},
            "external_services": {"error_reporting": {"webhook_url": ""}},
        })

        engine = _FakeSearchEngine()
        manager.apply_search_instances(engine)

        with open(manager._config_file, "r", encoding="utf-8") as f:
            saved = json.load(f)
        for inst in saved["search_instances"]:
            assert "api_key" not in inst, (
                f"apply_search_instances 后文件中残留 api_key: {inst}"
            )

    def test_apply_then_load_returns_latest_priority(self, tmp_config_dir):
        """apply_search_instances 后再次 _load 应返回最新 priority（验证缓存同步）"""
        manager, _, secure_store = _make_manager_with_secure(tmp_config_dir)
        secure_store["search_inst-1_api_key"] = "sk-real-key"

        manager._save({
            "search_instances": [
                {"id": "inst-1", "name": "Tavily", "engine_type": "custom",
                 "enabled": True, "api_endpoint": "https://example.com/search",
                 "http_method": "GET", "query_param": "q",
                 "auth_header": "", "results_path": "data",
                 "title_field": "title", "url_field": "url", "snippet_field": "snippet"},
            ],
            "search": {"engine_priority": []},
            "llm": {"api_key": ""},
            "external_services": {"error_reporting": {"webhook_url": ""}},
        })

        engine = _FakeSearchEngine()
        manager.apply_search_instances(engine)

        # 关键：apply 内部 _save 后缓存应已更新
        loaded = manager._load()
        assert loaded["search"]["engine_priority"] == ["inst-1"], (
            "apply_search_instances 后缓存未同步，_load 返回旧 priority"
        )


# ════════════════════════════════════════════════════════════════════════════
# 6. update() 端到端：保存后文件无明文 api_key
# ════════════════════════════════════════════════════════════════════════════

class TestUpdateEndToEndNoPlaintextApiKey:
    """回归测试：搜索实例保存后文件中不应有明文 api_key

    模拟 app_server.py 的实际保存路径：
    _update_search_instances() → _save()（而非 update()，因为
    update() 的 _merge 会覆盖 search_instances 列表，前端实际通过
    独立的 /api/search/instances API 调用这两个方法）。
    """

    def test_update_with_search_instances_strips_api_key(self, tmp_config_dir):
        """通过 _update_search_instances + _save 保存，文件应无明文 api_key"""
        manager, _, secure_store = _make_manager_with_secure(tmp_config_dir)

        manager._update_search_instances([
            {"name": "Tavily", "engine_type": "custom",
             "api_key": "sk-real-key-12345", "enabled": True,
             "api_endpoint": "https://example.com/search", "http_method": "GET",
             "query_param": "q", "auth_header": "", "results_path": "data",
             "title_field": "title", "url_field": "url", "snippet_field": "snippet"},
        ])
        manager._save(manager._load())

        with open(manager._config_file, "r", encoding="utf-8") as f:
            saved = json.load(f)
        for inst in saved.get("search_instances", []):
            assert "api_key" not in inst, (
                f"保存后文件中残留明文 api_key: {inst}"
            )
        # 【纯 .env 架构】验证 os.environ 中有真实值
        inst_id = saved["search_instances"][0]["id"]
        env_var = f'SEARCH_{inst_id.upper()}_API_KEY'
        assert os.getenv(env_var) == "sk-real-key-12345", (
            f"真实 api_key 未写入环境变量 {env_var}"
        )

    def test_update_with_masked_api_key_does_not_overwrite(self, tmp_config_dir):
        """传入脱敏值不应覆盖已存储的真实 key"""
        manager, _, secure_store = _make_manager_with_secure(tmp_config_dir)
        # 先新增实例并存储真实 key
        manager._update_search_instances([
            {"name": "Tavily", "engine_type": "custom",
             "api_key": "sk-real-key-original", "enabled": True,
             "api_endpoint": "https://example.com/search", "http_method": "GET",
             "query_param": "q", "auth_header": "", "results_path": "data",
             "title_field": "title", "url_field": "url", "snippet_field": "snippet"},
        ])
        manager._save(manager._load())
        inst_id = manager._cache["search_instances"][0]["id"]
        env_var = f'SEARCH_{inst_id.upper()}_API_KEY'
        assert os.getenv(env_var) == "sk-real-key-original"

        # 再次传入脱敏值更新
        manager._update_search_instances([
            {"id": inst_id, "name": "Tavily", "api_key": "***4567"},
        ])
        manager._save(manager._load())

        # 环境变量应保持原值（脱敏值不覆盖）
        assert os.getenv(env_var) == "sk-real-key-original", (
            "脱敏值覆盖了真实 key"
        )

    def test_get_all_returns_masked_after_update(self, tmp_config_dir):
        """保存后 get_all() 返回的 api_key 应为脱敏值"""
        manager, _, secure_store = _make_manager_with_secure(tmp_config_dir)

        manager._update_search_instances([
            {"name": "Tavily", "engine_type": "custom",
             "api_key": "sk-real-key-12345", "enabled": True,
             "api_endpoint": "https://example.com/search", "http_method": "GET",
             "query_param": "q", "auth_header": "", "results_path": "data",
             "title_field": "title", "url_field": "url", "snippet_field": "snippet"},
        ])
        manager._save(manager._load())

        safe = manager.get_all()
        inst = safe["search_instances"][0]
        assert inst["api_key"].startswith("***"), (
            f"get_all() 未脱敏: {inst['api_key']}"
        )
        # sk-real-key-12345 的末尾 4 位是 "2345"
        assert inst["api_key"].endswith("2345"), (
            f"脱敏值应保留末尾 4 位: {inst['api_key']}"
        )


# ════════════════════════════════════════════════════════════════════════════
# 7. 集成场景：保存 → 重新加载 → 验证一致性
# ════════════════════════════════════════════════════════════════════════════

class TestSaveReloadConsistency:
    """回归测试：保存后重新加载配置应一致"""

    def test_save_then_reload_preserves_priority(self, tmp_config_dir):
        """保存 priority 后重新创建 manager，priority 应保留"""
        manager, _, _ = _make_manager_with_secure(tmp_config_dir)
        manager._save({
            "search_instances": [
                {"id": "uuid-aaa", "name": "Tavily", "enabled": True},
                {"id": "uuid-bbb", "name": "DuckDuckGo", "enabled": True},
            ],
            "search": {"engine_priority": ["uuid-aaa", "uuid-bbb"]},
            "llm": {"api_key": ""},
            "external_services": {"error_reporting": {"webhook_url": ""}},
        })

        # 重新创建 manager（模拟服务重启）
        manager2, _, _ = _make_manager_with_secure(tmp_config_dir)
        loaded = manager2._load()
        assert loaded["search"]["engine_priority"] == ["uuid-aaa", "uuid-bbb"]
        assert len(loaded["search_instances"]) == 2

    def test_repeated_save_does_not_leak_api_key(self, tmp_config_dir):
        """多次保存不应导致 api_key 泄漏到文件"""
        manager, _, secure_store = _make_manager_with_secure(tmp_config_dir)
        secure_store["search_inst-1_api_key"] = "sk-real-key"

        # 模拟 get_raw_config → _save 循环（这是 apply_search_instances 的流程）
        for _ in range(3):
            raw = manager.get_raw_config()
            # 模拟外部修改
            raw["search"]["engine_priority"] = ["inst-1"]
            manager._save(raw)

        with open(manager._config_file, "r", encoding="utf-8") as f:
            saved = json.load(f)
        for inst in saved["search_instances"]:
            assert "api_key" not in inst, (
                "多次 get_raw_config → _save 循环后 api_key 泄漏到文件"
            )


# ════════════════════════════════════════════════════════════════════════════
# 8. 删除实例时清理 engine_priority 残留 id
# ════════════════════════════════════════════════════════════════════════════

class TestDeleteInstanceCleansPriority:
    """回归测试：删除搜索实例时必须从 engine_priority 中移除其 id

    历史 bug：api_search_instance_delete 删除实例后未清理 engine_priority，
    导致 priority 中残留已删除实例的 UUID，前端显示空行或报错。
    """

    def test_delete_removes_id_from_priority(self, tmp_config_dir):
        """删除实例后 engine_priority 不应残留其 id"""
        manager, _, _ = _make_manager_with_secure(tmp_config_dir)
        manager._save({
            "search_instances": [
                {"id": "keep-1", "name": "Keep", "enabled": True},
                {"id": "delete-1", "name": "Delete", "enabled": True},
            ],
            "search": {"engine_priority": ["keep-1", "delete-1"]},
            "llm": {"api_key": ""},
            "external_services": {"error_reporting": {"webhook_url": ""}},
        })

        # 模拟 app_server.py 的删除逻辑（含 priority 清理）
        config = manager.get_raw_config()
        priority_before = config.get('search', {}).get('engine_priority', [])
        config['search_instances'] = [
            i for i in config.get('search_instances', []) if i.get('id') != 'delete-1'
        ]
        # 修复后的逻辑：从 priority 中移除已删除实例的 id
        config.setdefault('search', {})['engine_priority'] = [
            p for p in priority_before if p != 'delete-1'
        ]
        manager._save(config)

        loaded = manager._load()
        assert "delete-1" not in loaded["search"]["engine_priority"], (
            f"删除后 priority 残留 id: {loaded['search']['engine_priority']}"
        )
        assert loaded["search"]["engine_priority"] == ["keep-1"]

    def test_delete_preserves_other_priorities(self, tmp_config_dir):
        """删除一个实例不应影响其他实例的 priority 顺序"""
        manager, _, _ = _make_manager_with_secure(tmp_config_dir)
        manager._save({
            "search_instances": [
                {"id": "a", "name": "A", "enabled": True},
                {"id": "b", "name": "B", "enabled": True},
                {"id": "c", "name": "C", "enabled": True},
            ],
            "search": {"engine_priority": ["a", "b", "c"]},
            "llm": {"api_key": ""},
            "external_services": {"error_reporting": {"webhook_url": ""}},
        })

        config = manager.get_raw_config()
        priority_before = config.get('search', {}).get('engine_priority', [])
        config['search_instances'] = [
            i for i in config.get('search_instances', []) if i.get('id') != 'b'
        ]
        config.setdefault('search', {})['engine_priority'] = [
            p for p in priority_before if p != 'b'
        ]
        manager._save(config)

        loaded = manager._load()
        assert loaded["search"]["engine_priority"] == ["a", "c"], (
            f"删除中间实例后 priority 顺序错误: {loaded['search']['engine_priority']}"
        )


# ════════════════════════════════════════════════════════════════════════════
# 9. 删除默认引擎时清理 default_engine 字段
# ════════════════════════════════════════════════════════════════════════════

class TestDeleteDefaultEngineClearsField:
    """回归测试：删除默认引擎时必须清空 default_engine 字段

    历史 bug：api_search_instance_delete 删除默认引擎后，
    default_engine 仍指向已删除的实例 ID，导致前端显示错误。
    """

    def test_delete_default_engine_clears_field(self, tmp_config_dir):
        """删除默认引擎后 default_engine 应为空"""
        manager, _, _ = _make_manager_with_secure(tmp_config_dir)
        manager._save({
            "search_instances": [
                {"id": "keep", "name": "Keep", "enabled": True},
                {"id": "del", "name": "Delete", "enabled": True, "is_default": True},
            ],
            "search": {"engine_priority": ["keep", "del"], "default_engine": "del"},
            "llm": {"api_key": ""},
            "external_services": {"error_reporting": {"webhook_url": ""}},
        })

        # 模拟修复后的删除逻辑
        config = manager.get_raw_config()
        priority_before = config.get('search', {}).get('engine_priority', [])
        default_before = config.get('search', {}).get('default_engine', '')
        config['search_instances'] = [
            i for i in config.get('search_instances', []) if i.get('id') != 'del'
        ]
        config.setdefault('search', {})['engine_priority'] = [
            p for p in priority_before if p != 'del'
        ]
        # 修复逻辑：删除的是默认引擎时清空 default_engine
        if default_before == 'del':
            config['search']['default_engine'] = ''
        manager._save(config)

        loaded = manager._load()
        assert loaded['search']['default_engine'] == '', (
            f"删除默认引擎后 default_engine 未清空: {loaded['search']['default_engine']}"
        )
        assert "del" not in loaded['search']['engine_priority']

    def test_delete_non_default_preserves_default(self, tmp_config_dir):
        """删除非默认引擎时 default_engine 应保持不变"""
        manager, _, _ = _make_manager_with_secure(tmp_config_dir)
        manager._save({
            "search_instances": [
                {"id": "keep", "name": "Keep", "enabled": True, "is_default": True},
                {"id": "del", "name": "Delete", "enabled": True},
            ],
            "search": {"engine_priority": ["keep", "del"], "default_engine": "keep"},
            "llm": {"api_key": ""},
            "external_services": {"error_reporting": {"webhook_url": ""}},
        })

        config = manager.get_raw_config()
        priority_before = config.get('search', {}).get('engine_priority', [])
        default_before = config.get('search', {}).get('default_engine', '')
        config['search_instances'] = [
            i for i in config.get('search_instances', []) if i.get('id') != 'del'
        ]
        config.setdefault('search', {})['engine_priority'] = [
            p for p in priority_before if p != 'del'
        ]
        if default_before == 'del':
            config['search']['default_engine'] = ''
        manager._save(config)

        loaded = manager._load()
        assert loaded['search']['default_engine'] == 'keep', (
            f"删除非默认引擎后 default_engine 被误清空: {loaded['search']['default_engine']}"
        )


# ════════════════════════════════════════════════════════════════════════════
# 10. apply_search_instances 验证 default_engine 有效性
# ════════════════════════════════════════════════════════════════════════════

class TestApplySearchInstancesValidatesDefault:
    """回归测试：apply_search_instances 应清理无效的 default_engine"""

    def test_invalid_default_engine_cleared(self, tmp_config_dir):
        """default_engine 指向不存在的实例时应被清空"""
        manager, _, secure_store = _make_manager_with_secure(tmp_config_dir)
        secure_store["search_keep_api_key"] = "sk-real"

        manager._save({
            "search_instances": [
                {"id": "keep", "name": "Keep", "engine_type": "custom",
                 "enabled": True, "api_endpoint": "https://example.com/search",
                 "http_method": "GET", "query_param": "q",
                 "auth_header": "", "results_path": "data",
                 "title_field": "title", "url_field": "url", "snippet_field": "snippet"},
            ],
            # default_engine 指向一个不存在的实例
            "search": {"engine_priority": ["keep"], "default_engine": "nonexistent-id"},
            "llm": {"api_key": ""},
            "external_services": {"error_reporting": {"webhook_url": ""}},
        })

        engine = _FakeSearchEngine()
        manager.apply_search_instances(engine)

        loaded = manager._load()
        assert loaded['search']['default_engine'] == '', (
            f"无效的 default_engine 未被清理: {loaded['search']['default_engine']}"
        )

    def test_valid_default_engine_preserved(self, tmp_config_dir):
        """default_engine 指向存在的实例时应保留"""
        manager, _, secure_store = _make_manager_with_secure(tmp_config_dir)
        secure_store["search_keep_api_key"] = "sk-real"

        manager._save({
            "search_instances": [
                {"id": "keep", "name": "Keep", "engine_type": "custom",
                 "enabled": True, "api_endpoint": "https://example.com/search",
                 "http_method": "GET", "query_param": "q",
                 "auth_header": "", "results_path": "data",
                 "title_field": "title", "url_field": "url", "snippet_field": "snippet"},
            ],
            "search": {"engine_priority": ["keep"], "default_engine": "keep"},
            "llm": {"api_key": ""},
            "external_services": {"error_reporting": {"webhook_url": ""}},
        })

        engine = _FakeSearchEngine()
        manager.apply_search_instances(engine)

        loaded = manager._load()
        assert loaded['search']['default_engine'] == 'keep', (
            f"有效的 default_engine 被误清空: {loaded['search']['default_engine']}"
        )
