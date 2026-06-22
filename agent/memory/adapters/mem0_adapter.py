"""Mem0Adapter — 语义事实提取与去重适配器

功能：
- 语义事实提取：从自然语言中提取结构化事实
- 智能去重：基于语义相似度自动合并重复事实
- 可选的 Mem0 后端（云端/本地）
- 实现 MemoryInterface 全部方法

设计思想：
- Mem0 作为「事实层」—— 从对话中提取不可变的事实
- 与 HolographicAdapter（全文搜索）互补
- 通过 MemoryRouter 在需事实提取的场景自动调用

适配策略：
1. 如果安装了 mem0（pip install mem0），使用 Mem0 引擎
2. 否则使用内置的轻量关键词提取 + 去重逻辑降级运行
"""

import json
import time
import logging
import hashlib
from typing import Any, Optional

from agent.memory.base import (
    MemoryInterface,
    MemoryResult,
    MemoryCapability,
)

logger = logging.getLogger(__name__)


class Mem0Adapter(MemoryInterface):
    """语义事实提取与去重适配器

    用法:
        adapter = Mem0Adapter(mem0_config={"api_key": "..."})
        await adapter.save("fact_001", "用户喜欢喝咖啡", {"category": "preference"})
        results = await adapter.search("偏好")
    """

    def __init__(
        self,
        mem0_config: Optional[dict] = None,
        storage_path: str = "./data/memory/mem0_facts.json",
    ):
        """
        Args:
            mem0_config: Mem0 引擎配置（api_key 等），None 则使用内置降级
            storage_path: 内置降级模式的存储路径
        """
        self.mem0_config = mem0_config or {}
        self.storage_path = storage_path

        # Mem0 引擎（可选）
        self._mem0_client = None
        self._has_mem0 = False
        self._init_mem0()

        # 内置降级存储
        self._facts: dict[str, dict] = {}
        self._load_facts()

        logger.info(
            "[Mem0Adapter] 初始化完成: engine=%s",
            "mem0" if self._has_mem0 else "builtin"
        )

    # ── 能力声明 ──

    @property
    def capabilities(self) -> set[MemoryCapability]:
        caps = {MemoryCapability.FACT_EXTRACTION}
        if self._has_mem0:
            caps.add(MemoryCapability.SEMANTIC_SEARCH)
        return caps

    # ── 初始化 ──

    def _init_mem0(self):
        """尝试初始化 Mem0 引擎"""
        try:
            import mem0
            self._mem0_client = mem0.Memory(**self.mem0_config)
            self._has_mem0 = True
            logger.info("[Mem0Adapter] Mem0 引擎就绪")
        except ImportError:
            logger.info("[Mem0Adapter] mem0 未安装，使用内置降级模式")
        except Exception as e:
            logger.warning("[Mem0Adapter] Mem0 初始化失败: %s，使用内置降级模式", e)

    def _load_facts(self):
        """从 JSON 文件加载持久化事实"""
        try:
            import os
            os.makedirs(os.path.dirname(self.storage_path), exist_ok=True)
            if os.path.exists(self.storage_path):
                with open(self.storage_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._facts = data if isinstance(data, dict) else {}
                logger.debug("[Mem0Adapter] 加载 %d 条持久化事实", len(self._facts))
        except Exception as e:
            logger.warning("[Mem0Adapter] 加载持久化事实失败: %s", e)
            self._facts = {}

    def _save_facts(self):
        """保存事实到 JSON 文件"""
        try:
            import os
            os.makedirs(os.path.dirname(self.storage_path), exist_ok=True)
            with open(self.storage_path, "w", encoding="utf-8") as f:
                json.dump(self._facts, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning("[Mem0Adapter] 保存事实失败: %s", e)

    def _normalize_key(self, key: str) -> str:
        """规范化 key 用于去重"""
        return hashlib.md5(key.strip().lower().encode()).hexdigest()

    def _should_deduplicate(self, new_content: str, existing_entry: dict) -> bool:
        """判断是否需要去重（基于简单内容相似度）

        如果新内容与已有事实高度相似，跳过插入。
        """
        old_content = existing_entry.get("data", "").strip().lower()
        new_clean = new_content.strip().lower()
        return new_clean == old_content or new_clean.startswith(old_content) or old_content.startswith(new_clean)

    # ── MemoryInterface 实现 ──

    async def save(
        self,
        key: str,
        data: Any,
        metadata: Optional[dict] = None,
    ) -> bool:
        """保存事实（带去重）"""
        if not key:
            logger.warning("[Mem0Adapter] save 失败: key 为空")
            return False

        data_str = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
        norm_key = self._normalize_key(key)

        # 如果使用 Mem0 引擎
        if self._has_mem0 and self._mem0_client:
            try:
                self._mem0_client.add(data_str, metadata=metadata or {})
                logger.debug("[Mem0Adapter] Mem0 引擎保存成功: key=%s", key)
                return True
            except Exception as e:
                logger.error("[Mem0Adapter] Mem0 引擎保存失败: %s", e)
                return False

        # 内置降级：去重检查
        if norm_key in self._facts:
            existing = self._facts[norm_key]
            if self._should_deduplicate(data_str, existing):
                logger.debug("[Mem0Adapter] 去重跳过: key=%s (内容重复)", key)
                # 更新访问时间
                existing["updated_at"] = time.time()
                existing["hit_count"] = existing.get("hit_count", 0) + 1
                self._save_facts()
                return True

        # 保存新事实
        entry = {
            "key": key,
            "data": data_str,
            "metadata": metadata or {},
            "created_at": time.time(),
            "updated_at": time.time(),
            "hit_count": 1,
        }
        self._facts[norm_key] = entry
        self._save_facts()
        logger.debug("[Mem0Adapter] 内置保存成功: key=%s", key)
        return True

    async def search(
        self,
        query: str,
        top_k: int = 5,
    ) -> list[MemoryResult]:
        """搜索已存储的事实"""
        if not query:
            return []

        # 如果使用 Mem0 引擎
        if self._has_mem0 and self._mem0_client:
            try:
                mem0_results = self._mem0_client.search(query, top_k=top_k)
                return [
                    MemoryResult(
                        content=getattr(r, "content", r.get("content", "")),
                        confidence=getattr(r, "score", r.get("score", 0.8)),
                        source="mem0",
                        metadata=getattr(r, "metadata", r.get("metadata", {})),
                    )
                    for r in mem0_results
                ]
            except Exception as e:
                logger.error("[Mem0Adapter] Mem0 搜索失败: %s，降级到内置搜索", e)

        # 内置降级：关键词匹配
        query_lower = query.strip().lower()
        results = []

        for entry in self._facts.values():
            data_lower = entry["data"].lower()
            key_lower = entry["key"].lower()

            # 简单分数：完全匹配 > 关键词包含 > 部分匹配
            score = 0.0
            if query_lower == data_lower or query_lower == key_lower:
                score = 0.95
            elif query_lower in data_lower or query_lower in key_lower:
                score = 0.7
            elif any(word in data_lower for word in query_lower.split()):
                score = 0.4

            if score > 0:
                content = entry["data"]
                try:
                    parsed = json.loads(content)
                    if isinstance(parsed, (dict, list)):
                        content = parsed
                except (json.JSONDecodeError, TypeError):
                    pass

                results.append(MemoryResult(
                    content=content,
                    confidence=score,
                    source="mem0",
                    metadata={
                        "key": entry["key"],
                        "created_at": entry.get("created_at"),
                        **entry.get("metadata", {}),
                    },
                ))

        # 按置信度降序，取 top_k
        results.sort(key=lambda r: r.confidence, reverse=True)
        return results[:top_k]

    async def get_profile(self, user_id: str) -> dict:
        """从 Facts 中提取用户画像

        搜索包含 user_id 或相关标签的事实，聚合为画像。
        """
        if not user_id:
            return {}

        facts_about_user = []

        for entry in self._facts.values():
            meta = entry.get("metadata", {})
            entry_user = meta.get("user_id", "") if isinstance(meta, dict) else ""
            key_lower = entry["key"].lower()
            data_lower = entry["data"].lower()

            if (
                str(user_id) in key_lower
                or str(user_id) in data_lower
                or (entry_user and str(entry_user) == str(user_id))
            ):
                facts_about_user.append(entry)

        if not facts_about_user:
            return {}

        # 按类别分组
        categories: dict[str, list[str]] = {}
        for entry in facts_about_user:
            meta = entry.get("metadata", {}) or {}
            cat = meta.get("category", "general") if isinstance(meta, dict) else "general"
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(entry["data"])

        return {
            "user_id": user_id,
            "source": "mem0",
            "fact_count": len(facts_about_user),
            "categories": categories,
        }

    async def update_graph(
        self,
        entities: list,
        relations: list,
    ) -> bool:
        """保存图谱事实"""
        if not entities and not relations:
            return True

        # 对每个实体保存一条事实
        success = True
        for entity in entities:
            name = entity.get("name") if isinstance(entity, dict) else str(entity)
            if name:
                ok = await self.save(
                    key=f"entity:{name}",
                    data=f"实体 {name}: {json.dumps(entity, ensure_ascii=False)}",
                    metadata={"type": "entity", "source": "update_graph"},
                )
                success = success and ok

        for relation in relations:
            if isinstance(relation, dict):
                source = relation.get("source", "")
                target = relation.get("target", "")
                rel_type = relation.get("type", "related_to")
                if source and target:
                    key = f"relation:{rel_type}:{source}:{target}"
                    ok = await self.save(
                        key=key,
                        data=f"{source} -[{rel_type}]-> {target}",
                        metadata={"type": "relation", "source": source, "target": target},
                    )
                    success = success and ok

        return success

    # ── 辅助方法 ──

    def get_stats(self) -> dict:
        """获取适配器统计信息"""
        return {
            "name": "Mem0Adapter",
            "source": "mem0",
            "engine": "mem0" if self._has_mem0 else "builtin",
            "total_facts": len(self._facts),
            "storage_path": self.storage_path,
            "capabilities": [c.value for c in self.capabilities],
        }

    def get_raw_facts(self) -> dict:
        """获取所有原始事实（调试用）"""
        return dict(self._facts)
