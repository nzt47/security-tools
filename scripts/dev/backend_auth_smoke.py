#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
管理后台接口故障排查自动化测试（对应维护文档 §6「故障排查与测试指引」）

覆盖场景：
  1. 登录：成功（token + role）/ 错误密码（code 401）/ 空参数（code 400）
  2. 用户信息：有效 Token / 无 Token（业务 401）/ 无效 Token（HTTP 401）
  3. 用户列表：分页 / 关键字搜索
  4. 删除用户：成功 / 内置管理员（code 400）/ 不存在（code 404）
  5. Redis 令牌过期模拟：删除 Redis key 后访问受保护接口 → HTTP 401
  6. Redis 降级检测：Redis 不可达时验证登录与用户信息仍可用（内存兜底，不 500）

依赖：requests、redis（系统 Python 已安装）
用法：
  python scripts/dev/backend_auth_smoke.py                          # 默认连本机后端
  python scripts/dev/backend_auth_smoke.py --base-url http://localhost:5173
  python scripts/dev/backend_auth_smoke.py --username admin --password admin123

注意：
  - 删除用例会改动后端进程内用户列表（内存数据，后端重启即恢复初始种子）。
  - Redis 断连场景需手动停止 Redis 后重跑本脚本即可验证降级（脚本会自动检测）。
