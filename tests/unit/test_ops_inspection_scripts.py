# -*- coding: utf-8 -*-
"""运维巡检脚本单测：审批超时统计 / 鉴权 401 分布（都是只读脚本）。"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def insp_approval():
    return _load("insp_approval", "scripts/inspect_approval_timeouts.py")


@pytest.fixture(scope="module")
def insp_auth():
    return _load("insp_auth", "scripts/inspect_auth_gate_rejects.py")


def test_审批_等待秒数解析(insp_approval):
    assert insp_approval.wait_seconds(
        "超时未审批（timeout deny）：待办 4854s ≥ 阈值 900s") == 4854
    assert insp_approval.wait_seconds("单条确认") is None
    assert insp_approval.wait_seconds("") is None


def test_审批_时间戳多格式(insp_approval):
    assert insp_approval.parse_ts("2026-09-25T22:31:53") == datetime(2026, 9, 25, 22, 31, 53)
    assert insp_approval.parse_ts("2026-09-25 22:31:53") == datetime(2026, 9, 25, 22, 31, 53)
    assert insp_approval.parse_ts("") is None
    assert insp_approval.parse_ts("不是时间") is None


def _rows():
    return [
        {"state": "approved", "object_id": "shell_execute", "level": "L1",
         "decision_reason": "单条确认", "created_at": "2026-10-12T11:00:00",
         "payload": {"risk": "critical"}},
        {"state": "rejected", "object_id": "shell_execute", "level": "L1",
         "decision_reason": "超时未审批（timeout deny）：待办 10122s ≥ 阈值 900s",
         "created_at": "2026-10-11T13:15:00", "payload": {"risk": "critical"}},
        {"state": "pending_review", "object_id": "shell_execute", "level": "L1",
         "decision_reason": "", "created_at": "2026-10-12T11:59:00",
         "payload": {"risk": "critical"}},
    ]


def test_审批_汇总口径(insp_approval):
    now = datetime(2026, 10, 12, 12, 0, 0)
    s = insp_approval.summarize(_rows(), now=now, pending_threshold_s=900)
    assert s["total"] == 3
    assert s["by_state"] == {"approved": 1, "rejected": 1, "pending_review": 1}
    assert s["timeout_denies"] == 1
    assert s["timeout_max_wait_s"] == 10122
    assert s["pending_count"] == 1
    assert s["overdue_pending_count"] == 0      # 只等了 60s
    assert s["overdue_pending_count"] == 0 and s["pending"][0]["tool"] == "shell_execute"
    s2 = insp_approval.summarize(_rows(), now=now, pending_threshold_s=30)
    assert s2["overdue_pending_count"] == 1     # 60s > 30s
    assert s2["overdue_pending"][0]["risk"] == "critical"


def test_审批_退出码(tmp_path, insp_approval):
    p = tmp_path / "ledger.jsonl"
    p.write_text(json.dumps({"state": "approved",
                             "created_at": "2026-10-12T11:00:00"}) + "\n",
                  encoding="utf-8")
    assert insp_approval.main(["--path", str(p), "--json"]) == 0
    assert insp_approval.main(["--path", str(tmp_path / "none.jsonl")]) == 3
    p.write_text(json.dumps({"state": "pending_review",
                             "created_at": "2020-01-01T00:00:00"}) + "\n",
                  encoding="utf-8")
    assert insp_approval.main(["--path", str(p)]) == 2


def test_审批_坏行计数(tmp_path, insp_approval):
    p = tmp_path / "bad.jsonl"
    p.write_text("{\"state\": \"approved\"}\n不是 json\n\n", encoding="utf-8")
    rows, bad = insp_approval.load_records(str(p))
    assert len(rows) == 1 and bad == 1


def test_鉴权_行解析(insp_auth):
    line = ("14:20:59 [WARNING ] __main__ : [AuthGate] 拒绝未授权请求 "
            "method=GET path=/api/context/status")
    assert insp_auth.extract_target(line, insp_auth.REJECT_MARK) == (
        "GET", "/api/context/status")
    assert insp_auth.extract_target("无关行", insp_auth.REJECT_MARK) is None


def test_鉴权_扫描与阈值(insp_auth):
    line = "x [AuthGate] 拒绝未授权请求 method=GET path=/api/context/status"
    shadow = "y [AuthGate][shadow] 将拦截未授权请求 method=POST path=/api/x source=token"
    agg = insp_auth.scan_lines([line, line, shadow, "无关"])
    assert agg["rejects"] == {"GET /api/context/status": 2}
    assert agg["shadow"] == {"POST /api/x": 1}
    assert agg["total"] == 2
    s = insp_auth.summarize(agg, threshold=2)
    assert s["over_threshold"] and s["over_threshold"][0]["path"] == "GET /api/context/status"
    assert insp_auth.summarize(agg, threshold=99)["over_threshold"] == []


def test_鉴权_退出码(tmp_path, insp_auth):
    log = tmp_path / "a.err.log"
    log.write_text("\n".join(
        ["[AuthGate] 拒绝未授权请求 method=GET path=/api/context/status"] * 5),
        encoding="utf-8")
    assert insp_auth.main(["--files", str(log), "--threshold", "10"]) == 0
    assert insp_auth.main(["--files", str(log), "--threshold", "3"]) == 2
    assert insp_auth.main(["--files", str(tmp_path / "none*.log")]) == 3
