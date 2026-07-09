"""脚本沙箱执行引擎 — 第三层（工具资源层）

文章描述的第三层:
    技能文件夹自带 Python 脚本模板。执行任务时，完整代码不在对话中传输，
    而是由后台直接运行，只将结果传给模型，极大地节约了成本。

本模块实现:
    - execute(skill_id, script_name, params): 沙箱执行 scripts/*.py
    - 安全限制: 超时、工作目录隔离、环境变量过滤
    - JSON 输出协议: 脚本通过 stdout 输出 JSON，只传结果不传代码
    - 结果捕获: stdout/stderr/退出码/耗时/生成文件

脚本约定:
    1. 脚本通过 stdin 接收 JSON 参数
    2. 脚本通过 stdout 输出 JSON 结果（最后一行为结果 JSON）
    3. 脚本可通过 stderr 输出日志（不传给模型，仅记录）
    4. 退出码 0=成功，非 0=失败

示例脚本 (scripts/main.py):
    import sys, json
    params = json.loads(sys.stdin.read())
    # 业务逻辑...
    result = {"summary": "处理完成", "data": [...]}
    print(json.dumps(result, ensure_ascii=False))

设计原则:
    - 安全第一: 超时/工作目录/环境变量 三重隔离
    - 边界显性化: 超时→SCRIPT_EXEC_TIMEOUT，失败→SCRIPT_EXEC_FAILED
    - 可观测: 输出结构化日志 (trace_id, module_name, action, duration_ms, exit_code)
    - trackEvent 埋点预留
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .file_store import SkillFileStore
from .observability import logger, emit_metric
from .exceptions import (
    SkillExecutionError,
    SkillNotFoundError,
    ErrorCode,
)


def _trace_id() -> str:
    return uuid.uuid4().hex[:16]


# ════════════════════════════════════════════════════════════
#  执行结果
# ════════════════════════════════════════════════════════════

class ExecutionResult:
    """脚本执行结果"""

    def __init__(self, *, skill_id: str, script_name: str,
                 success: bool, exit_code: int,
                 stdout: str, stderr: str,
                 duration_ms: float,
                 result: Any = None,
                 error: Optional[str] = None,
                 timed_out: bool = False,
                 validation_status: str = "skipped",
                 validation_errors: Optional[List[Dict[str, Any]]] = None):
        self.skill_id = skill_id
        self.script_name = script_name
        self.success = success
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.duration_ms = round(duration_ms, 2)
        self.result = result  # 解析后的 JSON 结果（如脚本输出了 JSON）
        self.error = error
        self.timed_out = timed_out
        # 输出后置验证门控: skipped | passed | failed
        self.validation_status = validation_status
        self.validation_errors = validation_errors or []

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "skill_id": self.skill_id,
            "script_name": self.script_name,
            "success": self.success,
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "layer": 3,
        }
        # 只把结果传给模型，不传代码和原始 stdout
        if self.result is not None:
            d["result"] = self.result
        else:
            d["result"] = self.stdout[-2000:] if self.stdout else ""
        if self.error:
            d["error"] = self.error
        if self.timed_out:
            d["timed_out"] = True
        # 输出验证门控状态(末尾追加,不破坏现有字段顺序)
        d["validation_status"] = self.validation_status
        if self.validation_errors:
            d["validation_errors"] = self.validation_errors
        # stderr 仅在失败时包含（调试用）
        if not self.success and self.stderr:
            d["stderr"] = self.stderr[-500:]
        return d


# ════════════════════════════════════════════════════════════
#  沙箱执行引擎
# ════════════════════════════════════════════════════════════

# 环境变量白名单（只传这些给脚本，防止泄露敏感信息）
_ENV_WHITELIST = {
    "PATH", "PYTHONPATH", "PYTHONUTF8", "PYTHONIOENCODING",
    "LANG", "LC_ALL", "SYSTEMROOT", "TEMP", "TMP",
    "HOME", "USERPROFILE", "APPDATA",
    "OS", "PROCESSOR_ARCHITECTURE",
}

# 默认超时（秒）
_DEFAULT_TIMEOUT = 30

# stdout 最大捕获字节数（防止内存爆炸）
_MAX_STDOUT_BYTES = 1024 * 1024  # 1MB
_MAX_STDERR_BYTES = 256 * 1024   # 256KB


class SkillExecutor:
    """脚本沙箱执行引擎 — 第三层

    安全机制:
        1. 超时限制: 默认 30 秒，可配置
        2. 工作目录: 设置为技能目录，隔离文件访问
        3. 环境变量: 白名单过滤，不传敏感信息
        4. 输出限制: stdout 最多 1MB，stderr 最多 256KB
        5. 路径检查: 脚本必须在 skills_repo 内（file_store 已实现）
    """

    def __init__(self, file_store: Optional[SkillFileStore] = None,
                 *, default_timeout: int = _DEFAULT_TIMEOUT,
                 python_exe: Optional[str] = None):
        self.fs = file_store or SkillFileStore()
        self.default_timeout = default_timeout
        self.python_exe = python_exe or sys.executable

    def execute(self, skill_id: str, script_name: str = "main.py",
                *, params: Optional[Dict[str, Any]] = None,
                timeout: Optional[int] = None) -> ExecutionResult:
        """执行技能脚本（第三层）

        代码不在对话中传输，后台直接运行，只将结果传给模型。

        Args:
            skill_id: 技能ID
            script_name: 脚本文件名（必须在 scripts/ 目录下）
            params: 传给脚本的参数（通过 stdin JSON 传入）
            timeout: 超时秒数（None 用默认值）

        Returns: ExecutionResult
        """
        t0 = time.time()
        tid = _trace_id()
        params = params or {}
        use_timeout = timeout or self.default_timeout

        # trackEvent 埋点预留
        # track_event('skill_execute', {'skill_id': skill_id, 'script': script_name})

        # 获取脚本路径（带安全检查）
        try:
            script_path = self.fs.get_script_path(skill_id, script_name)
        except SkillNotFoundError:
            raise SkillExecutionError(
                f"脚本不存在: {skill_id}/scripts/{script_name}",
                code=ErrorCode.SCRIPT_NOT_FOUND,
                skill_id=skill_id, script_name=script_name,
            )

        # 获取技能目录作为工作目录
        skill_dir = self.fs._skill_dir(skill_id)

        # 构建安全环境变量
        safe_env = self._build_safe_env()

        # 通过 stdin 传递参数
        stdin_data = json.dumps(params, ensure_ascii=False)

        # 预加载 output_schema(用于后置验证门控)
        output_schema = self._load_output_schema(skill_id)

        logger.info(json.dumps({
            "trace_id": tid,
            "module_name": "executor",
            "action": "execute.start",
            "skill_id": skill_id,
            "script_name": script_name,
            "timeout": use_timeout,
            "params_keys": list(params.keys()),
            "has_output_schema": bool(output_schema),
        }, ensure_ascii=False))

        try:
            proc = subprocess.run(
                [self.python_exe, "-u", str(script_path)],
                input=stdin_data,
                capture_output=True,
                text=True,
                timeout=use_timeout,
                cwd=str(skill_dir),
                env=safe_env,
                encoding="utf-8",
                errors="replace",
            )

            elapsed = (time.time() - t0) * 1000

            # 截断输出
            stdout = proc.stdout[-_MAX_STDOUT_BYTES:] if proc.stdout else ""
            stderr = proc.stderr[-_MAX_STDERR_BYTES:] if proc.stderr else ""

            success = proc.returncode == 0

            # 尝试解析 stdout 最后一行为 JSON 结果
            result_data = None
            if success and stdout:
                result_data = self._extract_json(stdout)

            # ── 输出后置验证门控 ──
            validation_status = "skipped"
            validation_errors: List[Dict[str, Any]] = []
            if success and result_data is not None and output_schema:
                validation_status, validation_errors = self._validate_output(
                    result_data, output_schema, skill_id,
                )
                if validation_status == "failed":
                    success = False

            # 记录执行结果
            logger.info(json.dumps({
                "trace_id": tid,
                "module_name": "executor",
                "action": "execute.end",
                "duration_ms": round(elapsed, 2),
                "skill_id": skill_id,
                "script_name": script_name,
                "exit_code": proc.returncode,
                "success": success,
                "stdout_chars": len(stdout),
                "stderr_chars": len(stderr),
                "validation_status": validation_status,
                "validation_errors_count": len(validation_errors),
            }, ensure_ascii=False))

            emit_metric("yunshu_skill_exec_latency_ms",
                        value=elapsed, kind="histogram",
                        labels={"skill_id": skill_id,
                                "success": str(success).lower()})
            emit_metric("yunshu_skill_exec_total",
                        value=1, kind="counter",
                        labels={"skill_id": skill_id,
                                "success": str(success).lower()})
            emit_metric("yunshu_skill_validation_total",
                        value=1, kind="counter",
                        labels={"skill_id": skill_id,
                                "status": validation_status})

            if not success:
                error_msg = f"脚本退出码 {proc.returncode}"
                if validation_status == "failed":
                    error_msg += (f"; 输出 schema 校验失败"
                                  f"({len(validation_errors)} 处)")
                return ExecutionResult(
                    skill_id=skill_id, script_name=script_name,
                    success=False, exit_code=proc.returncode,
                    stdout=stdout, stderr=stderr,
                    duration_ms=elapsed,
                    error=error_msg,
                    validation_status=validation_status,
                    validation_errors=validation_errors,
                )

            return ExecutionResult(
                skill_id=skill_id, script_name=script_name,
                success=True, exit_code=0,
                stdout=stdout, stderr=stderr,
                duration_ms=elapsed,
                result=result_data,
                validation_status=validation_status,
                validation_errors=validation_errors,
            )

        except subprocess.TimeoutExpired as e:
            elapsed = (time.time() - t0) * 1000
            logger.error(json.dumps({
                "trace_id": tid,
                "module_name": "executor",
                "action": "execute.timeout",
                "duration_ms": round(elapsed, 2),
                "skill_id": skill_id,
                "script_name": script_name,
                "timeout": use_timeout,
            }, ensure_ascii=False))
            emit_metric("yunshu_skill_exec_total",
                        value=1, kind="counter",
                        labels={"skill_id": skill_id,
                                "success": "false",
                                "reason": "timeout"})
            # 契约: 超时返回 ExecutionResult(timed_out=True) 而非抛异常
            # 见 skill_manager.execute docstring "超时返回 result 而非异常"
            return ExecutionResult(
                skill_id=skill_id, script_name=script_name,
                success=False, exit_code=-1,
                stdout="", stderr=str(e),
                duration_ms=elapsed,
                error=f"脚本执行超时（{use_timeout}秒）: {skill_id}/{script_name}",
                timed_out=True,
            )

        except Exception as e:
            elapsed = (time.time() - t0) * 1000
            if isinstance(e, SkillExecutionError):
                raise
            logger.error(json.dumps({
                "trace_id": tid,
                "module_name": "executor",
                "action": "execute.error",
                "duration_ms": round(elapsed, 2),
                "skill_id": skill_id,
                "script_name": script_name,
                "error": str(e),
            }, ensure_ascii=False))
            raise SkillExecutionError(
                f"脚本执行失败: {e}",
                code=ErrorCode.SCRIPT_EXEC_FAILED,
                skill_id=skill_id, script_name=script_name,
                duration_ms=elapsed,
                stderr=str(e),
            )

    # ──────────────────────────────────────────────
    #  内部方法
    # ──────────────────────────────────────────────

    def _build_safe_env(self) -> Dict[str, str]:
        """构建安全环境变量（白名单过滤）"""
        safe_env = {}
        for key in _ENV_WHITELIST:
            val = os.environ.get(key)
            if val is not None:
                safe_env[key] = val
        # 确保使用 UTF-8
        safe_env["PYTHONUTF8"] = "1"
        safe_env["PYTHONIOENCODING"] = "utf-8"
        return safe_env

    @staticmethod
    def _extract_json(stdout: str) -> Any:
        """从 stdout 中提取 JSON 结果（取最后一个 JSON 行）

        脚本可以在 stdout 输出多行日志，最后一行应为 JSON 结果。
        """
        if not stdout:
            return None
        lines = stdout.strip().splitlines()
        # 从后往前找 JSON 行
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            if line.startswith("{") or line.startswith("["):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
        # 如果没找到独立 JSON 行，尝试整体解析
        try:
            return json.loads(stdout.strip())
        except json.JSONDecodeError:
            return None

    # ──────────────────────────────────────────────
    #  输出 schema 后置验证门控
    # ──────────────────────────────────────────────

    def _load_output_schema(self, skill_id: str) -> Dict[str, Any]:
        """从 skill.md front matter 加载 output_schema"""
        try:
            meta = self.fs.get_metadata(skill_id) or {}
            schema = meta.get("output_schema") or {}
            if schema and not isinstance(schema, dict):
                logger.warning(
                    "[Executor] skill=%s output_schema 非对象,跳过校验",
                    skill_id,
                )
                return {}
            return schema
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[Executor] skill=%s 加载 output_schema 失败: %s",
                skill_id, e,
            )
            return {}

    def _validate_output(self, result: Any,
                         schema: Dict[str, Any],
                         skill_id: str) -> Tuple[str, List[Dict[str, Any]]]:
        """校验脚本输出是否符合 output_schema

        Returns:
            (status, errors): status ∈ {passed, failed, skipped}
        """
        if not schema:
            return "skipped", []

        try:
            import jsonschema
        except ImportError:
            logger.warning(
                "[Executor] jsonschema 未安装,跳过输出校验 (skill=%s)",
                skill_id,
            )
            return "skipped", []

        try:
            jsonschema.validate(instance=result, schema=schema)
            logger.info(
                "[Executor] skill=%s 输出 schema 校验通过",
                skill_id,
            )
            return "passed", []
        except jsonschema.ValidationError as e:
            path = ".".join(str(p) for p in e.absolute_path) or "(root)"
            errors = [{
                "field": path,
                "message": e.message,
                "validator": e.validator,
            }]
            logger.warning(
                "[Executor] skill=%s 输出 schema 校验失败 | path=%s | msg=%s",
                skill_id, path, e.message,
            )
            return "failed", errors
        except jsonschema.SchemaError as e:
            logger.error(
                "[Executor] skill=%s output_schema 本身非法: %s",
                skill_id, e.message,
            )
            return "skipped", [{
                "field": "(schema)",
                "message": f"output_schema 非法: {e.message}",
                "validator": "schema",
            }]

    # ──────────────────────────────────────────────
    #  健康检查
    # ──────────────────────────────────────────────

    def health(self) -> Dict[str, Any]:
        """健康检查"""
        try:
            return {
                "ok": True,
                "python_exe": self.python_exe,
                "default_timeout": self.default_timeout,
                "env_whitelist": list(_ENV_WHITELIST),
                "max_stdout_mb": _MAX_STDOUT_BYTES // (1024 * 1024),
                "layer": "executor",
            }
        except Exception as e:
            return {"ok": False, "error": str(e), "layer": "executor"}
