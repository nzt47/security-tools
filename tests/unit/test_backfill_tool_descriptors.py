"""scripts/backfill_tool_descriptors.py：枚举映射 + 「写进去必须能被运行时加载」守卫。

【为什么单独钉这一条（2026-11-05 实测事故）】
    该脚本把 YAML 的**治理轴**取值直接透传进描述符：risk: critical（描述符 RiskLevel
    只有 low/medium/high/destructive）、provenance "derived:tool_definitions"
    （ProvenanceLevel 只有 unknown/declared/verified/signed）、data_class "external"
    （DataClass 只有 public/internal/confidential/secret）—— 三处都不在枚举里。
    于是脚本报「cp.builtin 覆盖 96/96」，而 DescriptorRegistry.load() 把这 96 条
    **全部判 invalid 丢弃**；运行中的后端下一次 save 就把台账写成 1 条（cp.skill 全丢）。
    故本文件既钉映射表，也钉「跑完能被运行时 registry 真的加载」这条端到端事实。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_SPEC = importlib.util.spec_from_file_location(
    "backfill_tool_descriptors", _ROOT / "scripts" / "backfill_tool_descriptors.py")
bfd = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bfd)  # type: ignore[arg-type]


@pytest.fixture()
def ledger(tmp_path, monkeypatch):
    """把脚本的目标台账指到 tmp（绝不碰真实 data/descriptors.json）"""
    path = tmp_path / "descriptors.json"
    path.write_text(json.dumps({"schema_version": 1, "descriptors": {},
                                "aliases": {}, "variants": {}, "audit": []}),
                    encoding="utf-8")
    monkeypatch.setattr(bfd, "_DESCRIPTORS", str(path), raising=True)
    return path


def _run_main(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["backfill_tool_descriptors.py", *argv], raising=True)
    return bfd.main()


# ════════════════════════════════════════════════════════════
#  ① 映射表：治理轴 → 描述符枚举
# ════════════════════════════════════════════════════════════

class TestEnums:

    def test_治理轴全部风险档都能映射(self):
        from agent.descriptors.models import RiskLevel
        allowed = {e.value for e in RiskLevel}
        axes = set()
        for f in (_ROOT / "data" / "tool_definitions").glob("*.yaml"):
            doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            if doc.get("risk"):
                axes.add(str(doc["risk"]))
        assert axes, "没读到任何 risk，检查 YAML 目录"
        assert axes <= set(bfd._RISK_TO_DESCRIPTOR), (
            "治理轴里出现了映射表没有的 risk 档: %s" % (axes - set(bfd._RISK_TO_DESCRIPTOR)))
        assert set(bfd._RISK_TO_DESCRIPTOR.values()) <= allowed

    def test_critical_映射到_destructive_而不是_high(self):
        """映到 high = 把「不可逆」报成「可逆」，比缺字段更危险。"""
        assert bfd._RISK_TO_DESCRIPTOR["critical"] == "destructive"

    def test_data_class_取值都在枚举内(self):
        from agent.descriptors.models import DataClass
        allowed = {e.value for e in DataClass}
        bad = {k: v for k, v in bfd._DATA_CLASS_BY_TAG.items() if v not in allowed}
        assert bad == {}, "data_class 映射出现枚举外取值: %s" % bad

    def test_未映射的_risk_直接报错(self):
        with pytest.raises(ValueError):
            bfd._derive({"name": "x", "description": "", "schema": {}},
                        "apocalyptic", "act", "execute", [], False)


# ════════════════════════════════════════════════════════════
#  ② 端到端：跑完必须能被运行时 registry 加载
# ════════════════════════════════════════════════════════════

class TestRuntimeLoadable:

    def test_回填结果全部能被_registry_加载(self, ledger, monkeypatch):
        rc = _run_main(monkeypatch)
        assert rc == 0, "脚本自身校验失败（fail-closed 未写盘）"

        from agent.descriptors.registry import DescriptorRegistry
        reg = DescriptorRegistry(path=ledger)
        reg.load()
        builtins = json.loads(ledger.read_text(encoding="utf-8"))["descriptors"]
        assert len(builtins) >= 90
        assert reg._invalid_entries == {}, (
            "有描述符过不了运行时 schema（会被 load 丢弃、再被 save 抹掉）: %s"
            % list(reg._invalid_entries)[:5])
        assert len(reg._descriptors) == len(builtins)

    def test_destructive_工具带治理三件套(self, ledger, monkeypatch):
        assert _run_main(monkeypatch) == 0
        builtins = json.loads(ledger.read_text(encoding="utf-8"))["descriptors"]
        destructive = [(k, v) for k, v in builtins.items()
                       if v["trust"]["risk_level"] == "destructive"]
        assert destructive, "语料里应至少有一条 destructive（shell_execute 之类）"
        for cid, v in destructive:
            gov = v["governance"]
            assert gov["undo_hint"].strip(), cid
            assert gov["compensating_action"].strip(), cid
            assert v["trust"]["requires_approval"] is True, cid

    def test_provenance_是合法枚举且有证据(self, ledger, monkeypatch):
        assert _run_main(monkeypatch) == 0
        builtins = json.loads(ledger.read_text(encoding="utf-8"))["descriptors"]
        for cid, v in builtins.items():
            assert v["origin"]["provenance"] == "declared", cid
            assert v["origin"]["evidence"], cid


# ════════════════════════════════════════════════════════════
#  ③ --check 必须能识别「覆盖了但加载不了」
# ════════════════════════════════════════════════════════════

class TestCheckMode:

    def test_check_识别非法条目(self, ledger, monkeypatch):
        bad = {
            "meta": {"id": "cp.builtin.fake", "version": "0.1.0",
                     "created_at": "2026-11-05T00:00:00", "updated_at": "2026-11-05T00:00:00"},
            "origin": {"source_type": "builtin", "source_id": "builtin",
                       "provenance": "derived:tool_definitions", "evidence": [],
                       "external_endpoint": False, "manifest_ref": ""},
            "tenancy": {"tenant_id": "default", "scope": "project"},
            "capability": {"name": "fake", "description": "", "input_schema": {},
                           "output_schema": {}},
            "trust": {"risk_level": "critical", "data_class": "internal",
                      "requires_approval": False},
            "runtime": {"timeout_ms": 1000, "idempotent": True,
                        "retry_policy": {"mode": "fixed", "max_retries": 1,
                                         "backoff_ms": 100, "backoff_factor": 1.0}},
            "governance": {"audit_level": "summary", "policy_ref": "", "undo_hint": ""},
            "evolution": {"stage": "shadow", "internalize_attempts": 0, "shadow_config": {}},
            "quality": {"success_rate": 0.0, "p99_latency_ms": 0.0, "sample_count": 0,
                        "regression_baseline_id": ""},
        }
        ledger.write_text(json.dumps({"schema_version": 1,
                                      "descriptors": {"cp.builtin.fake": bad},
                                      "aliases": {}, "variants": {}, "audit": []}),
                          encoding="utf-8")
        rc = _run_main(monkeypatch, "--check")
        assert rc == 1, "覆盖不等于可用：非法条目必须让 --check 非零退出"
