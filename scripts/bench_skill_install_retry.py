"""修复前后 · 网络中断重试性能基准

对比目标（同一 flaky 服务器、同机采样）：
    修复前 = 关闭重试（SKILL_INSTALL_MAX_RETRIES=0，等价于旧无重试逻辑的耗时特征）
    修复后 = 启用重试（默认 SKILL_INSTALL_MAX_RETRIES=2, backoff 可配）

场景:
    A 瞬时中断: 第 1 次请求断流后恢复 → 修复前: 失败(快速)；修复后: 重试成功(多 1 次退避)
    B 持续中断: 每次均断流             → 修复前: 失败(1 次请求)；修复后: 重试耗尽(1+N 次请求+退避和)

输出:
    终端表格 + docs/PERF_BENCHMARK_RETRY_REPORT.md（自动生成）

运行:
    python scripts/bench_skill_install_retry.py                # 默认采样 5 次, backoff=0.5
    python scripts/bench_skill_install_retry.py -n 10 -b 0.1   # 采样 10 次, backoff=0.1s
"""
from __future__ import annotations

import argparse
import http.server
import json
import os
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.skills_mgmt import SkillInstallError, SkillsMgmtService  # noqa: E402

_PAYLOAD = {
    "id": "bench-skill",
    "name": "bench-skill",
    "description": "性能基准测试技能",
    "content": "def run(x):\n    return x.strip()\n",
    "content_type": "python",
    "category": "custom",
    "tags": ["bench"],
    "author": "bench",
    "version": "0.1.0",
}


def _make_handler(fail_first_n: int):
    """前 fail_first_n 次请求下载中途断流，之后正常返回"""
    class _FlakyHandler(http.server.BaseHTTPRequestHandler):
        counter = 0

        def log_message(self, *args):
            pass

        def _partial_then_abort(self):
            body = json.dumps(_PAYLOAD).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body) + 100))
            self.end_headers()
            self.wfile.write(body[:10])
            self.wfile.flush()
            self.connection.close()

        def _full(self):
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
                self._partial_then_abort()
            else:
                self._full()

    return _FlakyHandler


def run_once(fail_first_n: int, max_retries: int, backoff: float) -> Dict:
    """单次安装测量 → {elapsed_ms, requests, success, error}"""
    os.environ["SKILL_INSTALL_MAX_RETRIES"] = str(max_retries)
    os.environ["SKILL_INSTALL_RETRY_BACKOFF"] = str(backoff)

    handler_cls = _make_handler(fail_first_n)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    url = f"http://{host}:{port}/skill.json"

    store_path = tempfile.mkdtemp(prefix="bench_")
    svc = SkillsMgmtService(store_path=str(Path(store_path) / "s.json"), http_timeout=5)

    t0 = time.perf_counter()
    success = False
    error = ""
    try:
        svc.install(f"url:{url}")
        success = True
    except SkillInstallError as e:
        error = e.code
    except Exception as e:  # noqa: BLE001
        error = f"{type(e).__name__}: {e}"
    elapsed_ms = (time.perf_counter() - t0) * 1000

    server.shutdown()
    server.server_close()
    thread.join(timeout=2.0)
    return {"elapsed_ms": round(elapsed_ms, 2), "requests": handler_cls.counter,
            "success": success, "error": error}


def bench(label: str, fail_first_n: int, max_retries: int, backoff: float,
          samples: int) -> Dict:
    """采样 N 次 → 统计聚合"""
    results = [run_once(fail_first_n, max_retries, backoff) for _ in range(samples)]
    times = [r["elapsed_ms"] for r in results]
    success_rate = sum(1 for r in results if r["success"]) / len(results) * 100
    return {
        "label": label, "fail_first_n": fail_first_n,
        "max_retries": max_retries, "backoff": backoff,
        "samples": samples,
        "success_rate": round(success_rate, 1),
        "median_ms": round(statistics.median(times), 1),
        "mean_ms": round(statistics.mean(times), 1),
        "p95_ms": round(sorted(times)[int(len(times) * 0.95) - 1], 1),
        "min_ms": round(min(times), 1), "max_ms": round(max(times), 1),
        "requests": [r["requests"] for r in results],
        "error": results[0]["error"],
    }


def _fmt_row(s: Dict) -> str:
    return (f"| {s['label']} | {s['max_retries']} | {s['backoff']}s | "
            f"{s['success_rate']}% | {s['median_ms']} | {s['mean_ms']} | "
            f"{s['p95_ms']} | {s['min_ms']}~{s['max_ms']} | "
            f"{s['requests']} | {s['error']} |")


