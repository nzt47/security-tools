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
import sys
import time
from pathlib import Path

import pytest

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
        backup_root = tmp_path / "backups"
        db_path = tmp_path / "memory.db"
        # 写入正常数据
        db_path.write_text(json.dumps({"key": "value"}), encoding="utf-8")

        dr = DisasterRecovery(backup_root=backup_root, max_backups=3)
        dr.register("memory_db", db_path)

        # 创建备份
        backup_result = dr.backup("memory_db")
        assert backup_result.success is True
        assert backup_result.action == RecoveryAction.BACKUP

        # 损坏数据库
        db_path.write_text("CORRUPTED_DATA<<<>>>", encoding="utf-8")

        # 定义完整性校验函数
        def validator(path):
            try:
                content = Path(path).read_text(encoding="utf-8")
                json.loads(content)  # 损坏数据会抛 JSONDecodeError
                return True
            except Exception:
                return False

        # 检测到损坏应返回 False
        assert dr.check_integrity("memory_db", validator) is False

        # 修复
        repair_result = dr.repair_if_corrupted("memory_db", validator)
        assert repair_result.success is True
        assert repair_result.action == RecoveryAction.RESTORE

        # 验证：数据库已恢复
        restored_content = db_path.read_text(encoding="utf-8")
        restored_data = json.loads(restored_content)
        assert restored_data == {"key": "value"}

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_corrupted_db_without_backup_should_fail_gracefully(self, tmp_path):
        """无备份时数据库损坏应优雅失败

        场景：数据库损坏但无备份，repair_if_corrupted 应返回失败结果。
        预期：恢复失败，但不应抛异常。
        """
        set_trace_id("chaos-dr-db-002")
        backup_root = tmp_path / "backups"
        db_path = tmp_path / "memory.db"
        db_path.write_text("CORRUPTED", encoding="utf-8")

        dr = DisasterRecovery(backup_root=backup_root)
        dr.register("memory_db", db_path)

        def validator(path):
            return False  # 始终认为损坏

        # 无备份，应失败但不抛异常
        result = dr.repair_if_corrupted("memory_db", validator)
        assert result.success is False
        assert result.action == RecoveryAction.SKIP
        assert "无可用备份" in result.message


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
        backup_root = tmp_path / "backups"
        config_path = tmp_path / "config.yaml"
        config_path.write_text("key: value\n", encoding="utf-8")

        dr = DisasterRecovery(backup_root=backup_root)
        dr.register("config", config_path)

        # 备份
        assert dr.backup("config").success is True

        # 删除配置文件
        config_path.unlink()
        assert not config_path.exists()

        # 检测：文件不存在应判为损坏
        assert dr.check_integrity("config") is False

        # 恢复
        result = dr.restore("config")
        assert result.success is True
        assert config_path.exists()
        assert config_path.read_text(encoding="utf-8") == "key: value\n"


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
        backup_root = tmp_path / "backups"
        dr = DisasterRecovery(backup_root=backup_root)

        # 3 个资源
        res1 = tmp_path / "res1.txt"
        res2 = tmp_path / "res2.txt"
        res3 = tmp_path / "res3.txt"
        for r, content in [(res1, "data1"), (res2, "data2"), (res3, "data3")]:
            r.write_text(content, encoding="utf-8")
            dr.register(r.name, r)

        # 先全部备份
        dr.backup_all()

        # 损坏 res2
        res2.write_text("CORRUPTED", encoding="utf-8")

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

        # 3 个资源都应处理
        assert len(results) == 3
        # res2 应被恢复
        assert results["res2.txt"].success is True
        assert results["res2.txt"].action == RecoveryAction.RESTORE
        # res1/res3 应跳过（无需修复）
        assert results["res1.txt"].action == RecoveryAction.SKIP
        assert results["res3.txt"].action == RecoveryAction.SKIP
        # 验证 res2 内容已恢复
        assert res2.read_text(encoding="utf-8") == "data2"


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
        backup_root = tmp_path / "backups"
        data_path = tmp_path / "data.json"
        dr = DisasterRecovery(backup_root=backup_root, max_backups=3)
        dr.register("data", data_path)

        # 3 轮备份-修改-恢复
        for cycle in range(3):
            content = json.dumps({"cycle": cycle, "data": f"v{cycle}"})
            data_path.write_text(content, encoding="utf-8")
            backup_result = dr.backup("data")
            assert backup_result.success is True
            time.sleep(0.05)  # 确保时间戳不同

        # 损坏数据
        data_path.write_text("GARBAGE", encoding="utf-8")

        # 恢复（应恢复到最新备份 cycle=2）
        restore_result = dr.restore("data")
        assert restore_result.success is True

        restored = json.loads(data_path.read_text(encoding="utf-8"))
        assert restored == {"cycle": 2, "data": "v2"}

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_old_backups_should_be_cleaned_up(self, tmp_path):
        """超过 max_backups 的旧备份应被清理

        场景：max_backups=2，创建 5 个备份，应只保留最新 2 个。
        """
        set_trace_id("chaos-dr-consistency-002")
        backup_root = tmp_path / "backups"
        data_path = tmp_path / "data.txt"
        data_path.write_text("initial", encoding="utf-8")

        dr = DisasterRecovery(backup_root=backup_root, max_backups=2)
        dr.register("data", data_path)

        # 创建 5 个备份
        for i in range(5):
            data_path.write_text(f"v{i}", encoding="utf-8")
            dr.backup("data")
            time.sleep(0.05)

        # 验证：备份目录下应只有 2 个 .bak 文件
        backup_files = list((backup_root / "data").glob("*.bak"))
        assert len(backup_files) == 2, (
            f"应保留 2 个备份，实际 {len(backup_files)}"
        )


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
        backup_root = tmp_path / "backups"
        dr = DisasterRecovery(backup_root=backup_root)

        # 3 个资源
        paths = []
        for i in range(3):
            p = tmp_path / f"res_{i}.txt"
            p.write_text(f"original_{i}", encoding="utf-8")
            dr.register(f"res_{i}", p)
            paths.append(p)

        # 全部备份
        dr.backup_all()

        # 全部损坏
        for p in paths:
            p.write_text("CORRUPTED", encoding="utf-8")

        # 校验函数（任何不以 original_ 开头都算损坏）
        def validator(path):
            try:
                return Path(path).read_text(encoding="utf-8").startswith("original_")
            except Exception:
                return False

        validators = {f"res_{i}": validator for i in range(3)}

        # 批量恢复
        results = dr.recover_on_startup(validators)
        assert len(results) == 3

        for i in range(3):
            assert results[f"res_{i}"].success is True, (
                f"res_{i} 恢复失败: {results[f'res_{i}'].message}"
            )
            assert results[f"res_{i}"].action == RecoveryAction.RESTORE
            # 验证内容已恢复
            p = paths[i]
            assert p.read_text(encoding="utf-8") == f"original_{i}"


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

        # 修改配置文件
        config_path.write_text("updated: true", encoding="utf-8")

        # 触发热重载
        result = dr.reload_config("config")
        assert result.success is True
        assert result.action == RecoveryAction.RELOAD

        # 验证回调被调用
        assert reloaded["count"] == 1
        assert reloaded["path"] == str(config_path)

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_reload_without_callback_should_fail_gracefully(self, tmp_path):
        """未注册回调时热重载应优雅失败

        场景：注册资源但未提供 reload_callback。
        预期：返回失败结果，不抛异常。
        """
        set_trace_id("chaos-dr-reload-002")
        backup_root = tmp_path / "backups"
        config_path = tmp_path / "config.yaml"
        config_path.write_text("data", encoding="utf-8")

        dr = DisasterRecovery(backup_root=backup_root)
        dr.register("config", config_path)  # 不提供 callback

        result = dr.reload_config("config")
        assert result.success is False
        assert result.action == RecoveryAction.SKIP
        assert "未注册热重载回调" in result.message

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_reload_callback_exception_should_be_caught(self, tmp_path):
        """热重载回调抛异常应被捕获，返回失败结果

        场景：回调函数抛异常。
        预期：reload_config 返回失败，不抛异常。
        """
        set_trace_id("chaos-dr-reload-003")
        backup_root = tmp_path / "backups"
        config_path = tmp_path / "config.yaml"
        config_path.write_text("data", encoding="utf-8")

        def bad_callback(path):
            raise RuntimeError("callback failed")

        dr = DisasterRecovery(backup_root=backup_root)
        dr.register("config", config_path, reload_callback=bad_callback)

        result = dr.reload_config("config")
        assert result.success is False
        assert result.action == RecoveryAction.RELOAD
        assert "热重载失败" in result.message
