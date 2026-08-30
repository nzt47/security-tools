# plugins/loader.py
"""插件目录扫描自动加载器（任务 T4.1：动态装载）。

把阶段 1 的显式 import 清单扩展为「目录扫描自动加载」：
新插件丢进 plugins/ 即生效，无需改任何代码；单插件损坏不阻断整体。

职责：
- ``load_all()``：用 ``pkgutil.iter_modules`` 扫描 plugins/ 目录下所有 .py
  模块（跳过 ``_`` 开头与 plugin_api / services / loader 本身），逐个
  ``importlib`` 导入以触发模块级 ``register_plugin()``；单个失败记日志
  （print）不阻断；返回本调用成功加载的模块数。
- ``refresh_manifest()``：原子重建注册表（先加载到临时注册表，成功再替换
  ``_REGISTRY``），返回最新 manifest；任一步失败保留旧注册表并抛
  RuntimeError（由装配器端点转 500）。
- ``register_blueprints(app)``：把注册表中尚未挂载到 Flask app 的插件蓝图
  增量注册（启动装配时调用，插件路由即生效）。Flask 已注册 blueprint 不可
  注销、且首个请求后不可再追加（``register_blueprint`` 会被拒绝），因此
  新增/删除插件的路由生效/失效需重启进程——reload 只刷新 manifest。

已导入模块的处理（两种加载语义）：
- 启动装配（``reload_existing=False``）：plugins/__init__.py 显式清单已把
  「内置插件」导入注册，loader 对已导入模块幂等跳过（``register_plugin``
  幂等 + 此处不重复执行模块级代码），只补扫显式清单之外的插件；
- 刷新（``reload_existing=True``）：对已导入模块执行 ``importlib.reload``
  重新运行模块级 ``register_plugin()``，从而在临时注册表里重建完整清单。

模块级副作用说明：插件模块顶层仅含 Blueprint / 常量 / 轻量配置管理器，
reload 安全（无线程、无网络、无 app_server 顶层引用；循环导入红线不变）。
"""
from __future__ import annotations

import importlib
import pkgutil
import sys
import threading
from pathlib import Path
from typing import Any, Dict, Iterator, List

from .plugin_api import Plugin, register_plugin

# 扫描目录 / 导入用包名（测试可 monkeypatch 指向临时包）
PLUGIN_DIR = Path(__file__).parent
PLUGIN_PACKAGE = __package__ or "plugins"

# 保留模块：协议层 / 服务层 / 装配器自身不参与扫描
_SKIP_MODULES = {"plugin_api", "services", "loader"}

# 刷新/注册的并发互斥（防并发 reload 与增量蓝图注册交错）
_lock = threading.Lock()
# 已挂载到 Flask app 的 blueprint name（进程内；同名不可重复注册）
_loaded_blueprints: set = set()


def _candidate_modules() -> Iterator[str]:
    """产出待加载模块名：扫描 plugins/ 目录，跳过 _ 开头与保留模块。"""
    for mod in pkgutil.iter_modules([str(PLUGIN_DIR)]):
        if mod.name.startswith("_") or mod.name in _SKIP_MODULES:
            continue
        yield mod.name


def _full_name(name: str) -> str:
    return f"{PLUGIN_PACKAGE}.{name}"


def _import_module(name: str):
    """导入插件模块触发 register_plugin；已导入则 reload 重新执行。"""
    full = _full_name(name)
    if full in sys.modules:
        return importlib.reload(sys.modules[full])
    return importlib.import_module(full)


def load_all(*, reload_existing: bool = False) -> int:
    """扫描 plugins/ 目录并加载所有插件模块。

    Args:
        reload_existing: True 时对已在 sys.modules 的模块执行 importlib.reload
            （refresh_manifest 重建注册表需要）；False 时已导入模块幂等跳过
            （内置插件显式清单路径，避免重复执行模块级副作用）。

    Returns:
        本调用成功加载的模块数。单插件失败仅记日志，不阻断其余模块。
    """
    loaded = 0
    for name in _candidate_modules():
        full = _full_name(name)
        try:
            if full in sys.modules and not reload_existing:
                continue  # 已导入（显式清单/之前加载），幂等跳过
            _import_module(name)
            loaded += 1
        except Exception as exc:  # noqa: BLE001 - 单插件失败隔离，不阻断整体
            print(f"[plugins] 加载 {name} 失败: {exc}")
    return loaded


def refresh_manifest() -> Dict[str, Any]:
    """原子重建插件注册表并返回最新 manifest。

    - 先切换到临时注册表再扫描（load_all(reload_existing=True)），全部成功
      才保留新注册表；任一步失败恢复旧注册表并抛 RuntimeError；
    - 防御：重建后注册表为空（且旧注册表非空）视为整体失败，避免「先清空
      导致失败后无插件」。
    """
    from . import plugin_api as api
    from .plugin_api import manifest as _manifest

    with _lock:
        old_registry = api._REGISTRY
        fresh: List[Plugin] = []
        api._REGISTRY = fresh
        try:
            load_all(reload_existing=True)
            if not fresh and old_registry:
                raise RuntimeError("刷新后插件注册表为空（疑似全部加载失败）")
            return _manifest()
        except Exception as exc:
            api._REGISTRY = old_registry
            raise RuntimeError(f"刷新插件清单失败（旧注册表已保留）: {exc}") from exc


def register_blueprints(app) -> int:
    """把注册表中尚未挂载到 Flask app 的插件蓝图增量注册（新插件路由即生效）。

    已注册 blueprint 不可注销（Flask 限制），删除插件需重启进程生效；
    单个蓝图注册失败（如路由冲突）记日志跳过，不影响其余插件。
    """
    from .plugin_api import get_plugins

    registered = 0
    with _lock:
        for p in get_plugins():
            if p.blueprint is None:
                continue
            bp_name = p.blueprint.name
            if bp_name in _loaded_blueprints:
                continue
            try:
                app.register_blueprint(p.blueprint)
            except Exception as exc:  # noqa: BLE001 - 路由冲突/重复注册隔离
                print(f"[plugins] 注册蓝图 {bp_name} 失败: {exc}")
                continue
            _loaded_blueprints.add(bp_name)
            registered += 1
    return registered
