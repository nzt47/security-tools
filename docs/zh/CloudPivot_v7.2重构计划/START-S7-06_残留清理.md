# START-S7-06 残留清理（分发壳 · 可直接复制）

> **本文件不是任务书。** 任务规格 → [`TASK-S7-06_残留清理.md`](TASK-S7-06_残留清理.md)（★ 必须先完整阅读）
> 批次与通用约定 → [`PARALLEL_S7批次总表.md`](PARALLEL_S7批次总表.md)｜基线：`master` / `531515e0`｜预估：3–4 人日

---

## 一、开工前确认

1. `--base master`；worktree id 用 **`s706`**。
2. 依赖 S3-03（ROI 报告）、S4-04（回收三件套）、S5-02（指标字典）、S4-02/S4-03（降级落地）——**均已结案**。
3. 本任务是收官审计残留的**低风险收尾**（R5 已并入 S7-04、R6 属既有非本计划、R8 属环境清理）：
   **R1** 判定集构建成本计入 ROI｜**R4** 委派成功率机械信号清单｜**R7** 单机降级设施表。

---

## 二、启动提示词（整段复制给新会话）

```
【任务】TASK-S7-06 — 残留清理（判定集成本入 ROI / 委派成功率机械信号 / 单机降级设施表）
【任务书】C:\Users\Administrator\agent\docs\zh\CloudPivot_v7.2重构计划\TASK-S7-06_残留清理.md（★ 必须先完整阅读）
【批次总表】C:\Users\Administrator\agent\docs\zh\CloudPivot_v7.2重构计划\PARALLEL_S7批次总表.md
【上游依据】docs/zh/CloudPivot_v7.2重构计划/V72_全计划收官审计报告.md（§8.5 残留 R1/R4/R7；§三 T1/T7；§六 §5-8）
【预估】3–4 人日
【状态】依赖 S3-03/S4-04/S5-02/S4-02/S4-03 全部结案；无待裁定项

━━━ 一、工作区与隔离（强制）━━━
    cd C:\Users\Administrator\agent
    python scripts/dev/new_session_worktree.py create --id s706 --base master
此后所有 git 操作在 .worktrees/s706/ 内；主工作区禁令与提交流程见批次总表 §三。
双远端推送：git push origin master && git push gitee master

━━━ 二、R1：判定集构建成本计入 ROI ━━━
现状：agent/digestion/internalize.py::ROIReport 只消费 utc.utc_window() 的成本，**不含**判定集构建开销
      （生成用例的 LLM 调用、人工抽检工时、回放算力）→ 审计 T1 残留。
做法：
  • 采集：判定集构建路径**补埋点**（CaseStore 构建时记录 cost 事件，标注 stage=case_build）；
  • 扩展 ROIReport：新增 case_build_cost_cents（可区间估计，**必须标注估计方法与样本**）；
  • **建议（写在任务书里）**：判定集成本**单列披露，不参与摊销**——理由：一次性资产，混入摊销会扭曲"是否内化"的月成本比较；
    报告与 PR 描述中**同时给出不含/含两种 ROI**，保持透明可对比；
  • 文档：更新 ROI 公式说明与 PERF_BUDGET 相关表述。

━━━ 三、R4：委派成功率机械信号清单 ━━━
现状：审计 T7 指出"成功率判定主体可靠性"——机械信号优先原则体现在 S3-02 三层比对里，
      但**委派成功率的机械信号清单未显式化**。
做法：定义机械可验信号（优先级从高到低，写入指标字典并在复评中执行）：
  ① **产物结构**：task_file 声明的产物格式是否结构性通过（schema/必需字段）
  ② **测试/校验**：涉代码的委派，目标用例是否 fail→pass
  ③ **副作用核对**：声明副作用 vs 实际副作用集合一致性（复用 S3-02 ReplaySandbox 比对能力）
  ④ **可回放性**：产物能否重放复现（同输入同结果）
  ⑤ **LLM 复评（兜底）**：以上均不适用时使用，**必须标注 judge_kind** + 抽样人工校准
接入：
  • agent/subagent/ 回收三件套的**复评半边**改为"机械优先 + LLM 兜底"，结果标注 signal_kind；
  • agent/eval 的委派成功率/回收率指标**区分两列**（mechanical / llm），**不得混算**；
  • 样本 <20 → 只披露不考核（S5-02 口径）。

━━━ 四、R7：单机降级设施表 ━━━
产出 docs/zh/单机降级设施表.md，逐项列：**v7.2 要求 → 单机实现 → 边界/已知缺口 → 升级路径（P5）**，至少覆盖：
  Watchdog（集群+OS 服务 → watchdog_singleton 文件锁 → 集群仲裁 P5）
  审计存储（外部只追加 → 本地受保护文件+ed25519+外层根链 → WORM/S3 P5；**删除链尾不可检出**如实标注）
  出域控制（egress guard 进程外代理 → guardrails/egress_guard 进程内执行点 → 独立代理属部署侧）
  生成代码执行（Docker 沙箱 → 进程内确定性回放 + 子进程隔离 → **真实执行型产物未容器化**如实标注）
  策略引擎（OPA/WASM → 等效声明式引擎（接口形状兼容）→ 换 OPA 按需）
  多租户（隔离验证 → workspace-hash 逻辑租户 → P5）
  台账保留（TTL/冷归档/分库 → 现状无 TTL → 生产化批次）
要求：**每项可追溯到代码位置或验收报告**，并标注"已验证/未验证"。

━━━ 五、已就绪前置：勿重复实现 ━━━
  • ROI：agent/digestion/internalize.py（ROIReport）+ agent/observability/utc.py（成本同源）
  • 判定集：agent/digestion/cases.py（CaseStore 构建路径 = 埋点位置）
  • 委派与回收：agent/subagent/（回收三件套）+ agent/digestion/sandbox.py（副作用比对复用）
  • 指标字典：agent/eval/（§6.7 指标定义与计算，两列口径在此落地）
  • 降级落地证据：agent/self_healing/（watchdog_singleton/release_bundle/saga）、agent/audit/（chain）、
    agent/policy/（等效引擎）、agent/guardrails/（egress_guard）
  ★ 不要另建第二套 ROI/指标/降级实现

━━━ 六、本任务特有硬约束 ━━━
1. **不得编造数字**：ROI 估计值必须标注估计方法与样本；样本不足按 S5-02 口径只披露
2. 两列口径（mechanical/llm）**严禁混算**（用例断言）
3. R7 表中"未验证"项**不得写成已验证**（这是本任务的诚信底线）
4. 不改既有公开接口行为；新增字段以兼容方式叠加
5. 新文档链接必须通过本地 docs 链接预检（项目 CI 有此检查，曾有 3 处引用层级错误）

━━━ 七、验收与交付物 ━━━
交付：1) case_build 成本埋点 + ROIReport 双口径；2) 机械信号清单 + 复评接入 + 指标两列；
      3) docs/zh/单机降级设施表.md；4) TASK-S7-06_验收报告.md；5) S7-06_交付结案报告_<日期>.md；
      6) 更新 00_总览 状态行（并把收官审计 §8.5 的 R1/R4/R7 标注为已清理）
验收：逐条对照任务书 §四（R1/R4/R7 三组清单 + 通用项）

━━━ 八、本地门禁 ━━━
  • 相关套件：pytest tests/unit/test_digestion_internalize*.py、test_subagent*.py、test_eval*.py（按实际调整）
  • 邻接回归：digestion / subagent / eval 套件
  • kwarg 扫描两条（--path agent 与 --path tests）；mypy 改动模块；importlinter
  • **docs 链接预检**（本地脚本 check_docs_broken_links，或项目等价命令）
  • 真实提交场景验证 pre-commit；跑完门禁后 git status 检查产物漂移并还原

━━━ 九、上游已知坑 ━━━
1. 判定集/成本类用例会落盘 → **显式传路径或 autouse 会话级隔离**（S3-02/S3-03 两次污染）
2. 改 ROIReport 字段会影响 S3-03 既有断言与 UI 面板数据结构 → 需同步更新（兼容叠加，勿改既有字段语义）
3. 委派类用例若真实调用会花钱/耗时 → 用桩；真实路径可选并如实标注
4. 指标两列口径变更会影响 S5-02 的周报脚本输出 → 同步更新并保持字段向后兼容
5. 文档表格里的路径引用注意层级（`../` 与 `../../` 曾导致 CI 红点）

━━━ 十、回报格式 ━━━
见批次总表 §三"统一回报格式"；额外须附：R1 两种 ROI 的对照输出、R4 两列口径不混算的证据、R7 表的"已验证/未验证"统计
```

---

## 三、开工自查清单

- [ ] 已读任务书与收官审计 §8.5（R1/R4/R7 原始描述）
- [ ] `--base master`、id=`s706`；worktree 内工作
- [ ] case_build 埋点可采；双口径 ROI 输出且估计方法有标注
- [ ] 机械信号 5 条显式定义；复评带 signal_kind；两列不混算（断言）
- [ ] 样本 <20 只披露不结论
- [ ] 单机降级设施表 ≥7 项，可追溯 + 已/未验证标注诚实
- [ ] 新文档链接通过本地预检
- [ ] 邻接套件零回归；覆盖率 ≥80%
- [ ] 双远端同点推送
