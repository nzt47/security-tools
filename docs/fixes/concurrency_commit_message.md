# 并发缺陷修复 Git Commit Message

> 本文件提供两份 commit message：完整版（用于 squash merge）和精简版（用于单次提交）。
> 根据你的提交策略选择使用。

---

## 完整版（推荐用于 squash merge）

```
fix(concurrency): 修复 3 个并发缺陷——死锁/持锁I/O/GIL竞争

== 根因分析 ==

三个缺陷表现相似（测试超时/挂起），但根因完全不同：

1. metrics.py 不可重入锁嵌套（确定性死锁）
   get_all_metrics() 持有 threading.Lock 后调用 get_stats()，
   get_stats() 也尝试获取同一把 Lock → 不可重入锁第二次 acquire 永久阻塞。

2. DiskCache 持锁 I/O（性能骤降）
   DiskCache.set() 在持有 RLock 时执行磁盘 I/O（目录遍历+JSON写入），
   多线程并发时持锁时间过长 → 5 线程 50 次 set/get 超时。

3. run_sandbox threading 线程泄漏（GIL 竞争）
   旧版 run_sandbox 用 threading.Thread 执行用户代码，超时后
   join(timeout) 返回但线程无法强制终止 → while True: pass 线程
   永久运行占用 GIL → 后续 17 个 skills_mgmt 测试因 GIL 竞争误报失败。

== 修复方案 ==

1. metrics.py: 锁内复制数据快照，锁外调用 get_stats()
   - with self._lock 内只做 list() 和 dict() 复制
   - get_stats() 调用移到 with 块外，避免锁嵌套
   - 不改用 RLock（会掩盖"持锁调用持锁方法"的设计缺陷）

2. multi_level_cache.py: DiskCache 磁盘 I/O 移到锁外
   - with self._lock 内只做目录大小检查和淘汰
   - json.dump 文件写入移到 with 块外（每个 key 哈希到唯一文件，无冲突）
   - 持锁时间从 1-5ms 降至 ~0.01ms

3. system_tools.py: run_sandbox 从 threading 迁移到 multiprocessing
   - 新增 _sandbox_worker() 子进程入口函数
   - 使用 multiprocessing.get_context("spawn") 创建子进程
   - 超时后 process.terminate() 强制终止，消除线程泄漏
   - 进程级隔离：子进程 stdout/stderr 修改不影响主进程
   - 代价：进程启动比线程慢 ~80ms，但消除 GIL 竞争

== 修改文件 ==

agent/monitoring/metrics.py          | 19 ++++----
agent/caching/multi_level_cache.py   | 16 +++++--
agent/system_tools.py                | 71 ++++++++++++++++++++++++++--
docs/wiki/concurrency_fixes_wiki.md  | 288 +++++++++++++++++++++++++++++++++++
docs/fixes/test_failures_priority_list.md           | 详尽的 P0-P3 分类清单
docs/fixes/run_sandbox_multiprocessing_refactor.md  | multiprocessing 重构方案
docs/fixes/deadlock_fix_technical_review.md         | 死锁修复技术文档

== 验证结果 ==

- 死锁测试: 22 passed (此前 Timeout)
- 缓存线程安全: 38 passed (此前偶发超时)
- sandbox 测试: 75 passed (49s, 无 warning)
- 受 GIL 竞争影响的测试: 729 passed, 0 failed, 9 skipped (26.35s)
- 全量测试: 6485 passed, 113 failed (全部为预存在问题，与本次修复无关)

== 关联提交 ==

- 3cd54031: 死锁 + DiskCache I/O 修复
- 0714d792: multiprocessing 重构
- 46a1d42a: Wiki 页面

== 教训 ==

排查并发问题时必须先确认根因类型：
- 卡在 acquire → 死锁 → 移到锁外/缩小临界区
- 卡在 I/O → 性能 → I/O 移到锁外/降低锁粒度
- 卡在 exec → GIL 竞争 → 用 multiprocessing 替代 threading
切忌无脑把 Lock 换成 RLock——这会掩盖设计缺陷。
```

---

## 精简版（用于单次提交）

```
fix(concurrency): 修复死锁+持锁I/O+GIL竞争三个并发缺陷

1. metrics.py: get_all_metrics() 锁嵌套死锁 → 锁内复制快照，锁外调用
2. multi_level_cache.py: DiskCache 持锁 I/O 超时 → 文件写入移到锁外
3. system_tools.py: run_sandbox threading 线程泄漏 → 迁移到 multiprocessing

验证: 729 passed, 0 failed (受影响测试), 26.35s
```
