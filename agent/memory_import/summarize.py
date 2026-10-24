"""可选 LLM 摘要（只作用于「会话」批次；失败一律回退规则式）。

【为什么单独一个模块】本包的其余部分是**零第三方依赖**的纯域逻辑（只读解析 + 去重脱敏分红），
而 LLM 是「可选的、有成本的、会失败的外部服务」。把它隔离在这里，才能保证：

- `import agent.memory_import` 不会牵出 openai/anthropic 与网络栈（惰性 import）；
- 测试只需替换 `build_default_llm` 一处，不必碰读取器/流水线。

五条口径（交接单 §9 T2 与既有治理纪律）：

1. **默认关闭**：只有 `preview(use_llm=True)` 才尝试（界面复选框默认不勾）；
2. **预算硬上限**：每轮预览最多 N 次调用、每次输入最多 M 字符，超限直接回退，
   不做「再挤一点」——否则一次误点就能把账单拉到不可控；
3. **失败即降级且如实披露**：未配置 / 调用异常 / 空输出 / 超预算，全部回退规则式，
   并把原因码写进 `batch.digest_meta.reason`（界面可见，绝不静默冒充 LLM 输出）；
4. **只依据给定摘录**：system prompt 明令「不得引入摘录之外的信息、不得编造」，
   并要求保留日期/文件名/版本号/数字——否则摘要会变成「看起来很专业的假话」；
5. **不碰知识卡片**：卡片承载的是用户原话规则（CLAUDE.md / Trae rules），
   **改写即失真**，因此只有 carrier=memory_note 且非 prompts 的会话批次才走 LLM。

【为什么摘要里仍要带「覆盖原件统计」】LLM 的输出无法逐字核对，唯一可靠的验收方式是
把它声称的范围与真实记录对账：摘要头写清 user/assistant 条数与时间区间，
人一眼就能看出「它是不是只总结了 3 条就说完了 200 条」。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: 每次预览最多调用几次 LLM（超出即回退规则式）
DEFAULT_MAX_BATCHES = 3
#: 单次调用的输入字符上限（超出即跳过该批次，回退规则式）
DEFAULT_MAX_INPUT_CHARS = 12000
#: 单次调用的输出 token 上限
DEFAULT_MAX_OUTPUT_TOKENS = 600
#: 送进 prompt 的样例记录条数上限（避免把整批转录塞进去）
MAX_EXCERPT_RECORDS = 60
#: 单条样例截断长度
EXCERPT_CHARS = 300

MAX_BATCHES_ENV = "CP_MEMORY_IMPORT_LLM_MAX_BATCHES"
MAX_CHARS_ENV = "CP_MEMORY_IMPORT_LLM_MAX_CHARS"
MAX_BATCHES_RANGE: Tuple[int, int] = (1, 20)
MAX_CHARS_RANGE: Tuple[int, int] = (500, 60000)

#: 降级原因码（对外**只暴露这个白名单**；异常原文只进日志）
REASON_UNCONFIGURED = "llm_unconfigured"
REASON_BUDGET = "llm_budget_exhausted"
REASON_TOO_LARGE = "llm_input_too_large"
REASON_ERROR = "llm_error"
REASON_EMPTY = "llm_empty_output"

_SYSTEM_PROMPT = (
    "你是「会话摘要器」。只依据用户给出的会话摘录输出中文要点，用于人类事后回看。\n"
    "硬规则：\n"
    "1) 不得引入摘录之外的信息，不得猜测、不得补充建议；\n"
    "2) 保留摘录中出现的日期、文件名、版本号、命令与数字；\n"
    "3) 每条一句话、不超过 60 字；不要复述寒暄与工具调用噪声；\n"
    "4) 只输出要点列表（每条以「- 」开头），不要标题、不要前言、不要解释；\n"
    "5) 若摘录不足以支撑结论，就只列事实，不要下判断。"
)


def _clamped_int(raw: str, default: int, low: int, high: int) -> int:
    """读整数环境变量：空/非法/越界一律回退默认值（不因写坏配置炸掉预览）。"""
    text = str(raw or "").strip()
    if not text:
        return int(default)
    try:
        value = int(text)
    except (TypeError, ValueError):
        return int(default)
    return value if low <= value <= high else int(default)


@dataclass
class DigestBudget:
    """一轮预览的调用预算（**硬上限**，不是建议值）。"""

    max_batches: int = DEFAULT_MAX_BATCHES
    max_input_chars: int = DEFAULT_MAX_INPUT_CHARS
    calls: int = 0
    chars_used: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {"max_batches": int(self.max_batches),
                "max_input_chars": int(self.max_input_chars),
                "calls": int(self.calls), "chars_used": int(self.chars_used)}

    def check(self, input_chars: int) -> Tuple[bool, str]:
        """能否再调一次：返回 (可调, 原因码)。"""
        if self.calls >= self.max_batches:
            return False, REASON_BUDGET
        if int(input_chars) > self.max_input_chars:
            return False, REASON_TOO_LARGE
        return True, ""

    def spend(self, input_chars: int) -> None:
        self.calls += 1
        self.chars_used += int(input_chars)


def budget_from_env() -> DigestBudget:
    """按环境变量构造预算（两项都登记在 agent/settings/registry.py）。"""
    return DigestBudget(
        max_batches=_clamped_int(os.environ.get(MAX_BATCHES_ENV, ""),
                                 DEFAULT_MAX_BATCHES, *MAX_BATCHES_RANGE),
        max_input_chars=_clamped_int(os.environ.get(MAX_CHARS_ENV, ""),
                                     DEFAULT_MAX_INPUT_CHARS, *MAX_CHARS_RANGE),
    )


def _ensure_env_loaded() -> None:
    """把项目根 .env 加载进 os.environ（幂等；app_server 启动时已加载则无副作用）。

    【为什么需要它】CLI / 独立脚本里 os.environ 可能还没有 .env 的内容，
    此时 llm_available() 会误报「未配置」——那是**最难排查的一类假阴性**
    （界面说没配、.env 明明有）。与 agent/process_distill/service.py:37 同一口径。
    """
    try:
        from agent.env_config_manager import get_env_config_manager

        get_env_config_manager().reload()
    except Exception:  # noqa: BLE001 .env 加载失败不阻断（回退系统环境变量）
        pass


def llm_available() -> Tuple[bool, str]:
    """只查配置是否存在（不构造客户端、不发请求）：(可用, 原因码)。

    【为什么与 build_default_llm 分开】`/api/memory-import/sources` 每次打开面板都会调，
    那里只需要「能不能勾选」这一个布尔值；构造客户端属于真正要花钱的那一步。
    """
    key = (os.environ.get("LLM_API_KEY", "")
           or os.environ.get("DEEPSEEK_API_KEY", "")).strip()
    if not key:
        # 只在"看起来没配"时才去读 .env：服务进程启动时已加载，正常路径零额外开销
        _ensure_env_loaded()
        key = (os.environ.get("LLM_API_KEY", "")
               or os.environ.get("DEEPSEEK_API_KEY", "")).strip()
    if not key:
        return False, REASON_UNCONFIGURED
    return True, ""


def build_default_llm() -> Optional[Any]:
    """从 .env 构建 LLMService；未配置/构造失败返回 None（触发规则降级）。

    与 agent/process_distill/service.py:50 的 build_default_llm 同一口径
    （LLM_PROVIDER / LLM_API_KEY / LLM_MODEL / LLM_BASE_URL，env 优先），
    但**只在本模块内惰性 import**，保证纯域逻辑不依赖网络栈。
    """
    available, _reason = llm_available()
    if not available:
        return None
    try:
        from memory.llm_service import LLMService

        return LLMService(
            provider=os.environ.get("LLM_PROVIDER", "deepseek").strip().lower(),
            api_key=(os.environ.get("LLM_API_KEY", "")
                     or os.environ.get("DEEPSEEK_API_KEY", "")),
            model=os.environ.get("LLM_MODEL", "deepseek-v4-flash"),
            base_url=(os.environ.get("LLM_BASE_URL", "")
                      or os.environ.get("DEEPSEEK_BASE_URL", "")),
        )
    except Exception as exc:  # noqa: BLE001 构建失败必须降级而不是让预览失败
        logger.warning("[memory-import] LLM 构建失败，本次走规则式摘要: %s", exc)
        return None


def llm_eligible(carrier: str, scope: str) -> bool:
    """哪些批次允许 LLM 改写：只有会话载体，且不是「提问轨迹」。

