"""**E5 硬门禁**：Loader 初始化失败时启动仍成功 + health 200（TASK-05 §3 第 2 步第 6 项 / §5 E5）

    python scripts/verify_loader_degradation_startup.py                 # 默认：真实 app_server 应用
    python scripts/verify_loader_degradation_startup.py --mode routes_only

## 为什么不能直接跑 `python app_server.py`

`app_server.py:1545-1565`（在 `if __name__ == "__main__":` 内）有一段
**"启动前先清理 5678 端口的旧进程"**：它会 `netstat -ano` 找出监听 5678 的 PID 并
`taskkill /F`。也就是说，跑 `python app_server.py` 会**杀掉正在运行的后端**——
而本会话的运行纪律明确要求**不要终止该服务**（它是用户的真实运行实例，
并按 `.env:1215` 的 `APPROVAL_RECORDS_PATH` 正常写 `agent/data/approval_records.jsonl`）。

⇒ 本脚本因此走**等价但不自杀**的路径：

1. `import app_server` —— 全部路由（含 `/capabilities/*`）都在**模块级**注册
   （见 `app_server.py` 的 `try: ... reg_*(app, ...)` 块，`if __name__` 在 1491 行之后），
   故 import 就得到一个**完整装配好的真实 Flask app**，且**不执行**那段杀进程逻辑；
2. 用 **waitress**（与生产同款，16→本脚本用 8 线程）把它绑到
   **127.0.0.1:5679**（非生产端口），然后发**真实 HTTP 请求**；
3. 故意让一个 Loader 初始化失败：`CP_CAPABILITY_STDIO_SERVER` 指向一个
   **不存在的脚本** ⇒ `StdioLoader._do_connect` 抛 `LoaderInitError`。

## 断言（E5）

| # | 断言 | 依据 |
|---|---|---|
| 1 | 启动过程**没有**因 Loader 失败而中断 | `import app_server` 成功 + waitress 成功监听 |
| 2 | `/capabilities/health` 返回 200 | 真实 HTTP |
| 3 | `/capabilities/health` 里能看到**该 Loader 的失败记录** | `data.loaders.disabled_or_failed` |
| 4 | `/capabilities/tools?location=local` 仍返回 200 + 非空 | 失败的 Loader 不牵连本地链路 |
| 5 | 某个**平台既有** health 端点仍返回 200 | 证明平台整体没被拖坏 |

> **口径如实披露**：本脚本验证的是"**app_server 的应用装配** + Loader 失败"，
> 不是"`python app_server.py` 这个命令行本身"。差别只有那段杀端口逻辑与
> `webbrowser.open`；**启动链路的其余部分（含路由注册）完全一致**。
> 之所以不去跑命令行，是因为它会杀掉用户正在运行的后端 —— 这个取舍写在这里，
> 不藏着。
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

PORT = int(os.environ.get("CAPVERIFY_PORT", "5679"))
#: 指向一个**不存在**的脚本 ⇒ StdioLoader 必然连接失败（模拟"一个 Loader 初始化失败"）
BROKEN_STDIO = os.path.join("__nonexistent_for_verification__", "no_such_server.py")


def _api_token() -> str:
    """取平台当前生效的 API 令牌（**不打印明文**）

    【为什么必须带令牌】实测：真实 `app_server` 应用下，`/capabilities/*` 带
    `@require_token`（与同目录 `routes_workflow_learning.py`、
    `plugins/mcp_scheduler.py` 的口径一致）⇒ **无令牌一律 401**。
    这不是缺陷，是"默认需鉴权"的必然结果；验证脚本因此必须像真实调用方一样带上令牌。
    令牌来源与 `agent/server_auth.py::current_api_token()` 同源（环境/配置），
    故**不会**引入第二套凭据读取口径。
    """
    try:
        # 【为什么先 load_dotenv】实测：`.env:3294` 有 `FLASK_API_TOKEN`，但**裸
        #   python 进程不读 `.env`**（`app_server` 由 `EnvConfigManager` 加载）⇒
        #   不加载就拿不到令牌、每个 `/capabilities/*` 都 401。此处用与宿主同款的
        #   dotenv 加载，**不引入第二套凭据读取口径**。只读、不改任何配置。
        from dotenv import load_dotenv
        load_dotenv(os.path.join(_ROOT, ".env"), override=False)
    except Exception:  # noqa: BLE001  dotenv 不可用 ⇒ 就当未配置令牌
        pass
    try:
        from agent.server_auth import current_api_token
        return str(current_api_token() or "")
    except Exception:  # noqa: BLE001  取不到就当未配置（与 authorize_token 同口径）
        return ""


def _http_json(path: str, *, timeout: int = 20) -> Tuple[int, Dict[str, Any], float]:
    """真实 HTTP GET（用 requests，与生产同族；带平台令牌）"""
    import requests
    token = _api_token()
    headers = {"Accept": "application/json"}
    if token:
        headers["X-API-Token"] = token
        headers["Authorization"] = f"Bearer {token}"
    t0 = time.time()
    try:
        resp = requests.get(f"http://127.0.0.1:{PORT}{path}", timeout=timeout,
                            headers=headers)
    except Exception as exc:  # noqa: BLE001
        return -1, {"_error": f"{type(exc).__name__}: {exc}"}, time.time() - t0
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001
        body = {"_raw": resp.text[:400]}
    return resp.status_code, body if isinstance(body, dict) else {"_data": body}, \
        time.time() - t0


def _wait_port(port: int, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.3)
    return False


def _snapshot_dir(path: str) -> Dict[str, Tuple[int, int]]:
    """`(size, mtime_ns)` 窗口快照（D13 规定的归属判定方法，不用"是否存在"）"""
    out: Dict[str, Tuple[int, int]] = {}
    for dirpath, _dirnames, filenames in os.walk(path):
        for f in filenames:
            full = os.path.join(dirpath, f)
            try:
                st = os.stat(full)
                out[full] = (st.st_size, st.st_mtime_ns)
            except OSError:
                continue
    return out


def _serve_in_thread(app: Any, port: int, threads: int = 8) -> threading.Thread:
    from waitress import serve

    def _run() -> None:
        try:
            serve(app, host="127.0.0.1", port=port, threads=threads)
        except Exception as exc:  # noqa: BLE001  服务线程异常只记录
            print(f"[verify] waitress 退出: {type(exc).__name__}: {exc}")

    th = threading.Thread(target=_run, daemon=True, name="capverify-waitress")
    th.start()
    return th


def _build_routes_only_app() -> Any:
    """只挂能力层路由的最小 Flask app（`--mode routes_only` 用）"""
    from flask import Flask
    app = Flask("capverify_routes_only")
    from agent.server_routes.routes_capabilities import register_routes
    register_routes(app, lambda: None)
    # 一个"平台既有 health 端点"的替身（最小模式没有真实端点，如实标注）
    from flask import jsonify

    @app.route("/api/diagnostics/health", methods=["GET"])
    def _health():  # noqa: ANN202
        return jsonify({"ok": True, "source": "routes_only_stub"})
    return app


def main() -> int:
    ap = argparse.ArgumentParser(description="E5：Loader 失败不阻塞启动")
    ap.add_argument("--mode", choices=["app_server", "routes_only"],
                    default="app_server")
    args = ap.parse_args()

    lines: List[str] = []

    def say(msg: str = "") -> None:
        lines.append(msg)
        print(msg, flush=True)

    say("=" * 78)
    say("E5 硬门禁 · Loader 初始化失败时启动仍成功 + health 200")
    say("=" * 78)
    say(f"破坏方式：CP_CAPABILITY_STDIO_SERVER = {BROKEN_STDIO!r}（不存在的脚本）")
    os.environ["CP_CAPABILITY_STDIO_SERVER"] = BROKEN_STDIO
    os.environ["PYTHONUTF8"] = "1"
    say(f"mode = {args.mode}；端口 = {PORT}（非生产端口，避免与 5678 冲突）")

    data_snap_before = _snapshot_dir(os.path.join(_ROOT, "data"))

    # ── 启动（含真实应用装配）──
    say("")
    say("① 启动（含应用装配）")
    t0 = time.time()
    if args.mode == "app_server":
        try:
            import app_server  # noqa: PLC0415  模块级完成全部路由注册
        except Exception as exc:  # noqa: BLE001
            say(f"❌ import app_server 失败（这本身就是 E5 不通过）: "
                f"{type(exc).__name__}: {exc}")
            with open(os.path.join(_ROOT, "_ci_logs", "e5_startup_report.txt"),
                      "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            return 1
        app = app_server.app
    else:
        app = _build_routes_only_app()
    boot_seconds = time.time() - t0
    rule_count = len(list(app.url_map.iter_rules()))
    cap_rules = sorted(str(r) for r in app.url_map.iter_rules()
                       if str(r).startswith("/capabilities"))
    say(f"   装配耗时 {boot_seconds:.2f}s；路由总数 {rule_count}")
    say(f"   /capabilities/* 路由（{len(cap_rules)} 条）：{cap_rules}")
    if not cap_rules:
        say("❌ /capabilities/* 一条都没注册 ⇒ E5 不通过")
        return 1

    # ── 起服务 ──
    say("")
    say("② 起 waitress（真实 WSGI 服务）")
    _serve_in_thread(app, PORT)
    up = _wait_port(PORT, timeout=40)
    say(f"   监听 127.0.0.1:{PORT} ⇒ {'✅ 成功' if up else '❌ 失败'}")
    if not up:
        return 1

    results: List[Tuple[str, bool, str]] = []

    # ── ③ /capabilities/health ──
    say("")
    say("─" * 78)
    say("③ GET /capabilities/health（真实 HTTP）")
    say("─" * 78)
    st, body, dt = _http_json("/capabilities/health")
    loaders = (body.get("data") or {}).get("loaders") or {}
    failures = loaders.get("disabled_or_failed") or []
    stdio_state = ((loaders.get("loaders") or {}).get("stdio") or {})
    say(f"HTTP {st}（{dt*1000:.0f}ms）| status={body.get('status')}")
    say(f"loader 状态：{json.dumps({k: v.get('state') for k, v in (loaders.get('loaders') or {}).items()}, ensure_ascii=False)}")
    say(f"disabled_or_failed：{json.dumps(failures, ensure_ascii=False)[:400]}")
    say(f"stdio 详情：{json.dumps(stdio_state, ensure_ascii=False)[:400]}")
    ok_health = st == 200 and body.get("status") == "ok"
    say(f"断言 HTTP==200 且 status=='ok' ⇒ {'✅ 通过' if ok_health else '❌ 不通过'}")
    results.append(("启动后 /capabilities/health 200", ok_health, f"HTTP {st}"))

    saw_failure = bool(failures) or stdio_state.get("state") in ("backoff", "unhealthy")
    say(f"断言 能看到该 Loader 的失败记录 ⇒ "
        f"{'✅ 通过' if saw_failure else '❌ 不通过'}")
    results.append(("Loader 失败可见", saw_failure,
                    f"failures={len(failures)}, stdio_state={stdio_state.get('state')}"))

    # ── ④ 本地链路不受牵连 ──
    say("")
    say("─" * 78)
    say("④ GET /capabilities/tools?location=local（失败的 Loader 不得牵连本地链路）")
    say("─" * 78)
    st4, body4, dt4 = _http_json("/capabilities/tools?location=local")
    total4 = ((body4.get("data") or {}).get("total") or 0)
    ok4 = st4 == 200 and body4.get("status") == "ok" and total4 > 0
    say(f"HTTP {st4}（{dt4*1000:.0f}ms）| total={total4}")
    say(f"断言 ⇒ {'✅ 通过' if ok4 else '❌ 不通过'}")
    results.append(("local 链路可用", ok4, f"HTTP {st4}, total={total4}"))

    # ── ⑤ 平台既有 health 端点 ──
    say("")
    say("─" * 78)
    say("⑤ 平台既有 health 端点（证明平台整体没被拖坏）")
    say("─" * 78)
    ok5 = False
    chosen = ""
    for path in ("/api/health/score", "/api/diagnostics/health",
                 "/api/health/dashboard", "/health"):
        st5, body5, _dt5 = _http_json(path)
        say(f"   {path} ⇒ HTTP {st5}")
        if st5 == 200:
            ok5 = True
            chosen = path
            break
    say(f"断言 至少一个既有 health 端点 200 ⇒ {'✅ 通过' if ok5 else '❌ 不通过'}")
    results.append(("既有 health 端点 200", ok5, chosen or "无命中"))

    # ── 汇总 ──
    say("")
    say("=" * 78)
    say("E5 汇总")
    say("=" * 78)
    for name, ok, detail in results:
        say(f"  {'✅' if ok else '❌'} {name} — {detail}")
    all_ok = all(r[1] for r in results)
    say("")
    say(f"结论：{'✅ E5 通过（Loader 初始化失败不阻塞启动，health 200）' if all_ok else '❌ E5 不通过'}")
    say(f"（口径：mode={args.mode}；启动装配耗时 {boot_seconds:.2f}s，"
        f"未跑 `python app_server.py` 命令行——它会 taskkill 5678 从而杀掉运行中的后端）")

    # ── 数据目录窗口比对（D13：用 (size, mtime_ns) 判定归属）──
    data_snap_after = _snapshot_dir(os.path.join(_ROOT, "data"))
    added = sorted(set(data_snap_after) - set(data_snap_before))
    changed = sorted(k for k in set(data_snap_after) & set(data_snap_before)
                     if data_snap_after[k] != data_snap_before[k])
    say("")
    say(f"data/ 目录窗口比对：新增 {len(added)} 个文件，变更 {len(changed)} 个文件")
    for p in (added + changed)[:15]:
        say(f"   · {os.path.relpath(p, _ROOT)}")

    os.makedirs(os.path.join(_ROOT, "_ci_logs"), exist_ok=True)
    with open(os.path.join(_ROOT, "_ci_logs", "e5_startup_report.txt"),
              "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
