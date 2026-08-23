-- 审计日志租户隔离查询分析 SQL（配套 scripts/analyze_audit_logs.py）
-- 表结构：audit(timestamp, trace_id, action, status, tenant_id, input_hash,
--            output_hash, stack_depth, metadata)
-- 用法：sqlite3 audit.db < scripts/audit_logs_analysis.sql

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

-- 5) （可选）单租户明细：指定租户近 N 条
-- SELECT timestamp, action, status FROM audit WHERE tenant_id = 'system'
--   ORDER BY timestamp DESC LIMIT 20;
