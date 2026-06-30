#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
边界覆盖扫描脚本

【生成日志摘要】
- 生成时间：2026-06-26
- 版本：v1.0.0
- 模型配置：GLM-5.2
- 内容：扫描 tests/ 下测试函数名中的边界关键词，按模块统计覆盖率，
       输出 Markdown + JSON 报告，CI 阻断新增模块无边界测试的情况。

【可观测性约束】
- 结构化日志：包含 trace_id / module_name / action / duration_ms
- 边界显性化：未覆盖边界条件的模块以 ✅/⚠️/❌ 三态显性化
- 异常处理：报告生成失败时输出降级报告

用法：
    python scripts/check_boundary_coverage.py
    python scripts/check_boundary_coverage.py --config tests/boundary_config.yaml --strict
    python scripts/check_boundary_coverage.py --json-only
"""

from __future__ import annotations

import argparse
import ast
import json
import logging
import os
import sys
import time
import traceback
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# ── 项目根目录推断 ──
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent

# ── Windows 编码修复 ──
# Windows 默认 stdout/stderr 使用 GBK 编码，输出 emoji（✅/⚠️/❌）时会触发
# 'gbk' codec can't encode character '\u26a0' 异常，导致子进程 returncode=2
# visibility_report.py 判定 returncode 非 0/1 为异常，降级返回 0.0%
# 修复：强制 stdout/stderr 使用 UTF-8 编码，errors='replace' 确保不崩溃
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, OSError):
        # Python < 3.7 或重定向环境无 reconfigure 方法，降级忽略
        pass

# ── 日志配置（结构化 JSON 输出） ──
logger = logging.getLogger("boundary_coverage")


def _setup_logging(verbose: bool = False) -> None:
    """配置结构化日志"""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s [%(levelname)8s] %(name)-30s: %(message)s',
        datefmt='%H:%M:%S',
    )


def _trace_id() -> str:
    """生成简易 trace_id（无第三方依赖）"""
    import uuid
    return uuid.uuid4().hex[:16]


# ═══════════════════════════════════════════════════════════════
#  数据结构
# ═══════════════════════════════════════════════════════════════

@dataclass
class TestCase:
    """单个测试用例"""
    name: str
    file_path: str
    matched_keywords: List[str] = field(default_factory=list)
    is_boundary: bool = False


@dataclass
class ModuleReport:
    """模块级报告"""
    module_name: str
    description: str = ""
    total_tests: int = 0
    boundary_tests: int = 0
    covered_scenes: Set[str] = field(default_factory=set)
    required_scenes: List[str] = field(default_factory=list)
    min_tests: int = 0
    status: str = "✅"  # ✅ 通过 / ⚠️ 建议补充 / ❌ 阻断
    is_new_module: bool = False
    missing_scenes: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)
    test_cases: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class ScanResult:
    """整体扫描结果"""
    trace_id: str
    timestamp: str
    duration_ms: float
    total_modules: int
    total_tests: int
    total_boundary_tests: int
    modules: List[ModuleReport]
    blocked_modules: List[str]
    overall_status: str  # pass / warn / fail
    error: Optional[str] = None


# ═══════════════════════════════════════════════════════════════
#  配置加载
# ═══════════════════════════════════════════════════════════════

class ConfigLoader:
    """边界覆盖配置加载器"""

    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.config: Dict[str, Any] = {}

    def load(self) -> Dict[str, Any]:
        """加载 YAML 配置，失败时抛出明确异常（边界显性化原则）"""
        if not self.config_path.exists():
            raise FileNotFoundError(
                f"边界覆盖配置文件不存在: {self.config_path} "
                f"[error_code=BOUNDARY_CONFIG_NOT_FOUND]"
            )
        try:
            import yaml
        except ImportError as e:
            raise ImportError(
                "PyYAML 未安装，请运行: pip install pyyaml "
                "[error_code=DEPENDENCY_MISSING]"
            ) from e

        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                self.config = yaml.safe_load(f) or {}
        except yaml.YAMLError as e:
            raise ValueError(
                f"YAML 解析失败: {e} [error_code=BOUNDARY_CONFIG_PARSE_ERROR]"
            ) from e

        # 校验必要字段
        self._validate()
        return self.config

    def _validate(self) -> None:
        """校验配置完整性"""
        required_keys = ["global", "modules", "ci_policy"]
        for key in required_keys:
            if key not in self.config:
                raise ValueError(
                    f"配置缺少必要字段: {key} "
                    f"[error_code=BOUNDARY_CONFIG_INCOMPLETE]"
                )
        if "keywords" not in self.config["global"]:
            raise ValueError(
                "global.keywords 字段缺失 "
                "[error_code=BOUNDARY_CONFIG_NO_KEYWORDS]"
            )


# ═══════════════════════════════════════════════════════════════
#  测试文件解析
# ═══════════════════════════════════════════════════════════════

class TestFileParser:
    """解析 Python 测试文件，提取测试函数名"""

    def __init__(self, keywords_map: Dict[str, List[str]]):
        # 扁平化关键词列表；YAML 中空列表可能被解析为 None，需防御
        self.flat_keywords: List[Tuple[str, str]] = []
        if not keywords_map:
            return
        for scene, words in keywords_map.items():
            # scene 也可能是 None（YAML 边界情况），跳过非字符串
            if not isinstance(scene, str):
                continue
            if not words:
                continue
            for w in words:
                if not isinstance(w, str):
                    continue
                self.flat_keywords.append((scene, w.lower()))

    def parse_file(self, file_path: Path) -> List[TestCase]:
        """解析单个测试文件，返回测试用例列表"""
        try:
            source = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            # 结构化日志：文件读取失败降级，跳过该文件（不静默吞异常）
            logger.warning(json.dumps({
                "trace_id": _trace_id(),
                "module_name": "boundary_coverage",
                "action": "parse_file.read_failed",
                "duration_ms": 0,
                "file_path": str(file_path),
                "error": f"{type(e).__name__}: {e}",
            }, ensure_ascii=False))
            return []

        try:
            tree = ast.parse(source, filename=str(file_path))
        except SyntaxError as e:
            # 结构化日志：语法错误降级，跳过该文件（不静默吞异常）
            logger.warning(json.dumps({
                "trace_id": _trace_id(),
                "module_name": "boundary_coverage",
                "action": "parse_file.syntax_error",
                "duration_ms": 0,
                "file_path": str(file_path),
                "error": f"{type(e).__name__}: {e}",
            }, ensure_ascii=False))
            return []

        cases: List[TestCase] = []
        # 相对路径计算：跨盘符时退化为绝对路径，避免 relative_to 抛出异常
        try:
            rel_path = str(file_path.relative_to(PROJECT_ROOT))
        except ValueError:
            rel_path = str(file_path)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                case = TestCase(
                    name=node.name,
                    file_path=rel_path,
                )
                self._match_keywords(case)
                cases.append(case)
        return cases

    def _match_keywords(self, case: TestCase) -> None:
        """匹配测试函数名中的边界关键词"""
        name_lower = case.name.lower()
        for scene, word in self.flat_keywords:
            if word in name_lower:
                case.matched_keywords.append(scene)
                case.is_boundary = True
        # 去重
        case.matched_keywords = list(set(case.matched_keywords))


# ═══════════════════════════════════════════════════════════════
#  模块归属推断
# ═══════════════════════════════════════════════════════════════

class ModuleResolver:
    """将测试文件归属到 agent/ 下的业务模块"""

    def __init__(self, module_root: Path, test_root: Path):
        self.module_root = module_root
        self.test_root = test_root
        # 性能优化缓存：避免对同一文件/目录重复扫描与解析
        self._candidates_cache: Optional[List[str]] = None
        self._resolve_cache: Dict[str, Optional[str]] = {}  # 按文件路径缓存归属结果
        self._file_ast_cache: Dict[str, Optional[ast.AST]] = {}  # 按文件路径缓存 AST

    def resolve(self, test_file: Path) -> Optional[str]:
        """推断测试文件对应的模块名

        策略：
        1. 测试文件名包含模块名（如 test_circuit_breaker_boundary.py → circuit_breaker）
        2. 测试文件相对 test_root 的路径包含模块名（如 tests/unit/memory/ → memory）
        3. 文件内 import agent.<module> 的语句

        性能优化：按文件路径缓存归属结果，避免重复解析
        """
        cache_key = str(test_file)
        if cache_key in self._resolve_cache:
            return self._resolve_cache[cache_key]

        result: Optional[str] = None
        # 策略 1 & 2：扫描 agent/ 下所有子目录名作为候选
        candidates = self._candidate_module_names()
        file_name = test_file.stem.lower()  # test_circuit_breaker_boundary
        # 仅取相对 test_root 的路径，避免临时目录名（如 test_scan_new_module_blocked0）污染匹配
        try:
            rel_to_test = test_file.relative_to(self.test_root)
            rel_path_str = str(rel_to_test).lower().replace("\\", "/")
        except ValueError:
            rel_path_str = file_name

        # 匹配文件名或相对路径（最长匹配优先，避免 memory 匹配 memory_vector）
        matched = []
        for cand in candidates:
            if cand in file_name or cand in rel_path_str:
                matched.append(cand)
        if matched:
            # 取最长的匹配
            result = max(matched, key=len)
            self._resolve_cache[cache_key] = result
            return result

        # 策略 3：解析 import
        result = self._resolve_by_import(test_file, candidates)
        self._resolve_cache[cache_key] = result
        return result

    def _candidate_module_names(self) -> List[str]:
        """获取模块候选名（agent/ 下所有直接子目录 + 根级 .py 文件）

        性能优化：结果缓存，避免每次调用都扫描目录
        """
        if self._candidates_cache is not None:
            return self._candidates_cache
        if not self.module_root.exists():
            # 结构化日志：模块根目录不存在，降级返回空候选列表（不静默返回）
            logger.warning(json.dumps({
                "trace_id": _trace_id(),
                "module_name": "boundary_coverage",
                "action": "candidate_module_names.missing_root",
                "duration_ms": 0,
                "module_root": str(self.module_root),
                "reason": "模块根目录不存在，降级返回空候选列表",
            }, ensure_ascii=False))
            self._candidates_cache = []
            return self._candidates_cache
        candidates = []
        for p in self.module_root.iterdir():
            if p.name.startswith("_") or p.name.startswith("."):
                continue
            if p.is_dir():
                candidates.append(p.name)
            elif p.is_file() and p.suffix == ".py":
                candidates.append(p.stem)
        self._candidates_cache = candidates
        return self._candidates_cache

    def _resolve_by_import(self, test_file: Path, candidates: List[str]) -> Optional[str]:
        """通过 import 语句推断模块归属

        性能优化：按文件路径缓存 AST，避免重复读取和解析
        """
        cache_key = str(test_file)
        if cache_key in self._file_ast_cache:
            tree = self._file_ast_cache[cache_key]
            if tree is None:
                return None
        else:
            try:
                source = test_file.read_text(encoding="utf-8")
                tree = ast.parse(source, filename=str(test_file))
            except (OSError, SyntaxError) as e:
                # 结构化日志：AST 解析失败降级，缓存失败结果避免重复尝试（不静默吞异常）
                logger.warning(json.dumps({
                    "trace_id": _trace_id(),
                    "module_name": "boundary_coverage",
                    "action": "resolve_by_import.parse_failed",
                    "duration_ms": 0,
                    "test_file": str(test_file),
                    "error": f"{type(e).__name__}: {e}",
                }, ensure_ascii=False))
                # 缓存解析失败结果，避免重复尝试
                self._file_ast_cache[cache_key] = None
                return None
            self._file_ast_cache[cache_key] = tree

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and node.module.startswith("agent."):
                    parts = node.module.split(".")
                    if len(parts) >= 2 and parts[1] in candidates:
                        return parts[1]
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("agent."):
                        parts = alias.name.split(".")
                        if len(parts) >= 2 and parts[1] in candidates:
                            return parts[1]
        return None


# ═══════════════════════════════════════════════════════════════
#  新增模块检测
# ═══════════════════════════════════════════════════════════════

class NewModuleDetector:
    """检测新增模块（基于 git diff）"""

    def __init__(self, project_root: Path):
        self.project_root = project_root

    def detect_new_modules(self) -> Set[str]:
        """获取本次提交新增的模块名集合"""
        import subprocess

        try:
            # 与 main 分支对比
            result = subprocess.run(
                ["git", "diff", "--name-only", "--diff-filter=A", "origin/main...HEAD"],
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                # 退回到 HEAD 比较
                result = subprocess.run(
                    ["git", "diff", "--name-only", "--diff-filter=A", "HEAD~1"],
                    cwd=self.project_root,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            new_files = result.stdout.strip().split("\n") if result.stdout.strip() else []
        except (subprocess.SubprocessError, FileNotFoundError) as e:
            # 结构化日志：git 不可用，降级跳过新增模块检测（不静默吞异常）
            logger.warning(json.dumps({
                "trace_id": _trace_id(),
                "module_name": "boundary_coverage",
                "action": "detect_new_modules.git_unavailable",
                "duration_ms": 0,
                "error": f"{type(e).__name__}: {e}",
                "reason": "git 不可用或非 git 仓库，跳过新增模块检测",
            }, ensure_ascii=False))
            return set()
        except Exception as e:
            # 结构化日志：新增模块检测异常，降级返回空集（不静默吞异常）
            logger.warning(json.dumps({
                "trace_id": _trace_id(),
                "module_name": "boundary_coverage",
                "action": "detect_new_modules.failed",
                "duration_ms": 0,
                "error": f"{type(e).__name__}: {e}",
            }, ensure_ascii=False))
            return set()

        new_modules: Set[str] = set()
        for f in new_files:
            if not f:
                continue
            # 提取 agent/<module>/ 路径
            parts = f.replace("\\", "/").split("/")
            if len(parts) >= 2 and parts[0] == "agent":
                new_modules.add(parts[1])
        return new_modules


# ═══════════════════════════════════════════════════════════════
#  扫描器主类
# ═══════════════════════════════════════════════════════════════

class BoundaryScanner:
    """边界覆盖扫描器"""

    def __init__(
        self,
        config: Dict[str, Any],
        project_root: Path,
        new_modules: Optional[Set[str]] = None,
    ):
        self.config = config
        self.project_root = project_root
        self.new_modules = new_modules or set()

        global_cfg = config["global"]
        self.module_root = project_root / global_cfg.get("module_root", "agent")
        self.test_root = project_root / global_cfg.get("test_root", "tests")
        self.keywords_map: Dict[str, List[str]] = global_cfg["keywords"]

        self.parser = TestFileParser(self.keywords_map)
        self.resolver = ModuleResolver(self.module_root, self.test_root)
        self.modules_cfg: Dict[str, Any] = config.get("modules", {})
        self.ci_policy: Dict[str, Any] = config.get("ci_policy", {})

    def scan(self) -> ScanResult:
        """执行扫描，返回结果"""
        trace_id = _trace_id()
        start = time.time()
        logger.info(json.dumps({
            "trace_id": trace_id,
            "module_name": "boundary_coverage",
            "action": "scan.start",
            "timestamp": datetime.now().isoformat(),
        }, ensure_ascii=False))

        # 收集所有测试文件
        test_files = self._collect_test_files()
        logger.info(f"发现测试文件: {len(test_files)} 个")

        # 按模块聚合（性能优化：resolve 每文件只调用一次，避免内层循环重复解析）
        module_cases: Dict[str, List[TestCase]] = {}
        for tf in test_files:
            cases = self.parser.parse_file(tf)
            if not cases:
                continue
            # 模块归属按文件解析一次，而非每个测试用例重复解析
            module_name = self.resolver.resolve(tf)
            if not module_name:
                module_name = "_unmapped"
            module_cases.setdefault(module_name, []).extend(cases)

        # 生成模块报告
        module_reports: List[ModuleReport] = []
        blocked: List[str] = []

        # 配置中已声明的模块
        for mod_name, mod_cfg in self.modules_cfg.items():
            report = self._build_module_report(
                mod_name, mod_cfg, module_cases.get(mod_name, [])
            )
            self._evaluate_status(report, blocked)
            module_reports.append(report)

        # 配置中未声明的模块（仅信息性记录）
        for mod_name, cases in module_cases.items():
            if mod_name in self.modules_cfg or mod_name == "_unmapped":
                continue
            report = self._build_module_report(mod_name, {}, cases)
            self._evaluate_status(report, blocked)
            module_reports.append(report)

        duration_ms = (time.time() - start) * 1000
        total_tests = sum(r.total_tests for r in module_reports)
        total_boundary = sum(r.boundary_tests for r in module_reports)
        overall_status = "fail" if blocked else ("warn" if any(r.status == "⚠️" for r in module_reports) else "pass")

        logger.info(json.dumps({
            "trace_id": trace_id,
            "module_name": "boundary_coverage",
            "action": "scan.complete",
            "duration_ms": round(duration_ms, 2),
            "total_modules": len(module_reports),
            "total_tests": total_tests,
            "total_boundary_tests": total_boundary,
            "blocked_modules": blocked,
            "overall_status": overall_status,
        }, ensure_ascii=False))

        return ScanResult(
            trace_id=trace_id,
            timestamp=datetime.now().isoformat(),
            duration_ms=round(duration_ms, 2),
            total_modules=len(module_reports),
            total_tests=total_tests,
            total_boundary_tests=total_boundary,
            modules=module_reports,
            blocked_modules=blocked,
            overall_status=overall_status,
        )

    def _collect_test_files(self) -> List[Path]:
        """递归收集所有 test_*.py 文件"""
        if not self.test_root.exists():
            # 结构化日志：测试根目录不存在，降级返回空列表（不静默返回）
            logger.warning(json.dumps({
                "trace_id": _trace_id(),
                "module_name": "boundary_coverage",
                "action": "collect_test_files.missing_root",
                "duration_ms": 0,
                "test_root": str(self.test_root),
                "reason": "测试根目录不存在，降级返回空列表",
            }, ensure_ascii=False))
            return []
        return sorted(self.test_root.rglob("test_*.py"))

    def _build_module_report(
        self,
        mod_name: str,
        mod_cfg: Dict[str, Any],
        cases: List[TestCase],
    ) -> ModuleReport:
        """构建模块报告"""
        # 防御 YAML 解析返回 None 的情况
        raw_scenes = mod_cfg.get("required_scenes") or []
        required_scenes = [s for s in raw_scenes if isinstance(s, str)]
        report = ModuleReport(
            module_name=mod_name,
            description=mod_cfg.get("description", "") or "",
            total_tests=len(cases),
            required_scenes=required_scenes,
            min_tests=mod_cfg.get("min_tests", 0) or 0,
            is_new_module=mod_name in self.new_modules,
        )
        for case in cases:
            if case.is_boundary:
                report.boundary_tests += 1
                report.covered_scenes.update(case.matched_keywords)
                report.test_cases.append({
                    "name": case.name,
                    "file": case.file_path,
                    "scenes": case.matched_keywords,
                })
        # 缺失场景
        report.missing_scenes = [
            s for s in report.required_scenes if s not in report.covered_scenes
        ]
        return report

    def _evaluate_status(self, report: ModuleReport, blocked: List[str]) -> None:
        """评估模块状态：✅/⚠️/❌"""
        # 新增模块强制阻断
        if report.is_new_module and self.ci_policy.get("enforce_new_modules", True):
            if report.boundary_tests == 0:
                report.status = "❌"
                report.suggestions.append("新增模块必须包含至少 1 个边界测试")
                blocked.append(report.module_name)
                return

        # 配置中声明的模块：未达最低要求
        if report.required_scenes:
            if report.missing_scenes:
                if report.is_new_module:
                    report.status = "❌"
                    blocked.append(report.module_name)
                    report.suggestions.append(
                        f"缺失边界场景: {', '.join(report.missing_scenes)}"
                    )
                else:
                    report.status = "⚠️"
                    report.suggestions.append(
                        f"建议补充边界场景: {', '.join(report.missing_scenes)}"
                    )
            elif report.boundary_tests < report.min_tests:
                if report.is_new_module:
                    report.status = "❌"
                    blocked.append(report.module_name)
                    report.suggestions.append(
                        f"边界测试数 {report.boundary_tests} < 最低要求 {report.min_tests}"
                    )
                else:
                    report.status = "⚠️"
                    report.suggestions.append(
                        f"建议增加边界测试至 {report.min_tests} 个"
                    )
            else:
                report.status = "✅"
        else:
            # 未在配置中声明的模块
            if report.boundary_tests == 0 and report.total_tests > 0:
                report.status = "⚠️"
                report.suggestions.append("未配置边界场景要求，建议补充边界测试")
            elif report.is_new_module and report.boundary_tests == 0:
                report.status = "❌"
                blocked.append(report.module_name)
            else:
                report.status = "✅"


# ═══════════════════════════════════════════════════════════════
#  报告生成器
# ═══════════════════════════════════════════════════════════════

class ReportGenerator:
    """Markdown + JSON 报告生成器"""

    def __init__(
        self,
        project_root: Path,
        ci_policy: Dict[str, Any],
        threshold: Optional[int] = None,
        threshold_source: str = "ci_policy",
    ):
        self.project_root = project_root
        self.ci_policy = ci_policy
        # 阈值优先级：外部传入 > ci_policy.threshold > 默认值 5
        if threshold is not None:
            self.threshold = threshold
            self.threshold_source = threshold_source
        else:
            self.threshold = ci_policy.get("threshold", 5)
            self.threshold_source = "ci_policy"

    def generate_markdown(self, result: ScanResult) -> str:
        """生成 Markdown 报告"""
        lines: List[str] = []
        lines.append("# 边界覆盖扫描报告")
        lines.append("")
        lines.append(f"- **生成时间**：{result.timestamp}")
        lines.append(f"- **Trace ID**：`{result.trace_id}`")
        lines.append(f"- **扫描耗时**：{result.duration_ms:.2f} ms")
        lines.append(f"- **总体状态**：{self._status_badge(result.overall_status)}")
        lines.append("")
        lines.append("## 总览")
        lines.append("")
        lines.append("| 指标 | 数值 |")
        lines.append("| --- | --- |")
        lines.append(f"| 模块总数 | {result.total_modules} |")
        lines.append(f"| 测试用例总数 | {result.total_tests} |")
        lines.append(f"| 边界测试用例数 | {result.total_boundary_tests} |")
        coverage = (result.total_boundary_tests / result.total_tests * 100) if result.total_tests else 0
        lines.append(f"| 边界测试覆盖率 | {coverage:.1f}% |")
        lines.append(f"| 阻断模块数 | {len(result.blocked_modules)} |")
        if result.blocked_modules:
            lines.append(f"| 阻断模块清单 | {', '.join(result.blocked_modules)} |")
        lines.append("")

        lines.append("## 模块详情")
        lines.append("")
        lines.append("| 模块 | 描述 | 测试数 | 边界测试数 | 覆盖场景 | 缺失场景 | 状态 | 建议 |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
        # 阻断模块优先排序
        sorted_modules = sorted(
            result.modules,
            key=lambda r: (0 if r.module_name in result.blocked_modules else 1, r.module_name),
        )
        for r in sorted_modules:
            covered = ", ".join(sorted(r.covered_scenes)) if r.covered_scenes else "—"
            missing = ", ".join(r.missing_scenes) if r.missing_scenes else "—"
            suggestion = "；".join(r.suggestions) if r.suggestions else "—"
            new_tag = " 🆕" if r.is_new_module else ""
            lines.append(
                f"| `{r.module_name}`{new_tag} | {r.description} | "
                f"{r.total_tests} | {r.boundary_tests} | {covered} | "
                f"{missing} | {r.status} | {suggestion} |"
            )
        lines.append("")

        # 边界测试用例明细（前 50 条）
        lines.append("## 边界测试用例明细")
        lines.append("")
        all_cases: List[Dict[str, Any]] = []
        for r in result.modules:
            for c in r.test_cases:
                all_cases.append({"module": r.module_name, **c})
        if all_cases:
            lines.append("| 模块 | 测试名 | 文件 | 场景 |")
            lines.append("| --- | --- | --- | --- |")
            for c in all_cases[:50]:
                scenes = ", ".join(c.get("scenes", []))
                lines.append(
                    f"| `{c['module']}` | `{c['name']}` | {c['file']} | {scenes} |"
                )
            if len(all_cases) > 50:
                lines.append(f"\n> 仅展示前 50 条，共 {len(all_cases)} 条边界测试用例")
        else:
            lines.append("_暂无边界测试用例_")
        lines.append("")

        lines.append("## CI 阻断策略")
        lines.append("")
        lines.append(f"- **新增模块强制要求边界测试**：{self.ci_policy.get('enforce_new_modules', True)}")
        lines.append(f"- **存量模块策略**：{self.ci_policy.get('legacy_strategy', 'warn')}")
        if result.blocked_modules:
            lines.append(f"- **本次阻断模块**：{', '.join(result.blocked_modules)}")
            lines.append("")
            lines.append("> ⚠️ **CI 阻断**：上述模块需补充边界测试后方可合并。")
        else:
            lines.append("- **本次无阻断模块** ✅")
        lines.append("")
        lines.append("---")
        lines.append("_由 `scripts/check_boundary_coverage.py` 自动生成_")
        return "\n".join(lines)

    def generate_json(self, result: ScanResult) -> Dict[str, Any]:
        """生成 JSON 报告（含阈值信息，供 CI 配置驱动）"""
        coverage_percent = (
            round(result.total_boundary_tests / result.total_tests * 100, 1)
            if result.total_tests > 0
            else 0.0
        )
        passed = coverage_percent >= self.threshold and result.overall_status != "fail"
        return {
            "trace_id": result.trace_id,
            "timestamp": result.timestamp,
            "duration_ms": result.duration_ms,
            "overall_status": result.overall_status,
            "total_modules": result.total_modules,
            "total_tests": result.total_tests,
            "total_boundary_tests": result.total_boundary_tests,
            "coverage_percent": coverage_percent,
            "threshold": self.threshold,
            "threshold_source": self.threshold_source,
            "passed": passed,
            "blocked_modules": result.blocked_modules,
            "modules": [
                {
                    "module_name": r.module_name,
                    "description": r.description,
                    "total_tests": r.total_tests,
                    "boundary_tests": r.boundary_tests,
                    "covered_scenes": sorted(r.covered_scenes),
                    "required_scenes": r.required_scenes,
                    "missing_scenes": r.missing_scenes,
                    "min_tests": r.min_tests,
                    "status": r.status,
                    "is_new_module": r.is_new_module,
                    "suggestions": r.suggestions,
                }
                for r in result.modules
            ],
        }

    def _status_badge(self, status: str) -> str:
        """状态徽章"""
        return {
            "pass": "✅ 通过",
            "warn": "⚠️ 警告",
            "fail": "❌ 阻断",
        }.get(status, status)


# ═══════════════════════════════════════════════════════════════
#  降级报告
# ═══════════════════════════════════════════════════════════════

def _generate_degraded_report(error: Exception, output_path: Path) -> None:
    """报告生成失败时输出降级报告（边界显性化原则）"""
    trace_id = _trace_id()
    content = [
        "# 边界覆盖扫描报告（降级）",
        "",
        f"- **生成时间**：{datetime.now().isoformat()}",
        f"- **Trace ID**：`{trace_id}`",
        f"- **状态**：❌ 扫描失败",
        "",
        "## 错误信息",
        "",
        "```",
        f"{type(error).__name__}: {error}",
        "```",
        "",
        "## 错误堆栈",
        "",
        "```",
        traceback.format_exc(),
        "```",
        "",
        "## 处置建议",
        "",
        "1. 检查 `tests/boundary_config.yaml` 配置文件是否存在且格式正确",
        "2. 确认 PyYAML 已安装：`pip install pyyaml`",
        "3. 确认 `tests/` 目录存在且包含 `test_*.py` 文件",
        "4. 如问题持续，请联系平台研发组",
        "",
        "---",
        "_降级报告：扫描过程中发生异常，主报告未能生成_",
    ]
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("\n".join(content), encoding="utf-8")
        logger.error(f"降级报告已输出: {output_path}")
    except OSError as e:
        logger.error(f"降级报告输出失败: {e}")


# ═══════════════════════════════════════════════════════════════
#  配置文件阈值加载（配置驱动模式）
# ═══════════════════════════════════════════════════════════════

def _load_threshold_from_config(config_path: Path) -> Tuple[int, str]:
    """从 config.yaml 读取 boundary_test_coverage 阈值

    Args:
        config_path: config.yaml 文件路径

    Returns:
        (threshold, source_description) 元组

    Raises:
        FileNotFoundError: 配置文件不存在
        ValueError: 配置文件格式错误或阈值字段缺失
    """
    trace_id = _trace_id()
    start = time.time()

    if not config_path.exists():
        raise FileNotFoundError(
            f"配置文件不存在: {config_path} [error_code=BOUNDARY_CONFIG_NOT_FOUND]"
        )

    try:
        import yaml
    except ImportError as e:
        raise ImportError(
            "PyYAML 未安装，请运行: pip install pyyaml "
            "[error_code=DEPENDENCY_MISSING]"
        ) from e

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        raise ValueError(
            f"YAML 解析失败: {e} [error_code=CONFIG_PARSE_ERROR]"
        ) from e

    # 从 visibility_thresholds.verification.boundary_test_coverage 读取
    visibility_thresholds = config.get("visibility_thresholds", {})
    verification = visibility_thresholds.get("verification", {})
    threshold = verification.get("boundary_test_coverage")

    if threshold is None:
        # 降级：尝试扁平化 key（兼容旧格式）
        threshold = visibility_thresholds.get("boundary_test_coverage", 5)
        source = f"{config_path.name} (flat fallback, value={threshold})"
    else:
        source = f"{config_path.name} (visibility_thresholds.verification.boundary_test_coverage={threshold})"

    threshold = int(threshold)

    duration_ms = (time.time() - start) * 1000
    logger.info(json.dumps({
        "trace_id": trace_id,
        "module_name": "boundary_coverage",
        "action": "load_threshold_from_config",
        "duration_ms": round(duration_ms, 2),
        "config_path": str(config_path),
        "threshold": threshold,
        "source": source,
    }, ensure_ascii=False))

    return threshold, source


# ═══════════════════════════════════════════════════════════════
#  CLI 入口
# ═══════════════════════════════════════════════════════════════

def main(argv: Optional[List[str]] = None) -> int:
    """CLI 入口，返回退出码：0 通过 / 1 阻断 / 2 异常"""
    parser = argparse.ArgumentParser(
        description="边界覆盖扫描脚本：识别测试中的边界关键词，按模块统计覆盖率"
    )
    parser.add_argument(
        "--config", "-c",
        default=str(PROJECT_ROOT / "tests" / "boundary_config.yaml"),
        help="边界覆盖配置文件路径",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="严格模式：任何警告都视为阻断（CI 用）",
    )
    parser.add_argument(
        "--json-only",
        action="store_true",
        help="仅输出 JSON 到 stdout（供 CI 解析）",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="详细日志",
    )
    parser.add_argument(
        "--threshold-from-config",
        metavar="CONFIG_PATH",
        default=None,
        help="从 config.yaml 读取 boundary_test_coverage 阈值，覆盖 ci_policy.threshold",
    )
    args = parser.parse_args(argv)

    _setup_logging(args.verbose)

    config_path = Path(args.config)
    output_md = PROJECT_ROOT / "docs" / "observability" / "boundary_coverage_report.md"
    output_json = PROJECT_ROOT / "docs" / "observability" / "boundary_coverage_report.json"

    try:
        # 1. 加载配置
        config = ConfigLoader(config_path).load()

        # 2. 检测新增模块
        new_modules = NewModuleDetector(PROJECT_ROOT).detect_new_modules()
        if new_modules:
            logger.info(f"检测到新增模块: {new_modules}")

        # 3. 执行扫描
        scanner = BoundaryScanner(config, PROJECT_ROOT, new_modules)
        result = scanner.scan()

        # 4. 生成报告（支持从 config.yaml 读取阈值，配置驱动模式）
        threshold_override = None
        threshold_source = "ci_policy"
        if args.threshold_from_config:
            threshold_override, threshold_source = _load_threshold_from_config(
                Path(args.threshold_from_config)
            )
            logger.info(
                f"阈值从 config.yaml 读取: {threshold_override}% (source={threshold_source})"
            )

        report_gen = ReportGenerator(
            PROJECT_ROOT,
            config.get("ci_policy", {}),
            threshold=threshold_override,
            threshold_source=threshold_source,
        )

        # 覆盖配置中的报告路径
        ci_policy = config.get("ci_policy", {})
        if ci_policy.get("report_path"):
            output_md = PROJECT_ROOT / ci_policy["report_path"]
        if ci_policy.get("json_report_path"):
            output_json = PROJECT_ROOT / ci_policy["json_report_path"]

        json_data = report_gen.generate_json(result)
        if args.json_only:
            print(json.dumps(json_data, ensure_ascii=False, indent=2))
            return 0 if result.overall_status != "fail" else 1

        md_content = report_gen.generate_markdown(result)

        output_md.parent.mkdir(parents=True, exist_ok=True)
        output_md.write_text(md_content, encoding="utf-8")
        output_json.write_text(
            json.dumps(json_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f"Markdown 报告已生成: {output_md}")
        logger.info(f"JSON 报告已生成: {output_json}")

        # 5. 退出码决策
        if result.overall_status == "fail":
            logger.error(f"❌ 边界覆盖扫描阻断：{result.blocked_modules}")
            return 1
        if args.strict and result.overall_status == "warn":
            logger.warning("⚠️ 严格模式下警告视为阻断")
            return 1
        return 0

    except Exception as e:
        logger.error(f"扫描异常: {e}", exc_info=True)
        _generate_degraded_report(e, output_md)
        if args.json_only:
            print(json.dumps({
                "error": str(e),
                "error_type": type(e).__name__,
                "overall_status": "error",
            }, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.exit(main())
