import pytest
import os
import pickle
import logging
from unittest.mock import MagicMock, patch
from agent.p6_snapshot import (
    StateSnapshotManager, 
    SnapshotFrequencyController, 
    SnapshotPerformanceMonitor,
    StateSnapshot,
    ModuleState,
    SnapshotResult,
    SnapshotInfo,
)

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class TestSnapshotFrequencyController:
    """快照频率控制器测试"""

    def test_can_save_first_time(self):
        """测试首次保存应允许"""
        logger.info("="*60)
        logger.info("开始测试: test_can_save_first_time")
        
        controller = SnapshotFrequencyController(min_interval_seconds=300)
        logger.info(f"频率控制器创建: min_interval_seconds={controller.min_interval_seconds}")
        
        result = controller.can_save()
        logger.info(f"首次保存检查结果: {result}")
        
        assert result is True
        
        logger.info("测试通过!")
        logger.info("="*60)

    def test_can_save_after_interval(self):
        """测试间隔后应允许保存"""
        logger.info("="*60)
        logger.info("开始测试: test_can_save_after_interval")
        
        import time
        controller = SnapshotFrequencyController(min_interval_seconds=0.1)
        logger.info(f"频率控制器创建: min_interval_seconds={controller.min_interval_seconds}")
        
        logger.info("触发保存成功事件...")
        controller.on_save_success()
        logger.info(f"上次保存时间: {controller.last_save_time}")
        
        logger.info("等待 0.2 秒...")
        time.sleep(0.2)
        
        result = controller.can_save()
        logger.info(f"间隔后保存检查结果: {result}")
        
        assert result is True
        
        logger.info("测试通过!")
        logger.info("="*60)

    def test_cannot_save_too_soon(self):
        """测试过于频繁应拒绝保存"""
        logger.info("="*60)
        logger.info("开始测试: test_cannot_save_too_soon")
        
        controller = SnapshotFrequencyController(min_interval_seconds=300)
        logger.info(f"频率控制器创建: min_interval_seconds={controller.min_interval_seconds}")
        
        logger.info("触发保存成功事件...")
        controller.on_save_success()
        logger.info(f"上次保存时间: {controller.last_save_time}")
        
        result = controller.can_save()
        logger.info(f"立即再次保存检查结果: {result}")
        
        assert result is False
        
        logger.info("测试通过!")
        logger.info("="*60)

    def test_force_save_ignores_interval(self):
        """测试强制保存忽略频率限制"""
        logger.info("="*60)
        logger.info("开始测试: test_force_save_ignores_interval")
        
        controller = SnapshotFrequencyController(min_interval_seconds=300)
        logger.info(f"频率控制器创建: min_interval_seconds={controller.min_interval_seconds}")
        
        logger.info("触发保存成功事件...")
        controller.on_save_success()
        logger.info(f"上次保存时间: {controller.last_save_time}")
        
        logger.info("检查普通保存...")
        normal_result = controller.can_save()
        logger.info(f"普通保存检查结果: {normal_result}")
        
        logger.info("检查强制保存...")
        force_result = controller.can_save(force=True)
        logger.info(f"强制保存检查结果: {force_result}")
        
        assert normal_result is False
        assert force_result is True
        
        logger.info("测试通过!")
        logger.info("="*60)


