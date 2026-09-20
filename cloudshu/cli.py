"""云枢能力层 CLI（`python -m cloudshu ...`）—— 非 LLM 入口的"人"那一半

## 为什么需要一个顶层包

`TASK-05` §3 第 4 步第 3 项的实测提醒：

> 落地位置：`python -m cloudshu` 需要一个顶层包。**先核查**
> `pyproject.toml` 的 `packages.find` ⇒ 新增包**必须**登记进去，
> 否则安装后不可见。

本仓库的 `where` 列表已加入 `"cloudshu"`（见 `pyproject.toml`）。
**未登记时 `pip install -e .` 后 `python -m cloudshu` 会 `ModuleNotFoundError`** ——
那是"本地能跑、装完就没了"的经典陷阱。

## 🔴 架构选择：CLI 是**能力服务的客户端**，不是"另一个进程里自己跑一套工具"

这是本模块最需要解释的一个决定。

**候选与结论**

| 方案 | 结论 |
|---|---|
| **A. CLI = HTTP 客户端**（默认，唯一实现） | ✅ **采纳** |
| B. CLI 在**自己的进程里**注册一套工具再本地调用 | ❌ 否决 |

**为什么否决 B（"嵌入式"）**：

1. **它就是第二份执行环境**。能力的执行收口是**服务进程里的
   `agent/tools/__init__.py::_registry`**（闸门/限流/审批/审计/健康全在那里）。
   CLI 在另一个进程里重新注册一套，等于造出**第二份注册面** ——
   与 `TASK-00` §0.5 的 **D1（单一真相源）** 精神直接冲突。
2. **它必然与生产注册面漂移**（本任务实测）：生产的注册入口是
   `agent/orchestrator/lifecycle_manager.py::_register_builtin_tools(self)`，
   它依赖一个**完整的 `LifecycleManager` 实例**（`self` 被当作 `dl` 传入，
   内含真实 `_config` / `_planning_tools` / `_permission`）。
   用桩把 `dl` 装起来虽然能跑（`TASK-00` §0.3 用过这手法），
   但**桩的形状替代不了生产形状** —— 那正是 `TASK-00` §0.2f / D12
   "**测试夹具冒充生产**是最隐蔽的一类假绿"所警告的。
   实测差距：桩装载得到 **89** 个工具，而生产路径的注册面是 **91** 个。
3. **审计与身份会分裂**：服务进程里的每次调用进同一条审计链；
   嵌入式 CLI 的调用进**另一个进程的**内存状态与日志。

**A 的代价（如实说）**：CLI 依赖能力服务在跑。这在 `TASK-05` 的判据下不构成问题 ——
目标是"被 CI 与被别的系统用起来"，而 CLI 与 CI 用的**本来就是同一个能力服务**；
`v1.4` §5.3 的承诺也正是"CLI 输出 JSON 结构遵循 semver，与 HTTP 完全一致"，
**它本来就是同一个结构**。
`--endpoint` 可指向任意实例（默认 `CP_CAPABILITY_ENDPOINT` 或
`http://127.0.0.1:5678`），因此 `python app_server.py` 起的那个实例天然就是目标。

## 命令

    python -m cloudshu list      [--kind tool|skill] [--location local|remote]
                                 [--model M] [--identity human] [--json] [--limit N]
    python -m cloudshu describe  <name> [--json]
    python -m cloudshu invoke    <name> [--args '{"k":"v"}'] [--arg k=v]
                                 [--identity human] [--json]
    python -m cloudshu search    <query> [--top-k N] [--json]
    python -m cloudshu health    [--json]

## 为什么 CLI 也要带 `--identity`

同一能力对"人"与"服务账号"的可用性**不同**（`callable_by`）。若 CLI 不暴露身份，
CI 想用 CLI 走一遍就会拿到与人相同的判定 —— 那等于把身份层架空了。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

__all__ = ["main", "build_parser", "DEFAULT_ENDPOINT"]

_EXIT_OK = 0
_EXIT_ERROR = 1
_EXIT_USAGE = 2

DEFAULT_ENDPOINT = "http://127.0.0.1:5678"


def _force_utf8_stdio() -> None:
    """把 stdout/stderr 固定成 UTF-8

    【为什么必须做】本沙箱是 UTF-8/GBK 混合环境（`TASK-00` §0.2d 实测 4 类假象）。
    输出含 `⇒` / `✅` 这类字符时，GBK 控制台会抛
    `UnicodeEncodeError: 'gbk' codec can't encode character '⇒'`
    —— 那是**环境问题伪装成产品缺陷**。`scripts/audit_dependency_drift.py`
    曾因此有 10 处告警，故这里照抄同一对策。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="cloudshu",
        description="云枢能力层 CLI —— 不经过 agent loop、不依赖 LLM 的能力入口")
    ap.add_argument("--version", action="version", version="cloudshu 1.0.0")
    # 【不易·`--json` 为什么既在顶层又在每个子命令上】
    #   argparse 的顶层可选参数只能写在子命令**之前**（`cloudshu --json invoke x`），
    #   而人的直觉写法是写在后面（`cloudshu invoke x --json`）。只支持前者会让最
    #   常见的写法报 `unrecognized arguments`（实测踩到，退出码 2）。
    #   故用 `parents` 让两个位置都能写。
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true",
                        help="输出 JSON（结构与 HTTP 端点**逐字段一致**）")
    ap.add_argument("--json", action="store_true",
                    help=argparse.SUPPRESS)   # 顶层同名参数（位置兼容）
    ap.add_argument("--endpoint", default=None,
                    help=f"能力服务地址（默认 $CP_CAPABILITY_ENDPOINT 或 {DEFAULT_ENDPOINT}）")
    ap.add_argument("--token", default=None,
                    help="API 令牌（默认 $CP_API_TOKEN / $API_TOKEN）")
    ap.add_argument("--timeout", type=int, default=30, help="HTTP 超时秒数")
    sub = ap.add_subparsers(dest="command")

    p_list = sub.add_parser("list", parents=[common], help="列出能力（全量工具清单）")
    p_list.add_argument("--kind", choices=["tool", "skill"], default=None)
    p_list.add_argument("--location", choices=["local", "remote"], default=None)
    p_list.add_argument("--owner", default=None)
    p_list.add_argument("--namespace", default=None)
    p_list.add_argument("--tenant-id", dest="tenant_id", default=None)
    p_list.add_argument("--identity", default=None,
                        help="按入口身份过滤（llm/human/system/service_account）")
    p_list.add_argument("--model", default="",
                        help="模型名；不支持 tool calling 时返回裁剪后的清单")
    p_list.add_argument("--impl-status", dest="impl_status", default=None,
                        help="按实现状态过滤（implemented/not_implemented/...）")
    p_list.add_argument("--q", default=None, help="名字子串过滤")
    p_list.add_argument("--limit", type=int, default=0)
    p_list.add_argument("--offset", type=int, default=0)
    p_list.add_argument("--healthy-only", dest="healthy_only", action="store_true")
    p_list.add_argument("--llm-visible-only", dest="llm_visible_only",
                        action="store_true")

    p_desc = sub.add_parser("describe", parents=[common], help="查看单条能力详情")
    p_desc.add_argument("name")
    p_desc.add_argument("--tenant-id", dest="tenant_id", default="default")

    p_inv = sub.add_parser("invoke", parents=[common], help="调用一个能力")
    p_inv.add_argument("name")
    p_inv.add_argument("--args", default="{}", help="JSON 对象形式的参数")
    p_inv.add_argument("--arg", action="append", default=[],
                       help="单参数 k=v（可重复；与 --args 合并，--arg 优先）")
    p_inv.add_argument("--identity", default="human",
                       choices=["llm", "human", "system", "service_account"])
    p_inv.add_argument("--tenant-id", dest="tenant_id", default="default")
    p_inv.add_argument("--version", default="")

    p_search = sub.add_parser("search", parents=[common],
                              help="技能语义召回（复用既有检索栈）")
    p_search.add_argument("query")
    p_search.add_argument("--top-k", dest="top_k", type=int, default=5)
    p_search.add_argument("--no-vector", dest="use_vector", action="store_false")
    p_search.add_argument("--no-bm25", dest="use_bm25", action="store_false")
    p_search.add_argument("--reranker", dest="use_reranker", action="store_true")

    sub.add_parser("health", parents=[common], help="Registry / Loader 状态")
    return ap


