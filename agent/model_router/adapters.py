"""模型适配器——统一的模型调用接口

提供：
  - 统一的模型调用接口
  - 支持多种模型提供商
  - 请求重试和故障切换
"""

import json
import os
import uuid
import logging
import time
import threading
from typing import Dict, Any, Optional, List
from abc import ABC, abstractmethod
from agent.logging_utils import log_dict

logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]


#: OpenAI **兼容**端点（S9-02）：这些提供商说的是 OpenAI 的
#: ``chat/completions`` 协议，差别只在 ``base_url`` 与模型名，故复用 `OpenAIAdapter`。
#:
#: 存在的理由：`agent/digestion/judge_runtime.py::PROVIDER_CREDENTIAL_ENVS` 早已把
#: `deepseek` / `siliconflow` 当**一等 provider**（各自有凭证键），但本工厂此前不认它们
#: ⇒ `create()` 返回 ``None`` ⇒ 真实 LLM-judge 永远报"未构造出适配器"，
#: 于是"有凭证即用"沦为纸面承诺。此处补齐，**只为这些 provider 增加分支**，
#: 不改动既有分支的任何行为。
#:
#: 表里的值是**缺省端点**，仅当调用方未显式传 ``base_url`` 时生效；部署级
#: ``LLM_BASE_URL`` 优先（由调用方传入，见 `LLMJudge._adapter_kwargs`）。
OPENAI_COMPATIBLE_BASE_URLS: Dict[str, str] = {
    "deepseek": "https://api.deepseek.com/v1",
}

#: 【C1 修(2)】LLM 客户端显式超时与重试上限。
#:
#: 旧行为（审计 Q8 §5 实测）：`OpenAI(**kwargs)` 只传 api_key/base_url，于是生效的是
#: openai 2.24.0 的出厂默认 `DEFAULT_TIMEOUT = Timeout(connect=5.0, read=600, write=600,
#: pool=600)` × `DEFAULT_MAX_RETRIES = 2` ⇒ **单个僵尸外呼线程最长约 1800s 不回收**；
#: 而 waitress 只有 16 个工作线程（app_server.py threads=16），16 个慢请求即可钉死
#: 全部线程 10~30 分钟。
#:
#: 取值理由（初值来自审计 F5）：
#:   connect=5s  —— 建连失败必须快（DNS/端口不可达等），5s 已是宽裕值；
#:   read=45s    —— 远小于 600s；本系统单工具端到端实测 p90 约 3.2s、最慢路径 8.17s
#:                  ⇒ 45s 有 5~10 倍余量，足以容纳长回答而不至于把线程钉死；
#:   max_retries=1 —— 原为 2；SDK 只对连接错误/超时/5xx/429 重试，1 次已覆盖瞬时抖动。
#:                 最坏占用 (5 + 45) × 2 ≈ 100s，相比 1800s 收敛约 18 倍。
#: 部署级调参（不必改代码）：LLM_ADAPTER_CONNECT_TIMEOUT / LLM_ADAPTER_READ_TIMEOUT /
#: LLM_ADAPTER_MAX_RETRIES。
_DEFAULT_CONNECT_TIMEOUT = 5.0
_DEFAULT_READ_TIMEOUT = 45.0
_DEFAULT_MAX_RETRIES = 1


def _env_float(name: str, default: float) -> float:
    """读取浮点环境变量（非法值回退默认值，不抛异常）"""
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning(log_dict({'module_name': 'adapters', 'action': 'llm_client.env_invalid', 'env': name, 'value': str(raw)[:40], 'fallback': default}))
        return default
    return value if value > 0 else default


def _env_int(name: str, default: int) -> int:
    """读取整数环境变量（非法值回退默认值；允许 0 = 不重试）"""
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning(log_dict({'module_name': 'adapters', 'action': 'llm_client.env_invalid', 'env': name, 'value': str(raw)[:40], 'fallback': default}))
        return default
    return value if value >= 0 else default


