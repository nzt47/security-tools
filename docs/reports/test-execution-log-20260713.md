# 测试执行日志：snapshot.py 与 config_manager.py

**执行时间**: 2026-07-13 00:21:34
**测试文件**: `tests/unit/test_snapshot_comprehensive.py`, `tests/unit/test_config_manager_comprehensive.py`
**原始日志**: `docs/reports/test-execution-log-raw.txt`

---

## 1. 执行环境

| 项目 | 值 |
|------|------|
| 平台 | Windows 10 (win32) |
| Python | 3.12.0 |
| pytest | 9.0.3 |
| 超时设置 | 30.0s (thread method) |
| 随机种子 | 2786002818 |

---

## 2. 执行摘要

| 指标 | 值 |
|------|------|
| 总测试数 | 224 |
| 通过 | 224 |
| 失败 | 0 |
| 跳过 | 0 |
| 通过率 | 100.0% |
| 总耗时 | 4.56s |
| 平均耗时 | 20.4ms |

---

## 3. 按模块统计

### test_snapshot_comprehensive.py (88/88)

| 测试类 | 测试数 | 通过 | 失败 |
|--------|--------|------|------|
| TestCheckCompatibility | 3 | 3 | 0 |
| TestCleanupOldSnapshots | 2 | 2 | 0 |
| TestCleanupOldSnapshotsEdgeCases | 1 | 1 | 0 |
| TestCleanupSnapshots | 2 | 2 | 0 |
| TestCleanupSnapshotsEdgeCases | 1 | 1 | 0 |
| TestComputeChecksum | 3 | 3 | 0 |
| TestListSnapshots | 3 | 3 | 0 |
| TestListSnapshotsEdgeCases | 2 | 2 | 0 |
| TestLoadSnapshot | 5 | 5 | 0 |
| TestLoadSnapshotData | 6 | 6 | 0 |
| TestLoadSnapshotDataEdgeCases | 2 | 2 | 0 |
| TestLoadSnapshotEdgeCases | 1 | 1 | 0 |
| TestMergeSnapshots | 2 | 2 | 0 |
| TestPersistSnapshot | 4 | 4 | 0 |
| TestRestoreBehaviorEdgeCases | 2 | 2 | 0 |
| TestRestoreMethods | 8 | 8 | 0 |
| TestRestoreModulesByPriority | 5 | 5 | 0 |
| TestRestoreModulesByPriorityEdgeCases | 2 | 2 | 0 |
| TestSaveCoreModulesDeltaIncremental | 4 | 4 | 0 |
| TestSaveCoreModulesWithDelta | 5 | 5 | 0 |
| TestSaveSnapshot | 8 | 8 | 0 |
| TestSerializeMethods | 13 | 13 | 0 |
| TestShowPerformancePanel | 2 | 2 | 0 |
| TestUpdateModuleChecksums | 2 | 2 | 0 |

### test_config_manager_comprehensive.py (136/136)

| 测试类 | 测试数 | 通过 | 失败 |
|--------|--------|------|------|
| TestAddChangeLogEdgeCases | 2 | 2 | 0 |
| TestApplySearchInstances | 5 | 5 | 0 |
| TestApplySearchInstancesEdgeCases | 5 | 5 | 0 |
| TestApplyToApp | 8 | 8 | 0 |
| TestApplyToAppEdgeCases | 8 | 8 | 0 |
| TestChangeLog | 3 | 3 | 0 |
| TestEnsureConfigStructure | 3 | 3 | 0 |
| TestGetAll | 6 | 6 | 0 |
| TestGetRawConfig | 2 | 2 | 0 |
| TestImportConfigEdgeCases | 3 | 3 | 0 |
| TestLlmInstanceApi | 19 | 19 | 0 |
| TestLlmInstanceApiEdgeCases | 7 | 7 | 0 |
| TestLoad | 4 | 4 | 0 |
| TestMcpServiceApi | 10 | 10 | 0 |
| TestMcpServiceApiEdgeCases | 3 | 3 | 0 |
| TestRegisterSearchInstance | 4 | 4 | 0 |
| TestRegisterSearchInstanceEdgeCases | 3 | 3 | 0 |
| TestResetExportImport | 7 | 7 | 0 |
| TestSearchConfigEdgeCases | 3 | 3 | 0 |
| TestSearchEngines | 5 | 5 | 0 |
| TestSecureStorage | 6 | 6 | 0 |
| TestSeedBuiltinSearch | 1 | 1 | 0 |
| TestUpdate | 7 | 7 | 0 |
| TestUpdateLlmInstances | 3 | 3 | 0 |
| TestUpdateMcpConfig | 1 | 1 | 0 |
| TestUpdateMcpConfigEdgeCases | 2 | 2 | 0 |
| TestUpdateSearchInstances | 2 | 2 | 0 |
| TestUpdateWebhookEdgeCases | 2 | 2 | 0 |
| TestValidate | 2 | 2 | 0 |