def _parse_args_blob(raw: str, pairs: Sequence[str]) -> Dict[str, Any]:
    """合并 `--args '{...}'` 与 `--arg k=v`（后者优先）

    【为什么允许 `--arg k=v`】`--args` 在 PowerShell 里要写一层额外的引号转义
    （`TASK-00` §0.2d 第 3 类：PowerShell 重定向与引号会把 JSON 弄坏）。
    给一条不需要转义的入口能显著降低"人用 CLI"的摩擦 —— 这正是本 CLI 的用途。
    """
    out: Dict[str, Any] = {}
    if raw and raw.strip() and raw.strip() != "{}":
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"--args 不是合法 JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError("--args 必须是 JSON 对象")
        out.update(parsed)
    for pair in pairs:
        key, _, val = str(pair).partition("=")
        key = key.strip()
        if not key:
            continue
        out[key] = _coerce(val)
    return out


def _coerce(raw: str) -> Any:
    """把 CLI 字符串按 JSON 规则还原成布尔/数字/字符串（`true` → True）"""
    text = str(raw).strip()
    low = text.lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("null", "none"):
        return None
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


# ════════════════════════════════════════════════════════════
#  HTTP 客户端（唯一执行通道；见模块 docstring 的架构选择）
# ════════════════════════════════════════════════════════════