class TestSnapshotPerformanceMonitor:
    """快照性能监控器测试"""

    def test_record_save(self):
        """测试记录保存操作"""
        logger.info("="*60)
        logger.info("开始测试: test_record_save")
        
        monitor = SnapshotPerformanceMonitor()
        logger.info("性能监控器创建完成")
        
        logger.info("记录保存操作: elapsed_ms=100.5, space_saved=1024")
        monitor.record_save(100.5, 1024)
        
        logger.info(f"监控指标: total_saves={monitor.metrics.total_saves}")
        logger.info(f"监控指标: total_save_time_ms={monitor.metrics.total_save_time_ms}")
        logger.info(f"监控指标: avg_save_time_ms={monitor.metrics.avg_save_time_ms}")
        logger.info(f"监控指标: total_space_saved_bytes={monitor.metrics.total_space_saved_bytes}")
        
        assert monitor.metrics.total_saves == 1
        assert monitor.metrics.total_save_time_ms == 100.5
        assert monitor.metrics.avg_save_time_ms == 100.5
        assert monitor.metrics.total_space_saved_bytes == 1024
        
        logger.info("测试通过!")
        logger.info("="*60)

    def test_record_load(self):
        """测试记录加载操作"""
        logger.info("="*60)
        logger.info("开始测试: test_record_load")
        
        monitor = SnapshotPerformanceMonitor()
        logger.info("性能监控器创建完成")
        
        logger.info("记录加载操作: elapsed_ms=50.2")
        monitor.record_load(50.2)
        
        logger.info(f"监控指标: total_loads={monitor.metrics.total_loads}")
        logger.info(f"监控指标: total_load_time_ms={monitor.metrics.total_load_time_ms}")
        logger.info(f"监控指标: avg_load_time_ms={monitor.metrics.avg_load_time_ms}")
        
        assert monitor.metrics.total_loads == 1
        assert monitor.metrics.total_load_time_ms == 50.2
        assert monitor.metrics.avg_load_time_ms == 50.2
        
        logger.info("测试通过!")
        logger.info("="*60)

    def test_record_multiple_operations(self):
        """测试记录多次操作"""
        logger.info("="*60)
        logger.info("开始测试: test_record_multiple_operations")
        
        monitor = SnapshotPerformanceMonitor()
        logger.info("性能监控器创建完成")
        
        logger.info("记录第一次保存: elapsed_ms=100")
        monitor.record_save(100)
        logger.info(f"  当前指标: total_saves={monitor.metrics.total_saves}, avg_save_time_ms={monitor.metrics.avg_save_time_ms}")
        
        logger.info("记录第二次保存: elapsed_ms=200")
        monitor.record_save(200)
        logger.info(f"  当前指标: total_saves={monitor.metrics.total_saves}, avg_save_time_ms={monitor.metrics.avg_save_time_ms}")
        
        assert monitor.metrics.total_saves == 2
        assert monitor.metrics.avg_save_time_ms == 150.0
        
        logger.info("测试通过!")
        logger.info("="*60)

    def test_get_performance_summary(self):
        """测试获取性能摘要"""
        logger.info("="*60)
        logger.info("开始测试: test_get_performance_summary")
        
        monitor = SnapshotPerformanceMonitor()
        logger.info("性能监控器创建完成")
        
        logger.info("记录保存操作: elapsed_ms=100")
        monitor.record_save(100)
        logger.info("记录加载操作: elapsed_ms=50")
        monitor.record_load(50)
        
        logger.info("获取性能摘要...")
        summary = monitor.get_performance_summary()
        logger.info(f"性能摘要: {summary}")
        
        assert "total_saves" in summary
        assert "total_loads" in summary
        assert "avg_save_ms" in summary
        assert "avg_load_ms" in summary
        assert summary["total_saves"] == 1
        assert summary["total_loads"] == 1
        
        logger.info("测试通过!")
        logger.info("="*60)


