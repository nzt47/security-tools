#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
四层可见性报告生成器

【生成日志摘要】
- 生成时间：2026-06-26
- 版本：v1.0.0
- 内容：一键生成四层可见性覆盖报告（运行时/验证过程/业务价值/架构影响），
       支持阈值阻断，作为 PR 合并门槛。

【四层可见性定义】
1. 运行时可见：结构化日志覆盖率、链路追踪覆盖率、健康检查端点数
2. 验证过程可见：测试覆盖率、边界测试覆盖率、契约测试数
3. 业务价值可见：埋点覆盖率（track( 调用）、看板数、告警规则数
4. 架构影响可见：依赖图节点/边数、架构规则违规数、变更影响分析覆盖率

【可观测性约束】
- 结构化日志：trace_id / module_name / action / duration_ms
- 边界显性化：低于阈值的指标显性标红
- 异常处理：报告生成失败时输出降级报告
"""

from __future__ import annotations

import argparse
import ast
import json
import logging
import os
import re
import subprocess
import sys
import time
import traceback
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 项目根目录
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent

logger = logging.getLogger("visibility_report")


def _setup_logging(verbose: bool = False) -> None:
    """配置结构化日志"""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s [%(levelname)8s] %(name)-30s: %(message)s',
        datefmt='%H:%M:%S',
    )


def _trace_id() -> str:
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]


# ═══════════════════════════════════════════════════════════════
#  数据结构
# ═══════════════════════════════════════════════════════════════

@dataclass
class Metric:
    """单个指标"""
    name: str
    value: float
    threshold: float
    unit: str = ""
    description: str = ""
    # 是否通过阈值（None 表示未判定）
    # 调用方可显式传入 True/False 来覆盖自动判定（用于逆向指标，如违规数）
    passed: Optional[bool] = None
    # 附加信息
    details: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        # 如果调用方已显式设置 passed（非 None），则不覆盖
        if self.passed is not None:
            return
        if self.threshold is not None:
            self.passed = self.value >= self.threshold

    @property
    def status_icon(self) -> str:
        if self.passed is None:
            return "➖"
        return "✅" if self.passed else "❌"


@dataclass
class LayerReport:
    """层级报告"""
    layer_name: str
    description: str
    metrics: List[Metric] = field(default_factory=list)
    overall_passed: bool = True

    def add_metric(self, metric: Metric) -> None:
        self.metrics.append(metric)
        if metric.passed is False:
            self.overall_passed = False


@dataclass
class VisibilityReport:
    """四层可见性报告"""
    trace_id: str
    timestamp: str
    duration_ms: float
    layers: List[LayerReport]
    overall_status: str  # pass / fail / degraded
    threshold_violations: List[str]
    error: Optional[str] = None


# ═══════════════════════════════════════════════════════════════
#  指标采集器
# ═══════════════════════════════════════════════════════════════

class MetricCollector:
    """四层可见性指标采集器"""

    def __init__(self, project_root: Path, thresholds: Dict[str, Any]):
        self.project_root = project_root
        self.thresholds = thresholds
        # 文件内容缓存：惰性加载，避免对 agent/ 下 .py 文件重复扫描+读取
        # 优化目的：_calc_structured_log_coverage / _count_health_endpoints /
        # _calc_track_coverage 三个采集方法原本各自执行 rglob + read_text，
        # 导致同一批文件被读取 3 次。这里通过共享缓存将 IO 次数降为 1 次。
        self._file_content_cache: Optional[Dict[Path, str]] = None

    def _scan_agent_files(self) -> Dict[Path, str]:
        """惰性扫描 agent/ 下所有 .py 文件内容并缓存

        首次调用时执行一次性扫描+读取，后续调用直接返回缓存。
        读取失败的文件（OSError/UnicodeDecodeError）不入缓存，与原逻辑一致。
        """
        if self._file_content_cache is not None:
            return self._file_content_cache

        cache: Dict[Path, str] = {}
        agent_dir = self.project_root / "agent"
        if agent_dir.exists():
            for py_file in agent_dir.rglob("*.py"):
                try:
                    cache[py_file] = py_file.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    # 读取失败的文件跳过（与原各采集方法的行为一致）
                    continue
        self._file_content_cache = cache
        return cache

    def collect_all(self) -> List[LayerReport]:
        """采集全部四层指标"""
        layers: List[LayerReport] = []
        layers.append(self._collect_runtime_layer())
        layers.append(self._collect_verification_layer())
        layers.append(self._collect_business_layer())
        layers.append(self._collect_architecture_layer())
        return layers

    # ── 第一层：运行时可见 ──
    def _collect_runtime_layer(self) -> LayerReport:
        """运行时可见性指标"""
        layer = LayerReport(
            layer_name="运行时可见",
            description="结构化日志、链路追踪、健康检查端点",
        )
        rt_thresholds = self.thresholds.get("runtime", {})

        # 1. 结构化日志覆盖率
        log_coverage = self._calc_structured_log_coverage()
        layer.add_metric(Metric(
            name="structured_log_coverage",
            value=log_coverage,
            threshold=rt_thresholds.get("structured_log_coverage", 80),
            unit="%",
            description="包含 trace_id/module_name/action/duration_ms 的日志占比",
        ))

        # 2. 链路追踪覆盖率
        trace_coverage = self._calc_trace_coverage()
        layer.add_metric(Metric(
            name="trace_coverage",
            value=trace_coverage,
            threshold=rt_thresholds.get("trace_coverage", 70),
            unit="%",
            description="使用 @trace_route 或 TraceContext 的路由占比",
        ))

        # 3. 健康检查端点数
        health_endpoints = self._count_health_endpoints()
        layer.add_metric(Metric(
            name="health_endpoints",
            value=health_endpoints,
            threshold=rt_thresholds.get("health_endpoints", 1),
            unit="个",
            description="健康检查端点数量",
        ))

        return layer

    def _calc_structured_log_coverage(self) -> float:
        """计算结构化日志覆盖率（含 trace_id 的日志调用占比）"""
        trace_id = _trace_id()
        t0 = time.time()
        agent_dir = self.project_root / "agent"
        if not agent_dir.exists():
            logger.warning(json.dumps({
                "trace_id": trace_id,
                "module_name": "visibility_report",
                "action": "calc_structured_log.agent_dir_missing",
                "duration_ms": round((time.time() - t0) * 1000, 2),
                "reason": "agent/ 目录不存在，返回 0.0",
            }, ensure_ascii=False))
            return 0.0

        total_logs = 0
        structured_logs = 0
        # 优化：使用共享缓存，避免与 _count_health_endpoints / _calc_track_coverage 重复 IO
        file_cache = self._scan_agent_files()
        for content in file_cache.values():
            # 统计 logger.info/debug/warning/error 调用
            log_calls = re.findall(r"logger\.(info|debug|warning|error|critical)\(", content)
            total_logs += len(log_calls)
            # 统计包含 trace_id 的调用（json.dumps 含 trace_id，或 f-string 含 trace_id）
            structured = re.findall(
                r'logger\.\w+\(.*?(?:trace_id|json\.dumps)', content, re.DOTALL
            )
            structured_logs += len(structured)

        elapsed_ms = round((time.time() - t0) * 1000, 2)
        if total_logs == 0:
            logger.info(json.dumps({
                "trace_id": trace_id,
                "module_name": "visibility_report",
                "action": "calc_structured_log.no_logs",
                "duration_ms": elapsed_ms,
                "scanned_files": len(file_cache),
                "total_logs": 0,
                "coverage_percent": 100.0,
                "reason": "无日志调用，视为通过（100%）",
            }, ensure_ascii=False))
            return 100.0  # 无日志则视为通过
        coverage = round(structured_logs / total_logs * 100, 1)
        logger.info(json.dumps({
            "trace_id": trace_id,
            "module_name": "visibility_report",
            "action": "calc_structured_log.success",
            "duration_ms": elapsed_ms,
            "scanned_files": len(file_cache),
            "total_logs": total_logs,
            "structured_logs": structured_logs,
            "coverage_percent": coverage,
        }, ensure_ascii=False))
        return coverage

    def _calc_trace_coverage(self) -> float:
        """计算链路追踪覆盖率"""
        trace_id = _trace_id()
        t0 = time.time()
        routes_dir = self.project_root / "agent" / "server_routes"
        if not routes_dir.exists():
            logger.warning(json.dumps({
                "trace_id": trace_id,
                "module_name": "visibility_report",
                "action": "calc_trace.routes_dir_missing",
                "duration_ms": round((time.time() - t0) * 1000, 2),
                "reason": "agent/server_routes/ 目录不存在，返回 0.0",
            }, ensure_ascii=False))
            return 0.0

        total_routes = 0
        traced_routes = 0
        scanned_files = 0
        for py_file in routes_dir.glob("routes_*.py"):
            scanned_files += 1
            try:
                content = py_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            # 统计 @app.route 装饰器
            routes = re.findall(r'@app\.route\(["\']', content)
            total_routes += len(routes)
            # 统计 @trace_route 装饰器
            traced = re.findall(r'@trace_route', content)
            traced_routes += len(traced)

        elapsed_ms = round((time.time() - t0) * 1000, 2)
        if total_routes == 0:
            logger.info(json.dumps({
                "trace_id": trace_id,
                "module_name": "visibility_report",
                "action": "calc_trace.no_routes",
                "duration_ms": elapsed_ms,
                "scanned_files": scanned_files,
                "reason": "未扫描到 @app.route 装饰器，视为通过（100%）",
            }, ensure_ascii=False))
            return 100.0
        coverage = round(traced_routes / total_routes * 100, 1)
        logger.info(json.dumps({
            "trace_id": trace_id,
            "module_name": "visibility_report",
            "action": "calc_trace.success",
            "duration_ms": elapsed_ms,
            "scanned_files": scanned_files,
            "total_routes": total_routes,
            "traced_routes": traced_routes,
            "coverage_percent": coverage,
        }, ensure_ascii=False))
        return coverage

    def _count_health_endpoints(self) -> int:
        """统计健康检查端点数"""
        agent_dir = self.project_root / "agent"
        count = 0
        if not agent_dir.exists():
            return 0
        # 优化：使用共享缓存，避免与 _calc_structured_log_coverage / _calc_track_coverage 重复 IO
        file_cache = self._scan_agent_files()
        for content in file_cache.values():
            # 匹配 /api/health 或 /health 或 /status 端点
            count += len(re.findall(r'route\(["\'](?:/api/health|/health|/api/status|/status)["\']', content))
        return count

    # ── 第二层：验证过程可见 ──
    def _collect_verification_layer(self) -> LayerReport:
        """验证过程可见性指标"""
        layer = LayerReport(
            layer_name="验证过程可见",
            description="测试覆盖率、边界测试、契约测试",
        )
        v_thresholds = self.thresholds.get("verification", {})

        # 1. 测试覆盖率（从 coverage.xml 或 pyproject.toml 读取）
        test_coverage = self._read_test_coverage()
        layer.add_metric(Metric(
            name="test_coverage",
            value=test_coverage,
            threshold=v_thresholds.get("test_coverage", 40),
            unit="%",
            description="代码测试覆盖率（来自 coverage.xml 或 pyproject.toml）",
        ))

        # 2. 边界测试覆盖率（运行边界扫描脚本）
        boundary_coverage = self._calc_boundary_coverage()
        layer.add_metric(Metric(
            name="boundary_test_coverage",
            value=boundary_coverage,
            threshold=v_thresholds.get("boundary_test_coverage", 10),
            unit="%",
            description="边界测试用例占总测试比例",
        ))

        # 3. 契约测试数
        contract_count = self._count_contract_tests()
        layer.add_metric(Metric(
            name="contract_test_count",
            value=contract_count,
            threshold=v_thresholds.get("contract_test_count", 3),
            unit="个",
            description="Pact 契约测试数量",
        ))

        return layer

    def _read_test_coverage(self) -> float:
        """读取测试覆盖率

        解析优先级：
          1. coverage.xml 的 line-rate（真实覆盖率）
             - CI 环境：由 full-project-tests job 生成并通过 artifact 传递，
                        visibility-report job 下载后放置于项目根目录，直接读取真实 line-rate
             - 本地环境：需手动执行 `pytest --cov=agent --cov=scripts --cov-report=xml` 生成
          2. pyproject.toml 的 [tool.coverage.report] fail_under（仅本地兜底基线）
             - 注意：此降级路径在 CI 中不应触发（CI 总能从 artifact 拿到 coverage.xml）
             - 仅用于本地运行时 coverage.xml 缺失的兜底，避免直接返回 0.0 影响判断
          3. 0.0（明确 error 日志告警，不静默返回）

        异常处理：所有降级路径均输出结构化日志，便于排查 artifact 传递问题。
        """
        # ── 优先从 coverage.xml 读取真实 line-rate ──
        coverage_xml = self.project_root / "coverage.xml"
        if coverage_xml.exists():
            import xml.etree.ElementTree as ET
            try:
                tree = ET.parse(coverage_xml)
                root = tree.getroot()
                line_rate = float(root.attrib.get("line-rate", "0"))
                # line-rate=0 通常是空报告或生成失败，视为无效数据进入降级路径
                if line_rate > 0:
                    logger.info(json.dumps({
                        "trace_id": _trace_id(),
                        "module_name": "visibility_report",
                        "action": "read_test_coverage.success",
                        "duration_ms": 0,
                        "path": str(coverage_xml),
                        "line_rate": line_rate,
                        "coverage_percent": round(line_rate * 100, 1),
                        "source": "full-project-tests artifact (CI) 或本地生成",
                    }, ensure_ascii=False))
                    return round(line_rate * 100, 1)
                logger.warning(json.dumps({
                    "trace_id": _trace_id(),
                    "module_name": "visibility_report",
                    "action": "read_test_coverage.invalid_xml",
                    "duration_ms": 0,
                    "path": str(coverage_xml),
                    "line_rate": line_rate,
                    "reason": "coverage.xml line-rate=0，可能是空报告或测试未覆盖任何行，进入本地兜底降级",
                }, ensure_ascii=False))
            except (ValueError, OSError, ET.ParseError) as e:
                # 解析失败：明确记录错误，而非静默吞掉
                # ET.ParseError 继承自 SyntaxError，不被 ValueError/OSError 覆盖，需显式捕获
                logger.error(json.dumps({
                    "trace_id": _trace_id(),
                    "module_name": "visibility_report",
                    "action": "read_test_coverage.parse_failed",
                    "duration_ms": 0,
                    "path": str(coverage_xml),
                    "error": f"{type(e).__name__}: {e}",
                    "reason": "coverage.xml 解析失败，进入本地兜底降级",
                }, ensure_ascii=False))
        else:
            # 文件不存在
            # CI 环境：不应发生，full-project-tests job 应已通过 artifact 提供 coverage.xml
            #          若发生则说明 artifact 下载失败，需排查 visibility-report job 的下载步骤
            # 本地环境：常见情况，降级到 pyproject.toml fail_under 作为基线
            logger.error(json.dumps({
                "trace_id": _trace_id(),
                "module_name": "visibility_report",
                "action": "read_test_coverage.missing_xml",
                "duration_ms": 0,
                "path": str(coverage_xml),
                "reason": "coverage.xml 不存在",
                "ci_hint": "CI 中应由 full-project-tests job 上传 full-coverage-report artifact，visibility-report job 下载后放置于项目根目录",
                "local_hint": "本地运行可执行 `pytest --cov=agent --cov=scripts --cov-report=xml` 生成 coverage.xml",
            }, ensure_ascii=False))

        # ── 本地兜底降级：从 pyproject.toml 读取 fail_under ──
        # 注意：此路径在 CI 中不应触发（CI 总能从 artifact 拿到 coverage.xml）
        # 仅用于本地运行时 coverage.xml 缺失的兜底，避免直接返回 0.0
        pyproject = self.project_root / "pyproject.toml"
        if pyproject.exists():
            try:
                content = pyproject.read_text(encoding="utf-8")
                # 匹配 [tool.coverage.report] 段下的 fail_under
                match = re.search(r'fail_under\s*=\s*(\d+(?:\.\d+)?)', content)
                if match:
                    baseline = float(match.group(1))
                    logger.warning(json.dumps({
                        "trace_id": _trace_id(),
                        "module_name": "visibility_report",
                        "action": "read_test_coverage.fallback_pyproject",
                        "duration_ms": 0,
                        "baseline": baseline,
                        "reason": "降级使用 pyproject.toml fail_under 作为基线（本地兜底，非真实覆盖率）",
                        "ci_note": "CI 中此降级不应触发，请检查 full-project-tests artifact 是否正确下载",
                    }, ensure_ascii=False))
                    return baseline
                logger.error(json.dumps({
                    "trace_id": _trace_id(),
                    "module_name": "visibility_report",
                    "action": "read_test_coverage.fail_under_not_found",
                    "duration_ms": 0,
                    "path": str(pyproject),
                    "reason": "pyproject.toml 中未找到 fail_under 配置",
                }, ensure_ascii=False))
            except OSError as e:
                logger.error(json.dumps({
                    "trace_id": _trace_id(),
                    "module_name": "visibility_report",
                    "action": "read_test_coverage.pyproject_read_failed",
                    "duration_ms": 0,
                    "path": str(pyproject),
                    "error": f"{type(e).__name__}: {e}",
                }, ensure_ascii=False))
        else:
            logger.error(json.dumps({
                "trace_id": _trace_id(),
                "module_name": "visibility_report",
                "action": "read_test_coverage.pyproject_missing",
                "duration_ms": 0,
                "path": str(pyproject),
                "reason": "pyproject.toml 不存在，无法本地兜底降级",
            }, ensure_ascii=False))

        return 0.0

    def _calc_boundary_coverage(self) -> float:
        """计算边界测试覆盖率

        数据源：调用 scripts/check_boundary_coverage.py --json-only
        排查要点：
          1. 脚本是否存在
          2. subprocess 执行是否超时
          3. returncode 是否为 0/1（2 表示脚本异常）
          4. stdout 是否为空（日志被混入 stdout 时会污染 JSON）
          5. JSON 字段 total_tests / total_boundary_tests 是否为 0
        """
        trace_id = _trace_id()
        t0 = time.time()
        # 1. 检查脚本是否存在
        script_path = self.project_root / "scripts" / "check_boundary_coverage.py"
        if not script_path.exists():
            logger.warning(json.dumps({
                "trace_id": trace_id,
                "module_name": "visibility_report",
                "action": "calc_boundary_coverage.script_not_found",
                "duration_ms": round((time.time() - t0) * 1000, 2),
                "script_path": str(script_path),
                "reason": "check_boundary_coverage.py 不存在，返回 0.0",
            }, ensure_ascii=False))
            return 0.0

        # 2. 调用边界扫描脚本
        try:
            logger.info(json.dumps({
                "trace_id": trace_id,
                "module_name": "visibility_report",
                "action": "calc_boundary_coverage.subprocess_start",
                "duration_ms": 0,
                "cmd": [sys.executable, str(script_path), "--json-only"],
                "cwd": str(self.project_root),
                "timeout_sec": 60,
            }, ensure_ascii=False))
            # Windows 下 subprocess 默认用 GBK 解码子进程输出，
            # check_boundary_coverage.py 输出含 emoji（✅/⚠️/❌）的 UTF-8 文本，
            # GBK 解码会触发 UnicodeDecodeError 导致 stdout/stderr 为空。
            # 显式指定 encoding='utf-8', errors='replace' 保证读取不崩溃。
            result = subprocess.run(
                [sys.executable, str(script_path), "--json-only"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                cwd=self.project_root,
            )
            elapsed_ms = round((time.time() - t0) * 1000, 2)
            logger.info(json.dumps({
                "trace_id": trace_id,
                "module_name": "visibility_report",
                "action": "calc_boundary_coverage.subprocess_done",
                "duration_ms": elapsed_ms,
                "returncode": result.returncode,
                "stdout_len": len(result.stdout) if result.stdout else 0,
                "stderr_len": len(result.stderr) if result.stderr else 0,
                "stderr_preview": (result.stderr[:500] if result.stderr else ""),
            }, ensure_ascii=False))

            # 3. 校验返回码（0=通过, 1=阻断, 2=脚本异常）
            if result.returncode not in (0, 1):
                logger.warning(json.dumps({
                    "trace_id": trace_id,
                    "module_name": "visibility_report",
                    "action": "calc_boundary_coverage.bad_returncode",
                    "duration_ms": elapsed_ms,
                    "returncode": result.returncode,
                    "stderr": result.stderr[:1000] if result.stderr else "",
                    "reason": f"返回码 {result.returncode} 非 0/1，脚本执行异常，返回 0.0",
                }, ensure_ascii=False))
                return 0.0

            # 4. 校验 stdout 非空
            if not result.stdout:
                logger.warning(json.dumps({
                    "trace_id": trace_id,
                    "module_name": "visibility_report",
                    "action": "calc_boundary_coverage.empty_stdout",
                    "duration_ms": elapsed_ms,
                    "returncode": result.returncode,
                    "reason": "stdout 为空，脚本未输出 JSON，返回 0.0",
                }, ensure_ascii=False))
                return 0.0

            # 5. 解析 JSON
            try:
                data = json.loads(result.stdout)
            except json.JSONDecodeError as e:
                logger.warning(json.dumps({
                    "trace_id": trace_id,
                    "module_name": "visibility_report",
                    "action": "calc_boundary_coverage.json_parse_failed",
                    "duration_ms": elapsed_ms,
                    "error": f"{type(e).__name__}: {e}",
                    "stdout_preview": result.stdout[:500],
                    "reason": "stdout 非合法 JSON（可能日志被混入 stdout），返回 0.0",
                }, ensure_ascii=False))
                return 0.0

            # 6. 提取字段并计算
            total = data.get("total_tests", 0)
            boundary = data.get("total_boundary_tests", 0)
            overall_status = data.get("overall_status", "unknown")
            logger.info(json.dumps({
                "trace_id": trace_id,
                "module_name": "visibility_report",
                "action": "calc_boundary_coverage.parsed",
                "duration_ms": elapsed_ms,
                "total_tests": total,
                "total_boundary_tests": boundary,
                "overall_status": overall_status,
                "blocked_modules": data.get("blocked_modules", []),
                "total_modules": data.get("total_modules", 0),
            }, ensure_ascii=False))

            if total == 0:
                logger.warning(json.dumps({
                    "trace_id": trace_id,
                    "module_name": "visibility_report",
                    "action": "calc_boundary_coverage.zero_total_tests",
                    "duration_ms": elapsed_ms,
                    "reason": "total_tests=0（测试目录为空或扫描未命中任何测试），返回 0.0",
                    "hint": "请检查 tests/ 目录是否有测试文件，以及 boundary_config.yaml 的 test_root 配置",
                }, ensure_ascii=False))
                return 0.0

            coverage = round(boundary / total * 100, 1)
            logger.info(json.dumps({
                "trace_id": trace_id,
                "module_name": "visibility_report",
                "action": "calc_boundary_coverage.success",
                "duration_ms": elapsed_ms,
                "total_tests": total,
                "total_boundary_tests": boundary,
                "coverage_percent": coverage,
            }, ensure_ascii=False))
            return coverage

        except subprocess.TimeoutExpired as e:
            logger.warning(json.dumps({
                "trace_id": trace_id,
                "module_name": "visibility_report",
                "action": "calc_boundary_coverage.timeout",
                "duration_ms": round((time.time() - t0) * 1000, 2),
                "error": f"{type(e).__name__}: {e}",
                "reason": "subprocess 执行超时（60s），返回 0.0",
                "hint": "请检查 check_boundary_coverage.py 是否有死循环或扫描范围过大",
            }, ensure_ascii=False))
        except (subprocess.SubprocessError, FileNotFoundError) as e:
            logger.warning(json.dumps({
                "trace_id": trace_id,
                "module_name": "visibility_report",
                "action": "calc_boundary_coverage.subprocess_error",
                "duration_ms": round((time.time() - t0) * 1000, 2),
                "error": f"{type(e).__name__}: {e}",
                "reason": "subprocess 执行失败，返回 0.0",
            }, ensure_ascii=False))
        return 0.0

    def _count_contract_tests(self) -> int:
        """统计契约测试数"""
        trace_id = _trace_id()
        t0 = time.time()
        contract_dir = self.project_root / "tests" / "contract"
        if not contract_dir.exists():
            logger.info(json.dumps({
                "trace_id": trace_id,
                "module_name": "visibility_report",
                "action": "count_contract_tests.dir_missing",
                "duration_ms": round((time.time() - t0) * 1000, 2),
                "path": str(contract_dir),
                "reason": "tests/contract/ 目录不存在，返回 0",
            }, ensure_ascii=False))
            return 0
        # 统计 contracts/ 下的 *_contract.json 文件数
        contracts_dir = contract_dir / "contracts"
        if contracts_dir.exists():
            count = len(list(contracts_dir.glob("*_contract.json")))
            logger.info(json.dumps({
                "trace_id": trace_id,
                "module_name": "visibility_report",
                "action": "count_contract_tests.success",
                "duration_ms": round((time.time() - t0) * 1000, 2),
                "contracts_dir": str(contracts_dir),
                "contract_count": count,
            }, ensure_ascii=False))
            return count
        logger.info(json.dumps({
            "trace_id": trace_id,
            "module_name": "visibility_report",
            "action": "count_contract_tests.contracts_subdir_missing",
            "duration_ms": round((time.time() - t0) * 1000, 2),
            "path": str(contracts_dir),
            "reason": "tests/contract/contracts/ 子目录不存在，返回 0",
            "hint": "请创建 tests/contract/contracts/ 并放置 *_contract.json 契约文件",
        }, ensure_ascii=False))
        return 0

    # ── 第三层：业务价值可见 ──
    def _collect_business_layer(self) -> LayerReport:
        """业务价值可见性指标"""
        layer = LayerReport(
            layer_name="业务价值可见",
            description="埋点覆盖率、看板数、告警规则数",
        )
        b_thresholds = self.thresholds.get("business", {})

        # 1. 埋点覆盖率（静态扫描 track( 调用）
        track_coverage = self._calc_track_coverage()
        layer.add_metric(Metric(
            name="track_event_coverage",
            value=track_coverage,
            threshold=b_thresholds.get("track_event_coverage", 50),
            unit="%",
            description="包含 trackEvent/track( 调用的核心模块占比",
        ))

        # 2. 看板数
        dashboard_count = self._count_dashboards()
        layer.add_metric(Metric(
            name="dashboard_count",
            value=dashboard_count,
            threshold=b_thresholds.get("dashboard_count", 1),
            unit="个",
            description="监控看板数量",
        ))

        # 3. 告警规则数
        alert_rules = self._count_alert_rules()
        layer.add_metric(Metric(
            name="alert_rules_count",
            value=alert_rules,
            threshold=b_thresholds.get("alert_rules_count", 1),
            unit="条",
            description="Prometheus 告警规则数量",
        ))

        return layer

    def _calc_track_coverage(self) -> float:
        """计算埋点覆盖率"""
        trace_id = _trace_id()
        t0 = time.time()
        agent_dir = self.project_root / "agent"
        if not agent_dir.exists():
            logger.warning(json.dumps({
                "trace_id": trace_id,
                "module_name": "visibility_report",
                "action": "calc_track.agent_dir_missing",
                "duration_ms": round((time.time() - t0) * 1000, 2),
                "reason": "agent/ 目录不存在，返回 0.0",
            }, ensure_ascii=False))
            return 0.0

        total_modules = 0
        tracked_modules = 0
        untracked_list = []
        # 优化：使用共享缓存，避免与 _calc_structured_log_coverage / _count_health_endpoints 重复 IO
        # 原代码对每个子目录单独 rglob+read_text，这里改为通过路径归属判断复用缓存内容。
        file_cache = self._scan_agent_files()
        for sub_dir in agent_dir.iterdir():
            if not sub_dir.is_dir() or sub_dir.name.startswith("_"):
                continue
            total_modules += 1
            # 检查该模块下是否有 trackEvent / track( / BusinessMetricsCollector 调用
            # 通过 relative_to 判断文件是否属于当前子目录，避免对每个子目录再次 rglob
            module_tracked = False
            for py_file, content in file_cache.items():
                try:
                    py_file.relative_to(sub_dir)
                except ValueError:
                    # 不属于当前子目录，跳过
                    continue
                if re.search(r'(trackEvent|BusinessMetricsCollector|track\()', content):
                    tracked_modules += 1
                    module_tracked = True
                    break
            if not module_tracked:
                untracked_list.append(sub_dir.name)

        elapsed_ms = round((time.time() - t0) * 1000, 2)
        if total_modules == 0:
            logger.info(json.dumps({
                "trace_id": trace_id,
                "module_name": "visibility_report",
                "action": "calc_track.no_modules",
                "duration_ms": elapsed_ms,
                "reason": "agent/ 下无业务子目录，视为通过（100%）",
            }, ensure_ascii=False))
            return 100.0
        coverage = round(tracked_modules / total_modules * 100, 1)
        logger.info(json.dumps({
            "trace_id": trace_id,
            "module_name": "visibility_report",
            "action": "calc_track.success",
            "duration_ms": elapsed_ms,
            "total_modules": total_modules,
            "tracked_modules": tracked_modules,
            "untracked_modules": untracked_list,
            "coverage_percent": coverage,
        }, ensure_ascii=False))
        return coverage

    def _count_dashboards(self) -> int:
        """统计看板数

        扫描两个标准看板目录：
          1. monitoring/grafana_dashboards/        —— 模板生成的功能看板（主目录）
          2. monitoring/grafana/dashboards/        —— 历史看板（兼容存量）

        异常处理：目录不存在或读取失败返回 0，不静默吞掉（由调用方记录）。
        """
        count = 0
        # 主目录：模板生成的功能看板
        primary_dir = self.project_root / "monitoring" / "grafana_dashboards"
        if primary_dir.exists():
            count += len(list(primary_dir.glob("*.json")))
        # 兼容目录：历史看板
        legacy_dir = self.project_root / "monitoring" / "grafana" / "dashboards"
        if legacy_dir.exists():
            count += len(list(legacy_dir.glob("*.json")))
        return count

    def _count_alert_rules(self) -> int:
        """统计告警规则数"""
        alerts_file = self.project_root / "monitoring" / "alerts.yml"
        if not alerts_file.exists():
            return 0
        try:
            content = alerts_file.read_text(encoding="utf-8")
            # 统计 - alert: 出现次数
            return len(re.findall(r'^\s*-\s*alert:\s', content, re.MULTILINE))
        except OSError:
            return 0

    # ── 第四层：架构影响可见 ──
    def _collect_architecture_layer(self) -> LayerReport:
        """架构影响可见性指标"""
        layer = LayerReport(
            layer_name="架构影响可见",
            description="依赖图、架构规则、变更影响分析",
        )
        a_thresholds = self.thresholds.get("architecture", {})

        # 1. 依赖图节点数
        dep_nodes, dep_edges = self._read_dependency_graph()
        layer.add_metric(Metric(
            name="dependency_graph_nodes",
            value=dep_nodes,
            threshold=a_thresholds.get("dependency_graph_nodes", 1),
            unit="个",
            description="模块依赖图节点数",
        ))

        # 2. 架构规则违规数
        arch_violations = self._read_arch_violations()
        # 违规数为逆向指标，阈值表示最大允许值
        max_violations = a_thresholds.get("max_arch_violations", 10)
        layer.add_metric(Metric(
            name="arch_rule_violations",
            value=arch_violations,
            threshold=0,  # 期望为 0
            unit="个",
            description="架构规则违规数（越少越好）",
            passed=arch_violations <= max_violations,
        ))

        # 3. 变更影响分析覆盖率
        impact_coverage = self._calc_impact_coverage()
        layer.add_metric(Metric(
            name="impact_analysis_coverage",
            value=impact_coverage,
            threshold=a_thresholds.get("impact_analysis_coverage", 80),
            unit="%",
            description="变更影响分析报告覆盖率",
        ))

        return layer

    def _read_dependency_graph(self) -> Tuple[int, int]:
        """读取依赖图节点/边数"""
        graph_json = self.project_root / "docs" / "architecture" / "dependency_graph.json"
        if not graph_json.exists():
            return (0, 0)
        try:
            data = json.loads(graph_json.read_text(encoding="utf-8"))
            nodes = len(data.get("nodes", []))
            edges = len(data.get("edges", []))
            return (nodes, edges)
        except (json.JSONDecodeError, OSError):
            return (0, 0)

    def _read_arch_violations(self) -> int:
        """读取架构规则违规数"""
        report_json = self.project_root / "docs" / "architecture" / "arch_rules_report.json"
        if not report_json.exists():
            return 0
        try:
            data = json.loads(report_json.read_text(encoding="utf-8"))
            return data.get("violations_count", data.get("total_violations", 0))
        except (json.JSONDecodeError, OSError):
            return 0

    def _calc_impact_coverage(self) -> float:
        """计算变更影响分析覆盖率"""
        # 检查是否有 impact_report.json
        impact_json = self.project_root / "docs" / "architecture" / "impact_report.json"
        if impact_json.exists():
            return 100.0
        # 检查是否有 impact_analysis.py 脚本
        impact_script = self.project_root / "scripts" / "impact_analysis.py"
        return 100.0 if impact_script.exists() else 0.0


# ═══════════════════════════════════════════════════════════════
#  报告生成器
# ═══════════════════════════════════════════════════════════════

class ReportGenerator:
    """可见性报告生成器"""

    def __init__(self, project_root: Path):
        self.project_root = project_root

    def generate_markdown(self, report: VisibilityReport) -> str:
        """生成 Markdown 报告"""
        lines: List[str] = []
        lines.append("# 四层可见性覆盖报告")
        lines.append("")
        lines.append(f"- **生成时间**：{report.timestamp}")
        lines.append(f"- **Trace ID**：`{report.trace_id}`")
        lines.append(f"- **生成耗时**：{report.duration_ms:.2f} ms")
        lines.append(f"- **总体状态**：{self._status_badge(report.overall_status)}")
        if report.threshold_violations:
            lines.append(f"- **阈值违规**：{len(report.threshold_violations)} 项")
        if report.error:
            lines.append(f"- **错误信息**：{report.error}")
        lines.append("")

        # 四层概览
        lines.append("## 四层概览")
        lines.append("")
        lines.append("| 层级 | 描述 | 状态 |")
        lines.append("| --- | --- | --- |")
        for layer in report.layers:
            status = "✅ 通过" if layer.overall_passed else "❌ 阈值未达标"
            lines.append(f"| {layer.layer_name} | {layer.description} | {status} |")
        lines.append("")

        # 各层详情
        for layer in report.layers:
            lines.append(f"## {layer.layer_name}")
            lines.append("")
            lines.append(f"_{layer.description}_")
            lines.append("")
            lines.append("| 指标 | 数值 | 阈值 | 状态 | 说明 |")
            lines.append("| --- | --- | --- | --- | --- |")
            for m in layer.metrics:
                threshold_str = f"≥ {m.threshold}" if m.passed is not None else "—"
                lines.append(
                    f"| `{m.name}` | {m.value}{m.unit} | {threshold_str} | "
                    f"{m.status_icon} | {m.description} |"
                )
            lines.append("")

        # 阈值违规清单
        if report.threshold_violations:
            lines.append("## 阈值违规清单")
            lines.append("")
            for v in report.threshold_violations:
                lines.append(f"- ❌ {v}")
            lines.append("")
            lines.append("> ⚠️ **CI 阻断**：上述阈值未达标，请补充对应的可见性能力后重试。")
        else:
            lines.append("## 阈值检查")
            lines.append("")
            lines.append("- ✅ 所有指标均达到阈值要求")
        lines.append("")

        lines.append("---")
        lines.append("_由 `scripts/visibility_report.py` 自动生成_")
        return "\n".join(lines)

    def generate_json(self, report: VisibilityReport) -> Dict[str, Any]:
        """生成 JSON 报告"""
        return {
            "trace_id": report.trace_id,
            "timestamp": report.timestamp,
            "duration_ms": report.duration_ms,
            "overall_status": report.overall_status,
            "threshold_violations": report.threshold_violations,
            "error": report.error,
            "layers": [
                {
                    "layer_name": l.layer_name,
                    "description": l.description,
                    "overall_passed": l.overall_passed,
                    "metrics": [
                        {
                            "name": m.name,
                            "value": m.value,
                            "threshold": m.threshold,
                            "unit": m.unit,
                            "description": m.description,
                            "passed": m.passed,
                            "details": m.details,
                        }
                        for m in l.metrics
                    ],
                }
                for l in report.layers
            ],
        }

    def _status_badge(self, status: str) -> str:
        return {
            "pass": "✅ 通过",
            "fail": "❌ 阈值未达标",
            "degraded": "⚠️ 降级（部分指标采集失败）",
        }.get(status, status)


# ═══════════════════════════════════════════════════════════════
#  降级报告
# ═══════════════════════════════════════════════════════════════

def _generate_degraded_report(error: Exception, output_path: Path) -> None:
    """生成降级报告"""
    trace_id = _trace_id()
    content = [
        "# 四层可见性覆盖报告（降级）",
        "",
        f"- **生成时间**：{datetime.now().isoformat()}",
        f"- **Trace ID**：`{trace_id}`",
        f"- **状态**：❌ 报告生成失败",
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
        "1. 检查 `config.yaml` 的 `visibility_thresholds` 配置",
        "2. 确认 `scripts/check_boundary_coverage.py` 可正常运行",
        "3. 确认 `agent/` 与 `tests/` 目录存在",
        "4. 如问题持续，请联系平台研发组",
        "",
        "---",
        "_降级报告：可见性报告生成过程中发生异常_",
    ]
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("\n".join(content), encoding="utf-8")
        logger.error(f"降级报告已输出: {output_path}")
    except OSError as e:
        logger.error(f"降级报告输出失败: {e}")


# ═══════════════════════════════════════════════════════════════
#  主流程
# ═══════════════════════════════════════════════════════════════

def load_thresholds(config_path: Path) -> Dict[str, Any]:
    """从 config.yaml 加载可见性阈值"""
    if not config_path.exists():
        logger.warning(f"配置文件不存在: {config_path}，使用默认阈值")
        return {}
    try:
        import yaml
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        return config.get("visibility_thresholds", {})
    except Exception as e:
        logger.warning(f"配置加载失败: {e}，使用默认阈值")
        return {}


def generate_report(project_root: Path, thresholds: Dict[str, Any]) -> VisibilityReport:
    """生成可见性报告"""
    trace_id = _trace_id()
    start = time.time()

    logger.info(json.dumps({
        "trace_id": trace_id,
        "module_name": "visibility_report",
        "action": "generate.start",
        "timestamp": datetime.now().isoformat(),
    }, ensure_ascii=False))

    collector = MetricCollector(project_root, thresholds)
    layers = collector.collect_all()

    # 收集阈值违规
    violations: List[str] = []
    for layer in layers:
        for m in layer.metrics:
            if m.passed is False:
                violations.append(
                    f"{layer.layer_name}.{m.name}: 实际={m.value}{m.unit}, "
                    f"阈值={m.threshold}{m.unit}"
                )

    overall_status = "fail" if violations else "pass"
    duration_ms = (time.time() - start) * 1000

    logger.info(json.dumps({
        "trace_id": trace_id,
        "module_name": "visibility_report",
        "action": "generate.complete",
        "duration_ms": round(duration_ms, 2),
        "overall_status": overall_status,
        "violations_count": len(violations),
    }, ensure_ascii=False))

    return VisibilityReport(
        trace_id=trace_id,
        timestamp=datetime.now().isoformat(),
        duration_ms=round(duration_ms, 2),
        layers=layers,
        overall_status=overall_status,
        threshold_violations=violations,
    )


def main(argv=None) -> int:
    """CLI 入口：0 通过 / 1 阈值未达标 / 2 异常"""
    parser = argparse.ArgumentParser(
        description="四层可见性覆盖报告生成器"
    )
    parser.add_argument(
        "--config", "-c",
        default=str(PROJECT_ROOT / "config.yaml"),
        help="配置文件路径（含 visibility_thresholds 段）",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="报告输出路径（默认 docs/observability/visibility_report_<date>.md）",
    )
    parser.add_argument(
        "--json-only",
        action="store_true",
        help="仅输出 JSON 到 stdout",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    _setup_logging(args.verbose)

    config_path = Path(args.config)
    thresholds = load_thresholds(config_path)

    # 输出路径
    if args.output:
        output_md = Path(args.output)
    else:
        date_str = datetime.now().strftime("%Y%m%d")
        output_md = PROJECT_ROOT / "docs" / "observability" / f"visibility_report_{date_str}.md"
    output_json = output_md.with_suffix(".json")

    try:
        report = generate_report(PROJECT_ROOT, thresholds)

        report_gen = ReportGenerator(PROJECT_ROOT)

        if args.json_only:
            print(json.dumps(report_gen.generate_json(report), ensure_ascii=False, indent=2))
            return 0 if report.overall_status == "pass" else 1

        md_content = report_gen.generate_markdown(report)
        json_content = report_gen.generate_json(report)

        output_md.parent.mkdir(parents=True, exist_ok=True)
        output_md.write_text(md_content, encoding="utf-8")
        output_json.write_text(
            json.dumps(json_content, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f"Markdown 报告: {output_md}")
        logger.info(f"JSON 报告: {output_json}")

        if report.threshold_violations:
            logger.error(f"❌ 可见性阈值未达标: {len(report.threshold_violations)} 项")
            for v in report.threshold_violations:
                logger.error(f"  - {v}")
            return 1
        return 0

    except Exception as e:
        logger.error(f"报告生成异常: {e}", exc_info=True)
        _generate_degraded_report(e, output_md)
        if args.json_only:
            print(json.dumps({
                "error": str(e),
                "error_type": type(e).__name__,
                "overall_status": "degraded",
            }, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.exit(main())
