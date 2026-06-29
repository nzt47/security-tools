#!/usr/bin/env python3
"""
DigitalLife V1 到 V2 自动化迁移脚本

功能：
1. 自动检测配置文件版本
2. 备份原配置文件
3. 添加 V2 features 配置项
4. 支持批量迁移
5. 生成迁移报告
6. 详细的日志输出，记录每个配置项的变更过程

使用方法：
    python migrate_to_v2.py                          # 交互式
    python migrate_to_v2.py config.yaml             # 指定文件
    python migrate_to_v2.py --dry-run config.yaml   # 预览模式
    python migrate_to_v2.py --batch *.yaml          # 批量迁移
"""

import argparse
import os
import sys
import shutil
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field

# 尝试导入 yaml，若不可用则使用 json 作为备选
try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False
    import json

# ============================================
# 数据类定义
# ============================================

@dataclass
class ConfigChange:
    """配置项变更记录"""
    path: str                    # 配置路径，如 "features.v2_lifetrace"
    old_value: Any               # 旧值
    new_value: Any              # 新值
    change_type: str             # 变更类型：added/changed/removed/preserved
    reason: str = ""             # 变更原因

    def to_dict(self) -> dict:
        return {
            'path': self.path,
            'old_value': self.old_value,
            'new_value': self.new_value,
            'change_type': self.change_type,
            'reason': self.reason
        }


@dataclass
class MigrationLog:
    """迁移日志记录"""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    file_path: str = ""
    source_version: str = ""
    target_version: str = "v2"
    changes: List[ConfigChange] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    
    def add_change(self, change: ConfigChange):
        self.changes.append(change)
        
    def add_warning(self, msg: str):
        self.warnings.append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
        
    def add_error(self, msg: str):
        self.errors.append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

    def to_dict(self) -> dict:
        return {
            'timestamp': self.timestamp,
            'file_path': self.file_path,
            'source_version': self.source_version,
            'target_version': self.target_version,
            'changes': [c.to_dict() for c in self.changes],
            'warnings': self.warnings,
            'errors': self.errors
        }


# ============================================
# 颜色定义（Windows PowerShell 支持 ANSI）
# ============================================

class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    DIM = '\033[2m'


# ============================================
# 日志输出函数
# ============================================

def print_info(msg: str):
    print(f"{Colors.OKCYAN}[INFO]{Colors.ENDC} {msg}")


def print_success(msg: str):
    print(f"{Colors.OKGREEN}[SUCCESS]{Colors.ENDC} {msg}")


def print_warning(msg: str):
    print(f"{Colors.WARNING}[WARNING]{Colors.ENDC} {msg}")


def print_error(msg: str):
    print(f"{Colors.FAIL}[ERROR]{Colors.ENDC} {msg}")


def print_header(msg: str):
    print(f"\n{Colors.BOLD}{Colors.HEADER}{'='*60}")
    print(f"  {msg}")
    print(f"{'='*60}{Colors.ENDC}\n")


def print_debug(msg: str):
    """打印调试信息"""
    print(f"{Colors.DIM}[DEBUG]{Colors.ENDC} {msg}")


def print_change(change: ConfigChange, indent: int = 4):
    """打印配置变更详情"""
    spaces = " " * indent
    
    if change.change_type == 'added':
        print(f"{spaces}{Colors.OKGREEN}+{Colors.ENDC} {Colors.OKCYAN}{change.path}{Colors.ENDC}")
        print(f"{spaces}  {Colors.DIM}旧值: {Colors.ENDC}{Colors.DIM}<未设置>{Colors.ENDC}")
        print(f"{spaces}  {Colors.OKGREEN}新值: {Colors.ENDC}{change.new_value}")
    elif change.change_type == 'changed':
        print(f"{spaces}{Colors.WARNING}~{Colors.ENDC} {Colors.OKCYAN}{change.path}{Colors.ENDC}")
        print(f"{spaces}  {Colors.WARNING}旧值: {Colors.ENDC}{change.old_value}")
        print(f"{spaces}  {Colors.OKGREEN}新值: {Colors.ENDC}{change.new_value}")
    elif change.change_type == 'preserved':
        print(f"{spaces}{Colors.DIM}={Colors.ENDC} {Colors.OKCYAN}{change.path}{Colors.ENDC}")
        print(f"{spaces}  {Colors.DIM}保持不变: {Colors.ENDC}{change.new_value}")


# ============================================
# 配置加载和保存
# ============================================