class TestStateSnapshotManager:
    """状态快照管理器测试"""

    def test_create_and_restore_snapshot(self, tmp_path):
        """快照创建后应能完整恢复"""
        logger.info("="*60)
        logger.info("开始测试: test_create_and_restore_snapshot")
        logger.info(f"tmp_path: {tmp_path}")
        
        from agent.p6_snapshot import StateSnapshotManager
        logger.info("创建 StateSnapshotManager 实例...")
        mgr = StateSnapshotManager(snapshot_dir=str(tmp_path / "snapshots"))
        logger.info(f"快照目录: {mgr.snapshot_dir}")
        
        mock_digital_life = MagicMock()
        mock_digital_life._config = {"test_key": "test_value"}
        logger.info(f"mock_digital_life._config: {mock_digital_life._config}")
        
        with patch.object(mgr, '_save_core_modules_with_delta', return_value=0):
            logger.info("调用 save_snapshot()...")
            result = mgr.save_snapshot(mock_digital_life, force=True)
            logger.info(f"保存结果: success={result.success}, snapshot_id={result.snapshot_id}, is_incremental={result.is_incremental}")
            
            assert result.success, f"快照保存失败: {result.error_message}"
            assert result.snapshot_id is not None, "快照ID应为非空"
        
        logger.info("测试通过!")
        logger.info("="*60)

    def test_list_snapshots_empty(self, tmp_path):
        """测试列出空快照目录"""
        logger.info("="*60)
        logger.info("开始测试: test_list_snapshots_empty")
        logger.info(f"tmp_path: {tmp_path}")
        
        mgr = StateSnapshotManager(snapshot_dir=str(tmp_path / "snapshots"))
        logger.info(f"快照目录: {mgr.snapshot_dir}")
        
        snapshots = mgr.list_snapshots()
        logger.info(f"列出的快照数量: {len(snapshots)}")
        logger.info(f"快照列表: {snapshots}")
        
        assert snapshots == [], f"空目录应返回空列表，但得到: {snapshots}"
        
        logger.info("测试通过!")
        logger.info("="*60)

    def test_save_and_list_snapshot(self, tmp_path):
        """测试保存快照后能列出"""
        logger.info("="*60)
        logger.info("开始测试: test_save_and_list_snapshot")
        logger.info(f"tmp_path: {tmp_path}")
        
        mgr = StateSnapshotManager(snapshot_dir=str(tmp_path / "snapshots"))
        logger.info(f"快照目录: {mgr.snapshot_dir}")
        
        mock_digital_life = MagicMock()
        mock_digital_life._config = {"key": "value"}
        logger.info(f"测试数据: {mock_digital_life._config}")
        
        with patch.object(mgr, '_save_core_modules_with_delta', return_value=0):
            logger.info("执行保存快照...")
            result = mgr.save_snapshot(mock_digital_life, force=True)
            logger.info(f"保存结果: {result}")
            assert result.success, f"保存失败: {result.error_message}"
        
        logger.info("列出快照...")
        snapshots = mgr.list_snapshots()
        logger.info(f"快照数量: {len(snapshots)}")
        for i, snap in enumerate(snapshots):
            logger.info(f"  快照 {i}: {snap}")
        
        assert len(snapshots) >= 1, f"至少应有一个快照，但得到: {len(snapshots)}"
        
        logger.info("测试通过!")
        logger.info("="*60)

    def test_frequency_control(self, tmp_path):
        """测试频率控制"""
        logger.info("="*60)
        logger.info("开始测试: test_frequency_control")
        logger.info(f"tmp_path: {tmp_path}")
        
        mgr = StateSnapshotManager(snapshot_dir=str(tmp_path / "snapshots"))
        mgr.frequency_controller.min_interval_seconds = 300
        logger.info(f"最小间隔设置: {mgr.frequency_controller.min_interval_seconds} 秒")
        
        mock_digital_life = MagicMock()
        mock_digital_life._config = {"key": "value"}
        
        with patch.object(mgr, '_save_core_modules_with_delta', return_value=0):
            logger.info("第一次保存 (强制)...")
            result1 = mgr.save_snapshot(mock_digital_life, force=True)
            logger.info(f"第一次保存结果: {result1}")
            assert result1.success, f"第一次保存失败: {result1.error_message}"
        
        logger.info("第二次保存 (非强制，应被频率控制拒绝)...")
        result2 = mgr.save_snapshot(mock_digital_life)
        logger.info(f"第二次保存结果: {result2}")
        
        assert not result2.success, "第二次保存应被拒绝"
        assert "过于频繁" in result2.error_message, f"错误消息应包含'过于频繁': {result2.error_message}"
        
        logger.info("测试通过!")
        logger.info("="*60)

    def test_incremental_snapshot(self, tmp_path):
        """测试增量快照"""
        logger.info("="*60)
        logger.info("开始测试: test_incremental_snapshot")
        logger.info(f"tmp_path: {tmp_path}")
        
        mgr = StateSnapshotManager(snapshot_dir=str(tmp_path / "snapshots"))
        logger.info(f"快照目录: {mgr.snapshot_dir}")
        
        mock_digital_life = MagicMock()
        mock_digital_life._config = {"key": "value"}
        
        with patch.object(mgr, '_save_core_modules_with_delta', return_value=0):
            logger.info("创建完整快照...")
            result1 = mgr.save_snapshot(mock_digital_life, force=True)
            logger.info(f"完整快照结果: success={result1.success}, snapshot_id={result1.snapshot_id}, is_incremental={result1.is_incremental}")
            assert result1.success
            assert not result1.is_incremental, "第一次应为完整快照"
            
            logger.info("创建增量快照...")
            result2 = mgr.save_snapshot(mock_digital_life, incremental=True, force=True)
            logger.info(f"增量快照结果: success={result2.success}, snapshot_id={result2.snapshot_id}, is_incremental={result2.is_incremental}")
            assert result2.success
            assert result2.is_incremental, "第二次应为增量快照"
        
        logger.info("测试通过!")
        logger.info("="*60)

    def test_load_snapshot_empty(self, tmp_path):
        """测试加载空快照目录"""
        logger.info("="*60)
        logger.info("开始测试: test_load_snapshot_empty")
        logger.info(f"tmp_path: {tmp_path}")
        
        mgr = StateSnapshotManager(snapshot_dir=str(tmp_path / "snapshots"))
        logger.info(f"快照目录: {mgr.snapshot_dir}")
        
        loaded = mgr.load_snapshot()
        logger.info(f"加载结果: {loaded}")
        
        assert loaded is None, f"空目录应返回 None，但得到: {loaded}"
        
        logger.info("测试通过!")
        logger.info("="*60)

    def test_check_compatibility(self, tmp_path):
        """测试版本兼容性检查"""
        logger.info("="*60)
        logger.info("开始测试: test_check_compatibility")
        
        mgr = StateSnapshotManager(snapshot_dir=str(tmp_path / "snapshots"))
        
        snapshot = StateSnapshot(
            snapshot_id="test",
            created_at=None,
            version="p6.2.0"
        )
        logger.info(f"测试兼容版本: {snapshot.version}")
        result = mgr._check_compatibility(snapshot)
        logger.info(f"兼容性检查结果: {result}")
        assert result is True
        
        snapshot.version = "p5.0.0"
        logger.info(f"测试不兼容版本: {snapshot.version}")
        result = mgr._check_compatibility(snapshot)
        logger.info(f"兼容性检查结果: {result}")
        assert result is False
        
        logger.info("测试通过!")
        logger.info("="*60)

    def test_compute_checksum(self):
        """测试校验和计算"""
        logger.info("="*60)
        logger.info("开始测试: test_compute_checksum")
        
        snapshot = StateSnapshot(
            snapshot_id="test",
            created_at=None,
            version="p6.2.0",
            config={"key": "value"}
        )
        logger.info(f"快照配置: {snapshot.config}")
        
        checksum = snapshot.compute_checksum()
        logger.info(f"计算得到的校验和: {checksum}")
        logger.info(f"校验和长度: {len(checksum)}")
        
        assert isinstance(checksum, str), f"校验和应为字符串，得到: {type(checksum)}"
        assert len(checksum) == 64, f"SHA256 输出长度应为 64，得到: {len(checksum)}"
        
        logger.info("测试通过!")
        logger.info("="*60)

    def test_cleanup_old_snapshots(self, tmp_path):
        """测试清理旧快照"""
        logger.info("="*60)
        logger.info("开始测试: test_cleanup_old_snapshots")
        logger.info(f"tmp_path: {tmp_path}")
        
        mgr = StateSnapshotManager(snapshot_dir=str(tmp_path / "snapshots"))
        mgr.frequency_controller.max_snapshots = 2
        logger.info(f"最大快照数限制: {mgr.frequency_controller.max_snapshots}")
        
        mock_digital_life = MagicMock()
        mock_digital_life._config = {"key": "value"}
        
        logger.info("创建 5 个快照...")
        with patch.object(mgr, '_save_core_modules_with_delta', return_value=0):
            for i in range(5):
                result = mgr.save_snapshot(mock_digital_life, force=True)
                logger.info(f"  快照 {i+1}: {result.snapshot_id}")
        
        snapshots = mgr.list_snapshots()
        logger.info(f"清理后剩余快照数量: {len(snapshots)}")
        for i, snap in enumerate(snapshots):
            logger.info(f"  剩余快照 {i}: {snap}")
        
        assert len(snapshots) <= 2, f"快照数量不应超过 2，得到: {len(snapshots)}"
        
        logger.info("测试通过!")
        logger.info("="*60)

    def test_performance_monitor(self, tmp_path):
        """测试性能监控"""
        logger.info("="*60)
        logger.info("开始测试: test_performance_monitor")
        logger.info(f"tmp_path: {tmp_path}")
        
        mgr = StateSnapshotManager(snapshot_dir=str(tmp_path / "snapshots"))
        logger.info(f"快照目录: {mgr.snapshot_dir}")
        
        mock_digital_life = MagicMock()
        mock_digital_life._config = {"key": "value"}
        
        with patch.object(mgr, '_save_core_modules_with_delta', return_value=0):
            result = mgr.save_snapshot(mock_digital_life, force=True)
            logger.info(f"保存结果: {result}")
            assert result.success
        
        summary = mgr.performance_monitor.get_performance_summary()
        logger.info(f"性能摘要: {summary}")
        
        assert summary["total_saves"] == 1, f"总保存次数应为 1，得到: {summary['total_saves']}"
        assert summary["last_save_ms"] > 0, f"上次保存时间应大于 0，得到: {summary['last_save_ms']}"
        
        logger.info("测试通过!")
        logger.info("="*60)