class ModelAdapter(ABC):
    """模型适配器抽象基类"""
    
    @abstractmethod
    def get_provider_name(self) -> str:
        """获取提供商名称"""
        pass
    
    @abstractmethod
    def get_model_name(self) -> str:
        """获取模型名称"""
        pass
    
    @abstractmethod
    def get_cost_per_token(self) -> Dict[str, float]:
        """获取每 token 成本"""
        pass
    
    @abstractmethod
    def generate(self, prompt: str, **kwargs) -> Dict:
        """生成响应"""
        pass
    
    @abstractmethod
    def chat(self, messages: List[Dict], **kwargs) -> Dict:
        """对话模式"""
        pass
    
    @abstractmethod
    def is_available(self) -> bool:
        """检查模型是否可用"""
        pass


class OpenAIAdapter(ModelAdapter):
    """OpenAI 模型适配器（**也承载 OpenAI 兼容端点**，见 `OPENAI_COMPATIBLE_BASE_URLS`）"""

    def __init__(self, model_name: str, api_key: Optional[str] = None,
                 base_url: Optional[str] = None, *,
                 timeout: Optional[Any] = None,
                 max_retries: Optional[int] = None):
        """初始化 OpenAI（兼容）适配器

        Args:
            timeout: 客户端级超时（openai.Timeout 或秒数）。None（默认）= 用本模块
                     常量/环境变量解析出的 Timeout(connect=5s, read=45s)。
            max_retries: 客户端级重试上限。None（默认）= 常量/环境变量（1）。
        """
        self._model_name = model_name
        self._api_key = api_key
        self._base_url = base_url
        # 【C1 修(2)】显式超时/重试；None 表示走 _client_options() 的默认解析
        self._timeout = timeout
        self._max_retries = max_retries
        self._client = None
        # [2026-08-13 并发审计 #3] 懒加载双检锁：并发首次调用只创建一个 client
        self._client_lock = threading.Lock()

    def _client_options(self) -> Dict[str, Any]:
        """构造 OpenAI **客户端级**选项（timeout / max_retries）

        【C1 修(2)】旧实现不传这两个参数 ⇒ 吃 SDK 默认 read=600s × retries=2。
        【不易】不改变"调用方显式传 timeout"的语义：openai 2.24.0 里请求级参数优先，
                源码实测（openai/_base_client.py）：
                    timeout = self.timeout if isinstance(options.timeout, NotGiven)
                              else options.timeout
                    max_retries = options.get_max_retries(self.max_retries)
                ⇒ generate()/chat() 的 **kwargs 里显式传入的 timeout / max_retries
                仍然按调用方的值生效（它们经 safe_kwargs 透传给 chat.completions.create）。
        """
        options: Dict[str, Any] = {}
        timeout = self._timeout
        if timeout is None:
            connect = _env_float("LLM_ADAPTER_CONNECT_TIMEOUT", _DEFAULT_CONNECT_TIMEOUT)
            read = _env_float("LLM_ADAPTER_READ_TIMEOUT", _DEFAULT_READ_TIMEOUT)
            try:
                from openai import Timeout as OpenAITimeout
                timeout = OpenAITimeout(connect=connect, read=read, write=read, pool=connect)
            except Exception:  # noqa: BLE001  旧版 SDK 无 Timeout 导出 → 退回读超时单值
                timeout = read
        options["timeout"] = timeout
        if self._max_retries is None:
            options["max_retries"] = _env_int("LLM_ADAPTER_MAX_RETRIES", _DEFAULT_MAX_RETRIES)
        else:
            options["max_retries"] = int(self._max_retries)
        return options

    def _get_client(self):
        """获取客户端"""
        if self._client is None:
            with self._client_lock:
                if self._client is None:
                    try:
                        from openai import OpenAI
                        kwargs = {}
                        if self._api_key:
                            kwargs["api_key"] = self._api_key
                        if self._base_url:
                            kwargs["base_url"] = self._base_url
                        # 【C1 修(2)】显式传超时与重试上限，杜绝 SDK 默认 600s×3 的僵尸线程
                        kwargs.update(self._client_options())
                        self._client = OpenAI(**kwargs)
                    except ImportError:
                        logger.warning(log_dict({'module_name': 'adapters', 'action': 'openai', 'msg': 'openai 库未安装'}))
        return self._client
    
    def get_provider_name(self) -> str:
        return "openai"
    
    def get_model_name(self) -> str:
        return self._model_name
    
    def get_cost_per_token(self) -> Dict[str, float]:
        costs = {
            "gpt-3.5-turbo": {"prompt": 0.0015, "completion": 0.002},
            "gpt-4o-mini": {"prompt": 0.0015, "completion": 0.006},
            "gpt-4": {"prompt": 0.03, "completion": 0.06},
            "gpt-4o": {"prompt": 0.005, "completion": 0.015},
        }
        return costs.get(self._model_name, {"prompt": 0.0015, "completion": 0.002})
    
    def generate(self, prompt: str, **kwargs) -> Dict:
        try:
            client = self._get_client()
            if client is None:
                return {"error": "OpenAI client not available"}
            
            _api_reserved = {"model", "messages"}
            safe_kwargs = {k: v for k, v in kwargs.items() if k not in _api_reserved}
            response = client.chat.completions.create(
                model=self._model_name,
                messages=[{"role": "user", "content": prompt}],
                **safe_kwargs
            )
            
            return {
                "success": True,
                "content": response.choices[0].message.content,
                # S9-02：推理型模型可能把 token 预算吃在 reasoning 上 ⇒
                # 可见 content 为空而 success 仍为 True。透出 finish_reason
                # （``length`` = 被截断）让这种"成功但无内容"在生产日志里**可诊断**，
                # 而不是只看到一句"模型没回复"。
                "finish_reason": getattr(response.choices[0], "finish_reason", None),
                "usage": {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                },
                "model": self._model_name,
                "provider": self.get_provider_name(),
            }
        except Exception as e:
            logger.error(log_dict({'module_name': 'adapters', 'action': 'openai.api.error', 'msg': f'OpenAI API error: {e}'}))
            return {"success": False, "error": str(e)}
    
    def chat(self, messages: List[Dict], **kwargs) -> Dict:
        try:
            client = self._get_client()
            if client is None:
                return {"error": "OpenAI client not available"}
            
            _api_reserved = {"model", "messages"}
            safe_kwargs = {k: v for k, v in kwargs.items() if k not in _api_reserved}
            response = client.chat.completions.create(
                model=self._model_name,
                messages=messages,
                **safe_kwargs
            )
            
            return {
                "success": True,
                "content": response.choices[0].message.content,
                "finish_reason": getattr(response.choices[0], "finish_reason", None),
                "usage": {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                },
                "model": self._model_name,
                "provider": self.get_provider_name(),
            }
        except Exception as e:
            logger.error(log_dict({'module_name': 'adapters', 'action': 'openai.api.error', 'msg': f'OpenAI API error: {e}'}))
            return {"success": False, "error": str(e)}
    
    def is_available(self) -> bool:
        try:
            client = self._get_client()
            return client is not None
        except Exception:
            return False


