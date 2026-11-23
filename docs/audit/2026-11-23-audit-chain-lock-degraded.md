# 审计链 lock.degraded 分叉（2026-11-23 19:52）—— 处置记录

> 现象：`verify_audit_chain.py` 报 TAMPERED，首个异常 seq=88008；
> `prev_hash=a2d34a0c…` 与 seq=88007 重算 self_hash `df62f2cf…` 不一致，88009/88010 前向失败。
> 这是**并发 writer 的锁超时兜底分叉**，不是插件代码写坏了审计。

## 证据

- 预留日志里 seq=88007 有两条：一条 `lock.timeout`（prev=a2d34a0c…），一条
  `ui.ingestion.api_ingestion_preview.post`（prev=71712b37…，self=df62f2cf…）；DB 里只有后者。
- seq=88008 是 `lock.degraded`（payload: pid=16320 / reason=seq_lock_timeout… 回落到 DB 事务分配 seq=88007），
  它沿用了 `lock.timeout` 分支的内存链头 a2d34a0c… ⇒ 与 DB 的 88007 分叉。
- 触发时段（19:52）后端 + 多个探针/测试进程同时写审计；跨进程锁 5s 超时走了兜底路径。

## 处置

1. 停后端，备份 `data/audit/audit_chain.db` 与 `.seqjournal` 到
   `.local/audit_repair_backup_20261123_2015/`；
2. 以 DB 的 seq=88007 self_hash `df62f2cf…` 为锚，重算 88008–88010 的
   `prev_hash / payload_hash / self_hash`（payload 内容保留），同步修正 journal 三行；
3. `verify_audit_chain.py` → **OK，88010 条，链头 62ec8d33…**。

## 根因与修复

- 根因在 `agent/audit/chain.py::_allocate_via_db` 的异常兜底：DB 事务失败后回落到
  **进程内存链头** `_allocate_and_journal(prev_seq=self._next_seq-1, prev_hash=self._last_hash)`，
  当另一个 writer 已推进 DB 时就会分叉。锁超时本身是降级，但兜底不应该再写内存链头。
- **2026-11-23 21:2x 已修**：`_allocate_via_db` 异常后重试一次（`_retry=True` 防递归），
  仍失败则 `raise AuditEntryError`（fail-closed），不再写内存链头。
- 新增 `tests/unit/test_audit_chain_fallback.py`：断言「重试 1 次后拒绝写入」；回归
  `test_audit_chain` / `test_audit_chain_concurrency` / `test_audit_journal_drain`
  **131 passed + 4 concurrency passed**。交接条目 `audit-lock-degraded-fork` 置 done。
- 运维口径：不要让后端与多个写审计的探针/测试同时跑；验证审计链前先停后端。
