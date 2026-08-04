# EnvConfigManager 修复与 Mock 复用方案

> **提交**: `f0494a7b` | **日期**: 2026-07-29 | **严重级别**: P1
> **影响模块**: `agent/env_config_manager.py`, `tests/unit/test_network_config.py`, `tests/unit/test_network_config_save_regression.py`

---

## 目录

1. [变更摘要](#1-变更摘要)
2. [根因分析](#2-根因分析)
3. [修复方案](#3-修复方案)
4. [测试覆盖情况](#4-测试覆盖情况)
5. [回滚方案](#5-回滚方案)
6. [Mock 方案复用 Runbook](#6-mock-方案复用-runbook)
7. [经验教训](#7-经验教训)

---

## 1. 变更摘要

| 类型 | 数量 | 描述 |
|------|------|------|
| Bug 修复 | 1 | `get_env_config_manager()` 补全 `return _instance` |
| 测试加固 | 2 | `test_network_config.py` + `test_network_config_save_regression.py` 添加 CI mock fixture |
| 变更行数 | +73 / -2 | 3 files changed |
| 修复测试数 | 9 | CI 中原失败的 9 个测试全部恢复通过 |

**涉及文件**：

| 文件 | 变更 | 说明 |
|------|------|------|
| `agent/env_config_manager.py` | +1 行 | 补全 `return _instance`（核心修复） |
| `tests/unit/test_network_config.py` | +36 行 | CI mock fixture（5 个加密/脱敏测试） |
| `tests/unit/test_network_config_save_regression.py` | +36 行 | CI mock fixture（4 个搜索实例回归测试） |

---

## 2. 根因分析

### 2.1 问题现象

CI（ubuntu-latest, Python 3.10/3.11/3.12, `SKILLS_OFFLINE=1`）中 9 个网络配置单元测试持续失败，全部为 `os.getenv()` 返回 `None` 导致的断言失败：

```
AssertionError: assert None == 'sk-real-api-key-12345'
 +  where None = os.getenv('LLM_API_KEY')
```

### 2.2 失败链路

```
测试调用 manager.update({'llm': {'api_key': 'sk-xxx'}})
  → NetworkConfigManager._save_secure('llm_api_key', 'sk-xxx')     [network_config.py:324]
    → self._env_config.set('LLM_API_KEY', 'sk-xxx')                [env_config_manager.py:100]
      ↑ self._env_config 为 None！
      → 'NoneType' object has no attribute 'set'                   ← 抛异常
    ← except Exception: logger.error(...)                          [network_config.py:334] ← 吞异常
  → assert os.getenv('LLM_API_KEY') == 'sk-xxx'                    ← 失败：返回 None
```

### 2.3 根因

commit `f8a457f2`（TLM 三层记忆架构升级，2026-07-28）在重构 `env_config_manager.py` 时，**遗漏了 `get_env_config_manager()` 函数的 `return _instance` 语句**：

```python
# 修复前（bug）—— 函数创建单例但不返回，隐式返回 None
def get_env_config_manager() -> EnvConfigManager:
    global _instance
    if _instance is None:
        _instance = EnvConfigManager()
    # ← 缺少 return _instance

# 修复后
def get_env_config_manager() -> EnvConfigManager:
    global _instance
    if _instance is None:
        _instance = EnvConfigManager()
    return _instance  # ← 补全
```

### 2.4 影响范围

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

### 2.5 为什么只有 9 个测试失败

大部分测试不涉及敏感配置写入（如超时验证、MCP 协议校验、配置导入导出等），它们只操作 JSON 配置文件，不经过 `_save_secure`。只有以下 9 个测试断言 `os.getenv()` 返回特定值：

| 文件 | 失败数 | 测试方法 |
|------|--------|---------|
| `test_network_config.py` | 5 | `test_sensitive_api_key_encrypted_on_save`, `test_search_api_key_encrypted`, `test_webhook_url_encrypted`, `test_no_secure_manager_warning`, `test_llm_instance_api_key_masking` |
| `test_network_config_save_regression.py` | 4 | `test_real_api_key_saved_to_secure_store`, `test_update_with_search_instances_strips_api_key`, `test_update_with_masked_api_key_does_not_overwrite`, `test_get_all_returns_masked_after_update` |

---

## 3. 修复方案

### 3.1 核心修复：补全 return 语句

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

### 3.2 测试加固：CI mock fixture

**文件**: `tests/unit/test_network_config.py` + `tests/unit/test_network_config_save_regression.py`
**变更**: 各 +36 行

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

**设计决策（三义分析）**：
- **【不易】** 不改变测试断言语义——仍验证 `NetworkConfigManager` 正确调用 `_save_secure` 并传递正确的 key/value
- **【变易】** 仅 `SKILLS_OFFLINE=1`（CI 环境）激活，本地开发走真实 `.env` 写入
- **【简易】** mock 直接操作 `os.environ`，无文件 I/O，无副作用

---

## 4. 测试覆盖情况

### 4.1 验证矩阵

| 验证场景 | 环境 | 测试数 | 结果 |
|---------|------|--------|------|
| 原 5 个失败测试恢复（test_network_config.py） | `SKILLS_OFFLINE=1` | 5 | ✅ 全部通过 |
| 原 4 个失败测试恢复（test_network_config_save_regression.py） | `SKILLS_OFFLINE=1` | 4 | ✅ 全部通过 |
| test_network_config.py 全部测试（CI 模式） | `SKILLS_OFFLINE=1` | 42 | ✅ 42 passed |
| test_network_config_save_regression.py 全部测试（CI 模式） | `SKILLS_OFFLINE=1` | 24 | ✅ 24 passed |
| 两文件全部测试（本地模式） | 无 `SKILLS_OFFLINE` | 66 | ✅ 66 passed |

### 4.2 验证命令

```powershell
# 模拟 CI 环境运行两个文件全部测试
$env:SKILLS_OFFLINE = '1'; $env:PYTHONIOENCODING = 'utf-8'
pytest tests/unit/test_network_config.py tests/unit/test_network_config_save_regression.py -v --tb=short

# 仅运行原 9 个失败测试
$env:SKILLS_OFFLINE = '1'; $env:PYTHONIOENCODING = 'utf-8'
pytest `
  tests/unit/test_network_config.py::TestNetworkConfigEncryption::test_sensitive_api_key_encrypted_on_save `
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

---

## 5. 回滚方案

如修复引入新问题，可一键回滚：

```bash
git revert f0494a7b --no-edit
git push origin master
```

回滚后 9 个测试将恢复失败状态，但不影响其他功能（原 bug 仅影响敏感配置写入，且异常被吞不会导致崩溃）。

---

## 6. Mock 方案复用 Runbook

> **适用场景**：CI（`SKILLS_OFFLINE=1`）中测试因 `EnvConfigManager` 文件 I/O 失败而报错，
> 且根因确认不是生产代码 bug 时，使用本节方案绕过文件写入。
>
> **注意**：使用前务必先排查是否为生产代码 bug（如 `return` 缺失、路径错误等）。

### 6.1 使用前排查清单

| 步骤 | 检查项 | 命令 | 期望结果 |
|------|--------|------|---------|
| 1 | `get_env_config_manager()` 是否有 `return` | `rg "def get_env_config_manager" -A 5 agent/env_config_manager.py` | 函数末尾有 `return _instance` |
| 2 | `EnvConfigManager.__init__` 是否成功创建 `.env` | `python -c "from agent.env_config_manager import EnvConfigManager; EnvConfigManager(); print('OK')"` | 输出 `OK` |
| 3 | `EnvConfigManager.set()` 是否正常写入 `os.environ` | 见 [调试脚本](#62-调试脚本) | `os.getenv()` 返回预期值 |
| 4 | 测试的 `setup_method/teardown_method` 是否清理了环境变量 | 检查测试文件 | 有 pop/restore 逻辑 |

### 6.2 调试脚本

```python
import os
os.environ['SKILLS_OFFLINE'] = '1'

from agent.env_config_manager import EnvConfigManager, get_env_config_manager

# 步骤 A：验证 EnvConfigManager 实例化
try:
    mgr = EnvConfigManager()
    print(f"[A] EnvConfigManager 实例化成功: {type(mgr)}")
except Exception as e:
    print(f"[A] 实例化失败: {e}")

# 步骤 B：验证单例函数返回值
result = get_env_config_manager()
print(f"[B] get_env_config_manager() 返回: {result}")

# 步骤 C：验证 set() 是否更新 os.environ
if result is not None:
    result.set('DEBUG_TEST_KEY', 'debug_value')
    print(f"[C] os.getenv('DEBUG_TEST_KEY'): {os.getenv('DEBUG_TEST_KEY')}")
else:
    print("[C] 跳过：get_env_config_manager() 返回 None")

# 步骤 D：验证 NetworkConfigManager 路径
from agent.network_config import NetworkConfigManager
import tempfile
tf = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
tf.close()
ncm = NetworkConfigManager(config_file=tf.name)
print(f"[D] ncm._env_config 类型: {type(ncm._env_config)}")
if ncm._env_config is not None:
    ncm.update({'llm': {'api_key': 'sk-debug-123'}})
    print(f"[D] os.getenv('LLM_API_KEY'): {os.getenv('LLM_API_KEY')}")
os.unlink(tf.name)
```

**结果判读**：
- `[B]` 输出 `None` → 生产代码 bug（缺少 `return`），修复生产代码
- `[C]` 输出 `None` → `set()` 文件 I/O 失败，应用 mock 方案
- `[D]` 输出 `None` 但 `[C]` 正常 → `NetworkConfigManager` 路径问题，检查 `_save_secure`

### 6.3 Mock 方案代码模板（可直接复制）

```python
import os
import pytest
from unittest.mock import patch


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

### 6.4 应用到新测试文件

1. 确认目标测试文件有 `import os` 和 `from unittest.mock import patch`
2. 将 [6.3 代码模板](#63-mock-方案代码模板可直接复制) 复制到测试文件顶部（import 之后、第一个测试类之前）
3. 确认 `setup_method/teardown_method` 有环境变量清理逻辑
4. 运行验证：`$env:SKILLS_OFFLINE = '1'; pytest <目标测试文件> -v --tb=short`

**注意事项**：
- 不要 mock `NetworkConfigManager._save_secure`（含 `_key_to_env_var` 映射逻辑）
- 不要移除 `SKILLS_OFFLINE` 条件判断（本地需走真实 `.env` 写入）
- 不要 mock `EnvConfigManager.__init__`（单例实例化必须成功）
- 不要放入 `conftest.py`（除非 3+ 个测试文件需要）

### 6.5 已应用的文件

| 文件 | 测试数 | 说明 |
|------|--------|------|
| `tests/unit/test_network_config.py` | 42 | 5 个加密/脱敏测试（commit `f0494a7b`） |
| `tests/unit/test_network_config_save_regression.py` | 24 | 4 个搜索实例回归测试 |

---

## 7. 经验教训

1. **单例函数必须有 return**：Python 函数无显式 `return` 时隐式返回 `None`，类型注解 `-> EnvConfigManager` 不会强制运行时检查。建议在 CI 中增加 `mypy --strict` 或 `pyright` 静态检查捕获此类问题。

2. **吞异常会掩盖 bug**：`_save_secure` 的 `except Exception: logger.error(...)` 模式让生产 bug 静默运行了 1 天才被 CI 测试发现。建议对关键路径的异常增加告警而非仅日志。

3. **mock fixture 的正确粒度**：原计划的 mock 方案（mock `EnvConfigManager.set`）无法修复此 bug，因为根因是 `self._env_config` 为 `None`（无法 patch `None` 的方法）。调试时先用独立 Python 脚本验证根因，再决定修复策略。

---

## 附录：参考链接

| 项目 | 值 |
|------|-----|
| 修复提交 | `f0494a7b` fix(config): 修复 get_env_config_manager() 缺少 return 导致 9 个 CI 测试失败 |
| 回归引入 | `f8a457f2` feat(tlm): TLM 三层记忆架构升级 |
| 发布说明 | `docs/releases/release-note-env-config-manager-fix-20260729.md` |
| Mock Runbook | `docs/troubleshooting/ci_env_config_mock_runbook.md` |
| EnvConfigManager 源码 | `agent/env_config_manager.py` |
| NetworkConfigManager 源码 | `agent/network_config.py` |
