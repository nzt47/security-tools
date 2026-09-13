"""LLM-judge 凭证接入与成本护栏（TASK-S8-04 / v7.2 §4.5 层③）

**职责**：把 S3-03 留下的 judge 注入通道**接通**（真实模型 + 结构化判定），并给它
配上**成本护栏**（否则 judge 自己会把预算吃光）。本模块是"配置 + 凭证 + 预算 + 存档"
的薄层，**不重造 judge 框架**：

- 判定本身仍走 `shadow.LLMJudge` / `shadow.JudgeGuard` / `sandbox.diff_judge`；
- 成本记账仍走 `agent.observability.utc.record_cost()`（成本唯一数据源＝事件流）；
- 人工抽检仍走 `shadow.ManualReviewQueue`（M5 口径不变）。

## 五条可被用例断言的契约（验收清单逐条对应）

1. **默认关闭**：``CP_DIGESTION_JUDGE_ENABLED`` 默认 ``false``；未显式开启时
   可用性恒为 ``disabled``，`judge_kind` 恒为 ``deterministic_local(disabled)``。
2. **三态如实**：`judge_availability()` ∈ ``available`` / ``no_credentials`` /
   ``disabled``；无凭证时**绝不**标注 ``llm:...``（不冒充 LLM）。
3. **凭证优先 SecretStore、回落 .env**：解析顺序
   ``secret_provider``（默认＝密钥文件存储）→ 进程环境变量 → ``.env`` 文件；
   **任何日志/审计/事件/报告都只出现指纹（sha256 前 12 位）与变量名，绝不出现明文**。
4. **成本计入 UTC 且两栏不混算**：每次真实 judge 调用
   ``utc.record_cost(source="judge", ...)``；业务成本与 judge 成本由
   ``utc.cost_columns`` 分列，**总额口径不变**（故断食/熔断判定仍看得见 judge 开销）。
5. **超限自动回落 + 发事件（不静默）**：每日 judge 预算超限 / 断食策略 / 预算读不到
   ⇒ 前置拦截（**不发真实调用**）→ 回落确定性打分器 → `judge_kind` 写
   ``deterministic_local(<原因码>)`` → 发 ``model.degraded`` 事件（复用既有事件类型）。

**import 纪律**：与同包一致，重依赖（`agent.observability.*` /
`agent.monitoring.cost_brake` / `agent.audit.facade`）一律函数体内懒加载；
本模块导入期无文件/网络副作用（唯一写盘是显式调用 `record()` / `record_verdict()`）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .shadow import (
    JUDGE_BASE_URL_ENV,
    JUDGE_KIND_LOCAL,
    JUDGE_MODE_ENV,
    JUDGE_MODEL_ENV,
    JUDGE_PROVIDER_ENV,
    JUDGE_REASON_BUDGET_EXCEEDED,
    JUDGE_REASON_BUDGET_UNREADABLE,
    JUDGE_REASON_DISABLED,
    JUDGE_REASON_FASTING,
    JUDGE_REASON_FORMAT,
    JUDGE_REASON_LLM_UNAVAILABLE,
    JUDGE_REASON_NO_CREDENTIALS,
    JUDGE_REASON_NOTES,
    JUDGE_THRESHOLD,
    JudgeGuard,
    LLMJudge,
    ResolvedJudge,
    is_llm_kind,
    judge_fallback_kind,
    judge_kind_for,
    judge_similarity,
)

logger = logging.getLogger("agent.digestion.judge_runtime")

# ════════════════════════════════════════════════════════════
#  常量（口径单点定义）
# ════════════════════════════════════════════════════════════

#: judge 运行时版本（进自检输出与报告；口径变更须改版本以便追溯）
JUDGE_RUNTIME_VERSION = "s8-04.1"

#: 配置项环境键
JUDGE_ENABLE_ENV = "CP_DIGESTION_JUDGE_ENABLED"
JUDGE_BUDGET_ENV = "CP_DIGESTION_JUDGE_DAILY_BUDGET_CENTS"
JUDGE_THRESHOLD_ENV = "CP_DIGESTION_JUDGE_THRESHOLD"
JUDGE_FOLLOW_FASTING_ENV = "CP_DIGESTION_JUDGE_FOLLOW_FASTING"
JUDGE_SECRET_FILE_ENV = "CP_DIGESTION_JUDGE_SECRET_FILE"
JUDGE_DOTENV_ENV = "CP_DIGESTION_JUDGE_DOTENV"
#: 估算 token 的字符/ token 比（**仅当适配器不给 usage 时**用，且必须显式标注估算）
JUDGE_CHARS_PER_TOKEN_ENV = "CP_DIGESTION_JUDGE_CHARS_PER_TOKEN"
#: 端点环境键（S9-02）：复用部署级 `LLM_BASE_URL`（从 `.shadow` 导入，定义单点在彼）。
#: 不新造 `CP_DIGESTION_JUDGE_BASE_URL` —— 同一部署里"模型端点"只有一个真实来源，
#: 造第二个同名概念只会让两处漂移；该键已登记进 `agent/settings/registry.py`（`_c`），
#: 故本次改动**不引入任何未登记 env**。

#: 可用性三态（验收清单要求可读）
AVAILABILITY_AVAILABLE = "available"
AVAILABILITY_NO_CREDENTIALS = "no_credentials"
AVAILABILITY_DISABLED = "disabled"
AVAILABILITY_STATES: Tuple[str, ...] = (AVAILABILITY_AVAILABLE,
                                        AVAILABILITY_NO_CREDENTIALS,
                                        AVAILABILITY_DISABLED)

#: judge 成本的事件来源标注（= `utc.JUDGE_COST_SOURCE`，成本两栏之 judge 栏）
JUDGE_COST_SOURCE = "judge"

#: 每日 judge 预算默认（cents/日）。**保守上限**：真实 judge 有额外成本，
#: 故既使显式开启也默认只给 1 元/日；``0`` = 一分钱都不允许（立即回落）。
DEFAULT_DAILY_BUDGET_CENTS = 100.0

#: 凭证解析来源标注
CREDENTIAL_SOURCE_SECRET_STORE = "secret_store"
CREDENTIAL_SOURCE_ENV = "env"
CREDENTIAL_SOURCE_DOTENV = "dotenv"
CREDENTIAL_SOURCE_INJECTED = "injected"
CREDENTIAL_SOURCE_NONE = "none"

#: 各 provider 的凭证环境键（按 `LLMService` 兼容表的小写 provider 名）
PROVIDER_CREDENTIAL_ENVS: Dict[str, Tuple[str, ...]] = {
    "openai": ("OPENAI_API_KEY",),
    "claude": ("ANTHROPIC_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "zhipu": ("ZHIPU_API_KEY",),
    "qwen": ("DASHSCOPE_API_KEY",),
    "deepseek": ("DEEPSEEK_API_KEY",),
    "siliconflow": ("SILICONFLOW_API_KEY",),
    "ollama": (),                       # 本地端点：无需凭证（只需 provider/model）
}
#: 通用凭证键（与 `.env.example` 的 `LLM_API_KEY` 口径一致）
GENERIC_CREDENTIAL_ENV = "LLM_API_KEY"
GENERIC_PROVIDER_ENV = "LLM_PROVIDER"

#: 密钥文件（"SecretStore" 的文件后端）默认位置：**运行时区**，不入库
DEFAULT_SECRET_RELATIVE = os.path.join("config", "secrets", "digestion_judge.env")
#: 项目根（用于定位 `.env` / `config.yaml`）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

#: 自检状态字段（供面板/日志读取；**不含任何明文凭证**）
SELF_CHECK_FIELDS: Tuple[str, ...] = (
    "state", "reason", "provider", "model", "kind", "base_url", "enabled",
    "daily_budget_cents", "threshold", "credential_source", "credential_name",
    "credential_fingerprint", "follow_fasting", "runtime_version")


# ════════════════════════════════════════════════════════════
#  环境与文件（非法值一律回退默认，不抛不静默）
# ════════════════════════════════════════════════════════════


def _env_map(env: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    return dict(os.environ if env is None else env)


def _env_str(name: str, default: str = "",
             env: Optional[Mapping[str, str]] = None) -> str:
    raw = str(_env_map(env).get(name, "") or "").strip()
    return raw or str(default)


def _env_flag(name: str, default: bool = False,
              env: Optional[Mapping[str, str]] = None) -> bool:
    raw = str(_env_map(env).get(name, "") or "").strip().lower()
    if not raw:
        return bool(default)
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    logger.warning("%s=%r 非法布尔值，回退默认 %s", name, raw, default)
    return bool(default)


def _env_float(name: str, default: float,
               env: Optional[Mapping[str, str]] = None) -> float:
    raw = str(_env_map(env).get(name, "") or "").strip()
    if not raw:
        return float(default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning("%s=%r 非法数值，回退默认 %s", name, raw, default)
        return float(default)


def parse_env_file(path: str) -> Dict[str, str]:
    """极简 ``.env`` / 密钥文件解析（``KEY=VALUE``；不引入新依赖）

    支持 ``#`` 注释、空行、``export `` 前缀、单双引号包裹；**不做变量展开**
    （展开是注入面）。文件不存在/不可读 → 返回 ``{}``（调用方按"无此来源"处理）。
    """
    out: Dict[str, str] = {}
    try:
        with open(str(path), "r", encoding="utf-8") as fh:
            for line in fh:
                text = line.strip()
                if not text or text.startswith("#") or "=" not in text:
                    continue
                if text.lower().startswith("export "):
                    text = text[7:].strip()
                key, _, value = text.partition("=")
                key = key.strip()
                if not key:
                    continue
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                    value = value[1:-1]
                out[key] = value
    except OSError as e:
        logger.debug("密钥/环境文件不可读（按无此来源处理）%s: %s", path, e)
    return out


def secret_store_provider(path: str = "") -> Callable[[str], Optional[str]]:
    """文件后端"SecretStore"读取器（**凭证优先级最高的一环**）

    返回 ``lookup(name) -> Optional[str]``；支持 ``KEY=VALUE`` 与 JSON 两种文件。
    这是接 KMS/密钥服务时**唯一需要替换的注入点**（`resolve_judge_credential`
    的 ``secret_provider`` 参数），本仓库不假设任何具体密钥服务的存在。
    """
    resolved = str(path or os.getenv(JUDGE_SECRET_FILE_ENV) or "").strip()
    if not resolved:
        resolved = os.path.join(_PROJECT_ROOT, DEFAULT_SECRET_RELATIVE)

    def _lookup(name: str) -> Optional[str]:
        raw = str(name or "").strip()
        if not raw:
            return None
        data: Dict[str, Any] = {}
        try:
            with open(resolved, "r", encoding="utf-8") as fh:
                head = fh.read()
        except OSError:
            return None
        if head.lstrip().startswith("{"):
            try:
                parsed = json.loads(head)
                if isinstance(parsed, dict):
                    data = parsed
            except ValueError as e:
                logger.warning("密钥文件 JSON 解析失败（按无此来源处理）: %s", e)
                return None
        else:
            data = parse_env_file(resolved)
        value = data.get(raw)
        return str(value) if value else None

    return _lookup


def _fingerprint(secret: str) -> str:
    """凭证指纹（sha256 前 12 位十六进制）—— 可核对"用的是哪把钥匙"，**不可还原**"""
    return "sha256:" + hashlib.sha256(str(secret).encode("utf-8")).hexdigest()[:12]


# ════════════════════════════════════════════════════════════
#  配置项（`enabled` 默认 false）
# ════════════════════════════════════════════════════════════


@dataclass
class JudgeConfig:
    """judge 配置（非法值回退默认；``to_dict()`` **不含任何凭证明文**）"""

    enabled: bool = False
    provider: str = ""
    model: str = ""
    daily_budget_cents: float = DEFAULT_DAILY_BUDGET_CENTS
    threshold: float = JUDGE_THRESHOLD
    follow_fasting: bool = True
    secret_file: str = ""
    dotenv_path: str = ""
    chars_per_token: float = 4.0
    #: 端点（S9-02）：复用部署级 `LLM_BASE_URL`，**不新造同名概念**。
    #: OpenAI 兼容端点（DeepSeek 等）必需；空串 = 交给适配器缺省端点。
    base_url: str = ""
    source: Dict[str, str] = field(default_factory=dict)

    @property
    def threshold_ok(self) -> bool:
        return 0.0 < float(self.threshold) <= 1.0

    def to_dict(self) -> Dict[str, Any]:
        return {"enabled": bool(self.enabled), "provider": self.provider,
                "model": self.model,
                "daily_budget_cents": float(self.daily_budget_cents),
                "threshold": float(self.threshold),
                "follow_fasting": bool(self.follow_fasting),
                "secret_file": self.secret_file,
                "dotenv_path": self.dotenv_path,
                "chars_per_token": float(self.chars_per_token),
                "base_url": self.base_url,
                "source": dict(self.source),
                "note": ("凭证只以指纹形式出现在自检/报告里；本结构不含密钥明文")}


def judge_config_from_env(env: Optional[Mapping[str, str]] = None, *,
                          config: Optional[JudgeConfig] = None) -> JudgeConfig:
    """解析 judge 配置（显式 ``config`` 优先；env 未给 → 默认关闭）

    ``threshold`` 越界（不在 ``(0,1]``）⇒ 回退 §4.5 的 0.85；
    ``daily_budget_cents`` 负数 ⇒ 回退默认；``chars_per_token`` ≤0 ⇒ 回退 4.0。
    """
    base = config or JudgeConfig()
    sources = dict(base.source or {})
    if config is not None:
        # 显式配置：只补齐未给的项，不覆盖调用方给的值
        return config
    enabled = _env_flag(JUDGE_ENABLE_ENV, False, env)
    sources["enabled"] = f"env:{JUDGE_ENABLE_ENV}" if JUDGE_ENABLE_ENV in _env_map(env) else "default"
    provider = _env_str(JUDGE_PROVIDER_ENV, "", env)
    sources["provider"] = f"env:{JUDGE_PROVIDER_ENV}" if provider else "default"
    model = _env_str(JUDGE_MODEL_ENV, "", env)
    sources["model"] = f"env:{JUDGE_MODEL_ENV}" if model else "default"
    budget = _env_float(JUDGE_BUDGET_ENV, DEFAULT_DAILY_BUDGET_CENTS, env)
    if budget < 0:
        logger.warning("%s=%s 非法（须 ≥0），回退默认 %s",
                       JUDGE_BUDGET_ENV, budget, DEFAULT_DAILY_BUDGET_CENTS)
        budget = DEFAULT_DAILY_BUDGET_CENTS
    sources["daily_budget_cents"] = (f"env:{JUDGE_BUDGET_ENV}"
                                     if JUDGE_BUDGET_ENV in _env_map(env) else "default")
    threshold = _env_float(JUDGE_THRESHOLD_ENV, JUDGE_THRESHOLD, env)
    if not (0.0 < threshold <= 1.0):
        logger.warning("%s=%s 越界（须 0<threshold<=1），回退默认 %s",
                       JUDGE_THRESHOLD_ENV, threshold, JUDGE_THRESHOLD)
        threshold = JUDGE_THRESHOLD
    sources["threshold"] = (f"env:{JUDGE_THRESHOLD_ENV}"
                            if JUDGE_THRESHOLD_ENV in _env_map(env)
                            else "default:JUDGE_THRESHOLD(§4.5)")
    chars = _env_float(JUDGE_CHARS_PER_TOKEN_ENV, 4.0, env)
    if chars <= 0:
        chars = 4.0
    # S9-02：端点复用部署级 `LLM_BASE_URL`（兼容端点不传端点 ⇒ 请求打到错误主机）
    base_url = _env_str(JUDGE_BASE_URL_ENV, "", env)
    sources["base_url"] = (f"env:{JUDGE_BASE_URL_ENV}" if base_url
                           else "default:适配器缺省端点")
    return JudgeConfig(
        enabled=enabled, provider=provider, model=model,
        daily_budget_cents=float(budget), threshold=float(threshold),
        follow_fasting=_env_flag(JUDGE_FOLLOW_FASTING_ENV, True, env),
        secret_file=_env_str(JUDGE_SECRET_FILE_ENV, "", env),
        dotenv_path=_env_str(JUDGE_DOTENV_ENV, "", env),
        chars_per_token=float(chars), base_url=base_url, source=sources)


# ════════════════════════════════════════════════════════════
#  凭证解析（SecretStore → env → .env；只出指纹不出明文）
# ════════════════════════════════════════════════════════════


@dataclass
class CredentialResolution:
    """凭证解析结果（**只含指纹**；`to_dict()` 可安全进日志/报告/事件）

    ``secret`` 为**瞬时明文**：`repr=False` + 不进 `to_dict()` —— 它只在"把凭证交给
    适配器"这一步被读取（适配器默认只认 `os.environ`，不显式传下去就等于
    "SecretStore/.env 形同虚设"）。**任何序列化路径都不会带上它。**
    """

    present: bool = False
    source: str = CREDENTIAL_SOURCE_NONE
    name: str = ""
    fingerprint: str = ""
    reason: str = ""
    secret: str = field(default="", repr=False, compare=False)

    def to_dict(self) -> Dict[str, Any]:
        return {"present": bool(self.present), "source": self.source,
                "name": self.name, "fingerprint": self.fingerprint,
                "reason": self.reason, "has_secret": bool(self.secret)}

    def __repr__(self) -> str:  # pragma: no cover - 防明文进日志/异常栈
        return (f"CredentialResolution(present={self.present!r}, "
                f"source={self.source!r}, name={self.name!r}, "
                f"fingerprint={self.fingerprint!r}, secret=<redacted>)")


def credential_names_for(provider: str) -> Tuple[str, ...]:
    """该 provider 的候选凭证键（provider 专属优先，通用 ``LLM_API_KEY`` 兜底）"""
    key = str(provider or "").strip().lower()
    specific = tuple(PROVIDER_CREDENTIAL_ENVS.get(key, ()))
    return specific + (GENERIC_CREDENTIAL_ENV,)


def resolve_judge_credential(
        provider: str, *,
        env: Optional[Mapping[str, str]] = None,
        secret_provider: Optional[Callable[[str], Optional[str]]] = None,
        dotenv_path: str = "",
) -> CredentialResolution:
    """按 **SecretStore → 环境变量 → .env** 解析凭证（返回指纹，不返回明文）

    - ``ollama`` 等本地 provider **不需要凭证**（返回 ``present=True`` 且
      ``source="local_endpoint"``，使自检不把"本地模型"误判成"缺凭证"）；
    - 通用键 ``LLM_API_KEY`` 只在 provider 与 ``LLM_PROVIDER`` 一致时才算命中
      （否则会拿 A 家 key 去调 B 家，属于隐性的凭证错配）；
    - 三处都没有 ⇒ ``present=False`` 且 ``reason`` 说明"找了哪些键、哪几个来源"。
    """
    key = str(provider or "").strip().lower()
    if not key:
        return CredentialResolution(
            present=False, reason="未配置 provider（不知该找哪个凭证键）")
    if key == "ollama" or not credential_names_for(key):
        return CredentialResolution(
            present=True, source="local_endpoint", name="",
            reason=f"{key} 为本地端点，无需凭证")
    names = credential_names_for(key)
    env_map = _env_map(env)
    declared_provider = str(env_map.get(GENERIC_PROVIDER_ENV) or "").strip().lower()
    lookup = secret_provider or secret_store_provider(
        str(env_map.get(JUDGE_SECRET_FILE_ENV) or ""))
    dotenv = parse_env_file(str(dotenv_path)) if dotenv_path else {}

    tried: List[str] = []
    for name in names:
        if name == GENERIC_CREDENTIAL_ENV and declared_provider and declared_provider != key:
            tried.append(f"{name}(provider={declared_provider}≠{key} 不匹配)")
            continue
        tried.append(name)
        value = lookup(name)
        if value:
            return CredentialResolution(present=True,
                                        source=CREDENTIAL_SOURCE_SECRET_STORE,
                                        name=name, fingerprint=_fingerprint(value),
                                        reason=f"来自密钥存储（{JUDGE_SECRET_FILE_ENV}）",
                                        secret=str(value))
        if env_map.get(name):
            return CredentialResolution(present=True, source=CREDENTIAL_SOURCE_ENV,
                                        name=name,
                                        fingerprint=_fingerprint(str(env_map[name])),
                                        reason="来自进程环境变量",
                                        secret=str(env_map[name]))
        if dotenv.get(name):
            return CredentialResolution(present=True, source=CREDENTIAL_SOURCE_DOTENV,
                                        name=name,
                                        fingerprint=_fingerprint(str(dotenv[name])),
                                        reason=f"来自 .env 文件（{dotenv_path}）",
                                        secret=str(dotenv[name]))
    return CredentialResolution(
        present=False, source=CREDENTIAL_SOURCE_NONE,
        reason=("未找到凭证（按 SecretStore → 环境变量 → .env 顺序查找 "
                + "、".join(tried) + "）"))


# ════════════════════════════════════════════════════════════
#  可用性自检（三态：available / no_credentials / disabled）
# ════════════════════════════════════════════════════════════


@dataclass
class JudgeAvailability:
    """judge 可用性三态（**面板与日志可读**；无明文凭证）"""

    state: str = AVAILABILITY_DISABLED
    reason: str = ""
    provider: str = ""
    model: str = ""
    kind: str = ""
    #: 端点（S9-02；**非密钥**，可进日志/报告 —— "打的是哪台主机"必须可复盘）
    base_url: str = ""
    credential: CredentialResolution = field(default_factory=CredentialResolution)
    registered: bool = False

    @property
    def available(self) -> bool:
        return self.state == AVAILABILITY_AVAILABLE

    def to_dict(self) -> Dict[str, Any]:
        return {"state": self.state, "reason": self.reason,
                "provider": self.provider, "model": self.model, "kind": self.kind,
                "base_url": self.base_url,
                "credential": self.credential.to_dict(),
                "registered": bool(self.registered)}

    def markdown(self) -> str:
        return (f"judge 可用性：**{self.state}**"
                f"（provider=`{self.provider or '-'}` model=`{self.model or '-'}`"
                f" endpoint=`{self.base_url or '适配器缺省'}`；"
                f"凭证来源={self.credential.source}"
                f"{'/' + self.credential.name if self.credential.name else ''}"
                f"{' ' + self.credential.fingerprint if self.credential.fingerprint else ''}；"
                f"原因：{self.reason or '-'}）")


def judge_availability(config: JudgeConfig, *,
                       env: Optional[Mapping[str, str]] = None,
                       secret_provider: Optional[Callable[[str], Optional[str]]] = None,
                       dotenv_path: str = "",
                       invoke: Optional[Callable[[str], str]] = None,
                       adapter: Any = None) -> JudgeAvailability:
    """启动自检：判定三态（**不做网络调用** —— 只构造通道 + 查凭证）

    判定顺序（先开关、再配置、再凭证、最后通道）：

    1. ``enabled=False`` ⇒ ``disabled``（**默认**：真实 judge 有额外成本）；
    2. 缺 provider/model ⇒ ``no_credentials``（不知调谁，谈不上有凭证）；
    3. 凭证解析失败 ⇒ ``no_credentials``（**绝不冒充 LLM**）；
    4. 通道构造/可用性探测失败（缺依赖、适配器不存在）⇒ ``no_credentials``
       （原因写"依赖/适配器"，与"缺密钥"区分开）；
    5. 否则 ``available``，``kind = llm:<provider>:<model>``。
    """
    resolved_dotenv = str(dotenv_path or config.dotenv_path
                          or os.path.join(_PROJECT_ROOT, ".env"))
    if not config.enabled:
        return JudgeAvailability(
            state=AVAILABILITY_DISABLED, reason=JUDGE_REASON_NOTES[JUDGE_REASON_DISABLED],
            provider=config.provider, model=config.model, base_url=config.base_url,
            kind=judge_fallback_kind(JUDGE_REASON_DISABLED),
            credential=CredentialResolution(reason="未启用，不解析凭证"))
    if not (config.provider and config.model):
        meta = _env_map(env)
        missing = [name for name, value in
                   ((JUDGE_PROVIDER_ENV, config.provider),
                    (JUDGE_MODEL_ENV, config.model)) if not value]
        return JudgeAvailability(
            state=AVAILABILITY_NO_CREDENTIALS,
            reason=(f"未配置 {'/'.join(missing)}"
                    f"（当前 {JUDGE_PROVIDER_ENV}={meta.get(JUDGE_PROVIDER_ENV, '')!r} "
                    f"{JUDGE_MODEL_ENV}={meta.get(JUDGE_MODEL_ENV, '')!r}）"),
            provider=config.provider, model=config.model, base_url=config.base_url,
            kind=judge_fallback_kind(JUDGE_REASON_NO_CREDENTIALS))
    if invoke is not None or adapter is not None:
        credential = CredentialResolution(
            present=True, source=CREDENTIAL_SOURCE_INJECTED, name="(注入通道)",
            reason="调用方注入 invoke/adapter（无需凭证；用于桩 judge 与自有通道）")
    else:
        credential = resolve_judge_credential(
            config.provider, env=env, secret_provider=secret_provider,
            dotenv_path=resolved_dotenv)
        if not credential.present:
            return JudgeAvailability(
                state=AVAILABILITY_NO_CREDENTIALS, reason=credential.reason,
                provider=config.provider, model=config.model,
                kind=judge_fallback_kind(JUDGE_REASON_NO_CREDENTIALS),
                credential=credential)
    probe = LLMJudge(invoke=invoke, adapter=adapter, provider=config.provider,
                     model=config.model, threshold=config.threshold,
                     api_key=credential.secret, base_url=config.base_url)
    if not probe.is_available():
        return JudgeAvailability(
            state=AVAILABILITY_NO_CREDENTIALS,
            reason=(f"judge 通道不可用：{probe.unavailable_reason}"
                    "（缺依赖/适配器不可用，与『缺密钥』区分）"),
            provider=config.provider, model=config.model,
            base_url=config.base_url,
            kind=judge_fallback_kind(JUDGE_REASON_NO_CREDENTIALS),
            credential=credential)
    return JudgeAvailability(
        state=AVAILABILITY_AVAILABLE, reason="凭证与通道就绪（未发起模型调用）",
        provider=config.provider, model=config.model, base_url=config.base_url,
        kind=judge_kind_for(config.provider, config.model), credential=credential,
        registered=True)


# ════════════════════════════════════════════════════════════
#  成本护栏（每日 judge 预算 + 断食联动 + UTC 记账 + 事件）
# ════════════════════════════════════════════════════════════


@dataclass
class JudgeBudgetState:
    """预算快照（JSON 可序列化；供自检/报告读取）"""

    day: str = ""
    budget_cents: float = 0.0
    spent_cents: float = 0.0
    calls: int = 0
    remaining_cents: float = 0.0
    blocked: bool = False
    reason_code: str = ""
    reason: str = ""
    fasting_factor: float = 1.0
    read_error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"day": self.day, "budget_cents": round(self.budget_cents, 6),
                "spent_cents": round(self.spent_cents, 6), "calls": int(self.calls),
                "remaining_cents": round(self.remaining_cents, 6),
                "blocked": bool(self.blocked), "reason_code": self.reason_code,
                "reason": self.reason, "fasting_factor": float(self.fasting_factor),
                "read_error": self.read_error,
                "source": JUDGE_COST_SOURCE,
                "note": ("spent 取自 UTC 成本两栏的 judge 栏（事件流为唯一数据源）")}


def cost_policy_factor(env: Optional[Mapping[str, str]] = None) -> float:
    """S5-03 成本刹车联动系数（**默认 1.0 = 无影响**；异常/缺失一律 1.0）

    与 `shadow._cost_policy_factor` 同一数据源（`cost_brake.shadow_budget_factor`），
    断食/日熔断期返回 ``CP_BUDGET_SHADOW_FACTOR_FASTING``（默认 0.0 = 归零）。
    """
    scope = dict(env) if env else None
    try:
        from agent.monitoring.cost_brake import shadow_budget_factor
        return float(shadow_budget_factor(env=scope))
    except Exception as e:  # noqa: BLE001 成本刹车故障不得让 judge 逻辑崩
        logger.debug("成本刹车系数不可用（按 1.0 无影响处理）: %s", e)
        return 1.0


class JudgeBudgetGuard:
    """judge 每日预算护栏（**前置拦截 + 事后记账**，超限即回落且不静默）

    - `precheck()`：真实调用**之前**判定（超预算 / 断食 / 读不到）⇒ 返回原因
      （空串 = 放行）。放行检查不通过时**不发真实调用**（省的是钱，不是标签）。
    - `record()`：真实调用**之后**记 `utc.record_cost(source="judge", ...)`
      —— 计入 UTC（故参与日熔断/周断食判定），并由成本两栏与业务成本分账。
    - `state()`：预算快照（面板/报告可读）。
    """

    def __init__(self, config: JudgeConfig, *,
                 store: Any = None, events_dir: str = "",
                 env: Optional[Mapping[str, str]] = None,
                 day: Optional[str] = None,
                 utc_module: Any = None) -> None:
        self.config = config
        self._store = store
        self._events_dir = str(events_dir or "")
        self._env = dict(env) if env else None
        self._day = str(day or "")
        self._utc = utc_module
        self.recorded: List[Dict[str, Any]] = []
        self.record_errors: List[str] = []
        #: 写侧 store 缓存（见 `_writer_store`；**读写必须同一本账**）
        self._writer: Any = None
        self._writer_resolved = False

    # ── 读侧 ────────────────────────────────────────────────

    @property
    def day(self) -> str:
        return self._day or date.today().isoformat()

    def _utc_mod(self) -> Any:
        if self._utc is not None:
            return self._utc
        from agent.observability import utc as utc_mod
        return utc_mod

    def _writer_store(self) -> Any:
        """写侧 store（**与读侧同目录**，否则护栏永远看不见自己的花销）

        S9-02 修复的**护栏失聪**：`spent()` 按 `events_dir` 读当日 judge 栏，而
        `utc.record_cost(store=None)` 落到**全局默认**事件目录（`CP_EVENTS_DIR`
        /`data/events`）。只给 `events_dir` 而不给 `store` 时，读写指向**两本不同的账**
        ⇒ `spent_cents` 恒为 0 ⇒ 每日预算**永远不会触发**（"超预算回落"沦为纸面条款），
        同时花销被记到另一个目录。故：调用方给了 `events_dir`，写侧就显式绑到同目录；
        显式 `store` 仍然最优先（不夺走调用方的注入权）。
        """
        if self._store is not None:
            return self._store
        if self._writer_resolved:
            return self._writer
        self._writer_resolved = True
        if not self._events_dir:
            return None                      # 读写都走全局默认目录 ⇒ 天然一致
        try:
            from agent.observability.events import EventStore, active_events_path
            self._writer = EventStore(path=active_events_path(self._events_dir))
        except Exception as e:  # noqa: BLE001 绑不上就退回全局（并留痕，不静默）
            logger.warning("judge 写侧事件目录绑定失败（退回全局默认目录）: %s", e)
            self._writer = None
        return self._writer

    def spent(self) -> Dict[str, Any]:
        """当日 judge 栏花销（事件流；读失败 → ``error`` 非空 ⇒ fail-closed）"""
        try:
            return dict(self._utc_mod().judge_cost_cents(
                self.day, directory=self._events_dir or None))
        except Exception as e:  # noqa: BLE001 读侧异常统一走 fail-closed
            return {"day": self.day, "cost_normalized_cents": None, "calls": None,
                    "error": f"{type(e).__name__}: {e}"}

    def state(self) -> JudgeBudgetState:
        budget = max(0.0, float(self.config.daily_budget_cents))
        factor = cost_policy_factor(self._env) if self.config.follow_fasting else 1.0
        effective = budget * max(0.0, min(1.0, factor))
        read = self.spent()
        error = str(read.get("error") or "")
        spent_value = read.get("cost_normalized_cents")
        spent = float(spent_value) if spent_value is not None else 0.0
        reason = ""
        code = ""
        if error:
            code = JUDGE_REASON_BUDGET_UNREADABLE
            reason = (f"{JUDGE_REASON_BUDGET_UNREADABLE}: judge 成本读不到"
                      f"（{error}）⇒ fail-closed 停用真实 judge")
        elif effective <= 0:
            code = (JUDGE_REASON_FASTING if self.config.follow_fasting and factor <= 0
                    and budget > 0 else JUDGE_REASON_BUDGET_EXCEEDED)
            reason = (f"{code}: 当日 judge 可用预算为 0"
                      f"（budget={budget} × 断食系数 {factor}）")
        elif spent >= effective:
            code = JUDGE_REASON_BUDGET_EXCEEDED
            reason = (f"{JUDGE_REASON_BUDGET_EXCEEDED}: 当日 judge 已花 "
                      f"{spent:.6f} cents ≥ 可用预算 {effective:.6f} cents"
                      f"（budget={budget} × 断食系数 {factor}）")
        return JudgeBudgetState(
            day=self.day, budget_cents=budget, spent_cents=spent,
            calls=int(read.get("calls") or 0),
            remaining_cents=max(0.0, effective - spent), blocked=bool(code),
            reason_code=code, reason=reason, fasting_factor=factor,
            read_error=error)

    def precheck(self) -> str:
        """真实调用前的放行检查：``""`` = 放行，否则回落原因（含原因码）"""
        return self.state().reason

    # ── 写侧 ────────────────────────────────────────────────

    def record(self, *, model: str, provider: str = "",
               tokens_in: int = 0, tokens_out: int = 0,
               estimated: bool = False, interaction_id: str = "",
               task_id: str = "", correlation_id: str = "",
               duration_ms: Optional[float] = None) -> Dict[str, Any]:
        """把一次真实 judge 调用的成本记进 UTC（``source="judge"``）

        **不编造 token**：适配器给了 ``usage`` 就用真实值；拿不到时用字符数估算，
        但必须在事件里显式标 ``tokens_estimated=True``（下游可据此打折看待）。
        """
        payload = {"model": str(model or ""), "provider": str(provider or ""),
                   "tokens_in": max(0, int(tokens_in or 0)),
                   "tokens_out": max(0, int(tokens_out or 0)),
                   "estimated": bool(estimated),
                   "source": JUDGE_COST_SOURCE}
        try:
            utc_mod = self._utc_mod()
            envelope = utc_mod.record_cost(
                model=str(model or ""), provider=str(provider or ""),
                source=JUDGE_COST_SOURCE, tokens_in=payload["tokens_in"],
                tokens_out=payload["tokens_out"], interaction_id=str(interaction_id or ""),
                task_id=str(task_id or ""), correlation_id=str(correlation_id or ""),
                duration_ms=duration_ms, store=self._writer_store(),
                extra={"tokens_estimated": bool(estimated),
                       "cost_column": JUDGE_COST_SOURCE,
                       "recorded_by": f"judge_runtime/{JUDGE_RUNTIME_VERSION}"})
            payload["event_id"] = str(getattr(envelope, "event_id", "") or "")
            payload["recorded"] = envelope is not None
            payload["tokens_estimated"] = bool(estimated)
            payload["note"] = ("judge 成本计入 UTC（成本两栏之 judge 栏）；"
                               "与业务成本**不得混算**")
        except Exception as e:  # noqa: BLE001 记账失败必须留痕（不得静默丢账）
            error_text = f"{type(e).__name__}: {e}"
            payload["recorded"] = False
            payload["error"] = error_text
            self.record_errors.append(error_text)
            logger.warning("judge 成本记账失败（如实留痕，不静默）: %s", e)
        self.recorded.append(dict(payload))
        return payload

    def to_dict(self) -> Dict[str, Any]:
        return {"state": self.state().to_dict(),
                "recorded": len(self.recorded),
                "record_errors": list(self.record_errors),
                "note": ("每次真实 judge 调用都经 `utc.record_cost(source='judge')` "
                         "计入 UTC")}


def emit_judge_fallback(*, from_model: str, reason: str, reason_code: str = "",
                        provider: str = "", capability_id: str = "",
                        correlation_id: str = "", extra: Optional[Dict[str, Any]] = None,
                        store: Any = None) -> str:
    """发 `model.degraded` 事件：真实 judge → 确定性打分器（**不静默**）

    复用既有第 9 事件（P7.1-18），载荷带 ``judge_reason_code`` / ``judge_kind``，
    使"为什么回落"在事件流里可查；失败只记 debug（事件写入本身是 best-effort）。
    """
    code = str(reason_code or "").strip() or JUDGE_REASON_LLM_UNAVAILABLE
    try:
        from agent.observability.model_degrade import report_model_degraded
        payload: Dict[str, Any] = {
            "judge_reason_code": code,
            "judge_kind": judge_fallback_kind(code),
            "capability_id": str(capability_id or ""),
        }
        payload.update(dict(extra or {}))
        envelope = report_model_degraded(
            from_model=str(from_model or "llm_judge"),
            to_model=JUDGE_KIND_LOCAL, reason=str(reason or code),
            provider=str(provider or ""), correlation_id=str(correlation_id or ""),
            fallback_source="judge_budget_guard", fallback_attempted=False,
            extra=payload, store=store)
        return str(getattr(envelope, "event_id", "") or "")
    except Exception as e:  # noqa: BLE001 事件失败不得中断灰度
        logger.debug("judge 回落事件发送失败: %s", e)
        return ""


# ════════════════════════════════════════════════════════════
#  judge 判定存档 + 人工一致性（步骤 4）
# ════════════════════════════════════════════════════════════

JUDGE_VERDICT_FILENAME = "judge_verdicts.jsonl"
#: 样本量门槛（S5-02 口径）：< 20 只披露不结论
CONSISTENCY_MIN_SAMPLES = 20


class JudgeVerdictStore:
    """judge 判定存档（JSONL；与人工抽检台账**并列但不同文件**）

    为什么单独一个文件：`ManualReviewQueue` 的 `items()` 以"最后一条为准"，
    若把 judge 判定混写进 `manual_reviews.jsonl`，judge 的结论会被当成**人工裁定**
    ——那正是 M5 口径要防的事（"抽检清单已产出"≠"已人工复核"）。
    """

    def __init__(self, path: str = "", *, directory: str = "",
                 filename: str = JUDGE_VERDICT_FILENAME) -> None:
        base = str(directory or os.getenv("CP_DIGESTION_SHADOW_DIR") or "")
        if not base:
            base = os.path.join(_PROJECT_ROOT, "data", "digestion", "shadow")
        self.dir = base
        self.path = str(path or os.path.join(base, filename))
        self.errors = 0

    def record(self, *, capability_id: str, case_id: str, verdict: str,
               confidence: float = 0.0, reason: str = "", judge_kind: str = "",
               sample_id: str = "", judge_score: float = 0.0,
               manual_flagged: bool = False, format: str = "",
               ts: Optional[float] = None) -> Dict[str, Any]:
        """追加一条 judge 判定（幂等键 = (能力, 用例)；落盘失败只记 `errors`）"""
        import time as _time
        stamp = float(ts if ts is not None else _time.time())
        row = {"kind": "judge_verdict", "capability_id": str(capability_id or ""),
               "case_id": str(case_id or ""), "sample_id": str(sample_id or ""),
               "verdict": str(verdict or ""), "confidence": float(confidence or 0.0),
               "reason": str(reason or "")[:400], "judge_kind": str(judge_kind or ""),
               "judge_score": float(judge_score or 0.0),
               "manual_flagged": bool(manual_flagged), "format": str(format or ""),
               # 日桶口径与 `utc.utc_daily()` 一致（同一日历日，便于成本两栏对齐）
               "day": date.fromtimestamp(stamp).isoformat(),
               "recorded_at": stamp}
        try:
            parent = os.path.dirname(self.path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        except OSError as e:  # advisory：存档失败不得中断灰度
            self.errors += 1
            logger.warning("judge 判定存档失败（advisory）: %s", e)
        return row

    def rows(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                for line in fh:
                    text = line.strip()
                    if not text:
                        continue
                    try:
                        data = json.loads(text)
                    except ValueError:
                        continue
                    if isinstance(data, dict):
                        out.append(data)
        except OSError:
            return out
        return out

    def latest(self, capability_id: str = "") -> Dict[Tuple[str, str], Dict[str, Any]]:
        """当前判定（同 (能力, 用例) 以最后一条为准）"""
        latest: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for row in self.rows():
            if str(row.get("kind") or "") != "judge_verdict":
                continue
            key = (str(row.get("capability_id") or ""), str(row.get("case_id") or ""))
            if capability_id and key[0] != str(capability_id):
                continue
            latest[key] = row
        return latest

    def summary(self, capability_id: str = "") -> Dict[str, Any]:
        latest = self.latest(capability_id)
        by_verdict: Dict[str, int] = {}
        for row in latest.values():
            verdict = str(row.get("verdict") or "unknown")
            by_verdict[verdict] = by_verdict.get(verdict, 0) + 1
        return {"stored": len(latest), "by_verdict": dict(sorted(by_verdict.items())),
                "path": self.path, "errors": self.errors,
                "note": ("judge 判定独立存档；人工结论仍在 "
                         "ManualReviewQueue（两账并列、不互写）")}


def _manual_verdict_to_judge(verdict: str) -> str:
    """人工结论 → 可比对口径（``uncertain`` → ``""`` = 不计入一致率分母）"""
    text = str(verdict or "").strip().lower()
    if text == "pass":
        return "pass"
    if text == "fail":
        return "fail"
    return ""


def judge_consistency(*, verdict_store: JudgeVerdictStore,
                      review_queue: Any, capability_id: str = "",
                      min_samples: int = CONSISTENCY_MIN_SAMPLES,
                      enqueue_disagreements: bool = False,
                      queued_by: str = "judge_consistency",
                      case_set: Any = None,
                      cases: Optional[Sequence[Any]] = None) -> Dict[str, Any]:
    """judge 判定 vs 人工裁定的**一致率统计** + 分歧样本清单（步骤 4）

    - 分母只算**已裁定且非 uncertain** 的人工结论（`uncertain` 计入 `uncertain` 计数，
      不进分母 —— 否则"人也不知道"会被算成"judge 错了"）；
    - 样本 < ``min_samples``（默认 20，S5-02 口径）⇒ ``conclusion=""`` 且
      ``disclosure`` 明说"只披露不结论"（**不在小样本上宣布一致/不一致**）；
    - 分歧样本可入 `ManualReviewQueue`（``enqueue_disagreements=True``）；
      ``case_set`` / ``cases`` **透传**给入队闸（S8-05 的 D1/D2：不在现行判定集
      或形状不匹配的用例**不得入队**）—— 不传则由队列自身的 CaseStore 兜底；
    - 只读叶子字段，不搬运 live 对象。
    """
    latest = verdict_store.latest(capability_id)
    manual_items = review_queue.items(capability_id)
    manual = {(str(i.capability_id), str(i.case_id)): i for i in manual_items}
    agree = 0
    disagree: List[Dict[str, Any]] = []
    uncertain = 0
    undecided = 0
    compared = 0
    for key, row in sorted(latest.items()):
        item = manual.get(key)
        if item is None:
            continue
        human = _manual_verdict_to_judge(getattr(item, "verdict", ""))
        if not human:
            if str(getattr(item, "verdict", "") or "") == "uncertain":
                uncertain += 1
            else:
                undecided += 1
            continue
        judge_verdict = str(row.get("verdict") or "")
        if judge_verdict not in ("pass", "fail"):
            continue
        compared += 1
        if judge_verdict == human:
            agree += 1
        else:
            disagree.append({
                "capability_id": key[0], "case_id": key[1],
                "judge_verdict": judge_verdict,
                "judge_confidence": float(row.get("confidence") or 0.0),
                "judge_reason": str(row.get("reason") or ""),
                "judge_kind": str(row.get("judge_kind") or ""),
                "human_verdict": human,
                "reviewer": str(getattr(item, "reviewer", "") or ""),
                "role": str(getattr(item, "role", "") or ""),
                "manual_flagged": bool(row.get("manual_flagged")),
            })
    rate = round(agree / compared, 4) if compared else None
    enqueued: List[str] = []
    if enqueue_disagreements and disagree:
        by_cap: Dict[str, List[str]] = {}
        reasons: Dict[str, List[str]] = {}
        for entry in disagree:
            by_cap.setdefault(entry["capability_id"], []).append(entry["case_id"])
            reasons[entry["case_id"]] = [
                f"judge({entry['judge_verdict']}, conf={entry['judge_confidence']}) "
                f"与人工({entry['human_verdict']}) 分歧"]
        for cap, case_ids in by_cap.items():
            extra: Dict[str, Any] = {}
            if case_set is not None:
                extra["case_set"] = case_set
            if cases is not None:
                extra["cases"] = list(cases)
            items = review_queue.enqueue(cap, case_ids, reasons=reasons,
                                         queued_by=str(queued_by), **extra)
            enqueued.extend(i.case_id for i in items)
    sample_size = compared
    enough = sample_size >= int(min_samples)
    days = sorted({str(r.get("day") or "") for r in latest.values() if r.get("day")})
    return {
        "capability_id": str(capability_id or ""),
        "samples": sample_size,
        "min_samples": int(min_samples),
        "agreement_rate": rate,
        "agree": agree,
        "disagree": len(disagree),
        "disagreements": disagree,
        "uncertain": uncertain,
        "undecided": undecided,
        "judge_verdicts": len(latest),
        "manual_items": len(manual_items),
        "enqueued": enqueued,
        "conclusion": ("" if not enough else
                       ("judge 与人工一致率 %s" % rate)),
        "disclosure": ("" if enough else
                       (f"样本不足（{sample_size} < {min_samples}）："
                        "**只披露不结论**，不以小样本宣布一致或不一致")),
        "window": {"judge_days": days} if days else {},
        "note": ("一致率分母 = 已裁定且非 uncertain 的人工结论；"
                 "judge 判定存档与人工台账并列、不互写"),
    }


# ════════════════════════════════════════════════════════════
#  组装：配置 → 自检 → 判定器 + 守卫 + 预算（供 ShadowRunner 注入）
# ════════════════════════════════════════════════════════════


class JudgeRuntime:
    """judge 运行时（配置 + 可用性 + 判定器 + 守卫 + 预算护栏 + 判定存档）

    `ShadowRunner(judge_runtime=...)` 注入本对象即可让灰度走 S8-04 通道；
    `to_dict()` 只出叶子字段（**不含密钥明文、不含 live judge 对象**）。
    """

    def __init__(self, *, config: JudgeConfig, availability: JudgeAvailability,
                 resolved: ResolvedJudge, guard: JudgeGuard, judge: Optional[LLMJudge],
                 budget: JudgeBudgetGuard, verdict_store: Optional[JudgeVerdictStore],
                 events: List[Dict[str, Any]]) -> None:
        self.config = config
        self.availability = availability
        self.resolved = resolved
        self.guard = guard
        self.judge = judge
        self.budget = budget
        self.verdict_store = verdict_store
        self.events = events

    @property
    def state(self) -> str:
        return self.availability.state

    @property
    def kind(self) -> str:
        return self.guard.effective_kind

    @property
    def is_llm(self) -> bool:
        return is_llm_kind(self.kind)

    def to_dict(self) -> Dict[str, Any]:
        payload = {field_name: self.availability.to_dict().get(field_name)
                   for field_name in SELF_CHECK_FIELDS if field_name != "kind"}
        payload["kind"] = self.kind
        payload["availability"] = self.availability.to_dict()
        payload["budget"] = self.budget.to_dict()
        payload["guard"] = self.guard.to_dict()
        payload["config"] = self.config.to_dict()
        payload["events"] = [dict(e) for e in self.events]
        payload["runtime_version"] = JUDGE_RUNTIME_VERSION
        payload["verdict_store"] = (self.verdict_store.summary()
                                    if self.verdict_store is not None else None)
        return payload


def build_judge_runtime(config: Optional[JudgeConfig] = None, *,
                        env: Optional[Mapping[str, str]] = None,
                        invoke: Optional[Callable[[str], str]] = None,
                        adapter: Any = None,
                        secret_provider: Optional[Callable[[str], Optional[str]]] = None,
                        dotenv_path: str = "",
                        store: Any = None,
                        events_dir: str = "",
                        verdict_store: Optional[JudgeVerdictStore] = None,
                        capability_id: str = "",
                        emit_fallback_event: bool = True,
                        day: Optional[str] = None) -> JudgeRuntime:
    """组装 judge 运行时（**默认关闭**；关闭时零副作用、零成本、零事件）

    回落/前置拦截**必定**：① 把 `judge_kind` 写成
    ``deterministic_local(<原因码>)``；② 发 `model.degraded` 事件（除非显式关掉）；
    两者都只在"确实回落"时发生（未启用 ≠ 回落 —— 未启用时 `judge_kind` 仍是
    ``deterministic_local(disabled)``，但**不发降级事件**，避免把"默认关闭"报成故障）。
    """
    resolved_config = (config if config is not None
                       else judge_config_from_env(env))
    events: List[Dict[str, Any]] = []
    dotenv = str(dotenv_path or resolved_config.dotenv_path
                 or os.path.join(_PROJECT_ROOT, ".env"))
    availability = judge_availability(
        resolved_config, env=env, secret_provider=secret_provider,
        dotenv_path=dotenv, invoke=invoke, adapter=adapter)
    budget = JudgeBudgetGuard(resolved_config, store=store, events_dir=events_dir,
                              env=env, day=day)
    judge_obj: Optional[LLMJudge] = None
    store_verdicts = verdict_store
    if store_verdicts is None and resolved_config.enabled:
        store_verdicts = JudgeVerdictStore()

    def _on_call() -> None:
        """真实调用成功后的记账 + 判定快照（前置拦截成功时不会走到这里）"""
        if judge_obj is None:
            return
        usage = dict(judge_obj.last_usage or {})
        estimated = False
        tokens_in = int(usage.get("tokens_in") or 0)
        tokens_out = int(usage.get("tokens_out") or 0)
        if not usage:
            # 适配器没给 usage ⇒ 按**本次**字符数估算，并显式标注 estimated
            estimated = True
            chars = float(resolved_config.chars_per_token or 4.0)
            tokens_in = int(int(getattr(judge_obj, "last_prompt_chars", 0) or 0) / chars)
            tokens_out = int(int(getattr(judge_obj, "last_reply_chars", 0) or 0) / chars)
        record = budget.record(
            model=resolved_config.model, provider=resolved_config.provider,
            tokens_in=tokens_in, tokens_out=tokens_out, estimated=estimated,
            interaction_id=f"judge:{capability_id or 'digestion'}:{judge_obj.calls}")
        events.append({"kind": "judge_cost", **record})

    def _precheck() -> str:
        reason = budget.precheck()
        if reason:
            code = budget.state().reason_code or JUDGE_REASON_BUDGET_EXCEEDED
            if emit_fallback_event:
                event_id = emit_judge_fallback(
                    from_model=judge_kind_for(resolved_config.provider,
                                              resolved_config.model),
                    reason=reason, reason_code=code,
                    provider=resolved_config.provider,
                    capability_id=capability_id, extra={"budget": budget.state().to_dict()},
                    store=store)
                events.append({"kind": "judge_fallback", "reason_code": code,
                               "reason": reason, "event_id": event_id})
        return reason

    if availability.available:
        judge_obj = LLMJudge(invoke=invoke, adapter=adapter,
                             provider=resolved_config.provider,
                             model=resolved_config.model,
                             threshold=resolved_config.threshold,
                             api_key=availability.credential.secret,
                             base_url=resolved_config.base_url)
        resolved = ResolvedJudge(
            scorer=judge_obj, kind=availability.kind, mode="llm",
            detail={"provider": resolved_config.provider,
                    "model": resolved_config.model,
                    "base_url": resolved_config.base_url,
                    "threshold": resolved_config.threshold,
                    "credential_source": availability.credential.source,
                    "credential_fingerprint": availability.credential.fingerprint,
                    "note": "S8-04 真实 judge（凭证 + 预算护栏已就绪）"},
            judge=judge_obj)
    else:
        resolved = ResolvedJudge(
            scorer=judge_similarity, kind=availability.kind, mode="local",
            detail={"availability_state": availability.state,
                    "unavailable_reason": availability.reason,
                    "credential": availability.credential.to_dict(),
                    "note": ("S8-04：真实 judge 不可用 ⇒ 如实回落确定性打分器"
                             "（不冒充 LLM）")})

    guard = JudgeGuard(
        resolved.scorer if availability.available else judge_similarity,
        kind_primary=availability.kind,
        kind_fallback=judge_fallback_kind(
            availability.state if availability.state == AVAILABILITY_NO_CREDENTIALS
            else JUDGE_REASON_DISABLED),
        kind_fallback_for=judge_fallback_kind,
        precheck=_precheck if availability.available else None,
        on_call=_on_call if availability.available else None)
    if not availability.available:
        # 未进入真实通道：标签已经是"如实回落"，但**不**伪造"已回落"次数
        guard.reason_code = (JUDGE_REASON_NO_CREDENTIALS
                             if availability.state == AVAILABILITY_NO_CREDENTIALS
                             else JUDGE_REASON_DISABLED)
        guard.active = "fallback"
    return JudgeRuntime(config=resolved_config, availability=availability,
                        resolved=resolved, guard=guard, judge=judge_obj,
                        budget=budget, verdict_store=store_verdicts, events=events)


def judge_self_check(config: Optional[JudgeConfig] = None, *,
                     env: Optional[Mapping[str, str]] = None,
                     secret_provider: Optional[Callable[[str], Optional[str]]] = None,
                     dotenv_path: str = "", invoke: Optional[Callable[[str], str]] = None,
                     adapter: Any = None, log: bool = True,
                     verify: bool = False) -> Dict[str, Any]:
    """启动自检：输出 judge 可用性三态（**日志与面板可读，无明文凭证**）

    只做"能否用"的判断（构造通道 + 查凭证），**默认不发起模型调用**（不花钱、不探针）。

    ``verify=True``（**opt-in，会产生一次真实调用与费用**）额外做端到端探针，
    输出 ``verified`` / ``probe``：这是为了防"**假绿灯**" —— 凭证**填了但无效**
    （如 key 过期/被撤销）时，仅凭"有凭证 + 通道可构造"会报 ``available``，
    而真实调用其实 401。口径与运营期核查建议一致：区分 **configured（已填）**
    与 **verified（最近一次真实调用成功）**。

    运行期无需 ``verify``：`JudgeGuard.probe()` 在每次灰度开始前做一次真实探针，
    失败即如实回落并把真实原因写进 ``judge_kind``。
    """
    resolved_config = (config if config is not None else judge_config_from_env(env))
    availability = judge_availability(
        resolved_config, env=env, secret_provider=secret_provider,
        dotenv_path=dotenv_path, invoke=invoke, adapter=adapter)
    payload = availability.to_dict()
    # 扁平暴露 SELF_CHECK_FIELDS（面板/日志按一维字段读，不必知道嵌套结构）
    payload.setdefault("enabled", bool(resolved_config.enabled))
    payload.setdefault("daily_budget_cents", float(resolved_config.daily_budget_cents))
    payload.setdefault("threshold", float(resolved_config.threshold))
    payload.setdefault("follow_fasting", bool(resolved_config.follow_fasting))
    payload.setdefault("credential_source", availability.credential.source)
    payload.setdefault("credential_name", availability.credential.name)
    payload.setdefault("credential_fingerprint", availability.credential.fingerprint)
    payload.setdefault("runtime_version", JUDGE_RUNTIME_VERSION)
    payload["config"] = resolved_config.to_dict()
    # configured（已填）vs verified（真实调用成功）—— **默认 None = 未验证**，
    # 绝不把"有凭证"说成"已验证"。
    payload["verified"] = None
    payload["probe"] = {"ok": None, "reason": "未做端到端探针（verify=False，不产生费用）"}
    if verify and availability.available:
        probe_judge = LLMJudge(invoke=invoke, adapter=adapter,
                               provider=resolved_config.provider,
                               model=resolved_config.model,
                               threshold=resolved_config.threshold,
                               api_key=availability.credential.secret)
        probe = probe_judge.probe()
        payload["verified"] = bool(probe.get("ok"))
        payload["probe"] = dict(probe)
        if not probe.get("ok"):
            # 探针失败 ⇒ **降级三态**并如实标注原因（不报 available 的假绿灯）
            payload["state"] = AVAILABILITY_NO_CREDENTIALS
            payload["kind"] = judge_fallback_kind(JUDGE_REASON_LLM_UNAVAILABLE)
            payload["configured"] = True
            payload["reason"] = (f"凭证/通道**存在但真实调用失败**："
                                 f"{probe.get('reason') or ''}"
                                 f"（configured=true / verified=false）")
    payload["configured"] = bool(availability.registered)
    payload["note"] = ("三态＝available / no_credentials / disabled；"
                       "默认自检不发起模型调用（verify=True 才会，且会产生费用）；"
                       "凭证只出指纹不出明文；configured=已填 / verified=真实调用成功")
    if log:
        level = logging.INFO if availability.available else logging.WARNING
        logger.log(level, "judge 启动自检：%s%s", availability.markdown(),
                   "" if payload["verified"] is None
                   else f"｜verified={payload['verified']}")
    return payload


__all__ = [
    "JUDGE_RUNTIME_VERSION", "JUDGE_ENABLE_ENV", "JUDGE_BUDGET_ENV",
    "JUDGE_THRESHOLD_ENV", "JUDGE_FOLLOW_FASTING_ENV", "JUDGE_SECRET_FILE_ENV",
    "JUDGE_DOTENV_ENV", "JUDGE_CHARS_PER_TOKEN_ENV",
    "AVAILABILITY_AVAILABLE", "AVAILABILITY_NO_CREDENTIALS",
    "AVAILABILITY_DISABLED", "AVAILABILITY_STATES",
    "JUDGE_COST_SOURCE", "DEFAULT_DAILY_BUDGET_CENTS",
    "CREDENTIAL_SOURCE_SECRET_STORE", "CREDENTIAL_SOURCE_ENV",
    "CREDENTIAL_SOURCE_DOTENV", "CREDENTIAL_SOURCE_INJECTED",
    "CREDENTIAL_SOURCE_NONE", "PROVIDER_CREDENTIAL_ENVS",
    "GENERIC_CREDENTIAL_ENV", "GENERIC_PROVIDER_ENV", "DEFAULT_SECRET_RELATIVE",
    "JUDGE_VERDICT_FILENAME", "CONSISTENCY_MIN_SAMPLES", "SELF_CHECK_FIELDS",
    "JUDGE_REASON_NOTES", "JUDGE_REASON_FORMAT",
    "parse_env_file", "secret_store_provider", "credential_names_for",
    "resolve_judge_credential", "CredentialResolution",
    "JudgeConfig", "judge_config_from_env",
    "JudgeAvailability", "judge_availability",
    "JudgeBudgetState", "JudgeBudgetGuard", "cost_policy_factor",
    "emit_judge_fallback",
    "JudgeVerdictStore", "judge_consistency",
    "JudgeRuntime", "build_judge_runtime", "judge_self_check",
]