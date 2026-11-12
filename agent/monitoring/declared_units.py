# -*- coding: utf-8 -*-
"""包自声明的功能单位（2026-11-12）：让「新功能自动进单位看板」有一条真机制。

【要解决什么（§11.94 C 节的缺口之一）】单位看板此前是**纯手写**：新功能上线要人
记得去 `unit_probes._all()` 加一个 probe + `register(Unit(...))` 一行，忘了就永远
不进看板。拓扑已经有「包自己声明」的先例（`MODULE_STATUS_SOURCE`），这里复用**同一处**
声明文件（`agent/<pkg>/__init__.py`），不新造第二套元数据格式：

    MODULE_UNITS = [
        {"id": "perception.cycle", "label": "感知周期采集", "family": "perception",
         "probe": "agent.learning.perception_cycle:unit_probe",
         "description": "文件事件 drain / 快照 diff / 状态落盘"},
    ]

【三条纪律（别在这里破）】
1. **只做 AST 扫描**（复用 `agent/modules_discovery.py`）：启动期不 import 被声明的包；
   `probe` 是"调用时才 import"的字符串 ⇒ 新包写坏了也不会拖垮启动/看板。
2. **声明本身不改健康分分母**：probe 返回 `never`（从未产生数据）时单位不计分；
   只有真的给出 ok/warn/error 读数才进分子/分母（与手写的 12 个单位同一条口径）。
3. **probe 必须只读、无创建副作用**：探针每 60s 被报警采集线程调一次，任何
   get_scheduler()/get_singleton() 都会把"看一眼"变成"建一个"（§11.93 / §11.95 两次实证）。

【失败口径】声明格式不合法（缺 id / probe 不是 '模块:函数'）⇒ 该条**不登记**并记 warning；
读不到包目录/解析失败 ⇒ 返回空列表（看板照常显示手写的 12 个，不报假单位）。
"""
from __future__ import annotations

import logging
from typing import Callable, Dict, List, Optional

from agent import modules_discovery
from agent.monitoring.unit_monitor import Unit, register

logger = logging.getLogger(__name__)

__version__ = "1.0.0"


def _probe_factory(target: str) -> Callable[[], Dict[str, object]]:
    """把 "模块路径:函数名" 变成 probe 可调用（**调用时才 import**）。"""

    def _probe() -> Dict[str, object]:
        module_name, _, attr = target.partition(":")
        import importlib

        module = importlib.import_module(module_name.strip())
        fn = getattr(module, attr.strip())
        return fn()

    _probe.__name__ = "declared_probe_" + target.replace(":", "_").replace(".", "_")
    return _probe


def build_units(result: Optional[modules_discovery.DiscoveryResult] = None) -> List[Unit]:
    """把发现结果里的单位声明转成 `Unit` 列表（不登记，纯构造）。"""
    res = result if result is not None else modules_discovery.scan()
    units: List[Unit] = []
    for decl in res.units:
        units.append(Unit(
            id=decl.unit_id,
            name=decl.label,
            family=decl.family,
            probe=_probe_factory(decl.probe),
            description=decl.description or ("由 agent/" + decl.package + "/ 声明"),
        ))
    for bad in res.invalid_units:
        logger.warning("单位声明被忽略：%s（%s）", bad.get("package"), bad.get("reason"))
    return units


def register_declared_units(result: Optional[modules_discovery.DiscoveryResult] = None) -> int:
    """登记全部包声明单位（幂等；同名 id 后写覆盖）。返回登记条数。"""
    units = build_units(result)
    for unit in units:
        register(unit)
    if units:
        logger.info("[DeclaredUnits] 已从包声明登记 %d 个单位：%s",
                    len(units), ", ".join(u.id for u in units))
    return len(units)


def declared_unit_ids(result: Optional[modules_discovery.DiscoveryResult] = None) -> List[str]:
    """当前声明了哪些单位 id（测试/诊断用；不登记）。"""
    return [u.id for u in build_units(result)]


__all__: List[str] = [
    "build_units", "register_declared_units", "declared_unit_ids",
]
