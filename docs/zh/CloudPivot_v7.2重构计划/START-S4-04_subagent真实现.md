# START-S4-04 subagent 真实现（分发壳 · 可直接复制）

> **本文件不是任务书。** 任务规格 → [`TASK-S4-04_subagent真实现.md`](TASK-S4-04_subagent真实现.md)（★ 执行会话必须完整阅读）
> 批次与通用约定 → [`PARALLEL_并行铺开总表.md`](PARALLEL_并行铺开总表.md)
> 基线：`master` / `15eae00d`｜波次：**第二波**｜预估：6–10 人日
> ⚠ 依赖 **S4-01**（Actor 矩阵的 sub_agent 授权子集清单）→ 待其结案或接口定稿后开工；落点 `agent/subagent/` 与第一波无重叠。

---

## 一、开工前确认

1. `--base master`；worktree id 用 **`s404`**。
2. **开工前置检查**：`S4-01` 已结案或其矩阵/授权语义已定稿（本任务要消费"sub_agent 可执行 capability 的授权子集"）。
3. 无待裁定项。

---

## 二、启动提示词（整段复制给新会话）

```
【任务】TASK-S4-04 — subagent 真实现（八要素委派 + JSON Lines 通道 + 回收三件套 + 临时凭据 + 工具裁剪）
【任务书】C:\Users\Administrator\agent\docs\zh\CloudPivot_v7.2重构计划\TASK-S4-04_subagent真实现.md（★ 必须先完整阅读）
【批次总表】C:\Users\Administrator\agent\docs\zh\CloudPivot_v7.2重构计划\PARALLEL_并行铺开总表.md
【所属计划】C:\Users\Administrator\agent\docs\zh\CloudPivot_v7.2重构计划\00_总览_审计结论与重构总计划.md
【上游设计文档】C:\Users\Administrator\Desktop\设计思路\CloudPivot_v7.2_final_合并归档(智谱审核版).md
                （相关章节：§3.9 委派八要素 + 回收三件套 / §3.10 CLI 通道 JSON Lines / §5.9 临时凭据 / §5.7 机制3 能力最小暴露 / §2.4 三层组合拳）
【预估】6–10 人日
【状态】第二波：依赖 S4-01（Actor 矩阵/授权子集）；S2-01 已交付 TraceContext.child()

━━━ 一、工作区与隔离（强制）━━━
    cd C:\Users\Administrator\agent
    python scripts/dev/new_session_worktree.py create --id s404 --base master
此后所有 git 操作在 .worktrees/s404/ 内；主工作区禁令与提交流程见批次总表 §三。
双远端推送：git push origin master && git push gitee master

━━━ 二、任务目标（摘要）━━━
现状：agent/subagent/ 为**占位骨架**（container / barrier / lifecycle / sandbox / summarizer，不调 LLM、无并行编排、
无委派协议）——本任务把它升级为真实委派执行器：
1. **委派契约八要素（§3.9）**：①目标 ②约束 ③已有成果 ④禁止事项 ⑤产物格式 ⑥预算令牌 ⑦超时 ⑧回调地址
   （+ tenant/subject/TraceContext）；物化为 task_file.json；**缺任一即拒绝委派**（80% 委派失败源于含糊）
2. **CLI 通道物理协议（§3.10）**：`<agent_cli> -p <task_file.json> --output-format json --max-turns N`；
   输出要求 JSON Lines；**解析三级降级**：失败重试 1 次 → 纯文本 + LLM 抽取 → 仍失败记 E_UPSTREAM_FORMAT
3. **真执行器 + 并行编排**：真实 LLM 执行循环 + ThreadPoolExecutor 并行 + barrier（并发上限与回压）
4. **回收三件套（§3.9）**：产物 + 轨迹 + 反思（上游自评 + 云枢复评）；**缺任一 → 标记浪费、不计成本、阻塞 stage 推进**
5. **安全（§5.9 / §5.7 机制3）**：临时凭据 TTL ≤ 任务时长且任务结束销毁（每来源独立凭据）；
   子代理工具集为**裁剪子集**（不含记忆读写 / 核心改写 / 审批权）；第三方执行默认隔离（无宿主网络 / 无 SSH agent / 无 $HOME）
6. **轨迹**：委派全程写 S2-01 统一 Trace，actor=`sub_agent`、parent_trace_id 串联

━━━ 三、已就绪前置：勿重复实现 ━━━
  • 骨架：agent/subagent/（container / barrier / lifecycle / sandbox / summarizer）——在既有结构上升级，勿另起新包
  • 并行 worker 既有范式：agent/process_distill/distiller.py（ThreadPoolExecutor + 隔离 worker + LLM 缺失降级）
    ★ 参考其模式；若复用其代码路径，**不得改变其既有行为**（守不易）
  • **S2-01 已交付**：agent/observability/trace_v2.py → TraceContext.child()（S2-01 遗留 #5 指定本任务消费，已交付并单测）
  • 事件与审计：agent/observability/events.py（ACTOR_SUB_AGENT 常量）、agent/audit/facade.py::audit.record(...)
  • 授权清单来源（S4-01 交付）：Actor 矩阵中 sub_agent 行的"授权子集"定义 → 工具裁剪白名单与之对齐
  • 凭据设施：既有 SecretStore / .env 管理（勿把长期密钥写进 manifest —— §2.3 硬约束）

━━━ 四、本任务特有硬约束 ━━━
1. **八要素校验前置**：不合格的委派直接拒绝并说明缺哪一项（不得"猜"或补默认值蒙混）
2. **三件套缺一即不计成本核算**，且阻塞相关 stage 推进（与 S3 的消化流水线证据链一致）
3. 临时凭据必须**在 finally 中销毁**，并有单测验证销毁（隐藏失败会长期留存凭据）
4. 裁剪工具集外的调用必须被拒（含"间接调用"路径）
5. 委托输出的**外来文本按不可信处理**：不得直接拼接进工具参数或 system prompt（与 S4-03 的 taint 规则一致）
6. 不得让 subagent 绕过审批：sub_agent 不可 approve/reject（S4-01 矩阵）

━━━ 五、验收与交付物 ━━━
交付：1) agent/subagent/delegation.py（DelegationContext / build_task_file / 八要素校验）；
      2) 真执行器（LLM 循环 + 并行编排 + CLI 通道 + 三级降级解析）；
      3) 回收三件套 collect + 缺失判定；4) 临时凭据 TTL 销毁 + 工具裁剪 + 隔离默认值；
      5) TASK-S4-04_验收报告.md（含一次真实委派端到端样例）；6) S4-04_交付结案报告_<日期>.md；7) 更新 00_总览 状态行
验收：逐条对照任务书 §四（八要素拒绝 / 三级降级 / 并行回压 / 三件套计费 / TTL 销毁 / 裁剪断言 / Trace actor / S2-01 #5 用 child()）

━━━ 六、本地门禁 ━━━
  • 相关套件：pytest tests/unit/test_subagent*.py tests/unit/test_process_distill.py（邻接，勿回归）
  • 邻接回归：pytest tests/unit/test_trace_v2*.py + tests/unit/test_events*.py + approval 邻接（越权断言）
  • 全量抽查：pytest -m "not slow" -p no:randomly
  • kwarg 扫描两条：--path agent --min-risk HIGH；--path tests --min-risk HIGH
  • mypy 新增/改动模块 + 既有阻塞模块；importlinter lint --config .importlinter
  • 真实提交场景验证 pre-commit；跑完门禁后 git status 检查产物漂移并还原

━━━ 七、上游已知坑 ━━━
1. **不要真调外部 agent CLI**（环境可能无该 CLI/凭证）→ 提供可注入的执行器接口，测试用桩；真实调用作为可选路径并如实标注
2. 并行用例在 CI 高负载分片下易抖动 → 造数/等待类用例加 @pytest.mark.timeout，轮询上界留足（S3-01 曾因此 CI 双败）
3. 委派/子代理类用例会产生运行时目录写入 → **显式传路径或 autouse 会话级隔离**
4. 线程池 + 上下文变量：`TraceContext` 是 ContextVar 语义，跨线程需显式传递（勿假设自动继承）
5. process_distill 既有 32 例用例是基线，改动其共享路径后必须全绿

━━━ 八、回报格式 ━━━
见批次总表 §三"统一回报格式"；额外须附：一次真实（或桩注入的）委派端到端样例 + 凭据销毁验证 + 裁剪集拒绝证据
```

---

## 三、开工自查清单

- [ ] S4-01 已结案/接口定稿（拿到 sub_agent 授权子集定义）
- [ ] `--base master`、id=`s404`；worktree 内工作
- [ ] 八要素缺任一即拒绝（用例）
- [ ] 输出解析三级降级均有用例；E_UPSTREAM_FORMAT 可达
- [ ] 三件套齐才计成本；缺一阻塞 stage
- [ ] 凭据 TTL 销毁有单测；裁剪集外调用被拒
- [ ] Trace actor=sub_agent 且用 `TraceContext.child()`（跨线程显式传参）
- [ ] 未破坏 process_distill 既有行为（32 例全绿）
- [ ] 本地门禁全绿；覆盖率 ≥80%
- [ ] 双远端同点推送
