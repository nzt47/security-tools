"""模拟技能安装过程中网络突然中断，验证超时重试机制是否按预期工作

对应实现: agent/skills_mgmt/creator.py `SkillInstaller._fetch_json`
重试策略: 统一 RetryPolicy（指数退避），env 可配
    SKILL_INSTALL_MAX_RETRIES   最大重试次数（默认 3）
    SKILL_INSTALL_RETRY_BACKOFF 初始退避秒数（默认 0.5）

模拟场景（本地可控 HTTP 服务器，下载中途断流）:
    A. 瞬时中断:  第 1 次请求下载到一半连接断开，重试后第 2 次成功
       → 期望 install 成功，且服务器收到 2 次请求（证明重试生效）
    B. 持续中断:  每次请求都在下载中途断开，重试耗尽
       → 期望抛 SkillInstallError（INSTALL_SOURCE_UNREACHABLE），
         服务器收到 1+max_retries 次请求（初始 + 全部重试）

运行:
    python scripts/simulate_skill_install_network_flaky.py
    python scripts/simulate_skill_install_network_flaky.py --max-retries 3 --backoff 0.1
"""
from __future__ import annotations

import argparse
import http.server
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Dict, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.skills_mgmt import SkillInstallError, SkillsMgmtService  # noqa: E402

# 合法技能载荷（下载成功时返回）
_PAYLOAD = {
    "id": "flaky-skill",
    "name": "flaky-skill",
    "description": "网络抖动模拟测试技能",
    "content": "def run(x):\n    return x.strip()\n",
    "content_type": "python",
    "category": "custom",
    "tags": ["flaky", "test"],
    "author": "sim",
    "version": "0.1.0",
}


def _make_handler(fail_first_n: int):
    """构造一个可配置的 handler：前 fail_first_n 次请求下载中途断流"""
    class _FlakyHandler(http.server.BaseHTTPRequestHandler):
        counter = 0

        def log_message(self, *args):  # 静默访问日志
            pass

        def _send_partial_then_abort(self):
            """模拟网络中断：声明完整长度但只发送前 10 字节后强制断开连接"""
            body = json.dumps(_PAYLOAD).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body) + 100))  # 谎报长度
            self.end_headers()
            self.wfile.write(body[:10])  # 只发一小部分
            self.wfile.flush()
            self.connection.close()  # 突然断开 → 客户端读到 EOF → IncompleteRead

        def _send_full(self):
            body = json.dumps(_PAYLOAD).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()

        def do_GET(self):
            type(self).counter += 1
            if type(self).counter <= fail_first_n:
                self._send_partial_then_abort()
            else:
                self._send_full()

    return _FlakyHandler


def run_scenario(name: str, *, fail_first_n: int, max_retries: int,
                 backoff: float) -> Dict:
    """运行单个场景，返回统计结果"""
    os.environ["SKILL_INSTALL_MAX_RETRIES"] = str(max_retries)
    os.environ["SKILL_INSTALL_RETRY_BACKOFF"] = str(backoff)

    handler_cls = _make_handler(fail_first_n)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    host, port = server.server_address[:2]
    url = f"http://{host}:{port}/skill.json"

    store_path = tempfile.mkdtemp(prefix="flaky_sim_")
    svc = SkillsMgmtService(store_path=str(Path(store_path) / "store.json"),
                            http_timeout=5)

    t0 = time.perf_counter()
    error: Optional[str] = None
    result = None
    try:
        result = svc.install(f"url:{url}")
    except SkillInstallError as e:
        error = e.code
    except Exception as e:  # noqa: BLE001 记录非预期异常
        error = f"{type(e).__name__}: {e}"
    elapsed = (time.perf_counter() - t0) * 1000

    server.shutdown()
    server.server_close()
    thread.join(timeout=2.0)

    return {
        "scenario": name,
        "requests": handler_cls.counter,
        "expected_requests": 1 + max_retries,
        "success": result is not None,
        "skill_id": result.id if result else "",
        "error": error or "",
        "elapsed_ms": round(elapsed, 1),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="技能安装网络中断重试验证")
    ap.add_argument("--max-retries", type=int, default=2, help="最大重试次数")
    ap.add_argument("--backoff", type=float, default=0.1, help="初始退避秒数")
    args = ap.parse_args()

    print("=" * 68)
    print(" 技能安装 · 网络中断重试机制验证")
    print("=" * 68)
    print(f" 配置: max_retries={args.max_retries} backoff={args.backoff}s\n")

    results = [
        run_scenario("A 瞬时中断(重试后成功)", fail_first_n=1,
                     max_retries=args.max_retries, backoff=args.backoff),
        run_scenario("B 持续中断(重试耗尽)", fail_first_n=10 ** 6,
                     max_retries=args.max_retries, backoff=args.backoff),
    ]

    print("| 场景 | 服务器请求数 | 期望请求数 | 结果 | 错误码/技能ID | 耗时ms |")
    print("|------|-----------:|----------:|------|--------------|-------:|")
    for r in results:
        outcome = "成功✓" if r["success"] else "失败✗"
        detail = r["skill_id"] or r["error"]
        flag = "✓" if r["requests"] == r["expected_requests"] or r["success"] else ""
        print(f"| {r['scenario']} | {r['requests']} | "
              f"{r['expected_requests']} | {outcome}{flag} | {detail} | "
              f"{r['elapsed_ms']} |")
    print()

    # 结论判定
    a, b = results
    ok = True
    if not a["success"]:
        print("[结论] 场景 A 失败：瞬时中断后重试未能恢复")
        ok = False
    elif a["requests"] < 2:
        print("[结论] 场景 A 异常：请求数未增长，重试机制未触发")
        ok = False
    else:
        print(f"[结论] 场景 A 通过：瞬时中断后自动重试 {a['requests'] - 1} 次并成功")

    if not b["error"]:
        print("[结论] 场景 B 失败：持续中断未抛 SkillInstallError")
        ok = False
    elif "UNREACHABLE" not in b["error"]:
        print(f"[结论] 场景 B 失败：错误码非 UNREACHABLE（实际 {b['error']}）")
        ok = False
    elif b["requests"] != b["expected_requests"]:
        print(f"[结论] 场景 B 失败：请求数 {b['requests']} != 期望 "
              f"{b['expected_requests']}（重试次数不符）")
        ok = False
    else:
        print(f"[结论] 场景 B 通过：重试耗尽（{b['requests'] - 1} 次）后正确抛 "
              f"SkillInstallError 并停止重试")

    print()
    print("全部通过" if ok else "存在失败")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
