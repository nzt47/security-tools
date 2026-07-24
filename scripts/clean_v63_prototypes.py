"""v6.3 prototype 样本清洗脚本 — programming 类别移除英文冲突样本 + 新增中文编程问题

用途:
    执行 v6.3 优化计划 §3.1 的 programming 类别清洗：
    1. 移除 3 个英文冲突样本（与 context_aware/voice_interaction 语义重叠）
    2. 新增 5 个纯中文具体编程问题
    3. 更新版本号与描述
    4. 备份原文件到 .bak
    5. 输出变更摘要

设计原则:
    【不易】不修改 detector/loader 代码，仅改 prototype JSON 数据
    【不易】保留 version/embedding_model/threshold_default 等元字段
    【不易】其他 9 类别样本不动（守最小变更）
    【变易】参数化移除/新增列表，便于未来 v6.4 复用
    【简易】单文件可运行，无第三方依赖，幂等执行

用法:
    # 默认执行（按 v6.3 计划清洗）
    python scripts/clean_v63_prototypes.py

    # dry-run 模式（仅预览变更，不写文件）
    python scripts/clean_v63_prototypes.py --dry-run

    # 指定 prototype 文件路径
    python scripts/clean_v63_prototypes.py --prototypes-path custom/path.json

    # 跳过备份（不推荐，破坏【不易】可回滚性）
    python scripts/clean_v63_prototypes.py --no-backup

退出码:
    0: 清洗成功
    1: 文件不存在或格式错误
    2: programming 类别不存在
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

# ════════════════════════════════════════════════════════════
#  默认配置（v6.3 计划固化值，守【不易】）
# ════════════════════════════════════════════════════════════

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_PROTOTYPES = _PROJECT_ROOT / "tests" / "eval" / "negative_intent_prototypes.json"

# v6.3 计划 §3.1.1: 移除的 3 个英文样本
# 根因: 英文样本与中文技能 description 跨语言语义漂移，
#       且 function/implement 与 context_aware/voice_interaction 语义重叠
_SAMPLES_TO_REMOVE = [
    "def print_hello_world function",
    "java python c++ programming",
    "how to implement quick sort",
]

# v6.3 计划 §3.1.3: 新增的 5 个纯中文具体编程问题
# 选择标准: 避免通用动词（实现/调整），使用具体技术领域词汇
_SAMPLES_TO_ADD = [
    "python 列表怎么去重",
    "这段代码报错了 syntax error",
    "git 怎么回退上一个 commit",
    "sql 查询语句怎么写",
    "正则表达式匹配邮箱",
]

# v6.3 版本信息
_NEW_VERSION = "v6.3-prototypes"
_NEW_DESCRIPTION = (
    "编程技术问题类 — 非技能意图"
    "（v6.3 清洗：移除英文样本，改为纯中文具体编程问题，"
    "消除与 context_aware/voice_interaction 的语义冲突）"
)
_NEW_THRESHOLD_DEFAULT = 0.65  # v6.3 目标阈值（清洗后预期支持更低阈值）


# ════════════════════════════════════════════════════════════
#  核心逻辑
# ════════════════════════════════════════════════════════════

def load_prototypes(path: Path) -> Dict[str, Any]:
    """加载 prototype JSON

    【不易】文件不存在或格式错误时抛异常，不静默降级
    """
    if not path.exists():
        raise FileNotFoundError(f"prototype 文件不存在: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if "categories" not in data:
        raise ValueError(f"prototype 文件格式错误: 缺少 categories 字段")
    return data


def find_category(
    data: Dict[str, Any], category_name: str
) -> Tuple[int, Dict[str, Any]]:
    """查找指定类别，返回 (索引, 类别对象)

    【不易】类别不存在时抛异常
    """
    for i, cat in enumerate(data["categories"]):
        if cat.get("category") == category_name:
            return i, cat
    raise ValueError(f"类别 '{category_name}' 不存在于 prototype 文件中")


def clean_programming_samples(
    cat: Dict[str, Any],
    *,
    remove_list: List[str],
    add_list: List[str],
) -> Dict[str, Any]:
    """清洗 programming 类别样本

    策略:
        1. 移除 remove_list 中的样本（幂等：已移除的样本不会报错）
        2. 新增 add_list 中的样本（幂等：已存在的样本不重复添加）
        3. 更新类别 description

    Returns:
        变更摘要 {removed, added, skipped_removed, skipped_added, before_count, after_count}
    """
    original_samples: List[str] = cat.get("samples", [])
    before_count = len(original_samples)

    # Step 1: 移除样本（幂等）
    removed: List[str] = []
    skipped_removed: List[str] = []
    remaining: List[str] = []
    for s in original_samples:
        if s in remove_list:
            removed.append(s)
        else:
            remaining.append(s)
    # 记录 remove_list 中未找到的样本（幂等：不报错）
    skipped_removed = [s for s in remove_list if s not in original_samples]

    # Step 2: 新增样本（幂等：避免重复）
    added: List[str] = []
    skipped_added: List[str] = []
    for s in add_list:
        if s in remaining:
            skipped_added.append(s)
        else:
            remaining.append(s)
            added.append(s)

    after_count = len(remaining)

    # 更新类别对象
    cat["samples"] = remaining
    cat["description"] = _NEW_DESCRIPTION

    return {
        "removed": removed,
        "added": added,
        "skipped_removed": skipped_removed,
        "skipped_added": skipped_added,
        "before_count": before_count,
        "after_count": after_count,
    }


def update_metadata(data: Dict[str, Any]) -> Dict[str, Any]:
    """更新 prototype 文件元数据（版本号 + threshold_default）

    【不易】保留 embedding_model 字段不变
    """
    old_version = data.get("version", "unknown")
    old_threshold = data.get("threshold_default", "unknown")

    data["version"] = _NEW_VERSION
    data["threshold_default"] = _NEW_THRESHOLD_DEFAULT

    return {
        "old_version": old_version,
        "new_version": _NEW_VERSION,
        "old_threshold": old_threshold,
        "new_threshold": _NEW_THRESHOLD_DEFAULT,
    }


def backup_file(path: Path) -> Path:
    """备份原文件到 .bak（带时间戳避免覆盖）

    【不易】备份是可回滚性的保障
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = path.with_suffix(f".{timestamp}.bak")
    shutil.copy2(path, backup_path)
    return backup_path