class ClaudeAdapter(ModelAdapter):
    """Claude 模型适配器"""
    
    def __init__(self, model_name: str, api_key: str = None):
        self._model_name = model_name
        self._api_key = api_key
        self._client = None
        # [2026-08-13 并发审计 #3] 懒加载双检锁：并发首次调用只创建一个 client
        self._client_lock = threading.Lock()
    
    def _get_client(self):
        """获取客户端"""
        if self._client is None:
            with self._client_lock:
                if self._client is None:
                    try:
                        from anthropic import Anthropic
                        kwargs = {}
                        if self._api_key:
                            kwargs["api_key"] = self._api_key
                        self._client = Anthropic(**kwargs)
                    except ImportError:
                        logger.warning(log_dict({'module_name': 'adapters', 'action': 'anthropic', 'msg': 'anthropic 库未安装'}))
        return self._client
    
    def get_provider_name(self) -> str:
        return "claude"
    
    def get_model_name(self) -> str:
        return self._model_name
    
    def get_cost_per_token(self) -> Dict[str, float]:
        costs = {
            "claude-3-haiku": {"prompt": 0.00025, "completion": 0.00125},
            "claude-3-sonnet": {"prompt": 0.00075, "completion": 0.003},
            "claude-3-opus": {"prompt": 0.0015, "completion": 0.006},
        }
        return costs.get(self._model_name, {"prompt": 0.00075, "completion": 0.003})
    
    def generate(self, prompt: str, **kwargs) -> Dict:
        try:
            client = self._get_client()
            if client is None:
                return {"error": "Claude client not available"}
            
            response = client.messages.create(
                model=self._model_name,
                max_tokens=kwargs.get("max_tokens", 1024),
                messages=[{"role": "user", "content": prompt}],
            )
            
            return {
                "success": True,
                "content": response.content[0].text,
                "usage": {
                    "prompt_tokens": response.usage.input_tokens,
                    "completion_tokens": response.usage.output_tokens,
                    "total_tokens": response.usage.input_tokens + response.usage.output_tokens,
                },
                "model": self._model_name,
                "provider": self.get_provider_name(),
            }
        except Exception as e:
            logger.error(log_dict({'module_name': 'adapters', 'action': 'claude.api.error', 'msg': f'Claude API error: {e}'}))
            return {"success": False, "error": str(e)}
    
    def chat(self, messages: List[Dict], **kwargs) -> Dict:
        try:
            client = self._get_client()
            if client is None:
                return {"error": "Claude client not available"}
            
            response = client.messages.create(
                model=self._model_name,
                max_tokens=kwargs.get("max_tokens", 1024),
                messages=messages,
            )
            
            return {
                "success": True,
                "content": response.content[0].text,
                "usage": {
                    "prompt_tokens": response.usage.input_tokens,
                    "completion_tokens": response.usage.output_tokens,
                    "total_tokens": response.usage.input_tokens + response.usage.output_tokens,
                },
                "model": self._model_name,
                "provider": self.get_provider_name(),
            }
        except Exception as e:
            logger.error(log_dict({'module_name': 'adapters', 'action': 'claude.api.error', 'msg': f'Claude API error: {e}'}))
            return {"success": False, "error": str(e)}
    
    def is_available(self) -> bool:
        try:
            client = self._get_client()
            return client is not None
        except Exception:
            return False


