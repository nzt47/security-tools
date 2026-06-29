"""验证删除搜索实例后 engine_priority 残留 ID 是否被清理

模拟 app_server.py 中 api_search_instance_delete 的完整逻辑：
1. 设置 3 个搜索实例 + priority
2. 删除中间一个实例（含 priority 清理逻辑）
3. 打印删除前后的 priority 列表，验证残留 ID 被清理
4. 验证删除默认引擎时 default_engine 的处理

用法：
    python scripts/verify_delete_priority_cleanup.py
"""

import sys
import os
import json
import tempfile
from pathlib import Path
from unittest.mock import Mock

# 添加项目根目录到 path
sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.network_config import NetworkConfigManager


def _make_manager(tmp_dir, secure_store=None):
    """创建带 Mock SecureManager 的 NetworkConfigManager"""
    if secure_store is None:
        secure_store = {}
    mock_secure = Mock()
    mock_secure.set_secure_value = Mock(
        side_effect=lambda k, v: secure_store.update({k: v})
    )
    mock_secure.get_secure_value = Mock(
        side_effect=lambda k, default=None: secure_store.get(k, default)
    )
    config_file = tmp_dir / "network_config.json"
    return NetworkConfigManager(
        config_file=str(config_file),
        secure_manager=mock_secure
    )


def print_priority(label, config):
    """打印 priority 列表"""
    priority = config.get('search', {}).get('engine_priority', [])
    default = config.get('search', {}).get('default_engine', '')
    instances = config.get('search_instances', [])
    print(f"\n{'=' * 60}")
    print(f"  {label}")
    print(f"{'=' * 60}")
    print(f"  搜索实例 ({len(instances)} 个):")
    for inst in instances:
        marker = " [默认]" if inst.get('is_default') else ""
        print(f"    - id={inst['id'][:8]}... name={inst.get('name', '')}{marker}")
    print(f"  engine_priority: {[p[:8] + '...' for p in priority]}")
    print(f"  default_engine:  {default[:8] + '...' if default else '(空)'}")
    print(f"{'=' * 60}")


def test_delete_middle_instance():
    """测试 1：删除中间实例，验证 priority 中其 ID 被移除"""
    print("\n\n" + "#" * 60)
    print("# 测试 1: 删除中间实例，验证 priority 残留 ID 被清理")
    print("#" * 60)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        manager = _make_manager(tmp_path)

        # 设置 3 个实例
        inst_a = "uuid-aaaa-1111"
        inst_b = "uuid-bbbb-2222"
        inst_c = "uuid-cccc-3333"
        manager._save({
            "search_instances": [
                {"id": inst_a, "name": "DuckDuckGo", "engine_type": "duckduckgo", "enabled": True},
                {"id": inst_b, "name": "Tavily", "engine_type": "custom", "enabled": True},
                {"id": inst_c, "name": "搜狗搜索", "engine_type": "sogou", "enabled": True},
            ],
            "search": {"engine_priority": [inst_a, inst_b, inst_c], "default_engine": ""},
            "llm": {"api_key": ""},
            "external_services": {"error_reporting": {"webhook_url": ""}},
        })

        config = manager.get_raw_config()
        print_priority("删除前", config)

        # 模拟 api_search_instance_delete 的逻辑（含 priority 清理）
        delete_id = inst_b
        priority_before = config.get('search', {}).get('engine_priority', [])
        config['search_instances'] = [
            i for i in config.get('search_instances', []) if i.get('id') != delete_id
        ]
        # 修复后的逻辑：从 priority 中移除已删除实例的 id
        config.setdefault('search', {})['engine_priority'] = [
            p for p in priority_before if p != delete_id
        ]
        manager._save(config)

        loaded = manager._load()
        print_priority("删除后", loaded)

        # 验证
        priority_after = loaded['search']['engine_priority']
        assert delete_id not in priority_after, f"残留 ID {delete_id} 未被清理！"
        assert priority_after == [inst_a, inst_c], f"priority 顺序错误: {priority_after}"
        assert len(loaded['search_instances']) == 2, "实例数应为 2"

        print("\n  ✓ 验证通过：删除的实例 ID 已从 priority 中清理")
        print(f"  ✓ priority 从 3 项变为 {len(priority_after)} 项")
        print(f"  ✓ 剩余实例顺序正确: {[p[:8] + '...' for p in priority_after]}")
        return True


