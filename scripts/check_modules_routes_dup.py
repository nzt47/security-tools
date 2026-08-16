#!/usr/bin/env python3
"""检查 modules_registry.ACTION_ROUTES 中 24 个动作映射 URL 在路由层的重复定义

扫描 app_server.py 与 agent/server_routes/*.py 中的 @<bp>.route("url")
注册次数，输出重复定义清单（Flask 中同 URL 重复注册会覆盖或被 endpoint 冲突拦截）。

用法: python scripts/check_modules_routes_dup.py
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.modules_registry import ACTION_ROUTES  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ROUTE_FILES = [ROOT / "app_server.py", *sorted((ROOT / "agent" / "server_routes").glob("*.py"))]

# 匹配 @xxx.route("/path"[, methods=...]) 注册行
ROUTE_RE = re.compile(r'@(?:\w+\.)?route\(\s*(["\'])(.+?)\1', re.MULTILINE)


def main() -> int:
    url_to_actions: dict[str, list[str]] = {}
    for key, route in ACTION_ROUTES.items():
        url_to_actions.setdefault(route.url, []).append(key)

    # 统计每个 URL 在所有路由层的注册次数
    registrations: dict[str, list[tuple[str, int]]] = {}
    for fpath in ROUTE_FILES:
        if not fpath.exists():
            continue
        for lineno, line in enumerate(fpath.read_text(encoding="utf-8").splitlines(), 1):
            m = ROUTE_RE.search(line)
            if m:
                url = m.group(2)
                if url in url_to_actions:
                    registrations.setdefault(url, []).append((str(fpath.relative_to(ROOT)), lineno))

    dup = 0
    for url, actions in url_to_actions.items():
        regs = registrations.get(url, [])
        if len(regs) > 1:
            dup += 1
            print(f"[重复定义] {url} 注册 {len(regs)} 次 -> {regs}")
            print(f"           被动作引用: {actions}")
        elif len(regs) == 0:
            print(f"[未找到]   {url} 在路由层未发现注册行（可能定义在导入模块/装饰器非 route 形式）<- 动作: {actions}")
        else:
            print(f"[OK]       {url} 注册 1 次 @ {regs[0][0]}:{regs[0][1]} <- 动作: {actions}")

    total = len(url_to_actions)
    print(f"\n统计: 共 {total} 个 URL，重复定义 {dup} 个")
    return 1 if dup else 0


if __name__ == "__main__":
    sys.exit(main())
