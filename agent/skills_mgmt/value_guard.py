"""价值观对齐约束（ValueGuard）— 任务 EVO-T6 安全护栏

【任务定位】
    为全部进化产物（变异参数、编辑提案、优化建议）落地"进化方向红线"：
    禁止产出含歧视 / 诱导危害 / 隐私泄露 / 越权指令的内容。
    解决审计缺陷：价值观对齐约束弱（仅 reviewer 安全扫描，无进化方向显式红线）。

【与既有能力对齐（不重复造轮子）】
    - reviewer.SecurityScanner: 管恶意代码 / 危险命令 / 密钥泄漏（技能发布层）；
    - agent.safety_guard:       管输入输出的敏感词过滤（对话层）；
    - ValueGuard:               管**进化方向**价值观红线（进化产物层）。
    三者职责互补，ValueGuard 不做安全扫描，只做价值观方向判定。

【红线类别（可扩展）】
    discrimination        歧视内容（种族/性别/地域等）
    harm                  诱导危害（自残/伤害/非法指导）
    privacy               隐私泄露（身份证/手机号/银行卡号）
    privilege_escalation  越权指令（绕过权限/提权/伪装授权）
    generic               通用违规（兜底类别）

【规则配置（变易，禁止硬编码规则声明）】
    规则优先级: 构造参数 rules > VALUE_GUARD_RULES_PATH 指向的 JSON 文件 > 内置默认。
    内置默认仅作兜底，新规则应写入 rules 文件或构造参数。

【LLM 辅助检查（可选协议）】
    llm_checker: callable(text) -> {"blocked": bool, "reason": str, "score": float}
    调用失败/超时/不可用 → 忽略（不阻断，防误伤正常进化产物）。

【配置（.env，全部带默认值）】
    VALUE_GUARD_ENABLED        红线检查总开关，默认 1
    VALUE_GUARD_LLM_ENABLED    LLM 辅助检查开关，默认 0（规则引擎即可覆盖红线）
    VALUE_GUARD_RULES_PATH     外部规则 JSON 文件路径（空=仅内置规则）
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .observability import logger, emit_metric

_ENV_ENABLED = "VALUE_GUARD_ENABLED"
_ENV_LLM_ENABLED = "VALUE_GUARD_LLM_ENABLED"
_ENV_RULES_PATH = "VALUE_GUARD_RULES_PATH"

_CRITICAL_CATEGORIES = ("discrimination", "harm", "privacy", "privilege_escalation")


def _env_enabled() -> bool:
    return os.getenv(_ENV_ENABLED, "1").strip().lower() not in ("0", "false", "no", "off")


def _env_llm_enabled() -> bool:
    return os.getenv(_ENV_LLM_ENABLED, "0").strip().lower() not in ("0", "false", "no", "off")


def _env_rules_path() -> Optional[str]:
    v = os.getenv(_ENV_RULES_PATH, "").strip()
    return v or None


# ════════════════════════════════════════════════════════════
#  数据模型
# ════════════════════════════════════════════════════════════

@dataclass
class ValueFinding:
    """一条红线命中记录"""
    rule_id: str = ""
    category: str = "generic"
    severity: str = "warn"          # critical / error / warn
    message: str = ""
    matched_text: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "category": self.category,
            "severity": self.severity,
            "message": self.message,
            "matched_text": self.matched_text[:200],
        }


@dataclass
class ValueCheckResult:
    """红线检查结果"""
    blocked: bool = False           # 命中 critical 红线 → 拒绝进化产物
    findings: List[ValueFinding] = field(default_factory=list)
    score: float = 100.0            # 0-100 安全分
    llm_checked: bool = False
    llm_blocked: bool = False
    llm_reason: str = ""

    @property
    def critical_findings(self) -> List[ValueFinding]:
        return [f for f in self.findings if f.severity == "critical"]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "blocked": self.blocked,
            "score": round(self.score, 2),
            "findings": [f.to_dict() for f in self.findings],
            "llm_checked": self.llm_checked,
            "llm_blocked": self.llm_blocked,
            "llm_reason": self.llm_reason,
        }


# ════════════════════════════════════════════════════════════
#  内置默认红线规则（兜底；生产规则应配置化）
# ════════════════════════════════════════════════════════════

def _default_rules() -> List[Dict[str, Any]]:
    return [
        # ── 歧视内容 ──
        {"id": "VG_DISCRIMINATION_RACIST", "category": "discrimination",
         "severity": "critical", "pattern": re.compile(
             r"(黑人\s*(都是|全是|就是)|白种人至上|种族清洗|优等民族|劣等民族)", re.I),
         "message": "种族歧视内容"},
        {"id": "VG_DISCRIMINATION_GENDER", "category": "discrimination",
         "severity": "critical", "pattern": re.compile(
             r"(女人\s*(就该|就应该|只配)|男性\s*(就该|就应该|只配)|性别至上|物化女性)", re.I),
         "message": "性别歧视内容"},
        {"id": "VG_DISCRIMINATION_REGION", "category": "discrimination",
         "severity": "critical", "pattern": re.compile(
             r"(地域\s*(黑|污名|歧视)|某地人\s*(都是|全是)\s*(小偷|骗子))", re.I),
         "message": "地域歧视内容"},
        # ── 诱导危害 ──
        {"id": "VG_HARM_SELF_HARM", "category": "harm",
         "severity": "critical", "pattern": re.compile(
             r"(自杀\s*(方法|方式|教程|指导|步骤)|如何\s*自残|安眠药\s*致死量|割腕\s*方法)", re.I),
         "message": "诱导自残/自杀指导"},
        {"id": "VG_HARM_VIOLENCE", "category": "harm",
         "severity": "critical", "pattern": re.compile(
             r"(制作\s*(炸弹|炸药|毒药)|投毒\s*方法|杀人\s*教程|袭击\s*教学|制造\s*伤害他人)", re.I),
         "message": "诱导暴力/伤害他人指导"},
        # ── 隐私泄露 ──
        {"id": "VG_PRIVACY_ID_CARD", "category": "privacy",
         "severity": "critical", "pattern": re.compile(
             r"\b\d{17}[\dXx]\b", re.I),
         "message": "疑似身份证号泄露"},
        {"id": "VG_PRIVACY_PHONE", "category": "privacy",
         "severity": "critical", "pattern": re.compile(
             r"\b1[3-9]\d{9}\b"),
         "message": "疑似手机号泄露"},
        {"id": "VG_PRIVACY_BANK", "category": "privacy",
         "severity": "critical", "pattern": re.compile(
             r"\b[456]\d{15}(?:\d{2,3})?\b", re.I),
         "message": "疑似银行卡号泄露"},
        # ── 越权指令 ──
        {"id": "VG_PRIVILEGE_BYPASS", "category": "privilege_escalation",
         "severity": "critical", "pattern": re.compile(
             r"(绕过\s*(权限|认证|授权|登录)|提权\s*(方法|脚本|利用)|权限提升\s*(攻击|payload)|"
             r"伪装\s*(管理员|root|超级用户)|禁用\s*安全校验|关闭\s*审核)", re.I),
         "message": "越权/提权指令"},
    ]


# ════════════════════════════════════════════════════════════
#  ValueGuard
# ════════════════════════════════════════════════════════════

class ValueGuard:
    """价值观红线检查器 — 所有进化产物上线前必须过检（验收 5）

    用法:
        guard = ValueGuard()
        result = guard.check("新参数包含某用户手机号 138xxxx")   # blocked=True
        result = guard.check_artifact({"object_type": "skill",
                                       "params": {"prompt": "..."}})
        if result.blocked:
            # 拒绝进化产物并记录（由调用方写谱系 decision=rejected）
    """

    def __init__(self, rules: Optional[List[Dict[str, Any]]] = None,
                 llm_checker: Optional[Callable[[str], Dict[str, Any]]] = None,
                 enabled: Optional[bool] = None,
                 use_llm: Optional[bool] = None,
                 rules_path: Optional[str] = None):
        """Args:
            rules: 显式规则列表（最高优先级）；None=按 文件 > 内置 顺序解析
            llm_checker: 可选 LLM 校验器（callable(text)->dict）
            enabled: 总开关（None=读 .env）
            use_llm: LLM 辅助开关（None=读 .env；需同时提供 llm_checker）
            rules_path: 规则 JSON 文件（None=读 .env）
        """
        self._enabled = enabled if enabled is not None else _env_enabled()
        self._llm_checker = llm_checker
        self._use_llm = (use_llm if use_llm is not None else _env_llm_enabled()) \
            and llm_checker is not None
        self._rules = self._resolve_rules(rules, rules_path)
        if not self._enabled:
            # 用户显式关闭红线检查 → 醒目告警（安全开关必须可审计）
            logger.warning(
                "[ValueGuard] ⚠ 价值观红线检查 VALUE_GUARD_ENABLED=0 已关闭，"
                "进化产物不再过红线检查——请确认这是有意为之")
        logger.info("[ValueGuard] 初始化完成 enabled=%s llm=%s rules=%d",
                    self._enabled, self._use_llm, len(self._rules))

    # ─── 公共接口 ───

    def check(self, text: str, *, content_type: str = "") -> ValueCheckResult:
        """检查一段文本是否命中价值观红线（验收 5）

        Returns:
            ValueCheckResult — blocked=True 表示命中 critical 红线，必须拒绝。
        """
        result = ValueCheckResult()
        if not text or not self._enabled:
            return result
        for rule in self._rules:
            matches = list(rule["pattern"].finditer(text))
            if not matches:
                continue
            for m in matches:
                result.findings.append(ValueFinding(
                    rule_id=rule["id"], category=rule["category"],
                    severity=rule["severity"], message=rule["message"],
                    matched_text=m.group(0),
                ))
            if rule["severity"] == "critical":
                result.blocked = True
        result.score = self._score(result.findings)
        if result.findings:
            self._audit(text, result)
        if self._use_llm and not result.blocked:
            self._llm_check(text, result)
        return result

    def check_artifact(self, artifact: Dict[str, Any]) -> ValueCheckResult:
        """统一入口：检查一个进化产物（变异参数 / 编辑提案 / 优化建议）

        artifact 支持字段（全部可选，防御性）:
            object_type / object_id
            params: 变异参数 dict（对全部字符串值递归检查）
            content / suggestion / summary / description: 文本内容
            其余未知字段: 递归提取字符串一并检查
        """
        texts: List[str] = []
        for key in ("params", "content", "suggestion", "summary", "description"):
            value = artifact.get(key)
            if isinstance(value, dict):
                texts.extend(self._extract_strings(value))
            elif isinstance(value, str) and value.strip():
                texts.append(value)
        # 未知字段兜底：递归提取全部字符串（不放过任何进化产物内容）
        texts.extend(self._extract_strings(artifact))
        if not texts:
            return ValueCheckResult()
        merged = "\n".join(texts)
        return self.check(merged, content_type=artifact.get("object_type", ""))

    # ─── 内部 ───

    @staticmethod
    def _extract_strings(value: Any) -> List[str]:
        """递归提取 dict/list 中全部字符串（params 嵌套场景）"""
        out: List[str] = []
        if isinstance(value, str):
            out.append(value)
        elif isinstance(value, dict):
            for v in value.values():
                out.extend(ValueGuard._extract_strings(v))
        elif isinstance(value, (list, tuple)):
            for v in value:
                out.extend(ValueGuard._extract_strings(v))
        return out

    @staticmethod
    def _score(findings: List[ValueFinding]) -> float:
        score = 100.0
        for f in findings:
            if f.severity == "critical":
                score -= 40.0
            elif f.severity == "error":
                score -= 20.0
            else:
                score -= 5.0
        return max(0.0, score)

    def _llm_check(self, text: str, result: ValueCheckResult) -> None:
        """LLM 辅助检查（失败/超时忽略，不阻断——防误伤正常产物）"""
        try:
            verdict = self._llm_checker(text) or {}
            result.llm_checked = True
            if verdict.get("blocked"):
                result.llm_blocked = True
                result.llm_reason = str(verdict.get("reason", ""))[:200]
                result.blocked = True  # LLM 命中红线同样拒绝
        except Exception as e:  # noqa: BLE001
            logger.debug("[ValueGuard] LLM 红线检查不可用，已忽略: %s", e)

    def _audit(self, text: str, result: ValueCheckResult) -> None:
        """命中记录：结构化日志 + 业务指标（失败不影响主流程）"""
        brief = [f"{f.rule_id}({f.category}/{f.severity})"
                 for f in result.findings]
        try:
            emit_metric("yunshu_skill_value_guard_hit", value=1,
                        labels={"blocked": str(result.blocked).lower()})
            logger.warning(
                "[ValueGuard] 红线命中 blocked=%s score=%.1f rules=[%s] text_len=%d",
                result.blocked, result.score, ",".join(brief), len(text))
        except Exception:  # noqa: BLE001
            logger.debug("[ValueGuard] 命中审计记录失败", exc_info=True)

    def _resolve_rules(self, rules: Optional[List[Dict[str, Any]]],
                       rules_path: Optional[str]) -> List[Dict[str, Any]]:
        """规则解析：显式 rules > 文件 > 内置默认（变易）

        文件格式: [{"id", "category", "severity", "pattern", "message"}, ...]
        pattern 为字符串正则，加载时编译；非法规则跳过并告警（不阻断）。
        """
        if rules is not None:
            return self._compile_rules(rules, source="constructor")
        path = rules_path or _env_rules_path()
        if path:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, list):
                    return self._compile_rules(loaded, source=path)
                logger.warning("[ValueGuard] 规则文件 %s 根节点非 list，使用内置规则", path)
            except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
                logger.warning("[ValueGuard] 规则文件 %s 加载失败，使用内置规则: %s", path, e)
        return self._compile_rules(_default_rules(), source="builtin")

    @staticmethod
    def _compile_rules(rules: List[Dict[str, Any]], *, source: str) -> List[Dict[str, Any]]:
        compiled: List[Dict[str, Any]] = []
        for i, rule in enumerate(rules):
            try:
                pat = rule["pattern"]
                if isinstance(pat, str):
                    pat = re.compile(pat, re.I)
                compiled.append({
                    "id": str(rule.get("id", f"VG_RULE_{i}")),
                    "category": str(rule.get("category", "generic")),
                    "severity": str(rule.get("severity", "warn")),
                    "pattern": pat,
                    "message": str(rule.get("message", "")),
                })
            except (KeyError, TypeError, re.error) as e:
                logger.warning("[ValueGuard] 跳过非法规则 %s（source=%s）: %s",
                               rule.get("id", f"#{i}"), source, e)
        return compiled
