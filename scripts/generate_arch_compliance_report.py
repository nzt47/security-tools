"""架构合规性报告生成器

自动生成完整的架构合规性报告，包含：
- 执行摘要（通过/失败状态、关键指标）
- 规则概览（7 条内置规则的描述、严重度、状态）
- 违规项详情（规则、源/目标模块、文件位置、严重度、建议）
- 豁免项详情（豁免原因、缓解措施、技术债务工单）
- 依赖图统计（节点数、边数、层级分布）
- 建议和后续行动

使用：
    python scripts/generate_arch_compliance_report.py
    python scripts/generate_arch_compliance_report.py --output docs/architecture/arch_compliance_report.md
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# 规则元数据（与 arch_rules.py BUILTIN_RULES 对齐）
RULE_METADATA = {
    "no_orchestrator_to_dao": {
        "desc": "禁止 orchestrator 直接访问 dao 层",
        "severity": "high",
        "category": "跨层调用",
        "suggestion": "orchestrator 应通过 service 或 business 层访问数据，避免业务逻辑与数据访问耦合",
    },
    "no_cognitive_to_server_routes": {
        "desc": "禁止 cognitive 直接访问 server_routes",
        "severity": "high",
        "category": "跨层调用",
        "suggestion": "cognitive 应通过 orchestrator 协调 HTTP 路由，不应直接依赖表现层",
    },
    "no_cognitive_to_dao": {
        "desc": "禁止 cognitive 直接访问 dao 层",
        "severity": "high",
        "category": "跨层调用",
        "suggestion": "cognitive 不应直接读写数据，应通过 memory 或 service",
    },
    "no_tools_to_dao": {
        "desc": "禁止 tools 直接访问 dao 层",
        "severity": "medium",
        "category": "跨层调用",
        "suggestion": "工具模块应保持无状态，数据访问由上层处理",
    },
    "no_guardrails_to_server_routes": {
        "desc": "禁止 guardrails 直接访问 server_routes",
        "severity": "medium",
        "category": "跨层调用",
        "suggestion": "guardrails 应保持独立，不依赖 HTTP 路由层",
    },
    "no_circular_dependency": {
        "desc": "禁止循环依赖（A->B->A）",
        "severity": "high",
        "category": "循环依赖",
        "suggestion": "通过依赖倒置或中间层解耦，或使用 lazy_loader 延迟加载",
    },
    "no_agent_import_tests": {
        "desc": "禁止 agent/ 下模块直接 import tests/",
        "severity": "high",
        "category": "反向依赖",
        "suggestion": "生产代码不应依赖测试代码，请反转依赖方向",
    },
}

SEVERITY_EMOJI = {"high": "🔴", "medium": "🟡", "low": "🟢"}


def _run_arch_rules(root: str, exemptions: str, config: str) -> dict:
    """运行架构规则校验，返回 JSON 报告"""
    cmd = [
        sys.executable, "-m", "agent.observability.arch_rules",
        "--check", "--root", root,
        "--exemptions", exemptions,
        "--config", config,
        "--json-report", "docs/architecture/arch_rules_report.json",
        "--md-report", "docs/architecture/arch_rules_report.md",
    ]
    subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
    report_path = REPO_ROOT / "docs" / "architecture" / "arch_rules_report.json"
    return json.loads(report_path.read_text(encoding="utf-8"))


def _load_exemptions(path: str) -> dict:
    """加载豁免清单"""
    p = REPO_ROOT / path
    if not p.exists():
        return {"exemptions": []}
    return json.loads(p.read_text(encoding="utf-8"))


def _build_rule_summary(arch_report: dict) -> list[dict]:
    """构建规则摘要（每条规则的命中情况）"""
    violations = arch_report.get("violations", [])
    rule_hits: dict[str, dict] = {}

    for rule_id in RULE_METADATA:
        rule_hits[rule_id] = {
            "rule_id": rule_id,
            "total_violations": 0,
            "active": 0,
            "exempted": 0,
        }

    for v in violations:
        rid = v["rule_id"]
        if rid not in rule_hits:
            rule_hits[rid] = {"rule_id": rid, "total_violations": 0, "active": 0, "exempted": 0}
        rule_hits[rid]["total_violations"] += 1
        if v.get("is_exempted"):
            rule_hits[rid]["exempted"] += 1
        else:
            rule_hits[rid]["active"] += 1

    return list(rule_hits.values())


def generate_report(arch_report: dict, exemptions_data: dict, output_path: Path) -> str:
    """生成完整的架构合规性报告 Markdown"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    passed = arch_report.get("passed", False)
    status_emoji = "✅ 通过" if passed else "❌ 违规"
    stats = arch_report.get("graph_stats", {})
    exemptions_list = exemptions_data.get("exemptions", [])

    # 构建豁免查找表
    exemption_lookup = {}
    for ex in exemptions_list:
        key = f"{ex['rule_id']}:{ex['source']}->{ex['target']}"
        exemption_lookup[key] = ex
        # 循环依赖双向匹配
        if ex["rule_id"] == "no_circular_dependency":
            rev_key = f"{ex['rule_id']}:{ex['target']}->{ex['source']}"
            exemption_lookup[rev_key] = ex

    rule_summary = _build_rule_summary(arch_report)

    lines = []
    # ── 标题与执行摘要 ──
    lines.append("# 架构合规性报告")
    lines.append("")
    lines.append(f"> **生成时间**: {now}")
    lines.append(f"> **Trace ID**: `{arch_report.get('trace_id', 'N/A')}`")
    lines.append(f"> **扫描根目录**: `{arch_report.get('root_dir', 'agent')}`")
    lines.append(f"> **校验耗时**: {arch_report.get('duration_ms', 0):.0f} ms")
    lines.append("")

    lines.append("## 一、执行摘要")
    lines.append("")
    lines.append(f"**总体状态**: {status_emoji}")
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("|------|------|")
    lines.append(f"| 校验规则数 | {arch_report.get('total_rules', 0)} |")
    lines.append(f"| 违规总数 | {arch_report.get('total_violations', 0)} |")
    lines.append(f"| 未豁免违规（需修复） | {arch_report.get('active_violations', 0)} |")
    lines.append(f"| 已豁免违规（存量技术债务） | {arch_report.get('exempted_violations', 0)} |")
    lines.append(f"| 扫描文件数 | {stats.get('total_files', 0)} |")
    lines.append(f"| 模块节点数 | {stats.get('total_nodes', 0)} |")
    lines.append(f"| 依赖边数 | {stats.get('total_edges', 0)} |")
    lines.append(f"| 跨层调用数 | {stats.get('cross_layer_edges', 0)} |")
    lines.append(f"| 跨层违规数 | {stats.get('violation_edges', 0)} |")
    lines.append(f"| 动态 import 数 | {stats.get('dynamic_edges', 0)} |")
    lines.append("")

    # ── 规则概览 ──
    lines.append("## 二、规则概览")
    lines.append("")
    lines.append("| 规则 ID | 描述 | 类别 | 严重度 | 命中数 | 未豁免 | 已豁免 | 状态 |")
    lines.append("|---------|------|------|--------|--------|--------|--------|------|")
    for rs in rule_summary:
        meta = RULE_METADATA.get(rs["rule_id"], {})
        sev = meta.get("severity", "unknown")
        emoji = SEVERITY_EMOJI.get(sev, "⚪")
        total = rs["total_violations"]
        active = rs["active"]
        exempted = rs["exempted"]
        if active > 0:
            status = "❌ 未通过"
        elif total > 0:
            status = "🚫 全部豁免"
        else:
            status = "✅ 无违规"
        lines.append(
            f"| `{rs['rule_id']}` | {meta.get('desc', 'N/A')} | "
            f"{meta.get('category', 'N/A')} | {emoji} {sev} | "
            f"{total} | {active} | {exempted} | {status} |"
        )
    lines.append("")

    # ── 违规项详情 ──
    violations = arch_report.get("violations", [])
    active_violations = [v for v in violations if not v.get("is_exempted")]
    exempted_violations = [v for v in violations if v.get("is_exempted")]

    lines.append("## 三、违规项详情")
    lines.append("")

    if active_violations:
        lines.append("### 3.1 未豁免违规（需立即修复）")
        lines.append("")
        for i, v in enumerate(active_violations, 1):
            sev = v.get("severity", "unknown")
            emoji = SEVERITY_EMOJI.get(sev, "⚪")
            lines.append(f"#### 违规 #{i}: `{v['rule_id']}` {emoji} {sev}")
            lines.append("")
            lines.append(f"- **规则**: `{v['rule_id']}`")
            lines.append(f"- **描述**: {v.get('rule_desc', 'N/A')}")
            lines.append(f"- **源模块**: `{v.get('source', 'N/A')}`")
            lines.append(f"- **目标模块**: `{v.get('target', 'N/A')}`")
            lines.append(f"- **源文件**: `{v.get('source_file', 'N/A')}:{v.get('line', 0)}`")
            lines.append(f"- **严重度**: {sev}")
            lines.append(f"- **修复建议**: {v.get('suggestion', 'N/A')}")
            lines.append("")
    else:
        lines.append("### 3.1 未豁免违规")
        lines.append("")
        lines.append("✅ **无未豁免违规。** 所有检测到的违规均已登记在存量豁免清单中。")
        lines.append("")

    if exempted_violations:
        lines.append("### 3.2 已豁免违规（存量技术债务）")
        lines.append("")
        lines.append("以下违规已登记在 `docs/architecture/legacy_exemptions.json` 中，作为已知技术债务跟踪。")
        lines.append("")
        for i, v in enumerate(exempted_violations, 1):
            sev = v.get("severity", "unknown")
            emoji = SEVERITY_EMOJI.get(sev, "⚪")
            # 查找对应的豁免详情
            key = f"{v['rule_id']}:{v.get('source', '')}->{v.get('target', '')}"
            ex = exemption_lookup.get(key, {})

            lines.append(f"#### 豁免 #{i}: `{v['rule_id']}` {emoji} {sev}")
            lines.append("")
            lines.append(f"- **规则**: `{v['rule_id']}`")
            lines.append(f"- **描述**: {v.get('rule_desc', 'N/A')}")
            lines.append(f"- **循环路径**: `{v.get('source', 'N/A')}` <-> `{v.get('target', 'N/A')}`")
            lines.append(f"- **源文件**: `{v.get('source_file', 'N/A')}:{v.get('line', 0)}`")
            lines.append(f"- **严重度**: {sev}")
            lines.append(f"- **技术债务工单**: `{ex.get('tech_debt_ticket', 'N/A')}`")
            lines.append(f"- **登记日期**: {ex.get('added_at', 'N/A')}")
            lines.append(f"- **负责人**: {ex.get('owner', 'N/A')}")
            lines.append(f"- **豁免原因**: {ex.get('reason', 'N/A')}")
            lines.append(f"- **缓解措施**: {ex.get('mitigation', 'N/A')}")
            lines.append(f"- **修复建议**: {v.get('suggestion', 'N/A')}")
            lines.append("")
    else:
        lines.append("### 3.2 已豁免违规")
        lines.append("")
        lines.append("无已豁免违规。")
        lines.append("")

    # ── 依赖图统计 ──
    lines.append("## 四、依赖图统计")
    lines.append("")
    lines.append("### 4.1 总体统计")
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("|------|------|")
    lines.append(f"| 扫描文件数 | {stats.get('total_files', 0)} |")
    lines.append(f"| 模块节点数 | {stats.get('total_nodes', 0)} |")
    lines.append(f"| 依赖边数 | {stats.get('total_edges', 0)} |")
    lines.append(f"| 跨层调用数 | {stats.get('cross_layer_edges', 0)} |")
    lines.append(f"| 跨层违规数 | {stats.get('violation_edges', 0)} |")
    lines.append(f"| 动态 import 数 | {stats.get('dynamic_edges', 0)} |")
    lines.append(f"| 依赖图构建耗时 | {stats.get('build_duration_ms', 0):.0f} ms |")
    lines.append("")

    # ── 层级分布 ──
    layers = stats.get("layers", {})
    if layers:
        lines.append("### 4.2 层级分布")
        lines.append("")
        lines.append("| 层级 | 模块数 | 占比 |")
        lines.append("|------|--------|------|")
        total_nodes = stats.get("total_nodes", 1) or 1
        sorted_layers = sorted(layers.items(), key=lambda x: x[1], reverse=True)
        for layer, count in sorted_layers:
            pct = count / total_nodes * 100
            lines.append(f"| {layer} | {count} | {pct:.1f}% |")
        lines.append("")

    # ── 建议和后续行动 ──
    lines.append("## 五、建议和后续行动")
    lines.append("")
    if active_violations:
        lines.append("### 5.1 紧急行动（未豁免违规）")
        lines.append("")
        for v in active_violations:
            lines.append(
                f"- [ ] 修复 `{v['rule_id']}`: `{v.get('source', '')}` -> "
                f"`{v.get('target', '')}` ({v.get('source_file', '')}:{v.get('line', 0)})"
            )
        lines.append("")
    else:
        lines.append("### 5.1 紧急行动")
        lines.append("")
        lines.append("✅ 无未豁免违规，无需紧急行动。")
        lines.append("")

    lines.append("### 5.2 技术债务清零计划")
    lines.append("")
    if exempted_violations:
        lines.append("以下已豁免违规应制定清零计划：")
        lines.append("")
        for v in exempted_violations:
            key = f"{v['rule_id']}:{v.get('source', '')}->{v.get('target', '')}"
            ex = exemption_lookup.get(key, {})
            ticket = ex.get("tech_debt_ticket", "N/A")
            lines.append(
                f"- [ ] {ticket}: 解耦 `{v.get('source', '')}` <-> "
                f"`{v.get('target', '')}` 循环依赖"
            )
        lines.append("")
        lines.append("**建议方案**:")
        lines.append("- 将共享的基础设施代码（如 `with_retry`、`TemporaryNetworkError`）下沉到独立模块（如 `agent.utils.retry`）")
        lines.append("- 通过依赖倒置（接口抽象）解耦循环依赖")
        lines.append("- 使用事件驱动架构替代直接调用")
        lines.append("")
    else:
        lines.append("✅ 无技术债务。")
        lines.append("")

    lines.append("### 5.3 定期审查")
    lines.append("")
    lines.append("- 每季度评审豁免清单，推动技术债务清零")
    lines.append("- 每次新增模块时运行架构规则校验")
    lines.append("- 关注跨层调用数增长趋势，防止架构腐化")
    lines.append("- 定期更新依赖图文档 `docs/architecture/module_dependency_graph.md`")
    lines.append("")

    # ── 附录 ──
    lines.append("## 六、附录")
    lines.append("")
    lines.append("### 6.1 相关文件")
    lines.append("")
    lines.append("| 文件 | 说明 |")
    lines.append("|------|------|")
    lines.append("| `agent/observability/arch_rules.py` | 架构规则校验器 |")
    lines.append("| `agent/observability/dependency_graph.py` | 依赖图生成器 |")
    lines.append("| `docs/architecture/legacy_exemptions.json` | 存量豁免清单 |")
    lines.append("| `docs/architecture/legacy_exemptions_guide.md` | 豁免清单配置指南 |")
    lines.append("| `docs/architecture/dependency_graph.json` | 依赖图 JSON 数据 |")
    lines.append("| `docs/architecture/module_dependency_graph.md` | 依赖图 Mermaid 图 |")
    lines.append("| `docs/architecture/arch_rules_report.json` | 校验结果 JSON |")
    lines.append("| `config.yaml` | 架构规则配置（arch_rules 段） |")
    lines.append("| `.github/workflows/observability-ci.yml` | CI 工作流 |")
    lines.append("")

    lines.append("### 6.2 CLI 命令")
    lines.append("")
    lines.append("```bash")
    lines.append("# 运行架构规则校验")
    lines.append("python -m agent.observability.arch_rules --check \\")
    lines.append("  --root agent \\")
    lines.append("  --exemptions docs/architecture/legacy_exemptions.json \\")
    lines.append("  --config config.yaml \\")
    lines.append("  --json-report docs/architecture/arch_rules_report.json \\")
    lines.append("  --md-report docs/architecture/arch_rules_report.md")
    lines.append("")
    lines.append("# 生成模块依赖图")
    lines.append("python -m agent.observability.dependency_graph \\")
    lines.append("  --root agent \\")
    lines.append("  --output docs/architecture/module_dependency_graph.md \\")
    lines.append("  --json-output docs/architecture/dependency_graph.json")
    lines.append("")
    lines.append("# 变更影响分析")
    lines.append("python scripts/impact_analysis.py \\")
    lines.append("  --base origin/main --head HEAD \\")
    lines.append("  --output docs/architecture/impact_report.md")
    lines.append("")
    lines.append("# 生成合规性报告（本脚本）")
    lines.append("python scripts/generate_arch_compliance_report.py")
    lines.append("```")
    lines.append("")

    report_md = "\n".join(lines)
    output_path.write_text(report_md, encoding="utf-8")
    return report_md