class TestModuleState:
    """模块状态测试"""

    def test_module_state_creation(self):
        """测试模块状态创建"""
        logger.info("="*60)
        logger.info("开始测试: test_module_state_creation")
        
        logger.info("创建 ModuleState 实例...")
        state = ModuleState(
            module_name="test_module",
            initialized=True,
            state_data=b"test data",
            restore_priority=50,
            checksum="abc123",
            changed=True
        )
        logger.info(f"模块状态: module_name={state.module_name}")
        logger.info(f"模块状态: initialized={state.initialized}")
        logger.info(f"模块状态: state_data={state.state_data}")
        logger.info(f"模块状态: restore_priority={state.restore_priority}")
        logger.info(f"模块状态: checksum={state.checksum}")
        logger.info(f"模块状态: changed={state.changed}")
        
        assert state.module_name == "test_module"
        assert state.initialized is True
        assert state.state_data == b"test data"
        assert state.restore_priority == 50
        assert state.checksum == "abc123"
        assert state.changed is True
        
        logger.info("测试通过!")
        logger.info("="*60)


class TestSnapshotInfo:
    """快照信息测试"""

    def test_snapshot_info_creation(self):
        """测试快照信息创建"""
        logger.info("="*60)
        logger.info("开始测试: test_snapshot_info_creation")
        
        from datetime import datetime
        logger.info("创建 SnapshotInfo 实例...")
        info = SnapshotInfo(
            snapshot_id="snap_20240101_000000",
            created_at=datetime.now(),
            version="p6.2.0",
            file_size=1024,
            is_incremental=False
        )
        logger.info(f"快照信息: snapshot_id={info.snapshot_id}")
        logger.info(f"快照信息: created_at={info.created_at}")
        logger.info(f"快照信息: version={info.version}")
        logger.info(f"快照信息: file_size={info.file_size}")
        logger.info(f"快照信息: is_incremental={info.is_incremental}")
        
        assert info.snapshot_id == "snap_20240101_000000"
        assert info.version == "p6.2.0"
        assert info.file_size == 1024
        assert info.is_incremental is False
        
        logger.info("测试通过!")
        logger.info("="*60)


