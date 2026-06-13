# 云枢计划任务与心跳系统设计

## 概述

为云枢（Yunshu）添加完整的计划任务调度引擎和主动心跳健康检测机制，包括前端健康看板。

## 现状

- `agent/task_scheduler.py` 中已有一个 TaskScheduler 类，支持 cron 和 interval 任务，预注册了周报生成和日志清理任务，但其 `start()` 方法**从未被任何入口文件调用**
- `agent/system_tools.py` 中有一套基于 JSON 文件的定时任务 CRUD API（`create/delete/toggle/list`），但**没有后台引擎实际执行这些任务**
- `app_server.py` 中有一个 `GET /api/heartbeat` 被动端点，仅返回 CPU/内存/电池等系统指标

## 架构选择

**方案 A：单一增强型 TaskScheduler**（已选）

将 TaskScheduler 扩能为统一执行引擎，心跳作为内置特殊 interval 任务，避免另起独立服务。

## 设计详情

### 1. 增强型 TaskScheduler 架构

#### 位置
`agent/task_scheduler.py` — 重写现有文件

#### 支持三种任务类型

| 任务类型 | 来源 | 执行方式 | 示例 |
|---------|------|---------|------|
| `python_func` | 代码注册 | 直接调用 callable | 周报生成、日志清理 |
| `system_command` | API 创建 | `subprocess.Popen` 执行 | `curl api`, `python script.py` |
| `heartbeat` | 内置 | 综合健康检查函数 | 每 60 秒自动运行 |

#### 新增方法

- `add_command_task(name, command, interval_sec, enabled)` — 添加系统命令任务
- `load_from_json(path)` — 从 `data/scheduled_tasks.json` 加载 API 创建的任务
- `start_daemon(check_interval)` — 以 daemon 线程方式启动（替代阻塞式的 `start()`）
- `execute_now(task_id)` — 立即执行指定任务（手动触发）
- `get_history(limit, offset)` — 获取执行历史

#### 执行流程

```
app_server.py 初始化
  → get_scheduler() 获取单例
  → 从 data/scheduled_tasks.json 加载 API 创建的任务
  → 注册内置 heartbeat 特殊任务（interval=60s, type='heartbeat'）
  → scheduler.start_daemon(check_interval=10) → 新 daemon 线程进入 tick 循环
  → 每 10 秒 tick() → 检查到期任务 → 执行 → 记录历史到 JSONL
```

#### 系统命令执行

- 使用 `subprocess.Popen` + 超时（默认 300 秒）
- 保持 `system_tools.py` 中的白名单机制（可通过白名单配置添加新命令）
- 捕获 stdout/stderr，记录到执行历史
- 超时自动 `process.kill()` 并标记失败

#### Python 函数执行

- 保持现有 `add_cron_task` / `add_interval_task` 接口
- 在 try/except 中执行，异常记录到执行历史
- 不阻塞 tick 循环

#### 持久化

- 任务定义：`data/scheduled_tasks.json`（现有格式兼容）
- 执行历史：`data/task_history.jsonl`（JSONL 格式，按时间追加）
- 心跳历史：`data/heartbeat_history.json`（最新快照 + 最近 1440 条记录）

### 2. 心跳系统设计

心跳作为 TaskScheduler 中一个特殊的 interval 任务（类型 `heartbeat`），interval=60 秒。

#### 检测维度

| 维度 | 检测方式 | 数据来源 | 健康判定 |
|------|---------|---------|---------|
| 🖥️ 系统资源 | `collect_quick()` | BodySensor | CPU<90%, 内存<90%, 磁盘<95% |
| 🌐 LLM 连通性 | 简单 chat 调用 | LLMService | API 响应 < 5 秒 |
| 🧠 记忆系统 | 检查 MemoryManager | memory_manager | 存储可读写 |
| ⏱️ 调度器状态 | 检查 running 标志 | task_scheduler | 线程活跃 |
| 📊 关键线程 | `threading.enumerate()` | Python 运行时 | 所有关键线程 alive |

#### 数据格式