"""
import argparse
import os
import sys
import time

import requests

PASS = 0
FAIL = 0
FAILED: list[str] = []


def report(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        FAILED.append(name)
        print(f"[FAIL] {name} - {detail}")


def _env_value(key: str, default: str) -> str:
    """优先取进程环境变量，其次读根目录 .env（与后端加载行为一致）"""
    val = os.environ.get(key)
    if val:
        return val
    env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".env")
    try:
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == key:
                    return v.strip().strip('"').strip("'")
    except Exception:
        pass
    return default


def main() -> int:
    ap = argparse.ArgumentParser(description="管理后台接口故障排查自动化测试")
    ap.add_argument("--base-url", default="http://127.0.0.1:5678", help="后端地址（默认直连 5678，可传 Vite 代理）")
    ap.add_argument("--redis-url", default="redis://127.0.0.1:6379/0", help="Redis 地址")
    ap.add_argument("--username", default=_env_value("YUNSHU_ADMIN_USERNAME", "admin"), help="管理员用户名（默认读根目录 .env 的 YUNSHU_ADMIN_USERNAME）")
    ap.add_argument("--password", default=_env_value("YUNSHU_ADMIN_PASSWORD", "admin123"), help="管理员密码（默认读根目录 .env 的 YUNSHU_ADMIN_PASSWORD）")
    args = ap.parse_args()

    base = args.base_url.rstrip("/")
    hdr = lambda token: {"Authorization": f"Bearer {token}"} if token else {}

    # ── 0. 后端连通性 ──
    try:
        r = requests.get(f"{base}/api/health", timeout=5)
        report("后端 /api/health 可达", r.status_code == 200, f"HTTP {r.status_code}")
    except Exception as e:
        report("后端 /api/health 可达", False, str(e))
        print("后端不可达，终止。请先启动: python app_server.py")
        return 1

    # ── 1. 登录 ──
    r = requests.post(f"{base}/api/auth/login", json={"username": args.username, "password": args.password}, timeout=5)
    body = r.json()
    ok = r.status_code == 200 and body.get("code") == 200 and body.get("data", {}).get("token")
    report("登录成功返回 token", ok, f"HTTP {r.status_code} code={body.get('code')}")
    token = body.get("data", {}).get("token", "") if ok else ""
    if ok:
        report("登录返回角色 admin", body["data"]["user"].get("role") == "admin", str(body["data"]["user"].get("role")))

    r = requests.post(f"{base}/api/auth/login", json={"username": args.username, "password": "wrong-pass"}, timeout=5)
    body = r.json()
    report("错误密码返回业务 401", r.status_code == 200 and body.get("code") == 401, f"HTTP {r.status_code} code={body.get('code')}")

    r = requests.post(f"{base}/api/auth/login", json={"username": "", "password": ""}, timeout=5)
    body = r.json()
    report("空参数返回业务 400", r.status_code == 200 and body.get("code") == 400, f"HTTP {r.status_code} code={body.get('code')}")

    if not token:
        print("登录失败，后续依赖 token 的用例跳过。")
        return 1

    # ── 2. 用户信息 ──
    r = requests.get(f"{base}/api/user/info", headers=hdr(token), timeout=5)
    body = r.json()
    report("有效 Token 获取用户信息", r.status_code == 200 and body.get("code") == 200, f"HTTP {r.status_code} code={body.get('code')}")

    r = requests.get(f"{base}/api/user/info", timeout=5)
    body = r.json()
    report("无 Token 返回业务 401（HTTP 200）", r.status_code == 200 and body.get("code") == 401, f"HTTP {r.status_code} code={body.get('code')}")

    r = requests.get(f"{base}/api/user/info", headers=hdr("invalid-token-xxx"), timeout=5)
    body = r.json()
    report("无效 Token 返回 HTTP 401", r.status_code == 401 and body.get("code") == 401, f"HTTP {r.status_code} code={body.get('code')}")

    # ── 3. 用户列表 ──
    r = requests.get(f"{base}/api/user/list", params={"page": 1, "pageSize": 10}, headers=hdr(token), timeout=5)
    body = r.json()
    data = body.get("data", {})
    ok = r.status_code == 200 and body.get("code") == 200 and data.get("total", 0) >= len(data.get("list", [])) and len(data.get("list", [])) <= 10
    report("用户列表分页（total>=list 且 list<=10）", ok, f"total={data.get('total')} list={len(data.get('list', []))}")

    r = requests.get(f"{base}/api/user/list", params={"keyword": "user02"}, headers=hdr(token), timeout=5)
    body = r.json()
    kw = body.get("data", {}).get("list", [])
    report("关键字搜索 user02", r.status_code == 200 and len(kw) == 1 and kw[0]["username"] == "user02", f"命中 {len(kw)} 条")

    # ── 4. 删除用户 ──
    last_id = data.get("list", [])[-1]["id"] if data.get("list") else None
    if last_id:
        r = requests.delete(f"{base}/api/user/{last_id}", headers=hdr(token), timeout=5)
        body = r.json()
        report(f"删除用户 {last_id}", r.status_code == 200 and body.get("code") == 200, f"HTTP {r.status_code} code={body.get('code')}")
    else:
        report("删除用户（列表为空无法测试）", False, "用户列表为空")

    r = requests.delete(f"{base}/api/user/1", headers=hdr(token), timeout=5)
    body = r.json()
    report("删除内置管理员返回业务 400", r.status_code == 200 and body.get("code") == 400, f"HTTP {r.status_code} code={body.get('code')} msg={body.get('message')}")

    r = requests.delete(f"{base}/api/user/99999", headers=hdr(token), timeout=5)
    body = r.json()
    report("删除不存在用户返回业务 404", r.status_code == 200 and body.get("code") == 404, f"HTTP {r.status_code} code={body.get('code')}")

    # ── 4.5 新增 / 编辑用户 ──
    uname = f"smoke_{int(time.time())}"
    r = requests.post(f"{base}/api/user", json={"username": uname, "email": f"{uname}@yunshu.local", "role": "manager", "status": 1}, headers=hdr(token), timeout=5)
    body = r.json()
    new_id = body.get("data", {}).get("id")
    report("新增用户", r.status_code == 200 and body.get("code") == 200 and bool(new_id), f"HTTP {r.status_code} code={body.get('code')} id={new_id}")

    r = requests.post(f"{base}/api/user", json={"username": uname}, headers=hdr(token), timeout=5)
    body = r.json()
    report("重复用户名新增被拒（code 400）", r.status_code == 200 and body.get("code") == 400, f"HTTP {r.status_code} code={body.get('code')}")

    r = requests.post(f"{base}/api/user", json={"username": ""}, headers=hdr(token), timeout=5)
    body = r.json()
    report("空用户名新增被拒（code 400）", r.status_code == 200 and body.get("code") == 400, f"HTTP {r.status_code} code={body.get('code')}")

    if new_id:
        r = requests.put(f"{base}/api/user/{new_id}", json={"email": "updated@yunshu.local", "role": "admin", "status": 0}, headers=hdr(token), timeout=5)
        body = r.json()
        data = body.get("data", {})
        ok = r.status_code == 200 and body.get("code") == 200 and data.get("email") == "updated@yunshu.local" and data.get("role") == "admin" and data.get("status") == 0
        report("编辑用户（邮箱/角色/状态）", ok, f"HTTP {r.status_code} email={data.get('email')} role={data.get('role')} status={data.get('status')}")
        r = requests.delete(f"{base}/api/user/{new_id}", headers=hdr(token), timeout=5)
        body = r.json()
        report("清理测试用户", r.status_code == 200 and body.get("code") == 200, f"HTTP {r.status_code} code={body.get('code')}")
    else:
        report("编辑用户（无法测试：新增失败）", False, "new_id 为空")

    # ── 5. Redis 令牌过期模拟（Redis 可用时） ──
    redis_ok = False
    try:
        import redis as _redis
        rc = _redis.Redis.from_url(args.redis_url, socket_connect_timeout=2, socket_timeout=2)
        redis_ok = bool(rc.ping())
    except Exception as e:
        print(f"[INFO] Redis 不可达（{e}），跳过过期模拟与存储位置断言，进入降级检测。")

    if redis_ok:
        # 登录取 token 并确认落在 Redis
        r = requests.post(f"{base}/api/auth/login", json={"username": args.username, "password": args.password}, timeout=5)
        tk = r.json().get("data", {}).get("token", "")
        key = f"yunshu:user_token:{tk}"
        exists_before = bool(rc.exists(key))
        report("登录令牌写入 Redis", exists_before, key)
        # 模拟过期：删除 key
        rc.delete(key)
        r = requests.get(f"{base}/api/user/info", headers=hdr(tk), timeout=5)
        body = r.json()
        report("令牌过期（Redis key 删除）后返回 HTTP 401", r.status_code == 401 and body.get("code") == 401, f"HTTP {r.status_code}")
    else:
        # ── 6. Redis 降级检测（Redis 不可达） ──
        print("[INFO] 存储模式：内存降级（Redis 不可达）。验证登录与用户信息仍可用、无 500……")
        r = requests.post(f"{base}/api/auth/login", json={"username": args.username, "password": args.password}, timeout=5)
        tk = r.json().get("data", {}).get("token", "")
        report("Redis 不可达时登录仍成功（内存签发）", r.status_code == 200 and bool(tk), f"HTTP {r.status_code}")
        if tk:
            r = requests.get(f"{base}/api/user/info", headers=hdr(tk), timeout=5)
            body = r.json()
            report("Redis 不可达时用户信息仍可用（内存兜底，无 500）", r.status_code == 200 and body.get("code") == 200, f"HTTP {r.status_code}")

    # ── 汇总 ──
    print("=" * 56)
    print(f"测试完成：PASS={PASS}  FAIL={FAIL}")
    if FAILED:
        print("失败用例：")
        for n in FAILED:
            print(f"  - {n}")
    print("=" * 56)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
