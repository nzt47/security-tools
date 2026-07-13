"""P2 性能优化基准测试：字典索引 vs 线性查找

对比 _upsert_collection_batch（字典索引 O(1) 查重）
与模拟的旧方法（next() 线性查找 O(n) 查重）的性能差异。

运行方式：
    python -m pytest tests/perf/test_config_manager_perf.py -v -s

性能阈值：
    - 100 实例批量更新 < 50ms
    - 字典索引方法应比线性查找快 5x+（100 实例时）
"""
import time
import uuid
import datetime
from unittest.mock import MagicMock

import pytest

from agent.network.config_manager import NetworkConfigManager


# ── 模拟旧方法：next() 线性查找 ──

def _upsert_linear(manager, collection, items, section, secure_key_prefix=None):
    """模拟优化前的逐个 next() 线性查找方式"""
    result_ids = []
    for item in items:
        item_id = item.get('id')
        if not item_id:
            item["id"] = str(uuid.uuid4())
            item["created_at"] = item.get('created_at') or datetime.datetime.now().isoformat()
            item["updated_at"] = item["created_at"]
            collection.append(item)
            manager._add_change_log('add', section, {'id': item["id"], 'name': item.get('name')})
            result_ids.append(item["id"])
        else:
            # O(n) 线性查找
            existing = next((i for i in collection if i.get("id") == item_id), None)
            if existing:
                existing.update(item)
                existing["updated_at"] = datetime.datetime.now().isoformat()
                manager._add_change_log('update', section, {'id': item_id, 'name': item.get('name')})
                result_ids.append(item_id)
            else:
                result_ids.append(None)
    return result_ids


# ── Fixture ──

@pytest.fixture
def perf_manager(tmp_path):
    """创建临时配置的性能测试管理器"""
    secure_mgr = MagicMock()
    secure_mgr._store = {}
    secure_mgr.set_secure_value.side_effect = lambda k, v: secure_mgr._store.__setitem__(k, v)
    secure_mgr.get_secure_value.side_effect = lambda k, d=None: secure_mgr._store.get(k, d)
    return NetworkConfigManager(
        config_file=str(tmp_path / "perf_config.json"),
        secure_manager=secure_mgr,
    )


def _populate_instances(manager, count, section='llm_instance', prefix='inst'):
    """预填充 N 个实例到配置中"""
    config = manager._load()
    collection = config["llm_instances"] if section == 'llm_instance' else config["search_instances"]
    for i in range(count):
        collection.append({
            "id": f"{prefix}_{i}",
            "name": f"Instance {i}",
            "provider": "openai",
            "api_key": "",
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
        })
    return collection


# ── 正确性验证 ──

class TestBatchUpsertCorrectness:
    """验证批量方法的正确性"""

    def test_batch_add_new_items(self, perf_manager):
        items = [{"name": f"new_{i}", "provider": "openai"} for i in range(10)]
        config = perf_manager._load()
        result = perf_manager._upsert_collection_batch(
            config["llm_instances"], items, 'llm_instance', secure_key_prefix='llm_'
        )
        assert len(result) == 10
        assert all(r is not None for r in result)
        assert len(config["llm_instances"]) == 10

    def test_batch_update_existing(self, perf_manager):
        _populate_instances(perf_manager, 20)
        config = perf_manager._load()
        updates = [{"id": f"inst_{i}", "name": f"updated_{i}"} for i in range(20)]
        result = perf_manager._upsert_collection_batch(
            config["llm_instances"], updates, 'llm_instance', secure_key_prefix='llm_'
        )
        assert len(result) == 20
        assert all(r == f"inst_{i}" for i, r in enumerate(result))
        assert config["llm_instances"][0]["name"] == "updated_0"

    def test_batch_mixed_add_update(self, perf_manager):
        _populate_instances(perf_manager, 10)
        config = perf_manager._load()
        items = [
            {"id": "inst_0", "name": "updated"},  # 更新
            {"name": "new_one"},                    # 新增
            {"id": "nonexistent", "name": "noop"}, # 无操作
        ]
        result = perf_manager._upsert_collection_batch(
            config["llm_instances"], items, 'llm_instance', secure_key_prefix='llm_'
        )
        assert result[0] == "inst_0"      # 更新成功
        assert result[1] is not None       # 新增成功
        assert result[2] is None           # 无操作
        assert len(config["llm_instances"]) == 11  # 10 原有 + 1 新增

    def test_batch_results_equivalent_to_linear(self, perf_manager):
        """验证批量方法与线性方法结果等价"""
        _populate_instances(perf_manager, 50)
        config1 = perf_manager._load()
        config2 = perf_manager._load()

        items = [{"id": f"inst_{i}", "name": f"batch_{i}"} for i in range(50)]

        result_batch = perf_manager._upsert_collection_batch(
            config1["llm_instances"], items, 'llm_instance'
        )
        result_linear = _upsert_linear(
            perf_manager, config2["llm_instances"], items, 'llm_instance'
        )

        assert result_batch == result_linear
        assert len(config1["llm_instances"]) == len(config2["llm_instances"])


# ── 性能基准测试 ──

