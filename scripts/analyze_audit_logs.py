"""审计日志查询分析：JSONL → SQLite → SQL 统计（租户分布 / 异常占比）

用法：
  python scripts/analyze_audit_logs.py                     # 分析 + 告警（阈值 .env/默认 5）
  python scripts/analyze_audit_logs.py --audit-dir <dir>   # 指定目录
  python scripts/analyze_audit_logs.py --sql-only          # 仅打印 SQL（复用 .sql）
  python scripts/analyze_audit_logs.py --alert-threshold 3 # CLI 覆盖阈值（百分比）
  python scripts/analyze_audit_logs.py --no-alert          # 关闭告警

内置查询（同 scripts/audit_logs_analysis.sql）：
  1. 各租户日志量分布（条数 + 占比）
  2. 状态分布（异常请求占比 = status != 'success'）
  3. 异常请求按租户
  4. 按日日志量
告警（默认启用）：按租户异常请求占比 > 阈值触发邮件，正文附该租户最近 10 条异常
明细。阈值解析：CLI --alert-threshold > .env AUDIT_ALERT_THRESHOLD > 默认 5。
SMTP 配置走 .env（SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASS/SMTP_TO）；未配置仅打印。
仅标准库（sqlite3/json/smtplib），无第三方依赖。
"""
from __future__ import annotations

import argparse
import json
import os
import smtplib
import sqlite3
import sys
from email.mime.text import MIMEText
from email.header import Header
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_AUDIT_DIR = ROOT / "data" / "audit"

SQL_QUERIES = """
-- 1) 各租户日志量分布（条数 + 占比）
SELECT tenant_id AS 租户,
       COUNT(*)    AS 日志量,
       ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM audit), 2) AS 占比_pct
FROM audit
GROUP BY tenant_id
ORDER BY 日志量 DESC;

-- 2) 状态分布（异常请求占比 = status != 'success'）
SELECT status AS 状态,
       COUNT(*) AS 日志量,
       ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM audit), 2) AS 占比_pct
FROM audit
GROUP BY status
ORDER BY 日志量 DESC;

-- 3) 异常请求按租户（status != 'success'）
SELECT tenant_id AS 租户, status AS 状态, COUNT(*) AS 异常量
FROM audit
WHERE status != 'success'
GROUP BY tenant_id, status
ORDER BY 异常量 DESC;

-- 4) 按日日志量
SELECT substr(timestamp, 1, 10) AS 日期, COUNT(*) AS 日志量
FROM audit
GROUP BY 日期
ORDER BY 日期;
"""


def import_jsonl(audit_dir: Path, con: sqlite3.Connection) -> int:
    """导入全部 audit_*.jsonl → SQLite 表 audit，返回导入行数"""
    con.execute("DROP TABLE IF EXISTS audit")
    con.execute("""
        CREATE TABLE audit (
            timestamp TEXT, trace_id TEXT, action TEXT, status TEXT,
            tenant_id TEXT, input_hash TEXT, output_hash TEXT,
            stack_depth INTEGER, metadata TEXT
        )""")
    rows = 0
    for f in sorted(audit_dir.glob("audit_*.jsonl")):
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            con.execute(
                "INSERT INTO audit VALUES (?,?,?,?,?,?,?,?,?)",
                (r.get("timestamp", ""), r.get("trace_id", ""), r.get("action", ""),
                 r.get("status", ""), r.get("tenant_id", ""), r.get("input_hash", ""),
                 r.get("output_hash", ""), r.get("stack_depth"),
                 json.dumps(r.get("metadata", {}), ensure_ascii=False)),
            )
            rows += 1
    con.commit()
    return rows


def run_queries(con: sqlite3.Connection) -> list[list[tuple]]:
    """执行内置 SQL，返回各查询结果（含表头）"""
    results = []
    for stmt in [s.strip() for s in SQL_QUERIES.split(";") if s.strip()]:
        cur = con.execute(stmt)
        cols = [d[0] for d in cur.description]
        results.append([tuple(cols)] + [tuple(row) for row in cur.fetchall()])
    return results


def print_results(results: list[list[tuple]]) -> None:
    labels = ["各租户日志量分布", "状态分布（异常占比）", "异常请求按租户", "按日日志量"]
    for label, rows in zip(labels, results):
        print(f"\n=== {label} ===")
        if not rows[1:]:
            print("  （无数据）")
            continue
        widths = [max(len(str(r[i])) for r in rows) for i in range(len(rows[0]))]
        for ri, row in enumerate(rows):
            line = "  " + " | ".join(str(v).rjust(widths[i]) if ri else str(v).ljust(widths[i])
                                     for i, v in enumerate(row))
            print(line)
            if ri == 0:
                print("  " + "-" * (sum(widths) + 3 * (len(widths) - 1)))


# ── 告警：按租户异常请求占比（status != 'success'） ─────────────────────────

ANOMALY_SQL = """
SELECT tenant_id AS tenant, COUNT(*) AS total,
       SUM(CASE WHEN status != 'success' THEN 1 ELSE 0 END) AS err,
       ROUND(100.0 * SUM(CASE WHEN status != 'success' THEN 1 ELSE 0 END) / COUNT(*), 2) AS err_pct
FROM audit
GROUP BY tenant_id
ORDER BY err_pct DESC;
"""


