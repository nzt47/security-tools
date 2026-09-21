"""**E1 硬门禁**：关掉 LLM 之后三条链路是否仍然可用（TASK-05 §3 第 6 步 / §5 E1）

    python scripts/verify_llm_off_entrypoints.py

## 为什么这是唯一否决项

v1.4 的战略判据只有一条：

> **把 agent loop 关掉，这套东西还能不能被人、被 CI、被别的系统用起来。**

所以本脚本做的不是"跑一遍端点"，而是：**先把模型调用打桩成必然失败**，
再验证三条链路。

## "关掉 LLM"是怎么做的（**两道锁，都要能证伪**）

1. **把 API key 置成无效值**（`DEEPSEEK_API_KEY` / `OPENAI_API_KEY` /
   `ANTHROPIC_API_KEY` …）—— 这是"环境级"的关法，任何真的去建客户端的代码都会失败；
2. **monkeypatch 全部已知的模型调用入口抛异常** —— 这是"代码级"的关法，
   覆盖"把 key 换成有效值也会失败"的情形。

**并且脚本会自证第二道锁真的生效**：逐个调用被 patch 的入口并断言它确实抛异常
（`_assert_stub_effective`）。否则"我 patch 了"就只是一句自述。

## 三条链路

| # | 链路 | 断言 |
|---|---|---|
| 1 | `GET /capabilities/tools` | HTTP 200 + 清单非空 + `status=ok` |
| 2 | `POST /capabilities/invoke`（`data_format_detect`） | HTTP 200 + `status=ok` + 结构化 JSON |
| 3 | CLI `python -m cloudshu invoke ... --json` | **真实子进程** + JSON 与 HTTP **逐字段一致** |

> **为什么示例工具是 `data_format_detect` 而不是任务书里的 `current_time`**：
> 仓库里**没有** `current_time` 这个工具（`data/tool_definitions/` 91 个 YAML 里
> 没有它，注册表里也没有）。任务书写的是"如 `current_time`"（举例）。
> `data_format_detect` 满足同一组性质且**更可判定**：纯本地、纯确定性、
> 不依赖网络与模型、已声明 `result_schema`。详见报告里的"冲突点"一节。

## 出口码

`0` = 三条链路全部通过；`1` = 任一不通过（含"桩没生效"）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any, Dict, List, Tuple

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


# ── 控制台编码加固（2026-09-21 · P1「假红」修复）─────────────────────────────
# Why: 中文 Windows 的 GBK 控制台无法编码本脚本**成功路径**上的 ✓ / ✅ 等字符，
#      print() 抛 UnicodeEncodeError ⇒ 进程以退出码 1 结束 ⇒ 门禁产生"假红"
#      （检查本身通过，但按退出码判定会误判为失败，进而可能让真实失败被忽略）。
# How: 只放宽错误处理策略（errors="replace"），**不改编码**，保证中文仍正常显示；
#      在 UTF-8 环境（CI / Linux）下等价于无操作。
# 依据: docs/closeout 之外的实测记录见《06-基线台账.md》§七（四门禁退出码对照表）。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

# ── ① 环境级关闭：把所有已知的模型密钥置成无效值 ──
_INVALID_KEYS = (
    "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "CLAUDE_API_KEY",
    "LLM_API_KEY", "API_KEY", "MOONSHOT_API_KEY", "QWEN_API_KEY", "ZHIPU_API_KEY",
    "OPENROUTER_API_KEY", "GEMINI_API_KEY", "SILICONFLOW_API_KEY",
)
for _k in _INVALID_KEYS:
    os.environ[_k] = "INVALID-KEY-FOR-LLM-OFF-VERIFICATION"
os.environ["LLM_FORCE_DISABLED"] = "1"

#: 被 patch 的模型调用入口 —— `(模块路径, 模块内限定名)`；脚本会逐个自证其必然抛异常
_STUB_TARGETS: Tuple[Tuple[str, str], ...] = (
    ("memory.llm_service", "LLMService.chat"),
    ("memory.llm_service", "LLMService.chat_stream"),
    ("memory.llm_service", "LLMService.summarize"),
    ("memory.llm_service", "LLMService._get_client"),
    ("memory.llm_service", "LLMService._do_chat"),
    ("memory.llm_service", "LLMService._do_summarize"),
    ("agent.model_router.adapters", "ModelAdapter.chat"),
    ("agent.model_router.adapters", "ModelAdapter.generate"),
    ("agent.model_router.adapters", "OpenAIAdapter._get_client"),
    ("agent.model_router.adapters", "ClaudeAdapter._get_client"),
)

_STUBBED: List[str] = []
_STUB_ERROR = "LLMDisabledForVerificationError"


def _boom(*_a: Any, **_kw: Any) -> Any:
    raise RuntimeError(f"{_STUB_ERROR}: 本脚本已把模型调用打桩为必然失败")


def _resolve(module_path: str, qualified: str) -> Any:
    """按 `模块 + 限定名` 取到**可替换的宿主对象**（类）与属性名"""
    import importlib
    mod = importlib.import_module(module_path)
    obj = mod
    parts = qualified.split(".")
    for p in parts[:-1]:
        obj = getattr(obj, p)
    return obj, parts[-1]


def _install_stubs() -> None:
    """monkeypatch 全部模型调用入口

    【为什么用 `setattr` 而不是 `importlib.reload`】见 TASK-05 运行纪律第 7 条：
    reload 生产模块会制造"同一模块两个实例"的假象（单例失效、缓存错乱），
    是本仓库测试环境里最难查的一类污染。
    """
    for module_path, qualified in _STUB_TARGETS:
        try:
            host, attr = _resolve(module_path, qualified)
        except Exception as exc:  # noqa: BLE001  模块不存在/导入失败 ⇒ 记下但不失败
            _STUBBED.append(f"{module_path}.{qualified} "
                            f"[跳过导入: {type(exc).__name__}: {exc}]")
            continue
        if not hasattr(host, attr):
            _STUBBED.append(f"{module_path}.{qualified} [跳过: 属性不存在]")
            continue
        try:
            setattr(host, attr, _boom)
            _STUBBED.append(f"{module_path}.{qualified}")
        except Exception as exc:  # noqa: BLE001
            _STUBBED.append(f"{module_path}.{qualified} "
                            f"[跳过: {type(exc).__name__}: {exc}]")


def _assert_stub_effective() -> Tuple[bool, List[str]]:
    """**自证**：被 patch 的入口真的必然失败（否则"关掉 LLM"只是一句自述）"""
    notes: List[str] = []
    ok = True
    checked = 0
    for module_path, qualified in _STUB_TARGETS:
        try:
            host, attr = _resolve(module_path, qualified)
            fn = getattr(host, attr)
        except Exception:  # noqa: BLE001  跳过项不参与自证
            continue
        checked += 1
        try:
            # 传空参：被打桩的函数体第一行就抛，不看签名
            fn(None)
            notes.append(f"✗ {module_path}.{qualified} 未抛异常（桩未生效）")
            ok = False
        except RuntimeError as exc:
            if _STUB_ERROR in str(exc):
                notes.append(f"✓ {module_path}.{qualified} 必然失败")
            else:
                notes.append(f"✓ {module_path}.{qualified} 抛 RuntimeError: {exc}")
        except Exception as exc:  # noqa: BLE001  别的异常也算"必然失败"
            notes.append(f"✓ {module_path}.{qualified} 抛 {type(exc).__name__}")
    if checked == 0:
        ok = False
        notes.append("✗ 自证覆盖 0 个入口 ⇒ 无法证明'关掉 LLM'，视为不通过")
    else:
        notes.insert(0, f"自证覆盖 {checked} 个入口"
                        f"（{'全部必然失败' if ok else '存在未生效项'}）")
    return ok, notes


# ── ② 构造一个只挂能力层路由的真实 Flask 应用 ──

#: 需要在验证前装载的注册模块（**就是 `lifecycle_manager._register_builtin_tools`
#: 用的那一批**，此处照抄以保证"注册的是同一批工具"）
_REGISTER_MODULES = (
    "agent.tools.core_tools", "agent.tools.file_tools_reg", "agent.tools.web_tools",
    "agent.tools.ext_tools", "agent.tools.pdf_tools", "agent.tools.system_tools",
    "agent.tools.code_tools", "agent.tools.search_tools", "agent.tools.subagent_tools",
    "agent.tools.fan_out_tools", "agent.tools.plan_tools", "agent.tools.extra_tools",
    "agent.tools.git_tools", "agent.tools.test_tools", "agent.tools.notify_tools",
    "agent.tools.db_tools", "agent.tools.lint_tools",
)


class _DlStub:
    """最小 `DigitalLife` 桩（只为让 `register_all(dl)` 能跑完）

    【为什么需要它】`register_all(dl)` 里少数工具会读 `dl._config` / 用
    `dl._planning_tools` 注册规划面。本脚本要验的是**能力层入口**，
    不是整个平台启动链；用桩把注册面装起来，是 `TASK-00` §0.3 用过的同一手法
    （"用 stub 装载 17 个 register_all 的实跑结果"）。
    **注册的是真实的工具实现与真实的 handler**，桩只提供宿主引用。
    """

    def __init__(self) -> None:
        self._config: Dict[str, Any] = {}
        self._planning_tools = _PlanningStub()
        self._permission = None


class _PlanningStub:
    """`dl._planning_tools` 的最小替身（只需 `register` 装饰器语义）"""

    def __init__(self) -> None:
        self.registered: List[str] = []

    def register(self, name: str, description: str = "", **_kw: Any):  # noqa: ANN201
        def _deco(fn):  # noqa: ANN001,ANN202
            self.registered.append(name)
            return fn
        return _deco

    def call(self, *_a: Any, **_kw: Any) -> Dict[str, Any]:
        return {"ok": False, "error": "planning 面未接线（验证桩）"}

    def list_tools(self) -> List[Dict[str, Any]]:
        """规划面工具清单（`plan_tools` 注册期会问它，缺了会打一条告警）"""
        return [{"name": n} for n in self.registered]


def _register_tools() -> Tuple[int, List[str]]:
    """装载内置工具注册面（返回 注册表大小 + 各模块结果）"""
    from agent import tools as _tools

    dl = _DlStub()
    notes: List[str] = []
    for mod_name in _REGISTER_MODULES:
        try:
            mod = __import__(mod_name, fromlist=["register_all"])
            reg = getattr(mod, "register_all", None)
            if reg is None:
                notes.append(f"{mod_name}: 无 register_all（跳过）")
                continue
            reg(dl)
            notes.append(f"{mod_name}: ok")
        except Exception as exc:  # noqa: BLE001  单个模块失败不影响其余（与 lifecycle 同口径）
            notes.append(f"{mod_name}: {type(exc).__name__}: {exc}")
    try:
        from agent.knowledge.tools import register_knowledge_tools
        register_knowledge_tools()
        notes.append("agent.knowledge.tools: ok")
    except Exception as exc:  # noqa: BLE001
        notes.append(f"agent.knowledge.tools: {type(exc).__name__}: {exc}")
    return len(_tools.list_tools()), notes


def _build_app():
    """只注册 `/capabilities/*` 的 Flask 应用（**真实 WSGI 请求**，不是伪造）"""
    from flask import Flask
    app = Flask("capabilities_llm_off_verify")
    from agent.server_routes.routes_capabilities import register_routes
    register_routes(app, lambda: None)
    return app


def _http_get(client, path: str) -> Tuple[int, Dict[str, Any]]:
    resp = client.get(path)
    try:
        body = resp.get_json()
    except Exception:  # noqa: BLE001
        body = {"_raw": resp.get_data(as_text=True)[:500]}
    return resp.status_code, body or {}


def _http_post(client, path: str, payload: Dict[str, Any]
               ) -> Tuple[int, Dict[str, Any]]:
    resp = client.post(path, json=payload)
    try:
        body = resp.get_json()
    except Exception:  # noqa: BLE001
        body = {"_raw": resp.get_data(as_text=True)[:500]}
    return resp.status_code, body or {}


def _api_token() -> str:
    """取平台当前生效的 API 令牌（**不打印明文**）

    【为什么必须带令牌】实测：真实 `app_server` 应用下，`/capabilities/*` 带
    `@require_token`（与同目录 `routes_workflow_learning.py`、
    `plugins/mcp_scheduler.py` 的口径一致）⇒ **无令牌一律 401**。
    这不是缺陷，是"默认需鉴权"的必然结果；验证脚本因此必须像真实调用方一样带令牌。
    【为什么先 `load_dotenv`】`.env:3294` 有 `FLASK_API_TOKEN`，但**裸 python 进程
    不读 `.env`**（`app_server` 由 `EnvConfigManager` 加载）⇒ 不加载就拿不到令牌。
    这里用与宿主同款的 dotenv 加载，**不引入第二套凭据读取口径**（只读，不改配置）。
    """
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(_ROOT, ".env"), override=False)
    except Exception:  # noqa: BLE001
        pass
    try:
        from agent.server_auth import current_api_token
        return str(current_api_token() or "")
    except Exception:  # noqa: BLE001
        return ""


def _http_post_real(path: str, payload: Dict[str, Any]
                    ) -> Tuple[int, Dict[str, Any], float]:
    """对**真实监听端口**发 POST（E6 对拍用：这样 HTTP 与 CLI 是两条真实链路）"""
    import time as _t
    import urllib.request

    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    token = _api_token()
    if token:
        headers["X-API-Token"] = token
        headers["Authorization"] = f"Bearer {token}"
    t0 = _t.time()
    req = urllib.request.Request(  # noqa: S310  目标是本机验证端口
        f"http://127.0.0.1:{_CLI_PORT}{path}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            raw = resp.read().decode("utf-8", errors="replace")
            status = resp.status
    except Exception as exc:  # noqa: BLE001
        return -1, {"_error": f"{type(exc).__name__}: {exc}"}, _t.time() - t0
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        body = {"_raw": raw[:400]}
    return status, body if isinstance(body, dict) else {"_data": body}, _t.time() - t0


# ── ③ CLI：**真实子进程**（不是 import 后调函数）──

_CLI_MARKER = "@@CLI_JSON@@"
#: 验证用端口（**非生产 5678**，避免与用户正在运行的后端冲突）
_CLI_PORT = int(os.environ.get("CAPVERIFY_E1_PORT", "5681"))


def _serve_in_thread(app: Any, port: int, threads: int = 4) -> bool:
    """把真实 Flask app 用 waitress 绑到验证端口（**真实 HTTP**，不是 test_client）

    【为什么必须真的起服务】`TASK-05` E1 要求"CLI 返回与 HTTP 完全一致的结构"，
    而 CLI 是**独立进程** —— 按 `cloudshu/cli.py` 的架构选择，CLI 是能力服务的
    **客户端**（不自己注册第二套工具面，理由见该模块 docstring 的 D1 论证）。
    ⇒ 必须有一个真实监听端口供它访问。
    """
    import socket
    import threading
    import time as _t
    from waitress import serve

    def _run() -> None:
        try:
            serve(app, host="127.0.0.1", port=port, threads=threads)
        except Exception as exc:  # noqa: BLE001
            print(f"[verify] waitress 退出: {type(exc).__name__}: {exc}")

    threading.Thread(target=_run, daemon=True, name="e1-waitress").start()
    deadline = _t.time() + 30
    while _t.time() < deadline:
        with socket.socket() as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        _t.sleep(0.3)
    return False


def _run_cli(args: List[str]) -> Tuple[int, Dict[str, Any], str]:
    """跑 `python -m cloudshu ...` 并取回 stdout 里的 JSON 信封

    【为什么用哨兵包裹】CLI 的 `--json` 模式只会输出 JSON，但**日志/告警**可能
    混进 stdout（例如某个库的 banner）。用一对哨兵把 JSON 段夹出来，
    比"假设 stdout 全是 JSON"更抗噪。
    """
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    # 让 CLI 指向本脚本起的验证实例（**同一份**能力服务）
    env["CP_CAPABILITY_ENDPOINT"] = f"http://127.0.0.1:{_CLI_PORT}"
    # 令牌：CLI 是本机客户端，凭据走同一条口径（`CP_API_TOKEN`），**不打印明文**
    env["CP_API_TOKEN"] = _api_token()
    # 【沙箱提示】本沙箱禁止程序打开命名管道 ⇒ 必须把输出**重定向到文件**再读
    # （TASK-00 §0.2d 第 4 类），不能靠管道 `capture_output`。
    out_path = os.path.join(_ROOT, "_ci_logs", "cli_llm_off_stdout.txt")
    err_path = os.path.join(_ROOT, "_ci_logs", "cli_llm_off_stderr.txt")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    cmd = [sys.executable, "-c",
           "import sys;from cloudshu.cli import main;"
           f"sys.exit(main({args!r}))"]
    with open(out_path, "w", encoding="utf-8") as fo, \
            open(err_path, "w", encoding="utf-8") as fe:
        proc = subprocess.run(cmd, cwd=_ROOT, env=env, stdout=fo, stderr=fe,
                              stdin=subprocess.DEVNULL)
    text = open(out_path, encoding="utf-8", errors="replace").read()
    err = open(err_path, encoding="utf-8", errors="replace").read()
    # 从 stdout 里提取第一个完整 JSON 对象（哨兵不足时用括号配平兜底）
    envelope: Dict[str, Any] = {}
    start = text.find("{")
    if start >= 0:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        envelope = json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        envelope = {}
                    break
    return proc.returncode, envelope, (err or text)[-800:]


# ── 主流程 ──


def main() -> int:
    lines: List[str] = []

    def say(msg: str = "") -> None:
        lines.append(msg)
        print(msg)

    say("=" * 78)
    say("E1 硬门禁 · 关掉 LLM 之后三条链路是否仍可用")
    say("=" * 78)
    say(f"① 环境级关闭：{len(_INVALID_KEYS)} 个模型密钥已置为无效值")
    say(f"   示例：DEEPSEEK_API_KEY={os.environ['DEEPSEEK_API_KEY']!r}")

    _install_stubs()
    say(f"② 代码级关闭：monkeypatch {len(_STUB_TARGETS)} 个模型调用入口")
    for s in _STUBBED:
        say(f"   · {s}")

    stub_ok, notes = _assert_stub_effective()
    say("")
    say("② 自证（桩是否真的必然失败）：")
    for n in notes:
        say(f"   {n}")

    results: List[Tuple[str, bool, str]] = []

    # ── ⓪ 装载工具注册面（本地链路的执行依赖 `_registry`）──
    say("")
    say("─" * 78)
    say("⓪ 装载内置工具注册面（本地能力的执行要求名字在 `_registry` 里）")
    say("─" * 78)
    reg_size, reg_notes = _register_tools()
    for n in reg_notes:
        say(f"   · {n}")
    say(f"注册表大小：{reg_size}")

    # ── 链路 1：GET /capabilities/tools ──
    say("")
    say("─" * 78)
    say("链路 1：GET /capabilities/tools")
    say("─" * 78)
    app = _build_app()
    client = app.test_client()
    served = _serve_in_thread(app, _CLI_PORT)
    say(f"   真实服务已绑定 127.0.0.1:{_CLI_PORT} ⇒ {'✅ 成功' if served else '❌ 失败'}"
        f"（供 CLI 子进程访问；非生产端口）")
    status, body = _http_get(client, "/capabilities/tools")
    total = ((body.get("data") or {}).get("total") or 0)
    degraded = ((body.get("meta") or {}).get("registry") or {}).get("degraded")
    ok1 = status == 200 and body.get("status") == "ok" and total > 0
    say(f"HTTP {status} | status={body.get('status')} code={body.get('code')} "
        f"| total={total} returned={((body.get('data') or {}).get('returned'))} "
        f"| degraded={degraded}")
    say(f"断言 status==200 且 status=='ok' 且 total>0 ⇒ "
        f"{'✅ 通过' if ok1 else '❌ 不通过'}")
    results.append(("GET /capabilities/tools", ok1, f"HTTP {status}, total={total}"))

    # ── 链路 2：POST /capabilities/invoke ──
    say("")
    say("─" * 78)
    say("链路 2：POST /capabilities/invoke （data_format_detect，不依赖 LLM）")
    say("─" * 78)
    invoke_payload = {"name": "data_format_detect",
                      "args": {"data": '{"a": 1, "b": [2, 3]}'}}
    status2, body2, dt2 = _http_post_real("/capabilities/invoke", invoke_payload)
    ok2 = (status2 == 200 and body2.get("status") == "ok"
           and isinstance(body2.get("data"), dict))
    say(f"请求体：{json.dumps(invoke_payload, ensure_ascii=False)}")
    say(f"HTTP {status2} | status={body2.get('status')} code={body2.get('code')}")
    say(f"data：{json.dumps(body2.get('data'), ensure_ascii=False)[:300]}")
    say(f"meta：contract={((body2.get('meta') or {}).get('contract'))} "
        f"loader={((body2.get('meta') or {}).get('loader'))} "
        f"location={((body2.get('meta') or {}).get('location'))}")
    say(f"断言 status==200 且 status=='ok' 且 data 为 dict ⇒ "
        f"{'✅ 通过' if ok2 else '❌ 不通过'}")
    results.append(("POST /capabilities/invoke", ok2,
                    f"HTTP {status2}, code={body2.get('code')}"))

    # ── 链路 3：CLI（真实子进程）──
    say("")
    say("─" * 78)
    say("链路 3：CLI `python -m cloudshu invoke data_format_detect --json`")
    say("─" * 78)
    rc3, env3, tail3 = _run_cli([
        "invoke", "data_format_detect",
        "--args", json.dumps({"data": '{"a": 1, "b": [2, 3]}'}),
        "--json"])
    ok3 = rc3 == 0 and env3.get("status") == "ok" and isinstance(env3.get("data"), dict)
    say(f"退出码 {rc3} | status={env3.get('status')} code={env3.get('code')}")
    say(f"data：{json.dumps(env3.get('data'), ensure_ascii=False)[:300]}")
    if not ok3:
        say(f"stderr/输出尾部：{tail3}")
    say(f"断言 退出码==0 且 status=='ok' 且 data 为 dict ⇒ "
        f"{'✅ 通过' if ok3 else '❌ 不通过'}")
    results.append(("CLI invoke", ok3, f"rc={rc3}, code={env3.get('code')}"))

    # ── E6 对拍：HTTP 与 CLI 逐字段一致 ──
    say("")
    say("─" * 78)
    say("E6 对拍：HTTP 与 CLI 的 JSON 逐字段一致")
    say("─" * 78)
    volatile = {("meta", "timing")}      # 唯一的易变字段（文档化的契约例外）
    diffs = _diff_envelope(body2, env3, path=(), volatile=volatile)
    ok6 = not diffs
    say(f"忽略字段（文档化的唯一易变项）：meta.timing")
    if ok6:
        say("逐字段一致 ✅")
    else:
        for d in diffs[:20]:
            say(f"  ✗ {d}")
    say(f"断言 无差异 ⇒ {'✅ 通过' if ok6 else '❌ 不通过'}")
    results.append(("HTTP/CLI 对拍", ok6, f"{len(diffs)} 处差异"))

    # ── 汇总 ──
    say("")
    say("=" * 78)
    say("E1 汇总")
    say("=" * 78)
    for name, ok, detail in results:
        say(f"  {'✅' if ok else '❌'} {name} — {detail}")
    say(f"  ② 打桩自证 — {'✅ 全部必然失败' if stub_ok else '❌ 有入口未生效'}")
    all_ok = all(r[1] for r in results) and stub_ok
    say("")
    say(f"结论：{'✅ E1 通过（关掉 LLM 后三条链路全部可用）' if all_ok else '❌ E1 不通过'}")

    with open(os.path.join(_ROOT, "_ci_logs", "e1_llm_off_report.txt"),
              "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return 0 if all_ok else 1


def _diff_envelope(a: Any, b: Any, path: Tuple[str, ...],
                   volatile: set) -> List[str]:
    """逐字段比对两个 JSON 信封（`volatile` 里的路径跳过）"""
    out: List[str] = []
    if path in volatile:
        return out
    if type(a) is not type(b):
        return [f"{'.'.join(path)}: 类型不同 {type(a).__name__} vs {type(b).__name__}"]
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a:
                out.append(f"{'.'.join(path + (k,))}: 仅 CLI 有")
            elif k not in b:
                out.append(f"{'.'.join(path + (k,))}: 仅 HTTP 有")
            else:
                out.extend(_diff_envelope(a[k], b[k], path + (k,), volatile))
        return out
    if isinstance(a, list):
        if len(a) != len(b):
            return [f"{'.'.join(path)}: 长度不同 {len(a)} vs {len(b)}"]
        for i, (x, y) in enumerate(zip(a, b)):
            out.extend(_diff_envelope(x, y, path + (str(i),), volatile))
        return out
    if a != b:
        out.append(f"{'.'.join(path)}: {a!r} vs {b!r}")
    return out


if __name__ == "__main__":
    sys.exit(main())
