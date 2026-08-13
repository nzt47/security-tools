# D2/D3 缺陷替代方案技术设计——API 型架构下的上下文工程学习（CEL）框架

> 关联主文档：`docs/zh/智能体学习机制重构计划/智能体学习机制理想设计.md`（§3.2 缺陷 D2、D3）
> 适用范围：云枢(Yunshu) API 型 LLM 架构
> 文档版本：2026-08-12 v1.0
> 关联任务：TASK-01 ~ TASK-05、TASK-07、TASK-08

---

## 一、背景与问题重述

### 1.1 缺陷回顾（主文档 §3.2）

- **D2（参数记忆不可行）**：设计思路主张"非参数记忆（经验库/Skill库）↔ 参数记忆（模型权重）双向转换，形成持续进化闭环"。云枢通过 API 调用外部 LLM，**权重不可变**；微调成本不可承受、无训练基建。若照搬"参数记忆"将直接落空。
- **D3（TTL 与 LLM 不可变冲突）**：设计思路主张**测试时学习**（MIA 框架：推理过程中实时更新规划师参数，"边做边学"）。云枢的规划/认知全部依赖外部 LLM，**不存在可在线更新参数的自研规划师**。若照搬 TTL 将无法落地。

### 1.2 云枢约束清单（【不易】不变式）

| # | 约束 | 说明 |
|---|---|---|
| C1 | 模型权重不可变 | 无训练/微调管线，任何方案不得假设可改权重 |
| C2 | 成本敏感 | `configs/models.yaml` 已声明 `cost_limits` 但未强制；学习动作必须有预算护栏 |
| C3 | 配置驱动、可回滚 | 一切更新工件版本化、可回滚（项目既有 `bump_version/rollback_version` 模式） |
| C4 | 已有资产必须复用 | `workflow_learning` 在线闭环、`skills_mgmt` 检索/审核/版本、`knowledge` 五步闭环、`feedback.py`、`planning/reflector`、`lifetrace` |
| C5 | 主链路零退化 | 任何改造不得破坏 orchestrator 既有 `process()` 链路行为 |

### 1.3 设计目标

1. 用**上下文内容优化**与**外部策略工件更新**两类机制，等价覆盖设计思路中"参数记忆"与"TTL"想达成的能力目标。
2. 复用现有模块为主，最小新增；新增点与 TASK-01~05 的任务边界对齐。
3. 每个学习动作**可度量、可门控、可回滚**。

---

## 二、总体方案：上下文工程学习（CEL, Context-Engineering Learning）

### 2.1 核心定义

在模型权重不可变的前提下，**"学习" = 优化两个对象**：

- **上下文内容（给 LLM 看什么）**：记忆检索片段、Skill/工作流提示、工具定义、系统提示模板 —— 对应 D2，本文称**记忆通道**。
- **外部策略工件（用什么策略执行）**：工作流模板、Skill 参数、反思经验、阈值规则 —— 对应 D3，本文称**策略通道**。

> 能力公式（替代伪数学公式 D1）：`能力 = f(基础模型固定权重, ContextAssembler, 外部策略工件集, 学习信号管线)`

### 2.2 总体架构图

```
                       ┌────────────────────────────────────────────────┐
                       │            学习信号采集层（Signals）              │
                       │  feedback │ 反思 │ 执行结果 │ 新颖事件 │ 交互轨迹   │
                       └───────┬─────────────────────────────┬─────────┘
                               │      门控 + 预算护栏(TASK-03) │
                    ┌──────────▼──────────┐        ┌──────────▼──────────────┐
                    │ 记忆通道·沉积管线      │        │ 策略通道·PolicyUpdateBroker│
                    │ (D2)记忆→卡片→Skill   │        │ (D3)工作流/Skill参数/经验  │
                    │       /工作流        │        │        /规则             │
                    └──────────┬──────────┘        └──────────┬──────────────┘
                               │ 版本化+强制审核链(TASK-04)      │ 版本化+门控+回滚(TASK-05)
                    ┌──────────▼──────────┐        ┌──────────▼──────────────┐
                    │ 长期检索记忆          │        │ 可更新策略工件库          │
                    │ VectorStore+BM25     │        │ workflow repo /          │
                    │ +lifetrace+reflection│        │ skills_mgmt / rules      │
                    └──────────┬──────────┘        └──────────┬──────────────┘
                               └──────────────┬────────────────┘
                                              ▼
                                   ContextAssembler（上下文组装器）
                      系统提示 │ 记忆检索片段 │ Skill/工作流提示 │ 工具定义
                                              ▼
                                        LLM（权重不可变）
                                              ▼
                                    orchestrator 主链路 process()
```