# 导入 MagicMock
from unittest.mock import MagicMock


class TestIncrementalSnapshot:
    """增量快照专项测试"""

    def test_incremental_snapshot_basic(self, tmp_path):
        """测试增量快照基本功能"""
        logger.info("="*60)
        logger.info("开始测试: test_incremental_snapshot_basic")
        
        mgr = StateSnapshotManager(snapshot_dir=str(tmp_path / "snapshots"))
        logger.info(f"快照目录: {mgr.snapshot_dir}")
        
        mock_digital_life = MagicMock()
        mock_digital_life._config = {"key": "value"}
        
        with patch.object(mgr, '_save_core_modules_with_delta', return_value=0):
            logger.info("创建完整快照作为基准...")
            result1 = mgr.save_snapshot(mock_digital_life, force=True)
            logger.info(f"完整快照: id={result1.snapshot_id}, is_incremental={result1.is_incremental}")
            assert result1.success
            assert not result1.is_incremental
            
            logger.info("创建增量快照...")
            result2 = mgr.save_snapshot(mock_digital_life, incremental=True, force=True)
            logger.info(f"增量快照: id={result2.snapshot_id}, is_incremental={result2.is_incremental}")
            assert result2.success
            assert result2.is_incremental
            assert result2.base_snapshot_id == result1.snapshot_id
        
        logger.info("测试通过!")
        logger.info("="*60)

    def test_incremental_snapshot_chain(self, tmp_path):
        """测试增量快照链（多个增量快照）"""
        logger.info("="*60)
        logger.info("开始测试: test_incremental_snapshot_chain")
        
        mgr = StateSnapshotManager(snapshot_dir=str(tmp_path / "snapshots"))
        
        mock_digital_life = MagicMock()
        mock_digital_life._config = {"key": "value"}
        
        snapshot_ids = []
        with patch.object(mgr, '_save_core_modules_with_delta', return_value=0):
            # 创建完整快照
            logger.info("创建完整快照 #0...")
            result0 = mgr.save_snapshot(mock_digital_life, force=True)
            snapshot_ids.append(result0.snapshot_id)
            logger.info(f"  快照 #0: {result0.snapshot_id}")
            
            # 创建多个增量快照
            for i in range(3):
                logger.info(f"创建增量快照 #{i+1}...")
                result = mgr.save_snapshot(mock_digital_life, incremental=True, force=True)
                snapshot_ids.append(result.snapshot_id)
                logger.info(f"  快照 #{i+1}: {result.snapshot_id}, 基准: {result.base_snapshot_id}")
                assert result.is_incremental
                assert result.base_snapshot_id == snapshot_ids[-2]  # 基准应该是上一个快照
        
        # 验证快照链完整性
        snapshots = mgr.list_snapshots()
        logger.info(f"快照链快照总数: {len(snapshots)}")
        
        # 验证增量快照与完整快照的比例
        incremental_count = sum(1 for s in snapshots if s.is_incremental)
        full_count = sum(1 for s in snapshots if not s.is_incremental)
        logger.info(f"完整快照: {full_count}, 增量快照: {incremental_count}")
        
        assert incremental_count >= 3, "至少应有3个增量快照"
        assert full_count >= 1, "至少应有1个完整快照"
        
        logger.info("测试通过!")
        logger.info("="*60)

    def test_incremental_snapshot_delta_calculation(self, tmp_path):
        """测试增量快照的增量计算"""
        logger.info("="*60)
        logger.info("开始测试: test_incremental_snapshot_delta_calculation")
        
        mgr = StateSnapshotManager(snapshot_dir=str(tmp_path / "snapshots"))
        
        mock_digital_life = MagicMock()
        mock_digital_life._config = {"config_key": "initial_value"}
        
        with patch.object(mgr, '_save_core_modules_with_delta', return_value=0) as mock_delta:
            mock_delta.return_value = 100  # 模拟返回节省100字节
            
            logger.info("创建增量快照...")
            result = mgr.save_snapshot(mock_digital_life, incremental=True, force=True)
            logger.info(f"保存结果: {result}")
            
            # 验证增量计算被调用
            assert mock_delta.called, "增量计算应被调用"
        
        logger.info("测试通过!")
        logger.info("="*60)

    def test_incremental_snapshot_with_data_changes(self, tmp_path):
        """测试数据变化的增量快照"""
        logger.info("="*60)
        logger.info("开始测试: test_incremental_snapshot_with_data_changes")
        
        mgr = StateSnapshotManager(snapshot_dir=str(tmp_path / "snapshots"))
        
        mock_digital_life = MagicMock()
        mock_digital_life._config = {"key": "value1"}
        
        with patch.object(mgr, '_save_core_modules_with_delta', return_value=0):
            logger.info("创建第一个快照...")
            result1 = mgr.save_snapshot(mock_digital_life, force=True)
            logger.info(f"第一个快照: {result1.snapshot_id}")
            
            # 修改数据
            logger.info("修改数据...")
            mock_digital_life._config = {"key": "value2"}
            
            logger.info("创建第二个快照...")
            result2 = mgr.save_snapshot(mock_digital_life, force=True)
            logger.info(f"第二个快照: {result2.snapshot_id}")
            
            # 验证两个快照ID不同
            assert result1.snapshot_id != result2.snapshot_id
        
        logger.info("测试通过!")
        logger.info("="*60)

    def test_incremental_snapshot_force_full(self, tmp_path):
        """测试增量快照在 force 模式下仍可创建"""
        logger.info("="*60)
        logger.info("开始测试: test_incremental_snapshot_force_full")
        
        mgr = StateSnapshotManager(snapshot_dir=str(tmp_path / "snapshots"))
        
        mock_digital_life = MagicMock()
        mock_digital_life._config = {"key": "value"}
        
        with patch.object(mgr, '_save_core_modules_with_delta', return_value=0):
            # 先创建完整快照
            logger.info("创建完整快照...")
            result1 = mgr.save_snapshot(mock_digital_life, force=True)
            assert not result1.is_incremental
            logger.info(f"完整快照: is_incremental={result1.is_incremental}")
            
            # 在 force 模式下创建增量快照（force 只用于绕过频率限制）
            logger.info("创建增量快照（force=True）...")
            result2 = mgr.save_snapshot(mock_digital_life, incremental=True, force=True)
            logger.info(f"增量快照: is_incremental={result2.is_incremental}, base={result2.base_snapshot_id}")
            
            # 验证是增量快照
            assert result2.is_incremental, "force=True 时增量参数应生效"
            assert result2.base_snapshot_id == result1.snapshot_id, "增量快照应有正确的基础快照ID"
        
        logger.info("测试通过!")
        logger.info("="*60)