---

## 4. 最慢的 20 个测试

| 排名 | 耗时 | 测试 |
|------|------|------|
| 1 | 0.10s | `test_snapshot_comprehensive.py::TestCleanupSnapshots::test_cleanup_deletes_excess` |
| 2 | 0.09s | `test_snapshot_comprehensive.py::TestCleanupOldSnapshots::test_cleanup_removes_excess` |
| 3 | 0.08s | `test_snapshot_comprehensive.py::TestCleanupSnapshotsEdgeCases::test_cleanup_swallows_delete_error` |
| 4 | 0.07s | `test_snapshot_comprehensive.py::TestCleanupOldSnapshotsEdgeCases::test_cleanup_swallows_unlink_error` |
| 5 | 0.05s | `test_snapshot_comprehensive.py::TestListSnapshots::test_list_multiple_snapshots` |
| 6 | 0.05s | `test_config_manager_comprehensive.py::TestApplyToAppEdgeCases::test_apply_to_app_uses_default_llm_instance` |
| 7 | 0.03s | `test_snapshot_comprehensive.py::TestLoadSnapshotData::test_load_latest_snapshot` |
| 8 | 0.01s | `test_snapshot_comprehensive.py::TestShowPerformancePanel::test_show_panel_after_operations` |
| 9 | 0.01s | `test_snapshot_comprehensive.py::TestSerializeMethods::test_serialize_tools_registry_truncates_to_50` |
| 10 | 0.01s | `test_snapshot_comprehensive.py::TestSaveSnapshot::test_save_success` |
| 11 | 0.01s | `test_snapshot_comprehensive.py::TestSaveSnapshot::test_save_no_config_attribute` |
| 12 | 0.01s | `test_snapshot_comprehensive.py::TestSaveSnapshot::test_save_incremental` |
| 13 | 0.01s | `test_snapshot_comprehensive.py::TestSaveSnapshot::test_save_frequency_blocked` |
| 14 | 0.01s | `test_snapshot_comprehensive.py::TestSaveSnapshot::test_save_force_bypasses_frequency` |
| 15 | 0.01s | `test_snapshot_comprehensive.py::TestSaveSnapshot::test_save_auto_generate_id` |
| 16 | 0.01s | `test_snapshot_comprehensive.py::TestLoadSnapshotEdgeCases::test_load_restore_failure_continues` |
| 17 | 0.01s | `test_snapshot_comprehensive.py::TestLoadSnapshotDataEdgeCases::test_load_incremental_file_corrupted_falls_back_to_full` |
| 18 | 0.01s | `test_snapshot_comprehensive.py::TestLoadSnapshotDataEdgeCases::test_load_from_path_incremental_merges_base` |
| 19 | 0.01s | `test_snapshot_comprehensive.py::TestLoadSnapshotData::test_load_full_snapshot` |
| 20 | 0.01s | `test_snapshot_comprehensive.py::TestLoadSnapshot::test_load_with_class_creates_instance` |

---

## 5. 完整测试结果清单

### test_snapshot_comprehensive.py

#### TestCheckCompatibility

| # | 测试名 | 状态 |
|---|--------|------|
| 1 | test_compatible_p6_2 | PASS |
| 2 | test_compatible_p6_1 | PASS |
| 3 | test_incompatible_version | PASS |

#### TestCleanupOldSnapshots

| # | 测试名 | 状态 |
|---|--------|------|
| 1 | test_cleanup_no_excess | PASS |
| 2 | test_cleanup_removes_excess | PASS |

#### TestCleanupOldSnapshotsEdgeCases

| # | 测试名 | 状态 |
|---|--------|------|
| 1 | test_cleanup_swallows_unlink_error | PASS |

#### TestCleanupSnapshots

| # | 测试名 | 状态 |
|---|--------|------|
| 1 | test_cleanup_deletes_excess | PASS |
| 2 | test_cleanup_no_excess | PASS |

#### TestCleanupSnapshotsEdgeCases

| # | 测试名 | 状态 |
|---|--------|------|
| 1 | test_cleanup_swallows_delete_error | PASS |