def load_config(file_path: Path) -> Dict[str, Any]:
    """加载配置文件"""
    print_debug(f"开始加载配置文件: {file_path}")
    with open(file_path, 'r', encoding='utf-8') as f:
        if YAML_AVAILABLE and file_path.suffix in ['.yaml', '.yml']:
            config = yaml.safe_load(f) or {}
            print_debug(f"YAML 配置加载成功，包含 {len(config)} 个顶级键")
            return config
        else:
            config = json.load(f)
            print_debug(f"JSON 配置加载成功，包含 {len(config)} 个顶级键")
            return config


def save_config(config: Dict[str, Any], file_path: Path):
    """保存配置文件"""
    print_debug(f"开始保存配置文件: {file_path}")
    with open(file_path, 'w', encoding='utf-8') as f:
        if YAML_AVAILABLE and file_path.suffix in ['.yaml', '.yml']:
            yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
            print_debug(f"YAML 配置保存成功")
        else:
            json.dump(config, f, indent=2, ensure_ascii=False)
            print_debug(f"JSON 配置保存成功")


def detect_version(config: Dict[str, Any]) -> str:
    """检测配置版本"""
    print_debug("开始检测配置版本...")
    
    if 'features' in config:
        # 检查 features 中是否包含所有必需的 V2 键
        features = config.get('features', {})
        required_keys = {'v2_lifetrace', 'v2_persona', 'v2_distillation'}
        if required_keys.issubset(features.keys()):
            print_debug("检测结果: V2 (完整)")
            return 'v2'
        else:
            print_debug("检测结果: V2 (部分)")
            return 'v2_partial'
    elif any(key in config for key in ['memory', 'behavior', 'planning']):
        print_debug("检测结果: V1")
        return 'v1'
    elif 'v2_lifetrace' in config or 'v2_persona' in config:
        print_debug("检测结果: V2 (部分键在根级别)")
        return 'v2_partial'
    else:
        print_debug("检测结果: 未知格式")
        return 'unknown'


# ============================================
# 配置迁移核心逻辑
# ============================================

def migrate_config(
    config: Dict[str, Any], 
    options: Dict[str, Any],
    log: MigrationLog
) -> Dict[str, Any]:
    """
    迁移配置从 V1 到 V2
    
    迁移规则：
    1. 保留所有 V1 配置项
    2. 添加 features.v2_lifetrace（默认 false）
    3. 添加 features.v2_persona（默认 false）
    4. 添加 features.v2_distillation（默认 false）
    5. 添加 features.v2_lazy_loader（默认 true）
    """
    print_info("开始配置迁移分析...")
    
    new_config = config.copy()
    
    # 初始化 features 字典
    if 'features' not in new_config:
        print_debug("创建新的 features 配置节")
        new_config['features'] = {}
    else:
        print_debug(f"复用已存在的 features 配置节，当前包含: {list(new_config['features'].keys())}")
    
    features = new_config['features']
    
    # V2 新增配置项（带默认值）
    default_features = {
        'v2_lifetrace': {
            'default': options.get('lifetrace', False),
            'description': 'LifeTrace 长期记忆系统',
            'requires': 'lifetrace 模块'
        },
        'v2_persona': {
            'default': options.get('persona', False),
            'description': '动态人格注入系统',
            'requires': 'persona 模块'
        },
        'v2_distillation': {
            'default': options.get('distillation', False),
            'description': '人格蒸馏系统（从对话学习偏好）',
            'requires': 'persona 模块'
        },
        'v2_lazy_loader': {
            'default': True,
            'description': '模块懒加载机制',
            'requires': '无'
        },
    }
    
    # 遍历每个 V2 配置项，记录变更
    print_info("分析 V2 配置项变更...")
    for key, meta in default_features.items():
        path = f"features.{key}"
        default_value = meta['default']
        description = meta['description']
        
        old_value = features.get(key, '<未设置>')
        new_value = features.get(key, default_value)
        
        print_debug(f"  检查 {key}:")
        print_debug(f"    - 描述: {description}")
        print_debug(f"    - 需要: {meta['requires']}")
        print_debug(f"    - 当前值: {old_value}")
        print_debug(f"    - 将被设置为: {new_value}")
        
        # 设置值（如果用户未显式设置）
        if key not in features:
            features[key] = default_value
            change = ConfigChange(
                path=path,
                old_value='<未设置>',
                new_value=default_value,
                change_type='added',
                reason=f'新增 V2 功能配置项: {description}'
            )
            log.add_change(change)
            print_debug(f"    - 变更: 添加新配置项")
        elif features[key] != default_value:
            # 用户显式设置了不同的值
            change = ConfigChange(
                path=path,
                old_value=old_value,
                new_value=new_value,
                change_type='preserved',
                reason='保留用户显式设置的值'
            )
            log.add_change(change)
            print_debug(f"    - 变更: 保留用户设置")
        else:
            change = ConfigChange(
                path=path,
                old_value=old_value,
                new_value=new_value,
                change_type='preserved',
                reason='配置值与默认值相同'
            )
            log.add_change(change)
            print_debug(f"    - 变更: 无变更")
    
    # 检查其他配置节的兼容性
    print_info("检查配置兼容性...")
    compatibility_checks = [
        ('memory', '记忆管理配置', ['max_tokens', 'enable_long_term']),
        ('behavior', '行为控制配置', ['default_mode', 'allow_reflection']),
        ('planning', '规划引擎配置', ['enabled', 'max_depth']),
        ('llm', 'LLM 配置', ['provider', 'model']),
        ('tools', '工具配置', ['enabled', 'whitelist']),
        ('session', '会话配置', ['auto_save', 'save_interval']),
    ]
    
    for section, name, keys in compatibility_checks:
        if section in config:
            print_debug(f"  {name} ({section}):")
            for key in keys:
                if key in config[section]:
                    print_debug(f"    - {key}: {config[section][key]}")
            print_debug(f"    状态: {Colors.OKGREEN}兼容{Colors.ENDC}")
        else:
            print_debug(f"  {name} ({section}):")
            print_debug(f"    状态: {Colors.DIM}不存在（可选）{Colors.ENDC}")
    
    return new_config