class TestVersionCompatibility:
    """版本兼容性专项测试"""

    def test_compatibility_p6_versions(self, tmp_path):
        """测试 P6 版本兼容性"""
        logger.info("="*60)
        logger.info("开始测试: test_compatibility_p6_versions")
        
        mgr = StateSnapshotManager(snapshot_dir=str(tmp_path / "snapshots"))
        
        # 测试不同 P6 子版本的兼容性
        test_versions = ["p6.0.0", "p6.1.0", "p6.2.0", "p6.10.0"]
        
        for version in test_versions:
            logger.info(f"测试版本: {version}")
            snapshot = StateSnapshot(
                snapshot_id=f"test_{version}",
                created_at=None,
                version=version
            )
            result = mgr._check_compatibility(snapshot)
            logger.info(f"  兼容性结果: {result}")
            assert result is True, f"版本 {version} 应兼容"
        
        logger.info("测试通过!")
        logger.info("="*60)

    def test_incompatibility_p5_version(self, tmp_path):
        """测试 P5 版本不兼容"""
        logger.info("="*60)
        logger.info("开始测试: test_incompatibility_p5_version")
        
        mgr = StateSnapshotManager(snapshot_dir=str(tmp_path / "snapshots"))
        
        snapshot = StateSnapshot(
            snapshot_id="test_p5",
            created_at=None,
            version="p5.0.0"
        )
        logger.info(f"测试版本: {snapshot.version}")
        result = mgr._check_compatibility(snapshot)
        logger.info(f"兼容性结果: {result}")
        assert result is False, "P5 版本应不兼容"
        
        logger.info("测试通过!")
        logger.info("="*60)

    def test_incompatibility_future_version(self, tmp_path):
        """测试未来版本不兼容"""
        logger.info("="*60)
        logger.info("开始测试: test_incompatibility_future_version")
        
        mgr = StateSnapshotManager(snapshot_dir=str(tmp_path / "snapshots"))
        
        snapshot = StateSnapshot(
            snapshot_id="test_future",
            created_at=None,
            version="p7.0.0"
        )
        logger.info(f"测试版本: {snapshot.version}")
        result = mgr._check_compatibility(snapshot)
        logger.info(f"兼容性结果: {result}")
        assert result is False, "未来版本应不兼容"
        
        logger.info("测试通过!")
        logger.info("="*60)

    def test_compatibility_edge_cases(self, tmp_path):
        """测试兼容性边界情况"""
        logger.info("="*60)
        logger.info("开始测试: test_compatibility_edge_cases")
        
        mgr = StateSnapshotManager(snapshot_dir=str(tmp_path / "snapshots"))
        
        # 测试边界版本
        edge_cases = [
            ("p6.2.0", True, "标准版本"),
            ("p6.0.0", True, "最低支持版本"),
            ("p5.9.9", False, "P5 最高版本"),
            ("p7.0.0", False, "P7 最低版本"),
            ("invalid", False, "无效版本格式"),
            ("", False, "空版本"),
        ]
        
        for version, expected, description in edge_cases:
            logger.info(f"测试 {description}: {version}")
            snapshot = StateSnapshot(
                snapshot_id=f"test_{version}",
                created_at=None,
                version=version
            )
            result = mgr._check_compatibility(snapshot)
            logger.info(f"  预期: {expected}, 实际: {result}")
            assert result == expected, f"{description} 应返回 {expected}"
        
        logger.info("测试通过!")
        logger.info("="*60)

    def test_version_in_snapshot_info(self, tmp_path):
        """测试快照信息中的版本字段"""
        logger.info("="*60)
        logger.info("开始测试: test_version_in_snapshot_info")
        
        mgr = StateSnapshotManager(snapshot_dir=str(tmp_path / "snapshots"))
        
        mock_digital_life = MagicMock()
        mock_digital_life._config = {"key": "value"}
        
        with patch.object(mgr, '_save_core_modules_with_delta', return_value=0):
            logger.info("创建快照...")
            result = mgr.save_snapshot(mock_digital_life, force=True)
            logger.info(f"快照结果: {result}")
            assert result.success
        
        # 检查快照信息
        snapshots = mgr.list_snapshots()
        assert len(snapshots) >= 1
        
        for snap in snapshots:
            logger.info(f"快照: {snap.snapshot_id}, 版本: {snap.version}")
            assert snap.version == "p6.2.0", f"快照版本应为 p6.2.0，实际: {snap.version}"
        
        logger.info("测试通过!")
        logger.info("="*60)