#### TestComputeChecksum

| # | 测试名 | 状态 |
|---|--------|------|
| 1 | test_compute_checksum_stable_for_same_data | PASS |
| 2 | test_compute_checksum_differs_on_config_change | PASS |
| 3 | test_compute_checksum_returns_hex_string | PASS |

#### TestListSnapshots

| # | 测试名 | 状态 |
|---|--------|------|
| 1 | test_list_multiple_snapshots | PASS |
| 2 | test_list_empty | PASS |
| 3 | test_list_includes_incremental | PASS |

#### TestListSnapshotsEdgeCases

| # | 测试名 | 状态 |
|---|--------|------|
| 1 | test_list_handles_dir_iteration_error | PASS |
| 2 | test_list_skips_non_snapshot_files | PASS |

#### TestLoadSnapshot

| # | 测试名 | 状态 |
|---|--------|------|
| 1 | test_load_returns_snapshot_data_without_class | PASS |
| 2 | test_load_incompatible_version | PASS |
| 3 | test_load_class_creation_failure | PASS |
| 4 | test_load_with_class_creates_instance | PASS |
| 5 | test_load_returns_none_when_no_snapshot | PASS |

#### TestLoadSnapshotData

| # | 测试名 | 状态 |
|---|--------|------|
| 1 | test_load_specific_snapshot_not_found | PASS |
| 2 | test_load_latest_snapshot | PASS |
| 3 | test_load_from_path_decompresses | PASS |
| 4 | test_load_from_path_uncompressed | PASS |
| 5 | test_load_full_snapshot | PASS |
| 6 | test_load_returns_none_when_no_snapshots | PASS |

#### TestLoadSnapshotDataEdgeCases

| # | 测试名 | 状态 |
|---|--------|------|
| 1 | test_load_from_path_incremental_merges_base | PASS |
| 2 | test_load_incremental_file_corrupted_falls_back_to_full | PASS |

#### TestLoadSnapshotEdgeCases

| # | 测试名 | 状态 |
|---|--------|------|
| 1 | test_load_restore_failure_continues | PASS |

#### TestMergeSnapshots

| # | 测试名 | 状态 |
|---|--------|------|
| 1 | test_merge_skips_unchanged_modules | PASS |
| 2 | test_merge_applies_changed_modules | PASS |

#### TestPersistSnapshot

| # | 测试名 | 状态 |
|---|--------|------|
| 1 | test_persist_uncompressed | PASS |
| 2 | test_persist_failure_returns_false | PASS |
| 3 | test_persist_compressed | PASS |
| 4 | test_persist_incremental | PASS |

#### TestRestoreBehaviorEdgeCases

| # | 测试名 | 状态 |
|---|--------|------|
| 1 | test_restore_with_mode_history | PASS |
| 2 | test_restore_unknown_mode_logs_warning | PASS |

#### TestRestoreMethods

| # | 测试名 | 状态 |
|---|--------|------|
| 1 | test_restore_permission_success | PASS |
| 2 | test_restore_body_sensor_exception | PASS |
| 3 | test_restore_behavior_exception | PASS |
| 4 | test_restore_permission_exception | PASS |
| 5 | test_restore_behavior_success | PASS |
| 6 | test_restore_tools_registry_exception | PASS |
| 7 | test_restore_tools_registry_success | PASS |
| 8 | test_restore_body_sensor_success | PASS |

#### TestRestoreModulesByPriority

| # | 测试名 | 状态 |
|---|--------|------|
| 1 | test_restore_checksum_mismatch_continues | PASS |
| 2 | test_restore_all_modules | PASS |
| 3 | test_restore_skip_uninitialized | PASS |
| 4 | test_restore_priority_order | PASS |
| 5 | test_restore_unknown_module | PASS |

#### TestRestoreModulesByPriorityEdgeCases

| # | 测试名 | 状态 |
|---|--------|------|
| 1 | test_restore_body_with_get_method | PASS |
| 2 | test_restore_module_exception_continues | PASS |

#### TestSaveCoreModulesDeltaIncremental

| # | 测试名 | 状态 |
|---|--------|------|
| 1 | test_incremental_records_changed_module | PASS |
| 2 | test_incremental_skips_unchanged_behavior | PASS |
| 3 | test_incremental_skips_unchanged_permission | PASS |
| 4 | test_incremental_skips_unchanged_tools | PASS |

#### TestSaveCoreModulesWithDelta

