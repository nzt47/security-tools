"""MemoryRouter — 基于任务特征的智能记忆路由

设计思想（设计文档 3.3）：
- 不同任务类型映射到不同记忆提供商
- 云端不可用时自动降级到 HolographicAdapter（本地优先）
- 支持逐适配器注册、运行时替换
- 集成多级缓存作为统一的缓存层

路由策略表：

| 任务类型            | 首选适配器       | 能力需求           | 降级目标     |
|--------------------|------------------|-------------------|--------------|
| deep_reasoning     | hindsight        | 长链推理记忆       | holographic  |
| local_privacy      | holographic      | 纯本地运行         | — (兜底)     |
| user_profile       | honcho           | 用户画像管理       | holographic  |
| fact_extraction    | mem0             | 事实提取与去重     | holographic  |
| knowledge_nav      | openviking       | 知识图谱导航       | holographic  |
"""

import logging
import json
import uuid
from typing import Optional, Tuple, Any, List

from agent.memory.base import MemoryInterface, MemoryResult
from agent.memory.adapters.holographic_adapter import HolographicAdapter
from agent.memory.adapters.mem0_adapter import Mem0Adapter
from agent.logging_utils import log_dict

# 延迟导入敏感数据过滤器，避免循环依赖
# SensitiveDataFilter 来自 agent.utils.sensitive_data_filter（统一实现）
# 通过 agent.memory.filter 兼容层导入（向后兼容别名）
logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]



def _get_sensitive_filter():
    """延迟获取敏感数据过滤器实例（避免循环依赖）

    Returns:
        SensitiveDataFilter 实例
    """
    from agent.memory.filter import SensitiveDataFilter
    return SensitiveDataFilter()


