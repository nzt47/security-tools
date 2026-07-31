# CI 流水线运行报告：EnvConfigManager 修复验证

**日期**: 2026-07-29
**环境**: Windows 10 Pro, Python 3.12.0, pytest 9.0.3
**CI 模拟**: `SKILLS_OFFLINE=1`, `PYTHONIOENCODING=utf-8`
**修复提交**: `f0494a7b` (核心修复) + `990a02d7` (回归测试加固)

---

## 1. 执行摘要

| 指标 | 值 |
|------|-----|
| 测试文件数 | 4 |
| 测试用例总数 | 173 |
| 通过 | 173 |
| 失败 | 0 |
| 跳过 | 0 |
| **通过率** | **100%** |
| 总耗时 | 49.17s |
| 平均每用例耗时 | 0.28s |

---

## 2. 分文件通过率统计

| 测试文件 | 用例数 | 通过 | 失败 | 跳过 | 通过率 | 状态 |
|---------|--------|------|------|------|--------|------|
| `test_network_config.py` | 42 | 42 | 0 | 0 | 100% | ✅ |
| `test_network_config_save_regression.py` | 24 | 24 | 0 | 0 | 100% | ✅ |
| `test_env_hot_reload.py` | 40 | 40 | 0 | 0 | 100% | ✅ |
| `test_network_package.py` | 67 | 67 | 0 | 0 | 100% | ✅ |
| **合计** | **173** | **173** | **0** | **0** | **100%** | ✅ |

---

## 3. 原 9 个失败测试恢复确认

| # | 文件 | 测试方法 | 修复前 | 修复后 |
|---|------|---------|--------|--------|
| 1 | `test_network_config.py` | `test_sensitive_api_key_encrypted_on_save` | ❌ FAILED | ✅ PASSED |
| 2 | `test_network_config.py` | `test_search_api_key_encrypted` | ❌ FAILED | ✅ PASSED |
| 3 | `test_network_config.py` | `test_webhook_url_encrypted` | ❌ FAILED | ✅ PASSED |
| 4 | `test_network_config.py` | `test_no_secure_manager_warning` | ❌ FAILED | ✅ PASSED |
| 5 | `test_network_config.py` | `test_llm_instance_api_key_masking` | ❌ FAILED | ✅ PASSED |
| 6 | `test_network_config_save_regression.py` | `test_real_api_key_saved_to_secure_store` | ❌ FAILED | ✅ PASSED |
| 7 | `test_network_config_save_regression.py` | `test_update_with_search_instances_strips_api_key` | ❌ FAILED | ✅ PASSED |
| 8 | `test_network_config_save_regression.py` | `test_update_with_masked_api_key_does_not_overwrite` | ❌ FAILED | ✅ PASSED |
| 9 | `test_network_config_save_regression.py` | `test_get_all_returns_masked_after_update` | ❌ FAILED | ✅ PASSED |

---

## 4. 耗时分析（Top 20 最慢用例）

| 排名 | 耗时 | 阶段 | 测试用例 |
|------|------|------|---------|
| 1 | 6.44s | call | `test_env_hot_reload.py::TestThreadSafety::test_concurrent_set_no_corruption` |
| 2 | 0.84s | call | `test_env_hot_reload.py::TestThreadSafety::test_concurrent_same_key_last_write_wins` |
| 3 | 0.76s | setup | `test_network_config.py::TestNetworkConfigEncryption::test_get_all_returns_masked_values` |
| 4 | 0.67s | setup | `test_env_hot_reload.py::TestMaskingBehavior::test_get_raw_config_returns_real_llm_api_key` |
| 5 | 0.65s | call | `test_network_config.py::TestChangeLog::test_change_log_limit` |
| 6 | 0.55s | setup | `test_network_package.py::TestValidateLlmInstance::test_missing_name` |
| 7 | 0.47s | setup | `test_network_config_save_regression.py::...::test_apply_does_not_write_api_key_to_file` |
| 8 | 0.45s | setup | `test_network_config_save_regression.py::...::test_update_with_search_instances_strips_api_key` |
| 9 | 0.45s | setup | `test_env_hot_reload.py::...::test_mapping[llm_test-multi_api_key-...]` |
| 10 | 0.43s | setup | `test_network_config.py::...::test_sensitive_api_key_encrypted_on_save` |
| 11 | 0.43s | setup | `test_env_hot_reload.py::...::test_set_writes_to_file_and_environ` |
| 12 | 0.42s | setup | `test_network_config.py::...::test_set_default_llm_instance_updates_config` |
| 13 | 0.41s | setup | `test_env_hot_reload.py::...::test_json_no_search_instance_api_key` |
| 14 | 0.41s | setup | `test_env_hot_reload.py::...::test_get_reads_from_environ` |
| 15 | 0.38s | setup | `test_env_hot_reload.py::...::test_mapping[search_bing_key-...]` |
| 16 | 0.36s | setup | `test_env_hot_reload.py::...::test_reload_loads_env_file_to_environ` |
| 17 | 0.36s | setup | `test_network_config.py::...::test_masked_api_key_not_saved` |
| 18 | 0.35s | setup | `test_network_config_save_regression.py::...::test_priority_normalized_to_uuid` |
| 19 | 0.35s | setup | `test_network_config_save_regression.py::...::test_delete_default_engine_clears_field` |
| 20 | 0.34s | setup | `test_network_config.py::...::test_webhook_url_encrypted` |