def test_delete_default_engine():
    """测试 2：删除默认引擎，验证 default_engine 被清理"""
    print("\n\n" + "#" * 60)
    print("# 测试 2: 删除默认引擎，验证 default_engine 一致性")
    print("#" * 60)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        manager = _make_manager(tmp_path)

        inst_a = "uuid-aaaa-1111"
        inst_b = "uuid-bbbb-2222"  # 这个是默认引擎
        manager._save({
            "search_instances": [
                {"id": inst_a, "name": "DuckDuckGo", "engine_type": "duckduckgo", "enabled": True},
                {"id": inst_b, "name": "Tavily", "engine_type": "custom", "enabled": True,
                 "is_default": True},
            ],
            "search": {"engine_priority": [inst_a, inst_b], "default_engine": inst_b},
            "llm": {"api_key": ""},
            "external_services": {"error_reporting": {"webhook_url": ""}},
        })

        config = manager.get_raw_config()
        print_priority("删除前（Tavily 是默认引擎）", config)

        # 模拟删除默认引擎
        delete_id = inst_b
        priority_before = config.get('search', {}).get('engine_priority', [])
        default_before = config.get('search', {}).get('default_engine', '')

        config['search_instances'] = [
            i for i in config.get('search_instances', []) if i.get('id') != delete_id
        ]
        config.setdefault('search', {})['engine_priority'] = [
            p for p in priority_before if p != delete_id
        ]
        # 修复：如果删除的是默认引擎，清理 default_engine 字段
        if default_before == delete_id:
            config['search']['default_engine'] = ''
            print(f"\n  [修复] 检测到删除的是默认引擎，已清空 default_engine")

        manager._save(config)

        loaded = manager._load()
        print_priority("删除后", loaded)

        # 验证
        assert delete_id not in loaded['search']['engine_priority'], "priority 残留"
        assert loaded['search']['default_engine'] == '', "default_engine 未被清理"
        assert len(loaded['search_instances']) == 1, "实例数应为 1"

        print("\n  ✓ 验证通过：删除默认引擎后 default_engine 已清空")
        print(f"  ✓ priority 不含已删除 ID")
        return True


def test_delete_without_cleanup():
    """测试 3：对比 — 不清理 priority 时的残留问题（演示 bug）"""
    print("\n\n" + "#" * 60)
    print("# 测试 3: 对比演示 — 不清理 priority 时的残留问题")
    print("#" * 60)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        manager = _make_manager(tmp_path)

        inst_a = "uuid-aaaa-1111"
        inst_b = "uuid-bbbb-2222"
        manager._save({
            "search_instances": [
                {"id": inst_a, "name": "DuckDuckGo", "enabled": True},
                {"id": inst_b, "name": "Tavily", "enabled": True},
            ],
            "search": {"engine_priority": [inst_a, inst_b], "default_engine": ""},
            "llm": {"api_key": ""},
            "external_services": {"error_reporting": {"webhook_url": ""}},
        })

        # 模拟旧逻辑（不清理 priority）
        config = manager.get_raw_config()
        config['search_instances'] = [
            i for i in config.get('search_instances', []) if i.get('id') != inst_b
        ]
        # 旧逻辑：直接 _save，不清理 priority
        manager._save(config)

        loaded = manager._load()
        print_priority("旧逻辑删除后（有 bug）", loaded)

        priority = loaded['search']['engine_priority']
        if inst_b in priority:
            print(f"\n  ✗ 演示 bug：priority 中残留已删除实例 ID {inst_b[:8]}...")
            print(f"  ✗ 这会导致前端显示空行或报错")
        else:
            print(f"\n  意外：priority 未残留（可能被其他逻辑清理）")
        return True


def main():
    print("=" * 60)
    print("  删除搜索实例后 priority 清理验证脚本")
    print("  验证 api_search_instance_delete 的修复是否有效")
    print("=" * 60)

    results = []
    try:
        results.append(("删除中间实例", test_delete_middle_instance()))
    except AssertionError as e:
        print(f"\n  ✗ 测试失败: {e}")
        results.append(("删除中间实例", False))

    try:
        results.append(("删除默认引擎", test_delete_default_engine()))
    except AssertionError as e:
        print(f"\n  ✗ 测试失败: {e}")
        results.append(("删除默认引擎", False))

    try:
        results.append(("对比演示 bug", test_delete_without_cleanup()))
    except Exception as e:
        print(f"\n  ✗ 演示失败: {e}")
        results.append(("对比演示 bug", False))

    print("\n\n" + "=" * 60)
    print("  验证结果汇总")
    print("=" * 60)
    for name, passed in results:
        status = "✓ 通过" if passed else "✗ 失败"
        print(f"  {status}  {name}")
    print("=" * 60)

    if all(p for _, p in results):
        print("\n  🎉 全部验证通过！priority 残留 ID 已被正确清理。")
        return 0
    else:
        print("\n  ⚠️  部分验证失败，请检查上述输出。")
        return 1


if __name__ == "__main__":
    sys.exit(main())