class _Client:
    """能力服务的极简 HTTP 客户端

    【为什么用 stdlib `urllib` 而不是 `agent.web.http_client.HttpClient`】
    `HttpClient` 内含 **EgressGuard 出域检查点**（`agent/web/http_client.py:100-120`），
    它守的是**平台自身的出网行为**（SSRF / 凭据外泄）。CLI 是一个**本地运维工具**，
    它访问的是用户自己指定的能力服务地址 —— 把它塞进平台的出域策略里，
    会出现"用户的 CLI 被平台的 SSRF 规则拦住"这种莫名其妙的行为。
    另外 `urllib` 是 stdlib，CLI 因此**零新增依赖**（D3）。
    """

    def __init__(self, endpoint: str, token: str, timeout: int = 30) -> None:
        self.endpoint = str(endpoint or DEFAULT_ENDPOINT).rstrip("/")
        self.token = str(token or "")
        self.timeout = int(max(1, timeout))

    def _headers(self, *, json_body: bool) -> Dict[str, str]:
        h = {"Accept": "application/json"}
        if json_body:
            h["Content-Type"] = "application/json"
        if self.token:
            h["X-API-Token"] = self.token
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def request(self, method: str, path: str, *,
                body: Optional[Dict[str, Any]] = None
                ) -> Tuple[int, Dict[str, Any], str]:
        import urllib.error
        import urllib.request

        url = f"{self.endpoint}{path}"
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(  # noqa: S310  endpoint 由用户显式指定
            url, data=data, method=method.upper(),
            headers=self._headers(json_body=body is not None))
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
                raw = resp.read().decode("utf-8", errors="replace")
                status = resp.status
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            status = exc.code
        except Exception as exc:  # noqa: BLE001  连不上 ⇒ 明确报告而不是崩栈
            return -1, {
                "status": "error", "code": "unhealthy", "data": None,
                "error": {"code": "unhealthy",
                          "message": f"无法连接能力服务 {self.endpoint}",
                          "detail": f"{type(exc).__name__}: {exc}",
                          "retryable": True},
                "meta": {},
            }, f"{type(exc).__name__}: {exc}"
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {"status": "error", "code": "internal_error", "data": None,
                      "error": {"code": "internal_error",
                                "message": "服务返回的不是合法 JSON",
                                "detail": raw[:200], "retryable": False},
                      "meta": {}}
        return status, parsed, ""


# ── 输出 ──────────────────────────────────────────────────────


