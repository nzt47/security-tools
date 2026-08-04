"""alerts_production.yml 指标命名批量修复脚本

【不易】代码是真相源（project_memory 教训）：
    alerts_production.yml 中所有 yunshu_ 前缀指标与代码实际定义不一致。
    本脚本基于"代码真相源映射表"将告警规则中的指标名对齐代码定义。

【变易】三类断裂处理：
    1. 去前缀断裂：yunshu_http_requests_total → http_requests_total（代码无前缀）
    2. 前缀替换断裂：yunshu_cpu_usage_percent → system_cpu_usage_percent（代码用 system_ 前缀）
    3. 未定义指标：yunshu_active_connections → 标注 TODO（代码中未定义，需先补定义）

【简易】单文件脚本，支持 --dry-run 预览 + --backup 自动备份 + 修改报告

运行:
    # 预览模式（不修改文件）
    python scripts/fix_alerts_metric_naming.py --dry-run

    # 执行修复（自动备份原文件）
    python scripts/fix_alerts_metric_naming.py --backup

    # 指定文件路径
    python scripts/fix_alerts_metric_naming.py --file path/to/alerts.yml --backup
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path
from datetime import datetime


# ═══════════════════════════════════════════════════════════════════
#  代码真相源映射表
#  来源：utils/prometheus_monitor.py + agent/monitoring/prometheus.py 实际定义
# ═══════════════════════════════════════════════════════════════════

METRIC_MAPPING = {
    # ── 去前缀断裂：代码中无 yunshu_ 前缀（utils/prometheus_monitor.py）──
    "yunshu_http_requests_total": {
        "target": "http_requests_total",
        "source": "utils/prometheus_monitor.py:20 (Counter, 无前缀)",
        "reason": "代码定义为 http_requests_total，告警规则多加了 yunshu_ 前缀",
    },
    "yunshu_http_request_duration_seconds_bucket": {
        "target": "http_request_duration_seconds_bucket",
        "source": "utils/prometheus_monitor.py:28 (Histogram, 无前缀)",
        "reason": "代码定义为 http_request_duration_seconds，告警规则多加了 yunshu_ 前缀",
    },
    "yunshu_security_blocks_total": {
        "target": "security_blocks_total",
        "source": "utils/prometheus_monitor.py:71 (Counter, 无前缀)",
        "reason": "代码定义为 security_blocks_total，告警规则多加了 yunshu_ 前缀",
    },
    # ── 前缀替换断裂：代码中用 system_ 前缀（utils/prometheus_monitor.py）──
    "yunshu_cpu_usage_percent": {
        "target": "system_cpu_usage_percent",
        "source": "utils/prometheus_monitor.py:96 (Gauge, system_ 前缀)",
        "reason": "代码定义为 system_cpu_usage_percent，告警规则误用 yunshu_ 前缀",
    },
    "yunshu_memory_usage_percent": {
        "target": "system_memory_usage_percent",
        "source": "utils/prometheus_monitor.py:96 (Gauge, system_ 前缀)",
        "reason": "代码定义为 system_memory_usage_percent，告警规则误用 yunshu_ 前缀",
    },
}

# 未定义指标（代码中找不到定义，需先在代码中补定义再修复告警规则）
UNDEFINED_METRICS = {
    "yunshu_active_connections": {
        "source": "未在代码中找到定义",
        "reason": "agent/monitoring/prometheus.py 中有 Yunshu_active_connections 引用但未实际定义",
        "action": "需先在 prometheus.py 中补定义 Gauge，再决定告警规则中的指标名",
    },
}

# 已修复指标（上轮任务已对齐，本次不处理）
ALREADY_FIXED = {
    "yunshu_conversations_total": "Yunshu_conversations_total (上轮任务已修复)",
}


# ═══════════════════════════════════════════════════════════════════
#  修复引擎
# ═══════════════════════════════════════════════════════════════════

def fix_metrics(content: str) -> tuple[str, dict]:
    """执行指标名替换

    Returns:
        (修复后内容, 修改报告 dict)
    """
    report = {
        "replacements": [],  # [{old, new, count, lines}]
        "undefined_found": [],  # [metric_name]
        "already_fixed_found": [],  # [metric_name]
    }

    # 按指标名长度降序排列，避免短名误匹配长名子串
    # 例：yunshu_http_requests_total 和 yunshu_http_request_duration_seconds_bucket
    # 必须先替换长名，否则短名会破坏长名
    sorted_metrics = sorted(METRIC_MAPPING.items(), key=lambda x: len(x[0]), reverse=True)

    for old_name, info in sorted_metrics:
        new_name = info["target"]
        # 使用正则确保匹配完整指标名（word boundary）
        # \b 在 yunshu_ 前生效（y 是 word char）；指标名后跟 { 或 ] 或空格等非 word char
        pattern = re.compile(r'\b' + re.escape(old_name) + r'\b')

        # 找到所有匹配行号（1-based）
        lines = content.split('\n')
        matched_lines = []
        for i, line in enumerate(lines, 1):
            if pattern.search(line):
                matched_lines.append(i)

        if matched_lines:
            # 执行替换
            new_content = pattern.sub(new_name, content)
            count = len(matched_lines)
            report["replacements"].append({
                "old": old_name,
                "new": new_name,
                "count": count,
                "lines": matched_lines,
                "source": info["source"],
                "reason": info["reason"],
            })
            content = new_content

    # 检查未定义指标（不替换，仅报告）
    for metric_name in UNDEFINED_METRICS:
        pattern = re.compile(r'\b' + re.escape(metric_name) + r'\b')
        if pattern.search(content):
            report["undefined_found"].append(metric_name)

    # 检查已修复指标（确认不再有小写版本）
    for metric_name in ALREADY_FIXED:
        pattern = re.compile(r'\b' + re.escape(metric_name) + r'\b')
        if pattern.search(content):
            report["already_fixed_found"].append(metric_name)

    return content, report


# ═══════════════════════════════════════════════════════════════════
#  报告输出
# ═══════════════════════════════════════════════════════════════════

def print_report(report: dict, file_path: Path, dry_run: bool):
    """打印修复报告"""
    mode = "[DRY-RUN 预览]" if dry_run else "[已执行]"
    print(f"\n{'═' * 90}")
    print(f"  指标命名批量修复报告 {mode}")
    print(f"  文件: {file_path}")
    print(f"{'═' * 90}")

    if not report["replacements"]:
        print("\n  ℹ 未发现需要修复的指标引用（所有指标名已对齐代码真相源）")
    else:
        print(f"\n  共修复 {len(report['replacements'])} 个指标，{sum(r['count'] for r in report['replacements'])} 处引用:\n")
        for r in report["replacements"]:
            print(f"  ├─ {r['old']}  →  {r['new']}")
            print(f"  │   修改 {r['count']} 处 (行: {r['lines']})")
            print(f"  │   真相源: {r['source']}")
            print(f"  │   原因: {r['reason']}")
            print()

    if report["undefined_found"]:
        print(f"\n  ⚠ 发现 {len(report['undefined_found'])} 个未定义指标（未修复，需先在代码中补定义）:")
        for metric in report["undefined_found"]:
            info = UNDEFINED_METRICS[metric]
            print(f"  ├─ {metric}")
            print(f"  │   真相源: {info['source']}")
            print(f"  │   原因: {info['reason']}")
            print(f"  │   建议: {info['action']}")
            print()

    if report["already_fixed_found"]:
        print(f"\n  ℹ 发现 {len(report['already_fixed_found'])} 个已修复指标仍存在小写版本:")
        for metric in report["already_fixed_found"]:
            print(f"  ├─ {metric} → {ALREADY_FIXED[metric]}")
        print(f"  （可能来自其他未扫描的文件，需手动检查）")

    print(f"\n{'═' * 90}")
    if dry_run:
        print("  预览完成，未修改文件。执行修复请去掉 --dry-run 参数。")
    else:
        print("  修复完成。建议执行以下验证:")
        print("    1. kubectl apply -f monitoring/alerts_production.yml --dry-run=server")
        print("    2. 在 Prometheus 中验证告警规则加载正常")
    print(f"{'═' * 90}\n")


# ═══════════════════════════════════════════════════════════════════
#  主流程
# ═══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="alerts_production.yml 指标命名批量修复（对齐代码真相源）"
    )
    parser.add_argument(
        "--file",
        default="monitoring/alerts_production.yml",
        help="告警规则文件路径（默认: monitoring/alerts_production.yml）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="预览模式，只显示将要修改的内容，不实际修改文件",
    )
    parser.add_argument(
        "--backup",
        action="store_true",
        help="修改前自动备份原文件（.bak.YYYYMMDD_HHMMSS 后缀）",
    )
    args = parser.parse_args()

    file_path = Path(args.file).resolve()
    if not file_path.exists():
        print(f"❌ 文件不存在: {file_path}", file=sys.stderr)
        return 1

    # 读取文件
    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"❌ 读取文件失败: {e}", file=sys.stderr)
        return 1

    # 执行修复
    new_content, report = fix_metrics(content)

    # 打印报告
    print_report(report, file_path, args.dry_run)

    # 非 dry-run 模式写入文件
    if not args.dry_run and report["replacements"]:
        # 自动备份
        if args.backup:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = file_path.with_suffix(f".bak.{timestamp}{file_path.suffix}")
            shutil.copy2(file_path, backup_path)
            print(f"  ✓ 已备份原文件到: {backup_path}")

        # 写入修复后内容
        try:
            file_path.write_text(new_content, encoding="utf-8")
            print(f"  ✓ 已写入修复后文件: {file_path}")
        except Exception as e:
            print(f"  ❌ 写入文件失败: {e}", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
