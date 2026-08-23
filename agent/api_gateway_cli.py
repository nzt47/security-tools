"""API 网关限流/配额验证 CLI（独立工具）

验证 agent.api_gateway.ApiGateway 的限流与配额功能是否正常，供各团队直接调用。

验证分层（避免污染生产数据）：
1. 单元级验证（不触达生产）：构造独立 RateLimiter / QuotaManager / ApiGateway 实例，
   直接验证多级限流（429）与配额耗尽（429）逻辑。
2. HTTP 存活探测（只读）：探测运行中网关 /api/open/echo、/api/open/stats、/api/docs。
3. --http-stress（可选，对运行中网关做 429 压测）：连续请求 /api/open/echo 触发
   endpoint 级限流，断言出现 429。注意：会短暂占用生产限流令牌（约 1 秒恢复）。

用法（安装后全局命令）:
    pip install -e .          # 安装 Yunshu 包（含 console script）
    yunshu-gateway-check                  # 全量验证（需运行中服务）
    yunshu-gateway-check --unit-only      # 仅单元级（无需服务，CI 推荐）
    yunshu-gateway-check --http-stress    # 附带 429 压测
    yunshu-gateway-check --json           # JSON 输出

等价方式（不安装）:
    python -m agent.api_gateway_cli --unit-only

退出码: 0 = 全部通过; 1 = 任一检查失败
"""
import argparse
import json
import sys
import time
from types import SimpleNamespace

import requests


def test_rate_limit() -> None:
    """单元验证：多级令牌桶限流在耗尽后返回 429"""
    from agent.api_gateway import ApiGateway
    from agent.rate_limiter import RateLimiter

    gw = ApiGateway()
    # 独立限流器：endpoint 桶容量 2、refill 0.01/s（测试期间不会补充）
    # 规则名用请求路径（_get_endpoint_bucket 的 else 分支会命中 endpoint 规则）
    gw._rate_limiter = RateLimiter(max_concurrent=100)
    gw._rate_limiter.register_rule("/test/echo", 2, 0.01)
    gw.register_endpoint(
        path="/test/echo", method="GET",
        handler=lambda req: {"ok": True, "path": req.path},
        auth_required=False,
    )
    req = SimpleNamespace(path="/test/echo", method="GET", headers={})

    r1 = gw.handle_request(req)
    assert r1.get("status_code", 200) == 200, f"首次请求应 200, got {r1}"
    r2 = gw.handle_request(req)
    assert r2.get("status_code", 200) == 200, f"第二次请求应 200, got {r2}"
    r3 = gw.handle_request(req)
    assert r3.get("status_code") == 429, f"第三次请求应触发限流 429, got {r3}"


def test_quota() -> None:
    """单元验证：配额耗尽后返回 429"""
    from agent.api_gateway import ApiGateway

    gw = ApiGateway()
    # 独立配额管理器：anonymous（auth_required=False 的默认 user_id）配额 2 次
    gw._quota_manager.set_quota("anonymous", "api_calls", 2, period="day")
    gw.register_endpoint(
        path="/test/quota", method="GET",
        handler=lambda req: {"ok": True},
        auth_required=False,
    )
    req = SimpleNamespace(path="/test/quota", method="GET", headers={})

    for _ in range(2):
        resp = gw.handle_request(req)
        assert resp.get("status_code", 200) == 200, f"配额内应 200, got {resp}"
    resp = gw.handle_request(req)
    assert resp.get("status_code") == 429, f"配额耗尽应 429, got {resp}"


def test_http_liveness(base_url: str) -> None:
    """HTTP 存活探测（只读）：网关探活 / 统计 / Swagger"""
    endpoints = [
        ("/api/open/echo", 200),
        ("/api/open/stats", 200),
        ("/api/docs", 200),
    ]
    for path, expect in endpoints:
        resp = requests.get(base_url + path, timeout=10)
        assert resp.status_code == expect, (
            f"GET {path} 期望 {expect}, 实际 {resp.status_code}: {resp.text[:200]}"
        )


def test_http_rate_limit_stress(base_url: str, burst: int = 15) -> None:
    """HTTP 压测：连续请求触发 endpoint 级限流，断言出现 429

    Why：/api/open/echo 的 endpoint 桶默认容量 10、refill 1/s，
    burst=15 可稳定触发 429；占用令牌约 1 秒恢复。
    """
    seen_429 = False
    statuses = []
    for _ in range(burst):
        resp = requests.get(base_url + "/api/open/echo", timeout=10)
        statuses.append(resp.status_code)
        if resp.status_code == 429:
            seen_429 = True
    if not seen_429:
        raise AssertionError(
            f"连续 {burst} 次请求未触发限流 429, 状态序列: {statuses}"
        )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="yunshu-gateway-check",
        description="API 网关限流/配额验证（退出码 0=通过, 1=失败）",
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:5678",
                        help="网关服务地址（默认 http://127.0.0.1:5678）")
    parser.add_argument("--unit-only", action="store_true",
                        help="只运行单元级验证（无需运行中服务，供 CI 使用）")
    parser.add_argument("--http-stress", action="store_true",
                        help="对运行中网关做 429 压测（占用生产限流令牌约 1 秒）")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args(argv)

    results = []

    def run(name, fn):
        t0 = time.time()
        try:
            fn()
            results.append({"name": name, "ok": True, "duration_ms": round((time.time() - t0) * 1000, 1)})
        except Exception as e:  # noqa: BLE001 收集失败不中断后续检查
            results.append({"name": name, "ok": False, "error": str(e),
                            "duration_ms": round((time.time() - t0) * 1000, 1)})

    run("rate_limit(单元级)", test_rate_limit)
    run("quota(单元级)", test_quota)
    if not args.unit_only:
        run("http_liveness(只读探测)", lambda: test_http_liveness(args.base_url))
        if args.http_stress:
            run("http_rate_limit_stress(429 压测)", lambda: test_http_rate_limit_stress(args.base_url))

    all_ok = all(r["ok"] for r in results)

    if args.json:
        print(json.dumps({"ok": all_ok, "checks": results}, ensure_ascii=False, indent=2))
    else:
        for r in results:
            mark = "PASS" if r["ok"] else "FAIL"
            detail = f" - {r['error']}" if not r["ok"] else ""
            print(f"[{mark}] {r['name']} ({r['duration_ms']}ms){detail}")
        print(f"\n{'全部通过' if all_ok else '存在失败'}: {sum(1 for r in results if r['ok'])}/{len(results)}")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
