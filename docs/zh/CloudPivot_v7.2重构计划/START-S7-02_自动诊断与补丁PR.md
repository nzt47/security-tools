# START-S7-02 自动诊断与补丁 PR（分发壳 · 可直接复制）

> **本文件不是任务书。** 任务规格 → [`TASK-S7-02_自动诊断与补丁PR.md`](TASK-S7-02_自动诊断与补丁PR.md)（★ 必须先完整阅读，尤其 §一 的四条宪法式边界）
> 批次与通用约定 → [`PARALLEL_S7批次总表.md`](PARALLEL_S7批次总表.md)｜基线：`master` / `531515e0`｜预估：8–12 人日

---

## 一、开工前确认

1. `--base master`；worktree id 用 **`s702`**。
2. 依赖 S4-04（真子代理）、S5-02（L0 锚）、S3-02（回放沙箱）、S2-01/S2-02（Trace/审计）——**均已结案**。
3. **本任务只做 L1**：能修 → 产出补丁 PR → **人工合入**。**不做**自动触发、自动 push/合并、核心自改写（v7.2 §5.2 熔炉）。

---

## 二、启动提示词（整段复制给新会话）

```
【任务】TASK-S7-02 — 自动诊断与补丁 PR（自修复 L1：能修，但不自动落地）
【任务书】C:\Users\Administrator\agent\docs\zh\CloudPivot_v7.2重构计划\TASK-S7-02_自动诊断与补丁PR.md（★ 必须先完整阅读）
【批次总表】C:\Users\Administrator\agent\docs\zh\CloudPivot_v7.2重构计划\PARALLEL_S7批次总表.md
【上游设计文档】C:\Users\Administrator\Desktop\设计思路\CloudPivot_v7.2_final_合并归档(智谱审核版).md
                （相关章节：§3.9 委派八要素与回收三件套 / §3.10 CLI 通道 / §4.4 自愈 L1-L5 / §4.6 Saga / §5.2 核心改写（**本任务不做**）/ 八条不变量）
【预估】8–12 人日
【状态】依赖 S4-04/S5-02/S3-02/S2-01/S2-02 全部结案；无待裁定项

━━━ 一、工作区与隔离（强制）━━━
    cd C:\Users\Administrator\agent
    python scripts/dev/new_session_worktree.py create --id s702 --base master
此后所有 git 操作在 .worktrees/s702/ 内；主工作区禁令与提交流程见批次总表 §三。
双远端推送：git push origin master && git push gitee master

━━━ 二、任务目标（把已有零件串成受控闭环）━━━
流水线：体检（L0 锚 + 回归）→ 定位（失败用例 + Trace + 审计 + 源码切片）→ 派工（S4-04 八要素委派）
        → 隔离验证（临时 worktree：目标用例 + L0 锚 + 邻接回归）→ 产出（本地分支 + 补丁 + PR 描述 + 证据链）→ **人工合入**

━━━ 三、四条宪法式边界（违反即本任务失败）━━━
  1. **绝不自动 push 到远端、绝不自动合并**（产物止于本地分支/补丁与 PR 描述）
  2. **绝不触碰只读区**：core/auth/、core/audit/、schema/、agent/audit/chain.py、agent/security/、审计与签名相关代码
  3. **验证不过就不产出**：补丁必须同时通过「目标用例 + L0 锚」；否则丢弃并如实产出"未修复"报告（不得把未验证补丁交给人工）
  4. **全程可审计**：体检/定位/派工/验证/产出五步都写 Trace 与链式审计；不许出现不可见动作（不变量之五）

━━━ 四、实现要点 ━━━
  • 新增 agent/repair/ 包 + scripts/self_repair.py 入口；本任务**只做手动触发 + 体检报告**，不接自动触发
  • diagnose()：L0 锚（agent/eval + run_eval.py --layer L0）+ pytest --junitxml + 可选审计/告警快照；
    "无失败"也是合法结果（不编造问题）
  • locate()：聚合 UnifiedTraceStore.chain() + descriptor + 只读 git 局部历史 + 失败点 ±N 行源码切片
  • 派工：复用 agent/subagent/delegation.py（八要素缺一即拒），**子代理只读源码 + 只产出补丁文本**（无写权限/无审批权/无记忆读写）
  • 范围护栏（硬闸）：单次改动 ≤N 文件（默认 3）、单文件 ≤M 行（默认 120）；命中只读区即拒；
    若修改测试断言，**必须在 PR 描述中显式标注**供人工重点审
  • verify()：在**临时副本/临时 worktree** 打补丁 → 目标用例 fail→pass + L0 锚全过 + 邻接回归；
    三关全过才产出；任一不过即丢弃并记录
  • propose()：本地分支 repair/<date>-<slug> + 补丁 + PR 描述（问题/根因（引 Trace+审计证据）/补丁说明/验证证据/风险与 undo_hint/人工复核重点）
  • 预算与轮次上限：单次修复 token 预算 + 最多 N 轮；超限即停并留证

━━━ 五、已就绪前置：勿重复实现 ━━━
  • 委派：agent/subagent/delegation.py（八要素 + CLI 三级降级 + 并行回压 + 回收三件套 + 凭据 TTL + 工具裁剪）
  • 评测标尺：agent/eval/（**L0 锚 20 条系统不可写** + run_eval.py --layer L0）
  • 回放/比对：agent/digestion/sandbox.py（ReplaySandbox 三层比对）、gate.py（验收门）
  • 追溯：agent/observability/trace_v2.py（UnifiedTraceStore.chain/query）、agent/audit/chain.py（verify_chain）
  • 自愈层级与事故卡：agent/self_healing/levels.py（raise_incident）、saga.py（高风险补偿）、release_bundle.py（整包回滚）
  ★ 不要自建第二套委派/回放/评测

━━━ 六、验收与交付物 ━━━
交付：1) agent/repair/ + scripts/self_repair.py；2) 隔离验证三关与"不过即丢弃"；3) 范围护栏与预算轮次上限；
      4) 本地补丁 PR 产物（不 push、不合并）；5) 端到端演示（**人为注入真实小 bug** → 体检 → 产出 PR → 人工合入后 L0 通过）；
      6) TASK-S7-02_验收报告.md（含完整证据链）；7) S7-02_交付结案报告_<日期>.md；8) 更新 00_总览 状态行
验收：逐条对照任务书 §四（含"未过不产出""无 push 断言""只读区拒绝""五步审计留痕"）

━━━ 七、本地门禁 ━━━
  • 相关套件：pytest tests/unit/test_repair*.py（新增）+ subagent/eval/digestion 邻接回归
  • kwarg 扫描两条（--path agent 与 --path tests）；mypy 新增模块 + 既有阻塞模块；importlinter
  • 真实提交场景验证 pre-commit；跑完门禁后 git status 检查产物漂移并还原
  • **额外自证**：用探针/桩证明代码路径中不存在 git push / merge 调用（这是本任务的硬验收项）

━━━ 八、上游已知坑 ━━━
1. **不要真调外部 agent CLI**（环境可能无凭证）→ 执行器可注入，测试用桩，真实路径可选并如实标注
2. 隔离验证**必须在临时副本**进行；**绝不在主工作区或本会话 worktree 直接改文件**（否则污染交付）
3. 涉及 git 的操作一律**只读**（`git log/show/diff`），禁止 `checkout/reset/merge/commit`（除产物分支的创建）
4. 造数/等待类用例加 @pytest.mark.timeout（CI 高负载分片会误判）
5. 委派/子代理类用例会产生运行时写入 → 显式传路径或 autouse 隔离
6. TraceContext 是 ContextVar 语义，跨线程需显式传参（S4-04 实测）
7. L0 锚是**系统不可写**的：不得为了让补丁"过关"而改 L0 用例（属只读区范畴，命中即拒）

━━━ 九、回报格式 ━━━
见批次总表 §三"统一回报格式"；额外须附：端到端证据链（注入 bug → 体检报告 → 补丁 → 三关验证输出 → PR 描述 → 合入后 L0 通过）与"无 push/merge"自证
```

---

## 三、开工自查清单

- [ ] 已读任务书 §一 四条边界与 §五"明确不做"
- [ ] `--base master`、id=`s702`；worktree 内工作
- [ ] 体检器能发现人为注入的真实 bug（而非只跑通空路径）
- [ ] 八要素不全拒绝派工；子代理无写权限/审批权
- [ ] 只读区/文件数/行数护栏生效
- [ ] 补丁未过三关即丢弃（含"故意失败"用例）
- [ ] 代码路径无 push/merge（自证）
- [ ] 五步全部留 Trace + 审计，`verify_chain` 通过
- [ ] 端到端样例可复现
- [ ] 双远端同点推送
