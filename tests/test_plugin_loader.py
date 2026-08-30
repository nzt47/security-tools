"""plugins/loader.py 目录扫描自动加载 + 原子刷新单测（T4.1 动态装载）。

覆盖：
- load_all()：扫描 candidates、跳过 _ 开头与保留模块（plugin_api/services/loader），
  返回成功数；单插件损坏（运行期异常/语法错误）隔离不阻断整体；
- 幂等：reload_existing=False 时已导入模块跳过，不重复注册；
- refresh_manifest()：目录新增插件被刷新发现；已导入模块经 reload 重建
  （修改后以新注册为准）；失败（加载机制异常/重建为空）保留旧注册表并抛
  RuntimeError；
- register_blueprints(app)：增量挂载——新蓝图注册（路由即生效）、已注册跳过。

测试用临时插件包（monkeypatch loader.PLUGIN_DIR/PLUGIN_PACKAGE + sys.path），
不触碰真实 plugins/ 目录与真实插件注册表。
"""

import uuid

import pytest

from plugins import loader
from plugins import plugin_api as api

# 临时包内无相对 plugin_api，插件模板用绝对导入
_PLUGIN_TMPL = '''\
from flask import Blueprint
from plugins.plugin_api import Plugin, register_plugin

bp = Blueprint({bp_name!r}, __name__)

@bp.route({route!r})
def probe():
    from flask import jsonify
    return jsonify({{"plugin": {name!r}, "ok": True}})

PLUGIN = register_plugin(Plugin(
    name={name!r},
    version="1.0.0",
    description="loader test",
    blueprint=bp,
    routes=[{route!r}],
))
'''

_BROKEN_RUNTIME = 'raise RuntimeError("boom")\n'
_BROKEN_SYNTAX = "def broken(:\n    pass\n"


@pytest.fixture(autouse=True)
def _isolated_loader_state():
    """每个用例前后保存/恢复注册表与已挂载蓝图集合，避免污染真实插件状态。"""
    saved_registry = list(api._REGISTRY)
    saved_blueprints = set(loader._loaded_blueprints)
    api._REGISTRY.clear()
    loader._loaded_blueprints.clear()
    yield
    api._REGISTRY[:] = saved_registry
    loader._loaded_blueprints.clear()
    loader._loaded_blueprints.update(saved_blueprints)