| # | 测试名 | 状态 |
|---|--------|------|
| 1 | test_incremental_with_changed_module | PASS |
| 2 | test_body_with_get_method | PASS |
| 3 | test_full_save_all_modules | PASS |
| 4 | test_incremental_skip_unchanged | PASS |
| 5 | test_no_body_attribute | PASS |

#### TestSaveSnapshot

| # | 测试名 | 状态 |
|---|--------|------|
| 1 | test_save_auto_generate_id | PASS |
| 2 | test_save_frequency_blocked | PASS |
| 3 | test_save_force_bypasses_frequency | PASS |
| 4 | test_save_exception_returns_failure | PASS |
| 5 | test_save_no_config_attribute | PASS |
| 6 | test_save_persist_failure | PASS |
| 7 | test_save_incremental | PASS |
| 8 | test_save_success | PASS |

#### TestSerializeMethods

| # | 测试名 | 状态 |
|---|--------|------|
| 1 | test_serialize_body_sensor_full | PASS |
| 2 | test_serialize_tools_registry_none | PASS |
| 3 | test_serialize_body_sensor_exception | PASS |
| 4 | test_serialize_permission_full | PASS |
| 5 | test_serialize_tools_registry_exception | PASS |
| 6 | test_serialize_tools_registry_empty | PASS |
| 7 | test_serialize_behavior_exception | PASS |
| 8 | test_serialize_behavior_long_history | PASS |
| 9 | test_serialize_permission_exception | PASS |
| 10 | test_serialize_body_sensor_minimal | PASS |
| 11 | test_serialize_behavior_full | PASS |
| 12 | test_serialize_tools_registry_full | PASS |
| 13 | test_serialize_tools_registry_truncates_to_50 | PASS |

#### TestShowPerformancePanel

| # | 测试名 | 状态 |
|---|--------|------|
| 1 | test_show_panel_after_operations | PASS |
| 2 | test_show_panel_no_crash | PASS |

#### TestUpdateModuleChecksums

| # | 测试名 | 状态 |
|---|--------|------|
| 1 | test_update_checksums_overwrites | PASS |
| 2 | test_update_checksums | PASS |

### test_config_manager_comprehensive.py

#### TestAddChangeLogEdgeCases

| # | 测试名 | 状态 |
|---|--------|------|
| 1 | test_change_log_not_truncated_below_100 | PASS |
| 2 | test_change_log_truncated_at_100 | PASS |

#### TestApplySearchInstances

| # | 测试名 | 状态 |
|---|--------|------|
| 1 | test_no_search_engine_returns_early | PASS |
| 2 | test_seeds_builtins_when_empty | PASS |
| 3 | test_registers_existing_instances | PASS |
| 4 | test_skips_disabled_instances | PASS |
| 5 | test_rebuilds_engine_priority | PASS |

#### TestApplySearchInstancesEdgeCases

| # | 测试名 | 状态 |
|---|--------|------|
| 1 | test_apply_seeds_builtins_when_empty | PASS |
| 2 | test_apply_rebuilds_priority_with_valid_ids | PASS |
| 3 | test_apply_skips_disabled_instances | PASS |
| 4 | test_apply_skips_instance_without_id | PASS |
| 5 | test_apply_removes_stale_priority_entries | PASS |

#### TestApplyToApp

| # | 测试名 | 状态 |
|---|--------|------|
| 1 | test_apply_to_app_no_web_http | PASS |
| 2 | test_apply_to_app_no_instance | PASS |
| 3 | test_apply_to_app_updates_http_timeout | PASS |
| 4 | test_apply_to_app_llm_exception_handled | PASS |
| 5 | test_apply_to_app_updates_search_config | PASS |
| 6 | test_apply_to_app_llm_incomplete_skips | PASS |
| 7 | test_apply_to_app_configures_llm | PASS |
| 8 | test_apply_to_app_llm_disabled_skips | PASS |

#### TestApplyToAppEdgeCases

| # | 测试名 | 状态 |
|---|--------|------|
| 1 | test_apply_to_app_no_web_http | PASS |
| 2 | test_apply_to_app_register_exception_swallowed | PASS |
| 3 | test_apply_to_app_uses_first_enabled_llm_instance | PASS |
| 4 | test_apply_to_app_search_exception_swallowed | PASS |
| 5 | test_apply_to_app_http_exception_swallowed | PASS |
| 6 | test_apply_to_app_uses_default_llm_instance | PASS |
| 7 | test_apply_to_app_no_configure_llm_method | PASS |
| 8 | test_apply_to_app_no_llm_instances_uses_legacy | PASS |

