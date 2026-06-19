"""测试文件合并脚本 - 安全合并补充测试文件到主文件

策略:
1. 每组选一个主文件
2. 将小补充文件(<=300行)的方法/类合并到主文件
3. 对冲突类名自动加前缀
4. 大文件(>300行)保留原样
"""

import os
import re
import shutil
import sys

# 修复 Windows 控制台编码
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def get_test_classes(content: str) -> dict:
    """提取文件中的测试类和函数"""
    classes = {}
    for m in re.finditer(r'^class\s+(\w+).*', content, re.MULTILINE):
        name = m.group(1)
        # 找类体的起始和结束位置
        start = m.start()
        # 简单启发式：找下一个 class 或文件末尾
        rest = content[m.end():]
        next_class = re.search(r'^\nclass\s', rest, re.MULTILINE)
        if next_class:
            end = m.end() + next_class.start()
        else:
            end = len(content)
        classes[name] = content[start:end]
    return classes


def merge_files(primary_path: str, supplement_paths: list[str], backup_dir: str):
    """将 supplement 文件合并到 primary 文件"""
    with open(primary_path, 'r', encoding='utf-8') as f:
        primary_content = f.read()

    # 获取主文件已有类名
    existing_classes = set(re.findall(r'^class\s+(\w+)', primary_content, re.MULTILINE))

    # 备份 supplement 文件
    os.makedirs(backup_dir, exist_ok=True)

    for sp in supplement_paths:
        base = os.path.basename(sp)
        with open(sp, 'r', encoding='utf-8') as f:
            content = f.read()

        # 提取 import 块（文件顶部的 import 语句）
        imports = []
        for m in re.finditer(r'^(import |from )', content, re.MULTILINE):
            imports.append(m.group(0))

        # 提取类
        classes = get_test_classes(content)

        # 处理冲突类名
        added_classes = []
        for cls_name, cls_body in classes.items():
            final_name = cls_name
            if cls_name in existing_classes:
                # 加前缀避免冲突
                prefix = re.sub(r'test_(\w+)\.py', r'\1', base)
                prefix = re.sub(r'test_', '', prefix.split('.')[0])
                final_name = f'Test{prefix.title().replace("_", "")}{cls_name.replace("Test", "")}'
                cls_body = cls_body.replace(f'class {cls_name}', f'class {final_name}', 1)

            primary_content += f"\n\n# — 迁移自 {base}\n"
            primary_content += cls_body
            existing_classes.add(final_name)
            added_classes.append(final_name)

        # 备份 supplement 到 archive
        shutil.move(sp, os.path.join(backup_dir, base))
        print(f"  ✓ {base} → {len(added_classes)} 个类合并到 {os.path.basename(primary_path)}")

    # 写回主文件
    with open(primary_path, 'w', encoding='utf-8') as f:
        f.write(primary_content)
    print(f"  ✓ {os.path.basename(primary_path)} 更新完成")


# ═══════════════════════════════════════════════════
# 主执行逻辑
# ═══════════════════════════════════════════════════

TEST_DIR = os.path.dirname(os.path.abspath(__file__))
BACKUP_DIR = os.path.join(os.path.dirname(TEST_DIR), 'scripts', 'archive', 'tests_merged')

# — system_tools 组 —
SYSTEM_TOOLS_PRIMARY = os.path.join(TEST_DIR, 'unit', 'test_system_tools.py')
SYSTEM_TOOLS_SMALL = [
    os.path.join(TEST_DIR, 'unit', 'test_system_tools_additional.py'),     # 116行
    os.path.join(TEST_DIR, 'unit', 'test_system_tools_supplement.py'),     # 182行
    os.path.join(TEST_DIR, 'unit', 'test_system_tools_full_mock.py'),      # 148行
    os.path.join(TEST_DIR, 'unit', 'test_system_tools_path.py'),           # 221行
    os.path.join(TEST_DIR, 'unit', 'test_system_tools_platform.py'),       # 248行
    os.path.join(TEST_DIR, 'unit', 'test_system_tools_platform_mock.py'),  # 212行
]

# — error_handler 组 —
ERROR_HANDLER_PRIMARY = os.path.join(TEST_DIR, 'unit', 'test_error_handler.py')
ERROR_HANDLER_SMALL = [
    os.path.join(TEST_DIR, 'unit', 'test_error_handler_comprehensive.py'),
    os.path.join(TEST_DIR, 'unit', 'test_error_handler_final.py'),
    os.path.join(TEST_DIR, 'unit', 'test_error_handler_final_coverage.py'),
    os.path.join(TEST_DIR, 'unit', 'test_error_handler_last.py'),
    os.path.join(TEST_DIR, 'unit', 'test_error_handler_remaining.py'),
    os.path.join(TEST_DIR, 'unit', 'test_error_handler_supplement.py'),
]

# — network_config 组 —
NETWORK_CONFIG_PRIMARY = os.path.join(TEST_DIR, 'unit', 'test_network_config.py')
NETWORK_CONFIG_SMALL = [
    os.path.join(TEST_DIR, 'unit', 'test_network_config_supplement.py'),
]

# — monitoring 组 —
MONITORING_PRIMARY = os.path.join(TEST_DIR, 'unit', 'test_monitoring.py')
MONITORING_SMALL = [
    os.path.join(TEST_DIR, 'unit', 'test_monitoring_decorators.py'),
    os.path.join(TEST_DIR, 'unit', 'test_monitoring_error_reporter.py'),
    os.path.join(TEST_DIR, 'unit', 'test_monitoring_metrics.py'),
    os.path.join(TEST_DIR, 'unit', 'test_monitoring_tracing.py'),
]

# — task_scheduler 组: 只合并小文件 —
TASK_SCHEDULER_PRIMARY = os.path.join(TEST_DIR, 'unit', 'test_task_scheduler.py')
TASK_SCHEDULER_SMALL = [
    os.path.join(TEST_DIR, 'unit', 'test_task_scheduler_simple.py'),
    os.path.join(TEST_DIR, 'unit', 'test_task_scheduler_supplement.py'),
]


if __name__ == '__main__':
    groups = [
        ("system_tools", SYSTEM_TOOLS_PRIMARY, SYSTEM_TOOLS_SMALL),
        ("error_handler", ERROR_HANDLER_PRIMARY, ERROR_HANDLER_SMALL),
        ("network_config", NETWORK_CONFIG_PRIMARY, NETWORK_CONFIG_SMALL),
        ("monitoring", MONITORING_PRIMARY, MONITORING_SMALL),
        ("task_scheduler", TASK_SCHEDULER_PRIMARY, TASK_SCHEDULER_SMALL),
    ]

    for name, primary, supplements in groups:
        # 只处理存在的文件
        existing_supplements = [s for s in supplements if os.path.exists(s)]
        if not existing_supplements:
            print(f"— {name}: 无补充文件可合并")
            continue
        if not os.path.exists(primary):
            print(f"— {name}: 主文件 {primary} 不存在，跳过")
            continue

        print(f"\n▶ 合并 {name} ({len(existing_supplements)} 个文件 → {os.path.basename(primary)})")
        merge_files(primary, existing_supplements, BACKUP_DIR)

    print(f"\n✅ 合并完成！备份文件在: {BACKUP_DIR}")
    print("   删除备份：rm -rf scripts/archive/tests_merged/")