def migrate_file(
    file_path: Path, 
    backup: bool = True,
    dry_run: bool = False,
    options: Optional[Dict[str, Any]] = None,
    verbose: bool = True
) -> Dict[str, Any]:
    """
    迁移单个配置文件
    
    返回：
        包含迁移结果的字典
    """
    options = options or {}
    
    # 创建迁移日志
    migration_log = MigrationLog()
    migration_log.file_path = str(file_path)
    
    result = {
        'file': str(file_path),
        'success': False,
        'backup': None,
        'changes': [],
        'log': migration_log,
        'error': None
    }
    
    try:
        # 阶段 1: 加载配置
        if verbose:
            print_info(f"阶段 1/4: 读取配置文件")
        print_debug(f"  文件路径: {file_path}")
        
        config = load_config(file_path)
        print_debug(f"  配置键数量: {len(config)}")
        
        # 阶段 2: 检测版本
        if verbose:
            print_info(f"阶段 2/4: 检测配置版本")
        version = detect_version(config)
        migration_log.source_version = version
        print_info(f"  源版本: {version}")
        
        if version == 'v2':
            print_warning("  配置文件已是 V2 格式，将检查并补充缺失的配置项")
            migration_log.add_warning("配置文件已是 V2 格式")
        elif version not in ['v1', 'v2', 'v2_partial']:
            print_warning(f"  无法识别的配置格式，将尝试迁移")
            migration_log.add_warning(f"无法识别的配置格式: {version}")
        
        # 阶段 3: 执行迁移
        if verbose:
            print_info(f"阶段 3/4: 执行配置迁移")
        new_config = migrate_config(config, options, migration_log)
        
        # 统计变更
        added_count = sum(1 for c in migration_log.changes if c.change_type == 'added')
        changed_count = sum(1 for c in migration_log.changes if c.change_type == 'changed')
        preserved_count = sum(1 for c in migration_log.changes if c.change_type == 'preserved')
        
        print_info(f"  变更统计: 新增 {added_count}, 修改 {changed_count}, 保留 {preserved_count}")
        
        if not migration_log.changes:
            print_info("  配置无需修改，已是最新格式")
            result['success'] = True
            result['changes'] = [c.to_dict() for c in migration_log.changes]
            return result
        
        # 显示变更详情
        if verbose:
            print_header("配置变更详情")
            print(f"  {'操作':<10} {'配置路径':<30} {'旧值':<20} {'新值':<20}")
            print(f"  {'-'*10} {'-'*30} {'-'*20} {'-'*20}")
            for change in migration_log.changes:
                op_symbol = {'added': '+', 'changed': '~', 'preserved': '='}[change.change_type]
                op_color = {'added': Colors.OKGREEN, 'changed': Colors.WARNING, 'preserved': Colors.DIM}[change.change_type]
                old_val_str = str(change.old_value)[:18] if change.old_value != '<未设置>' else '<未设置>'
                new_val_str = str(change.new_value)[:18]
                print(f"  {op_color}{op_symbol:<10}{Colors.ENDC} {Colors.OKCYAN}{change.path:<30}{Colors.ENDC} {Colors.WARNING}{old_val_str:<20}{Colors.ENDC} {Colors.OKGREEN}{new_val_str:<20}{Colors.ENDC}")
            
            print()
            print_info("变更原因:")
            for change in migration_log.changes:
                if change.change_type in ['added', 'changed']:
                    print(f"  • {change.path}: {change.reason}")
        
        # 阶段 4: 备份和写入
        if verbose:
            print_info(f"阶段 4/4: 保存配置文件")
        
        # 备份原文件
        if backup and not dry_run:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_path = file_path.with_suffix(f'.v1_backup_{timestamp}{file_path.suffix}')
            shutil.copy2(file_path, backup_path)
            print_success(f"  备份完成: {backup_path}")
            result['backup'] = str(backup_path)
            migration_log.add_warning(f"已创建备份: {backup_path}")
        elif backup:
            print_info(f"  [DRY RUN] 将创建备份: {file_path.stem}.v1_backup_YYYYMMDD_HHMMSS{file_path.suffix}")
        
        # 写入新配置
        if not dry_run:
            save_config(new_config, file_path)
            print_success(f"  配置已更新: {file_path}")
            migration_log.add_warning(f"配置文件已更新")
        else:
            print_warning("  [DRY RUN] 未实际修改文件")
            migration_log.add_warning("[DRY RUN] 预览模式，未实际修改")
        
        result['success'] = True
        result['changes'] = [c.to_dict() for c in migration_log.changes]
        
    except Exception as e:
        print_error(f"迁移失败: {e}")
        result['error'] = str(e)
        migration_log.add_error(str(e))
        import traceback
        traceback.print_exc()
    
    return result


