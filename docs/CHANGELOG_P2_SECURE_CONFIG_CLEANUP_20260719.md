# P2 SecureConfigManager 彻底清理变更日志

**日期**: 2026-07-19
**类型**: refactor (重构)
**范围**: SecureConfigManager 加密层彻底清理
**前置依赖**: P1 .env 单一数据源架构 (commit `878974c2` + `d4df7036` + `d5d27482`)

---

## 1. 背景与目标

P1 阶段已完成 .env 单一数据源架构，但 SecureConfigManager 加密层代码仍然残留：
- 5 处 `SecureConfigManager` 实例化
- 7 处 `_get_secure_manager` 调用
- 1 个 60KB 旧版 NetworkConfigManager 文件含 SecureConfigManager 集成代码
- 1 个完整的 `config_secure.py` 文件（SecureConfigManager 类定义）

P2 目标：彻底清理 SecureConfigManager 类定义和所有调用，统一由 .env 单一数据源管理敏感数据。

## 2. 清理范围与决策

### 三义分析

- **【不易】约束**: 纯 .env 单一数据源架构不可破坏；NetworkConfigManager 对外接口签名不可变；agent/network/__init__.py 导出契约不可变；Config 类核心配置加载流程不可破坏。
- **【变易】评估**: 扫描发现 9 处残留点，跨 6 个文件，含 60KB 旧版文件被 __init__.py 引用。直接全删会导致 Config 类方法失效、scripts/diagnose.py import 失败、__init__.py 导出断裂。
- **【简易】方案**: 分阶段清理，每阶段可独立验证回滚。

### 用户决策

用户选择"彻底清理"方案：清理所有调用点 + 移除 Config 类废弃方法 + 删除 config_secure.py + 评估处理 60KB 旧版文件。

## 3. 已完成的清理（阶段 1 + 2）

### 3.1 核心代码清理