class GeminiAdapter(ModelAdapter):
    """Gemini 模型适配器"""
    
    def __init__(self, model_name: str, api_key: str = None):
        self._model_name = model_name
        self._api_key = api_key
        self._client = None
    
    def _get_client(self):
        """获取客户端"""
        if self._client is None:
            try:
                import google.generativeai as genai
                if self._api_key:
                    genai.configure(api_key=self._api_key)
                self._client = genai.GenerativeModel(self._model_name)
            except ImportError:
                logger.warning(log_dict({'module_name': 'adapters', 'action': 'google.generativeai', 'msg': 'google-generativeai 库未安装'}))
        return self._client
    
    def get_provider_name(self) -> str:
        return "gemini"
    
    def get_model_name(self) -> str:
        return self._model_name
    
    def get_cost_per_token(self) -> Dict[str, float]:
        costs = {
            "gemini-1.0-pro": {"prompt": 0.0015, "completion": 0.0015},
            "gemini-1.5-flash": {"prompt": 0.000125, "completion": 0.000375},
            "gemini-1.5-pro": {"prompt": 0.001, "completion": 0.003},
        }
        return costs.get(self._model_name, {"prompt": 0.000125, "completion": 0.000375})
    
    def generate(self, prompt: str, **kwargs) -> Dict:
        try:
            client = self._get_client()
            if client is None:
                return {"error": "Gemini client not available"}
            
            response = client.generate_content(prompt)
            
            return {
                "success": True,
                "content": response.text,
                "usage": {
                    "prompt_tokens": response.usage_metadata.prompt_token_count,
                    "completion_tokens": response.usage_metadata.candidates_token_count,
                    "total_tokens": response.usage_metadata.total_token_count,
                },
                "model": self._model_name,
                "provider": self.get_provider_name(),
            }
        except Exception as e:
            logger.error(log_dict({'module_name': 'adapters', 'action': 'gemini.api.error', 'msg': f'Gemini API error: {e}'}))
            return {"success": False, "error": str(e)}
    
    def chat(self, messages: List[Dict], **kwargs) -> Dict:
        try:
            client = self._get_client()
            if client is None:
                return {"error": "Gemini client not available"}
            
            chat = client.start_chat(history=[])
            for msg in messages[:-1]:
                chat.send_message(msg["content"])
            
            response = chat.send_message(messages[-1]["content"])
            
            return {
                "success": True,
                "content": response.text,
                "model": self._model_name,
                "provider": self.get_provider_name(),
            }
        except Exception as e:
            logger.error(log_dict({'module_name': 'adapters', 'action': 'gemini.api.error', 'msg': f'Gemini API error: {e}'}))
            return {"success": False, "error": str(e)}
    
    def is_available(self) -> bool:
        try:
            client = self._get_client()
            return client is not None
        except Exception:
            return False