#### TestChangeLog

| # | 测试名 | 状态 |
|---|--------|------|
| 1 | test_get_change_log_with_entries | PASS |
| 2 | test_get_change_log_empty | PASS |
| 3 | test_change_log_limit | PASS |

#### TestEnsureConfigStructure

| # | 测试名 | 状态 |
|---|--------|------|
| 1 | test_adds_error_reporting | PASS |
| 2 | test_assigns_ids_to_instances_without_id | PASS |
| 3 | test_adds_missing_keys | PASS |

#### TestGetAll

| # | 测试名 | 状态 |
|---|--------|------|
| 1 | test_llm_api_key_masked_long | PASS |
| 2 | test_search_instance_api_key_masked | PASS |
| 3 | test_webhook_url_masked | PASS |
| 4 | test_llm_instance_api_key_masked | PASS |
| 5 | test_returns_config_with_structure | PASS |
| 6 | test_llm_api_key_masked_short | PASS |

#### TestGetRawConfig

| # | 测试名 | 状态 |
|---|--------|------|
| 1 | test_returns_raw_config | PASS |
| 2 | test_llm_instance_api_key_decrypted | PASS |

#### TestImportConfigEdgeCases

| # | 测试名 | 状态 |
|---|--------|------|
| 1 | test_import_invalid_json_raises_value_error | PASS |
| 2 | test_import_skip_strategy_keeps_existing | PASS |
| 3 | test_import_merge_strategy_deep_merge | PASS |

#### TestLlmInstanceApi

| # | 测试名 | 状态 |
|---|--------|------|
| 1 | test_add_llm_instance_with_api_key | PASS |
| 2 | test_get_llm_instance_by_id | PASS |
| 3 | test_delete_default_llm_instance_clears_default | PASS |
| 4 | test_delete_llm_instance_not_found | PASS |
| 5 | test_update_llm_instance_not_found | PASS |
| 6 | test_add_llm_instance_duplicate_name_raises | PASS |
| 7 | test_set_default_llm_instance_by_name | PASS |
| 8 | test_update_llm_instance_masked_api_key_skipped | PASS |
| 9 | test_update_llm_instance_by_name | PASS |
| 10 | test_delete_llm_instance_by_name | PASS |
| 11 | test_get_llm_instance_not_found | PASS |
| 12 | test_update_llm_instance_duplicate_name_raises | PASS |
| 13 | test_get_llm_instance_by_name | PASS |
| 14 | test_set_default_llm_instance_not_found | PASS |
| 15 | test_set_default_llm_instance | PASS |
| 16 | test_update_llm_instance | PASS |
| 17 | test_add_llm_instance | PASS |
| 18 | test_delete_llm_instance | PASS |
| 19 | test_get_llm_instances_empty | PASS |

#### TestLlmInstanceApiEdgeCases

| # | 测试名 | 状态 |
|---|--------|------|
| 1 | test_update_llm_instance_name_conflict | PASS |
| 2 | test_delete_llm_instance_clears_default | PASS |
| 3 | test_update_llm_instance_masked_api_key_skipped | PASS |
| 4 | test_set_default_llm_instance_by_name | PASS |
| 5 | test_delete_llm_instance_by_name | PASS |
| 6 | test_update_llm_instance_api_key_encrypted | PASS |
| 7 | test_update_llm_instance_not_found_returns_none | PASS |

#### TestLoad

| # | 测试名 | 状态 |
|---|--------|------|
| 1 | test_load_uses_cache | PASS |
| 2 | test_load_from_existing_file | PASS |
| 3 | test_load_handles_json_error | PASS |
| 4 | test_load_creates_default_when_no_file | PASS |

#### TestMcpServiceApi

| # | 测试名 | 状态 |
|---|--------|------|
| 1 | test_update_mcp_service_duplicate_name_raises | PASS |
| 2 | test_update_mcp_service_not_found | PASS |
| 3 | test_get_mcp_service_by_id | PASS |
| 4 | test_update_mcp_service | PASS |
| 5 | test_add_mcp_service_duplicate_name_raises | PASS |
| 6 | test_add_mcp_service | PASS |
| 7 | test_delete_mcp_service | PASS |
| 8 | test_delete_mcp_service_not_found | PASS |
| 9 | test_get_mcp_services_empty | PASS |
| 10 | test_get_mcp_service_not_found | PASS |

#### TestMcpServiceApiEdgeCases

