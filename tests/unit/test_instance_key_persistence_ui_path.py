"""UI/API 路径：实例 API Key 必须真正落盘 .env（W1 / TASK-01 回归锁）

【为什么需要这个文件】曾有一版守卫在 EnvConfigManager.set() 里**硬拒绝** UUID 形态
变量名，而 network_config._save_secure（:405-409）把异常吞成一条 error 日志、
路由（agent/server_routes/routes_config.py:270）仍返回 {"ok": true} ⇒
**用户看到"添加成功"，Key 却没进 .env**（静默丢失）。

而"UUID 是合法实例 id"是既有数据模型（agent/data/network_config.json 现存 3 个
search_instances 的 id 全为 UUID），且 validate_llm_instance 不要求 id
（network_config.py:1616-1619）、POST /api/llm/instances 也不注入 id
（routes_config.py:263-267）。

本文件的断言就是那条契约：**只要调用返回成功，Key 就必须能在 .env 里读到**。
全部使用 tmp 目录的独立 dotenv 文件，绝不触碰仓库根 .env。
"""

from __future__ import annotations

import os
import re
import uuid
from unittest.mock import patch

import pytest

from agent.env_config_manager import EnvConfigManager
from agent.network_config import NetworkConfigManager

_TEST_PREFIXES = ("LLM_", "SEARCH_", "MCP_", "ERROR_REPORTING_")

#: 与生产现存实例同形（agent/data/network_config.json 的 3 个 search_instances）
_UUID_ID = "7be25fd0-a636-46b4-9ab2-a116e09c71a9"


@pytest.fixture
def ncm_with_tmp_dotenv(tmp_path):
    """干净的 NetworkConfigManager + 独立 tmp dotenv（不碰仓库 .env）"""
    env_file = tmp_path / ".env"
    env_file.touch()
    env_mgr = EnvConfigManager(env_file_path=str(env_file))

    saved = {k: os.environ.get(k) for k in list(os.environ) if k.startswith(_TEST_PREFIXES)}
    with patch("agent.network_config.get_env_config_manager", return_value=env_mgr):
        mgr = NetworkConfigManager(config_file=str(tmp_path / "network_config.json"))
        try:
            yield mgr, env_file
        finally:
            for k in [k for k in os.environ if k.startswith(_TEST_PREFIXES)]:
                os.environ.pop(k, None)
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v


def test_ui_add_llm_instance_without_id_persists_key(ncm_with_tmp_dotenv):
    """UI 新建 LLM 实例（**不传 id**，合法调用）⇒ Key 必须落盘

    这就是"ok:true 但没保存"的回归锁。
    """
    mgr, env_file = ncm_with_tmp_dotenv

    result = mgr.add_llm_instance({
        "name": "UI 新建实例",
        "provider": "openai",
        "api_endpoint": "https://api.example.com/v1",
        "api_key": "sk-ui-no-id-key",
    })

    inst_id = result["id"]
    env_var = f"LLM_{inst_id.upper()}_API_KEY"
    content = env_file.read_text(encoding="utf-8")

    assert "sk-ui-no-id-key" in content, (
        "UI 新建实例（不传 id）的 API Key 没有写进 .env —— 静默丢失")
    assert f"{env_var}=sk-ui-no-id-key" in content
    assert os.getenv(env_var) == "sk-ui-no-id-key", "热重载未生效"


def test_ui_add_search_instance_without_id_persists_key(ncm_with_tmp_dotenv):
    """UI 新建搜索实例（不传 id）⇒ Key 必须落盘"""
    mgr, env_file = ncm_with_tmp_dotenv

    mgr._update_search_instances([{
        "name": "UI 新建搜索",
        "engine_type": "custom",
        "api_key": "sk-ui-search-key",
        "api_endpoint": "https://search.example.com",
    }])

    inst_id = mgr._cache["search_instances"][0]["id"]
    env_var = f"SEARCH_{inst_id.upper()}_API_KEY"
    content = env_file.read_text(encoding="utf-8")

    assert f"{env_var}=sk-ui-search-key" in content, (
        "UI 新建搜索实例的 API Key 没有写进 .env —— 静默丢失")
    assert os.getenv(env_var) == "sk-ui-search-key"