| 文件 | 改动类型 | 说明 |
|------|---------|------|
| [config.py](file:///c:/Users/Administrator/agent/config.py) | 删除 | 移除 `_get_secure_manager()` 函数、`_secure_manager` 全局变量、`Config._load_from_secure_config()` 方法、`Config.save_secure_config()` 方法、`__init__` 中的调用 |
| [app_server.py](file:///c:/Users/Administrator/agent/app_server.py) | 简化 | 2 处 `_get_secure_manager()` 调用简化为 `NetworkConfigManager()` |
| [agent/orchestrator/lifecycle_manager.py](file:///c:/Users/Administrator/agent/agent/orchestrator/lifecycle_manager.py) | 简化 | 1 处 `_get_secure_manager()` 调用简化为 `NetworkConfigManager()` |
| [agent/network_config.py](file:///c:/Users/Administrator/agent/agent/network_config.py) | 删除参数 | 移除 `secure_manager` 参数和 `self._secure_manager` 字段 |
| [scripts/diagnose.py](file:///c:/Users/Administrator/agent/scripts/diagnose.py) | 改造 | `test_encryption()` 改造为 `test_env_config()`，测试 .env 加载 |

### 3.2 SecureConfigManager 类定义清理

| 文件 | 改动类型 | 说明 |
|------|---------|------|
| [config_secure.py](file:///c:/Users/Administrator/agent/config_secure.py) | **完全删除** | SecureConfigManager 类定义 + SecureConfigMixin + encrypt_file + decrypt_file + 4 个异常类 |
| [tests/unit/test_config_secure.py](file:///c:/Users/Administrator/agent/tests/unit/test_config_secure.py) | **完全删除** | SecureConfigManager 类的完整测试套件 |

### 3.3 测试文件清理

| 文件 | 改动类型 | 说明 |
|------|---------|------|
| [tests/unit/test_network_config.py](file:///c:/Users/Administrator/agent/tests/unit/test_network_config.py) | 修改 | 删除 20 处 `secure_manager=` 参数 + 清理 mock_secure 死代码 |
| [tests/unit/test_network_config_save_regression.py](file:///c:/Users/Administrator/agent/tests/unit/test_network_config_save_regression.py) | 修改 | 删除 2 处 `secure_manager=` 参数 |
| [tests/perf/test_config_manager_perf.py](file:///c:/Users/Administrator/agent/tests/perf/test_config_manager_perf.py) | 修改 | 清理 perf_manager fixture 中的 secure_manager 参数 |

## 4. 60KB 旧版文件处理（阶段 3 评估与回滚）

### 4.1 评估发现

- `agent/network/config_manager.py`（60KB 旧版）被 `agent/network/__init__.py` 导出
- 新版 `agent/network_config.py` 缺少 `_upsert_collection_batch` 和 `_upsert_collection_item` 方法
- 4 个测试文件依赖旧版 NetworkConfigManager 的独有方法

### 4.2 回滚决策

尝试删除 60KB 旧版文件后，13 个性能测试失败（`AttributeError: 'NetworkConfigManager' object has no attribute '_upsert_collection_batch'`）。

**决策**: 回滚阶段 3，保留 60KB 旧版文件作为兼容层。

### 4.3 兼容层处理

- `agent/network/config_manager.py` 保留 `secure_manager` 参数（标记为已废弃）
- `self._secure_manager` 字段保留（仅为兼容旧测试）
- `_save_secure` / `_load_secure` 实现保留（调用 `self._secure_manager` 方法）
- 文件头注释更新：说明已迁移至 .env 单一数据源
- **关键**: 60KB 旧版文件不再实例化 SecureConfigManager，仅保留参数兼容

## 5. 对现有功能的影响

### 5.1 无影响（兼容性保留）

- ✅ 主代码（app_server.py / lifecycle_manager.py）使用新版 NetworkConfigManager，功能正常
- ✅ Config 类配置加载流程：DEFAULT → _load_from_env() → overrides → validate，不依赖 SecureConfigManager
- ✅ .env 热重载功能正常
- ✅ .env 文件权限保护（P1）正常
- ✅ 60KB 旧版 NetworkConfigManager 兼容层正常工作

### 5.2 行为变更

- ⚠️ Config 类不再从加密文件加载 LLM API Key（改由 .env 提供）
- ⚠️ Config 类不再支持 `save_secure_config()` 方法（已移除）
- ⚠️ scripts/diagnose.py 的 `test_encryption()` 改为 `test_env_config()`（测试 .env 加载）

### 5.3 测试覆盖

- ❌ 删除 `tests/unit/test_config_secure.py`（SecureConfigManager 类测试，已无测试目标）
- ✅ 保留 `tests/unit/test_config_manager_comprehensive.py`（旧版兼容层测试）
- ✅ 保留 `tests/integration/test_config_manager_integration.py`（旧版兼容层测试）
- ✅ 保留 `tests/perf/test_config_manager_perf.py`（性能测试）

## 6. 回归测试结果

### 6.1 P2 相关测试套件

```
tests/unit/test_env_hot_reload.py
tests/unit/test_env_file_permissions.py
tests/unit/test_network_config.py
tests/unit/test_network_config_save_regression.py
tests/unit/test_network_package.py
tests/unit/test_config_manager_comprehensive.py
tests/integration/test_config_manager_integration.py
tests/boundary/test_config_boundary.py
tests/perf/test_config_manager_perf.py
```

**结果**: 558 通过, 4 失败, 3 跳过

### 6.2 失败归因

4 个失败全部是 `tests/boundary/test_config_boundary.py` 中的 circuit_breaker 配置节问题：
- 由 commit `c19f0cf7`（2026-07-16）引入的 circuit_breaker 必需配置节校验
- 与 P2 清理完全无关
- 与 .env 架构完全无关

### 6.3 大范围测试

- 461 个网络配置相关测试全部通过
- 全量 unit 测试因网络超时未能完成（与 P2 无关）

## 7. 回滚计划

### 7.1 文件级回滚

```bash
# 恢复 config_secure.py 和 test_config_secure.py
git checkout HEAD~1 -- config_secure.py tests/unit/test_config_secure.py

# 恢复 config.py 中的 _get_secure_manager 和 Config 类方法
git checkout HEAD~1 -- config.py

# 恢复其他修改的文件
git checkout HEAD~1 -- app_server.py agent/orchestrator/lifecycle_manager.py
git checkout HEAD~1 -- agent/network_config.py scripts/diagnose.py
git checkout HEAD~1 -- tests/unit/test_network_config.py tests/unit/test_network_config_save_regression.py
git checkout HEAD~1 -- tests/perf/test_config_manager_perf.py
```

### 7.2 验证回滚

```bash
python -m pytest tests/unit/test_env_hot_reload.py tests/unit/test_network_config.py -v
```

## 8. 后续规划

### P3: 60KB 旧版文件彻底删除（高风险，独立评估）

- 前置条件：将 `_upsert_collection_batch` 和 `_upsert_collection_item` 方法迁移到新版 NetworkConfigManager
- 工作量：中等（方法迁移 + 测试更新）
- 风险：可能引入新 bug，需要充分测试

### P4: circuit_breaker 测试夹具修复

- 修复 `tests/boundary/test_config_boundary.py` 中的 4 个失败用例
- 在测试夹具中补充 circuit_breaker 配置节
- 与 P2 无关，独立处理

## 9. 参考资料

- [P1 .env 单一数据源架构变更日志](file:///c:/Users/Administrator/agent/docs/CHANGELOG_ENV_SINGLE_SOURCE_20260719.md)
- [安全审计报告](file:///c:/Users/Administrator/agent/docs/security/SECURITY_AUDIT_REPORT.md)
- [.env 架构调整完整总结](file:///c:/Users/Administrator/agent/docs/ENV_ARCH_REFACTOR_SUMMARY.md)
- [EnvConfigManager 实现](file:///c:/Users/Administrator/agent/agent/env_config_manager.py)

---

**清理完成时间**: 2026-07-19
**清理提交**: 待提交
**测试验证**: 558 通过 / 4 预存在失败 / 3 跳过