| # | 测试名 | 状态 |
|---|--------|------|
| 1 | test_update_mcp_service_name_conflict | PASS |
| 2 | test_delete_mcp_service_not_found | PASS |
| 3 | test_update_mcp_service_not_found | PASS |

#### TestRegisterSearchInstance

| # | 测试名 | 状态 |
|---|--------|------|
| 1 | test_register_builtin_engine | PASS |
| 2 | test_register_sets_default | PASS |
| 3 | test_register_custom_engine | PASS |
| 4 | test_no_search_engine_returns_early | PASS |

#### TestRegisterSearchInstanceEdgeCases

| # | 测试名 | 状态 |
|---|--------|------|
| 1 | test_builtin_engine_with_api_key_syncs | PASS |
| 2 | test_builtin_engine_handler_fallback_to_custom | PASS |
| 3 | test_custom_engine_no_api_key_sync | PASS |

#### TestResetExportImport

| # | 测试名 | 状态 |
|---|--------|------|
| 1 | test_import_config_skip | PASS |
| 2 | test_import_config_merge | PASS |
| 3 | test_import_config_overwrite | PASS |
| 4 | test_import_config_invalid_json_raises | PASS |
| 5 | test_reset_returns_default | PASS |
| 6 | test_export_config_returns_json | PASS |
| 7 | test_reset_clears_custom_config | PASS |

#### TestSearchConfigEdgeCases

| # | 测试名 | 状态 |
|---|--------|------|
| 1 | test_get_search_engines_returns_api_keys | PASS |
| 2 | test_update_search_config_engine_enabled | PASS |
| 3 | test_update_search_config_max_results | PASS |

#### TestSearchEngines

| # | 测试名 | 状态 |
|---|--------|------|
| 1 | test_update_search_config_engine_priority | PASS |
| 2 | test_update_search_config_empty | PASS |
| 3 | test_update_search_config_timeout | PASS |
| 4 | test_get_search_engines | PASS |
| 5 | test_update_search_config_default_engine | PASS |

#### TestSecureStorage

| # | 测试名 | 状态 |
|---|--------|------|
| 1 | test_load_secure_exception_returns_default | PASS |
| 2 | test_save_secure_with_manager | PASS |
| 3 | test_load_secure_with_manager | PASS |
| 4 | test_save_secure_without_manager | PASS |
| 5 | test_load_secure_without_manager | PASS |
| 6 | test_save_secure_exception_does_not_raise | PASS |

#### TestSeedBuiltinSearch

| # | 测试名 | 状态 |
|---|--------|------|
| 1 | test_seeds_three_builtins | PASS |

#### TestUpdate

| # | 测试名 | 状态 |
|---|--------|------|
| 1 | test_update_webhook_url | PASS |
| 2 | test_update_adds_change_log | PASS |
| 3 | test_update_llm_api_key_masked_skipped | PASS |
| 4 | test_update_search_api_keys | PASS |
| 5 | test_update_llm_api_key | PASS |
| 6 | test_update_returns_config | PASS |
| 7 | test_update_mcp_config | PASS |

#### TestUpdateLlmInstances

| # | 测试名 | 状态 |
|---|--------|------|
| 1 | test_add_new_instance | PASS |
| 2 | test_masked_api_key_not_saved | PASS |
| 3 | test_update_existing_instance | PASS |

#### TestUpdateMcpConfig

| # | 测试名 | 状态 |
|---|--------|------|
| 1 | test_update_mcp_with_new_service | PASS |

#### TestUpdateMcpConfigEdgeCases

| # | 测试名 | 状态 |
|---|--------|------|
| 1 | test_update_mcp_with_existing_service_id | PASS |
| 2 | test_update_mcp_service_without_id_no_auto_generate | PASS |

#### TestUpdateSearchInstances

| # | 测试名 | 状态 |
|---|--------|------|
| 1 | test_add_new_search_instance | PASS |
| 2 | test_update_existing_search_instance | PASS |

#### TestUpdateWebhookEdgeCases

| # | 测试名 | 状态 |
|---|--------|------|
| 1 | test_update_webhook_masked_skipped | PASS |
| 2 | test_update_webhook_only_masked_marker | PASS |

#### TestValidate

| # | 测试名 | 状态 |
|---|--------|------|
| 1 | test_validate_llm_instance | PASS |
| 2 | test_validate_mcp_service | PASS |

---

## 6. 结论

全部 224 个测试用例执行通过，通过率 100%。
最慢测试耗时 0.10s（TestCleanupSnapshots::test_cleanup_deletes_excess），
平均每个测试耗时约 20.4ms，整体执行效率良好。