### 2.3 与设计思路的能力对照表

| 设计思路主张 | 想达成的能力 | CEL 等价实现 | 状态 |
|---|---|---|---|
| 参数记忆吸收内化能力 | 能力随使用沉淀 | 程序性记忆（Skill/工作流）承担内化 | ◎ 复用 |
| 非参数↔参数双向转换 | 经验↔能力互转 | 沉积管线（上行）+ 实例化管线（下行） | ◐ 接线中 |
| 测试时学习（更新规划师参数） | 边做边学、任务间改进 | 测试时策略更新 TTPU（流程间更新工件） | ◐ 接线中 |
| 推理中实时优化 | 当轮任务即时改进 | 会话内工作记忆即时注入（检索/经验） | ◐ 部分 |

---

## 三、D2 替代方案：双层上下文记忆架构

### 3.1 三层记忆模型（替代"非参数/参数"二分）

| 层 | 定位 | 云枢对应实现 | 读写方式 |
|---|---|---|---|
| **工作记忆**（Working Memory） | 当前会话上下文 | `memory/memory_manager.py` 的 `get_context()`（摘要+最近 20 条）+ `planning/react.py` 的 hints 回灌 | 会话内读写 |
| **长期检索记忆**（Long-term Retrieval Memory） | 跨会话经验/知识 | `VectorStore` + `knowledge/search.py`（BM25+向量+RRF+reranker）+ `lifetrace/` + 反思经验（TASK-02 入检索面） | 检索注入 |
| **程序性记忆**（Procedural Memory） | 可复用能力封装 | `skills_mgmt`（Skill）+ `workflow_learning`（工作流模板） | 沉积写入、实例化读出 |

**关键翻译**：设计思路"参数记忆吸收内化能力" → 云枢"**程序性记忆（Skill/工作流）承担能力内化**"；"双向转换" → "**沉积（Deposition）与实例化（Instantiation）两条管线**"。

### 3.2 上行沉积管线（Deposition Pipeline）

数据流：`交互轨迹/反思/反馈 → 知识 Note/卡片 → Skill DRAFT → 强制审核链 → PUBLISHED`

- 本设计**不重复实现**，仅定义 schema 契约，落地归属：
  - 反思沉淀：TASK-02（`self_reflect` 入检索面 + `learn_from_experience` 接线）；
  - 卡片→Skill：TASK-04（`knowledge_bridge` 连接器）；
  - 反馈→进化：TASK-05（`feedback_agent`）。
- **契约**（供任务对接，字段以 TASK-02/04 变更说明为准）：
  - 反思记录：`{task_id, input_hash, score, suggestions[], created_at}`；
  - 卡片→Skill 映射：卡片 `one_line_insight / core_points` → Skill `description / body`，幂等标记 `converted_to_skill`。

### 3.3 下行实例化管线（Instantiation Pipeline）

orchestrator 组装上下文时，从三层拉取（对应现有代码路径）：

| 层 | 拉取动作 | 现有实现 |
|---|---|---|
| 工作记忆 | `get_context()` 摘要+近况 | `memory/memory_manager.py` |
| 长期检索 | 对任务 query 检索 topK + 反思经验注入 | `knowledge/search.py` + `planning/reflector.get_advice_for_task()`（D17 已实现注入，待 TASK-02 补数据源） |
| 程序性 | workflow 拦截（0-Token 命中）→ Skill `match()`/`load_instruction()` | `agent/orchestrator/_workflow_learning_layer_match()` + `skills_mgmt/loader.py` L1/L2 |