class MemoryRouter:
    """基于任务特征的智能记忆路由

    用法:
        router = MemoryRouter()
        # 自动选择适配器
        adapter = router.route("local_privacy")
        await adapter.save("key", "data")

        # 手动覆盖
        router.register("custom", CustomAdapter())
        adapter = router.route("custom_task", override="custom")
    """

    # ── 任务 → 适配器名称映射 ──
    ROUTE_MAP: dict[str, str] = {
        "deep_reasoning": "hindsight",       # Hindsight / RetainDB
        "local_privacy": "holographic",      # 默认兜底（本地优先）
        "user_profile": "honcho",            # Honcho
        "fact_extraction": "mem0",           # Mem0
        "knowledge_nav": "openviking",       # OpenViking / Supermemory
    }

    def __init__(self, default_adapter: Optional[MemoryInterface] = None):
        """
        Args:
            default_adapter: 默认兜底适配器（默认为 HolographicAdapter）
        """
        self._adapters: dict[str, MemoryInterface] = {}
        self._default: MemoryInterface = default_adapter or HolographicAdapter()
        self._cache_layer = None

        # 敏感信息过滤配置（默认禁用，需显式开启）
        # _sensitive_filter_enabled: 启用敏感信息检测
        # _memory_boundary_enabled: 启用内存边界约束（检测到敏感信息时阻止写入）
        # _sensitive_filter: SensitiveDataFilter 实例（延迟初始化）
        self._sensitive_filter_enabled = False
        self._memory_boundary_enabled = False
        self._sensitive_filter = None
        self._memory_classification_enabled = False

        logger.info("[MemoryRouter] 初始化完成，默认适配器: %s", self._default.__class__.__name__)

    # ── 适配器管理 ──

    def register(self, name: str, adapter: MemoryInterface):
        """注册适配器

        Args:
            name: 适配器名称（与 ROUTE_MAP 中的值对应）
            adapter: 实现了 MemoryInterface 的适配器实例
        """
        if not isinstance(adapter, MemoryInterface):
            raise TypeError(f"适配器必须实现 MemoryInterface: {name}")
        self._adapters[name] = adapter
        logger.info("[MemoryRouter] 注册适配器: %s = %s", name, adapter.__class__.__name__)

    def unregister(self, name: str):
        """注销适配器"""
        if name in self._adapters:
            del self._adapters[name]
            logger.info("[MemoryRouter] 注销适配器: %s", name)

    def get_adapter(self, name: str) -> Optional[MemoryInterface]:
        """按名称获取适配器"""
        return self._adapters.get(name)

    def list_adapters(self) -> list[dict]:
        """列出所有已注册的适配器"""
        result = []
        for name, adapter in self._adapters.items():
            result.append({
                "name": name,
                "class": adapter.__class__.__name__,
                "capabilities": [c.value for c in adapter.capabilities],
                "details": adapter.to_dict() if hasattr(adapter, "to_dict") else {},
            })
        # 加入默认适配器
        result.append({
            "name": "__default__",
            "class": self._default.__class__.__name__,
            "capabilities": [c.value for c in self._default.capabilities],
            "details": self._default.to_dict() if hasattr(self._default, "to_dict") else {},
        })
        return result

    # ── 路由逻辑 ──

    def route(self, task_type: str = "local_privacy") -> MemoryInterface:
        """根据任务类型返回适配器

        路由策略：
        1. 在 ROUTE_MAP 中查找 task_type
        2. 如果对应的适配器已注册，返回它
        3. 如果对应的适配器未注册，返回默认适配器
        4. 如果 ROUTE_MAP 中没有该 task_type，返回默认适配器

        Args:
            task_type: 任务类型（参见 ROUTE_MAP）

        Returns:
            适配器实例（一定非 None）
        """
        adapter_name = self.ROUTE_MAP.get(task_type, "holographic")
        adapter = self._adapters.get(adapter_name)

        if adapter:
            logger.debug("[MemoryRouter] 路由 [%s] -> %s (%s)", task_type, adapter_name, adapter.__class__.__name__)
            return adapter

        # 兜底：返回默认适配器
        logger.debug("[MemoryRouter] 路由 [%s] -> %s 未注册，降级到默认适配器", task_type, adapter_name)
        return self._default

    @property
    def default(self) -> MemoryInterface:
        """获取默认适配器"""
        return self._default

    @default.setter
    def default(self, adapter: MemoryInterface):
        """替换默认适配器"""
        if not isinstance(adapter, MemoryInterface):
            raise TypeError("默认适配器必须实现 MemoryInterface")
        self._default = adapter
        logger.info("[MemoryRouter] 更新默认适配器: %s", adapter.__class__.__name__)

    # ── 缓存层集成 ──

    def attach_cache_layer(self, cache):
        """附加多级缓存层

        所有路由请求在返回前会经过缓存层。

        Args:
            cache: MultiLevelCache 实例
        """
        self._cache_layer = cache
        logger.info(log_dict({'module_name': 'router', 'action': 'log', 'msg': '[MemoryRouter] 缓存层已附加'}))

    def detach_cache_layer(self):
        """移除缓存层"""
        self._cache_layer = None
        logger.info(log_dict({'module_name': 'router', 'action': 'log', 'msg': '[MemoryRouter] 缓存层已移除'}))

    # ── 敏感信息过滤 ──

    def _filter_sensitive_info(self, content: Any) -> Tuple[bool, Any, List]:
        """检测并过滤敏感信息

        当 _sensitive_filter_enabled 为 False 时，直接返回原内容（不检测）。
        当启用时，使用 SensitiveDataFilter 检测内容中的敏感信息。

        Args:
            content: 待检测的内容（支持 str、dict、list 等类型）

        Returns:
            tuple: (has_sensitive, filtered_content, patterns)
                - has_sensitive: bool, 是否检测到敏感信息
                - filtered_content: Any, 过滤/脱敏后的内容
                - patterns: list, 匹配的敏感模式列表（SensitiveMatch 对象）
        """
        # 未启用过滤时，直接返回原内容
        if not self._sensitive_filter_enabled:
            return (False, content, [])

        # 支持自定义敏感模式（_sensitive_patterns 属性优先）
        custom_patterns = getattr(self, '_sensitive_patterns', None)
        if custom_patterns:
            import re
            content_str = str(content)
            has_sensitive = False
            filtered = content_str
            matched_patterns = []
            for pattern in custom_patterns:
                if re.search(pattern, content_str, re.IGNORECASE):
                    has_sensitive = True
                    matched_patterns.append(pattern)
                    filtered = re.sub(pattern, '[REDACTED]', filtered, flags=re.IGNORECASE)
            return (has_sensitive, filtered, matched_patterns)

        # 延迟初始化敏感过滤器
        if self._sensitive_filter is None:
            self._sensitive_filter = _get_sensitive_filter()

        # 使用 detect 方法检测敏感信息
        result = self._sensitive_filter.detect(content)
        has_sensitive = not result.allowed
        patterns = result.violations if result.violations else []

        if has_sensitive:
            # 检测到敏感信息，进行脱敏处理
            # 优先使用 sanitized_content，其次使用 mask 方法
            if result.sanitized_content is not None:
                filtered = result.sanitized_content
            else:
                try:
                    filtered = self._sensitive_filter.mask(str(content))
                except Exception:
                    filtered = str(content)

            # 测试期望 "[REDACTED]" 标记，将 ******** 替换为 [REDACTED]
            # SensitiveDataFilter 使用 "********" 作为默认脱敏值
            if isinstance(filtered, str) and "[REDACTED]" not in filtered:
                filtered = filtered.replace("********", "[REDACTED]")
            logger.debug(
                "[MemoryRouter] 检测到敏感信息，已脱敏: %d 个违规项",
                len(patterns),
            )
        else:
            # 无敏感信息，返回原内容
            filtered = content

        return (has_sensitive, filtered, patterns)

    def _classify_context(self, data: str) -> str:
        """分类上下文数据为长期或临时存储

        启用 _memory_classification_enabled 时，根据内容关键词判断存储类别。

        Args:
            data: 待分类的文本数据

        Returns:
            str: "long_term" 或 "temporary"
        """
        if not self._memory_classification_enabled:
            return "temporary"

        long_term_keywords = ["偏好", "设置", "配置", "喜欢", "习惯", "姓名", "年龄", "地址"]
        for keyword in long_term_keywords:
            if keyword in data:
                return "long_term"
        return "temporary"

    # ── 便捷方法（路由 + 执行） ──

    async def save(
        self,
        key: str,
        data: object,
        metadata: Optional[dict] = None,
        task_type: str = "local_privacy",
    ) -> bool:
        """路由到适配器执行 save

        当 _memory_boundary_enabled 和 _sensitive_filter_enabled 均启用时，
        会先检测数据中的敏感信息。如果检测到敏感信息，将阻止写入并返回 False。
        """
        # 边界约束检查：启用时检测敏感信息并阻止写入
        if self._memory_boundary_enabled and self._sensitive_filter_enabled:
            has_sensitive, _, _ = self._filter_sensitive_info(data)
            if has_sensitive:
                logger.warning(
                    "[MemoryRouter] 边界约束拦截敏感数据写入: key=%s, task_type=%s",
                    key,
                    task_type,
                )
                return False

        adapter = self.route(task_type)
        return await adapter.save(key, data, metadata)

    async def search(
        self,
        query: str,
        top_k: int = 5,
        task_type: str = "local_privacy",
    ) -> list[MemoryResult]:
        """路由到适配器执行 search（可经过缓存层）"""
        # 缓存层查找
        if self._cache_layer:
            cache_key = f"router:search:{task_type}:{query}:{top_k}"
            cached = self._cache_layer.get(cache_key)
            if cached is not None:
                return cached

        adapter = self.route(task_type)
        results = await adapter.search(query, top_k)

        # 缓存层写入
        if self._cache_layer:
            self._cache_layer.set(cache_key, results, ttl_seconds=30)

        return results

    async def get_profile(
        self,
        user_id: str,
        task_type: str = "user_profile",
    ) -> dict:
        """路由到适配器执行 get_profile"""
        adapter = self.route(task_type)
        return await adapter.get_profile(user_id)

    async def update_graph(
        self,
        entities: list,
        relations: list,
        task_type: str = "knowledge_nav",
    ) -> bool:
        """路由到适配器执行 update_graph"""
        adapter = self.route(task_type)
        return await adapter.update_graph(entities, relations)

    # ── 统计信息 ──

    def to_dict(self) -> dict:
        """获取路由器的完整状态"""
        return {
            "type": self.__class__.__name__,
            "adapters": self.list_adapters(),
            "route_map": dict(self.ROUTE_MAP),
            "cache_layer": self._cache_layer is not None,
            "boundary_enabled": self._memory_boundary_enabled,
            "sensitive_filter_enabled": self._sensitive_filter_enabled,
        }
