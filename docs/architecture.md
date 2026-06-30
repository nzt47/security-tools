# 云枢系统架构图

## 完整数据流

```mermaid
graph TB
    User[用户] --> IG[InputGuard]
    IG -->|通过| Orch[Orchestrator]
    IG -->|拦截| Blocked[返回错误]

    Orch --> WE[WorkflowEngine]
    WE -->|匹配| LocalExec[本地执行<br/>0 Token]
    WE -->|未匹配| MR[ModelRouter]

    MR -->|简单| Small[小模型<br/>低成本]
    MR -->|复杂| Large[旗舰模型<br/>高能力]

    Small --> LLM[LLM 推理]
    Large --> LLM

    LLM --> CL[CognitiveLoop]
    CL -->|反思| Refl[Reflection评估]
    CL -->|知识| Know[知识沉淀]
    CL -->|辩论| Debate[多角度辩论]
    CL -->|校验| AC[ActorCritic审核]

    CL --> OG[OutputGuard]
    OG -->|PII| Masked[遮盖返回]
    OG -->|通过| Response[返回用户]

    subgraph 记忆系统
        MRouter[MemoryRouter]
        Holo[HolographicAdapter]
        Mem0[Mem0Adapter]
        Hin[HindsightAdapter]
        MRouter --> Holo & Mem0 & Hin
    end

    subgraph 安全体系
        HITL[HITLManager]
        Ethics[EthicsEngine]
        CG[CommandGuard]
    end

    subgraph 可观测性
        Tracer[Trace_ID]
        TStore[TraceStore]
        ALog[AuditLogger]
        HAssess[HealthAssessor]
    end

    Orch --> HITL
    HITL --> Ethics
    Orch --> CG
    Orch --> MRouter
    Orch --> ALog
    Orch --> Tracer
    Tracer --> TStore
    TStore --> HAssess
```

## 模块依赖图

```mermaid
graph LR
    Orch[Orchestrator] --> WE[WorkflowEngine]
    Orch --> MR[ModelRouter]
    Orch --> CL[CognitiveLoop]
    Orch --> TP[TaskPlanner]
    Orch --> SM[SubagentManager]
    Orch --> SR[StatusReporter]
    Orch --> VV[VoiceVision]

    WE --> Rules[BuiltinRules]
    TP --> DAG[DAGEngine]
    SM --> Sandbox[Sandbox]

    CL --> Refl[Reflection]
    CL --> Know[Knowledge]
    CL --> Debate[Debate]
    CL --> AC[ActorCritic]

    subgraph 数据层
        Config[configs/]
        Memory[MemoryRouter]
        Audit[AuditLogger]
        Trace[TraceStore]
    end

    Orch --> Config
    Orch --> Memory
    Orch --> Audit
    Orch --> Trace
```

## 部署架构

```mermaid
graph TB
    Client[客户端] -->|HTTP/WS| API[API Server<br/>8123]
    API --> App[app_server.py]
    App --> Orch[Orchestrator]

    subgraph 本地
        Orch --> LLM_Local[LocalLLM<br/>Ollama/vLLM]
        Orch --> SQLite[(SQLite<br/>记忆存储)]
        Orch --> Files[(文件系统<br/>workspace)]
    end

    subgraph 云端（可选）
        Orch --> LLM_Cloud[GPT-4 / Claude]
        Orch --> Mem0[Mem0 Cloud]
        Orch --> Hindsight[Hindsight API]
    end

    subgraph 监控
        Prom[Prometheus]
        Health[Health Dashboard]
    end

    API --> Prom
    API --> Health
```

## 用户请求处理时序

```mermaid
sequenceDiagram
    participant U as 用户
    participant IG as InputGuard
    participant O as Orchestrator
    participant WE as WorkflowEngine
    participant MR as ModelRouter
    participant CL as CognitiveLoop
    participant OG as OutputGuard

    U->>IG: 发送消息
    IG->>IG: 检测注入/威胁
    IG->>O: 通过安全检查

    O->>WE: 尝试本地规则匹配
    alt 规则匹配成功
        WE-->>O: 返回本地执行结果
    else 规则未匹配
        WE->>MR: 请求 LLM 处理
        MR->>MR: 路由选择（成本/能力）
        MR->>CL: 调用 LLM + 认知循环
        CL->>CL: 反思 / 知识 / 辩论
        CL-->>O: 返回处理结果
    end

    O->>OG: 安全输出过滤
    OG->>OG: PII 遮盖
    OG-->>U: 最终响应
```

## 模块责任清单

| 模块 | 文件名 | 核心职责 |
|------|--------|----------|
| Orchestrator | `orchestrator/orchestrator.py` | 消息路由、流程编排、异常恢复 |
| WorkflowEngine | `workflow_engine/engine.py` | 本地规则匹配、0 Token 决策 |
| CognitiveLoop | `cognitive/` | 反思纠错、知识沉淀、辩论、审核 |
| MemoryRouter | `memory/router.py` | 统一记忆接口、7 提供商自适应路由 |
| SubagentManager | `subagent/manager.py` | 容器生命周期、全选配启动 |
| InputGuard | `guardrails/input_guard.py` | SQL 注入 / XSS / 路径遍历检测 |
| CommandGuard | `guardrails/command_guard.py` | Shell 命令白名单 + 黑名单 |
| OutputGuard | `guardrails/output_guard.py` | PII 遮盖、敏感信息过滤 |
| HITLManager | `human_in_the_loop/manager.py` | 风险分级、人工确认流程 |
| EthicsEngine | `human_in_the_loop/ethics.py` | 11 条伦理硬约束 |
| ModelRouter | `model_router/router.py` | 成本敏感模型路由、降级策略 |
| CostTracker | `model_router/cost_tracker.py` | Token 用量统计、成本追踪 |
| TaskPlanner | `task_planner/planner.py` | DAG 目标分解、任务编排 |
| AuditLogger | `audit/logger.py` | 结构化 Append-only 审计日志 |
| TraceStore | `observability/trace_store.py` | 内存 Trace 存储、订阅通知 |
| HealthAssessor | `health/assessor.py` | 系统健康评分、模块状态检测 |
| ConfigLoader | `configs/loader.py` | YAML + 环境变量声明式配置 |
| ExtensionManager | `extensions/manager.py` | Skills / MCP / Plugins 安装管理 |

---

*本文档由 P23 自动生成，反映云枢系统 23 个 Phase 迭代后的最终架构。*
