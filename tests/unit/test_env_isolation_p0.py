"""P0 回归：测试**不得**触碰仓库根 `.env`（2026-09-13）

【背景（真实事故形态）】
`tests/unit/test_network_config.py::TestNetworkConfigEncryption::`
`test_no_secure_manager_warning` 调 `NetworkConfigManager.update({'llm': {'api_key': 'sk-test-key'}})`；
它隔离了 `network_config.json`（临时文件）与进程环境变量，**但没有隔离 `.env` 文件本身**，
而 `get_env_config_manager()` 是**懒加载单例且无路径覆盖** ⇒ 写的是**仓库根的真实 `.env`**。
后果：正在运行的服务随即对模型 401、响应退化成兜底文案，
**极易把"答得对"误判为"不达成"**（S9-01 的验证就被这样误导过）；
`.env.backups/` 已有数百次覆盖记录（2026-08-15 事故）⇒ 复发问题。

【本文件的判据（两条缺一不可）】
1. 仓库根 `.env` **逐字节未变**；
2. 隔离目标文件**真的收到了写入** —— 用来证明修法是"重定向真实 I/O"，
   而**不是**用 mock 把写入掩盖掉（若只断言 (1)，一个"把 set 变成 no-op"的
   假修法也能通过，那会把真实文件 I/O 契约一起废掉）。
"""
from __future__ import annotations

import os
from pathlib import Path

from agent.env_config_manager import (
    ENV_FILE_OVERRIDE_VAR,
    EnvConfigManager,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_REPO_ENV = _REPO_ROOT / ".env"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def test_dotenv_target_is_redirected_during_tests():
    """用例期间 `EnvConfigManager` 的目标**不得**是仓库根 `.env`"""
    override = str(os.environ.get(ENV_FILE_OVERRIDE_VAR) or "").strip()
    assert override, (
        f"P0 护栏未生效：`{ENV_FILE_OVERRIDE_VAR}` 未被设置 ——"
        "请检查 tests/conftest.py 的 `_isolate_dotenv_target` autouse fixture")
    target = Path(EnvConfigManager()._env_file).resolve()
    assert target != _REPO_ENV.resolve(), (
        f"EnvConfigManager 的目标仍是仓库根 .env（{target}）—— P0 未修好")


def test_network_config_update_does_not_touch_repo_dotenv(tmp_path, monkeypatch):
    """`NetworkConfigManager.update()` 走真实 I/O，但**不碰仓库 `.env`**

    同时验证"隔离目标真的收到写入"，以防出现"把写入变成 no-op"的假修法。
    """
    from agent.network_config import NetworkConfigManager

    # 产品代码会把新 key 热重载进 os.environ；用 monkeypatch 记录原值以便还原，
    # 避免把 `sk-test-key` 泄漏给后续用例（那正是我们要消灭的污染）。
    monkeypatch.setenv("LLM_API_KEY", os.environ.get("LLM_API_KEY", ""))

    before = _read(_REPO_ENV)
    cfg = tmp_path / "network_config.json"
    cfg.write_text("{}", encoding="utf-8")

    NetworkConfigManager(config_file=str(cfg)).update(
        {"llm": {"api_key": "sk-test-key"}})

    after = _read(_REPO_ENV)
    assert before == after, "P0 复发：测试写到了仓库根 .env（真实凭证被覆盖）"

    isolated = Path(str(os.environ[ENV_FILE_OVERRIDE_VAR]))
    assert isolated.exists(), (
        "隔离目标文件未被创建 —— 说明写入被 mock/no-op 掩盖了，"
        "而不是被重定向；这会连带废掉真实文件 I/O 契约")
    assert "sk-test-key" in _read(isolated), (
        "隔离目标文件未收到写入 —— 同上：这不是重定向，是掩盖")
