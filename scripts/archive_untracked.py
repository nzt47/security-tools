"""untracked 文件清理/归档脚本（安全版，默认 dry-run）

用途：83 个 untracked 文件（并行会话产物 + 运行时数据 + 文档/脚本）分类归档，
不删除任何文件，支持一键还原。

用法：
    python scripts/archive_untracked.py --dry-run          # 默认：仅打印分类清单
    python scripts/archive_untracked.py --archive          # 归档到 backup/untracked_archive/<日期>/
    python scripts/archive_untracked.py --restore <日期>   # 从归档目录还原

安全约束（不易）：
    - 归档 = 移动（Move），绝不删除
    - 归档前校验 git ls-files：HEAD 已跟踪的文件永不触碰
    - 记录 manifest.csv（原路径 → 归档路径），可完整还原
"""
import argparse
import csv
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ARCHIVE_ROOT = REPO / "backup" / "untracked_archive"

# 分类规则（顺序匹配，先匹配先归类）
# 每个分类: (目录关键字列表, 分类名, 备注)
RULES = [
    (["agent/skills_mgmt/approval.py", "agent/skills_mgmt/precipitate.py",
      "agent/skills_mgmt/review_gate.py", "agent/skills_mgmt/rollback.py",
      "agent/skills_mgmt/value_guard.py", "agent/knowledge/skill_bridge.py"],
     "parallel_evo_guard", "并行会话 EVO 安全护栏模块（被未跟踪测试引用，待并行会话收口）"),
    (["tests/unit/"], "parallel_tests", "并行会话测试产物（test_evolution_loop 等，未入库）"),
    (["data/health/", "data/reflection/", "data/sandbox/", "data/snapshots/",
      "data/lifetrace/", "data/knowledge/reports/", "data/evolution_",
      "data/eval_planning", "data/sim_", "data/skills_repo/"],
     "runtime_data", "运行时数据（归档而非提交，防误入库）"),
    (["docs/"], "docs_zh", "文档/报告（含并行会话未提交的交付物）"),
    (["scripts/", "deploy/", "monitoring/", "utils/", "deploy_", "evolve_cli.py",
      "fill_skill_params.py", "generate_evolution_week_report.py",
      "run_schedule_sim.py", "sandbox_evolution_run.py",
      "simulate_evolution_week.py"],
     "scripts_deploy", "脚本/部署配置"),
    (["backup/logs/", "probe_out.txt", "pytest_chunks/"], "logs", "日志/过程产物"),
]
DEFAULT_CAT = "misc"


def get_untracked() -> list[str]:
    """git status --porcelain -z 获取 untracked 文件（NUL 分隔，防非 ASCII 引号转义）

    -z 输出格式：每条记录以 NUL 结尾，文件名原始无引号转义（中文文件名关键）。
    跳过目录条目（git 对目录只列一次，--untracked-files=all 后为文件级）。
    """
    out = subprocess.run(
        ["git", "status", "--porcelain", "-z", "--untracked-files=all"],
        cwd=REPO, capture_output=True, text=True, encoding="utf-8",
        errors="replace").stdout
    items = [rec for rec in out.split("\x00") if rec]
    # 每条记录形如 "?? <路径>"；路径含空格/中文时亦保持原样
    return [rec[3:] for rec in items if rec.startswith("??")]


def tracked_in_head(rel: str) -> bool:
    """HEAD 是否已跟踪该文件（真 → 绝不归档）"""
    r = subprocess.run(["git", "ls-files", "--", rel], cwd=REPO,
                       capture_output=True, text=True)
    return bool(r.stdout.strip())


def imported_by_tracked(rel: str) -> bool:
    """untracked 的 .py 模块是否被已跟踪文件 import（git grep 只搜已跟踪文件）

    【不易】归档后已跟踪生产代码不得 import 失败：
    - 对 agent/**/*.py / scripts/**/*.py 等模块，若已跟踪文件含其模块路径引用，
      则跳过归档（保护）。
    - 例：agent/knowledge/skill_bridge.py 被 __main__.py + check_cli_parser.py 引用。
    """
    if not (rel.endswith(".py") and rel.startswith(("agent/", "scripts/"))):
        return False
    module_path = rel[:-3].replace("/", ".")          # agent.knowledge.skill_bridge
    for needle in (module_path, rel[:-3].replace("/", "\\")):
        r = subprocess.run(["git", "grep", "-l", needle], cwd=REPO,
                           capture_output=True, text=True)
        if r.stdout.strip():
            return True
    return False


def classify(rel: str) -> tuple[str, str]:
    """返回 (分类, 备注)"""
    for keys, cat, note in RULES:
        if any(rel.startswith(k) for k in keys):
            return cat, note
    return DEFAULT_CAT, "其他"


def main() -> int:
    ap = argparse.ArgumentParser(description="untracked 文件分类归档（安全版）")
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true",
                       help="仅打印分类清单（默认）")
    group.add_argument("--archive", action="store_true",
                       help="归档到 backup/untracked_archive/<日期>/")
    group.add_argument("--restore", metavar="日期",
                       help="从 backup/untracked_archive/<日期>/ 还原全部文件")
    args = ap.parse_args()

    if args.restore:
        return restore(args.restore)

    untracked = [f for f in get_untracked() if not tracked_in_head(f)]
    if not untracked:
        print("[OK] 无 untracked 文件")
        return 0

    # 【不易】保护：被已跟踪文件 import 的模块不归档（防生产代码 import 失败）
    protected = [f for f in untracked if imported_by_tracked(f)]
    if protected:
        print("[PROTECT] 以下 untracked 模块被已跟踪文件引用，跳过归档：")
        for f in protected:
            print(f"     {f}")
        untracked = [f for f in untracked if f not in protected]

    cats: dict[str, list[str]] = {}
    for rel in untracked:
        cat, _ = classify(rel)
        cats.setdefault(cat, []).append(rel)

    print(f"[INFO] 共 {len(untracked)} 个 untracked 文件，分类如下：\n")
    for cat, files in sorted(cats.items()):
        note = next((n for k, c, n in RULES if c == cat), "其他")
        print(f"── {cat}（{len(files)} 个）{note}")
        for f in sorted(files):
            print(f"     {f}")

    if not args.archive:
        print("\n[提示] dry-run 结束。执行 --archive 归档；--restore <日期> 还原。")
        return 0

    # 归档
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest_root = ARCHIVE_ROOT / stamp
    dest_root.mkdir(parents=True, exist_ok=True)
    manifest = []
    moved, skipped = 0, 0
    for rel in untracked:
        src = REPO / rel
        if not src.exists():
            skipped += 1
            continue
        dest = dest_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        manifest.append([rel, str(dest)])
        moved += 1

    mf = dest_root / "manifest.csv"
    with open(mf, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(manifest)
    print(f"\n[OK] 归档完成：{moved} 移动，{skipped} 跳过，manifest: {mf}")
    return 0


def restore(stamp: str) -> int:
    src_root = ARCHIVE_ROOT / stamp
    if not src_root.exists():
        print(f"[ERROR] 归档目录不存在: {src_root}")
        return 1
    mf = src_root / "manifest.csv"
    if not mf.exists():
        print("[ERROR] 缺少 manifest.csv，无法安全还原")
        return 1
    restored, skipped = 0, 0
    with open(mf, newline="", encoding="utf-8") as fh:
        for orig, archived in csv.reader(fh):
            src = Path(archived)
            if not src.exists():
                skipped += 1
                continue
            dest = REPO / orig
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))
            restored += 1
    print(f"[OK] 还原完成：{restored} 还原，{skipped} 跳过（源不存在）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
