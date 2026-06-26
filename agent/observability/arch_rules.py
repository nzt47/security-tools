"""架构规则校验器 — ArchUnit 思想的纯 Python 实现

参考 ArchUnit（Java）思想，纯 Python 实现，不引入第三方依赖。
校验模块依赖关系是否符合架构规则，违规时输出结构化报告。

内置规则：
1. 禁止 orchestrator 直接访问 dao 层
2. 禁止 cognitive 直接访问 server_routes
3. 禁止 cognitive 直接访问 dao 层
4. 禁止 tools 直接访问 dao 层
5. 禁止 guardrails 直接访问 server_routes
6. 禁止循环依赖（A→B→A）
7. 禁止 agent/ 下模块直接 import tests/

规则可配置（config.yaml 的 arch_rules 段），支持存量豁免清单。

CLI 入口：
    python -m agent.observability.arch_rules --check
    python -m agent.observability.arch_rules --check --root agent --exemptions docs/architecture/legacy_exemptions.json
    python -m agent.observability.arch_rules --check --json-report arch_report.json

退出码：0=通过，1=违规

可观测性约束实现：
- 结构化日志（trace_id/module_name/action/duration_ms）
- 边界显性化（带业务错误码的异常）
- 健康检查接口
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from agent.observability.dependency_graph import (
    DependencyGraphBuilder,
    DependencyEdge,
    DependencyGraphError,
)

logger = logging.getLogger(__name__)


class ArchRuleError(Exception):
    """架构规则校验异常 — 带业务错误码"""

    def __init__(self, message: str, error_code: str = "ARCH_RULE_ERROR"):
        super().__init__(message)
        self.error_code = error_code
        self.message = message


@dataclass
class Violation:
    """架构规则违规项"""

    rule_id: str  # 规则 ID（如 no_orchestrator_to_dao）
    rule_desc: str  # 规则描述
    source: str  # 违规源模块
    target: str  # 违规目标模块
    source_file: str  # 源文件
    line: int  # 行号
    severity: str  # high / medium / low
    suggestion: str  # 修复建议
    is_exempted: bool = False  # 是否在豁免清单中

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ArchRule:
    """架构规则定义"""

    rule_id: str  # 规则 ID
    desc: str  # 规则描述
    severity: str  # high / medium / low
    suggestion: str  # 修复建议


# ── 内置架构规则定义 ──────────────────────────────────────────

BUILTIN_RULES: Dict[str, ArchRule] = {
    "no_orchestrator_to_dao": ArchRule(
        rule_id="no_orchestrator_to_dao",
        desc="禁止 orchestrator 直接访问 dao 层",
        severity="high",
        suggestion="orchestrator 应通过 service 或 business 层访问数据，"
        "避免业务逻辑与数据访问耦合",
    ),
    "no_cognitive_to_server_routes": ArchRule(
        rule_id="no_cognitive_to_server_routes",
        desc="禁止 cognitive 直接访问 server_routes",
        severity="high",
        suggestion="cognitive 应通过 orchestrator 协调 HTTP 路由，"
        "不应直接依赖表现层",
    ),
    "no_cognitive_to_dao": ArchRule(
        rule_id="no_cognitive_to_dao",
        desc="禁止 cognitive 直接访问 dao 层",
        severity="high",
        suggestion="cognitive 不应直接读写数据，应通过 memory 或 service",
    ),
    "no_tools_to_dao": ArchRule(
        rule_id="no_tools_to_dao",
        desc="禁止 tools 直接访问 dao 层",
        severity="medium",
        suggestion="工具模块应保持无状态，数据访问由上层处理",
    ),
    "no_guardrails_to_server_routes": ArchRule(
        rule_id="no_guardrails_to_server_routes",
        desc="禁止 guardrails 直接访问 server_routes",
        severity="medium",
        suggestion="guardrails 应保持独立，不依赖 HTTP 路由层",
    ),
    "no_circular_dependency": ArchRule(
        rule_id="no_circular_dependency",
        desc="禁止循环依赖（A→B→A）",
        severity="high",
        suggestion="检测到循环依赖，请通过依赖倒置或中间层解耦，"
        "或使用 agent/lazy_loader_async.py 延迟加载",
    ),
    "no_agent_import_tests": ArchRule(
        rule_id="no_agent_import_tests",
        desc="禁止 agent/ 下模块直接 import tests/",
        severity="high",
        suggestion="生产代码不应依赖测试代码，请反转依赖方向",
    ),
}

# 跨层调用规则 → 规则 ID 映射（与 dependency_graph.CROSS_LAYER_VIOLATIONS 对应）
CROSS_LAYER_TO_RULE_ID: Dict[Tuple[str, str], str] = {
    ("orchestrator", "dao"): "no_orchestrator_to_dao",
    ("cognitive", "server_routes"): "no_cognitive_to_server_routes",
    ("cognitive", "dao"): "no_cognitive_to_dao",
    ("tools", "dao"): "no_tools_to_dao",
    ("guardrails", "server_routes"): "no_guardrails_to_server_routes",
}


class ArchRuleValidator:
    """架构规则校验器

    基于依赖图校验架构规则，支持存量豁免、循环依赖检测、tests 反向依赖检测。

    使用示例：
    ```python
    validator = ArchRuleValidator(root_dir="agent")
    report = validator.validate()
    if report.has_violations:
        for v in report.violations:
            print(v)
    ```
    """

    def __init__(
        self,
        root_dir: str = "agent",
        trace_id: Optional[str] = None,
        exemptions_path: Optional[str] = None,
        config_path: Optional[str] = None,
    ):
        """初始化架构规则校验器

        Args:
            root_dir: 扫描根目录
            trace_id: 链路追踪 ID
            exemptions_path: 存量豁免清单路径（JSON）
            config_path: config.yaml 路径（读取 arch_rules 段）

        Raises:
            ArchRuleError: 初始化失败时抛出
        """
        self.trace_id = trace_id or uuid.uuid4().hex[:16]
        self.root_dir = root_dir
        self.exemptions_path = exemptions_path
        self.config_path = config_path

        # 加载豁免清单
        self.exemptions: Set[str] = set()
        if exemptions_path:
            self.exemptions = self._load_exemptions(exemptions_path)

        # 加载配置（可选）
        self.rules: Dict[str, ArchRule] = dict(BUILTIN_RULES)
        if config_path and Path(config_path).exists():
            self._load_config(config_path)

        self._log_action(
            "init",
            f"架构规则校验器初始化: rules={len(self.rules)}, "
            f"exemptions={len(self.exemptions)}",
        )

    # ── 公开接口 ──────────────────────────────────────────────────

    def validate(self) -> "ValidationReport":
        """执行架构规则校验

        Returns:
            校验报告对象
        """
        total_start = time.perf_counter()
        self._log_action("validate_start", "开始架构规则校验")

        # 1. 构建依赖图
        try:
            builder = DependencyGraphBuilder(
                root_dir=self.root_dir, trace_id=self.trace_id
            )
            graph_data = builder.build()
        except DependencyGraphError as e:
            raise ArchRuleError(
                f"依赖图构建失败: {e.message}",
                error_code=f"ARCH_GRAPH_FAIL:{e.error_code}",
            ) from e

        edges: List[DependencyEdge] = builder.edges
        violations: List[Violation] = []

        # 2. 校验跨层调用规则
        cross_violations = self._check_cross_layer_rules(edges)
        violations.extend(cross_violations)
        self._log_action(
            "phase_cross_layer",
            f"跨层规则校验完成: 扫描边={len(edges)}, "
            f"命中违规={len(cross_violations)}",
        )

        # 3. 校验 agent → tests 反向依赖（独立扫描，因依赖图过滤了非项目内 import）
        tests_violations = self._check_agent_import_tests(builder)
        violations.extend(tests_violations)
        self._log_action(
            "phase_tests_reverse",
            f"tests 反向依赖校验完成: 命中违规={len(tests_violations)}",
        )

        # 4. 校验循环依赖
        cycle_violations = self._check_circular_dependencies(edges)
        violations.extend(cycle_violations)
        self._log_action(
            "phase_circular",
            f"循环依赖校验完成: 命中违规={len(cycle_violations)}",
        )

        # 5. 应用豁免清单
        violations = self._apply_exemptions(violations)

        # 6. 统计
        elapsed_ms = (time.perf_counter() - total_start) * 1000
        active_violations = [v for v in violations if not v.is_exempted]
        report = ValidationReport(
            trace_id=self.trace_id,
            root_dir=self.root_dir,
            total_rules=len(self.rules),
            total_violations=len(violations),
            active_violations=len(active_violations),
            exempted_violations=len(violations) - len(active_violations),
            violations=violations,
            graph_stats=graph_data.get("stats", {}),
            duration_ms=elapsed_ms,
        )

        self._log_action(
            "validate_complete",
            f"校验完成: total={len(violations)}, "
            f"active={len(active_violations)}, "
            f"exempted={len(violations) - len(active_violations)}, "
            f"duration_ms={elapsed_ms:.2f}",
            duration_ms=elapsed_ms,
        )

        return report

    def health(self) -> Dict[str, Any]:
        """健康检查"""
        return {
            "status": "healthy",
            "trace_id": self.trace_id,
            "root_dir": self.root_dir,
            "rules_count": len(self.rules),
            "exemptions_count": len(self.exemptions),
            "exemptions_path": self.exemptions_path,
        }

    # ── 校验逻辑 ──────────────────────────────────────────────────

    def _check_cross_layer_rules(
        self, edges: List[DependencyEdge]
    ) -> List[Violation]:
        """校验跨层调用规则（基于依赖图已识别的违规边）"""
        violations: List[Violation] = []
        cross_layer_count = 0
        for edge in edges:
            # 排查日志：记录所有跨层调用（非违规也记录，便于核对规则覆盖）
            if edge.is_cross_layer:
                cross_layer_count += 1
                logger.debug(
                    "[ArchRules] trace_id=%s 跨层调用: %s(%s) -> %s(%s) "
                    "file=%s:%d violation=%s",
                    self.trace_id,
                    edge.source,
                    edge.source_layer,
                    edge.target,
                    edge.target_layer,
                    edge.source_file,
                    edge.line,
                    edge.is_violation,
                )
            if not edge.is_violation:
                continue
            rule_key = (edge.source_layer, edge.target_layer)
            rule_id = CROSS_LAYER_TO_RULE_ID.get(rule_key)
            if not rule_id or rule_id not in self.rules:
                # 规则未注册或未启用，记录便于排查"为何没报违规"
                logger.debug(
                    "[ArchRules] trace_id=%s 违规边但规则未启用: "
                    "rule_key=%s, rule_id=%s",
                    self.trace_id,
                    rule_key,
                    rule_id,
                )
                continue
            rule = self.rules[rule_id]
            logger.info(
                "[ArchRules] trace_id=%s 命中跨层违规: rule=%s, "
                "%s(%s) -> %s(%s), file=%s:%d",
                self.trace_id,
                rule.rule_id,
                edge.source,
                edge.source_layer,
                edge.target,
                edge.target_layer,
                edge.source_file,
                edge.line,
            )
            violations.append(
                Violation(
                    rule_id=rule.rule_id,
                    rule_desc=rule.desc,
                    source=edge.source,
                    target=edge.target,
                    source_file=edge.source_file,
                    line=edge.line,
                    severity=rule.severity,
                    suggestion=rule.suggestion,
                )
            )
        self._log_action(
            "cross_layer_detail",
            f"跨层调用统计: 总跨层边={cross_layer_count}, "
            f"违规边={len(violations)}",
        )
        return violations

    def _check_agent_import_tests(
        self, builder: DependencyGraphBuilder
    ) -> List[Violation]:
        """校验 agent/ 下模块是否直接 import tests/

        独立扫描 agent/ 下所有 .py 文件，用 ast 检测 `import tests` / `from tests`。
        依赖图构建器会过滤非项目内 import（tests 不以 agent. 开头），
        因此此规则需独立扫描。

        Args:
            builder: 依赖图构建器（复用文件收集与 ast 解析能力）

        Returns:
            违规列表
        """
        import ast as _ast

        violations: List[Violation] = []
        rule = self.rules.get("no_agent_import_tests")
        if not rule:
            self._log_action("tests_skip", "no_agent_import_tests 规则未启用，跳过")
            return violations

        scanned_files = 0
        for file_path in builder._collect_python_files():
            scanned_files += 1
            try:
                content = file_path.read_text(encoding="utf-8")
                tree = _ast.parse(content, filename=str(file_path))
            except (OSError, SyntaxError, UnicodeDecodeError) as e:
                logger.debug(
                    "[ArchRules] trace_id=%s 跳过文件解析: %s, err=%s",
                    self.trace_id,
                    file_path,
                    e,
                )
                continue

            source_module = builder._path_to_module(file_path)
            source_rel = str(file_path.relative_to(builder.root_dir.parent))

            for node in _ast.walk(tree):
                if isinstance(node, _ast.Import):
                    for alias in node.names:
                        if alias.name.startswith("tests.") or alias.name == "tests":
                            logger.info(
                                "[ArchRules] trace_id=%s 命中 tests 反向依赖: "
                                "%s -> %s, file=%s:%d",
                                self.trace_id,
                                source_module,
                                alias.name,
                                source_rel,
                                node.lineno,
                            )
                            violations.append(
                                Violation(
                                    rule_id=rule.rule_id,
                                    rule_desc=rule.desc,
                                    source=source_module,
                                    target=alias.name,
                                    source_file=source_rel,
                                    line=node.lineno,
                                    severity=rule.severity,
                                    suggestion=rule.suggestion,
                                )
                            )
                elif isinstance(node, _ast.ImportFrom):
                    if node.module and (
                        node.module.startswith("tests.")
                        or node.module == "tests"
                    ):
                        logger.info(
                            "[ArchRules] trace_id=%s 命中 tests 反向依赖: "
                            "%s -> %s, file=%s:%d",
                            self.trace_id,
                            source_module,
                            node.module,
                            source_rel,
                            node.lineno,
                        )
                        violations.append(
                            Violation(
                                rule_id=rule.rule_id,
                                rule_desc=rule.desc,
                                source=source_module,
                                target=node.module,
                                source_file=source_rel,
                                line=node.lineno,
                                severity=rule.severity,
                                suggestion=rule.suggestion,
                            )
                            )
        self._log_action(
            "tests_scan_detail",
            f"tests 反向依赖扫描完成: 扫描文件={scanned_files}, "
            f"命中违规={len(violations)}",
        )
        return violations

    def _check_circular_dependencies(
        self, edges: List[DependencyEdge]
    ) -> List[Violation]:
        """检测循环依赖（A→B→A）

        使用 DFS + 三色标记法检测环。
        为避免误报，仅在模块点级别检测（而非文件级别），
        且对白名单中的动态 import 边予以放宽。

        Args:
            edges: 依赖边列表

        Returns:
            循环依赖违规列表
        """
        violations: List[Violation] = []
        rule = self.rules.get("no_circular_dependency")
        if not rule:
            self._log_action("circular_skip", "no_circular_dependency 规则未启用，跳过")
            return violations

        # 构建邻接表（模块点 → 目标模块列表）
        adj: Dict[str, List[DependencyEdge]] = {}
        for edge in edges:
            adj.setdefault(edge.source, []).append(edge)

        self._log_action(
            "circular_build_adj",
            f"循环依赖检测: 邻接表节点数={len(adj)}, 边数={len(edges)}",
        )

        # 三色标记：0=未访问, 1=访问中, 2=已完成
        color: Dict[str, int] = {}
        # 记录当前 DFS 路径
        path: List[str] = []
        # 已报告的环（避免重复）
        reported_cycles: Set[Tuple[str, ...]] = set()
        trace_id = self.trace_id

        def dfs(node: str) -> None:
            color[node] = 1
            path.append(node)

            for edge in adj.get(node, []):
                tgt = edge.target
                if tgt not in adj:
                    # 目标无出边，跳过
                    continue
                c = color.get(tgt, 0)
                if c == 1:
                    # 发现环：从 path 中找到环的起点
                    if tgt in path:
                        idx = path.index(tgt)
                        cycle = tuple(path[idx:])
                        if cycle not in reported_cycles:
                            reported_cycles.add(cycle)
                            cycle_str = " → ".join(cycle) + f" → {tgt}"
                            logger.info(
                                "[ArchRules] trace_id=%s 发现循环依赖: %s "
                                "(source=%s -> target=%s, file=%s:%d)",
                                trace_id,
                                cycle_str,
                                node,
                                tgt,
                                edge.source_file,
                                edge.line,
                            )
                            violations.append(
                                Violation(
                                    rule_id=rule.rule_id,
                                    rule_desc=f"{rule.desc}: {cycle_str}",
                                    source=node,
                                    target=tgt,
                                    source_file=edge.source_file,
                                    line=edge.line,
                                    severity=rule.severity,
                                    suggestion=rule.suggestion,
                                )
                            )
                elif c == 0:
                    dfs(tgt)

            path.pop()
            color[node] = 2

        # 对每个节点执行 DFS
        for node in adj:
            if color.get(node, 0) == 0:
                dfs(node)

        self._log_action(
            "circular_detect_detail",
            f"循环依赖检测完成: 发现环={len(violations)}, "
            f"独立环={len(reported_cycles)}",
        )
        return violations

    # ── 豁免与配置 ──────────────────────────────────────────────

    def _apply_exemptions(
        self, violations: List[Violation]
    ) -> List[Violation]:
        """应用存量豁免清单

        豁免键格式："{rule_id}:{source}->{target}"
        对循环依赖（rule_id=no_circular_dependency）双向匹配，
        因 DFS 遍历方向不确定，source/target 可能与豁免清单顺序相反。

        Args:
            violations: 原始违规列表

        Returns:
            标记豁免后的违规列表（保留但标记 is_exempted=True）
        """
        for v in violations:
            key = f"{v.rule_id}:{v.source}->{v.target}"
            if key in self.exemptions:
                v.is_exempted = True
                logger.info(
                    "[ArchRules] trace_id=%s 豁免命中(正向): key=%s, "
                    "rule=%s, %s -> %s",
                    self.trace_id,
                    key,
                    v.rule_id,
                    v.source,
                    v.target,
                )
                continue
            # 循环依赖双向匹配：A->B 等同于 B->A
            if v.rule_id == "no_circular_dependency":
                reverse_key = f"{v.rule_id}:{v.target}->{v.source}"
                if reverse_key in self.exemptions:
                    v.is_exempted = True
                    logger.info(
                        "[ArchRules] trace_id=%s 豁免命中(反向): key=%s, "
                        "rule=%s, %s -> %s",
                        self.trace_id,
                        reverse_key,
                        v.rule_id,
                        v.source,
                        v.target,
                    )
            else:
                # 非循环依赖且未命中豁免，记录便于排查
                logger.debug(
                    "[ArchRules] trace_id=%s 豁免未命中: key=%s",
                    self.trace_id,
                    key,
                )
        exempted_count = sum(1 for v in violations if v.is_exempted)
        self._log_action(
            "exemption_apply_detail",
            f"豁免匹配完成: 总违规={len(violations)}, "
            f"已豁免={exempted_count}, 未豁免={len(violations) - exempted_count}",
        )
        return violations

    @staticmethod
    def _load_exemptions(exemptions_path: str) -> Set[str]:
        """加载存量豁免清单

        Args:
            exemptions_path: 豁免清单 JSON 路径

        Returns:
            豁免键集合

        Raises:
            ArchRuleError: 加载失败时抛出
        """
        path = Path(exemptions_path)
        if not path.exists():
            # 豁免清单不存在视为空清单（非错误）
            logger.warning(
                "[ArchRules] 豁免清单不存在: %s，视为空清单", exemptions_path
            )
            return set()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise ArchRuleError(
                f"加载豁免清单失败: {e}",
                error_code="ARCH_EXEMPTION_LOAD_FAIL",
            ) from e

        exemptions: Set[str] = set()
        for item in data.get("exemptions", []):
            rule_id = item.get("rule_id", "")
            source = item.get("source", "")
            target = item.get("target", "")
            key = f"{rule_id}:{source}->{target}"
            exemptions.add(key)
        return exemptions

    def _load_config(self, config_path: str) -> None:
        """从 config.yaml 加载架构规则配置（可选）

        支持 arch_rules 段：
            arch_rules:
              enabled: true
              rules:
                no_orchestrator_to_dao:
                  severity: high

        Args:
            config_path: config.yaml 路径
        """
        try:
            import yaml  # 延迟导入，避免硬依赖
        except ImportError:
            logger.debug("[ArchRules] yaml 模块未安装，跳过配置加载")
            return

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
        except (OSError, yaml.YAMLError) as e:
            raise ArchRuleError(
                f"加载配置文件失败: {e}",
                error_code="ARCH_CONFIG_LOAD_FAIL",
            ) from e

        arch_config = config.get("arch_rules", {})
        if not arch_config.get("enabled", True):
            # 全局禁用架构规则
            self.rules.clear()
            self._log_action(
                "load_config", "架构规则已被配置禁用"
            )
            return

        # 应用规则覆盖（深拷贝避免污染全局 BUILTIN_RULES）
        import copy
        rules_config = arch_config.get("rules", {})
        for rule_id, override in rules_config.items():
            if rule_id in self.rules and isinstance(override, dict):
                rule = copy.copy(self.rules[rule_id])
                if "severity" in override:
                    rule.severity = override["severity"]
                if "suggestion" in override:
                    rule.suggestion = override["suggestion"]
                self.rules[rule_id] = rule

        self._log_action(
            "load_config",
            f"配置加载完成: rules={len(self.rules)}",
        )

    # ── 工具方法 ──────────────────────────────────────────────────

    def _log_action(
        self, action: str, message: str, duration_ms: Optional[float] = None
    ) -> None:
        """输出结构化日志"""
        log_data = {
            "trace_id": self.trace_id,
            "module_name": "arch_rules",
            "action": action,
            "message": message,
        }
        if duration_ms is not None:
            log_data["duration_ms"] = round(duration_ms, 2)
        logger.info(
            "[ArchRules] %s", json.dumps(log_data, ensure_ascii=False)
        )


@dataclass
class ValidationReport:
    """架构规则校验报告"""

    trace_id: str
    root_dir: str
    total_rules: int
    total_violations: int
    active_violations: int
    exempted_violations: int
    violations: List[Violation] = field(default_factory=list)
    graph_stats: Dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0

    @property
    def has_violations(self) -> bool:
        """是否存在未豁免的违规"""
        return self.active_violations > 0

    @property
    def passed(self) -> bool:
        """校验是否通过（无未豁免违规）"""
        return not self.has_violations

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "root_dir": self.root_dir,
            "passed": self.passed,
            "total_rules": self.total_rules,
            "total_violations": self.total_violations,
            "active_violations": self.active_violations,
            "exempted_violations": self.exempted_violations,
            "violations": [v.to_dict() for v in self.violations],
            "graph_stats": self.graph_stats,
            "duration_ms": round(self.duration_ms, 2),
        }

    def to_markdown(self) -> str:
        """生成 Markdown 格式校验报告"""
        status = "✅ 通过" if self.passed else "❌ 违规"
        lines = [
            "# 架构规则校验报告",
            "",
            f"- **状态**: {status}",
            f"- **Trace ID**: `{self.trace_id}`",
            f"- **扫描根目录**: `{self.root_dir}`",
            f"- **校验规则数**: {self.total_rules}",
            f"- **违规总数**: {self.total_violations}",
            f"- **未豁免违规**: {self.active_violations}",
            f"- **已豁免违规**: {self.exempted_violations}",
            f"- **耗时**: {self.duration_ms:.2f} ms",
            "",
        ]

        if not self.violations:
            lines.append("## ✅ 未发现架构违规")
            return "\n".join(lines)

        # 按严重程度分组
        by_severity: Dict[str, List[Violation]] = {"high": [], "medium": [], "low": []}
        for v in self.violations:
            by_severity.setdefault(v.severity, []).append(v)

        severity_label = {"high": "高", "medium": "中", "low": "低"}
        for sev in ["high", "medium", "low"]:
            items = by_severity.get(sev, [])
            if not items:
                continue
            lines.append(f"## {severity_label.get(sev, sev)} 严重度（{len(items)} 项）")
            lines.append("")
            lines.append("| 规则 | 源模块 | 目标模块 | 文件:行 | 状态 | 建议 |")
            lines.append("|------|--------|----------|---------|------|------|")
            for v in items:
                status = "🚫 豁免" if v.is_exempted else "❌ 违规"
                location = f"{v.source_file}:{v.line}"
                lines.append(
                    f"| {v.rule_id} | `{v.source}` | `{v.target}` | "
                    f"{location} | {status} | {v.suggestion} |"
                )
            lines.append("")

        return "\n".join(lines)


# ── CLI 入口 ──────────────────────────────────────────────────


def main() -> int:
    """CLI 入口：执行架构规则校验

    Usage:
        python -m agent.observability.arch_rules --check
        python -m agent.observability.arch_rules --check --root agent \\
            --exemptions docs/architecture/legacy_exemptions.json \\
            --json-report arch_report.json
    """
    parser = argparse.ArgumentParser(description="架构规则校验器（ArchUnit 风格）")
    parser.add_argument("--check", action="store_true", help="执行架构规则校验")
    parser.add_argument("--root", default="agent", help="扫描根目录（默认: agent）")
    parser.add_argument(
        "--exemptions",
        default="docs/architecture/legacy_exemptions.json",
        help="存量豁免清单 JSON 路径",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="配置文件路径（默认: config.yaml）",
    )
    parser.add_argument(
        "--json-report",
        default="",
        help="JSON 报告输出路径（可选）",
    )
    parser.add_argument(
        "--md-report",
        default="",
        help="Markdown 报告输出路径（可选）",
    )
    args = parser.parse_args()

    if not args.check:
        parser.print_help()
        return 0

    try:
        validator = ArchRuleValidator(
            root_dir=args.root,
            exemptions_path=args.exemptions,
            config_path=args.config,
        )
        report = validator.validate()

        # 输出到控制台
        print(report.to_markdown())

        # 输出 JSON 报告
        if args.json_report:
            Path(args.json_report).parent.mkdir(parents=True, exist_ok=True)
            Path(args.json_report).write_text(
                json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"\n✓ JSON 报告已生成: {args.json_report}")

        # 输出 Markdown 报告
        if args.md_report:
            Path(args.md_report).parent.mkdir(parents=True, exist_ok=True)
            Path(args.md_report).write_text(
                report.to_markdown(), encoding="utf-8"
            )
            print(f"✓ Markdown 报告已生成: {args.md_report}")

        return 0 if report.passed else 1

    except ArchRuleError as e:
        print(f"✗ 校验失败 [{e.error_code}]: {e.message}")
        return 2
    except Exception as e:
        print(f"✗ 未知异常: {e}")
        logger.exception("[ArchRules] 校验过程发生未知异常")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