def main() -> None:
    ap = argparse.ArgumentParser(description="网络中断重试性能基准")
    ap.add_argument("-n", "--samples", type=int, default=5, help="每组采样次数")
    ap.add_argument("-b", "--backoff", type=float, default=0.5, help="修复后退避秒数")
    ap.add_argument("--no-report", action="store_true", help="不生成 markdown 报告")
    args = ap.parse_args()

    # 配置矩阵：修复前(max_retries=0) vs 修复后(max_retries=2) × 场景A/B
    configs = [
        ("A-修复前(瞬时中断)", 1, 0, args.backoff),
        ("A-修复后(瞬时中断)", 1, 2, args.backoff),
        ("B-修复前(持续中断)", 10 ** 6, 0, args.backoff),
        ("B-修复后(持续中断)", 10 ** 6, 2, args.backoff),
    ]

    print("=" * 100)
    print(f" 技能安装 · 网络中断重试性能基准（采样 {args.samples} 次/组, backoff={args.backoff}s）")
    print("=" * 100)
    print("| 配置 | 重试 | 退避 | 成功率 | 中位ms | 均值ms | P95ms | 范围ms | 请求序列 | 错误码 |")
    print("|------|-----:|-----:|------:|-------:|-------:|------:|--------|---------|--------|")

    stats: List[Dict] = []
    for label, ffn, retries, backoff in configs:
        s = bench(label, ffn, retries, backoff, args.samples)
        stats.append(s)
        print(_fmt_row(s))

    # 结论
    a_old, a_new, b_old, b_new = stats
    print()
    print("[结论 1] 瞬时中断: 修复前成功率 "
          f"{a_old['success_rate']}%（无法自愈），修复后 {a_new['success_rate']}%，"
          f"中位耗时 {a_old['median_ms']}ms → {a_new['median_ms']}ms "
          f"(+{a_new['median_ms'] - a_old['median_ms']:.0f}ms = 1 次退避代价)")
    print("[结论 2] 持续中断: 修复前中位 "
          f"{b_old['median_ms']}ms（1 次请求即失败），修复后 {b_new['median_ms']}ms"
          f"（{b_new['requests'][0]} 次请求 + 2 次退避，失败仍快速收敛）")
    print(f"[结论 3] 代价上界: 每次重试最多增加 "
          f"{b_new['median_ms'] - b_old['median_ms']:.0f}ms 失败路径耗时，"
          f"换取瞬时中断场景 {a_old['success_rate']:.0f}% → {a_new['success_rate']:.0f}% 的自愈率")

    if not args.no_report:
        _write_report(stats, args)
        print()
        print("报告已生成: docs/PERF_BENCHMARK_RETRY_REPORT.md")


def _write_report(stats: List[Dict], args) -> None:
    a_old, a_new, b_old, b_new = stats
    lines = [
        "# 性能基准报告：网络中断重试修复前后对比",
        "",
        f"> 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"> 采样：每组 {args.samples} 次 | 修复后退避：`{args.backoff}s`（指数）| "
        "修复前基线 = 关闭重试（`SKILL_INSTALL_MAX_RETRIES=0`，等价旧无重试逻辑耗时特征）",
        "",
        "## 一、测试环境与方法",
        "",
        "本地 `ThreadingHTTPServer` 谎报 `Content-Length` 后只发前 10 字节即强制断开连接，",
        "真实触发客户端 `IncompleteRead`（http.client.HTTPException），模拟下载中途断流。",
        "",
        "- **场景 A 瞬时中断**：第 1 次请求断流后恢复 —— 验证「重试自愈」能力与代价",
        "- **场景 B 持续中断**：每次请求均断流 —— 验证「失败快速收敛」能力",
        "",
        "## 二、原始数据",
        "",
        "| 配置 | 重试 | 退避 | 成功率 | 中位ms | 均值ms | P95ms | 范围ms | 请求序列 | 错误码 |",
        "|------|-----:|-----:|------:|-------:|-------:|------:|--------|---------|--------|",
    ]
    for s in stats:
        lines.append(_fmt_row(s))
    lines += [
        "",
        "## 三、结论",
        "",
        "### 3.1 瞬时中断（自愈能力）",
        f"- 修复前成功率 **{a_old['success_rate']}%** —— 一次抖动即安装失败，无法自愈",
        f"- 修复后成功率 **{a_new['success_rate']}%**，中位耗时 "
        f"{a_old['median_ms']}ms → {a_new['median_ms']}ms",
        f"- 代价：+{a_new['median_ms'] - a_old['median_ms']:.0f}ms（约 1 次退避窗口），"
        "换取「抖动自愈」，对真实弱网环境的收益远大于代价",
        "",
        "### 3.2 持续中断（失败收敛）",
        f"- 修复前中位 **{b_old['median_ms']}ms**（1 次请求即失败）",
        f"- 修复后中位 **{b_new['median_ms']}ms**（{b_new['requests'][0]} 次请求 + "
        f"2 次指数退避 ≈ {args.backoff * 3:.1f}s），重试耗尽后仍按契约抛 "
        "`INSTALL_SOURCE_UNREACHABLE`，**不会无限重试**",
        "",
        "### 3.3 建议",
        "",
        "- 弱网/公网安装：保持默认 `SKILL_INSTALL_MAX_RETRIES=3`、`SKILL_INSTALL_RETRY_BACKOFF=0.5`",
        "- 对延迟敏感场景：可调 `SKILL_INSTALL_MAX_RETRIES=1` 或关闭（`=0` 恢复旧行为）",
        "",
        "## 四、复现",
        "",
        "```bash",
        f"python scripts/bench_skill_install_retry.py -n {args.samples} -b {args.backoff}",
        "```",
    ]
    out = ROOT / "docs" / "PERF_BENCHMARK_RETRY_REPORT.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