class ZhipuAdapter(ModelAdapter):
    """智谱 AI 模型适配器"""
    
    def __init__(self, model_name: str, api_key: str = None):
        self._model_name = model_name
        self._api_key = api_key
        self._client = None
    
    def _get_client(self):
        """获取客户端"""
        if self._client is None:
            try:
                from zhipuai import ZhipuAI
                kwargs = {}
                if self._api_key:
                    kwargs["api_key"] = self._api_key
                self._client = ZhipuAI(**kwargs)
            except ImportError:
                logger.warning(log_dict({'module_name': 'adapters', 'action': 'zhipuai', 'msg': 'zhipuai 库未安装'}))
        return self._client
    
    def get_provider_name(self) -> str:
        return "zhipu"
    
    def get_model_name(self) -> str:
        return self._model_name
    
    def get_cost_per_token(self) -> Dict[str, float]:
        costs = {
            "glm-4": {"prompt": 0.002, "completion": 0.002},
            "glm-4v": {"prompt": 0.002, "completion": 0.002},
            "glm-3-turbo": {"prompt": 0.0005, "completion": 0.0005},
        }
        return costs.get(self._model_name, {"prompt": 0.002, "completion": 0.002})
    
    def generate(self, prompt: str, **kwargs) -> Dict:
        try:
            client = self._get_client()
            if client is None:
                return {"error": "Zhipu client not available"}
            
            _api_reserved = {"model", "messages"}
            safe_kwargs = {k: v for k, v in kwargs.items() if k not in _api_reserved}
            response = client.chat.completions.create(
                model=self._model_name,
                messages=[{"role": "user", "content": prompt}],
                **safe_kwargs
            )
            
            return {
                "success": True,
                "content": response.choices[0].message.content,
                "usage": {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                },
                "model": self._model_name,
                "provider": self.get_provider_name(),
            }
        except Exception as e:
            logger.error(log_dict({'module_name': 'adapters', 'action': 'zhipu.api.error', 'msg': f'Zhipu API error: {e}'}))
            return {"success": False, "error": str(e)}
    
    def chat(self, messages: List[Dict], **kwargs) -> Dict:
        try:
            client = self._get_client()
            if client is None:
                return {"error": "Zhipu client not available"}
            
            _api_reserved = {"model", "messages"}
            safe_kwargs = {k: v for k, v in kwargs.items() if k not in _api_reserved}
            response = client.chat.completions.create(
                model=self._model_name,
                messages=messages,
                **safe_kwargs
            )
            
            return {
                "success": True,
                "content": response.choices[0].message.content,
                "usage": {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                },
                "model": self._model_name,
                "provider": self.get_provider_name(),
            }
        except Exception as e:
            logger.error(log_dict({'module_name': 'adapters', 'action': 'zhipu.api.error', 'msg': f'Zhipu API error: {e}'}))
            return {"success": False, "error": str(e)}
    
    def is_available(self) -> bool:
        try:
            client = self._get_client()
            return client is not None
        except Exception:
            return False


