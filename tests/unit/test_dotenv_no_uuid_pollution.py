"""回归锁：UUID 形态变量名的**生产源**与生产 .env 现状（W1 / TASK-01）

事故形态：生产 .env 被写入 1801 行
LLM_<UUID>_API_KEY / SEARCH_<UUID>_API_KEY（LLM 923 / SEARCH 878），
每组只有 5 个不同取值（皆为测试占位符）。

【最终防御设计：修源头 + 测试期检查，**不做写入期硬拒绝**】
来源（带行号，agent/network_config.py）：
  :347 / :352  _ensure_config_structure 给缺 id 的实例补 uuid4
  :706         _update_llm_instances   instance_id or str(uuid.uuid4())
  :745         _update_search_instances inst_id or str(uuid.uuid4())
  :1189        add_llm_instance        new_instance.get("id") or str(uuid.uuid4())
  -> :253-272 _key_to_env_var() 把 id 拼成 LLM_{ID}_API_KEY / SEARCH_{ID}_API_KEY

【为什么**不**在 EnvConfigManager.set() 里硬拒绝（实测回归）】"实例 id 是 UUID"
是既有数据模型（agent/data/network_config.json 现存 3 个 search_instances 的 id
全为 UUID；validate_llm_instance 不要求 id；POST /api/llm/instances 不注入 id）。
而 _save_secure 会把异常吞成 error 日志（network_config.py:405-409）、路由仍返回
{"ok": true} => 硬拒绝 = **静默丢失 API Key**。实测复现于
tests/unit/test_instance_key_persistence_ui_path.py（初版守卫下 4 个用例变红）。

故本文件的职责是：① 锁住"生产 .env 不得再出现 UUID 形态行"；
② 锁住**生产者产出**在调用方给出 id 时可读、确定（"来源已堵住"的证据）；
③ 把"set() 不得硬拒绝"这一设计决定钉住，防止有人未读文档就重新加回。
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from agent.env_config_manager import (
    ENV_VAR_NAME_RE,
    EnvConfigManager,
    InvalidEnvVarName,
    is_uuid_shaped_var_name,
    validate_env_var_name,
)
from agent.network_config import NetworkConfigManager

#: 实测污染样本（占位值，非真实密钥），结构取自生产 .env
_UUID_SAMPLES = [
    "LLM_991444D3-CE22-41A7-BDA8-B273D7219192_API_KEY",
    "LLM_83702F54-9377-457E-A7E0-C806DE9F3B7B_API_KEY",
    "SEARCH_0BED263C-73C6-4C13-BDC0-1A844B76C00A_API_KEY",
    "SEARCH_FE62613D-F331-40B3-8B30-A66013772804_API_KEY",
    # 大小写混用同样必须识别（污染里两种都有）
    "llm_991444d3-ce22-41a7-bda8-b273d7219192_api_key",
]

#: 必须继续放行的合法名（历史契约，改不得）
_LEGAL_NAMES = [
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "SEARCH_TAVILY_API_KEY",
    "MCP_service_TOKEN",
    "HF_HUB_OFFLINE",
    # tests/unit/test_env_hot_reload.py:258 断言过的连字符名（不能误伤）
    "LLM_TEST-MULTI-NEW_API_KEY",
]

_TEST_PREFIXES = ("LLM_", "SEARCH_", "MCP_", "ERROR_REPORTING_")


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


# ── 1. 检测函数契约（供测试/巡检使用） ──────────────────────────────

@pytest.mark.parametrize("name", _UUID_SAMPLES)
def test_uuid_shaped_name_is_detected(name):
    assert is_uuid_shaped_var_name(name) is True
    with pytest.raises(InvalidEnvVarName):
        validate_env_var_name(name)


@pytest.mark.parametrize("name", _LEGAL_NAMES)
def test_legal_names_still_accepted(name):
    """合法名不得被误伤（防"一刀切禁连字符"式过度修复）"""
    assert is_uuid_shaped_var_name(name) is False
    assert validate_env_var_name(name) == name


@pytest.mark.parametrize("name", ["", "   ", "1BAD", "HAS SPACE", "X=Y", None])
def test_malformed_names_rejected(name):
    with pytest.raises(InvalidEnvVarName):
        validate_env_var_name(name)


def test_set_does_not_hard_reject_uuid_shaped_name(tmp_path):
    """**设计锁**：set() 不得拒绝 UUID 形态名（否则 = 静默丢 Key）

    初版守卫在此抛 InvalidEnvVarName，配合 _save_secure 吞异常
    （network_config.py:405-409）会造成"路由返回 ok:true 但 Key 没落盘"。
    本用例把"不做写入期硬拒绝"这一决定钉死；若有人重新加回，这里会红。
    """
    env_file = tmp_path / ".env"
    env_file.write_text("LLM_API_KEY=placeholder\n", encoding="utf-8")
    mgr = EnvConfigManager(env_file_path=str(env_file))

    uuid_name = "SEARCH_7BE25FD0-A636-46B4-9AB2-A116E09C71A9_API_KEY"
    mgr.set(uuid_name, "sk-legacy-uuid-key")   # 不得抛异常

    content = env_file.read_text(encoding="utf-8")
    assert f"{uuid_name}=sk-legacy-uuid-key" in content, (
        "set() 拒写或漏写了 UUID 形态名 —— 既有数据模型下这等于静默丢 Key")
    assert os.getenv(uuid_name) == "sk-legacy-uuid-key"
    os.environ.pop(uuid_name, None)


# ── 2. 生产者产出：给了 id 就必须可读、确定（"来源已堵住"的证据） ──

def test_producer_readable_name_for_explicit_llm_id(ncm_with_tmp_dotenv):
    mgr, env_file = ncm_with_tmp_dotenv
    result = mgr.add_llm_instance({
        "id": "readable-1", "name": "Readable", "provider": "openai",
        "api_endpoint": "https://api.example.com/v1", "api_key": "sk-readable",
    })
    assert result["id"] == "readable-1", "显式 id 被 uuid4 覆盖了（生产者回归）"
    name = "LLM_READABLE-1_API_KEY"
    assert f"{name}=sk-readable" in env_file.read_text(encoding="utf-8")
    assert is_uuid_shaped_var_name(name) is False


def test_producer_readable_name_for_explicit_search_id(ncm_with_tmp_dotenv):
    mgr, env_file = ncm_with_tmp_dotenv
    mgr._update_search_instances([{
        "id": "tavily-1", "name": "Tavily", "engine_type": "custom",
        "api_key": "sk-search-readable",
    }])
    name = "SEARCH_TAVILY-1_API_KEY"
    assert f"{name}=sk-search-readable" in env_file.read_text(encoding="utf-8")
    assert is_uuid_shaped_var_name(name) is False


# ── 3. 生产 .env 现状锁（只读，不修改） ─────────────────────────────

def test_repo_dotenv_has_no_uuid_pollution():
    """仓库根 .env 不得含 UUID 形态变量行（与 1801 行清理互为回归）"""
    repo_env = Path(__file__).resolve().parents[2] / ".env"
    if not repo_env.exists():  # pragma: no cover - 无 .env 的环境（如纯 CI 检出）
        pytest.skip("仓库根无 .env（未配置环境）")
    uuid_line = re.compile(
        r"^(LLM|SEARCH|MCP)_[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}"
        r"-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}_")
    text = repo_env.read_text(encoding="utf-8", errors="replace")
    offenders = [i for i, l in enumerate(text.splitlines(), 1) if uuid_line.match(l.strip())]
    assert not offenders, (
        f"仓库根 .env 出现 UUID 形态变量行（行号 {offenders[:10]}，共 {len(offenders)} 行）——"
        "说明污染源被重新引入（见 env_archive/20260921_test_pollution.txt 清理记录）"
    )
    # 分层设计自查：字符集正则按历史契约允许连字符，因此它**会**放行 UUID 名；
    # 识别 UUID 靠第二层 is_uuid_shaped_var_name()。
    uuid_name = "LLM_991444D3-CE22-41A7-BDA8-B273D7219192_API_KEY"
    assert ENV_VAR_NAME_RE.match(uuid_name), "字符集正则的既有契约变了（允许连字符）"
    assert is_uuid_shaped_var_name(uuid_name) is True
