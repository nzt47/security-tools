"""
云枢感知底座 单元测试与示例代码

测试覆盖:
- 所有传感器模块的基础采集
- SensorReading 数据类的 JSON 序列化
- BodySensor 集成采集
- 文件系统监听
- 变更检测
"""
import unittest
import os
import sys
import tempfile
import time
import json

# 确保 sensor 包可导入
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sensor.sensor_reading import SensorReading, Severity, Category, reading, normal, warning, critical
from sensor.cpu_sensor import CPUSensor
from sensor.memory_sensor import MemorySensor
from sensor.disk_sensor import DiskSensor
from sensor.battery_sensor import BatterySensor
from sensor.network_sensor import NetworkSensor
from sensor.board_sensor import BoardSensor
from sensor.body_sensor import BodySensor
from sensor.file_watcher import FileWatcher
from sensor.change_detector import ChangeDetector


class TestSensorReading(unittest.TestCase):
    """测试 SensorReading 数据类"""

    def test_create_reading(self):
        r = SensorReading("test_sensor", 42.5, "%", "测试传感器")
        self.assertEqual(r.sensor_name, "test_sensor")
        self.assertEqual(r.value, 42.5)
        self.assertEqual(r.unit, "%")
        self.assertEqual(r.severity, "normal")
        self.assertIsNotNone(r.timestamp)

    def test_to_dict(self):
        r = SensorReading("test", 10, "℃", "温度", Category.CPU, Severity.WARNING)
        d = r.to_dict()
        self.assertEqual(d["sensor_name"], "test")
        self.assertEqual(d["category"], "cpu")
        self.assertEqual(d["severity"], "warning")

    def test_to_json(self):
        r = normal("test", 50, "%", "测试")
        j = r.to_json()
        data = json.loads(j)
        self.assertEqual(data["value"], 50)
        self.assertIn("timestamp", data)

    def test_shortcut_factories(self):
        r1 = normal("n", 1, "", "")
        r2 = warning("w", 2, "", "")
        r3 = critical("c", 3, "", "")
        self.assertEqual(r1.severity, "normal")
        self.assertEqual(r2.severity, "warning")
        self.assertEqual(r3.severity, "critical")

    def test_severity_enum(self):
        self.assertEqual(Severity.NORMAL.value, "normal")
        self.assertEqual(Severity.WARNING.value, "warning")
        self.assertEqual(Severity.CRITICAL.value, "critical")

    def test_category_enum(self):
        self.assertEqual(Category.CPU.value, "cpu")
        self.assertEqual(Category.NETWORK.value, "network")
        self.assertEqual(Category.CHASSIS.value, "chassis")


class TestCPUSensor(unittest.TestCase):
    """测试 CPU 传感器"""

    def setUp(self):
        self.sensor = CPUSensor()

    def test_collect(self):
        results = self.sensor.collect()
        self.assertIsInstance(results, list)
        self.assertTrue(len(results) > 0)
        self.assertIsInstance(results[0], SensorReading)
        # 应该至少包含 cpu_usage
        names = [r.sensor_name for r in results]
        self.assertIn("cpu_usage", names)

    def test_info(self):
        info = self.sensor.info
        self.assertIn("processor", info)
        self.assertIn("physical_cores", info)
        self.assertIn("logical_cores", info)


class TestMemorySensor(unittest.TestCase):
    """测试内存传感器"""

    def setUp(self):
        self.sensor = MemorySensor()

    def test_collect(self):
        results = self.sensor.collect()
        self.assertIsInstance(results, list)
        names = [r.sensor_name for r in results]
        self.assertIn("memory_usage", names)
        self.assertIn("memory_total", names)


class TestDiskSensor(unittest.TestCase):
    """测试磁盘传感器"""

    def setUp(self):
        self.sensor = DiskSensor()

    def test_collect(self):
        results = self.sensor.collect()
        self.assertIsInstance(results, list)
        self.assertTrue(len(results) > 0)


class TestBatterySensor(unittest.TestCase):
    """测试电池传感器"""

    def setUp(self):
        self.sensor = BatterySensor()

    def test_collect(self):
        results = self.sensor.collect()
        self.assertIsInstance(results, list)
        # 可能笔记本有电池返回数据，台式机返回空列表——两种都合法

    def test_has_battery(self):
        result = self.sensor.has_battery
        self.assertIsInstance(result, bool)


