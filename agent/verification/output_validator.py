"""轻量输出验证门控（TASK-07）

设计思路要求"验证门控（功能/安全/泛化）"。审计发现系统真实门控仅两处——
workflow_learning/skill_converter.py 的学习产物门控与 orchestrator DST 上下文软
门控；LLM 输出内容/质量的运行时验证不存在。本模块补齐该断点。

【不变式】
- `verification.conservative_mode: true` 语义保留：门控默认只记录不拦截，
  conservative_mode=true 时用户响应零影响；
- 不做：不拦截不重试时不得丢弃用户响应；
- LLM-as-Judge 本期只做接口预留 + 规则降级（参照 critic.py 的
  RULE_BASED / LLM_DRIVEN 双模式惯例），默认 mode=rule_based 零额外 LLM 调用。

【变易】
- 规则层 5 类检查（空/超长/缺关键字段/格式不符/PII 泄漏）全部声明式可配；
- mode=llm_based 且配置启用时调用 LLM 判相关性/完整性（复用
  verification.critic_evaluation.llm_config）；未配置时静默降级到规则层。

【简易】
- 单类 + 纯函数风格；所有记录操作内部 try/except，验证器异常绝不影响主链路。

用法:
    validator = OutputValidator()
    final_response, verdict = validator.check_and_act(response, task_type="text_response")
    # 保守模式（默认）: final_response == response，verdict 记录到 metrics/审计日志
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from agent.monitoring.metrics import get_metrics_collector

logger = logging.getLogger(__name__)


class ValidatorMode(Enum):
    """验证模式（与 critic.py 双模式惯例对齐）"""

    RULE_BASED = "rule_based"  # 规则层，零 Token
    LLM_BASED = "llm_based"    # LLM-as-Judge（接口预留，默认不启用）


@dataclass
class Verdict:
    """验证结果"""

    ok: bool
    issues: List[str] = field(default_factory=list)
    score: float = 100.0
    mode: str = ValidatorMode.RULE_BASED.value
    task_type: str = "text_response"
    retried: bool = False      # 是否发生过重试
    degraded: bool = False     # 验证器自身异常降级

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "issues": list(self.issues),
            "score": self.score,
            "mode": self.mode,
            "task_type": self.task_type,
            "retried": self.retried,
            "degraded": self.degraded,
        }


# ── 声明式规则参数（config.yaml verification.output_validator.* 可覆盖）──

_DEFAULT_MAX_OUTPUT_LENGTH = 8000

# task_type → 必含关键字段标记（缺任一即判"关键字段缺失"；其余类型不设标记）
_REQUIRED_MARKERS: Dict[str, Tuple[str, ...]] = {
    "summary_report": ("结论", "总结", "摘要"),
    "plan": ("步骤", "计划", "方案"),
    "report": ("结论", "建议"),
}

# schema_validation.supported_types 默认声明（config 可覆盖）
_DEFAULT_SUPPORTED_TYPES = (
    "text_response",
    "tool_call",
    "summary_report",
    "error_message",
)


def _default_score_penalties() -> Dict[str, float]:
    return {
        "empty_output": 100.0,
        "output_too_long": 20.0,
        "missing_required_field": 20.0,
        "unsupported_task_type": 10.0,
        "pii_leak": 30.0,
    }


class OutputValidator:
    """输出验证器：规则层（零 Token）+ LLM 层（接口预留）

    conservative_mode=True（默认）：只记录 verdict 到 metrics 与审计日志，
    返回原响应；False 且 enable_retry：失败 → retry_fn 重试一次 → 仍失败
    返回原响应（降级保底，不阻断）。
    """

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        *,
        enabled: bool = True,
        conservative_mode: bool = True,
        mode: ValidatorMode = ValidatorMode.RULE_BASED,
        max_output_length: int = _DEFAULT_MAX_OUTPUT_LENGTH,
        supported_types: Optional[Tuple[str, ...]] = None,
        required_markers: Optional[Dict[str, Tuple[str, ...]]] = None,
        enable_retry: bool = True,
        max_retries: int = 1,
        llm_client: Any = None,
        llm_config: Optional[Dict[str, Any]] = None,
    ):
        self.enabled = bool(enabled)
        self.conservative_mode = bool(conservative_mode)
        self.mode = mode if isinstance(mode, ValidatorMode) else (
            ValidatorMode.LLM_BASED if str(mode).strip().lower() == "llm_based"
            else ValidatorMode.RULE_BASED)
        self.max_output_length = int(max_output_length) if max_output_length else _DEFAULT_MAX_OUTPUT_LENGTH
        self.supported_types = tuple(supported_types) if supported_types else _DEFAULT_SUPPORTED_TYPES
        self.required_markers = required_markers or _REQUIRED_MARKERS
        self.enable_retry = bool(enable_retry)
        self.max_retries = max(1, int(max_retries or 1))
        self._llm_client = llm_client
        self._llm_config = llm_config
        self._score_penalties = _default_score_penalties()

        # PII 泄漏检查复用 OutputGuard 既有脱敏规则（反向查找：应被遮盖而未遮盖）
        self._pii_patterns = None  # 懒加载

    # ════════════════════════════════════════════════════════════════
    #  对外主入口
    # ════════════════════════════════════════════════════════════════

    def validate(self, response: Optional[str], task_type: str = "text_response") -> Verdict:
        """纯校验（无副作用）：返回 Verdict，不记录"""
        if not self.enabled:
            return Verdict(ok=True, task_type=task_type, mode=self.mode.value)
        issues: List[str] = []
        score = 100.0

        # ── 规则层（零 Token）──
        rules_issues = self._check_rules(response, task_type)
        for issue in rules_issues:
            issues.append(issue)
            score -= self._score_penalties.get(issue, 0.0)

        # ── LLM 层（接口预留）──
        if self.mode == ValidatorMode.LLM_BASED:
            llm_verdict = self._validate_with_llm(response, task_type)
            if llm_verdict is not None:
                # 合并不覆盖规则层结果（LLM 打分仅作为补充视图）
                score = min(score, float(llm_verdict.score))
                for issue in llm_verdict.issues:
                    if issue not in issues:
                        issues.append(issue)

        score = max(0.0, min(100.0, score))
        return Verdict(
            ok=not issues,
            issues=issues,
            score=round(score, 2),
            mode=self.mode.value,
            task_type=task_type,
        )

    def check_and_act(
        self,
        response: Optional[str],
        task_type: str = "text_response",
        *,
        conservative_mode: Optional[bool] = None,
        enable_retry: Optional[bool] = None,
        retry_fn: Optional[Callable[[], str]] = None,
    ) -> Tuple[str, Verdict]:
        """门控入口：校验 + 按模式处置（保守记录 / 非保守重试）

        【不变式】任何路径都不丢弃用户响应：保守模式返回原响应；
        非保守 + retry 重试一次仍失败返回原响应（降级保底）。

        Returns:
            (最终响应文本, Verdict)
        """
        try:
            verdict = self.validate(response, task_type)
            self._record(verdict)
        except Exception as e:
            # 验证器抛错 → 主链路正常（降级验证：返回原响应 + degraded 标记）
            logger.warning(
                "[输出验证] 验证器异常，降级跳过（主链路不受影响）: %s", e,
                extra={"module_name": "output_validator", "action": "check_and_act.degraded"},
            )
            return (response if response is not None else ""), Verdict(
                ok=True, degraded=True, task_type=task_type, mode=self.mode.value)

        use_conservative = self.conservative_mode if conservative_mode is None else conservative_mode
        if use_conservative or verdict.ok:
            return (response if response is not None else ""), verdict

        # 非保守 + 校验失败 + 允许重试
        use_retry = self.enable_retry if enable_retry is None else enable_retry
        if use_retry and retry_fn is not None:
            for _ in range(self.max_retries):
                try:
                    retried = retry_fn()
                except Exception as e:
                    logger.warning("[输出验证] 重试生成异常: %s", e)
                    break
                if retried is None:
                    break
                retried_verdict = self.validate(retried, task_type)
                self._record(retried_verdict)
                if retried_verdict.ok:
                    return retried, Verdict(
                        ok=True, issues=[], score=retried_verdict.score,
                        mode=self.mode.value, task_type=task_type, retried=True)
                # 仍失败：降级保底返回原响应（不阻断）
                logger.info(
                    "[输出验证] 重试后仍失败，返回原响应（降级保底）: %s", retried_verdict.issues)
                return (response if response is not None else ""), Verdict(
                    ok=False, issues=list(verdict.issues) + ["retry_still_failed"],
                    score=retried_verdict.score, mode=self.mode.value,
                    task_type=task_type, retried=True)

        # 非保守但不可重试/无 retry_fn：不丢弃响应，仅返回原响应 + 失败 verdict
        return (response if response is not None else ""), verdict

    # ════════════════════════════════════════════════════════════════
    #  规则层（零 Token）
    # ════════════════════════════════════════════════════════════════

    def _check_rules(self, response: Optional[str], task_type: str) -> List[str]:
        issues: List[str] = []

        # 1) 空输出
        if response is None or not str(response).strip():
            return ["empty_output"]

        text = str(response)

        # 2) 超长截断
        if len(text) > self.max_output_length:
            issues.append("output_too_long")

        # 3) 关键字段缺失（按 task_type 声明式标记）
        markers = self.required_markers.get(task_type)
        if markers:
            missing = [m for m in markers if m not in text]
            if len(missing) == len(markers):
                issues.append("missing_required_field")

        # 4) 格式不符（复用 verification.schema_validation 的 supported_types 声明）
        if task_type not in self.supported_types:
            issues.append("unsupported_task_type")

        # 5) PII 泄漏（复用 OutputGuard 既有脱敏规则反向查找）
        pii_fields = self._find_pii(text)
        if pii_fields:
            issues.append("pii_leak")

        return issues

    def _find_pii(self, text: str) -> List[str]:
        """查找未被遮盖的 PII 字段名（复用 agent.guardrails.output_guard 的规则）"""
        if self._pii_patterns is None:
            try:
                from agent.guardrails.output_guard import _pii_patterns
                self._pii_patterns = _pii_patterns()
            except Exception:
                self._pii_patterns = []
        fields = []
        for pattern, field_name, _replacer in self._pii_patterns:
            if pattern.search(text):
                fields.append(field_name)
        return fields

    # ════════════════════════════════════════════════════════════════
    #  LLM 层（接口预留，参照 critic.py RULE_BASED/LLM_DRIVEN 双模式）
    # ════════════════════════════════════════════════════════════════

    def _validate_with_llm(
        self, response: Optional[str], task_type: str
    ) -> Optional[Verdict]:
        """LLM-as-Judge 接口预留：mode=llm_based 且 client 可用时调用；
        未配置 → 静默降级返回 None（由规则层兜底），不引入默认路径额外成本"""
        if self._llm_client is None or not (self._llm_config or {}).get("enabled", False):
            logger.debug(
                "[输出验证] LLM 层未配置（llm_client=%s, enabled=%s），静默降级到规则层",
                self._llm_client is not None,
                bool((self._llm_config or {}).get("enabled", False)),
            )
            return None
        # 接口预留：此处调用 LLM 判相关性/完整性（复用 critic_evaluation.llm_config）
        # 本期不实现默认调用，避免在默认路径引入额外 LLM 成本。
        return None

    # ════════════════════════════════════════════════════════════════
    #  记录（保守模式：仅记录，异常不影响主链路）
    # ════════════════════════════════════════════════════════════════

    def _record(self, verdict: Verdict) -> None:
        """记录 verdict 到 metrics（learning.eval_* 指标族）与审计日志"""
        try:
            collector = get_metrics_collector()
            collector.increment_counter("learning.eval.total")
            if verdict.ok:
                collector.increment_counter("learning.eval.passed")
            else:
                collector.increment_counter("learning.eval.failed")
        except Exception:
            pass  # 埋点失败隔离
        try:
            log = logger.info if verdict.ok else logger.warning
            log(
                "[输出验证] task_type=%s mode=%s ok=%s score=%s issues=%s",
                verdict.task_type, verdict.mode, verdict.ok, verdict.score,
                ",".join(verdict.issues) if verdict.issues else "-",
                extra={"module_name": "output_validator", "action": "output_validation",
                       "task_type": verdict.task_type, "verdict_ok": verdict.ok,
                       "score": verdict.score, "issues": list(verdict.issues)},
            )
        except Exception:
            pass


# ════════════════════════════════════════════════════════════════
#  配置加载（优先级: 环境变量 > config.yaml verification.output_validator >
#  verification.conservative_mode 兜底 > 硬编码默认值）
# ════════════════════════════════════════════════════════════════

_OUTPUT_VALIDATOR_CONFIG: Optional[Dict[str, Any]] = None


def load_validator_config() -> Dict[str, Any]:
    """读取 verification.output_validator.* 配置（失败降级为默认值）"""
    global _OUTPUT_VALIDATOR_CONFIG
    if _OUTPUT_VALIDATOR_CONFIG is not None:
        return _OUTPUT_VALIDATOR_CONFIG
    cfg: Dict[str, Any] = {}
    try:
        cpath = Path(__file__).resolve().parent.parent.parent / "config.yaml"
        if cpath.exists():
            import yaml as _yaml
            with open(cpath, "r", encoding="utf-8") as f:
                data = _yaml.safe_load(f) or {}
            verification = data.get("verification") or {}
            # conservative_mode 兜底到 verification.conservative_mode（保留既有语义）
            cfg["conservative_mode"] = bool(
                (verification.get("output_validator") or {}).get(
                    "conservative_mode", verification.get("conservative_mode", True)))
            ov = verification.get("output_validator") or {}
            for key in ("enabled", "mode", "max_output_length", "enable_retry",
                        "max_retries", "supported_types"):
                if key in ov and ov[key] is not None:
                    cfg[key] = ov[key]
    except Exception as e:
        logger.debug("[输出验证] output_validator 配置读取失败，使用默认值: %s", e)
    _OUTPUT_VALIDATOR_CONFIG = cfg
    return cfg


def reset_config_cache() -> None:
    """重置配置缓存（仅测试用）"""
    global _OUTPUT_VALIDATOR_CONFIG
    _OUTPUT_VALIDATOR_CONFIG = None


def build_validator_from_config() -> OutputValidator:
    """按配置构建验证器（默认路径：rule_based + conservative=true，零 LLM 调用）"""
    cfg = load_validator_config()
    mode = ValidatorMode.LLM_BASED if str(cfg.get("mode", "")).strip().lower() == "llm_based" \
        else ValidatorMode.RULE_BASED
    supported = tuple(cfg["supported_types"]) if cfg.get("supported_types") else None
    return OutputValidator(
        enabled=bool(cfg.get("enabled", True)),
        conservative_mode=bool(cfg.get("conservative_mode", True)),
        mode=mode,
        max_output_length=int(cfg.get("max_output_length", _DEFAULT_MAX_OUTPUT_LENGTH)),
        supported_types=supported,
        enable_retry=bool(cfg.get("enable_retry", True)),
        max_retries=int(cfg.get("max_retries", 1)),
    )


__all__ = [
    "ValidatorMode",
    "Verdict",
    "OutputValidator",
    "load_validator_config",
    "build_validator_from_config",
    "reset_config_cache",
]
