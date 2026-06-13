
"""
云枢 MemoryTree - 三层记忆树架构
参考 OpenHuman 的 Memory Tree 设计
"""

import os
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


class MemoryNode:
    """记忆节点 - 记忆树的基本单元"""

    def __init__(
        self,
        node_id: str,
        content: str,
        node_type: str = "leaf",
        metadata: Optional[Dict] = None,
        created_at: Optional[str] = None,
    ):
        self.node_id = node_id
        self.content = content
        self.node_type = node_type  # "leaf" | "branch" | "root"
        self.metadata = metadata or {}
        self.created_at = created_at or datetime.now().isoformat()
        self.children: List[str] = []  # 子节点 ID
        self.parent: Optional[str] = None
        self.tags: List[str] = []
        self.importance: float = 0.5  # 重要性评分 0-1
        self.access_count: int = 0
        self.last_access: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            "node_id": self.node_id,
            "content": self.content,
            "node_type": self.node_type,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "children": self.children,
            "parent": self.parent,
            "tags": self.tags,
            "importance": self.importance,
            "access_count": self.access_count,
            "last_access": self.last_access,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "MemoryNode":
        node = cls(
            node_id=data["node_id"],
            content=data["content"],
            node_type=data["node_type"],
            metadata=data.get("metadata"),
            created_at=data.get("created_at"),
        )
        node.children = data.get("children", [])
        node.parent = data.get("parent")
        node.tags = data.get("tags", [])
        node.importance = data.get("importance", 0.5)
        node.access_count = data.get("access_count", 0)
        node.last_access = data.get("last_access")
        return node


class MemoryTree:
    """记忆树基类"""

    def __init__(self, tree_name: str, data_dir: str):
        self.tree_name = tree_name
        self.data_dir = Path(data_dir) / tree_name
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.nodes: Dict[str, MemoryNode] = {}
        self.root_id: Optional[str] = None
        self._load_tree()

    def _get_node_path(self, node_id: str) -> Path:
        return self.data_dir / f"{node_id}.json"

    def _load_tree(self):
        """从磁盘加载记忆树"""
        try:
            index_path = self.data_dir / "index.json"
            if index_path.exists():
                with open(index_path, "r", encoding="utf-8") as f:
                    index = json.load(f)
                    self.root_id = index.get("root_id")
                    for node_id in index.get("nodes", []):
                        node_path = self._get_node_path(node_id)
                        if node_path.exists():
                            with open(node_path, "r", encoding="utf-8") as nf:
                                node_data = json.load(nf)
                                self.nodes[node_id] = MemoryNode.from_dict(node_data)
                logger.info(f"已加载记忆树 {self.tree_name}，共 {len(self.nodes)} 个节点")
        except Exception as e:
            logger.error(f"加载记忆树失败: {e}")

    def _save_tree(self):
        """保存记忆树到磁盘"""
        try:
            index = {
                "root_id": self.root_id,
                "nodes": list(self.nodes.keys()),
                "updated_at": datetime.now().isoformat(),
            }
            with open(self.data_dir / "index.json", "w", encoding="utf-8") as f:
                json.dump(index, f, ensure_ascii=False, indent=2)

            for node_id, node in self.nodes.items():
                with open(self._get_node_path(node_id), "w", encoding="utf-8") as f:
                    json.dump(node.to_dict(), f, ensure_ascii=False, indent=2)

            logger.debug(f"记忆树 {self.tree_name} 已保存")
        except Exception as e:
            logger.error(f"保存记忆树失败: {e}")

    def add_node(
        self,
        content: str,
        parent_id: Optional[str] = None,
        node_type: str = "leaf",
        metadata: Optional[Dict] = None,
        tags: Optional[List[str]] = None,
    ) -> MemoryNode:
        """添加新节点"""
        node_id = f"{self.tree_name}_{len(self.nodes)}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        node = MemoryNode(
            node_id=node_id,
            content=content,
            node_type=node_type,
            metadata=metadata,
        )
        node.tags = tags or []

        if parent_id and parent_id in self.nodes:
            node.parent = parent_id
            self.nodes[parent_id].children.append(node_id)
        elif not self.root_id:
            self.root_id = node_id

        self.nodes[node_id] = node
        self._save_tree()
        logger.debug(f"添加记忆节点: {node_id}")
        return node

    def get_node(self, node_id: str) -> Optional[MemoryNode]:
        """获取节点"""
        if node_id in self.nodes:
            node = self.nodes[node_id]
            node.access_count += 1
            node.last_access = datetime.now().isoformat()
            self._save_tree()
            return node
        return None

    def search_by_tag(self, tag: str) -> List[MemoryNode]:
        """按标签搜索"""
        return [node for node in self.nodes.values() if tag in node.tags]

    def search_by_content(self, keyword: str) -> List[MemoryNode]:
        """按内容搜索"""
        keyword = keyword.lower()
        return [
            node
            for node in self.nodes.values()
            if keyword in node.content.lower()
        ]

    def get_recent_nodes(self, limit: int = 10) -> List[MemoryNode]:
        """获取最近的节点"""
        sorted_nodes = sorted(
            self.nodes.values(),
            key=lambda n: n.created_at,
            reverse=True
        )
        return sorted_nodes[:limit]


