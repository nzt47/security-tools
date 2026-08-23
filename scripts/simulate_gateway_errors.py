"""云枢 T8 网关 401/429 报错场景模拟 + 日志定位步骤演示

场景（对应运维手册 §6 故障排查表）：
  S1 401-无 Key    : 未携带任何认证头访问灰度开放端点 → 401 Unauthorized
  S2 401-无效 Key  : 伪造 64 位 Key → 401（认证失败）
  S3 403-权限不足  : 仅 read scope 的 Key 访问要求 write 的端点（本脚本构造演示端点）
  S4 429-接口限流  : 带 Key 连发 > 接口令牌桶容量(默认10) → Rate limit exceeded
  S5 429-配额耗尽  : 进程内操纵 QuotaManager 演示租户配额 429（不依赖外部服务）

每个场景后打印"日志定位四步法"：
  ① 响应体 error 字段 → ② /api/open/stats 状态码分布 → ③ app_server 终端日志
  → ④ /api/audit/logs 审计记录

用法：python scripts/simulate_gateway_errors.py [--base-url http://127.0.0.1:5678]
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "http://127.0.0.1:5678"
AUDIT_PATH = "/api/audit/logs"


def _http(method: str, path: str, headers: dict | None = None, body: dict | None = None,
          timeout: int = 10, base: str = BASE):
    url = f"{base}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, (json.loads(resp.read().decode()) if resp.status != 204 else None)
    except urllib.error.HTTPError as e:
        return e.code, (json.loads(e.read().decode()) if e.reason else None)


def get_valid_key() -> str:
    """从 .env 读有效 Key（部署脚本写入的 YUNSHU_API_KEY）"""
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("YUNSHU_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"')
    return ""


def locate(status_code: int, body: dict | None, key: str, tag: str) -> None:
    """日志定位四步法（对照运维手册 §6 故障排查表）"""
    print(f"\n  --- 日志定位（{tag}，{status_code}） ---")
    print(f"  [① 响应体] error={ (body or {}).get('error') or (body or {}) }")
    st, stats = _http("GET", "/api/open/stats")
    if st == 200 and stats:
        s = stats.get("stats", {})
        codes = s.get("status_codes") or {}
        if codes:
            print(f"  [② 网关统计] /api/open/stats status_codes={codes}")
        else:
            print(f"  [② 网关统计] /api/open/stats access_logs={s.get('access_logs')}（"
                  f"内存日志；状态分布请查 app_server 结构化日志 access 条目）")
    print(f"  [③ 服务端]   app_server 终端日志搜索: status_code={status_code} 或 "
          f"error 关键词（结构化日志 access 条目）")
    if key:
        st2, aud = _http("GET", f"{AUDIT_PATH}?limit=3", headers={"X-API-Key": key})
        if st2 == 200 and aud:
            print(f"  [④ 审计日志] 最近 {aud.get('count')} 条可查（data/audit/audit_*.jsonl，"
                  f"仅成功请求落审计）")


def scenario_401_no_key():
    """S1 无 Key → 401"""
    print("\n" + "=" * 60)
    print("S1 模拟：无 Key 访问灰度开放端点（对照运维手册 §6 第 1 行）")
    print("=" * 60)
    status, body = _http("GET", AUDIT_PATH)
    print(f"  -> GET {AUDIT_PATH}（无认证头） => HTTP {status}")
    locate(status, body, "", "401 无 Key")


def scenario_401_bad_key():
    """S2 伪造 Key → 401"""
    print("\n" + "=" * 60)
    print("S2 模拟：伪造 64 位无效 Key（对照运维手册 §6 第 1 行）")
    print("=" * 60)
    fake = "f" * 64
    status, body = _http("GET", AUDIT_PATH, headers={"X-API-Key": fake})
    print(f"  -> GET {AUDIT_PATH}（X-API-Key: ffff...） => HTTP {status}")
    locate(status, body, "", "401 无效 Key")


def scenario_403_scope():
    """S3 权限不足 → 403（本地网关注册 require write 的端点验证）"""
    print("\n" + "=" * 60)
    print("S3 模拟：scope 不足（对照运维手册 §6 第 2 行）")
    print("=" * 60)
    key = get_valid_key()
    # 在独立网关实例上注册一个 require write 的端点，用 read-only Key 访问
    try:
        from agent.api_gateway import ApiGateway
        gw = ApiGateway()
        gw.register_endpoint(path="/test/write-only", method="GET",
                             handler=lambda req: {"ok": True},
                             auth_required=True, scopes=["write"])
        gw._api_key_manager._api_keys[key] = {
            "key": key, "user_id": "deploy", "scopes": ["read"],
            "tenant_id": "", "role": "", "compat_until": "", "enabled": True,
            "usage_count": 0, "quota_remaining": 10000, "total_quota": 10000,
            "last_used_at": "", "created_at": "2026-08-16T00:00:00",
        }
        from types import SimpleNamespace
        resp = gw.handle_request(SimpleNamespace(path="/test/write-only", method="GET",
                                                 headers={"X-API-Key": key}))
        print(f"  -> GET /test/write-only（仅 read scope） => HTTP {resp.get('status_code')}"
              f" error={resp.get('error')}")
        locate(resp.get("status_code"), resp, key, "403 权限不足")
    except Exception as e:  # noqa: BLE001 演示失败不阻断
        print(f"  [WARN] S3 本地构造失败: {e}")


def scenario_429_rate_limit():
    """S4 接口级限流 → 429（真实 HTTP 连发打满令牌桶）"""
    print("\n" + "=" * 60)
    print("S4 模拟：接口级限流（对照运维手册 §6 第 4 行）")
    print("  原理：接口令牌桶默认 10 令牌 / 1s 补充，1s 内连发 15 次")
    print("=" * 60)
    key = get_valid_key()
    if not key:
        print("  [WARN] .env 无 YUNSHU_API_KEY，先运行 deploy_t8_gateway.py")
        return
    codes: list[int] = []
    for i in range(1, 16):
        status, body = _http("GET", f"{AUDIT_PATH}?limit=1",
                             headers={"X-API-Key": key}, timeout=8)
        codes.append(status)
        if status == 429:
            print(f"  -> 第 {i} 次请求 => HTTP 429 error={ (body or {}).get('error') }")
        time.sleep(0.05)
    print(f"  -> 状态码序列（15 连发）: {codes}")
    print(f"  -> 前 {codes.index(429) if 429 in codes else '-'} 次放行，第 "
          f"{codes.index(429) + 1 if 429 in codes else '-'} 次起 429（令牌耗尽）")
    locate(429, {"error": "Rate limit exceeded"}, key, "429 接口限流")


def scenario_429_quota():
    """S5 租户配额 → 429（进程内操纵 QuotaManager，快速验证配额链路）"""
    print("\n" + "=" * 60)
    print("S5 模拟：租户配额耗尽（对照运维手册 §6 第 5 行）")
    print("  说明：运行时无 HTTP 配额管理端点，此场景在进程内直接操纵")
    print("        QuotaManager（等效 handle_request 的配额检查链路）")
    print("=" * 60)
    try:
        from agent.api_gateway import ApiGateway, QuotaManager
        from types import SimpleNamespace
        gw = ApiGateway()
        gw._quota_manager.set_tenant_quota("org_demo", "api_calls", limit=1)
        gw.register_endpoint(path=AUDIT_PATH, method="GET",
                             handler=lambda req: {"ok": True},
                             auth_required=True, scopes=["read"])
        key = "k" * 64
        gw._api_key_manager._api_keys[key] = {
            "key": key, "user_id": "u1", "scopes": ["read"],
            "tenant_id": "org_demo", "role": "member", "compat_until": "",
            "enabled": True, "usage_count": 0, "quota_remaining": 10000,
            "total_quota": 10000, "last_used_at": "", "created_at": "2026-08-16T00:00:00",
        }
        with mock.patch(
                "agent.multi_tenant.tenant_manager.has_permission", return_value=True):
            r1 = gw.handle_request(SimpleNamespace(path=AUDIT_PATH, method="GET",
                                                   headers={"X-API-Key": key}))
            r2 = gw.handle_request(SimpleNamespace(path=AUDIT_PATH, method="GET",
                                                   headers={"X-API-Key": key}))
        print(f"  -> 第 1 次 => HTTP {r1.get('status_code', 200)}"
              f"（消耗租户配额 used=1/limit=1）")
        print(f"  -> 第 2 次 => HTTP {r2.get('status_code')} error={r2.get('error')}")
        locate(r2.get("status_code"), r2, "", "429 租户配额")
    except Exception as e:  # noqa: BLE001
        print(f"  [WARN] S5 本地构造失败: {e}")


def main():
    global BASE
    ap = argparse.ArgumentParser(description="T8 网关 401/429 模拟与日志定位")
    ap.add_argument("--base-url", default=BASE)
    ap.add_argument("--only", default="all",
                    help="all | 401 | 403 | 429rate | 429quota")
    args = ap.parse_args()
    BASE = args.base_url

    print("云枢 T8 网关错误场景模拟（对照运维部署手册 §6 故障排查表）")
    key = get_valid_key()
    print(f"有效 Key 来源: .env YUNSHU_API_KEY {'已就绪' if key else '缺失'}")

    if args.only in ("all", "401"):
        scenario_401_no_key()
        scenario_401_bad_key()
    if args.only in ("all", "403"):
        scenario_403_scope()
    if args.only in ("all", "429rate"):
        scenario_429_rate_limit()
    if args.only in ("all", "429quota"):
        scenario_429_quota()

    print("\n" + "=" * 60)
    print("模拟完成。完整排查步骤见 docs/zh/云枢T8多租户运维部署手册_20260816.md §6")
    print("=" * 60)


if __name__ == "__main__":
    main()
