# Runbook: CI 环境中 Mock EnvConfigManager 绕过 .env 文件 I/O

> **适用场景**：CI（`SKILLS_OFFLINE=1`）中测试因 `EnvConfigManager` 文件 I/O 失败而报错，
> 且根因确认不是生产代码 bug 时，使用本文档的 mock 方案绕过文件写入。
>
> **注意**：使用前务必先排查是否为生产代码 bug（如 `return` 缺失、路径错误等）。
> Mock 是防御性手段，不能替代根因修复。详见 [排查清单](#1-使用前排查清单)。

---

## 1. 使用前排查清单

在应用 mock 之前，按顺序排查以下根因：

| 步骤 | 检查项 | 命令 | 期望结果 |
|------|--------|------|---------|
| 1 | `get_env_config_manager()` 是否有 `return` | `rg "def get_env_config_manager" -A 5 agent/env_config_manager.py` | 函数末尾有 `return _instance` |
| 2 | `EnvConfigManager.__init__` 是否成功创建 `.env` | 手动 `python -c "from agent.env_config_manager import EnvConfigManager; EnvConfigManager(); print('OK')"` | 输出 `OK`，无异常 |
| 3 | `EnvConfigManager.set()` 是否正常写入 `os.environ` | 见 [调试脚本](#2-调试脚本) | `os.getenv()` 返回预期值 |
| 4 | 测试的 `setup_method/teardown_method` 是否清理了环境变量 | 检查测试文件 | 有 pop/restore 逻辑 |

**如果步骤 1 发现缺少 `return`**：这是生产代码 bug，直接补全 `return _instance`，无需 mock。

---

## 2. 调试脚本

用以下脚本快速定位问题：

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
print(f"[B] get_env_config_manager() 返回: {result}")  # 应为 EnvConfigManager 实例，不是 None

# 步骤 C：验证 set() 是否更新 os.environ
if result is not None:
    result.set('DEBUG_TEST_KEY', 'debug_value')
    print(f"[C] os.getenv('DEBUG_TEST_KEY'): {os.getenv('DEBUG_TEST_KEY')}")  # 应为 'debug_value'
else:
    print("[C] 跳过：get_env_config_manager() 返回 None")

# 步骤 D：验证 NetworkConfigManager 路径
from agent.network_config import NetworkConfigManager
import tempfile
tf = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
tf.close()
ncm = NetworkConfigManager(config_file=tf.name)
print(f"[D] ncm._env_config 类型: {type(ncm._env_config)}")  # 应为 EnvConfigManager，不是 NoneType
if ncm._env_config is not None:
    ncm.update({'llm': {'api_key': 'sk-debug-123'}})
    print(f"[D] os.getenv('LLM_API_KEY'): {os.getenv('LLM_API_KEY')}")  # 应为 'sk-debug-123'
os.unlink(tf.name)
```

**结果判读**：
- `[B]` 输出 `None` → 生产代码 bug（缺少 `return`），修复生产代码
- `[C]` 输出 `None` → `set()` 文件 I/O 失败，应用本文档的 mock 方案
- `[D]` 输出 `None` 但 `[C]` 正常 → `NetworkConfigManager` 路径问题，检查 `_save_secure`

---

## 3. Mock 方案代码模板

### 3.1 标准模板

在需要 mock 的测试文件顶部（import 之后、第一个测试类之前）添加以下 autouse fixture：

```python
import os
import pytest
from unittest.mock import patch


@pytest.fixture(autouse=True)
def _mock_env_config_in_ci():
    """CI 环境中 mock EnvConfigManager.set/delete，绕过 .env 文件 I/O。

    Why: CI (ubuntu-latest) 中 EnvConfigManager._atomic_write() 可能因
         overlayfs/权限问题抛异常，导致 os.environ 未更新，依赖
         os.getenv() 断言的测试失败。Mock 后直接设置 os.environ，
         跳过文件 I/O，仅激活于 SKILLS_OFFLINE 环境（CI 专用）。

    【不易】不改变测试断言语义——仍验证 NetworkConfigManager 正确调用
           _save_secure 并传递正确的 key/value 到 EnvConfigManager。
    【变易】仅 SKILLS_OFFLINE=1（CI 环境）激活，本地开发走真实 .env 写入。
    【简易】mock 直接操作 os.environ，无文件 I/O，无副作用。
    """
    if not os.environ.get('SKILLS_OFFLINE'):
        yield
        return

    from agent.env_config_manager import EnvConfigManager

    def _mock_set(self, key, value):
        """绕过 .env 文件写入，直接设置 os.environ（热重载等效）"""
        os.environ[key] = value

    def _mock_delete(self, key):
        """绕过 .env 文件删除，直接移除 os.environ"""
        os.environ.pop(key, None)

    with patch.object(EnvConfigManager, 'set', _mock_set), \
         patch.object(EnvConfigManager, 'delete', _mock_delete):
        yield
```

### 3.2 机制说明

```
测试调用 manager.update({'llm': {'api_key': 'sk-xxx'}})
  → NetworkConfigManager._save_secure('llm_api_key', 'sk-xxx')
    → self._env_config.set('LLM_API_KEY', 'sk-xxx')
      ↑ patch.object 替换了类方法，走 _mock_set
      → os.environ['LLM_API_KEY'] = 'sk-xxx'    ← 直接设置，跳过文件 I/O
  → assert os.getenv('LLM_API_KEY') == 'sk-xxx'  ← 通过
```

关键点：
- `patch.object(EnvConfigManager, 'set', _mock_set)` 在**类级别**替换方法，
  所有实例（含 `get_env_config_manager()` 单例）均走 mock 路径
- fixture 为 `autouse=True`，对该文件所有测试方法自动生效
- 本地（无 `SKILLS_OFFLINE`）走 `yield; return`，不影响现有行为
- fixture 与测试类的 `setup_method/teardown_method` 互不冲突：
  fixture 在 setup_method 之前激活，teardown_method 之后清理

---

## 4. 应用到新测试文件

### 步骤

1. 确认目标测试文件中有 `import os` 和 `from unittest.mock import patch`
2. 将 [3.1 标准模板](#31-标准模板) 复制到测试文件顶部（import 之后、第一个测试类之前）
3. 确认测试文件的 `setup_method/teardown_method` 有环境变量清理逻辑
   （`_TEST_ENV_KEYS` 列表 + pop/restore），避免测试间状态泄漏
4. 运行验证：

```powershell
$env:SKILLS_OFFLINE = '1'; $env:PYTHONIOENCODING = 'utf-8'
pytest <目标测试文件> -v --tb=short
```

### 注意事项

- **不要 mock `NetworkConfigManager._save_secure`**：它包含 `_key_to_env_var`
  映射逻辑，mock 它需要复制映射规则；mock `EnvConfigManager.set` 则保留完整业务逻辑
- **不要移除 `SKILLS_OFFLINE` 条件判断**：本地开发需要走真实 `.env` 写入以验证文件 I/O
- **不要 mock `EnvConfigManager.__init__`**：单例实例化需要成功，否则 `_env_config` 为 `None`
- **不要放入 `conftest.py`**（除非 3+ 个测试文件需要）：测试文件内作用域更精确，
  不影响其他测试文件

---

## 5. 已应用的文件

| 文件 | 添加日期 | 提交 | 说明 |
|------|---------|------|------|
| `tests/unit/test_network_config.py` | 2026-07-29 | `f0494a7b` | 5 个加密/脱敏测试 |

### 待应用（如需）

| 文件 | 测试数 | 说明 |
|------|--------|------|
| `tests/unit/test_network_config_save_regression.py` | 4 个 | 已因生产代码修复通过，mock 为可选加固 |

---

## 6. 常见问题

### Q1: mock 后测试仍失败，`os.getenv()` 返回 `None`

**排查**：检查 `self._env_config` 是否为 `None`（生产代码 bug）。
运行 [调试脚本](#2-调试脚本)，如果 `[B]` 输出 `None`，说明 `get_env_config_manager()`
缺少 `return`，需修复生产代码而非加 mock。

### Q2: mock 只在 CI 生效，本地测试不生效

**预期行为**。mock 的条件是 `os.environ.get('SKILLS_OFFLINE')`，本地开发不设此变量，
走真实 `.env` 写入。如需本地模拟 CI：

```powershell
$env:SKILLS_OFFLINE = '1'; pytest <测试文件> -v
```

### Q3: 加了 mock 后其他测试文件也受影响

**不会**。`autouse=True` 的作用域是**当前测试文件**，不影响其他文件。
如需跨文件共享，改用 `conftest.py`（但会扩大影响范围，需谨慎）。

### Q4: 是否需要 mock `EnvConfigManager.get()`？

**不需要**。`NetworkConfigManager._load_secure` 直接使用 `os.getenv()`，
不经过 `EnvConfigManager.get()`。mock `set/delete` 已足够覆盖所有读写路径。

---

## 7. 相关文档

- [发布说明：env_config_manager 修复](../releases/release-note-env-config-manager-fix-20260729.md)
- [EnvConfigManager 源码](../../agent/env_config_manager.py)
- [NetworkConfigManager 源码](../../agent/network_config.py)
- 提交: `f0494a7b` fix(config): 修复 get_env_config_manager() 缺少 return 导致 9 个 CI 测试失败