def save_prototypes(path: Path, data: Dict[str, Any]) -> None:
    """保存 prototype JSON（ensure_ascii=False 保留中文可读性）"""
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ════════════════════════════════════════════════════════════
#  变更摘要打印
# ════════════════════════════════════════════════════════════

def print_summary(
    cat_summary: Dict[str, Any],
    meta_summary: Dict[str, Any],
    backup_path: Path | None,
    *,
    dry_run: bool,
) -> None:
    """打印变更摘要"""
    mode = "dry-run（预览，未写文件）" if dry_run else "已执行"
    print(f"\n{'='*70}")
    print(f"  v6.3 prototype 清洗摘要 ({mode})")
    print(f"{'='*70}")

    print(f"\n[元数据变更]:")
    print(f"  version:            {meta_summary['old_version']} → {meta_summary['new_version']}")
    print(f"  threshold_default:  {meta_summary['old_threshold']} → {meta_summary['new_threshold']}")

    print(f"\n[programming 类别变更]:")
    print(f"  样本数: {cat_summary['before_count']} → {cat_summary['after_count']}")

    if cat_summary["removed"]:
        print(f"\n  移除样本 ({len(cat_summary['removed'])} 个):")
        for s in cat_summary["removed"]:
            print(f"    - {s}")
    else:
        print(f"\n  移除样本: 0 个（已清洗，幂等执行）")

    if cat_summary["skipped_removed"]:
        print(f"\n  ⚠️  remove_list 中未找到的样本（幂等跳过）:")
        for s in cat_summary["skipped_removed"]:
            print(f"    - {s}")

    if cat_summary["added"]:
        print(f"\n  新增样本 ({len(cat_summary['added'])} 个):")
        for s in cat_summary["added"]:
            print(f"    + {s}")
    else:
        print(f"\n  新增样本: 0 个（已添加，幂等执行）")

    if cat_summary["skipped_added"]:
        print(f"\n  ⚠️  add_list 中已存在的样本（幂等跳过）:")
        for s in cat_summary["skipped_added"]:
            print(f"    - {s}")

    if backup_path and not dry_run:
        print(f"\n[备份]:")
        print(f"  原文件已备份至: {backup_path}")

    print(f"\n{'='*70}")
    if dry_run:
        print(f"  ✅ dry-run 完成: 实际执行请去掉 --dry-run 参数")
    else:
        print(f"  ✅ 清洗完成: 建议运行校准确认新阈值")
        print(f"     python scripts/calibrate_v62_threshold.py "
              f"--output tests/eval/v63_threshold_calibration.json")
    print(f"{'='*70}")


