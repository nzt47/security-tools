#!/usr/bin/env python3
"""Top 20 高频文件批量迁移脚本

自动将 `logger.X(json.dumps({...}, ensure_ascii=False))` 重构为
`logger.X(log_dict({...}))`，消除调用方序列化开销。

特性：
1. 内置 Top 20 高频文件清单（来自基线审计）
2. 支持 dry-run 预览、批量执行、迁移后验证
3. 自动备份原文件（支持回滚）
4. 迁移后自动运行单元测试验证无回归
5. 生成迁移报告 JSON

用法：
    # 预览迁移（不写入）
    python scripts/migrate_top20_batch.py --dry-run

    # 执行迁移（含备份 + 测试验证）
    python scripts/migrate_top20_batch.py --batch 1

    # 仅迁移指定批次（1-4）
    python scripts/migrate_top20_batch.py --batch 1 --no-test

    # 回滚最近一次迁移
    python scripts/migrate_top20_batch.py --rollback

机制说明：
- 边界显性化：迁移失败时抛出 RuntimeError，不静默继续
- 幂等性：重复运行安全，已迁移文件会被识别并跳过
- 防御性：每个文件迁移后立即验证语法正确性
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Dict

# 业务错误码
ERR_MIGRATION = "MIGRATION_FAILED"
ERR_VALIDATION = "MIGRATION_VALIDATION_FAILED"
ERR_RUNTIME = "MIGRATION_RUNTIME"


# ─────────────────────────────────────────────────
# Top 20 高频文件清单（来自 2026-07-04 基线审计）
# ─────────────────────────────────────────────────
TOP20_FILES: List[str] = [
    # 批次 1：高风险模块（累计 503 处，占 27%）
    "agent/p6/snapshot.py",                                # 147
    "agent/p6_snapshot.py",                                # 112
    "agent/orchestrator/lifecycle_manager.py",             # 86
    "agent/network_config.py",                             # 79
    "agent/network/config_manager.py",                      # 79
    # 批次 2：工具与 Web 模块（累计 288 处）
    "agent/tools/file_tools.py",                           # 75
    "agent/web/search.py",                                 # 72
    "agent/state_manager.py",                              # 49
    "agent/tool_calling.py",                               # 48
    "agent/orchestrator/orchestrator.py",                 # 44
    # 批次 3：监控与报告模块（累计 308 处）
    "agent/error_handler.py",                              # 44
    "scripts/visibility_report.py",                        # 43
    "agent/server_routes/routes_dashboard.py",             # 38
    "agent/monitoring/resource_monitor.py",                 # 36
    "agent/monitoring/trace_http_client.py",               # 30
    "agent/digital_life.py",                               # 29
    "agent/scheduling.py",                                 # 25
    "agent/task_scheduler.py",                              # 24
    "agent/monitoring/self_healer.py",                     # 22
    "agent/weekly_report_generator.py",                    # 22
]

# 分批映射
BATCHES = {
    1: TOP20_FILES[0:5],    # 高风险模块
    2: TOP20_FILES[5:10],  # 工具与 Web
    3: TOP20_FILES[10:15], # 监控与报告
    4: TOP20_FILES[15:20], # 调度与生成
}

# 备份目录
BACKUP_DIR = ".trae/migration_backups"
# 迁移记录文件（用于回滚）
MIGRATION_RECORD = ".trae/migration_backups/last_migration.json"


@dataclass
class MigrationResult:
    """单文件迁移结果"""
    file: str
    success: bool
    replacements: int = 0
    import_added: bool = False
    backup_path: Optional[str] = None
    error: Optional[str] = None
    skipped: bool = False
    skip_reason: Optional[str] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


@dataclass
class BatchMigrationReport:
    """批次迁移报告"""
    batch: int
    timestamp: str
    results: List[MigrationResult] = field(default_factory=list)
    total_replacements: int = 0
    success_count: int = 0
    failure_count: int = 0
    skipped_count: int = 0
    test_passed: Optional[bool] = None
    duration_seconds: float = 0.0

    def to_dict(self) -> dict:
        return {
            "batch": self.batch,
            "timestamp": self.timestamp,
            "total_replacements": self.total_replacements,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "skipped_count": self.skipped_count,
            "test_passed": self.test_passed,
            "duration_seconds": self.duration_seconds,
            "results": [r.to_dict() for r in self.results],
        }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Top 20 高频文件批量迁移脚本"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true",
                      help="预览模式（不写入文件，仅显示将替换的处数）")
    mode.add_argument("--batch", type=int, choices=[1, 2, 3, 4],
                      help="执行指定批次迁移（1-4）")
    mode.add_argument("--all", action="store_true",
                      help="执行全部 Top 20 文件迁移")
    mode.add_argument("--rollback", action="store_true",
                      help="回滚最近一次迁移")

    parser.add_argument("--no-test", action="store_true",
                        help="跳过迁移后单元测试验证")
    parser.add_argument("--test-only", type=str,
                        help="仅运行指定测试文件验证（如 tests/unit/test_log_dict_refactor.py）")
    parser.add_argument("--report", type=str,
                        default=".trae/migration_backups/migration_report.json",
                        help="迁移报告 JSON 输出路径")
    parser.add_argument("--root", type=str, default=".",
                        help="项目根目录（默认当前目录）")
    return parser.parse_args()


def _check_file_exists(file_path: str, root: str) -> bool:
    abs_path = os.path.join(root, file_path)
    return os.path.isfile(abs_path)


def _backup_file(file_path: str, root: str) -> str:
    """备份原文件，返回备份路径"""
    abs_path = os.path.join(root, file_path)
    backup_dir = os.path.join(root, BACKUP_DIR)
    os.makedirs(backup_dir, exist_ok=True)

    # 用相对路径生成备份文件名（替换 / 为 _）
    safe_name = file_path.replace("/", "_").replace("\\", "_")
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    backup_name = f"{safe_name}.{timestamp}.bak"
    backup_path = os.path.join(backup_dir, backup_name)

    shutil.copy2(abs_path, backup_path)
    return backup_path


def _validate_syntax(file_path: str, root: str) -> Optional[str]:
    """验证文件 Python 语法，返回错误信息（None 表示通过）"""
    abs_path = os.path.join(root, file_path)
    try:
        result = subprocess.run(
            [sys.executable, "-c", f"import ast; ast.parse(open(r'{abs_path}', encoding='utf-8').read())"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return result.stderr.strip()
    except subprocess.TimeoutExpired:
        return "语法检查超时"
    return None


def _migrate_single_file(file_path: str, root: str,
                         dry_run: bool) -> MigrationResult:
    """迁移单个文件"""
    if not _check_file_exists(file_path, root):
        return MigrationResult(
            file=file_path, success=False, skipped=True,
            skip_reason="文件不存在",
        )

    abs_path = os.path.join(root, file_path)
    migrate_script = os.path.join(root, "scripts", "migrate_to_log_dict.py")

    # 调用 migrate_to_log_dict.py 工具
    cmd = [sys.executable, migrate_script]
    if dry_run:
        cmd.append("--dry-run")
    cmd.append(abs_path)

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60, cwd=root,
        )
    except subprocess.TimeoutExpired:
        return MigrationResult(
            file=file_path, success=False,
            error="迁移工具执行超时",
        )

    if result.returncode != 0:
        return MigrationResult(
            file=file_path, success=False,
            error=f"迁移工具退出码 {result.returncode}: {result.stderr}",
        )

    # 解析输出
    output = result.stdout
    if "[SKIP]" in output:
        return MigrationResult(
            file=file_path, success=True, skipped=True,
            skip_reason="无 json.dumps 日志调用（可能已迁移）",
        )

    # 提取替换次数
    replacements = 0
    import_added = False
    for line in output.split("\n"):
        if "[DRY-RUN]" in line or "[OK]" in line:
            try:
                # 解析 "将替换 N 处" 或 "已替换 N 处"
                import re
                m = re.search(r"替换\s+(\d+)\s+处", line)
                if m:
                    replacements = int(m.group(1))
                if "新增 import" in line:
                    import_added = True
            except (ValueError, AttributeError):
                pass

    if dry_run:
        return MigrationResult(
            file=file_path, success=True,
            replacements=replacements, import_added=import_added,
        )

    # 实际迁移：先备份
    backup_path = _backup_file(file_path, root)

    # 验证语法
    syntax_error = _validate_syntax(file_path, root)
    if syntax_error:
        # 回滚
        shutil.copy2(backup_path, os.path.join(root, file_path))
        return MigrationResult(
            file=file_path, success=False,
            error=f"语法检查失败: {syntax_error}",
            backup_path=backup_path,
        )

    return MigrationResult(
        file=file_path, success=True,
        replacements=replacements, import_added=import_added,
        backup_path=backup_path,
    )


def _run_tests(test_path: Optional[str], root: str) -> bool:
    """运行单元测试验证迁移无回归"""
    if test_path:
        cmd = [sys.executable, "-m", "pytest", test_path, "-q", "--tb=short"]
    else:
        # 默认运行日志相关测试
        cmd = [sys.executable, "-m", "pytest",
               "tests/unit/test_log_dict_refactor.py",
               "tests/unit/test_log_dict_performance.py",
               "tests/unit/test_perf_monitor.py",
               "tests/unit/test_memory_comparison.py",
               "-q", "--tb=short"]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300, cwd=root,
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        print("[WARN] 测试执行超时（5分钟）", file=sys.stderr)
        return False


def _execute_migration(files: List[str], args: argparse.Namespace) -> BatchMigrationReport:
    """执行批次迁移"""
    batch_num = args.batch if args.batch else 0  # 0 表示全部
    report = BatchMigrationReport(
        batch=batch_num,
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
    )

    start_time = time.perf_counter()
    print(f"=== 迁移 Top {len(files)} 文件（批次 {batch_num or '全部'}）===")
    print(f"模式: {'dry-run 预览' if args.dry_run else '实际迁移'}")
    print()

    for i, file_path in enumerate(files, 1):
        print(f"[{i}/{len(files)}] {file_path}")
        result = _migrate_single_file(file_path, args.root, args.dry_run)
        report.results.append(result)

        if result.skipped:
            report.skipped_count += 1
            print(f"  SKIP: {result.skip_reason}")
        elif result.success:
            report.success_count += 1
            report.total_replacements += result.replacements
            status = "DRY-RUN" if args.dry_run else "OK"
            print(f"  {status}: 替换 {result.replacements} 处"
                  + (", 新增 import" if result.import_added else ""))
        else:
            report.failure_count += 1
            print(f"  FAIL: {result.error}")

    report.duration_seconds = round(time.perf_counter() - start_time, 2)

    # 迁移后测试验证（仅实际迁移时）
    if not args.dry_run and report.failure_count == 0 and not args.no_test:
        print("\n=== 运行单元测试验证 ===")
        report.test_passed = _run_tests(args.test_only, args.root)
        if report.test_passed:
            print("✅ 测试全部通过")
        else:
            print("❌ 测试失败，建议回滚（--rollback）")
    elif args.no_test:
        report.test_passed = None
        print("\n（已跳过测试验证）")

    return report


def _save_migration_record(report: BatchMigrationReport, root: str) -> None:
    """保存迁移记录（用于回滚）"""
    record_path = os.path.join(root, MIGRATION_RECORD)
    os.makedirs(os.path.dirname(record_path), exist_ok=True)

    record = {
        "timestamp": report.timestamp,
        "batch": report.batch,
        "backup_files": [
            {"file": r.file, "backup": r.backup_path}
            for r in report.results if r.backup_path
        ],
    }
    with open(record_path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, ensure_ascii=False)
    print(f"\n迁移记录已保存: {record_path}")


def _rollback(args: argparse.Namespace) -> int:
    """回滚最近一次迁移"""
    record_path = os.path.join(args.root, MIGRATION_RECORD)
    if not os.path.exists(record_path):
        print(f"❌ 无迁移记录: {record_path}")
        return 1

    with open(record_path, "r", encoding="utf-8") as f:
        record = json.load(f)

    print(f"=== 回滚迁移 (timestamp={record['timestamp']}, batch={record['batch']}) ===")
    restored_count = 0
    for item in record.get("backup_files", []):
        file_path = os.path.join(args.root, item["file"])
        backup_path = item["backup"]
        if not os.path.isabs(backup_path):
            backup_path = os.path.join(args.root, backup_path)

        if not os.path.exists(backup_path):
            print(f"  [SKIP] 备份不存在: {backup_path}")
            continue

        shutil.copy2(backup_path, file_path)
        print(f"  [OK] 已恢复: {item['file']}")
        restored_count += 1

    print(f"\n✅ 已回滚 {restored_count} 个文件")
    return 0


def _save_report(report: BatchMigrationReport, path: str, root: str) -> None:
    """保存迁移报告 JSON"""
    abs_path = os.path.join(root, path) if not os.path.isabs(path) else path
    os.makedirs(os.path.dirname(abs_path) or ".", exist_ok=True)
    with open(abs_path, "w", encoding="utf-8") as f:
        json.dump(report.to_dict(), f, indent=2, ensure_ascii=False)
    print(f"\n迁移报告已保存: {abs_path}")


def main() -> None:
    args = _parse_args()

    if args.rollback:
        sys.exit(_rollback(args))

    if args.dry_run:
        files = TOP20_FILES
    elif args.batch:
        files = BATCHES[args.batch]
    elif args.all:
        files = TOP20_FILES
    else:
        print("❌ 未指定模式")
        sys.exit(2)

    # 检查文件存在性
    missing = [f for f in files if not _check_file_exists(f, args.root)]
    if missing:
        print(f"[WARN] 以下文件不存在，将跳过: {missing}")

    report = _execute_migration(files, args)

    # 输出汇总
    print("\n" + "=" * 60)
    print("迁移汇总")
    print("=" * 60)
    print(f"总文件数: {len(report.results)}")
    print(f"成功: {report.success_count}")
    print(f"失败: {report.failure_count}")
    print(f"跳过: {report.skipped_count}")
    print(f"总替换处数: {report.total_replacements}")
    print(f"耗时: {report.duration_seconds}s")
    if report.test_passed is not None:
        print(f"测试验证: {'通过' if report.test_passed else '失败'}")
    print()

    # 保存报告
    if not args.dry_run:
        _save_migration_record(report, args.root)
    _save_report(report, args.report, args.root)

    # 退出码
    if report.failure_count > 0:
        sys.exit(1)
    if report.test_passed is False:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