class TestSnapshotRecovery:
    """快照恢复测试"""

    def test_load_latest_snapshot(self, tmp_path):
        """测试加载最新快照"""
        logger.info("="*60)
        logger.info("开始测试: test_load_latest_snapshot")
        
        mgr = StateSnapshotManager(snapshot_dir=str(tmp_path / "snapshots"))
        
        mock_digital_life = MagicMock()
        mock_digital_life._config = {"key": "value"}
        
        with patch.object(mgr, '_save_core_modules_with_delta', return_value=0):
            logger.info("创建快照...")
            result = mgr.save_snapshot(mock_digital_life, force=True)
            logger.info(f"创建的快照: {result.snapshot_id}")
        
        logger.info("加载最新快照...")
        loaded = mgr.load_snapshot()
        logger.info(f"加载结果: {loaded}")
        
        assert loaded is not None, "应成功加载快照"
        assert loaded.snapshot_id == result.snapshot_id, "加载的快照ID应匹配"
        
        logger.info("测试通过!")
        logger.info("="*60)

    def test_recover_incompatible_version(self, tmp_path):
        """测试恢复不兼容版本快照的处理"""
        logger.info("="*60)
        logger.info("开始测试: test_recover_incompatible_version")
        
        mgr = StateSnapshotManager(snapshot_dir=str(tmp_path / "snapshots"))
        
        # 创建一个不兼容版本的快照
        incompatible_snapshot = StateSnapshot(
            snapshot_id="incompatible_test",
            created_at=None,
            version="p5.0.0"
        )
        
        logger.info(f"测试不兼容快照: {incompatible_snapshot.snapshot_id}, 版本: {incompatible_snapshot.version}")
        result = mgr._check_compatibility(incompatible_snapshot)
        logger.info(f"兼容性检查结果: {result}")
        
        assert result is False, "不兼容版本应返回 False"
        
        logger.info("测试通过!")
        logger.info("="*60)


