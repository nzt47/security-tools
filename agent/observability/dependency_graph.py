"""模块依赖图生成器 — 架构影响可见性核心模块

使用 Python ast 模块静态解析 agent/ 下所有 .py 文件的 import 关系，
构建模块依赖图，输出 JSON 格式依赖关系与 Mermaid 拓扑图。

功能：
- 静态解析 import / from...import 关系
- 识别跨层调用（orchestrator→dao、cognitive→server_routes 等）
- 结合 lazy_loader_async.py 白名单识别动态 import
- 输出 JSON 依赖关系（节点+边+统计）
- 输出 Mermaid 拓扑图（跨层调用高亮标红）
- 性能要求：扫描全项目 < 5 秒

可观测性约束实现：
- 结构化日志：所有核心节点输出 JSON 日志（trace_id/module_name/action/duration_ms）
- 边界显性化：解析失败、文件缺失等抛出带业务错误码的异常
- 健康检查：提供 health() 方法返回构建器状态

使用示例：
```python
from agent.observability.dependency_graph import DependencyGraphBuilder

builder = DependencyGraphBuilder(root_dir="agent")
graph = builder.build()
json_data = builder.to_json()
mermaid_str = builder.to_mermaid()
builder.write_mermaid_to_file("docs/architecture/module_dependency_graph.md")
```
"""
from __future__ import annotations

import ast
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# 模块层名 → 所属层映射（用于跨层调用识别）
# 项目没有独立 dao 目录，data 目录承担数据访问层职责
DEFAULT_LAYER_MAPPING: Dict[str, str] = {
    "orchestrator": "orchestrator",
    "cognitive": "cognitive",
    "server_routes": "server_routes",
    "tools": "tools",
    "memory": "memory",
    "data": "dao",  # data 目录作为数据访问层
    "monitoring": "monitoring",
    "observability": "observability",
    "guardrails": "guardrails",
    "extensions": "extensions",
    "utils": "utils",
    "caching": "caching",
    "network": "network",
    "audit": "audit",
    "health": "health",
    "human_in_the_loop": "human_in_the_loop",
    "lazy_loader": "lazy_loader",
    "log_system": "log_system",
    "model_router": "model_router",
    "p6": "p6",
    "prompt_manager": "prompt_manager",
    "quality": "quality",
    "subagent": "subagent",
    "task_planner": "task_planner",
    "web": "web",
    "workflow_engine": "workflow_engine",
}

# 跨层调用违规规则定义：(源层, 目标层) → 违规描述
# 这些规则定义了禁止的跨层调用方向
CROSS_LAYER_VIOLATIONS: Dict[Tuple[str, str], str] = {
    ("orchestrator", "dao"): "禁止 orchestrator 直接访问 dao 层，应通过 service 中转",
    ("cognitive", "server_routes"): "禁止 cognitive 直接访问 server_routes，应通过 orchestrator 协调",
    ("cognitive", "dao"): "禁止 cognitive 直接访问 dao 层",
    ("tools", "dao"): "禁止 tools 直接访问 dao 层",
    ("guardrails", "server_routes"): "禁止 guardrails 直接访问 server_routes",
}


class DependencyGraphError(Exception):
    """依赖图构建异常 — 带业务错误码"""

    def __init__(self, message: str, error_code: str = "DEP_GRAPH_ERROR"):
        super().__init__(message)
        self.error_code = error_code
        self.message = message


