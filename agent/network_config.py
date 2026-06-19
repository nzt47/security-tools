"""网络配置管理器 - 集中管理所有联网相关的外部支持配置

负责管理以下配置项：
- API 端点 URL
- 访问令牌（敏感信息使用 AES-GCM 加密存储）
- 网络超时设置
- 外部服务开关状态
- 数据同步频率
- 代理设置
- 搜索引擎配置（优先级、启用状态、API Key）
- LLM 多实例配置
- MCP（Management Control Plane）配置

安全特性：
- 敏感信息（API Key、Token）通过 SecureConfigManager 加密存储
- 配置导入/导出时自动脱敏
- 配置变更即时生效机制
"""

import os
import json
import logging
import uuid
import datetime
from copy import deepcopy
from pathlib import Path
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)

# 配置文件路径
_NETWORK_CONFIG_FILE = Path(__file__).parent / 'data' / 'network_config.json'

# 默认配置
_DEFAULT_NETWORK_CONFIG = {
    # LLM 服务配置（单实例，兼容旧版）
    "llm": {
        "enabled": True,
        "provider": "",
        "api_key": "",  # 敏感信息，加密存储
        "model": "",
        "api_endpoint": "",
        "timeout": 30,
        "max_retries": 3,
    },
    # LLM 多实例配置（新版）
    "llm_instances": [],
    # 默认 LLM 实例 ID
    "default_llm_instance": "",
    # MCP 服务配置
    "mcp": {
        "enabled": False,
        "services": [],
    },
    # 网络基础设置
    "network": {
        "timeout": 30,
        "max_retries": 3,
        "backoff_factor": 0.5,
        "proxy_enabled": False,
        "proxy_url": "",
    },
    # 搜索服务配置
    "search": {
        "enabled": True,
        "default_engine": "",
        "max_results": 10,
        "timeout": 30,
        "cache_ttl": 300,
        "engine_priority": ["duckduckgo"],
        "engine_enabled": {},
    },
    # 搜索实例配置（新版）
    "search_instances": [],
    # Web 抓取服务
    "web_scraping": {
        "enabled": True,
        "respect_robots_txt": True,
        "delay_between_requests": 1.0,
    },
    # 浏览器自动化
    "browser": {
        "enabled": False,
        "headless": True,
        "timeout": 30,
    },
    # 数据同步
    "sync": {
        "enabled": True,
        "interval_minutes": 60,
        "auto_sync_on_start": True,
    },
    # 外部服务开关
    "external_services": {
        "error_reporting": {
            "enabled": False,
            "webhook_url": "",  # 敏感信息，加密存储
        },
        "monitoring": {
            "enabled": False,
            "endpoint": "",
        },
    },
    # 配置变更日志
    "change_log": [],
}

# LLM 实例默认配置
_DEFAULT_LLM_INSTANCE = {
    "id": "",
    "name": "",
    "provider": "",
    "api_key": "",
    "model": "",
    "api_endpoint": "",
    "auth_method": "api_key",
    "max_concurrent_requests": 5,
    "timeout": 30,
    "max_retries": 3,
    "description": "",
    "enabled": True,
    "created_at": "",
    "updated_at": "",
}

# 搜索实例默认配置
_DEFAULT_SEARCH_INSTANCE = {
    "id": "",
    "name": "",
    "engine_type": "custom",
    "api_endpoint": "",
    "http_method": "GET",
    "query_param": "q",
    "api_key": "",
    "auth_header": "Authorization: Bearer {key}",
    "results_path": "data",
    "title_field": "title",
    "url_field": "url",
    "snippet_field": "snippet",
    "enabled": True,
    "is_default": False,
    "timeout": 30,
    "created_at": "",
    "updated_at": "",
}

# MCP 服务默认配置
_DEFAULT_MCP_SERVICE = {
    "id": "",
    "name": "",
    "address": "",
    "port": 8080,
    "protocol": "http",
    "timeout": 30,
    "retry_strategy": "fixed",
    "max_retries": 3,
    "security_methods": [],
    "certificate_path": "",
    "description": "",
    "enabled": True,
    "created_at": "",
    "updated_at": "",
}