class SourceTree(MemoryTree):
    """来源树 - 原始数据按来源分类存储"""

    def __init__(self, data_dir: str):
        super().__init__("sources", data_dir)

    def record_chat(self, role: str, content: str, metadata: Optional[Dict] = None):
        """记录对话"""
        return self.add_node(
            content=content,
            node_type="leaf",
            metadata={
                "source": "chat",
                "role": role,
                **(metadata or {}),
            },
            tags=["chat", role],
        )

    def record_sensor(self, sensor_type: str, data: Dict, metadata: Optional[Dict] = None):
        """记录传感器数据"""
        return self.add_node(
            content=json.dumps(data, ensure_ascii=False),
            node_type="leaf",
            metadata={
                "source": "sensor",
                "sensor_type": sensor_type,
                **(metadata or {}),
            },
            tags=["sensor", sensor_type],
        )

    def record_window(self, window_title: str, event_type: str, metadata: Optional[Dict] = None):
        """记录窗口活动"""
        return self.add_node(
            content=f"窗口活动: {window_title} - {event_type}",
            node_type="leaf",
            metadata={
                "source": "window",
                "window_title": window_title,
                "event_type": event_type,
                **(metadata or {}),
            },
            tags=["window", event_type],
        )

    def record_file(self, file_path: str, event_type: str, metadata: Optional[Dict] = None):
        """记录文件变更"""
        return self.add_node(
            content=f"文件变更: {file_path} - {event_type}",
            node_type="leaf",
            metadata={
                "source": "file",
                "file_path": file_path,
                "event_type": event_type,
                **(metadata or {}),
            },
            tags=["file", event_type],
        )


class TopicTree(MemoryTree):
    """主题树 - 按主题聚类的记忆"""

    def __init__(self, data_dir: str):
        super().__init__("topics", data_dir)
        self.topics: Dict[str, List[str]] = {}  # topic -> node_ids

    def add_to_topic(self, topic: str, content: str, tags: Optional[List[str]] = None):
        """添加到主题"""
        if topic not in self.topics:
            # 创建主题分支
            topic_node = self.add_node(
                content=f"主题: {topic}",
                node_type="branch",
                tags=["topic", topic],
            )
            self.topics[topic] = []

        all_tags = ["topic", topic] + (tags or [])
        content_node = self.add_node(
            content=content,
            parent_id=self.root_id,  # 简化版，实际可挂在主题分支下
            node_type="leaf",
            tags=all_tags,
        )
        self.topics[topic].append(content_node.node_id)
        return content_node

    def get_topic_content(self, topic: str) -> List[MemoryNode]:
        """获取主题内容"""
        if topic in self.topics:
            return [self.get_node(nid) for nid in self.topics[topic] if self.get_node(nid)]
        return []


class GlobalTree(MemoryTree):
    """全局树 - 长期摘要和核心人格"""

    def __init__(self, data_dir: str):
        super().__init__("global", data_dir)
        self.persona_path = Path(data_dir) / "global" / "persona.json"
        self.summary_path = Path(data_dir) / "global" / "summary.md"

    def load_persona(self) -> Optional[Dict]:
        """加载人格数据"""
        if self.persona_path.exists():
            with open(self.persona_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return None

    def save_persona(self, persona: Dict):
        """保存人格数据"""
        self.persona_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.persona_path, "w", encoding="utf-8") as f:
            json.dump(persona, f, ensure_ascii=False, indent=2)

    def load_summary(self) -> Optional[str]:
        """加载摘要"""
        if self.summary_path.exists():
            with open(self.summary_path, "r", encoding="utf-8") as f:
                return f.read()
        return None

    def save_summary(self, summary: str):
        """保存摘要"""
        self.summary_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.summary_path, "w", encoding="utf-8") as f:
            f.write(summary)

