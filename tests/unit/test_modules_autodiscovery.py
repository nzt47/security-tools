# -*- coding: utf-8 -*-
"""模块自动发现（2026-11-11）：白名单 + 「未登记」单列的行为测试。

【要钉住的四条口径】
  1. **白名单**：只有声明了 `MODULE_STATUS_SOURCE` 的包才生成节点；只声明了名字/描述的包
     进「未登记」，**不生成节点、不参与健康分**（L5：没状态源不是 0 分，也不是"不存在"）；
  2. **不 import 被扫的包**：只做 AST 解析（导入 = 执行 __init__ = 启动期副作用/循环依赖）；
  3. **不重复上图**：手写 DOMAINS 已经点到过的包，即使自己再声明一次也只算一个节点；
  4. **开关**：`CP_MODULE_AUTODISCOVERY=0` 完全退回"只有手写清单"的世界。
"""
from __future__ import annotations

import textwrap

import pytest

from agent import modules_discovery as disc
from agent import modules_registry as reg


# ════════════════════════════════════════════════════════════
#  AST 解析
# ════════════════════════════════════════════════════════════

class TestParseDeclarations:
    def test_只认模块级字面量(self):
        decls = disc.parse_declarations(textwrap.dedent('''
            MODULE_STATUS_SOURCE = "api:/api/x/status"
            MODULE_NAME = "X 服务"
            MODULE_METRICS = ["a", "b"]
            NOT_A_DECL = "ignored"
        '''))
        assert decls["MODULE_STATUS_SOURCE"] == "api:/api/x/status"
        assert decls["MODULE_NAME"] == "X 服务"
        assert decls["MODULE_METRICS"] == ["a", "b"]
        assert "NOT_A_DECL" not in decls

    def test_带注解的赋值也认(self):
        decls = disc.parse_declarations('MODULE_STATUS_SOURCE: str = "api:/api/y"\n')
        assert decls["MODULE_STATUS_SOURCE"] == "api:/api/y"

    def test_动态值不算声明(self):
        """`= os.environ[...]` 之类不是字面量 ⇒ 当作没声明（不能靠运行期值上图）。"""
        decls = disc.parse_declarations("import os\nMODULE_STATUS_SOURCE = os.environ['X']\n")
        assert "MODULE_STATUS_SOURCE" not in decls

    def test_函数里的同名变量不算声明(self):
        decls = disc.parse_declarations(textwrap.dedent('''
            def f():
                MODULE_STATUS_SOURCE = "api:/api/inside"
        '''))
        assert decls == {}

    def test_语法错误不抛(self):
        assert disc.parse_declarations("def broken(:") == {}


# ════════════════════════════════════════════════════════════
#  扫描：白名单 / 未登记 / 忽略
# ════════════════════════════════════════════════════════════

def _pkg(root, name, body=""):
    pkg = root / name
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text(body, encoding="utf-8")
    return pkg