- knowledge_card：承载 CLAUDE.md / Trae rules 的**原话规则**，改写即失真 => 不做；
- scope == prompts：本来就是一句一条的提问清单，规则式已是最忠实形态
  （LLM 会把「提问」改写成「主题」，反而丢原文）。
    """
    return str(carrier) == "memory_note" and str(scope) != "prompts"


def _excerpts(items: Sequence[Any]) -> str:
    """把记录压成「[时间] 角色: 正文」的摘录（条数与单条长度都有上限）。"""
    lines: List[str] = []
    for record in list(items)[:MAX_EXCERPT_RECORDS]:
        text = " ".join(str(getattr(record, "text", "") or "").split())
        if not text:
            continue
        if len(text) > EXCERPT_CHARS:
            text = text[:EXCERPT_CHARS] + "…"
        ts = str(getattr(record, "ts", "") or "")[:16]
        role = str(getattr(record, "role", "") or "")
        lines.append(f"[{ts}] {role}: {text}")
    return "\n".join(lines)


def _coverage_line(items: Sequence[Any]) -> str:
    """覆盖原件统计（供人核对 LLM 是否只看了几条就说完了全批）。"""
    users = sum(1 for r in items if getattr(r, "role", "") == "user")
    assistants = sum(1 for r in items if getattr(r, "role", "") == "assistant")
    stamps = sorted(str(getattr(r, "ts", "") or "")[:10] for r in items)
    span = f"{stamps[0]} → {stamps[-1]}" if stamps and stamps[0] else "未知区间"
    return (f"覆盖原件 {len(items)} 条（user {users} / assistant {assistants}），"
            f"时间 {span}")


def _normalise_output(text: str, limit: int) -> str:
    """把模型输出收敛成「- 要点」列表；不像要点列表的一律当空输出。"""
    bullets: List[str] = []
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith(("- ", "* ", "• ")):
            bullets.append("- " + line[2:].strip())
        elif line.startswith("#") or line.startswith("> "):
            continue
        elif bullets:
            bullets[-1] = bullets[-1] + line  # 模型换行续写：并回上一条
    if not bullets:
        return ""
    body = "\n".join(bullets)
    return body if len(body) <= limit else body[:limit] + "\n- …（输出超长已截断）"


def make_digest_fn(llm: Any, *, budget: Optional[DigestBudget] = None,
                   max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS):
    """构造 digest_fn（pipeline.build_batches 的注入点）。

    返回值签名: (carrier, source, scope, period, items, limit) -> dict
        {"digest": str, "source": "llm", "meta": {...}}  —— 成功
        {"digest": "", "reason": <原因码>, "meta": {...}} —— 失败（调用方回退规则式）

    这里**不抛异常**：摘要失败是正常分支，不是错误路径（预览必须能出结果）。
    """
    state = budget if budget is not None else DigestBudget()

    def digest_fn(*, carrier: str, source: str, scope: str, period: str,
                  items: Sequence[Any], limit: int) -> Dict[str, Any]:
        if not llm_eligible(carrier, scope):
            return {"digest": "", "reason": "llm_not_eligible", "meta": {}}
        prompt = _excerpts(items)
        input_chars = len(prompt)
        ok, reason = state.check(input_chars)
        if not ok:
            return {"digest": "", "reason": reason,
                    "meta": {"input_chars": input_chars}}
        model = str(getattr(llm, "model", "") or "unknown")
        try:
            raw = llm.summarize(
                messages=[{"role": "user",
                           "content": f"来源 {source} / 项目 {scope} / 周期 {period}\n\n{prompt}"}],
                max_tokens=max_output_tokens,
                system_prompt=_SYSTEM_PROMPT,
            )
        except Exception as exc:  # noqa: BLE001 失败即降级；原文只进日志
            logger.warning("[memory-import] LLM 摘要失败（%s），回退规则式: %s",
                           source, exc)
            state.spend(input_chars)
            return {"digest": "", "reason": REASON_ERROR,
                    "meta": {"input_chars": input_chars, "model": model}}
        state.spend(input_chars)
        body = _normalise_output(raw, max(200, int(limit) - 400))
        if not body:
            return {"digest": "", "reason": REASON_EMPTY,
                    "meta": {"input_chars": input_chars, "model": model}}
        head = (f"# {source} {scope} {period}（外部记忆导入 · LLM 摘要）\n\n"
                f"> 本段由 LLM（{model}）摘要，**可能漏项**；原文仍在批次的记录里可核对。\n"
                f"> {_coverage_line(items)}\n")
        digest = head + "\n" + body + "\n"
        if len(digest) > int(limit):
            digest = digest[: max(0, int(limit) - 40)] + "\n…（受篇幅限制已截断）\n"
        return {"digest": digest, "source": "llm",
                "meta": {"model": model, "input_chars": input_chars,
                         "output_chars": len(body), "records": len(items)}}

    digest_fn.budget = state  # type: ignore[attr-defined]
    return digest_fn


__all__ = [
    "DEFAULT_MAX_BATCHES", "DEFAULT_MAX_INPUT_CHARS", "DEFAULT_MAX_OUTPUT_TOKENS",
    "DigestBudget", "MAX_BATCHES_ENV", "MAX_CHARS_ENV", "REASON_BUDGET",
    "REASON_EMPTY", "REASON_ERROR", "REASON_TOO_LARGE", "REASON_UNCONFIGURED",
    "budget_from_env", "build_default_llm", "llm_available", "llm_eligible",
    "make_digest_fn",
]
