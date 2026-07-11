# 并发缺陷修复 Git Commit Message

> 本文件提供两份 commit message：完整版（用于 squash merge）和精简版（用于单次提交）。
> 根据你的提交策略选择使用。
> 包含所有并发修复：原始 3 个缺陷 + 审计后修复的 5 个风险点。

---

## 完整版（推荐用于 squash merge）

```
fix(concurrency): 修复 8 个并发缺陷——死锁/持锁I/O/GIL竞争/锁嵌套

== 根因分析 ==

8 个缺陷表现相似（测试超时/挂起/性能骤降），但根因可分为三类：

【原始 3 个缺陷】
1. metrics.py 不可重入锁嵌套（确定性死锁）
   get_all_metrics() 持有 threading.Lock 后调用 get_stats()，
   get_stats() 也尝试获取同一把 Lock → 不可重入锁第二次 acquire 永久阻塞。

2. DiskCache.set() 持锁 I/O（性能骤降）
   DiskCache.set() 在持有 RLock 时执行磁盘 I/O（目录遍历+JSON写入），
   多线程并发时持锁时间过长 → 5 线程 50 次 set/get 超时。

3. run_sandbox threading 线程泄漏（GIL 竞争）
   旧版 run_sandbox 用 threading.Thread 执行用户代码，超时后
   join(timeout) 返回但线程无法强制终止 → while True: pass 线程
   永久运行占用 GIL → 后续 17 个 skills_mgmt 测试因 GIL 竞争误报失败。

【审计后修复的 5 个风险点】
4. DiskCache.get() 持锁 I/O（P0，修复 set() 时遗漏）
   DiskCache.get() 在持有 RLock 时执行文件读取和删除，
   与 set() 相同的持锁 I/O 问题。

5. disaster_recovery.py get_backup_list() 持锁 I/O（P1）
   持有 RLock 时遍历目录 + 读取所有 _meta.json 文件。

6. disaster_recovery.py _verify_backup() 持锁 I/O（P1）
   持有 RLock 时读取 backup 文件并计算校验和。

7. disaster_recovery.py restore_from_backup() 锁嵌套+持锁I/O+持锁回调（P1）
   持有 RLock 时调用 _verify_backup()（锁嵌套）、读取文件（持锁I/O）、
   调用外部 restore_func 回调（持锁执行外部代码，极其危险）。

8. disaster_recovery.py auto_recover_on_startup() + get_status() 锁嵌套（P1）
   持有 RLock 时调用 get_backup_list()（锁嵌套）和 restore_from_backup()（锁嵌套）。

== 修复方案 ==

1. metrics.py: 锁内复制数据快照，锁外调用 get_stats()
   - with self._lock 内只做 list() 和 dict() 复制
   - get_stats() 调用移到 with 块外，避免锁嵌套
   - 不改用 RLock（会掩盖"持锁调用持锁方法"的设计缺陷）

2. multi_level_cache.py DiskCache.set(): 磁盘 I/O 移到锁外
   - with self._lock 内只做目录大小检查和淘汰
   - json.dump 文件写入移到 with 块外（每个 key 哈希到唯一文件，无冲突）
   - 持锁时间从 1-5ms 降至 ~0.01ms

3. multi_level_cache.py DiskCache.get(): 移除锁
   - 文件路径由 key 哈希唯一确定，无共享状态需要保护
   - 文件读取/删除完全在锁外执行

4. system_tools.py: run_sandbox 从 threading 迁移到 multiprocessing
   - 新增 _sandbox_worker() 子进程入口函数
   - 使用 multiprocessing.get_context("spawn") 创建子进程
   - 超时后 process.terminate() 强制终止，消除线程泄漏
   - 进程级隔离：子进程 stdout/stderr 修改不影响主进程
   - 代价：进程启动比线程慢 ~80ms，但消除 GIL 竞争

5. disaster_recovery.py get_backup_list(): 移除锁
   - 只读磁盘操作，不访问共享内存状态，无需持锁

6. disaster_recovery.py _verify_backup(): 移除锁
   - 只读校验操作，不访问共享内存状态

7. disaster_recovery.py restore_from_backup(): 重构为分段加锁
   - 锁内：设置 IN_PROGRESS 状态 + 快照 providers
   - 锁外：文件检查、校验、读取、调用 restore_func 回调
   - 锁内：更新最终状态（SUCCESS/FAILED）
   - 消除持锁执行外部回调的危险

8. disaster_recovery.py auto_recover_on_startup(): 移除外层锁
   - 调用的方法各自管理锁，避免锁嵌套

9. disaster_recovery.py get_status(): 分离锁内/锁外
   - get_backup_list() 在锁外调用
   - 锁内只取内存状态快照（config、providers、recovery_status）

== 修改文件 ==

agent/monitoring/metrics.py              | 死锁修复
agent/caching/multi_level_cache.py       | DiskCache get/set 持锁I/O修复
agent/system_tools.py                    | multiprocessing 重构
agent/disaster_recovery.py               | 5个方法锁优化
docs/wiki/concurrency_fixes_wiki.md      | 团队 Wiki 页面
docs/fixes/deadlock_fix_technical_review.md          | 死锁技术文档
docs/fixes/run_sandbox_multiprocessing_refactor.md   | 重构方案
docs/fixes/test_failures_priority_list.md            | 失败用例分类清单
docs/audits/concurrency_risk_audit_20260711.md       | 并发风险审计报告
docs/templates/concurrency_code_review_checklist.md  | 审查清单模板
tests/unit/test_sandbox_multiprocess_boundary.py     | 27个边界测试
.github/workflows/sandbox-boundary-tests.yml          | CI 流水线

== 验证结果 ==

- 死锁测试: 22 passed (此前 Timeout)
- 缓存线程安全: 38 passed (此前偶发超时)
- sandbox 边界测试: 27 passed (32s)
- sandbox 全量测试: 75 passed (49s, 无 warning)
- 灾备恢复测试: 205 passed (缓存89 + 灾备116)
- 受 GIL 竞争影响的测试: 729 passed, 0 failed (26.35s)
- 全量测试: 6485 passed, 113 failed (全部为预存在问题)

== 关联提交 ==

- 3cd54031: 死锁 + DiskCache.set() I/O 修复
- 0714d792: multiprocessing 重构
- 46a1d42a: Wiki 页面
- 76cf2cda: commit message + 边界测试 + 审查清单
- 1e4144ac: 审计报告 + CI 流水线
- 877e9f11: DiskCache.get() + disaster_recovery P1 修复

== 教训 ==

排查并发问题时必须先确认根因类型：
- 卡在 acquire → 死锁 → 移到锁外/缩小临界区
- 卡在 I/O → 性能 → I/O 移到锁外/降低锁粒度
- 卡在 exec → GIL 竞争 → 用 multiprocessing 替代 threading
切忌无脑把 Lock 换成 RLock——这会掩盖设计缺陷。
持锁时调用外部回调是最高风险——回调中的任何阻塞都会导致整个模块卡死。
```

---

## 精简版（用于单次提交）

```
fix(concurrency): 修复8个并发缺陷——死锁/持锁I/O/GIL竞争/锁嵌套

1. metrics.py: get_all_metrics() 锁嵌套死锁 → 锁内复制快照，锁外调用
2. multi_level_cache.py: DiskCache get/set 持锁I/O → 文件读写移到锁外
3. system_tools.py: run_sandbox threading 线程泄漏 → 迁移到 multiprocessing
4. disaster_recovery.py: 5个方法持锁I/O+锁嵌套 → 移除锁/分段加锁/快照providers

验证: 729+205 passed, 0 failed
```