### 3.4 ContextAssembler 设计（新增/收口）

**决策（遵【简易】）**：优先**增强现有** `agent/orchestrator/prompt_builder.py`，不新建平行组装器；若收口改造过大，则新增 `agent/context/assembler.py` 并让 `prompt_builder` 委托它。二选一以 TASK-01/02 实施时的最小 diff 为准。

接口定义：

```python
# agent/context/assembler.py（或增强 prompt_builder.py）
class ContextAssembler:
    """统一组装 LLM 上下文：工作记忆 + 长期检索 + 程序性记忆 + 工具定义。"""

    def assemble(self, task: str, session, mode: str) -> PromptContext:
        wm = self.working_memory.get_context(session)          # 现有 MemoryManager
        ltm = self.long_term.retrieve(task, top_k)             # 现有检索 + 反思经验
        pm  = self.procedural.match(task, mode)                # workflow 拦截层 + skill loader
        return PromptContext(
            system=self.system_prompt(session),
            memories=wm + ltm,
            skills=pm.instructions,       # load_instruction 产物
            tools=self.tools_whitelist(mode),
        )
```

**改造点（随 TASK-01/02）**：将 orchestrator 中分散的 `prompt_builder` 调用、workflow 拦截层、skill 匹配收口为一条组装链；**不改变各模块内部实现**，只统一调度顺序与数据结构。

### 3.5 检索参数自适应（新设计，低优先级，随 TASK-05 后可选）

- 现状：`skills_mgmt.loader` 的 RRF 权重 / `min_score` 阈值静态配置。
- 增强：在"命中→成功"与"命中→失败"反馈下对 `retrieval_weights` / `min_score` 做小步微调，版本化存储于 `data/learning/retrieval_policy.json`，超预算/越界自动回退。
- 护栏：受 TASK-03 `LearningBudget` 约束；默认关闭，先观察后调。

---

## 四、D3 替代方案：测试时策略更新（TTPU, Test-Time Policy Update）

### 4.1 与 TTL 的本质差异与等价性论证

| | 测试时学习（TTL，设计思路） | 测试时策略更新（TTPU，本方案） |
|---|---|---|
| 更新对象 | 自研规划师模型参数 | 外部策略工件（模板/参数/经验/规则） |
| 更新时机 | **同一推理流程内**实时更新 | **流程间**（交互结束后）更新，下一次匹配实时生效 |
| 可行性 | 云枢无自研可训练规划师 ✗ | 全部工件读取点已支持热加载（skills loader / workflow matcher / cognitive YAML 热改）✓ |
| 风险 | 中断当前推理、不可控 | 不中断当前推理，版本化+门控+回滚 |

**等价性论证**：对话型智能体的"边做边学"实质是**任务间改进**（下次同类任务做得更好），TTL 的"当轮改进"可由工作记忆即时注入（反思 hints 回灌、检索经验注入）覆盖。故 TTPU + 工作记忆即时注入 ≡ TTL 的目标能力，且更安全。

### 4.2 四类可更新工件与更新器映射

| 工件 | 云枢模块 | 更新器 | 状态 |
|---|---|---|---|
| 工作流模板 | `workflow_learning/`（learner→matcher→executor→skill_converter） | `WorkflowLearner.learn()` | ✅ 已实现（自动闭环 v1） |
| Skill 参数 | `skills_mgmt/models.py`（`SkillMetrics.param_stats` / `avoid_params`） | `enhancer.optimize_params()` | ✅ 已实现（缺调度，TASK-05 接） |
| 反思经验 | `planning/reflector.py`（`experiences.json`/`lessons.json`） | `learn_from_experience()` | ⚠️ 待接线（TASK-02） |
| 阈值规则 | `cognitive/`（YAML 阈值） | `rule_tuner`（新增，可选低优先） | ✗ 可选 |

### 4.3 PolicyUpdateBroker 设计（新增核心模块）

新增 `agent/learning/policy_broker.py`，作为策略通道统一入口：