class QwenAdapter(ModelAdapter):
    """阿里云通义千问适配器"""
    
    def __init__(self, model_name: str, api_key: str = None, api_secret: str = None):
        self._model_name = model_name
        self._api_key = api_key
        self._api_secret = api_secret
        self._client = None
    
    def _get_client(self):
        """获取客户端"""
        if self._client is None:
            try:
                from alibabacloud_tea_openapi import models as open_api_models
                from alibabacloud_dashscope_api20230714 import Client, models
                
                config = open_api_models.Config(
                    access_key_id=self._api_key,
                    access_key_secret=self._api_secret,
                )
                config.endpoint = "dashscope.cn-beijing.aliyuncs.com"
                self._client = Client(config)
            except ImportError:
                logger.warning(log_dict({'module_name': 'adapters', 'action': 'alibabacloud.dashscope.api', 'msg': 'alibabacloud-dashscope-api 库未安装'}))
        return self._client
    
    def get_provider_name(self) -> str:
        return "qwen"
    
    def get_model_name(self) -> str:
        return self._model_name
    
    def get_cost_per_token(self) -> Dict[str, float]:
        costs = {
            "qwen-turbo": {"prompt": 0.0008, "completion": 0.0012},
            "qwen-plus": {"prompt": 0.0015, "completion": 0.002},
            "qwen-max": {"prompt": 0.003, "completion": 0.006},
        }
        return costs.get(self._model_name, {"prompt": 0.0008, "completion": 0.0012})
    
    def generate(self, prompt: str, **kwargs) -> Dict:
        try:
            client = self._get_client()
            if client is None:
                return {"error": "Qwen client not available"}
            
            from alibabacloud_dashscope_api20230714 import models as dash_models
            
            request = dash_models.ChatCompletionRequest(
                model=self._model_name,
                messages=[{"role": "user", "content": prompt}],
            )
            response = client.chat_completion(request)
            
            return {
                "success": True,
                "content": response.body.output.choices[0].message.content,
                "model": self._model_name,
                "provider": self.get_provider_name(),
            }
        except Exception as e:
            logger.error(log_dict({'module_name': 'adapters', 'action': 'qwen.api.error', 'msg': f'Qwen API error: {e}'}))
            return {"success": False, "error": str(e)}
    
    def chat(self, messages: List[Dict], **kwargs) -> Dict:
        try:
            client = self._get_client()
            if client is None:
                return {"error": "Qwen client not available"}
            
            from alibabacloud_dashscope_api20230714 import models as dash_models
            
            request = dash_models.ChatCompletionRequest(
                model=self._model_name,
                messages=messages,
            )
            response = client.chat_completion(request)
            
            return {
                "success": True,
                "content": response.body.output.choices[0].message.content,
                "model": self._model_name,
                "provider": self.get_provider_name(),
            }
        except Exception as e:
            logger.error(log_dict({'module_name': 'adapters', 'action': 'qwen.api.error', 'msg': f'Qwen API error: {e}'}))
            return {"success": False, "error": str(e)}
    
    def is_available(self) -> bool:
        try:
            client = self._get_client()
            return client is not None
        except Exception:
            return False


class ModelAdapterFactory:
    """模型适配器工厂"""
    
    @staticmethod
    def create(provider: str, model_name: str, **kwargs) -> Optional[ModelAdapter]:
        """创建模型适配器"""
        provider = provider.lower()
        
        if provider == "openai":
            return OpenAIAdapter(model_name, kwargs.get("api_key"), kwargs.get("base_url"),
                                 timeout=kwargs.get("timeout"),
                                 max_retries=kwargs.get("max_retries"))
        elif provider in OPENAI_COMPATIBLE_BASE_URLS:
            # S9-02：OpenAI 兼容端点（DeepSeek 等）——同协议，只是端点与模型名不同
            base_url = str(kwargs.get("base_url")
                           or OPENAI_COMPATIBLE_BASE_URLS[provider])
            # 【C1 修(2)】把调用方显式给的 timeout / max_retries 透传到客户端；
            # 其余未知 kwargs 的忽略行为不变（保持向后兼容）
            return OpenAIAdapter(model_name, kwargs.get("api_key"), base_url,
                                 timeout=kwargs.get("timeout"),
                                 max_retries=kwargs.get("max_retries"))
        elif provider == "claude":
            return ClaudeAdapter(model_name, kwargs.get("api_key"))
        elif provider == "gemini":
            return GeminiAdapter(model_name, kwargs.get("api_key"))
        elif provider == "zhipu":
            return ZhipuAdapter(model_name, kwargs.get("api_key"))
        elif provider == "qwen":
            return QwenAdapter(model_name, kwargs.get("api_key"), kwargs.get("api_secret"))
        else:
            logger.warning(log_dict({'module_name': 'adapters', 'action': 'provider', 'msg': f'未知提供商: {provider}'}))
            return None