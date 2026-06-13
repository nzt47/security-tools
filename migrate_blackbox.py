
#!/usr/bin/env python3
"""迁移黑匣子日志到加密格式
"""

import sys
import os
import logging

# 设置日志级别为 DEBUG，方便查看详细过程
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from memory.black_box import BlackBox


def main():
    print("=" * 70)
    print("黑匣子日志加密迁移工具")
    print("=" * 70)

    log_dir = "./data/blackbox"
    print(f"\n日志目录: {log_dir}")

    # 初始化黑匣子
    print("\n初始化黑匣子...")
    bb = BlackBox(
        log_dir=log_dir,
        encryption_enabled=True
    )

    # 显示当前统计
    print("\n当前状态:")
    stats = bb.get_stats()
    print(f"  文件数量: {stats['file_count']}")
    print(f"  总日志条数: {stats['total_entries']}")
    print(f"  加密可用: {stats['encryption_available']}")
    print(f"  加密启用: {stats['encryption_enabled']}")

    if not stats['encryption_enabled']:
        print("\n⚠️  加密未启用，无法执行迁移")
        return 1

    # 分析现有日志
    print("\n分析现有日志:")
    analysis = bb.analyze()
    print(f"  统计: {analysis}")

    # 执行迁移
    print("\n开始迁移...")
    result = bb.migrate_to_encrypted()

    print("\n" + "=" * 70)
    print("迁移完成！")
    print(f"  成功迁移: {result['migrated']} 条记录")

    if result['errors']:
        print(f"\n警告: {len(result['errors'])} 个错误")
        for err in result['errors'][:5]:
            print(f"  - {err}")

    # 验证结果
    print("\n验证结果验证...")
    new_stats = bb.get_stats()
    print(f"  文件数量: {new_stats['file_count']}")
    print(f"  总日志条数: {new_stats['total_entries']}")

    new_analysis = bb.analyze()
    print(f"\n新统计: {new_analysis}")

    # 测试查询
    print("\n测试查询（自动解密）:")
    entries = bb.query(limit=3)
    print(f"查询到 {len(entries)} 条记录")
    for i, entry in enumerate(entries):
        print(f"\n记录 {i+1}:")
        print(f"  ID: {entry.get('id')}")
        print(f"  类型: {entry.get('event_type')}")
        print(f"  加密: {entry.get('_encrypted')}")
        print(f"  迁移: {entry.get('_migrated')}")

    print("\n" + "=" * 70)
    print("✅ 所有步骤完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