def _emit_json(envelope: Dict[str, Any]) -> None:
    """把信封打成**确定性** JSON（不排序键：键序是契约的一部分）"""
    sys.stdout.write(json.dumps(envelope, ensure_ascii=False, indent=2,
                                default=str) + "\n")


def _emit_list_table(envelope: Dict[str, Any]) -> None:
    data = envelope.get("data") or {}
    items = data.get("items") or []
    mc = data.get("model_capability") or {}
    sys.stdout.write(
        f"能力 {data.get('returned', 0)}/{data.get('total', 0)} 条"
        f"（model={mc.get('model') or '(未指定)'}"
        f", supports_tool_calling={mc.get('supports_tool_calling')}）\n")
    sys.stdout.write(f"{'名称':<24}{'形态':<8}{'位置':<8}{'实现状态':<16}说明\n")
    sys.stdout.write("-" * 96 + "\n")
    for it in items:
        sys.stdout.write(
            f"{str(it.get('name', '')):<24}{str(it.get('kind', '')):<8}"
            f"{str(it.get('location', '')):<8}{str(it.get('impl_status', '')):<16}"
            f"{str(it.get('description') or '')[:40]}\n")
    reg = (envelope.get("meta") or {}).get("registry") or {}
    if reg.get("degraded"):
        sys.stdout.write("\n⚠ Registry 处于**降级**态（走清单快照）："
                         + "；".join(reg.get("build_warnings") or [])[:300] + "\n")


def _emit_invoke_text(envelope: Dict[str, Any]) -> None:
    sys.stdout.write(f"status={envelope.get('status')} code={envelope.get('code')}\n")
    if envelope.get("status") == "ok":
        sys.stdout.write(json.dumps(envelope.get("data"), ensure_ascii=False,
                                    indent=2, default=str) + "\n")
    else:
        err = envelope.get("error") or {}
        sys.stdout.write(f"error: {err.get('message')}\n")
        if err.get("detail"):
            sys.stdout.write(f"detail: {err.get('detail')}\n")
    meta = envelope.get("meta") or {}
    sys.stdout.write(f"meta: loader={meta.get('loader')} "
                     f"location={meta.get('location')} "
                     f"contract={meta.get('contract')} "
                     f"impl_status={meta.get('impl_status')}\n")


def _emit_describe_text(envelope: Dict[str, Any]) -> None:
    data = envelope.get("data")
    if not data:
        sys.stdout.write(f"未找到：{(envelope.get('error') or {}).get('message')}\n")
        return
    for key in ("name", "capability_id", "kind", "location", "owner", "version",
                "plane", "effect", "risk", "permission_level", "needs_approval",
                "llm_callable", "callable_mode", "callable_by", "impl_status",
                "reachable", "schema_registered", "host_executor", "declared_in"):
        sys.stdout.write(f"{key:<18}: {data.get(key)}\n")
    sys.stdout.write(f"{'description':<18}: {str(data.get('description') or '')[:200]}\n")


def _emit_search_text(envelope: Dict[str, Any]) -> None:
    items = (envelope.get("data") or {}).get("items") or []
    sys.stdout.write(f"召回 {len(items)} 条\n")
    for it in items:
        sys.stdout.write(f"  {it.get('rank')}. {it.get('skill_id')} "
                         f"(score={it.get('score')}) {it.get('name')}\n")
    meta = envelope.get("meta") or {}
    if not meta.get("entity_available"):
        sys.stdout.write("⚠ 技能实体不可读（不在版本控制内）⇒ 召回为空属既有约束\n")


# ── 子命令 ────────────────────────────────────────────────────


def _client_of(args: argparse.Namespace) -> _Client:
    endpoint = (args.endpoint
                or os.environ.get("CP_CAPABILITY_ENDPOINT")
                or DEFAULT_ENDPOINT)
    token = (args.token or os.environ.get("CP_API_TOKEN")
             or os.environ.get("API_TOKEN") or "")
    return _Client(endpoint, token, args.timeout)