def generate_migration_report(results: list) -> str:
    """生成迁移报告"""
    total_files = len(results)
    success_count = sum(1 for r in results if r['success'])
    fail_count = total_files - success_count
    
    report_lines = [
        "=" * 70,
        "  DigitalLife V1 到 V2 迁移报告",
        "=" * 70,
        f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        f"总计文件: {total_files}",
        f"成功: {success_count}",
        f"失败: {fail_count}",
        "",
        "-" * 70,
        "文件详情:",
        "-" * 70,
    ]
    
    for i, result in enumerate(results, 1):
        status = "成功" if result['success'] else "失败"
        status_icon = "✓" if result['success'] else "✗"
        report_lines.append(f"\n{i}. [{status_icon}] {result['file']} - {status}")
        
        if result.get('backup'):
            report_lines.append(f"   备份: {result['backup']}")
        
        changes = result.get('changes', [])
        if changes:
            report_lines.append("   变更详情:")
            for change in changes:
                old_val = str(change.get('old_value', ''))[:30]
                new_val = str(change.get('new_value', ''))[:30]
                change_type = change.get('change_type', '')
                path = change.get('path', '')
                reason = change.get('reason', '')
                
                if change_type == 'added':
                    report_lines.append(f"     + {path}: <未设置> -> {new_val}")
                elif change_type == 'changed':
                    report_lines.append(f"     ~ {path}: {old_val} -> {new_val}")
                else:
                    report_lines.append(f"     = {path}: {new_val} (保持不变)")
                
                if reason:
                    report_lines.append(f"       原因: {reason}")
        
        if result.get('error'):
            report_lines.append(f"   错误: {result['error']}")
        
        # 添加详细日志
        log = result.get('log')
        if log and log.warnings:
            report_lines.append("   警告:")
            for warning in log.warnings:
                report_lines.append(f"     - {warning}")
    
    report_lines.append("\n" + "=" * 70)
    report_lines.append("迁移建议:")
    report_lines.append("-" * 70)
    report_lines.append("1. 检查迁移后的配置文件内容")
    report_lines.append("2. 根据需要调整 features 中的各项设置")
    report_lines.append("3. 运行测试验证配置正确性:")
    report_lines.append("   python -m pytest tests/integration/test_digital_life_integration.py -v")
    report_lines.append("4. 如有问题，可使用 .v1_backup_* 文件恢复")
    report_lines.append("=" * 60)
    
    return "\n".join(report_lines)