`data/heartbeat_history.json`:
```json
{
  "latest": {
    "timestamp": "2026-06-13T10:00:00",
    "status": "healthy",
    "checks": {
      "system": { "status": "ok", "cpu": 45.2, "memory": 62.1, "disk": 55.0 },
      "llm": { "status": "ok", "latency_ms": 1234 },
      "memory": { "status": "ok", "message": "正常运行" },
      "scheduler": { "status": "ok", "tasks": 5, "running": true },
      "threads": { "status": "ok", "total": 12, "alive": true }
    }
  },
  "history": [
    { "timestamp": "...", "status": "healthy", "cpu": 45, "memory": 62, "llm_latency_ms": 1234 }
  ]
}
```

历史保留最近 1440 条（24 小时 × 每分钟 1 条），超出自动裁剪。

### 3. API 端点

#### 增强现有端点

| 方法 | 路径 | 改动 |
|------|------|------|
| `GET` | `/api/heartbeat` | 返回最新完整健康状态（含 LLM、记忆、调度器检查结果） |
| `GET` | `/api/scheduler/list` | 增加 `type`、`next_run` 字段 |
| `POST` | `/api/scheduler/toggle` | 同步更新调度器中任务的启用状态 |

#### 新增端点

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/heartbeat/history` | 返回心跳历史数组 (limit/offset 分页) |
| `GET` | `/api/heartbeat/status` | 概览：当前状态、健康运行时长、总检查次数 |
| `POST` | `/api/scheduler/execute-now` | 手动触发立即执行指定任务 |
| `GET` | `/api/scheduler/history` | 任务执行历史 (limit/offset/type 筛选) |

### 4. 前端健康看板

#### 新增页面 `/health`

##### 4.1 实时健康概览卡

顶部展示整体状态 + 各维度指标卡片：
- 🟢/🟡/🔴 状态指示
- CPU、内存、磁盘、LLM 延迟、记忆系统、调度器
- 系统运行时长、总心跳检查次数

##### 4.2 心跳趋势图

使用 Canvas API 绘制（不引入第三方图表库）：
- CPU 使用率折线图（最近 60 个数据点）
- 内存使用率折线图（叠加显示）
- 异常时间点红色标记
- 跟随窗口大小自适应

##### 4.3 计划任务管理

- 任务列表：名称、类型、间隔、启用/禁用开关、上次/下次执行时间
- "立即执行"按钮（调用 `POST /api/scheduler/execute-now`）
- 删除任务按钮
- 新建任务表单（名称、命令、间隔、启用）

##### 4.4 执行历史列表

- 时间线列表：时间、任务名、类型、结果（成功/失败/运行中）、耗时
- 筛选标签：全部 / 成功 / 失败
- 最近 100 条

##### UI 风格

- 文件：`templates/health.html` + `static/js/health.js`
- 沿用暗色主题和现有 CSS 变量
- 导航栏新增"健康"入口

### 5. 启动集成

在 `app_server.py` 的 `__main__` 入口中添加：

```python
# 启动增强型定时任务调度器（daemon 模式）
from agent.task_scheduler import get_scheduler
scheduler = get_scheduler()
scheduler.start_daemon(check_interval=10)
```

不再需要现有 TaskScheduler 的守卫线程。

### 6. 预注册任务

| 任务名 | 类型 | 调度方式 | 说明 |
|-------|------|---------|------|
| 生成周报 | python_func | cron 每周一 09:00 | `generate_weekly_report()` |
| 清理旧日志 | python_func | cron 每天 02:00 | `cleanup_old_logs()` |
| 系统心跳 | heartbeat | interval 60s | 内置健康检查 |

## 涉及文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `agent/task_scheduler.py` | **重写** | 增强为统一执行引擎 |
| `agent/system_tools.py` | **微调** | 保持 CRUD 接口，添加动态白名单更新 |
| `app_server.py` | **修改** | 添加启动 TaskScheduler、注册新路由 |
| `data/scheduled_tasks.json` | **使用** | 现有格式不变 |
| `data/heartbeat_history.json` | **新建** | 心跳历史持久化 |
| `data/task_history.jsonl` | **新建** | 任务执行历史 |
| `templates/health.html` | **新建** | 健康看板页面 |
| `static/js/health.js` | **新建** | 看板前端逻辑 |
| `static/css/layout.css` | **可能追加** | 看板布局样式 |
| `templates/nav.html` 或等效文件 | **修改** | 添加"健康"导航入口 |

## 不涉及的范围

- 不引入 Celery / APScheduler / asyncio
- 不引入 WebSocket / SSE
- 不引入第三方图表库
- 不修改现有的 `cognitive/`、`memory/`、`sensor/` 核心模块
- 安全白名单保持现有机制