@pytest.fixture
def pkg(tmp_path, monkeypatch):
    """建一个临时插件包并让 loader 指向它；返回临时包目录。"""
    pkg_name = f"pkg_{uuid.uuid4().hex[:8]}"
    d = tmp_path / pkg_name
    d.mkdir()
    (d / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(loader, "PLUGIN_DIR", d)
    monkeypatch.setattr(loader, "PLUGIN_PACKAGE", pkg_name)
    monkeypatch.syspath_prepend(str(tmp_path))
    return d


def _write_module(d, name, body):
    (d / f"{name}.py").write_text(body, encoding="utf-8")


def _plug(name, route=None):
    return _PLUGIN_TMPL.format(
        bp_name=f"bp_{name}", name=name, route=route or f"/api/loader/{name}"
    )


# ════════════════════════════════════════════════════════════════
#  load_all()：扫描 + 跳过规则 + 成功数
# ════════════════════════════════════════════════════════════════

def test_load_all_imports_candidates_and_skips_reserved(pkg):
    _write_module(pkg, "ok_a", _plug("a"))
    _write_module(pkg, "ok_b", _plug("b"))
    # 跳过：_ 开头 + 保留模块（plugin_api / services / loader）
    _write_module(pkg, "_skipme", _plug("skip"))
    _write_module(pkg, "plugin_api", _plug("pa"))
    _write_module(pkg, "services", _plug("svc"))
    _write_module(pkg, "loader", _plug("ld"))

    assert loader.load_all() == 2
    assert [p.name for p in api._REGISTRY] == ["a", "b"]


def test_load_all_isolates_broken_modules(pkg):
    _write_module(pkg, "ok_a", _plug("a"))
    _write_module(pkg, "broken_runtime", _BROKEN_RUNTIME)
    _write_module(pkg, "broken_syntax", _BROKEN_SYNTAX)

    # 损坏插件仅记日志，不阻断整体，返回成功数 = 1
    assert loader.load_all() == 1
    assert [p.name for p in api._REGISTRY] == ["a"]


def test_load_all_idempotent_without_reload(pkg):
    _write_module(pkg, "ok_a", _plug("a"))
    assert loader.load_all() == 1
    # 已导入模块幂等跳过：不重复执行模块级代码、不重复注册
    assert loader.load_all() == 0
    assert [p.name for p in api._REGISTRY] == ["a"]


# ════════════════════════════════════════════════════════════════
#  refresh_manifest()：发现新插件 / reload 重建 / 原子性
# ════════════════════════════════════════════════════════════════

def test_refresh_manifest_discovers_new_plugin(pkg):
    _write_module(pkg, "ok_a", _plug("a"))
    assert loader.load_all() == 1

    # 目录新增插件，无需改任何代码，reload 后可发现
    _write_module(pkg, "ok_b", _plug("b"))
    m = loader.refresh_manifest()
    assert {p["name"] for p in m["plugins"]} == {"a", "b"}
    assert {p.name for p in api._REGISTRY} == {"a", "b"}


def test_refresh_manifest_reloads_existing_module(pkg):
    _write_module(pkg, "ok_a", _plug("a"))
    assert loader.load_all() == 1

    # 修改已导入模块（换注册名），refresh 应对其 reload 并以新注册为准
    _write_module(pkg, "ok_a", _plug("a_v2", route="/api/loader/a_v2"))
    m = loader.refresh_manifest()
    assert {p["name"] for p in m["plugins"]} == {"a_v2"}
    assert {p.name for p in api._REGISTRY} == {"a_v2"}


def test_refresh_manifest_preserves_old_registry_when_load_fails(pkg, monkeypatch):
    _write_module(pkg, "ok_a", _plug("a"))
    assert loader.load_all() == 1

    def _boom(*, reload_existing=False):
        raise RuntimeError("scan machinery broke")

    monkeypatch.setattr(loader, "load_all", _boom)
    with pytest.raises(RuntimeError, match="刷新插件清单失败"):
        loader.refresh_manifest()
    # 旧注册表保留（先加载到临时注册表，成功才替换）
    assert [p.name for p in api._REGISTRY] == ["a"]


def test_refresh_manifest_guards_empty_rebuild(pkg, monkeypatch):
    _write_module(pkg, "ok_a", _plug("a"))
    assert loader.load_all() == 1

    def _noop(*, reload_existing=False):
        return 0  # 模拟「全部加载失败」→ 临时注册表为空

    monkeypatch.setattr(loader, "load_all", _noop)
    with pytest.raises(RuntimeError, match="刷新后插件注册表为空"):
        loader.refresh_manifest()
    assert [p.name for p in api._REGISTRY] == ["a"]


# ════════════════════════════════════════════════════════════════
#  register_blueprints(app)：增量挂载
# ════════════════════════════════════════════════════════════════

def test_register_blueprints_incremental(pkg):
    from flask import Flask

    _write_module(pkg, "ok_a", _plug("a"))
    assert loader.load_all() == 1

    app = Flask(__name__)
    app.config.update(TESTING=True)

    # 首次：新蓝图挂载，路由即生效
    assert loader.register_blueprints(app) == 1
    resp = app.test_client().get("/api/loader/a")
    assert resp.status_code == 200
    assert resp.get_json() == {"plugin": "a", "ok": True}

    # 再次：已注册跳过（Flask 已注册 blueprint 不可重复注册）
    assert loader.register_blueprints(app) == 0
