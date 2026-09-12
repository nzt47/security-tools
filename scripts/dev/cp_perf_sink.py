"""U6 性能结果接收端（TASK-S6-01 本地门禁用）

【做什么】
    前端性能用例（`src/pages/hub/governance/perf.test.tsx`）与浏览器实测脚本
    （`scripts/dev/cp_perf_probe.py`）把实测 JSON POST 到这里，本脚本落盘到
    `reports/s6_01/*.json`，供验收报告与 `docs/PERF_BUDGET_REBASED.md` 回填引用。

【为什么不用前端直接写文件】
    vitest 跑在 jsdom（浏览器语义）里，直接 fs 写仓库路径会引入环境耦合；
    用本机接收端更接近"采集"语义，也让**落盘失败不影响断言**成为默认行为。

用法::

    python scripts/dev/cp_perf_sink.py --port 5711 --out reports/s6_01
"""

from __future__ import annotations

import argparse
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_LOCK = threading.Lock()


def _sink_handler(out_dir: str):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # noqa: A003 静音默认访问日志
            return

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                payload = {"raw_len": len(raw), "parse_error": True}
            name = str(payload.get("metric") or "unknown")
            os.makedirs(out_dir, exist_ok=True)
            path = os.path.join(out_dir, f"{name}.json")
            with _LOCK:
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, ensure_ascii=False, indent=2)
            body = json.dumps({"ok": True, "saved": path}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802 健康检查
            body = b'{"ok": true, "sink": "cp-perf"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description="U6 性能结果接收端")
    parser.add_argument("--port", type=int, default=5711)
    parser.add_argument("--out", default=os.path.join("reports", "s6_01"))
    parser.add_argument("--once", action="store_true",
                        help="收到第一份结果后退出（CI/单次门禁用）")
    args = parser.parse_args()

    server = ThreadingHTTPServer(("127.0.0.1", args.port),
                                 _sink_handler(args.out))
    print(f"[cp-perf-sink] listening on http://127.0.0.1:{args.port} → {args.out}")
    if args.once:
        server.handle_request()
    else:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
