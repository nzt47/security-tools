# START-S7-04 验证与探活补全（分发壳 · 可直接复制）

> **本文件不是任务书。** 任务规格 → [`TASK-S7-04_验证与探活补全.md`](TASK-S7-04_验证与探活补全.md)（★ 必须先完整阅读，含子项 A/B 分别的验收清单）
> 批次与通用约定 → [`PARALLEL_S7批次总表.md`](PARALLEL_S7批次总表.md)｜基线：`master` / `531515e0`｜预估：4–6 人日

---

## 一、开工前确认

1. `--base master`；worktree id 用 **`s704`**。
2. 本任务含**两个子项**（可分步执行、分别验收）：**A 性能补测**（清 ❓）、**B native 期探活设施**（把未来的闸门先建好）。
3. 依赖 S5-03（`PERF_BUDGET_REBASED.md`/`cost_brake`）、S3-02（判定集/沙箱）、S3-03（shadow/stage）、S4-03（自愈层级）——**均已结案**。

---

## 二、启动提示词（整段复制给新会话）

```
【任务】TASK-S7-04 — 验证与探活补全（性能预算补测 + native 期探活设施）
【任务书】C:\Users\Administrator\agent\docs\zh\CloudPivot_v7.2重构计划\TASK-S7-04_验证与探活补全.md（★ 必须先完整阅读）
【批次总表】C:\Users\Administrator\agent\docs\zh\CloudPivot_v7.2重构计划\PARALLEL_S7批次总表.md
【上游设计文档】C:\Users\Administrator\Desktop\设计思路\CloudPivot_v7.2_final_合并归档(智谱审核版).md
                （相关章节：§3.3 native = 30 天零回退 + 每周探活 / §4.4 L1-L5 自愈 / §4.5 漂移重探 / §11.2 性能预算 / §11.10 混沌清单）
【预估】4–6 人日
【状态】依赖 S5-03/S3-02/S3-03/S4-03 全部结案；无待裁定项

━━━ 一、工作区与隔离（强制）━━━
    cd C:\Users\Administrator\agent
    python scripts/dev/new_session_worktree.py create --id s704 --base master
此后所有 git 操作在 .worktrees/s704/ 内；主工作区禁令与提交流程见批次总表 §三。
双远端推送：git push origin master && git push gitee master

━━━ 二、子项 A：性能预算补测（清掉 ❓）━━━
目标：PERF_BUDGET_REBASED.md 中仍标 ❓ 的项给出实测；S6-01 已补测「状态灯 p95 0.7ms」「首屏 FCP/LCP 500ms」，
      本任务处理剩余项（**以该文档 §五 现列 ❓ 为准，逐项复测或给出测法**）：
        ① Watchdog <10s（主进程失联 → 感知/恢复时间）
        ② 熔断「达阈值 → 生效」<3s（连续失败达阈值 → 实际阻断 outbound 的时延）
        ③ 其余 ❓ 项（如有）
做法：
  • 新增 scripts/measure_perf_budget.py：可复现、可单跑、输出**原始毫秒级采样**（非估算）
  • **受控环境**：模拟心跳停止 / 用桩触发连续失败；**严禁 kill 真实生产进程**
  • 结果回填 PERF_BUDGET_REBASED.md：实测值 + 采样方法 + 环境说明
  • 单机确实测不出的（如集群相关）**保留 ❓ 并写明测法与环境要求**——不得删指标了事

━━━ 三、子项 B：native 期探活设施（把未来闸门先建好）━━━
现状：v7.2 要求 native "30 天零回退 + 每周探活"，审计 T4 指出**探活内容未定义**；当前无 native 能力，但设施可先建。
做法：
  • 新增 agent/digestion/probe.py（与消化链同包，复用其资产）：
      LivenessProbe.run(capability_id)：取该能力**判定集子集**（默认抽样 N 组，可配）→ 用 ReplaySandbox 重放 →
        记录通过率 / **真实墙钟 p99** / 成本增量
      evaluate_liveness(...)：四项退化条件 —— ① 通过率 < 基线×0.98 ② p99 回归超阈 ③ 成本显著上升 ④ 超周期未成功探活；
        任一命中即 degraded
      suggest_stage_rollback(...)：产出 native → borrowed **回退建议**（**不自动执行** stage 变更；如需执行走既有 stage_migrate + 审计）
      退化升级：raise_incident(...) 产出事故卡（agent/self_healing/levels.py；六要素齐才可 resolved）
  • 调度：register_liveness_job()（每周；**默认关闭**，CP_DIGESTION_LIVENESS_ENABLED=true 才注册——安全底线）
  • 作用范围：对 internalized/native 能力生效；无此类能力时 run() 返回"无可探活能力"（**不报错、不编造**）
  • 与 S3-02 的 30 天**漂移重探**明确区分：重探 = 上游契约漂移；探活 = **自身实现退化**（报告中并列说明，避免混淆）

━━━ 四、已就绪前置：勿重复实现 ━━━
  • 判定集与沙箱：agent/digestion/cases.py（CaseSet/CaseStore）、sandbox.py（ReplaySandbox 三层比对）
  • stage 迁移与门：agent/digestion/stage.py（stage_migrate/evaluate_migration）、gate.py（PassportStore）
  • 灰度与内化：agent/digestion/shadow.py（真实墙钟 p99 口径）、internalize.py
  • 自愈层级与事故卡：agent/self_healing/levels.py（HealLevel/raise_incident）、watchdog_singleton.py
  • 性能预算现状：docs/PERF_BUDGET_REBASED.md（§五 已列 ❓ 与复测方案）
  ★ 不要自建第二套回放/判定集/事故卡

━━━ 五、本任务特有硬约束 ━━━
1. 探活调度**默认关闭**（安全底线），需显式环境变量才注册；开启后有预算上限
2. 退化只产出**建议**，不自动 rollback / 不自动改 stage（人工或既有审批门）
3. **不得编造数字**：性能项无实测一律保留 ❓ + 给测法；"无 native 能力"时探活如实返回空
4. 混沌/故障注入在受控环境进行，不得影响主工作区与生产服务
5. 事故卡六要素齐备才可 resolved（S4-03 语义）

━━━ 六、验收与交付物 ━━━
交付：1) scripts/measure_perf_budget.py + PERF_BUDGET_REBASED.md 更新；2) agent/digestion/probe.py
      （LivenessProbe / evaluate_liveness / suggest_stage_rollback / register_liveness_job）；
      3) 探活退化 → 事故卡 + stage 回退建议（不自动执行）；4) TASK-S7-04_验收报告.md；
      5) S7-04_交付结案报告_<日期>.md；6) 更新 00_总览 状态行
验收：逐条对照任务书 §四（子项 A 与 B 各自清单）

━━━ 七、本地门禁 ━━━
  • 相关套件：pytest tests/unit/test_digestion_probe*.py（新增）+ digestion/self_healing/cost_brake 邻接回归
  • kwarg 扫描两条（--path agent 与 --path tests）；mypy 新增模块；importlinter（新模块注意依赖方向）
  • 真实提交场景验证 pre-commit；跑完门禁后 git status 检查产物漂移并还原

━━━ 八、上游已知坑 ━━━
1. **落盘用例必须显式传路径或 autouse 隔离**（S3-02/S3-03 两次运行时区污染）
2. 性能断言避免硬编码墙钟（CI 覆盖率插桩抖动）→ 用相对断言 + **标明 clock 口径**（这正是子项 A 要产出的东西）
3. 时间/周期类用例（每周探活、30 天窗口）用**注入时钟**，勿依赖真实墙钟
4. 探活涉及 stage 建议但**不得**触发真实 stage 变更（用例断言 stage 未变）
5. 造数型用例加 @pytest.mark.timeout（CI 分片高负载会误判）
6. 事故卡与告警通道有既有实现，勿重复留痕

━━━ 九、回报格式 ━━━
见批次总表 §三"统一回报格式"；额外须附：性能实测原始采样（或 ❓ 项测法说明）+ 探活演示记录（含"退化建议但不自动执行"的证据）
```

---

## 三、开工自查清单

- [ ] 已读任务书，确认子项 A/B 分步计划
- [ ] `--base master`、id=`s704`；worktree 内工作
- [ ] 性能实测有原始采样 + 环境说明；测不出的保留 ❓ 并给测法
- [ ] 探活对 internalized/native 生效；无能力时安全返回
- [ ] 四项退化条件各自有触发用例
- [ ] 退化只出建议，**不自动改 stage**（断言）
- [ ] 调度默认关闭；事故卡六要素齐备
- [ ] 未编造数字；邻接套件零回归
- [ ] 双远端同点推送
