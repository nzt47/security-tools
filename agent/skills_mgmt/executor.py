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
from agent.logging_utils import log_dict


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


#: effect 的严宽序（与 `agent.lines.models._EFFECT_ORDER` 同值域；本地只用于「取更严的一个」）
_EFFECT_RANK = {"read": 0, "write": 1, "execute": 2, "extend": 3}

#: 闸门不可用时的留痕事件名（技能脚本执行面；与 `agent/tools/__init__.py` 的
#: `tool_gate_import_failed` 区分开 —— 便于巡检分辨「哪条链路的闸门没装上」）
SKILL_GATE_UNAVAILABLE_EVENT = "skill_exec_gate_unavailable"

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

        # ── 确认闸门（A2/S3）──────────────────────────────────────────────
        # 【改动前的事实】这条链路**整条不过闸门**：routes_skills_mgmt →
        #   service.execute_skill_script → 本方法 → subprocess.run(python) 起子进程执行
        #   任意技能脚本，而本模块不 import `agent.tool_gate`（审计 S3 / Q1 §5.3）。
        #   它也因此不在 L0–L3 任何一级里、不落 `tool.confirm_decision`。
        # 【为什么收口在这里】本方法是**所有**执行入口的必经点（HTTP 两条路由、
        #   SkillManager、评估器 runner 都汇到 `SkillExecutor.execute`）。
        # 【effect=execute 从哪来】见 `_capability_descriptor` 的 docstring（技能域没有
        #   `confirm_level`，`skill.md` 也没有 `effect`；清单里那个值是派生值）。
        gate_denied = self._confirm_gate_outcome(skill_id, script_name, params)
        if gate_denied is not None:
            elapsed_ms = (time.time() - t0) * 1000
            gate_text = self._gate_error_text(gate_denied)
            logger.warning(log_dict({
                "module_name": "executor", "action": "execute.blocked_by_confirm_gate",
                "skill_id": skill_id, "script_name": script_name,
                "error_code": str(gate_denied.get("error_code") or ""),
                "confirm_level": str(gate_denied.get("confirm_level") or ""),
                "approval_id": str(gate_denied.get("approval_id") or ""),
                "degraded": bool(gate_denied.get("degraded", False)),
            }))
            emit_metric("yunshu_skill_exec_total",
                        value=1, kind="counter",
                        labels={"skill_id": skill_id, "success": "false",
                                "reason": "confirm_gate"})
            # 契约与超时分支一致：返回 ExecutionResult（不抛异常），让上层原样回传
            return ExecutionResult(
                skill_id=skill_id, script_name=script_name,
                success=False, exit_code=-1,
                stdout="", stderr=gate_text,
                duration_ms=elapsed_ms,
                error=gate_text,
            )

        logger.info(log_dict({'module_name': 'executor', 'action': 'execute.start', 'skill_id': skill_id, 'script_name': script_name, 'timeout': use_timeout, 'params_keys': list(params.keys()), 'has_output_schema': bool(output_schema)}))

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
            logger.info(log_dict({'module_name': 'executor', 'action': 'execute.end', 'skill_id': skill_id, 'script_name': script_name, 'exit_code': proc.returncode, 'success': success, 'stdout_chars': len(stdout), 'stderr_chars': len(stderr), 'validation_status': validation_status, 'validation_errors_count': len(validation_errors)}))

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
            logger.error(log_dict({'module_name': 'executor', 'action': 'execute.timeout', 'skill_id': skill_id, 'script_name': script_name, 'timeout': use_timeout}))
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
            logger.error(log_dict({'module_name': 'executor', 'action': 'execute.error', 'skill_id': skill_id, 'script_name': script_name, 'error': str(e)}))
            raise SkillExecutionError(
                f"脚本执行失败: {e}",
                code=ErrorCode.SCRIPT_EXEC_FAILED,
                skill_id=skill_id, script_name=script_name,
                duration_ms=elapsed,
                stderr=str(e),
            )

    # ──────────────────────────────────────────────
    #  确认闸门（A2/S3：技能脚本执行面原先**整条不过闸门**）
    # ──────────────────────────────────────────────

    def _capability_descriptor(self, skill_id: str, script_name: str) -> Dict[str, str]:
        """技能脚本执行面的**能力标识 + 治理三轴**（plane/effect/risk）

        【effect 从哪来 —— 这条必须写清，否则就是拍脑袋定级】
          · 技能域**没有** `confirm_level` 字段（那是工具侧 `data/tool_definitions/*.yaml`
            的概念），`skill.md` 的 front matter 里也**没有** `effect` 字段（实测
            `scripted-selftest` 的 front matter 只有 id/name/description/category/tags/
            version/enabled/status/author/source/content_type/default_params）；
          · 能力清单 `data/capability_manifest.json` 里那个 `effect` 是**派生值**，
            派生规则在 `agent/lines/callability.py:1159`：
            `"read" if not facts.get("has_scripts") else "execute"`
            （`has_scripts` = `data/skills_repo/<id>/scripts/*.py` 是否存在）；
          · 本方法只在**即将真的执行一个脚本**时被调用 ⇒ `has_scripts` 恒为真 ⇒
            `effect="execute"`。这不是猜测，是同一事实在同一时刻的等价判定。

        【从严取向（三处）】
          ① front matter 里**将来**若声明了 `effect`，取「声明值 vs 派生值」中**更严**的
             一个（声明 `read` 而实际在跑脚本时，不能让声明把级别降下去）；
          ② `risk` 同理：声明了用声明的，没声明按 `low`（与清单派生一致）；
          ③ 取值非法/读不到 ⇒ 按最严（`effect=extend` / `risk=high` ⇒ L3）处置 —— 由
             `agent.tool_gate.check_declared_capability` 承接（它自己也会对非法 effect
             按最严处理，两层不冲突）。

        Returns:
            `{"capability","plane","effect","risk"}`；`plane` 固定 `resident`（技能平面，
            与清单 `agent/lines/callability.py:1158` 的取值一致）。
        """
        declared: Dict[str, Any] = {}
        try:
            meta = self.fs.get_metadata(skill_id) or {}
            if isinstance(meta, dict):
                declared = meta
        except Exception as e:  # noqa: BLE001  读不到 front matter ⇒ 用最严的缺省（见下）
            logger.warning("[Executor] skill=%s 读取技能元数据失败（按最严处置）: %s",
                           skill_id, e)

        effect_derived = "execute"          # 见 docstring：本方法只在执行脚本时被调用
        effect_declared = str(declared.get("effect") or "").strip().lower()
        if effect_declared in _EFFECT_RANK:
            effect = max((effect_declared, effect_derived), key=lambda e: _EFFECT_RANK[e])
        else:
            effect = effect_derived

        risk_declared = str(declared.get("risk") or "").strip().lower()
        risk = risk_declared if risk_declared in ("low", "medium", "high", "critical") else "low"

        return {
            "capability": "skill." + str(skill_id or "").strip(),
            "plane": "resident",
            "effect": effect,
            "risk": risk,
        }

    def _gate_unavailable_outcome(self, skill_id: str, script_name: str,
                                  desc: Dict[str, str],
                                  exc: BaseException) -> Dict[str, Any]:
        """闸门不可用 ⇒ **拒绝**本次脚本执行（fail-closed），并留结构化事件

        【为什么这里比工具侧的降级更严】工具侧（`agent/tools/__init__.py`）对 L0/L1
        仍放行（否则闸门一坏，日常读写全封）；而本路径只有**一种**能力类别，且它是
        `effect=execute`（跑任意 Python）—— 没有注入防御层、没有限流层、没有注册表
        兜底，只有本层。证不出它被治理过就放行，代价无上界。
        """
        payload = {
            "event": SKILL_GATE_UNAVAILABLE_EVENT,
            "module_name": "executor",
            "action": "skill.exec_gate_unavailable",
            "skill_id": str(skill_id or ""),
            "capability": desc.get("capability", ""),
            "effect": desc.get("effect", ""),
            "risk": desc.get("risk", ""),
            "decision": "denied",
            "error": "%s: %s" % (type(exc).__name__, exc),
        }
        try:
            logger.error(log_dict(payload))
        except Exception:  # noqa: BLE001  结构化日志不可用 ⇒ 退回普通日志（仍不静默）
            logger.error("[Executor] %s skill=%s error=%s",
                         SKILL_GATE_UNAVAILABLE_EVENT, skill_id, payload["error"])
        try:
            from agent.audit.chain import SOURCE_SYSTEM  # noqa: PLC0415
            from agent.audit.facade import record as _audit_record  # noqa: PLC0415
            _audit_record(action=SKILL_GATE_UNAVAILABLE_EVENT,
                          subject="skill:" + str(skill_id or ""),
                          extra=payload, source=SOURCE_SYSTEM)
        except Exception as e:  # noqa: BLE001  留痕失败不得改变判定（但绝不静默）
            logger.error("[Executor] 技能闸门降级事件审计写入失败: %s: %s",
                         type(e).__name__, e)
        return {
            "ok": False, "blocked": True,
            "error_code": "PERMISSION_DENIED",
            "error_code_detail": SKILL_GATE_UNAVAILABLE_EVENT,
            "error": ("技能脚本执行**必须**经过确认闸门，但闸门当前不可用（%s）"
                      "⇒ 按 fail-closed **拒绝**执行 %s/scripts/%s。"
                      % (type(exc).__name__, skill_id, script_name)),
            "tool": desc.get("capability", ""),
            "confirm_level": "L3",
            "degraded": True,
        }

    def _confirm_gate_outcome(self, skill_id: str, script_name: str,
                              params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """执行脚本**之前**过确认闸门 → ``None`` = 放行；dict = 拒绝/待审批结果

        【为什么必须走闸门而不是自己判级】判级口径（`derive_confirm_level`）与裁决流程
        （身份 / 非交互 / SA 预授权 / 审批闭环 / 审计留痕）都在 `agent.tool_gate` 里，
        自己抄一份就是第二份口径（D1）。本方法只负责**如实声明**这个能力的三轴。
        【为什么用 `check_declared_capability` 而不是 `check_tool_call`】技能在
        `data/tool_definitions/*.yaml` 里**没有条目**：用后者会被判成「未登记 ⇒ 放行」
        （`_confirm_level_of` 返回空级别）—— 那正是审计 S3 说的「绕闸」。
        """
        desc = self._capability_descriptor(skill_id, script_name)
        try:
            from agent.tool_gate import check_declared_capability  # noqa: PLC0415 惰性
        except Exception as e:  # noqa: BLE001  闸门装不上 ⇒ 拒（见 _gate_unavailable_outcome）
            return self._gate_unavailable_outcome(skill_id, script_name, desc, e)
        try:
            return check_declared_capability(
                desc["capability"], plane=desc["plane"], effect=desc["effect"],
                risk=desc["risk"], args=dict(params or {}))
        except Exception as e:  # noqa: BLE001  闸门自己炸了 ⇒ 同一处置（拒）
            return self._gate_unavailable_outcome(skill_id, script_name, desc, e)

    @staticmethod
    def _gate_error_text(denied: Dict[str, Any]) -> str:
        """把闸门裁决压成一行可读错误（含审批单号与出路，便于人在 UI/日志里直接照做）"""
        parts = [str(denied.get("error") or denied.get("reason") or "确认闸门拒绝了本次调用")]
        approval_id = str(denied.get("approval_id") or "")
        if approval_id:
            parts.append("审批单号=%s" % approval_id)
        if denied.get("error_code"):
            parts.append("error_code=%s" % denied["error_code"])
        guidance = str(denied.get("guidance") or "")
        if guidance:
            parts.append(guidance)
        return "确认闸门拦截：%s" % "｜".join(parts)

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
                # [DET-4] _ENV_WHITELIST 是 set => list(...) 的次序随进程变；
                # health() 经 service.health -> GET /api/skills-mgmt/health 进响应体。
                # 常量本身保持 set（:499 的成员判定与子进程 envp 顺序无语义，且
                # scripts/scan_settings.py 按名字把它登记在 PASS_THROUGH_SITES），
                # 只在这一处把次序定义成字典序（该列表无既有次序契约）。
                "env_whitelist": sorted(_ENV_WHITELIST),
                "max_stdout_mb": _MAX_STDOUT_BYTES // (1024 * 1024),
                "layer": "executor",
            }
        except Exception as e:
            return {"ok": False, "error": str(e), "layer": "executor"}