class TestScan:
    def test_三类结果各归各的桶(self, tmp_path):
        _pkg(tmp_path, "declared", 'MODULE_STATUS_SOURCE = "api:/api/a"\nMODULE_DOMAIN = "ops"\n')
        _pkg(tmp_path, "named_only", 'MODULE_NAME = "只有名字"\n')
        _pkg(tmp_path, "plumbing", "")            # 工具包：不是模块候选
        (tmp_path / "no_init").mkdir()            # 没有 __init__.py：不是包
        result = disc.scan(tmp_path, use_cache=False)
        assert [m.package for m in result.declared] == ["declared"]
        assert [u["package"] for u in result.unregistered] == ["agent/named_only/"]
        assert result.scanned_packages == 3, "只扫有 __init__.py 的包目录"

    def test_域名写错也归未登记并说明原因(self, tmp_path):
        _pkg(tmp_path, "typo", 'MODULE_STATUS_SOURCE = "api:/api/a"\nMODULE_DOMAIN = "opsx"\n')
        result = disc.scan(tmp_path, use_cache=False)
        assert not result.declared
        assert "不是六域之一" in result.unregistered[0]["reason"]

    def test_缺省域名落在服务层(self, tmp_path):
        _pkg(tmp_path, "nodefault", 'MODULE_STATUS_SOURCE = "api:/api/a"\n')
        result = disc.scan(tmp_path, use_cache=False)
        assert result.declared[0].domain_id == disc.DEFAULT_DOMAIN_ID == "service"

    def test_扫描不执行包代码(self, tmp_path):
        """包里写 `raise`：真 import 会炸，AST 扫描必须安然无恙。"""
        _pkg(tmp_path, "explodes", 'raise RuntimeError("imported!")\n')
        disc.scan(tmp_path, use_cache=False)  # 不抛即通过

    def test_默认根扫到三个已声明的真实包(self):
        """哨兵：本仓现在有 3 个包自声明（删掉声明行不会报错，但这条会红）。"""
        result = disc.scan(use_cache=False)
        packages = {m.package for m in result.declared}
        assert {"workflow_learning", "ingestion", "observability"} <= packages, packages
        for module in result.declared:
            assert module.status_source.startswith(("api:", "config:"))
            assert module.domain_id in disc.DOMAIN_IDS


# ════════════════════════════════════════════════════════════
#  并入拓扑：effective_domains / 自检 / 开关
# ════════════════════════════════════════════════════════════

def _fake_result(*modules):
    return disc.DiscoveryResult(
        declared=tuple(modules),
        unregistered=({"package": "agent/half/", "name": "half", "reason": disc.REASON_NO_STATUS_SOURCE},),
        scanned_packages=2,
        root="<fixture>",
    )


def _module(package="demo", domain_id="ops", source="api:/api/demo/status"):
    return disc.DiscoveredModule(
        package=package,
        display_path="agent/" + package + "/",
        module_id=disc.MODULE_ID_PREFIX + package,
        name=package,
        description="",
        domain_id=domain_id,
        status_source=source,
        has_status_source=True,
    )


