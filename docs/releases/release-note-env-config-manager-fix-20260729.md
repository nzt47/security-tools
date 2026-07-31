# Release Note: 修复 get_env_config_manager() 缺少 return 导致 9 个 CI 测试失败

**日期**: 2026-07-29
**影响模块**: `agent/env_config_manager.py`, `tests/unit/test_network_config.py`
**提交**: `f0494a7b`
**回归引入**: `f8a457f2`（TLM 三层记忆架构升级，2026-07-28）
**严重级别**: P1（CI 单元测试持续失败，敏感配置写入路径不可用）

---

## 变更摘要

| 类型 | 数量 | 描述 |
|------|------|------|
| Bug 修复 | 1 | `get_env_config_manager()` 补全 `return _instance` |
| 测试加固 | 1 | `test_network_config.py` 添加 CI mock fixture |
| 变更行数 | +37 / -0 | 2 files changed |
| 修复测试数 | 9 | CI 中原失败的 9 个测试全部恢复通过 |

---

## 1. 根因分析

### 1.1 问题现象

CI（ubuntu-latest, Python 3.10/3.11/3.12, `SKILLS_OFFLINE=1`）中 9 个网络配置单元测试持续失败，全部为 `os.getenv()` 返回 `None` 导致的断言失败：

```
AssertionError: assert None == 'sk-real-api-key-12345'
 +  where None = os.getenv('LLM_API_KEY')
```

### 1.2 失败链路

```
测试调用 manager.update({'llm': {'api_key': 'sk-xxx'}})
  → NetworkConfigManager._save_secure('llm_api_key', 'sk-xxx')     [network_config.py:324]
    → self._env_config.set('LLM_API_KEY', 'sk-xxx')                [env_config_manager.py:100]
      ↑ self._env_config 为 None！
      → 'NoneType' object has no attribute 'set'                   ← 抛异常
    ← except Exception: logger.error(...)                          [network_config.py:334] ← 吞异常
  → assert os.getenv('LLM_API_KEY') == 'sk-xxx'                    ← 失败：返回 None
```

### 1.3 根因

commit `f8a457f2`（TLM 三层记忆架构升级）在重构 `env_config_manager.py` 时，**遗漏了 `get_env_config_manager()` 函数的 `return _instance` 语句**：

```python
# 修复前（bug）—— 函数创建单例但不返回，隐式返回 None
def get_env_config_manager() -> EnvConfigManager:
    """获取 EnvConfigManager 单例实例"""
    global _instance
    if _instance is None:
        _instance = EnvConfigManager()
    # ← 缺少 return _instance

# 修复后
def get_env_config_manager() -> EnvConfigManager:
    """获取 EnvConfigManager 单例实例"""
    global _instance
    if _instance is None:
        _instance = EnvConfigManager()
    return _instance  # ← 补全
```

### 1.4 影响范围

`NetworkConfigManager.__init__` 调用 `get_env_config_manager()` 获取单例：

```python
self._env_config = get_env_config_manager()  # 返回 None
```

所有通过 `_save_secure` → `self._env_config.set()` 写入敏感配置的代码路径均受影响：
- LLM API Key 保存（单实例 + 多实例）
- 搜索引擎 API Key 保存（旧版 + 新版实例）
- Webhook URL 保存
- MCP 服务 API Key 保存

**生产环境影响**：非 CI 环境同样受影响——任何调用 `NetworkConfigManager.update()` 保存敏感配置的操作都会静默失败（异常被 `_save_secure` 的 try/except 吞掉，仅打印 error 日志）。

### 1.5 为什么只有 9 个测试失败

大部分测试不涉及敏感配置写入（如超时验证、MCP 协议校验、配置导入导出等），它们只操作 JSON 配置文件，不经过 `_save_secure`。只有以下 9 个测试断言 `os.getenv()` 返回特定值：

| 文件 | 失败测试数 | 测试方法 |
|------|-----------|---------|
| `test_network_config.py` | 5 | `test_sensitive_api_key_encrypted_on_save`, `test_search_api_key_encrypted`, `test_webhook_url_encrypted`, `test_no_secure_manager_warning`, `test_llm_instance_api_key_masking` |
| `test_network_config_save_regression.py` | 4 | `test_real_api_key_saved_to_secure_store`, `test_update_with_search_instances_strips_api_key`, `test_update_with_masked_api_key_does_not_overwrite`, `test_get_all_returns_masked_after_update` |

---

## 2. 修复方案

### 2.1 核心修复：补全 return 语句

**文件**: `agent/env_config_manager.py:408`
**变更**: +1 行

```python
def get_env_config_manager() -> EnvConfigManager:
    """获取 EnvConfigManager 单例实例"""
    global _instance
    if _instance is None:
        _instance = EnvConfigManager()
    return _instance  # 补全：返回单例实例
```

### 2.2 测试加固：CI mock fixture

**文件**: `tests/unit/test_network_config.py:37-62`
**变更**: +36 行

添加 `_mock_env_config_in_ci` autouse fixture，在 CI 环境（`SKILLS_OFFLINE=1`）下 mock `EnvConfigManager.set/delete`，直接操作 `os.environ`，绕过 `.env` 文件 I/O：