@dataclass
class DependencyEdge:
    """依赖边 — 描述两个模块之间的 import 关系"""

    source: str  # 源模块点路径（agent.orchestrator.core）
    target: str  # 目标模块点路径（agent.data.repository）
    source_layer: str  # 源层
    target_layer: str  # 目标层
    import_type: str  # import 类型：import / from_import / dynamic
    line: int  # import 语句所在行号
    source_file: str  # 源文件相对路径
    is_cross_layer: bool = False  # 是否跨层调用
    is_violation: bool = False  # 是否违反架构规则
    violation_desc: str = ""  # 违规描述
    is_dynamic: bool = False  # 是否动态 import（在 lazy_loader 白名单中）

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DependencyNode:
    """依赖节点 — 描述一个模块"""

    path: str  # 模块点路径
    name: str  # 模块短名
    layer: str  # 所属层
    file_path: str  # 文件相对路径
    in_degree: int = 0  # 入度（被依赖次数）
    out_degree: int = 0  # 出度（依赖他人次数）

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class GraphStats:
    """依赖图统计信息"""

    total_files: int = 0  # 扫描文件总数
    total_nodes: int = 0  # 节点总数
    total_edges: int = 0  # 边总数
    cross_layer_edges: int = 0  # 跨层调用边数
    violation_edges: int = 0  # 违规边数
    dynamic_edges: int = 0  # 动态 import 边数
    layers: Dict[str, int] = field(default_factory=dict)  # 各层模块数
    build_duration_ms: float = 0.0  # 构建耗时

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class DependencyGraphBuilder:
    """模块依赖图构建器

    使用 ast 静态解析 import 关系，构建模块依赖图。
    支持跨层调用识别、动态 import 白名单、JSON/Mermaid 输出。
    """

    def __init__(
        self,
        root_dir: str = "agent",
        trace_id: Optional[str] = None,
        layer_mapping: Optional[Dict[str, str]] = None,
        cross_layer_rules: Optional[Dict[Tuple[str, str], str]] = None,
    ):
        """初始化依赖图构建器

        Args:
            root_dir: 扫描根目录（agent/）
            trace_id: 链路追踪 ID，未提供则自动生成
            layer_mapping: 层级映射，未提供则使用默认
            cross_layer_rules: 跨层违规规则，未提供则使用默认

        Raises:
            DependencyGraphError: 根目录不存在时抛出
        """
        self.root_dir = Path(root_dir).resolve()
        if not self.root_dir.exists():
            raise DependencyGraphError(
                f"扫描根目录不存在: {self.root_dir}",
                error_code="DEP_GRAPH_ROOT_NOT_FOUND",
            )

        self.trace_id = trace_id or uuid.uuid4().hex[:16]
        self.layer_mapping = layer_mapping or DEFAULT_LAYER_MAPPING
        self.cross_layer_rules = cross_layer_rules or CROSS_LAYER_VIOLATIONS

        self.nodes: Dict[str, DependencyNode] = {}
        self.edges: List[DependencyEdge] = []
        self.stats = GraphStats()

        # 动态 import 白名单（从 lazy_loader 提取）
        self._dynamic_whitelist: Set[str] = set()
        # 顶层包名（用于过滤非项目内 import）
        self._package_root = self.root_dir.name  # agent

        self._log_action(
            "init",
            f"依赖图构建器初始化: root={self.root_dir}, trace_id={self.trace_id}",
        )

    # ── 公开接口 ──────────────────────────────────────────────────

    def load_lazy_loader_whitelist(self) -> Set[str]:
        """从 lazy_loader_async.py 提取动态 import 模块白名单

        解析 register() 调用，提取被注册的模块名，避免动态 import 被误判为缺失依赖。

        Returns:
            动态 import 白名单模块名集合
        """
        start = time.perf_counter()
        whitelist: Set[str] = set()

        # lazy_loader 文件可能位于多个位置
        candidates = [
            self.root_dir / "lazy_loader_async.py",
            self.root_dir / "lazy_loader" / "__init__.py",
            self.root_dir / "lazy_loader" / "_core.py",
        ]

        try:
            for candidate in candidates:
                if not candidate.exists():
                    continue
                content = candidate.read_text(encoding="utf-8")
                # 匹配 register('module_name', ...) 或 register("module_name", ...)
                pattern = re.compile(
                    r"\.register\(\s*['\"]([\w_]+)['\"]",
                    re.MULTILINE,
                )
                matches = pattern.findall(content)
                whitelist.update(matches)

            self._dynamic_whitelist = whitelist
            elapsed_ms = (time.perf_counter() - start) * 1000
            self._log_action(
                "load_whitelist",
                f"加载 lazy_loader 白名单: {len(whitelist)} 个模块",
                duration_ms=elapsed_ms,
            )
            return whitelist

        except OSError as e:
            raise DependencyGraphError(
                f"读取 lazy_loader 文件失败: {e}",
                error_code="DEP_GRAPH_WHITELIST_READ_FAIL",
            ) from e

    def build(self) -> Dict[str, Any]:
        """构建完整依赖图

        扫描 root_dir 下所有 .py 文件，解析 import 关系，识别跨层调用。

        Returns:
            依赖图字典（nodes + edges + stats）

        Raises:
            DependencyGraphError: 解析失败时抛出
        """
        total_start = time.perf_counter()
        self._log_action("build_start", "开始构建依赖图")

        # 1. 加载动态 import 白名单
        try:
            self.load_lazy_loader_whitelist()
        except DependencyGraphError:
            # 白名单加载失败不阻断主流程，仅记录日志
            logger.warning(
                "[DependencyGraph] trace_id=%s 白名单加载失败，跳过动态 import 识别",
                self.trace_id,
            )

        # 2. 扫描所有 .py 文件
        py_files = self._collect_python_files()
        if not py_files:
            raise DependencyGraphError(
                f"未找到任何 Python 文件: {self.root_dir}",
                error_code="DEP_GRAPH_NO_PYTHON_FILES",
            )

        self.stats.total_files = len(py_files)
        self._log_action(
            "collect_files", f"扫描到 {len(py_files)} 个 Python 文件"
        )

        # 3. 解析每个文件的 import
        parse_errors: List[str] = []
        for file_path in py_files:
            try:
                edges = self._parse_imports(file_path)
                self.edges.extend(edges)
            except DependencyGraphError as e:
                parse_errors.append(f"{file_path}: {e.message}")
                # 单文件解析失败不阻断整体流程

        # 4. 构建节点（从边的源/目标聚合）
        self._build_nodes()

        # 5. 计算统计信息
        self.stats.total_nodes = len(self.nodes)
        self.stats.total_edges = len(self.edges)
        self.stats.cross_layer_edges = sum(
            1 for e in self.edges if e.is_cross_layer
        )
        self.stats.violation_edges = sum(
            1 for e in self.edges if e.is_violation
        )
        self.stats.dynamic_edges = sum(
            1 for e in self.edges if e.is_dynamic
        )
        self.stats.build_duration_ms = (time.perf_counter() - total_start) * 1000

        self._log_action(
            "build_complete",
            (
                f"依赖图构建完成: nodes={self.stats.total_nodes}, "
                f"edges={self.stats.total_edges}, "
                f"violations={self.stats.violation_edges}, "
                f"duration_ms={self.stats.build_duration_ms:.2f}"
            ),
            duration_ms=self.stats.build_duration_ms,
        )

        if parse_errors:
            logger.warning(
                "[DependencyGraph] trace_id=%s %d 个文件解析失败: %s",
                self.trace_id,
                len(parse_errors),
                parse_errors[:3],
            )

        return self.to_json()

    def to_json(self) -> Dict[str, Any]:
        """输出 JSON 格式依赖关系

        Returns:
            包含 nodes/edges/stats 的字典
        """
        return {
            "trace_id": self.trace_id,
            "root_dir": str(self.root_dir),
            "nodes": [n.to_dict() for n in self.nodes.values()],
            "edges": [e.to_dict() for e in self.edges],
            "stats": self.stats.to_dict(),
        }

    def to_mermaid(self) -> str:
        """生成 Mermaid 拓扑图字符串

        跨层违规调用用红色标出（classDef violation + linkStyle 红色粗线），
        普通跨层调用用黄色虚线，普通依赖用灰色实线。

        Returns:
            Mermaid flowchart 语法字符串
        """
        lines: List[str] = [
            "# 模块依赖图（自动生成）",
            "",
            "```mermaid",
            "flowchart LR",
        ]

        # 节点样式定义：违规目标节点红色背景，跨层目标节点黄色背景
        lines.append("    classDef violation fill:#ff4444,stroke:#cc0000,color:#fff,stroke-width:2px")
        lines.append("    classDef crosslayer fill:#fff3cd,stroke:#ffc107,color:#664d03")

        # 按 layer 分组声明 subgraph
        layer_nodes: Dict[str, List[str]] = {}
        violation_targets: Set[str] = set()
        crosslayer_targets: Set[str] = set()
        for edge in self.edges:
            if edge.is_violation:
                violation_targets.add(edge.target)
            elif edge.is_cross_layer:
                crosslayer_targets.add(edge.target)

        for node_path, node in self.nodes.items():
            layer_nodes.setdefault(node.layer, []).append(node_path)

        for layer, node_paths in sorted(layer_nodes.items()):
            safe_layer = layer.replace("-", "_")
            lines.append(f"    subgraph {safe_layer} [{layer}]")
            for np in sorted(node_paths):
                safe_id = self._safe_mermaid_id(np)
                # 违规目标节点应用红色样式，跨层目标节点应用黄色样式
                if np in violation_targets:
                    lines.append(f'        {safe_id}["{np}"]:::violation')
                elif np in crosslayer_targets:
                    lines.append(f'        {safe_id}["{np}"]:::crosslayer')
                else:
                    lines.append(f'        {safe_id}["{np}"]')
            lines.append("    end")

        # 输出边（记录违规边的索引用于 linkStyle）
        edge_index = 0
        violation_edge_indices: List[int] = []
        for edge in self.edges:
            src = self._safe_mermaid_id(edge.source)
            tgt = self._safe_mermaid_id(edge.target)
            if edge.is_violation:
                # 跨层违规调用：粗线 + 违规标签
                lines.append(f'    {src} ==>|违规| {tgt}')
                violation_edge_indices.append(edge_index)
            elif edge.is_cross_layer:
                # 普通跨层调用：虚线
                lines.append(f"    {src} -.-> {tgt}")
            else:
                # 普通依赖：实线
                lines.append(f"    {src} --> {tgt}")
            edge_index += 1

        # 给违规边应用红色样式（linkStyle 按边声明顺序索引）
        for idx in violation_edge_indices:
            lines.append(
                f"    linkStyle {idx} stroke:#ff0000,stroke-width:3px,color:#cc0000"
            )

        lines.append("```")
        lines.append("")
        lines.append("## 图例说明")
        lines.append("- `-->` : 普通依赖（灰色实线）")
        lines.append("- `-.->` : 跨层调用（允许但需关注，黄色虚线）")
        lines.append("- `==>|违规|` : 跨层违规调用（红色粗线，目标节点红色背景，需修复）")
        lines.append("")

        # 统计信息
        lines.append("## 统计信息")
        lines.append(f"- 扫描文件数: {self.stats.total_files}")
        lines.append(f"- 模块节点数: {self.stats.total_nodes}")
        lines.append(f"- 依赖边数: {self.stats.total_edges}")
        lines.append(f"- 跨层调用数: {self.stats.cross_layer_edges}")
        lines.append(f"- 违规调用数: {self.stats.violation_edges}")
        lines.append(f"- 动态 import 数: {self.stats.dynamic_edges}")
        lines.append(f"- 构建耗时: {self.stats.build_duration_ms:.2f} ms")
        lines.append("")

        # 违规清单
        violations = [e for e in self.edges if e.is_violation]
        if violations:
            lines.append("## 违规调用清单")
            for v in violations:
                lines.append(
                    f"- `{v.source}` → `{v.target}` ({v.source_file}:{v.line}) "
                    f"— {v.violation_desc}"
                )
            lines.append("")

        return "\n".join(lines)

    def write_mermaid_to_file(self, output_path: str) -> str:
        """将 Mermaid 拓扑图写入文件

        Args:
            output_path: 输出文件路径（如 docs/architecture/module_dependency_graph.md）

        Returns:
            实际写入的文件路径

        Raises:
            DependencyGraphError: 写入失败时抛出
        """
        output_file = Path(output_path)
        try:
            output_file.parent.mkdir(parents=True, exist_ok=True)
            content = self.to_mermaid()
            output_file.write_text(content, encoding="utf-8")
            self._log_action(
                "write_mermaid",
                f"Mermaid 拓扑图已写入: {output_file}",
            )
            return str(output_file)
        except OSError as e:
            raise DependencyGraphError(
                f"写入 Mermaid 文件失败: {e}",
                error_code="DEP_GRAPH_WRITE_FAIL",
            ) from e

    def write_json_to_file(self, output_path: str) -> str:
        """将 JSON 依赖关系写入文件

        Args:
            output_path: 输出文件路径

        Returns:
            实际写入的文件路径

        Raises:
            DependencyGraphError: 写入失败时抛出
        """
        output_file = Path(output_path)
        try:
            output_file.parent.mkdir(parents=True, exist_ok=True)
            output_file.write_text(
                json.dumps(self.to_json(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return str(output_file)
        except (OSError, TypeError) as e:
            raise DependencyGraphError(
                f"写入 JSON 文件失败: {e}",
                error_code="DEP_GRAPH_JSON_WRITE_FAIL",
            ) from e

    def health(self) -> Dict[str, Any]:
        """健康检查 — 返回构建器状态

        Returns:
            健康状态字典
        """
        return {
            "status": "healthy" if self.nodes else "uninitialized",
            "trace_id": self.trace_id,
            "root_dir": str(self.root_dir),
            "root_exists": self.root_dir.exists(),
            "whitelist_loaded": len(self._dynamic_whitelist) > 0,
            "whitelist_size": len(self._dynamic_whitelist),
            "nodes_count": len(self.nodes),
            "edges_count": len(self.edges),
        }

    # ── 内部实现 ──────────────────────────────────────────────────

    def _collect_python_files(self) -> List[Path]:
        """收集 root_dir 下所有 .py 文件（排除 __pycache__、tests）"""
        files: List[Path] = []
        for path in self.root_dir.rglob("*.py"):
            # 排除 __pycache__、tests 目录
            if "__pycache__" in path.parts:
                continue
            if "tests" in path.parts:
                continue
            files.append(path)
        return files

    def _parse_imports(self, file_path: Path) -> List[DependencyEdge]:
        """解析单个 Python 文件的 import 关系

        使用 ast 模块静态解析，识别 import x / from x import y / __import__ 等形式。

        Args:
            file_path: Python 文件路径

        Returns:
            该文件的依赖边列表

        Raises:
            DependencyGraphError: 语法错误或读取失败时抛出
        """
        try:
            content = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            raise DependencyGraphError(
                f"读取文件失败: {file_path}: {e}",
                error_code="DEP_GRAPH_READ_FAIL",
            ) from e

        try:
            tree = ast.parse(content, filename=str(file_path))
        except SyntaxError as e:
            raise DependencyGraphError(
                f"语法错误: {file_path}: {e}",
                error_code="DEP_GRAPH_SYNTAX_ERROR",
            ) from e

        source_module = self._path_to_module(file_path)
        source_layer = self._get_layer(source_module)
        source_rel = str(file_path.relative_to(self.root_dir.parent))

        edges: List[DependencyEdge] = []

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                # import agent.xxx
                for alias in node.names:
                    target = alias.name
                    edge = self._make_edge(
                        source=source_module,
                        target=target,
                        source_layer=source_layer,
                        import_type="import",
                        line=node.lineno,
                        source_file=source_rel,
                    )
                    if edge:
                        edges.append(edge)

            elif isinstance(node, ast.ImportFrom):
                # from agent.xxx import yyy
                # node.level > 0 表示相对 import（from . import x）
                if node.module is None:
                    # 纯相对 import（from . import x），跳过
                    continue
                target = node.module
                edge = self._make_edge(
                    source=source_module,
                    target=target,
                    source_layer=source_layer,
                    import_type="from_import",
                    line=node.lineno,
                    source_file=source_rel,
                )
                if edge:
                    edges.append(edge)

            elif isinstance(node, ast.Call):
                # 识别 __import__('xxx') / importlib.import_module('xxx')
                if isinstance(node.func, ast.Name) and node.func.id == "__import__":
                    target = self._extract_string_arg(node)
                    if target:
                        edge = self._make_edge(
                            source=source_module,
                            target=target,
                            source_layer=source_layer,
                            import_type="dynamic",
                            line=node.lineno,
                            source_file=source_rel,
                            is_dynamic=True,
                        )
                        if edge:
                            edges.append(edge)
                elif (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "import_module"
                ):
                    target = self._extract_string_arg(node)
                    if target:
                        edge = self._make_edge(
                            source=source_module,
                            target=target,
                            source_layer=source_layer,
                            import_type="dynamic",
                            line=node.lineno,
                            source_file=source_rel,
                            is_dynamic=True,
                        )
                        if edge:
                            edges.append(edge)

        return edges

    def _make_edge(
        self,
        source: str,
        target: str,
        source_layer: str,
        import_type: str,
        line: int,
        source_file: str,
        is_dynamic: bool = False,
    ) -> Optional[DependencyEdge]:
        """构造依赖边

        仅保留项目内（agent.xxx）的依赖，过滤标准库与第三方库。

        Args:
            source: 源模块点路径
            target: 目标模块点路径
            source_layer: 源层
            import_type: import 类型
            line: 行号
            source_file: 源文件相对路径
            is_dynamic: 是否动态 import

        Returns:
            依赖边对象，非项目内 import 返回 None
        """
        # 仅保留项目内 import（以 agent. 开头或顶层包匹配）
        if not target.startswith(f"{self._package_root}.") and target != self._package_root:
            return None

        target_layer = self._get_layer(target)
        is_cross = source_layer != target_layer and target_layer != "unknown"
        is_violation = False
        violation_desc = ""

        if is_cross:
            rule_key = (source_layer, target_layer)
            if rule_key in self.cross_layer_rules:
                is_violation = True
                violation_desc = self.cross_layer_rules[rule_key]

        # 动态 import 在白名单中则标记，否则保留边
        if is_dynamic:
            target_short = target.split(".")[-1]
            if target_short in self._dynamic_whitelist:
                # 白名单中的动态 import，标记但不算违规
                pass

        return DependencyEdge(
            source=source,
            target=target,
            source_layer=source_layer,
            target_layer=target_layer,
            import_type=import_type,
            line=line,
            source_file=source_file,
            is_cross_layer=is_cross,
            is_violation=is_violation,
            violation_desc=violation_desc,
            is_dynamic=is_dynamic,
        )

    def _build_nodes(self) -> None:
        """从边的源/目标聚合构建节点"""
        node_layers: Dict[str, str] = {}
        node_files: Dict[str, str] = {}

        for edge in self.edges:
            if edge.source not in node_layers:
                node_layers[edge.source] = edge.source_layer
                node_files[edge.source] = edge.source_file
            if edge.target not in node_layers:
                node_layers[edge.target] = edge.target_layer
                node_files[edge.target] = ""

        for module_path, layer in node_layers.items():
            short_name = module_path.split(".")[-1]
            self.nodes[module_path] = DependencyNode(
                path=module_path,
                name=short_name,
                layer=layer,
                file_path=node_files.get(module_path, ""),
            )

        # 计算入度出度
        for edge in self.edges:
            if edge.source in self.nodes:
                self.nodes[edge.source].out_degree += 1
            if edge.target in self.nodes:
                self.nodes[edge.target].in_degree += 1

        # 统计各层模块数
        for node in self.nodes.values():
            self.stats.layers[node.layer] = (
                self.stats.layers.get(node.layer, 0) + 1
            )

    def _path_to_module(self, file_path: Path) -> str:
        """将文件路径转换为模块点路径

        例如 agent/orchestrator/core.py → agent.orchestrator.core
        agent/orchestrator/__init__.py → agent.orchestrator
        """
        try:
            rel = file_path.relative_to(self.root_dir.parent)
        except ValueError:
            rel = file_path
        parts = list(rel.with_suffix("").parts)
        # __init__.py 表示包本身
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        return ".".join(parts)

    def _get_layer(self, module_path: str) -> str:
        """从模块点路径提取所属层

        agent.orchestrator.core → orchestrator
        agent.data.repository → dao（根据 layer_mapping）
        agent.tool_calling → core（根目录文件统一归 core 层）
        """
        parts = module_path.split(".")
        if len(parts) < 2:
            return "unknown"
        # parts[0] 应为 'agent'，parts[1] 为子目录名或根模块名
        subdir = parts[1]
        # agent/ 根目录下的 .py 文件归为 core 层
        if (self.root_dir / f"{subdir}.py").exists():
            return "core"
        return self.layer_mapping.get(subdir, subdir)

    @staticmethod
    def _extract_string_arg(call_node: ast.Call) -> Optional[str]:
        """从函数调用中提取第一个字符串参数"""
        if not call_node.args:
            return None
        first = call_node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value
        return None

    @staticmethod
    def _safe_mermaid_id(name: str) -> str:
        """将模块路径转换为安全的 Mermaid 节点 ID"""
        return name.replace(".", "_")

    def _log_action(
        self, action: str, message: str, duration_ms: Optional[float] = None
    ) -> None:
        """输出结构化日志（满足可观测性强制约束）"""
        log_data = {
            "trace_id": self.trace_id,
            "module_name": "dependency_graph",
            "action": action,
            "message": message,
        }
        if duration_ms is not None:
            log_data["duration_ms"] = round(duration_ms, 2)
        logger.info(
            "[DependencyGraph] %s", json.dumps(log_data, ensure_ascii=False)
        )


# ── CLI 入口 ──────────────────────────────────────────────────


def main() -> int:
    """CLI 入口：生成依赖图并写入文件

    Usage:
        python -m agent.observability.dependency_graph [--root agent] \\
            [--output docs/architecture/module_dependency_graph.md] \\
            [--json-output docs/architecture/dependency_graph.json]
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="生成模块依赖图（JSON + Mermaid）"
    )
    parser.add_argument(
        "--root", default="agent", help="扫描根目录（默认: agent）"
    )
    parser.add_argument(
        "--output",
        default="docs/architecture/module_dependency_graph.md",
        help="Mermaid 输出文件路径",
    )
    parser.add_argument(
        "--json-output",
        default="docs/architecture/dependency_graph.json",
        help="JSON 输出文件路径",
    )
    args = parser.parse_args()

    try:
        builder = DependencyGraphBuilder(root_dir=args.root)
        builder.build()
        mermaid_path = builder.write_mermaid_to_file(args.output)
        json_path = builder.write_json_to_file(args.json_output)
        print(f"✓ Mermaid 拓扑图已生成: {mermaid_path}")
        print(f"✓ JSON 依赖关系已生成: {json_path}")
        print(f"✓ 统计: {builder.stats.total_nodes} 节点, "
              f"{builder.stats.total_edges} 边, "
              f"{builder.stats.violation_edges} 违规")
        return 0
    except DependencyGraphError as e:
        print(f"✗ 构建失败 [{e.error_code}]: {e.message}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
