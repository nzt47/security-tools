# -*- coding: utf-8 -*-
"""D3 巡检脚本回归：四道门的判定口径 + **只读性**

【被测对象】`scripts/audit_governance_check.py`（D3 交付的只读巡检；
主报告 §1.5 的 S1/S7 与 §3 风险预案表「确认门豁免漂移」一行的落地）

【本文件断言什么】
1. **G1 三种情形都要红/黄/绿分明**：名单含 L2 工具 ⇒ FAIL、含 L1 ⇒ WARN、
   空/缺失 ⇒ PASS。这三条是"门不是假绿"的最小证据——只有 PASS 用例的门，
   无法区分"真的没豁免"与"脚本根本没看名单"。
2. **G1 的名单项解析**：名单里写能力 id（`cp.builtin.fan_out`）与写工具名
   （`fan_out`）都要认出同一个工具（口径与闸门 `_query_keys` 对齐）；
   解析不出的名字只 WARN，不 FAIL（它对闸门无实际效果）。
3. **G2 断链必红**：正常链 ⇒ PASS；被人改过 `prev_hash` 的链 ⇒ FAIL。
4. **G3 窗口内日根重放失败 ⇒ FAIL**（封印路径出错的信号）。
5. **只读性**：台账缺失时**不创建**文件；整轮巡检**一次都不调用** `AuditChain.append`
   （用例把它换成抛异常的桩：真写了就必红）。

【不依赖生产库】全部夹具用 `tmp_path` 造：假覆盖层 JSON、临时 sqlite 台账、
临时每日根文件。生产库/`data/` 下的任何文件都不被本文件读取或写入。
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

from agent.audit.chain import AuditChain, reset_audit_chains
from scripts.audit_governance_check import (
    EXEMPT_SETTING_KEY,
    Report,
    check_chain_integrity,
    check_exempt_drift,
    main,
    open_reader,
    parse_names,
)

pytestmark = [pytest.mark.unit, pytest.mark.p3]

_REPO = Path(__file__).resolve().parents[2]


# ════════════════════════════════════════════════════════════
#  夹具
# ════════════════════════════════════════════════════════════

@dataclass
class FakeMeta:
    """`ToolMeta` 的最小替身（只带巡检读的那几个属性）"""

    name: str
    effective_confirm_level: str = "L0"
    plane: str = "act"
    effect: str = "read"
    risk: str = "low"

    @property
    def confirm_level_semantics(self) -> str:
        return "级别语义占位（%s）" % self.effective_confirm_level


def write_settings(tmp_path: Path, value: Optional[str]) -> str:
    """造一个与 `data/ui_settings.json` 同结构的假覆盖层（不碰生产文件）"""
    doc: Dict[str, Any] = {"schema_version": 1, "note": "d3 test",
                           "overrides": {}}
    if value is not None:
        doc["overrides"][EXEMPT_SETTING_KEY] = {
            "key": EXEMPT_SETTING_KEY, "value": value, "actor": "tok_test",
            "risk": "A", "updated_at": "2026-09-25T00:00:00+0800",
            "previous": None, "reason": "d3 test"}
    p = tmp_path / "ui_settings.json"
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return str(p)


@pytest.fixture(autouse=True)
def _cleanup_chains():
    reset_audit_chains()
    yield
    reset_audit_chains()


def make_chain(tmp_path: Path, *, day: str = "2026-09-10", rows: int = 4) -> AuditChain:
    """临时 sqlite 台账（真库结构，假数据；writer 只为造数据，关在 close() 里）"""
    c = AuditChain(str(tmp_path / "audit_chain.db"),
                   roots_path=str(tmp_path / "daily_roots.jsonl"),
                   signing_key_path=str(tmp_path / "k.pem"),
                   signing_enabled=False, auto_seal=False, lock_enabled=False,
                   auto_start_writer=True)
    for i in range(rows):
        assert c.append("act%d" % i, "tester", "s:%d" % i, source="agent",
                        ts="%sT00:00:%02d.000000+00:00" % (day, i)) is not None
    assert c.flush(timeout=5.0) is True
    return c


def cli_args(tmp_path: Path, chain: AuditChain, settings: str, *extra: str):
    return ["--root", str(_REPO), "--db", chain.db_path,
            "--roots", chain.roots_path, "--settings", settings, *extra]


# ════════════════════════════════════════════════════════════
#  G1 —— 三种情形（PASS / WARN / FAIL）
# ════════════════════════════════════════════════════════════

class TestExemptDriftGate:

    def test_empty_list_is_pass(self, tmp_path):
        """名单为空/缺失 ⇒ PASS（既不 FAIL 也不 WARN）"""
        rep = Report()
        out = check_exempt_drift(rep, settings_path=write_settings(tmp_path, None),
                                 tool_meta={}, effective={"raw": "", "source": "default"})
        assert out["verdict"] == "PASS"
        assert rep.fails == [] and rep.warns == []
        assert rep.gates["G1"].startswith("PASS")

    def test_l2_tool_is_fail_with_tool_level_and_reason(self, tmp_path):
        """含 L2 工具 ⇒ FAIL，且必须点名「哪个工具/什么级别/为什么危险」"""
        rep = Report()
        metas = {"fan_out": FakeMeta("fan_out", "L2", plane="act",
                                     effect="execute", risk="high")}
        out = check_exempt_drift(rep, settings_path=write_settings(tmp_path, "fan_out"),
                                 tool_meta=metas,
                                 effective={"raw": "fan_out", "source": "ui_override"})
        assert out["verdict"] == "FAIL"
        assert len(rep.fails) == 1
        msg = rep.fails[0]
        assert "fan_out" in msg and "L2" in msg
        assert "execute" in msg and "high" in msg          # 危险理由必须落到要素上
        assert "自动路由执行" in msg

    def test_l1_tool_is_warn_only(self, tmp_path):
        """仅含 L1 ⇒ WARN（不阻塞、不 FAIL）"""
        rep = Report()
        metas = {"notify": FakeMeta("notify", "L1", plane="act",
                                    effect="execute", risk="low")}
        out = check_exempt_drift(rep, settings_path=write_settings(tmp_path, "notify"),
                                 tool_meta=metas,
                                 effective={"raw": "notify", "source": "ui_override"})
        assert out["verdict"] == "WARN"
        assert rep.fails == []
        assert any("notify" in w for w in rep.warns)

    def test_l3_tool_is_fail(self, tmp_path):
        """含 L3 ⇒ FAIL（默认禁止被取消）"""
        rep = Report()
        metas = {"shell_execute": FakeMeta("shell_execute", "L3", plane="act",
                                           effect="execute", risk="critical")}
        out = check_exempt_drift(rep, settings_path=write_settings(tmp_path, "shell_execute"),
                                 tool_meta=metas, effective={"raw": "shell_execute"})
        assert out["verdict"] == "FAIL" and "L3" in rep.fails[0]

    def test_unknown_name_warns_not_fails(self, tmp_path):
        """名单里写了工具清单里没有的名字 ⇒ WARN（对闸门无实际效果），不 FAIL"""
        rep = Report()
        out = check_exempt_drift(rep, settings_path=write_settings(tmp_path, "no_such_tool"),
                                 tool_meta={}, effective={"raw": "no_such_tool"})
        assert out["verdict"] == "WARN" and rep.fails == []
        assert any("no_such_tool" in w for w in rep.warns)

    def test_canonical_id_resolves_to_tool(self, tmp_path):
        """名单写能力 id（cp.builtin.fan_out）⇒ 与写工具名同样识别（不得假绿）"""
        rep = Report()
        metas = {"fan_out": FakeMeta("fan_out", "L2", plane="act",
                                     effect="execute", risk="high")}
        out = check_exempt_drift(rep, settings_path=write_settings(tmp_path, "cp.builtin.fan_out"),
                                 tool_meta=metas, effective={"raw": "cp.builtin.fan_out"})
        assert out["verdict"] == "FAIL"
        assert out["names"][0]["tool"] == "fan_out"

    def test_override_metadata_is_reported(self, tmp_path):
        """覆盖层的 actor/updated_at 必须被读出来（"谁在什么时候改的"是证据）"""
        rep = Report()
        out = check_exempt_drift(rep, settings_path=write_settings(tmp_path, "notify"),
                                 tool_meta={"notify": FakeMeta("notify", "L1")},
                                 effective={"raw": "notify"})
        assert out["override_meta"]["actor"] == "tok_test"
        assert out["override_meta"]["updated_at"].startswith("2026-09-25")

    def test_parse_names_matches_exemptions_module(self):
        """解析口径：去空白、忽略空段、保序去重（与 tool_exemptions.exempt_names 同形）"""
        assert parse_names(" a , b ,,a ") == ["a", "b"]
        assert parse_names(None) == [] and parse_names("") == []


# ════════════════════════════════════════════════════════════
#  G2 —— 链完整性
# ════════════════════════════════════════════════════════════

class TestChainIntegrityGate:

    def test_clean_chain_passes(self, tmp_path):
        chain = make_chain(tmp_path)
        try:
            rep = Report()
            out = check_chain_integrity(rep, db_path=chain.db_path, chain=chain)
            assert out["ok"] is True and out["checked"] == 4
            assert rep.fails == []
        finally:
            chain.close(timeout=2.0)

    def test_tampered_prev_hash_fails(self, tmp_path):
        """把第 2 条的 prev_hash 改成乱码（等于有人绕过入口篡改）⇒ FAIL"""
        chain = make_chain(tmp_path)
        db = chain.db_path
        chain.close(timeout=2.0)
        con = sqlite3.connect(db)
        con.execute("UPDATE audit_chain SET prev_hash=? WHERE seq=2", ("f" * 64,))
        con.commit()
        con.close()
        rep = Report()
        reader = open_reader(db, str(tmp_path / "daily_roots.jsonl"))
        try:
            out = check_chain_integrity(rep, db_path=db, chain=reader)
        finally:
            reader.close(timeout=2.0)
        assert out["ok"] is False
        assert rep.fails and "G2" in rep.fails[0]
        assert rep.gates["G2"].startswith("FAIL")

    def test_missing_db_is_fail_and_not_created(self, tmp_path):
        """台账不存在 ⇒ FAIL，且**绝不创建**文件（只读铁律的第一道守卫）"""
        db = tmp_path / "not_there.db"
        rep = Report()
        out = check_chain_integrity(rep, db_path=str(db), chain=None)
        assert out["ok"] is False and rep.fails
        assert not db.exists()

    def test_cli_full_run_on_clean_chain_is_pass(self, tmp_path, capsys):
        chain = make_chain(tmp_path)
        try:
            rc = main(cli_args(tmp_path, chain, write_settings(tmp_path, None)))
        finally:
            chain.close(timeout=2.0)
        out = capsys.readouterr().out
        assert rc == 0
        assert "PASS" in out and "G2" in out


# ════════════════════════════════════════════════════════════
#  G3 —— 日根重放（窗口内失败 ⇒ FAIL）
# ════════════════════════════════════════════════════════════

class TestDailyRootGate:

    def test_recent_root_mismatch_fails(self, tmp_path, capsys):
        """封存当日根后再改该日记录 ⇒ 最近有根日重放失败 ⇒ 退出码 1"""
        chain = make_chain(tmp_path)
        try:
            chain.daily_merkle_root("2026-09-10", sign=False, protect=False)
            settings = write_settings(tmp_path, None)
            assert main(cli_args(tmp_path, chain, settings)) == 0   # 封印后未动 ⇒ PASS
        finally:
            chain.close(timeout=2.0)
        con = sqlite3.connect(chain.db_path)
        con.execute("UPDATE audit_chain SET payload='{\"tampered\":1}' WHERE seq=2")
        con.commit()
        con.close()
        rc = main(cli_args(tmp_path, chain, write_settings(tmp_path, None)))
        out = capsys.readouterr().out
        assert rc == 1
        assert "G3" in out and "2026-09-10" in out

    def test_day_without_root_is_warn_only(self, tmp_path, capsys):
        """有记录但无日根 ⇒ WARN（已知历史事实），不改变退出码"""
        chain = make_chain(tmp_path)
        try:
            rc = main(cli_args(tmp_path, chain, write_settings(tmp_path, None)))
        finally:
            chain.close(timeout=2.0)
        out = capsys.readouterr().out
        assert rc == 0
        assert "缺 Merkle 日根" in out and "WARN" in out


# ════════════════════════════════════════════════════════════
#  只读性（本卡铁律）
# ════════════════════════════════════════════════════════════

class TestReadOnly:

    def test_reader_role_is_enforced(self, tmp_path):
        chain = make_chain(tmp_path)
        try:
            reader = open_reader(chain.db_path, chain.roots_path)
            try:
                assert reader.role == "reader"
            finally:
                reader.close(timeout=2.0)
        finally:
            chain.close(timeout=2.0)

    def test_full_run_never_appends(self, tmp_path, monkeypatch):
        """整轮巡检**一次都不许**调用 append（桩函数会抛异常 ⇒ 真写了就必红）"""
        chain = make_chain(tmp_path)
        db = chain.db_path
        chain.close(timeout=2.0)

        calls = []

        def _boom(*a, **k):                       # noqa: ANN002, ANN003
            calls.append((a, k))
            raise AssertionError("巡检脚本不得写审计链")

        monkeypatch.setattr(AuditChain, "append", _boom)
        rc = main(["--root", str(_REPO), "--db", db,
                   "--roots", str(tmp_path / "daily_roots.jsonl"),
                   "--settings", write_settings(tmp_path, None)])
        assert rc == 0
        assert calls == [], "巡检调用了 AuditChain.append ⇒ 违反只读铁律"

    def test_settings_file_is_never_written(self, tmp_path):
        """假覆盖层的字节在巡检前后完全一致（脚本只读它）"""
        path = write_settings(tmp_path, "fan_out")
        before = Path(path).read_bytes()
        main(["--root", str(_REPO), "--only", "G1", "--settings", path])
        assert Path(path).read_bytes() == before