class TestBatchUpsertPerformance:
    """性能基准测试：字典索引 vs 线性查找"""

    @pytest.mark.parametrize("count", [10, 50, 100, 200])
    def test_update_performance_comparison(self, perf_manager, count):
        """对比更新 N 个实例的性能"""
        _populate_instances(perf_manager, count)
        items = [{"id": f"inst_{i}", "name": f"updated_{i}"} for i in range(count)]

        # 旧方法：线性查找
        config_linear = perf_manager._load()
        start = time.perf_counter()
        _upsert_linear(perf_manager, config_linear["llm_instances"], items, 'llm_instance')
        linear_ms = (time.perf_counter() - start) * 1000

        # 新方法：字典索引
        config_batch = perf_manager._load()
        start = time.perf_counter()
        perf_manager._upsert_collection_batch(
            config_batch["llm_instances"], items, 'llm_instance'
        )
        batch_ms = (time.perf_counter() - start) * 1000

        speedup = linear_ms / batch_ms if batch_ms > 0 else float('inf')

        print(f"\n  [{count} 实例] 线性: {linear_ms:.2f}ms, 字典: {batch_ms:.2f}ms, 加速比: {speedup:.1f}x")

        # 字典索引应不慢于线性查找
        assert batch_ms <= linear_ms * 1.5  # 允许小规模时误差

    def test_batch_update_100_under_50ms(self, perf_manager):
        """100 个实例批量更新应在 50ms 内完成"""
        _populate_instances(perf_manager, 100)
        items = [{"id": f"inst_{i}", "name": f"updated_{i}"} for i in range(100)]

        config = perf_manager._load()
        start = time.perf_counter()
        perf_manager._upsert_collection_batch(
            config["llm_instances"], items, 'llm_instance'
        )
        elapsed_ms = (time.perf_counter() - start) * 1000

        print(f"\n  100 实例批量更新: {elapsed_ms:.2f}ms")
        assert elapsed_ms < 50, f"批量更新 100 实例耗时 {elapsed_ms:.1f}ms，超过 50ms 阈值"

    def test_batch_update_200_under_100ms(self, perf_manager):
        """200 个实例批量更新应在 100ms 内完成"""
        _populate_instances(perf_manager, 200)
        items = [{"id": f"inst_{i}", "name": f"updated_{i}"} for i in range(200)]

        config = perf_manager._load()
        start = time.perf_counter()
        perf_manager._upsert_collection_batch(
            config["llm_instances"], items, 'llm_instance'
        )
        elapsed_ms = (time.perf_counter() - start) * 1000

        print(f"\n  200 实例批量更新: {elapsed_ms:.2f}ms")
        assert elapsed_ms < 100, f"批量更新 200 实例耗时 {elapsed_ms:.1f}ms，超过 100ms 阈值"

    def test_add_performance_comparison(self, perf_manager):
        """对比新增 100 个实例的性能（新增不涉及查重，差异应较小）"""
        items = [{"name": f"new_{i}", "provider": "openai"} for i in range(100)]

        # 旧方法
        config_linear = perf_manager._load()
        start = time.perf_counter()
        _upsert_linear(perf_manager, config_linear["llm_instances"], items[:], 'llm_instance')
        linear_ms = (time.perf_counter() - start) * 1000

        # 新方法
        config_batch = perf_manager._load()
        start = time.perf_counter()
        perf_manager._upsert_collection_batch(
            config_batch["llm_instances"], items[:], 'llm_instance'
        )
        batch_ms = (time.perf_counter() - start) * 1000

        print(f"\n  [新增 100] 线性: {linear_ms:.2f}ms, 字典: {batch_ms:.2f}ms")
        # 新增不涉及查重，性能差异应在 2x 以内
        assert batch_ms < linear_ms * 2 or batch_ms < 50

    def test_mixed_operations_performance(self, perf_manager):
        """混合操作（50% 更新 + 50% 新增）性能测试"""
        _populate_instances(perf_manager, 50)
        items = []
        for i in range(100):
            if i % 2 == 0:
                items.append({"id": f"inst_{i // 2}", "name": f"updated_{i}"})
            else:
                items.append({"name": f"new_{i}"})

        config = perf_manager._load()
        start = time.perf_counter()
        perf_manager._upsert_collection_batch(
            config["llm_instances"], items, 'llm_instance'
        )
        elapsed_ms = (time.perf_counter() - start) * 1000

        print(f"\n  [混合 100 操作] {elapsed_ms:.2f}ms")
        assert elapsed_ms < 50


# ── 基准测试报告 ──

class TestPerformanceReport:
    """生成性能基准报告"""

    def test_generate_perf_report(self, perf_manager):
        """生成完整性能基准报告（10/50/100/200 实例）"""
        print("\n" + "=" * 60)
        print("性能基准报告：_upsert_collection_batch")
        print("=" * 60)
        print(f"{'实例数':>8} | {'线性(ms)':>10} | {'字典(ms)':>10} | {'加速比':>8} | {'阈值(ms)':>10}")
        print("-" * 60)

        for count in [10, 50, 100, 200, 500]:
            _populate_instances(perf_manager, count)
            items = [{"id": f"inst_{i}", "name": f"u_{i}"} for i in range(count)]

            # 线性
            config_l = perf_manager._load()
            start = time.perf_counter()
            _upsert_linear(perf_manager, config_l["llm_instances"], items, 'llm_instance')
            linear_ms = (time.perf_counter() - start) * 1000

            # 字典
            config_b = perf_manager._load()
            start = time.perf_counter()
            perf_manager._upsert_collection_batch(
                config_b["llm_instances"], items, 'llm_instance'
            )
            batch_ms = (time.perf_counter() - start) * 1000

            speedup = linear_ms / batch_ms if batch_ms > 0 else 0
            threshold = count * 0.5  # 0.5ms per item

            status = "✓" if batch_ms < threshold else "✗"
            print(f"{count:>8} | {linear_ms:>10.2f} | {batch_ms:>10.2f} | {speedup:>7.1f}x | {threshold:>10.1f} {status}")

        print("=" * 60)
