# -*- coding: utf-8 -*-
"""灾备恢复混沌测试 — 数据损坏下的恢复能力验证

【测试目标】
验证 DisasterRecovery 在以下数据损坏场景下的恢复能力：
1. 数据库文件损坏（内容被污染）
2. 配置文件丢失（突然消失）
3. 服务重启后状态恢复
4. 备份与恢复的一致性
5. 多资源同时损坏的批量恢复
6. 配置热重载（不重启服务）

【可观测性约束】
- 边界显性化：所有故障注入通过 tmp_path 真实文件操作实现
- 异常处理：恢复失败应返回失败结果，不抛异常
- 埋点预留：灾备模块内部已埋点（backup_success/restore_success）

【生成日志摘要】
- 生成时间：2026-06-27
- 版本：v1.0.0
- 内容：灾备恢复混沌测试
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

import pytest

# 模块级 logger，便于排查测试执行链路
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agent.disaster_recovery import (  # noqa: E402
    DisasterRecovery,
    RecoveryAction,
    RecoveryResult,
    set_trace_id,
)


# ═══════════════════════════════════════════════════════════════
#  1. 数据库损坏
# ═══════════════════════════════════════════════════════════════

class TestDatabaseCorruption:
    """数据库文件损坏的自动修复"""

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_corrupted_db_should_be_restored_from_backup(self, tmp_path):
        """数据库损坏应从备份恢复

        场景：先创建数据库并备份，然后损坏数据库文件，
        调用 repair_if_corrupted 应自动恢复。

        预期：恢复后数据库内容与备份一致。
        """
        set_trace_id("chaos-dr-db-001")
        trace_id = "chaos-dr-db-001"
        # 记录测试方法入口，便于在日志中定位执行链路
        logger.info("[DR_CHAOS] %s - action=test_start, test=test_corrupted_db_should_be_restored_from_backup",
                    trace_id)
        backup_root = tmp_path / "backups"
        db_path = tmp_path / "memory.db"
        # 写入正常数据
        db_path.write_text(json.dumps({"key": "value"}), encoding="utf-8")

        dr = DisasterRecovery(backup_root=backup_root, max_backups=3)
        dr.register("memory_db", db_path)
        # 记录资源注册信息，便于追溯 resource 与磁盘路径的映射
        logger.info("[DR_CHAOS] %s - action=resource_registered, resource=memory_db, path=%s",
                    trace_id, db_path)

        # 创建备份
        backup_result = dr.backup("memory_db")
        # 记录备份结果与备份文件路径
        logger.info("[DR_CHAOS] %s - action=backup_done, resource=memory_db, success=%s, backup_file=%s",
                    trace_id, backup_result.success, getattr(backup_result, "backup_file", None))
        # 断言前记录预期值与实际值，便于失败时快速定位
        logger.info("[DR_CHAOS] %s - action=assert_backup_success, expected_success=True, actual_success=%s, expected_action=%s, actual_action=%s",
                    trace_id, backup_result.success, RecoveryAction.BACKUP, backup_result.action)
        assert backup_result.success is True
        assert backup_result.action == RecoveryAction.BACKUP

        # 损坏数据库
        db_path.write_text("CORRUPTED_DATA<<<>>>", encoding="utf-8")
        # 记录数据损坏注入操作
        logger.info("[DR_CHAOS] %s - action=corruption_injected, resource=memory_db, reason=content_overwritten_with_garbage",
                    trace_id)

        # 定义完整性校验函数
        def validator(path):
            try:
                content = Path(path).read_text(encoding="utf-8")
                json.loads(content)  # 损坏数据会抛 JSONDecodeError
                return True
            except Exception:
                return False

        # 检测到损坏应返回 False
        integrity_result = dr.check_integrity("memory_db", validator)
        # 记录损坏检测结果与原因
        logger.info("[DR_CHAOS] %s - action=integrity_check, resource=memory_db, is_corrupted=%s, reason=json_parse_failed",
                    trace_id, not integrity_result)
        logger.info("[DR_CHAOS] %s - action=assert_integrity_corrupted, expected=False, actual=%s",
                    trace_id, integrity_result)
        assert integrity_result is False

        # 修复
        repair_result = dr.repair_if_corrupted("memory_db", validator)
        # 记录恢复结果与恢复来源
        logger.info("[DR_CHAOS] %s - action=repair_done, resource=memory_db, success=%s, restore_source=%s",
                    trace_id, repair_result.success, getattr(repair_result, "backup_file", None))
        logger.info("[DR_CHAOS] %s - action=assert_restore_success, expected_success=True, actual_success=%s, expected_action=%s, actual_action=%s",
                    trace_id, repair_result.success, RecoveryAction.RESTORE, repair_result.action)
        assert repair_result.success is True
        assert repair_result.action == RecoveryAction.RESTORE

        # 验证：数据库已恢复
        restored_content = db_path.read_text(encoding="utf-8")
        restored_data = json.loads(restored_content)
        # 记录最终恢复内容校验
        logger.info("[DR_CHAOS] %s - action=assert_restored_content, expected=%s, actual=%s",
                    trace_id, {"key": "value"}, restored_data)
        assert restored_data == {"key": "value"}
        # 测试通过出口日志
        logger.info("[DR_CHAOS] %s - action=test_end, test=test_corrupted_db_should_be_restored_from_backup, result=passed",
                    trace_id)

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_corrupted_db_without_backup_should_fail_gracefully(self, tmp_path):
        """无备份时数据库损坏应优雅失败

        场景：数据库损坏但无备份，repair_if_corrupted 应返回失败结果。
        预期：恢复失败，但不应抛异常。
        """
        set_trace_id("chaos-dr-db-002")
        trace_id = "chaos-dr-db-002"
        # 测试入口日志：无备份场景下的损坏恢复
        logger.info("[DR_CHAOS] %s - action=test_start, test=test_corrupted_db_without_backup_should_fail_gracefully",
                    trace_id)
        backup_root = tmp_path / "backups"
        db_path = tmp_path / "memory.db"
        db_path.write_text("CORRUPTED", encoding="utf-8")

        dr = DisasterRecovery(backup_root=backup_root)
        dr.register("memory_db", db_path)
        # 记录资源注册信息
        logger.info("[DR_CHAOS] %s - action=resource_registered, resource=memory_db, path=%s",
                    trace_id, db_path)

        def validator(path):
            return False  # 始终认为损坏

        # 无备份，应失败但不抛异常
        result = dr.repair_if_corrupted("memory_db", validator)
        # 记录恢复失败结果与错误信息
        logger.info("[DR_CHAOS] %s - action=repair_failed, resource=memory_db, success=%s, error_message=%s",
                    trace_id, result.success, result.message)
        # 断言前记录预期与实际值
        logger.info("[DR_CHAOS] %s - action=assert_fail_graceful, expected_success=False, actual_success=%s, expected_action=%s, actual_action=%s",
                    trace_id, result.success, RecoveryAction.SKIP, result.action)
        assert result.success is False
        assert result.action == RecoveryAction.SKIP
        assert "无可用备份" in result.message
        # 测试通过出口日志
        logger.info("[DR_CHAOS] %s - action=test_end, test=test_corrupted_db_without_backup_should_fail_gracefully, result=passed",
                    trace_id)


# ═══════════════════════════════════════════════════════════════
#  2. 配置文件丢失
# ═══════════════════════════════════════════════════════════════

class TestConfigLoss:
    """配置文件丢失的恢复"""

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_lost_config_should_be_restored_from_backup(self, tmp_path):
        """配置文件丢失应从备份恢复

        场景：先备份配置，然后删除配置文件，调用 restore 应恢复。
        """
        set_trace_id("chaos-dr-config-001")
        trace_id = "chaos-dr-config-001"
        # 测试入口日志：配置丢失恢复场景
        logger.info("[DR_CHAOS] %s - action=test_start, test=test_lost_config_should_be_restored_from_backup",
                    trace_id)
        backup_root = tmp_path / "backups"
        config_path = tmp_path / "config.yaml"
        config_path.write_text("key: value\n", encoding="utf-8")

        dr = DisasterRecovery(backup_root=backup_root)
        dr.register("config", config_path)
        # 记录资源注册信息
        logger.info("[DR_CHAOS] %s - action=resource_registered, resource=config, path=%s",
                    trace_id, config_path)

        # 备份
        backup_result = dr.backup("config")
        # 记录备份结果
        logger.info("[DR_CHAOS] %s - action=backup_done, resource=config, success=%s, backup_file=%s",
                    trace_id, backup_result.success, getattr(backup_result, "backup_file", None))
        logger.info("[DR_CHAOS] %s - action=assert_backup_success, expected_success=True, actual_success=%s",
                    trace_id, backup_result.success)
        assert backup_result.success is True

        # 删除配置文件
        config_path.unlink()
        # 记录配置文件丢失注入
        logger.info("[DR_CHAOS] %s - action=config_lost_injected, resource=config, reason=file_unlinked",
                    trace_id)
        assert not config_path.exists()

        # 检测：文件不存在应判为损坏
        integrity_result = dr.check_integrity("config")
        # 记录损坏检测：文件不存在
        logger.info("[DR_CHAOS] %s - action=integrity_check, resource=config, is_corrupted=%s, reason=file_not_exists",
                    trace_id, not integrity_result)
        logger.info("[DR_CHAOS] %s - action=assert_integrity_corrupted, expected=False, actual=%s",
                    trace_id, integrity_result)
        assert integrity_result is False

        # 恢复
        result = dr.restore("config")
        # 记录恢复成功与恢复来源
        logger.info("[DR_CHAOS] %s - action=restore_done, resource=config, success=%s, restore_source=%s",
                    trace_id, result.success, getattr(result, "backup_file", None))
        logger.info("[DR_CHAOS] %s - action=assert_restore_success, expected_success=True, actual_success=%s",
                    trace_id, result.success)
        assert result.success is True
        assert config_path.exists()
        restored_content = config_path.read_text(encoding="utf-8")
        # 记录恢复内容一致性校验
        logger.info("[DR_CHAOS] %s - action=assert_restored_content, expected=%s, actual=%s",
                    trace_id, "key: value\\n", restored_content)
        assert restored_content == "key: value\n"
        # 测试通过出口日志
        logger.info("[DR_CHAOS] %s - action=test_end, test=test_lost_config_should_be_restored_from_backup, result=passed",
                    trace_id)


# ═══════════════════════════════════════════════════════════════
#  3. 服务重启状态恢复
# ═══════════════════════════════════════════════════════════════

class TestStartupRecovery:
    """服务启动时的批量状态恢复"""

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_recover_on_startup_checks_all_resources(self, tmp_path):
        """服务启动时应检查所有资源并恢复损坏的

        场景：注册 3 个资源，其中 1 个损坏。启动恢复应自动修复损坏的。
        """
        set_trace_id("chaos-dr-startup-001")
        trace_id = "chaos-dr-startup-001"
        # 测试入口日志：启动时批量恢复场景
        logger.info("[DR_CHAOS] %s - action=test_start, test=test_recover_on_startup_checks_all_resources",
                    trace_id)
        backup_root = tmp_path / "backups"
        dr = DisasterRecovery(backup_root=backup_root)

        # 3 个资源
        res1 = tmp_path / "res1.txt"
        res2 = tmp_path / "res2.txt"
        res3 = tmp_path / "res3.txt"
        for r, content in [(res1, "data1"), (res2, "data2"), (res3, "data3")]:
            r.write_text(content, encoding="utf-8")
            dr.register(r.name, r)
            # 记录每个资源注册信息
            logger.info("[DR_CHAOS] %s - action=resource_registered, resource=%s, path=%s",
                        trace_id, r.name, r)

        # 先全部备份
        dr.backup_all()
        # 记录全量备份完成
        logger.info("[DR_CHAOS] %s - action=backup_all_done, resource_count=3",
                    trace_id)

        # 损坏 res2
        res2.write_text("CORRUPTED", encoding="utf-8")
        # 记录 res2 损坏注入
        logger.info("[DR_CHAOS] %s - action=corruption_injected, resource=res2.txt, reason=content_overwritten_with_garbage",
                    trace_id)

        # res2 校验函数
        def validator_res2(path):
            try:
                content = Path(path).read_text(encoding="utf-8")
                return content.startswith("data")
            except Exception:
                return False

        validators = {"res2.txt": validator_res2}

        # 启动恢复
        results = dr.recover_on_startup(validators)
        # 记录启动恢复结果总数
        logger.info("[DR_CHAOS] %s - action=recover_on_startup_done, result_count=%d, resources=%s",
                    trace_id, len(results), list(results.keys()))
        # 记录每个资源的恢复动作
        for res_name, res_result in results.items():
            logger.info("[DR_CHAOS] %s - action=startup_recovery_item, resource=%s, success=%s, action=%s, restore_source=%s",
                        trace_id, res_name, res_result.success, res_result.action,
                        getattr(res_result, "backup_file", None))

        # 断言前记录预期与实际
        logger.info("[DR_CHAOS] %s - action=assert_result_count, expected=3, actual=%d",
                    trace_id, len(results))
        # 3 个资源都应处理
        assert len(results) == 3
        # res2 应被恢复
        logger.info("[DR_CHAOS] %s - action=assert_res2_restored, expected_success=True, actual_success=%s, expected_action=%s, actual_action=%s",
                    trace_id, results["res2.txt"].success, RecoveryAction.RESTORE, results["res2.txt"].action)
        assert results["res2.txt"].success is True
        assert results["res2.txt"].action == RecoveryAction.RESTORE
        # res1/res3 应跳过（无需修复）
        logger.info("[DR_CHAOS] %s - action=assert_res1_skipped, expected_action=%s, actual_action=%s",
                    trace_id, RecoveryAction.SKIP, results["res1.txt"].action)
        assert results["res1.txt"].action == RecoveryAction.SKIP
        logger.info("[DR_CHAOS] %s - action=assert_res3_skipped, expected_action=%s, actual_action=%s",
                    trace_id, RecoveryAction.SKIP, results["res3.txt"].action)
        assert results["res3.txt"].action == RecoveryAction.SKIP
        # 验证 res2 内容已恢复
        res2_restored = res2.read_text(encoding="utf-8")
        # 记录 res2 恢复内容校验
        logger.info("[DR_CHAOS] %s - action=assert_res2_content, expected=data2, actual=%s",
                    trace_id, res2_restored)
        assert res2_restored == "data2"
        # 测试通过出口日志
        logger.info("[DR_CHAOS] %s - action=test_end, test=test_recover_on_startup_checks_all_resources, result=passed",
                    trace_id)


# ═══════════════════════════════════════════════════════════════
#  4. 备份与恢复一致性
# ═══════════════════════════════════════════════════════════════

class TestBackupRestoreConsistency:
    """备份与恢复的数据一致性"""

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_multiple_backup_restore_cycles_should_be_consistent(self, tmp_path):
        """多轮备份-修改-恢复循环应保持数据一致

        场景：每轮修改数据并备份，然后恢复到最新备份。
        预期：每次恢复后数据与最新备份一致。
        """
        set_trace_id("chaos-dr-consistency-001")
        trace_id = "chaos-dr-consistency-001"
        # 测试入口日志：多轮备份-恢复一致性场景
        logger.info("[DR_CHAOS] %s - action=test_start, test=test_multiple_backup_restore_cycles_should_be_consistent",
                    trace_id)
        backup_root = tmp_path / "backups"
        data_path = tmp_path / "data.json"
        dr = DisasterRecovery(backup_root=backup_root, max_backups=3)
        dr.register("data", data_path)
        # 记录资源注册信息
        logger.info("[DR_CHAOS] %s - action=resource_registered, resource=data, path=%s",
                    trace_id, data_path)

        # 3 轮备份-修改-恢复
        for cycle in range(3):
            content = json.dumps({"cycle": cycle, "data": f"v{cycle}"})
            data_path.write_text(content, encoding="utf-8")
            backup_result = dr.backup("data")
            # 记录每轮备份结果
            logger.info("[DR_CHAOS] %s - action=backup_cycle, resource=data, cycle=%d, success=%s, backup_file=%s",
                        trace_id, cycle, backup_result.success, getattr(backup_result, "backup_file", None))
            logger.info("[DR_CHAOS] %s - action=assert_backup_success, expected_success=True, actual_success=%s, cycle=%d",
                        trace_id, backup_result.success, cycle)
            assert backup_result.success is True
            time.sleep(0.05)  # 确保时间戳不同

        # 损坏数据
        data_path.write_text("GARBAGE", encoding="utf-8")
        # 记录数据损坏注入
        logger.info("[DR_CHAOS] %s - action=corruption_injected, resource=data, reason=content_overwritten_with_garbage",
                    trace_id)

        # 恢复（应恢复到最新备份 cycle=2）
        restore_result = dr.restore("data")
        # 记录恢复成功与恢复来源
        logger.info("[DR_CHAOS] %s - action=restore_done, resource=data, success=%s, restore_source=%s",
                    trace_id, restore_result.success, getattr(restore_result, "backup_file", None))
        logger.info("[DR_CHAOS] %s - action=assert_restore_success, expected_success=True, actual_success=%s",
                    trace_id, restore_result.success)
        assert restore_result.success is True

        restored = json.loads(data_path.read_text(encoding="utf-8"))
        # 记录恢复内容一致性校验（应恢复到最新备份 cycle=2）
        logger.info("[DR_CHAOS] %s - action=assert_restored_content, expected=%s, actual=%s",
                    trace_id, {"cycle": 2, "data": "v2"}, restored)
        assert restored == {"cycle": 2, "data": "v2"}
        # 测试通过出口日志
        logger.info("[DR_CHAOS] %s - action=test_end, test=test_multiple_backup_restore_cycles_should_be_consistent, result=passed",
                    trace_id)

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_old_backups_should_be_cleaned_up(self, tmp_path):
        """超过 max_backups 的旧备份应被清理

        场景：max_backups=2，创建 5 个备份，应只保留最新 2 个。
        """
        set_trace_id("chaos-dr-consistency-002")
        trace_id = "chaos-dr-consistency-002"
        # 测试入口日志：旧备份清理场景
        logger.info("[DR_CHAOS] %s - action=test_start, test=test_old_backups_should_be_cleaned_up",
                    trace_id)
        backup_root = tmp_path / "backups"
        data_path = tmp_path / "data.txt"
        data_path.write_text("initial", encoding="utf-8")

        dr = DisasterRecovery(backup_root=backup_root, max_backups=2)
        dr.register("data", data_path)
        # 记录资源注册信息
        logger.info("[DR_CHAOS] %s - action=resource_registered, resource=data, path=%s, max_backups=2",
                    trace_id, data_path)

        # 创建 5 个备份
        for i in range(5):
            data_path.write_text(f"v{i}", encoding="utf-8")
            dr.backup("data")
            # 记录每次备份创建
            logger.info("[DR_CHAOS] %s - action=backup_created, resource=data, index=%d",
                        trace_id, i)
            time.sleep(0.05)

        # 验证：备份目录下应只有 2 个 .bak 文件
        backup_files = list((backup_root / "data").glob("*.bak"))
        # 记录清理后保留的备份文件数与文件名
        logger.info("[DR_CHAOS] %s - action=backup_cleanup_check, resource=data, remaining_count=%d, files=%s",
                    trace_id, len(backup_files), [f.name for f in backup_files])
        logger.info("[DR_CHAOS] %s - action=assert_backup_count, expected=2, actual=%d",
                    trace_id, len(backup_files))
        assert len(backup_files) == 2, (
            f"应保留 2 个备份，实际 {len(backup_files)}"
        )
        # 测试通过出口日志
        logger.info("[DR_CHAOS] %s - action=test_end, test=test_old_backups_should_be_cleaned_up, result=passed",
                    trace_id)


# ═══════════════════════════════════════════════════════════════
#  5. 多资源同时损坏
# ═══════════════════════════════════════════════════════════════

class TestMultiResourceFailure:
    """多资源同时损坏的批量恢复"""

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_multi_resource_corruption_should_all_recover(self, tmp_path):
        """多个资源同时损坏应全部能恢复

        场景：3 个资源都损坏，调用 recover_on_startup 应全部恢复。
        """
        set_trace_id("chaos-dr-multi-001")
        trace_id = "chaos-dr-multi-001"
        # 测试入口日志：多资源同时损坏批量恢复场景
        logger.info("[DR_CHAOS] %s - action=test_start, test=test_multi_resource_corruption_should_all_recover",
                    trace_id)
        backup_root = tmp_path / "backups"
        dr = DisasterRecovery(backup_root=backup_root)

        # 3 个资源
        paths = []
        for i in range(3):
            p = tmp_path / f"res_{i}.txt"
            p.write_text(f"original_{i}", encoding="utf-8")
            dr.register(f"res_{i}", p)
            paths.append(p)
            # 记录每个资源注册信息
            logger.info("[DR_CHAOS] %s - action=resource_registered, resource=res_%d, path=%s",
                        trace_id, i, p)

        # 全部备份
        dr.backup_all()
        # 记录全量备份完成
        logger.info("[DR_CHAOS] %s - action=backup_all_done, resource_count=3",
                    trace_id)

        # 全部损坏
        for p in paths:
            p.write_text("CORRUPTED", encoding="utf-8")
        # 记录多资源损坏注入
        logger.info("[DR_CHAOS] %s - action=corruption_injected, resources=%s, reason=all_overwritten_with_corrupted",
                    trace_id, [p.name for p in paths])

        # 校验函数（任何不以 original_ 开头都算损坏）
        def validator(path):
            try:
                return Path(path).read_text(encoding="utf-8").startswith("original_")
            except Exception:
                return False

        validators = {f"res_{i}": validator for i in range(3)}

        # 批量恢复
        results = dr.recover_on_startup(validators)
        # 记录批量恢复结果总数
        logger.info("[DR_CHAOS] %s - action=recover_on_startup_done, result_count=%d, resources=%s",
                    trace_id, len(results), list(results.keys()))
        logger.info("[DR_CHAOS] %s - action=assert_result_count, expected=3, actual=%d",
                    trace_id, len(results))
        assert len(results) == 3

        for i in range(3):
            # 记录每个资源的恢复结果与恢复来源
            logger.info("[DR_CHAOS] %s - action=multi_recovery_item, resource=res_%d, success=%s, action=%s, restore_source=%s, message=%s",
                        trace_id, i, results[f"res_{i}"].success, results[f"res_{i}"].action,
                        getattr(results[f"res_{i}"], "backup_file", None), results[f"res_{i}"].message)
            logger.info("[DR_CHAOS] %s - action=assert_res_restored, resource=res_%d, expected_success=True, actual_success=%s, expected_action=%s, actual_action=%s",
                        trace_id, i, results[f"res_{i}"].success, RecoveryAction.RESTORE, results[f"res_{i}"].action)
            assert results[f"res_{i}"].success is True, (
                f"res_{i} 恢复失败: {results[f'res_{i}'].message}"
            )
            assert results[f"res_{i}"].action == RecoveryAction.RESTORE
            # 验证内容已恢复
            p = paths[i]
            restored_content = p.read_text(encoding="utf-8")
            # 记录每个资源恢复内容校验
            logger.info("[DR_CHAOS] %s - action=assert_res_content, resource=res_%d, expected=%s, actual=%s",
                        trace_id, i, f"original_{i}", restored_content)
            assert restored_content == f"original_{i}"
        # 测试通过出口日志
        logger.info("[DR_CHAOS] %s - action=test_end, test=test_multi_resource_corruption_should_all_recover, result=passed",
                    trace_id)


# ═══════════════════════════════════════════════════════════════
#  6. 配置热重载
# ═══════════════════════════════════════════════════════════════

class TestConfigHotReload:
    """配置文件热重载（不重启服务）"""

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_config_reload_should_invoke_callback(self, tmp_path):
        """配置热重载应调用注册的回调函数

        场景：注册配置资源并附带 reload_callback，
        调用 reload_config 应触发回调。
        """
        set_trace_id("chaos-dr-reload-001")
        trace_id = "chaos-dr-reload-001"
        # 测试入口日志：配置热重载回调触发场景
        logger.info("[DR_CHAOS] %s - action=test_start, test=test_config_reload_should_invoke_callback",
                    trace_id)
        backup_root = tmp_path / "backups"
        config_path = tmp_path / "config.yaml"
        config_path.write_text("initial: true", encoding="utf-8")

        # 热重载回调
        reloaded = {"count": 0, "path": None}

        def reload_callback(path):
            reloaded["count"] += 1
            reloaded["path"] = str(path)

        dr = DisasterRecovery(backup_root=backup_root)
        dr.register("config", config_path, reload_callback=reload_callback)
        # 记录资源注册信息（带 reload_callback）
        logger.info("[DR_CHAOS] %s - action=resource_registered, resource=config, path=%s, has_callback=True",
                    trace_id, config_path)

        # 修改配置文件
        config_path.write_text("updated: true", encoding="utf-8")
        # 记录配置文件更新
        logger.info("[DR_CHAOS] %s - action=config_updated, resource=config, new_content=updated: true",
                    trace_id)

        # 触发热重载
        result = dr.reload_config("config")
        # 记录热重载结果与回调是否被调用
        logger.info("[DR_CHAOS] %s - action=reload_done, resource=config, success=%s, callback_invoked=%s, callback_count=%d",
                    trace_id, result.success, reloaded["count"] > 0, reloaded["count"])
        logger.info("[DR_CHAOS] %s - action=assert_reload_success, expected_success=True, actual_success=%s, expected_action=%s, actual_action=%s",
                    trace_id, result.success, RecoveryAction.RELOAD, result.action)
        assert result.success is True
        assert result.action == RecoveryAction.RELOAD

        # 验证回调被调用
        logger.info("[DR_CHAOS] %s - action=assert_callback_invoked, expected_count=1, actual_count=%d, expected_path=%s, actual_path=%s",
                    trace_id, reloaded["count"], str(config_path), reloaded["path"])
        assert reloaded["count"] == 1
        assert reloaded["path"] == str(config_path)
        # 测试通过出口日志
        logger.info("[DR_CHAOS] %s - action=test_end, test=test_config_reload_should_invoke_callback, result=passed",
                    trace_id)

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_reload_without_callback_should_fail_gracefully(self, tmp_path):
        """未注册回调时热重载应优雅失败

        场景：注册资源但未提供 reload_callback。
        预期：返回失败结果，不抛异常。
        """
        set_trace_id("chaos-dr-reload-002")
        trace_id = "chaos-dr-reload-002"
        # 测试入口日志：无回调时热重载应优雅失败
        logger.info("[DR_CHAOS] %s - action=test_start, test=test_reload_without_callback_should_fail_gracefully",
                    trace_id)
        backup_root = tmp_path / "backups"
        config_path = tmp_path / "config.yaml"
        config_path.write_text("data", encoding="utf-8")

        dr = DisasterRecovery(backup_root=backup_root)
        dr.register("config", config_path)  # 不提供 callback
        # 记录资源注册信息（无 reload_callback）
        logger.info("[DR_CHAOS] %s - action=resource_registered, resource=config, path=%s, has_callback=False",
                    trace_id, config_path)

        result = dr.reload_config("config")
        # 记录热重载失败结果与错误信息
        logger.info("[DR_CHAOS] %s - action=reload_failed, resource=config, success=%s, callback_invoked=False, error_message=%s",
                    trace_id, result.success, result.message)
        logger.info("[DR_CHAOS] %s - action=assert_reload_fail, expected_success=False, actual_success=%s, expected_action=%s, actual_action=%s",
                    trace_id, result.success, RecoveryAction.SKIP, result.action)
        assert result.success is False
        assert result.action == RecoveryAction.SKIP
        # 记录断言：错误信息应包含"未注册热重载回调"
        logger.info("[DR_CHAOS] %s - action=assert_error_message, expected_contains=未注册热重载回调, actual_message=%s",
                    trace_id, result.message)
        assert "未注册热重载回调" in result.message
        # 测试通过出口日志
        logger.info("[DR_CHAOS] %s - action=test_end, test=test_reload_without_callback_should_fail_gracefully, result=passed",
                    trace_id)

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_reload_callback_exception_should_be_caught(self, tmp_path):
        """热重载回调抛异常应被捕获，返回失败结果

        场景：回调函数抛异常。
        预期：reload_config 返回失败，不抛异常。
        """
        set_trace_id("chaos-dr-reload-003")
        trace_id = "chaos-dr-reload-003"
        # 测试入口日志：回调抛异常时热重载应被捕获
        logger.info("[DR_CHAOS] %s - action=test_start, test=test_reload_callback_exception_should_be_caught",
                    trace_id)
        backup_root = tmp_path / "backups"
        config_path = tmp_path / "config.yaml"
        config_path.write_text("data", encoding="utf-8")

        def bad_callback(path):
            raise RuntimeError("callback failed")

        dr = DisasterRecovery(backup_root=backup_root)
        dr.register("config", config_path, reload_callback=bad_callback)
        # 记录资源注册信息（带会抛异常的回调）
        logger.info("[DR_CHAOS] %s - action=resource_registered, resource=config, path=%s, has_callback=True, callback_raises=True",
                    trace_id, config_path)

        result = dr.reload_config("config")
        # 记录热重载失败结果与错误信息（异常被捕获）
        logger.info("[DR_CHAOS] %s - action=reload_failed, resource=config, success=%s, callback_invoked=True, callback_raised_exception=True, error_message=%s",
                    trace_id, result.success, result.message)
        logger.info("[DR_CHAOS] %s - action=assert_reload_fail, expected_success=False, actual_success=%s, expected_action=%s, actual_action=%s",
                    trace_id, result.success, RecoveryAction.RELOAD, result.action)
        assert result.success is False
        assert result.action == RecoveryAction.RELOAD
        # 记录断言：错误信息应包含"热重载失败"
        logger.info("[DR_CHAOS] %s - action=assert_error_message, expected_contains=热重载失败, actual_message=%s",
                    trace_id, result.message)
        assert "热重载失败" in result.message
        # 测试通过出口日志
        logger.info("[DR_CHAOS] %s - action=test_end, test=test_reload_callback_exception_should_be_caught, result=passed",
                    trace_id)