class NetworkConfigManager:
    """网络配置管理器"""

    def __init__(self, config_file: str = None, secure_manager=None):
        """
        Args:
            config_file: 配置文件路径
            secure_manager: SecureConfigManager 实例，用于加密存储敏感信息
        """
        self._config_file = Path(config_file) if config_file else _NETWORK_CONFIG_FILE
        self._secure_manager = secure_manager
        self._cache = None

    def _load(self) -> dict:
        """加载网络配置（带缓存）"""
        if self._cache is not None:
            return self._cache

        try:
            if self._config_file.exists():
                with open(self._config_file, 'r', encoding='utf-8') as f:
                    self._cache = json.load(f)
                logger.info(f"[网络配置] 已从文件加载: {self._config_file}")
            else:
                self._cache = deepcopy(_DEFAULT_NETWORK_CONFIG)
                self._save(self._cache)
                logger.info(f"[网络配置] 使用默认配置，已创建: {self._config_file}")
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"[网络配置] 加载失败，使用默认配置: {e}")
            self._cache = deepcopy(_DEFAULT_NETWORK_CONFIG)

        # 确保配置结构完整
        self._ensure_config_structure()

        return self._cache

    def _ensure_config_structure(self):
        """确保配置结构完整，添加缺失的配置项"""
        if 'llm_instances' not in self._cache:
            self._cache['llm_instances'] = []
        if 'default_llm_instance' not in self._cache:
            self._cache['default_llm_instance'] = ''
        if 'mcp' not in self._cache:
            self._cache['mcp'] = {'enabled': False, 'services': []}
        if 'change_log' not in self._cache:
            self._cache['change_log'] = []
        
        # 确保 llm 配置存在
        if 'llm' not in self._cache:
            self._cache['llm'] = {
                'enabled': True,
                'provider': '',
                'api_key': '',
                'model': '',
                'api_endpoint': '',
                'timeout': 30,
                'max_retries': 3,
            }
        
        # 确保 external_services 配置存在
        if 'external_services' not in self._cache:
            self._cache['external_services'] = {}
        if 'error_reporting' not in self._cache['external_services']:
            self._cache['external_services']['error_reporting'] = {
                'enabled': False,
                'webhook_url': '',
            }
        
        # 确保 search_instances 配置存在
        if 'search_instances' not in self._cache:
            self._cache['search_instances'] = []

        # 确保搜索实例都有 ID
        for inst in self._cache.get('search_instances', []):
            if not inst.get('id'):
                inst['id'] = str(uuid.uuid4())

        # 确保 LLM 实例都有 ID
        for instance in self._cache.get('llm_instances', []):
            if not instance.get('id'):
                instance['id'] = str(uuid.uuid4())
                logger.info(f"[网络配置] 为实例 {instance.get('name')} 自动生成 ID: {instance['id']}")

    def _save(self, data: dict):
        """保存网络配置到文件"""
        self._config_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._config_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"[网络配置] 已保存到文件: {self._config_file}")

    def _save_secure(self, key: str, value: str):
        """保存敏感配置到加密文件"""
        if self._secure_manager:
            try:
                self._secure_manager.set_secure_value(key, value)
                logger.info(f"[网络配置] 已加密保存: {key}")
            except Exception as e:
                logger.error(f"[网络配置] 加密保存失败 {key}: {e}")
        else:
            logger.warning("[网络配置] SecureManager 未初始化，敏感信息将明文存储")

    def _load_secure(self, key: str, default: str = None) -> str:
        """从加密文件加载敏感配置"""
        if self._secure_manager:
            try:
                return self._secure_manager.get_secure_value(key, default)
            except Exception as e:
                logger.error(f"[网络配置] 加密加载失败 {key}: {e}")
                return default
        return default

    def _add_change_log(self, action: str, section: str, details: dict = None):
        """添加配置变更日志"""
        import datetime
        log_entry = {
            "id": str(uuid.uuid4()),
            "timestamp": datetime.datetime.now().isoformat(),
            "action": action,
            "section": section,
            "details": details or {},
        }
        # 只保留最近 100 条日志
        self._cache['change_log'].insert(0, log_entry)
        if len(self._cache['change_log']) > 100:
            self._cache['change_log'] = self._cache['change_log'][:100]

    def get_all(self) -> dict:
        """获取完整配置（敏感信息脱敏）"""
        config = self._load()

        # 加载加密的敏感信息
        config['llm']['api_key'] = self._load_secure('llm_api_key', config.get('llm', {}).get('api_key', ''))
        config['external_services']['error_reporting']['webhook_url'] = self._load_secure(
            'error_reporting_webhook',
            config.get('external_services', {}).get('error_reporting', {}).get('webhook_url', '')
        )

        # 加载 LLM 实例的敏感信息
        for instance in config.get('llm_instances', []):
            instance_id = instance.get('id', instance.get('name', 'default'))
            instance['api_key'] = self._load_secure(
                f'llm_{instance_id}_api_key',
                instance.get('api_key', '')
            )

        # 加载搜索实例的敏感信息
        for inst in config.get('search_instances', []):
            inst_id = inst.get('id', '')
            if inst_id:
                inst['api_key'] = self._load_secure(
                    f'search_{inst_id}_api_key',
                    inst.get('api_key', '')
                )

        # 脱敏处理
        safe_config = deepcopy(config)
        
        # LLM API Key 脱敏
        if safe_config['llm'].get('api_key'):
            safe_config['llm']['api_key'] = '***' + safe_config['llm']['api_key'][-4:] if len(safe_config['llm']['api_key']) > 4 else '***'
        
        # 错误报告 Webhook 脱敏
        if safe_config['external_services']['error_reporting'].get('webhook_url'):
            safe_config['external_services']['error_reporting']['webhook_url'] = '***'

        # LLM 实例 API Key 脱敏
        for instance in safe_config.get('llm_instances', []):
            if instance.get('api_key'):
                value = instance['api_key']
                instance['api_key'] = '***' + value[-4:] if len(value) > 4 else '***'

        # 搜索实例 API Key 脱敏
        for inst in safe_config.get('search_instances', []):
            if inst.get('api_key'):
                v = inst['api_key']
                inst['api_key'] = '***' + v[-4:] if len(v) > 4 else '***'

        return safe_config

    def get_raw_config(self) -> dict:
        """获取完整原始配置（包含解密后的敏感信息）"""
        config = self._load()

        # 加载加密的敏感信息
        config['llm']['api_key'] = self._load_secure('llm_api_key', config.get('llm', {}).get('api_key', ''))
        config['external_services']['error_reporting']['webhook_url'] = self._load_secure(
            'error_reporting_webhook',
            config.get('external_services', {}).get('error_reporting', {}).get('webhook_url', '')
        )

        # 加载 LLM 实例的敏感信息
        for instance in config.get('llm_instances', []):
            instance_id = instance.get('id', instance.get('name', 'default'))
            instance['api_key'] = self._load_secure(
                f'llm_{instance_id}_api_key',
                instance.get('api_key', '')
            )

        # 加载搜索实例的敏感信息
        for inst in config.get('search_instances', []):
            inst_id = inst.get('id', '')
            if inst_id:
                inst['api_key'] = self._load_secure(
                    f'search_{inst_id}_api_key',
                    inst.get('api_key', '')
                )

        return config

    def update(self, updates: dict) -> dict:
        """更新网络配置

        Args:
            updates: 配置更新字典，格式与 get_all() 返回一致

        Returns:
            更新后的完整配置
        """
        logger.info("[网络配置] 开始更新配置...")
        config = self._load()

        # 处理 LLM API Key（敏感信息）
        if 'llm' in updates:
            api_key = updates['llm'].get('api_key')
            if api_key and api_key != '***' and not api_key.startswith('***'):
                logger.info("[网络配置] 检测到新的 LLM API Key，准备加密保存...")
                self._save_secure('llm_api_key', api_key)
                logger.info("[网络配置] LLM API Key 已加密保存")
            elif api_key and api_key.startswith('***'):
                logger.info("[网络配置] LLM API Key 未变更（脱敏值），跳过更新")

        # 处理错误报告 Webhook URL（敏感信息）
        if 'external_services' in updates:
            if 'error_reporting' in updates['external_services']:
                webhook_url = updates['external_services']['error_reporting'].get('webhook_url')
                if webhook_url and webhook_url != '***' and not webhook_url.startswith('***'):
                    logger.info("[网络配置] 检测到新的 Webhook URL，准备加密保存...")
                    self._save_secure('error_reporting_webhook', webhook_url)
                    logger.info("[网络配置] Webhook URL 已加密保存")
                elif webhook_url and webhook_url.startswith('***'):
                    logger.info("[网络配置] Webhook URL 未变更（脱敏值），跳过更新")

        # 处理 LLM 实例
        if 'llm_instances' in updates:
            self._update_llm_instances(updates['llm_instances'])

        # 处理搜索实例
        if 'search_instances' in updates:
            self._update_search_instances(updates['search_instances'])

        # 处理 MCP 配置
        if 'mcp' in updates:
            self._update_mcp_config(updates['mcp'])

        # 递归合并配置
        logger.info("[网络配置] 合并配置到当前配置...")
        self._merge(config, updates)

        # 保存到文件
        logger.info("[网络配置] 保存配置到文件: %s", self._config_file)
        self._save(config)

        # 清除缓存
        self._cache = config

        # 记录变更日志
        self._add_change_log('update', 'config', {'keys': list(updates.keys())})

        logger.info("[网络配置] 配置已更新到文件")
        return self.get_all()

    def _update_llm_instances(self, instances: list):
        """更新 LLM 实例配置"""
        config = self._load()
        
        for instance in instances:
            instance_id = instance.get('id')
            if not instance_id:
                # 新增实例
                instance['id'] = str(uuid.uuid4())
                instance['created_at'] = instance.get('created_at') or datetime.datetime.now().isoformat()
                instance['updated_at'] = instance['created_at']
                
                # 加密保存 API Key
                api_key = instance.get('api_key', '')
                if api_key and api_key != '***' and not api_key.startswith('***'):
                    self._save_secure(f'llm_{instance["id"]}_api_key', api_key)
                
                config['llm_instances'].append(instance)
                self._add_change_log('add', 'llm_instance', {'id': instance['id'], 'name': instance.get('name')})
            else:
                # 更新现有实例
                existing = next((i for i in config['llm_instances'] if i['id'] == instance_id), None)
                if existing:
                    # 处理 API Key 更新
                    api_key = instance.get('api_key', '')
                    if api_key and api_key != '***' and not api_key.startswith('***'):
                        self._save_secure(f'llm_{instance_id}_api_key', api_key)
                    
                    existing.update(instance)
                    existing['updated_at'] = datetime.datetime.now().isoformat()
                    self._add_change_log('update', 'llm_instance', {'id': instance_id, 'name': instance.get('name')})

    def _update_search_instances(self, instances: list):
        """更新搜索实例配置"""
        config = self._load()
        for inst in instances:
            inst_id = inst.get('id')
            if not inst_id:
                # 新增
                inst['id'] = str(uuid.uuid4())
                inst['created_at'] = datetime.datetime.now().isoformat()
                inst['updated_at'] = inst['created_at']
                api_key = inst.get('api_key', '')
                if api_key and api_key != '***' and not api_key.startswith('***'):
                    self._save_secure(f'search_{inst["id"]}_api_key', api_key)
                config['search_instances'].append(inst)
                self._add_change_log('add', 'search_instance', {'id': inst['id'], 'name': inst.get('name')})
            else:
                existing = next((i for i in config['search_instances'] if i['id'] == inst_id), None)
                if existing:
                    api_key = inst.get('api_key', '')
                    if api_key and api_key != '***' and not api_key.startswith('***'):
                        self._save_secure(f'search_{inst_id}_api_key', api_key)
                    existing.update(inst)
                    existing['updated_at'] = datetime.datetime.now().isoformat()
                    self._add_change_log('update', 'search_instance', {'id': inst_id, 'name': inst.get('name')})

    def _update_mcp_config(self, mcp_config: dict):
        """更新 MCP 配置"""
        config = self._load()
        config['mcp'] = mcp_config
        
        # 添加变更日志
        if 'services' in mcp_config:
            for service in mcp_config['services']:
                if 'id' in service:
                    existing = next((s for s in config['mcp']['services'] if s['id'] == service['id']), None)
                    if existing:
                        self._add_change_log('update', 'mcp_service', {'id': service['id'], 'name': service.get('name')})
                    else:
                        service['id'] = str(uuid.uuid4())
                        service['created_at'] = datetime.datetime.now().isoformat()
                        service['updated_at'] = service['created_at']
                        self._add_change_log('add', 'mcp_service', {'id': service['id'], 'name': service.get('name')})

    def _register_search_instance(self, instance: dict, search_engine):
        """注册单个搜索实例到 SearchEngine

        Args:
            instance: 搜索实例配置字典
            search_engine: SearchEngine 实例
        """
        if not search_engine:
            return
        inst_id = instance.get('id', '')
        name = instance.get('name', inst_id)
        engine_type = instance.get('engine_type', 'custom')
        enabled = instance.get('enabled', True)

        from functools import partial
        if engine_type == 'custom':
            handler = partial(search_engine._search_custom, instance)
        else:
            # 内置引擎用专用 handler，回退到通用 handler
            handler = getattr(search_engine, f'_search_{engine_type}', None)
            if handler is None:
                handler = partial(search_engine._search_custom, instance)

        # 注册引擎（UUID 供管理，名称/类型名供 LLM 调用）
        search_engine.register_engine(
            name=inst_id, label=name, handler=handler,
            needs_key=bool(instance.get('api_key')),
            description=f"搜索引擎: {engine_type}",
        )
        search_engine.register_engine(
            name=name, label=name, handler=handler,
            needs_key=bool(instance.get('api_key')),
            description=f"搜索引擎: {engine_type}",
        )
        if engine_type != 'custom':
            search_engine.register_engine(
                name=engine_type, label=name, handler=handler,
                needs_key=bool(instance.get('api_key')),
                description=f"搜索引擎: {engine_type}",
            )

        # 同步 API Key 到引擎注册表（供内置 handler 读取）
        api_key = instance.get('api_key', '')
        if api_key and engine_type != 'custom':
            search_engine._api_keys[engine_type] = api_key

        # 设置启用状态和默认引擎
        search_engine.set_engine_enabled(inst_id, enabled)
        if instance.get('is_default'):
            search_engine.set_default_engine(inst_id)

    def apply_search_instances(self, search_engine=None):
        """将搜索实例注册到 SearchEngine

        先清理已注册的引擎，再根据配置重新注册所有实例。

        Args:
            search_engine: SearchEngine 实例
        """
        if not search_engine:
            return
        config = self.get_raw_config()
        instances = config.get('search_instances', [])

        # 首次运行：将内置引擎填充为实例
        if not instances:
            instances = self._seed_builtin_search_instances(config)
            config['search_instances'] = instances
            self._save(config)

        logger.info("[网络配置] 开始注册 %d 个搜索实例...", len(instances))

        # 先清理之前已注册的引擎
        for inst in instances:
            inst_id = inst.get('id', '')
            if inst_id in search_engine._engine_registry:
                search_engine.remove_engine(inst_id)

        # 重新注册
        for inst in instances:
            inst_id = inst.get('id', '')
            if not inst_id:
                continue  # 跳过没有 ID 的实例
            if not inst.get('enabled', True):
                continue
            self._register_search_instance(inst, search_engine)

        # 更新优先级：实例按配置顺序排列
        instance_ids = [inst.get('id', '') for inst in instances if inst.get('id') and inst.get('enabled', True)]
        if instance_ids:
            priority = list(search_engine._engine_priority)
            # 把实例 ID 移到前面
            for cid in instance_ids:
                if cid in priority:
                    priority.remove(cid)
            priority = instance_ids + priority
            search_engine.set_engine_priority(priority)

        logger.info("[网络配置] 搜索实例注册完成")

        # 同步到 web_search 工具的 engine 参数 enum
        try:
            from agent.tools import sync_web_search_engines
            sync_web_search_engines([], search_engine=search_engine)
        except ImportError:
            pass  # 工具模块不可用时跳过

    def reset(self) -> dict:
        """重置为默认配置"""
        self._cache = deepcopy(_DEFAULT_NETWORK_CONFIG)
        self._save(self._cache)
        self._add_change_log('reset', 'all')
        logger.info("[网络配置] 已重置为默认配置")
        return self.get_all()

    def export_config(self) -> str:
        """导出配置为 JSON 字符串（脱敏）"""
        config = self.get_all()
        return json.dumps(config, ensure_ascii=False, indent=2)

    def import_config(self, json_str: str, conflict_strategy: str = 'overwrite') -> dict:
        """从 JSON 字符串导入配置

        Args:
            json_str: JSON 格式的配置字符串
            conflict_strategy: 冲突处理策略: overwrite(覆盖), skip(跳过), merge(合并)

        Returns:
            导入后的配置

        Raises:
            ValueError: JSON 格式错误
        """
        try:
            imported = json.loads(json_str)
        except json.JSONDecodeError as e:
            raise ValueError(f"配置格式错误: {e}")

        config = self._load()

        # 处理冲突
        if conflict_strategy == 'overwrite':
            # 完全覆盖
            self._cache = imported
            # 确保基本结构存在
            self._ensure_config_structure()
        elif conflict_strategy == 'skip':
            # 跳过已存在的配置项
            self._merge_skip_existing(config, imported)
        elif conflict_strategy == 'merge':
            # 合并配置
            self._merge(config, imported)

        # 保存并记录日志
        self._save(self._cache)
        self._add_change_log('import', 'all', {'strategy': conflict_strategy})

        return self.get_all()

    def _merge_skip_existing(self, target: dict, source: dict):
        """合并配置，跳过已存在的键"""
        for key, value in source.items():
            if key not in target:
                target[key] = value
            elif isinstance(target[key], dict) and isinstance(value, dict):
                self._merge_skip_existing(target[key], value)

    def _merge(self, target: dict, source: dict):
        """递归合并配置字典"""
        for key, value in source.items():
            if key in target and isinstance(target[key], dict) and isinstance(value, dict):
                self._merge(target[key], value)
            else:
                target[key] = value

    # ════════════════════════════════════════════════════════════
    # LLM 实例管理 API
    # ════════════════════════════════════════════════════════════

    def get_llm_instances(self) -> List[dict]:
        """获取所有 LLM 实例（脱敏）"""
        logger.info(f"[网络配置] 获取所有 LLM 实例")
        config = self.get_all()
        instances = config.get('llm_instances', [])
        logger.info(f"[网络配置] 获取到 {len(instances)} 个 LLM 实例")
        return instances

    def get_llm_instance(self, instance_id: str) -> Optional[dict]:
        """获取单个 LLM 实例"""
        logger.info(f"[网络配置] 获取 LLM 实例: instance_id={instance_id}")
        instances = self.get_llm_instances()
        result = next((i for i in instances if i.get('id') == instance_id or i.get('name') == instance_id), None)
        if result:
            logger.info(f"[网络配置] 找到 LLM 实例: name={result['name']}")
        else:
            logger.warning(f"[网络配置] 未找到 LLM 实例: instance_id={instance_id}")
        return result

    def add_llm_instance(self, instance: dict) -> dict:
        """添加 LLM 实例"""
        import datetime
        
        logger.info(f"[网络配置] 开始添加 LLM 实例: name={instance.get('name')}, provider={instance.get('provider')}")
        
        new_instance = deepcopy(_DEFAULT_LLM_INSTANCE)
        new_instance.update(instance)
        new_instance['id'] = str(uuid.uuid4())
        new_instance['created_at'] = datetime.datetime.now().isoformat()
        new_instance['updated_at'] = new_instance['created_at']
        
        logger.debug(f"[网络配置] LLM 实例初始化完成: id={new_instance['id']}, model={new_instance.get('model')}")

        config = self._load()
        
        # 检查名称是否重复
        if any(i['name'] == new_instance['name'] for i in config['llm_instances']):
            logger.warning(f"[网络配置] LLM 实例名称重复: {new_instance['name']}")
            raise ValueError(f"LLM 实例名称已存在: {new_instance['name']}")

        # 加密保存 API Key
        api_key = new_instance.get('api_key', '')
        if api_key and api_key != '***' and not api_key.startswith('***'):
            logger.debug(f"[网络配置] 加密保存 LLM 实例 API Key: id={new_instance['id']}")
            self._save_secure(f'llm_{new_instance["id"]}_api_key', api_key)
            new_instance['api_key'] = api_key  # 保持原始值用于后续处理

        config['llm_instances'].append(new_instance)
        self._save(config)
        self._add_change_log('add', 'llm_instance', {'id': new_instance['id'], 'name': new_instance['name']})

        logger.info(f"[网络配置] 已成功添加 LLM 实例: id={new_instance['id']}, name={new_instance['name']}, provider={new_instance.get('provider')}")
        return self.get_llm_instance(new_instance['id'])

    def update_llm_instance(self, instance_id: str, updates: dict) -> Optional[dict]:
        """更新 LLM 实例
        
        Args:
            instance_id: 实例 ID 或实例名称
        """
        import datetime
        
        logger.info(f"[网络配置] 开始更新 LLM 实例: instance_id={instance_id}, updates_keys={list(updates.keys())}")
        
        config = self._load()
        instances = config.get('llm_instances', [])
        
        for instance in instances:
            # 支持 UUID 和名称匹配
            if (instance.get('id') == instance_id or instance.get('name') == instance_id):
                actual_id = instance.get('id') or instance.get('name')
                
                # 检查名称是否与其他实例重复
                if 'name' in updates:
                    if any(i['name'] == updates['name'] and (i.get('id') or i.get('name')) != actual_id for i in instances):
                        logger.warning(f"[网络配置] LLM 实例名称重复: {updates['name']}")
                        raise ValueError(f"LLM 实例名称已存在: {updates['name']}")

                # 处理 API Key 更新
                api_key = updates.get('api_key', '')
                if api_key and api_key != '***' and not api_key.startswith('***'):
                    logger.debug(f"[网络配置] 更新 LLM 实例 API Key: instance_id={actual_id}")
                    self._save_secure(f'llm_{actual_id}_api_key', api_key)
                elif api_key and api_key.startswith('***'):
                    updates.pop('api_key', None)  # 跳过脱敏值

                instance.update(updates)
                instance['updated_at'] = datetime.datetime.now().isoformat()
                
                self._save(config)
                self._add_change_log('update', 'llm_instance', {'id': actual_id, 'name': instance.get('name')})
                
                logger.info(f"[网络配置] 已成功更新 LLM 实例: id={actual_id}, name={instance.get('name')}")
                return self.get_llm_instance(actual_id)
        
        logger.warning(f"[网络配置] 更新 LLM 实例失败，未找到实例: instance_id={instance_id}")
        return None

    def delete_llm_instance(self, instance_id: str) -> bool:
        """删除 LLM 实例
        
        Args:
            instance_id: 实例 ID 或实例名称
        """
        logger.info(f"[网络配置] 开始删除 LLM 实例: instance_id={instance_id}")
        
        config = self._load()
        instances = config.get('llm_instances', [])
        
        # 找到要删除的实例
        instance_to_delete = None
        for instance in instances:
            if instance.get('id') == instance_id or instance.get('name') == instance_id:
                instance_to_delete = instance
                break
        
        if not instance_to_delete:
            logger.warning(f"[网络配置] 删除 LLM 实例失败，未找到实例: instance_id={instance_id}")
            return False
        
        actual_id = instance_to_delete.get('id') or instance_to_delete.get('name')
        
        # 删除实例
        config['llm_instances'] = [i for i in instances if (i.get('id') or i.get('name')) != actual_id]
        
        # 删除对应的加密密钥
        logger.debug(f"[网络配置] 删除 LLM 实例加密密钥: instance_id={actual_id}")
        self._save_secure(f'llm_{actual_id}_api_key', '')
        
        # 如果删除的是默认实例，清空 default_llm_instance
        if config.get('default_llm_instance') == actual_id:
            config['default_llm_instance'] = ''
        
        self._save(config)
        self._add_change_log('delete', 'llm_instance', {'id': actual_id})
        
        logger.info(f"[网络配置] 已成功删除 LLM 实例: instance_id={actual_id}")
        return True

    def set_default_llm_instance(self, instance_id: str) -> bool:
        """设置默认 LLM 实例
        
        Args:
            instance_id: 实例 ID 或实例名称
        """
        logger.info(f"[网络配置] 开始设置默认 LLM 实例: instance_id={instance_id}")
        
        config = self._load()
        
        # 检查实例是否存在（支持 UUID 和名称匹配）
        instance_found = None
        for instance in config.get('llm_instances', []):
            if instance.get('id') == instance_id or instance.get('name') == instance_id:
                instance_found = instance
                break
        
        if not instance_found:
            logger.warning(f"[网络配置] 设置默认 LLM 实例失败，实例不存在: instance_id={instance_id}")
            return False
        
        # 获取实际的实例 ID（优先使用 UUID，如果没有则使用名称）
        actual_id = instance_found.get('id') or instance_found.get('name')
        logger.info(f"[网络配置] 找到实例: name={instance_found.get('name')}, actual_id={actual_id}")
        
        # 更新所有实例的 is_default 标记
        for instance in config.get('llm_instances', []):
            instance['is_default'] = ((instance.get('id') or instance.get('name')) == actual_id)
        
        # 更新默认实例 ID
        config['default_llm_instance'] = actual_id
        
        self._save(config)
        self._add_change_log('update', 'llm_instance', {'id': actual_id, 'action': 'set_default'})
        
        logger.info(f"[网络配置] 已成功设置默认 LLM 实例: instance_id={actual_id}")
        return True

    # ════════════════════════════════════════════════════════════
    # MCP 服务管理 API
    # ════════════════════════════════════════════════════════════

    def get_mcp_services(self) -> List[dict]:
        """获取所有 MCP 服务"""
        config = self.get_all()
        return config.get('mcp', {}).get('services', [])

    def get_mcp_service(self, service_id: str) -> Optional[dict]:
        """获取单个 MCP 服务"""
        services = self.get_mcp_services()
        return next((s for s in services if s['id'] == service_id), None)

    def add_mcp_service(self, service: dict) -> dict:
        """添加 MCP 服务"""
        import datetime
        
        new_service = deepcopy(_DEFAULT_MCP_SERVICE)
        new_service.update(service)
        new_service['id'] = str(uuid.uuid4())
        new_service['created_at'] = datetime.datetime.now().isoformat()
        new_service['updated_at'] = new_service['created_at']

        config = self._load()
        
        # 检查名称是否重复
        if any(s['name'] == new_service['name'] for s in config['mcp'].get('services', [])):
            raise ValueError(f"MCP 服务名称已存在: {new_service['name']}")

        config['mcp']['services'].append(new_service)
        self._save(config)
        self._add_change_log('add', 'mcp_service', {'id': new_service['id'], 'name': new_service['name']})

        logger.info(f"[网络配置] 已添加 MCP 服务: {new_service['name']}")
        return self.get_mcp_service(new_service['id'])

    def update_mcp_service(self, service_id: str, updates: dict) -> Optional[dict]:
        """更新 MCP 服务"""
        import datetime
        
        config = self._load()
        services = config.get('mcp', {}).get('services', [])
        
        for service in services:
            if service['id'] == service_id:
                # 检查名称是否与其他服务重复
                if 'name' in updates:
                    if any(s['name'] == updates['name'] and s['id'] != service_id for s in services):
                        raise ValueError(f"MCP 服务名称已存在: {updates['name']}")

                service.update(updates)
                service['updated_at'] = datetime.datetime.now().isoformat()
                
                self._save(config)
                self._add_change_log('update', 'mcp_service', {'id': service_id, 'name': service.get('name')})
                
                logger.info(f"[网络配置] 已更新 MCP 服务: {service['name']}")
                return self.get_mcp_service(service_id)
        
        return None

    def delete_mcp_service(self, service_id: str) -> bool:
        """删除 MCP 服务"""
        config = self._load()
        services = config.get('mcp', {}).get('services', [])
        
        before_count = len(services)
        config['mcp']['services'] = [s for s in services if s['id'] != service_id]
        
        if len(config['mcp']['services']) < before_count:
            self._save(config)
            self._add_change_log('delete', 'mcp_service', {'id': service_id})
            
            logger.info(f"[网络配置] 已删除 MCP 服务: {service_id}")
            return True
        
        return False

    def get_change_log(self, limit: int = 20) -> List[dict]:
        """获取配置变更日志"""
        config = self._load()
        return config.get('change_log', [])[:limit]

    def apply_to_app(self, app_instance=None):
        """将网络配置应用到应用实例

        Args:
            app_instance: 应用实例（如 DigitalLife），用于即时生效配置
        """
        logger.info("[网络配置] 开始将配置应用到应用实例...")
        logger.info("[网络配置] 应用实例: %s", app_instance)
        logger.info("[网络配置] 应用实例类型: %s", type(app_instance))
        
        config = self.get_raw_config()

        # 应用到 HttpClient 配置
        try:
            if app_instance and hasattr(app_instance, '_web_http'):
                old_timeout = getattr(app_instance._web_http, 'timeout', None)
                new_timeout = config['network']['timeout']
                app_instance._web_http.timeout = new_timeout
                logger.info("[网络配置] [即时生效] HTTP 客户端超时已更新: %s → %ss",
                           old_timeout, new_timeout)
                logger.info("[网络配置] HTTP 客户端当前状态: timeout=%s, max_retries=%s",
                           app_instance._web_http.timeout,
                           getattr(app_instance._web_http, 'max_retries', 'N/A'))
            else:
                logger.warning("[网络配置] 应用实例无 _web_http 属性，跳过 HTTP 配置应用")
        except Exception as e:
            logger.warning("[网络配置] 应用 HTTP 配置失败: %s", e, exc_info=True)

        # 应用到搜索引擎配置
        try:
            if app_instance and hasattr(app_instance, '_web_search'):
                search_config = config['search']

                # 更新搜索引擎配置
                update_config = {
                    'engine_priority': search_config.get('engine_priority', ['duckduckgo']),
                    'engine_enabled': search_config.get('engine_enabled', {}),
                    'timeout': search_config.get('timeout', 30),
                    'default_engine': search_config.get('default_engine', ''),
                }

                app_instance._web_search.update_config(update_config)
                logger.info("[网络配置] [即时生效] 搜索引擎配置已更新:")
                logger.info(f"  - 默认引擎: {search_config.get('default_engine')}")
                logger.info(f"  - 优先级: {search_config.get('engine_priority')}")
                logger.info(f"  - 超时: {search_config.get('timeout')}s")
                logger.info(f"  - 启用状态: {search_config.get('engine_enabled')}")
            else:
                logger.warning("[网络配置] 应用实例无 _web_search 属性，跳过搜索引擎配置应用")
        except Exception as e:
            logger.warning("[网络配置] 应用搜索引擎配置失败: %s", e, exc_info=True)

        # 注册搜索实例
        try:
            if app_instance and hasattr(app_instance, '_web_search'):
                self.apply_search_instances(app_instance._web_search)
                logger.info("[网络配置] 搜索实例已注册")
            else:
                logger.warning("[网络配置] 应用实例无 _web_search 属性，跳过搜索实例注册")
        except Exception as e:
            logger.warning("[网络配置] 注册搜索实例失败: %s", e, exc_info=True)

        # 应用到 LLM 配置
        if app_instance and hasattr(app_instance, 'configure_llm'):
            llm = config['llm']

            # ── 确定最终 LLM 参数：优先使用 llm_instances 中配置的实例 ──
            provider = llm.get('provider', '')
            api_key = llm.get('api_key', '')
            model = llm.get('model', '')
            base_url = llm.get('api_endpoint', '')

            llm_instances = config.get('llm_instances', [])
            instance_source = 'legacy'
            if llm_instances:
                # 先找默认实例
                default_id = config.get('default_llm_instance', '')
                selected = None
                if default_id:
                    selected = next(
                        (i for i in llm_instances
                         if i.get('id') == default_id and i.get('enabled', False)),
                        None
                    )
                    if selected:
                        instance_source = f"default({selected.get('name')})"
                # 没有默认实例，用第一个启用的
                if not selected:
                    selected = next(
                        (i for i in llm_instances if i.get('enabled', False)),
                        None
                    )
                    if selected:
                        instance_source = f"first_enabled({selected.get('name')})"

                if selected:
                    provider = selected.get('provider') or provider
                    api_key = selected.get('api_key') or api_key
                    model = selected.get('model') or model
                    base_url = selected.get('api_endpoint') or base_url
                    logger.info("[网络配置] 使用 LLM 实例 [%s]: name=%s, provider=%s, model=%s",
                               instance_source, selected.get('name'), provider, model)

            logger.info("[网络配置] LLM 配置状态: enabled=%s, provider=%s, api_key_set=%s, model=%s",
                       llm['enabled'], provider,
                       '***' if api_key and not api_key.startswith('***') else 'no',
                       model)

            if llm['enabled'] and provider and api_key:
                logger.info("[网络配置] 正在调用 configure_llm (来源: %s)...", instance_source)
                try:
                    result = app_instance.configure_llm(
                        provider=provider,
                        api_key=api_key,
                        model=model,
                        base_url=base_url,
                    )
                    if result.get('ok'):
                        logger.info("[网络配置] [即时生效] LLM 配置已应用: %s/%s",
                                   provider, model)
                    else:
                        logger.warning("[网络配置] LLM 配置应用失败: %s", result.get('error'))
                except Exception as e:
                    logger.warning("[网络配置] 应用 LLM 配置失败: %s", e, exc_info=True)
            else:
                logger.info("[网络配置] LLM 配置不完整，跳过 LLM 应用 (enabled=%s, provider=%s, api_key=%s)",
                           llm['enabled'], bool(provider), bool(api_key))

        logger.info("[网络配置] 配置应用完成")

    def get_search_engines(self) -> dict:
        """获取搜索引擎配置信息"""
        config = self.get_raw_config()
        search_config = config.get('search', {})

        return {
            'enabled': search_config.get('enabled', True),
            'default_engine': search_config.get('default_engine', ''),
            'max_results': search_config.get('max_results', 10),
            'timeout': search_config.get('timeout', 30),
            'engine_priority': search_config.get('engine_priority', ['duckduckgo']),
            'engine_enabled': search_config.get('engine_enabled', {}),
            'api_keys': {},
        }

    def update_search_config(self, search_updates: dict) -> dict:
        """更新搜索引擎配置（即时生效）"""
        logger.info("[网络配置] 更新搜索引擎配置: %s", search_updates)
        
        # 构建更新字典
        updates = {}
        
        # 处理搜索基础配置
        if 'default_engine' in search_updates:
            updates['search'] = updates.get('search', {})
            updates['search']['default_engine'] = search_updates['default_engine']
        
        if 'max_results' in search_updates:
            updates['search'] = updates.get('search', {})
            updates['search']['max_results'] = search_updates['max_results']
        
        if 'timeout' in search_updates:
            updates['search'] = updates.get('search', {})
            updates['search']['timeout'] = search_updates['timeout']
        
        if 'engine_priority' in search_updates:
            updates['search'] = updates.get('search', {})
            updates['search']['engine_priority'] = search_updates['engine_priority']
        
        if 'engine_enabled' in search_updates:
            updates['search'] = updates.get('search', {})
            updates['search']['engine_enabled'] = search_updates['engine_enabled']
        
        # 执行更新
        if updates:
            self.update(updates)
        
        logger.info("[网络配置] 搜索引擎配置更新完成")
        return self.get_search_engines()

    def validate_llm_instance(self, instance: dict) -> List[str]:
        """验证 LLM 实例配置"""
        errors = []
        
        if not instance.get('name'):
            errors.append('服务名称不能为空')
        if not instance.get('api_endpoint'):
            errors.append('API 端点 URL 不能为空')
        if not instance.get('provider'):
            errors.append('提供商不能为空')
        
        # URL 格式验证
        if instance.get('api_endpoint'):
            try:
                from urllib.parse import urlparse
                parsed = urlparse(instance['api_endpoint'])
                if not parsed.scheme or not parsed.netloc:
                    errors.append('API 端点 URL 格式无效')
            except Exception:
                errors.append('API 端点 URL 格式无效')
        
        # 数值验证
        if 'max_concurrent_requests' in instance:
            if not isinstance(instance['max_concurrent_requests'], int) or instance['max_concurrent_requests'] < 1:
                errors.append('最大并发请求数必须是正整数')
        
        if 'timeout' in instance:
            if not isinstance(instance['timeout'], int) or instance['timeout'] < 1 or instance['timeout'] > 300:
                errors.append('超时时间必须在 1-300 秒之间')
        
        if 'max_retries' in instance:
            if not isinstance(instance['max_retries'], int) or instance['max_retries'] < 0 or instance['max_retries'] > 10:
                errors.append('最大重试次数必须在 0-10 之间')
        
        return errors

    def validate_mcp_service(self, service: dict) -> List[str]:
        """验证 MCP 服务配置"""
        errors = []
        
        if not service.get('name'):
            errors.append('服务名称不能为空')
        if not service.get('address'):
            errors.append('MCP 服务地址不能为空')
        if not service.get('port'):
            errors.append('通信端口不能为空')
        
        # 端口范围验证
        if service.get('port'):
            if not isinstance(service['port'], int) or service['port'] < 1 or service['port'] > 65535:
                errors.append('通信端口必须在 1-65535 之间')
        
        # 协议类型验证
        if service.get('protocol') and service['protocol'] not in ['http', 'https']:
            errors.append('协议类型必须是 HTTP 或 HTTPS')
        
        # 超时时间验证
        if service.get('timeout'):
            if not isinstance(service['timeout'], int) or service['timeout'] < 1 or service['timeout'] > 300:
                errors.append('超时时间必须在 1-300 秒之间')
        
        # 重试策略验证
        if service.get('retry_strategy') and service['retry_strategy'] not in ['fixed', 'exponential', 'none']:
            errors.append('重试策略必须是固定间隔/指数退避/无重试')
        
        # 重试次数验证
        if service.get('max_retries'):
            if not isinstance(service['max_retries'], int) or service['max_retries'] < 0 or service['max_retries'] > 10:
                errors.append('重试次数必须在 0-10 之间')
        
        return errors