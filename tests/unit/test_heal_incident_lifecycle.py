r"""事故卡生命周期（六要素补齐 / resolve 可达 / 出域链去重）+ 测试期目录隔离

回归对象（2026-10-28 实测，有真实后果）：
  1) `IncidentCard.resolve()` 要求六要素齐备，而 `raise_incident()` 原先只能填 3 项
     ⇒ 除 `agent/digestion/probe.py` 直接赋值外，任何来源的卡**永远不可闭合**
     ⇒ 面板「未闭合」只增不减（实测 14 张 L4 出域链卡，五项全缺）。
     本文件钉住：补齐入口 `complete_incident()` 存在、补齐后真的能 resolve、
     以及「补完仍缺要素时不落盘」（失败要看得见，不能半写）。
  2) 出域链同一 (scope, host) 0.5s 内连开两张（14 张 = 7 组重复对）⇒ 去重窗口收敛为一张。
  3) 事故卡目录在测试期间必须隔离（conftest autouse）：任何用例都不得写生产目录。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from agent.guardrails.egress_chain import (
    EgressChainMonitor,
    reset_egress_chain_monitor,
)
from agent.policy.taint import reset_secret_taint
from agent.self_healing import levels as L

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(
        name, _PROJECT_ROOT / "scripts" / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[arg-type]
    return mod


ALL_SIX = dict(
    fatal_change="deadbeef",
    evasion_rule="policy_id:p-1",
    in_strategy_memory={"yes": True, "id": "mem-1"},
    regression_case_added={"yes": True, "case_id": "case-1"},
    trace_ids=["t-1"],
)


@pytest.fixture(autouse=True)
def _reset_chain_state():
    """逐用例复位链路监测器 + 污点账（两套都是进程级状态）"""
    reset_egress_chain_monitor()
    reset_secret_taint()
    yield
    reset_egress_chain_monitor()
    reset_secret_taint()


class TestSixElements:
    def test_开卡就能填齐六要素(self, tmp_path):
        card = L.raise_incident(L.HealLevel.L4, root_cause="rc",
                                directory=str(tmp_path), **ALL_SIX)
        assert card.missing_elements() == []
        assert card.is_resolvable() is True

    def test_缺要素不可_resolve_补齐后可(self, tmp_path):
        card = L.raise_incident(L.HealLevel.L4, root_cause="rc", directory=str(tmp_path))
        assert card.is_resolvable() is False
        with pytest.raises(L.LevelError):
            card.resolve()
        done = L.complete_incident(card.incident_id, directory=str(tmp_path),
                                   resolve=True, **ALL_SIX)
        assert done.status == L.STATUS_RESOLVED
        assert done.resolved_at
        again = L.load_incident(card.incident_id, directory=str(tmp_path))
        assert again is not None and again.status == L.STATUS_RESOLVED

    def test_补完仍缺要素_抛错且不落盘(self, tmp_path):
        card = L.raise_incident(L.HealLevel.L4, root_cause="rc", directory=str(tmp_path))
        path = tmp_path / (card.incident_id + ".json")
        before = path.read_text(encoding="utf-8")
        with pytest.raises(L.LevelError):
            L.complete_incident(card.incident_id, fatal_change="deadbeef",
                                resolve=True, directory=str(tmp_path))
        assert path.read_text(encoding="utf-8") == before, (
            "resolve 失败时不得落盘（半写会让面板显示成「已补一半」）")

    def test_补齐不存在的卡抛错(self, tmp_path):
        with pytest.raises(L.LevelError):
            L.complete_incident("inc-nope", fatal_change="x", directory=str(tmp_path))


class TestEgressCardDedupe:
    def _arm(self, monitor, scope):
        monitor.record_secret_read("~/.aws/credentials",
                                   content_kinds=["aws_access_key"], scope=scope)

    def test_同作用域同目标窗口内只开一张(self, tmp_path):
        m = EgressChainMonitor(enabled=True)
        self._arm(m, "s-dedupe")
        v1 = m.evaluate(url="https://api.example.com/a", scope="s-dedupe",
                        incident_dir=str(tmp_path), trip_breaker=False)
        v2 = m.evaluate(url="https://api.example.com/b", scope="s-dedupe",
                        incident_dir=str(tmp_path), trip_breaker=False)
        assert v1.incident_id, "第一次命中必须开卡"
        assert v1.incident_id == v2.incident_id, "同一 scope+host 窗口内应复用同一张卡"
        assert len(list(tmp_path.glob("inc-*.json"))) == 1

    def test_不同目标各开一张(self, tmp_path):
        m = EgressChainMonitor(enabled=True)
        self._arm(m, "s-two")
        v1 = m.evaluate(url="https://api.example.com/x", scope="s-two",
                        incident_dir=str(tmp_path), trip_breaker=False)
        v2 = m.evaluate(url="https://other.example.org/x", scope="s-two",
                        incident_dir=str(tmp_path), trip_breaker=False)
        assert v1.incident_id and v2.incident_id and v1.incident_id != v2.incident_id
        assert len(list(tmp_path.glob("inc-*.json"))) == 2

    def test_开卡带上_trace_id(self, tmp_path):
        m = EgressChainMonitor(enabled=True)
        self._arm(m, "s-trace")
        v = m.evaluate(url="https://api.example.com/t", scope="s-trace",
                       trace_id="trace-abc", incident_dir=str(tmp_path),
                       trip_breaker=False)
        card = L.load_incident(v.incident_id, directory=str(tmp_path))
        assert card is not None and card.trace_ids == ["trace-abc"]


class TestIncidentsViewSplit:
    def test_未闭合拆成可闭合与缺要素(self, tmp_path):
        from agent.ui_panels import data as D

        L.raise_incident(L.HealLevel.L4, root_cause="rc-1", directory=str(tmp_path))
        L.raise_incident(L.HealLevel.L4, root_cause="rc-2", directory=str(tmp_path),
                         **ALL_SIX)
        view = D.incidents_view(incidents_dir=str(tmp_path),
                                events_dir=str(tmp_path / "events"))
        assert view["open_count"]["value"] == 2
        assert view["open_blocked_count"]["value"] == 1
        assert view["open_resolvable_count"]["value"] == 1
        assert "complete_incident" in view["resolve_hint"]


class TestTestIsolation:
    def test_生产事故目录在测试期间不被写入(self):
        """conftest 的 session 级 autouse 夹具必须把目录指到临时区"""
        env_dir = L.incidents_dir()
        assert str(env_dir) != str(Path(L.DEFAULT_INCIDENTS_DIR)), (
            "测试期间 incidents_dir() 仍指向生产目录 —— conftest 的隔离夹具失效了")


class TestOpsScript:
    def test_dry_run_不动文件_confirm_才归档(self, tmp_path):
        mod = _load_script("heal_incidents")
        card = L.raise_incident(L.HealLevel.L4, root_cause="rc", directory=str(tmp_path))
        src = tmp_path / (card.incident_id + ".json")
        assert mod._archive_one(card, directory=str(tmp_path), reason="r",
                                confirm=False) is True
        assert src.exists(), "dry-run 不得移动文件"
        assert mod._archive_one(card, directory=str(tmp_path), reason="r",
                                confirm=True) is True
        assert not src.exists()
        archived = list((tmp_path / mod.ARCHIVE_DIRNAME).rglob("inc-*.json"))
        assert len(archived) == 1, "归档区必须保留 JSON（证据可追溯）"

    def test_按_host_与_id_过滤(self, tmp_path):
        mod = _load_script("heal_incidents")
        a = L.raise_incident(L.HealLevel.L4, root_cause="rc", directory=str(tmp_path),
                             detail={"target_host": "api.example.com"})
        b = L.raise_incident(L.HealLevel.L4, root_cause="rc", directory=str(tmp_path),
                             detail={"target_host": "real.example.org"})
        cards = L.list_incidents(directory=str(tmp_path))
        picked = mod._select(cards, [], "api.example.com", "")
        assert [c.incident_id for c in picked] == [a.incident_id]
        assert [c.incident_id for c in mod._select(cards, [b.incident_id], "", "")] == [b.incident_id]
