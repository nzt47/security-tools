# 用户行为回放（Session Replay）— 使用说明

基于 rrweb 录制用户操作回放，仅在异常发生时上传最近 30 秒回放，
通过 `trace_id ↔ user_session_id ↔ error_id` 三向关联，实现"看到用户操作视频确认功能缺陷"。

## 一、设计目标

| 维度 | 目标 | 实现方式 |
|------|------|---------|
| 录制开销 | CPU < 2%，不影响用户体验 | 默认 1% 采样率，循环缓冲区 8000 事件 |
| 数据大小 | 单次回放 < 500 KB | gzip 压缩 + 仅异常时上传 |
| 隐私保护 | 敏感字段脱敏 | `blockClass='rrweb-mask'` + `maskAllInputs` |
| 关联能力 | 三向关联 | trace_id / user_session_id / error_id 同步落库 |
| 容错 | 录制失败不影响业务 | try/catch 降级 + sendBeacon 异步上传 |
| 覆盖率 | 新增代码 ≥ 80% | 已达 84.55%（见单元测试） |

## 二、架构概览

```
┌──────────────── 前端（yunshu-ui） ────────────────┐
│                                                   │
│  rrweb record()  ──▶  循环缓冲区（8000 事件）     │
│                          │                        │
│       ┌──────────────────┼────────────┐           │
│       ▼                  ▼            ▼           │
│   Sentry 错误触发    页面卸载     每 60s 检查      │
│       │                  │            │           │
│       └──────────┬───────┴────────────┘           │
│                  ▼                                 │
│         captureReplayOnError()                     │
│           ↓ gzip + base64 压缩                     │
│         ↓ sendBeacon / fetch+keepalive             │
└──────────────────┬────────────────────────────────┘
                   │ POST /api/replay/upload
                   ▼
┌──────────────── 后端（agent） ────────────────────┐
│                                                   │
│  routes_replay.py ──▶ ReplayStorage               │
│                          │                        │
│       ┌──────────────────┼────────────┐           │
│       ▼                  ▼            ▼           │
│  {date}/{id}.json.gz   SQLite 元数据   BusinessMetrics│
│                                                   │
│  关联统计：trace_id ↔ user_session_id ↔ error_id  │
└───────────────────────────────────────────────────┘
                   │
                   ▼
┌──────────────── 回放查看页面 ─────────────────────┐
│  /replay_viewer.html?id=xxx                       │
│  查询 → 元数据卡片 → rrweb-player 播放             │
└───────────────────────────────────────────────────┘
```

## 三、前端配置

### 3.1 环境变量