```python
# agent/learning/policy_broker.py
class PolicyUpdateBroker:
    """接收学习信号 → 门控 → 路由到对应工件更新器 → 版本提交 → 生效/回滚。"""

    def receive(self, signal: LearningSignal) -> UpdateResult:
        if not self.budget.allow(signal):          # TASK-03 成本预算
            return UpdateResult(skipped="budget")
        if not self.gate.pass_quality(signal):     # 复用质量门控（success_count/confidence）
            return UpdateResult(skipped="quality_gate")
        updater = self._route(signal)              # → workflow_learner / enhancer / reflector / rule_tuner
        with self.audit.record(signal):            # 审计（TASK-05）
            version = updater.apply(signal)        # 版本快照 + 变更
            self.rollout.set(version, ratio=0.1)   # 灰度 10% → 达标全量
        return UpdateResult(applied=version)

    def rollback(self, artifact: str, to_version: str) -> None:
        """一键回滚到上任版本（审计记录）。"""
```

- 信号类型：`feedback`（rating/type）→ Skill 更新器（promote/deprecate/optimize_params）；`reflection`（lesson/experience）→ reflector 落盘；`execution_result`（success/failure, task_type）→ workflow learner / skill metrics；`novelty_event`（TASK-06）→ 记忆沉积建议。
- 门控复用链：`LearningBudget(成本) → 质量门控(confidence/success_count，复用 workflow_learning 模式) → 强制审核链(TASK-04 enforce_before_publish) → 版本提交 → 灰度生效`。
- 默认 dry-run + 审计（遵 TASK-05 规范）。

### 4.4 版本与回滚设计

| 工件 | 版本机制 | 回滚方式 |
|---|---|---|
| Skill | `bump_version/rollback_version`（已有） | `rollback_version(skill_id, version)` |
| 工作流 | **repository 补版本快照**（新增）：每次 update 写 `before` 快照 | `repository.restore(workflow_id, version)` |
| 反思经验/规则 | 文件级快照 `data/learning/policy_versions/`（新增） | 快照替换 |

统一审计字段：`update_id / artifact / old_value / new_value / trigger_signal / rollback_command`（写 `audit_file`）。

### 4.5 在线生效与灰度

- 工件读取点均已支持热加载（skills loader、workflow matcher、cognitive config 热改）——**更新即生效，无需重启**（与项目既有 `Reranker 热重载` 模式一致）。
- 灰度：新版本先 `rollout=10%` 流量命中，KPI（TASK-03）达标后全量；不达标自动回退上一版本。

---

## 五、数据流与状态机

### 5.1 学习信号生命周期

```
collected → gated（预算+质量门控）→ committed（版本快照+审计）→ live（灰度）→ full（全量）
                                                              ↘ rolled_back（KPI 不达标/人工触发）
```

### 5.2 工件版本状态机

```
vN-1(active) → vN(candidate, gated) → vN(active@rollout) → vN(active@full)
                                    ↘ vN(rejected) → 保持 vN-1
                                    ↘ vN(rolled_back) → 恢复 vN-1
```

---

## 六、成本模型与护栏

1. **学习动作预算**：TASK-03 `LearningBudget`（单次动作 token 上限 / 日预算 / 熔断），`mode=warn_only` 默认，`enforce` 可选。
2. **零 Token 优先**：策略通道优先规则/模板/参数类更新（0 成本）；仅 LLM 参与环节（卡片蒸馏、审核、LLM-as-Judge）计入预算。
3. **有效性判据**：TASK-03 KPI —— token 复用率上升、同类任务失败率下降、Skill/工作流命中率上升、反馈均分上升。任一 KPI 连续 2 个评估周期恶化 → 触发策略回滚 + 人工 review。

---

## 七、安全与隐私

1. **学习样本脱敏**：记忆/经验入库前走 `memory/black_box.py` 既有 AES-256-GCM 加密与脱敏管道。
2. **策略更新受控**：自动更新必须过 TASK-04 强制审核链 + TASK-05 dry-run 规范；全部动作写审计。
3. **自主权联动**：L1-L5 分级（TASK-07）下，高自主等级（L4/L5）会话产生的策略更新建议只记录不自动执行；低等级会话完全禁止自动策略更新。

