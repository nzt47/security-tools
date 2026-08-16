#!/usr/bin/env python3
"""S2 smoke 验证：modules_api 注册无冲突 + 三端点可用 + 干预拦截逻辑

1. import app_server 构建真实 app，检查 ACTION_ROUTES 中 URL 在 url_map 无重复规则
2. test_client 调用 /api/modules/topology、detail、actions（仅测拦截分支，不执行真转发）

用法: python scripts/verify_modules_api.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.modules_registry import ACTION_ROUTES  # noqa: E402

passed = 0
failed = 0


def check(name: str, cond: bool, detail: str = ""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  [PASS] {name} {detail}")
    else:
        failed += 1
        print(f"  [FAIL] {name} {detail}")


def main() -> int:
    print("== 构建 app（import app_server）==")
    import app_server  # noqa: F401

    # 测试隔离：本机 .env 可能配置了 FLASK_API_TOKEN（require_token 启用），
    # 关闭 token 拦截使 actions 拦截分支断言不受环境影响（生产行为不变）
    import agent.server_auth as _sa
    _sa._API_TOKEN_ENABLED = False

    app = app_server.app
    app.testing = True  # 让视图异常直接传播，便于定位 500 根因

    # ── 1. url_map 重复规则检查（按 URL 合并 method 集合判定真冲突）──
    print("== 1. ACTION_ROUTES URL 在 url_map 中规则数 ==")
    # 同一 URL 可合法存在多个 rule（GET/POST 方法对）；真冲突 = 两个 rule 有重叠业务方法
    # 排除 Flask 自动添加的 OPTIONS/HEAD，避免方法对误判
    _AUTO = {"OPTIONS", "HEAD"}
    rule_by_url: dict[str, list[set]] = {}
    for rule in app.url_map.iter_rules():
        rule_by_url.setdefault(rule.rule, []).append(set(rule.methods) - _AUTO)
    dup = 0
    for url in {r.url for r in ACTION_ROUTES.values()}:
        rules = rule_by_url.get(url, [])
        if not rules:
            print(f"  [缺失] {url} -> 0 条规则")
            continue
        # 检查是否存在重叠 methods 的两个 rule
        conflict = False
        for i in range(len(rules)):
            for j in range(i + 1, len(rules)):
                if rules[i] & rules[j]:
                    conflict = True
        # 合并展示
        all_methods = sorted({m for s in rules for m in s})
        if conflict:
            dup += 1
            print(f"  [真冲突] {url} -> {len(rules)} 条 rule, 方法重叠: {all_methods}")
        else:
            print(f"  [OK] {url} -> {len(rules)} 条 rule ({','.join(all_methods)})")
    check("url_map 无真冲突(method 重叠)", dup == 0, f"(真冲突 {dup} 个)")

    # 未映射动作（需新增接口）不应在 url_map 中
    if "/api/planning/toggle" in rule_by_url:
        check("toggle_planning 不应已注册", False, "需新增接口竟已存在")
    else:
        check("toggle_planning 未注册(符合'需新增')", True)

    # ── 2. topology / detail ──
    print("== 2. 三端点冒烟 ==")
    client = app.test_client()

    r = client.get("/api/modules/topology")
    data = r.get_json(silent=True) or {}
    check("topology 200", r.status_code == 200, f"status={r.status_code}")
    check("topology 含六域", len(data.get("domains", [])) == 6, f"domains={len(data.get('domains', []))}")
    all_nodes = [n for d in data.get("domains", []) for n in d.get("nodes", [])]
    check("topology 节点数=32", len(all_nodes) == 32, f"nodes={len(all_nodes)}")
    statuses = {n["status"] for n in all_nodes}
    print(f"    节点状态分布: {statuses}")

    r = client.get("/api/modules/action.tools/detail")
    d = r.get_json(silent=True) or {}
    check("detail 200 + 字段", r.status_code == 200 and "actions" in d and d.get("module_id") == "action.tools",
          f"status={r.status_code}")

    r = client.get("/api/modules/not.exist/detail")
    check("detail 未知模块 404", r.status_code == 404, f"status={r.status_code}")

    # ── 3. 干预拦截分支（不执行真转发，避免副作用）──
    print("== 3. 干预拦截分支 ==")
    r = client.post("/api/modules/action.tools/actions", json={"action": ""})
    check("空 action -> 400", r.status_code == 400, f"status={r.status_code}")

    r = client.post("/api/modules/action.tools/actions", json={"action": "not_a_real_action"})
    check("未声明动作 -> 400", r.status_code == 400, f"status={r.status_code}")

    r = client.post("/api/modules/action.llm/actions", json={"action": "reconfigure_llm", "params": {}})
    check("高危无 reason -> 400", r.status_code == 400, f"status={r.status_code}")

    r = client.post("/api/modules/not.exist/actions", json={"action": "toggle_tool", "reason": "x"})
    check("未知模块 -> 404", r.status_code == 404, f"status={r.status_code}")

    print(f"\n结果: {passed} passed / {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