class TestEffectiveDomains:
    def test_发现节点并入对应域且带_origin(self, monkeypatch):
        monkeypatch.setattr(reg, "_discovery_result", lambda: _fake_result(_module()))
        domains = reg.effective_domains()
        ops = next(d for d in domains if d.domain_id == "ops")
        node = next(n for n in ops.nodes if n.module_id == "discovered.demo")
        assert node.origin == "discovered"
        assert node.status_source == "api:/api/demo/status"
        # 手写节点仍是 declared
        assert all(n.origin == "declared" for d in reg.DOMAINS for n in d.nodes)

    def test_不污染_DOMAINS(self, monkeypatch):
        before = sum(len(d.nodes) for d in reg.DOMAINS)
        monkeypatch.setattr(reg, "_discovery_result", lambda: _fake_result(_module()))
        reg.effective_domains()
        assert sum(len(d.nodes) for d in reg.DOMAINS) == before, "运行期不得改写声明源"

    def test_手写清单已覆盖的包不重复上图(self, monkeypatch):
        """`memory` 已被 memory.manager 等手写节点点到 ⇒ 再声明一次也只算手写那一个。"""
        monkeypatch.setattr(reg, "_discovery_result", lambda: _fake_result(_module(package="memory")))
        ids = [n.module_id for d in reg.effective_domains() for n in d.nodes]
        assert "discovered.memory" not in ids
        assert "memory.manager" in ids

    def test_未登记不生成节点(self, monkeypatch):
        monkeypatch.setattr(reg, "_discovery_result", lambda: _fake_result())
        ids = [n.module_id for d in reg.effective_domains() for n in d.nodes]
        assert not [i for i in ids if i.startswith(disc.MODULE_ID_PREFIX)]
        assert reg.unregistered_modules()[0]["package"] == "agent/half/"

    def test_开关关闭退回纯手写清单(self, monkeypatch):
        # 注意：这里**不能**替换 _discovery_result —— 开关判断就在它里面，
        # 替换掉等于把被测的分支一起换走了（那样测的是夹具，不是代码）。
        monkeypatch.setenv(disc.ENV_FLAG, "0")
        assert reg.effective_domains() == reg.DOMAINS
        assert reg.unregistered_modules() == []
        assert reg.summary()["autodiscovery"]["discovered"] == 0

    def test_开关打开时真实扫描并入三个包(self, monkeypatch):
        monkeypatch.delenv(disc.ENV_FLAG, raising=False)
        ids = [n.module_id for d in reg.effective_domains() for n in d.nodes]
        assert "discovered.workflow_learning" in ids
        assert "discovered.ingestion" in ids
        assert "discovered.observability" in ids

    def test_自检覆盖发现节点的状态源(self, monkeypatch):
        """url_map 自检必须把「包自己声明的状态源」一起校验，否则自动发现=绕过契约对拍。"""
        monkeypatch.setattr(reg, "_discovery_result",
                            lambda: _fake_result(_module(source="api:/api/demo/status")))
        paths = {(i["source"], i["path"]) for i in reg.declared_http_endpoints()}
        assert ("node:discovered.demo", "/api/demo/status") in paths

    def test_发现节点可被详情端点取到(self, monkeypatch):
        monkeypatch.setattr(reg, "_discovery_result", lambda: _fake_result(_module()))
        assert reg.get_node("discovered.demo") is not None

    def test_读数与注册表概览同源(self, monkeypatch):
        monkeypatch.setattr(reg, "_discovery_result", lambda: _fake_result(_module()))
        summary = reg.summary()
        assert summary["autodiscovery"]["discovered"] == 1
        assert summary["autodiscovery"]["unregistered"] == 1


# ════════════════════════════════════════════════════════════
#  端点行为（/api/modules/topology）
# ════════════════════════════════════════════════════════════

class TestTopologyEndpoint:
    @staticmethod
    def _client(monkeypatch, *modules):
        from flask import Flask

        import agent.modules_api as ma
        monkeypatch.setattr(reg, "_discovery_result", lambda: _fake_result(*modules))
        monkeypatch.setattr(ma, "_api_token_provider", None)
        app = Flask(__name__)
        app.register_blueprint(ma.modules_bp)
        return app.test_client()

    def test_发现节点上图且带_origin_与未登记单列(self, monkeypatch):
        client = self._client(monkeypatch, _module())
        body = client.get("/api/modules/topology").get_json()["data"]
        ids = [n["module_id"] for d in body["domains"] for n in d["nodes"]]
        assert "discovered.demo" in ids
        node = next(n for d in body["domains"] for n in d["nodes"] if n["module_id"] == "discovered.demo")
        assert node["origin"] == "discovered"
        assert body["autodiscovery"]["discovered"] == 1
        assert body["unregistered"][0]["package"] == "agent/half/"
        # 未登记的包没有变成节点（不参与健康分）
        assert "discovered.half" not in ids

    def test_未登记不进健康分平均(self, monkeypatch):
        """unregistered 只在旁路列表里，**不是节点** ⇒ 不参与 overall_health 的平均。

        判据取"它没有变成节点"：健康分只对 domains[].nodes 逐个采集后求平均，
        没有节点的东西在数学上不可能进入平均（这里同时钉住"别为了方便把它塞进 nodes"）。
        """
        client = self._client(monkeypatch, _module())
        body = client.get("/api/modules/topology").get_json()["data"]
        ids = [n["module_id"] for d in body["domains"] for n in d["nodes"]]
        assert "discovered.half" not in ids
        assert body["autodiscovery"]["unregistered"] == 1
        assert body["unregistered"][0]["package"] == "agent/half/"
        # 发现节点（有状态源）才是节点
        assert "discovered.demo" in ids
