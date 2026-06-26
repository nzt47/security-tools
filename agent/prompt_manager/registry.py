#!/usr/bin/env python3
"""
Prompt 注册中心模块

提供提示词的注册、查询和管理功能
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List

from .storage import PromptStorage, PromptRecord, PromptType, get_prompt_storage

logger = logging.getLogger(__name__)


@dataclass
class PromptMetadata:
    """提示词元数据"""
    prompt_id: str
    name: str
    prompt_type: PromptType
    description: str = ""
    version: str = "1.0.0"
    author: str = ""
    tags: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=lambda: __import__('time').time())
    updated_at: float = field(default_factory=lambda: __import__('time').time())


class PromptRegistry:
    """提示词注册中心"""
    
    def __init__(self, storage: PromptStorage = None):
        self.storage = storage or get_prompt_storage()
    
    def register_prompt(self, prompt_id: str, name: str, content: str, 
                       prompt_type: PromptType = PromptType.SYSTEM,
                       description: str = "", author: str = "",
                       tags: List[str] = None, metadata: Dict[str, Any] = None) -> PromptRecord:
        """注册新提示词"""
        # 检查是否已存在
        existing = self.storage.get_prompt(prompt_id)
        if existing:
            raise ValueError(f"提示词已存在: {prompt_id}")
        
        record = PromptRecord(
            prompt_id=prompt_id,
            name=name,
            content=content,
            prompt_type=prompt_type,
            metadata=metadata or {},
            tags=tags or [],
            created_at=_now(),
            updated_at=_now()
        )
        
        self.storage.save_prompt(record)
        
        logger.info(json.dumps({
            "trace_id": "",
            "module_name": "prompt_manager",
            "action": "register_prompt",
            "prompt_id": prompt_id,
            "prompt_type": prompt_type.value,
            "duration_ms": 0,
            "level": "INFO"
        }))
        
        return record
    
    def update_prompt(self, prompt_id: str, **kwargs) -> PromptRecord:
        """更新提示词"""
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
        
        prompt.updated_at = _now()
        self.storage.save_prompt(prompt)
        
        logger.info(json.dumps({
            "trace_id": "",
            "module_name": "prompt_manager",
            "action": "update_prompt",
            "prompt_id": prompt_id,
            "duration_ms": 0,
            "level": "INFO"
        }))
        
        return prompt
    
    def get_prompt(self, prompt_id: str) -> Optional[PromptRecord]:
        """获取提示词"""
        return self.storage.get_prompt(prompt_id)
    
    def list_prompts(self, prompt_type: PromptType = None, 
                     tags: List[str] = None, limit: int = 100, offset: int = 0) -> List[PromptRecord]:
        """列出提示词"""
        prompts = self.storage.list_prompts(prompt_type, limit, offset)
        
        # 如果指定了标签，进行过滤
        if tags:
            prompts = [p for p in prompts if any(t in p.tags for t in tags)]
        
        return prompts
    
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
        """获取提示词元数据"""
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
_global_prompt_registry = None

def get_prompt_registry() -> PromptRegistry:
    """获取全局提示词注册中心实例"""
    global _global_prompt_registry
    if _global_prompt_registry is None:
        _global_prompt_registry = PromptRegistry()
    return _global_prompt_registry


