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
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
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

        # 1. 测试覆盖率（从 coverage.xml 读取真实 line-rate，不再降级到 pyproject.toml）
        test_coverage = self._read_test_coverage()
        layer.add_metric(Metric(
            name="test_coverage",
            value=test_coverage,
            threshold=v_thresholds.get("test_coverage", 40),
            unit="%",
            description="代码测试覆盖率（来自 coverage.xml 真实 line-rate）",
        ))

        # 2. 边界测试覆盖率（运行边界扫描脚本）
        # 【指标定义修订】阶段 2 起从「用例数比例」改为「已声明模块的必需场景覆盖率」
        # 详见 _calc_boundary_coverage() 的 docstring
        boundary_coverage = self._calc_boundary_coverage()
        layer.add_metric(Metric(
            name="boundary_test_coverage",
            value=boundary_coverage,
            threshold=v_thresholds.get("boundary_test_coverage", 10),
            unit="%",
            description="已声明模块的必需场景覆盖率（阶段2起采用，替代原用例数比例）",
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

        # 4. 异常处理覆盖率（核心业务模块中含 try/except/raise 的文件占比）
        # 呼应"边界显性化"原则：可能失败的分支必须显式处理异常或抛出带业务错误码的 Error
        exception_coverage = self._calc_exception_coverage()
        layer.add_metric(Metric(
            name="exception_coverage",
            value=exception_coverage,
            threshold=v_thresholds.get("exception_coverage", 60),
            unit="%",
            description="含 try/except/raise 异常处理的核心模块占比",
        ))

        return layer

    def _read_test_coverage(self) -> float:
        """读取测试覆盖率

        数据源：coverage.xml 的 line-rate（真实覆盖率）
        - CI 环境：由 full-project-tests job 生成，通过 full-coverage-report artifact 传递，
                  visibility-report job 下载后放置于项目根目录
        - 本地环境：需手动执行 `pytest --cov=agent --cov=scripts --cov-report=xml` 生成

        不再降级：coverage.xml 缺失或无效时直接返回 0.0 并输出 error 日志，
                  不再读取 pyproject.toml fail_under 作为基线。
                  原因：用配置基线（如 40%）掩盖真实覆盖率缺失会导致指标失真，
                  CI 中 coverage.xml 由 full-project-tests artifact 保证就位，
                  本地缺失即为真实问题，应显式暴露而非降级掩盖。

        【日志节点说明】
        本方法在以下 5 个关键逻辑节点输出结构化日志（含 trace_id/module_name/action/duration_ms）：
          1. read_test_coverage.enter         (debug)  方法入口，记录待检查路径
          2. read_test_coverage.missing_xml   (error)  coverage.xml 不存在 → 返回 0.0（不降级）
          3. read_test_coverage.success       (info)   line-rate>0 → 返回真实覆盖率
          4. read_test_coverage.invalid_xml   (warning) line-rate=0 → 返回 0.0（不降级）
          5. read_test_coverage.parse_failed  (error)  XML 解析异常 → 返回 0.0（不降级）
        每条日志均显式标注 no_fallback=true，确认未读取 pyproject.toml fail_under。
        """
        trace_id = _trace_id()
        t0 = time.time()
        coverage_xml = self.project_root / "coverage.xml"

        # 节点 1：方法入口（debug 级别，便于排查"为什么覆盖率是 0"）
        logger.debug(json.dumps({
            "trace_id": trace_id,
            "module_name": "visibility_report",
            "action": "read_test_coverage.enter",
            "duration_ms": 0,
            "path": str(coverage_xml),
            "exists": coverage_xml.exists(),
            "fallback_to_pyproject": False,
            "reason": "方法入口：开始读取测试覆盖率，将优先检查 coverage.xml，缺失即返回 0.0",
        }, ensure_ascii=False))

        if not coverage_xml.exists():
            # 节点 2：coverage.xml 不存在 → 返回 0.0（CI 中不应发生，本地需手动生成）
            # 显式不降级：不读取 pyproject.toml fail_under，避免用配置基线掩盖真实数据缺失
            elapsed_ms = round((time.time() - t0) * 1000, 2)
            logger.error(json.dumps({
                "trace_id": trace_id,
                "module_name": "visibility_report",
                "action": "read_test_coverage.missing_xml",
                "duration_ms": elapsed_ms,
                "path": str(coverage_xml),
                "return_value": 0.0,
                "no_fallback": True,
                "fallback_rejected": "pyproject.toml fail_under",
                "reason": "coverage.xml 不存在，返回 0.0（不降级到 pyproject.toml）",
                "ci_hint": "CI 中应由 full-project-tests job 上传 full-coverage-report artifact，visibility-report job 下载后放置于项目根目录",
                "local_hint": "本地运行可执行 `pytest --cov=agent --cov=scripts --cov-report=xml` 生成 coverage.xml",
            }, ensure_ascii=False))
            return 0.0

        import xml.etree.ElementTree as ET
        try:
            tree = ET.parse(coverage_xml)
            root = tree.getroot()
            line_rate = float(root.attrib.get("line-rate", "0"))
            if line_rate > 0:
                # 节点 3：成功读取有效 line-rate → 返回真实覆盖率百分比
                elapsed_ms = round((time.time() - t0) * 1000, 2)
                coverage_percent = round(line_rate * 100, 1)
                logger.info(json.dumps({
                    "trace_id": trace_id,
                    "module_name": "visibility_report",
                    "action": "read_test_coverage.success",
                    "duration_ms": elapsed_ms,
                    "path": str(coverage_xml),
                    "line_rate": line_rate,
                    "coverage_percent": coverage_percent,
                    "return_value": coverage_percent,
                    "no_fallback": True,
                    "source": "full-project-tests artifact (CI) 或本地生成",
                }, ensure_ascii=False))
                return coverage_percent
            # 节点 4：line-rate=0 → 视为无效数据，返回 0.0（不降级）
            # 常见原因：空报告、测试未覆盖任何行、或 coverage.py 生成失败
            elapsed_ms = round((time.time() - t0) * 1000, 2)
            logger.warning(json.dumps({
                "trace_id": trace_id,
                "module_name": "visibility_report",
                "action": "read_test_coverage.invalid_xml",
                "duration_ms": elapsed_ms,
                "path": str(coverage_xml),
                "line_rate": line_rate,
                "return_value": 0.0,
                "no_fallback": True,
                "fallback_rejected": "pyproject.toml fail_under",
                "reason": "coverage.xml line-rate=0，可能是空报告或测试未覆盖任何行，返回 0.0（不降级）",
            }, ensure_ascii=False))
            return 0.0
        except (ValueError, OSError, ET.ParseError) as e:
            # 节点 5：XML 解析异常 → 返回 0.0（不降级）
            # ET.ParseError 继承自 SyntaxError，不被 ValueError/OSError 覆盖，需显式捕获
            elapsed_ms = round((time.time() - t0) * 1000, 2)
            logger.error(json.dumps({
                "trace_id": trace_id,
                "module_name": "visibility_report",
                "action": "read_test_coverage.parse_failed",
                "duration_ms": elapsed_ms,
                "path": str(coverage_xml),
                "error": f"{type(e).__name__}: {e}",
                "return_value": 0.0,
                "no_fallback": True,
                "fallback_rejected": "pyproject.toml fail_under",
                "reason": "coverage.xml 解析失败，返回 0.0（不降级到 pyproject.toml）",
            }, ensure_ascii=False))
            return 0.0

    def _calc_boundary_coverage(self) -> float:
        """计算边界测试覆盖率

        【指标定义（阶段 2 起修订）】
        主指标：scene_coverage_percent（已声明模块的必需场景覆盖率）
          = sum(每个声明模块已覆盖的必需场景数) / sum(每个声明模块的必需场景总数) * 100
          数据源：check_boundary_coverage.py 输出的 scene_coverage_percent 字段
          优势：基于声明清单，反映「关键边界场景的覆盖完成度」，不受总测试数增长稀释影响

        降级指标：coverage_percent（用例数比例，向后兼容）
          = total_boundary_tests / total_tests * 100
          当 scene_coverage_percent 字段缺失时使用（旧版本兼容）

        数据源：调用 scripts/check_boundary_coverage.py --json-only
        排查要点：
          1. 脚本是否存在
          2. subprocess 执行是否超时
          3. returncode 是否为 0/1（2 表示脚本异常）
          4. stdout 是否为空（日志被混入 stdout 时会污染 JSON）
          5. JSON 字段 scene_total_count 是否为 0（无声明模块）
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
            # 新指标（推荐）：已声明模块的必需场景覆盖率
            # 优先使用 scene_coverage_percent，不存在时降级到 coverage_percent（向后兼容）
            scene_covered = data.get("scene_covered_count", 0)
            scene_total = data.get("scene_total_count", 0)
            scene_pct = data.get("scene_coverage_percent")
            overall_status = data.get("overall_status", "unknown")
            logger.info(json.dumps({
                "trace_id": trace_id,
                "module_name": "visibility_report",
                "action": "calc_boundary_coverage.parsed",
                "duration_ms": elapsed_ms,
                "total_tests": total,
                "total_boundary_tests": boundary,
                "scene_covered_count": scene_covered,
                "scene_total_count": scene_total,
                "scene_coverage_percent": scene_pct,
                "overall_status": overall_status,
                "blocked_modules": data.get("blocked_modules", []),
                "total_modules": data.get("total_modules", 0),
            }, ensure_ascii=False))

            # 新指标优先：scene_coverage_percent（基于声明模块的必需场景覆盖率）
            # 该指标不受总测试数增长稀释影响，更真实反映边界测试质量
            if scene_pct is not None and scene_total > 0:
                logger.info(json.dumps({
                    "trace_id": trace_id,
                    "module_name": "visibility_report",
                    "action": "calc_boundary_coverage.success",
                    "duration_ms": elapsed_ms,
                    "metric": "scene_coverage_percent",
                    "scene_covered_count": scene_covered,
                    "scene_total_count": scene_total,
                    "coverage_percent": scene_pct,
                    "legacy_case_ratio": round(boundary / total * 100, 1) if total else 0.0,
                }, ensure_ascii=False))
                return float(scene_pct)

            # 降级：无 scene_coverage_percent 字段时使用旧指标（用例数比例）
            if total == 0:
                logger.warning(json.dumps({
                    "trace_id": trace_id,
                    "module_name": "visibility_report",
                    "action": "calc_boundary_coverage.zero_total_tests",
                    "duration_ms": elapsed_ms,
                    "reason": "total_tests=0 且无 scene_coverage_percent，返回 0.0",
                    "hint": "请检查 tests/ 目录是否有测试文件，以及 boundary_config.yaml 的 test_root 配置",
                }, ensure_ascii=False))
                return 0.0

            coverage = round(boundary / total * 100, 1)
            logger.info(json.dumps({
                "trace_id": trace_id,
                "module_name": "visibility_report",
                "action": "calc_boundary_coverage.success",
                "duration_ms": elapsed_ms,
                "metric": "legacy_case_ratio",
                "total_tests": total,
                "total_boundary_tests": boundary,
                "coverage_percent": coverage,
                "reason": "scene_coverage_percent 字段缺失，降级使用用例数比例",
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

    def _calc_exception_coverage(self) -> float:
        """计算异常处理覆盖率

        定义：核心业务模块（agent/ 目录）中包含异常处理（try/except 或 raise）的
        .py 文件占比。呼应"边界显性化"原则——可能失败的分支必须显式处理异常
        或抛出带业务错误码的 Error，而非静默返回 null。

        数据源：AST 解析 agent/ 目录下所有 .py 文件
        判定标准：文件中存在 ast.Try 或 ast.Raise 节点即视为"已处理异常"

        异常处理：AST 解析失败时记录错误日志并跳过该文件，不计入分母
        """
        trace_id = _trace_id()
        t0 = time.time()
        agent_dir = self.project_root / "agent"
        if not agent_dir.exists():
            logger.warning(json.dumps({
                "trace_id": trace_id,
                "module_name": "visibility_report",
                "action": "calc_exception_coverage.dir_missing",
                "duration_ms": round((time.time() - t0) * 1000, 2),
                "path": str(agent_dir),
                "reason": "agent/ 目录不存在，返回 0.0",
            }, ensure_ascii=False))
            return 0.0

        total_files = 0
        handled_files = 0
        skipped_files: List[str] = []
        for py_file in agent_dir.rglob("*.py"):
            # 跳过 __init__.py 等 dunder 文件
            if py_file.name.startswith("__"):
                continue
            total_files += 1
            try:
                source = py_file.read_text(encoding="utf-8")
                tree = ast.parse(source, filename=str(py_file))
                # 遍历 AST，检查是否包含异常处理节点
                has_exception_handling = any(
                    isinstance(node, (ast.Try, ast.Raise))
                    for node in ast.walk(tree)
                )
                if has_exception_handling:
                    handled_files += 1
            except (SyntaxError, OSError, UnicodeDecodeError) as e:
                # 解析失败：记录错误并跳过，不计入分母
                skipped_files.append(str(py_file))
                total_files -= 1
                logger.warning(json.dumps({
                    "trace_id": trace_id,
                    "module_name": "visibility_report",
                    "action": "calc_exception_coverage.parse_failed",
                    "duration_ms": 0,
                    "path": str(py_file),
                    "error": f"{type(e).__name__}: {e}",
                    "reason": "AST 解析失败，跳过该文件",
                }, ensure_ascii=False))

        coverage = round(handled_files / total_files * 100, 1) if total_files else 0.0
        elapsed_ms = round((time.time() - t0) * 1000, 2)
        logger.info(json.dumps({
            "trace_id": trace_id,
            "module_name": "visibility_report",
            "action": "calc_exception_coverage.success",
            "duration_ms": elapsed_ms,
            "total_files": total_files,
            "handled_files": handled_files,
            "skipped_files": len(skipped_files),
            "coverage_percent": coverage,
        }, ensure_ascii=False))
        return coverage

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
#  Prometheus 指标导出
# ═══════════════════════════════════════════════════════════════

# 指标命名规范：yunshu_<模块>_<动作>（遵循项目硬约束）
# 所有 gauge 指标必须包含 success 标签（项目规范），visibility 上下文中：
#   success="true"  → 该指标 passed=True（达到阈值）
#   success="false" → 该指标 passed=False（未达标）
# 总体状态使用单独 gauge（yunshu_visibility_overall_status）以 0/1/2 编码。

_VIS_METRIC_PREFIX = "yunshu_visibility"

# 层级名称 → Prometheus label value（英文小写下划线，便于 PromQL 查询）
_LAYER_LABEL_MAP = {
    "运行时可见": "runtime",
    "验证过程可见": "verification",
    "业务价值可见": "business",
    "架构影响可见": "architecture",
}

# 逆向指标（值越小越好）：导出时 success 标签取反映阈值的语义
# 例如 arch_rule_violations：passed=True 当 violations ≤ max_violations
_INVERSE_METRICS = {"arch_rule_violations"}


def export_to_prometheus(report: VisibilityReport) -> str:
    """将四层可见性报告导出为 Prometheus exposition 格式文本

    输出指标清单（所有指标均为 gauge 类型）：
      - yunshu_visibility_overall_status{status}  总体状态（0=pass, 1=fail, 2=degraded）
      - yunshu_visibility_threshold_violations_total  阈值违规项总数
      - yunshu_visibility_layer_passed{layer, success}  各层是否达标（0/1）
      - yunshu_visibility_runtime_structured_log_coverage{success}
      - yunshu_visibility_runtime_trace_coverage{success}
      - yunshu_visibility_runtime_health_endpoints{success}
      - yunshu_visibility_verification_test_coverage{success}
      - yunshu_visibility_verification_boundary_test_coverage{success}
      - yunshu_visibility_verification_contract_test_count{success}
      - yunshu_visibility_business_track_event_coverage{success}
      - yunshu_visibility_business_dashboard_count{success}
      - yunshu_visibility_business_alert_rules_count{success}
      - yunshu_visibility_architecture_dependency_graph_nodes{success}
      - yunshu_visibility_architecture_dependency_graph_edges{success}
      - yunshu_visibility_architecture_rule_violations{success}
      - yunshu_visibility_architecture_impact_analysis_coverage{success}
      - yunshu_visibility_report_duration_seconds  报告生成耗时
      - yunshu_visibility_up  服务存活探针（恒为 1）

    Args:
        report: 已生成的 VisibilityReport 对象

    Returns:
        Prometheus 文本格式字符串，可直接通过 /metrics 端点暴露
    """
    trace_id = _trace_id()
    t0 = time.time()
    lines: List[str] = []
    timestamp_ms = int(time.time() * 1000)

    # ── 总体状态指标 ──
    status_code = {"pass": 0, "fail": 1, "degraded": 2}.get(report.overall_status, 2)
    lines.append(f"# HELP {_VIS_METRIC_PREFIX}_overall_status Overall visibility status (0=pass, 1=fail, 2=degraded)")
    lines.append(f"# TYPE {_VIS_METRIC_PREFIX}_overall_status gauge")
    lines.append(
        f'{_VIS_METRIC_PREFIX}_overall_status{{status="{report.overall_status}"}} {status_code} {timestamp_ms}'
    )

    lines.append(f"# HELP {_VIS_METRIC_PREFIX}_threshold_violations_total Total number of threshold violations")
    lines.append(f"# TYPE {_VIS_METRIC_PREFIX}_threshold_violations_total gauge")
    lines.append(
        f"{_VIS_METRIC_PREFIX}_threshold_violations_total {len(report.threshold_violations)} {timestamp_ms}"
    )

    lines.append(f"# HELP {_VIS_METRIC_PREFIX}_report_duration_seconds Visibility report generation duration in seconds")
    lines.append(f"# TYPE {_VIS_METRIC_PREFIX}_report_duration_seconds gauge")
    lines.append(
        f"{_VIS_METRIC_PREFIX}_report_duration_seconds {report.duration_ms / 1000.0:.4f} {timestamp_ms}"
    )

    lines.append(f"# HELP {_VIS_METRIC_PREFIX}_up Visibility exporter liveness probe")
    lines.append(f"# TYPE {_VIS_METRIC_PREFIX}_up gauge")
    lines.append(f"{_VIS_METRIC_PREFIX}_up 1 {timestamp_ms}")

    # ── 各层达标状态 ──
    lines.append(f"# HELP {_VIS_METRIC_PREFIX}_layer_passed Whether a visibility layer passed its threshold (0/1)")
    lines.append(f"# TYPE {_VIS_METRIC_PREFIX}_layer_passed gauge")
    for layer in report.layers:
        layer_label = _LAYER_LABEL_MAP.get(layer.layer_name, layer.layer_name)
        passed_int = 1 if layer.overall_passed else 0
        success_label = "true" if layer.overall_passed else "false"
        lines.append(
            f'{_VIS_METRIC_PREFIX}_layer_passed{{layer="{layer_label}",success="{success_label}"}} {passed_int} {timestamp_ms}'
        )

    # ── 各层明细指标 ──
    # 同一指标名可能出现多次（不同 layer 下的同名 metric 会被分别导出），
    # 这里通过 layer 标签区分，保证 PromQL 可按层聚合。
    seen_metric_help: set = set()
    for layer in report.layers:
        layer_label = _LAYER_LABEL_MAP.get(layer.layer_name, layer.layer_name)
        for m in layer.metrics:
            # 指标名规范化：runtime_structured_log_coverage 等
            prom_name = f"{_VIS_METRIC_PREFIX}_{layer_label}_{m.name}"
            if prom_name not in seen_metric_help:
                lines.append(f"# HELP {prom_name} {m.description or m.name}")
                lines.append(f"# TYPE {prom_name} gauge")
                seen_metric_help.add(prom_name)
            success_label = "true" if m.passed else "false"
            # 数值规范化：确保输出为浮点数
            try:
                value = float(m.value)
            except (TypeError, ValueError):
                logger.warning(json.dumps({
                    "trace_id": trace_id,
                    "module_name": "visibility_report",
                    "action": "export_prometheus.invalid_value",
                    "duration_ms": round((time.time() - t0) * 1000, 2),
                    "metric": m.name,
                    "raw_value": m.value,
                    "reason": "指标值无法转为 float，跳过该指标",
                }, ensure_ascii=False))
                continue
            lines.append(
                f'{prom_name}{{layer="{layer_label}",success="{success_label}"}} {value} {timestamp_ms}'
            )

    elapsed_ms = round((time.time() - t0) * 1000, 2)
    logger.info(json.dumps({
        "trace_id": trace_id,
        "module_name": "visibility_report",
        "action": "export_prometheus.success",
        "duration_ms": elapsed_ms,
        "lines": len(lines),
        "metrics_count": len(seen_metric_help),
        "overall_status": report.overall_status,
    }, ensure_ascii=False))
    return "\n".join(lines) + "\n"


# ═══════════════════════════════════════════════════════════════
#  Prometheus HTTP 指标服务
# ═══════════════════════════════════════════════════════════════

class _VisibilityMetricsState:
    """指标服务共享状态

    持有最近一次报告快照与互斥锁，支持后台线程定期刷新、HTTP handler 即时读取。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._snapshot: str = ""
        self._report_status: str = "degraded"
        self._last_update: float = 0.0
        self._error: Optional[str] = None

    def update(self, prometheus_text: str, status: str, error: Optional[str] = None) -> None:
        with self._lock:
            self._snapshot = prometheus_text
            self._report_status = status
            self._error = error
            self._last_update = time.time()

    def snapshot(self) -> Tuple[str, str, Optional[str], float]:
        with self._lock:
            return self._snapshot, self._report_status, self._error, self._last_update


def _build_prometheus_handler(state: "_VisibilityMetricsState") -> type:
    """构造绑定到共享状态的 HTTP handler 类"""

    class _MetricsHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            # 抑制默认 access log，避免污染 stdout
            pass

        def do_GET(self) -> None:  # noqa: N802
            trace_id = _trace_id()
            t0 = time.time()
            if self.path in ("/metrics", "/metrics/"):
                snapshot, status, error, last_update = state.snapshot()
                if not snapshot:
                    # 尚未生成首份报告：返回降级指标
                    timestamp_ms = int(time.time() * 1000)
                    body = (
                        f"# HELP {_VIS_METRIC_PREFIX}_up Visibility exporter liveness probe\n"
                        f"# TYPE {_VIS_METRIC_PREFIX}_up gauge\n"
                        f"{_VIS_METRIC_PREFIX}_up 1 {timestamp_ms}\n"
                        f"# HELP {_VIS_METRIC_PREFIX}_overall_status Overall visibility status (0=pass, 1=fail, 2=degraded)\n"
                        f"# TYPE {_VIS_METRIC_PREFIX}_overall_status gauge\n"
                        f'{_VIS_METRIC_PREFIX}_overall_status{{status="degraded"}} 2 {timestamp_ms}\n'
                    )
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(body.encode("utf-8"))
                    logger.info(json.dumps({
                        "trace_id": trace_id,
                        "module_name": "visibility_report",
                        "action": "metrics_endpoint.empty_snapshot",
                        "duration_ms": round((time.time() - t0) * 1000, 2),
                        "reason": "首份报告尚未生成，返回降级指标",
                    }, ensure_ascii=False))
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
                self.end_headers()
                self.wfile.write(snapshot.encode("utf-8"))
                logger.info(json.dumps({
                    "trace_id": trace_id,
                    "module_name": "visibility_report",
                    "action": "metrics_endpoint.served",
                    "duration_ms": round((time.time() - t0) * 1000, 2),
                    "status": status,
                    "snapshot_age_sec": round(time.time() - last_update, 2),
                    "error": error,
                }, ensure_ascii=False))
            elif self.path in ("/health", "/status", "/"):
                # 健康检查端点（遵循可观测性约束：必须暴露依赖连接状态）
                _, status, error, last_update = state.snapshot()
                payload = {
                    "ok": True,
                    "status": status,
                    "last_update": datetime.fromtimestamp(last_update).isoformat() if last_update else None,
                    "error": error,
                }
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            else:
                self.send_response(404)
                self.end_headers()

    return _MetricsHandler


def serve_metrics(
    port: int,
    refresh_interval: int,
    project_root: Path,
    thresholds: Dict[str, Any],
    host: str = "0.0.0.0",
) -> int:
    """启动 Prometheus 指标 HTTP 服务

    定期重新采集指标并刷新共享快照；暴露：
      - GET /metrics  → Prometheus exposition 文本
      - GET /health   → 健康检查 JSON

    Args:
        port: 监听端口（默认 9101）
        refresh_interval: 报告刷新间隔（秒，默认 300）
        project_root: 项目根目录
        thresholds: 可见性阈值配置
        host: 绑定地址

    Returns:
        进程退出码（0=正常退出，1=端口占用/初始化失败）
    """
    trace_id = _trace_id()
    state = _VisibilityMetricsState()

    def _refresh_loop() -> None:
        """后台刷新线程：周期性重新采集指标"""
        thread_trace = _trace_id()
        while True:
            t0 = time.time()
            try:
                report = generate_report(project_root, thresholds)
                prom_text = export_to_prometheus(report)
                state.update(prom_text, report.overall_status, error=None)
                logger.info(json.dumps({
                    "trace_id": thread_trace,
                    "module_name": "visibility_report",
                    "action": "serve_metrics.refresh.success",
                    "duration_ms": round((time.time() - t0) * 1000, 2),
                    "overall_status": report.overall_status,
                    "next_refresh_sec": refresh_interval,
                }, ensure_ascii=False))
            except Exception as e:
                # 边界显性化：捕获异常并写入 state.error，不静默吞掉
                logger.error(json.dumps({
                    "trace_id": thread_trace,
                    "module_name": "visibility_report",
                    "action": "serve_metrics.refresh.failed",
                    "duration_ms": round((time.time() - t0) * 1000, 2),
                    "error": f"{type(e).__name__}: {e}",
                    "stack": traceback.format_exc(),
                }, ensure_ascii=False))
                state.update("", "degraded", error=f"{type(e).__name__}: {e}")
            time.sleep(max(refresh_interval, 10))

    # 启动后台刷新线程（daemon=True，主进程退出时自动终止）
    refresh_thread = threading.Thread(target=_refresh_loop, name="visibility-refresh", daemon=True)
    refresh_thread.start()

    # 启动 HTTP 服务
    handler_cls = _build_prometheus_handler(state)
    try:
        server = HTTPServer((host, port), handler_cls)
    except OSError as e:
        logger.error(json.dumps({
            "trace_id": trace_id,
            "module_name": "visibility_report",
            "action": "serve_metrics.bind_failed",
            "duration_ms": 0,
            "host": host,
            "port": port,
            "error": f"{type(e).__name__}: {e}",
            "reason": "端口占用或绑定失败",
        }, ensure_ascii=False))
        return 1

    logger.info(json.dumps({
        "trace_id": trace_id,
        "module_name": "visibility_report",
        "action": "serve_metrics.started",
        "duration_ms": 0,
        "host": host,
        "port": port,
        "refresh_interval_sec": refresh_interval,
        "endpoints": ["/metrics", "/health"],
    }, ensure_ascii=False))

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info(json.dumps({
            "trace_id": trace_id,
            "module_name": "visibility_report",
            "action": "serve_metrics.shutdown",
            "duration_ms": 0,
            "reason": "收到 KeyboardInterrupt，正在关闭",
        }, ensure_ascii=False))
        server.shutdown()
    return 0


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
    parser.add_argument(
        "--export-metrics",
        action="store_true",
        help="输出 Prometheus exposition 格式指标到 stdout（用于 node/textfile 或一次性采集）",
    )
    parser.add_argument(
        "--metrics-output", "-m",
        default=None,
        help="Prometheus 指标输出文件路径（默认随 --export-metrics 输出到 stdout）",
    )
    parser.add_argument(
        "--serve-metrics",
        action="store_true",
        help="启动 HTTP 指标服务（暴露 /metrics 与 /health 端点供 Prometheus 抓取）",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=9101,
        help="指标 HTTP 服务端口（默认 9101，仅 --serve-metrics 时生效）",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="指标 HTTP 服务绑定地址（默认 0.0.0.0，仅 --serve-metrics 时生效）",
    )
    parser.add_argument(
        "--refresh-interval",
        type=int,
        default=300,
        help="指标刷新间隔（秒，默认 300，仅 --serve-metrics 时生效）",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    _setup_logging(args.verbose)

    config_path = Path(args.config)
    thresholds = load_thresholds(config_path)

    # ── 模式 1：HTTP 指标服务（最长生命周期，优先处理）──
    if args.serve_metrics:
        return serve_metrics(
            port=args.port,
            refresh_interval=args.refresh_interval,
            project_root=PROJECT_ROOT,
            thresholds=thresholds,
            host=args.host,
        )

    # ── 模式 2：一次性 Prometheus 指标导出 ──
    if args.export_metrics:
        try:
            report = generate_report(PROJECT_ROOT, thresholds)
            prom_text = export_to_prometheus(report)
            if args.metrics_output:
                output_path = Path(args.metrics_output)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text(prom_text, encoding="utf-8")
                logger.info(f"Prometheus 指标已写入: {output_path}")
            else:
                # 输出到 stdout（注意：不要混入日志，否则污染 textfile collector）
                sys.stdout.write(prom_text)
            return 0 if report.overall_status == "pass" else 1
        except Exception as e:
            logger.error(f"Prometheus 指标导出异常: {e}", exc_info=True)
            # 边界显性化：导出失败时输出降级指标，保证 /metrics 不空
            timestamp_ms = int(time.time() * 1000)
            degraded_text = (
                f"# HELP {_VIS_METRIC_PREFIX}_up Visibility exporter liveness probe\n"
                f"# TYPE {_VIS_METRIC_PREFIX}_up gauge\n"
                f"{_VIS_METRIC_PREFIX}_up 1 {timestamp_ms}\n"
                f"# HELP {_VIS_METRIC_PREFIX}_overall_status Overall visibility status (0=pass, 1=fail, 2=degraded)\n"
                f"# TYPE {_VIS_METRIC_PREFIX}_overall_status gauge\n"
                f'{_VIS_METRIC_PREFIX}_overall_status{{status="degraded"}} 2 {timestamp_ms}\n'
            )
            if args.metrics_output:
                Path(args.metrics_output).write_text(degraded_text, encoding="utf-8")
            else:
                sys.stdout.write(degraded_text)
            return 2

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