# ════════════════════════════════════════════════════════════
#  主流程
# ════════════════════════════════════════════════════════════

def run_clean(
    prototypes_path: Path,
    *,
    dry_run: bool,
    no_backup: bool,
) -> int:
    """执行清洗流程"""
    print(f"=" * 70)
    print(f"  v6.3 prototype 样本清洗脚本")
    print(f"  目标文件: {prototypes_path}")
    print(f"  模式: {'dry-run' if dry_run else 'execute'}")
    print(f"=" * 70)

    # Step 1: 加载原文件
    try:
        data = load_prototypes(prototypes_path)
    except (FileNotFoundError, ValueError) as e:
        print(f"❌ 加载失败: {e}", file=sys.stderr)
        return 1

    # Step 2: 查找 programming 类别
    try:
        _, programming_cat = find_category(data, "programming")
    except ValueError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 2

    print(f"\n[清洗前] programming 类别:")
    print(f"  样本数: {len(programming_cat.get('samples', []))}")
    print(f"  description: {programming_cat.get('description', '')[:80]}...")

    # Step 3: 执行清洗（内存中）
    cat_summary = clean_programming_samples(
        programming_cat,
        remove_list=_SAMPLES_TO_REMOVE,
        add_list=_SAMPLES_TO_ADD,
    )
    meta_summary = update_metadata(data)

    print(f"\n[清洗后] programming 类别:")
    print(f"  样本数: {cat_summary['after_count']}")
    print(f"  description: {programming_cat['description'][:80]}...")

    # Step 4: 备份 + 保存（非 dry-run 模式）
    backup_path = None
    if not dry_run:
        if not no_backup:
            backup_path = backup_file(prototypes_path)
        save_prototypes(prototypes_path, data)

    # Step 5: 打印摘要
    print_summary(cat_summary, meta_summary, backup_path, dry_run=dry_run)

    return 0


# ════════════════════════════════════════════════════════════
#  CLI 入口
# ════════════════════════════════════════════════════════════

def main() -> int:
    parser = argparse.ArgumentParser(
        description="v6.3 prototype 样本清洗 — programming 类别移除英文冲突样本 + 新增中文编程问题"
    )
    parser.add_argument(
        "--prototypes-path", type=str, default=str(_DEFAULT_PROTOTYPES),
        help=f"prototype JSON 文件路径（默认: {_DEFAULT_PROTOTYPES}）"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="仅预览变更，不写文件（推荐首次执行时使用）"
    )
    parser.add_argument(
        "--no-backup", action="store_true",
        help="跳过备份（不推荐，破坏可回滚性）"
    )
    args = parser.parse_args()

    return run_clean(
        Path(args.prototypes_path),
        dry_run=args.dry_run,
        no_backup=args.no_backup,
    )


if __name__ == "__main__":
    sys.exit(main())
