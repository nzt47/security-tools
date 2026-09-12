#!/usr/bin/env python
"""数据资产机械扫描（TASK-S8-01 步骤 1）。

【本脚本做什么】
    按 `agent/retention/policy.py` 的**策略表**逐类扫描真实文件系统，输出每类的
    路径 / 文件数 / 体积 / 记录数 / 时间范围 / 保留期 / 可否删除 / 执行者 / 依据，
    并额外复用 S2-02 既有的 `inventory_legacy_files()` 盘点存量审计轨
    （**不重造第二套盘点**）。

【本脚本不做什么】
    只读。不建目录、不落盘、不改任何文件；`--json` / `--md` 写的是**报告文件**
    （写在调用方指定的路径上），与数据资产无关。

【用法】
    python scripts/scan_data_assets.py                    # 人读摘要
    python scripts/scan_data_assets.py --json out.json     # 机器可读报告
    python scripts/scan_data_assets.py --md 策略附录.md     # Markdown 表格
    python scripts/scan_data_assets.py --check             # 红线自检（非零退出=有问题）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional, Sequence

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from agent.retention.policy import (  # noqa: E402
    DEFAULT_CLASSES,
    POLICY_SCHEMA,
    POLICY_SCHEMA_VERSION,
    load_policy,
)
from agent.retention.scan import (  # noqa: E402
    class_footprint,
    footprint_totals,
    sqlite_tables,
)

#: 扫描用的固定时间戳口径说明（体积/条数为**扫描时刻**的真实值，不是估算）
SCAN_NOTE = ("体积/条数为扫描时刻真实文件系统值；不存在的类如实标注 missing，"
             "不按 0 计入体积（避免'没有数据'与'数据为 0 字节'混同）")


def _human(n: int) -> str:
    """字节数人读化（KB/MB/GB，一位小数；不改变原始数字）。"""
    value = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f}{unit}" if unit != "B" else f"{int(value)}B"
        value /= 1024
    return f"{value:.1f}TB"  # pragma: no cover - 不可达


def _legacy_inventory(root: str) -> Dict[str, Any]:
    """复用 S2-02 的存量盘点（`agent/audit/migration.py::inventory_legacy_files`）。

    该函数是既有事实源（纯审计轨置只读 / 系统记录只镜像），本脚本**只读它的结论**，
    不重新实现"哪些文件是存量审计轨"的判定。
    """
    try:
        from agent.audit.migration import inventory_legacy_files

        infos = inventory_legacy_files(root)
    except Exception as e:  # noqa: BLE001 盘点失败如实标注，不影响主表
        return {"available": False, "reason": f"{type(e).__name__}: {e}", "files": []}
    files = [i.to_dict() for i in infos]
    return {
        "available": True,
        "total": len(files),
        "existing": sum(1 for f in files if f.get("exists")),
        "readonly": sum(1 for f in files if f.get("read_only")),
        "bytes": sum(int(f.get("size_bytes") or 0) for f in files),
        "files": files,
        "note": ("来自 agent/audit/migration.py::inventory_legacy_files（既有事实源）"),
    }


def _sqlite_detail(footprint: Dict[str, Any]) -> List[Dict[str, Any]]:
    """SQLite 类的表级明细（便于确认 `sqlite_table` 口径是否正确）。"""
    out: List[Dict[str, Any]] = []
    for row in footprint.get("files") or []:
        out.append({"path": row["path"], "tables": sqlite_tables(row["path"]),
                    "bytes": row["bytes"]})
    return out


def build_report(*, root: str = "", archive_dir: str = "") -> Dict[str, Any]:
    """构造完整扫描报告（纯只读）。"""
    policy = load_policy()
    if archive_dir:
        policy.archive_dir = os.path.abspath(archive_dir)
    root = os.path.abspath(root or REPO_ROOT)

    footprints = [class_footprint(cls, root) for cls in policy.classes]
    for fp in footprints:
        if fp["kind"] == "sqlite_db":
            fp["sqlite_detail"] = _sqlite_detail(fp)
        fp["human_bytes"] = _human(int(fp.get("bytes") or 0))

    return {
        "schema": POLICY_SCHEMA,
        "schema_version": POLICY_SCHEMA_VERSION,
        "root": root,
        "archive_dir": policy.archive_dir,
        "note": SCAN_NOTE,
        "policy": policy.summary(),
        "totals": footprint_totals(footprints),
        "classes": footprints,
        "legacy_inventory": _legacy_inventory(root),
    }


def render_text(report: Dict[str, Any]) -> str:
    """人读摘要（数字全部来自真实扫描）。"""
    lines: List[str] = []
    lines.append("═" * 96)
    lines.append(f"数据资产普查（{report['schema']} v{report['schema_version']}）"
                 f"  根={report['root']}")
    lines.append("═" * 96)
    header = (f"{'数据类':<22}{'文件':>5}{'记录':>9}{'体积':>10}  "
              f"{'保留期':<10}{'归档':<11}{'可否删除':<12}{'执行者'}")
    lines.append(header)
    lines.append("-" * 96)
    for fp in report["classes"]:
        deletable = ("禁止删除(红线)" if fp["redline"]
                     else ("可删" if fp["can_purge"] else "禁止删除"))
        lines.append(
            f"{fp['class_id']:<22}{fp['file_count']:>5}{fp['records']:>9}"
            f"{fp['human_bytes']:>10}  {fp['retention_label']:<10}"
            f"{fp['archive_mode']:<11}{deletable:<12}{fp['owner']}")
    lines.append("-" * 96)
    t = report["totals"]
    lines.append(f"合计：{t['classes']} 类（有数据 {t['classes_present']} / "
                 f"未落盘 {t['classes_missing']}）、{t['files']} 文件、"
                 f"{t['records']} 记录、{_human(int(t['bytes']))}")
    lines.append(f"红线类（禁止删除）：{'、'.join(t['redline_classes'])}")
    lines.append(f"标为可删的类：{'、'.join(t['deletable_classes']) or '（无）'}")
    legacy = report["legacy_inventory"]
    if legacy.get("available"):
        lines.append(f"存量审计轨盘点（复用 S2-02 inventory_legacy_files）："
                     f"{legacy['existing']}/{legacy['total']} 个目标存在，"
                     f"已置只读 {legacy['readonly']} 个，"
                     f"合计 {_human(int(legacy['bytes']))}")
    else:
        lines.append(f"存量审计轨盘点不可用：{legacy.get('reason')}")
    lines.append(f"说明：{report['note']}")
    return "\n".join(lines)


def render_markdown(report: Dict[str, Any]) -> str:
    """Markdown 策略表（供 `docs/zh/数据生命周期策略.md` 附录粘贴）。"""
    lines = ["| 数据类 | 路径（globs） | 文件数 | 记录数 | 体积 | 保留期 | 归档方式 | 可否删除 | 执行者 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for fp in report["classes"]:
        deletable = ("**禁止删除**（红线）" if fp["redline"]
                     else ("可删" if fp["can_purge"] else "禁止删除"))
        globs = "；".join(fp.get("globs") or []) or "—"
        lines.append(
            f"| `{fp['class_id']}`<br>{fp['title']} | `{globs}` | {fp['file_count']} | "
            f"{fp['records']} | {fp['human_bytes']} | {fp['retention_label']} | "
            f"{fp['archive_mode']} | {deletable} | {fp['owner']} |")
    return "\n".join(lines)


def check(report: Dict[str, Any]) -> List[str]:
    """机械自检（非空 = 有问题，退出码 1）。

    检查项：
        1. 每条策略都必须有执行者与依据（不许留空 => 不许"没人的策略"）；
        2. 红线类必须 `deletable=False` 且 `delete_mode=none`；
        3. 记忆类必须走 `s5_01_forgetting`（不得标 `guarded`）；
        4. 非空类必须给出真实记录数/体积的来源（`files` 明细非空）。
    """
    problems: List[str] = []
    policy = report["policy"]
    by_id = {c["class_id"]: c for c in report["classes"]}
    for fp in report["classes"]:
        cid = fp["class_id"]
        if not fp["owner"] or not fp["basis"]:
            problems.append(f"{cid}：缺执行者或依据")
        if fp["redline"]:
            if fp["deletable"] or fp["delete_mode"] != "none":
                problems.append(
                    f"{cid}：红线类必须 deletable=False 且 delete_mode=none，"
                    f"实际 deletable={fp['deletable']} delete_mode={fp['delete_mode']}")
        if cid in (policy.get("deletable_via_forgetting_classes") or []):
            if fp["delete_mode"] != "s5_01_forgetting":
                problems.append(f"{cid}：记忆类必须走 S5-01 删记忆不删证据路径")
        if fp["file_count"] and not fp["files"]:
            problems.append(f"{cid}：声明有 {fp['file_count']} 个文件但无明细（数字无来源）")
        if fp["warm_allowed"] and not fp["reader_shard_aware"]:
            problems.append(f"{cid}：温层开启但读端非分片感知（会改变统计口径）")
    known = {c.class_id for c in DEFAULT_CLASSES}
    if set(by_id) != known:
        problems.append(f"扫描结果与内置策略表类集合不一致：{set(by_id) ^ known}")
    return problems


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="数据资产机械扫描（TASK-S8-01 步骤 1）")
    parser.add_argument("--root", default="", help="扫描根（默认仓库根）")
    parser.add_argument("--archive-dir", default="", help="归档目录（仅登记到报告）")
    parser.add_argument("--json", default="", help="把完整报告写入该 JSON 文件")
    parser.add_argument("--md", default="", help="把策略表（Markdown）写入该文件")
    parser.add_argument("--check", action="store_true",
                        help="策略表自检（有问题 → 退出码 1）")
    parser.add_argument("--quiet", action="store_true", help="只输出结论行")
    args = parser.parse_args(argv)

    report = build_report(root=args.root, archive_dir=args.archive_dir)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
        print(f"[scan_data_assets] JSON 报告已写入 {args.json}")
    if args.md:
        with open(args.md, "w", encoding="utf-8") as fh:
            fh.write(render_markdown(report) + "\n")
        print(f"[scan_data_assets] Markdown 策略表已写入 {args.md}")

    if not args.quiet:
        print(render_text(report))

    problems = check(report)
    if args.check:
        if problems:
            print("\n[scan_data_assets] 自检不通过：")
            for item in problems:
                print(f"  ✗ {item}")
            return 1
        print("\n[scan_data_assets] 自检通过：策略表完整、红线未越界、温层口径自洽")
    elif problems:
        print("\n[scan_data_assets] 提示（未开启 --check，仅提示）：")
        for item in problems:
            print(f"  ! {item}")
    return 0


if __name__ == "__main__":                      # pragma: no cover
    sys.exit(main())
