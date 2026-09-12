# START-S8-01 数据生命周期治理（分发壳 · 可直接复制）

> **本文件不是任务书。** 任务规格 → [`TASK-S8-01_数据生命周期治理.md`](TASK-S8-01_数据生命周期治理.md)（★ 必须先完整阅读）
> 批次与通用约定 → [`PARALLEL_S8批次总表.md`](PARALLEL_S8批次总表.md)｜基线：`master` / `7e094eab`｜预估：5–7 人日

---

## 一、开工前确认

1. `--base master`；worktree id 用 **`s801`**。
2. 依赖 S2/S3/S5 写入设施——**均已结案**，无待裁定项。
3. **三条不可越界原则（批次总表 §二）**：① 审计链永久保留、只归档不删；② 默认保守（只归档不删除、首跑 dry-run）；③ 不改变既有统计口径。

---

## 二、启动提示词（整段复制给新会话）

```
【任务】TASK-S8-01 — 数据生命周期治理（TTL / 保留策略 / 冷归档 / 可还原）
【任务书】C:\Users\Administrator\agent\docs\zh\CloudPivot_v7.2重构计划\TASK-S8-01_数据生命周期治理.md（★ 必须先完整阅读）
【批次总表】C:\Users\Administrator\agent\docs\zh\CloudPivot_v7.2重构计划\PARALLEL_S8批次总表.md
【上游依据】docs/zh/CloudPivot_v7.2重构计划/V72_全计划收官审计报告.md（§8.2 生产化类）+ S2-01 #7 / S2-02 #4 / S2-03 #10 / S3-01 #5 / S3-02 #6 / S3-03 #8
【预估】5–7 人日｜【状态】无待裁定项

━━━ 一、工作区与隔离（强制）━━━
    cd C:\Users\Administrator\agent
    python scripts/dev/new_session_worktree.py create --id s801 --base master
此后所有 git 操作在 .worktrees/s801/ 内；主工作区禁令与提交流程见批次总表 §三。
双远端推送：git push origin master && git push gitee master

━━━ 二、任务目标（摘要）━━━
1. 数据资产普查：机械扫描 data/、agent/data/ 与各模块默认落点，覆盖 ≥7 类运行时数据
   （unified_traces / 链式审计链与每日根 / 事件流分片 / 判定集历史 / 消化草稿 / 灰度·抽检台账 / 决策日志 / cost_daily）
2. 产出 docs/zh/数据生命周期策略.md：每类含 保留期 / 归档方式 / 删除方式 / 可否删除 / 执行者 / 依据；
   **审计链与每日根显式标注"禁止删除"**
3. 新增 agent/retention/：RetentionPolicy / Archiver（温·冷）/ Restorer（可还原）/ PurgeGuard（删除前校验红线与引用）
4. 归档自描述：含 schema 版本 / 时间范围 / 记录数 / 校验和；压缩落地 data/archive/<class>/<period>.*
5. 调度 register_retention_job()（建议每周，**默认关闭**，CP_RETENTION_ENABLED 才注册）；
   **首次运行必须 dry-run**（列清单与体积）；每次执行入链式审计（action=retention.run，记条数与体积）
6. **指标口径可复算验证**：至少 2 个既有指标（如 UTC 周成本、消化吞吐）在归档前后结果一致；
   若某类归档后无法复算 → 改为"保留聚合摘要 + 明细归档"双轨
7. 记忆类删除必须走 S5-01 的"删记忆不删证据"路径（审计匿名化、链保留）

━━━ 三、已就绪前置：勿重复实现 ━━━
  • 台账与审计：agent/observability/trace_v2.py、agent/audit/chain.py（链 + Merkle 根 + verify_chain）
  • 事件流：agent/observability/events.py（按日分片）+ log_archiver 既有归档样例
  • 判定集：agent/digestion/cases.py（CaseStore，已有 MAX_STORE_HISTORY=5 裁定）
  • 草稿与灰度台账：agent/digestion/{generation,shadow}.py 的落点
  • 决策日志：agent/policy/decisions.py（读分片已支持）
  • 记忆删除语义：agent/memory/forgetting.py（先快照后删除 + 审计匿名化）
  ★ 不要自建第二套归档/校验；**不要删审计链**

━━━ 四、本任务特有硬约束 ━━━
1. **审计链与每日根禁止删除**（PurgeGuard 必须拦截"误传审计类"）
2. dry-run 默认先跑、不落盘；执行需显式确认
3. 归档必须**可还原**（抽样往返一致）
4. 归档/轮转**不得改变统计口径**（历史指标仍可复算）
5. 调度**默认关闭**；默认策略 = 只归档不删除（仅显式标"可删"的类如草稿可删）
6. 不编造体积/条数：所有数字来自实际扫描与执行输出

━━━ 五、验收与交付物 ━━━
交付：1) 数据生命周期策略.md；2) agent/retention/ + scripts/scan_data_assets.py；3) 调度（默认关闭）+ dry-run + 审计留痕；
      4) 指标复算一致性报告；5) TASK-S8-01_验收报告.md（含策略表、dry-run 输出、还原抽样证据、复算对照）；
      6) S8-01_交付结案报告_<日期>.md；7) 更新 00_总览 状态行
验收：逐条对照任务书 §四（含红线拦截、可还原、口径不变、默认关闭）

━━━ 六、本地门禁 ━━━
  • 相关套件：pytest tests/unit/test_retention*.py（新增）+ observability/audit/digestion 邻接回归
  • kwarg 扫描两条（--path agent 与 --path tests）；mypy 新增模块；importlinter
  • 真实提交场景验证 pre-commit；跑完门禁后 git status 检查产物漂移并还原

━━━ 七、上游已知坑 ━━━
1. 归档/还原类用例务必在**临时目录**做（勿动真实 data/）；用显式路径参数
2. 时间类断言（保留期、周期）用**注入时钟**（跨午夜/时区问题在既有轮次踩过）
3. 事件流/台账有后台 writer 线程 → 用例结束前 flush，避免"文件仍在写"的假失败
4. 审计链校验对**空洞敏感** → 若归档导致 seq 不连续，必须在链语义上解释清楚（本任务指向"只归档不改链"）
5. 门禁脚本产物漂移要还原

━━━ 八、回报格式 ━━━
见批次总表 §三"统一回报格式"；额外须附：策略表摘要（类别/保留期/可否删除）、dry-run 输出、还原抽样证据、指标复算对照
```

---

## 三、开工自查清单

- [ ] 已读任务书与批次总表 §二 三条不可越界原则
- [ ] `--base master`、id=`s801`；worktree 内工作
- [ ] ≥7 类数据均有成文策略；审计类标"禁止删除"
- [ ] PurgeGuard 拦截误删审计（用例）
- [ ] 归档可还原（抽样往返一致）
- [ ] 归档前后指标一致（≥2 个）
- [ ] 首跑 dry-run；调度默认关闭
- [ ] 双远端同点推送
