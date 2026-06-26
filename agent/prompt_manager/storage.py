#!/usr/bin/env python3
"""
Prompt 存储模块

提供持久化存储功能，支持版本管理和历史记录
"""

import os
import json
import time
import sqlite3
import logging
import threading
from datetime import datetime
from enum import Enum
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)


class PromptType(Enum):
    """提示词类型"""
    SYSTEM = "system"           # 系统提示词
    USER = "user"               # 用户提示词
    TOOL = "tool"               # 工具提示词
    SKILL = "skill"             # 技能提示词
    TEMPLATE = "template"       # 模板提示词
    CHAT = "chat"               # 对话提示词


@dataclass
class PromptRecord:
    """提示词记录"""
    prompt_id: str
    name: str
    content: str
    prompt_type: PromptType
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    tags: List[str] = field(default_factory=list)
    
    def to_dict(self) -> dict:
        d = asdict(self)
        d['prompt_type'] = self.prompt_type.value
        d['created_at_iso'] = datetime.fromtimestamp(self.created_at).isoformat()
        d['updated_at_iso'] = datetime.fromtimestamp(self.updated_at).isoformat()
        return d


@dataclass
class VersionRecord:
    """版本记录"""
    version_id: str
    prompt_id: str
    version_number: str
    content: str
    change_log: str = ""
    author: str = ""
    status: str = "draft"  # draft, testing, approved, deprecated
    created_at: float = field(default_factory=time.time)
    tested_at: Optional[float] = None
    approved_at: Optional[float] = None
    deprecated_at: Optional[float] = None
    test_results: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        d = asdict(self)
        d['created_at_iso'] = datetime.fromtimestamp(self.created_at).isoformat()
        if self.tested_at:
            d['tested_at_iso'] = datetime.fromtimestamp(self.tested_at).isoformat()
        if self.approved_at:
            d['approved_at_iso'] = datetime.fromtimestamp(self.approved_at).isoformat()
        if self.deprecated_at:
            d['deprecated_at_iso'] = datetime.fromtimestamp(self.deprecated_at).isoformat()
        return d