### 耗时分布

| 耗时区间 | 用例数 | 占比 | 说明 |
|---------|--------|------|------|
| > 5s | 1 | 0.6% | 并发写入测试（`test_concurrent_set_no_corruption`） |
| 0.5s - 5s | 4 | 2.3% | 并发测试 + 大量 setup |
| 0.3s - 0.5s | 15 | 8.7% | 多为 setup 阶段（EnvConfigManager 实例化） |
| < 0.3s | 153 | 88.4% | 常规测试 |

**瓶颈分析**：最慢用例为 `test_concurrent_set_no_corruption`（6.44s），这是线程安全并发写入测试，耗时来自多线程等待。其余用例耗时集中在 setup 阶段（`EnvConfigManager` 初始化 + `.env` 文件创建），平均 0.4s，属正常范围。

---

## 5. Mock fixture 覆盖情况

| 测试文件 | 是否含 mock fixture | 原因 |
|---------|-------------------|------|
| `test_network_config.py` | ✅ 已应用 | 用户指定 + 防御性加固 |
| `test_network_config_save_regression.py` | ✅ 已应用 | 与上文保持一致 |
| `test_env_hot_reload.py` | ❌ 不需要 | 使用临时文件隔离模式（patch 工厂函数注入独立实例） |
| `test_network_package.py` | ❌ 不需要 | 不断言 `os.getenv()` 敏感配置值 |

---

## 6. 验证命令

```powershell
# 完整验证（4 文件 173 用例）
$env:SKILLS_OFFLINE = '1'; $env:PYTHONIOENCODING = 'utf-8'
pytest tests/unit/test_network_config.py tests/unit/test_network_config_save_regression.py `
  tests/unit/test_env_hot_reload.py tests/unit/test_network_package.py `
  --tb=short --durations=20

# 仅验证原 9 个失败测试
$env:SKILLS_OFFLINE = '1'; $env:PYTHONIOENCODING = 'utf-8'
pytest tests/unit/test_network_config.py tests/unit/test_network_config_save_regression.py `
  -k "test_sensitive_api_key_encrypted_on_save or test_search_api_key_encrypted or `
  test_webhook_url_encrypted or test_no_secure_manager_warning or `
  test_llm_instance_api_key_masking or test_real_api_key_saved_to_secure_store or `
  test_update_with_search_instances_strips_api_key or `
  test_update_with_masked_api_key_does_not_overwrite or test_get_all_returns_masked_after_update" `
  -v --tb=short
```

---

## 7. 结论

| 检查项 | 结果 |
|--------|------|
| 原 9 个失败测试全部恢复 | ✅ 是 |
| 无新增失败 | ✅ 是 |
| 无回归（原有通过测试仍通过） | ✅ 是 |
| CI 模式（SKILLS_OFFLINE=1）全部通过 | ✅ 是 |
| 本地模式（无 SKILLS_OFFLINE）全部通过 | ✅ 是 |
| 耗时在可接受范围内（< 50s / 173 用例） | ✅ 是 |
| 可推送 master 触发 CI | ✅ 已推送 `990a02d7` |
