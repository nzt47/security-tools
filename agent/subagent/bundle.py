"""分身可带走 bundle 契约（S5）—— 身份与装配 / 引用式密钥 / 幂等启动

【它解决什么】
    S5 行的设计口径是「可带走 bundle + 通信」：一个分身除了活在这一台机器上，
    还要能被**导出成一个包**带走，在另一台机器上**按同一份契约启动**。本模块把
    这件事收敛成**一份可序列化契约 + 纯函数**，不自己造第二套装配/密钥/通道口径：

        bundle = build_bundle(config, line_id=…, llm_resolution=…, assembly=…)
        text   = bundle_to_json(bundle)
        bundle2 = bundle_from_json(text)            # 严格：不合规即抛，不静默补默认
        config2 = import_config(bundle2)             # 重建 SubagentConfig
        executor = resolve_backend(bundle2, llm=…)   # inproc / subprocess

【固定结构（键集可被测试钉死）】
    schema_version / bundle_id / generated_at
    identity   {name, tags, ttl_seconds, context_window}
    assembly   {role{template,text,mode}, model{model_id,temperature},
                memory{mode,scope}, tools{tool_sources,line,authorized?},
                skills?, permissions}
    secrets    {refs:[{source,name,env_var}]}    ← **只存引用，绝不存值**
    entrypoint {protocol:"task_file-jsonl", argv_template:[…]}
    runtime    {backend}

【三条守卫（设计原文）】
    ① **导出包不含任何密钥形态**：build_bundle 返回前过
       agent/subagent/credentials.py::assert_manifest_secret_free（复用既有闸门，
       本模块**不重写**密钥正则）。secrets 段只放引用三元组；任何 ref 带了第四个键
       （例如 value）一律抛异常——"顺手把值也带上"必须是一条走不通的路。
    ② **导入后结论与母体一致（同源对拍）**：bundle 存的是 model.model_id /
       model.temperature（**请求值**，不是解析后的生效值）与 tools.line，因此
       导入后分别再跑 resolve_subagent_llm / resolve_subagent_assembly，结论与
       导出侧逐字一致。存"生效值"会把 explicit 在导入侧降级成 inherit。
    ③ **同一 bundle 幂等启动，换后端不改协议**：import_config 是纯函数（同一
       bundle 导入两次得到逐字相同的配置）；runtime.backend 决定执行后端，但
       entrypoint.argv_template 对 inproc / subprocess 是**同一份**
       task_file-jsonl 协议，render_argv() 的渲染结果恒等于
       channel.build_cli_argv()。

【失败语义：fail-closed】
    · 未知 schema_version / 非对象 / 缺必需键 ⇒ BundleValidationError（不静默补默认）；
    · 未知 runtime.backend ⇒ UnsupportedBackend（不静默回落到 inproc）；
    · 包内检出密钥形态 ⇒ ManifestSecretLeak（credentials.py 既有异常）。

【依赖纪律】
    仅承载"配置 → 契约"的纯逻辑；executor / assembly / lines 全部**函数内惰性导入**，
    免得导出契约反向把执行器与主线装配的导入期副作用带进每个使用方。
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from agent.subagent.channel import (
    CLI_ARGV_TAIL,
    DEFAULT_MAX_TURNS,
    DEFAULT_OUTPUT_FORMAT,
    default_agent_cli,
    split_agent_cli,
)
from agent.subagent.container import SubagentConfig
from agent.subagent.credentials import (
    ENV_PREFIX,
    assert_manifest_secret_free,
    find_manifest_secrets,
)

logger = logging.getLogger(__name__)

#: bundle 契约版本（结构一变必须 +1，bundle_from_json 据此 fail-closed）
#: v2 新增可选顶层 environment 段（离线依赖清单）；导入侧**兼容 v1**（旧包仍可带走）。
BUNDLE_SCHEMA_VERSION = 2
SUPPORTED_SCHEMA_VERSIONS: Tuple[int, ...] = (1, 2)

#: entrypoint 协议名（唯一取值；inproc / subprocess 共用）
BUNDLE_PROTOCOL = "task_file-jsonl"

#: 后端词表（复用既有两条真实执行路径；container 属 S5 以外的后续批次）
BACKEND_INPROC = "inproc"
BACKEND_SUBPROCESS = "subprocess"
SUPPORTED_BACKENDS: Tuple[str, ...] = (BACKEND_INPROC, BACKEND_SUBPROCESS)
DEFAULT_BACKEND = BACKEND_INPROC

#: argv 模板占位符（{cli} 渲染成可执行串切分后的多个 token）
ARGV_PLACEHOLDER_CLI = "{cli}"
ARGV_PLACEHOLDER_TASK_FILE = "{task_file}"
ARGV_PLACEHOLDER_OUTPUT_FORMAT = "{output_format}"
ARGV_PLACEHOLDER_MAX_TURNS = "{max_turns}"

#: §3.10 协议 argv 模板（**唯一权威**；渲染结果必须等于 build_cli_argv）
ARGV_TEMPLATE: Tuple[str, ...] = (
    ARGV_PLACEHOLDER_CLI,
    "-p",
    ARGV_PLACEHOLDER_TASK_FILE,
    "--output-format",
    ARGV_PLACEHOLDER_OUTPUT_FORMAT,
    "--max-turns",
    ARGV_PLACEHOLDER_MAX_TURNS,
)

_ARGV_PLACEHOLDERS = frozenset({
    ARGV_PLACEHOLDER_CLI, ARGV_PLACEHOLDER_TASK_FILE,
    ARGV_PLACEHOLDER_OUTPUT_FORMAT, ARGV_PLACEHOLDER_MAX_TURNS,
})

#: 顶层必需键（缺一即非法）
REQUIRED_TOP_KEYS: Tuple[str, ...] = (
    "schema_version", "bundle_id", "generated_at",
    "identity", "assembly", "secrets", "entrypoint", "runtime",
)

#: 引用式密钥条目的键集（**恰好三个**；多一个键即拒——防"顺手带值"）
SECRET_REF_KEYS: Tuple[str, ...] = ("source", "name", "env_var")

#: identity / assembly 子结构的必需键
_IDENTITY_KEYS: Tuple[str, ...] = ("name", "tags", "ttl_seconds", "context_window")
_ASSEMBLY_KEYS: Tuple[str, ...] = ("role", "model", "memory", "tools", "permissions")
_ROLE_KEYS: Tuple[str, ...] = ("template", "text", "mode")
_MODEL_KEYS: Tuple[str, ...] = ("model_id", "temperature")
_MEMORY_KEYS: Tuple[str, ...] = ("mode", "scope")
_TOOLS_REQUIRED_KEYS: Tuple[str, ...] = ("tool_sources", "line")


# ════════════════════════════════════════════════════════════
#  异常
# ════════════════════════════════════════════════════════════


class BundleError(Exception):
    """bundle 契约异常基类"""

    code = "E_BUNDLE_INVALID"


class BundleValidationError(BundleError):
    """bundle 非法（结构 / 版本 / 必需键 / 缺失协议），携带问题清单"""

    code = "E_BUNDLE_INVALID"

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = [str(p) for p in problems]
        super().__init__(
            f"{self.code}: 非法 bundle（{len(self.problems)} 处）：" + "；".join(self.problems))


class UnsupportedBackend(BundleError):
    """未知 runtime.backend（**不静默回落**到 inproc）"""

    code = "E_BUNDLE_BACKEND_UNSUPPORTED"

    def __init__(self, backend: Any, supported: Sequence[str] = SUPPORTED_BACKENDS) -> None:
        self.backend = str(backend or "")
        self.supported = tuple(str(s) for s in supported)
        super().__init__(
            f"{self.code}: 未知执行后端 {self.backend!r}；"
            f"已知后端 {list(self.supported)}（fail-closed：不回落到默认后端）")


# ════════════════════════════════════════════════════════════
#  构造
# ════════════════════════════════════════════════════════════


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_str_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return [str(value)]
    return [str(v) for v in value if str(v).strip()]


def _normalize_secret_ref(ref: Any) -> Dict[str, str]:
    """把一条密钥引用归一为恰好三键；**多了键就抛**（尤其 value）"""
    if not isinstance(ref, Mapping):
        raise BundleValidationError([f"secrets.refs 条目必须是对象，收到 {type(ref).__name__}"])
    keys = tuple(str(k) for k in ref.keys())
    extra = sorted(set(keys) - set(SECRET_REF_KEYS))
    missing = sorted(set(SECRET_REF_KEYS) - set(keys))
    problems: List[str] = []
    if extra:
        problems.append(f"secrets.refs 条目含非法键 {extra}（只允许 {list(SECRET_REF_KEYS)}，**不得存值**）")
    if missing:
        problems.append(f"secrets.refs 条目缺键 {missing}")
    out: Dict[str, str] = {}
    for key in SECRET_REF_KEYS:
        value = ref.get(key)
        if not isinstance(value, str) or not value.strip():
            problems.append(f"secrets.refs.{key} 必须是非空字符串")
        else:
            out[key] = value.strip()
    if problems:
        raise BundleValidationError(problems)
    return out


def secret_refs_from_env(environ: Optional[Mapping[str, str]] = None) -> List[Dict[str, str]]:
    """从环境**变量名**派生密钥引用（**只读名字，绝不读值**）

    CP_TEMP_* 是 credentials.py::ENV_PREFIX 约定的临时凭据注入位。导出时给出
    "这个包需要哪些凭据槽位"即可——值由到达端自己的 TTL 凭据管理器签发。
    environ 可注入（测试/离线打包时喂一份环境快照，不依赖进程真环境）。
    """
    env = os.environ if environ is None else environ
    refs: List[Dict[str, str]] = []
    for key in sorted(str(k) for k in env.keys()):
        if not key.startswith(ENV_PREFIX):
            continue
        refs.append({"source": "env", "name": key[len(ENV_PREFIX):], "env_var": key})
    return refs


def build_bundle(
    config: SubagentConfig,
    *,
    line_id: str = "",
    llm_resolution: Any = None,
    assembly: Any = None,
    secret_refs: Iterable[Mapping[str, Any]] = (),
    environment: Optional[Mapping[str, Any]] = None,
    backend: str = DEFAULT_BACKEND,
    generated_at: Optional[str] = None,
    bundle_id: Optional[str] = None,
) -> Dict[str, Any]:
    """把一份分身配置固化成可带走的 bundle（导出前**必过密钥闸**）

    Args:
        config: 分身配置（SubagentConfig）。
        line_id: 生效主线 id（空 = 未点名 ⇒ 导入侧按只读默认集装配）。
        llm_resolution: LlmResolution（可选）。只取它的**请求值**
            （requested / temperature）——存生效值会让 explicit 在导入侧变 inherit。
        assembly: SubagentAssembly（可选）。给了就带 tools.authorized 与
            skills 快照（授权面，不含任何正文）。
        secret_refs: 引用式密钥条目（恰好 source/name/env_var 三键）。
        backend: 执行后端（inproc / subprocess）。
        generated_at / bundle_id: 注入点（测试复现；缺省用当前时间与随机 id）。

    Returns:
        bundle dict（JSON-able；所有序列都是 list，便于往返逐字相等）。

    Raises:
        BundleValidationError: 某个 secret_ref 形态非法。
        ManifestSecretLeak: 包内检出长期密钥（§2.3 硬约束）。
    """
    refs = [_normalize_secret_ref(r) for r in (secret_refs or ())]

    model_id = str(config.model_id or "")
    temperature: Optional[float] = config.llm_temperature
    if llm_resolution is not None:
        requested = getattr(llm_resolution, "requested", None)
        if requested is not None and str(requested).strip():
            model_id = str(requested).strip()
        if getattr(llm_resolution, "temperature", None) is not None:
            temperature = llm_resolution.temperature

    tools: Dict[str, Any] = {
        "tool_sources": _as_str_list(getattr(config, "tool_sources", None)),
        "line": str(line_id or ""),
    }
    if assembly is not None:
        tools["authorized"] = [str(t) for t in (getattr(assembly, "tools", ()) or ())]
        needs = [str(t) for t in (getattr(assembly, "needs_approval", ()) or ())]
        if needs:
            tools["needs_approval"] = needs

    asm: Dict[str, Any] = {
        "role": {
            "template": str(getattr(config, "role_template", "") or ""),
            "text": str(getattr(config, "role_text", "") or ""),
            "mode": str(getattr(config, "role_mode", "template") or "template"),
        },
        "model": {
            "model_id": model_id,
            "temperature": (None if temperature is None else float(temperature)),
        },
        "memory": {
            "mode": str(getattr(config, "memory_mode", "none") or "none"),
            "scope": (dict(config.memory_scope) if getattr(config, "memory_scope", None) else None),
        },
        "tools": tools,
        "permissions": _as_str_list(getattr(config, "permissions", None)),
    }
    if assembly is not None:
        asm["skills"] = {
            "ids": [str(s) for s in (getattr(assembly, "skills", ()) or ())],
            "mode": str(getattr(assembly, "skills_mode", "") or ""),
        }

    bundle: Dict[str, Any] = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "bundle_id": str(bundle_id or f"bnd-{uuid.uuid4().hex[:12]}"),
        "generated_at": str(generated_at or _now_iso()),
        "identity": {
            "name": str(getattr(config, "name", "") or ""),
            "tags": _as_str_list(getattr(config, "tags", None)),
            "ttl_seconds": int(getattr(config, "ttl_seconds", 0) or 0),
            "context_window": int(getattr(config, "context_window", 4096) or 4096),
        },
        "assembly": asm,
        "secrets": {"refs": refs},
        "entrypoint": {"protocol": BUNDLE_PROTOCOL, "argv_template": list(ARGV_TEMPLATE)},
        "runtime": {"backend": str(backend or DEFAULT_BACKEND)},
    }
    if environment is not None:
        # 离线依赖清单（v2 顶层可选段）。包名以 list[{name,...}] 承载，绝不作 dict 键：
        # 否则 tokenizers 这类包名会命中密钥闸的"可疑键名"正则 ⇒ 导出被误判为密钥泄漏。
        bundle["environment"] = dict(environment)
    # ① 导出前必过既有密钥闸门（复用 credentials.py，不重写正则）
    assert_manifest_secret_free(bundle)
    return bundle


# ════════════════════════════════════════════════════════════
#  序列化（严格）
# ════════════════════════════════════════════════════════════


def bundle_to_json(bundle: Mapping[str, Any]) -> str:
    """bundle → JSON 文本（排序键，便于 diff / 复制 / 逐字对拍）"""
    return json.dumps(bundle, ensure_ascii=False, sort_keys=True, indent=2)


def bundle_from_json(text: str) -> Dict[str, Any]:
    """JSON 文本 → bundle（**严格**：不合规即抛，不静默补默认）

    Raises:
        BundleValidationError: 非 JSON / 非对象 / 未知 schema_version / 缺必需键。
    """
    try:
        doc = json.loads(text)
    except (ValueError, TypeError) as e:
        raise BundleValidationError([f"不是合法 JSON：{e}"]) from e
    if not isinstance(doc, dict):
        raise BundleValidationError([f"bundle 顶层必须是对象，实际为 {type(doc).__name__}"])
    problems: List[str] = []
    if doc.get("schema_version") not in SUPPORTED_SCHEMA_VERSIONS:
        problems.append(
            f"schema_version 不受支持：{doc.get('schema_version')!r}"
            f"（支持 {list(SUPPORTED_SCHEMA_VERSIONS)}）")
    problems.extend(f"缺少必需键：{k}" for k in REQUIRED_TOP_KEYS if k not in doc)
    if problems:
        raise BundleValidationError(problems)
    return doc


# ════════════════════════════════════════════════════════════
#  校验（问题清单，空 = 通过）
# ════════════════════════════════════════════════════════════


def _check_sub_object(bundle: Mapping[str, Any], key: str,
                      required_keys: Sequence[str], problems: List[str]) -> Optional[Mapping[str, Any]]:
    value = bundle.get(key)
    if not isinstance(value, Mapping):
        problems.append(f"{key} 必须是对象")
        return None
    for sub in required_keys:
        if sub not in value:
            problems.append(f"{key} 缺少必需键：{sub}")
    return value


def validate_bundle(bundle: Any) -> List[str]:
    """校验 bundle，返回问题清单（**空列表 = 通过**）

    覆盖：顶层必需键 / schema_version / 三个子结构 / secrets 只存引用 /
    entrypoint 协议与 argv 模板 / runtime.backend 在词表内 / 包内无密钥形态。
    """
    problems: List[str] = []
    if not isinstance(bundle, Mapping):
        return [f"bundle 顶层必须是对象，实际为 {type(bundle).__name__}"]
    schema_version = bundle.get("schema_version")
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        problems.append(
            f"schema_version 不受支持：{schema_version!r}"
            f"（支持 {list(SUPPORTED_SCHEMA_VERSIONS)}）")
    problems.extend(f"缺少必需键：{k}" for k in REQUIRED_TOP_KEYS if k not in bundle)
    # v2 起要求 environment 段（依赖清单）；v1 旧包仍接受（不因新增字段而拒绝"可带走"）
    if schema_version == 2 and "environment" not in bundle:
        problems.append("缺少必需键：environment（schema_version=2）")
    if "environment" in bundle:
        from agent.subagent.dependencies import validate_environment
        problems.extend(validate_environment(bundle.get("environment")))

    # ── identity ──
    ident = _check_sub_object(bundle, "identity", _IDENTITY_KEYS, problems)
    if ident is not None and not str(ident.get("name") or "").strip():
        problems.append("identity.name 不得为空")
    # ── assembly ──
    asm = _check_sub_object(bundle, "assembly", _ASSEMBLY_KEYS, problems)
    if asm is not None:
        _check_sub_object(asm, "role", _ROLE_KEYS, problems)
        _check_sub_object(asm, "model", _MODEL_KEYS, problems)
        _check_sub_object(asm, "memory", _MEMORY_KEYS, problems)
        tools = _check_sub_object(asm, "tools", _TOOLS_REQUIRED_KEYS, problems)
        if tools is not None and "tool_sources" in tools and not isinstance(tools["tool_sources"], (list, tuple)):
            problems.append("assembly.tools.tool_sources 必须是列表")
        if "permissions" in asm and not isinstance(asm["permissions"], (list, tuple)):
            problems.append("assembly.permissions 必须是列表")
    # ── secrets：只存引用 ──
    secrets = bundle.get("secrets")
    if not isinstance(secrets, Mapping):
        problems.append("secrets 必须是对象")
    else:
        refs = secrets.get("refs")
        if not isinstance(refs, list):
            problems.append("secrets.refs 必须是列表")
        else:
            for idx, ref in enumerate(refs):
                try:
                    _normalize_secret_ref(ref)
                except BundleValidationError as e:
                    problems.extend(f"secrets.refs[{idx}]: {p}" for p in e.problems)
    # ── entrypoint：协议与 argv 模板 ──
    ep = _check_sub_object(bundle, "entrypoint", ("protocol", "argv_template"), problems)
    if ep is not None:
        if ep.get("protocol") != BUNDLE_PROTOCOL:
            problems.append(
                f"entrypoint.protocol 必须是 {BUNDLE_PROTOCOL!r}，收到 {ep.get('protocol')!r}")
        tmpl = ep.get("argv_template")
        if not isinstance(tmpl, list) or not all(isinstance(t, str) for t in tmpl):
            problems.append("entrypoint.argv_template 必须是字符串列表")
        else:
            literals = tuple(t for t in tmpl if t not in _ARGV_PLACEHOLDERS)
            if literals != CLI_ARGV_TAIL:
                problems.append(
                    f"entrypoint.argv_template 的固定 token {list(literals)} 与 §3.10 "
                    f"协议 {list(CLI_ARGV_TAIL)} 不一致")
            missing = sorted(_ARGV_PLACEHOLDERS - set(tmpl))
            if missing:
                problems.append(f"entrypoint.argv_template 缺占位符 {missing}")
    # ── runtime.backend：fail-closed ──
    runtime = bundle.get("runtime")
    if not isinstance(runtime, Mapping):
        problems.append("runtime 必须是对象")
    else:
        backend = runtime.get("backend")
        if backend not in SUPPORTED_BACKENDS:
            problems.append(
                f"runtime.backend 未知：{backend!r}（已知 {list(SUPPORTED_BACKENDS)}）")

    # ── 全包密钥扫描（与导出闸门同一份判定）──
    for offender in find_manifest_secrets(bundle):
        problems.append(f"包内疑似长期密钥：{offender}")
    # 去重保序
    seen: List[str] = []
    for p in problems:
        if p not in seen:
            seen.append(p)
    return seen


# ════════════════════════════════════════════════════════════
#  导入（重建配置；幂等）
# ════════════════════════════════════════════════════════════


def import_config(bundle: Mapping[str, Any]) -> SubagentConfig:
    """从 bundle 重建 SubagentConfig（**纯函数**；同一 bundle 两次导入逐字相等）

    只重建 SubagentConfig 承载得住的字段。memory_provider 不在契约结构里
    （设计的 identity/assembly 段未收录它，且它当前是"声明字段无消费者"），
    导入侧取 SubagentConfig 默认值 —— 这是**已登记的边界**，不是静默丢字段。

    Raises:
        BundleValidationError: bundle 非法（不建半成品配置）。
    """
    problems = validate_bundle(bundle)
    if problems:
        raise BundleValidationError(problems)
    ident = bundle["identity"]
    asm = bundle["assembly"]
    model = asm["model"]
    memory = asm["memory"]
    role = asm["role"]
    temperature = model.get("temperature")
    return SubagentConfig(
        name=str(ident["name"]),
        model_id=str(model.get("model_id") or ""),
        tags=[str(t) for t in (ident.get("tags") or [])],
        ttl_seconds=int(ident.get("ttl_seconds") or 0),
        context_window=int(ident.get("context_window") or 4096),
        tool_sources=[str(t) for t in (asm["tools"].get("tool_sources") or [])],
        permissions=[str(p) for p in (asm.get("permissions") or ["read"])],
        llm_temperature=(None if temperature is None else float(temperature)),
        role_template=str(role.get("template") or ""),
        role_text=str(role.get("text") or ""),
        role_mode=str(role.get("mode") or "template"),
        memory_mode=str(memory.get("mode") or "none"),
        memory_scope=(dict(memory["scope"]) if memory.get("scope") else None),
    )


# ════════════════════════════════════════════════════════════
#  协议渲染（换后端不改协议）
# ════════════════════════════════════════════════════════════


def render_argv(bundle: Mapping[str, Any], *, agent_cli: str, task_file: str,
                max_turns: int = DEFAULT_MAX_TURNS,
                output_format: str = DEFAULT_OUTPUT_FORMAT) -> Tuple[str, ...]:
    """把 entrypoint.argv_template 渲染成 argv（**必须**等于 build_cli_argv）

    【为什么不是"再拼一遍命令行"】模板是**声明**，渲染是**求值**：两处各写一遍
    §3.10 的形态，迟早分叉。本函数只做占位符替换，{cli} 的切分复用
    channel.split_agent_cli（同一份 shell 规则）；守卫用例把渲染结果与
    channel.build_cli_argv 做逐 token 对拍。
    """
    problems = validate_bundle(bundle)
    if problems:
        raise BundleValidationError(problems)
    tmpl = bundle["entrypoint"]["argv_template"]
    if not str(task_file or "").strip():
        raise BundleError("task_file 不得为空")
    turns = int(max_turns)
    if turns <= 0:
        raise BundleError(f"max_turns 必须为正整数：{max_turns!r}")
    cli_parts = split_agent_cli(agent_cli)
    rendered: List[str] = []
    for token in tmpl:
        if token == ARGV_PLACEHOLDER_CLI:
            rendered.extend(cli_parts)
        elif token == ARGV_PLACEHOLDER_TASK_FILE:
            rendered.append(str(task_file))
        elif token == ARGV_PLACEHOLDER_OUTPUT_FORMAT:
            rendered.append(str(output_format or DEFAULT_OUTPUT_FORMAT))
        elif token == ARGV_PLACEHOLDER_MAX_TURNS:
            rendered.append(str(turns))
        else:
            rendered.append(str(token))
    return tuple(rendered)


# ════════════════════════════════════════════════════════════
#  后端解析（inproc / subprocess；未知即 fail-closed）
# ════════════════════════════════════════════════════════════


def get_backend(bundle: Mapping[str, Any]) -> str:
    """取 runtime.backend；未知/缺省即抛 UnsupportedBackend（**不回落**）"""
    if not isinstance(bundle, Mapping):
        raise BundleValidationError([f"bundle 顶层必须是对象，实际为 {type(bundle).__name__}"])
    runtime = bundle.get("runtime")
    backend = ""
    if isinstance(runtime, Mapping):
        backend = str(runtime.get("backend") or "")
    if backend not in SUPPORTED_BACKENDS:
        raise UnsupportedBackend(backend)
    return backend


def resolve_backend(bundle: Mapping[str, Any], *, llm: Any = None, agent_cli: str = "",
                    channel: Any = None, **kwargs: Any) -> Any:
    """把 runtime.backend 映射到既有执行器（复用 build_executor）

    · inproc     → 内部 LLM 执行器（LlmChannelExecutor，同进程）；
    · subprocess → 外部 agent CLI 执行器（需 agent_cli / CP_SUBAGENT_AGENT_CLI）；
    两者共用 entrypoint.argv_template 的同一份 task_file-jsonl 协议。

    Raises:
        UnsupportedBackend: 未知后端（fail-closed）。
        BundleError: inproc 未注入 llm / subprocess 未配置 CLI。
    """
    backend = get_backend(bundle)
    from agent.subagent.executor import LlmChannelExecutor, build_executor

    if backend == BACKEND_SUBPROCESS:
        cli = str(agent_cli or default_agent_cli() or "").strip()
        if not cli:
            raise BundleError(
                "subprocess 后端需要 agent_cli（CP_SUBAGENT_AGENT_CLI 未配置）："
                "已停止启动，不回落到 inproc")
        return build_executor(llm=llm, agent_cli=cli, channel=channel, **kwargs)

    # inproc：显式构造内部 LLM 通道，避免被环境里的 CP_SUBAGENT_AGENT_CLI 拐去子进程
    if channel is not None:
        return build_executor(llm=llm, channel=channel, **kwargs)
    if llm is None:
        raise BundleError("inproc 后端需要注入 llm（内部 LLM 执行器）")
    return build_executor(llm=llm, channel=LlmChannelExecutor(llm), **kwargs)


__all__ = [
    "BUNDLE_SCHEMA_VERSION", "BUNDLE_PROTOCOL",
    "BACKEND_INPROC", "BACKEND_SUBPROCESS", "SUPPORTED_BACKENDS", "DEFAULT_BACKEND",
    "ARGV_TEMPLATE", "REQUIRED_TOP_KEYS", "SECRET_REF_KEYS",
    "BundleError", "BundleValidationError", "UnsupportedBackend",
    "build_bundle", "bundle_to_json", "bundle_from_json",
    "validate_bundle", "import_config", "render_argv", "get_backend", "resolve_backend",
    "secret_refs_from_env",
]