class TestNetworkSensor(unittest.TestCase):
    """测试网络传感器"""

    def setUp(self):
        self.sensor = NetworkSensor()

    def test_collect(self):
        results = self.sensor.collect()
        self.assertIsInstance(results, list)
        self.assertTrue(len(results) > 0)


class TestBoardSensor(unittest.TestCase):
    """测试主板传感器"""

    def setUp(self):
        self.sensor = BoardSensor()

    def test_collect(self):
        results = self.sensor.collect()
        self.assertIsInstance(results, list)
        # 主板传感器在不同平台上返回不同数量的数据
        # 至少应有系统信息
        names = [r.sensor_name for r in results]
        # 检查是否有跨平台的系统信息字段
        system_fields = [n for n in names if n.startswith("system_")]
        self.assertTrue(len(system_fields) > 0, f"应包含系统信息字段，实际: {names}")


class TestBodySensor(unittest.TestCase):
    """测试 BodySensor 主类集成"""

    def setUp(self):
        self.sensor = BodySensor(enable_change_detection=False)

    def test_collect_all(self):
        results = self.sensor.collect_all()
        self.assertIsInstance(results, list)
        self.assertTrue(len(results) > 0)
        self.assertIsInstance(results[0], SensorReading)

    def test_collect_category(self):
        cpu_data = self.sensor.collect_category(Category.CPU)
        self.assertTrue(len(cpu_data) > 0)

    def test_collect_quick(self):
        results = self.sensor.collect_quick()
        self.assertIsInstance(results, list)
        self.assertTrue(len(results) > 0)

    def test_health_report(self):
        report = self.sensor.get_health_report()
        self.assertIsInstance(report, str)
        self.assertIn("云枢", report)

    def test_to_json_static(self):
        j = BodySensor.to_json("test", 100, "%", "测试")
        data = json.loads(j)
        self.assertEqual(data["sensor_name"], "test")
        self.assertEqual(data["value"], 100)


class TestFileWatcher(unittest.TestCase):
    """测试文件系统监听"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.events = []

    def test_file_events(self):
        watcher = FileWatcher([self.temp_dir], callback=lambda r: self.events.append(r))
        watcher.start()
        time.sleep(0.3)  # 等待观察者就绪

        # 创建文件
        test_file = os.path.join(self.temp_dir, 'test.txt')
        with open(test_file, 'w') as f:
            f.write('hello')
        time.sleep(0.5)

        # 修改文件
        with open(test_file, 'a') as f:
            f.write(' world')
        time.sleep(0.5)

        # 删除文件
        os.remove(test_file)
        time.sleep(0.5)

        watcher.stop()
        time.sleep(0.3)

        self.assertTrue(len(self.events) > 0, f"应至少有一个文件事件，实际: {len(self.events)}")
        # 事件应为 SensorReading 类型
        self.assertIsInstance(self.events[0], SensorReading)

    def tearDown(self):
        import shutil
        try:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass


class TestChangeDetector(unittest.TestCase):
    """测试变更检测器"""

    def setUp(self):
        self.detector = ChangeDetector()

    def test_set_baseline(self):
        baseline = self.detector.set_baseline()
        self.assertIsNotNone(baseline)
        self.assertIn("devices", baseline)
        self.assertIn("processes", baseline)
        self.assertIn("hash", baseline)

    def test_collect_first_time(self):
        results = self.detector.collect()
        self.assertTrue(len(results) > 0)
        # 首次采集应建立基准
        self.assertTrue(any("baseline" in r.sensor_name for r in results))

    def test_collect_second_time(self):
        self.detector.collect()  # 首次建立基准
        results = self.detector.collect()  # 第二次对比
        self.assertIsInstance(results, list)

    def test_baseline_hash(self):
        self.detector.set_baseline()
        h = self.detector.baseline_hash
        self.assertIsInstance(h, str)
        self.assertEqual(len(h), 16)


class TestGPUSensorOptional(unittest.TestCase):
    """测试 GPU 传感器（可选，无 GPU 时跳过）"""

    def test_collect_no_crash(self):
        from sensor.gpu_sensor import GPUSensor
        sensor = GPUSensor()
        results = sensor.collect()
        self.assertIsInstance(results, list)
        # 无 GPU 时返回空列表也正常


class TestChassisSensorOptional(unittest.TestCase):
    """测试机箱传感器（可选）"""

    def test_collect_no_crash(self):
        from sensor.chassis_sensor import ChassisSensor
        sensor = ChassisSensor()
        results = sensor.collect()
        self.assertIsInstance(results, list)


if __name__ == '__main__':
    unittest.main(verbosity=2)