def main() -> int:
    parser = argparse.ArgumentParser(description="生成架构合规性报告")
    parser.add_argument("--root", default="agent", help="扫描根目录")
    parser.add_argument("--exemptions", default="docs/architecture/legacy_exemptions.json", help="豁免清单路径")
    parser.add_argument("--config", default="config.yaml", help="配置文件路径")
    parser.add_argument("--output", default="docs/architecture/arch_compliance_report.md", help="输出路径")
    args = parser.parse_args()

    print("=" * 60)
    print("  架构合规性报告生成器")
    print("=" * 60)

    print("\n[1/3] 运行架构规则校验...")
    arch_report = _run_arch_rules(args.root, args.exemptions, args.config)
    passed = arch_report.get("passed", False)
    print(f"  状态: {'✅ 通过' if passed else '❌ 违规'}")
    print(f"  违规: {arch_report.get('total_violations', 0)} "
          f"(未豁免: {arch_report.get('active_violations', 0)}, "
          f"已豁免: {arch_report.get('exempted_violations', 0)})")

    print("\n[2/3] 加载豁免清单...")
    exemptions_data = _load_exemptions(args.exemptions)
    print(f"  豁免项: {len(exemptions_data.get('exemptions', []))} 个")

    print("\n[3/3] 生成报告...")
    output_path = REPO_ROOT / args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report = generate_report(arch_report, exemptions_data, output_path)
    print(f"  ✓ 报告已生成: {output_path}")
    print(f"  报告长度: {len(report)} 字符, {report.count(chr(10))} 行")

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