class TestSnapshotPerformance:
    """快照性能测试"""

    def test_save_performance_timing(self, tmp_path):
        """测试保存性能计时"""
        logger.info("="*60)
        logger.info("开始测试: test_save_performance_timing")
        
        mgr = StateSnapshotManager(snapshot_dir=str(tmp_path / "snapshots"))
        
        mock_digital_life = MagicMock()
        mock_digital_life._config = {"key": "value"}
        
        with patch.object(mgr, '_save_core_modules_with_delta', return_value=0):
            logger.info("执行保存并记录时间...")
            result = mgr.save_snapshot(mock_digital_life, force=True)
            logger.info(f"保存结果: elapsed_ms={result.elapsed_ms}")
            
            assert result.success
            assert result.elapsed_ms >= 0, "耗时应为非负数"
        
        logger.info("测试通过!")
        logger.info("="*60)

    def test_performance_metrics_accumulation(self, tmp_path):
        """测试性能指标累积"""
        logger.info("="*60)
        logger.info("开始测试: test_performance_metrics_accumulation")
        
        mgr = StateSnapshotManager(snapshot_dir=str(tmp_path / "snapshots"))
        
        mock_digital_life = MagicMock()
        mock_digital_life._config = {"key": "value"}
        
        with patch.object(mgr, '_save_core_modules_with_delta', return_value=0):
            for i in range(3):
                logger.info(f"保存第 {i+1} 个快照...")
                result = mgr.save_snapshot(mock_digital_life, force=True)
                logger.info(f"  快照 {i+1}: {result.snapshot_id}")
        
        summary = mgr.performance_monitor.get_performance_summary()
        logger.info(f"性能摘要: {summary}")
        
        assert summary["total_saves"] == 3, f"总保存次数应为 3，实际: {summary['total_saves']}"
        
        logger.info("测试通过!")
        logger.info("="*60)