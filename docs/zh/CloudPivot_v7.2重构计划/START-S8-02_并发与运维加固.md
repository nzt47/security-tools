# START-S8-02 并发与运维加固（分发壳 · 可直接复制）

> **本文件不是任务书。** 任务规格 → [`TASK-S8-02_并发与运维加固.md`](TASK-S8-02_并发与运维加固.md)（★ 必须先完整阅读）
> 批次与通用约定 → [`PARALLEL_S8批次总表.md`](PARALLEL_S8批次总表.md)｜基线：`master` / `7e094eab`｜预估：5–7 人日

---

## 一、开工前确认

1. `--base master`；worktree id 用 **`s802`**。
2. 依赖 S2（trace/审计/events）、S3-01、S4-02（决策日志）——**均已结案**。
3. 目标是把"**进程内单写者假设**"升级为**可证明的多进程安全**，并补**决策日志轮转**。**不引入外部依赖**（Redis/etcd 锁不做）。

---

## 二、启动提示词（整段复制给新会话）

```
【任务】TASK-S8-02 — 并发与运维加固（跨进程锁 + 决策日志轮转 + 多写者正确性）
【任务书】C:\Users\Administrator\agent\docs\zh\CloudPivot_v7.2重构计划\TASK-S8-02_并发与运维加固.md（★ 必须先完整阅读）
【批次总表】C:\Users\Administrator\agent\docs\zh\CloudPivot_v7.2重构计划\PARALLEL_S8批次总表.md
【上游依据】收官审计 §8.2 + S2-01 #6 / S2-02 #5 / S2-03 #11 / S3-01 #6 / S4-02 L9·L10
【预估】5–7 人日｜【状态】无待裁定项

━━━ 一、工作区与隔离（强制）━━━
    cd C:\Users\Administrator\agent
    python scripts/dev/new_session_worktree.py create --id s802 --base master
此后所有 git 操作在 .worktrees/s802/ 内；主工作区禁令与提交流程见批次总表 §三。
双远端推送：git push origin master && git push gitee master

━━━ 二、任务目标（摘要）━━━
1. 写入路径盘点（docs/zh/并发与写入路径盘点.md）：UnifiedTraceStore / AuditChain / EventStore /
   DecisionLog / cost 落盘 / 技能审计分片 —— 每条标注并发模型、已有保护、**多进程下的失效模式**
2. **统一跨进程锁原语**：复用既有非阻塞 OS 文件锁模式（agent/env_config_manager、knowledge.ingest、
   self_healing/watchdog_singleton 同源）→ 提取共享工具，**严禁复制第 N 份实现**；
   提供 try_lock()（非阻塞，失败即退避/降级）与 locked(timeout)（有限等待）
3. 多写者正确性：
   · **seq 全局单调**（跨进程）：锁内分配 / 预留区间 / DB 事务，任选并说明**崩溃后语义**
   · 写丢失防护：队列满、进程崩溃、锁超时三种场景各有明确行为（记事件/审计，**不静默**）
   · 损坏防护：原子写（临时文件+rename / DB 事务）+ 校验和隔离告警
4. 决策日志轮转：按大小（可配）或按日切分；**读分片保持兼容**；旧分片纳入 S8-01 保留策略（归档不删）；
   轮转前后决策统计口径一致
5. 可观测：锁等待/冲突/超时/降级计数（否则"加固了但不知有没有生效"）

━━━ 三、已就绪前置：勿重复实现 ━━━
  • 既有锁原语与范式：agent/env_config_manager.py、agent/knowledge/ingest.py、agent/self_healing/watchdog_singleton.py
  • 写入设施：agent/observability/trace_v2.py（UnifiedTraceStore 批量 writer）、agent/audit/chain.py（单写者+seq）、
    agent/observability/events.py（EventStore）、agent/policy/decisions.py（DecisionLog，读分片已支持）
  • 审计与事件通道：agent/audit/facade.py、agent/observability/events.py
  ★ 不要新建第二套锁；不要改既有公开接口语义

━━━ 四、本任务特有硬约束 ━━━
1. 降级路径**不静默丢数据**（队列/退避/显式失败三行为均须有用例）
2. 锁可恢复：持锁进程被杀**不得死锁**；超时须显式失败 + 留痕
3. 单进程写入 p99 **不退化**（附对照数据 + clock 口径标注）
4. 若用"预留区间"分配 seq：必须说明空洞语义，且**链式哈希校验仍能通过**（不得造成"链断"误判）
5. 轮转不得改变统计口径；旧分片只能归档不能删（与 S8-01 一致）
6. 不引入外部依赖（Redis/etcd）

━━━ 五、验收与交付物 ━━━
交付：1) 并发与写入路径盘点.md；2) 统一锁工具 + 全部写入路径接入；3) 决策日志轮转；4) 并发测试（≥4 进程）
      + 冲突/崩溃场景；5) 锁与写入可观测指标；6) TASK-S8-02_验收报告.md；7) S8-02_交付结案报告_<日期>.md；
      8) 更新 00_总览 状态行
验收：逐条对照任务书 §四（含"无重复 seq/无静默丢失/无损坏"、锁恢复、性能不退化、口径一致）

━━━ 六、本地门禁 ━━━
  • 相关套件：pytest tests/unit/test_concurrency*.py / test_locking*.py（新增）+ observability/audit/policy/digestion 邻接回归
  • kwarg 扫描两条（--path agent 与 --path tests）；mypy 新增模块；importlinter（新共享工具注意依赖方向）
  • 真实提交场景验证 pre-commit；跑完门禁后 git status 检查产物漂移并还原
  • **并发测试务必可重复运行**（避免偶发绿灯）；建议固定种子/固定进程数并输出原始统计

━━━ 七、上游已知坑 ━━━
1. 并发/多进程用例在 CI 高负载分片下易抖动 → 加 @pytest.mark.timeout，轮询上界留足
2. Windows 与 Linux 的文件锁语义不同（S5-01 曾因 `os.path.normcase` 的 Windows 专属语义导致 CI 挂）
   → 平台相关断言必须 gate 或改为跨平台语义
3. 用例若落盘必须**显式传路径或 autouse 隔离**（S3-02/S3-03 两次污染教训）
4. 后台 writer 线程需在用例结束前 flush/stop，避免假失败
5. 门禁脚本产物漂移要还原

━━━ 八、回报格式 ━━━
见批次总表 §三"统一回报格式"；额外须附：并发测试原始输出（进程数/条数/重复 seq 断言）、性能对照（含 clock 口径）、锁可观测证据
```

---

## 三、开工自查清单

- [ ] 已读任务书；确认"不新建第二套锁"与崩溃语义要求
- [ ] `--base master`、id=`s802`；worktree 内工作
- [ ] 全部写入路径已盘点并接入锁
- [ ] ≥4 进程并发测试：无重复 seq / 无静默丢失 / 无损坏
- [ ] 锁被杀可恢复、超时显式失败留痕
- [ ] 决策日志轮转生效且读分片兼容、口径一致
- [ ] 单进程 p99 不退化
- [ ] 平台相关断言已 gate（Windows/Linux 差异）
- [ ] 双远端同点推送
