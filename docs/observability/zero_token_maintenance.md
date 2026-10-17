# 零 token 维护作业（09:00–18:00）

> 一句话：云枢**不跟人聊天**时也能自己干的活儿，都是确定性动作；
> 本页把它们排进工作时段，按天跑，不烧 token。
>
> 实现：`agent/maintenance/zero_token_jobs.py`；
> 注册点：`agent/task_scheduler.py::_create_scheduler`（随进程启动注册）。

## 1. 判定口径：什么算「不烧 token」

调用链上**不出现任何 LLM 会话调用**（chat / summarize / 生成式改写）。
允许的是：纯函数计算、仓库自带 CLI 的 `--check`、本机已鉴权端点的只读查询。

**必须调 LLM、因此不在本清单的**：

| 动作 | 为什么必须 LLM | 落点 |
|---|---|---|
| 对话压缩成摘要 | 需要生成式改写 | `memory/summarizer.py` |
| 经验提炼 / 过程蒸馏 | 需要从素材生成结构化结论 | `agent/digestion/` |
| 技能再定义（redraft） | 需要生成说明与展示名 | `agent/skills_mgmt/` |
| 自我反思 / 案例生成 | 需要生成式评审 | `agent/evolution/` |

## 2. 已排程（每天，时:分，全部在 **05:00–18:00** 内）

| 时间 | 作业 | 做什么 | 写到哪里 | 零 token |
|---|---|---|---|---|
| 05:10 | 清理旧日志 | 删 30 天前黑匣子日志 | 日志目录 | ✅ |
| 05:20 | `memory_review` | 记忆库审查：陈旧 / 重复 / 健康条目 + 建议（**不删除**） | 内存 + 日志 | ✅ |
| 06:20 | `knowledge_audit` | 知识库巡检：孤儿 / 断链 / index 漂移 / 过期声明 | `data/maintenance/knowledge/` | ✅ |
| 07:20 | `route_coverage_check` | 路由↔UI 覆盖产物与工作树是否一致（`--check`） | 只读 | ✅ |
| 08:20 | `capability_manifest_check` | 能力清单与权威数据是否一致（`--check`） | 只读 | ✅ |
| 09:00（周一） | 生成周报 | 聚合 + 渲染周报 | `data/reports/` | ✅ |
| 09:20 | `skills_classify` | 技能自动分类（**纯规则**，为未归类技能补类） | 分类数据 | ✅ |
| 14:20 | `health_snapshot` | 健康端点 + 六域拓扑节点状态计数 | 日志一行 | ✅ |
| 15:20 | 保留策略 dry-run | `Archiver.plan()`：只出清单与体积，**不删除** | 只读 | ✅ |
| 16:20（周一） | SLO 指标周报 | 指标聚合 + 报告 | `data/` | ✅ |

时刻刻意错开，避免同一分钟并发读写。

**注册但默认关闭**（需要 token 或未核实，想用时在页面上点「恢复」）：

| 任务 | 原因 |
|---|---|
| 反馈建议执行 | 疑似生成式，未核实 |
| 周期进化 | 疑似生成式，未核实 |
| 生命周期检查 | 未核实 |

## 3. 手工触发

```powershell
python -m agent.maintenance.zero_token_jobs --list          # 看清单与排程
python -m agent.maintenance.zero_token_jobs --run memory_review
python -m agent.maintenance.zero_token_jobs --all           # 顺序全跑
```

令牌来源：先 `FLASK_API_TOKEN` 环境变量，再回退仓库 `.env`（与服务端同源），
只进请求头，**不打印**。

## 4. 在 UI 上管理（启停 / 改时刻）

「全景看板 → 定时任务」页新增「调度器任务（真实执行）」卡片，数据来自
`GET /api/scheduler/overview`（`TaskScheduler` 运行态，含 cron/interval 的 python 任务）：

- 每行标注**是否零 token**（零 token / 含 LLM / 未标注）与归属模块；
- 「暂停 / 恢复」即时生效；
- 选中任务填「时」「分」保存，即改「每天 时:分」；
- 改动写入 `data/scheduler_overrides.json`（按任务名），**重启后仍按你改过的样子注册**。

> 为什么之前看不到：旧页面读 `/api/schedules`（另一套「动作任务」调度器，长期为空），
> 而真正跑 cron 的是 `TaskScheduler`；`/api/scheduler/tasks` 又只列文件登记的命令任务，
> 所以 python 任务（含周报、日志清理）一直不在任何列表里。

## 5. 开关与边界

- 整体开关：`ZERO_TOKEN_JOBS_ENABLED=0`（false/no/off 亦可）后启动，不注册任何作业。
- 单条超时：shell 600s / http 120s；任一作业异常只记日志，**不会拖垮调度线程**。
- **不做删除**：保留策略只跑 dry-run；记忆只审不删；知识库只巡检不改卡。
- **不改受版本控制的产物**：两个 `--check` 都不重写报告，避免把工作树弄脏。

## 6. 同样是零 token，但**故意没排程**的

排程 = 无人值守。下面这些有确定性入口，但会改数据或跨语义边界，留人工触发：

| 能力 | 入口 | 现状 |
|---|---|---|
| 遗忘 / TTL 清理 | `agent/memory/forgetting.py` | **仍人工**：会改记忆数据（TTL 降级），需人工确认；且构造需要存储与质量源实例 |
| 记忆 ↔ Markdown 同步 | `agent/memory/markdown_syncer.py` | **仍人工**：需要绑定的 adapter 实例，且会写文件（要看 diff） |
| 向量重建 / 重排 | `agent/capregistry/skillsearch.py` | **仍人工**：要调 embedding / rerank 模型，非零成本 |
| 技能自动分类 | `agent/skills_mgmt/categorizer.py`（纯规则） | **已排程** 09:20（走 `/api/skills/classify/run-auto`） |
| 保留策略 dry-run | `agent/retention/` | **已排程** 15:20（dry-run，只出清单） |
| 保留策略正式执行 | `agent/retention/`（需显式确认） | **仍人工**：会归档/删除 |
| 周报生成 | 已有 cron | **已排程** 周一 09:00 |
| 清理旧日志 | 已有 cron | **已排程** 05:10（原 02:00，已挪进窗口） |