def interactive_mode():
    """交互式迁移模式"""
    print_header("DigitalLife V1 到 V2 自动化迁移工具")
    
    print_info("请选择要迁移的功能（直接回车使用默认值）:\n")
    
    # 获取用户选择
    lifetrace = input(f"  启用 LifeTrace（长期记忆）[y/N]: ").strip().lower() == 'y'
    persona = input(f"  启用 Persona（动态人格）[y/N]: ").strip().lower() == 'y'
    distillation = input(f"  启用 Distillation（人格蒸馏）[y/N]: ").strip().lower() == 'y'
    
    options = {
        'lifetrace': lifetrace,
        'persona': persona,
        'distillation': distillation,
    }
    
    print_info(f"\n选择的配置: {options}")
    
    # 获取文件路径
    print("\n请输入配置文件路径（多个文件用空格分隔，直接回车使用默认）:")
    default_path = 'config.yaml'
    files_input = input(f"  [默认: {default_path}]: ").strip()
    
    if not files_input:
        file_paths = [Path(default_path)]
    else:
        file_paths = [Path(p.strip()) for p in files_input.split()]
    
    return file_paths, options


def main():
    parser = argparse.ArgumentParser(
        description='DigitalLife V1 到 V2 自动化迁移工具',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s config.yaml                    # 迁移单个文件
  %(prog)s --dry-run config.yaml           # 预览迁移结果
  %(prog)s --batch configs/*.yaml          # 批量迁移
  %(prog)s --lifetrace --persona config.yaml  # 启用指定功能
  %(prog)s -i                             # 交互式迁移
        """
    )
    
    parser.add_argument('files', nargs='*', help='配置文件路径')
    parser.add_argument('-i', '--interactive', action='store_true', help='交互式迁移')
    parser.add_argument('--dry-run', action='store_true', help='预览模式，不实际修改文件')
    parser.add_argument('--no-backup', action='store_true', help='不创建备份文件')
    parser.add_argument('--lifetrace', action='store_true', help='启用 LifeTrace 功能')
    parser.add_argument('--persona', action='store_true', help='启用 Persona 功能')
    parser.add_argument('--distillation', action='store_true', help='启用 Distillation 功能')
    parser.add_argument('--lazy-loader', action='store_true', default=True, help='启用懒加载（默认开启）')
    parser.add_argument('--report', metavar='FILE', help='生成迁移报告文件')
    
    args = parser.parse_args()
    
    # 准备选项
    options = {
        'lifetrace': args.lifetrace,
        'persona': args.persona,
        'distillation': args.distillation,
        'v2_lazy_loader': args.lazy_loader,
    }
    
    # 交互式模式
    if args.interactive:
        file_paths, interactive_options = interactive_mode()
        options.update(interactive_options)
    else:
        if not args.files:
            # 尝试默认配置文件
            default_file = Path('config.yaml')
            if default_file.exists():
                print_info(f"使用默认配置文件: {default_file}")
                file_paths = [default_file]
            else:
                print_error("请指定配置文件路径，或使用 --interactive 交互式迁移")
                print_info("示例: python migrate_to_v2.py config.yaml")
                sys.exit(1)
        else:
            file_paths = [Path(p) for p in args.files]
    
    # 显示模式信息
    if args.dry_run:
        print_warning("[DRY RUN 模式] 不会实际修改任何文件")
    
    if not args.no_backup:
        print_info("将自动创建备份文件")
    
    # 处理每个文件
    results = []
    for file_path in file_paths:
        print_header(f"迁移: {file_path}")
        
        if not file_path.exists():
            print_error(f"文件不存在: {file_path}")
            results.append({
                'file': str(file_path),
                'success': False,
                'error': '文件不存在'
            })
            continue
        
        result = migrate_file(
            file_path,
            backup=not args.no_backup,
            dry_run=args.dry_run,
            options=options
        )
        results.append(result)
    
    # 生成报告
    report = generate_migration_report(results)
    print("\n" + report)
    
    # 保存报告
    if args.report:
        with open(args.report, 'w', encoding='utf-8') as f:
            # 移除 ANSI 颜色码
            import re
            clean_report = re.sub(r'\x1b\[[0-9;]*m', '', report)
            f.write(clean_report)
        print_success(f"报告已保存: {args.report}")
    
    # 返回状态码
    failed = sum(1 for r in results if not r['success'])
    sys.exit(0 if failed == 0 else 1)


if __name__ == '__main__':
    main()
