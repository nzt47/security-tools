# 云枢智能体系统架构总览

## 1. 系统架构总览

### 1.1 设计理念

云枢（Yunshu）是一个模块化、可扩展的智能体系统，采用分层架构设计，
核心围绕"编排调度-认知推理-工具执行-记忆存储"四大支柱构建。

设计原则：
- **模块化**：每个功能模块独立封装，通过明确的接口交互
- **可观测性**：全链路追踪、结构化日志、指标监控三位一体
- **高可用**：熔断器、自动重试、故障降级确保系统韧性
- **安全性**：输入输出双重校验、敏感数据脱敏、权限控制
- **可扩展**：插件化架构，支持动态加载技能、工具、模型适配器

### 1.2 整体架构图

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           用户交互层 (UI / API)                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                 │
│  │  Web 界面    │  │  REST API    │  │  WebSocket   │                 │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘                 │
└─────────┼─────────────────┼─────────────────┼──────────────────────────┘
          │                 │                 │
┌─────────▼─────────────────▼─────────────────▼──────────────────────────┐
│                           编排调度层 (Orchestrator)                      │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │                     Orchestrator (核心调度)                       │   │
│  │  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐    │   │
│  │  │ 消息处理   │ │ 任务分发   │ │ 生命周期   │ │ 状态管理   │    │   │
│  │  └────────────┘ └────────────┘ └────────────┘ └────────────┘    │   │
│  └──────────────────────────────────────────────────────────────────┘   │
└─────────┬───────────────────────────────────────────────────────────────┘
          │
    ┌─────┴──────┬──────────┬──────────┬──────────┐
    ▼            ▼          ▼          ▼          ▼
┌────────┐  ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐
│ 认知层  │  │ 工具层  │ │ 记忆层  │ │ 规划层  │ │ 分身层  │
│Cognitive│  │ Tools  │ │ Memory │ │ Planner│ │Subagent│
└────────┘  └────────┘ └────────┘ └────────┘ └────────┘
    │            │          │          │          │
    └─────┬──────┴──────────┴──────────┴──────────┘
          ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                           基础能力层 (Infrastructure)                    │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐   │
