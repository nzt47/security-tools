# -*- coding: utf-8 -*-
"""``agent/monitoring/coverage_gaps.py`` —— 覆盖缺口自检（2026-11-12）。

【这组用例钉什么】
1. 六类缺口的判据各自**能命中**（新包缺状态源 / 单位 probe 打不开 / 传感器缺 collect /
   周期注册函数没人调用 / 路由前缀命中不到 / 索引指向不存在的工具）；
2. **全部覆盖 ⇒ 0 gap**（判据不能为了"看起来有效"而永远报点什么）；
3. 外发去重：首现推一次 → 第二轮不推 → 缺口消失后再现**再推一次**；推送失败不记指纹（下轮重试）；
4. 读数口径：状态文件缺失 ⇒ available=false / 单位 never（不折算成 0 条）；停摆 ⇒ warn。

【纪律】所有用例都显式给 tmp 路径（``root=`` / ``state=``）：默认值就是生产路径，
不给就等于让单测去扫真仓库、写真实状态文件（本仓 §11.95 的"默认值=生产路径"教训）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.monitoring import coverage_gaps as cg


# ════════════════════════════════════════════════════════════
#  夹具：一棵最小的"仓库"
# ════════════════════════════════════════════════════════════

def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _covered_pkg(root: Path) -> None:
    """一个**完全被覆盖**的包：有状态源（落在路由前缀内）+ 一个能解析的单位。"""
    _write(root / "agent" / "good" / "__init__.py", '''
MODULE_NAME = "好包"
MODULE_DOMAIN = "ops"
MODULE_STATUS_SOURCE = "api:/api/good/health"
MODULE_UNITS = [{"id": "good.unit", "label": "好单位", "family": "ops",
                 "probe": "agent.good.probe:unit_probe"}]
''')
    _write(root / "agent" / "good" / "probe.py", '''
def unit_probe():
    return {"status": "ok", "summary": "ok"}
''')


def _repo(tmp_path: Path) -> Path:
    """一棵"一切都被覆盖"的最小仓库（各用例只破坏其中一处）。"""
    root = tmp_path / "repo"
    _covered_pkg(root)
    # 传感器：一个合规的 + 一个非传感器文件（不该被算成缺口）
    _write(root / "sensor" / "cpu_sensor.py", '''
class CpuSensor:
    def collect(self):
        return None
''')
    _write(root / "sensor" / "engine.py", '''
class TTSEngine:
    def speak(self, text):
        return text
''')
    # 周期注册函数：app_server 里调用点齐全
    _write(root / "agent" / "sched" / "__init__.py", "")
    _write(root / "agent" / "sched" / "mod.py", '''
def register_demo_scheduler(scheduler=None):
    return {"status": "scheduled"}
''')
    _write(root / "app_server.py", '''
from agent.sched.mod import register_demo_scheduler

def init():
    return register_demo_scheduler()
''')
    # 路由表 + 活体路由报告（前缀命中；通配前缀 ``/*`` 由专门的用例覆盖）
    _write(root / "config" / "route_domains.json", json.dumps({
        "schema_version": 1,
        "domains": [{"domain": "观测与诊断", "prefixes": ["/api/good"]}],
    }, ensure_ascii=False))
    _write(root / "reports" / "route_ui_coverage.json", json.dumps({
        "entries": [{"path": "/api/good/health"}, {"path": "/"}],
    }, ensure_ascii=False))
    # 工具索引 ⇄ YAML 一一对应
    _write(root / "data" / "tool_index.json", json.dumps({"tools": [{"name": "demo_tool"}]}))
    _write(root / "data" / "tool_definitions" / "demo_tool.yaml", "name: demo_tool\n")
    # 规则文件（能读到即可，不做判定）
    _write(root / "monitoring" / "alerts.yml", "groups: []\n")
    return root


def _kinds(report) -> set:
    return {g["kind"] for g in report["gaps"]}


def _ids(report) -> set:
    return {g["id"] for g in report["gaps"]}


# ════════════════════════════════════════════════════════════
#  判据：能命中 / 全绿时为 0
# ════════════════════════════════════════════════════════════

def test_fully_covered_repo_has_zero_gaps(tmp_path):
    root = _repo(tmp_path)
    report = cg.collect(root=root)
    assert report["count"] == 0, report["gaps"]
    assert report["by_kind"] == {}
    assert report["truncated"] is False
    assert report["degraded"] is False
    for surface in ("packages", "units", "sensors", "scheduler", "routes", "tools", "rules"):
        assert surface in report["surfaces"], surface


def test_unregistered_package_is_a_gap(tmp_path):
    """只写了名字、没有状态源也没有单位 ⇒ 拓扑不上图 + 看板不探测 = 缺口。"""
    root = _repo(tmp_path)
    _write(root / "agent" / "lonely" / "__init__.py", 'MODULE_NAME = "孤儿能力"\n')
    report = cg.collect(root=root)
    assert "package" in _kinds(report)
    gap = next(g for g in report["gaps"] if g["kind"] == "package")
    assert gap["id"] == "agent/lonely/"
    assert "MODULE_STATUS_SOURCE" in gap["fix"]
    assert set(gap) == {"kind", "id", "why", "fix"}


def test_unit_only_package_is_not_a_gap(tmp_path):
    """只声明单位（不声明状态源）的包有监控路径 ⇒ 不算缺口（与 modules_discovery 同口径）。"""
    root = _repo(tmp_path)
    _write(root / "agent" / "unitonly" / "__init__.py", '''
MODULE_NAME = "只有单位"
MODULE_UNITS = [{"id": "uonly.u", "probe": "agent.good.probe:unit_probe"}]
''')
    report = cg.collect(root=root)
    assert "package" not in _kinds(report)


def test_status_source_outside_route_table_is_a_gap(tmp_path):
    """状态源路径不在路由前缀里 ⇒ 节点永远离线（写错了地址，不是"没数据"）。"""
    root = _repo(tmp_path)
    _write(root / "agent" / "wrongsrc" / "__init__.py", '''
MODULE_NAME = "错地址"
MODULE_DOMAIN = "ops"
MODULE_STATUS_SOURCE = "api:/api/nowhere/health"
''')
    report = cg.collect(root=root)
    gap = next(g for g in report["gaps"] if g["id"] == "agent/wrongsrc/")
    assert "route_domains" in gap["why"]


def test_declared_unit_with_broken_probe_is_a_gap(tmp_path):
    root = _repo(tmp_path)
    _write(root / "agent" / "badunit" / "__init__.py", '''
MODULE_UNITS = [
    {"id": "badunit.missing_fn", "probe": "agent.good.probe:not_there"},
    {"id": "badunit.missing_mod", "probe": "agent.nowhere.probe:unit_probe"},
]
''')
    report = cg.collect(root=root)
    ids = _ids(report)
    assert "badunit.missing_fn" in ids
    assert "badunit.missing_mod" in ids
    gap = next(g for g in report["gaps"] if g["id"] == "badunit.missing_fn")
    assert gap["kind"] == "unit"
    assert "not_there" in gap["why"]


def test_malformed_unit_declaration_is_a_gap(tmp_path):
    root = _repo(tmp_path)
    _write(root / "agent" / "shapeless" / "__init__.py", '''
MODULE_UNITS = [{"id": "shapeless.no_probe", "label": "缺 probe"}]
''')
    report = cg.collect(root=root)
    assert "unit" in _kinds(report)
    gap = next(g for g in report["gaps"] if g["kind"] == "unit")
    assert "声明被忽略" in gap["why"]


def test_sensor_without_collect_is_a_gap(tmp_path):
    """``*Sensor`` 类没有 collect() ⇒ 自发现静默跳过 = 这一路感知不存在。"""
    root = _repo(tmp_path)
    _write(root / "sensor" / "ocr_sensor.py", '''
class OcrSensor:
    def capture_screen(self):
        return None
''')
    report = cg.collect(root=root)
    gap = next(g for g in report["gaps"] if g["kind"] == "sensor")
    assert gap["id"] == "sensor/ocr_sensor.py"
    assert "OcrSensor" in gap["why"]


def test_non_sensor_module_is_not_a_gap(tmp_path):
    """没有 ``*Sensor`` 类的文件（TTS 引擎之类）不归传感器检查管。"""
    root = _repo(tmp_path)
    _write(root / "sensor" / "voice_engine.py", '''
class TTSEngine:
    def speak(self, text):
        return text
''')
    report = cg.collect(root=root)
    assert "sensor" not in _kinds(report)


def test_scheduler_registration_without_call_site_is_a_gap(tmp_path):
    root = _repo(tmp_path)
    _write(root / "agent" / "forgot" / "mod.py", '''
def register_forgot_scheduler(scheduler=None):
    return {"status": "scheduled"}
''')
    report = cg.collect(root=root)
    gap = next(g for g in report["gaps"] if g["kind"] == "scheduler")
    assert gap["id"].endswith(":register_forgot_scheduler")
    assert not Path(gap["id"].split(":")[0]).is_absolute(), "缺口 id 必须是仓库相对路径"


def test_route_prefix_without_live_route_is_a_gap(tmp_path):
    root = _repo(tmp_path)
    _write(root / "config" / "route_domains.json", json.dumps({
        "domains": [{"domain": "对话与会话", "prefixes": ["/api/good", "/api/voice", "/*"]}],
    }, ensure_ascii=False))
    report = cg.collect(root=root)
    gap = next(g for g in report["gaps"] if g["kind"] == "route")
    assert gap["id"] == "/api/voice"


def test_tool_index_ghost_is_a_gap(tmp_path):
    root = _repo(tmp_path)
    _write(root / "data" / "tool_index.json",
           json.dumps({"tools": [{"name": "demo_tool"}, {"name": "ghost_tool"}]}))
    report = cg.collect(root=root)
    gap = next(g for g in report["gaps"] if g["kind"] == "tool")
    assert gap["id"] == "ghost_tool"


def test_yaml_not_in_index_is_not_a_gap(tmp_path):
    """反方向（YAML 有、索引没有）由 CI 的 sync_tool_index 守门，这里**不**报。"""
    root = _repo(tmp_path)
    _write(root / "data" / "tool_definitions" / "new_tool.yaml", "name: new_tool\n")
    report = cg.collect(root=root)
    assert "tool" not in _kinds(report)


def test_missing_artifacts_are_disclosed_not_faked(tmp_path):
    """报告/索引缺失 ⇒ surfaces 标 available:false（不是"0 缺口"）。"""
    root = _repo(tmp_path)
    (root / "reports" / "route_ui_coverage.json").unlink()
    (root / "data" / "tool_index.json").unlink()
    report = cg.collect(root=root)
    assert report["surfaces"]["routes"]["available"] is False
    assert report["surfaces"]["tools"]["available"] is False
    assert report["surfaces"]["routes"].get("reason")


def test_check_failure_marks_degraded(tmp_path, monkeypatch):
    """单条检查抛异常 ⇒ degraded=True（结果不完整，不许看起来像 0 缺口）。"""
    root = _repo(tmp_path)

    def _boom(_root):
        raise RuntimeError("boom")

    # CHECKS 在 import 时就绑定了函数对象 ⇒ 必须整体替换元组（替换函数名不起作用）
    monkeypatch.setattr(cg, "CHECKS", tuple(
        (name, _boom if name == "sensors" else fn) for name, fn in cg.CHECKS))
    report = cg.collect(root=root)
    assert report["degraded"] is True
    assert report["surfaces"]["sensors"]["ok"] is False


def test_prefix_matches_boundary_and_glob():
    assert cg._prefix_matches("/api/skills", "/api/skills") is True
    assert cg._prefix_matches("/api/skills", "/api/skills-mgmt") is False
    assert cg._prefix_matches("/api/skills/", "/api/skills/list") is True
    assert cg._prefix_matches("/*", "/anything/at/all") is True


# ════════════════════════════════════════════════════════════
#  一轮 refresh：落盘 + 首现外发 + 指纹去重
# ════════════════════════════════════════════════════════════

def _ok_sender(calls):
    def _send(alert, transition):
        calls.append((alert, transition))
        return {"ok": True, "sent": ["ch1"], "failed": [], "enabled": 1}
    return _send


def test_refresh_pushes_first_seen_only(tmp_path):
    root = _repo(tmp_path)
    _write(root / "agent" / "lonely" / "__init__.py", 'MODULE_NAME = "孤儿能力"\n')
    state = tmp_path / "state.json"
    calls = []
    first = cg.refresh(root=root, state=state, dispatcher=_ok_sender(calls))
    assert first["count"] == 1
    assert first["push"]["pushed"], "首次出现必须推一次"
    assert len(calls) == 1
    assert calls[0][1] == "firing"
    assert calls[0][0]["severity"] == "warning"
    assert json.loads(state.read_text(encoding="utf-8"))["count"] == 1

    second = cg.refresh(root=root, state=state, dispatcher=_ok_sender(calls))
    assert second["push"]["attempted"] == 0, "同一缺口不该再推"
    assert len(calls) == 1


def test_push_failure_is_retried_next_round(tmp_path):
    """没通道/发失败 ⇒ 不记指纹（配好通道下一轮还能收到第一次提示）。"""
    root = _repo(tmp_path)
    _write(root / "agent" / "lonely" / "__init__.py", 'MODULE_NAME = "孤儿能力"\n')
    state = tmp_path / "state.json"

    def _no_channel(alert, transition):
        return {"ok": True, "sent": [], "reason": "no_enabled_channel"}

    first = cg.refresh(root=root, state=state, dispatcher=_no_channel)
    assert first["push"]["pushed"] == []
    assert first["push"]["failed"]

    calls = []
    second = cg.refresh(root=root, state=state, dispatcher=_ok_sender(calls))
    assert second["push"]["pushed"], "上一轮没发出去的缺口必须还能再推"
    assert len(calls) == 1


def test_reappearing_gap_is_pushed_again(tmp_path):
    """修好（缺口消失）后又坏掉 ⇒ 指纹被修剪，再推一次。"""
    root = _repo(tmp_path)
    lonely = root / "agent" / "lonely" / "__init__.py"
    _write(lonely, 'MODULE_NAME = "孤儿能力"\n')
    state = tmp_path / "state.json"
    calls = []
    cg.refresh(root=root, state=state, dispatcher=_ok_sender(calls))
    lonely.unlink()
    clean = cg.refresh(root=root, state=state, dispatcher=_ok_sender(calls))
    assert clean["count"] == 0
    _write(lonely, 'MODULE_NAME = "孤儿能力"\n')
    again = cg.refresh(root=root, state=state, dispatcher=_ok_sender(calls))
    assert again["count"] == 1
    assert len(calls) == 2


def test_refresh_once_never_raises(monkeypatch):
    monkeypatch.setattr(cg, "refresh", lambda **_: (_ for _ in ()).throw(RuntimeError("boom")))
    out = cg.refresh_once()
    assert out["count"] is None
    assert out["error"] == "RuntimeError"


# ════════════════════════════════════════════════════════════
#  读数：summary / unit_probe
# ════════════════════════════════════════════════════════════

def test_summary_without_state_is_unavailable(tmp_path):
    data = cg.summary(state=tmp_path / "nope.json")
    assert data["available"] is False
    assert data["count"] is None, "没跑过 ≠ 0 条（L5）"
    assert data["gaps"] == []


def test_unit_probe_is_never_without_state(tmp_path):
    out = cg.unit_probe(state=tmp_path / "nope.json")
    assert out["status"] == "never"
    assert out["issues"] == []


def test_unit_probe_ok_when_no_gaps(tmp_path):
    root = _repo(tmp_path)
    state = tmp_path / "state.json"
    cg.refresh(root=root, state=state, push=False)
    out = cg.unit_probe(state=state)
    assert out["status"] == "ok"
    assert out["metrics"]["count"] == 0


def test_unit_probe_warn_when_gaps_exist(tmp_path):
    root = _repo(tmp_path)
    _write(root / "agent" / "lonely" / "__init__.py", 'MODULE_NAME = "孤儿能力"\n')
    state = tmp_path / "state.json"
    cg.refresh(root=root, state=state, push=False)
    out = cg.unit_probe(state=state)
    assert out["status"] == "warn"
    assert out["issues"], "有缺口必须逐条给 issue"
    assert out["metrics"]["count"] == 1
    assert "未覆盖 1" in out["summary"]


def test_unit_probe_warns_when_stale(tmp_path):
    """状态文件很旧 ⇒ 自检停摆（不是"没有缺口"）。"""
    root = _repo(tmp_path)
    state = tmp_path / "state.json"
    cg.refresh(root=root, state=state, push=False, now=1.8e9)
    out = cg.unit_probe(state=state, now=1.8e9 + cg.interval_seconds() * (cg.STALE_ROUNDS + 1))
    assert out["status"] == "warn"
    assert any("停摆" in issue for issue in out["issues"])


def test_interval_and_enabled_defaults(monkeypatch):
    monkeypatch.delenv(cg.ENV_ENABLED, raising=False)
    monkeypatch.delenv(cg.ENV_INTERVAL_SECONDS, raising=False)
    assert cg.enabled() is True
    assert cg.interval_seconds() == cg.DEFAULT_INTERVAL_SECONDS
    monkeypatch.setenv(cg.ENV_INTERVAL_SECONDS, "60")
    assert cg.interval_seconds() == cg.MIN_INTERVAL_SECONDS, "低于下限要夹到下限"
    monkeypatch.setenv(cg.ENV_ENABLED, "false")
    assert cg.enabled() is False


class _FakeScheduler:
    def __init__(self):
        self.tasks = []

    def add_interval_task(self, name, func=None, interval_seconds=0, **kwargs):
        self.tasks.append({"name": name, "task_id": "t%d" % (len(self.tasks) + 1),
                           "func": func, "interval_seconds": interval_seconds})

    def remove_task(self, task_id):
        self.tasks = [t for t in self.tasks if t["task_id"] != task_id]


def test_register_gap_scheduler_is_idempotent(monkeypatch):
    monkeypatch.delenv(cg.ENV_ENABLED, raising=False)
    scheduler = _FakeScheduler()
    first = cg.register_gap_scheduler(scheduler)
    second = cg.register_gap_scheduler(scheduler)
    assert first["status"] == "scheduled"
    assert second["status"] == "scheduled"
    assert [t["name"] for t in scheduler.tasks] == [cg.TASK_NAME], "幂等：同名任务只保留一份"
    assert cg.unschedule_gap_scheduler(scheduler) is True
    assert scheduler.tasks == []


def test_register_gap_scheduler_disabled_is_noop(monkeypatch):
    monkeypatch.setenv(cg.ENV_ENABLED, "0")
    scheduler = _FakeScheduler()
    out = cg.register_gap_scheduler(scheduler)
    assert out["status"] == "disabled"
    assert scheduler.tasks == []
