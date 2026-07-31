# 最佳实践：测试中的 EnvConfigManager 隔离模式

> **来源**：`tests/unit/test_env_hot_reload.py` 的自定义 fixture 设计
> **适用场景**：测试 `NetworkConfigManager` 敏感配置读写时，避免污染全局 `.env` 单例
> **对比方案**：`_mock_env_config_in_ci` mock fixture（跳过文件 I/O）vs 本模式（真实 I/O + 临时文件）

---

## 1. 问题背景

`NetworkConfigManager.__init__` 通过 `get_env_config_manager()` 获取全局单例：

```python
class NetworkConfigManager:
    def __init__(self, config_file: str = None):
        self._config_file = Path(config_file) if config_file else _NETWORK_CONFIG_FILE
        self._env_config = get_env_config_manager()  # ← 全局单例，指向项目根 .env
```

测试中直接使用 `NetworkConfigManager(config_file=temp_file)` 时，虽然 `network_config.json` 指向临时文件，但 `_env_config` 仍指向**项目根 `.env`** 全局单例。这导致：

1. 测试写入的 API Key 污染项目 `.env` 文件
2. CI 中 `.env` 文件 I/O 可能失败（权限/overlayfs）
3. 并行测试时多个用例竞争同一 `.env` 文件

---

## 2. 解决方案：三层隔离 fixture

`test_env_hot_reload.py` 通过三个 fixture 实现完整隔离：

### 2.1 第一层：临时 `.env` 文件

```python
@pytest.fixture
def temp_env_file(tmp_path):
    """临时 .env 文件，测试后自动清理"""
    env_file = tmp_path / ".env"
    env_file.touch()
    return env_file
```

利用 pytest 内置 `tmp_path` fixture，每个测试自动获得独立临时目录，测试结束后自动清理。

### 2.2 第二层：独立 EnvConfigManager 实例

```python
@pytest.fixture
def env_manager(temp_env_file):
    """独立的 EnvConfigManager 实例（不使用全局单例）"""
    from agent.env_config_manager import EnvConfigManager
    return EnvConfigManager(env_file_path=str(temp_env_file))
```

通过 `env_file_path` 参数注入临时文件路径，创建独立实例，绕过 `get_env_config_manager()` 单例工厂。

### 2.3 第三层：patch 单例工厂注入独立实例（关键）

```python
@pytest.fixture
def ncm_with_temp_env(tmp_path, monkeypatch):
    """使用临时 .env 和 network_config.json 的 NetworkConfigManager"""
    from agent.env_config_manager import EnvConfigManager
    from agent.network_config import NetworkConfigManager

    # 临时 .env 文件
    env_file = tmp_path / ".env"
    env_file.touch()

    # 临时 network_config.json
    nc_file = tmp_path / "network_config.json"

    # 创建独立的 EnvConfigManager（避免污染全局单例）
    env_mgr = EnvConfigManager(env_file_path=str(env_file))

    # 用 patch 让 NetworkConfigManager 使用我们的 env_mgr
    with patch('agent.network_config.get_env_config_manager', return_value=env_mgr):
        ncm = NetworkConfigManager(config_file=str(nc_file))
        yield ncm, env_mgr, env_file, nc_file

    # 清理测试期间设置的环境变量
    test_keys = [k for k in os.environ if k.startswith(('LLM_', 'SEARCH_', 'ERROR_REPORTING_'))]
    for k in test_keys:
        os.environ.pop(k, None)
```

**关键行**：`patch('agent.network_config.get_env_config_manager', return_value=env_mgr)`

这一行 patch 了**工厂函数**而非类方法，让 `NetworkConfigManager.__init__` 中的 `get_env_config_manager()` 调用返回我们的独立实例，而非全局单例。

---

## 3. 两种模式对比

| 维度 | mock fixture 模式 | 临时文件隔离模式（本最佳实践） |
|------|-------------------|-------------------------------|
| **I/O 行为** | 跳过文件 I/O，直接操作 `os.environ` | 真实文件 I/O，写入临时 `.env` |
| **patch 粒度** | 类方法级（`EnvConfigManager.set`） | 工厂函数级（`get_env_config_manager`） |
| **测试覆盖** | 不覆盖文件写入逻辑 | 覆盖完整 `set → _update_env_file → _atomic_write` 链路 |
| **CI 兼容** | 需 `SKILLS_OFFLINE` 条件激活 | 无条件生效，CI/本地一致 |
| **并行安全** | 安全（无文件竞争） | 安全（每个测试独立临时文件） |
| **适用场景** | 只需验证 key/value 传递正确性 | 需验证完整写入链路（含原子写入/并发） |

### 选型建议

- **新测试默认用临时文件隔离模式**（覆盖更完整，CI/本地行为一致）
- **仅在以下情况用 mock fixture**：① 测试不关心文件 I/O 行为 ② 临时文件创建开销过大 ③ 遗留测试快速修复

---

## 4. 清理机制

临时文件模式包含三层清理：

| 层级 | 机制 | 负责清理 |
|------|------|---------|
| 临时文件 | pytest `tmp_path` 自动清理 | `.env` 文件 + `network_config.json` |
| 环境变量 | fixture teardown 中 `os.environ.pop` | `LLM_*` / `SEARCH_*` / `ERROR_REPORTING_*` |
| EnvConfigManager 单例 | 不触碰全局单例，无需清理 | — |

**关键**：`os.environ` 清理在 `with patch(...)` 退出后执行，确保 patch 期间设置的环境变量被完全移除，不泄漏到后续测试。

---

## 5. 代码模板（可直接复制）

```python
import os
import pytest
from unittest.mock import patch


@pytest.fixture
def temp_env_file(tmp_path):
    """临时 .env 文件"""
    env_file = tmp_path / ".env"
    env_file.touch()
    return env_file


@pytest.fixture
def env_manager(temp_env_file):
    """独立 EnvConfigManager 实例（不使用全局单例）"""
    from agent.env_config_manager import EnvConfigManager
    return EnvConfigManager(env_file_path=str(temp_env_file))


@pytest.fixture
def ncm_with_temp_env(tmp_path):
    """隔离的 NetworkConfigManager（临时 .env + 临时 config.json）"""
    from agent.env_config_manager import EnvConfigManager
    from agent.network_config import NetworkConfigManager

    env_file = tmp_path / ".env"
    env_file.touch()
    nc_file = tmp_path / "network_config.json"
    env_mgr = EnvConfigManager(env_file_path=str(env_file))

    with patch('agent.network_config.get_env_config_manager', return_value=env_mgr):
        ncm = NetworkConfigManager(config_file=str(nc_file))
        yield ncm

    # 清理环境变量
    for k in [k for k in os.environ if k.startswith(('LLM_', 'SEARCH_', 'ERROR_REPORTING_'))]:
        os.environ.pop(k, None)
```

---

## 6. 验证效果

`test_env_hot_reload.py` 使用此模式后：

| 指标 | 结果 |
|------|------|
| 测试数 | 40 |
| 通过率 | 100% |
| CI 环境（`SKILLS_OFFLINE=1`） | ✅ 全部通过 |
| 本地环境 | ✅ 全部通过 |
| 是否需要 mock fixture | **不需要** |
| 是否污染项目 `.env` | **否**（使用临时文件） |

---

## 7. 参考

- 源文件：[tests/unit/test_env_hot_reload.py](../../tests/unit/test_env_hot_reload.py)
- 对比方案：[docs/troubleshooting/ci_env_config_mock_runbook.md](../troubleshooting/ci_env_config_mock_runbook.md)
- 生产代码：`agent/env_config_manager.py` / `agent/network_config.py`