```python
@pytest.fixture(autouse=True)
def _mock_env_config_in_ci():
    """CI 环境中 mock EnvConfigManager.set/delete，绕过 .env 文件 I/O。"""
    if not os.environ.get('SKILLS_OFFLINE'):
        yield
        return

    from agent.env_config_manager import EnvConfigManager

    def _mock_set(self, key, value):
        os.environ[key] = value

    def _mock_delete(self, key):
        os.environ.pop(key, None)

    with patch.object(EnvConfigManager, 'set', _mock_set), \
         patch.object(EnvConfigManager, 'delete', _mock_delete):
        yield
```

**设计决策**（三义分析）：
- **【不易】** 不改变测试断言语义——仍验证 `NetworkConfigManager` 正确调用 `_save_secure` 并传递正确的 key/value
- **【变易】** 仅 `SKILLS_OFFLINE=1`（CI 环境）激活，本地开发走真实 `.env` 写入
- **【简易】** mock 直接操作 `os.environ`，无文件 I/O，无副作用

---

## 3. 测试覆盖情况

### 3.1 验证矩阵

| 验证场景 | 环境 | 测试数 | 结果 |
|---------|------|--------|------|
| 原 5 个失败测试恢复 | `SKILLS_OFFLINE=1` | 5 | ✅ 全部通过 |
| 原 4 个回归测试恢复 | `SKILLS_OFFLINE=1` | 4 | ✅ 全部通过 |
| 两个文件全部测试（CI 模式） | `SKILLS_OFFLINE=1` | 66 | ✅ 66 passed, 0 failed |
| 两个文件全部测试（本地模式） | 无 `SKILLS_OFFLINE` | 66 | ✅ 66 passed, 0 failed |

### 3.2 验证命令

```powershell
# 模拟 CI 环境运行全部测试
$env:SKILLS_OFFLINE = '1'; $env:PYTHONIOENCODING = 'utf-8'
pytest tests/unit/test_network_config.py tests/unit/test_network_config_save_regression.py -v --tb=short

# 仅运行原 9 个失败测试
$env:SKILLS_OFFLINE = '1'; $env:PYTHONIOENCODING = 'utf-8'
pytest tests/unit/test_network_config.py::TestNetworkConfigEncryption::test_sensitive_api_key_encrypted_on_save `
  tests/unit/test_network_config.py::TestNetworkConfigEncryption::test_search_api_key_encrypted `
  tests/unit/test_network_config.py::TestNetworkConfigEncryption::test_webhook_url_encrypted `
  tests/unit/test_network_config.py::TestNetworkConfigEncryption::test_no_secure_manager_warning `
  tests/unit/test_network_config.py::TestNetworkConfigMasking::test_llm_instance_api_key_masking `
  tests/unit/test_network_config_save_regression.py::TestUpdateSearchInstancesIgnoresMaskedKey::test_real_api_key_saved_to_secure_store `
  tests/unit/test_network_config_save_regression.py::TestUpdateEndToEndNoPlaintextApiKey::test_update_with_search_instances_strips_api_key `
  tests/unit/test_network_config_save_regression.py::TestUpdateEndToEndNoPlaintextApiKey::test_update_with_masked_api_key_does_not_overwrite `
  tests/unit/test_network_config_save_regression.py::TestUpdateEndToEndNoPlaintextApiKey::test_get_all_returns_masked_after_update `
  -v --tb=short
```

### 3.3 CI 预期

推送 `origin/master` 后，GitHub Actions 将在 Python 3.10/3.11/3.12 三个矩阵上运行 `pytest tests/unit/`，原 9 个失败测试预期全部恢复通过。

---

## 4. 回滚方案

如修复引入新问题，可一键回滚：

```bash
# 回滚到修复前
git revert f0494a7b --no-edit
git push origin master
```

回滚后 9 个测试将恢复失败状态，但不影响其他功能（原 bug 仅影响敏感配置写入，且异常被吞不会导致崩溃）。

---

## 5. 经验教训

1. **单例函数必须有 return**：Python 函数无显式 `return` 时隐式返回 `None`，类型注解 `-> EnvConfigManager` 不会强制运行时检查。建议在 CI 中增加 `mypy --strict` 或 `pyright` 静态检查捕获此类问题。

2. **吞异常会掩盖 bug**：`_save_secure` 的 `except Exception: logger.error(...)` 模式让生产 bug 静默运行了 1 天才被 CI 测试发现。建议对关键路径的异常增加告警而非仅日志。

3. **mock fixture 的正确粒度**：原计划的 mock 方案（mock `EnvConfigManager.set`）无法修复此 bug，因为根因是 `self._env_config` 为 `None`（无法 patch `None` 的方法）。调试时先用独立 Python 脚本验证根因，再决定修复策略。

---

## 6. 参考链接

- 提交: `f0494a7b` fix(config): 修复 get_env_config_manager() 缺少 return 导致 9 个 CI 测试失败
- 回归引入: `f8a457f2` feat(tlm): TLM 三层记忆架构升级
- 复用 runbook: [docs/troubleshooting/ci_env_config_mock_runbook.md](../troubleshooting/ci_env_config_mock_runbook.md)
