"""模块注册表自校验守卫（2026-10-03 · 审计 H-2 的机制化修复 / 成功指标 K1）。

【解决什么】agent/modules_registry.py 是一份**手写声明**：6 域 32 节点 + 23 条动作，
每个节点用 status_source 声明"状态从哪取"、每条动作声明"打哪个 method+url"。
声明与事实一旦偏离，**不会有任何请求失败** —— 只会让拓扑图说谎：
  · status_source 指向不存在的端点 ⇒ 该节点状态永远显示"未知"；
  · ACTION_ROUTES 的 url/method 不对 ⇒ 前端渲染出的按钮点了 404/405；
  · node.path 指向不存在的文件 ⇒ 详情面板跳转到空处。
审计 H-2 就是第三种形态（service.gateway 声明了一个不存在的 api_gateway_flask.py），
而它被发现靠的是**三个月后的一次人工审计**，不是任何机制。

【两把尺子，缺一不完整】
  · 静态（CI · scripts/audit/contract_diff.py）：AST/正则扫 3 个注册面，
    已有的三类判定 registry_status_source_missing / registry_path_missing /
    registry_action_url_missing 都在那里；
  · 运行期（本文件 + app_server.audit_modules_registry）：用**真实 url_map**，
    任何静态看不到的注册方式（add_url_rule、运行期动态注册）都无处可藏。
本文件同时守"纯函数判据"与"今天的真实数据确实零漂移"（后者是 K1 的锚点）。

【纪律】新增节点/动作后请跑一次
    python scripts/audit/contract_diff.py
本文件会红在 test_当前真实路由集下零漂移 上，提示你去改正声明。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

from agent.modules_registry import (  # noqa: E402
    ACTION_ROUTES,
    DOMAINS,
    declared_http_endpoints,
    validate_against_url_map,
)


def rules(*items):
    """模拟 app.url_map.iter_rules() 的 (rule, methods) 迭代器。"""
    return list(items)


FIXTURE = rules(
    ("/api/sensors", ("GET", "HEAD", "OPTIONS")),
    ("/api/panorama", ("GET", "HEAD", "OPTIONS")),
    ("/api/knowledge/cards", ("GET", "HEAD", "OPTIONS")),
    ("/api/knowledge/graph", ("GET", "HEAD", "OPTIONS")),
    ("/api/tools/toggle", ("POST", "OPTIONS")),
    ("/api/permission/emergency", ("POST", "OPTIONS")),
)


class TestDeclaredEndpoints:
    def test_覆盖节点与动作两类来源(self):
        items = declared_http_endpoints()
        sources = {i["source"].split(":")[0] for i in items}
        assert sources == {"node", "action"}, sources

    def test_只收_api_前缀的_status_source(self):
        """config: 类的 status_source 不是 HTTP 端点，不得进入校验（否则恒假警报）。"""
        for item in declared_http_endpoints():
            assert not item["path"].startswith("config:"), item

    def test_动作方法被规范化成大写(self):
        for item in declared_http_endpoints():
            if item["source"].startswith("action:"):
                assert item["method"] == item["method"].upper()

    def test_条数与注册表规模一致(self):
        items = declared_http_endpoints()
        n_nodes_api = sum(
            1 for d in DOMAINS for n in d.nodes
            if (n.status_source or "").strip().startswith("api:")
        )
        assert len(items) == n_nodes_api + len(ACTION_ROUTES)


class TestValidate:
    def test_全部命中时返回空(self):
        """用只含被测声明的夹具：把 53 条真实声明过滤到夹具覆盖的那几条。"""
        covered = {"api:/api/sensors", "api:/api/panorama"}
        items = [i for i in declared_http_endpoints() if "api:" + i["path"] in covered]
        assert items, "夹具前提失效：找不到 /api/sensors 或 /api/panorama 的声明"
        # 直接对全量跑会报其余 51 条，故这里只验证"覆盖到的那几条确实命中"
        misses = validate_against_url_map(FIXTURE)
        for item in items:
            assert item["source"] + " " + item["method"] + " " + item["path"] not in misses

    def test_路径不存在即报(self):
        misses = validate_against_url_map(rules(("/api/other", ("GET",))))
        assert any("/api/sensors" in m for m in misses), misses

    def test_方法不符即报(self):
        """路径对、方法错同样要报 —— 前端点按钮会拿 405。"""
        misses = validate_against_url_map(rules(("/api/tools/toggle", ("GET",))))
        assert any("action:toggle_tool" in m for m in misses), misses

    def test_通配声明命中其下任一规则(self):
        """节点用 /api/knowledge/* 表达"这一片都归我"是合法声明，不得误报。"""
        misses = validate_against_url_map(rules(("/api/knowledge/cards", ("GET",))))
        assert not any("/api/knowledge/*" in m for m in misses), misses

    def test_通配声明在其下无规则时报(self):
        misses = validate_against_url_map(rules(("/api/other", ("GET",))))
        assert any("/api/knowledge/*" in m for m in misses), misses

    def test_空规则集下全部报(self):
        """证明判定不是恒空集（否则守卫是个空壳）。"""
        assert len(validate_against_url_map([])) == len(declared_http_endpoints())

    def test_返回已排序(self):
        misses = validate_against_url_map(rules(("/api/other", ("GET",))))
        assert misses == sorted(misses)


@pytest.fixture(scope="module")
def cd():
    """加载契约对拍工具（它复用同一套静态路由扫描，不重复实现）。"""
    path = ROOT / "scripts" / "audit" / "contract_diff.py"
    spec = importlib.util.spec_from_file_location("contract_diff_for_registry", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["contract_diff_for_registry"] = mod
    spec.loader.exec_module(mod)
    return mod


class Test当前真实路由集:
    """K1 锚点：今天的注册表必须**零漂移**（这是"100% 命中率"的可执行形式）。"""

    def test_当前真实路由集下零漂移(self, cd):
        routes = cd.collect_routes_static()
        assert routes, "静态路由扫描返回空集 —— 前提失效，本用例会变成空壳"
        rules = [(v["path"], v["methods"]) for v in routes.values()]
        misses = validate_against_url_map(rules)
        assert not misses, (
            "modules_registry 有 " + str(len(misses)) + " 条声明未命中真实路由：\n  "
            + "\n  ".join(misses)
            + "\n修法：改正声明，或补上缺失的路由；然后重跑 scripts/audit/contract_diff.py。"
        )

    def test_扫描规模足以支撑判定(self, cd):
        """静态扫描必须真的扫到了量级正确的路由集（否则"零漂移"可能是扫了个空集）。"""
        routes = cd.collect_routes_static()
        assert len(routes) > 300, "静态扫描只找到 " + str(len(routes)) + " 条路由，量级不对"
