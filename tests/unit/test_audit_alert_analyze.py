"""审计告警分析 + SMTP 发送链路自动化测试（上线前检查单 A/B 项）

覆盖（A 项 CI 每日告警逻辑 + B 项真实 SMTP 发送逻辑）：
- import_jsonl：正常导入 / 空行与损坏行跳过 / 缺失字段默认值
- resolve_threshold：CLI 优先 > env > 默认 5；env 非数字回退默认
- send_mail：未配置降级（False 不抛异常）/ SMTP_SSL 选择 / sendmail 参数（主题/收件人/正文）
- check_anomalies：超阈值告警（含明细）/ 未超阈值不告警
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from scripts.analyze_audit_logs import (  # noqa: E402
    import_jsonl,
    run_queries,
    resolve_threshold,
    send_mail,
    check_anomalies,
)


def _write_audit(directory: Path, lines: list[dict]) -> Path:
    f = directory / "audit_20260816.jsonl"
    f.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n",
                 encoding="utf-8")
    return f


def _sample(tenant: str = "org_a", status: str = "success", i: int = 0) -> dict:
    return {"timestamp": f"2026-08-16T00:00:{i:02d}+00:00", "trace_id": f"tr{i}",
            "action": f"act_{i}", "status": status, "tenant_id": tenant}


class TestImportJsonl:
    """A 项：审计日志导入（JSONL → SQLite）"""

    def test_import_normal(self, tmp_path):
        con = sqlite3.connect(":memory:")
        f = _write_audit(tmp_path, [_sample(tenant="org_a", status="error", i=i) for i in range(5)])
        assert import_jsonl(tmp_path, con) == 5
        assert con.execute("SELECT COUNT(*) FROM audit").fetchone()[0] == 5

    def test_skip_empty_and_broken_lines(self, tmp_path):
        f = tmp_path / "audit_20260816.jsonl"
        f.write_text(json.dumps(_sample()) + "\n\n{bad json}\n" + json.dumps(_sample(status="error")),
                     encoding="utf-8")
        con = sqlite3.connect(":memory:")
        assert import_jsonl(tmp_path, con) == 2

    def test_missing_fields_defaults(self, tmp_path):
        con = sqlite3.connect(":memory:")
        _write_audit(tmp_path, [{"timestamp": "t"}])  # 仅 timestamp
        assert import_jsonl(tmp_path, con) == 1
        row = con.execute("SELECT tenant_id, status, action FROM audit").fetchone()
        assert row == ("", "", "")  # 缺失字段默认空串


class TestRunQueries:
    """A 项：内置 SQL 查询（4 组，含表头）"""

    def test_four_query_groups(self):
        con = sqlite3.connect(":memory:")
        rows = [_sample(tenant="org_a", status="error", i=i) for i in range(2)]
        rows += [_sample(tenant="org_a", status="success", i=i) for i in range(2)]
        rows += [_sample(tenant="org_b", status="success", i=i) for i in range(1)]
        TestCheckAnomalies._insert(TestCheckAnomalies(), con, rows)
        results = run_queries(con)
        assert len(results) == 4  # 租户分布 / 状态分布 / 异常按租户 / 按日
        # 组 1：租户分布含 org_a(4) 与 org_b(1)
        tenants = [r[0] for r in results[0][1:]]
        assert set(tenants) == {"org_a", "org_b"}
        # 组 2：状态分布 error=2
        err_row = next(r for r in results[1][1:] if r[0] == "error")
        assert err_row[1] == 2


class TestResolveThreshold:
    """A 项：阈值解析优先级（CLI > env > 默认 5）"""

    def test_cli_wins(self):
        with mock.patch.dict("os.environ", {"AUDIT_ALERT_THRESHOLD": "3"}, clear=False):
            assert resolve_threshold(7) == 7.0  # CLI 显式优先

    def test_env_when_no_cli(self):
        with mock.patch.dict("os.environ", {"AUDIT_ALERT_THRESHOLD": "8"}, clear=False):
            assert resolve_threshold(None) == 8.0

    def test_default_when_nothing_set(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            # load_smtp_config 读 .env，可能含阈值；隔离后应回退默认 5
            with mock.patch("scripts.analyze_audit_logs.load_smtp_config",
                            return_value={"threshold_env": ""}):
                assert resolve_threshold(None) == 5.0

    def test_invalid_env_falls_back(self):
        with mock.patch("scripts.analyze_audit_logs.load_smtp_config",
                        return_value={"threshold_env": "abc"}):
            assert resolve_threshold(None) == 5.0


class TestSendMail:
    """B 项：SMTP 发送逻辑（复用生产 send_mail）"""

    def _cfg(self, **over):
        cfg = {"host": "smtp.test", "port": 465, "user": "alert@test", "password": "x",
               "to": "ops@example.com", "sender": "alert@test", "use_ssl": True}
        cfg.update(over)
        return cfg

    def test_unconfigured_degrades_false(self):
        assert send_mail("t", "b", {"host": "", "to": ""}) is False

    def test_ssl_ok_and_sendmail_args(self):
        with mock.patch("smtplib.SMTP_SSL") as m_ssl:
            send_mail("告警主题", "正文\n明细", self._cfg())
        assert m_ssl.called
        m_ssl.return_value.login.assert_called_once_with("alert@test", "x")
        m_ssl.return_value.sendmail.assert_called_once()
        _args = m_ssl.return_value.sendmail.call_args
        assert _args.args[0] == "alert@test"           # sender
        assert _args.args[1] == ["ops@example.com"]    # recipients
        # MIME Subject 为 RFC2047 编码（Header 包装），用 decode_header 解码后断言
        from email import message_from_string
        from email.header import decode_header, make_header
        msg = message_from_string(_args.args[2])
        assert str(make_header(decode_header(msg["Subject"]))) == "告警主题"
        assert msg["To"] == "ops@example.com"
        assert "正文" in msg.get_payload(decode=True).decode("utf-8")

    def test_non_ssl_uses_smtp(self):
        with mock.patch("smtplib.SMTP") as m_smtp, \
             mock.patch("smtplib.SMTP_SSL") as m_ssl:
            send_mail("t", "b", self._cfg(use_ssl=False))
        assert m_smtp.called and not m_ssl.called

    def test_send_failure_returns_false_not_raise(self):
        with mock.patch("smtplib.SMTP_SSL", side_effect=ConnectionError("refused")):
            assert send_mail("t", "b", self._cfg()) is False


class TestCheckAnomalies:
    """A 项：按租户异常占比告警（超阈值触发 + 明细 + 未超不触发）"""

    def _insert(self, con: sqlite3.Connection, rows: list[dict]) -> None:
        """建表并插入（跳过文件依赖，直接测告警逻辑）"""
        con.execute("DROP TABLE IF EXISTS audit")
        con.execute("""CREATE TABLE audit (
            timestamp TEXT, trace_id TEXT, action TEXT, status TEXT,
            tenant_id TEXT, input_hash TEXT, output_hash TEXT,
            stack_depth INTEGER, metadata TEXT)""")
        for r in rows:
            con.execute("INSERT INTO audit VALUES (?,?,?,?,?,?,?,?,?)",
                        (r.get("timestamp", ""), r.get("trace_id", ""), r.get("action", ""),
                         r.get("status", ""), r.get("tenant_id", ""), "", "", None, "{}"))
        con.commit()

    def test_above_threshold_alerts_with_details(self):
        con = sqlite3.connect(":memory:")
        rows = [_sample(tenant="org_alarm", status="error", i=i) for i in range(6)]
        rows += [_sample(tenant="org_alarm", status="success", i=100 + i) for i in range(4)]
        self._insert(con, rows)
        with mock.patch("scripts.analyze_audit_logs.send_mail") as m_send, \
             mock.patch("scripts.analyze_audit_logs.load_smtp_config",
                        return_value={"host": "x", "to": "y"}):
            alerts = check_anomalies(con, 5.0)
        assert alerts == 1
        body = m_send.call_args.args[1]
        assert "org_alarm" in body and "60.0%" in body
        assert "act_0" in body  # 明细含异常 action

    def test_below_threshold_no_alert(self):
        con = sqlite3.connect(":memory:")
        rows = [_sample(tenant="org_ok", status="error", i=i) for i in range(10)]
        rows += [_sample(tenant="org_ok", status="success", i=i) for i in range(200)]
        self._insert(con, rows)
        with mock.patch("scripts.analyze_audit_logs.send_mail") as m_send:
            assert check_anomalies(con, 5.0) == 0  # 10/210 ≈ 4.76% < 5%
        m_send.assert_not_called()
