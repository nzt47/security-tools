"""历史审计日志租户归属迁移脚本（T8 数据隔离补全）

背景：/api/audit/logs 跨租户隔离修复（filter_by_key）后，修复前写入的
历史记录无 tenant_id 字段 → 绑定租户 Key 不可见（保守隔离）。
本脚本为历史记录补全 tenant_id 归属。

归属策略（默认）：
- 历史记录（tenant_id 缺失或为空）标记为 --tenant 指定的租户（默认 "system"，
  系统级租户，绑定租户 Key 不可见，内部通道可读）
- 已含非空 tenant_id 的记录保持不变（幂等，可重复执行）

用法：
  python scripts/migrate_audit_logs_tenant.py                     # dry-run 预览
  python scripts/migrate_audit_logs_tenant.py --tenant legacy     # dry-run 指定租户
  python scripts/migrate_audit_logs_tenant.py --apply             # 应用（自动备份）
  python scripts/migrate_audit_logs_tenant.py --apply --yes       # 非交互应用（CI/容器）
  python scripts/migrate_audit_logs_tenant.py --audit-dir <dir>   # 自定义目录（测试）
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

# 项目根（data/audit 相对项目根）
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_AUDIT_DIR = ROOT / "data" / "audit"


def collect_files(audit_dir: Path) -> list[Path]:
    """收集全部审计日志文件（audit_*.jsonl，按名排序）"""
    return sorted(audit_dir.glob("audit_*.jsonl"))


def analyze(audit_dir: Path) -> tuple[int, int]:
    """统计 (受影响行数, 文件数) —— 仅统计 tenant_id 缺失/为空的记录"""
    total_lines = 0
    affected_files = 0
    for f in collect_files(audit_dir):
        changed = False
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            total_lines += 1
            if not rec.get("tenant_id"):
                changed = True
        if changed:
            affected_files += 1
    return total_lines, affected_files


def migrate(audit_dir: Path, tenant: str, dry_run: bool) -> dict:
    """补全 tenant_id；dry_run=True 只统计不写盘"""
    result = {"files": 0, "updated_lines": 0, "total_lines": 0}
    for f in collect_files(audit_dir):
        lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        new_lines: list[str] = []
        updated = 0
        for line in lines:
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                new_lines.append(line)  # 损坏行原样保留
                continue
            result["total_lines"] += 1
            if not rec.get("tenant_id"):
                rec["tenant_id"] = tenant
                updated += 1
            new_lines.append(json.dumps(rec, ensure_ascii=False))
        if updated == 0:
            continue
        result["files"] += 1
        result["updated_lines"] += updated
        if not dry_run:
            # 备份一次（文件级 .bak，原子写回 .tmp + os.replace）
            backup = Path(str(f) + f".bak_{datetime.now().strftime('%Y%m%d%H%M%S')}")
            if not backup.exists():
                shutil.copy2(f, backup)
            tmp = Path(str(f) + ".tmp")
            tmp.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
            tmp.replace(f)
    return result


def main():
    ap = argparse.ArgumentParser(description="历史审计日志租户归属迁移")
    ap.add_argument("--audit-dir", type=Path, default=DEFAULT_AUDIT_DIR,
                    help="审计日志目录（默认 data/audit）")
    ap.add_argument("--tenant", default="system",
                    help="历史记录归属租户 id（默认 system，系统级）")
    ap.add_argument("--apply", action="store_true",
                    help="应用迁移（默认 dry-run，仅预览）")
    ap.add_argument("--yes", action="store_true",
                    help="跳过交互确认（CI / 容器 / 脚本化调用）")
    args = ap.parse_args()

    if not args.audit_dir.exists():
        print(f"[FAIL] 目录不存在: {args.audit_dir}")
        sys.exit(1)

    files = collect_files(args.audit_dir)
    if not files:
        print(f"[OK] 无审计日志文件（{args.audit_dir}）")
        return

    total, affected_files = analyze(args.audit_dir)
    print(f"审计目录: {args.audit_dir}")
    print(f"文件数: {len(files)} | 总记录数: {total} | 含待补全记录的文件数: {affected_files}")

    if not args.apply:
        res = migrate(args.audit_dir, args.tenant, dry_run=True)
        print(f"\n[dry-run] 将补全 {res['updated_lines']} 条记录（{res['files']} 个文件）→ tenant_id={args.tenant}")
        print("未写盘。确认后加 --apply 执行。")
        return

    # 二次确认（安全操作）；--yes 跳过（CI/容器）
    if not args.yes:
        confirm = input(f"应用迁移：补全 {affected_files} 个文件中的历史记录为 tenant_id={args.tenant}？[y/N] ")
        if confirm.strip().lower() not in ("y", "yes"):
            print("已取消")
            return

    res = migrate(args.audit_dir, args.tenant, dry_run=False)
    print(f"\n[apply] 已补全 {res['updated_lines']} 条记录（{res['files']} 个文件）→ tenant_id={args.tenant}")
    print(f"[apply] 备份: 每个修改文件生成 .bak_<时间戳>（data/audit/audit_*.jsonl.bak_*）")
    print(f"[apply] 幂等: 已含 tenant_id 的记录保持不变，可重复执行")


if __name__ == "__main__":
    main()
