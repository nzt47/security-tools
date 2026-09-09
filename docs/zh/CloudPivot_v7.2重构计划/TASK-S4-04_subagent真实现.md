# TASK-S4-04 subagent 真实现（八要素上下文包 + JSON Lines 协议 + 临时凭据 + 工具裁剪）

> 所属阶段：S4 治理与安全｜依赖：S2-01（Trace）、S4-01（Actor 矩阵）｜预估：6–10 人日
> 来源设计文档：`CloudPivot_v7.2_final_合并归档(智谱审核版).md` §3.9（委派契约八要素 + 回收三件套）/§3.10（CLI 通道 JSON Lines）/§5.9（临时凭据 TTL）
> 验收报告归档于本目录 `docs/zh/CloudPivot_v7.2重构计划/`

---

## 一、目标描述

把云枢**占位的 subagent 容器升级为真实委派执行器**（P7 修正；现状：`agent/subagent/` 骨架存在——container/barrier/lifecycle/sandbox/summarizer——但为占位实现，不调 LLM、无并行编排、无委派协议），承载 v7.2 三层组合拳第 3 层（委派与经验萃取）与 process_distill 的并行蒸馏升级：

1. **委派契约八要素**（§3.9）：①目标 ②约束 ③已有成果 ④禁止事项 ⑤产物格式 ⑥预算令牌 ⑦超时 ⑧回调地址（+tenant/subject/TraceContext）——`task_file` JSON 物化。
2. **CLI 通道协议（§3.10）**：`<agent_cli> -p <task_file.json> --output-format json --max-turns N`；输出 JSON Lines；解析失败→重试 1 次→降级纯文本+LLM 抽取→仍失败记 E_UPSTREAM_FORMAT。
3. **回收三件套**：产物 + 轨迹 + 反思（上游自评+云枢复评）；缺任一 → 不计成本核算、视为浪费、阻塞 stage 推进。
4. **临时凭据（§5.9）**：TTL ≤ 任务时长，任务结束即销毁；每来源独立凭据；manifest 禁存长期密钥。
5. **能力最小暴露（§5.7 机制 3 + §7.0）**：sub_agent 工具集为裁剪子集（无记忆读写/核心改写/审批权）；actor=sub_agent 权限按矩阵（配合 S4-01）。
6. **轨迹接入**：委派执行全程写 S2-01 Trace（actor=sub_agent，parent=编排任务）。

## 二、执行步骤

### 步骤 1：委派契约与上下文包
- 新增 `agent/subagent/delegation.py`：`DelegationContext`（八要素 + tenant/subject/TraceContext 序列化）、`build_task_file(ctx) -> task_file.json`（对齐 §3.10 物化格式）。
- 校验器：八要素缺任一 → 拒绝委派（80% 委派失败源于写得太含糊——§3.9）。

### 步骤 2：真执行器与并行编排
- 升级 `container.py`/`lifecycle.py`：真实 LLM 执行循环（对齐 `process_distill/distiller.py` 的 worker 模式，并支持其切换/复用）——ThreadPoolExecutor 并行多任务 + barrier（并发上限与回压，§4.2）。
- CLI 通道：本地执行入口 `<cli> -p task_file --output-format json --max-turns N`（可配置调用真实 agent CLI 或内部 executor 等价实现，按 S0-01 RFC 形态裁决）；输出解析器（JSON Lines → 失败重试 1 次 → 纯文本+LLM 抽取 → E_UPSTREAM_FORMAT）。
- 回调：任务结束回调地址（记录结果/触发后续），超时注入（上下文包⑦）。

### 步骤 3：回收三件套
- `collect(agent_id) -> {artifacts, trace, reflection}`：产物（任务文件输出）、轨迹（S2-01 子 Trace 链）、反思（上游自评 + 云枢复评——复用既有反思评估器或 LLM judge）。
- 三件套缺任一 → 标记浪费、不计成本核算、阻塞相关 stage 推进（联动 S3 stage 迁移证据）。

### 步骤 4：安全（临时凭据 + 工具裁剪）
- 凭据：临时凭据注入环境（TTL ≤ 任务时长），任务结束销毁（finally + 单测验证销毁）；每来源独立凭据。
- 工具裁剪：sub_agent 可见工具白名单（不含记忆读写/核心改写/审批权）——通过执行器工具注入层实现，单测断言裁剪集外工具不可用。
- 沙箱（`sandbox.py` 已有骨架）：第三方执行默认隔离（无宿主网络/无 SSH agent/无 $HOME——§5.9），落地为执行配置默认值。

### 步骤 5：回归与归档
- 回归：process_distill/subagent 相关套件零回归；新增单测（八要素校验、CLI 解析三级降级、并行编排、回收三件套、凭据 TTL 销毁、工具裁剪、Trace actor 标注）≥50 例、覆盖率 ≥80%。
- 撰写 `TASK-S4-04_验收报告.md`（含一次真实委派端到端样例）。

## 三、预期成果

1. `agent/subagent/delegation.py`（八要素 + task_file + 校验）。
2. 真执行器（LLM 循环 + 并行编排 + CLI 通道 + 输出解析三级降级）。
3. 回收三件套 collect + 缺失判定。
4. 临时凭据 TTL 销毁 + 工具裁剪 + 隔离沙箱默认值。
5. `TASK-S4-04_验收报告.md`。

## 四、评估标准（验收清单）

- [ ] 八要素缺任一即拒绝委派（用例）
- [ ] task_file 符合 §3.10 物化格式；CLI 入口可执行
- [ ] 输出解析：JSON Lines 成功 → 失败重试 1 次 → 纯文本+LLM 抽取 → E_UPSTREAM_FORMAT（四级用例）
- [ ] 并行委派受并发上限与回压约束（测试）
- [ ] 回收三件套齐全才计成本；缺任一标记浪费并阻塞 stage 推进
- [ ] 临时凭据 TTL 结束销毁（注入后强制销毁用例）；每来源独立凭据
- [ ] 裁剪工具集外调用被拒（无记忆读写/核心改写/审批权断言）
- [ ] 委派全程 Trace 带 actor=sub_agent + parent_trace_id
- [ ] 既有 process_distill/subagent 套件零回归；新增单测全绿、覆盖率 ≥80%
- [ ] 真实委派端到端样例在验收报告可复现