def test_existing_uuid_id_instance_update_persists_key(ncm_with_tmp_dotenv):
    """**不误伤存量**：id 本就是 UUID 的既有实例，更新其 Key 必须成功落盘

    对应 agent/data/network_config.json 现存的 3 个 search_instances。
    """
    mgr, env_file = ncm_with_tmp_dotenv

    mgr._update_search_instances([{
        "id": _UUID_ID,
        "name": "DuckDuckGo",
        "engine_type": "custom",
        "api_key": "sk-existing-uuid-key",
    }])

    env_var = f"SEARCH_{_UUID_ID.upper()}_API_KEY"
    content = env_file.read_text(encoding="utf-8")

    assert f"{env_var}=sk-existing-uuid-key" in content, (
        "既有 UUID id 实例更新 Key 失败 —— 存量数据被误伤")
    assert os.getenv(env_var) == "sk-existing-uuid-key"


def test_add_llm_instance_honors_explicit_readable_id(ncm_with_tmp_dotenv):
    """生产者侧锁：显式给出的可读 id 不得被 uuid4 覆盖（确定性 id 的前提）

    改动前 add_llm_instance 无条件 new_instance["id"] = str(uuid.uuid4())，
    调用方给的可读 id 被丢弃 ⇒ 测试无法产生确定性环境变量名。
    """
    mgr, env_file = ncm_with_tmp_dotenv

    result = mgr.add_llm_instance({
        "id": "readable-1",
        "name": "Readable",
        "provider": "openai",
        "api_endpoint": "https://api.example.com/v1",
        "api_key": "sk-readable-key",
    })

    assert result["id"] == "readable-1", "显式 id 被覆盖了"
    content = env_file.read_text(encoding="utf-8")
    assert "LLM_READABLE-1_API_KEY=sk-readable-key" in content
    # 该名字**不含** UUID 段（生产者产出的名字可读、确定）
    from agent.env_config_manager import is_uuid_shaped_var_name
    assert is_uuid_shaped_var_name("LLM_READABLE-1_API_KEY") is False


def test_key_persisted_after_masked_value_does_not_overwrite(ncm_with_tmp_dotenv):
    """脱敏值（***xxxx）不得覆盖已落盘的真实 Key（既有契约，UI 更新路径）"""
    mgr, env_file = ncm_with_tmp_dotenv

    mgr._update_search_instances([{
        "id": _UUID_ID, "name": "DuckDuckGo",
        "engine_type": "custom", "api_key": "sk-original-real",
    }])
    mgr._update_search_instances([{
        "id": _UUID_ID, "name": "DuckDuckGo", "api_key": "***real",
    }])

    env_var = f"SEARCH_{_UUID_ID.upper()}_API_KEY"
    assert os.getenv(env_var) == "sk-original-real", "脱敏值覆盖了真实 Key"
    assert f"{env_var}=sk-original-real" in env_file.read_text(encoding="utf-8")


def test_no_route_visible_instance_id_is_random_for_ui(ncm_with_tmp_dotenv):
    """记录既有数据模型事实：不传 id 时确实生成 UUID（本测试显式固化该行为）

    若将来改为"从 name 派生确定性 id"，本用例会失败并提醒同步更新——
    这是有意的：id 生成口径的变化必须被看见。
    """
    mgr, _ = ncm_with_tmp_dotenv
    result = mgr.add_llm_instance({
        "name": "UUIDShaped",
        "provider": "openai",
        "api_endpoint": "https://api.example.com/v1",
        "api_key": "sk-x",
    })
    assert re.fullmatch(r"[0-9a-fA-F-]{36}", str(result["id"])), (
        "不传 id 时的 id 生成口径已变化，请同步更新本文件的断言")
    assert uuid.UUID(result["id"])  # 形态确为 UUID
