#!/usr/bin/env python3
"""
Prompt 注册中心模块

提供提示词的注册、查询和管理功能（含"拥有者"维度）。

【两个正交维度，不要混为一谈】
    - prompt_type（agent.prompt_manager.storage.PromptType）：
      "这段提示词**是什么**"（system / user / tool / skill / template / chat）。
    - owner（agent.prompt_manager.roles.PROMPT_ROLES）：
      "这段提示词**归谁管、谁有权改**"（system / persona / line / skill / tool /
      memory / task）。
    一条记录可以同时是 prompt_type=system + owner=line（例如主线片段模板）：
    前者决定渲染方式，后者决定改动的责任人。

    拥有者落在 metadata["owner"] 里（零 schema 迁移，见 storage.OWNER_METADATA_KEY）；
    未声明拥有者的历史记录 owner 为空串，不会被默认归给任何角色。
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List

from .storage import (
    OWNER_METADATA_KEY,
    PromptStorage,
    PromptRecord,
    PromptType,
    get_prompt_storage,
    record_owner,
)
from .roles import PROMPT_ROLES
from agent.logging_utils import log_dict

logger = logging.getLogger(__name__)


@dataclass
class PromptMetadata:
    """提示词元数据

    Attributes:
        owner: 拥有者角色（metadata["owner"]）；空串 = 未声明。
            词表见 agent/prompt_manager/roles.py::PROMPT_ROLES。
    """
    prompt_id: str
    name: str
    prompt_type: PromptType
    description: str = ""
    version: str = "1.0.0"
    author: str = ""
    tags: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    owner: str = ""
    created_at: float = field(default_factory=lambda: __import__('time').time())
    updated_at: float = field(default_factory=lambda: __import__('time').time())


class PromptRegistry:
    """提示词注册中心（含拥有者维度）"""

    def __init__(self, storage: Optional[PromptStorage] = None):
        """
        Args:
            storage: 存储实现；None ⇒ 用全局单例（get_prompt_storage）。
        """
        self.storage = storage or get_prompt_storage()
    
    def register_prompt(self, prompt_id: str, name: str, content: str, 
                       prompt_type: PromptType = PromptType.SYSTEM,
                       description: str = "", author: str = "",
                       tags: Optional[List[str]] = None,
                       metadata: Optional[Dict[str, Any]] = None,
                       owner: str = "") -> PromptRecord:
        """注册新提示词

        Args:
            prompt_id / name / content: 标识与正文。
            prompt_type: 内容类型（"是什么"）。
            description / author: 描述与署名。
            tags: 标签。
            metadata: 附加元数据；owner 形参会合并进去并优先。
            owner: **拥有者角色**（"归谁管"）。词表见
                agent/prompt_manager/roles.py::PROMPT_ROLES。
                未在词表内的取值只**告警不拒绝**（数据不得因词表更迭而丢失）。

        Returns:
            PromptRecord

        Raises:
            ValueError: prompt_id 已存在。
        """
        # 检查是否已存在
        existing = self.storage.get_prompt(prompt_id)
        if existing:
            raise ValueError(f"提示词已存在: {prompt_id}")

        merged_metadata = dict(metadata or {})
        if owner:
            if owner not in PROMPT_ROLES:
                logger.warning(log_dict({
                    'module_name': 'prompt_manager',
                    'action': 'register_prompt.unknown_owner',
                    'prompt_id': prompt_id,
                    'owner': owner,
                    'known_roles': list(PROMPT_ROLES),
                    'message': 'owner 不在角色词表内，仍按原文存储（词表可在 roles.py 扩展）',
                    'level': 'WARNING',
                }))
            merged_metadata[OWNER_METADATA_KEY] = owner

        record = PromptRecord(
            prompt_id=prompt_id,
            name=name,
            content=content,
            prompt_type=prompt_type,
            metadata=merged_metadata,
            tags=tags or [],
            created_at=_now(),
            updated_at=_now()
        )
        
        self.storage.save_prompt(record)
        
        logger.info(log_dict({'module_name': 'prompt_manager', 'action': 'register_prompt', 'prompt_id': prompt_id, 'prompt_type': prompt_type.value, 'level': 'INFO'}))
        
        return record
    
    def update_prompt(self, prompt_id: str, **kwargs) -> PromptRecord:
        """更新提示词

        支持的关键字：name / content / prompt_type / description / tags /
        metadata（浅合并）/ **owner**（写 metadata["owner"]；传空串 = 撤销拥有者声明）。

        Raises:
            ValueError: prompt_id 不存在。
        """
        prompt = self.storage.get_prompt(prompt_id)
        if not prompt:
            raise ValueError(f"提示词不存在: {prompt_id}")
        
        if 'name' in kwargs:
            prompt.name = kwargs['name']
        if 'content' in kwargs:
            prompt.content = kwargs['content']
        if 'prompt_type' in kwargs:
            prompt.prompt_type = kwargs['prompt_type']
        if 'description' in kwargs:
            if 'description' in prompt.metadata:
                prompt.metadata['description'] = kwargs['description']
            else:
                prompt.metadata = {**prompt.metadata, 'description': kwargs['description']}
        if 'tags' in kwargs:
            prompt.tags = kwargs['tags']
        if 'metadata' in kwargs:
            prompt.metadata = {**prompt.metadata, **kwargs['metadata']}
        if 'owner' in kwargs:
            # owner 是"谁有权改"的声明，改它就是换责任人 —— 显式支持，避免只能
            # 通过 metadata 浅合并"顺带"改掉（那样调用点看不出语义）
            owner = str(kwargs['owner'] or '').strip()
            prompt.metadata = {**prompt.metadata, OWNER_METADATA_KEY: owner}
        
        prompt.updated_at = _now()
        self.storage.save_prompt(prompt)
        
        logger.info(log_dict({'module_name': 'prompt_manager', 'action': 'update_prompt', 'prompt_id': prompt_id, 'level': 'INFO'}))
        
        return prompt
    
    def get_prompt(self, prompt_id: str) -> Optional[PromptRecord]:
        """获取提示词"""
        return self.storage.get_prompt(prompt_id)
    
    def list_prompts(self, prompt_type: Optional[PromptType] = None,
                     tags: Optional[List[str]] = None, limit: int = 100, offset: int = 0,
                     owner: Optional[str] = None) -> List[PromptRecord]:
        """列出提示词（可按内容类型 / 标签 / 拥有者过滤）

        Args:
            prompt_type: 内容类型过滤（None = 全部）。
            tags: 标签过滤（命中任一即算，None/[] = 不过滤）。
            limit / offset: 分页窗口。
            owner: **拥有者角色**过滤（None = 不过滤；空串 = 未声明拥有者的记录）。

        Returns:
            List[PromptRecord]

        注意：owner 与 tags 都在**存储返回的窗口内**做 Python 过滤
        （metadata/tags 在 SQLite 里是 JSON/文本袋，没有索引）。提示词是小基数
        数据（本仓 < 100 条），实践上等于全量过滤；条数涨上去时请调大 limit。
        """
        prompts = self.storage.list_prompts(prompt_type, limit, offset, owner=owner)
        
        # 如果指定了标签，进行过滤
        if tags:
            prompts = [p for p in prompts if any(t in p.tags for t in tags)]
        
        return prompts

    def list_prompts_by_owner(self, owner: Optional[str], prompt_type: Optional[PromptType] = None,
                              tags: Optional[List[str]] = None, limit: int = 100,
                              offset: int = 0) -> List[PromptRecord]:
        """按拥有者列出提示词（形状与 list_prompts 一致的便捷入口）

        Args:
            owner: 拥有者角色（agent/prompt_manager/roles.py::PROMPT_ROLES）；
                空串（或 None） = 只列"未声明拥有者"的记录。
            其余形参语义与 :meth:`list_prompts` 完全相同。

        Returns:
            List[PromptRecord]
        """
        return self.list_prompts(prompt_type=prompt_type, tags=tags,
                                 limit=limit, offset=offset, owner=owner or "")
    
    def search_prompts(self, query: str) -> List[PromptRecord]:
        """搜索提示词"""
        prompts = self.storage.list_prompts()
        query_lower = query.lower()
        
        results = []
        for p in prompts:
            if (query_lower in p.name.lower() or 
                query_lower in p.prompt_id.lower() or 
                query_lower in p.content.lower() or
                any(query_lower in t.lower() for t in p.tags)):
                results.append(p)
        
        return results
    
    def delete_prompt(self, prompt_id: str) -> bool:
        """删除提示词"""
        return self.storage.delete_prompt(prompt_id)
    
    def get_prompt_metadata(self, prompt_id: str) -> Optional[PromptMetadata]:
        """获取提示词元数据（含拥有者 owner）

        Returns:
            PromptMetadata；记录不存在 ⇒ None。
        """
        prompt = self.storage.get_prompt(prompt_id)
        if not prompt:
            return None
        
        return PromptMetadata(
            prompt_id=prompt.prompt_id,
            name=prompt.name,
            prompt_type=prompt.prompt_type,
            description=prompt.metadata.get('description', ''),
            author=prompt.metadata.get('author', ''),
            tags=prompt.tags,
            owner=record_owner(prompt),
            created_at=prompt.created_at,
            updated_at=prompt.updated_at
        )
    
    def validate_prompt(self, prompt_id: str) -> Dict[str, Any]:
        """验证提示词"""
        prompt = self.storage.get_prompt(prompt_id)
        if not prompt:
            return {"valid": False, "errors": ["提示词不存在"]}
        
        errors = []
        warnings = []
        
        # 验证内容
        if not prompt.content.strip():
            errors.append("提示词内容为空")
        
        # 检查内容长度
        if len(prompt.content) > 100000:
            warnings.append("提示词内容过长（超过100KB）")
        
        # 检查特殊字符
        if '\x00' in prompt.content:
            errors.append("提示词包含空字符")
        
        # 检查JSON格式（如果是JSON类型提示词）
        if prompt.prompt_type == PromptType.TEMPLATE:
            try:
                import json
                json.loads(prompt.content)
            except json.JSONDecodeError:
                warnings.append("提示词内容不是有效的JSON格式")
        
        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "prompt_id": prompt_id
        }


def _now():
    """获取当前时间戳"""
    import time
    return time.time()


# 全局注册中心实例
_global_prompt_registry = None  # 保留作为 fallback

try:
    from agent.utils.singleton_manager import register_singleton, get_singleton
    _SINGLETON_AVAILABLE = True
except ImportError:
    _SINGLETON_AVAILABLE = False
    register_singleton = None
    get_singleton = None


def _create_prompt_registry(config=None):
    """PromptRegistry 工厂函数（供 SingletonManager 使用）"""
    return PromptRegistry()


def get_prompt_registry() -> PromptRegistry:
    """获取全局提示词注册中心实例"""
    if _SINGLETON_AVAILABLE:
        return get_singleton("prompt_registry")
    global _global_prompt_registry
    if _global_prompt_registry is None:
        _global_prompt_registry = _create_prompt_registry()
    return _global_prompt_registry


if _SINGLETON_AVAILABLE:
    register_singleton("prompt_registry", _create_prompt_registry)


