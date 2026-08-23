"""云枢 T8 多租户网关一键部署脚本（幂等，可重复执行）

按序自动执行：
  1. .env 配置    — 缺失 FLASK_API_TOKEN 时生成随机令牌写入 .env（经 env_config_manager，走 .env 单一数据源）
  2. 服务就绪     — 轮询 /api/open/echo 直到 200
  3. 租户初始化   — 按 name 查重，不存在则创建组织租户（自动 owner）
  4. Key 签发     — 复用 .env 中 YUNSHU_API_KEY（若有效）或新建并回写 .env
  5. 网关注册验证 — /api/docs 检索灰度开放端点是否已注册
  6. 监控指标采集 — /api/open/stats 状态码分布 + 审计日志条数，输出摘要

用法：
  python scripts/deploy_t8_gateway.py                      # 默认 127.0.0.1:5678
  python scripts/deploy_t8_gateway.py --base-url http://x:5678 --tenant-name acme \
         --owner-email ops@acme.com --key-user ops@acme.com --skip-env

只使用 Python 标准库（urllib/secrets），无第三方依赖。
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

# 项目根入 sys.path（脚本可在任意 cwd 下运行）
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Windows 控制台默认 GBK：强制 UTF-8 输出 + 脚本内使用 ASCII 装饰符双保险
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 灰度开放端点（与 api_gateway_flask._INTERNAL_OPEN_BATCH1/2 一致，用于网关注册验证）
EXPECTED_OPEN_ENDPOINTS = [
    "/api/news",
    "/api/search-performance/status",
    "/api/audit/logs",
    "/api/schedules",
    "/api/skills",
    "/api/tasks",
]


def _http(method: str, url: str, body: dict | None = None,
          headers: dict | None = None, timeout: int = 10):
    """极简 HTTP 客户端，返回 (status, json_dict 或 None)"""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            return resp.status, (json.loads(text) if text else None)
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", errors="replace")
        return e.code, (json.loads(text) if text else None)
    except urllib.error.URLError as e:
        return None, {"error": f"连接失败: {e.reason}"}


def step(title: str):
    print(f"\n==> {title}")


def _load_dotenv_file() -> None:
    """把 .env 文件内容并入 os.environ（已有值不覆盖）

    env_config_manager.get() 只读 os.environ；新进程需先从 .env 文件加载，
    否则每次运行都会重新生成 FLASK_API_TOKEN / 重新签发 Key（幂等性破坏）。
    """
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def ensure_env(skip: bool) -> None:
    """1) .env 配置：加载现有配置，缺失 FLASK_API_TOKEN 时生成"""
    step("1. .env 配置（FLASK_API_TOKEN）")
    _load_dotenv_file()
    if skip:
        print("  [OK] --skip-env：仅加载 .env，不写入")
        return
    from agent.env_config_manager import get_env_config_manager
    mgr = get_env_config_manager()
    token = os.environ.get("FLASK_API_TOKEN", "")
    if token:
        print(f"  [OK] FLASK_API_TOKEN 已存在（{token[:6]}***），复用")
    else:
        new_token = secrets.token_hex(32)
        mgr.set("FLASK_API_TOKEN", new_token)
        print(f"  [OK] 已生成 FLASK_API_TOKEN 并写入 .env: {new_token[:8]}***")
        print("  [WARN] 服务已在运行则需重启 app_server 才生效（下次启动自动加载）")


def wait_ready(base: str, tries: int = 30, interval: float = 2.0) -> bool:
    """2) 服务就绪检查"""
    step("2. 服务就绪检查（/api/open/echo）")
    for i in range(1, tries + 1):
        status, _ = _http("GET", f"{base}/api/open/echo", timeout=3)
        if status == 200:
            print(f"  [OK] 服务就绪（第 {i} 次探测，200）")
            return True
        time.sleep(interval)
    print(f"  [FAIL] 服务未就绪（{tries} 次探测失败），请先启动 python app_server.py")
    return False


def ensure_tenant(base: str, name: str, owner_email: str, admin_token: str) -> tuple[str, str]:
    """3) 租户初始化（幂等：按 name 查重）

    管理端点带 @require_token（内部令牌 X-API-Token），生产必传。
    返回 (tenant_id, owner_user_id)；幂等复用场景 owner_user_id 为 ""。
    """
    step("3. 租户初始化")
    headers = {"X-API-Token": admin_token} if admin_token else {}
    status, data = _http("GET", f"{base}/api/open/tenants", headers=headers, timeout=10)
    if status == 200 and data:
        for t in data.get("tenants", []):
            if t.get("name") == name:
                print(f"  [OK] 租户已存在，复用: {t.get('id')}")
                return t.get("id"), ""
    status, data = _http("POST", f"{base}/api/open/tenants", body={
        "name": name, "type": "organization", "owner_email": owner_email,
    }, headers=headers, timeout=15)
    if status in (200, 201) and data:
        tid = data.get("organization", {}).get("id") or data.get("id")
        owner_id = data.get("owner", {}).get("id", "")
        if tid:
            print(f"  [OK] 租户创建成功: {tid}（owner={owner_email}）")
            return tid, owner_id
    print(f"  [FAIL] 租户创建失败（{status}）: {data}")
    sys.exit(1)


def ensure_key(base: str, tenant_id: str, owner_id: str, user_email: str) -> str:
    """4) Key 签发（幂等：优先复用 .env 的 YUNSHU_API_KEY）

    - 有 owner_id（本次新建租户）→ 绑定租户 + role=admin（验证 RBAC 链路）
    - 无 owner_id（幂等复用）→ 未绑定 Key（自带 scopes，无权限依赖）
    """
    step("4. Key 签发")
    _load_dotenv_file()
    from agent.env_config_manager import get_env_config_manager
    mgr = get_env_config_manager()
    existing = (os.environ.get("YUNSHU_API_KEY") or "").strip()
    if existing:
        status, _ = _http("GET", f"{base}/api/audit/logs?limit=1",
                          headers={"X-API-Key": existing}, timeout=8)
        if status == 200:
            print(f"  [OK] 复用 .env 中 YUNSHU_API_KEY（{existing[:8]}***），校验通过")
            return existing
        print(f"  [WARN] .env 中 YUNSHU_API_KEY 校验失败（{status}），重新签发")
    body = {"user_id": owner_id or user_email, "description": "deploy_t8_gateway"}
    if owner_id:
        body.update({"tenant_id": tenant_id, "role": "admin"})
    else:
        body.update({"tenant_id": "", "scopes": ["read", "write"]})
    status, data = _http("POST", f"{base}/api/open/keys", body=body, timeout=10)
    if status in (200, 201) and data and data.get("api_key"):
        key = data["api_key"]
        mgr.set("YUNSHU_API_KEY", key)
        mode = "绑定租户+admin" if owner_id else "未绑定（自带 scopes）"
        print(f"  [OK] 新 Key 已签发（{mode}）并回写 .env: {key[:8]}***（明文仅此一次）")
        return key
    print(f"  [FAIL] Key 签发失败（{status}）: {data}")
    sys.exit(1)


def verify_registry(base: str) -> None:
    """5) 网关注册验证：/api/docs 检索开放端点"""
    step("5. 网关注册验证（/api/docs）")
    status, data = _http("GET", f"{base}/api/docs", timeout=10)
    if status != 200 or not data:
        print(f"  [FAIL] /api/docs 不可用（{status}）")
        return
    paths = data.get("paths", {})
    missing = [p for p in EXPECTED_OPEN_ENDPOINTS if p not in paths]
    if not missing:
        print(f"  [OK] 全部 {len(EXPECTED_OPEN_ENDPOINTS)} 个灰度开放端点已注册于 Swagger")
    else:
        print(f"  [WARN] 缺失端点: {missing}（可能未重启或未扫描）")
    print(f"  - /api/docs 共登记 {len(paths)} 条路径")


def collect_metrics(base: str, api_key: str) -> None:
    """6) 监控指标采集"""
    step("6. 监控指标采集")
    status, stats = _http("GET", f"{base}/api/open/stats", timeout=8)
    if status == 200 and stats:
        st = stats.get("stats", {})
        rl = st.get("rate_limiter", {})
        print(f"  - 网关受管端点: {st.get('endpoints')}")
        print(f"  - API Key 数:   {st.get('api_keys')}")
        print(f"  - 访问日志条数: {st.get('access_logs')}")
        print(f"  - 限流器规则:   {len(rl.get('rules', {}))} 条，全局桶余量 "
              f"{rl.get('global_bucket', {}).get('tokens', 'n/a')}")
    else:
        print(f"  [WARN] /api/open/stats 不可用（{status}）")
    # 审计日志采集
    status, data = _http("GET", f"{base}/api/audit/logs?limit=5",
                         headers={"X-API-Key": api_key}, timeout=8)
    if status == 200 and data:
        print(f"  - 审计日志最近条数: {data.get('count')}")
    else:
        print(f"  [WARN] 审计日志采集失败（{status}）")


def main():
    ap = argparse.ArgumentParser(description="云枢 T8 多租户网关一键部署")
    ap.add_argument("--base-url", default="http://127.0.0.1:5678")
    ap.add_argument("--tenant-name", default="deploy-demo")
    ap.add_argument("--owner-email", default="deploy@example.com")
    ap.add_argument("--key-user", default="deploy@example.com")
    ap.add_argument("--skip-env", action="store_true", help="跳过 .env 写入")
    ap.add_argument("--admin-token", default="",
                    help="内部管理令牌（FLASK_API_TOKEN）；缺省读 .env")
    args = ap.parse_args()

    print("=" * 60)
    print(f"云枢 T8 网关一键部署  base={args.base_url}")
    print("=" * 60)

    ensure_env(args.skip_env)
    if not wait_ready(args.base_url):
        sys.exit(1)
    admin_token = args.admin_token or os.environ.get("FLASK_API_TOKEN", "")
    tid, owner_id = ensure_tenant(args.base_url, args.tenant_name, args.owner_email, admin_token)
    key = ensure_key(args.base_url, tid, owner_id, args.key_user)
    verify_registry(args.base_url)
    collect_metrics(args.base_url, key)

    print("\n" + "=" * 60)
    print("部署完成 [DONE]  Key 已写入 .env 的 YUNSHU_API_KEY（明文仅此一次）")
    print(f"示例调用: curl -H \"X-API-Key: $env:YUNSHU_API_KEY\" "
          f"{args.base_url}/api/audit/logs?limit=5")
    print("=" * 60)


if __name__ == "__main__":
    main()