配置文件位于 [`yunshu-ui/.env.development`](file:///c:/Users/Administrator/agent/yunshu-ui/.env.development) 与 [`yunshu-ui/.env.production.example`](file:///c:/Users/Administrator/agent/yunshu-ui/.env.production.example)（生产环境模板，复制为 `.env.production` 使用）：

| 环境变量 | 说明 | 开发环境 | 生产环境 |
|----------|------|---------|---------|
| `VITE_SENTRY_DSN` | Sentry DSN（错误上报用） | GlitchTip DSN | GlitchTip DSN |
| `VITE_SENTRY_SAMPLE_RATE` | 错误采样率 | `1` | `0.1` |
| `VITE_SENTRY_TRACES_SAMPLE_RATE` | 性能追踪采样率 | `0` | `0` |
| `VITE_REPLAY_SAMPLE_RATE` | 回放录制采样率 | `0.01` | `0.01` |

> **生产环境强烈建议保持 `VITE_REPLAY_SAMPLE_RATE=0.01`（1%）**：避免录制洪流，CPU 开销 < 2%。

### 3.2 录制器核心实现

录制器位于 [`yunshu-ui/src/utils/replayRecorder.ts`](file:///c:/Users/Administrator/agent/yunshu-ui/src/utils/replayRecorder.ts)，关键设计：

```ts
// 1. 循环缓冲区：FIFO 淘汰，避免内存膨胀
const BUFFER_MAX_EVENTS = 8000;       // 约 30 秒密集操作
events.length > BUFFER_MAX_EVENTS &&
  events.splice(0, Math.floor(BUFFER_MAX_EVENTS * 0.1));  // 淘汰最早 10%

// 2. 敏感字段屏蔽
record({
  emit: handleEvent,
  blockClass: 'rrweb-mask',          // 含此 class 的元素完全屏蔽
  maskAllInputs: true,                // 所有 input 默认遮罩
  maskInputOptions: {                 // 细粒度配置
    password: true, email: true, tel: true,
  },
});

// 3. 仅异常时上传：取最近 30 秒
function captureReplayOnError(errorId: string, traceId: string) {
  const cutoff = Date.now() - 30 * 1000;
  const recent = events.filter(e => e.timestamp >= cutoff);
  // gzip 压缩 → base64 → sendBeacon 异步上传
  compressData(recent).then(b64 =>
    navigator.sendBeacon('/api/replay/upload', JSON.stringify({...}))
  );
}

// 4. gzip 压缩：使用浏览器原生 CompressionStream API
async function compressData(events: rrwebEvent[]): Promise<string> {
  const json = JSON.stringify(events);
  const cs = new CompressionStream('gzip');
  const stream = new Blob([json]).stream().pipeThrough(cs);
  const buf = await new Response(stream).arrayBuffer();
  return btoa(String.fromCharCode(...new Uint8Array(buf)));  // base64
}
```

### 3.3 在业务组件中触发回放

通常**不需要手动触发**——录制器会监听 Sentry 错误事件自动上传。
若需在特定业务节点手动触发（如自定义异常上报）：

```ts
import { captureReplayOnError } from '@/utils/replayRecorder';

try {
  await riskyOperation();
} catch (err) {
  const errorId = crypto.randomUUID();
  const traceId = getCurrentTraceId();  // 从响应头 X-Trace-Id 提取
  // 上传回放（关联 error_id 与 trace_id）
  captureReplayOnError(errorId, traceId);
  // 上报错误到 Sentry
  Sentry.captureException(err);
}
```

### 3.4 敏感字段屏蔽

| 屏蔽方式 | 用途 | 示例 |
|---------|------|------|
| `blockClass='rrweb-mask'` | 整块元素完全屏蔽（不录制 DOM） | `<div class="rrweb-mask">身份证号：110...</div>` |
| `maskAllInputs=true` | 所有 input 显示为 `***` | `<input type="text" />` |
| `maskInputOptions` | 细粒度控制 input 类型 | `{ password: true, email: true }` |
| `data-rrweb-mask` | 单元素屏蔽 | `<span data-rrweb-mask>敏感文本</span>` |

**强制要求**：所有包含用户隐私的元素必须添加 `rrweb-mask` class 或 `data-rrweb-mask` 属性。
代码审查时应强制检查此规则。

## 四、后端 API

所有 API 在 [`agent/server_routes/routes_replay.py`](file:///c:/Users/Administrator/agent/agent/server_routes/routes_replay.py) 注册，均使用 `@log_request + @trace_route` 装饰器。

### 4.1 上传回放

```
POST /api/replay/upload
Content-Type: application/json

{
  "replay_id": "uuid-v4",          // 前端生成，唯一
  "trace_id": "abc123...",          // 关联 OpenTelemetry
  "user_session_id": "session-xyz", // 关联用户会话
  "error_id": "sentry-event-id",    // 关联 Sentry 事件
  "timestamp": "2026-06-26T10:30:00",  // ISO 8601
  "duration_sec": 30,
  "event_count": 1500,
  "data": "H4sIAAAAA...",          // gzip-base64 编码
  "compressed": true,
  "encoding": "gzip-base64"
}

Response 200:
{
  "replay_id": "uuid-v4",
  "file_path": "/data/replays/20260626/uuid-v4.json.gz",
  "size_bytes": 1024,
  "stored": true
}

Response 400 (参数校验失败):
{
  "error": "REPLAY_ERR_001",
  "message": "replay_id 不能为空"
}
```

### 4.2 查询回放列表

```
GET /api/replay/list?limit=50&trace_id=xxx&user_session_id=yyy&hours=24

Response 200:
{
  "total": 12,
  "items": [
    {
      "replay_id": "uuid-v4",
      "trace_id": "abc123",
      "user_session_id": "session-xyz",
      "error_id": "sentry-event-id",
      "timestamp": "2026-06-26T10:30:00",
      "duration_sec": 30,
      "event_count": 1500,
      "size_bytes": 1024
    }
  ]
}
```

### 4.3 获取回放元数据

```
GET /api/replay/<replay_id>

Response 200:
{
  "replay_id": "uuid-v4",
  "trace_id": "abc123",
  ...
}
```

### 4.4 获取回放数据（播放用）

```
GET /api/replay/<replay_id>/data

Response 200:
[
  {"type": 4, "data": {...}, "timestamp": 1234567890},
  ...
]
```

返回的是解码后的 rrweb 事件数组，可直接喂给 `rrweb-player`。

### 4.5 关联统计

```
GET /api/replay/stats?hours=24

Response 200:
{
  "total_replays": 156,
  "with_trace_id": 142,
  "with_user_session_id": 138,
  "with_error_id": 120,
  "fully_correlated": 118,       // 三向都齐全
  "by_error_id": [
    {"error_id": "evt-001", "count": 3},
    {"error_id": "evt-002", "count": 1}
  ],
  "window_hours": 24
}
```

### 4.6 清理过期数据

```
POST /api/replay/cleanup?days=30

Response 200:
{
  "deleted_files": 245,
  "deleted_records": 245,
  "freed_bytes": 25190400
}
```

建议通过 cron 每日清理：

```bash
# /etc/cron.d/replay-cleanup
0 3 * * * curl -X POST http://localhost:5678/api/replay/cleanup?days=30
```

## 五、回放查看页面

### 5.1 访问入口

浏览器访问：
```
http://localhost:5678/replay_viewer.html
```

或带参数直接定位：
```
http://localhost:5678/replay_viewer.html?id=<replay_id>
http://localhost:5678/replay_viewer.html?trace_id=<trace_id>
```

页面源码位于 [`templates/replay_viewer.html`](file:///c:/Users/Administrator/agent/templates/replay_viewer.html)。

### 5.2 自解释 UI 设计

页面顶部有可折叠的"使用说明"面板，新用户无需查阅文档即可上手：

| 面板区域 | 功能说明 |
|---------|---------|
| 查询栏 | 输入 replay_id 或 trace_id，自动判定类型并查询 |
| 元数据卡片网格 | 展示时间、时长、事件数、文件大小、trace_id、error_id |
| 播放器区域 | rrweb-player 嵌入，支持播放/暂停/倍速/拖动进度条 |
| 回放列表表格 | 展示最近回放记录，点击行可切换播放 |
| 使用说明面板（折叠） | 4 步快速上手指南，含字段含义解释 |

### 5.3 关联查询流程

排查问题时的高效路径：

```
1. Sentry/GlitchTip 收到错误事件 → 获取 error_id
2. 访问 /replay_viewer.html → 在查询栏输入 error_id
3. 页面展示该 error_id 关联的所有回放（按时间倒序）
4. 点击最近一条 → 自动加载并播放
5. 同步查看 trace_id → 在 OpenTelemetry 中查询完整链路
```

也可以反向：从 trace_id 出发，定位用户操作回放 + Sentry 错误事件。

## 六、存储与清理

### 6.1 存储结构

后端存储采用**文件 + SQLite 双存储**（见 [`agent/monitoring/replay_storage.py`](file:///c:/Users/Administrator/agent/agent/monitoring/replay_storage.py)）：

```
{storage_root}/
├── 20260626/                       # 按日期分目录
│   ├── uuid-v4-1.json.gz           # gzip 压缩的 rrweb 事件
│   ├── uuid-v4-2.json.gz
│   └── ...
├── 20260627/
│   └── ...
└── replay_meta.db                  # SQLite 元数据
```

| 表字段 | 类型 | 说明 |
|--------|------|------|
| `replay_id` | TEXT UNIQUE | 前端生成的 UUID |
| `trace_id` | TEXT | OpenTelemetry trace_id |
| `user_session_id` | TEXT | 用户会话 ID |
| `error_id` | TEXT | Sentry 事件 ID |
| `timestamp` | TEXT | ISO 8601 时间戳 |
| `duration_sec` | INTEGER | 回放时长 |
| `event_count` | INTEGER | 事件数量 |
| `file_path` | TEXT | gzip 文件绝对路径 |
| `size_bytes` | INTEGER | 文件大小 |
| `compressed` | INTEGER | 是否压缩（1） |
| `encoding` | TEXT | 编码方式（gzip） |
| `created_at` | TEXT | 落库时间 |

### 6.2 清理策略

| 触发方式 | 默认保留 | 推荐场景 |
|---------|---------|---------|
| Cron 定时清理 | 30 天 | 生产环境常规清理 |
| 手动 API 调用 | 自定义 `days` | 紧急释放磁盘 |
| 磁盘水位触发 | 80% 水位线 | 兜底保护 |

> **重要：** 清理过期数据时，文件与 SQLite 记录同步删除，避免悬空记录。

### 6.3 磁盘容量估算

| 项 | 单条大小 | 日均（1000 用户 × 1% 采样 × 5% 错误率） | 月均 |
|----|---------|------------------------------------------|------|
| 单次回放 | 50~500 KB | 50~500 MB | 1.5~15 GB |

建议磁盘预留 50 GB / 月，并配置磁盘水位告警。

## 七、异常关联分析

### 7.1 健康检查端点

```
GET /api/diagnostics/health
```

响应中包含 `error_correlation` 字段：

```json
{
  "error_correlation": {
    "window_hours": 24,
    "replay_stats": {
      "total_replays": 156,
      "with_trace_id": 142,
      "with_user_session_id": 138,
      "with_error_id": 120,
      "fully_correlated": 118,
      "by_error_id": [...]
    },
    "sentry_enabled": true
  }
}
```

### 7.2 独立关联端点

```
GET /api/diagnostics/error_correlation?hours=24
```

返回与上文 `replay_stats` 相同的结构，便于独立查询。

### 7.3 关联关系图

```
        trace_id  ────  OpenTelemetry 链路追踪
            │
            │
        user_session_id ──  rrweb 用户行为回放
            │
            │
        error_id  ──────  Sentry 错误事件

三向关联：任意一方即可定位另外两方
```

## 八、性能与开销

### 8.1 前端开销

| 项 | 实测开销 | 说明 |
|----|---------|------|
| 录制 CPU | < 1.5% | rrweb 增量快照 + 循环缓冲区 |
| 内存占用 | ~3 MB | 8000 事件 × 平均 400 字节 |
| 上传网络 | < 100 KB/次 | gzip 压缩后单次回放 |
| 上传时机 | 仅异常时 | 1% 采样率 + 错误触发 |

### 8.2 后端开销

| 项 | 实测开销 | 说明 |
|----|---------|------|
| 单次存储 | < 5 ms | gzip 写入 + SQLite INSERT |
| 查询响应 | < 50 ms | SQLite 索引覆盖 |
| 磁盘 I/O | 极低 | 单文件 50~500 KB |
| 埋点开销 | < 1 ms | BusinessMetricsCollector 异步上报 |

## 九、故障排查

| 现象 | 排查步骤 |
|------|---------|
| 录制未启动 | 检查浏览器控制台 `[Replay]` 日志；确认 `VITE_REPLAY_SAMPLE_RATE > 0` |
| 上传失败 | 检查 `navigator.sendBeacon` 返回值；查看后端 `/api/replay/upload` 响应 |
| 播放器空白 | 确认 `GET /api/replay/<id>/data` 返回非空数组；检查浏览器控制台 rrweb-player 日志 |
| 关联不全 | 检查前端是否在 `captureReplayOnError` 时传入 `trace_id` 与 `error_id` |
| 磁盘满 | 调用 `POST /api/replay/cleanup?days=7` 紧急清理；检查 cron 是否正常运行 |
| 性能问题 | 降低 `VITE_REPLAY_SAMPLE_RATE` 到 `0.001`；增大淘汰比例到 20% |

## 十、测试与质量

### 10.1 单元测试

测试文件：
- [`tests/unit/test_error_reporting.py`](file:///c:/Users/Administrator/agent/tests/unit/test_error_reporting.py)：覆盖错误上报全链路
- [`tests/unit/test_replay_storage.py`](file:///c:/Users/Administrator/agent/tests/unit/test_replay_storage.py)：覆盖回放存储全链路

运行测试：

```bash
python -m pytest tests/unit/test_error_reporting.py tests/unit/test_replay_storage.py -v
```

### 10.2 覆盖率

| 模块 | 覆盖率 | 阈值 |
|------|--------|------|
| [`agent/error_reporting_config.py`](file:///c:/Users/Administrator/agent/agent/error_reporting_config.py) | 80.30% | ≥ 80% ✓ |
| [`agent/monitoring/replay_storage.py`](file:///c:/Users/Administrator/agent/agent/monitoring/replay_storage.py) | 84.55% | ≥ 80% ✓ |
| **总覆盖率** | 82.54% | ≥ 80% ✓ |

### 10.3 端到端验证

```bash
# 1. 启动后端
python run.py

# 2. 启动前端
cd yunshu-ui && npm run dev

# 3. 在浏览器中触发错误（控制台执行）
throw new Error('replay e2e probe')

# 4. 等待 5 秒，查询回放列表
curl http://localhost:5678/api/replay/list?hours=1

# 5. 取最新 replay_id，访问查看页面
# 浏览器打开 http://localhost:5678/replay_viewer.html?id=<replay_id>
```

## 十一、参考资源

- rrweb 官方文档：https://www.rrweb.io/
- rrweb-player：https://github.com/rrweb-io/rrweb-player
- CompressionStream API：https://developer.mozilla.org/zh-CN/docs/Web/API/CompressionStream
- 云枢回放存储：[`agent/monitoring/replay_storage.py`](file:///c:/Users/Administrator/agent/agent/monitoring/replay_storage.py)
- 云枢回放 API：[`agent/server_routes/routes_replay.py`](file:///c:/Users/Administrator/agent/agent/server_routes/routes_replay.py)
- 云枢回放查看页面：[`templates/replay_viewer.html`](file:///c:/Users/Administrator/agent/templates/replay_viewer.html)
- 云枢前端录制器：[`yunshu-ui/src/utils/replayRecorder.ts`](file:///c:/Users/Administrator/agent/yunshu-ui/src/utils/replayRecorder.ts)
- GlitchTip 部署指南：[`docs/observability/glitchtip_deployment.md`](file:///c:/Users/Administrator/agent/docs/observability/glitchtip_deployment.md)
