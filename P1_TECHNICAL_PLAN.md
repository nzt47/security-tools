# P1阶段技术实现计划

## 目录

1. [安全合规增强实现](#1-安全合规增强)
2. [多模态感知增强实现](#2-多模态感知增强)
3. [反思学习增强实现](#3-反思学习增强)
4. [长期记忆实现](#4-长期记忆实现)

---

## 1. 安全合规增强

### 1.1 日志加密模块

**新文件: `agent/security/encryptor.py`**

```python
"""
日志加密模块 — 保护敏感数据

使用AES-256-GCM加密敏感字段，密钥从环境变量读取
"""

import os
import json
import base64
import logging
from typing import Dict, Any, Optional
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logger = logging.getLogger(__name__)

class LogEncryptor:
    """日志加密器"""
    
    def __init__(self, key_env_var: str = "Yunshu_ENCRYPT_KEY", 
                 salt_env_var: str = "Yunshu_ENCRYPT_SALT"):
        """初始化加密器
        
        Args:
            key_env_var: 加密密钥的环境变量名
            salt_env_var: 盐值的环境变量名
        """
        self._key = self._load_or_generate_key(key_env_var, salt_env_var)
        self._cipher = Fernet(self._key)
        logger.info("日志加密器已初始化")
        
    def _load_or_generate_key(self, key_env_var: str, salt_env_var: str) -> bytes:
        """加载或生成加密密钥"""
        # 尝试从环境变量加载
        key_str = os.getenv(key_env_var)
        if key_str:
            try:
                return base64.urlsafe_b64decode(key_str)
            except Exception as e:
                logger.warning(f"加载密钥失败: {e}，将生成新密钥")
        
        # 生成新密钥
        new_key = Fernet.generate_key()
        logger.info("已生成新的加密密钥，请保存到环境变量")
        logger.info(f"  key_env_var={key_env_var}")
        logger.info(f"  key_value={base64.urlsafe_b64encode(new_key).decode()}")
        return new_key
    
    def encrypt_string(self, plaintext: str) -> str:
        """加密字符串"""
        if not plaintext:
            return plaintext
        try:
            ciphertext = self._cipher.encrypt(plaintext.encode("utf-8"))
            return base64.urlsafe_b64encode(ciphertext).decode()
        except Exception as e:
            logger.error(f"加密失败: {e}")
            return plaintext
    
    def decrypt_string(self, ciphertext: str) -> str:
        """解密字符串"""
        if not ciphertext:
            return ciphertext
        try:
            decoded = base64.urlsafe_b64decode(ciphertext)
            plaintext = self._cipher.decrypt(decoded)
            return plaintext.decode("utf-8")
        except Exception as e:
            logger.error(f"解密失败: {e}")
            return ciphertext
    
    def encrypt_dict(self, data: Dict[str, Any], fields: list) -> Dict[str, Any]:
        """加密字典中指定的字段
        
        Args:
            data: 原始字典
            fields: 需要加密的字段列表
            
        Returns:
            加密后的字典
        """
        result = dict(data)
        for field in fields:
            if field in result:
                result[field] = self.encrypt_string(str(result[field]))
                result[f"_{field}_encrypted"] = True
        return result
    
    def decrypt_dict(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """解密字典中标记为已加密的字段"""
        result = dict(data)
        # 找出所有标记为加密的字段
        encrypted_fields = [k[1:] for k in result.keys() if k.startswith('_') and k.endswith('_encrypted')]
        for field in encrypted_fields:
            if field in result:
                result[field] = self.decrypt_string(str(result[field]))
        return result
```

**集成步骤**:
1. 在 `memory/black_box.py` 中集成加密器
2. 配置敏感字段列表（如 `user_message`, `llm_response` 等）

---

### 1.2 数据脱敏模块

**新文件: `agent/security/data_sanitizer.py`**

```python
"""
数据脱敏模块 — 自动检测并替换敏感数据
"""

import re
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

# 敏感数据模式
SENSITIVE_PATTERNS = {
    "api_key": re.compile(r'(?i)(api[_-]?key|secret[_-]?key|token)\s*[=:]\s*["\']?([a-zA-Z0-9-_]{16,})["\']?', re.IGNORECASE),
    "password": re.compile(r'(?i)password\s*[=:]\s*["\']?([^\s"\']{6,})["\']?', re.IGNORECASE),
    "email": re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', re.IGNORECASE),
    "phone": re.compile(r'(?<!\d)((1[3-9]\d{9})|(\d{3}-\d{4}-\d{4}))(?!\d)', re.IGNORECASE),
}

class DataSanitizer:
    """数据脱敏器"""
    
    def __init__(self):
        self._patterns = SENSITIVE_PATTERNS
        self._replacements = {}
        logger.info("数据脱敏器已初始化")
    
    def sanitize_string(self, text: str, placeholder: str = "[REDACTED]") -> str:
        """脱敏字符串"""
        result = text
        for name, pattern in self._patterns.items():
            matches = pattern.findall(result)
            for match in matches:
                if isinstance(match, tuple):
                    match = match[-1]
                result = result.replace(match, placeholder)
        return result
    
    def sanitize_dict(self, data: Dict[str, Any], placeholder: str = "[REDACTED]") -> Dict[str, Any]:
        """脱敏字典"""
        result = dict(data)
        for key, value in result.items():
            if isinstance(value, str):
                result[key] = self.sanitize_string(value, placeholder)
            elif isinstance(value, dict):
                result[key] = self.sanitize_dict(value, placeholder)
            elif isinstance(value, list):
                result[key] = [self.sanitize_string(item, placeholder) if isinstance(item, str) else item for item in value]
        return result
```

---

## 2. 多模态感知增强

### 2.1 图像理解模块

**新文件: `agent/multimodal/image_understanding.py`**

```python
"""
图像理解模块 — 使用视觉LLM分析屏幕内容

基于现有的OCR传感器，增加视觉理解能力
"""

import base64
import logging
from typing import Dict, Any, Optional
from ..tools import register as register_tool

logger = logging.getLogger(__name__)

class ImageAnalyzer:
    """图像分析器"""
    
    def __init__(self, llm_service=None):
        """初始化图像分析器
        
        Args:
            llm_service: LLM服务（支持视觉理解
        """
        self._llm = llm_service
        self._ocr_sensor = None  # 会在DigitalLife中注入
        logger.info("图像分析器已初始化")
    
    def describe_image(self, image_path: Optional[str] = None, 
                      image_base64: Optional[str] = None, 
                      prompt: str = "请描述这张图片的内容") -> str:
        """描述图像内容"""
        if not self._llm or not hasattr(self._llm, "vision_chat"):
            return "视觉理解服务不可用"
            
        try:
            if image_path:
                with open(image_path, "rb") as f:
                    image_base64 = base64.b64encode(f.read()).decode()
                    
            return self._llm.vision_chat(prompt, image_base64)
        except Exception as e:
            logger.error(f"图像分析失败: {e}")
            return f"图像分析失败: {e}"
    
    def analyze_current_screen(self, prompt: str = "请描述当前屏幕显示的内容") -> Dict[str, Any]:
        """分析当前屏幕"""
        if not self._ocr_sensor:
            return {"error": "OCR传感器不可用"}
        
        ocr_result = self._ocr_sensor.capture_and_recognize()
        
        if not ocr_result.get("has_content"):
            return {"error": "未检测到屏幕内容"}
        
        # 这里可以调用视觉LLM增强理解
        return {
            "ocr_text": ocr_result.get("text", ""),
            "analysis": "（需要视觉LLM才能提供更丰富的分析）"
        }

@register_tool("describe_screen", "描述当前屏幕内容")
def describe_screen(**kwargs):
    """MCP工具: 描述当前屏幕"""
    # 实际实现会在DigitalLife中注入
    return "屏幕描述功能需要配置"
```

---

### 2.2 语音识别与合成

**新文件: `agent/multimodal/voice.py`**

```python
"""
语音识别与合成模块

提供语音转文字(STT)和文字转语音(TTS)功能
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

class VoiceProcessor:
    """语音处理器"""
    
    def __init__(self):
        self._stt_model = None
        self._tts_model = None
        logger.info("语音处理器已初始化")
    
    def recognize_speech(self, audio_file: str) -> str:
        """语音识别（语音转文字）"""
        # 集成 Whisper
        try:
            import whisper
            if not self._stt_model:
                self._stt_model = whisper.load_model("base")
            
            result = self._stt_model.transcribe(audio_file)
            return result.get("text", "")
        except ImportError:
            logger.warning("Whisper未安装，语音识别不可用")
            return ""
    
    def synthesize_speech(self, text: str, output_file: str = "output.wav"):
        """语音合成（文字转语音）"""
        try:
            import edge_tts
            import asyncio
            asyncio.run(self._synthesize_async(text, output_file))
        except ImportError:
            logger.warning("edge-tts未安装，语音合成不可用")
```

---

## 3. 反思学习增强

### 3.1 增强的反思引擎

**修改文件: `planning/reflector.py`**

```python
"""
增强的反思引擎 — 从经验中学习

新增功能：
- 执行评分系统
- 根因分析
- 经验提取与存储
- 提示词优化建议
"""

import logging
from typing import Dict, Any, List
from datetime import datetime

logger = logging.getLogger(__name__)

class EnhancedReflector:
    """增强的反思器"""
    
    def __init__(self):
        self._execution_history = []
        self._reflection_log = []
        
    def evaluate_execution(self, task: str, response: str, 
                          success: bool, metrics: Dict[str, Any]) -> Dict[str, Any]:
        """评估执行效果"""
        evaluation = {
            "timestamp": datetime.now().isoformat(),
            "task": task,
            "success": success,
            "metrics": metrics,
            "score": self._calculate_score(success, metrics),
        }
        
        self._execution_history.append(evaluation)
        logger.info(f"执行评估: {evaluation['score']}分")
        return evaluation
    
    def _calculate_score(self, success: bool, metrics: Dict[str, Any]) -> int:
        """计算执行评分（0-100分）"""
        if not success:
            return 30
        
        score = 70  # 基础分
        # 根据执行时间、工具调用次数等调整评分
        time_taken = metrics.get("time_taken", 0)
        if time_taken < 5:
            score += 20
        elif time_taken < 15:
            score += 10
        
        return min(100, score)
    
    def extract_lesson(self, evaluation: Dict[str, Any]) -> str:
        """提取经验教训"""
        # 根据评估结果生成经验教训
        if evaluation["success"] and evaluation["score"] >= 80:
            return f"成功经验：{evaluation['task']} 执行良好，得分{evaluation['score']}"
        elif not evaluation["success"]:
            return f"失败教训：{evaluation['task']} 执行失败"
        
        return f"普通经验：{evaluation['task']}"
```

---

## 4. 长期记忆系统

### 4.1 向量数据库集成

**新文件: `memory/vector_store.py`**

```python
"""
向量数据库模块 — 语义相似性检索

使用ChromaDB或FAISS实现长期记忆的语义检索
"""

import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

class VectorStore:
    """向量存储管理器"""
    
    def __init__(self, storage_path: str = "data/vector_db"):
        self._storage_path = storage_path
        self._collection = None
        self._embedder = None
        logger.info("向量存储管理器已初始化")
    
    def _init_chromadb(self):
        """初始化ChromaDB"""
        try:
            import chromadb
            from chromadb.config import Settings
            self._client = chromadb.Client(Settings(
                persist_directory=self._storage_path,
                anonymized_telemetry=False
            ))
            self._collection = self._client.get_or_create_collection("Yunshu_memory")
            logger.info("ChromaDB已初始化")
        except ImportError:
            logger.warning("ChromaDB未安装，向量搜索不可用")
    
    def add_memory(self, id: str, text: str, metadata: Dict[str, Any]):
        """添加记忆"""
        if not self._collection:
            self._init_chromadb()
        
        if self._collection:
            self._collection.add(
                ids=[id],
                documents=[text],
                metadatas=[metadata]
            )
            logger.debug(f"已添加记忆: {id}")
    
    def search_memories(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """搜索相关记忆"""
        if not self._collection:
            self._init_chromadb()
        
        if not self._collection:
            return []
        
        results = self._collection.query(
            query_texts=[query],
            n_results=top_k
        )
        return results
```

---

## 集成顺序

### 第一阶段：安全合规（优先级最高）
1. 第1天：创建 `agent/security/` 包和基础框架
2. 第2-3天：实现日志加密
3. 第4-5天：实现数据脱敏
4. 第6-7天：集成到BlackBox和DigitalLife

### 第二阶段：多模态感知
1. 第8-10天：增强图像理解
2. 第11-13天：语音识别与合成
3. 第14-15天：多模态融合集成

### 第三阶段：反思学习
1. 第16-18天：增强反思引擎
2. 第19-21天：经验知识库
3. 第22-25天：向量数据库集成

---

## 测试策略

为每个新增模块创建测试文件：
- `test_security.py`
- `test_multimodal.py`
- `test_enhanced_reflector.py`
- `test_vector_store.py`
