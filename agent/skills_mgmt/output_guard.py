"""技能输出护栏 — 对执行结果与 LLM 输出做结构化校验

三层护栏职责:
    1. validate_execution_result: 校验脚本执行结果
       - 格式校验: result.result 是否符合 skill.output_schema
       - 合规校验: PII / 密钥泄露检测
       - 一致性校验: result.success 与 result.exit_code 是否一致
    2. validate_llm_output: 校验 LLM 输出
       - 幻觉检测: LLM 是否声称调用了未加载的技能
       - 格式校验: 期望 JSON 时是否合法
       - 合规校验: Prompt Injection 痕迹
       - 越界检测: 危险动作关键词

设计原则 [三义]:
    - [不易] 护栏本身不能抛异常, 所有异常转为 critical finding
    - [不易] 不改 ExecutionResult / SkillExecutor.execute 接口
    - [变易] validate_llm_output 不阻塞主流程, passed=True + critical finding 由调用方决策
    - [简易] 纯规则检测, 不引入 LLM 二次调用 (成本太高)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from .observability import logger


# ════════════════════════════════════════════════════════════
#  模块级正则常量 (编译一次, 守简易+性能)
# ════════════════════════════════════════════════════════════

# PII 检测
_PII_ID_CARD_RE = re.compile(r"\d{17}[\dXx]")
_PII_PHONE_RE = re.compile(r"1[3-9]\d{9}")
_PII_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")

# 密钥检测: 匹配 key=value / "key":"value" 形式 (value 至少 8 字符)
# [变易] value 字符类用 [^"\'] 覆盖含 @#$%& 等特殊字符的密码 (如 P@ssw0rd)
_SECRET_KEY_RE = re.compile(
    r'(?i)(api_key|apikey|secret|secret_key|token|access_key|password|passwd)'
    r'["\']?\s*[:=]\s*["\']([^"\']{8,})["\']'
)

# Prompt Injection 关键词
_PROMPT_INJECTION_KEYWORDS: tuple = (
    "忽略上述指令", "忽略上面指令", "忽略之前指令",
    "ignore previous", "ignore above", "disregard prior",
    "you are now", "system prompt", "</system>",
)

# 幻觉检测: 从 LLM 输出提取显式技能 ID 引用
# 1) JSON/键值对中的 skill_id 字段
_SKILL_ID_EXPLICIT_RE = re.compile(
    r'"?skill_?id"?\s*[:=]\s*["\']([a-z0-9][a-z0-9_\-]{2,})["\']',
    re.IGNORECASE,
)
# 2) 反引号包裹的 kebab/snake_case 标识符 (要求含分隔符, 排除纯字母词)
_SKILL_BACKTICK_RE = re.compile(
    r'`([a-z0-9][a-z0-9_\-]*[_\-][a-z0-9_\-]+)`'
)

# 越界检测: 危险动作关键词
_DANGEROUS_ACTIONS: tuple = (
    "rm -rf", "rm -fr", "del /f", "format ", "mkfs",
    "drop table", "delete from", "truncate table",
    "sudo rm", "shutdown", "reboot",
)

# severity 优先级 (用于取最高级)
_SEVERITY_RANK = {"info": 0, "warn": 1, "error": 2, "critical": 3}


def _top_severity(findings: List["GuardFinding"]) -> str:
    """取 findings 中最高 severity; 空列表返回 info"""
    if not findings:
        return "info"
    return max(findings, key=lambda f: _SEVERITY_RANK.get(f.severity, 0)).severity


def _log_findings_summary(method: str, findings: List["GuardFinding"],
                          severity: str, passed: bool) -> None:
    """出口汇总日志: 逐条打印 finding 详情, 便于排查报错"""
    if not findings:
        logger.info(
            "[OutputGuard] %s 出口 | severity=%s | passed=%s | findings=0 (全部通过)",
            method, severity, passed,
        )
        return
    detail = "; ".join(
        f"[{f.category}/{f.severity}] {f.message}"
        f"{' @' + f.location if f.location else ''}"
        for f in findings
    )
    logger.info(
        "[OutputGuard] %s 出口 | severity=%s | passed=%s | findings=%d | %s",
        method, severity, passed, len(findings), detail,
    )


# ════════════════════════════════════════════════════════════
#  数据结构
# ════════════════════════════════════════════════════════════

@dataclass
class GuardFinding:
    """单条护栏发现"""
    category: str  # format | compliance | hallucination | consistency
    severity: str  # info | warn | error | critical
    message: str
    location: Optional[str] = None  # 字段路径或字符偏移

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category,
            "severity": self.severity,
            "message": self.message,
            "location": self.location,
        }


@dataclass
class GuardResult:
    """护栏校验结果"""
    passed: bool
    severity: str  # info | warn | error | critical
    findings: List[GuardFinding] = field(default_factory=list)
    sanitized_output: Optional[str] = None  # 脱敏后的输出 (如需)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "severity": self.severity,
            "findings": [f.to_dict() for f in self.findings],
            "sanitized_output": self.sanitized_output,
        }


# ════════════════════════════════════════════════════════════
#  护栏主体
# ════════════════════════════════════════════════════════════

class SkillOutputGuard:
    """技能输出护栏 — 对执行结果与 LLM 输出做格式/合规/幻觉校验

    线程安全: 无状态, 可全局复用单例
    """

    # ──────────────────────────────────────────────
    #  执行结果校验
    # ──────────────────────────────────────────────

    def validate_execution_result(self, result: Any,
                                  skill: Any = None) -> GuardResult:
        """校验脚本执行结果

        Args:
            result: ExecutionResult (用 Any 避免循环导入强耦合)
            skill: Skill 对象 (可选, 用于 output_schema 校验)

        Returns:
            GuardResult — severity=critical 时 passed=False (触发"不注入结果"策略)
            severity=error/warn 时 passed=True (error 追加警告, warn 仅记日志)
        """
        findings: List[GuardFinding] = []
        sanitized: Optional[str] = None
        skill_id = getattr(result, "skill_id", "?")
        success = getattr(result, "success", "?")
        exit_code = getattr(result, "exit_code", "?")
        has_schema = bool(skill is not None and getattr(skill, "output_schema", None))
        logger.info(
            "[OutputGuard] validate_execution_result 入口 | skill_id=%s | "
            "success=%s | exit_code=%s | has_output_schema=%s",
            skill_id, success, exit_code, has_schema,
        )
        try:
            # 1. 一致性校验: success vs exit_code
            self._check_consistency(result, findings)

            # 2. 格式校验: output_schema (skill 非空时)
            if skill is not None and getattr(skill, "output_schema", None):
                self._check_schema(result, skill, findings)

            # 3. 合规校验: PII / 密钥
            text_to_scan = self._result_to_text(result)
            sanitized = self._check_compliance(text_to_scan, findings)

        except Exception as e:  # noqa: BLE001 [不易] 护栏不抛异常
            logger.error(
                "[OutputGuard] validate_execution_result 内部异常: %s",
                e, exc_info=True,
            )
            findings.append(GuardFinding(
                category="consistency",
                severity="critical",
                message=f"护栏内部异常: {e}",
            ))
            logger.info(
                "[OutputGuard] validate_execution_result 异常兜底 | "
                "severity=critical | findings=%d",
                len(findings),
            )
            return GuardResult(
                passed=False, severity="critical",
                findings=findings, sanitized_output=sanitized,
            )

        severity = _top_severity(findings)
        # [不易] critical 时不通过 (触发"不注入结果"); error/warn/info 通过
        passed = severity != "critical"
        _log_findings_summary("validate_execution_result", findings, severity, passed)
        return GuardResult(
            passed=passed, severity=severity,
            findings=findings, sanitized_output=sanitized,
        )

    # ──────────────────────────────────────────────
    #  LLM 输出校验
    # ──────────────────────────────────────────────

    def validate_llm_output(self, llm_output: str,
                            loaded_skills: List[str],
                            intent: str) -> GuardResult:
        """校验 LLM 输出

        Args:
            llm_output: LLM 回复文本
            loaded_skills: 已加载的技能 ID 列表 (来自 build_context)
            intent: 用户原始意图

        Returns:
            GuardResult — [变易] passed 始终 True (不阻塞主流程),
            severity=critical 时由调用方决策重试/降级
        """
        findings: List[GuardFinding] = []
        sanitized: Optional[str] = None
        loaded_set = set(loaded_skills or [])
        logger.info(
            "[OutputGuard] validate_llm_output 入口 | output_len=%d | "
            "loaded_skills=%d | intent=%s",
            len(llm_output or ""), len(loaded_set), (intent or "")[:60],
        )
        try:
            # 1. 幻觉检测: LLM 提到未加载的技能
            self._check_hallucination(llm_output, loaded_set, findings)

            # 2. 格式校验: 期望 JSON 时是否合法
            self._check_json_format(llm_output, findings)

            # 3. 合规校验: PII / 密钥
            sanitized = self._check_compliance(llm_output, findings)

            # 4. Prompt Injection 检测
            sanitized = self._check_prompt_injection(
                llm_output, findings, sanitized)

            # 5. 越界检测: 危险动作
            self._check_boundary(llm_output, intent, findings)

        except Exception as e:  # noqa: BLE001 [不易] 护栏不抛异常
            logger.error(
                "[OutputGuard] validate_llm_output 内部异常: %s",
                e, exc_info=True,
            )
            findings.append(GuardFinding(
                category="hallucination",
                severity="critical",
                message=f"护栏内部异常: {e}",
            ))
            logger.info(
                "[OutputGuard] validate_llm_output 异常兜底 | "
                "severity=critical | findings=%d",
                len(findings),
            )

        severity = _top_severity(findings)
        # [变易] 不阻塞主流程: passed 始终 True, 由调用方根据 severity 决策
        _log_findings_summary("validate_llm_output", findings, severity, True)
        return GuardResult(
            passed=True, severity=severity,
            findings=findings, sanitized_output=sanitized,
        )

    # ──────────────────────────────────────────────
    #  内部检测方法
    # ──────────────────────────────────────────────

    def _check_consistency(self, result: Any,
                           findings: List[GuardFinding]) -> None:
        """一致性校验: success 与 exit_code 是否自洽"""
        success = getattr(result, "success", None)
        exit_code = getattr(result, "exit_code", None)
        if success is None or exit_code is None:
            return
        if success and exit_code != 0:
            findings.append(GuardFinding(
                category="consistency", severity="error",
                message=f"success=True 但 exit_code={exit_code}",
                location="result.success/exit_code",
            ))
        elif not success and exit_code == 0:
            findings.append(GuardFinding(
                category="consistency", severity="warn",
                message="success=False 但 exit_code=0",
                location="result.success/exit_code",
            ))

    def _check_schema(self, result: Any, skill: Any,
                      findings: List[GuardFinding]) -> None:
        """格式校验: result.result 是否符合 skill.output_schema"""
        schema = getattr(skill, "output_schema", None)
        result_data = getattr(result, "result", None)
        if not schema or result_data is None:
            return
        try:
            import jsonschema
        except ImportError:
            findings.append(GuardFinding(
                category="format", severity="info",
                message="jsonschema 未安装, 跳过 output_schema 校验",
            ))
            return
        try:
            jsonschema.validate(instance=result_data, schema=schema)
        except jsonschema.ValidationError as e:
            path = ".".join(str(p) for p in e.absolute_path) or "(root)"
            findings.append(GuardFinding(
                category="format", severity="error",
                message=f"output_schema 校验失败: {e.message}",
                location=path,
            ))
        except jsonschema.SchemaError as e:
            findings.append(GuardFinding(
                category="format", severity="warn",
                message=f"output_schema 本身非法: {e.message}",
                location="(schema)",
            ))
        except Exception as e:  # noqa: BLE001
            findings.append(GuardFinding(
                category="format", severity="warn",
                message=f"schema 校验异常: {e}",
            ))

    def _check_compliance(self, text: str,
                          findings: List[GuardFinding]) -> Optional[str]:
        """合规校验: PII / 密钥泄露, 返回脱敏后的文本 (无命中返回 None)"""
        if not text:
            return None
        sanitized = text
        # PII: 身份证
        for m in _PII_ID_CARD_RE.finditer(text):
            findings.append(GuardFinding(
                category="compliance", severity="warn",
                message="检测到身份证号",
                location=f"offset={m.start()}",
            ))
            sanitized = sanitized.replace(m.group(), "[REDACTED:id_card]")
        # PII: 手机号
        for m in _PII_PHONE_RE.finditer(text):
            findings.append(GuardFinding(
                category="compliance", severity="warn",
                message="检测到手机号",
                location=f"offset={m.start()}",
            ))
            sanitized = sanitized.replace(m.group(), "[REDACTED:phone]")
        # PII: 邮箱
        for m in _PII_EMAIL_RE.finditer(text):
            findings.append(GuardFinding(
                category="compliance", severity="warn",
                message="检测到邮箱",
                location=f"offset={m.start()}",
            ))
            sanitized = sanitized.replace(m.group(), "[REDACTED:email]")
        # 密钥: api_key/secret/token/password
        for m in _SECRET_KEY_RE.finditer(text):
            findings.append(GuardFinding(
                category="compliance", severity="warn",
                message=f"检测到密钥字段: {m.group(1)}",
                location=f"offset={m.start()}",
            ))
            # 只替换 value 部分, 保留 key 名便于排查
            sanitized = sanitized.replace(m.group(2), "[REDACTED:secret]")
        return sanitized if sanitized != text else None

    def _check_prompt_injection(self, text: str,
                                findings: List[GuardFinding],
                                sanitized: Optional[str]) -> Optional[str]:
        """Prompt Injection 检测: 关键词命中 → critical + 脱敏"""
        if not text:
            return sanitized
        result = sanitized if sanitized is not None else text
        changed = False
        lowered = text.lower()
        for kw in _PROMPT_INJECTION_KEYWORDS:
            # ASCII 关键词大小写不敏感匹配; 中文直接匹配
            haystack = lowered if kw.isascii() else text
            if kw in haystack:
                findings.append(GuardFinding(
                    category="compliance", severity="critical",
                    message=f"检测到 Prompt Injection 痕迹: {kw}",
                ))
                pattern = re.compile(re.escape(kw), re.IGNORECASE)
                result = pattern.sub("[BLOCKED]", result)
                changed = True
        if changed and result != (sanitized if sanitized is not None else text):
            return result
        return sanitized

    def _check_hallucination(self, text: str, loaded_set: Set[str],
                             findings: List[GuardFinding]) -> None:
        """幻觉检测: LLM 提到的技能 ID 不在 loaded_skills 中 → critical"""
        if not text:
            return
        mentioned: Set[str] = set()
        for pat in (_SKILL_ID_EXPLICIT_RE, _SKILL_BACKTICK_RE):
            for m in pat.finditer(text):
                sid = m.group(1)
                if sid:
                    mentioned.add(sid.lower())
        # 与 loaded_skills 比对
        if not loaded_set:
            # 未提供 loaded_skills 时无法判定幻觉, 仅记录提到的技能
            return
        for sid in sorted(mentioned):
            if sid not in loaded_set:
                findings.append(GuardFinding(
                    category="hallucination", severity="critical",
                    message=f"LLM 提到未加载的技能: {sid}",
                    location=sid,
                ))

    def _check_json_format(self, text: str,
                           findings: List[GuardFinding]) -> None:
        """格式校验: 输出疑似 JSON 时校验合法性"""
        if not text:
            return
        stripped = text.strip()
        # 启发式: 以 { 或 [ 开头才尝试 JSON 解析
        if not stripped or stripped[0] not in '{[':
            return
        try:
            json.loads(stripped)
        except json.JSONDecodeError as e:
            findings.append(GuardFinding(
                category="format", severity="warn",
                message=f"输出疑似 JSON 但解析失败: {e.msg}",
                location=f"line={e.lineno} col={e.colno}",
            ))

    def _check_boundary(self, text: str, intent: str,
                        findings: List[GuardFinding]) -> None:
        """越界检测: 危险动作关键词 (warn 级, 可能合法)"""
        if not text:
            return
        lowered = text.lower()
        for action in _DANGEROUS_ACTIONS:
            if action.lower() in lowered:
                findings.append(GuardFinding(
                    category="hallucination", severity="warn",
                    message=f"检测到危险动作关键词: {action.strip()}",
                ))

    # ──────────────────────────────────────────────
    #  辅助
    # ──────────────────────────────────────────────

    @staticmethod
    def _result_to_text(result: Any) -> str:
        """将 ExecutionResult 中可能含敏感信息的字段拼成文本"""
        parts: List[str] = []
        result_data = getattr(result, "result", None)
        if result_data is not None:
            try:
                parts.append(json.dumps(result_data, ensure_ascii=False))
            except (TypeError, ValueError):
                parts.append(str(result_data))
        stdout = getattr(result, "stdout", None)
        if stdout:
            parts.append(stdout)
        error = getattr(result, "error", None)
        if error:
            parts.append(error)
        return "\n".join(parts)
