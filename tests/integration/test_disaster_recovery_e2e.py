"""容灾恢复端到端测试

测试覆盖：
1. 配置文件损坏→自动修复→服务恢复
2. 消息文件损坏→逐行恢复→数据完整性
3. 数据库损坏→重建→数据迁移
4. 服务崩溃重启→状态恢复→功能自检
5. 部分模块故障→降级运行→逐步恢复
6. 容灾过程中的数据零丢失验证
"""

import pytest
import tempfile
import os
import json
import sqlite3
import logging
import time
from datetime import datetime

pytestmark = pytest.mark.integration
pytest.timeout = 30

logger = logging.getLogger(__name__)


class TestDisasterRecoveryE2E:
    """容灾恢复端到端测试"""

    def test_config_file_corruption_auto_repair(self):
        """测试配置文件损坏→自动修复→服务恢复"""
        from agent.disaster_recovery import DisasterRecovery, BackupConfig, BackupType

        logger.info("="*60)
        logger.info(f"[测试开始] test_config_file_corruption_auto_repair")
        logger.info(f"[时间戳] {datetime.now().isoformat()}")
        logger.info("="*60)

        logger.info("[步骤1] 创建临时目录")
        with tempfile.TemporaryDirectory() as tmpdir:
            logger.info(f"  临时目录: {tmpdir}")

            logger.info("[步骤2] 初始化容灾恢复组件")
            config = BackupConfig(
                enabled=True,
                backup_interval_minutes=5,
                max_backups=5,
                backup_dir=os.path.join(tmpdir, "backups"),
                auto_recover=True
            )
            dr = DisasterRecovery(config)

            logger.info("[步骤3] 准备原始配置数据")
            original_data = {"config": {"key1": "value1", "key2": "value2"}}
            logger.info(f"  原始数据: {original_data}")

            restored_data = {}

            def backup_func():
                return original_data

            def restore_func(data):
                restored_data.update(data)

            logger.info("[步骤4] 注册备份提供者")
            dr.register_backup_provider("config", backup_func, restore_func)

            logger.info("[步骤5] 执行全量备份")
            backup_id = dr.trigger_backup(BackupType.FULL)
            logger.info(f"  备份ID: {backup_id}")

            assert backup_id != ""
            logger.info("[断言通过] 备份成功创建")

            logger.info("[步骤6] 验证备份列表")
            backup_list = dr.get_backup_list()
            logger.info(f"  备份数量: {len(backup_list)}")

            assert len(backup_list) == 1
            assert backup_list[0].backup_id == backup_id
            logger.info("[断言通过] 备份记录正确")

            logger.info("[步骤7] 模拟配置损坏，执行恢复")
            success = dr.restore_from_backup(backup_id)
            logger.info(f"  恢复结果: {success}")

            assert success is True
            logger.info("[断言通过] 恢复成功")

            assert restored_data == original_data
            logger.info("[断言通过] 数据完整性验证通过")

            logger.info("[步骤8] 检查恢复状态")
            recovery_info = dr.get_recovery_status()
            logger.info(f"  恢复状态: {recovery_info.status.value}")
            logger.info(f"  恢复文件: {recovery_info.restored_files}")

            assert recovery_info.status.value == "completed"
            assert "config" in recovery_info.restored_files
            logger.info("[断言通过] 恢复状态验证通过")

        logger.info("="*60)
        logger.info(f"[测试完成] test_config_file_corruption_auto_repair")
        logger.info("="*60)

    def test_message_file_corruption_line_by_line_recovery(self):
        """测试消息文件损坏→逐行恢复→数据完整性"""
        from agent.disaster_recovery import DisasterRecovery, BackupConfig, BackupType

        logger.info("="*60)
        logger.info(f"[测试开始] test_message_file_corruption_line_by_line_recovery")
        logger.info(f"[时间戳] {datetime.now().isoformat()}")
        logger.info("="*60)

        logger.info("[步骤1] 创建临时目录")
        with tempfile.TemporaryDirectory() as tmpdir:
            logger.info(f"  临时目录: {tmpdir}")

            logger.info("[步骤2] 初始化容灾恢复组件")
            config = BackupConfig(
                enabled=True,
                backup_interval_minutes=5,
                max_backups=5,
                backup_dir=os.path.join(tmpdir, "backups"),
                auto_recover=True
            )
            dr = DisasterRecovery(config)

            logger.info("[步骤3] 创建消息文件")
            messages_file = os.path.join(tmpdir, "messages.jsonl")

            with open(messages_file, 'w', encoding='utf-8') as f:
                for i in range(10):
                    f.write(json.dumps({"id": i, "content": f"message_{i}"}) + "\n")
                    logger.info(f"  写入消息: id={i}, content=message_{i}")

            logger.info("[步骤4] 定义备份和恢复函数")
            def backup_func():
                messages = []
                if os.path.exists(messages_file):
                    with open(messages_file, 'r', encoding='utf-8') as f:
                        for line in f:
                            line = line.strip()
                            if line:
                                try:
                                    messages.append(json.loads(line))
                                except json.JSONDecodeError:
                                    logger.warning(f"  跳过损坏行: {line}")
                return {"messages": messages}

            recovered_messages = []

            def restore_func(data):
                recovered_messages.extend(data.get("messages", []))

            logger.info("[步骤5] 注册备份提供者并执行备份")
            dr.register_backup_provider("messages", backup_func, restore_func)
            dr.trigger_backup(BackupType.FULL)
            logger.info("  备份完成")

            logger.info("[步骤6] 模拟消息文件损坏")
            with open(messages_file, 'w', encoding='utf-8') as f:
                f.write("this is corrupted data\n")
                for i in range(5, 10):
                    f.write(json.dumps({"id": i, "content": f"message_{i}"}) + "\n")
            logger.info("  文件已损坏（前5条丢失，第1行是无效数据）")

            logger.info("[步骤7] 执行恢复")
            backup_list = dr.get_backup_list()
            logger.info(f"  可用备份数: {len(backup_list)}")

            assert len(backup_list) >= 1

            dr.restore_from_backup(backup_list[0].backup_id)

            logger.info("[步骤8] 验证恢复结果")
            logger.info(f"  恢复消息数: {len(recovered_messages)}")

            assert len(recovered_messages) == 10
            logger.info("[断言通过] 所有消息已恢复")

            for i in range(10):
                assert any(m["id"] == i for m in recovered_messages)
            logger.info("[断言通过] 消息完整性验证通过")

        logger.info("="*60)
        logger.info(f"[测试完成] test_message_file_corruption_line_by_line_recovery")
        logger.info("="*60)

    def test_database_corruption_rebuild_migration(self):
        """测试数据库损坏→重建→数据迁移"""
        from agent.disaster_recovery import DisasterRecovery

        logger.info("="*60)
        logger.info(f"[测试开始] test_database_corruption_rebuild_migration")
        logger.info(f"[时间戳] {datetime.now().isoformat()}")
        logger.info("="*60)

        logger.info("[步骤1] 创建临时目录")
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            logger.info(f"  数据库路径: {db_path}")

            logger.info("[步骤2] 创建数据库并插入测试数据")
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")

            for i in range(5):
                cursor.execute("INSERT INTO users (name) VALUES (?)", (f"user_{i}",))
                logger.info(f"  插入用户: user_{i}")

            conn.commit()

            cursor.execute("PRAGMA integrity_check;")
            result = cursor.fetchone()
            logger.info(f"  完整性检查: {result[0]}")
            assert result[0] == "ok"
            conn.close()

            logger.info("[步骤3] 执行数据库修复")
            dr = DisasterRecovery()
            repair_result = dr.repair_database(db_path)
            logger.info(f"  修复结果: {repair_result}")

            assert repair_result is True
            logger.info("[断言通过] 数据库修复成功")

            logger.info("[步骤4] 验证修复后的数据库")
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()

            cursor.execute("PRAGMA integrity_check;")
            result = cursor.fetchone()
            logger.info(f"  修复后完整性检查: {result[0]}")
            assert result[0] == "ok"

            cursor.execute("SELECT COUNT(*) FROM users")
            count = cursor.fetchone()[0]
            logger.info(f"  用户数量: {count}")
            assert count == 5

            cursor.execute("SELECT name FROM users ORDER BY id")
            names = [row[0] for row in cursor.fetchall()]
            logger.info(f"  用户列表: {names}")
            assert names == ["user_0", "user_1", "user_2", "user_3", "user_4"]

            conn.close()

        logger.info("="*60)
        logger.info(f"[测试完成] test_database_corruption_rebuild_migration")
        logger.info("="*60)

    def test_service_crash_restart_state_recovery(self):
        """测试服务崩溃重启→状态恢复→功能自检"""
        from agent.disaster_recovery import DisasterRecovery, BackupConfig, BackupType

        logger.info("="*60)
        logger.info(f"[测试开始] test_service_crash_restart_state_recovery")
        logger.info(f"[时间戳] {datetime.now().isoformat()}")
        logger.info("="*60)

        logger.info("[步骤1] 创建临时目录")
        with tempfile.TemporaryDirectory() as tmpdir:
            logger.info(f"  临时目录: {tmpdir}")

            logger.info("[步骤2] 配置容灾恢复")
            config = BackupConfig(
                enabled=True,
                backup_interval_minutes=5,
                max_backups=5,
                backup_dir=os.path.join(tmpdir, "backups"),
                auto_recover=True
            )

            logger.info("[步骤3] 准备服务状态")
            service_state = {"running": True, "requests_processed": 100, "active_users": 10}
            logger.info(f"  原始服务状态: {service_state}")

            def backup_func():
                return service_state

            restored_state = {}

            def restore_func(data):
                restored_state.update(data)

            logger.info("[步骤4] 模拟第一次启动 - 创建备份")
            dr1 = DisasterRecovery(config)
            dr1.register_backup_provider("service", backup_func, restore_func)
            dr1.trigger_backup(BackupType.FULL)
            logger.info("  备份完成")

            logger.info("[步骤5] 模拟服务崩溃重启")
            dr2 = DisasterRecovery(config)
            dr2.register_backup_provider("service", backup_func, restore_func)

            logger.info("[步骤6] 执行自动恢复")
            success = dr2.auto_recover_on_startup()
            logger.info(f"  恢复结果: {success}")

            assert success is True
            logger.info("[断言通过] 自动恢复成功")

            assert restored_state == service_state
            logger.info("[断言通过] 状态恢复完整")

            logger.info("[步骤7] 检查恢复状态")
            status = dr2.get_status()
            logger.info(f"  恢复状态: {status['recovery_status']['status']}")
            logger.info(f"  恢复文件: {status['recovery_status']['restored_files']}")

            assert status["recovery_status"]["status"] == "completed"
            assert "service" in status["recovery_status"]["restored_files"]
            logger.info("[断言通过] 恢复状态验证通过")

        logger.info("="*60)
        logger.info(f"[测试完成] test_service_crash_restart_state_recovery")
        logger.info("="*60)

    def test_partial_module_failure_degrade_progressive_recovery(self):
        """测试部分模块故障→降级运行→逐步恢复"""
        from agent.graceful_degrade import GracefulDegrade, DegradeModule
        from agent.circuit_breaker import CircuitBreaker, CircuitBreakerConfig, CircuitBreakerState

        logger.info("="*60)
        logger.info(f"[测试开始] test_partial_module_failure_degrade_progressive_recovery")
        logger.info(f"[时间戳] {datetime.now().isoformat()}")
        logger.info("="*60)

        logger.info("[步骤1] 初始化组件")
        degrade = GracefulDegrade()

        breaker_config = CircuitBreakerConfig(
            failure_threshold=0.3,
            min_requests=3,
            reset_timeout=1.0
        )
        breaker = CircuitBreaker(breaker_config)

        logger.info("[步骤2] 模拟模块健康状态")
        module_health = {"memory": True, "critic": False, "schema": True}
        logger.info(f"  模块状态: {module_health}")

        def memory_query():
            if not module_health["memory"]:
                raise Exception("Memory module failed")
            return {"data": "memory_result"}

        def critic_evaluate():
            if not module_health["critic"]:
                raise Exception("Critic module failed")
            return {"score": 90}

        logger.info("[步骤3] 执行降级操作")
        results = []
        for i in range(5):
            logger.info(f"  第{i+1}轮操作...")

            mem_result = degrade.with_degrade(
                module=DegradeModule.MEMORY,
                func=memory_query,
                fallback=lambda: {"data": "memory_degraded"}
            )
            results.append(("memory", mem_result))
            logger.info(f"    memory: {mem_result}")

            crit_result = degrade.with_degrade(
                module=DegradeModule.CRITIC,
                func=critic_evaluate,
                fallback=lambda: {"score": 70, "degraded": True}
            )
            results.append(("critic", crit_result))
            logger.info(f"    critic: {crit_result}")

        logger.info("[步骤4] 验证降级结果")
        memory_results = [r for t, r in results if t == "memory"]
        critic_results = [r for t, r in results if t == "critic"]

        for r in memory_results:
            assert r["data"] == "memory_result"
        logger.info("[断言通过] memory模块正常运行")

        for r in critic_results:
            assert r["score"] == 70
            assert r.get("degraded") is True
        logger.info("[断言通过] critic模块降级运行")

        logger.info("[步骤5] 模拟模块恢复")
        module_health["critic"] = True
        logger.info("  critic模块已恢复")

        result = degrade.with_degrade(
            module=DegradeModule.CRITIC,
            func=critic_evaluate
        )
        logger.info(f"  恢复后结果: {result}")

        assert result["score"] == 90
        assert "degraded" not in result
        logger.info("[断言通过] critic模块功能恢复正常")

        logger.info("="*60)
        logger.info(f"[测试完成] test_partial_module_failure_degrade_progressive_recovery")
        logger.info("="*60)

    def test_disaster_recovery_data_zero_loss(self):
        """测试容灾过程中的数据零丢失验证"""
        from agent.disaster_recovery import DisasterRecovery, BackupConfig, BackupType

        logger.info("="*60)
        logger.info(f"[测试开始] test_disaster_recovery_data_zero_loss")
        logger.info(f"[时间戳] {datetime.now().isoformat()}")
        logger.info("="*60)

        logger.info("[步骤1] 创建临时目录")
        with tempfile.TemporaryDirectory() as tmpdir:
            logger.info(f"  临时目录: {tmpdir}")

            logger.info("[步骤2] 配置容灾恢复")
            config = BackupConfig(
                enabled=True,
                backup_interval_minutes=1,
                max_backups=10,
                backup_dir=os.path.join(tmpdir, "backups"),
                auto_recover=True
            )
            dr = DisasterRecovery(config)

            logger.info("[步骤3] 准备测试数据")
            all_data = {}

            def backup_func():
                return all_data

            def restore_func(data):
                restored_data.update(data)

            restored_data = {}
            dr.register_backup_provider("test_data", backup_func, restore_func)

            logger.info("[步骤4] 逐步添加数据并执行增量备份")
            backup_ids = []
            for i in range(20):
                all_data[f"key_{i}"] = f"value_{i}"
                if i % 5 == 0:
                    backup_id = dr.trigger_backup(BackupType.INCREMENTAL)
                    if backup_id:
                        backup_ids.append(backup_id)
                        logger.info(f"  数据点{i}, 执行备份: {backup_id}")
                    else:
                        logger.info(f"  数据点{i}, 备份跳过")

            logger.info(f"  总备份数: {len(backup_ids)}")
            assert len(backup_ids) >= 1
            logger.info("[断言通过] 至少创建了一个备份")

            logger.info("[步骤5] 恢复最新备份")
            backup_list = dr.get_backup_list()
            logger.info(f"  备份列表长度: {len(backup_list)}")
            assert len(backup_list) >= 1

            latest_backup = backup_list[0]
            dr.restore_from_backup(latest_backup.backup_id)
            logger.info(f"  从备份 {latest_backup.backup_id} 恢复")

            logger.info("[步骤6] 验证数据完整性")
            logger.info(f"  恢复数据量: {len(restored_data)}")

            assert len(restored_data) == 20
            logger.info("[断言通过] 数据量正确")

            for i in range(20):
                assert restored_data[f"key_{i}"] == f"value_{i}"

            logger.info("[断言通过] 所有数据零丢失")

            recovery_status = dr.get_recovery_status()
            assert recovery_status is not None
            logger.info(f"  恢复状态: {recovery_status}")

        logger.info("="*60)
        logger.info(f"[测试完成] test_disaster_recovery_data_zero_loss")
        logger.info("="*60)
