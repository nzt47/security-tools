"""模拟 PR 测试流程脚本

模拟 GitHub Actions 中 observability-ci.yml 的 architecture-visibility-check job
完整执行三个步骤：依赖图生成 → 架构规则校验 → 变更影响分析，
并输出模拟的 PR 评论内容，便于本地预览 CI 行为。

使用：
    python scripts/simulate_pr_review.py --base bdefb546~1 --head bdefb546
    python scripts/simulate_pr_review.py  # 默认使用 HEAD~1...HEAD
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run(cmd: list[str], cwd: Path = REPO_ROOT) -> tuple[int, str, str]:
    """运行命令，返回 (exit_code, stdout, stderr)

    设置 PYTHONIOENCODING=utf-8 避免 Windows GBK 终端无法输出 Unicode 字符（✓ 等）。
    """
    import os
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        cmd, cwd=str(cwd), capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env,
    )
    return result.returncode, result.stdout or "", result.stderr or ""


def _step(num: int, total: int, name: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  步骤 {num}/{total}：{name}")
    print(f"{'=' * 60}")


def main() -> int:
    parser = argparse.ArgumentParser(description="模拟 PR 测试流程")
    parser.add_argument("--base", default="HEAD~1", help="基准 ref")
    parser.add_argument("--head", default="HEAD", help="目标 ref")
    parser.add_argument("--root", default="agent", help="源码根目录")
    args = parser.parse_args()

    print("=" * 60)
    print("  模拟 PR 测试流程 — observability-ci.yml")
    print("  Job: architecture-visibility-check")
    print("=" * 60)
    print(f"  Base: {args.base}")
    print(f"  Head: {args.head}")
    print(f"  Root: {args.root}")

    total_steps = 4
    results = {"steps": [], "exit_codes": []}

    # ── 步骤 1/4：生成模块依赖图 ──
    _step(1, total_steps, "生成模块依赖图")
    start = time.perf_counter()
    code, out, err = _run([
        sys.executable, "-m", "agent.observability.dependency_graph",
        "--root", args.root,
        "--output", "docs/architecture/module_dependency_graph.md",
        "--json-output", "docs/architecture/dependency_graph.json",
    ])
    elapsed = (time.perf_counter() - start) * 1000
    print(f"  退出码: {code}（预期 0）")
    print(f"  耗时: {elapsed:.0f} ms")
    if out.strip():
        print(f"  输出:\n{out.strip()}")
    if code != 0 and err.strip():
        print(f"  错误:\n{err.strip()}")
    results["steps"].append({
        "name": "dependency_graph",
        "exit_code": code,
        "duration_ms": elapsed,
    })
    results["exit_codes"].append(code)

    # ── 步骤 2/4：架构规则校验 ──
    _step(2, total_steps, "架构规则校验（违规阻断合并）")
    start = time.perf_counter()
    code, out, err = _run([
        sys.executable, "-m", "agent.observability.arch_rules",
        "--check",
        "--root", args.root,
        "--exemptions", "docs/architecture/legacy_exemptions.json",
        "--config", "config.yaml",
        "--json-report", "docs/architecture/arch_rules_report.json",
        "--md-report", "docs/architecture/arch_rules_report.md",
    ])
    elapsed = (time.perf_counter() - start) * 1000
    print(f"  退出码: {code}（0=通过，1=违规阻断）")
    print(f"  耗时: {elapsed:.0f} ms")
    # 读取 JSON 报告摘要
    report_path = REPO_ROOT / "docs" / "architecture" / "arch_rules_report.json"
    if report_path.exists():
        report = json.loads(report_path.read_text(encoding="utf-8"))
        print(f"  通过状态: {'✓ 通过' if report.get('passed') else '❌ 违规'}")
        print(f"  违规总数: {report.get('total_violations', 0)}")
        print(f"  未豁免: {report.get('active_violations', 0)}")
        print(f"  已豁免: {report.get('exempted_violations', 0)}")
    results["steps"].append({
        "name": "arch_rules",
        "exit_code": code,
        "duration_ms": elapsed,
    })
    results["exit_codes"].append(code)

    # ── 步骤 3/4：变更影响分析（仅 PR 时执行）──
    _step(3, total_steps, "变更影响分析（PR 评论内容）")
    start = time.perf_counter()
    code, out, err = _run([
        sys.executable, "scripts/impact_analysis.py",
        "--base", args.base,
        "--head", args.head,
        "--root", args.root,
        "--output", "docs/architecture/impact_report.md",
        "--json-report", "docs/architecture/impact_report.json",
        "--github-comment",
    ])
    elapsed = (time.perf_counter() - start) * 1000
    print(f"  退出码: {code}（CI 中 continue-on-error: true，不阻断）")
    print(f"  耗时: {elapsed:.0f} ms")
    # 读取 JSON 报告摘要
    impact_path = REPO_ROOT / "docs" / "architecture" / "impact_report.json"
    if impact_path.exists():
        impact = json.loads(impact_path.read_text(encoding="utf-8"))
        print(f"  变更文件数: {impact.get('changed_files_count', 0)}")
        print(f"  受影响模块数: {impact.get('impacted_modules_count', 0)}")
        print(f"  推荐测试数: {impact.get('recommended_tests_count', 0)}")
    results["steps"].append({
        "name": "impact_analysis",
        "exit_code": code,
        "duration_ms": elapsed,
    })
    results["exit_codes"].append(code)

    # ── 步骤 4/4：生成模拟 PR 评论 ──
    _step(4, total_steps, "生成模拟 PR 评论")
    pr_comment = _build_pr_comment(args)
    comment_path = REPO_ROOT / "docs" / "architecture" / "pr_comment_preview.md"
    comment_path.write_text(pr_comment, encoding="utf-8")
    print(f"  模拟 PR 评论已生成: {comment_path}")
    print("\n" + "─" * 60)
    print("  PR 评论内容预览（前 80 行）:")
    print("─" * 60)
    for line in pr_comment.split("\n")[:80]:
        print(f"  {line}")
    results["steps"].append({
        "name": "pr_comment",
        "exit_code": 0,
        "duration_ms": 0,
    })

    # ── 汇总 ──
    print(f"\n{'=' * 60}")
    print("  CI 模拟汇总")
    print(f"{'=' * 60}")
    arch_pass = results["exit_codes"][1] == 0
    print(f"  架构规则校验: {'✓ 通过（允许合并）' if arch_pass else '❌ 违规（阻断合并）'}")
    print(f"  变更影响分析: {'✓ 已生成' if results['exit_codes'][2] == 0 else '[!] 跳过/失败'}")
    print(f"  PR 评论: ✓ 已生成")
    print(f"\n  报告文件:")
    print(f"    - docs/architecture/module_dependency_graph.md")
    print(f"    - docs/architecture/dependency_graph.json")
    print(f"    - docs/architecture/arch_rules_report.md")
    print(f"    - docs/architecture/arch_rules_report.json")
    print(f"    - docs/architecture/impact_report.md")
    print(f"    - docs/architecture/impact_report.json")
    print(f"    - docs/architecture/pr_comment_preview.md")

    return 0


def _build_pr_comment(args: argparse.Namespace) -> str:
    """构建模拟 PR 评论内容（与 observability-ci.yml 中 github-script 步骤输出一致）"""
    lines = ["## 📊 变更影响分析报告", ""]

    # 架构规则校验结果
    arch_report_path = REPO_ROOT / "docs" / "architecture" / "arch_rules_report.json"
    if arch_report_path.exists():
        arch = json.loads(arch_report_path.read_text(encoding="utf-8"))
        status = "✅ 通过" if arch.get("passed") else "❌ 违规"
        lines.append(f"### 🏛️ 架构规则校验：{status}")
        lines.append("")
        lines.append(f"- 违规总数：{arch.get('total_violations', 0)}")
        lines.append(f"- 未豁免：{arch.get('active_violations', 0)}")
        lines.append(f"- 已豁免：{arch.get('exempted_violations', 0)}")
        lines.append(f"- 耗时：{arch.get('duration_ms', 0):.0f} ms")
        lines.append("")

        # 列出未豁免违规
        active_violations = [
            v for v in arch.get("violations", []) if not v.get("is_exempted")
        ]
        if active_violations:
            lines.append("**未豁免违规（需修复）：**")
            for v in active_violations:
                lines.append(
                    f"- `{v['rule_id']}`: `{v['source']}` → `{v['target']}` "
                    f"({v['source_file']}:{v['line']})"
                )
            lines.append("")

    # 变更影响分析
    impact_path = REPO_ROOT / "docs" / "architecture" / "impact_report.md"
    if impact_path.exists():
        lines.append("### 📈 变更影响分析")
        lines.append("")
        content = impact_path.read_text(encoding="utf-8")
        # 跳过标题，直接附加内容
        content_lines = content.split("\n")
        # 找到第一个非标题、非空行开始
        start_idx = 0
        for i, line in enumerate(content_lines):
            if line.startswith("# ") or line.strip() == "":
                continue
            start_idx = i
            break
        lines.extend(content_lines[start_idx:])
        lines.append("")

    # 依赖图统计
    graph_path = REPO_ROOT / "docs" / "architecture" / "dependency_graph.json"
    if graph_path.exists():
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
        stats = graph.get("stats", {})
        lines.append("### 📐 依赖图统计")
        lines.append("")
        lines.append(f"- 节点数：{stats.get('total_nodes', 0)}")
        lines.append(f"- 边数：{stats.get('total_edges', 0)}")
        lines.append(f"- 跨层调用：{stats.get('cross_layer_edges', 0)}")
        lines.append(f"- 违规调用：{stats.get('violation_edges', 0)}")
        lines.append(f"- 构建耗时：{stats.get('build_duration_ms', 0):.0f} ms")
        lines.append("")
        lines.append("> 完整报告见 Artifact：`architecture-visibility-report`")

    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
