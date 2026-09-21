# -*- coding: utf-8 -*-
"""R2 探针 · 优雅关闭「收到信号即显式落盘会话最后一条 LLM 通信」

对应事实（W5/TASK-08 · R2）：
  * `atexit` 对 SIGTERM/SIGKILL **不触发** ⇒ 此前"关闭时必存最后一条"实际由
    agent/llm_monitor.py:218-223 的「每条即写 + 5s 节流」(PERSIST_MIN_INTERVAL_S=5.0)
    保证，**最坏暴露窗口 ≤5s**。
  * app_server.py 的优雅关闭路径此前**没有**任何信号处理器
    ⇒ 本探针验证新钩子 `_install_graceful_shutdown_hooks` / `_graceful_shutdown_persist`。

判据（受控探针，非"看起来对"）：
  ① hook 组：起真服务（waitress，**非 5678** 的空闲端口）→ 造出"节流窗口内的最后一条"
     （基线条已落盘、最后一条被 5s 节流跳过）→ 发 SIGTERM →
     断言 data/llm_monitor_last.json 的内容 == 那条"被节流跳过"的记录。
  ② 反事实（nohook 组）：同一序列**不装钩子** → SIGTERM 后文件仍是基线条，
     证明"信号路径确实会丢最后一条"、且钩子正是缺口所在（不是巧合）。

用法：
  python scripts/dev/graceful_shutdown_persist_probe.py            # 编排两组并给结论
  python scripts/dev/graceful_shutdown_persist_probe.py --child --mode hook
  python scripts/dev/graceful_shutdown_persist_probe.py --win-term-demo   # Windows 平台事实

⚠ 探针会写仓库 data/llm_monitor_last.json（LLM 监控自身的运行时快照，非受保护数据）；
   编排模式**先快照后还原**，跑完不留痕（D6）。
⚠ 不占用 5678；子进程只在空闲端口起服务，退出即释放。
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PERSIST_PATH = os.path.join(_REPO_ROOT, "data", "llm_monitor_last.json")


# ────────────────────────────── 子进程（被探针编排） ──────────────────────────────

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _child(mode: str) -> int:
    """真服务 + 真记录 + 真信号；退出码交由父进程判定。"""
    sys.path.insert(0, _REPO_ROOT)
    os.chdir(_REPO_ROOT)

    import agent.llm_monitor as lm

    marker_a = f"PROBE-BASELINE-{os.getpid()}-{int(time.time())}"
    marker_b = f"PROBE-LAST-{os.getpid()}-{int(time.time())}"

    monitor = lm.get_monitor()
    monitor.clear()  # 干净起点（同时删掉旧快照）

    # ① 基线条：此时 _last_persist_ts=0 ⇒ record() 内部会立即落盘
    monitor.record(lm.LLMInteraction(
        source="probe", model="deepseek-chat", provider="deepseek",
        system_prompt="probe", messages=[{"role": "user", "content": "baseline"}],
        response_text=marker_a, request_tokens=1, response_tokens=1))

    # ② 人为"武装"节流窗口：模拟"上一条刚写盘、最后一条又来"的真实最坏时序
    monitor._last_persist_ts = time.time()
    monitor.record(lm.LLMInteraction(
        source="probe", model="deepseek-chat", provider="deepseek",
        system_prompt="probe", messages=[{"role": "user", "content": "last"}],
        response_text=marker_b, request_tokens=1, response_tokens=1))
    # 此刻：缓冲区最后一条 = marker_b，而磁盘上是 marker_a（≤5s 暴露窗口）

    # ③ 起真服务：waitress 托管真实 app_server.app，绑**空闲端口**
    import threading
    from waitress import serve

    import app_server  # noqa: F401  真实应用对象（含新钩子）
    port = _free_port()
    threading.Thread(
        target=lambda: serve(app_server.app, host="127.0.0.1", port=port, threads=4),
        daemon=True).start()

    installed = []
    if mode == "hook":
        installed = app_server._install_graceful_shutdown_hooks()

    # 等服务就绪（真 HTTP 200 才算"起来了"）
    import urllib.request
    ready_at = time.time() + 60
    ok_http = False
    while time.time() < ready_at:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as r:
                ok_http = r.status == 200
                if ok_http:
                    break
        except Exception:
            time.sleep(0.3)
    print(json.dumps({"event": "ready", "mode": mode, "port": port, "http_ok": ok_http,
                      "signals": installed, "marker_a": marker_a, "marker_b": marker_b,
                      "disk_before_signal": _disk_text()}), flush=True)

    # ④ 发 SIGTERM（本进程内 raise(SIGTERM)，走 signal.signal 注册的处理器）
    time.sleep(1.0)
    signal_raise_sigterm()
    time.sleep(10.0)  # 处理器未生效时在这里耗尽（父进程据此判定失败）
    print(json.dumps({"event": "signal_not_handled", "mode": mode}), flush=True)
    return 97


def signal_raise_sigterm() -> None:
    import signal
    signal.raise_signal(signal.SIGTERM)


def _disk_text() -> str:
    try:
        with open(PERSIST_PATH, "r", encoding="utf-8") as f:
            return str(json.load(f).get("response_text", ""))
    except Exception:
        return "<missing>"


# ────────────────────────────── 父进程（编排 + 判定） ──────────────────────────────

def _run_child(mode: str) -> dict:
    proc = subprocess.Popen(
        [sys.executable, "-u", os.path.abspath(__file__), "--child", "--mode", mode],
        cwd=_REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace")
    try:
        out, _ = proc.communicate(timeout=300)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate()
        return {"mode": mode, "timeout": True, "raw": out, "rc": None}
    ready = None
    for line in (out or "").splitlines():
        line = line.strip()
        if line.startswith("{") and '"event": "ready"' in line:
            try:
                ready = json.loads(line)
            except Exception:
                pass
    return {"mode": mode, "ready": ready, "rc": proc.returncode, "raw": out}


def _snapshot() -> str | None:
    try:
        with open(PERSIST_PATH, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return None


def _restore(snap: str | None) -> None:
    try:
        if snap is None:
            if os.path.exists(PERSIST_PATH):
                os.remove(PERSIST_PATH)
        else:
            with open(PERSIST_PATH, "w", encoding="utf-8") as f:
                f.write(snap)
    except Exception as e:  # noqa: BLE001
        print(f"[probe] 还原快照失败: {e}")


def orchestrate() -> int:
    snap = _snapshot()
    print("=" * 74)
    print("R2 探针：优雅关闭「收到信号即显式落盘会话最后一条 LLM 通信」")
    print("=" * 74)
    verdicts = {}
    try:
        for mode in ("hook", "nohook"):
            r = _run_child(mode)
            r["disk_after"] = _disk_text()  # 必须在下一组启动前读
            verdicts[mode] = r
            ready = r.get("ready") or {}
            a, b, after = ready.get("marker_a"), ready.get("marker_b"), r["disk_after"]
            ok = (after == b) if mode == "hook" else (after == a)
            print(f"\n── 组 {mode} ──")
            print(f"  真服务    : port={ready.get('port')} http_ok={ready.get('http_ok')} "
                  f"已注册信号={ready.get('signals')}")
            print(f"  发信号前   : 磁盘={ready.get('disk_before_signal')!r}（= 基线条，最后一条被 5s 节流跳过）")
            print(f"  基线条 A   : {a!r}")
            print(f"  最后一条 B : {b!r}")
            print(f"  子进程退出 : rc={r.get('rc')} timeout={r.get('timeout', False)}")
            print(f"  信号后磁盘 : {after!r}")
            print(f"  该组判据   : {'PASS' if ok else 'FAIL'}  "
                  f"({'信号路径落盘了最后一条' if mode == 'hook' else '信号路径丢失最后一条（反事实成立）'})")

        print("\n" + "=" * 74)
        print("总判定")
        print("=" * 74)
        h, n = verdicts["hook"], verdicts["nohook"]
        hb = (h.get("ready") or {}).get("marker_b")
        ha = (n.get("ready") or {}).get("marker_a")
        hook_pass = h.get("disk_after") == hb and h.get("rc") not in (None, 97)
        counter_pass = n.get("disk_after") == ha
        print(f"  ① hook 组：SIGTERM 后最后一条已落盘        : {'PASS' if hook_pass else 'FAIL'}")
        print(f"  ② 反事实：不装钩子则信号后丢失最后一条     : {'PASS' if counter_pass else 'FAIL'}")
        print(f"  ③ 结论：{'R2 缺口已被闭合' if (hook_pass and counter_pass) else '未闭合，需复查'}")
        return 0 if (hook_pass and counter_pass) else 1
    finally:
        _restore(snap)
        print("\n[probe] 已还原 data/llm_monitor_last.json（不留痕）")


# ────────────────────────────── Windows 平台事实演示 ──────────────────────────────

def win_term_demo() -> int:
    """演示 Windows 上 os.kill(pid, SIGTERM) 走 TerminateProcess ⇒ 处理器不执行。

    Why:这是"为什么不能只注册 SIGTERM 就以为万事大吉"的平台事实证据。
    """
    if os.name != "nt":
        print("[skip] 非 Windows 平台，SIGTERM 可正常投递")
        return 0
    code = (
        "import signal,sys,time\n"
        "signal.signal(signal.SIGTERM, lambda s,f:(print('HANDLER_RAN',flush=True),sys.exit(0)))\n"
        "print('CHILD_READY',flush=True)\n"
        "time.sleep(20)\n"
    )
    p = subprocess.Popen([sys.executable, "-u", "-c", code], stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, encoding="utf-8")
    p.stdout.readline()  # CHILD_READY
    p.send_signal(__import__("signal").SIGTERM)
    try:
        out, _ = p.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        p.kill()
        out = "<timeout>"
    ran = "HANDLER_RAN" in (out or "")
    print(f"os.kill/terminate(SIGTERM) 后处理器是否执行: {ran}（退出码 {p.returncode}）")
    print("结论: Windows 上跨进程 SIGTERM = TerminateProcess，**不执行** Python 信号处理器；"
          "可投递的是 CTRL_C_EVENT/CTRL_BREAK_EVENT（SIGINT/SIGBREAK），故三个信号都注册。")
    return 0 if not ran else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--child", action="store_true")
    ap.add_argument("--mode", default="hook")
    ap.add_argument("--win-term-demo", action="store_true")
    a = ap.parse_args()
    if a.win_term_demo:
        return win_term_demo()
    if a.child:
        return _child(a.mode)
    return orchestrate()


if __name__ == "__main__":
    sys.exit(main())
