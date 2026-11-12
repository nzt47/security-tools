"""包自声明功能单位（`MODULE_UNITS`）的发现与登记（2026-11-12）

【判据】这条机制的价值全在"**声明即自动进看板**，且启动期零副作用"：
① 只做 AST 解析（不 import 被声明的包）；
② 声明格式非法 ⇒ 该条不登记并给原因（不静默吞掉）；
③ 只声明单位、不声明状态源的包**不会**因此进拓扑的"未登记"（两张清单互不污染）；
④ probe 是"调用时才 import"的字符串（写坏了不会拖垮启动）。
"""
from __future__ import annotations

import textwrap

import pytest

from agent import modules_discovery
from agent.monitoring import declared_units, unit_monitor


@pytest.fixture(autouse=True)
def _clean_registry():
    unit_monitor.reset_registry()
    modules_discovery.clear_cache()
    yield
    unit_monitor.reset_registry()
    modules_discovery.clear_cache()


def _make_pkg(root, name: str, source: str):
    pkg = root / name
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(textwrap.dedent(source), encoding="utf-8")
    return pkg


def test_scan_parses_module_units(tmp_path):
    _make_pkg(tmp_path, "demo", '''
        MODULE_UNITS = [
            {"id": "demo.one", "label": "演示一", "family": "ops",
             "probe": "agent.monitoring.port_probe:unit_probe",
             "description": "一句话"},
        ]
    ''')
    result = modules_discovery.scan(tmp_path, use_cache=False)
    assert [u.unit_id for u in result.units] == ["demo.one"]
    unit = result.units[0]
    assert unit.package == "demo"
    assert unit.label == "演示一"
    assert unit.family == "ops"
    assert unit.probe.endswith(":unit_probe")


def test_single_module_unit_dict_is_accepted(tmp_path):
    _make_pkg(tmp_path, "demo2", '''
        MODULE_UNIT = {"id": "demo2.only", "label": "单个",
                       "probe": "agent.monitoring.log_anomaly:unit_probe"}
    ''')
    result = modules_discovery.scan(tmp_path, use_cache=False)
    assert [u.unit_id for u in result.units] == ["demo2.only"]
    assert result.units[0].family == "module"          # 缺省族


def test_invalid_declarations_are_reported_not_swallowed(tmp_path):
    _make_pkg(tmp_path, "bad", '''
        MODULE_UNITS = [
            {"id": "bad.no_probe", "label": "x"},
            {"id": "bad.bad_probe", "label": "y", "probe": "no_colon_here"},
        ]
    ''')
    result = modules_discovery.scan(tmp_path, use_cache=False)
    assert result.units == ()
    reasons = [item["reason"] for item in result.invalid_units]
    assert len(reasons) == 2
    assert any("probe" in r for r in reasons)


def test_unit_only_package_does_not_pollute_topology_buckets(tmp_path):
    """只声明 MODULE_UNITS、不声明状态源的包：不进「已登记」也不进「未登记」。"""
    _make_pkg(tmp_path, "uonly", '''
        MODULE_UNITS = [{"id": "uonly.u", "probe": "agent.monitoring.port_probe:unit_probe"}]
    ''')
    result = modules_discovery.scan(tmp_path, use_cache=False)
    assert result.declared == ()
    assert result.unregistered == ()


def test_register_declared_units_builds_units_and_is_idempotent():
    n = declared_units.register_declared_units()
    assert n >= 3
    ids = {u.id for u in unit_monitor.registered()}
    assert {"perception.cycle", "alarms.collector", "ops.ports", "ops.logs"} <= ids
    # 幂等：再登记一次不会翻倍
    declared_units.register_declared_units()
    assert len(unit_monitor.registered()) == n


def test_declared_probe_resolves_lazily_and_returns_unit_shape():
    """probe 是字符串：真正调用时才 import；返回体是单位看板的形状。"""
    units = {u.id: u for u in declared_units.build_units()}
    out = units["ops.ports"].probe()
    assert set(out) >= {"status", "summary", "metrics", "issues", "last_activity"}
    assert out["status"] in unit_monitor.STATUSES


def test_declared_unit_ids_match_packages():
    ids = declared_units.declared_unit_ids()
    assert ids.count("ops.ports") == 1
    assert all(":" not in i for i in ids)
