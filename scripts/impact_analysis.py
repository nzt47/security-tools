"""变更影响分析脚本 — 基于 git diff 与依赖图反查受影响模块

输入 git diff（对比 origin/main...HEAD），解析变更文件，
基于依赖图反查受影响的上游/下游模块，关联测试用例，
输出 Markdown 影响报告（受影响模块列表 + 风险等级 + 推荐测试清单）。

集成到 .github/workflows/observability-ci.yml，在 PR 中自动评论影响报告。

使用示例：
    # 本地运行
    python scripts/impact_analysis.py --base origin/main --head HEAD

    # CI 中运行（输出 Markdown 报告并评论到 PR）
    python scripts/impact_analysis.py --base origin/main --head HEAD \\
        --output impact_report.md --github-comment

可观测性约束实现：
- 结构化日志（trace_id/module_name/action/duration_ms）
- 边界显性化（git 失败、依赖图缺失等抛出带错误码异常）
- 埋点预留（关键节点 trackEvent 占位）
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# 将项目根目录加入 sys.path，支持直接运行
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from agent.observability.dependency_graph import (  # noqa: E402
    DependencyGraphBuilder,
    DependencyEdge,
    DependencyGraphError,
)

logger = logging.getLogger(__name__)


class ImpactAnalysisError(Exception):
    """变更影响分析异常 — 带业务错误码"""

    def __init__(self, message: str, error_code: str = "IMPACT_ANALYSIS_ERROR"):
        super().__init__(message)
        self.error_code = error_code
        self.message = message


# ── 数据结构 ──────────────────────────────────────────────────


@dataclass
class ChangedFile:
    """变更文件"""

    path: str  # 相对路径
    status: str  # A=新增, M=修改, D=删除, R=重命名
    module_path: str  # 模块点路径（agent.xxx）
    insertions: int = 0  # 新增行数
    deletions: int = 0  # 删除行数

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ImpactedModule:
    """受影响模块"""

    module_path: str  # 模块点路径
    impact_type: str  # upstream=上游（依赖变更模块） / downstream=下游（被变更模块依赖）
    impact_chain: str  # 影响链描述（A → B → C）
    risk_level: str  # high / medium / low
    related_tests: List[str] = field(default_factory=list)  # 关联测试用例
    reason: str = ""  # 受影响原因

    def to_dict(self) -> Dict[str, Any]:
        return {
            **asdict(self),
            "related_tests": self.related_tests,
        }


@dataclass
class ImpactReport:
    """变更影响报告"""

    trace_id: str
    base_ref: str
    head_ref: str
    changed_files: List[ChangedFile]
    impacted_modules: List[ImpactedModule]
    recommended_tests: List[str]
    risk_summary: Dict[str, int]  # {high: n, medium: n, low: n}
    duration_ms: float

    @property
    def has_impact(self) -> bool:
        return len(self.impacted_modules) > 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "base_ref": self.base_ref,
            "head_ref": self.head_ref,
            "changed_files": [f.to_dict() for f in self.changed_files],
            "impacted_modules": [m.to_dict() for m in self.impacted_modules],
            "recommended_tests": self.recommended_tests,
            "risk_summary": self.risk_summary,
            "duration_ms": round(self.duration_ms, 2),
        }

    def to_markdown(self) -> str:
        """生成 Markdown 影响报告"""
        lines = [
            "# 📊 变更影响分析报告",
            "",
            f"- **Trace ID**: `{self.trace_id}`",
            f"- **对比基准**: `{self.base_ref}` → `{self.head_ref}`",
            f"- **变更文件数**: {len(self.changed_files)}",
            f"- **受影响模块数**: {len(self.impacted_modules)}",
            f"- **推荐测试数**: {len(self.recommended_tests)}",
            f"- **风险分布**: 🔴 高 {self.risk_summary.get('high', 0)} / "
            f"🟡 中 {self.risk_summary.get('medium', 0)} / "
            f"🟢 低 {self.risk_summary.get('low', 0)}",
            f"- **分析耗时**: {self.duration_ms:.2f} ms",
            "",
        ]

        # 变更文件清单
        if self.changed_files:
            lines.append("## 📁 变更文件清单")
            lines.append("")
            lines.append("| 状态 | 文件路径 | 模块 | +行/-行 |")
            lines.append("|------|----------|------|---------|")
            status_icon = {"A": "🆕", "M": "📝", "D": "🗑️", "R": "📦"}
            for f in self.changed_files:
                icon = status_icon.get(f.status, f.status)
                lines.append(
                    f"| {icon} {f.status} | `{f.path}` | `{f.module_path}` | "
                    f"+{f.insertions}/-{f.deletions} |"
                )
            lines.append("")

        # 受影响模块清单
        if self.impacted_modules:
            lines.append("## 🎯 受影响模块清单")
            lines.append("")
            lines.append("| 模块 | 影响类型 | 风险 | 影响链 | 原因 |")
            lines.append("|------|----------|------|--------|------|")
            risk_icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}
            for m in self.impacted_modules:
                icon = risk_icon.get(m.risk_level, "")
                lines.append(
                    f"| `{m.module_path}` | {m.impact_type} | "
                    f"{icon} {m.risk_level} | `{m.impact_chain}` | {m.reason} |"
                )
            lines.append("")

        # 推荐测试用例
        if self.recommended_tests:
            lines.append("## ✅ 推荐执行的测试用例")
            lines.append("")
            lines.append("```bash")
            lines.append(
                "python -m pytest "
                + " ".join(self.recommended_tests)
                + " -v --tb=short"
            )
            lines.append("```")
            lines.append("")
        else:
            lines.append("## ✅ 推荐测试用例")
            lines.append("")
            lines.append("_未匹配到关联测试用例，建议补充测试覆盖。_")
            lines.append("")

        return "\n".join(lines)


# ── 分析器 ──────────────────────────────────────────────────


class ImpactAnalyzer:
    """变更影响分析器"""

    def __init__(
        self,
        base_ref: str = "origin/main",
        head_ref: str = "HEAD",
        root_dir: str = "agent",
        tests_dir: str = "tests",
        trace_id: Optional[str] = None,
        repo_root: Optional[str] = None,
    ):
        """初始化变更影响分析器

        Args:
            base_ref: 基准 ref（默认 origin/main）
            head_ref: 目标 ref（默认 HEAD）
            root_dir: 源码根目录（用于构建依赖图）
            tests_dir: 测试目录
            trace_id: 链路追踪 ID
            repo_root: git 仓库根目录（默认自动检测）
        """
        self.base_ref = base_ref
        self.head_ref = head_ref
        self.trace_id = trace_id or uuid.uuid4().hex[:16]
        self.repo_root = repo_root or str(_PROJECT_ROOT)
        # root_dir 相对于 repo_root 解析（支持相对路径）
        root_path = Path(root_dir)
        if not root_path.is_absolute():
            root_path = Path(self.repo_root) / root_dir
        self.root_dir = str(root_path)
        # tests_dir 相对于 repo_root 解析
        tests_path = Path(tests_dir)
        if not tests_path.is_absolute():
            tests_path = Path(self.repo_root) / tests_dir
        self.tests_dir = str(tests_path)

        self._log_action(
            "init",
            f"变更影响分析器初始化: base={base_ref}, head={head_ref}",
        )

    def analyze(self) -> ImpactReport:
        """执行变更影响分析

        Returns:
            变更影响报告

        Raises:
            ImpactAnalysisError: 分析失败时抛出
        """
        total_start = time.perf_counter()
        self._log_action("analyze_start", "开始变更影响分析")
        # 埋点预留：变更影响分析启动
        self._track_event("impact_analysis_started", {"base": self.base_ref})

        # 1. 获取变更文件
        changed_files = self._get_changed_files()
        self._log_action(
            "get_changed_files",
            f"获取变更文件: {len(changed_files)} 个",
        )

        # 无变更则返回空报告
        if not changed_files:
            elapsed_ms = (time.perf_counter() - total_start) * 1000
            return ImpactReport(
                trace_id=self.trace_id,
                base_ref=self.base_ref,
                head_ref=self.head_ref,
                changed_files=[],
                impacted_modules=[],
                recommended_tests=[],
                risk_summary={"high": 0, "medium": 0, "low": 0},
                duration_ms=elapsed_ms,
            )

        # 2. 构建依赖图
        try:
            builder = DependencyGraphBuilder(
                root_dir=self.root_dir, trace_id=self.trace_id
            )
            builder.build()
            edges = builder.edges
            nodes = builder.nodes
        except DependencyGraphError as e:
            raise ImpactAnalysisError(
                f"依赖图构建失败: {e.message}",
                error_code=f"IMPACT_GRAPH_FAIL:{e.error_code}",
            ) from e

        # 3. 反查受影响模块
        changed_modules = {f.module_path for f in changed_files}
        impacted = self._find_impacted_modules(changed_modules, edges, nodes)

        # 4. 关联测试用例
        # 修复：预收集 all_tests 一次，传递给 _relate_tests 和后续循环，避免重复收集
        # 原代码 _relate_tests 内部和此处各收集一次，导致 _collect_test_files 被调用 2 次
        tests_root = Path(self.repo_root) / self.tests_dir
        all_tests = self._collect_test_files(tests_root)
        impacted = self._relate_tests(impacted, all_tests)

        # 5. 推荐测试用例（去重）
        recommended: List[str] = []
        seen: Set[str] = set()
        for m in impacted:
            for t in m.related_tests:
                if t not in seen:
                    seen.add(t)
                    recommended.append(t)
        # 变更文件本身对应的测试也加入（复用已收集的 all_tests，不再重复收集）
        for f in changed_files:
            for t in self._find_tests_for_module(f.module_path, all_tests):
                if t not in seen:
                    seen.add(t)
                    recommended.append(t)

        # 6. 风险统计
        risk_summary = {"high": 0, "medium": 0, "low": 0}
        for m in impacted:
            risk_summary[m.risk_level] = risk_summary.get(m.risk_level, 0) + 1

        elapsed_ms = (time.perf_counter() - total_start) * 1000
        report = ImpactReport(
            trace_id=self.trace_id,
            base_ref=self.base_ref,
            head_ref=self.head_ref,
            changed_files=changed_files,
            impacted_modules=impacted,
            recommended_tests=recommended,
            risk_summary=risk_summary,
            duration_ms=elapsed_ms,
        )

        self._log_action(
            "analyze_complete",
            f"分析完成: changed={len(changed_files)}, "
            f"impacted={len(impacted)}, tests={len(recommended)}, "
            f"duration_ms={elapsed_ms:.2f}",
            duration_ms=elapsed_ms,
        )
        # 埋点预留：变更影响分析完成
        self._track_event(
            "impact_analysis_completed",
            {
                "changed_files": len(changed_files),
                "impacted_modules": len(impacted),
                "high_risk": risk_summary.get("high", 0),
            },
        )

        return report

    # ── git 操作 ──────────────────────────────────────────────

    def _get_changed_files(self) -> List[ChangedFile]:
        """获取 git diff 变更文件列表

        Returns:
            变更文件列表

        Raises:
            ImpactAnalysisError: git 命令失败时抛出
        """
        # 使用 --numstat 获取行数统计，--name-status 获取状态
        cmd = [
            "git",
            "diff",
            "--numstat",
            f"{self.base_ref}...{self.head_ref}",
        ]
        try:
            result = subprocess.run(
                cmd,
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except FileNotFoundError as e:
            raise ImpactAnalysisError(
                "git 命令不可用，请确认 git 已安装",
                error_code="IMPACT_GIT_NOT_FOUND",
            ) from e
        except subprocess.TimeoutExpired as e:
            raise ImpactAnalysisError(
                "git diff 执行超时",
                error_code="IMPACT_GIT_TIMEOUT",
            ) from e

        if result.returncode != 0:
            # 基准不存在（如首次 PR），降级为空变更列表
            stderr = result.stderr.strip()
            if "unknown revision" in stderr or "bad revision" in stderr:
                self._log_action(
                    "git_fallback",
                    f"基准 ref 不存在: {self.base_ref}，返回空变更列表",
                )
                return []
            raise ImpactAnalysisError(
                f"git diff 失败: {stderr}",
                error_code="IMPACT_GIT_FAIL",
            )

        # 解析 --numstat 输出：每行 "insertions\tdeletions\tpath"
        files: List[ChangedFile] = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            ins_str, del_str, path = parts[0], parts[1], parts[2]
            # 二进制文件显示为 "-"
            try:
                insertions = int(ins_str) if ins_str != "-" else 0
                deletions = int(del_str) if del_str != "-" else 0
            except ValueError:
                insertions = deletions = 0

            # 获取状态（A/M/D/R）
            status = self._get_file_status(path)

            # 仅保留 .py 文件
            if not path.endswith(".py"):
                continue

            module_path = self._path_to_module(path)
            files.append(
                ChangedFile(
                    path=path,
                    status=status,
                    module_path=module_path,
                    insertions=insertions,
                    deletions=deletions,
                )
            )

        return files

    def _get_file_status(self, path: str) -> str:
        """获取单个文件的变更状态（A/M/D/R）"""
        cmd = [
            "git",
            "diff",
            "--name-status",
            f"{self.base_ref}...{self.head_ref}",
            "--",
            path,
        ]
        try:
            result = subprocess.run(
                cmd,
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return "M"
        if result.returncode != 0 or not result.stdout.strip():
            return "M"
        line = result.stdout.strip().split("\n")[0]
        status = line.split("\t")[0]
        # R100 / R090 等重命名状态取首字母
        return status[0] if status else "M"

    # ── 影响反查 ──────────────────────────────────────────────

    def _find_impacted_modules(
        self,
        changed: Set[str],
        edges: List[DependencyEdge],
        nodes: Dict,
    ) -> List[ImpactedModule]:
        """反查受影响模块

        - 上游（upstream）：依赖变更模块的模块
        - 下游（downstream）：变更模块依赖的模块

        Args:
            changed: 变更模块点路径集合
            edges: 依赖边
            nodes: 依赖节点

        Returns:
            受影响模块列表
        """
        impacted: List[ImpactedModule] = []
        seen: Set[str] = set()

        # 上游反查：谁依赖了变更模块（source → target=changed）
        for edge in edges:
            if edge.target in changed and edge.source not in changed:
                if edge.source in seen:
                    continue
                seen.add(edge.source)
                risk = self._assess_risk(edge.source, "upstream", edge)
                impacted.append(
                    ImpactedModule(
                        module_path=edge.source,
                        impact_type="upstream",
                        impact_chain=f"{edge.source} → {edge.target}",
                        risk_level=risk,
                        reason=f"依赖了变更模块 {edge.target}",
                    )
                )

        # 下游反查：变更模块依赖了谁（source=changed → target）
        for edge in edges:
            if edge.source in changed and edge.target not in changed:
                if edge.target in seen:
                    continue
                seen.add(edge.target)
                risk = self._assess_risk(edge.target, "downstream", edge)
                impacted.append(
                    ImpactedModule(
                        module_path=edge.target,
                        impact_type="downstream",
                        impact_chain=f"{edge.source} → {edge.target}",
                        risk_level=risk,
                        reason=f"被变更模块 {edge.source} 依赖",
                    )
                )

        return impacted

    @staticmethod
    def _assess_risk(module: str, impact_type: str, edge: DependencyEdge) -> str:
        """评估风险等级

        规则：
        - 跨层调用 → high
        - 违规调用 → high
        - 动态 import → medium
        - 普通同层依赖 → low
        """
        if edge.is_violation:
            return "high"
        if edge.is_cross_layer:
            return "medium"
        if edge.is_dynamic:
            return "medium"
        return "low"

    # ── 测试关联 ──────────────────────────────────────────────

    def _relate_tests(
        self, impacted: List[ImpactedModule],
        all_tests: Optional[List[Path]] = None
    ) -> List[ImpactedModule]:
        """为受影响模块关联测试用例

        Args:
            impacted: 受影响模块列表
            all_tests: 预收集的测试文件列表（未提供则实时收集）
        """
        # 修复：接受外部传入的 all_tests，避免与 analyze() 重复收集
        if all_tests is None:
            tests_root = Path(self.repo_root) / self.tests_dir
            all_tests = self._collect_test_files(tests_root)

        for m in impacted:
            m.related_tests = self._find_tests_for_module(m.module_path, all_tests)

        return impacted

    def _collect_test_files(self, tests_root: Path) -> List[Path]:
        """收集所有测试文件"""
        if not tests_root.exists():
            return []
        return list(tests_root.rglob("test_*.py"))

    def _find_tests_for_module(
        self, module_path: str, all_tests: Optional[List[Path]] = None
    ) -> List[str]:
        """根据模块点路径匹配关联测试用例

        匹配规则：
        1. 模块最后一段名称出现在测试文件名中
        2. 模块所属层出现在测试文件名中

        Args:
            module_path: 模块点路径（agent.orchestrator.core）
            all_tests: 测试文件列表（未提供则实时收集）

        Returns:
            匹配的测试文件相对路径列表

        跨平台兼容：
            module_path 支持点分隔（agent.core.sub）、反斜杠（agent\\core\\sub）、
            正斜杠（agent/core/sub）以及混合分隔符（agent\\core/sub）。
            归一化时统一转换为点分隔，避免 Windows/Linux 路径差异导致匹配失败。
        """
        # 跨平台路径分隔符归一化：将 \ 和 / 统一替换为 .
        # 注意：必须先处理反斜杠再处理正斜杠，避免在 Windows 上重复替换
        normalized_path = module_path.replace("\\", ".").replace("/", ".")
        parts = normalized_path.split(".")
        if len(parts) < 2:
            return []

        # 候选关键词：模块短名 + 所属层
        short_name = parts[-1]
        layer = parts[1] if len(parts) > 1 else ""

        # 修复：空字符串匹配防护
        # 当 module_path 含空段（如 "agent..core"）时，short_name 或 layer 为空字符串，
        # "" in fname_lower 始终返回 True，会导致匹配所有测试文件。
        # 此处过滤空字符串，仅用非空关键词匹配。
        short_name_lower = short_name.lower() if short_name else ""
        layer_lower = layer.lower() if layer else ""

        if all_tests is None:
            tests_root = Path(self.repo_root) / self.tests_dir
            all_tests = self._collect_test_files(tests_root)

        matched: List[str] = []
        seen: Set[str] = set()
        for test_file in all_tests:
            fname = test_file.stem  # test_xxx
            fname_lower = fname.lower()
            # 匹配模块短名或层名（跳过空关键词，避免误匹配所有文件）
            if (
                (short_name_lower and short_name_lower in fname_lower)
                or (layer_lower and layer_lower in fname_lower)
            ):
                rel = str(
                    test_file.relative_to(self.repo_root)
                ).replace("\\", "/")
                if rel not in seen:
                    seen.add(rel)
                    matched.append(rel)
        return matched

    # ── 工具方法 ──────────────────────────────────────────────

    def _path_to_module(self, rel_path: str) -> str:
        """将文件相对路径转换为模块点路径"""
        # 统一路径分隔符
        normalized = rel_path.replace("\\", "/")
        # 去除 .py 后缀
        if normalized.endswith(".py"):
            normalized = normalized[:-3]
        # __init__.py 表示包本身
        parts = normalized.split("/")
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        return ".".join(parts)

    def _track_event(self, event_name: str, payload: Dict[str, Any]) -> None:
        """埋点预留 — 关键节点事件追踪占位符

        实际接入时替换为 agent/monitoring/business_metrics.py 的埋点调用。
        """
        logger.debug(
            "[ImpactAnalysis] trackEvent(%s, %s)", event_name, payload
        )

    def _log_action(
        self, action: str, message: str, duration_ms: Optional[float] = None
    ) -> None:
        """输出结构化日志"""
        log_data = {
            "trace_id": self.trace_id,
            "module_name": "impact_analysis",
            "action": action,
            "message": message,
        }
        if duration_ms is not None:
            log_data["duration_ms"] = round(duration_ms, 2)
        logger.info(
            "[ImpactAnalysis] %s", json.dumps(log_data, ensure_ascii=False)
        )


# ── CLI 入口 ──────────────────────────────────────────────────


def main() -> int:
    """CLI 入口：执行变更影响分析

    Usage:
        python scripts/impact_analysis.py --base origin/main --head HEAD
        python scripts/impact_analysis.py --base origin/main --head HEAD \\
            --output impact_report.md --json-report impact_report.json
    """
    parser = argparse.ArgumentParser(description="变更影响分析（基于 git diff + 依赖图）")
    parser.add_argument("--base", default="origin/main", help="基准 ref（默认 origin/main）")
    parser.add_argument("--head", default="HEAD", help="目标 ref（默认 HEAD）")
    parser.add_argument("--root", default="agent", help="源码根目录")
    parser.add_argument("--tests-dir", default="tests", help="测试目录")
    parser.add_argument("--output", default="", help="Markdown 报告输出路径")
    parser.add_argument("--json-report", default="", help="JSON 报告输出路径")
    parser.add_argument(
        "--github-comment",
        action="store_true",
        help="输出 GitHub PR 评论格式（用于 CI）",
    )
    args = parser.parse_args()

    try:
        analyzer = ImpactAnalyzer(
            base_ref=args.base,
            head_ref=args.head,
            root_dir=args.root,
            tests_dir=args.tests_dir,
        )
        report = analyzer.analyze()

        markdown = report.to_markdown()
        print(markdown)

        if args.output:
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_text(markdown, encoding="utf-8")
            print(f"\n✓ Markdown 报告已生成: {args.output}")

        if args.json_report:
            Path(args.json_report).parent.mkdir(parents=True, exist_ok=True)
            Path(args.json_report).write_text(
                json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"✓ JSON 报告已生成: {args.json_report}")

        # GitHub PR 评论格式输出（CI 中通过 >> $GITHUB_STEP_SUMMARY 使用）
        if args.github_comment:
            print("\n::set-output name=has_impact::" + str(report.has_impact))
            print(
                f"::set-output name=impacted_count::{len(report.impacted_modules)}"
            )

        return 0

    except ImpactAnalysisError as e:
        print(f"✗ 分析失败 [{e.error_code}]: {e.message}")
        return 2
    except Exception as e:
        print(f"✗ 未知异常: {e}")
        logger.exception("[ImpactAnalysis] 未知异常")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
