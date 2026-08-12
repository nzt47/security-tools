# ContextAssembler 集成验证总结报告

- 日期：2026-08-12
- 状态：验证通过，观察模式已开启
- 依据：docs/zh/智能体学习机制重构计划/D2D3_API架构替代方案设计.md §3.3/§3.4（CEL 框架）

---

## 1. 背景与目标

云枢现有架构中，模型权重不可变，上下文组装散落在 orchestrator 各分支（lifetrace / skill_instructions / user_context），
无统一的三层记忆组装入口。本任务将 CEL（Context-Engineering Learning）框架核心 `ContextAssembler`
集成到云枢实际运行环境：

1. 生产模块 `agent/context/assembler.py`：统一组装工作记忆 / 长期检索记忆 / 程序性记忆 + 工具白名单
2. orchestrator `_call_llm_v2` 旁路注入：组装产物追加到 system prompt
3. 配置开关 `learning.context_assembler.enabled`（默认关闭 → 集成验证通过后开启观察模式）

## 2. 验证范围与方法

| 层 | 方法 | 覆盖点 |
|---|---|---|
| 单元测试 | pytest tests/unit/test_context_assembler.py（12 用例） | 组装效果（简单/复杂/失败重试）、分级注入、预算截断、异常降级、render_text、orchestrator 接线两态、配置约束 |
| 集成验证 | scripts/verify_context_assembler_integration.py | 真实反思经验文件、真实 SkillLoader、真实 MemoryManager、开关两态、降级、性能基准 |
| 回归 | orchestrator 相关 3 个测试文件（130 用例） | 主链路无回归 |

## 3. 验证结果

### 3.1 单元测试（12/12 通过）

- 场景 A 简单任务：无技能命中，工作记忆注入
- 场景 B 复杂任务：技能 + 工作流 + 反思经验命中
- 分级注入：简单任务注入量 ≤ 复杂任务（守 token 预算）
- 预算截断：极小预算触发 truncated，截断后不超预算
- 降级：provider 缺失 / 异常 → 对应层为空，assemble 不抛异常
- orchestrator 接线：enabled=false → None；enabled=true → 组装文本；异常 → None
- 配置：观察模式已开启（enabled=true）

### 3.2 集成验证（真实组件协作，全部通过）

- 真实反思经验文件读取：2 条（experiences / lessons）
- 真实 SkillLoader：命中 skill_id（如 self_reflection / context_aware）
- 真实 MemoryManager（临时目录）：上下文读取正常
- 观察模式（config.yaml enabled=true）→ 返回组装文本（含工作记忆/反思/技能指令）
- 强制关闭 → None（可回滚验证）
- 提供者异常 → 静默降级 None（主链路零影响）
- 性能基准 n=20：平均 4.10ms | p95 4.40ms | max 4.65ms

### 3.3 回归（130/130 通过）

orchestrator 主链路（refactor / reject / workflow_learning_layer）无回归。

## 4. 性能开销分析

### 4.1 实测数据（Windows 本地 · Python 3.12 · 真实组件）

| 指标 | 数值 |
|---|---|
| 单次组装平均耗时 | 4.10 ms |
| p95 耗时 | 4.40 ms |
| 最大耗时 | 4.65 ms |
| 单次注入 token | 206 ~ 443（占 budget 3000 的 7% ~ 15%） |
| 单次注入字符数 | 723 ~ 1433 |

### 4.2 开销分解

| 环节 | 占比 | 说明 |
|---|---|---|
| SkillLoader.match（程序性记忆） | 主要 | TF-IDF 检索 + top_k 匹配，毫秒级 |
| data/reflection JSON 读取（长期检索） | 次要 | 两文件全量读入，文件小时可忽略 |
| MemoryManager.get_context（工作记忆） | 次要 | 既有主链路同源调用，无新增成本 |
| 组装 + render（纯字符串） | 可忽略 | 微秒级 |

### 4.3 对主链路影响评估

- 旁路注入为**纯增量**：system prompt 追加文本，不修改任何既有分支
- 与 LLM 调用（秒级）相比，4ms 级开销占比 < 0.5%，可忽略
- 异常路径全部静默降级，主链路零中断
- **唯一需注意**：首次调用触发 SkillLoader 懒初始化（读 data/skills_mgmt.json + 建索引），
  实测约几十毫秒级一次性开销，后续调用复用单例

### 4.4 token 预算风险

- 当前 token_budget=3000，实测注入 206~443 token，余量充足
- 若后续反思经验文件增长 / 技能指令变长，可能触发 truncated（已实现截断保护：保留工作记忆与技能）
- 建议观察期监控 truncated 比例，≥5% 时上调 budget 或加长长期检索过滤

## 5. 日志可观测性（实时查看方式）

观察模式开启后，组装过程日志实时可见：

**INFO 级（默认可见）— 每次请求一条结构化摘要：**

```
action: orchestrator.context_assembler.injected
duration_ms: 4.42  token_total: 414  token_budget: 3000  truncated: False
skills_hit: [self_reflection, context_aware]  reflections_hit: 2
layer_tokens: {reflections: 18, skills: 348, ...}  injected_chars: 1347
```

另有两类信号：`empty`（三层全空跳过，INFO）、`degraded`（组装异常降级，WARNING）。

**DEBUG 级（需开启）— 各层拉取明细 + 组装完成摘要：**

```
CONTEXT_ASSEMBLER_LOG_LEVEL=DEBUG   # 环境变量覆盖（项目规范：环境变量 > 配置 > 默认值）
```

开启后输出：工作记忆拉取条数、长期检索片段数、程序性技能/工作流命中、组装耗时与 layer_tokens。

## 6. 后续监控建议

### 6.1 指标（Prometheus，建议新增）

| 指标 | 类型 | 用途 |
|---|---|---|
| context_assembler_injected_total | Counter | 注入次数（按 skills_hit 标签） |
| context_assembler_degraded_total | Counter | 降级次数（告警源） |
| context_assembler_duration_ms | Histogram | 组装耗时 p50/p95/p99 |
| context_assembler_injected_tokens | Gauge | 注入 token 占比（budget 风险监控） |

### 6.2 告警建议

- degraded 率 > 1%（异常上升，说明数据源/路径异常）
- 组装耗时 p95 > 50ms（正常 < 5ms，异常波动）
- 注入 token / budget 占比 > 50%（预算逼近，需扩容或收紧检索）

### 6.3 观察期迭代路径（2 周）

1. **观察期**：保持 enabled=true，收集命中率 / 注入收益 / 用户反馈
2. **调优**：按实测 truncated 比例调整 token_budget；反思经验文件过大时加 task_type 过滤
3. **回流**：组装产物中的技能/工作流命中可反哺 workflow_learning 与 skills_mgmt 数据
4. **评估**：对比开启前后响应质量（llm 评审 / 用户满意度），决定继续观察或收窄/回滚

### 6.4 回滚方案

```bash
# 方式一：改 config.yaml
learning.context_assembler.enabled: false
# 方式二：环境变量覆盖（不重启改配置）
LEARNING_CONTEXT_ASSEMBLER_ENABLED=0
```

## 7. 结论

- ContextAssembler 组装逻辑已稳定集成到云枢实际运行环境，单测 / 集成 / 回归全部通过
- 性能开销毫秒级（平均 4.10ms），相对 LLM 调用可忽略，预算余量充足
- 观察模式已开启，日志实时可见（INFO 摘要 + 可选 DEBUG 明细），支持降级与回滚