│  │ 模型路由  │ │ 错误处理  │ │ 监控告警  │ │ 安全防护  │ │ 配置管理  │   │
│  │ModelRouter│ │ErrorHandler│ │Monitoring│ │Guardrails│ │ Config   │   │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘   │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐   │
│  │ 日志系统  │ │ 缓存系统  │ │ 扩展市场  │ │ 权限系统  │ │ 多租户   │   │
│  │LogSystem │ │  Caching │ │Extension │ │Permission│ │MultiTenant│  │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                           数据存储层 (Data Layer)                        │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐   │
│  │ SQLite   │ │ 文件系统 │ │ 向量数据库│ │ 内存缓存  │ │ 配置文件 │   │
│  │ (主数据) │ │(工作空间) │ │(语义记忆) │ │(热点数据)│ │  (YAML)  │   │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
```

### 1.3 分层职责说明

| 层级 | 主要职责 | 核心模块 |
|------|---------|---------|
| 用户交互层 | 接收用户输入、展示响应结果 | Web UI、REST API、WebSocket |
| 编排调度层 | 请求路由、流程编排、生命周期管理 | Orchestrator |
| 业务能力层 | 具体的业务功能实现 | 认知、工具、记忆、规划、分身 |
| 基础能力层 | 通用技术能力支撑 | 模型路由、错误处理、监控、安全等 |
| 数据存储层 | 数据持久化和缓存 | SQLite、文件系统、向量数据库等 |

---

## 2. 核心模块说明

### 2.1 编排调度模块 (Orchestrator)

**位置**: `agent/orchestrator/`

**核心职责**：
- 接收并处理用户请求，协调各模块协同工作
- 管理智能体生命周期（初始化、运行、暂停、销毁）
- 任务分发和状态管理
- 异常捕获和恢复策略执行

**核心组件**：
| 组件 | 文件 | 职责 |
|------|------|------|
| Orchestrator | `orchestrator.py` | 主编排器，消息路由和流程控制 |
| MessageHandler | `message_handler.py` | 消息预处理和格式转换 |
| TaskDispatcher | `task_dispatcher.py` | 任务分发和调度 |
| LifecycleManager | `lifecycle_manager.py` | 智能体生命周期管理 |
| StatusReporter | `status_reporter.py` | 状态报告和进度反馈 |
| PromptBuilder | `prompt_builder.py` | 提示词构建和组装 |
| ResponseBuilder | `response_builder.py` | 响应格式化和后处理 |
| SubagentManager | `subagent_manager.py` | 子智能体管理 |
| VoiceVision | `voice_vision.py` | 多模态输入处理 |

### 2.2 认知模块 (Cognitive)

**位置**: `agent/cognitive/`

**核心职责**：
- 反思与自我纠错
- 知识沉淀和提取
- 多角度推理和辩论
- 结果审核和质量评估

**核心组件**：
| 组件 | 文件 | 职责 |
|------|------|------|
| CognitiveLoop | `loop.py` | 认知循环主控制器 |
| Reflection | `reflection.py` | 反思与自我评估 |
| Knowledge | `knowledge.py` | 知识管理和提取 |
| Debate | `debate.py` | 多角度辩论推理 |
| ActorCritic | `actor_critic.py` | 执行者-评判者模式 |
| Critic | `critic.py` | 结果评判和建议 |

### 2.3 记忆模块 (Memory)

**位置**: `agent/memory/`

**核心职责**：
- 短期记忆和长期记忆管理
- 记忆检索和过滤
- 记忆摘要和压缩
- 多存储后端适配

**核心组件**：
| 组件 | 文件 | 职责 |
|------|------|------|
| MemoryRouter | `router.py` | 记忆路由，统一接口 |
| ShortTermMemory | `short_term_memory.py` | 短期记忆管理 |
| LongTermMemory | `long_term_memory.py` | 长期记忆管理 |
| MemoryFilter | `filter.py` | 记忆过滤和筛选 |
| MemoryReviewer | `reviewer.py` | 记忆审核和质量评估 |
| BaseMemory | `base.py` | 记忆存储基类 |
| HolographicAdapter | `adapters/holographic_adapter.py` | 全息记忆适配器 |
| Mem0Adapter | `adapters/mem0_adapter.py` | Mem0 适配器 |

### 2.4 工具模块 (Tools)

**位置**: `agent/tools/`

**核心职责**：
- 提供各类工具供智能体调用
- 工具注册和发现
- 工具执行和结果封装
- 安全沙箱执行

**核心组件**：
| 组件 | 文件 | 职责 |
|------|------|------|
| CoreTools | `core_tools.py` | 核心基础工具 |
| FileTools | `file_tools.py` | 文件操作工具 |
| ShellTools | `shell_tools.py` | Shell 命令工具 |
| WebTools | `web_tools.py` | 网络访问工具 |
| BrowserTools | `browser_tools.py` | 浏览器自动化工具 |
| CodeTools | `code_tools.py` | 代码处理工具 |
| SoftwareTools | `software_tools.py` | 软件操作工具 |
| MCPConnector | `mcp_connector.py` | MCP 协议连接器 |
| DiscoveryService | `discovery_service.py` | 工具发现服务 |
| ToolGenerator | `tool_generator.py` | 工具生成器 |

### 2.5 监控模块 (Monitoring)

**位置**: `agent/monitoring/`

**核心职责**：
- 系统指标收集和导出
- 分布式链路追踪
- 日志收集和分析
- 告警规则评估和通知
- 故障自愈

**核心组件**：
| 组件 | 文件 | 职责 |
|------|------|------|
| Metrics | `metrics.py` | 指标收集和 Prometheus 导出 |
| Tracing | `tracing.py` | 分布式追踪（OpenTelemetry） |
| Loki | `loki.py` | Loki 日志聚合 |
| Performance | `performance.py` | 性能监控 |
| AlertManager | `alert_manager.py` | 告警管理 |
| AlertEvaluator | `alert_evaluator.py` | 告警规则评估 |
| AlertNotifier | `alert_notifier.py` | 告警通知 |
| SelfHealer | `self_healer.py` | 故障自愈 |
| BusinessMetrics | `business_metrics.py` | 业务指标 |
| Decorators | `decorators.py` | 监控装饰器 |

### 2.6 模型路由模块 (Model Router)

**位置**: `agent/model_router/`

**核心职责**：
- 根据任务复杂度选择合适的模型
- 多模型提供商适配
- 成本优化和配额管理
- 模型降级和故障转移

**核心组件**：
| 组件 | 文件 | 职责 |
|------|------|------|
| ModelRouter | `router.py` | 模型路由决策 |
| Adapters | `adapters.py` | 模型适配器集合 |
| CostTracker | `cost_tracker.py` | 成本追踪和预算管理 |

### 2.7 任务规划模块 (Task Planner)

**位置**: `agent/task_planner/`

**核心职责**：
- 复杂任务分解（DAG 有向无环图）
- 任务依赖管理
- 执行计划生成
- 进度跟踪和调整

**核心组件**：
| 组件 | 文件 | 职责 |
|------|------|------|
| Planner | `planner.py` | 任务规划器 |
| EnhancedPlanner | `enhanced_planner.py` | 增强型规划器 |
| DAGEngine | `dag.py` | DAG 执行引擎 |
| EnhancedDAG | `enhanced_dag.py` | 增强型 DAG |
| Executor | `executor.py` | 任务执行器 |
| BuiltinPlans | `builtin_plans.py` | 内置计划模板 |

### 2.8 子智能体模块 (Subagent)

**位置**: `agent/subagent/`

**核心职责**：
- 子智能体容器管理
- 沙箱环境隔离
- 生命周期控制
- 任务分发和结果汇总

**核心组件**：
| 组件 | 文件 | 职责 |
|------|------|------|
| Container | `container.py` | 子智能体容器 |
| Lifecycle | `lifecycle.py` | 生命周期管理 |
| Sandbox | `sandbox.py` | 沙箱隔离 |
| Barrier | `barrier.py` | 同步屏障 |
| Summarizer | `summarizer.py` | 结果汇总 |

### 2.9 安全防护模块 (Guardrails)

**位置**: `agent/guardrails/`

**核心职责**：
- 输入安全检测（注入、越权）
- 输出安全过滤（敏感信息、不当内容）
- 命令执行安全控制
- 输出格式校验

**核心组件**：
| 组件 | 文件 | 职责 |
|------|------|------|
| InputGuard | `input_guard.py` | 输入安全检测 |
| OutputGuard | `output_guard.py` | 输出安全过滤 |
| OutputSchema | `output_schema.py` | 输出格式校验 |

### 2.10 扩展模块 (Extensions)

**位置**: `agent/extensions/`

**核心职责**：
- 技能/插件安装和管理
- 扩展市场对接
- 安全沙箱执行
- 依赖管理

**核心组件**：
| 组件 | 文件 | 职责 |
|------|------|------|
| Manager | `manager.py` | 扩展管理器 |
| Installer | `installer.py` | 安装器基类 |
| SkillsInstaller | `skills_installer.py` | 技能安装器 |
| MCPInstaller | `mcp_installer.py` | MCP 安装器 |
| PluginsInstaller | `plugins_installer.py` | 插件安装器 |
| Sandbox | `sandbox.py` | 扩展沙箱 |
| Store | `store.py` | 扩展存储 |
| Market | `market.py` | 扩展市场 |
| SecurityChecker | `security_checker.py` | 安全检查器 |

---

## 3. 数据流说明

### 3.1 用户请求处理流程

```
用户输入
    │
    ▼