---

## 八、与任务提示词衔接矩阵

| 本设计点 | 落地归属 | 说明 |
|---|---|---|
| ContextAssembler 组装链收口 | TASK-01 / TASK-02 | 规划/反思注入点统一 |
| 反思经验入检索面 + 注入 | TASK-02 | `learn_from_experience` 接线 + `get_advice_for_task` 数据源补全 |
| LearningBudget 成本护栏 | TASK-03 | 本框架前置依赖 |
| 沉积管线（卡片→Skill） | TASK-04 | 本设计仅定契约 |
| PolicyUpdateBroker + 版本快照 + 灰度 | TASK-05 | 与 feedback_agent/lifecycle 共用调度与审计 |
| 新颖事件→记忆沉积建议 | TASK-06 | 作为 LearningSignal 一种 |
| 自主权联动 | TASK-07 | 策略更新与 L1-L5 交互 |
| 检索参数自适应 / rule_tuner | TASK-05 后可选 | 低优先级，不进主线 |
| 远期可训练组件评估 | TASK-08 | CEL 与未来 LoRA 适配层兼容（见 §十） |

---

## 九、分阶段实施路线

| 阶段 | 内容 | 前置 |
|---|---|---|
| A | ContextAssembler 组装链收口 + 反思经验注入 | TASK-01/02 |
| B | 预算护栏 + 强制审核链 + 调度执行体 | TASK-03/04/05 |
| C（可选） | 检索参数自适应、rule_tuner | TASK-05 后 |
| D（远期） | 轻量可训练组件评估（如本机 vLLM LoRA） | TASK-08 报告决策 |

---

## 十、验收标准

1. **能力等价性**：构造"参数记忆/TTL 想解决的任务集"（反复同类任务 token 下降、失败任务再犯率下降、跨会话知识复用），在 CEL 框架下达到等价或更优（以 TASK-03 KPI 度量）。
2. **门控完备**：任何策略更新必须依次过"预算→质量门控→强制审核链"，零绕过路径（代码审计可证）。
3. **回滚可用**：四类工件均可一键回滚到上一版本，回滚后行为与旧版本一致（快照比对）。
4. **主链路零退化**：组装链改造后全量回归（`python -m pytest tests/unit -q`）全绿；`wire_enabled=false` 等保守态下行为与现状一致。
5. **零额外默认成本**：默认路径（规则/模板更新）不产生新增 LLM 调用（LLMMonitor 断言）。

---

## 十一、风险与开放问题

| 风险 | 缓解 |
|---|---|
| 检索噪声污染上下文 | 复用 RRF 质量门禁 + `min_score` 阈值过滤（已有）；检索参数自适应默认关闭 |
| 策略过拟合单一反馈 | 采纳率监控（TASK-03）+ 人工 review 兜底 + KPI 恶化自动回滚 |
| 工作流版本快照增长 | 快照保留 N=10 版本滚动清理 |
| 参数记忆长期缺位 | CEL 兼容未来可训练组件：`ContextAssembler` 预留 `model_adapter` 注入点，若 TASK-08 评估支持（如本机 vLLM 低秩适配），可无痛升级 |
| 灰度期间行为不一致 | rollout 只影响命中概率，失败自动回退上一版本并告警 |

---

## 十二、结论

在 API 型架构下，设计思路 D2/D3 的能力目标可由**上下文工程学习（CEL）**框架等价实现：

- **D2 → 双层上下文记忆架构**：工作记忆/长期检索记忆/程序性记忆三层模型 + 沉积/实例化双管线，替代"参数记忆↔非参数记忆双向转换"。
- **D3 → 测试时策略更新（TTPU）**：四类可更新外部策略工件 + `PolicyUpdateBroker` 统一门控/版本/回滚/灰度，替代"推理中更新规划师参数"。

全部实现复用现有模块（workflow_learning / skills_mgmt / knowledge / feedback / reflector），新增仅 `policy_broker.py`、`context/assembler.py` 收口与少量版本快照逻辑，并严格对齐 TASK-01~05 的任务边界，符合【简易】最小充分解原则。
