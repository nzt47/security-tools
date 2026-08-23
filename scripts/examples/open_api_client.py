"""云枢开放 API（API Key 鉴权）Python 调用示例客户端

覆盖：创建 Key → 用 Key 调用 T8.4 灰度开放的 8 个只读端点 → 错误场景演示。
每个请求前后打印详细日志（时间戳 + 脱敏 Key + 响应状态/体），便于排查鉴权失败。

鉴权方式（二选一，示例用 X-API-Key）：
  headers = {"X-API-Key": key}
  # 或 headers = {"Authorization": f"Bearer {key}"}

状态码语义：
  200 正常 | 401 无 Key/Key 无效 | 403 无权限（scope 不足）| 429 限流/配额耗尽

用法：
  # 1) 已有 Key：直接探测全部开放端点
  python scripts/examples/open_api_client.py --api-key <key>

  # 2) 无 Key：自动经 /api/open/keys 创建（user_id 必填，可绑 tenant/role）后探测
  python scripts/examples/open_api_client.py --user-id demo@example.com

  # 3) 指定服务地址（默认 http://127.0.0.1:5678）
  python scripts/examples/open_api_client.py --user-id demo@example.com --base-url http://127.0.0.1:5678
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime

try:
    import requests
except ImportError:  # pragma: no cover
    sys.stderr.write("缺少 requests 依赖：pip install requests\n")
    sys.exit(2)

# T8.4 灰度开放的内部只读端点（scope=read，需 API Key）
OPEN_ENDPOINTS = [
    "/api/news",
    "/api/audit/logs",
    "/api/schedules",
    "/api/skills",
    "/api/tasks",
    "/api/search-performance/status",
    "/api/search-performance/history",
    "/api/search-performance/summary",
]


def _log(msg: str) -> None:
    """详细日志：时间戳 + [DEBUG] 前缀（排查鉴权失败用）"""
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [DEBUG] {msg}")


def _mask_key(key: str) -> str:
    """Key 脱敏：仅保留前 6 后 4（日志/报错不得泄露完整明文）"""
    if len(key) <= 10:
        return "***"
    return f"{key[:6]}...{key[-4:]}"


def create_api_key(base_url: str, user_id: str, tenant_id: str = "", role: str = "") -> str:
    """创建 API Key（POST /api/open/keys）；明文仅返回一次"""
    payload = {"user_id": user_id}
    if tenant_id:
        payload["tenant_id"] = tenant_id
    if role:
        payload["role"] = role
    url = f"{base_url}/api/open/keys"
    _log(f"==> POST {url}  body={payload}")
    resp = requests.post(url, json=payload, timeout=10)
    _log(f"<== {resp.status_code}  body={resp.text[:100]}")
    resp.raise_for_status()
    key = resp.json().get("api_key", "")
    _log(f"创建成功 user_id={user_id} tenant={tenant_id or '-'} role={role or '(默认角色)'} "
         f"api_key={_mask_key(key)}")
    print(f"[创建] user_id={user_id} tenant={tenant_id or '-'} role={role or '(默认角色)'}")
    print(f"[创建] api_key={key}")
    return key


def probe(base_url: str, key: str) -> int:
    """用 Key 探测全部开放端点；返回失败数"""
    headers = {"X-API-Key": key}
    failed = 0
    print("\n== 开放端点探测（X-API-Key）==")
    _log(f"鉴权方式: X-API-Key: {_mask_key(key)}  （等价 Authorization: Bearer <key>）")
    for path in OPEN_ENDPOINTS:
        url = base_url + path
        _log(f"==> GET {url}  headers={{'X-API-Key': '{_mask_key(key)}'}}")
        try:
            r = requests.get(url, headers=headers, timeout=10)
            ok = r.status_code == 200
            if not ok:
                failed += 1
            body = (r.text or "")[:60].replace("\n", " ")
            _log(f"<== {r.status_code}  body={body}")
            print(f"  [{'OK ' if ok else f'HTTP {r.status_code}'}] GET {path}  {body}")
        except requests.RequestException as e:
            failed += 1
            _log(f"<== ERR {type(e).__name__}: {e}")
            print(f"  [ERR] GET {path}  {e}")
    return failed


def demo_errors(base_url: str) -> None:
    """演示错误场景：无 Key 401 / 无效 Key 401（服务不可达时打印 ERR 不中断）"""
    print("\n== 错误场景演示 ==")
    path = OPEN_ENDPOINTS[0]
    try:
        url = f"{base_url}{path}"
        _log(f"==> GET {url}  headers={{}}（无 Key，期望 401）")
        r = requests.get(url, timeout=10)
        _log(f"<== {r.status_code}  body={r.text[:80]}")
        print(f"  [无 Key]   GET {path} -> {r.status_code}（期望 401）")

        bad = "deadbeef" * 8
        _log(f"==> GET {url}  headers={{'X-API-Key': '{_mask_key(bad)}'}}（无效 Key，期望 401）")
        r = requests.get(url, headers={"X-API-Key": bad}, timeout=10)
        _log(f"<== {r.status_code}  body={r.text[:80]}")
        print(f"  [无效 Key] GET {path} -> {r.status_code}（期望 401）")

        url = f"{base_url}/api/open/echo"
        _log(f"==> GET {url}  headers={{}}（探活端点 auth_required=False，期望 200）")
        r = requests.get(url, timeout=10)
        _log(f"<== {r.status_code}  body={r.text[:80]}")
        print(f"  [探活]     GET /api/open/echo -> {r.status_code}（无 Key 也应 200）")
    except requests.RequestException as e:
        _log(f"<== ERR {type(e).__name__}: {e}")
        print(f"  [ERR] 错误场景演示无法连接服务：{e}")


def main():
    ap = argparse.ArgumentParser(description="开放 API（API Key）调用示例客户端")
    ap.add_argument("--base-url", default="http://127.0.0.1:5678", help="服务地址")
    ap.add_argument("--api-key", default="", help="已有 API Key（二选一，与 --user-id 互斥）")
    ap.add_argument("--user-id", default="", help="无 Key 时自动创建（必填 user_id）")
    ap.add_argument("--tenant-id", default="", help="创建 Key 时绑定租户")
    ap.add_argument("--role", default="", help="创建 Key 时绑定角色（如 viewer/member/owner）")
    args = ap.parse_args()

    if not args.api_key and not args.user_id:
        ap.error("请提供 --api-key 或 --user-id（自动创建）")

    key = args.api_key
    if not key:
        try:
            key = create_api_key(args.base_url, args.user_id, args.tenant_id, args.role)
        except requests.RequestException as e:
            sys.stderr.write(f"[FAIL] 创建 Key 失败（服务是否在线？{args.base_url}）：{e}\n")
            sys.exit(1)

    failed = probe(args.base_url, key)
    demo_errors(args.base_url)

    if failed:
        print(f"\n[FAIL] {failed}/{len(OPEN_ENDPOINTS)} 个开放端点非 200")
        sys.exit(1)
    print(f"\n[OK] 全部 {len(OPEN_ENDPOINTS)} 个开放端点可正常访问（Key 鉴权链路验证通过）")


if __name__ == "__main__":
    main()