┌─────────────┐
│ 输入安全检测 │ (InputGuard: SQL注入/XSS/路径遍历)
└──────┬──────┘
       │ 通过
       ▼
┌─────────────┐
│ 编排器接收   │ (Orchestrator: 创建Trace上下文)
└──────┬──────┘
       │
       ▼
┌─────────────┐
│ 工作流引擎   │ (WorkflowEngine: 尝试本地规则匹配)
└──────┬──────┘
       ├─匹配成功→ 本地执行 (0 Token) ─┐
       │                               │
       │ 未匹配                        │
       ▼                               │
┌─────────────┐                        │
│ 模型路由     │ (ModelRouter: 选择合适模型)│
└──────┬──────┘                        │
       │                               │
       ▼                               │
┌─────────────┐                        │
│ 认知循环     │ (CognitiveLoop)        │
│  - 反思      │                        │
│  - 知识      │                        │
│  - 辩论      │                        │
│  - 审核      │                        │
└──────┬──────┘                        │
       │                               │
       ▼                               │
┌─────────────┐                        │
│ 工具调用(可选)│ (Tools: 文件/网络/代码等) │
└──────┬──────┘                        │
       │                               │
       ▼                               │
┌─────────────┐                        │
│ 记忆存储/检索│ (MemoryRouter)          │
└──────┬──────┘                        │
       │                               │
       ▼                               │
