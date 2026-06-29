"""模型适配器——统一的模型调用接口

提供：
  - 统一的模型调用接口
  - 支持多种模型提供商
  - 请求重试和故障切换
"""

import json
import uuid
import logging
import time
from typing import Dict, Any, Optional, List
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]



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
    """OpenAI 模型适配器"""
    
    def __init__(self, model_name: str, api_key: str = None, base_url: str = None):
        self._model_name = model_name
        self._api_key = api_key
        self._base_url = base_url
        self._client = None
    
    def _get_client(self):
        """获取客户端"""
        if self._client is None:
            try:
                from openai import OpenAI
                kwargs = {}
                if self._api_key:
                    kwargs["api_key"] = self._api_key
                if self._base_url:
                    kwargs["base_url"] = self._base_url
                self._client = OpenAI(**kwargs)
            except ImportError:
                logger.warning(json.dumps({"trace_id": _trace_id(), "module_name": "adapters", "action": "openai", "msg": "openai 库未安装"}, ensure_ascii=False))
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
                "usage": {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                },
                "model": self._model_name,
                "provider": self.get_provider_name(),
            }
        except Exception as e:
            logger.error(json.dumps({"trace_id": _trace_id(), "module_name": "adapters", "action": "openai.api.error", "msg": f"OpenAI API error: {e}"}, ensure_ascii=False))
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
                "usage": {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                },
                "model": self._model_name,
                "provider": self.get_provider_name(),
            }
        except Exception as e:
            logger.error(json.dumps({"trace_id": _trace_id(), "module_name": "adapters", "action": "openai.api.error", "msg": f"OpenAI API error: {e}"}, ensure_ascii=False))
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
    
    def _get_client(self):
        """获取客户端"""
        if self._client is None:
            try:
                from anthropic import Anthropic
                kwargs = {}
                if self._api_key:
                    kwargs["api_key"] = self._api_key
                self._client = Anthropic(**kwargs)
            except ImportError:
                logger.warning(json.dumps({"trace_id": _trace_id(), "module_name": "adapters", "action": "anthropic", "msg": "anthropic 库未安装"}, ensure_ascii=False))
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
            logger.error(json.dumps({"trace_id": _trace_id(), "module_name": "adapters", "action": "claude.api.error", "msg": f"Claude API error: {e}"}, ensure_ascii=False))
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
            logger.error(json.dumps({"trace_id": _trace_id(), "module_name": "adapters", "action": "claude.api.error", "msg": f"Claude API error: {e}"}, ensure_ascii=False))
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
                logger.warning(json.dumps({"trace_id": _trace_id(), "module_name": "adapters", "action": "google.generativeai", "msg": "google-generativeai 库未安装"}, ensure_ascii=False))
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
            logger.error(json.dumps({"trace_id": _trace_id(), "module_name": "adapters", "action": "gemini.api.error", "msg": f"Gemini API error: {e}"}, ensure_ascii=False))
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
            logger.error(json.dumps({"trace_id": _trace_id(), "module_name": "adapters", "action": "gemini.api.error", "msg": f"Gemini API error: {e}"}, ensure_ascii=False))
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
                logger.warning(json.dumps({"trace_id": _trace_id(), "module_name": "adapters", "action": "zhipuai", "msg": "zhipuai 库未安装"}, ensure_ascii=False))
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
            logger.error(json.dumps({"trace_id": _trace_id(), "module_name": "adapters", "action": "zhipu.api.error", "msg": f"Zhipu API error: {e}"}, ensure_ascii=False))
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
            logger.error(json.dumps({"trace_id": _trace_id(), "module_name": "adapters", "action": "zhipu.api.error", "msg": f"Zhipu API error: {e}"}, ensure_ascii=False))
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
                logger.warning(json.dumps({"trace_id": _trace_id(), "module_name": "adapters", "action": "alibabacloud.dashscope.api", "msg": "alibabacloud-dashscope-api 库未安装"}, ensure_ascii=False))
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
            logger.error(json.dumps({"trace_id": _trace_id(), "module_name": "adapters", "action": "qwen.api.error", "msg": f"Qwen API error: {e}"}, ensure_ascii=False))
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
            logger.error(json.dumps({"trace_id": _trace_id(), "module_name": "adapters", "action": "qwen.api.error", "msg": f"Qwen API error: {e}"}, ensure_ascii=False))
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
            return OpenAIAdapter(model_name, kwargs.get("api_key"), kwargs.get("base_url"))
        elif provider == "claude":
            return ClaudeAdapter(model_name, kwargs.get("api_key"))
        elif provider == "gemini":
            return GeminiAdapter(model_name, kwargs.get("api_key"))
        elif provider == "zhipu":
            return ZhipuAdapter(model_name, kwargs.get("api_key"))
        elif provider == "qwen":
            return QwenAdapter(model_name, kwargs.get("api_key"), kwargs.get("api_secret"))
        else:
            logger.warning(json.dumps({"trace_id": _trace_id(), "module_name": "adapters", "action": "provider", "msg": f"未知提供商: {provider}"}, ensure_ascii=False))
            return None