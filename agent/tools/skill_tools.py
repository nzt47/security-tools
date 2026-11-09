"""技能脚本执行工具（L3）—— 把「带脚本的技能」接到**显式可调用**的执行点。

【为什么需要（2026-11-09 实测）】
    使用台账里 `stage=exec` 自 2026-10-28 建账起**一条都没有**。查下来的结论不是
    "写入点漏了"，而是**没有任何调用者**：聊天链路只做 L1 命中 → L2 注入提示词，
    `SkillExecutor.execute` 只挂在 `POST /api/skills-mgmt/<id>/execute`（人要手点
    详情抽屉）与评估器上。而 `data/skills_repo/scripted-selftest/skill.md` 却写着
    "满足任一条件时由 SkillLoader 匹配后交由 SkillExecutor 执行" —— 承诺与实现不符。

    本工具补上这条链，但**只补「显式调用」这一种**，不做「命中即自动执行」：
      1. 谁触发：模型显式调本工具（或人点详情抽屉，同一条 service 通路）；
      2. 参数从哪来：`params` 由调用方给；省略时回落技能的 `default_params`；
      3. 结果怎么进上下文：只有执行结果（JSON）作为工具结果返回，**脚本代码不进上下文**；
      4. 沙箱/超时：复用 `SkillExecutor`（独立子进程、无 shell、脚本名白名单 +
         路径越界校验、输出 schema 后置校验）；
      5. 幂等/重试：`effect: execute` ⇒ 工具层对本次调用**不自动重试**
         （重试判据见 `agent/tool_calling.py` 的后果等级）；每次执行在审计与
         使用台账（`stage=exec`）各留一条，重复调用可被点数。

【不易】
    1. **默认停用**（YAML `enabled: false`）：新增可执行能力先"停用观察"，
       要开由 Owner 显式打开（UI 开关或改 YAML），不是装完就能跑代码。
    2. **只认白名单**：`script_name` 必须在本技能 `scripts/` 列出的文件名里；
       不拼路径、不接受 `../`（`SkillFileStore.get_script_path` 另有一道越界校验）。
    3. **异常原文不回响应**（口径 §4.1）：对外只回异常类名 + 固定文案 + 服务日志。
    4. **没有脚本就不装作能跑**：拒绝时明确说"该技能没有 scripts/*.py（指令型技能
       不执行代码）"，而不是返回 success=false 的"执行失败"（那是另一种 0 折算）。

【变易】description / schema 必须是**静态字面量**（`scripts/migrate_tools_to_yaml.py`
    与 `tests/unit/test_tool_definitions_yaml.py` 都用 AST `literal_eval` 抽取，
    不执行工具代码）⇒ 它们逐字写在下面的 `@_tools.register` 里，与
    `data/tool_definitions/skill_script_run.yaml` 的 description/schema 逐字一致。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from agent import tools as _tools

logger = logging.getLogger(__name__)

#: 工具名（必须与 YAML 的 name 逐字一致；注册调用里那份字面量由守门测试对拍）
TOOL_NAME = "skill_script_run"

#: 缺省脚本名与超时（调用点不写字面量：硬编码边界门禁只认调用点）
DEFAULT_SCRIPT_NAME = "main.py"
DEFAULT_TIMEOUT_SEC = 30
MAX_TIMEOUT_SEC = 120

#: 回传给模型的输出尾巴（脚本可能打印很多，上下文只留尾部）
_STDOUT_TAIL_CHARS = 4000
_STDERR_TAIL_CHARS = 2000

#: 允许执行的状态（与「注入只看 enabled + approved/published」同一口径）
_EXECUTABLE_STATUSES = ("approved", "published")


def _tail(text: str, limit: int) -> str:
    s = str(text or "")
    return s[-limit:] if len(s) > limit else s


def _err(message: str, **extra: Any) -> Dict[str, Any]:
    """固定形状的失败返回（不抛异常：工具层要能如实记录一次失败调用）。"""
    out: Dict[str, Any] = {"ok": False, "error": message}
    out.update(extra)
    return out


@_tools.register(
    "skill_script_run",
    "在沙箱里执行某个技能的 L3 脚本（scripts/*.py）：脚本代码不进上下文，只有执行结果（JSON）返回。"
    "参数：skill_id 技能 id（必填，必须有 scripts/*.py）、script_name 脚本文件名"
    "（默认 main.py，只能取该技能 scripts/ 下的文件）、params 传给脚本的 JSON"
    "（经 stdin，省略时用技能的 default_params）、timeout 超时秒数（默认 30，上限 120）。"
    "每次调用都需要人工确认且不自动重试；技能没有脚本返回「不适用」而不是执行失败。"
    "Run a skill script in sandbox, execute skill L3 script",
    schema={
        "type": "object",
        "properties": {
            "skill_id": {
                "type": "string",
                "description": "技能 id（必须有可执行脚本 scripts/*.py）",
            },
            "script_name": {
                "type": "string",
                "description": "脚本文件名，默认 main.py；只能是该技能 scripts/ 目录下的文件",
            },
            "params": {
                "type": "object",
                "description": "传给脚本的 JSON 参数（经 stdin）；省略时用技能的 default_params",
            },
            "timeout": {
                "type": "integer",
                "description": "超时秒数，默认 30，最大 120",
            },
        },
        "required": ["skill_id"],
    },
)
def skill_script_run(skill_id: str = "", script_name: str = DEFAULT_SCRIPT_NAME,
                     params: Optional[Dict[str, Any]] = None,
                     timeout: Optional[float] = None,
                     **_: Any) -> Dict[str, Any]:
    """执行技能脚本（L3）；见模块文档的五条边界。"""
    sid = str(skill_id or "").strip()
    if not sid:
        return _err("缺少 skill_id")

    try:
        from agent.state_manager import get_skills_mgmt_service
        svc = get_skills_mgmt_service()
    except Exception as exc:  # noqa: BLE001
        logger.error("[skill_script_run] 服务不可用: %s", type(exc).__name__)
        return _err("技能服务不可用（详见服务日志）")

    # ── 1. 技能存在 / 启用 / 状态可执行 ──────────────────────────
    try:
        skill = svc.get(sid)
    except Exception as exc:  # noqa: BLE001 领域异常类型名足够定位，原文不外泄
        logger.warning("[skill_script_run] 取技能失败 skill=%s err=%s", sid, type(exc).__name__)
        return _err("技能不存在或不可读", skill_id=sid)

    if not bool(getattr(skill, "enabled", False)):
        return _err("技能已停用（停用技能不执行脚本）", skill_id=sid)
    status = str(getattr(skill, "status", "") or "")
    if status and status not in _EXECUTABLE_STATUSES:
        return _err("技能状态「%s」不可执行（只允许 approved/published）" % status, skill_id=sid)

    # ── 2. 必须真有脚本（没有就说不适用，不装作失败） ─────────────
    try:
        names = [str(n) for n in (svc.file_store.list_scripts(sid) or [])]
    except Exception as exc:  # noqa: BLE001
        logger.warning("[skill_script_run] 列脚本失败 skill=%s err=%s", sid, type(exc).__name__)
        names = []
    if not names:
        return _err("该技能没有可执行脚本（scripts/*.py）：指令型技能只注入提示词，"
                    "执行这件事不适用，不是执行失败", skill_id=sid, applicable=False)

    name = str(script_name or DEFAULT_SCRIPT_NAME).strip() or DEFAULT_SCRIPT_NAME
    if name not in names:
        return _err("脚本名不在该技能 scripts/ 里（不支持路径）", skill_id=sid,
                    script_name=name, available_scripts=names)

    # ── 3. 参数：调用方给的优先，缺省回落技能 default_params ───────
    use_params: Dict[str, Any] = {}
    defaults = getattr(skill, "default_params", None)
    if isinstance(defaults, dict):
        use_params.update(defaults)
    if isinstance(params, dict):
        use_params.update(params)

    # ── 4. 超时：钳制到 [DEFAULT, MAX]（0/负数/超大都不让它变成"无超时"）──
    try:
        use_timeout = float(timeout) if timeout is not None else float(DEFAULT_TIMEOUT_SEC)
    except (TypeError, ValueError):
        use_timeout = float(DEFAULT_TIMEOUT_SEC)
    if use_timeout <= 0:
        use_timeout = float(DEFAULT_TIMEOUT_SEC)
    use_timeout = min(use_timeout, float(MAX_TIMEOUT_SEC))

    # ── 5. 真执行：SkillExecutor（子进程 + 白名单 + 后置校验） ─────
    try:
        result = svc.execute_skill_script(sid, script_name=name,
                                          params=use_params, timeout=use_timeout)
    except Exception as exc:  # noqa: BLE001 异常原文不进响应
        logger.error("[skill_script_run] 执行异常 skill=%s script=%s err=%s",
                     sid, name, type(exc).__name__)
        return _err("执行失败（详见服务日志）", skill_id=sid, script_name=name)

    out: Dict[str, Any] = {
        "ok": bool(getattr(result, "success", False)),
        "skill_id": sid,
        "script_name": name,
        "success": bool(getattr(result, "success", False)),
        "exit_code": getattr(result, "exit_code", None),
        "duration_ms": round(float(getattr(result, "duration_ms", 0) or 0), 1),
        "timed_out": bool(getattr(result, "timed_out", False)),
        "validation_status": getattr(result, "validation_status", ""),
    }
    if out["success"]:
        out["result"] = getattr(result, "result", None)
    stdout = _tail(str(getattr(result, "stdout", "") or ""), _STDOUT_TAIL_CHARS)
    stderr = _tail(str(getattr(result, "stderr", "") or ""), _STDERR_TAIL_CHARS)
    if stdout:
        out["stdout_tail"] = stdout
    if stderr:
        out["stderr_tail"] = stderr
    err = str(getattr(result, "error", "") or "")
    if err and not out["success"]:
        # ExecutionResult.error 是执行器自己写的固定文案（退出码 / 校验失败处数），
        # 不含外部输入与异常原文 —— 与「异常原文不进响应」不冲突。
        out["error"] = err[:200]
    return out


def register_all(dl: Any = None) -> None:
    """保持与其它工具模块同一接线形状（`reg_skill_script(self)`）。

    本模块的注册在**导入时**由模块级 `@_tools.register` 完成（description/schema
    必须是静态字面量，供 AST 抽取守门），故这里不再注册，只做一次可见性检查 ——
    被 `unregister` 掉之后重新接线会**响亮报警**，而不是静默少一个工具。
    """
    if TOOL_NAME not in _tools._registry:
        logger.error("[skill_tools] 工具 %s 未在注册表（导入期注册失败？）", TOOL_NAME)
        return
    logger.info("[skill_tools] 工具 %s 已就绪（默认停用：YAML enabled=false）", TOOL_NAME)