┌─────────────┐                        │
│ 输出安全过滤 │ (OutputGuard: 脱敏/格式化)│
└──────┬──────┘                        │
       │                               │
       └───────────┬───────────────────┘
                   │
                   ▼
              返回用户
```

### 3.2 记忆数据流

```
用户消息 + 智能体回复
        │
        ▼
┌─────────────────┐
│ 短期记忆写入      │ (ShortTermMemory: 会话级记忆)
└────────┬────────┘
         │
         ├───────────────────────────┐
         │ 定期摘要                    │ 实时检索
         ▼                            ▼
┌─────────────────┐          ┌─────────────────┐
│ 长期记忆写入      │          │ 记忆检索查询     │
│ (LongTermMemory) │          │ (MemoryRouter)   │
└────────┬────────┘          └─────────────────┘
         │
         ▼
┌─────────────────┐
│ 向量数据库存储    │ (Holographic/Mem0 Adapter)
└─────────────────┘
```

### 3.3 监控数据流

```
业务代码
    │
    ├───── 日志 ─────► 结构化日志 ─► Loki / 文件存储
    │
    ├───── 指标 ─────► Prometheus Client ─► /metrics端点 ─► Prometheus Server
    │
    └───── 追踪 ─────► OpenTelemetry SDK ─► Jaeger / Zipkin
```

### 3.4 错误处理流

```
异常发生
    │
    ▼
┌─────────────┐
│ 是否可重试?  │
└──────┬──────┘
       │是
       ▼
┌─────────────┐
│ 重试策略执行  │ (RetryPolicy: 指数退避+抖动)
└──────┬──────┘
       │
       ├─ 成功 → 继续执行
       │
       │ 失败且未达上限 → 延迟后重试
       │
       │ 达到上限
       ▼
┌─────────────┐
│ 熔断器检查   │ (CircuitBreaker)
└──────┬──────┘
       │
       ├─ 熔断中 → 快速失败 / 降级处理
       │
       └─ 正常 → 记录错误 → 抛出异常