class PromptStorage:
    """提示词存储管理器"""
    
    def __init__(self, storage_path: str = None):
        self.storage_path = storage_path or os.path.join(
            os.path.dirname(__file__), '..', '..', 'data', 'prompts'
        )
        os.makedirs(self.storage_path, exist_ok=True)
        self._db_path = os.path.join(self.storage_path, 'prompts.db')
        self._local = threading.local()
        self._write_lock = threading.Lock()
        self._initialized = False
        
        logger.info(f"[PromptManager] 初始化存储，路径: {self.storage_path}")
    
    def _get_conn(self) -> sqlite3.Connection:
        """获取数据库连接"""
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
            self._local.conn = sqlite3.connect(
                self._db_path, check_same_thread=False
            )
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn
    
    def initialize(self):
        """初始化数据库表"""
        if self._initialized:
            return
        
        with self._write_lock, self._get_conn() as conn:
            cursor = conn.cursor()
            
            # 提示词表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS prompts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    prompt_id TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    content TEXT NOT NULL,
                    prompt_type TEXT NOT NULL,
                    metadata TEXT DEFAULT '{}',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    tags TEXT DEFAULT '[]',
                    created_at_iso TEXT DEFAULT (datetime('now'))
                )
            """)
            
            # 版本表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    version_id TEXT NOT NULL UNIQUE,
                    prompt_id TEXT NOT NULL,
                    version_number TEXT NOT NULL,
                    content TEXT NOT NULL,
                    change_log TEXT DEFAULT '',
                    author TEXT DEFAULT '',
                    status TEXT DEFAULT 'draft',
                    created_at REAL NOT NULL,
                    tested_at REAL,
                    approved_at REAL,
                    deprecated_at REAL,
                    test_results TEXT DEFAULT '{}',
                    created_at_iso TEXT DEFAULT (datetime('now'))
                )
            """)
            
            # 索引
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_prompts_id ON prompts(prompt_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_prompts_type ON prompts(prompt_type)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_versions_prompt_id ON versions(prompt_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_versions_status ON versions(status)")
            
            conn.commit()
        
        self._initialized = True
        logger.info("[PromptManager] 数据库初始化完成")
    
    def save_prompt(self, record: PromptRecord):
        """保存提示词"""
        self.initialize()
        
        metadata_json = json.dumps(record.metadata, ensure_ascii=False)
        tags_json = json.dumps(record.tags, ensure_ascii=False)
        
        with self._write_lock, self._get_conn() as conn:
            cursor = conn.cursor()
            
            cursor.execute("SELECT * FROM prompts WHERE prompt_id = ?", (record.prompt_id,))
            exists = cursor.fetchone()
            
            if exists:
                cursor.execute(
                    """UPDATE prompts
                       SET name = ?, content = ?, prompt_type = ?, metadata = ?,
                           updated_at = ?, tags = ?
                       WHERE prompt_id = ?""",
                    (record.name, record.content, record.prompt_type.value,
                     metadata_json, record.updated_at, tags_json, record.prompt_id)
                )
                logger.info(f"[PromptManager] 更新提示词: {record.prompt_id}")
            else:
                cursor.execute(
                    """INSERT INTO prompts
                       (prompt_id, name, content, prompt_type, metadata,
                        created_at, updated_at, tags)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (record.prompt_id, record.name, record.content, record.prompt_type.value,
                     metadata_json, record.created_at, record.updated_at, tags_json)
                )
                logger.info(f"[PromptManager] 创建提示词: {record.prompt_id}")
            
            conn.commit()
    
    def get_prompt(self, prompt_id: str) -> Optional[PromptRecord]:
        """获取提示词"""
        self.initialize()
        
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM prompts WHERE prompt_id = ?", (prompt_id,))
            row = cursor.fetchone()
            
            if not row:
                return None
            
            return PromptRecord(
                prompt_id=row['prompt_id'],
                name=row['name'],
                content=row['content'],
                prompt_type=PromptType(row['prompt_type']),
                metadata=json.loads(row['metadata']),
                created_at=row['created_at'],
                updated_at=row['updated_at'],
                tags=json.loads(row['tags'])
            )
    
    def list_prompts(self, prompt_type: PromptType = None, limit: int = 100, offset: int = 0) -> List[PromptRecord]:
        """列出提示词"""
        self.initialize()
        
        sql = "SELECT * FROM prompts WHERE 1=1"
        params = []
        
        if prompt_type:
            sql += " AND prompt_type = ?"
            params.append(prompt_type.value)
        
        sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            
            results = []
            for row in cursor.fetchall():
                results.append(PromptRecord(
                    prompt_id=row['prompt_id'],
                    name=row['name'],
                    content=row['content'],
                    prompt_type=PromptType(row['prompt_type']),
                    metadata=json.loads(row['metadata']),
                    created_at=row['created_at'],
                    updated_at=row['updated_at'],
                    tags=json.loads(row['tags'])
                ))
        
        return results
    
    def delete_prompt(self, prompt_id: str) -> bool:
        """删除提示词"""
        self.initialize()
        
        with self._write_lock, self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM prompts WHERE prompt_id = ?", (prompt_id,))
            cursor.execute("DELETE FROM versions WHERE prompt_id = ?", (prompt_id,))
            conn.commit()
        
        logger.info(f"[PromptManager] 删除提示词: {prompt_id}")
        return True
    
    def save_version(self, record: VersionRecord):
        """保存版本"""
        self.initialize()
        
        test_results_json = json.dumps(record.test_results, ensure_ascii=False)
        
        with self._write_lock, self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO versions
                   (version_id, prompt_id, version_number, content, change_log,
                    author, status, created_at, tested_at, approved_at,
                    deprecated_at, test_results)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (record.version_id, record.prompt_id, record.version_number,
                 record.content, record.change_log, record.author, record.status,
                 record.created_at, record.tested_at, record.approved_at,
                 record.deprecated_at, test_results_json)
            )
            conn.commit()
        
        logger.info(f"[PromptManager] 创建版本: {record.version_id}, prompt_id={record.prompt_id}")
    
    def get_version(self, version_id: str) -> Optional[VersionRecord]:
        """获取版本"""
        self.initialize()
        
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM versions WHERE version_id = ?", (version_id,))
            row = cursor.fetchone()
            
            if not row:
                return None
            
            return VersionRecord(
                version_id=row['version_id'],
                prompt_id=row['prompt_id'],
                version_number=row['version_number'],
                content=row['content'],
                change_log=row['change_log'],
                author=row['author'],
                status=row['status'],
                created_at=row['created_at'],
                tested_at=row['tested_at'],
                approved_at=row['approved_at'],
                deprecated_at=row['deprecated_at'],
                test_results=json.loads(row['test_results'])
            )
    
    def get_versions_by_prompt(self, prompt_id: str) -> List[VersionRecord]:
        """获取提示词的所有版本"""
        self.initialize()
        
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM versions WHERE prompt_id = ? ORDER BY created_at DESC",
                (prompt_id,)
            )
            
            results = []
            for row in cursor.fetchall():
                results.append(VersionRecord(
                    version_id=row['version_id'],
                    prompt_id=row['prompt_id'],
                    version_number=row['version_number'],
                    content=row['content'],
                    change_log=row['change_log'],
                    author=row['author'],
                    status=row['status'],
                    created_at=row['created_at'],
                    tested_at=row['tested_at'],
                    approved_at=row['approved_at'],
                    deprecated_at=row['deprecated_at'],
                    test_results=json.loads(row['test_results'])
                ))
        
        return results
    
    def update_version_status(self, version_id: str, status: str):
        """更新版本状态"""
        self.initialize()
        
        update_fields = {'status': status}
        if status == 'testing':
            pass
        elif status == 'approved':
            update_fields['approved_at'] = time.time()
        elif status == 'deprecated':
            update_fields['deprecated_at'] = time.time()
        
        set_clause = ", ".join(f"{k} = ?" for k in update_fields.keys())
        params = list(update_fields.values()) + [version_id]
        
        with self._write_lock, self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""UPDATE versions SET {set_clause} WHERE version_id = ?""",
                params
            )
            conn.commit()
        
        logger.info(f"[PromptManager] 更新版本状态: {version_id} -> {status}")
    
    def update_version_test_results(self, version_id: str, test_results: Dict[str, Any]):
        """更新版本测试结果"""
        self.initialize()
        
        test_results_json = json.dumps(test_results, ensure_ascii=False)
        
        with self._write_lock, self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """UPDATE versions
                   SET test_results = ?, tested_at = ?
                   WHERE version_id = ?""",
                (test_results_json, time.time(), version_id)
            )
            conn.commit()
        
        logger.info(f"[PromptManager] 更新版本测试结果: {version_id}")


# 全局存储实例
_global_prompt_storage = None

def get_prompt_storage() -> PromptStorage:
    """获取全局提示词存储实例"""
    global _global_prompt_storage
    if _global_prompt_storage is None:
        _global_prompt_storage = PromptStorage()
        _global_prompt_storage.initialize()
    return _global_prompt_storage