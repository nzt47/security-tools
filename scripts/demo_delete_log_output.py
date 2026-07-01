"""模拟删除搜索实例操作，展示日志格式化器的控制台输出效果

模拟 app_server.py 中 api_search_instance_delete 的完整流程，
包括 priority 清理、default_engine 清理、结构化日志输出。
"""

import sys
import json
import time
import tempfile
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).parent.parent))

# 启用结构化日志易读格式
from scripts.struct_log_formatter import setup_readable_logging
setup_readable_logging()

import logging
logger = logging.getLogger("app_server")


def _trace_id():
    import uuid
    return uuid.uuid4().hex[:16]


def _log_struct(action, message, duration_ms=0, **extra):
    """与 app_server.py 中的 _log_struct 保持一致"""
    payload = {
        "trace_id": _trace_id(),
        "module_name": "app_server",
        "action": action,
        "duration_ms": duration_ms,
        "message": message,
    }
    payload.update(extra)
    logger.info(json.dumps(payload, ensure_ascii=False))


def simulate_delete_instance():
    """模拟删除搜索实例的完整流程"""
    from agent.network_config import NetworkConfigManager

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        secure_store = {}
        mock_secure = Mock()
        mock_secure.set_secure_value = Mock(side_effect=lambda k, v: secure_store.update({k: v}))
        mock_secure.get_secure_value = Mock(side_effect=lambda k, default=None: secure_store.get(k, default))

        manager = NetworkConfigManager(
            config_file=str(tmp_path / "network_config.json"),
            secure_manager=mock_secure
        )

        # 初始配置：3 个实例，中间一个是默认引擎
        inst_a = "uuid-aaaa-1111-2222"
        inst_b = "uuid-bbbb-3333-4444"  # 默认引擎
        inst_c = "uuid-cccc-5555-6666"

        manager._save({
            "search_instances": [
                {"id": inst_a, "name": "DuckDuckGo", "engine_type": "duckduckgo", "enabled": True},
                {"id": inst_b, "name": "Tavily", "engine_type": "custom", "enabled": True, "is_default": True},
                {"id": inst_c, "name": "搜狗搜索", "engine_type": "sogou", "enabled": True},
            ],
            "search": {"engine_priority": [inst_a, inst_b, inst_c], "default_engine": inst_b},
            "llm": {"api_key": ""},
            "external_services": {"error_reporting": {"webhook_url": ""}},
        })

        print("\n" + "=" * 70)
        print("  模拟删除搜索实例：Tavily（默认引擎）")
        print("=" * 70)

        t0 = time.time()
        instance_id = inst_b

        # 模拟 api_search_instance_delete 的逻辑
        config = manager.get_raw_config()
        before = len(config.get('search_instances', []))
        priority_before = config.get('search', {}).get('engine_priority', [])
        default_before = config.get('search', {}).get('default_engine', '')

        config['search_instances'] = [
            i for i in config.get('search_instances', []) if i.get('id') != instance_id
        ]

        if len(config['search_instances']) < before:
            # 清理 priority
            config.setdefault('search', {})['engine_priority'] = [
                p for p in priority_before if p != instance_id
            ]
            # 清理 default_engine
            default_changed = False
            if default_before == instance_id:
                config['search']['default_engine'] = ''
                default_changed = True

            manager._save(config)
            manager._save_secure(f'search_{instance_id}_api_key', '')

            priority_after = manager.get_all().get('search', {}).get('engine_priority', [])

            # 输出结构化日志（会通过格式化器美化显示）
            _log_struct(
                'api_search_instance_delete.done',
                '搜索实例已删除',
                duration_ms=int((time.time() - t0) * 1000),
                instance_id=instance_id,
                instance_name='Tavily',
                priority_before=priority_before,
                priority_after=priority_after,
                priority_changed=priority_before != priority_after,
                default_engine_cleared=default_changed,
            )

            print(f"\n  删除完成，耗时 {int((time.time() - t0) * 1000)}ms")
            print(f"  剩余实例数: {len(config['search_instances'])}")
            print(f"  priority: {priority_after}")
            print(f"  default_engine: {config['search']['default_engine'] or '(空)'}")


def simulate_update_config():
    """模拟保存网络配置（含 priority 变化）"""
    from agent.network_config import NetworkConfigManager

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        secure_store = {}
        mock_secure = Mock()
        mock_secure.set_secure_value = Mock(side_effect=lambda k, v: secure_store.update({k: v}))
        mock_secure.get_secure_value = Mock(side_effect=lambda k, default=None: secure_store.get(k, default))

        manager = NetworkConfigManager(
            config_file=str(tmp_path / "network_config.json"),
            secure_manager=mock_secure
        )

        manager._save({
            "search_instances": [
                {"id": "uuid-aaa", "name": "DuckDuckGo", "enabled": True},
                {"id": "uuid-bbb", "name": "Tavily", "enabled": True},
            ],
            "search": {"engine_priority": ["uuid-aaa", "uuid-bbb"], "default_engine": ""},
            "llm": {"api_key": ""},
            "external_services": {"error_reporting": {"webhook_url": ""}},
        })

        print("\n" + "=" * 70)
        print("  模拟保存网络配置（调整 priority 顺序）")
        print("=" * 70)

        t0 = time.time()
        before = ["uuid-aaa", "uuid-bbb"]
        after = ["uuid-bbb", "uuid-aaa"]  # 顺序反转

        _log_struct(
            'api_network_config_update.done',
            '网络配置已更新',
            duration_ms=int((time.time() - t0) * 1000),
            priority_before=before,
            priority_after=after,
            priority_changed=True,
            default_engine='uuid-bbb',
        )


def simulate_add_instance():
    """模拟新增搜索实例"""
    print("\n" + "=" * 70)
    print("  模拟新增搜索实例")
    print("=" * 70)

    t0 = time.time()
    _log_struct(
        'api_search_instance_add.done',
        '搜索实例已新增',
        duration_ms=int((time.time() - t0) * 1000) + 45,
        instance_id='550e8400-e29b-41d4-a716-446655440000',
        instance_name='Firecrawl',
        engine_type='custom',
        priority_before=["uuid-aaa", "uuid-bbb"],
        priority_after=["uuid-aaa", "uuid-bbb", "550e8400"],
        priority_changed=True,
    )


def simulate_set_default():
    """模拟设置默认引擎"""
    print("\n" + "=" * 70)
    print("  模拟设置默认搜索引擎")
    print("=" * 70)

    t0 = time.time()
    _log_struct(
        'api_search_instance_set_default.done',
        '已设为默认搜索引擎',
        duration_ms=int((time.time() - t0) * 1000) + 22,
        instance_id='uuid-ccc',
        instance_name='DuckDuckGo',
        default_before='uuid-aaa',
        default_after='uuid-ccc',
    )


def simulate_error():
    """模拟更新失败"""
    print("\n" + "=" * 70)
    print("  模拟更新失败（异常路径）")
    print("=" * 70)

    t0 = time.time()
    _log_struct(
        'api_search_instance_update.failed',
        '更新搜索实例失败: ValueError',
        duration_ms=int((time.time() - t0) * 1000) + 5,
        instance_id='uuid-xxx',
        error='实例不存在',
    )


if __name__ == "__main__":
    print("=" * 70)
    print("  日志格式化器输出效果演示")
    print("  展示删除/保存/新增/设置默认/失败 5 种场景的日志输出")
    print("=" * 70)

    simulate_delete_instance()
    simulate_update_config()
    simulate_add_instance()
    simulate_set_default()
    simulate_error()

    print("\n" + "=" * 70)
    print("  演示完成")
    print("=" * 70)
