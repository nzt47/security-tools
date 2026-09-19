"""MemoryManager 的 LLM 配置来源（.env 优先，config.yaml 兜底）测试

线上问题：`Yunshu._llm` 由 MemoryManager 构建，而它此前**只读 config.yaml**
（模板遗留 provider=openai / model=gpt-4）。结果是同一次部署里两条链路两个模型：
工作台对话走 .env（deepseek），子代理委派/记忆摘要走 gpt-4 ⇒ 真委派必然 400：
``The supported API model names are deepseek-flash, deepseek-v4-pro, but you passed gpt-4``。

本文件锁死"单一来源"：
  1. `.env` 有 LLM_API_KEY ⇒ provider/model/base_url/key 全部取 .env（忽略 config.yaml）；
  2. `.env` 无 key ⇒ 回退 config.yaml:llm（保持既有部署可用的兜底）；
  3. 两者都无 ⇒ 不构造 LLMService（摘要降级），不抛异常。
"""
from __future__ import annotations

import pytest

from memory.memory_manager import _resolve_llm_config


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("LLM_API_KEY", "DEEPSEEK_API_KEY", "LLM_PROVIDER", "LLM_MODEL",
              "LLM_BASE_URL", "DEEPSEEK_BASE_URL"):
        monkeypatch.delenv(k, raising=False)


CONFIG_YAML_LLM = {"llm": {"provider": "openai", "model": "gpt-4",
                           "api_key": "sk-from-yaml-1234567890", "temperature": 0.7}}


class TestEnvFirst:
    def test_env_优先_连_model_与_base_url_一起取(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "DeepSeek")
        monkeypatch.setenv("LLM_API_KEY", "sk-env-" + "a" * 30)
        monkeypatch.setenv("LLM_MODEL", "deepseek-v4-flash")
        monkeypatch.setenv("LLM_BASE_URL", "https://api.deepseek.com/v1")

        conf = _resolve_llm_config(CONFIG_YAML_LLM)
        assert conf["source"] == ".env(LLM_*)"
        assert conf["provider"] == "deepseek"          # 大小写归一
        assert conf["model"] == "deepseek-v4-flash"    # 不再被 config.yaml 的 gpt-4 覆盖
        assert conf["api_key"].startswith("sk-env-")
        assert conf["base_url"] == "https://api.deepseek.com/v1"

    def test_只设_DEEPSEEK_API_KEY_也认(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds-" + "b" * 30)
        conf = _resolve_llm_config({})
        assert conf["source"] == ".env(LLM_*)"
        assert conf["provider"] == "deepseek"
        assert conf["model"] == "deepseek-v4-flash"

    def test_env_缺_model_时给默认而非_gpt4(self, monkeypatch):
        monkeypatch.setenv("LLM_API_KEY", "sk-env-" + "c" * 30)
        conf = _resolve_llm_config(CONFIG_YAML_LLM)
        assert conf["model"] == "deepseek-v4-flash"


class TestYamlFallback:
    def test_env_无_key_时回退_config_yaml(self):
        conf = _resolve_llm_config(CONFIG_YAML_LLM)
        assert conf["source"] == "config.yaml:llm"
        assert conf["provider"] == "openai"
        assert conf["model"] == "gpt-4"
        assert conf["api_key"].startswith("sk-from-yaml")
        assert conf["timeout"] == 30

    def test_两者都无_key_时_api_key_为空(self):
        assert _resolve_llm_config({})["api_key"] == ""

    def test_空配置不抛异常(self):
        assert _resolve_llm_config(None)["api_key"] == ""


class TestMemoryManagerWiring:
    def test_构造时用_env_模型并传入_base_url(self, monkeypatch, tmp_path):
        """端到端接线：MemoryManager 构造出的 LLMService 必须是 .env 的模型 + base_url"""
        monkeypatch.setenv("LLM_PROVIDER", "deepseek")
        monkeypatch.setenv("LLM_API_KEY", "sk-env-" + "d" * 30)
        monkeypatch.setenv("LLM_MODEL", "deepseek-chat")
        monkeypatch.setenv("LLM_BASE_URL", "https://gateway.example.com/v1")

        from memory.memory_manager import MemoryManager

        mgr = MemoryManager(config={"data_dir": str(tmp_path), "llm": CONFIG_YAML_LLM["llm"]})
        llm = mgr._llm_service
        assert llm is not None
        assert llm.model == "deepseek-chat"                 # 不是 gpt-4
        assert llm.provider == "deepseek"
        assert llm._base_url == "https://gateway.example.com/v1"

    def test_env_无_key_且_yaml_无_key_时不构造(self, tmp_path):
        from memory.memory_manager import MemoryManager

        mgr = MemoryManager(config={"data_dir": str(tmp_path), "llm": {}})
        assert mgr._llm_service is None