```

---

## 4. 扩展点说明

### 4.1 工具扩展

**扩展方式**：
1. 继承 `BaseTool` 基类，实现 `execute()` 方法
2. 通过 `DiscoveryService` 注册工具
3. 或通过 MCP 协议接入外部工具服务

**扩展点**：
- 文件：`agent/tools/core_tools.py`、`agent/tools/mcp_connector.py`
- 接口：工具类需包含 `name`、`description`、`execute()` 方法
- 示例：新增数据库查询工具、第三方 API 调用工具

### 4.2 模型适配器扩展

**扩展方式**：
1. 实现模型适配器类，遵循统一接口
2. 在 `ModelRouter` 中注册新适配器
3. 配置模型路由规则

**扩展点**：
- 文件：`agent/model_router/adapters.py`
- 接口：`complete()`、`stream()`、`embed()` 等方法
- 示例：新增 Anthropic Claude、本地 Ollama 等模型支持

### 4.3 记忆存储扩展

**扩展方式**：
1. 继承 `BaseMemory` 基类
2. 实现 `store()`、`search()`、`delete()` 等方法
3. 在 `MemoryRouter` 中注册

**扩展点**：
- 文件：`agent/memory/base.py`、`agent/memory/router.py`
- 接口：`store()`、`search()`、`update()`、`delete()`
- 示例：新增 Redis 记忆存储、Pinecone 向量数据库

### 4.4 认知能力扩展

**扩展方式**：
1. 在 `agent/cognitive/` 目录下新增模块
2. 在 `CognitiveLoop` 中注册新的认知环节
3. 配置认知流程顺序

**扩展点**：
- 文件：`agent/cognitive/loop.py`
- 接口：认知环节需实现 `process()` 方法
- 示例：新增风险评估、创意生成等认知能力

### 4.5 监控指标扩展

**扩展方式**：
1. 使用 `MetricsCollector` 注册自定义指标
2. 通过装饰器 `@monitor()` 自动埋点
3. 配置 Grafana 仪表盘展示

**扩展点**：
- 文件：`agent/monitoring/metrics.py`、`agent/monitoring/decorators.py`
- 接口：`increment_counter()`、`observe_histogram()`、`set_gauge()`
- 示例：新增业务成功率、用户活跃度等指标

### 4.6 安全规则扩展

**扩展方式**：
1. 在 `InputGuard` / `OutputGuard` 中添加检测规则
2. 配置敏感数据过滤模式
3. 实现自定义安全检查器

**扩展点**：
- 文件：`agent/guardrails/`、`agent/utils/sensitive_data_filter.py`
- 接口：检测函数接收内容，返回 `(是否通过, 违规信息)`
- 示例：新增特定行业合规检测、自定义敏感词过滤

### 4.7 扩展市场插件

**扩展方式**：
1. 按照扩展规范开发技能/插件包
2. 上传到扩展市场或本地安装
3. 通过 `ExtensionManager` 动态加载

**扩展点**：
- 文件：`agent/extensions/`
- 接口：技能需包含 `skill.yaml` 配置文件和入口函数
- 示例：新增行业领域技能、可视化插件等

---

## 5. 关键技术栈

| 类别 | 技术 | 说明 |
|------|------|------|
| 编程语言 | Python 3.10+ | 主开发语言 |
| Web框架 | Flask / FastAPI | API 服务 |
| 前端 | HTML / CSS / JS | 原生 + 模块化 |
| 数据库 | SQLite | 主数据存储 |
| 向量数据库 | ChromaDB / 可插拔 | 语义记忆存储 |
| 缓存 | 内存 / Redis | 多层缓存 |
| 监控 | Prometheus + Grafana | 指标监控 |
| 追踪 | OpenTelemetry + Jaeger | 分布式追踪 |
| 日志 | 结构化 JSON + Loki | 日志聚合 |
| 容器化 | Docker / Docker Compose | 部署方案 |
| CI/CD | GitHub Actions | 持续集成 |

---

## 6. 部署架构

### 6.1 单机部署（开发/测试）

```
┌─────────────────────────────────┐
│         单台服务器/本地          │
│  ┌───────────────────────────┐  │
│  │     云枢智能体 (Python)   │  │
│  │  - Web UI                 │  │
│  │  - API Server             │  │
│  │  - 所有业务模块           │  │
│  └─────────────┬─────────────┘  │
│                │                │
│  ┌─────────────▼─────────────┐  │
│  │     数据存储 (本地)        │  │
│  │  - SQLite                 │  │
│  │  - 文件系统               │  │
│  │  - 本地向量数据库         │  │
│  └───────────────────────────┘  │
└─────────────────────────────────┘
```

### 6.2 生产级部署

```
┌─────────────────────────────────────────────────────────────┐
│                      负载均衡器                               │
└──────────────────────────┬──────────────────────────────────┘
                           │
        ┌──────────────────┼──────────────────┐
        ▼                  ▼                  ▼
┌──────────────┐   ┌──────────────┐   ┌──────────────┐
│  应用节点 1   │   │  应用节点 2   │   │  应用节点 N   │
│ (无状态横向扩展)│   │              │   │              │
└──────┬───────┘   └──────┬───────┘   └──────┬───────┘
       │                  │                  │
       └──────────────────┼──────────────────┘
                          │
        ┌─────────────────┴─────────────────┐
        │                                   │
        ▼                                   ▼
┌──────────────────┐              ┌──────────────────┐
│   数据存储集群    │              │   监控集群        │
│  - PostgreSQL    │              │  - Prometheus    │
│  - Redis 缓存    │              │  - Grafana       │
│  - 向量数据库     │              │  - Loki          │
│  - 对象存储      │              │  - Jaeger        │
└──────────────────┘              └──────────────────┘
```

---

**文档版本**: v1.0  
**最后更新**: 2026-06-24  
**适用版本**: 云枢智能体 v10.x