def _cmd_list(args: argparse.Namespace, as_json: bool) -> int:
    q: Dict[str, Any] = {}
    for key in ("tenant_id", "namespace", "kind", "location", "owner",
                "identity", "impl_status", "q", "model"):
        val = getattr(args, key, None)
        if val:
            q[key] = val
    for key in ("healthy_only", "llm_visible_only"):
        if getattr(args, key, False):
            q[key] = "1"
    if args.limit:
        q["limit"] = args.limit
    if args.offset:
        q["offset"] = args.offset
    from urllib.parse import urlencode
    path = "/capabilities/tools" + (("?" + urlencode(q)) if q else "")
    status, envelope, err = _client_of(args).request("GET", path)
    _emit_json(envelope) if as_json else _emit_list_table(envelope)
    if status != 200:
        sys.stderr.write(f"HTTP {status} {err}\n")
        return _EXIT_ERROR
    return _EXIT_OK


def _cmd_describe(args: argparse.Namespace, as_json: bool) -> int:
    from urllib.parse import urlencode
    path = f"/capabilities/{args.name}"
    if args.tenant_id:
        path += "?" + urlencode({"tenant_id": args.tenant_id})
    status, envelope, err = _client_of(args).request("GET", path)
    _emit_json(envelope) if as_json else _emit_describe_text(envelope)
    if status != 200:
        sys.stderr.write(f"HTTP {status} {err}\n")
        return _EXIT_ERROR
    return _EXIT_OK


def _cmd_invoke(args: argparse.Namespace, as_json: bool) -> int:
    try:
        payload = _parse_args_blob(args.args, args.arg)
    except ValueError as exc:
        sys.stderr.write(f"参数错误：{exc}\n")
        return _EXIT_USAGE
    body: Dict[str, Any] = {"name": args.name, "args": payload,
                            "identity": args.identity}
    if args.tenant_id:
        body["tenant_id"] = args.tenant_id
    if args.version:
        body["version"] = args.version
    status, envelope, err = _client_of(args).request(
        "POST", "/capabilities/invoke", body=body)
    _emit_json(envelope) if as_json else _emit_invoke_text(envelope)
    if status != 200 or envelope.get("status") != "ok":
        if status != 200:
            sys.stderr.write(f"HTTP {status} {err}\n")
        return _EXIT_ERROR
    return _EXIT_OK


def _cmd_search(args: argparse.Namespace, as_json: bool) -> int:
    body = {"query": args.query, "top_k": args.top_k,
            "use_vector": args.use_vector, "use_bm25": args.use_bm25,
            "use_reranker": args.use_reranker}
    status, envelope, err = _client_of(args).request(
        "POST", "/capabilities/skills/search", body=body)
    _emit_json(envelope) if as_json else _emit_search_text(envelope)
    if status != 200:
        sys.stderr.write(f"HTTP {status} {err}\n")
        return _EXIT_ERROR
    return _EXIT_OK


def _cmd_health(args: argparse.Namespace, as_json: bool) -> int:
    status, envelope, err = _client_of(args).request("GET", "/capabilities/health")
    if as_json:
        _emit_json(envelope)
    else:
        sys.stdout.write(json.dumps(envelope.get("data"), ensure_ascii=False,
                                    indent=2, default=str) + "\n")
    if status != 200:
        sys.stderr.write(f"HTTP {status} {err}\n")
        return _EXIT_ERROR
    return _EXIT_OK


_COMMANDS = {
    "list": _cmd_list, "describe": _cmd_describe, "invoke": _cmd_invoke,
    "search": _cmd_search, "health": _cmd_health,
}


def main(argv: Optional[Sequence[str]] = None) -> int:
    _force_utf8_stdio()
    ap = build_parser()
    args = ap.parse_args(list(argv) if argv is not None else None)
    if not args.command:
        ap.print_help()
        return _EXIT_USAGE
    handler = _COMMANDS.get(args.command)
    if handler is None:  # pragma: no cover - argparse 已拦住
        ap.print_help()
        return _EXIT_USAGE
    try:
        return handler(args, bool(args.json))
    except KeyboardInterrupt:  # pragma: no cover
        return 130