def load_smtp_config() -> dict:
    """从 .env 读取 SMTP 配置（用户约定：配置走 .env 单一数据源）"""
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith(("SMTP_", "AUDIT_ALERT_THRESHOLD")) and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return {
        "host": os.environ.get("SMTP_HOST", ""),
        "port": int(os.environ.get("SMTP_PORT", "465")),
        "user": os.environ.get("SMTP_USER", ""),
        "password": os.environ.get("SMTP_PASS", ""),
        "to": os.environ.get("SMTP_TO", ""),
        "sender": os.environ.get("SMTP_FROM", os.environ.get("SMTP_USER", "")),
        "use_ssl": os.environ.get("SMTP_SSL", "1") != "0",
        "threshold_env": os.environ.get("AUDIT_ALERT_THRESHOLD", ""),
    }


def send_mail(subject: str, body: str, cfg: dict) -> bool:
    """发送告警邮件；SMTP 未配置时降级（返回 False，不抛异常）"""
    if not (cfg["host"] and cfg["to"]):
        print(f"  [WARN] SMTP 未配置（.env 需 SMTP_HOST/SMTP_TO 等），告警仅打印不发送")
        return False
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = cfg["sender"] or cfg["user"]
    msg["To"] = cfg["to"]
    try:
        if cfg["use_ssl"]:
            server = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=10)
        else:
            server = smtplib.SMTP(cfg["host"], cfg["port"], timeout=10)
        if cfg["user"]:
            server.login(cfg["user"], cfg["password"])
        server.sendmail(msg["From"], [cfg["to"]], msg.as_string())
        server.quit()
        print(f"  [OK] 告警邮件已发送至 {cfg['to']}")
        return True
    except Exception as e:  # noqa: BLE001 邮件失败不阻塞分析
        print(f"  [WARN] 告警邮件发送失败: {e}")
        return False


ANOMALY_DETAIL_SQL = """
SELECT timestamp, action, status, trace_id
FROM audit
WHERE tenant_id = ? AND status != 'success'
ORDER BY timestamp DESC
LIMIT 10;
"""


def check_anomalies(con: sqlite3.Connection, threshold: float) -> int:
    """按租户异常占比告警；返回告警租户数"""
    rows = con.execute(ANOMALY_SQL).fetchall()
    alerts = [r for r in rows if r[3] is not None and r[3] > threshold]
    if not alerts:
        print(f"\n[告警] 无租户异常占比超过 {threshold}%（共 {len(rows)} 个租户）")
        return 0
    print(f"\n[告警] 发现 {len(alerts)} 个租户异常占比超过 {threshold}%：")
    body_lines = [f"租户异常请求占比超过阈值 {threshold}%（审计日志分析告警）", ""]
    for tenant, total, err, pct in alerts:
        print(f"  - {tenant or '(空)'}: 异常 {err}/{total} = {pct}%")
        body_lines.append(f"租户: {tenant or '(空)'} | 异常 {err}/{total} = {pct}%")
        # 附最近 10 条异常明细（控制台 + 邮件正文）
        details = con.execute(ANOMALY_DETAIL_SQL, (tenant,)).fetchall()
        body_lines.append(f"  最近 {len(details)} 条异常请求：")
        print(f"    最近 {len(details)} 条异常请求：")
        for ts, action, status, trace_id in details:
            line = f"      [{ts}] action={action} status={status} trace={trace_id or '-'}"
            body_lines.append(line)
            print(line)
        body_lines.append("")
    body_lines += ["来源: scripts/analyze_audit_logs.py（data/audit）"]
    send_mail(f"[云枢] 租户异常请求告警（{len(alerts)} 租户超阈值）",
              "\n".join(body_lines), load_smtp_config())
    return len(alerts)


def resolve_threshold(cli_value: float | None) -> float:
    """阈值解析：CLI 显式 > 0 优先；否则 .env AUDIT_ALERT_THRESHOLD；默认 5"""
    if cli_value is not None and cli_value > 0:
        return cli_value
    env_val = load_smtp_config().get("threshold_env", "")
    try:
        if env_val:
            return float(env_val)
    except ValueError:
        print(f"  [WARN] AUDIT_ALERT_THRESHOLD 非数字（{env_val}），回退默认 5")
    return 5.0


def main():
    ap = argparse.ArgumentParser(description="审计日志 SQL 查询分析 + 异常告警")
    ap.add_argument("--audit-dir", type=Path, default=DEFAULT_AUDIT_DIR)
    ap.add_argument("--sql-only", action="store_true", help="仅打印 SQL")
    ap.add_argument("--alert-threshold", type=float, default=None,
                    help="租户异常请求占比告警阈值（百分比）；缺省读 .env "
                         "AUDIT_ALERT_THRESHOLD，无则默认 5；显式 0 关闭告警")
    ap.add_argument("--no-alert", action="store_true", help="关闭告警（等价 --alert-threshold 0）")
    args = ap.parse_args()

    if args.sql_only:
        print(SQL_QUERIES)
        return

    if not args.audit_dir.exists():
        print(f"[FAIL] 目录不存在: {args.audit_dir}")
        sys.exit(1)

    con = sqlite3.connect(":memory:")
    rows = import_jsonl(args.audit_dir, con)
    print(f"已导入 {rows} 条审计记录（{args.audit_dir}）")
    print_results(run_queries(con))
    if args.no_alert or args.alert_threshold == 0:
        print("\n[告警] 已关闭（--no-alert 或 --alert-threshold 0）")
    else:
        threshold = resolve_threshold(args.alert_threshold)
        print(f"\n[告警] 阈值 = {threshold}%（CLI 优先 > .env AUDIT_ALERT_THRESHOLD > 默认 5）")
        check_anomalies(con, threshold)
    con.close()


if __name__ == "__main__":
    main()
